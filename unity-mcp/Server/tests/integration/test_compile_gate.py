"""
The compile gate: script writes, refresh_unity and read_console must never show
"0 errors" for a compile that has not happened, is still running, or was read
from a status the Editor could not answer.

Each test drives the real tool against a fake Editor whose compile status moves
through a scripted timeline, one step per status poll. The mutation itself does
not advance the timeline, so step 0 is the state before the write.
"""
from __future__ import annotations

import pytest

from .test_helpers import DummyContext, setup_script_tools

try:
    import services.tools.compile_status as cs
except ImportError:  # the gate does not exist yet (running against the old code)
    cs = None

UNREADABLE = "unreadable"
RETRY = {
    "success": False,
    "error": "Unity did not respond to 'get_compile_status' within 2.0s; please retry",
    "hint": "retry",
}
CS0029 = {
    "code": "CS0029", "file": "Assets/Scripts/Probe.cs", "line": 1, "column": 52,
    "message": "Assets/Scripts/Probe.cs(1,52): error CS0029: Cannot implicitly convert type 'string' to 'int'",
}
STALE_CONSOLE_ERROR = "Assets/Old.cs(3,1): error CS0103: The name 'x' does not exist in the current context"


def status(epoch, finished=None, *, compiling=False, failed=False, errors=(),
           reloaded=True, changed=0, updating=False, failed_now=None):
    finished = epoch if finished is None else finished
    return {
        "is_compiling": compiling, "is_updating": updating,
        "compilation_failed_now": failed if failed_now is None else failed_now,
        "epoch": epoch, "finished_epoch": finished, "last_failed": failed,
        "error_count": len(errors), "warning_count": 0, "errors": list(errors),
        "reload_done_after_finish": reloaded,
        "scripts_changed_since_compile": {
            "count": changed, "paths": [f"Assets/Changed{i}.cs" for i in range(min(changed, 5))]},
    }


def step(st, console=()):
    return {"status": st, "console": list(console)}


class FakeEditor:
    def __init__(self, steps, unsupported=False, write_reply=None):
        self.steps = steps
        self.index = 0
        self.calls = []
        self.unsupported = unsupported
        self.write_reply = write_reply
        self.returned_at_step = None

    def current(self):
        return self.steps[min(self.index, len(self.steps) - 1)]

    def commands(self, name):
        return [c for c in self.calls if c[0] == name]

    async def send(self, command_type, params, **kwargs):
        self.calls.append((command_type, dict(params or {}), kwargs))
        if command_type in ("get_compile_status", "get_editor_state"):
            if command_type == "get_compile_status" and self.unsupported:
                return {"success": False, "error": "Unknown or unsupported command type: get_compile_status"}
            st = self.current()["status"]
            self.index += 1
            if st == UNREADABLE:
                return dict(RETRY)
            if command_type == "get_compile_status":
                return {"success": True, "message": "Retrieved compile status.", "data": dict(st)}
            busy = st["is_compiling"] or st["epoch"] > st["finished_epoch"]
            # Measured on 6000.4: between compilationFinished and the reload,
            # isCompiling is already false and no reload is flagged yet.
            return {"success": True, "data": {
                "compilation": {"is_compiling": busy, "is_domain_reload_pending": False},
                "advice": {"ready_for_tools": not busy, "blocking_reasons": ["compiling"] if busy else []},
            }}
        if command_type == "read_console":
            return {"success": True, "data": {"lines": list(self.current()["console"])}}
        if command_type == "refresh_unity":
            return {"success": True, "message": "Refresh requested.", "data": {
                "refresh_triggered": True, "compile_requested": params.get("compile") == "request",
                "resulting_state": "compiling"}}
        if command_type == "manage_script":
            if params.get("action") in ("read", "get_sha"):
                return {"success": True, "data": {"contents": "public class Probe {}\n", "sha256": "abc"}}
            if self.write_reply is not None:
                return dict(self.write_reply)
            return {"success": True, "message": "written", "data": {}}
        raise AssertionError(f"unexpected command {command_type}")


