"""AUTO-WAKE queue: background-task completions that arrive with nobody listening.

A run in this backend is exactly one HTTP request (`POST /chat-stream`): when the
StreamingResponse closes, there is no server-side loop left to resume the turn.
So when a background task or subagent finishes AFTER the SSE is gone, the
provider has nowhere to push. It writes a notice here instead, and
`GET /conversations/{id}/wake-stream` hands that notice to the frontend, which
starts the next run on the user's behalf.

Deliberately in-process and NOT persisted: a notice is only meaningful while the
CLI session that produced it is still alive in this process. Surviving a restart
would mean waking a conversation whose task state no longer exists.

Bounded on purpose. A stuck provider could enqueue without limit and nothing
drains an abandoned conversation, so the per-conversation list is capped and the
OLDEST notice is dropped — the newest completion is the one the model needs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Per-conversation notice cap. Notices are coalesced into one wake anyway, so
# this only bounds memory for a conversation nobody is watching.
MAX_NOTICES = 20

# How many consecutive wakes may run without a real user message in between.
# The guard rail exists because a wake starts a run, a run can start tasks, and
# those tasks can wake again — an unattended loop that spends the user's quota.
MAX_CHAIN = 3

# A stale wake remains claimable for at most 120 seconds.
WAKE_TICKET_TTL = 120.0


@dataclass
class _ConvWake:
    notices: List[str] = field(default_factory=list)
    chain: int = 0
    waiters: int = 0
    # Created lazily on first use; asyncio.Event does not bind a loop at
    # construction time on Python 3.10+, so a module-level store is safe.
    event: asyncio.Event = field(default_factory=asyncio.Event)


_QUEUES: Dict[int, _ConvWake] = {}
_WAKE_TICKETS: Dict[int, float] = {}
# The notices a ticket's frame carried. The turn that consumes the ticket
# reads them here, not from the client's request, when it must know them
# (mail delivery keeps the other notices of the same frame).
_TICKET_NOTICES: Dict[int, List[str]] = {}


def _entry(conv_id: int) -> _ConvWake:
    e = _QUEUES.get(conv_id)
    if e is None:
        e = _ConvWake()
        _QUEUES[conv_id] = e
    return e


def enqueue(conv_id: int, text: str) -> None:
    """Record one completion notice and wake anyone waiting on this conversation."""
    text = (text or "").strip()
    if not conv_id or not text:
        return
    e = _entry(conv_id)
    e.notices.append(text)
    if len(e.notices) > MAX_NOTICES:
        dropped = len(e.notices) - MAX_NOTICES
        del e.notices[:dropped]
        logger.warning("[wake_queue] conv=%s notice cap exceeded, dropped %s oldest entries",
                       conv_id, dropped)
    e.event.set()


def drain(conv_id: int) -> List[str]:
    """Take every pending notice and clear the waiting flag."""
    e = _QUEUES.get(conv_id)
    if e is None:
        return []
    out, e.notices = e.notices, []
    e.event.clear()
    return out


def issue_ticket(conv_id: int, notices: Optional[List[str]] = None) -> None:
    """Mark that the backend has issued one wake frame for this conversation."""
    if not conv_id:
        return
    now = monotonic()
    for ticket_conv, issued_at in list(_WAKE_TICKETS.items()):
        if now - issued_at >= WAKE_TICKET_TTL:
            _WAKE_TICKETS.pop(ticket_conv, None)
            _TICKET_NOTICES.pop(ticket_conv, None)
    if conv_id not in _WAKE_TICKETS:
        _WAKE_TICKETS[conv_id] = now
        _TICKET_NOTICES[conv_id] = list(notices or [])


def take_ticket_notices(conv_id: int) -> List[str]:
    """The notices of the frame whose ticket was just consumed (once)."""
    return _TICKET_NOTICES.pop(conv_id, [])


def pending_conversations() -> List[int]:
    """Conversations with at least one notice waiting."""
    return [cid for cid, e in list(_QUEUES.items()) if e.notices]


def ticket_outstanding(conv_id: int) -> bool:
    """Return whether an unconsumed, unexpired wake ticket is still held."""
    issued_at = _WAKE_TICKETS.get(conv_id)
    if issued_at is None:
        return False
    if monotonic() - issued_at >= WAKE_TICKET_TTL:
        _WAKE_TICKETS.pop(conv_id, None)
        _TICKET_NOTICES.pop(conv_id, None)
        return False
    return True


def consume_ticket(conv_id: int) -> bool:
    """Consume a fresh wake ticket once; expired or missing tickets are invalid."""
    issued_at = _WAKE_TICKETS.pop(conv_id, None)
    if issued_at is None:
        return False
    return monotonic() - issued_at < WAKE_TICKET_TTL


def pending(conv_id: int) -> int:
    e = _QUEUES.get(conv_id)
    return len(e.notices) if e else 0


async def wait(conv_id: int) -> None:
    """Block until at least one notice is pending for this conversation."""
    e = _entry(conv_id)
    e.waiters += 1
    try:
        if e.notices:
            return
        await e.event.wait()
    finally:
        e.waiters -= 1
        release(conv_id)


def chain(conv_id: int) -> int:
    e = _QUEUES.get(conv_id)
    return e.chain if e else 0


def bump_chain(conv_id: int) -> int:
    """Count one more consecutive wake; returns the new count."""
    e = _entry(conv_id)
    e.chain += 1
    return e.chain


def chain_exhausted(conv_id: int) -> bool:
    """Would the NEXT wake exceed the consecutive-wake budget?"""
    return chain(conv_id) >= MAX_CHAIN


def reset_chain(conv_id: int) -> None:
    """A real user message ends the chain — the human is back in the loop."""
    e = _QUEUES.get(conv_id)
    if e is not None:
        e.chain = 0


def reset(conv_id: int) -> None:
    """Drop everything for one conversation (delete/compact/tests)."""
    e = _QUEUES.get(conv_id)
    if e is not None:
        # Signal before removal so waiters holding this entry wake and re-evaluate state.
        e.event.set()
        _QUEUES.pop(conv_id, None)
    _WAKE_TICKETS.pop(conv_id, None)
    _TICKET_NOTICES.pop(conv_id, None)


def release(conv_id: int) -> None:
    """Drop an idle conversation entry after its last waiter goes away."""
    e = _QUEUES.get(conv_id)
    # All three guards matter: waiters still need the entry, notices must not
    # be lost, and the chain counter belongs to the next wake sequence.
    if e is not None and e.waiters == 0 and not e.notices and e.chain == 0:
        _QUEUES.pop(conv_id, None)


def reset_all() -> None:
    # Signal every entry before clearing it so existing waiters are never stranded.
    for e in _QUEUES.values():
        e.event.set()
    _QUEUES.clear()
    _WAKE_TICKETS.clear()
    _TICKET_NOTICES.clear()
