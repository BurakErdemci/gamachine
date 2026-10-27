"""Windows platform facts tested without a driver or desktop input."""
import asyncio
import os
import threading

import pytest

from computer_use import platform, policy
from computer_use.driver_client import DriverUnavailable
from computer_use.platform.base import WindowListUnavailable
from computer_use.platform.windows import DENY, WindowsPlatform


class FakeWin32:
    def __init__(self):
        self.dpi_calls = 0
        self.paths = {41: r"C:\Apps\MyEditor.exe", 42: r"C:\Windows\ApplicationFrameHost.exe",
                      43: r"C:\Apps\Calculator.exe"}
        self.levels = {os.getpid(): 0x2000, 41: 0x2000}
        self.parents = {}
        self.elevated = False
        self.foreground = 10
        self.windows = [10]
        self.pids = {10: 41}
        self.children = {}
        self.info = {10: self.window()}

    @staticmethod
    def window(**changes):
        return {"visible": True, "cloaked": False, "minimized": False, "owner": 0,
                "title": "Document", "class_name": "Editor", "bounds": (-50, 20, 400, 300), **changes}

    def set_dpi_awareness(self):
        self.dpi_calls += 1

    def image_path(self, pid):
        return self.paths.get(pid)

    def integrity(self, pid):
        return self.levels.get(pid)

    def self_elevated(self):
        return self.elevated

    def parent_pids(self):
        return self.parents

    def foreground_window(self):
        return self.foreground

    def window_pid(self, hwnd):
        return self.pids.get(hwnd)

    def top_level_windows(self):
        return self.windows

    def window_info(self, hwnd):
        return self.info.get(hwnd)

    def child_windows(self, hwnd):
        return self.children.get(hwnd, [])


@pytest.fixture
def win():
    return WindowsPlatform(win32=FakeWin32())


@pytest.mark.parametrize("value, expected", [
    (r"win:c:\apps\editor.exe", True),
    (r"win:z:\program files\unity hub\unity hub.exe", True),
    (r"win:c:\editor.exe", True),
    ("com.gamachine.clipboard", True), ("COM.GAMACHINE.CLIPBOARD", True),
    (None, False), (41, False), ("com.apple.Safari", False),
    (r"C:\Apps\editor.exe", False), (r"win:C:\apps\editor.exe", False),
    (r"win:c:\Apps\editor.exe", False), (r"win:\\server\share\editor.exe", False),
    (r"win:c:\apps\..\editor.exe", False), (r"win:c:\apps\.\editor.exe", False),
    (r"win:c:\apps\\editor.exe", False), (r"win:c:\apps\editor.exe:stream", False),
    (r"win:c:\apps\editor.txt", False), (r"win:c:editor.exe", False),
    (r"win:c:\apps\a?.exe", False), ("win:c:\\apps\\a\n.exe", False),
    ("win:c:\\" + "x" * 256 + ".exe", False),
])
def test_valid_app_id(win, value, expected):
    assert win.valid_app_id(value) is expected


def test_constructor_and_identity_helpers(win):
    assert win.win32.dpi_calls == 1
    assert win.app_id_for(41) == r"win:c:\apps\myeditor.exe"
    assert win.app_id_for(999) is None
    win.win32.paths[41] = r"\\server\share\Editor.exe"
    assert win.app_id_for(41) is None
    assert win.resolve_launch_targets([], r"win:c:\apps\editor.exe") is None


@pytest.mark.parametrize("name", sorted(DENY) + ["DashlaneAgent.exe", "LastPassHelper.exe"])
def test_hard_denied_basename(win, name):
    assert win.is_hard_denied("win:c:\\anywhere\\" + name.upper(), 41)


def test_hard_denied_self_ancestors_and_cycle(win):
    own = os.getpid()
    win.win32.parents = {own: 10000001, 10000001: 10000002, 10000002: 10000001}
    for pid in (own, 10000001, 10000002):
        assert win.is_hard_denied(r"win:c:\apps\editor.exe", pid)
    assert not win.is_hard_denied(r"win:c:\apps\editor.exe", 10000003)
    assert not win.is_hard_denied("com.gamachine.clipboard", own)
    assert not win.is_hard_denied("COM.GAMACHINE.CLIPBOARD", own)
    assert not win.is_hard_denied(r"win:c:\apps\lastpass.txt", None)
    assert not win.is_hard_denied(r"win:c:\apps\mydashlane.exe", None)