@pytest.fixture
def editor_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from core.config import config
    monkeypatch.setattr(config, "transport_mode", "stdio")
    if cs is not None:
        monkeypatch.setattr(cs, "POLL_S", 0.002)
        monkeypatch.setattr(cs, "RETRY_DELAY_S", 0.002)
        monkeypatch.setattr(cs, "START_WINDOW_S", 0.15)
        monkeypatch.setattr(cs, "MAX_WAIT_S", 1.5)

    def make(steps, **kwargs):
        editor = FakeEditor(steps, **kwargs)
        import transport.legacy.unity_connection as legacy
        import services.resources.editor_state as editor_state
        import services.tools.read_console as read_console_mod
        import services.tools.script_apply_edits as sae
        monkeypatch.setattr(legacy, "async_send_command_with_retry", editor.send)
        monkeypatch.setattr(editor_state, "async_send_command_with_retry", editor.send)
        monkeypatch.setattr(read_console_mod, "async_send_command_with_retry", editor.send)
        monkeypatch.setattr(sae, "async_send_command_with_retry", editor.send)
        return editor

    return make


async def _create(**kwargs):
    tools = setup_script_tools()
    resp = await tools["create_script"](
        DummyContext(), path="Assets/Scripts/Probe.cs",
        contents="public class Probe { void M() { int x = \"s\"; } }", **kwargs)
    assert resp["success"] is True, resp
    return resp["data"]["compile"]


def _never_claims_zero_errors_unproven(compile_result):
    """The success number: a response may say 0 errors only with verdict clean."""
    if compile_result.get("error_count") == 0 or compile_result.get("errors") == []:
        assert compile_result.get("verdict") == "clean", compile_result


# ── script writes (create_script as the representative mutation) ──────────────

@pytest.mark.asyncio
async def test_status_unreadable_mid_reload_is_not_idle(editor_factory):
    """Old bug: a failed editor_state read counted as 'not busy', so the finish
    wait ended at the first retry hint and the console (still empty) said 0 errors."""
    editor = editor_factory(
        [step(status(5)), step(status(5)), step(status(6, 5, compiling=True))]
        + [step(UNREADABLE)] * 4
        + [step(status(6, 5, compiling=True))] * 2
        + [step(status(6, failed=True, errors=[CS0029], reloaded=False), console=[CS0029["message"]])])
    result = await _create(wait_for_compile=True)
    _never_claims_zero_errors_unproven(result)
    assert result["verdict"] == "errors", result
    assert result["error_count"] == 1
    assert result["errors"][0]["code"] == "CS0029"
    assert result["errors"][0]["file"] == "Assets/Scripts/Probe.cs"
    assert result["errors"][0]["line"] == 1
    assert len(editor.commands("manage_script")) == 1


@pytest.mark.asyncio
async def test_compile_that_starts_late_is_waited_for(editor_factory):
    editor_factory(
        [step(status(5))] * 6
        + [step(status(6, 5, compiling=True))] * 3
        + [step(status(6, failed=True, errors=[CS0029], reloaded=False))])
    result = await _create(wait_for_compile=True)
    assert result.get("error_count") == 1, result
    assert result["verdict"] == "errors", result
    assert result["errors"][0]["code"] == "CS0029"
    assert result["compilation_observed"] is True


@pytest.mark.asyncio
async def test_no_compile_with_changed_script_is_stale_not_zero_errors(editor_factory):
    """Old bug: no compile within the window still returned error_count 0 (only a note)."""
    editor_factory([step(status(5, changed=1))])
    result = await _create(wait_for_compile=True)
    _never_claims_zero_errors_unproven(result)
    assert result["verdict"] == "stale", result
    assert result["scripts_changed_since_compile"]["count"] == 1
    assert "refresh_unity" in result["note"]


@pytest.mark.asyncio
async def test_no_compile_and_nothing_changed_is_clean_no_compile_needed(editor_factory):
    editor_factory([step(status(5, changed=0))])
    result = await _create(wait_for_compile=True)
    assert result["verdict"] == "clean", result
    assert "no compile needed" in result["note"]


