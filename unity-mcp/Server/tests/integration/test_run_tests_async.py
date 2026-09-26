import pytest

from .test_helpers import DummyContext


@pytest.mark.asyncio
async def test_run_tests_async_forwards_params(monkeypatch):
    from services.tools.run_tests import run_tests

    captured = {}

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        if command_type == "get_compile_status":
            return {"success": True, "data": _compile_status()}
        captured["command_type"] = command_type
        captured["params"] = params
        return {"success": True, "data": {"job_id": "abc123", "status": "running", "mode": "EditMode"}}

    import services.tools.run_tests as mod
    monkeypatch.setattr(
        mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)

    resp = await run_tests(
        DummyContext(),
        mode="EditMode",
        test_names="MyNamespace.MyTests.TestA",
        include_details=True,
    )
    assert captured["command_type"] == "run_tests"
    assert captured["params"]["mode"] == "EditMode"
    assert captured["params"]["testNames"] == ["MyNamespace.MyTests.TestA"]
    assert captured["params"]["includeDetails"] is True
    assert resp.success is True
    assert resp.data is not None
    assert resp.data.job_id == "abc123"


@pytest.mark.asyncio
async def test_run_tests_forwards_init_timeout(monkeypatch):
    from services.tools.run_tests import run_tests

    captured = {}

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        if command_type == "get_compile_status":
            return {"success": True, "data": _compile_status()}
        captured["params"] = params
        return {"success": True, "data": {"job_id": "abc123", "status": "running", "mode": "PlayMode"}}

    import services.tools.run_tests as mod
    monkeypatch.setattr(
        mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)

    resp = await run_tests(
        DummyContext(),
        mode="PlayMode",
        init_timeout=120000,
    )
    assert captured["params"]["initTimeout"] == 120000
    assert resp.success is True


@pytest.mark.asyncio
async def test_run_tests_omits_init_timeout_when_none(monkeypatch):
    from services.tools.run_tests import run_tests

    captured = {}

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        if command_type == "get_compile_status":
            return {"success": True, "data": _compile_status()}
        captured["params"] = params
        return {"success": True, "data": {"job_id": "abc123", "status": "running", "mode": "EditMode"}}

    import services.tools.run_tests as mod
    monkeypatch.setattr(
        mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)

    resp = await run_tests(DummyContext(), mode="EditMode")
    assert "initTimeout" not in captured["params"]
    assert resp.success is True


@pytest.mark.asyncio
async def test_run_tests_rejects_negative_init_timeout():
    from services.tools.run_tests import run_tests

    resp = await run_tests(DummyContext(), mode="EditMode", init_timeout=-1)
    assert resp.success is False
    assert "init_timeout" in resp.error


@pytest.mark.asyncio
async def test_run_tests_rejects_zero_init_timeout():
    from services.tools.run_tests import run_tests

    resp = await run_tests(DummyContext(), mode="EditMode", init_timeout=0)
    assert resp.success is False
    assert "init_timeout" in resp.error


@pytest.mark.asyncio
async def test_run_tests_clear_stuck_forwards_only_the_flag(monkeypatch):
    from services.tools.run_tests import run_tests

    captured = {}

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        captured["command_type"] = command_type
        captured["params"] = params
        return {"success": True, "message": "Stuck job cleared.", "data": {"cleared": True}}

    import services.tools.run_tests as mod
    monkeypatch.setattr(
        mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)

    resp = await run_tests(DummyContext(), clear_stuck=True)

    # C# reads @params["clear_stuck"] verbatim (RunTests.cs:23), so the key must stay snake_case.
    assert captured["command_type"] == "run_tests"
    assert captured["params"] == {"clear_stuck": True}
    assert resp.success is True
    assert resp.data == {"cleared": True}


