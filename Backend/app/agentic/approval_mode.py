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

Default (owner decision, 25 Sep 2026): a fresh install, where nothing was ever
saved, starts in auto. A saved choice always wins and survives restarts.
Exception: a process that holds no UI secret (Docker backend, which Electron
never spawns; the uvicorn reload worker, where main's __main__ block never
ran) could never be switched out of auto, so its fresh-install mode is step.
That is decided on every read, not in bind_store(): main binds the store at
import time and only reads the secret later, in __main__.
The same holds for a SAVED auto (external audit 2026-09-25): such a process
reads it as step. The row itself is left alone, so the real app, which has a
secret, still sees the user's choice.

Known limit: the persisted value is only as trustworthy as the user's data
directory; a same-user process that edits the SQLite file (or deletes the row,
which now reads as a fresh install) changes the mode from the NEXT launch on.
The value is therefore read once at startup and logged.
"""

from __future__ import annotations

import hmac
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

MODES = ("auto", "step")
FRESH_INSTALL_MODE = "auto"
# Before the store is read, and whenever it cannot be trusted.
FALLBACK_MODE = "step"
_SETTING_KEY = "approval_mode"

_LOCK = threading.Lock()
_mode: str = FALLBACK_MODE
_stored: bool = False
# A clean read found no saved row; the effective mode then depends on whether a
# UI secret is configured at the time of asking.
_fresh_install: bool = False
# _mode came from the saved row (not from set_mode in this process).
_from_row: bool = False
_warned_row_auto_without_secret: bool = False
_store: Any = None
_ui_secret: bytes = b""


def bind_store(store: Any) -> None:
    """Attach the persistence layer (DatabaseManager) and load the saved mode.

    Only a clean read that finds no row at all is a fresh install. An
    unreadable or tampered value must fall to step, never fail open to auto.
    """
    global _store, _mode, _stored, _fresh_install, _from_row
    value: Optional[str] = None
    read_ok = False
    try:
        value = store.get_setting(_SETTING_KEY)
        read_ok = True
    except Exception as exc:
        logger.error("[approval-mode] saved mode could not be read: %s", exc)
    with _LOCK:
        _store = store
        _from_row = isinstance(value, str) and value in MODES
        if _from_row:
            _mode, _stored, _fresh_install = value, True, False
        elif read_ok and value is None:
            _mode, _stored, _fresh_install = FALLBACK_MODE, False, True
        else:
            _mode, _stored, _fresh_install = FALLBACK_MODE, False, False
        fresh = _fresh_install
    if fresh:
        logger.info("[approval-mode] startup: fresh install, %s with a UI secret, %s without",
                    FRESH_INSTALL_MODE, FALLBACK_MODE)
    else:
        logger.info("[approval-mode] startup mode=%s (stored=%s)", _mode, _stored)


def _row_auto_without_secret_locked() -> bool:
    return _from_row and _mode == "auto" and not _ui_secret


def _effective_mode_locked() -> str:
    if _fresh_install:
        return FRESH_INSTALL_MODE if _ui_secret else FALLBACK_MODE
    if _row_auto_without_secret_locked():
        return FALLBACK_MODE
    return _mode


def current_mode() -> str:
    global _warned_row_auto_without_secret
    with _LOCK:
        mode = _effective_mode_locked()
        warn = _row_auto_without_secret_locked() and not _warned_row_auto_without_secret
        if warn:
            _warned_row_auto_without_secret = True
    if warn:
        logger.warning("[approval-mode] saved mode is auto but no UI secret is configured, "
                       "so nothing could switch it off; running as %s", FALLBACK_MODE)
    return mode


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
    global _mode, _stored, _fresh_install, _from_row
    with _LOCK:
        previous = _effective_mode_locked()
        store = _store
    if store is not None:
        # Persist first: a mode that is live but not saved would silently
        # revert on the next launch.
        store.set_setting(_SETTING_KEY, mode)
    with _LOCK:
        _mode, _stored, _fresh_install, _from_row = mode, True, False, False
    logger.warning("[approval-mode] %s -> %s (source=%s)", previous, mode, source)
    _propagate_to_live_sessions(mode == "auto")
    return previous


def _propagate_to_live_sessions(auto: bool) -> None:
    """Update the auto_approve flag of registered CLI sessions in place.

    What this reaches: Claude SDK and Codex sessions read auto_approve on every
    approval request of their running process, so for them a flip bites within
    the current turn.

    What it cannot reach: agy and the one-shot CLIs (cursor, copilot, opencode,
    kimi) carry the attribute, but nothing in their running process reads it;
    their approval behaviour is fixed when the process is spawned (agent_runner
    passes `interactive=` then, agy_provider's one-shot analyze hard-codes
    auto). Such a process keeps that behaviour for its own built-in tools until
    it exits. Only its Unity MCP calls follow a flip at once, because
    /mcp-approval-request reads current_mode() on every request. Nothing here
    kills a running process.
    """
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
    global _mode, _stored, _store, _ui_secret, _fresh_install, _from_row
    global _warned_row_auto_without_secret
    with _LOCK:
        _mode, _stored, _store, _ui_secret = FALLBACK_MODE, False, None, b""
        _fresh_install = _from_row = _warned_row_auto_without_secret = False
