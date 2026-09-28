"""Per-conversation ring of turn events (docs/remote-control.md, "Backend pieces").

Only the client that opened `/chat-stream` sees a turn; this ring lets a second
reader (the phone bridge, a reopened window) catch up from a sequence number and
then follow live. Memory only: after a restart every conversation's `seq`
starts at 1 again. `since` reports that as a gap (the reader's seq is past the
end) and every reply carries `epoch`, which changes per process.

Writers are on the stream's hot path, so every public writer swallows its own
failures and logs only the first one: a broken ring must never cost a turn.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import secrets
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RING_SIZE = 500
# Consecutive text deltas become one event per this much time or size, so a
# token-by-token stream does not push the rest of the turn out of 500 slots.
TEXT_FLUSH_S = 0.25
TEXT_FLUSH_BYTES = 2048
# One text event never holds more than this (UTF-8 bytes); a larger provider
# delta becomes several consecutive events that concatenate back to it.
TEXT_EVENT_MAX_BYTES = 8192
# Per chat, on top of RING_SIZE: 500 slots alone do not bound memory.
RING_MAX_BYTES = 1024 * 1024
SUBSCRIBER_QUEUE = 1000
# Deleted-chat ids kept to refuse late writes (see `drop`).
DROPPED_KEEP = 1000
DROPPED_TTL_S = 600.0
# Enough for a tool call line on a phone; a write_file body is not a summary.
SUMMARY_CHARS = 200

KINDS = ("turn_start", "text", "tool_call", "card_opened", "card_closed", "turn_end")
TURN_END_STATUSES = ("done", "error", "stopped")

EPOCH = secrets.token_hex(8)

_failure_logged = False


def _swallow(default=None):
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            global _failure_logged
            try:
                return fn(*args, **kwargs)
            except Exception:
                if not _failure_logged:
                    _failure_logged = True
                    logger.exception("[turn-events] %s failed; later failures are not logged",
                                     fn.__name__)
                return default
        return inner
    return wrap


def _utf8(value: str) -> bytes:
    # surrogatepass: a lone surrogate from a provider must survive split + join.
    return value.encode("utf-8", "surrogatepass")


def _event_bytes(event: dict) -> int:
    return sum(len(_utf8(v)) for v in event.values() if isinstance(v, str))


def split_text(content: str, max_bytes: int = TEXT_EVENT_MAX_BYTES) -> List[str]:
    """`content` in pieces of at most `max_bytes` UTF-8 bytes, never cutting a
    character; joined in order they give `content` back."""
    raw = _utf8(content)
    if len(raw) <= max_bytes:
        return [content]
    pieces, start = [], 0
    while start < len(raw):
        end = min(start + max_bytes, len(raw))
        while end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        pieces.append(raw[start:end].decode("utf-8", "surrogatepass"))
        start = end
    return pieces


def summarize(value: Any, limit: int = SUMMARY_CHARS) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = str(value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


class Subscription:
    """Live events of one conversation. `get()` returns None once closed.

    A reader that falls SUBSCRIBER_QUEUE events behind loses events and gets
    `lagged = True`; it fills the hole with `since(last_seq)`.
    """

    def __init__(self, hub: "TurnEventRing", conv_id: int):
        self._hub = hub
        self.conversation_id = conv_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self.lagged = False
        self.closed = False

    def _offer(self, item: Optional[dict]) -> None:
        if self.closed and item is not None:
            return
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            if item is None:
                # The close sentinel must get through: make room for it.
                try:
                    self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self.queue.put_nowait(None)
            else:
                self.lagged = True

    def _deliver(self, item: Optional[dict]) -> None:
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is None or running is loop:
            self._offer(item)
        elif not loop.is_closed():
            loop.call_soon_threadsafe(self._offer, item)

    async def get(self) -> Optional[dict]:
        if self.closed and self.queue.empty():
            return None
        item = await self.queue.get()
        if item is None:
            self.closed = True
        return item

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        item = await self.get()
        if item is None:
            raise StopAsyncIteration
        return item

    def close(self) -> None:
        if not self.closed:
            self._hub._unsubscribe(self)
            self.closed = True
            self._deliver(None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _Dropped(Exception):
    pass


class _ConvRing:
    __slots__ = ("events", "bytes", "next_seq", "text_parts", "text_bytes", "text_gen",
                 "flush_handle", "subs")

    def __init__(self, size: int = RING_SIZE):
        self.events: deque = deque(maxlen=size)
        self.bytes = 0
        self.next_seq = 1
        self.text_parts: List[str] = []
        self.text_bytes = 0
        self.text_gen = 0
        self.flush_handle = None
        self.subs: List[Subscription] = []


class TurnEventRing:
    def __init__(self, size: int = RING_SIZE, max_bytes: int = RING_MAX_BYTES):
        self.size = size
        self.max_bytes = max_bytes
        self._rings: Dict[int, _ConvRing] = {}
        self._lock = threading.RLock()
        self._stops: set = set()
        # Deleted chats -> time of delete. Conversation ids are never reused,
        # and a session closing after the delete still closes cards and ends
        # its turn; those writes must not bring the ring back. Bounded by age
        # and count: that session closes within seconds of the delete, so a
        # write arriving after its id aged out is not expected, and if one
        # does, it only recreates one small ring.
        self._dropped: "OrderedDict[int, float]" = OrderedDict()

    def _is_dropped(self, conv_id: int) -> bool:
        at = self._dropped.get(conv_id)
        if at is None:
            return False
        if time.monotonic() - at > DROPPED_TTL_S:
            del self._dropped[conv_id]
            return False
        return True

    def _prune_dropped(self) -> None:
        cutoff = time.monotonic() - DROPPED_TTL_S
        while self._dropped:
            oldest_id, at = next(iter(self._dropped.items()))
            if at >= cutoff and len(self._dropped) <= DROPPED_KEEP:
                break
            del self._dropped[oldest_id]

    def _ring(self, conv_id: int) -> _ConvRing:
        if self._is_dropped(conv_id):
            raise _Dropped()
        ring = self._rings.get(conv_id)
        if ring is None:
            ring = _ConvRing(self.size)
            self._rings[conv_id] = ring
        return ring

    def _push(self, conv_id: int, ring: _ConvRing, kind: str, data: dict) -> int:
        seq = ring.next_seq
        ring.next_seq += 1
        event = {**data, "seq": seq, "kind": kind, "ts": time.time()}
        if len(ring.events) == ring.events.maxlen:
            ring.bytes -= _event_bytes(ring.events[0])
        ring.events.append(event)
        ring.bytes += _event_bytes(event)
        # Oldest events go first, as with the slot limit, so `since` reports
        # the hole as a gap the same way; the newest event always stays.
        while ring.bytes > self.max_bytes and len(ring.events) > 1:
            ring.bytes -= _event_bytes(ring.events.popleft())
        for sub in list(ring.subs):
            sub._deliver(dict(event))
        return seq

    def _flush_locked(self, conv_id: int, ring: _ConvRing) -> Optional[int]:
        if ring.flush_handle is not None:
            ring.flush_handle.cancel()
            ring.flush_handle = None
        if not ring.text_parts:
            return None
        content = "".join(ring.text_parts)
        ring.text_parts = []
        ring.text_bytes = 0
        ring.text_gen += 1
        seq = None
        for piece in split_text(content):
            seq = self._push(conv_id, ring, "text", {"content": piece})
        return seq

    def _timed_flush(self, conv_id: int, gen: int) -> None:
        try:
            with self._lock:
                ring = self._rings.get(conv_id)
                if ring is None or ring.text_gen != gen:
                    return
                ring.flush_handle = None
                self._flush_locked(conv_id, ring)
        except Exception:
            logger.debug("[turn-events] timed flush failed", exc_info=True)

    # ── writers ────────────────────────────────────────────────────────────
    @_swallow()
    def append(self, conv_id: int, kind: str, /, **data) -> Optional[int]:
        if kind not in KINDS or kind == "text":
            raise ValueError(f"unknown turn event kind {kind!r}")
        with self._lock:
            try:
                ring = self._ring(conv_id)
            except _Dropped:
                return None
            # A text buffer is older than this event; flushing first keeps order.
            self._flush_locked(conv_id, ring)
            return self._push(conv_id, ring, kind, data)

    @_swallow()
    def text(self, conv_id: int, delta: str) -> None:
        if not delta:
            return
        with self._lock:
            try:
                ring = self._ring(conv_id)
            except _Dropped:
                return
            if not ring.text_parts:
                gen = ring.text_gen
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    ring.flush_handle = loop.call_later(
                        TEXT_FLUSH_S, self._timed_flush, conv_id, gen)
            ring.text_parts.append(delta)
            ring.text_bytes += len(_utf8(delta))
            if ring.text_bytes >= TEXT_FLUSH_BYTES:
                self._flush_locked(conv_id, ring)

    @_swallow()
    def flush(self, conv_id: int) -> None:
        with self._lock:
            ring = self._rings.get(conv_id)
            if ring is not None:
                self._flush_locked(conv_id, ring)

    @_swallow()
    def drop(self, conv_id: int) -> None:
        """A deleted conversation takes its ring and its readers with it."""
        with self._lock:
            ring = self._rings.pop(conv_id, None)
            self._stops.discard(conv_id)
            self._dropped.pop(conv_id, None)
            self._dropped[conv_id] = time.monotonic()
            self._prune_dropped()
        if ring is None:
            return
        if ring.flush_handle is not None:
            ring.flush_handle.cancel()
        for sub in list(ring.subs):
            sub.closed = True
            sub._deliver(None)

    @_swallow()
    def note_stop(self, conv_id: int) -> None:
        """Stop was pressed for this chat; the running turn ends as `stopped`.
        Needed because Claude's interrupted turn still ends with `complete`."""
        with self._lock:
            self._stops.add(conv_id)

    def _take_stop(self, conv_id: int) -> bool:
        with self._lock:
            if conv_id in self._stops:
                self._stops.discard(conv_id)
                return True
            return False

    def _clear_stop(self, conv_id: int) -> None:
        with self._lock:
            self._stops.discard(conv_id)

    # ── readers ────────────────────────────────────────────────────────────
    def since(self, conv_id: int, seq: int = 0) -> dict:
        """Events after `seq`. `gap` is True when the ring no longer holds all
        of them, or when `seq` is past the end (a previous process's seq)."""
        with self._lock:
            ring = self._rings.get(conv_id)
            if ring is None:
                return {"conversation_id": conv_id, "epoch": EPOCH, "events": [],
                        "gap": seq > 0, "last_seq": 0}
            last_seq = ring.next_seq - 1
            events = [dict(e) for e in ring.events if e["seq"] > seq]
            first_held = ring.events[0]["seq"] if ring.events else ring.next_seq
            gap = seq > last_seq or (seq + 1 < first_held and last_seq > seq)
            if seq > last_seq:
                events = [dict(e) for e in ring.events]
            return {"conversation_id": conv_id, "epoch": EPOCH, "events": events,
                    "gap": gap, "last_seq": last_seq}

    def subscribe(self, conv_id: int) -> Subscription:
        sub = Subscription(self, conv_id)
        with self._lock:
            try:
                self._ring(conv_id).subs.append(sub)
            except _Dropped:
                sub.closed = True
                sub._offer(None)
        return sub

    def join(self, conv_id: int, seq: int = 0):
        """Backlog and live subscription taken together, so no event falls
        between reading the backlog and starting to listen."""
        with self._lock:
            sub = self.subscribe(conv_id)
            return self.since(conv_id, seq), sub

    def _unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            ring = self._rings.get(sub.conversation_id)
            if ring is not None and sub in ring.subs:
                ring.subs.remove(sub)

    def conversations(self) -> List[int]:
        with self._lock:
            return list(self._rings)

    def reset(self) -> None:
        with self._lock:
            ids = list(self._rings)
        for cid in ids:
            self.drop(cid)
        with self._lock:
            self._stops.clear()
            self._dropped.clear()