@pytest.mark.asyncio
async def test_run_tests_clear_stuck_bypasses_preflight(monkeypatch):
    """#1272: preflight(requires_no_tests=True) would reject the call that clears the job blocking it."""
    from services.tools.run_tests import run_tests

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        return {"success": True, "message": "Stuck job cleared.", "data": {"cleared": True}}

    async def exploding_preflight(*args, **kwargs):
        raise AssertionError("clear_stuck must short-circuit before preflight")

    import services.tools.run_tests as mod
    monkeypatch.setattr(
        mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)
    monkeypatch.setattr(mod, "preflight", exploding_preflight)

    resp = await run_tests(DummyContext(), clear_stuck=True)
    assert resp.success is True


@pytest.mark.asyncio
async def test_run_tests_clear_stuck_ignores_invalid_init_timeout(monkeypatch):
    """Recovery must be unconditional: an unrelated bad arg must not block clearing."""
    from services.tools.run_tests import run_tests

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        return {"success": True, "message": "Stuck job cleared.", "data": {"cleared": True}}

    import services.tools.run_tests as mod
    monkeypatch.setattr(
        mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)

    resp = await run_tests(DummyContext(), clear_stuck=True, init_timeout=0)
    assert resp.success is True


@pytest.mark.asyncio
async def test_run_tests_without_clear_stuck_still_preflights(monkeypatch):
    from services.tools.run_tests import run_tests

    calls = []

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        if command_type == "get_compile_status":
            return {"success": True, "data": _compile_status()}
        return {"success": True, "data": {"job_id": "abc123", "status": "running", "mode": "EditMode"}}

    async def recording_preflight(*args, **kwargs):
        calls.append(kwargs)
        return None

    import services.tools.run_tests as mod
    monkeypatch.setattr(
        mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)
    monkeypatch.setattr(mod, "preflight", recording_preflight)

    resp = await run_tests(DummyContext(), mode="EditMode")
    assert len(calls) == 1
    assert calls[0]["requires_no_tests"] is True
    assert resp.success is True


@pytest.mark.asyncio
async def test_get_test_job_forwards_job_id(monkeypatch):
    from services.tools.run_tests import get_test_job

    captured = {}

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        captured["command_type"] = command_type
        captured["params"] = params
        return {"success": True, "data": {"job_id": params["job_id"], "status": "running", "mode": "EditMode"}}

    import services.tools.run_tests as mod
    monkeypatch.setattr(
        mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)

    resp = await get_test_job(DummyContext(), job_id="job-1")
    assert captured["command_type"] == "get_test_job"
    assert captured["params"]["job_id"] == "job-1"
    assert resp.success is True
    assert resp.data is not None
    assert resp.data.job_id == "job-1"


def _compile_status(*, failed=False, changed=0, compiling=False):
    return {
        "is_compiling": compiling, "is_updating": False,
        "compilation_failed_now": failed, "epoch": 3, "finished_epoch": 3 if not compiling else 2,
        "last_failed": failed, "reload_done_after_finish": True,
        "error_count": 1 if failed else 0, "warning_count": 0,
        "errors": [{"code": "CS0246", "file": "Assets/Tests/ProbeTests.cs", "line": 4}] if failed else [],
        "scripts_changed_since_compile": {"count": changed, "paths": []},
    }


_UNSUPPORTED = {"success": False, "error": "Unknown or unsupported command type: get_compile_status"}


def _fake_editor(monkeypatch, compile_status, reply, *, compile_reply=None):
    """compile_reply, when given, is the raw get_compile_status reply (an Exception is raised)."""
    import services.tools.compile_status as cs
    import services.tools.run_tests as mod

    sent = []
    if compile_reply is None:
        compile_reply = _UNSUPPORTED if compile_status is None else {"success": True, "data": compile_status}

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        sent.append(command_type)
        if command_type == "get_compile_status":
            if isinstance(compile_reply, Exception):
                raise compile_reply
            return compile_reply
        return reply

    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)
    monkeypatch.setattr(cs, "RETRY_DELAY_S", 0)
    return sent


_STARTED = {"success": True, "data": {"job_id": "abc123", "status": "running", "mode": "EditMode"}}


