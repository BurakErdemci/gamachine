"""Latest computer action per conversation, for the desktop's live presence.

In memory only: the desktop polls it once a second to decide whether to show
the live indicator and which window to mirror. Only calls that were let
through (allowed outright or approved on a card) count: a denied or pending
call drove nothing, and mirroring a refused app's window would show the user
something the agent was never allowed to touch.
"""
import threading
import time

ACTIVE_SECONDS = 15.0
# A conversation silent this long is forgotten, so the map cannot grow forever.
FORGET_SECONDS = 3600.0
RECORDED_DECISIONS = ("allow", "approved")

_lock = threading.Lock()
_conversations = {}


def _int_or_none(value):
    return value if type(value) is int else None


def _str_or_none(value):
    return value if isinstance(value, str) and value else None


def record(conversation_id, tool, params, decision, now=None):
    """Remember one computer call; ignored unless it was let through."""
    if decision not in RECORDED_DECISIONS:
        return
    if type(conversation_id) is not int or conversation_id <= 0:
        return
    if not isinstance(params, dict):
        params = {}
    now = time.time() if now is None else now
    action = {
        "tool": tool if isinstance(tool, str) else None,
        "bundle_id": _str_or_none(params.get("bundle_id")),
        "app_name": _str_or_none(params.get("app_name")),
        "pid": _int_or_none(params.get("pid")),
        "window_id": _int_or_none(params.get("window_id")),
        "at": int(now * 1000),
        "decision": decision,
    }
    with _lock:
        for cid, entry in list(_conversations.items()):
            if now - entry["last_at"] > FORGET_SECONDS:
                del _conversations[cid]
        entry = _conversations.get(conversation_id)
        if entry is None or now - entry["last_at"] > ACTIVE_SECONDS:
            entry = {"started_at": now, "target": None}
            _conversations[conversation_id] = entry
        entry["last_at"] = now
        entry["last"] = action
        # Pid-less tools (app list, clipboard) keep mirroring the last window.
        if action["pid"] is not None:
            entry["target"] = {key: action[key] for key in ("bundle_id", "app_name", "pid", "window_id")}


def snapshot(now=None):
    """The most recently active conversation, or an inactive answer.

    Times are epoch milliseconds, the unit the desktop clock uses.
    """
    now = time.time() if now is None else now
    with _lock:
        best = None
        for cid, entry in _conversations.items():
            if now - entry["last_at"] <= ACTIVE_SECONDS and (best is None or entry["last_at"] > best[1]["last_at"]):
                best = (cid, entry)
        if best is None:
            return {"active": False, "conversation_id": None, "started_at": None, "last": None, "target": None}
        cid, entry = best
        return {
            "active": True,
            "conversation_id": cid,
            "started_at": int(entry["started_at"] * 1000),
            "last": dict(entry["last"]),
            "target": dict(entry["target"]) if entry["target"] else None,
        }


def reset():
    with _lock:
        _conversations.clear()
