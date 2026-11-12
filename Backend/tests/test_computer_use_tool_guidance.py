"""Tool descriptions, list_apps ids and bad-id refusals carry what a model needs to launch an app."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from computer_use import platform, policy, server
from computer_use.platform import macos
from computer_use.platform.windows import WindowsPlatform

BASE = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"


class FakeWin32:
    def __init__(self, paths=None):
        self.paths = paths or {}

    def set_dpi_awareness(self):
        pass

    def image_path(self, pid):
        return self.paths.get(pid)


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, keys):
        self.keys = {(root, path.casefold()): values for (root, path), values in keys.items()}

    def OpenKey(self, root, path):
        if (root, path.casefold()) not in self.keys and not any(
                k[0] == root and k[1].startswith(path.casefold() + "\\") for k in self.keys):
            raise FileNotFoundError(path)
        return FakeKey((root, path.casefold()))

    def EnumKey(self, key, index):
        prefix = key.path[1] + "\\"
        subs = sorted({k[1][len(prefix):].split("\\")[0] for k in self.keys
                       if k[0] == key.path[0] and k[1].startswith(prefix)})
        if index >= len(subs):
            raise OSError("no more")
        return subs[index]

    def QueryValueEx(self, key, name):
        values = self.keys.get(key.path, {})
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1


@pytest.fixture
def win(monkeypatch):
    instance = WindowsPlatform(FakeWin32())
    monkeypatch.setattr(platform, "current", lambda: instance)
    return instance


@pytest.fixture
def mac(monkeypatch):
    instance = macos.MacPlatform()
    monkeypatch.setattr(platform, "current", lambda: instance)
    return instance


def descriptions():
    tools = asyncio.run(server.create_server().list_tools())
    return {tool.name: tool.description for tool in tools}


def test_launch_description_names_windows_formats(win):
    text = descriptions()["computer_launch_app"]
    assert "'aumid:<package family>!<app>'" in text
    assert "'win:<lowercase full .exe path>'" in text
    assert "computer_list_apps" in text
    assert len(text) < 900


def test_launch_description_on_macos_stays_macos(mac):
    text = descriptions()["computer_launch_app"]
    assert "bundle identifier such as com.apple.TextEdit" in text
    assert "win:" not in text and "aumid" not in text


@pytest.mark.parametrize("fixture", ["win", "mac"])
def test_coordinate_tools_state_pixel_space_and_element_token(request, fixture):
    request.getfixturevalue(fixture)
    found = descriptions()
    for name in ("computer_click", "computer_double_click", "computer_right_click", "computer_scroll"):
        assert "pixels in the latest computer_window_state screenshot" in found[name]
        assert "Prefer element_token when the window exposes elements." in found[name]
    assert "need from_zoom" in found["computer_click"]
    assert "pixels in the latest computer_window_state screenshot" in found["computer_drag"]
    assert "element_token" not in found["computer_drag"]


def install_fake_registry(monkeypatch, tmp_path):
    app = tmp_path / "Blender Foundation" / "Blender 4.2"
    app.mkdir(parents=True)
    exe = app / "blender.exe"
    exe.write_bytes(b"")
    other = tmp_path / "Other"
    other.mkdir()
    (other / "blendertool.exe").write_bytes(b"")
    (other / "blender-helper.exe").write_bytes(b"")
    fake = FakeWinreg({
        ("HKLM", BASE + "App Paths\\blender.exe"): {"": '"' + str(exe) + '"'},
        ("HKLM", BASE + "Uninstall\\Blender"): {
            "DisplayName": "Blender 4.2", "DisplayIcon": str(exe) + ",0"},
        ("HKCU", BASE + "Uninstall\\{ABC}"): {
            "DisplayName": "Blender Launcher", "InstallLocation": str(app)},
        ("HKLM", BASE + "Uninstall\\Ambiguous"): {
            "DisplayName": "Blender Extras", "InstallLocation": str(other)},
        ("HKLM", BASE + "Uninstall\\Exact"): {
            "DisplayName": "Blender Exact", "InstallLocation": str(app.parent)},
        ("HKLM", BASE + "Uninstall\\Unrelated"): {"DisplayName": "Notepad++"},
    })
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return "win:" + str(exe).casefold()


def test_resolver_finds_installed_desktop_app_from_registry(win, monkeypatch, tmp_path):
    expected = install_fake_registry(monkeypatch, tmp_path)
    for query in ("Blender", "blender.exe", " BLENDER "):
        assert win.installed_app_ids(query) == [expected]
    assert win.installed_app_ids("notepad++") == []
    assert win.installed_app_ids("") == []
    assert win.valid_app_id(expected)


def test_resolver_returns_at_most_five_and_skips_missing_files(win, tmp_path):
    keys = {}
    for i in range(8):
        exe = tmp_path / f"app{i}" / "tool.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"")
        keys[("HKLM", BASE + f"Uninstall\\k{i}")] = {"DisplayName": "Tool", "DisplayIcon": str(exe)}
    keys[("HKLM", BASE + "Uninstall\\gone")] = {"DisplayName": "Tool", "DisplayIcon": str(tmp_path / "gone.exe")}
    assert len(win.installed_app_ids("tool", winreg=FakeWinreg(keys))) == 5


def test_bad_name_refusal_lists_formats_and_matching_id(win, monkeypatch, tmp_path):
    expected = install_fake_registry(monkeypatch, tmp_path)
    refusal = policy.validate_call("computer_launch_app", {"bundle_id": "Blender"})
    assert isinstance(refusal, policy.Refusal)
    assert "'win:<lowercase full .exe path>'" in refusal.reason
    assert "computer_list_apps" in refusal.reason
    assert "Did you mean: " + expected in refusal.reason


def test_bad_name_refusal_without_match_still_names_format(win, monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg({}))
    refusal = policy.validate_call("computer_launch_app", {"bundle_id": "Nothing"})
    assert isinstance(refusal, policy.Refusal)
    assert "Did you mean" not in refusal.reason
    assert "'aumid:<package family>!<app>'" in refusal.reason


def test_bad_id_refusal_on_macos_names_bundle_format(mac):
    refusal = policy.validate_call("computer_launch_app", {"bundle_id": "not an id"})
    assert isinstance(refusal, policy.Refusal)
    assert "bundle identifier such as com.apple.TextEdit" in refusal.reason
    assert "computer_list_apps" in refusal.reason


def list_apps(monkeypatch, result, paths):
    instance = WindowsPlatform(FakeWin32(paths))
    monkeypatch.setattr(platform, "current", lambda: instance)
    monkeypatch.setattr(server, "fetch_runtime", AsyncMock(return_value=("fake", "fake-socket")))
    monkeypatch.setattr(server.approval_bridge, "request_approval", AsyncMock(return_value={"approved": True}))
    driver = SimpleNamespace(binary="fake", socket="fake-socket", call=AsyncMock(return_value=result))
    return asyncio.run(server.ComputerServer(driver).call("computer_list_apps", {}))


BLENDER = "C:\\Program Files\\Blender\\blender.exe"


def test_running_app_rows_show_launchable_id(monkeypatch):
    payload = {"apps": [{"pid": 7, "name": "Blender", "running": True}, {"pid": 8, "name": "Ghost"}]}
    result = CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))],
                            structured_content=payload)
    merged = list_apps(monkeypatch, result, {7: BLENDER})
    expected = "win:" + BLENDER.casefold()
    for view in (json.loads(merged.content[0].text), merged.structured_content):
        assert view["apps"][0]["launch_id"] == expected
        assert "launch_id" not in view["apps"][1]


def test_running_app_text_lines_show_launchable_id(monkeypatch):
    text = "3 apps: 2 running, 1 installed-not-running\nBlender (7)\nGhost (8)"
    merged = list_apps(monkeypatch, CallToolResult(content=[TextContent(type="text", text=text)]), {7: BLENDER})
    lines = merged.content[0].text.splitlines()
    assert lines[0] == "3 apps: 2 running, 1 installed-not-running"
    assert lines[1] == "Blender (7) id=win:" + BLENDER.casefold()
    assert lines[2] == "Ghost (8)"
