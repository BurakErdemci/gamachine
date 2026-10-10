"""Native Unity dialogs block EditorApplication.update and plugin replies.
Measured on Windows with Unity 6000.4.4f1: the blocked editor cannot run
an in-editor probe, so the separate server must inspect native windows.
"""

import ctypes
from ctypes import wintypes
import ntpath
import sys


def find_unity_dialog_titles(process_names: tuple[str, ...] = ("unity.exe",)) -> list[str]:
    """Return visible native dialog titles owned by the requested processes."""
    if sys.platform != "win32":
        return []
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        requested_names = {name.casefold() for name in process_names}
        process_matches: dict[int, bool] = {}
        titles: list[str] = []
        callback_failed = False

        def visit(hwnd, _lparam):
            nonlocal callback_failed
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                class_name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_name, len(class_name))
                if class_name.value != "#32770":
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value not in process_matches:
                    process_matches[pid.value] = False
                    handle = kernel32.OpenProcess(0x1000, False, pid.value)
                    if handle:
                        try:
                            image_name = ctypes.create_unicode_buffer(32768)
                            size = wintypes.DWORD(len(image_name))
                            if kernel32.QueryFullProcessImageNameW(handle, 0, image_name, ctypes.byref(size)):
                                process_matches[pid.value] = ntpath.basename(image_name.value).casefold() in requested_names
                        finally:
                            kernel32.CloseHandle(handle)
                if process_matches[pid.value]:
                    length = user32.GetWindowTextLengthW(hwnd)
                    title = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, title, len(title))
                    if title.value not in titles:
                        titles.append(title.value)
                return True
            except Exception:
                # ctypes otherwise swallows callback exceptions and keeps enumerating.
                callback_failed = True
                return False

        callback = callback_type(visit)
        if not user32.EnumWindows(callback, 0) or callback_failed:
            return []
        return titles
    except Exception:
        return []
