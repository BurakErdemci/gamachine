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
import sys
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

MODES = ("auto", "step")
FRESH_INSTALL_MODE = "auto"
# Before the store is read, and whenever it cannot be trusted.
FALLBACK_MODE = "step"
_SETTING_KEY = "approval_mode"

_LOCK = threading.Lock()

# The owner's red line: whenever the mode is step, no agy built-in write or
# shell command runs without a card. agy's hook reads one state file on every
# gated tool call, so the invariant enforced here is:
#
#   At every instant at which current_mode() returns "step", every live or
#   closing agy child's hook state file denies per the step grammar: it says
#   "step" (or "closed"), or it is absent or unreadable, which the hook
#   treats as deny.
#
# It holds by construction, through one ordering rule under this one lock,
# which covers both the publish of the mode and every write of that file:
#   - a change whose effective mode is step first tightens the agy state
#     (_tighten_agy_gates: write step, else remove the file, else kill the
#     children), and only then publishes;
#   - a change to auto publishes first, then loosens (the file follows the
#     published mode, rewritten by the live sessions' setters);
#   - every other writer (agy_session: the flag setter, turn start, spawn,
#     post-spawn resync, close) reads the published mode under this lock and
#     writes in the same critical section, so none can write auto after step
#     was published, and none can interleave between a tighten and its publish.
# Before this (verification round 2, 26 Sep 2026) the mode was published
# first and the file rewritten under a separate lock: overlapping flips left
# step published while the file still said auto.
#
# Reentrant: set_mode holds it while _propagate_to_live_sessions runs the agy
# flag setter, which takes it again. Lock order is GATE_LOCK, then _LOCK;
# _LOCK is never held while taking GATE_LOCK. It is taken on the event loop as
# well as on request threads, so nothing may await while holding it, and no
# code that holds it waits on the loop.
GATE_LOCK = threading.RLock()

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
    from_row = isinstance(value, str) and value in MODES
    if from_row:
        new = (value, True, False)
    elif read_ok and value is None:
        new = (FALLBACK_MODE, False, True)
    else:
        new = (FALLBACK_MODE, False, False)
    with GATE_LOCK:
        with _LOCK:
            after = _effective(new[0], new[2], from_row, _ui_secret)
        if after == "step":
            _tighten_agy_gates()
        with _LOCK:
            _store = store
            _from_row = from_row
            _mode, _stored, _fresh_install = new
            fresh = _fresh_install
    if fresh:
        logger.info("[approval-mode] startup: fresh install, %s with a UI secret, %s without",
                    FRESH_INSTALL_MODE, FALLBACK_MODE)
    else:
        logger.info("[approval-mode] startup mode=%s (stored=%s)", _mode, _stored)


def _row_auto_without_secret_locked() -> bool:
    return _from_row and _mode == "auto" and not _ui_secret


def _effective(mode: str, fresh_install: bool, from_row: bool, ui_secret: bytes) -> str:
    if fresh_install:
        return FRESH_INSTALL_MODE if ui_secret else FALLBACK_MODE
    if from_row and mode == "auto" and not ui_secret:
        return FALLBACK_MODE
    return mode


def _effective_mode_locked() -> str:
    return _effective(_mode, _fresh_install, _from_row, _ui_secret)


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
    # One critical section from persist to propagation (see GATE_LOCK): two
    # overlapping flips can no longer interleave their publish and their
    # state writes, nor leave the saved and the live mode different.
    with GATE_LOCK:
        with _LOCK:
            previous = _effective_mode_locked()
            store = _store
        if mode == "step":
            # Before step is published, never after; and before it is saved, so
            # a refused flip leaves nothing behind (a tightened gate under auto
            # only over-restricts until the next sync).
            _tighten_agy_gates()
        if store is not None:
            # Persist before publishing: a mode that is live but not saved would
            # silently revert on the next launch.
            try:
                store.set_setting(_SETTING_KEY, mode)
            except Exception:
                if mode == "step":
                    # The flip failed, so the gates follow the published mode
                    # again; else an auto-mode agy child stays denied
                    # (verification round 4).
                    _resync_agy_gates()
                raise
        with _LOCK:
            _mode, _stored, _fresh_install, _from_row = mode, True, False, False
        _propagate_to_live_sessions(mode == "auto")
    logger.warning("[approval-mode] %s -> %s (source=%s)", previous, mode, source)
    return previous


def _tighten_agy_gates() -> None:
    """Make every agy child's hook deny per the step grammar; the caller
    publishes step only after this returns. Caller holds GATE_LOCK.

    A raise propagates on purpose: the caller then never publishes step, so
    the invariant holds either way (the flip fails instead).
    """
    module = sys.modules.get("providers.agy_session")
    if module is None:
        # Never imported: this process has spawned no agy child.
        return
    module.tighten_gate_state()


def _resync_agy_gates() -> None:
    """Rewrite agy's hook state file from the published mode. Caller holds
    GATE_LOCK. Best effort: a failure leaves the file over-restrictive."""
    module = sys.modules.get("providers.agy_session")
    if module is None:
        return
    try:
        module._sync_gate_state()
    except Exception:
        logger.warning("[approval-mode] agy gate state not restored", exc_info=True)


def _propagate_to_live_sessions(auto: bool) -> None:
    """Update the auto_approve flag of registered CLI sessions in place.

    What this reaches: Claude SDK and Codex sessions read auto_approve on every
    approval request of their running process, so for them a flip bites within
    the current turn.

    agy: its built-in tools are gated by a workspace hook that every agy
    process gets at spawn, in both modes (a failed install refuses the spawn).
    The hook reads a state file on every tool call. A flip to step has
    already written that file before the mode was published
    (_tighten_agy_gates); setting the flag here rewrites it from the
    published mode, which is how a flip to auto loosens it. Either way the
    flip bites on the process's next tool call, one-shot agy sessions
    included. The caller holds GATE_LOCK.

    What it cannot reach: the one-shot CLIs (cursor, copilot, opencode, kimi)
    carry the attribute, but nothing in their running process reads it; their
    approval behaviour is fixed when the process is spawned (agent_runner
    passes `interactive=` then). Such a process keeps that behaviour for its
    own built-in tools until it exits.

    For every client, Unity MCP calls follow a flip at once, because
    /mcp-approval-request reads current_mode() on every request. Nothing here
    kills a running process.
    """
    targets = (
        ("providers.claude_sdk_session", "_SESSIONS"),
        ("providers.codex_session", "_SESSIONS"),
        ("providers.agy_session", "_SESSIONS"),
        ("providers.oneshot_cli", "_SESSIONS"),
    )
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
    # The secret decides the effective mode of a fresh install or a saved
    # auto, so setting it is a mode change like set_mode (see GATE_LOCK).
    global _ui_secret
    new = (secret or "").encode("utf-8")
    with GATE_LOCK:
        with _LOCK:
            after = _effective(_mode, _fresh_install, _from_row, new)
        if after == "step":
            _tighten_agy_gates()
        with _LOCK:
            _ui_secret = new


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
    # Tests only, and deliberately no _tighten_agy_gates: a test's leftover
    # agy session would otherwise write the real home's gate state file.
    global _mode, _stored, _store, _ui_secret, _fresh_install, _from_row
    global _warned_row_auto_without_secret
    with GATE_LOCK, _LOCK:
        _mode, _stored, _store, _ui_secret = FALLBACK_MODE, False, None, b""
        _fresh_install = _from_row = _warned_row_auto_without_secret = False
