"""A dedicated Win32 message thread owns the emergency halt shortcut."""
import ctypes
import threading
import time
from ctypes import wintypes


class HaltHotkey:
    def __init__(self):
        self.halted_at = None
        self.registered = False
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._thread = None
        self._thread_id = None
        self._api = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(2):
            self._stopping.set()
            self.registered = False

    def _run(self):
        registered = False
        try:
            api = ctypes.WinDLL("user32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentThreadId.argtypes = []
            kernel.GetCurrentThreadId.restype = wintypes.DWORD
            api.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            api.RegisterHotKey.restype = wintypes.BOOL
            api.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            api.UnregisterHotKey.restype = wintypes.BOOL
            api.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                        wintypes.UINT, wintypes.UINT]
            api.GetMessageW.restype = wintypes.BOOL
            api.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                         wintypes.UINT, wintypes.UINT, wintypes.UINT]
            api.PeekMessageW.restype = wintypes.BOOL
            api.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                              wintypes.WPARAM, wintypes.LPARAM]
            api.PostThreadMessageW.restype = wintypes.BOOL
            self._api = api
            self._thread_id = kernel.GetCurrentThreadId()
            msg = wintypes.MSG()
            # Create the queue before announcing readiness, so stop can post WM_QUIT.
            api.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            registered = bool(api.RegisterHotKey(None, 1, 0x2 | 0x1 | 0x4000, 0x1B))
            self.registered = registered and not self._stopping.is_set()
            self._ready.set()
            if not self.registered:
                return
            while api.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x312 and msg.wParam == 1:  # WM_HOTKEY
                    self.halted_at = time.monotonic()
        except (OSError, AttributeError):
            self.registered = False
        finally:
            if registered:
                self._api.UnregisterHotKey(None, 1)
            self.registered = False
            self._ready.set()

    def stop(self):
        self._stopping.set()
        if self._api is not None and self._thread_id is not None:
            self._api.PostThreadMessageW(self._thread_id, 0x12, 0, 0)  # WM_QUIT
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.registered = False

    def clear(self):
        self.halted_at = None