def test_ancestor_walk_is_bounded(win):
    own = os.getpid()
    chain = [own] + list(range(10000000, 10000100))
    win.win32.parents = dict(zip(chain, chain[1:]))
    assert not win.is_hard_denied(None, chain[-1])


@pytest.mark.parametrize("value, expected", [
    (r"win:c:\unity\Unity.EXE", True), (r"win:c:\apps\unityhub.exe", False),
    (None, False), (r"win:c:\unity.exe\editor.exe", False),
])
def test_unity_editor(win, value, expected):
    assert win.is_unity_editor(value) is expected


@pytest.mark.parametrize("pid", [None, 0, -1, True, "41", 999, 42])
def test_identify_missing_or_host(win, pid):
    assert asyncio.run(win.identify(None, pid)) == (None, None, None)


@pytest.mark.parametrize("level, refused", [(0x1000, False), (0x2000, False), (0x3000, True), (None, True)])
def test_identify_integrity_and_original_case(win, level, refused):
    win.win32.levels[41] = level
    app_id, name, reason = asyncio.run(win.identify(object(), 41))
    assert app_id == r"win:c:\apps\myeditor.exe"
    assert name == "MyEditor"
    if refused:
        assert reason == (
            "Bu uygulama yönetici olarak çalışıyor; Windows yönetici pencerelerine dışarıdan "
            "girdi gönderilmesine izin vermez. Uygulamayı normal başlatıp yeniden dene."
        )
    else:
        assert reason is None


def test_identify_unknown_own_integrity_refuses(win):
    win.win32.levels[os.getpid()] = None
    assert asyncio.run(win.identify(None, 41))[2]


def test_identify_os_calls_leave_event_loop_thread(win, monkeypatch):
    main_thread = threading.get_ident()
    calls = []
    original = win.win32.image_path

    def path(pid):
        calls.append(threading.get_ident())
        return original(pid)

    monkeypatch.setattr(win.win32, "image_path", path)
    asyncio.run(win.identify(None, 41))
    assert calls and all(thread != main_thread for thread in calls)


def test_driver_command_environment_and_freshness(win, monkeypatch):
    keys = ("PATH", "SYSTEMROOT", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "ProgramData",
            "ALLUSERSPROFILE", "TEMP", "TMP", "USERNAME", "HOMEDRIVE", "HOMEPATH")
    values = {key: "value-" + key for key in keys}
    monkeypatch.setattr(os, "environ", {**values, "SECRET": "excluded", "HOME": "excluded"})
    command, env = win.driver_command("driver.exe", "pipe")
    assert command == ["driver.exe", "mcp", "--embedded", "--socket", "pipe"]
    assert env == {**values, "CUA_DRIVER_EMBEDDED": "1", "DO_NOT_TRACK": "1",
                   "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0", "CUA_DRIVER_RS_UPDATE_CHECK": "0"}
    env["PATH"] = "changed"
    assert win.driver_command("a", "b")[1]["PATH"] == values["PATH"]
    monkeypatch.setattr(os, "environ", {})
    assert set(win.driver_command("a", "b")[1]) == {
        "CUA_DRIVER_EMBEDDED", "DO_NOT_TRACK", "CUA_DRIVER_RS_TELEMETRY_ENABLED", "CUA_DRIVER_RS_UPDATE_CHECK"}


def test_elevated_driver_refused(win):
    win.win32.elevated = True
    with pytest.raises(DriverUnavailable, match="Normal başlatın"):
        win.driver_command("driver.exe", "pipe")


def test_frontmost_uwp_resolution(win):
    win.win32.pids = {10: 42, 11: 42, 12: 43, 13: 41}
    win.win32.children[10] = [11, 12, 13]
    win.win32.info.update({child: win.win32.window(class_name="Windows.UI.Core.CoreWindow")
                          for child in (11, 12, 13)})
    assert asyncio.run(win.frontmost_pid(None)) == 43
    assert win.real_pid(99, 42) == 42
    assert win.real_pid(10, 41) == 41


@pytest.mark.parametrize("pid", [41, None, 0, -5, True])
def test_frontmost_pid(win, pid):
    win.win32.pids[10] = pid
    assert asyncio.run(win.frontmost_pid(None)) == (41 if pid == 41 else None)


