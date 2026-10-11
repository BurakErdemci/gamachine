"""Capture native editor windows without asking Unity's main loop to run."""

import base64
import ctypes
from ctypes import wintypes
import ntpath
import struct
import sys
import zlib


class CaptureError(Exception):
    """A native window could not be captured."""


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode top-down RGB rows as an 8-bit, true-color PNG."""
    if width <= 0 or height <= 0 or len(rgb) != width * height * 3:
        raise ValueError("PNG dimensions must match the RGB buffer.")

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    stride = width * 3
    rows = b"".join(b"\x00" + rgb[offset:offset + stride] for offset in range(0, len(rgb), stride))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6))
            + chunk(b"IEND", b""))


def find_unity_main_window(project: str) -> dict | None:
    """Find the largest visible, ownerless editor window for this exact project."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        for name in ("IsWindowVisible", "IsIconic"):
            function = getattr(user32, name)
            function.argtypes = [wintypes.HWND]
            function.restype = wintypes.BOOL
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        processes: dict[int, bool] = {}
        windows: list[tuple[int, dict]] = []
        callback_failed = False

        def visit(hwnd, _lparam):
            nonlocal callback_failed
            try:
                if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):
                    return True
                class_name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_name, len(class_name))
                if class_name.value != "UnityContainerWndClass":
                    return True
                title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
                user32.GetWindowTextW(hwnd, title, len(title))
                if title.value.split(" - ", 1)[0] != project:
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value not in processes:
                    processes[pid.value] = False
                    handle = kernel32.OpenProcess(0x1000, False, pid.value)
                    if handle:
                        try:
                            image = ctypes.create_unicode_buffer(32768)
                            size = wintypes.DWORD(len(image))
                            if kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                                processes[pid.value] = ntpath.basename(image.value).casefold() == "unity.exe"
                        finally:
                            kernel32.CloseHandle(handle)
                if processes[pid.value]:
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
                        windows.append((area, {
                            "hwnd": int(hwnd), "title": title.value,
                            "minimized": bool(user32.IsIconic(hwnd)),
                        }))
                return True
            except Exception:
                # ctypes swallows exceptions crossing a native callback boundary.
                callback_failed = True
                return False

        if not user32.EnumWindows(callback_type(visit), 0) or callback_failed:
            return None
        return max(windows, key=lambda item: item[0])[1] if windows else None
    except Exception:
        return None


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 1)]


def capture_window(hwnd: int, max_size: int) -> dict:
    """Print a window into DIB memory and return a scaled PNG, without disk I/O."""
    if sys.platform != "win32":
        raise CaptureError("Window capture is only implemented on Windows.")
    if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
        raise CaptureError("max_size must be a positive integer.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.SetStretchBltMode.restype = ctypes.c_int
    gdi32.SetBrushOrgEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.POINTER(wintypes.POINT)]
    gdi32.SetBrushOrgEx.restype = wintypes.BOOL
    gdi32.StretchBlt.argtypes = [
        wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
    ]
    gdi32.StretchBlt.restype = wintypes.BOOL
    gdi32.GdiFlush.argtypes = []
    gdi32.GdiFlush.restype = wintypes.BOOL
    set_dpi = getattr(user32, "SetThreadDpiAwarenessContext", None)
    if set_dpi is not None:
        set_dpi.argtypes = [ctypes.c_void_p]
        set_dpi.restype = ctypes.c_void_p

    previous_dpi = None
    window_dc = None
    surfaces = []

    def surface(width, height):
        dc = gdi32.CreateCompatibleDC(window_dc)
        if not dc:
            raise CaptureError("Could not create a capture memory DC.")
        resource = [dc, None, None]
        surfaces.append(resource)
        info = _BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(window_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        resource[1] = bitmap
        if not bitmap or not bits.value:
            raise CaptureError("Could not create a capture bitmap.")
        previous = gdi32.SelectObject(dc, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            raise CaptureError("Could not select the capture bitmap.")
        resource[2] = previous
        return dc, bits

    try:
        if set_dpi is not None:
            previous_dpi = set_dpi(ctypes.c_void_p(-4))
        if user32.IsIconic(hwnd):
            raise CaptureError("Unity window is minimized; restore it to capture.")
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise CaptureError("Could not get the Unity window rectangle.")
        full_width, full_height = rect.right - rect.left, rect.bottom - rect.top
        if full_width <= 0 or full_height <= 0:
            raise CaptureError("Unity window has an empty rectangle.")
        window_dc = user32.GetDC(hwnd)
        if not window_dc:
            raise CaptureError("Could not get the Unity window DC.")
        memory_dc, bits = surface(full_width, full_height)
        if not user32.PrintWindow(hwnd, memory_dc, 2):
            raise CaptureError("PrintWindow could not capture the Unity window.")
        width, height = full_width, full_height
        longest = max(width, height)
        if longest > max_size:
            width = max(1, round(full_width * max_size / longest))
            height = max(1, round(full_height * max_size / longest))
            scaled_dc, bits = surface(width, height)
            if not gdi32.SetStretchBltMode(scaled_dc, 4):
                raise CaptureError("Could not set the capture scaling mode.")
            if not gdi32.SetBrushOrgEx(scaled_dc, 0, 0, None):
                raise CaptureError("Could not set the capture scaling origin.")
            if not gdi32.StretchBlt(scaled_dc, 0, 0, width, height,
                                   memory_dc, 0, 0, full_width, full_height, 0x00CC0020):
                raise CaptureError("Could not scale the Unity window capture.")
        # Finish batched GDI writes before accessing the DIB's memory directly.
        if not gdi32.GdiFlush():
            raise CaptureError("Could not flush the capture bitmap.")
        bgra = ctypes.string_at(bits, width * height * 4)
        rgb = bytearray(width * height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
        return {
            "image_base64": base64.b64encode(encode_png(width, height, bytes(rgb))).decode("ascii"),
            "mime": "image/png", "width": width, "height": height,
            "full_width": full_width, "full_height": full_height,
        }
    finally:
        for dc, bitmap, previous in reversed(surfaces):
            if previous:
                gdi32.SelectObject(dc, previous)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(dc)
        if window_dc:
            user32.ReleaseDC(hwnd, window_dc)
        if previous_dpi:
            set_dpi(previous_dpi)
