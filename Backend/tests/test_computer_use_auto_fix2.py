"""Regression coverage for automatic computer-use guards, with fakes only."""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult

from computer_use import gate, platform, policy, server
from computer_use.platform.macos import MacPlatform
from computer_use.platform.windows import WindowsPlatform
from computer_use.standalone import config
from computer_use.standalone import __main__ as entry
from computer_use.standalone.approvals import LocalApprovals


APP = "win:c:" + chr(92) + "apps" + chr(92) + "editor.exe"


@pytest.fixture
def win(monkeypatch):
    native = SimpleNamespace(set_dpi_awareness=lambda: None, foreground_window=lambda: 31,
                             window_pid=lambda hwnd: 77, parent_pids=lambda: {})
    instance = WindowsPlatform(native)
    instance.identify = AsyncMock(return_value=(APP, "Editor", None))
    monkeypatch.setattr(platform, "current", lambda: instance)
    return instance


@pytest.fixture
def handler(win):
    driver = SimpleNamespace(binary="fake", socket="fake", close=AsyncMock(),
                             call=AsyncMock(return_value=CallToolResult(content=[])))
    approvals = SimpleNamespace(preflight=lambda: None, accepts_confirm=True,
                                request=AsyncMock(return_value={"approved": True}),
                                batch_stop=AsyncMock(return_value=None))
    return server.ComputerServer(driver, approvals, runtime=AsyncMock(return_value=("fake", "fake")))


@pytest.mark.parametrize("foreground", [31, 32, None])
def test_panel_fallback_requires_native_foreground(win, monkeypatch, foreground):
    win.win32.foreground_window = lambda: foreground
    win.panel_windows = AsyncMock(return_value=[{"window_id": 31}])
    win.top_window_is = AsyncMock(return_value=True)
    win.frontmost_pid = AsyncMock(return_value=77)
    monkeypatch.setattr(server.asyncio, "sleep", AsyncMock())
    driver = SimpleNamespace(call=AsyncMock(return_value=CallToolResult(content=[])))
    assert (asyncio.run(server._front_guard(driver, 77, 31)) is None) is (foreground == 31)


def test_native_foreground_errors_fail_closed(win):
    win.win32.foreground_window = lambda: (_ for _ in ()).throw(OSError("unavailable"))
    assert win.foreground_window_is(31) is False
    assert MacPlatform().foreground_window_is(31) is True


def test_cache_parser_preserves_rows_and_quoted_names(handler):
    tree = ('- [2] Button "Send"\n'
            '- [1] Button "Pencil [2] Button "Settings""\n'
            '- [2] Button "Pencil"\n'
            '  - [3] Push Button "Say "hello"" [enabled]\n'
            '- [4] Button ""\n'
            'Text [5] Button "Send"\n'
            '- [6] Button no quotes')
    handler._cache_snapshot(CallToolResult(content=[], structured_content={
        "snapshot_id": "s1", "tree_markdown": tree}), {"pid": 77, "window_id": 31})
    for row, name in [(1, 'Pencil [2] Button "Settings"'), (2, "Send"),
                      (3, 'Say "hello"'), (4, ""), (5, None), (6, None)]:
        assert handler._element_name({"pid": 77, "window_id": 31, "element_token": f"s1:{row}"}) == name


@pytest.mark.parametrize("split", [False, True])
def test_batch_observation_populates_cache(handler, split):
    snapshot = {"snapshot_id": "observed", "tree_markdown": '- [1] Push Button "Send"'}
    handler.driver.call.return_value = CallToolResult(content=[], structured_content={
        "ok": True, "steps": [{"ok": True}], "observation": snapshot})
    steps = [{"press_key": {"key": "tab"}}] * (2 if split else 1)
    result = asyncio.run(handler.call("computer_run_steps", {
        "pid": 77, "window_id": 31, "steps": steps, "observe": True}))
    assert not result.is_error
    assert handler._element_name({"pid": 77, "window_id": 31, "element_token": "observed:1"}) == "Send"
    asyncio.run(handler.call("computer_click", {"pid": 77, "window_id": 31, "element_token": "observed:1"}))
    params = handler.approvals.request.call_args.args[1]
    assert params["element_name"] == "Send" and params["critical_reason"]