def test_frontmost_none_and_error(win, monkeypatch):
    win.win32.foreground = None
    assert asyncio.run(win.frontmost_pid(None)) is None

    def fail():
        raise OSError("window lookup failed")

    monkeypatch.setattr(win.win32, "foreground_window", fail)
    assert asyncio.run(win.frontmost_pid(None)) is None


def test_top_window_filtering_and_order(win):
    win.win32.windows = list(range(1, 8))
    win.win32.pids = {hwnd: 41 for hwnd in win.win32.windows}
    win.win32.pids[5] = 43
    win.win32.info = {1: win.win32.window(visible=False), 2: win.win32.window(cloaked=True),
                      3: win.win32.window(minimized=True), 4: win.win32.window(owner=99),
                      5: win.win32.window(), 6: win.win32.window(), 7: win.win32.window()}
    assert asyncio.run(win.top_window_is(41, 6)) is True
    assert asyncio.run(win.top_window_is(41, 7)) is False
    assert asyncio.run(win.top_window_is(999, 6)) is False
    win.win32.windows = None
    with pytest.raises(WindowListUnavailable):
        asyncio.run(win.top_window_is(41, 6))


def test_panels_shape_filters_and_order(win):
    win.win32.windows = list(range(1, 8))
    win.win32.pids = {hwnd: 41 for hwnd in win.win32.windows}
    win.win32.pids[5] = 43
    win.win32.info = {
        1: win.win32.window(owner=99, title=None), 2: win.win32.window(owner=99, visible=False),
        3: win.win32.window(owner=99, cloaked=True), 4: win.win32.window(owner=99, bounds=(0, 0, 39, 80)),
        5: win.win32.window(owner=99), 6: win.win32.window(),
        7: win.win32.window(owner=99, bounds=(-10, -20, 40, 40)),
    }
    rows = asyncio.run(win.panel_windows(41, 100))
    assert rows == [
        {"window_id": 1, "pid": 41, "app_name": "MyEditor", "title": "", "layer": 1,
         "bounds": {"x": -50, "y": 20, "width": 400, "height": 300},
         "is_on_screen": True, "on_current_space": True, "z_index": 102},
        {"window_id": 7, "pid": 41, "app_name": "MyEditor", "title": "Document", "layer": 1,
         "bounds": {"x": -10, "y": -20, "width": 40, "height": 40},
         "is_on_screen": True, "on_current_space": True, "z_index": 101},
    ]


def test_panels_cap_and_missing_name(win):
    win.win32.windows = list(range(1, 80))
    win.win32.pids = {hwnd: 999 for hwnd in win.win32.windows}
    win.win32.info = {hwnd: win.win32.window(owner=90) for hwnd in win.win32.windows}
    rows = asyncio.run(win.panel_windows(999, 10))
    assert len(rows) == 64
    assert rows[0]["z_index"] == 74 and rows[-1]["z_index"] == 11
    assert rows[-1]["window_id"] == 64
    assert rows[0]["app_name"] is None


def test_panels_errors_and_running_apps(win, monkeypatch):
    def fail():
        raise OSError("cannot enumerate")

    monkeypatch.setattr(win.win32, "top_level_windows", fail)
    assert asyncio.run(win.panel_windows(41, 0)) == []
    assert asyncio.run(win.running_apps()) == []


def test_windows_launch_targets(win, monkeypatch):
    assert policy.platform is platform
    monkeypatch.setattr(platform, "current", lambda: win)
    result = policy.validate_call("computer_launch_app", {"bundle_id": r"win:c:\apps\editor.exe"})
    assert isinstance(result, policy.Refusal)
    result = policy.validate_call("computer_launch_app", {
        "bundle_id": "aumid:Microsoft.Paint_8wekyb3d8bbwe!App"})
    assert isinstance(result, policy.Validated)
    assert isinstance(policy.validate_call("computer_launch_app", {
        "bundle_id": "aumid:Microsoft.WindowsStore_8wekyb3d8bbwe!App"}), policy.Refusal)


def test_win32_unavailable_returns_safe_defaults(monkeypatch):
    from computer_use.platform import _win32

    monkeypatch.setattr(_win32, "_available", False)
    assert _win32.set_dpi_awareness() is None
    assert _win32.image_path(41) is None
    assert _win32.integrity(41) is None
    assert _win32.self_elevated() is False
    assert _win32.parent_pids() == {}
    assert _win32.foreground_window() is None
    assert _win32.window_pid(10) is None
    assert _win32.top_level_windows() is None
    assert _win32.window_info(10) is None
    assert _win32.child_windows(10) == []