@pytest.mark.asyncio
async def test_stale_console_errors_are_not_this_compiles_errors(editor_factory):
    """Old bug: every console error was returned, including one a later compile fixed."""
    editor_factory(
        [step(status(5), console=[STALE_CONSOLE_ERROR])] * 2
        + [step(status(6, 5, compiling=True), console=[STALE_CONSOLE_ERROR])] * 2
        + [step(status(6, reloaded=False), console=[STALE_CONSOLE_ERROR])] * 2
        + [step(status(6, reloaded=True), console=[STALE_CONSOLE_ERROR])])
    result = await _create(wait_for_compile=True)
    assert result.get("error_count") == 0, result
    assert "CS0103" not in str(result)
    assert result["verdict"] == "clean", result


@pytest.mark.asyncio
async def test_clean_is_only_reported_after_the_domain_reload(editor_factory):
    steps = ([step(status(5))] * 2 + [step(status(6, 5, compiling=True))]
             + [step(status(6, reloaded=False))] * 5 + [step(status(6, reloaded=True))])
    editor = editor_factory(steps)
    result = await _create(wait_for_compile=True)
    # Every "finished but not reloaded" step was read before the verdict.
    assert editor.index >= len(steps), (editor.index, result)
    assert result["verdict"] == "clean", result


@pytest.mark.asyncio
async def test_compile_that_never_finishes_is_a_timeout(editor_factory, monkeypatch):
    if cs is not None:
        monkeypatch.setattr(cs, "MAX_WAIT_S", 0.3)
    editor_factory([step(status(5)), step(status(5))] + [step(status(6, 5, compiling=True))])
    result = await _create(wait_for_compile=True)
    _never_claims_zero_errors_unproven(result)
    assert result["verdict"] == "timeout", result


@pytest.mark.asyncio
async def test_status_unreadable_throughout_is_unknown(editor_factory, monkeypatch):
    """Old bug: unreadable status read as idle -> 'no compile' -> console 0 errors."""
    if cs is not None:
        monkeypatch.setattr(cs, "MAX_WAIT_S", 0.3)
    editor = editor_factory([step(status(5))] + [step(UNREADABLE)])
    result = await _create(wait_for_compile=True)
    _never_claims_zero_errors_unproven(result)
    assert result["verdict"] == "unknown", result
    assert len(editor.commands("manage_script")) == 1


@pytest.mark.asyncio
async def test_plugin_without_the_handler_is_unknown_at_once(editor_factory):
    editor = editor_factory([step(status(5))], unsupported=True)
    result = await _create(wait_for_compile=True)
    assert result["verdict"] == "unknown", result
    assert "does not answer get_compile_status" in result["note"]
    assert len(editor.commands("get_compile_status")) == 1


@pytest.mark.asyncio
async def test_epoch_is_read_before_the_write_and_the_write_is_sent_once(editor_factory):
    editor = editor_factory([step(status(5)), step(status(6, reloaded=True))])
    result = await _create(wait_for_compile=True)
    names = [c[0] for c in editor.calls]
    assert names.index("get_compile_status") < names.index("manage_script")
    writes = editor.commands("manage_script")
    assert len(writes) == 1
    assert writes[0][2].get("retry_on_reload") is False
    assert result["epoch_before"] == 5
    assert result["verdict"] == "clean"


# ── wait_for_compile defaults to true on every script write ───────────────────

@pytest.mark.asyncio
async def test_create_script_waits_by_default(editor_factory):
    editor_factory([step(status(5)), step(status(6, failed=True, errors=[CS0029], reloaded=False))])
    result = await _create()
    assert result["verdict"] == "errors"


