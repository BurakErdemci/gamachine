"""Windows launch, frame-host identity and canvas delivery with no desktop input."""
import asyncio
from copy import deepcopy
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult

from computer_use import platform, policy, server
from computer_use.platform import macos, windows
from computer_use.platform.windows import WindowsPlatform
from computer_use.standalone.approvals import LocalApprovals


CALCULATOR = "aumid:Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
DRAG = {"from_x": 10, "from_y": 20, "to_x": 40, "to_y": 50}


class FakeWin32:
    def __init__(self):
        self.paths = {
            41: chr(92).join(("C:", "Apps", "MyEditor.exe")),
            42: chr(92).join(("C:", "Windows", "ApplicationFrameHost.exe")),
            43: chr(92).join(("C:", "Apps", "Calculator.exe")),
        }
        self.levels = {os.getpid(): 0x2000, 41: 0x2000}
        self.children = {}
        self.pids = {}
        self.info = {}

    def set_dpi_awareness(self):
        pass

    @staticmethod
    def window(**changes):
        return {"visible": True, "cloaked": False, "minimized": False, "owner": 0, **changes}

    def image_path(self, pid):
        return self.paths.get(pid)

    def integrity(self, pid):
        return self.levels.get(pid)

    def parent_pids(self):
        return {}

    def child_windows(self, hwnd):
        return self.children.get(hwnd, [])

    def window_pid(self, hwnd):
        return self.pids.get(hwnd)

    def window_info(self, hwnd):
        return self.info.get(hwnd)

    def foreground_window(self):
        return 100

    def top_level_windows(self):
        return [100]


@pytest.fixture
def win(monkeypatch):
    facts = FakeWin32()
    facts.children[100] = [101]
    facts.pids.update({10: 41, 100: 42, 101: 43})
    facts.info[100] = facts.window()
    facts.info[101] = facts.window(class_name="Windows.UI.Core.CoreWindow")
    facts.levels.update({42: 0x2000, 43: 0x2000, 44: 0x2000})
    facts.paths[44] = facts.paths[41]
    instance = WindowsPlatform(facts)
    monkeypatch.setattr(platform, "current", lambda: instance)
    return instance


@pytest.fixture
def proxy(monkeypatch):
    driver = SimpleNamespace(
        installed_app_name=AsyncMock(return_value="Calculator"),
        call=AsyncMock(return_value=CallToolResult(
            content=[], structured_content={"ok": True, "steps": [{"ok": True}]})))
    approvals = SimpleNamespace(
        preflight=lambda: None, request=AsyncMock(return_value={"approved": True}),
        dispatch_refusal=AsyncMock(return_value=None), batch_stop=AsyncMock(return_value=None))
    handler = server.ComputerServer(driver, approvals)
    monkeypatch.setattr(handler, "_runtime_driver", AsyncMock(return_value=driver))
    return handler, driver, approvals


@pytest.mark.parametrize("app_id", [CALCULATOR, "aumid:Microsoft.Paint_8wekyb3d8bbwe!App"])
def test_aumid_launch_forwarded_and_gated_by_aumid(win, proxy, app_id):
    handler, driver, approvals = proxy
    assert win.valid_app_id(app_id)
    assert win.launch_target(app_id) == {"aumid": app_id[6:]}
    result = asyncio.run(handler.call("computer_launch_app", {"bundle_id": app_id}))
    assert not result.is_error
    driver.call.assert_awaited_once_with("launch_app", {"aumid": app_id[6:]})
    assert approvals.request.await_args.args[1]["bundle_id"] == app_id


