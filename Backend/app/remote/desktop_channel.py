"""Backend -> renderer hand-off for phone messages.

`send_message` from a phone is not run by the backend: the renderer owns the
provider arguments, the message queue, cards and wake rules, so it sends the
text like a typed message (docs/remote-control.md, "Messages"). The frame
rides `/wake-stream-all`, the one server -> renderer stream that stays open
for every chat. Frame shape (step 4 acts on it):

    {"type": "remote_message", "request_id": str, "conversation_id": int,
     "text": str, "source": "phone", "device_id": str, "device_name": str,
     "at": <ms>}

A card a phone answered first is closed on the desktop too, so the card
left open there does not wait for a click that can only get
`already_answered`:

    {"type": "card_closed", "card_id": str, "conversation_id": int | None,
     "by": "phone:<device name>", "decision": str, "outcome": str, "at": <ms>}

A phone that changed the approval mode tells the renderer, which shows the new
mode and clears the in-chat cards a switch to auto approved, as its own switch
would (the backend has already applied and drained; this is only the news):

    {"type": "approval_mode_changed", "mode": "auto" | "balanced" | "step",
     "previous": str, "approved_pending": int, "by": "phone:<device name>",
     "at": <ms>}
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)

FRAME_TYPE = "remote_message"
CARD_CLOSED_TYPE = "card_closed"
MODE_CHANGED_TYPE = "approval_mode_changed"
QUEUE_SIZE = 100


class DesktopChannel:
    def __init__(self):
        self._queues: Set[asyncio.Queue] = set()

    def listen(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues.add(q)
        return q

    def unlisten(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def ready(self) -> bool:
        return bool(self._queues)

    def publish(self, frame: dict) -> bool:
        """True when at least one renderer stream took the frame."""
        delivered = False
        for q in list(self._queues):
            try:
                q.put_nowait(frame)
                delivered = True
            except asyncio.QueueFull:
                logger.warning("[remote] renderer stream is not reading; frame dropped for it")
        return delivered


CHANNEL = DesktopChannel()


async def next_frame(q: asyncio.Queue, timeout: float) -> Optional[dict]:
    """The next frame within `timeout`, else None (the stream's poll tick)."""
    try:
        return await asyncio.wait_for(q.get(), timeout)
    except asyncio.TimeoutError:
        return None
