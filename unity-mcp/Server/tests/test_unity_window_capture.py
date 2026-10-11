"""In-memory editor captures, PNG encoding, and project-scoped errors."""

import base64
import ctypes
from ctypes import wintypes
import importlib
import json
import struct
import sys
import threading
import time
from unittest.mock import AsyncMock, Mock
import uuid
import zlib

import pytest

from transport.unity_window_capture import CaptureError, capture_window, encode_png

tool = importlib.import_module("services.tools.unity_window_capture")
native = importlib.import_module("transport.unity_window_capture")


@pytest.mark.parametrize("count", [0, 1, 2])
def test_single_window_compatibility_rejects_ambiguity(monkeypatch, count):
    windows = [{"hwnd": index, "title": "ProjA - Scene - Unity", "minimized": False} for index in range(count)]
    enumerate_windows = Mock(return_value=windows)
    monkeypatch.setattr(native, "find_unity_main_windows", enumerate_windows)
    assert native.find_unity_main_window("ProjA") == (windows[0] if count == 1 else None)
    enumerate_windows.assert_called_once_with("ProjA")


def _decode_png(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    compressed = bytearray()
    width = height = None
    chunks = []
    while offset < len(png):
        size, kind = struct.unpack_from(">I4s", png, offset)
        body = png[offset + 8:offset + 8 + size]
        crc = struct.unpack_from(">I", png, offset + 8 + size)[0]
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        chunks.append(kind)
        if kind == b"IHDR":
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            assert (depth, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)
        elif kind == b"IDAT":
            compressed.extend(body)
        offset += size + 12
    assert offset == len(png)
    assert chunks == [b"IHDR", b"IDAT", b"IEND"]
    raw = zlib.decompress(compressed)
    stride = width * 3 + 1
    assert len(raw) == stride * height
    assert all(raw[row * stride] == 0 for row in range(height))
    return width, height, b"".join(raw[row * stride + 1:(row + 1) * stride] for row in range(height))


@pytest.mark.parametrize("width,height", [(1, 1), (3, 2), (17, 5)])
def test_encode_png_round_trip(width, height):
    rgb = bytes(index % 256 for index in range(width * height * 3))
    assert _decode_png(encode_png(width, height, rgb)) == (width, height, rgb)


@pytest.mark.asyncio
async def test_non_windows_error(monkeypatch):
    monkeypatch.setattr(tool.sys, "platform", "linux")
    target = AsyncMock()
    monkeypatch.setattr(tool, "_target_project", target)
    assert await tool.unity_window_capture(None) == {
        "success": False,
        "error": "unity_window_capture is only implemented on Windows so far.",
    }
    target.assert_not_awaited()


@pytest.fixture
def native_capture(monkeypatch):
    monkeypatch.setattr(tool.sys, "platform", "win32")
    target = AsyncMock(return_value="ProjA")
    window = Mock(return_value=[{"hwnd": 123, "title": "ProjA - Scene - Unity", "minimized": False}])
    dialogs = Mock(return_value=[
        {"title": "Save scene", "buttons": ["Save", "Cancel"], "project": "ProjA", "hwnd": 7},
        {"title": "Other project", "buttons": ["Yes"], "project": "ProjB", "hwnd": 8},
    ])
    capture = Mock(return_value={
        "image_base64": base64.b64encode(encode_png(1, 1, b"\x10\x20\x30")).decode("ascii"),
        "mime": "image/png", "width": 1, "height": 1, "full_width": 2, "full_height": 2,
    })
    for name, value in [("_target_project", target), ("find_unity_main_windows", window),
                        ("find_unity_dialogs", dialogs), ("capture_window", capture)]:
        monkeypatch.setattr(tool, name, value)
    return target, window, dialogs, capture


@pytest.mark.asyncio
async def test_no_project(native_capture):
    target, window, dialogs, capture = native_capture
    target.return_value = None
    assert await tool.unity_window_capture(None) == {"success": False, "error": tool._NO_PROJECT}
    window.assert_not_called()
    dialogs.assert_not_called()
    capture.assert_not_called()


@pytest.mark.asyncio
async def test_no_window(native_capture):
    _, window, _, capture = native_capture
    window.return_value = []
    assert await tool.unity_window_capture(None) == {
        "success": False, "error": "No Unity editor window found for project 'ProjA'.",
    }
    window.assert_called_once_with("ProjA")
    capture.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_windows_refuses_capture(native_capture):
    _, window, dialogs, capture = native_capture
    window.return_value.append({"hwnd": 456, "title": "ProjA - Other Scene - Unity", "minimized": False})
    assert await tool.unity_window_capture(None) == {
        "success": False,
        "error": "Several Unity editor windows show project 'ProjA'; close the other editor or rename one project before capturing.",
    }
    window.assert_called_once_with("ProjA")
    dialogs.assert_not_called()
    capture.assert_not_called()


@pytest.mark.asyncio
async def test_capture_error_lists_only_target_dialogs(native_capture):
    _, _, _, capture = native_capture
    capture.side_effect = CaptureError("Unity window is minimized; restore it to capture.")
    assert await tool.unity_window_capture(None) == {
        "success": False, "error": str(capture.side_effect),
        "data": {"dialogs": [{"title": "Save scene", "buttons": ["Save", "Cancel"]}]},
    }


@pytest.mark.asyncio
async def test_success_returns_image_and_metadata_without_base64_text(native_capture):
    _, _, _, capture = native_capture
    result = await tool.unity_window_capture(None, 256)
    capture.assert_called_once_with(123, 256)
    text, image = result.content
    metadata = json.loads(text.text)
    assert metadata["success"] is True
    assert metadata["message"] == "Captured the Unity editor window."
    assert metadata["data"]["title"] == "ProjA - Scene - Unity"
    assert metadata["data"]["dialogs"] == [{"title": "Save scene", "buttons": ["Save", "Cancel"]}]
    assert "image_base64" not in text.text
    assert image.mimeType == "image/png"
    assert _decode_png(base64.b64decode(image.data)) == (1, 1, b"\x10\x20\x30")


@pytest.mark.asyncio
@pytest.mark.parametrize("max_size", [255, 1921, True, 256.5])
async def test_invalid_size_never_calls_native_functions(native_capture, max_size):
    target, window, dialogs, capture = native_capture
    assert (await tool.unity_window_capture(None, max_size))["success"] is False
    target.assert_not_awaited()
    window.assert_not_called()
    dialogs.assert_not_called()
    capture.assert_not_called()


@pytest.mark.asyncio
async def test_native_work_runs_outside_event_loop(native_capture):
    _, window, dialogs, capture = native_capture
    loop_thread = threading.get_ident()
    threads = []
    for native in (window, dialogs, capture):
        value = native.return_value

        def call(*args, value=value):
            threads.append(threading.get_ident())
            return value

        native.side_effect = call
    await tool.unity_window_capture(None)
    assert len(threads) == 3
    assert all(thread != loop_thread for thread in threads)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 native capture test")
def test_capture_real_message_box():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    title = "Gamachine capture test " + uuid.uuid4().hex
    # One dialog thread per test, closed and joined before returning.
    thread = threading.Thread(
        target=lambda: user32.MessageBoxW(None, "Capture this dialog with visible text and buttons.", title, 0),
        daemon=True,
    )
    thread.start()
    hwnd = None
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            hwnd = user32.FindWindowW("#32770", title)
            if hwnd:
                break
            time.sleep(0.05)
        assert hwnd
        assert user32.GetWindowThreadProcessId(hwnd, None) == thread.native_id
        captured = capture_window(hwnd, 256)
        width, height, rgb = _decode_png(base64.b64decode(captured["image_base64"]))
        assert captured["mime"] == "image/png"
        assert (width, height) == (captured["width"], captured["height"])
        assert max(width, height) <= 256
        assert captured["full_width"] >= width and captured["full_height"] >= height
        assert any(rgb[channel::3] != rgb[channel:channel + 1] * (width * height) for channel in range(3))
    finally:
        hwnd = user32.FindWindowW("#32770", title)
        if hwnd and user32.GetWindowThreadProcessId(hwnd, None) == thread.native_id:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        thread.join(5.0)
    assert not thread.is_alive()
