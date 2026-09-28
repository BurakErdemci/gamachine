"""Every open approval / question card, across chats and kinds, behind one answer path.

Kinds: `mcp` (unityai / Unity MCP bridge gates, `/mcp-approval-request`),
`mail` (a note between chats in step mode), `command` (in-stream approval cards
of Claude, Codex and the API loops) and `question` (Claude's AskUserQuestion).

`answer_card` is the only way a person's answer reaches a card, from the desktop
now and from a phone later, so both share one gate: whoever claims the card
first wins, a later answer gets `already_answered {by, at}`. The claim is a
check-and-set under a lock with no await inside; the kind-specific resolver
(set the waiter's result, wake it) runs after the claim.

A card closed without an answer (timeout, Stop, chat deleted, switch to auto)
goes through `close_card`, which is first-wins too. Every close, answered or
not, writes one ledger row; the ledger is where the remote-control metric
(share of cards that time out while the owner is away) is counted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from agentic import turn_events

logger = logging.getLogger(__name__)

KINDS = ("mcp", "mail", "command", "question")
OUTCOMES = ("approved", "rejected", "answered", "timed_out", "cancelled")
SYSTEM = "system"
# Closed cards stay findable this long so a late answer learns who won.
CLOSED_KEEP = 500
# Card ids are gate ids (uuid hex, or what the MCP bridge sends); a phone
# bridge hands them over from JSON, so anything else is refused up front.
CARD_ID_MAX = 1024


def _valid_id(card_id: Any) -> bool:
    return isinstance(card_id, str) and 0 < len(card_id) <= CARD_ID_MAX


def params_hash(params: Any) -> Optional[str]:
    """sha256 of the params in a canonical JSON form; the params themselves
    are never kept (a command line or a file body can hold secrets)."""
    if params is None:
        return None
    try:
        text = json.dumps(params, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), default=str)
    except Exception:
        text = repr(params)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _current_mode() -> Optional[str]:
    try:
        from agentic import approval_mode
        return approval_mode.current_mode()
    except Exception:
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Card:
    card_id: str
    conversation_id: Optional[int]
    kind: str
    tool: Optional[str]
    summary: str
    risk: Optional[str]
    created_at: float
    params_hash: Optional[str]
    approval_mode: Optional[str]
    resolver: Optional[Callable[[Any], None]] = field(default=None, repr=False)
    open: bool = True
    decision: Optional[str] = None
    by: Optional[str] = None
    at: Optional[float] = None
    outcome: Optional[str] = None

    def public(self) -> dict:
        return {"card_id": self.card_id, "conversation_id": self.conversation_id,
                "kind": self.kind, "tool": self.tool, "summary": self.summary,
                "risk": self.risk, "created_at": _iso(self.created_at)}


_LOCK = threading.Lock()
_OPEN: Dict[str, Card] = {}
_CLOSED: "OrderedDict[str, Card]" = OrderedDict()
_ledger_writer: Optional[Callable[[dict], Any]] = None
_ledger_failure_logged = False


def set_ledger(writer: Optional[Callable[[dict], Any]]) -> None:
    global _ledger_writer
    _ledger_writer = writer


def open_card(card_id: str, *, conversation_id: Optional[int], kind: str,
              tool: Optional[str] = None, summary: Any = None, risk: Optional[str] = None,
              params: Any = None, resolver: Optional[Callable[[Any], None]] = None) -> Optional[Card]:
    """Register an open card. Never raises: a card that cannot be registered
    still works the old way, it only misses the list and the ledger."""
    if not _valid_id(card_id):
        logger.warning("[cards] card with a malformed id not registered")
        return None
    try:
        card = Card(card_id=card_id, conversation_id=conversation_id,
                    kind=kind if kind in KINDS else "command",
                    tool=None if tool is None else str(tool),
                    summary=turn_events.summarize(summary),
                    risk=risk or None, created_at=time.time(),
                    params_hash=params_hash(params), approval_mode=_current_mode(),
                    resolver=resolver)
        with _LOCK:
            _CLOSED.pop(card_id, None)
            _OPEN[card_id] = card
    except Exception:
        logger.exception("[cards] card %s not registered", card_id)
        return None
    if conversation_id is not None:
        turn_events.append(conversation_id, "card_opened", card_id=card_id, card_kind=card.kind,
                           tool=card.tool, summary=card.summary)
    return card


def set_resolver(card_id: str, resolver: Callable[[Any], None]) -> None:
    with _LOCK:
        card = _OPEN.get(card_id)
        if card is not None:
            card.resolver = resolver


def get(card_id: str) -> Optional[Card]:
    if not _valid_id(card_id):
        return None
    with _LOCK:
        return _OPEN.get(card_id) or _CLOSED.get(card_id)


def is_open(card_id: str) -> bool:
    if not _valid_id(card_id):
        return False
    with _LOCK:
        return card_id in _OPEN


def list_pending(conversation_id: Optional[int] = None) -> List[dict]:
    with _LOCK:
        cards = list(_OPEN.values())
    cards.sort(key=lambda c: c.created_at)
    return [c.public() for c in cards
            if conversation_id is None or c.conversation_id == conversation_id]


def _claim(card_id: str, outcome: str, decision: Optional[str], device: str) -> Optional[Card]:
    with _LOCK:
        card = _OPEN.pop(card_id, None)
        if card is None:
            return None
        card.open = False
        card.outcome = outcome
        card.decision = decision
        card.by = device
        card.at = time.time()
        _CLOSED[card_id] = card
        while len(_CLOSED) > CLOSED_KEEP:
            _CLOSED.popitem(last=False)
        return card


def _after_close(card: Card) -> None:
    global _ledger_failure_logged
    writer = _ledger_writer
    if writer is not None:
        try:
            writer({
                "at": _iso(card.at or time.time()),
                "card_id": card.card_id,
                "conversation_id": card.conversation_id,
                "kind": card.kind,
                "tool": card.tool,
                "params_hash": card.params_hash,
                "approval_mode": card.approval_mode,
                "decision": card.decision,
                "device": card.by,
                "outcome": card.outcome,
            })
        except Exception:
            if not _ledger_failure_logged:
                _ledger_failure_logged = True
                logger.exception("[cards] ledger row not written; later failures are not logged")
    if card.conversation_id is not None:
        turn_events.append(card.conversation_id, "card_closed", card_id=card.card_id,
                           decision=card.decision or card.outcome, by=card.by,
                           outcome=card.outcome)


def already_answered(card: Card) -> dict:
    return {"status": "already_answered", "card_id": card.card_id, "by": card.by,
            "at": _iso(card.at or 0), "decision": card.decision, "outcome": card.outcome}


def answer_card(card_id: str, decision: str, choice: Any = None,
                device: str = "desktop") -> dict:
    """A person's answer. First answer wins; the reply says what happened:

    ok               -> this answer resolved the card
    already_answered -> someone (or a timeout / Stop) closed it first: {by, at}
    not_found        -> no such card, or it closed so long ago it was forgotten
    invalid          -> the decision does not fit the card's kind

    Call it on the event loop thread: resolvers set asyncio events.
    """
    if not _valid_id(card_id):
        return {"status": "invalid", "error": "card_id must be a non-empty string"}
    if not isinstance(decision, str):
        return {"status": "invalid", "error": "decision must be a string"}
    if device is not None and not isinstance(device, str):
        return {"status": "invalid", "error": "device must be a string"}
    with _LOCK:
        card = _OPEN.get(card_id)
        closed = _CLOSED.get(card_id)
    if card is None:
        return already_answered(closed) if closed is not None else {"status": "not_found"}
    if card.kind == "question":
        if decision == "answer" and isinstance(choice, dict):
            outcome, result = "answered", choice
        elif decision == "reject":
            outcome, result = "rejected", None
        else:
            return {"status": "invalid", "error": "question cards take answer + choice or reject"}
    elif decision in ("approve", "reject"):
        outcome, result = ("approved", True) if decision == "approve" else ("rejected", False)
    else:
        return {"status": "invalid", "error": "decision must be approve or reject"}
    won = _claim(card_id, outcome, decision, device or "desktop")
    if won is None:
        with _LOCK:
            closed = _CLOSED.get(card_id)
        return already_answered(closed) if closed is not None else {"status": "not_found"}
    if won.resolver is not None:
        try:
            won.resolver(result)
        except Exception:
            logger.exception("[cards] resolver of %s failed after the answer was claimed", card_id)
    _after_close(won)
    return {"status": "ok", "card_id": card_id, "outcome": outcome, "by": won.by,
            "at": _iso(won.at or 0)}


def close_card(card_id: str, outcome: str, *, decision: Optional[str] = None,
               device: str = SYSTEM, resolve: Any = ...) -> bool:
    """Close a card nobody answered (timeout, Stop, delete, a mode switch).

    First-wins like an answer: False when the card was already closed, and
    then the caller must not overwrite the winner's result. The resolver runs
    only when `resolve` is passed (the value the waiter should read).
    """
    if not _valid_id(card_id):
        return False
    if outcome not in OUTCOMES:
        outcome = "cancelled"
    card = _claim(card_id, outcome, decision, device)
    if card is None:
        return False
    if resolve is not ... and card.resolver is not None:
        try:
            card.resolver(resolve)
        except Exception:
            logger.exception("[cards] resolver of %s failed on close", card_id)
    _after_close(card)
    return True


def reset() -> None:
    """Tests only: forget every card."""
    with _LOCK:
        _OPEN.clear()
        _CLOSED.clear()