@pytest.mark.asyncio
async def test_run_tests_refuses_when_scripts_do_not_compile(monkeypatch):
    from services.tools.run_tests import run_tests

    sent = _fake_editor(monkeypatch, _compile_status(failed=True), _STARTED)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert resp.success is False
    assert resp.error == "compile"
    assert resp.data["compile"]["verdict"] == "errors"
    assert resp.data["compile"]["errors"][0]["code"] == "CS0246"
    assert "run_tests" not in sent


@pytest.mark.asyncio
async def test_run_tests_refuses_when_scripts_changed_since_compile(monkeypatch):
    from services.tools.run_tests import run_tests

    sent = _fake_editor(monkeypatch, _compile_status(changed=2), _STARTED)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert resp.success is False
    assert resp.data["compile"]["verdict"] == "stale"
    assert "refresh_unity" in resp.message
    assert "run_tests" not in sent


@pytest.mark.asyncio
async def test_run_tests_busy_while_compiling(monkeypatch):
    from services.tools.run_tests import run_tests

    sent = _fake_editor(monkeypatch, _compile_status(compiling=True), _STARTED)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert resp.success is False
    assert resp.error == "busy"
    assert resp.hint == "retry"
    assert "run_tests" not in sent


# An older package answers get_compile_status as an unknown command.
@pytest.mark.asyncio
@pytest.mark.parametrize("status,compile_reply,verdict", [
    (_compile_status(), None, "clean"),
    (None, None, "unknown"),
], ids=["clean", "unsupported-command"])
async def test_run_tests_starts_when_compile_is_clean_or_unsupported(monkeypatch, status, compile_reply, verdict):
    from services.tools.run_tests import run_tests

    sent = _fake_editor(monkeypatch, status, _STARTED, compile_reply=compile_reply)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert resp.success is True
    assert resp.data.job_id == "abc123"
    assert resp.data.compile["verdict"] == verdict
    assert sent[-1] == "run_tests"


