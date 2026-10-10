"""Native dialog detection and command-wait regression tests."""

import asyncio
import ctypes
from ctypes import wintypes
import ntpath
import sys
import threading
import time
from unittest.mock import AsyncMock

import pytest

from models.models import MCPResponse
from transport import plugin_hub, unity_dialog_probe
from transport.plugin_hub import PluginDisconnectedError, PluginHub
from transport.plugin_registry import PluginRegistry


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 native dialog test")
def test_probe_finds_only_matching_process_dialog():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    title = "Gamachine probe test"
    thread = threading.Thread(
        target=lambda: user32.MessageBoxW(None, "probe body", title, 0), daemon=True
    )
    thread.start()
    found = False
    unity_titles = []
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if title in unity_dialog_probe.find_unity_dialog_titles(("python.exe", "pythonw.exe")):
                found = True
                break
            time.sleep(0.05)
        unity_titles = unity_dialog_probe.find_unity_dialog_titles()
    finally:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        thread.join(5.0)
    assert not thread.is_alive()
    assert found
    assert title not in unity_titles


def _dialogs_of(titles, project="ProjA"):
    return [{"title": t, "buttons": [], "hwnd": i + 1, "project": project} for i, t in enumerate(titles)]


@pytest.fixture
def command_hub(monkeypatch):
    # Match the existing transport fixtures, but restore all shared hub state.
    websocket = AsyncMock()
    registry = PluginRegistry()
    asyncio.run(registry.register("dialog-session", "ProjA", "hashA", "6000"))
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_connections", {"dialog-session": websocket})
    monkeypatch.setattr(PluginHub, "_pending", {})
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 1.0)
    monkeypatch.setattr(PluginHub, "FAST_FAIL_TIMEOUT", 0.06)
    monkeypatch.setattr(plugin_hub, "_DIALOG_POLL_S", 0.01)
    monkeypatch.setattr(plugin_hub, "_find_unity_dialogs", lambda: [])
    return websocket


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["Scene(s) Have Been Modified", ""])
async def test_wait_returns_user_action_after_two_dialog_polls(command_hub, monkeypatch, title):
    polls = []
    futures = []
    loop_thread = threading.get_ident()

    def probe():
        polls.append(threading.get_ident())
        return _dialogs_of([title])

    async def sent(_message):
        futures.append(next(iter(PluginHub._pending.values()))["future"])

    command_hub.send_json.side_effect = sent
    monkeypatch.setattr(plugin_hub, "_find_unity_dialogs", probe)
    started = time.monotonic()
    result = await PluginHub.send_command("dialog-session", "manage_scene", {})
    dialog = f'a dialog "{title}"' if title else "a dialog"
    expected = (
        f"Unity is showing {dialog} that blocks the editor, so 'manage_scene' "
        "cannot run until it is closed. Answer it with unity_dialog (action press) or ask the user to close it."
    )
    assert result == MCPResponse(success=False, error=expected, hint="user_action").model_dump()
    assert time.monotonic() - started < 0.5
    assert len(polls) == 2
    assert all(thread_id != loop_thread for thread_id in polls)
    assert not futures[0].done()
    assert PluginHub._pending == {}


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 native dialog test")
@pytest.mark.parametrize("unknown_button,expected", [(False, 7), (True, 2)])
def test_probe_lists_buttons_and_posts_only_exact_clicks(unknown_button, expected):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    title = "Gamachine press test"
    result = []
    # Each test creates at most one window thread and joins it before returning.
    thread = threading.Thread(
        target=lambda: result.append(user32.MessageBoxW(None, "press body", title, 0x3)),
        daemon=True,
    )
    thread.start()
    dialog = None
    names = ("python.exe", "pythonw.exe", ntpath.basename(sys.executable))
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            dialog = next((d for d in unity_dialog_probe.find_unity_dialogs(names) if d["title"] == title), None)
            if dialog and len(dialog["buttons"]) == 3:
                break
            time.sleep(0.05)
        assert dialog is not None
        buttons = dialog["buttons"]
        assert len(buttons) == 3
        if unknown_button:
            button = "Maybe-no-such-button"
            assert button not in buttons
            assert unity_dialog_probe.press_dialog_button(dialog["hwnd"], button) is False
            thread.join(0.2)
            assert thread.is_alive()
            assert result == []
            assert any(d["hwnd"] == dialog["hwnd"] for d in unity_dialog_probe.find_unity_dialogs(names))
            assert unity_dialog_probe.press_dialog_button(dialog["hwnd"], buttons[2]) is True
        else:
            assert unity_dialog_probe.press_dialog_button(dialog["hwnd"], buttons[1]) is True
        thread.join(5.0)
        assert not thread.is_alive()
        assert result == [expected]
    finally:
        if thread.is_alive():
            hwnd = user32.FindWindowW("#32770", title)
            # Never answer a same-title window belonging to another thread.
            if hwnd and user32.GetWindowThreadProcessId(hwnd, None) == thread.native_id:
                own_dialog = next((d for d in unity_dialog_probe.find_unity_dialogs(names) if d["hwnd"] == hwnd), None)
                if own_dialog and len(own_dialog["buttons"]) == 3:
                    unity_dialog_probe.press_dialog_button(hwnd, own_dialog["buttons"][2])
            thread.join(5.0)


