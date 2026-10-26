"""Read-only Win32 facts with explicit, pointer-sized ctypes signatures."""
import ctypes as _ct
from ctypes import wintypes as _wt


class _PROCESSENTRY32W(_ct.Structure):
    _fields_ = [
        ("dwSize", _wt.DWORD), ("cntUsage", _wt.DWORD), ("th32ProcessID", _wt.DWORD),
        ("th32DefaultHeapID", _ct.c_size_t), ("th32ModuleID", _wt.DWORD),
        ("cntThreads", _wt.DWORD), ("th32ParentProcessID", _wt.DWORD),
        ("pcPriClassBase", _wt.LONG), ("dwFlags", _wt.DWORD), ("szExeFile", _wt.WCHAR * 260),
    ]


class _SID_AND_ATTRIBUTES(_ct.Structure):
    _fields_ = [("Sid", _ct.c_void_p), ("Attributes", _wt.DWORD)]


def _bind(library, name, args, result):
    function = getattr(library, name)
    function.argtypes = args
    function.restype = result
    return function


try:
    _kernel = _ct.WinDLL("kernel32", use_last_error=True)
    _advapi = _ct.WinDLL("advapi32", use_last_error=True)
    _user = _ct.WinDLL("user32", use_last_error=True)
    _dwm = _ct.WinDLL("dwmapi", use_last_error=True)
    _ENUMPROC = _ct.WINFUNCTYPE(_wt.BOOL, _wt.HWND, _wt.LPARAM)
    _open_process = _bind(_kernel, "OpenProcess", [_wt.DWORD, _wt.BOOL, _wt.DWORD], _wt.HANDLE)
    _close = _bind(_kernel, "CloseHandle", [_wt.HANDLE], _wt.BOOL)
    _query_image = _bind(_kernel, "QueryFullProcessImageNameW",
                         [_wt.HANDLE, _wt.DWORD, _wt.LPWSTR, _ct.POINTER(_wt.DWORD)], _wt.BOOL)
    _long_path = _bind(_kernel, "GetLongPathNameW", [_wt.LPCWSTR, _wt.LPWSTR, _wt.DWORD], _wt.DWORD)
    _current_process = _bind(_kernel, "GetCurrentProcess", [], _wt.HANDLE)
    _snapshot = _bind(_kernel, "CreateToolhelp32Snapshot", [_wt.DWORD, _wt.DWORD], _wt.HANDLE)
    _process_first = _bind(_kernel, "Process32FirstW",
                           [_wt.HANDLE, _ct.POINTER(_PROCESSENTRY32W)], _wt.BOOL)
    _process_next = _bind(_kernel, "Process32NextW",
                          [_wt.HANDLE, _ct.POINTER(_PROCESSENTRY32W)], _wt.BOOL)
    _open_token = _bind(_advapi, "OpenProcessToken",
                        [_wt.HANDLE, _wt.DWORD, _ct.POINTER(_wt.HANDLE)], _wt.BOOL)
    _token_info = _bind(_advapi, "GetTokenInformation",
                        [_wt.HANDLE, _ct.c_int, _ct.c_void_p, _wt.DWORD, _ct.POINTER(_wt.DWORD)], _wt.BOOL)
    _sid_count = _bind(_advapi, "GetSidSubAuthorityCount", [_ct.c_void_p], _ct.POINTER(_wt.BYTE))
    _sid_rid = _bind(_advapi, "GetSidSubAuthority",
                     [_ct.c_void_p, _wt.DWORD], _ct.POINTER(_wt.DWORD))
    _dpi = _bind(_user, "SetProcessDpiAwarenessContext", [_wt.HANDLE], _wt.BOOL)
    _foreground = _bind(_user, "GetForegroundWindow", [], _wt.HWND)
    _window_pid = _bind(_user, "GetWindowThreadProcessId",
                        [_wt.HWND, _ct.POINTER(_wt.DWORD)], _wt.DWORD)
    _enum_windows = _bind(_user, "EnumWindows", [_ENUMPROC, _wt.LPARAM], _wt.BOOL)
    _enum_children = _bind(_user, "EnumChildWindows", [_wt.HWND, _ENUMPROC, _wt.LPARAM], _wt.BOOL)
    _visible = _bind(_user, "IsWindowVisible", [_wt.HWND], _wt.BOOL)
    _iconic = _bind(_user, "IsIconic", [_wt.HWND], _wt.BOOL)
    _owner = _bind(_user, "GetWindow", [_wt.HWND, _wt.UINT], _wt.HWND)
    _title = _bind(_user, "GetWindowTextW", [_wt.HWND, _wt.LPWSTR, _ct.c_int], _ct.c_int)
    _class_name = _bind(_user, "GetClassNameW", [_wt.HWND, _wt.LPWSTR, _ct.c_int], _ct.c_int)
    _rect = _bind(_user, "GetWindowRect", [_wt.HWND, _ct.POINTER(_wt.RECT)], _wt.BOOL)
    _dwm_attr = _bind(_dwm, "DwmGetWindowAttribute",
                      [_wt.HWND, _wt.DWORD, _ct.c_void_p, _wt.DWORD], _wt.LONG)
    _available = True
