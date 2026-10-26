"""Select the computer-use platform without changing unsupported-host behavior."""
import sys

from .base import Platform

_platforms = {}


def current() -> Platform:
    # Keyed by sys.platform and filled with setdefault: concurrent first calls never see a half-set cache.
    key = sys.platform
    found = _platforms.get(key)
    if found is None:
        if key == "win32":
            from .windows import WindowsPlatform

            found = WindowsPlatform()
        else:
            from .macos import MacPlatform, UnsupportedPlatform

            found = MacPlatform() if key == "darwin" else UnsupportedPlatform()
        found = _platforms.setdefault(key, found)
    return found


def valid_app_id(value) -> bool:
    return current().valid_app_id(value)
