"""Native Windows consent dialog with a bounded lifetime."""
import asyncio
import ctypes
import itertools
from ctypes import wintypes


_titles = itertools.count(1)


def _user32():
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    api.MessageBoxW.restype = ctypes.c_int
    api.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    api.FindWindowW.restype = wintypes.HWND
    api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    api.PostMessageW.restype = wintypes.BOOL
    return api


async def ask(title, text, timeout=150):
    api = _user32()
    title = f"{title} #{next(_titles)}"
    # YESNO | ICONWARNING | TOPMOST | SETFOREGROUND | DEFBUTTON2
    flags = 0x4 | 0x30 | 0x40000 | 0x10000 | 0x100
    task = asyncio.create_task(asyncio.to_thread(api.MessageBoxW, None, text, title, flags))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout) == 6
    except asyncio.TimeoutError:
        hwnd = api.FindWindowW(None, title)
        if hwnd:
            api.PostMessageW(hwnd, 0x10, 0, 0)  # WM_CLOSE
        return False