except (AttributeError, OSError):
    _available = False


def set_dpi_awareness():
    try:
        if _available:
            _dpi(_wt.HANDLE(-4))
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        pass


def image_path(pid) -> str | None:
    process = None
    try:
        if not _available:
            return None
        process = _open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return None
        path = _ct.create_unicode_buffer(32768)
        size = _wt.DWORD(len(path))
        if not _query_image(process, 0, path, _ct.byref(size)):
            return None
        expanded = _ct.create_unicode_buffer(32768)
        length = _long_path(path.value, expanded, len(expanded))
        return expanded.value if 0 < length < len(expanded) else None
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        return None
    finally:
        if process:
            _close(process)


def _read_token(process, information_class):
    token = _wt.HANDLE()
    try:
        if not _open_token(process, 0x0008, _ct.byref(token)):  # TOKEN_QUERY
            return None
        size = _wt.DWORD()
        _token_info(token, information_class, None, 0, _ct.byref(size))
        if not size.value:
            return None
        buffer = _ct.create_string_buffer(size.value)
        if not _token_info(token, information_class, buffer, len(buffer), _ct.byref(size)):
            return None
        if information_class == 20:  # TokenElevation
            return bool(_ct.cast(buffer, _ct.POINTER(_wt.DWORD)).contents.value)
        label = _ct.cast(buffer, _ct.POINTER(_SID_AND_ATTRIBUTES)).contents
        count = _sid_count(label.Sid)
        if not count or count.contents.value == 0:
            return None
        rid = _sid_rid(label.Sid, count.contents.value - 1)
        return rid.contents.value if rid else None
    finally:
        if token.value:
            _close(token)


def integrity(pid) -> int | None:
    process = None
    try:
        if not _available:
            return None
        process = _open_process(0x1000, False, pid)
        return _read_token(process, 25) if process else None  # TokenIntegrityLevel
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        return None
    finally:
        if process:
            _close(process)


def self_elevated() -> bool:
    try:
        if not _available:
            return False
        value = _read_token(_current_process(), 20)
        return True if value is None else bool(value)
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        return True


def parent_pids() -> dict[int, int]:
    snapshot = None
    try:
        if not _available:
            return {}
        snapshot = _snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == _ct.c_void_p(-1).value:
            snapshot = None
            return {}
        entry = _PROCESSENTRY32W()
        entry.dwSize = _ct.sizeof(entry)
        if not _process_first(snapshot, _ct.byref(entry)):
            return {}
        parents = {}
        while True:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            if not _process_next(snapshot, _ct.byref(entry)):
                return parents if _ct.get_last_error() == 18 else {}  # ERROR_NO_MORE_FILES
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        return {}
    finally:
        if snapshot:
            _close(snapshot)


def foreground_window() -> int | None:
    try:
        return (_foreground() or None) if _available else None
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        return None


def window_pid(hwnd) -> int | None:
    try:
        if not _available:
            return None
        pid = _wt.DWORD()
        return (pid.value or None) if _window_pid(hwnd, _ct.byref(pid)) else None
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        return None


def top_level_windows() -> list[int] | None:
    try:
        if not _available:
            return None
        windows = []

        @_ENUMPROC
        def collect(hwnd, _):
            windows.append(hwnd)
            return True

        return windows if _enum_windows(collect, 0) else None
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        return None


def window_info(hwnd) -> dict | None:
    try:
        if not _available:
            return None
        cloaked = _wt.DWORD()
        if _dwm_attr(hwnd, 14, _ct.byref(cloaked), _ct.sizeof(cloaked)) != 0:
            return None
        bounds = _wt.RECT()
        if (_dwm_attr(hwnd, 9, _ct.byref(bounds), _ct.sizeof(bounds)) != 0
                and not _rect(hwnd, _ct.byref(bounds))):
            return None
        title = _ct.create_unicode_buffer(32768)
        class_name = _ct.create_unicode_buffer(256)
        _ct.set_last_error(0)
        if not _title(hwnd, title, len(title)) and _ct.get_last_error():
            return None
        if not _class_name(hwnd, class_name, len(class_name)):
            return None
        return {
            "visible": bool(_visible(hwnd)), "cloaked": bool(cloaked.value),
            "minimized": bool(_iconic(hwnd)), "owner": _owner(hwnd, 4) or 0,  # GW_OWNER
            "title": title.value, "class_name": class_name.value,
            "bounds": (bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top),
        }
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        return None


def child_windows(hwnd) -> list[int]:
    try:
        if not _available:
            return []
        windows = []

        @_ENUMPROC
        def collect(child, _):
            windows.append(child)
            return True

        # EnumChildWindows has no meaningful return value, including for no children.
        _enum_children(hwnd, collect, 0)
        return windows
    except (OSError, ValueError, TypeError, _ct.ArgumentError):
        return []