@pytest.mark.asyncio
async def test_apply_text_edits_waits_by_default(editor_factory):
    editor_factory([step(status(5)), step(status(6, failed=True, errors=[CS0029], reloaded=False))])
    tools = setup_script_tools()
    resp = await tools["apply_text_edits"](
        DummyContext(), "Assets/Scripts/Probe.cs",
        [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// x\n"}],
        precondition_sha256="abc")
    assert resp["data"]["compile"]["verdict"] == "errors"


@pytest.mark.asyncio
async def test_script_apply_edits_waits_by_default(editor_factory):
    editor_factory([step(status(5)), step(status(6, failed=True, errors=[CS0029], reloaded=False))])
    tools = setup_script_tools()
    resp = await tools["script_apply_edits"](
        DummyContext(), name="Probe", path="Assets/Scripts",
        edits=[{"op": "replace_method", "className": "Probe", "methodName": "M",
                "replacement": "void M() { int x = \"s\"; }"}])
    assert resp["success"] is True, resp
    assert resp["data"]["compile"]["verdict"] == "errors"


@pytest.mark.asyncio
async def test_manage_script_create_waits_by_default(editor_factory):
    editor_factory([step(status(5)), step(status(6, failed=True, errors=[CS0029], reloaded=False))])
    tools = setup_script_tools()
    resp = await tools["manage_script"](
        DummyContext(), action="create", name="Probe", path="Assets/Scripts",
        contents="public class Probe { void M() { int x = \"s\"; } }")
    assert resp["success"] is True, resp
    assert resp["data"]["compile"]["verdict"] == "errors"


@pytest.mark.asyncio
async def test_wait_for_compile_false_reads_no_status(editor_factory):
    editor = editor_factory([step(status(5))])
    tools = setup_script_tools()
    resp = await tools["create_script"](
        DummyContext(), path="Assets/Scripts/Probe.cs", contents="public class Probe {}",
        wait_for_compile=False)
    assert "compile" not in resp.get("data", {})
    assert editor.commands("get_compile_status") == []


# ── read_console ──────────────────────────────────────────────────────────────

async def _read_console(**kwargs):
    from services.tools.read_console import read_console
    return await read_console(ctx=DummyContext(), action="get", types=["error"], **kwargs)


@pytest.mark.asyncio
async def test_read_console_says_compiling_instead_of_a_silent_empty_list(editor_factory):
    editor_factory([step(status(6, 5, compiling=True))])
    resp = await _read_console()
    assert resp["data"]["lines"] == []
    assert resp["compile_state"]["verdict"] == "compiling"
    assert "in progress" in resp["compile_state"]["note"]


@pytest.mark.asyncio
async def test_read_console_after_an_own_tool_write_says_stale(editor_factory):
    """The measured false clean: .cs written with the agent's own file tool, Unity
    unfocused, read_console without refresh -> 0 errors, 3/3."""
    editor_factory([step(status(5, changed=1))])
    resp = await _read_console()
    assert resp["data"]["lines"] == []
    state = resp["compile_state"]
    assert state["verdict"] == "stale"
    assert state["scripts_changed_since_compile"]["paths"] == ["Assets/Changed0.cs"]
    assert "refresh_unity" in state["note"]


@pytest.mark.asyncio
async def test_read_console_before_the_reload_says_pending(editor_factory):
    editor_factory([step(status(6, reloaded=False))])
    resp = await _read_console()
    assert resp["compile_state"]["verdict"] == "pending"


@pytest.mark.asyncio
async def test_read_console_with_unreadable_status_says_unknown(editor_factory):
    editor_factory([step(UNREADABLE)])
    resp = await _read_console()
    assert resp["success"] is True
    assert resp["compile_state"]["verdict"] == "unknown"


@pytest.mark.asyncio
async def test_read_console_after_a_cleared_console_still_reports_failed_compile(editor_factory):
    editor_factory([step(status(6, failed=True, errors=[CS0029], reloaded=False), console=[])])
    resp = await _read_console()
    assert resp["compile_state"]["verdict"] == "errors"
    assert resp["compile_state"]["errors"][0]["code"] == "CS0029"


@pytest.mark.asyncio
async def test_read_console_shape_is_unchanged_when_the_compile_is_final_and_clean(editor_factory):
    editor_factory([step(status(6, reloaded=True), console=["a log line"])])
    resp = await _read_console()
    assert resp == {"success": True, "data": {"lines": ["a log line"]}}


@pytest.mark.asyncio
async def test_read_console_clear_does_not_read_compile_status(editor_factory):
    editor = editor_factory([step(status(6, 5, compiling=True))])
    from services.tools.read_console import read_console
    resp = await read_console(ctx=DummyContext(), action="clear")
    assert "compile_state" not in resp
    assert editor.commands("get_compile_status") == []


# ── refresh_unity ─────────────────────────────────────────────────────────────

async def _refresh(**kwargs):
    from services.tools.refresh_unity import refresh_unity
    resp = await refresh_unity(DummyContext(), **kwargs)
    return resp.model_dump() if hasattr(resp, "model_dump") else resp


@pytest.mark.asyncio
async def test_refresh_with_compile_request_returns_the_compile_errors(editor_factory):
    """Matchday, 11 Sep: refresh_unity returned resulting_state 'compiling' and a
    read_console right after showed 0 errors, 5/5."""
    editor_factory([step(status(5))] * 2 + [step(status(6, 5, compiling=True))] * 3
                   + [step(status(6, failed=True, errors=[CS0029], reloaded=False))])
    resp = await _refresh(compile="request")
    assert resp["success"] is True
    compile_result = resp["data"]["compile"]
    assert compile_result["verdict"] == "errors", compile_result
    assert compile_result["errors"][0]["code"] == "CS0029"


@pytest.mark.asyncio
async def test_refresh_with_compile_request_clean_after_reload(editor_factory):
    editor_factory([step(status(5))] * 2 + [step(status(6, reloaded=False))] * 3
                   + [step(status(6, reloaded=True))])
    resp = await _refresh(compile="request")
    assert resp["data"]["compile"]["verdict"] == "clean"


@pytest.mark.asyncio
async def test_refresh_that_imports_changed_scripts_returns_their_verdict(editor_factory):
    editor_factory([step(status(5))] + [step(status(6, 5, compiling=True))] * 2
                   + [step(status(6, failed=True, errors=[CS0029], reloaded=False))])
    resp = await _refresh(compile="none")
    assert resp["data"]["compile"]["verdict"] == "errors"


@pytest.mark.asyncio
async def test_refresh_without_script_activity_adds_no_compile_field(editor_factory):
    editor_factory([step(status(5))])
    resp = await _refresh(compile="none")
    assert "compile" not in (resp.get("data") or {})


# ── compile_status tool and the pure verdict ──────────────────────────────────

@pytest.mark.asyncio
async def test_compile_status_tool_retries_a_retry_hint_then_reads(editor_factory):
    editor = editor_factory([step(UNREADABLE), step(status(6, reloaded=True))])
    resp = await cs.compile_status(DummyContext())
    assert resp["data"]["verdict"] == "clean"
    assert resp["data"]["status"]["epoch"] == 6
    assert all(c[2].get("retry_on_reload") is False for c in editor.commands("get_compile_status"))


@pytest.mark.asyncio
async def test_compile_status_tool_never_reports_unreadable_as_clean(editor_factory):
    editor_factory([step(UNREADABLE)])
    resp = await cs.compile_status(DummyContext())
    assert resp["data"]["verdict"] == "unknown"


@pytest.mark.parametrize("st, expected", [
    (status(0, reloaded=False), "clean"),
    (status(0, reloaded=False, failed_now=True), "errors"),
    (status(0, reloaded=False, changed=2), "stale"),
    (status(3, updating=True), "pending"),
    (status(4, 3), "compiling"),
    (status(4, failed=True, errors=[CS0029], changed=1), "stale"),
    (status(4, failed=True, errors=[CS0029]), "errors"),
    (status(4, reloaded=False), "pending"),
    (status(4, reloaded=True), "clean"),
])
def test_live_verdict_table(st, expected):
    assert cs.live_verdict(st)["verdict"] == expected


def test_failed_before_tracking_does_not_claim_a_count():
    out = cs.live_verdict(status(0, reloaded=False, failed_now=True))
    assert out["error_count"] is None
    assert "refresh_unity(compile='request')" in out["note"]


# ── a status that cannot be trusted is never clean ────────────────────────────

def _without(st, key):
    return {k: v for k, v in st.items() if k != key}


def _with_changed(value):
    return {**status(2), "scripts_changed_since_compile": value}


MALFORMED = [
    (_with_changed({"count": "not-a-number"}), "scripts_changed_since_compile"),
    (_with_changed({"paths": []}), "scripts_changed_since_compile"),
    (_with_changed({"count": True}), "scripts_changed_since_compile"),
    (_with_changed({"count": -1}), "scripts_changed_since_compile"),
    (_with_changed(None), "scripts_changed_since_compile"),
    (_with_changed([0]), "scripts_changed_since_compile"),
    (_without(status(2), "scripts_changed_since_compile"), "scripts_changed_since_compile"),
    (status(2, 3), "finished_epoch=3 is ahead of epoch=2"),
    ({**status(2), "epoch": "2"}, "epoch='2'"),
    ({**status(2), "finished_epoch": None}, "finished_epoch=None"),
    ({**status(2), "finished_epoch": 2.0}, "finished_epoch=2.0"),
    ({**status(2), "epoch": True, "finished_epoch": 1}, "epoch=True"),
    (_without(status(2), "compilation_failed_now"), "compilation_failed_now"),
    ({**status(2), "reload_done_after_finish": "yes"}, "reload_done_after_finish"),
    (_without(status(2), "is_compiling"), "is_compiling"),
]


@pytest.mark.parametrize("st, field", MALFORMED)
def test_malformed_status_is_unknown_naming_the_field(st, field):
    out = cs.live_verdict(st)
    assert out["verdict"] == "unknown", out
    assert field in out["note"], out
    _never_claims_zero_errors_unproven(out)


@pytest.mark.asyncio
async def test_compile_status_tool_with_malformed_status_is_unknown(editor_factory):
    editor_factory([step(_with_changed({"count": "x"}))])
    resp = await cs.compile_status(DummyContext())
    assert resp["data"]["verdict"] == "unknown"


@pytest.mark.asyncio
async def test_read_console_with_malformed_status_says_unknown(editor_factory):
    editor_factory([step(status(2, 3))])
    resp = await _read_console()
    assert resp["compile_state"]["verdict"] == "unknown"
    assert "finished_epoch" in resp["compile_state"]["note"]


@pytest.mark.asyncio
async def test_wait_with_malformed_poll_status_is_unknown(editor_factory):
    editor = editor_factory([step(status(5)), step({**status(6), "finished_epoch": "6"})])
    result = await _create(wait_for_compile=True)
    assert result["verdict"] == "unknown", result
    assert "finished_epoch" in result["note"]
    assert len(editor.commands("manage_script")) == 1


@pytest.mark.asyncio
async def test_wait_with_malformed_final_scan_is_unknown(editor_factory):
    """The poll reads without the file scan; the final read that decides clean has it."""
    editor_factory([step(status(5)), step(status(6)),
                    step(_without(status(6), "scripts_changed_since_compile"))])
    result = await _create(wait_for_compile=True)
    assert result["verdict"] == "unknown", result
    assert "scripts_changed_since_compile" in result["note"]


# ── an editor restart during the wait is not a clean compile ──────────────────

@pytest.mark.asyncio
async def test_epoch_below_baseline_in_the_poll_is_unknown(editor_factory):
    editor_factory([step(status(5)), step(status(0, reloaded=False))])
    result = await _create(wait_for_compile=True)
    assert result["verdict"] == "unknown", result
    assert "editor restarted during the wait - call compile_status" in result["note"]
    assert result["epoch_before"] == 5


@pytest.mark.asyncio
async def test_epoch_below_baseline_in_the_final_read_is_unknown(editor_factory):
    """Restart between the finishing poll and the final scan read."""
    editor_factory([step(status(5)), step(status(6)), step(status(1))])
    result = await _create(wait_for_compile=True)
    assert result["verdict"] == "unknown", result
    assert "editor restarted during the wait" in result["note"]


@pytest.mark.asyncio
async def test_epoch_reset_direct_wait_after_restart_is_unknown(editor_factory):
    editor_factory([step(status(2))])
    result = await cs.await_compile_verdict(None, cs.CompileBaseline(5), max_wait_s=0.2, start_window_s=0)
    assert result["verdict"] == "unknown", result


@pytest.mark.asyncio
async def test_without_a_baseline_a_low_epoch_keeps_the_start_window_rule(editor_factory):
    editor_factory([step(status(0, reloaded=False))])
    result = await cs.await_compile_verdict(None, cs.CompileBaseline(None, "unreadable: x"),
                                            max_wait_s=0.3, start_window_s=0)
    assert result["verdict"] == "clean", result
    assert "no compile needed" in result["note"]


# ── Unity's live compile-failed flag outranks a later clean epoch ─────────────

def test_failed_now_after_a_clean_tracked_compile_is_errors():
    out = cs.live_verdict(status(7, reloaded=True, failed_now=True))
    assert out["verdict"] == "errors", out
    assert out["error_count"] is None
    assert out["errors"] == []
    assert "refresh_unity(compile='request')" in out["note"]


def test_failed_now_waits_for_the_reload_first():
    """Inside compilationFinished the flag still holds the previous compile's
    result (CompileTracker.OnCompilationFinished), so before the reload it is pending."""
    assert cs.live_verdict(status(7, reloaded=False, failed_now=True))["verdict"] == "pending"


def test_failed_now_with_a_failed_tracked_compile_keeps_its_errors():
    out = cs.live_verdict(status(7, failed=True, errors=[CS0029], failed_now=True))
    assert out["verdict"] == "errors"
    assert out["error_count"] == 1
    assert out["errors"][0]["code"] == "CS0029"


@pytest.mark.asyncio
async def test_skipped_failed_epoch_masked_by_a_later_partial_compile_is_errors(editor_factory):
    """Baseline 5; epoch 6 (this write) failed; epoch 7 rebuilt another assembly
    clean before the first poll. Only Unity's live flag still shows the failure."""
    editor_factory([step(status(5)), step(status(7, reloaded=True, failed_now=True))])
    result = await _create(wait_for_compile=True)
    assert result["verdict"] == "errors", result
    assert result["error_count"] is None


@pytest.mark.asyncio
async def test_no_compile_needed_is_not_clean_while_unity_reports_failure(editor_factory):
    editor_factory([step(status(5, reloaded=True, failed_now=True))])
    result = await _create(wait_for_compile=True)
    assert result["verdict"] == "errors", result
    assert "no compile needed" not in result.get("note", "")


# ── a script write goes to Unity once, even after a reloading reply ───────────

RELOADING = {"success": False, "error": "Unity is reloading; please retry", "hint": "retry",
             "data": {"reason": "reloading", "retry_after_ms": 250}}


@pytest.mark.asyncio
async def test_reloading_reply_to_a_write_is_not_resent(editor_factory):
    editor = editor_factory([step(status(5))], write_reply=RELOADING)
    tools = setup_script_tools()
    resp = await tools["create_script"](
        DummyContext(), path="Assets/Scripts/Probe.cs", contents="public class Probe {}")
    assert resp["success"] is False, resp
    assert len(editor.commands("manage_script")) == 1


@pytest.mark.asyncio
async def test_reloading_reply_to_manage_script_create_is_not_resent(editor_factory):
    editor = editor_factory([step(status(5))], write_reply=RELOADING)
    tools = setup_script_tools()
    await tools["manage_script"](
        DummyContext(), action="create", name="Probe", path="Assets/Scripts",
        contents="public class Probe {}")
    assert len([c for c in editor.commands("manage_script") if c[1].get("action") == "create"]) == 1


# ── refresh_unity always says what it knows about the compile ─────────────────

@pytest.mark.asyncio
async def test_refresh_with_unreadable_status_after_it_is_unknown(editor_factory):
    editor_factory([step(status(5))] + [step(UNREADABLE)])
    resp = await _refresh(compile="none")
    assert resp["success"] is True
    assert resp["data"]["compile"]["verdict"] == "unknown", resp


@pytest.mark.asyncio
async def test_refresh_with_status_unreadable_throughout_is_unknown(editor_factory):
    editor_factory([step(UNREADABLE)])
    resp = await _refresh(compile="none")
    assert resp["data"]["compile"]["verdict"] == "unknown", resp


@pytest.mark.asyncio
async def test_refresh_with_malformed_status_after_it_is_unknown(editor_factory):
    editor_factory([step(status(5)), step(_with_changed({"count": "x"}))])
    resp = await _refresh(compile="none")
    assert resp["data"]["compile"]["verdict"] == "unknown", resp


@pytest.mark.asyncio
async def test_refresh_after_an_editor_restart_is_unknown(editor_factory):
    editor_factory([step(status(5)), step(status(0, reloaded=False))])
    resp = await _refresh(compile="none")
    assert resp["data"]["compile"]["verdict"] == "unknown", resp
    assert "editor restarted" in resp["data"]["compile"]["note"]


@pytest.mark.asyncio
async def test_refresh_without_script_activity_still_reports_a_failed_compile(editor_factory):
    editor_factory([step(status(5, failed=True, errors=[CS0029], reloaded=False))])
    resp = await _refresh(compile="none")
    assert resp["data"]["compile"]["verdict"] == "errors", resp


@pytest.mark.asyncio
async def test_refresh_without_a_baseline_reports_even_a_clean_verdict(editor_factory):
    editor_factory([step(UNREADABLE)] * 2 + [step(status(6, reloaded=True))])
    resp = await _refresh(compile="none")
    assert resp["data"]["compile"]["verdict"] == "clean", resp


@pytest.mark.asyncio
async def test_refresh_with_plugin_without_the_handler_is_unknown(editor_factory):
    editor_factory([step(status(5))], unsupported=True)
    resp = await _refresh(compile="none")
    assert resp["data"]["compile"]["verdict"] == "unknown", resp


@pytest.mark.asyncio
async def test_refresh_failures_after_the_send_carry_an_unknown_verdict(editor_factory, monkeypatch):
    import services.tools.refresh_unity as refresh_mod
    editor_factory([step(status(5))])
    real_send = refresh_mod.unity_transport.send_with_unity_instance

    async def send(send_fn, instance, command, params, **kwargs):
        if command == "refresh_unity":
            return {"success": False, "error": "Unity did not respond within 30s", "hint": "retry"}
        return await real_send(send_fn, instance, command, params, **kwargs)

    monkeypatch.setattr(refresh_mod.unity_transport, "send_with_unity_instance", send)
    resp = await _refresh(compile="none", wait_for_ready=False)
    assert resp["success"] is False
    assert resp["data"]["compile"]["verdict"] == "unknown", resp


# ── registration: ledger and profiles ─────────────────────────────────────────

def test_compile_status_is_a_read_in_the_ledger():
    from services.registry.tool_actions import READ, classify
    assert classify("compile_status", {}) == READ


def test_compile_status_is_in_core_and_on_default_and_gamachine_profiles():
    from services.registry import DEFAULT_ENABLED_GROUPS, get_registered_tools
    from transport import tool_profiles
    from transport.tool_profiles import DEFAULT_PROFILE, PROFILES
    entry = next(t for t in get_registered_tools() if t["name"] == "compile_status")
    assert entry["group"] == "core"
    assert entry["unity_target"] == "read_console"
    assert entry["kwargs"]["annotations"].readOnlyHint is True
    before = tool_profiles.server_enabled_groups()
    try:
        tool_profiles.set_server_enabled_groups(DEFAULT_ENABLED_GROUPS)
        assert DEFAULT_PROFILE.allows({"core"})
        assert PROFILES["gamachine"].allows({"core"})
    finally:
        tool_profiles.set_server_enabled_groups(before)
