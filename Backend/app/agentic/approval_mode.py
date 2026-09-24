"""The one global approval mode: "auto" (no approval cards anywhere) or "step".

Owner decision, 25 Sep 2026: in auto mode no card appears for Gamachine's own
agents nor for external MCP clients (Claude Code connected straight to the
Unity MCP server). Step mode is never removed or weakened.

The mode used to live in renderer localStorage and travel per request as
`generation_mode`; external MCP clients carry no request, so their calls could
never be auto. It now lives here, persisted in the app database, and every
path reads it through `current_mode()`.

Who may WRITE it: only the app UI. The write endpoint demands a second secret
(`ui secret`) that Electron main hands the backend over stdin at spawn time.
LOCAL_APP_TOKEN is not enough on its own: it sits in the Unity MCP server's
environment and in a 0600 file that every model-run child can read, so any of
them could otherwise flip itself into auto mode.

Known limit: the persisted value is only as trustworthy as the user's data
directory; a same-user process that edits the SQLite file changes the mode
from the NEXT launch on. The value is therefore read once at startup and logged.
"""

from __future__ import annotations

import hmac
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

MODES = ("auto", "step")
DEFAULT_MODE = "step"
_SETTING_KEY = "approval_mode"

_LOCK = threading.Lock()
_mode: str = DEFAULT_MODE
_stored: bool = False
_store: Any = None
_ui_secret: bytes = b""


def bind_store(store: Any) -> None:
    """Attach the persistence layer (DatabaseManager) and load the saved mode.

    Anything other than an exact stored "auto"/"step" leaves the default: an
    unreadable or tampered value must fall to step, never to auto.
    """
    global _store, _mode, _stored
    value: Optional[str] = None
    try:
        value = store.get_setting(_SETTING_KEY)
    except Exception as exc:
        logger.error("[approval-mode] saved mode could not be read: %s", exc)
    with _LOCK:
        _store = store
        if isinstance(value, str) and value in MODES:
            _mode, _stored = value, True
        else:
            _mode, _stored = DEFAULT_MODE, False
    logger.info("[approval-mode] startup mode=%s (stored=%s)", _mode, _stored)


def current_mode() -> str:
    with _LOCK:
        return _mode


def is_auto() -> bool:
    return current_mode() == "auto"


def is_stored() -> bool:
    """False until a value was ever saved: the renderer migrates its old value then."""
    with _LOCK:
        return _stored


def set_mode(mode: str, source: str = "ui") -> str:
    """Persist and apply a new mode; returns the previous one."""
    if mode not in MODES:
        raise ValueError(f"unknown approval mode: {mode!r}")
    global _mode, _stored
    with _LOCK:
        previous = _mode
        store = _store
    if store is not None:
        # Persist first: a mode that is live but not saved would silently
        # revert on the next launch.
        store.set_setting(_SETTING_KEY, mode)
    with _LOCK:
        _mode, _stored = mode, True
    logger.warning("[approval-mode] %s -> %s (source=%s)", previous, mode, source)
    _propagate_to_live_sessions(mode == "auto")
    return previous


def _propagate_to_live_sessions(auto: bool) -> None:
    """Running CLI sessions snapshot the mode per turn; update them in place so a
    flip applies to the rest of the current turn too (step must bite at once)."""
    targets = (
        ("providers.claude_sdk_session", "_SESSIONS"),
        ("providers.codex_session", "_SESSIONS"),
        ("providers.agy_session", "_SESSIONS"),
        ("providers.oneshot_cli", "_SESSIONS"),
    )
    import sys

    for module_name, attr in targets:
        module = sys.modules.get(module_name)
        if module is None:
            # Never imported means no live session; importing it here would only
            # pay a heavy import for nothing.
            continue
        try:
            for session in list(getattr(module, attr, {}).values()):
                if hasattr(session, "auto_approve"):
                    session.auto_approve = auto
        except Exception as exc:
            logger.warning("[approval-mode] %s sessions not updated: %s", module_name, exc)


# ── UI secret ────────────────────────────────────────────────────────────────

def set_ui_secret(secret: str) -> None:
    global _ui_secret
    with _LOCK:
        _ui_secret = (secret or "").encode("utf-8")


def ui_secret_configured() -> bool:
    with _LOCK:
        return bool(_ui_secret)


def check_ui_secret(presented: str) -> bool:
    """Fail-closed: with no secret configured nothing matches, not even ''."""
    with _LOCK:
        expected = _ui_secret
    if not expected:
        return False
    return hmac.compare_digest(str(presented or "").encode("utf-8"), expected)


def _reset_for_tests() -> None:
    global _mode, _stored, _store, _ui_secret
    with _LOCK:
        _mode, _stored, _store, _ui_secret = DEFAULT_MODE, False, None, b""