@pytest.mark.parametrize("family", [
    "Microsoft.WindowsTerminal", "Microsoft.PowerShell", "windows.immersivecontrolpanel",
    "Microsoft.WindowsStore", "Microsoft.DesktopAppInstaller", "Microsoft.Windows.Explorer",
    "Microsoft.WindowsCommunicationsApps", "MICROSOFT.WindowsTerminalPreview",
    "PythonSoftwareFoundation.Python.3.13", "CanonicalGroupLimited.Ubuntu",
    "MicrosoftCorporationII.WindowsSubsystemForLinux", "Microsoft.WindowsSubsystemForLinux",
    "Microsoft.SecHealthUI",
])
def test_denied_packaged_families(win, proxy, family):
    app_id = "aumid:" + family + "_8wekyb3d8bbwe!App"
    assert win.valid_app_id(app_id)
    assert win.is_hard_denied(app_id, None)
    assert win.launch_target(app_id) is None
    handler, driver, approvals = proxy
    assert asyncio.run(handler.call("computer_launch_app", {"bundle_id": app_id})).is_error
    driver.call.assert_not_awaited()
    approvals.request.assert_not_awaited()


@pytest.mark.parametrize("app_id", [
    "aumid:Microsoft.WindowsCalculator!App", "aumid:Microsoft.WindowsCalculator_8WEKYB3D8BBWE!App",
    "aumid:Microsoft.WindowsCalculator_8wekyb3d8bbwe", "aumid:Microsoft.Paint_8wekyb3d8bbwe!",
    "aumid:Microsoft.Paint_8wekyb3d8bbwe!App!Other", "aumid:Microsoft.Paint_8wekyb3d8bbwe!App ",
    "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", "com.apple.Safari",
])
def test_invalid_or_system_aumid_refused(win, app_id):
    assert not win.valid_app_id(app_id)
    assert isinstance(policy.validate_call("computer_launch_app", {"bundle_id": app_id}), policy.Refusal)


def test_desktop_launch_preserves_filesystem_case(win, proxy, monkeypatch):
    app_id = win.app_id_for(41)
    original = win.win32.paths[41]
    monkeypatch.setattr(windows.os.path, "realpath", lambda value: original)
    monkeypatch.setattr(windows.os.path, "isfile", lambda value: value == original)
    assert win.launch_target(app_id) == {"path": original}
    handler, driver, _ = proxy
    assert not asyncio.run(handler.call("computer_launch_app", {"bundle_id": app_id})).is_error
    driver.call.assert_awaited_once_with("launch_app", {"path": original})


@pytest.mark.parametrize("name,exists", [
    ("missing.exe", False), ("editor.txt", True), ("systemsettings.exe", True),
    ("cmd.exe", True), ("explorer.exe", True),
])
def test_unlaunchable_desktop_paths_refused(win, monkeypatch, name, exists):
    path = chr(92).join(("c:", "apps", name))
    monkeypatch.setattr(windows.os.path, "realpath", lambda value: value)
    monkeypatch.setattr(windows.os.path, "isfile", lambda value: exists)
    app_id = "win:" + path
    assert win.launch_target(app_id) is None
    assert isinstance(policy.validate_call("computer_launch_app", {"bundle_id": app_id}), policy.Refusal)


def test_resolved_desktop_target_is_checked_again(win, monkeypatch):
    monkeypatch.setattr(windows.os.path, "realpath", lambda value: chr(92).join(("C:", "cmd.exe")))
    monkeypatch.setattr(windows.os.path, "isfile", lambda value: True)
    assert win.launch_target(win.app_id_for(41)) is None


def test_urls_keep_existing_refusal_path(win, proxy):
    handler, driver, approvals = proxy
    result = asyncio.run(handler.call("computer_launch_app", {
        "bundle_id": CALCULATOR, "urls": ["https://example.com/document"]}))
    assert result.is_error and "urls must target existing directories" in result.content[0].text
    driver.call.assert_not_awaited()
    approvals.request.assert_not_awaited()


@pytest.mark.parametrize("key", ["additional_arguments", "launch_path", "name", "creates_new_application_instance"])
def test_extra_windows_launch_arguments_refused(win, proxy, key):
    handler, driver, _ = proxy
    result = asyncio.run(handler.call("computer_launch_app", {"bundle_id": CALCULATOR, key: True}))
    assert result.is_error
    driver.call.assert_not_awaited()


def test_frame_identity_uses_core_owner_and_integrity(win):
    app_id, name, refusal = asyncio.run(win.identify(None, 42, window_id=100))
    assert (app_id, name, refusal) == (win.app_id_for(43), "Calculator", None)
    win.win32.levels[43] = 0x3000
    assert asyncio.run(win.identify(None, 42, window_id=100))[2]
    win.win32.levels[43] = None
    assert asyncio.run(win.identify(None, 42, window_id=100))[2]


