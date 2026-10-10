"""Native dialog detection and command-wait regression tests."""

import asyncio
import ctypes
from ctypes import wintypes
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


@pytest.fixture
def command_hub(monkeypatch):
    # Match the existing transport fixtures, but restore all shared hub state.
    websocket = AsyncMock()
    monkeypatch.setattr(PluginHub, "_registry", PluginRegistry())
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_connections", {"dialog-session": websocket})
    monkeypatch.setattr(PluginHub, "_pending", {})
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 1.0)
    monkeypatch.setattr(PluginHub, "FAST_FAIL_TIMEOUT", 0.06)
    monkeypatch.setattr(plugin_hub, "_DIALOG_POLL_S", 0.01)
    monkeypatch.setattr(plugin_hub, "_find_unity_dialog_titles", lambda: [])
    return websocket


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["Scene(s) Have Been Modified", ""])
async def test_wait_returns_user_action_after_two_dialog_polls(command_hub, monkeypatch, title):
    polls = []
    futures = []
    loop_thread = threading.get_ident()

    def probe():
        polls.append(threading.get_ident())
        return [title]

    async def sent(_message):
        futures.append(next(iter(PluginHub._pending.values()))["future"])

    command_hub.send_json.side_effect = sent
    monkeypatch.setattr(plugin_hub, "_find_unity_dialog_titles", probe)
    started = time.monotonic()
    result = await PluginHub.send_command("dialog-session", "manage_scene", {})
    dialog = f'a dialog "{title}"' if title else "a dialog"
    expected = (
        f"Unity is showing {dialog} that blocks the editor, so 'manage_scene' "
        "cannot run until it is closed. Ask the user to close it in Unity, then retry."
    )
    assert result == MCPResponse(success=False, error=expected, hint="user_action").model_dump()
    assert time.monotonic() - started < 0.5
    assert len(polls) == 2
    assert all(thread_id != loop_thread for thread_id in polls)
    assert not futures[0].done()
    assert PluginHub._pending == {}


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
    monkeypatch.setattr(plugin_hub, "_find_unity_dialog_titles", probe)
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
    monkeypatch.setattr(plugin_hub, "_find_unity_dialog_titles", lambda: titles)
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
    monkeypatch.setattr(plugin_hub, "_find_unity_dialog_titles", lambda: titles)
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