RING = TurnEventRing()

append = RING.append
text = RING.text
flush = RING.flush
drop = RING.drop
since = RING.since
subscribe = RING.subscribe
join = RING.join
note_stop = RING.note_stop


class TurnTap:
    """Feeds one `/chat-stream` turn into the ring.

    Placed where the route relays `AgentRunner.run`, which every provider path
    goes through (Claude SDK, Codex, agy, OpenCode, Copilot, Cursor, Kimi, the
    API loops, wake turns), so no provider carries its own hook. Card events
    are not written here: the card registry writes them when a card opens or
    closes, which also covers cards that never pass through this stream.
    """

    def __init__(self, conv_id: int, provider: str, model: str, origin: str = "user",
                 ring: Optional[TurnEventRing] = None):
        self.conv_id = conv_id
        self.provider = provider
        self.model = model
        self.origin = origin
        self._ring = ring or RING
        self._saw_text = False
        self._ended = False

    @_swallow()
    def start(self) -> None:
        self._ring._clear_stop(self.conv_id)
        self._ring.append(self.conv_id, "turn_start", provider=self.provider,
                          model=self.model, origin=self.origin)

    @_swallow()
    def feed(self, event: Any) -> None:
        etype = getattr(event, "type", None)
        data = getattr(event, "data", None) or {}
        if etype == "text":
            content = data.get("content") or ""
            if content:
                self._saw_text = True
                self._ring.text(self.conv_id, content)
        elif etype == "tool_call":
            detail = (data.get("summary") or data.get("arguments")
                      or data.get("input") or data.get("command"))
            self._ring.append(self.conv_id, "tool_call",
                              name=str(data.get("tool") or data.get("name") or ""),
                              summary=summarize(detail))
        elif etype == "response":
            # Some paths answer only with `response`; where `text` streamed,
            # `response` repeats it and is skipped.
            content = data.get("content") or ""
            if content and not self._saw_text:
                self._ring.text(self.conv_id, content)
        elif etype == "done":
            reason = data.get("stop_reason") or "complete"
            self.end("stopped" if reason == "cancelled" else "done", stop_reason=reason)
        elif etype == "error":
            self.end("error")

    @_swallow()
    def end(self, status: str = "stopped", stop_reason: Optional[str] = None) -> None:
        if self._ended:
            return
        self._ended = True
        if self._ring._take_stop(self.conv_id) and status == "done":
            status = "stopped"
        data = {"status": status}
        if stop_reason:
            data["stop_reason"] = stop_reason
        self._ring.append(self.conv_id, "turn_end", **data)
