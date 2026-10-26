"""Standalone automatic policy and proxy boundaries, using fakes only."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult

from computer_use import platform, policy, server
from computer_use.platform.macos import MacPlatform
from computer_use.platform.windows import WindowsPlatform


APP = "win:" + "c:" + chr(92) + "apps" + chr(92) + "editor.exe"


@pytest.fixture
def win(monkeypatch):
    native = SimpleNamespace(set_dpi_awareness=lambda: None, foreground_window=lambda: 31,
                             window_pid=lambda hwnd: 77, image_path=lambda pid: "editor.exe",
                             parent_pids=lambda: {})
    instance = WindowsPlatform(native)
    monkeypatch.setattr(platform, "current", lambda: instance)
    return instance


@pytest.mark.parametrize("report, expected", [
    ({"exact_window_effect": {"verified": True}}, True),
    ({"exact_window_effect": {"verified": 1}}, False),
    ({"exact_window_effect": None}, False), ({}, False), (None, False),
    ({"landed_on_target": True}, False),
])
def test_macos_front_verification_keeps_existing_contract(report, expected):
    assert MacPlatform().front_verified(report, 77, 31) is expected
    assert MacPlatform().read_only_app(APP) is False


@pytest.mark.parametrize("change", [None, "landed", "now", "target", "native", "pid", "exception", "missing"])
def test_windows_front_verification_requires_report_and_native_identity(win, change):
    report = {"landed_on_target": True, "now_fg_hwnd": "0x1f", "target_hwnd": "0x1f"}
    if change == "landed":
        report["landed_on_target"] = 1
    if change in {"now", "target"}:
        report[change + "_hwnd" if change == "target" else "now_fg_hwnd"] = "0x20"
    if change == "native":
        win.win32.foreground_window = lambda: 32
    if change == "exception":
        win.win32.foreground_window = lambda: (_ for _ in ()).throw(OSError())
    if change == "missing":
        report.pop("now_fg_hwnd")
    assert win.front_verified(report, 78 if change == "pid" else 77, 31) is (change is None)


def test_windows_front_guard_accepts_matching_report(win):
    driver = SimpleNamespace(call=AsyncMock(return_value=CallToolResult(
        content=[], structured_content={"landed_on_target": True, "now_fg_hwnd": "0x1f", "target_hwnd": "0x1f"})))
    assert asyncio.run(server._front_guard(driver, 77, 31)) is None
    driver.call.assert_awaited_once_with("bring_to_front", {"pid": 77, "window_id": 31})


@pytest.mark.parametrize("name", [
    "msedge.exe", "chrome.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe",
    "outlook.exe", "olk.exe", "hxoutlook.exe", "thunderbird.exe", "whatsapp.exe",
    "whatsapp.root.exe", "telegram.exe", "discord.exe", "slack.exe", "ms-teams.exe",
    "teams.exe", "signal.exe", "messenger.exe",
])
def test_communication_and_browser_apps_are_read_only(win, name):
    app = APP.replace("editor.exe", name)
    assert win.read_only_app(app)
    assert not win.read_only_app(name)
    assert not win.read_only_app(APP)


@pytest.mark.parametrize("name", ["exodus.exe", "ledger live.exe", "trezor suite.exe", "electrum.exe",
                                  "atomic wallet.exe", "binance.exe", "tws.exe", "terminal64.exe"])
def test_finance_apps_are_hard_denied(win, name):
    assert win.is_hard_denied(APP.replace("editor.exe", name), None)


@pytest.mark.parametrize("tool, args, expected", [
    ("computer_press_key", {"key": "alt+f4"}, {"key": "f4", "modifiers": ["alt"]}),
    ("computer_press_key", {"key": "f4", "modifiers": ["alt+shift"]}, {"key": "f4", "modifiers": ["alt", "shift"]}),
    ("computer_hotkey", {"keys": ["alt+f4"]}, {"keys": ["alt", "f4"]}),
    ("computer_hotkey", {"keys": ["+"]}, {"keys": ["+"]}),
    ("computer_press_key", {"key": "+"}, {"key": "+"}),
])
def test_key_tokens_expand_without_changing_input(win, tool, args, expected):
    original = repr(args)
    result = policy.validate_call(tool, {"pid": 77, **args})
    assert isinstance(result, policy.Validated)
    assert result.args == {"pid": 77, **expected}
    assert repr(args) == original
    if "alt+f4" in str(args):
        assert policy.risky_reason(tool, args)


@pytest.mark.parametrize("name", [
    "send", "GÖNDER", "delete", "sil", "remove", "kaldır", "Buy now", "Satın al", "purchase",
    "pay", "ÖDE", "order", "sipariş", "checkout", "confirm", "onayla", "submit", "publish",
    "yayınla", "post", "paylaş", "share", "transfer", "uninstall",
])
def test_critical_button_names_match_unicode_words(win, name):
    assert policy.critical_reason("computer_click", {}, app_id=APP, element_name=name)
    assert policy.critical_reason("computer_run_steps", {"steps": [{"set_value": {"value": "x"}}]},
                                  app_id=APP, element_name=name)


@pytest.mark.parametrize("name", ["Silver", "Pencil", "Settings", "Close Tab", "Sending options"])
def test_ordinary_button_names_do_not_match(win, name):
    assert policy.critical_reason("computer_click", {}, app_id=APP, element_name=name) == ""


@pytest.mark.parametrize("tool", ["click", "double_click", "right_click", "type_text", "set_value",
                                 "press_key", "hotkey", "drag", "clipboard_write"])
def test_read_only_app_input_requires_confirmation(win, tool):
    app = APP.replace("editor.exe", "chrome.exe")
    assert policy.critical_reason("computer_" + tool, {}, app_id=app, element_name=None)
    assert policy.critical_reason("computer_run_steps", {"steps": [{tool: {}}]}, app_id=app, element_name=None)


@pytest.mark.parametrize("tool", ["scroll", "list_windows", "window_state", "bring_to_front", "zoom"])
def test_read_only_app_observation_is_not_critical(win, tool):
    assert policy.critical_reason("computer_" + tool, {},
                                  app_id=APP.replace("editor.exe", "chrome.exe"), element_name="Send") == ""


class Approvals:
    def __init__(self, refusal=None):
        self.refusal = refusal
        self.params = []

    def preflight(self):
        return self.refusal

    async def request(self, tool, params):
        self.params.append(params)
        return {"approved": True}


@pytest.fixture
def handler(win, monkeypatch):
    win.identify = AsyncMock(return_value=(APP, "Editor", None))
    driver = SimpleNamespace(binary="fake", socket="fake", call=AsyncMock(return_value=CallToolResult(content=[])))
    monkeypatch.setattr(server, "DriverClient", lambda *a: driver)
    return server.ComputerServer(approvals=Approvals(), runtime=AsyncMock(return_value=("fake", "fake")))


@pytest.mark.parametrize("confirm, expected", [(None, False), (True, False), ("", False), ("   ", False),
                                            ("x" * 501, False), (" x ", True), ("x" * 500, True)])
def test_confirm_is_validated_and_never_forwarded(handler, confirm, expected):
    args = {"pid": 77, "text": "hello", "confirm": confirm}
    result = asyncio.run(handler.call("computer_type_text", args))
    assert not result.is_error
    assert handler.approvals.params[-1]["confirmed"] is expected
    assert handler.driver.call.call_args.args == ("type_text", {"pid": 77, "text": "hello"})
    assert "confirm" in args


def test_preflight_refuses_before_validation_or_runtime():
    runtime = AsyncMock(side_effect=AssertionError("runtime must not start"))
    approvals = Approvals("disabled")
    result = asyncio.run(server.ComputerServer(approvals=approvals, runtime=runtime).call("computer_click", {"pid": 77}))
    assert result.is_error and result.content[0].text == "disabled"
    runtime.assert_not_awaited()
    assert not approvals.params


def test_snapshot_names_are_used_for_single_and_batch_calls(handler):
    async def scenario():
        handler.driver = await handler._runtime_driver()
        handler.driver.call.return_value = CallToolResult(content=[], structured_content={
            "snapshot_id": "s1", "tree_markdown": '- [59] Button "Gönder" [actions=[invoke]]\n- [14] Button "Pencil" [id=X actions=[toggle]]'})
        assert not (await handler.call("computer_window_state", {"pid": 77, "window_id": 31})).is_error
        await handler.call("computer_click", {"pid": 77, "window_id": 31, "element_token": "s1:59"})
        assert handler.approvals.params[-1]["element_name"] == "Gönder"
        assert handler.approvals.params[-1]["critical_reason"]
        await handler.call("computer_run_steps", {"pid": 77, "window_id": 31, "confirm": "Send the message", "steps": [
            {"click": {"element_token": "s1:59"}}]})
        params = handler.approvals.params[-1]
        assert params["confirmed"] and params["element_name"] == "Gönder" and params["critical_reason"]
        assert "confirm" not in handler.driver.call.call_args.args[1]
        await handler.call("computer_run_steps", {"pid": 77, "window_id": 31, "steps": [
            {"scroll": {"element_token": "s1:59", "direction": "down"}}]})
        assert handler.approvals.params[-1]["critical_reason"] == ""
        await handler.call("computer_click", {"pid": 77, "window_id": 31, "element_token": "s1:14"})
        assert handler.approvals.params[-1]["element_name"] == "Pencil"
        assert handler.approvals.params[-1]["critical_reason"] == ""
        await handler.call("computer_run_steps", {"pid": 77, "window_id": 31, "steps": [
            {"click": {"element_token": "s1:14"}}]})
        assert handler.approvals.params[-1]["element_name"] == "Pencil"
        assert handler.approvals.params[-1]["critical_reason"] == ""
        await handler.call("computer_click", {"pid": 78, "window_id": 31, "element_token": "s1:59"})
        assert handler.approvals.params[-1]["element_name"] is None
        await handler.call("computer_click", {"pid": 77, "window_id": 32, "element_token": "s1:59"})
        assert handler.approvals.params[-1]["element_name"] is None
    asyncio.run(scenario())


def test_snapshot_cache_keeps_latest_per_window_and_sixteen_total(handler):
    def cache(snapshot, window, error=False):
        result = CallToolResult(content=[], is_error=error, structured_content={
            "snapshot_id": snapshot, "tree_markdown": '- [1] Button "Delete"'})
        handler._cache_snapshot(result, {"pid": 77, "window_id": window})
    cache("old", 31)
    cache("new", 31)
    cache("failed", 31, error=True)
    assert len(handler._snapshots) == 1
    assert handler._element_name({"pid": 77, "element_token": "old:1"}) is None
    assert handler._element_name({"pid": 77, "element_token": "new:1"}) == "Delete"
    for window in range(32, 48):
        cache(str(window), window)
    assert len(handler._snapshots) == 16
    assert handler._element_name({"pid": 77, "element_token": "new:1"}) is None