@pytest.mark.parametrize("raw,cleaned", [
    ("&Yes", "Yes"), ("  &No  ", "No"), ("Save && Exit", "Save & Exit"),
    ("&&&Save", "&Save"), ("&", ""), ("Cancel", "Cancel"),
])
def test_button_accelerators_are_cleaned(raw, cleaned):
    assert unity_dialog_probe._clean_button_text(raw) == cleaned


def test_non_windows_probe_and_press_are_harmless(monkeypatch):
    monkeypatch.setattr(unity_dialog_probe.sys, "platform", "linux")
    assert unity_dialog_probe.find_unity_dialogs() == []
    assert unity_dialog_probe.find_unity_dialog_titles() == []
    assert unity_dialog_probe.press_dialog_button(123, "Save") is False


def test_probe_and_press_fail_closed_on_native_exceptions(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("Native API unavailable")

    monkeypatch.setattr(unity_dialog_probe.sys, "platform", "win32")
    monkeypatch.setattr(unity_dialog_probe.ctypes, "WinDLL", unavailable, raising=False)
    assert unity_dialog_probe.find_unity_dialogs() == []
    assert unity_dialog_probe.press_dialog_button(123, "Save") is False


def test_legacy_title_probe_preserves_order_and_deduplicates(monkeypatch):
    monkeypatch.setattr(unity_dialog_probe, "find_unity_dialogs", lambda names: [
        {"title": "X", "buttons": [], "hwnd": 1},
        {"title": "", "buttons": [], "hwnd": 2},
        {"title": "X", "buttons": [], "hwnd": 3},
    ])
    assert unity_dialog_probe.find_unity_dialog_titles() == ["X", ""]


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["early", "disconnect", "timeout"])
async def test_blocking_dialog_errors_list_first_dialog_buttons(command_hub, monkeypatch, branch):
    monkeypatch.setattr(plugin_hub, "_find_unity_dialogs", lambda: [
        {"title": "Save scene", "buttons": ["Save", "Don't Save", "Cancel"], "hwnd": 123, "project": "ProjA"},
        {"title": "Other", "buttons": ["Yes", "No"], "hwnd": 456, "project": "ProjA"},
    ])
    if branch == "disconnect":
        async def sent(_message):
            future = next(iter(PluginHub._pending.values()))["future"]
            future.set_exception(PluginDisconnectedError("Disconnected"))
        command_hub.send_json.side_effect = sent
    elif branch == "timeout":
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.02)
        monkeypatch.setattr(plugin_hub, "_DIALOG_POLL_S", 1.0)
    result = await PluginHub.send_command("dialog-session", "manage_scene", {})
    assert result["success"] is False
    assert result["hint"] == "user_action"
    assert 'Unity is showing a dialog "Save scene" (buttons: Save, Don\'t Save, Cancel) that blocks the editor' in result["error"]
    assert "Other" not in result["error"]
    if branch == "early":
        assert result["error"].endswith("Answer it with unity_dialog (action press) or ask the user to close it.")


@pytest.mark.asyncio
async def test_result_survives_several_wait_slices(command_hub, monkeypatch):
    result = {"success": True, "data": {"scene": "unchanged"}}
    polls = []

    def probe():
        polls.append(None)
        return []

    async def sent(_message):
        future = next(iter(PluginHub._pending.values()))["future"]
        asyncio.get_running_loop().call_later(0.045, future.set_result, result)

    command_hub.send_json.side_effect = sent
    monkeypatch.setattr(plugin_hub, "_find_unity_dialogs", probe)
    assert await PluginHub.send_command("dialog-session", "manage_scene", {}) is result
    assert len(polls) >= 2
    assert PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("titles", [[], ["X"], [""]])
