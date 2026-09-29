"""stdio (legacy) sender: which replies count as "Unity is reloading" and are sent again.

A plugin reply that names its own error code (ErrorResponse code + message + data) is the plugin's
answer and goes back to the caller on the first reply, even when its sentence mentions a "domain
reload". get_test_job answers that way for a job that passed while a compile, import or reload is
pending (CompileGate.RejectedRunMessage, wrapped by GetTestJob.cs as code "compile" with the job as
data). Only replies with no explicit error code, or a code that itself says reload, fall back to the
text check.
"""
import pytest

import transport.legacy.unity_connection as uc
from services.tools.run_tests import GetTestJobResponse, get_test_job

from .test_helpers import DummyContext

# The message and payload shape are the plugin's for the compiling/pending verdict (data has no `reason`).
PLUGIN_REJECTED_JOB_COMPILING = {
    "success": False, "code": "compile", "error": "compile",
    "message": ("The run reported a pass (4 tests), but it does not count. A compile, asset import or domain "
                "reload is in progress now, so the scripts may differ from the ones the run tested and their "
                "compile result is not final. Wait until compile_status says clean, then run the tests again. "
                "data.result keeps the run's summary."),
    "data": {
        "job_id": "j1", "status": "failed", "mode": "EditMode", "error": "compile",
        "progress": {"completed": 4, "total": 4},
        "result": {"mode": "EditMode", "summary": {
            "total": 4, "passed": 4, "failed": 0, "skipped": 0, "durationSeconds": 0.1, "resultState": "Passed"}},
        "compile": {"verdict": "compiling", "note": "compilation in progress - error list is not final"},
    },
}


class _CountingConnection:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def send_command(self, command_type, params, max_attempts=None):
        self.calls += 1
        return self.reply


@pytest.fixture
def connection(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_RELOAD_MAX_WAIT_S", "0.5")

    def install(reply):
        conn = _CountingConnection(reply)
        monkeypatch.setattr(uc, "get_unity_connection", lambda instance_id=None: conn)
        return conn
    return install


def test_the_rejected_job_answer_is_not_read_as_a_reload():
    assert uc._extract_response_reason(PLUGIN_REJECTED_JOB_COMPILING) is None
    assert uc._is_reloading_response(PLUGIN_REJECTED_JOB_COMPILING) is False


def test_the_sender_returns_the_rejected_job_on_the_first_reply(connection):
    conn = connection(PLUGIN_REJECTED_JOB_COMPILING)

    resp = uc.send_command_with_retry("get_test_job", {"job_id": "j1"}, max_retries=40, retry_ms=50)

    assert conn.calls == 1
    assert resp is PLUGIN_REJECTED_JOB_COMPILING


@pytest.mark.asyncio
async def test_get_test_job_keeps_the_plugins_answer_for_a_compiling_verdict(connection):
    conn = connection(PLUGIN_REJECTED_JOB_COMPILING)

    resp = await get_test_job(DummyContext(), job_id="j1")

    assert conn.calls == 1
    assert isinstance(resp, GetTestJobResponse)
    assert resp.success is False
    assert resp.error == "compile"
    assert resp.message == PLUGIN_REJECTED_JOB_COMPILING["message"]
    assert resp.data.compile["verdict"] == "compiling"


@pytest.mark.parametrize("reply", [
    # The transport's own preflight answer: an error sentence and a hint, no code.
    {"success": False, "error": "Unity is reloading; please retry", "hint": "retry"},
    {"success": False, "message": "Unity is reloading"},
    # A code that says reload is a reload whatever the message reads.
    {"success": False, "code": "compiling_or_reloading", "error": "compiling_or_reloading", "data": {"hint": "retry"}},
    {"success": False, "code": "Cannot reflect while Unity is compiling. Wait for domain reload to complete.",
     "error": "Cannot reflect while Unity is compiling. Wait for domain reload to complete."},
    {"success": False, "state": "reloading"},
])
def test_genuine_reload_replies_are_still_sent_again(connection, reply):
    conn = connection(reply)

    uc.send_command_with_retry("get_test_job", {"job_id": "j1"}, max_retries=3, retry_ms=50)

    assert conn.calls > 1


def test_an_explicit_reason_wins_over_the_error_code():
    reply = {"success": False, "code": "busy", "error": "busy", "message": "No test run was started.",
             "data": {"reason": "reloading"}}
    assert uc._is_reloading_response(reply) is True
    reply["data"]["reason"] = "compiling"
    assert uc._is_reloading_response(reply) is False