def test_driver_replacement_invalidates_all_snapshots(handler, monkeypatch):
    handler._cache_snapshot(CallToolResult(content=[], structured_content={
        "snapshot_id": "s1", "tree_markdown": '- [1] Button "Pencil"'}), {"pid": 77, "window_id": 31})
    old = handler.driver
    replacement = SimpleNamespace(binary="new", socket="new")
    handler.runtime.return_value = ("new", "new")
    monkeypatch.setattr(server, "DriverClient", lambda *args: replacement)
    assert asyncio.run(handler._runtime_driver()) is replacement
    old.close.assert_awaited_once()
    assert not handler._snapshots
    assert handler._element_name({"pid": 77, "element_token": "s1:1"}) is None


@pytest.mark.parametrize("name", ["Go\u0308nder", "Se\u200bnd", "Ｓｅｎｄ", "SendButton", "Send_Button",
                                  "Send-Button", "Send.Button", "gönderButton"])
@pytest.mark.parametrize("tool", ["click", "double_click", "right_click", "set_value", "type_text", "press_key"])
def test_normalized_critical_names(win, name, tool):
    assert policy.critical_reason("computer_" + tool, {"element_token": "s1:1"},
                                  app_id=APP, resolve=lambda target: (True, name))


@pytest.mark.parametrize("name", ["Silver", "Pencil", "Settings", "Close Tab", "Kapat"])
def test_harmless_names_stay_noncritical(win, name):
    assert policy.critical_reason("computer_click", {"name": name}, app_id=APP,
                                  resolve=lambda target: (True, target["name"])) == ""


@pytest.mark.parametrize("target", [{"element_token": "missing:1"}, {"role": "Button"},
                                    {"name": "Send"}, {"target": {"name": "Send"}}])
def test_element_target_resolution_is_required(win, target):
    assert policy.critical_reason("computer_click", target, app_id=APP, resolve=lambda target: (False, None))
    assert policy.critical_reason("computer_click", {"x": 1, "y": 2}, app_id=APP,
                                  resolve=lambda target: (False, None)) == ""


def test_batch_targets_inherit_context_and_use_each_steps_name(win):
    seen = []
    def resolve(target):
        seen.append(target)
        return True, "Send" if target.get("element_token") == "s1:2" else "Pencil"
    args = {"pid": 77, "window_id": 31, "element_token": "s1:1", "steps": [
        {"tool": "click", "args": {}}, {"click": {"element_token": "s1:2"}}]}
    assert policy.critical_reason("computer_run_steps", args, app_id=APP, resolve=resolve)
    assert seen == [{"pid": 77, "window_id": 31, "element_token": "s1:1"},
                    {"pid": 77, "window_id": 31, "element_token": "s1:2"}]


@pytest.mark.parametrize("target, expected", [({"name": "Send"}, True),
                                              ({"name": "Pencil"}, False),
                                              ({"role": "Button"}, False),
                                              ({"element_token": "missing:1"}, True)])
def test_server_resolves_named_and_unknown_step_targets(handler, target, expected):
    result = asyncio.run(handler.call("computer_run_steps", {
        "pid": 77, "window_id": 31, "steps": [{"click": target}]}))
    assert not result.is_error
    assert bool(handler.approvals.request.call_args.args[1]["critical_reason"]) is expected


@pytest.mark.parametrize("target, expected", [
    ({"target": {"role": "Button", "name": "Send"}}, "Send"),
    ({"role": "Button", "target": {"name": "Send"}}, "Send"),
    ({"role": "Button", "name": ""}, ""),
])
def test_server_resolves_nested_and_empty_names(handler, target, expected):
    assert handler._resolve_target(target) == (True, expected)
    assert bool(policy.critical_reason("computer_click", target, app_id=APP,
                                       resolve=handler._resolve_target)) is bool(expected)


