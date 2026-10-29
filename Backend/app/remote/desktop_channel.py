"""Backend -> renderer hand-off for phone messages.

`send_message` from a phone is not run by the backend: the renderer owns the
provider arguments, the message queue, cards and wake rules, so it sends the
text like a typed message (docs/remote-control.md, "Messages"). The frame
rides `/wake-stream-all`, the one server -> renderer stream that stays open
for every chat. Frame shape (step 4 acts on it):

    {"type": "remote_message", "request_id": str, "conversation_id": int,
     "text": str, "source": "phone", "device_id": str, "device_name": str,
     "at": <ms>, "effort"?: str}

`effort` is present only when the phone chose one (already checked against the
chat's model); the renderer sends that message with it instead of its default.

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

A phone that switched a chat's model tells the renderer, which re-reads that
chat's model if the chat is on screen (the backend has stored it; this is only
the news). The pick also became the default a new chat opens on, as a pick on
the desktop does, so a screen showing that default follows too:

    {"type": "chat_model_changed", "conversation_id": int, "provider_type": str,
     "model_name": str, "by": "phone:<device name>", "at": <ms>}

A phone that asks for another effort sends the renderer the level. Effort is
one state of the renderer (`thinkingLevel`), so the renderer decides: it applies
the level only if the active model offers it, and reports what it has
(`PUT /remote/desktop-effort`), which is how the phone learns the outcome:

    {"type": "remote_effort", "level": <canonical level>,
     "by": "phone:<device name>", "at": <ms>}

A paired browser's `ui_ipc` (remote/ui_dispatch.py) runs in the renderer,
which holds `window.ipc`. The channel is already on the remote allow-list and
`payload` is the argument list; the renderer checks its own copy of the list,
invokes, and posts `{ok, result | error}` to
`POST /remote/ui-ipc-result/{request_id}` within 30 s:

    {"type": "remote_ipc", "request_id": <16 hex>, "channel": str,
     "payload": [arg, ...]}

A browser subscribing to a main -> renderer event channel asks the renderer to
listen to it and post every event to `POST /remote/ui-ipc-event`
`{channel, args}`; the answer `{"subscribed": false}` means no session wants
it any more and the renderer drops the listener:

    {"type": "remote_ipc_subscribe", "channel": str}

The command frames (`DESKTOP_ONLY_TYPES`) are for the desktop renderer alone:
a browser's copy of `/wake-stream-all` leaves them out.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)

FRAME_TYPE = "remote_message"
CARD_CLOSED_TYPE = "card_closed"
MODE_CHANGED_TYPE = "approval_mode_changed"
MODEL_CHANGED_TYPE = "chat_model_changed"
EFFORT_SET_TYPE = "remote_effort"
IPC_TYPE = "remote_ipc"
IPC_SUBSCRIBE_TYPE = "remote_ipc_subscribe"
DESKTOP_ONLY_TYPES = frozenset({FRAME_TYPE, EFFORT_SET_TYPE, IPC_TYPE, IPC_SUBSCRIBE_TYPE})
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


def publish_ipc(request_id: str, channel: str, payload: list) -> bool:
    return CHANNEL.publish({"type": IPC_TYPE, "request_id": request_id, "channel": channel,
                            "payload": payload})


def publish_ipc_subscribe(channel: str) -> bool:
    return CHANNEL.publish({"type": IPC_SUBSCRIBE_TYPE, "channel": channel})


async def next_frame(q: asyncio.Queue, timeout: float) -> Optional[dict]:
    """The next frame within `timeout`, else None (the stream's poll tick)."""
    try:
        return await asyncio.wait_for(q.get(), timeout)
    except asyncio.TimeoutError:
        return None
