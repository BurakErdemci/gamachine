"""Which assistant row answered the turn that began with a given user row.

chat-stream records a turn here when it stores the user's row (`begin`) and
again when the turn ends (`end`), with the assistant row it stored or None.
The agent bridge (remote/agent_bridge.py) reads it to give a terminal agent
exactly its own turn's reply: row order cannot, since a detached older turn
may store its reply after a newer user row. In memory only: a backend restart
forgets every record (docs/terminal-agents.md).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Dict, Optional, Tuple

KEEP = 2000

_lock = threading.Lock()
_turns: "OrderedDict[Tuple[int, int], Dict]" = OrderedDict()


def begin(conv_id: int, user_row_id: int) -> None:
    with _lock:
        _turns[(int(conv_id), int(user_row_id))] = {"state": "running"}
        _turns.move_to_end((int(conv_id), int(user_row_id)))
        while len(_turns) > KEEP:
            _turns.popitem(last=False)


def end(conv_id: int, user_row_id: int, assistant_row_id: Optional[int], status: str) -> None:
    """Record the outcome once; a later end only adds a reply the first lacked
    (a Claude turn whose stream went away and stored its reply afterwards)."""
    key = (int(conv_id), int(user_row_id))
    with _lock:
        old = _turns.get(key)
        if old is not None and old.get("state") == "ended" and (
                old.get("assistant_row_id") is not None or assistant_row_id is None):
            return
        _turns[key] = {"state": "ended", "assistant_row_id": assistant_row_id, "status": status}
        _turns.move_to_end(key)
        while len(_turns) > KEEP:
            _turns.popitem(last=False)


def get(conv_id: int, user_row_id: int) -> Optional[dict]:
    with _lock:
        entry = _turns.get((int(conv_id), int(user_row_id)))
        return dict(entry) if entry is not None else None


def clear() -> None:
    """Tests only."""
    with _lock:
        _turns.clear()