@pytest.mark.parametrize("exe", ["iexplore.exe", "arc.exe", "zoom.exe", "skype.exe", "wechat.exe", "mailspring.exe"])
def test_additional_read_only_apps_require_confirmation(win, exe):
    app = APP.replace("editor.exe", exe)
    assert win.read_only_app(app)
    assert policy.critical_reason("computer_type_text", {"text": "hello"}, app_id=app,
                                  resolve=lambda target: (True, None))


@pytest.mark.parametrize("tool, args, expected", [
    ("computer_hotkey", {"keys": "ctrl++"}, {"keys": ["ctrl", "+"]}),
    ("computer_hotkey", {"keys": ["ctrl++"]}, {"keys": ["ctrl", "+"]}),
    ("computer_hotkey", {"keys": "+"}, {"keys": ["+"]}),
    ("computer_press_key", {"key": "ctrl++"}, {"key": "+", "modifiers": ["ctrl"]}),
])
def test_plus_key_survives_normalization(win, tool, args, expected):
    result = policy.validate_call(tool, {"pid": 77, **args})
    assert isinstance(result, policy.Validated)
    assert result.args == {"pid": 77, **expected}


def hook_settings(matcher="mcp__gamachineComputerLocal__.*", command="bash guard-computer-use-confirm.sh"):
    return {"hooks": {"PreToolUse": [{"matcher": matcher, "hooks": [{"command": command}]}]}}


@pytest.mark.parametrize("settings, expected", [
    (hook_settings(), True),
    (hook_settings("mcp__gamachineComputerLocal__computer_click"), True),
    (hook_settings("mcp__gamachineComputerLocal__"), False),
    (hook_settings("Bash"), False), (hook_settings("["), False),
    (hook_settings(command="another-hook.sh"), False),
    ({"hooks": {"PostToolUse": hook_settings()["hooks"]["PreToolUse"]}}, False),
    ({"hooks": {"PreToolUse": [None, {"matcher": 1}]}}, False),
    ({"hooks": None}, False), ([], False),
])
def test_hook_check_matches_only_pretool_confirm_commands(tmp_path, settings, expected):
    from computer_use.standalone import hookcheck
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    assert hookcheck.confirm_hook_installed([path]) is expected