@pytest.mark.parametrize("window_id", [None, True, False, "100", 999, 0, -1, 2**64])
def test_frame_without_integer_window_stays_unidentified(win, window_id):
    assert asyncio.run(win.identify(None, 42, window_id=window_id)) == (None, None, None)


def test_frame_identity_refuses_mismatched_window_owner(win):
    win.win32.paths[45] = win.win32.paths[42]
    win.win32.pids[200] = 45
    win.win32.children[200] = [101]
    assert asyncio.run(win.identify(None, 42, window_id=200)) == (None, None, None)


@pytest.mark.parametrize("read", ["window_pid", "image_path", "child_windows", "window_info"])
def test_frame_identity_refuses_ownership_read_errors(win, monkeypatch, read):
    def unavailable(*args):
        raise OSError("Window unavailable")
    monkeypatch.setattr(win.win32, read, unavailable)
    assert asyncio.run(win.identify(None, 43, window_id=100)) == (None, None, None)


@pytest.mark.parametrize("pid", [42, 43])
def test_frame_foreground_ownership_and_default_drag(win, proxy, pid):
    handler, driver, approvals = proxy
    report = {"landed_on_target": True, "now_fg_hwnd": "64", "target_hwnd": "64"}
    assert win.front_verified(report, pid, 100)
    assert asyncio.run(win.top_window_is(pid, 100))
    assert asyncio.run(win.frontmost_pid(driver)) == 43

    async def dispatch(tool, args):
        return CallToolResult(content=[], structured_content=report if tool == "bring_to_front" else {"ok": True})
    driver.call.side_effect = dispatch
    result = asyncio.run(handler.call("computer_drag", {"pid": pid, "window_id": 100, **DRAG}))
    assert not result.is_error
    assert approvals.request.await_args.args[1]["foreground"] is True
    assert [call.args for call in driver.call.await_args_list] == [
        ("bring_to_front", {"pid": pid, "window_id": 100}),
        ("drag", {"pid": pid, "window_id": 100, **DRAG, "delivery_mode": "foreground"}),
    ]


@pytest.mark.parametrize("pid", [42, 43])
def test_frame_panel_ownership(win, pid):
    win.win32.info[100] = win.win32.window(owner=99, bounds=(0, 0, 300, 200))
    assert asyncio.run(win.top_window_is(pid, 100))
    panels = asyncio.run(win.panel_windows(pid, 0))
    assert [(panel["window_id"], panel["pid"]) for panel in panels] == [(100, pid)]


def test_denied_core_owner_is_refused_before_dispatch(win, proxy, monkeypatch, tmp_path):
    handler, driver, _ = proxy
    win.win32.paths[43] = chr(92).join(("C:", "Windows", "SystemSettings.exe"))
    app_id, _, refusal = asyncio.run(win.identify(None, 42, window_id=100))
    assert refusal is None and win.is_hard_denied(app_id, 42)
    monkeypatch.setattr("computer_use.standalone.config.load", lambda: {"enabled": True})
    handler.approvals = LocalApprovals(
        None, SimpleNamespace(registered=True, halted_at=None), journal=lambda: tmp_path / "journal.jsonl")
    result = asyncio.run(handler.call("computer_window_state", {"pid": 42, "window_id": 100}))
    assert result.is_error
    driver.call.assert_not_awaited()


@pytest.mark.parametrize("changed", [False, True])
def test_frame_identity_rechecked_after_approval(win, proxy, changed):
    handler, driver, approvals = proxy
    async def approve(tool, params):
        assert params["bundle_id"] == win.app_id_for(43)
        if changed:
            win.win32.pids[101] = 44
        return {"approved": True}
    approvals.request.side_effect = approve
    result = asyncio.run(handler.call("computer_window_state", {"pid": 42, "window_id": 100}))
    assert result.is_error is changed
    if changed:
        driver.call.assert_not_awaited()
    else:
        driver.call.assert_awaited_once_with("get_window_state", {
            "pid": 42, "window_id": 100, "tree_format": "both"})