async def test_disconnect_preserves_error_and_adds_dialog_hint(command_hub, monkeypatch, titles):
    original = "Unity plugin session dialog-session disconnected while awaiting command_result"

    async def sent(_message):
        future = next(iter(PluginHub._pending.values()))["future"]
        future.set_exception(PluginDisconnectedError(original))

    command_hub.send_json.side_effect = sent
    monkeypatch.setattr(plugin_hub, "_find_unity_dialogs", lambda: _dialogs_of(titles))
    error = original
    if titles:
        dialog = f'a dialog "{titles[0]}"' if titles[0] else "a dialog"
        error += f" Unity is showing {dialog} that blocks the editor; ask the user to close it."
    assert await PluginHub.send_command("dialog-session", "manage_scene", {}) == MCPResponse(
        success=False, error=error, hint="user_action" if titles else "retry"
    ).model_dump()
    assert PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["ping", "manage_scene"])
@pytest.mark.parametrize("titles", [[], ["X"], [""]])
async def test_total_timeout_preserves_outputs_or_adds_dialog_hint(command_hub, monkeypatch, command, titles):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.06)
    # Reach the total timeout before a polling slice can confirm two sightings.
    monkeypatch.setattr(plugin_hub, "_DIALOG_POLL_S", 1.0)
    monkeypatch.setattr(plugin_hub, "_find_unity_dialogs", lambda: _dialogs_of(titles))
    if command == "manage_scene" and not titles:
        with pytest.raises(asyncio.TimeoutError) as caught:
            await PluginHub.send_command("dialog-session", command, {})
        assert str(caught.value) == ""
    else:
        error = f"Unity did not respond to '{command}' within 0.1s; please retry"
        if titles:
            dialog = f'a dialog "{titles[0]}"' if titles[0] else "a dialog"
            error += f" Unity is showing {dialog} that blocks the editor; ask the user to close it."
        assert await PluginHub.send_command("dialog-session", command, {}) == MCPResponse(
            success=False, error=error, hint="user_action" if titles else "retry"
        ).model_dump()
    assert PluginHub._pending == {}


DIALOGS = [
    {"title": "A", "buttons": ["Save"], "hwnd": 1, "project": "ProjA"},
    {"title": "B", "buttons": ["Save"], "hwnd": 2, "project": "ProjB"},
]


def test_dialogs_for_project_keeps_only_exact_project():
    assert [d["title"] for d in unity_dialog_probe.dialogs_for_project(DIALOGS, "ProjA")] == ["A"]
    assert unity_dialog_probe.dialogs_for_project(DIALOGS, "proja") == []
    assert unity_dialog_probe.dialogs_for_project(DIALOGS, "Proj") == []


@pytest.mark.parametrize("project", [None, ""])
def test_dialogs_for_project_unknown_target_sees_nothing(project):
    assert unity_dialog_probe.dialogs_for_project(DIALOGS, project) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 native dialog test")
def test_ownerless_message_box_has_empty_project():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    title = "Gamachine project test"
    thread = threading.Thread(
        target=lambda: user32.MessageBoxW(None, "project body", title, 0), daemon=True
    )
    thread.start()
    dialog = None
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and dialog is None:
            dialog = next((d for d in unity_dialog_probe.find_unity_dialogs(("python.exe", "pythonw.exe"))
                           if d["title"] == title), None)
            time.sleep(0.05)
    finally:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        thread.join(5.0)
    assert dialog is not None
    assert dialog["project"] == ""


@pytest.mark.asyncio
async def test_other_project_dialog_does_not_block_command(command_hub, monkeypatch):
    monkeypatch.setattr(plugin_hub, "_find_unity_dialogs", lambda: _dialogs_of(["Save scene"], "ProjB"))
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.1)
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await PluginHub.send_command("dialog-session", "manage_scene", {})
    assert time.monotonic() - started >= 0.09


@pytest.mark.asyncio
async def test_own_project_dialog_still_blocks_command(command_hub, monkeypatch):
    monkeypatch.setattr(
        plugin_hub, "_find_unity_dialogs",
        lambda: _dialogs_of(["Other"], "ProjB") + _dialogs_of(["Save scene"], "ProjA"),
    )
    result = await PluginHub.send_command("dialog-session", "manage_scene", {})
    assert result["hint"] == "user_action"
    assert 'a dialog "Save scene"' in result["error"]
    assert "Other" not in result["error"]