def test_hook_check_handles_missing_invalid_and_override_files(tmp_path, monkeypatch):
    from computer_use.standalone import hookcheck
    missing = tmp_path / "missing.json"
    bad = tmp_path / "bad.json"
    bad.write_text("invalid json", encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text(json.dumps(hook_settings()), encoding="utf-8")
    assert hookcheck.confirm_hook_installed([missing, bad]) is False
    monkeypatch.setenv("GAMACHINE_CU_CLAUDE_SETTINGS", os.pathsep.join(map(str, [missing, bad, good])))
    assert hookcheck.confirm_hook_installed() is True
    monkeypatch.setenv("GAMACHINE_CU_CLAUDE_SETTINGS", "")
    assert hookcheck.confirm_hook_installed() is False


@pytest.mark.parametrize("hook", [False, True])
@pytest.mark.parametrize("confirmed", [False, True, "yes", 1])
def test_critical_confirmation_requires_hook_and_boolean(win, tmp_path, hook, confirmed):
    journal = tmp_path / "journal.jsonl"
    approval = LocalApprovals(None, SimpleNamespace(registered=True, halted_at=None),
                              journal=lambda: journal, confirm_available=hook)
    result = asyncio.run(approval.request("computer_click", {
        "bundle_id": APP, "critical_reason": "Send", "confirmed": confirmed, "confirm_text": "Send reply"}))
    expected = hook and confirmed is True
    assert result["approved"] is expected
    row = json.loads(journal.read_text(encoding="utf-8"))
    assert (row["decision"] == "confirmed") is expected
    if expected:
        assert row["confirm"] == "Send reply"
    else:
        assert "confirm" not in row


@pytest.mark.parametrize("hook", [False, True])
def test_explicit_confirmation_tool_requires_hook(win, tmp_path, hook):
    approval = LocalApprovals(None, SimpleNamespace(registered=True, halted_at=None),
                              journal=lambda: tmp_path / "journal.jsonl", confirm_available=hook)
    assert asyncio.run(approval.request("computer_confirm_risky_step", {
        "bundle_id": APP, "summary": "Send reply"}))["approved"] is hook


@pytest.mark.parametrize("registered", [False, True])
def test_halt_cannot_be_cleared_by_confirm(win, tmp_path, registered):
    hotkey = SimpleNamespace(registered=registered, halted_at=0.0)
    approval = LocalApprovals(None, hotkey, journal=lambda: tmp_path / "journal.jsonl", confirm_available=True)
    result = asyncio.run(approval.request("computer_click", {"bundle_id": APP, "confirmed": True}))
    assert result == {"approved": False, "error": gate.HALTED_REASON
                      + " Bilgisayar kullanımını sürdürmek için sunucuyu yeniden başlat (Claude Code: /mcp)."}
    assert hotkey.halted_at == 0.0


def test_dead_hotkey_interrupts_split_batch(handler, monkeypatch, tmp_path):
    hotkey = SimpleNamespace(registered=True, halted_at=None)
    handler.approvals = LocalApprovals(None, hotkey, journal=lambda: tmp_path / "journal.jsonl")
    monkeypatch.setattr(config, "load", lambda: {"enabled": True})
    async def send(*args):
        hotkey.registered = False
        return CallToolResult(content=[], structured_content={"ok": True, "steps": [{"ok": True}]})
    handler.driver.call.side_effect = send
    result = asyncio.run(handler.call("computer_run_steps", {
        "pid": 77, "steps": [{"type_text": {"text": "first"}}, {"type_text": {"text": "second"}}]}))
    assert result.is_error
    report = json.loads(result.content[0].text)
    assert report["stop_reason"] == "halted" and report["executed"] == 1
    handler.driver.call.assert_awaited_once()


def test_journal_adds_confirmation_only_if_provided(tmp_path):
    path = tmp_path / "journal.jsonl"
    gate.actions_log_append(path, {"tool": "computer_click"})
    gate.actions_log_append(path, {"tool": "computer_click", "confirm": "Send reply"})
    first, second = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert "confirm" not in first and second["confirm"] == "Send reply"


def test_backend_still_refuses_confirm_without_starting_runtime():
    runtime = AsyncMock(side_effect=AssertionError("runtime must not start"))
    handler = server.ComputerServer(runtime=runtime)
    result = asyncio.run(handler.call("computer_click", {"pid": 77, "confirm": "Send reply"}))
    assert result.is_error and result.content[0].text == "Forbidden argument: confirm"
    runtime.assert_not_awaited()


def test_server_passes_stripped_confirmation_without_mutating_args(handler):
    args = {"pid": 77, "text": "hello", "confirm": "  Send reply  "}
    result = asyncio.run(handler.call("computer_type_text", args))
    assert not result.is_error
    params = handler.approvals.request.call_args.args[1]
    assert params["confirmed"] is True and params["confirm_text"] == "Send reply"
    handler.driver.call.assert_awaited_once_with("type_text", {"pid": 77, "text": "hello"})
    assert args["confirm"] == "  Send reply  "


@pytest.mark.parametrize("hook", [False, True])
def test_entry_point_sets_hook_availability_and_instructions(monkeypatch, tmp_path, hook):
    from computer_use.standalone import hookcheck
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "log_path", lambda: tmp_path / "server.log")
    monkeypatch.setattr(hookcheck, "confirm_hook_installed", lambda: hook)
    monkeypatch.setattr(entry, "_startup_refusal", lambda *args: None)
    hotkey = SimpleNamespace(stop=lambda: None)
    monkeypatch.setattr(entry, "HaltHotkey", lambda: hotkey)
    monkeypatch.setattr(entry, "SessionLock", lambda: SimpleNamespace(close=lambda: None))
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run=lambda **kwargs: None)
    monkeypatch.setattr(entry, "create_server", create)
    entry.main([])
    assert captured["approvals"].confirm_available is hook
    assert ("Onay hook'u kurulu değil: kritik işlemler bu oturumda yapılamaz."
            in captured["instructions_extra"]) is (not hook)