@pytest.mark.parametrize("changed", [False, True])
def test_frame_identity_rechecked_between_batch_steps(win, proxy, changed):
    handler, driver, approvals = proxy
    async def stop(params):
        if changed and driver.call.await_count:
            win.win32.pids[101] = 44
        return None
    approvals.batch_stop.side_effect = stop
    result = asyncio.run(handler.call("computer_run_steps", {
        "pid": 42, "window_id": 100, "steps": [{"type_text": {"text": "x"}}] * 2}))
    assert result.is_error is changed
    assert driver.call.await_count == (1 if changed else 2)


@pytest.mark.parametrize("shape", ["single", "short", "long", "split"])
@pytest.mark.parametrize("mode", [None, "background", "foreground"])
def test_drag_delivery_before_approval_and_forwarding(win, proxy, monkeypatch, shape, mode):
    handler, driver, approvals = proxy
    guard = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "_front_guard", guard)
    tool = "computer_drag" if shape == "single" else "computer_run_steps"
    target = dict(DRAG)
    if mode is not None:
        target["delivery_mode"] = mode
    if shape == "single":
        args = {"pid": 41, "window_id": 10, **target}
    else:
        step = {"tool": "drag", "args": target} if shape == "long" else {"drag": target}
        args = {"pid": 41, "window_id": 10, "steps": [step] * (2 if shape == "split" else 1)}
    original = deepcopy(args)
    assert not asyncio.run(handler.call(tool, args)).is_error
    assert args == original
    foreground = mode != "background"
    assert approvals.request.await_args.args[1]["foreground"] is foreground
    assert guard.await_count == ((2 if shape == "split" else 1) if foreground else 0)
    for call in driver.call.await_args_list:
        forwarded = call.args[1]
        if shape != "single":
            step = forwarded["steps"][0]
            forwarded = step.get("args") if "tool" in step else step["drag"]
        assert forwarded["delivery_mode"] == (mode or "foreground")


def test_batch_background_overrides_drag_default(win, proxy, monkeypatch):
    handler, driver, approvals = proxy
    guard = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "_front_guard", guard)
    args = {"pid": 41, "window_id": 10, "delivery_mode": "background", "steps": [{"drag": DRAG}]}
    assert not asyncio.run(handler.call("computer_run_steps", args)).is_error
    driver.call.assert_awaited_once_with("run_steps", args)
    assert approvals.request.await_args.args[1]["foreground"] is False
    guard.assert_not_awaited()


def test_default_foreground_requires_window_before_dispatch(win, proxy):
    handler, driver, approvals = proxy
    result = asyncio.run(handler.call("computer_drag", {"pid": 41, **DRAG}))
    assert result.is_error and "needs window_id" in result.content[0].text
    assert approvals.request.await_args.args[1]["foreground"] is True
    driver.call.assert_not_awaited()


def test_mac_launch_args_drag_and_identity_unchanged(proxy, monkeypatch):
    mac = macos.MacPlatform()
    monkeypatch.setattr(platform, "current", lambda: mac)
    handler, driver, _ = proxy
    launch = {"bundle_id": "com.apple.TextEdit", "creates_new_application_instance": True}
    assert not asyncio.run(handler.call("computer_launch_app", launch)).is_error
    driver.call.assert_awaited_once_with("launch_app", launch)
    identify = AsyncMock(return_value=("com.apple.TextEdit", "TextEdit"))
    monkeypatch.setattr(macos, "identify_running_app", identify)
    assert asyncio.run(mac.identify(driver, 41, window_id=100)) == ("com.apple.TextEdit", "TextEdit", None)
    identify.assert_awaited_once_with(driver, 41)
    for tool, args in [
        ("computer_drag", {"pid": 41, **DRAG}),
        ("computer_run_steps", {"pid": 41, "steps": [{"drag": DRAG}]}),
    ]:
        driver.call.reset_mock()
        assert not asyncio.run(handler.call(tool, args)).is_error
        driver.call.assert_awaited_once_with(policy.TOOLS[tool].driver_tool, args)
