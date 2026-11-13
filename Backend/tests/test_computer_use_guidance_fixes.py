"""Fix round: Store-app launch ids, standalone instruction budget and installer-free suggestions."""
import asyncio
import json
import sys

import pytest
from mcp.types import CallToolResult, TextContent

from computer_use import platform, policy, server
from computer_use.platform import macos
from computer_use.platform.windows import WindowsPlatform
from computer_use.standalone.__main__ import INSTRUCTIONS_EXTRA
from tests.test_computer_use_tool_guidance import BASE, FakeWin32, FakeWinreg, descriptions, list_apps, win  # noqa: F401

HOOK_MISSING = " Onay hook'u kurulu değil: kritik işlemler bu oturumda yapılamaz."
CALCULATOR = "Microsoft.WindowsCalculator_8wekyb3d8bbwe"
FRAME_HOST = "C:\\Windows\\System32\\ApplicationFrameHost.exe"


def result_for(rows):
    payload = {"apps": rows}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))], structured_content=payload)


def test_launch_description_restores_the_store_recipe(win):
    text = descriptions()["computer_launch_app"]
    assert "'aumid:' + the bundle_id from computer_list_apps + '!App'" in text
    assert "aumid:Microsoft.Paint_8wekyb3d8bbwe!App" in text
    assert "aumid:Microsoft.WindowsCalculator_8wekyb3d8bbwe!App" in text
    assert len(text) < 900


def test_store_row_gets_an_aumid_launch_id(monkeypatch):
    rows = [{"pid": 9, "name": "Calculator", "bundle_id": CALCULATOR, "running": True}]
    merged = list_apps(monkeypatch, result_for(rows), {9: FRAME_HOST})
    expected = "aumid:" + CALCULATOR + "!App"
    for view in (json.loads(merged.content[0].text), merged.structured_content):
        assert view["apps"][0]["launch_id"] == expected
    assert policy.valid_bundle_id(expected)


def test_frame_host_pid_without_bundle_id_gets_no_launch_id(monkeypatch):
    merged = list_apps(monkeypatch, result_for([{"pid": 9, "name": "Calculator"}]), {9: FRAME_HOST})
    assert "launch_id" not in json.loads(merged.content[0].text)["apps"][0]
    text = list_apps(monkeypatch, CallToolResult(content=[TextContent(type="text", text="Calculator (9)")]),
                     {9: FRAME_HOST})
    assert text.content[0].text == "Calculator (9)"


def test_hard_denied_rows_get_no_launch_id(monkeypatch):
    rows = [{"pid": 3, "name": "Terminal"}, {"pid": 4, "name": "Files"}]
    merged = list_apps(monkeypatch, result_for(rows), {3: "C:\\Windows\\System32\\cmd.exe",
                                                       4: "C:\\Windows\\explorer.exe"})
    assert all("launch_id" not in row for row in json.loads(merged.content[0].text)["apps"])


def test_error_results_are_not_tagged(win):
    error = CallToolResult(content=[TextContent(type="text", text="Blender (7)")], is_error=True)
    assert asyncio.run(server._add_launch_ids(error)) is error


@pytest.mark.parametrize("name", ["win32", "darwin"])
def test_standalone_instructions_fit_the_provider_limit(monkeypatch, name):
    instance = WindowsPlatform(FakeWin32()) if name == "win32" else macos.MacPlatform()
    monkeypatch.setattr(platform, "current", lambda: instance)
    for notice in ("", HOOK_MISSING):
        text = server.create_server(instructions_extra=INSTRUCTIONS_EXTRA + notice,
                                    name="gamachineComputerLocal").instructions
        assert len(text) <= 2000
        assert "args.confirm" in text
        assert text.endswith(notice) if notice else True


def installed_tools(tmp_path, entries):
    """entries: (key, DisplayName, relative exe) -> a registry whose DisplayIcon points at each created exe."""
    keys = {}
    for key, display, relative in entries:
        exe = tmp_path / relative
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"")
        keys[("HKLM", BASE + "Uninstall\\" + key)] = {"DisplayName": display, "DisplayIcon": str(exe)}
    return FakeWinreg(keys)


def test_suggestions_skip_hard_denied_exes(win, tmp_path):
    registry = installed_tools(tmp_path, [("a", "Cmd Tool", "x\\cmd.exe"), ("b", "Cmd Tool", "y\\powershell.exe")])
    assert win.installed_app_ids("cmd tool", winreg=registry) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry resolver: ids need a drive-letter path")
def test_suggestions_skip_installers_uninstallers_and_package_caches(win, tmp_path):
    registry = installed_tools(tmp_path, [
        ("a", "Tool", "t\\tool.exe"),
        ("b", "Tool", "t\\uninst.exe"),
        ("c", "Tool", "t\\unins000.exe"),
        ("d", "Tool", "t\\setup.exe"),
        ("e", "Tool", "t\\toolsetup.exe"),
        ("f", "Tool", "t\\uninstall tool.exe"),
        ("g", "Tool", "t\\install.exe"),
        ("h", "Tool", "Package Cache\\{1}\\tool-3.exe"),
        ("i", "Tool", "Installer\\tool-4.exe"),
    ])
    assert win.installed_app_ids("tool", winreg=registry) == ["win:" + str(tmp_path / "t" / "tool.exe").casefold()]


@pytest.mark.parametrize("name", ["a\x00b", "a\nb", 7, None])
def test_invalid_names_return_nothing(win, name):
    assert win.installed_app_ids(name, winreg=FakeWinreg({})) == []


def test_registry_failure_does_not_escape_the_refusal(win, monkeypatch):
    def broken(value):
        raise PermissionError("registry")

    monkeypatch.setattr(win, "installed_app_ids", broken, raising=False)
    refusal = policy.validate_call("computer_launch_app", {"bundle_id": "Blender"})
    assert isinstance(refusal, policy.Refusal)
    assert "'win:<lowercase full .exe path>'" in refusal.reason
    assert "computer_list_apps" in refusal.reason
