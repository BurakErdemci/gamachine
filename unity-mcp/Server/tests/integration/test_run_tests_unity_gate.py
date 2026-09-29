"""run_tests / get_test_job when the Unity package has already answered with its own compile gate.

The Editor plugin refuses a start and rejects a finished job itself (CompileGate.cs), because the CLI's
POST /api/command reaches it without passing this server's gate. The two full replies below are the
plugin's real output, captured from RunTests.HandleCommand and GetTestJob.HandleCommand in Unity
6000.4.4f1 (UnityMCPTests, one script with a CS0029 error). The server passes them through and does
not read the compile status again for a job the plugin has already rejected.
"""
import pytest

from .test_helpers import DummyContext
from .test_run_tests_async import _STARTED, _compile_status, _fake_editor

_COMPILE = {
    "verdict": "errors", "epoch": 1, "finished_epoch": 1,
    "scripts_changed_since_compile": {"count": 0, "paths": [], "scan_ms": 6.34},
    "errors": [{
        "code": "CS0029", "file": "Assets/ZzProbeTemp/ZzBroken.cs", "line": 1, "column": 33,
        "message": "Assets\\ZzProbeTemp\\ZzBroken.cs(1,33): error CS0029: Cannot implicitly convert type 'string' to 'int'",
        "assembly": "Assembly-CSharp"}],
    "error_count": 1, "warning_count": 0,
}

PLUGIN_REFUSAL = {
    "success": False, "code": "compile", "error": "compile",
    "message": ("Scripts do not compile (data.compile lists the errors). Tests would run against the last good "
                "assemblies, and a test assembly that failed to compile would report 0 tests. "
                "Fix the errors, then run the tests again."),
    "data": {"reason": "compile_errors", "compile": _COMPILE},
}

PLUGIN_REJECTED_JOB = {
    "success": False, "code": "compile", "error": "compile",
    "message": ("The run reported a pass (1 tests), but it does not count. Scripts do not compile now "
                "(data.compile lists the errors), so the run may have tested the last good assemblies, and a test "
                "assembly that failed to compile reports no tests. Fix the errors, then run the tests again. "
                "data.result keeps the run's summary."),
    "data": {
        "job_id": "c81e9b95e87e4a11ae092ab9c2a929a6", "status": "failed", "mode": "EditMode",
        "started_unix_ms": 1790657791620, "finished_unix_ms": 1790657792571, "last_update_unix_ms": 1790657792571,
        "progress": {
            "completed": 1, "total": 1, "current_test_full_name": None,
            "current_test_started_unix_ms": 1790657792536,
            "last_finished_test_full_name": "MCPForUnityTests.Editor.Services.CompileTrackerTests.ToJson_WithoutCode_LeavesCodeNull",
            "last_finished_unix_ms": 1790657792547, "stuck_suspected": False, "editor_is_focused": True,
            "blocked_reason": None, "failures_so_far": [], "failures_capped": False},
        "error": "compile",
        "result": {"mode": "EditMode", "summary": {
            "total": 1, "passed": 1, "failed": 0, "skipped": 0,
            "durationSeconds": 0.0843178, "resultState": "Passed"}, "results": None},
        "compile": _COMPILE,
    },
}

# The message says "domain reload"; data.reason must keep the transport from reading it as the editor reloading.
PLUGIN_BUSY = {
    "success": False, "code": "busy", "error": "busy",
    "message": ("A compile, asset import or domain reload is in progress, so no test run was started. "
                "Retry once compile_status says clean."),
    "data": {"reason": "compiling", "retry_after_ms": 500,
             "compile": {"verdict": "compiling", "note": "compilation in progress - error list is not final"}},
}


@pytest.mark.asyncio
async def test_run_tests_passes_the_plugins_compile_refusal_through(monkeypatch):
    from services.tools.run_tests import run_tests

    sent = _fake_editor(monkeypatch, _compile_status(), PLUGIN_REFUSAL)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert sent[-1] == "run_tests"
    assert resp.success is False
    assert resp.error == "compile"
    assert resp.message == PLUGIN_REFUSAL["message"]
    assert resp.hint is None
    assert resp.data["reason"] == "compile_errors"
    assert resp.data["compile"]["verdict"] == "errors"
    assert resp.data["compile"]["errors"][0]["code"] == "CS0029"


@pytest.mark.asyncio
async def test_run_tests_passes_the_plugins_busy_answer_through_with_a_retry_hint(monkeypatch):
    from services.tools.run_tests import run_tests

    _fake_editor(monkeypatch, _compile_status(), PLUGIN_BUSY)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert resp.success is False
    assert resp.error == "busy"
    assert resp.hint == "retry"
    assert resp.message == PLUGIN_BUSY["message"]
    assert resp.data["reason"] == "compiling"
    assert resp.data["retry_after_ms"] == 500


@pytest.mark.asyncio
async def test_run_tests_keeps_the_plugins_tests_running_answer(monkeypatch):
    from services.tools.run_tests import run_tests

    running = {"success": False, "code": "tests_running", "error": "tests_running",
               "data": {"reason": "tests_running", "retry_after_ms": 5000}}
    _fake_editor(monkeypatch, _compile_status(), running)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert resp.success is False
    assert resp.error == "tests_running"
    assert resp.hint is None
    assert resp.data["retry_after_ms"] == 5000


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_timeout", [None, 5])
async def test_get_test_job_passes_the_plugins_rejected_job_through(monkeypatch, wait_timeout):
    from services.tools.run_tests import GetTestJobResponse, get_test_job

    sent = _fake_editor(monkeypatch, _compile_status(), PLUGIN_REJECTED_JOB)
    resp = await get_test_job(DummyContext(), job_id="c81e9b95e87e4a11ae092ab9c2a929a6", wait_timeout=wait_timeout)

    assert isinstance(resp, GetTestJobResponse)
    assert resp.success is False
    assert resp.error == "compile"
    assert resp.message == PLUGIN_REJECTED_JOB["message"]
    assert resp.data.status == "failed"
    assert resp.data.error == "compile"
    assert resp.data.result.summary.total == 1
    assert resp.data.result.summary.passed == 1
    assert resp.data.compile["verdict"] == "errors"
    # The plugin has already judged the job; a second read of the status here would only add a race.
    assert "get_compile_status" not in sent


@pytest.mark.asyncio
async def test_get_test_job_passes_other_plugin_errors_through(monkeypatch):
    from services.tools.run_tests import GetTestJobResponse, get_test_job

    _fake_editor(monkeypatch, _compile_status(),
                 {"success": False, "code": "Unknown job_id.", "error": "Unknown job_id."})
    resp = await get_test_job(DummyContext(), job_id="nope")

    assert not isinstance(resp, GetTestJobResponse)
    assert resp.success is False
    assert resp.error == "Unknown job_id."


@pytest.mark.asyncio
async def test_the_server_gate_still_stops_a_start_the_plugin_would_allow(monkeypatch):
    """Defence in depth: an older plugin has no gate, so the server's own read still refuses."""
    from services.tools.run_tests import run_tests

    sent = _fake_editor(monkeypatch, _compile_status(failed=True), _STARTED)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert resp.success is False
    assert resp.error == "compile"
    assert "run_tests" not in sent