_MALFORMED = {**_compile_status(), "is_compiling": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("compile_reply", [
    {"success": False, "error": "status read failed"},
    TimeoutError("timed out"),
    "not a dict",
    {"success": True, "data": _MALFORMED},
    # Success-shaped but unusable: not an older package (that answers "unknown command").
    {"success": True, "data": {"message": "ok"}},
    {"success": True, "data": {**_compile_status(), "epoch": "3"}},
    {"success": True, "data": None},
    {"success": True},
], ids=["failed-read", "transport-timeout", "non-dict", "malformed",
        "success-no-epoch", "success-string-epoch", "success-null-data", "success-no-data"])
async def test_run_tests_refuses_when_compile_status_is_unreadable(monkeypatch, compile_reply):
    from services.tools.run_tests import run_tests

    sent = _fake_editor(monkeypatch, None, _STARTED, compile_reply=compile_reply)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert resp.success is False
    assert resp.error == "busy"
    assert resp.hint == "retry"
    assert resp.data["reason"] == "compile_status_unknown"
    assert resp.data["compile"]["verdict"] == "unknown"
    assert "run_tests" not in sent


@pytest.mark.asyncio
async def test_run_tests_refuses_on_a_timeout_verdict(monkeypatch):
    import services.tools.run_tests as mod

    sent = _fake_editor(monkeypatch, _compile_status(), _STARTED)
    monkeypatch.setattr(mod, "live_verdict", lambda status, problem=None: {"verdict": "timeout"})
    resp = await mod.run_tests(DummyContext(), mode="EditMode")

    assert resp.success is False
    assert resp.error == "busy"
    assert resp.data["compile"]["verdict"] == "timeout"
    assert "run_tests" not in sent


def _finished(total, status="succeeded"):
    return {"success": True, "data": {
        "job_id": "abc123", "status": status, "mode": "EditMode",
        "result": {"mode": "EditMode", "summary": {
            "total": total, "passed": total, "failed": 0, "skipped": 0,
            "durationSeconds": 0.1, "resultState": "Passed"}},
    }}


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_timeout", [None, 5])
async def test_get_test_job_zero_tests_with_compile_errors_is_a_failure(monkeypatch, wait_timeout):
    from services.tools.run_tests import get_test_job

    _fake_editor(monkeypatch, _compile_status(failed=True), _finished(0))
    resp = await get_test_job(DummyContext(), job_id="abc123", wait_timeout=wait_timeout)

    assert resp.success is False
    assert resp.error == "compile"
    assert resp.data.status == "failed"
    assert resp.data.compile["verdict"] == "errors"
    assert resp.message.startswith("The run found 0 tests.")


@pytest.mark.asyncio
async def test_get_test_job_zero_tests_with_clean_compile_stays_green(monkeypatch):
    from services.tools.run_tests import get_test_job

    _fake_editor(monkeypatch, _compile_status(), _finished(0))
    resp = await get_test_job(DummyContext(), job_id="abc123")

    assert resp.success is True
    assert resp.data.status == "succeeded"
    assert resp.data.compile["verdict"] == "clean"


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_timeout", [None, 5])
@pytest.mark.parametrize("total", [1, 4])
@pytest.mark.parametrize("status,verdict", [
    (_compile_status(failed=True), "errors"),
    (_compile_status(changed=1), "stale"),
], ids=["errors", "stale"])
async def test_get_test_job_passed_tests_with_untrusted_compile_is_a_failure(
        monkeypatch, status, verdict, total, wait_timeout):
    """Scripts can stop compiling while a run is in progress; a green N>0 result is not a pass then."""
    from services.tools.run_tests import get_test_job

    _fake_editor(monkeypatch, status, _finished(total))
    resp = await get_test_job(DummyContext(), job_id="abc123", wait_timeout=wait_timeout)

    assert resp.success is False
    assert resp.error == "compile"
    assert resp.data.status == "failed"
    assert resp.data.compile["verdict"] == verdict
    assert resp.data.result.summary.total == total
    assert resp.data.result.summary.passed == total


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_timeout", [None, 5])
@pytest.mark.parametrize("compile_reply,verdict", [
    ({"success": True, "data": _compile_status(compiling=True)}, "compiling"),
    ({"success": True, "data": {**_compile_status(), "is_updating": True}}, "pending"),
    ({"success": True, "data": {**_compile_status(), "reload_done_after_finish": False}}, "pending"),
    ({"success": False, "error": "status read failed"}, "unknown"),
    (TimeoutError("timed out"), "unknown"),
    ({"success": True, "data": _MALFORMED}, "unknown"),
    ({"success": True, "data": {"message": "ok"}}, "unknown"),
], ids=["compiling", "importing", "reload-pending", "failed-read", "transport-timeout", "malformed",
        "success-no-epoch"])
async def test_get_test_job_passed_tests_with_unsettled_compile_are_not_green(
        monkeypatch, compile_reply, verdict, wait_timeout):
    """A pass read while the compile verdict is not final, or not readable, proves nothing."""
    from services.tools.run_tests import get_test_job

    _fake_editor(monkeypatch, None, _finished(4), compile_reply=compile_reply)
    resp = await get_test_job(DummyContext(), job_id="abc123", wait_timeout=wait_timeout)

    assert resp.success is False
    assert resp.error == "compile"
    assert resp.data.status == "failed"
    assert resp.data.compile["verdict"] == verdict
    assert resp.data.result.summary.passed == 4
    assert "does not count" in resp.message


@pytest.mark.asyncio
@pytest.mark.parametrize("status,verdict", [(_compile_status(), "clean"), (None, "unknown")],
                         ids=["clean", "unsupported"])
async def test_get_test_job_passed_tests_carry_the_compile_verdict(monkeypatch, status, verdict):
    from services.tools.run_tests import get_test_job

    _fake_editor(monkeypatch, status, _finished(4))
    resp = await get_test_job(DummyContext(), job_id="abc123")

    assert resp.success is True
    assert resp.data.status == "succeeded"
    assert resp.data.result.summary.total == 4
    assert resp.data.compile["verdict"] == verdict


@pytest.mark.asyncio
async def test_get_test_job_failed_run_carries_the_compile_verdict(monkeypatch):
    from services.tools.run_tests import get_test_job

    _fake_editor(monkeypatch, _compile_status(failed=True), _finished(4, status="failed"))
    resp = await get_test_job(DummyContext(), job_id="abc123")

    assert resp.success is True
    assert resp.data.status == "failed"
    assert resp.data.compile["verdict"] == "errors"


@pytest.mark.asyncio
async def test_get_test_job_running_poll_does_not_read_compile_status(monkeypatch):
    from services.tools.run_tests import get_test_job

    sent = _fake_editor(monkeypatch, _compile_status(failed=True), _STARTED)
    resp = await get_test_job(DummyContext(), job_id="abc123")

    assert resp.success is True
    assert resp.data.status == "running"
    assert resp.data.compile is None
    assert "get_compile_status" not in sent


# --- The start and clear commands are sent once, even when Unity answers "reloading". ---

class _FakeConnection:
    def __init__(self, first_reply):
        self.first_reply = first_reply
        self.calls = []

    def send_command(self, command_type, params, max_attempts=None):
        if command_type == "get_compile_status":
            return {"success": True, "data": _compile_status()}
        self.calls.append(command_type)
        if len(self.calls) == 1:
            return self.first_reply
        return _STARTED


_RELOADING_REPLIES = [
    {"success": False, "data": {"reason": "reloading", "retry_after_ms": 1}},
    {"success": False, "state": "reloading"},
    {"success": False, "error": "Unity is reloading; please retry"},
]


async def _forwarding_router(send_fn, unity_instance, *args, **kwargs):
    return await send_fn(*args, **kwargs)


async def _kwarg_dropping_router(send_fn, unity_instance, command_type, params, **kwargs):
    return await send_fn(command_type, params, retry_ms=1, max_retries=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("router", [_forwarding_router, _kwarg_dropping_router], ids=["forwarding", "kwarg-dropping"])
@pytest.mark.parametrize("first_reply", _RELOADING_REPLIES, ids=["data-reason", "state", "message"])
@pytest.mark.parametrize("clear_stuck", [False, True], ids=["start", "clear"])
async def test_run_tests_reload_reply_is_not_resent(monkeypatch, router, first_reply, clear_stuck):
    import services.tools.run_tests as mod
    import transport.legacy.unity_connection as legacy

    conn = _FakeConnection(first_reply)
    monkeypatch.setattr(legacy, "get_unity_connection", lambda instance_id=None: conn)
    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", router)

    async def no_preflight(*args, **kwargs):
        return None

    monkeypatch.setattr(mod, "preflight", no_preflight)
    resp = await mod.run_tests(DummyContext(), clear_stuck=clear_stuck)

    assert conn.calls == ["run_tests"]
    assert resp.success is False
    assert resp.hint == "retry"
    assert resp.data["reason"] == "reloading"


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_stuck", [False, True], ids=["start", "clear"])
async def test_run_tests_asks_the_router_not_to_retry(monkeypatch, clear_stuck):
    """The HTTP route reads retry_on_reload from the router's keyword arguments."""
    import services.tools.run_tests as mod

    seen = []

    async def recording_router(send_fn, unity_instance, command_type, params, **kwargs):
        if command_type == "run_tests":
            seen.append(kwargs.get("retry_on_reload"))
        if command_type == "get_compile_status":
            return {"success": True, "data": _compile_status()}
        return _STARTED

    async def no_preflight(*args, **kwargs):
        return None

    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", recording_router)
    monkeypatch.setattr(mod, "preflight", no_preflight)
    await mod.run_tests(DummyContext(), clear_stuck=clear_stuck)

    assert seen == [False]
