"""Select the computer-use platform without changing unsupported-host behavior."""
import sys

from .base import Platform

_mac = None
_unsupported = None
_windows = None


def current() -> Platform:
    from .macos import MacPlatform, UnsupportedPlatform

    global _mac, _unsupported, _windows
    if sys.platform == "win32":
        from .windows import WindowsPlatform

        if _windows is None:
            _windows = WindowsPlatform()
        return _windows
    if _mac is None:
        _mac = MacPlatform()
        _unsupported = UnsupportedPlatform()
    return _mac if sys.platform == "darwin" else _unsupported


def valid_app_id(value) -> bool:
    return current().valid_app_id(value)
