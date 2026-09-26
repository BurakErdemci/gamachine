import pytest

from .test_helpers import DummyContext


@pytest.mark.asyncio
async def test_run_tests_async_forwards_params(monkeypatch):
    from services.tools.run_tests import run_tests

    captured = {}

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
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


def _fake_editor(monkeypatch, compile_status, reply):
    import services.tools.run_tests as mod

    sent = []

    async def fake_send_with_unity_instance(send_fn, unity_instance, command_type, params, **kwargs):
        sent.append(command_type)
        if command_type == "get_compile_status":
            if compile_status is None:
                return {"success": False, "error": "Unknown or unsupported command type: get_compile_status"}
            return {"success": True, "data": compile_status}
        return reply

    monkeypatch.setattr(mod.unity_transport, "send_with_unity_instance", fake_send_with_unity_instance)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [_compile_status(), None], ids=["clean", "unsupported"])
async def test_run_tests_starts_when_compile_is_clean_or_unknown(monkeypatch, status):
    from services.tools.run_tests import run_tests

    sent = _fake_editor(monkeypatch, status, _STARTED)
    resp = await run_tests(DummyContext(), mode="EditMode")

    assert resp.success is True
    assert resp.data.job_id == "abc123"
    assert sent[-1] == "run_tests"


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
    assert resp.data["status"] == "failed"
    assert resp.data["compile"]["verdict"] == "errors"
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
async def test_get_test_job_with_tests_does_not_read_compile_status(monkeypatch):
    from services.tools.run_tests import get_test_job

    sent = _fake_editor(monkeypatch, _compile_status(failed=True), _finished(4))
    resp = await get_test_job(DummyContext(), job_id="abc123")

    assert resp.success is True
    assert resp.data.result.summary.total == 4
    assert "get_compile_status" not in sent
