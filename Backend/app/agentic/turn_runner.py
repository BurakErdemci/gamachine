"""One turn per conversation at a time, whoever starts it.

Every turn path passes the same per-conversation gate: the HTTP user turn and
the UI's wake turn (`POST /chat-stream`, through `run_turn`) and turns the
backend starts by itself (`start_background_turn`, no request, no window).

The turn body itself lives in `routes/conversation_routes.py` (`_turn`): it
needs the router's state (owed replies, turn-reply records, mail claims), and
tests patch `conversation_routes.AgentRunner`. The router registers it here
with `set_executor`, so a background turn runs exactly the code `/chat-stream`
runs and the two cannot drift.

Gate rules:
- A turn that finds the chat busy waits; nothing ever runs beside it.
- Background inputs that arrive while the chat is busy are queued; when the
  turn ends they all run as ONE merged turn.
- A user turn waiting on a busy chat is served before the queued inputs and
  carries them in its own turn, after the user's message. A wake turn carries
  nothing: its early refusals (stale ticket, chain limit) would lose them.
- "turn ended -> anything queued?" and "enqueue -> chat idle?" are each
  decided under `_LOCK`, so an input queued as a turn ends is never stranded.

State is plain data under a threading lock; the only loop-bound objects are a
waiting turn's future and the background tasks, so a test's closed loop leaves
nothing that blocks the next one (a gate held by a closed loop is reclaimed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from time import time
from typing import AsyncIterator, Callable, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Origins a turn started by an HTTP request carries; anything else is a
# backend label (e.g. "background", later "agent").
HTTP_ORIGINS = ("user", "wake")

# A queued input whose turn failed before storing it goes back to the queue;
# after this many failed turns it is dropped (recorded), so a chat whose turn
# always fails early cannot loop.
MAX_ATTEMPTS = 3

# Recent background-turn errors kept per conversation.
MAX_ERRORS = 20


@dataclass
class QueuedInput:
    text: str
    origin: str = "background"
    attempts: int = 0


@dataclass
class TurnInput:
    """Everything a turn needs, with no Request object."""
    conversation_id: int
    message: str = ""
    origin: str = "user"
    http: bool = True
    user_id: Optional[int] = None
    language: str = "tr"
    ui_language: Optional[str] = None
    editor_code: str = ""
    thinking_level: str = "medium"
    images: Optional[list] = None
    videos: Optional[list] = None
    effort_level: str = "medium"
    ultracode: bool = False
    # Background inputs this turn runs on, handed over by the gate.
    carried: List[QueuedInput] = field(default_factory=list)
    # Set by the turn body once the carried inputs are stored as messages.
    consumed: bool = False


Executor = Callable[[TurnInput], AsyncIterator[str]]


class _Waiter:
    __slots__ = ("loop", "fut", "carry", "handed", "carried")

    def __init__(self, loop, carry: bool):
        self.loop = loop
        self.fut = loop.create_future()
        self.carry = carry
        self.handed = False
        self.carried: List[QueuedInput] = []


class _Gate:
    __slots__ = ("busy", "loop", "queued", "waiters")

    def __init__(self):
        self.busy = False
        self.loop = None
        self.queued: List[QueuedInput] = []
        self.waiters: Deque[_Waiter] = deque()


_LOCK = threading.Lock()
_GATES: Dict[int, _Gate] = {}
_TASKS: Set[asyncio.Task] = set()
_ERRORS: Dict[int, Deque[dict]] = {}
_EXECUTOR: Optional[Executor] = None


def set_executor(executor: Executor) -> None:
    """The turn body background turns run (the router's, set at its creation)."""
    global _EXECUTOR
    _EXECUTOR = executor


def _gate(conv_id: int) -> _Gate:
    """The chat's gate; caller holds _LOCK."""
    g = _GATES.get(conv_id)
    if g is None:
        g = _GATES[conv_id] = _Gate()
    elif g.busy and g.loop is not None and g.loop.is_closed():
        # Its turn died with its loop (a test's asyncio.run); nothing can release it.
        logger.warning("[turn-gate] conv=%s gate held by a closed loop reclaimed", conv_id)
        g.busy, g.loop = False, None
        g.waiters.clear()
    return g


def _take(g: _Gate) -> List[QueuedInput]:
    out, g.queued = g.queued, []
    return out


def busy(conv_id: int) -> bool:
    """Is a turn of this chat running (or handed the gate) right now?"""
    with _LOCK:
        g = _GATES.get(conv_id)
        return bool(g and g.busy and not (g.loop is not None and g.loop.is_closed()))


def queued(conv_id: int) -> int:
    with _LOCK:
        g = _GATES.get(conv_id)
        return len(g.queued) if g else 0


def background_errors(conv_id: int) -> List[dict]:
    """Recent errors of this chat's background turns, oldest first."""
    with _LOCK:
        return list(_ERRORS.get(conv_id) or [])


def _record_error(conv_id: int, origin: str, message: str) -> None:
    with _LOCK:
        _ERRORS.setdefault(conv_id, deque(maxlen=MAX_ERRORS)).append(
            {"at": time(), "origin": origin, "error": message})


async def _acquire(conv_id: int, carry: bool) -> List[QueuedInput]:
    """Wait until this chat's gate is ours; the queued inputs we carry."""
    loop = asyncio.get_running_loop()
    with _LOCK:
        g = _gate(conv_id)
        if not g.busy:
            g.busy, g.loop = True, loop
            return _take(g) if carry else []
        w = _Waiter(loop, carry)
        g.waiters.append(w)
    try:
        await w.fut
    except BaseException:
        with _LOCK:
            if not w.handed:
                if w in g.waiters:
                    g.waiters.remove(w)
                raise
            # Handed over just as we were cancelled: the gate is ours to pass on.
            g.queued[:0] = w.carried
        _release(conv_id)
        raise
    return w.carried


def _resolve(w: _Waiter) -> None:
    if not w.fut.done():
        w.fut.set_result(None)


def _release(conv_id: int) -> None:
    """The gate's turn ended: hand it to a waiting turn, else run the queue, else idle."""
    start: Optional[List[QueuedInput]] = None
    loop = None
    with _LOCK:
        g = _GATES.get(conv_id)
        if g is None or not g.busy:
            return
        while g.waiters:
            w = g.waiters.popleft()
            if w.fut.done() or w.loop.is_closed():
                continue  # cancelled while waiting
            w.handed = True
            w.carried = _take(g) if w.carry else []
            g.loop = w.loop
            w.loop.call_soon_threadsafe(_resolve, w)
            return
        if g.queued:
            start, loop = _take(g), g.loop
        else:
            g.busy, g.loop = False, None
            _GATES.pop(conv_id, None)
    if start:
        _spawn(conv_id, start, loop)


def _finish(turn: TurnInput) -> None:
    """Give unstored carried inputs back to the queue, then release the gate."""
    if turn.carried and not turn.consumed:
        back = []
        for q in turn.carried:
            q.attempts += 1
            if q.attempts < MAX_ATTEMPTS:
                back.append(q)
            else:
                logger.error("[turn-gate] conv=%s input dropped after %d failed turns: %.80r",
                             turn.conversation_id, q.attempts, q.text)
                _record_error(turn.conversation_id, q.origin,
                              f"input dropped after {q.attempts} failed turns")
        if back:
            with _LOCK:
                g = _GATES.get(turn.conversation_id)
                if g is not None:
                    g.queued[:0] = back
    _release(turn.conversation_id)


async def _owned(turn: TurnInput, executor: Executor) -> AsyncIterator[str]:
    """Run the turn body with the gate already held; always releases it."""
    try:
        frames = executor(turn)
        try:
            async for frame in frames:
                yield frame
        finally:
            await frames.aclose()
    finally:
        _finish(turn)


async def run_turn(turn: TurnInput, executor: Executor) -> AsyncIterator[str]:
    """One turn through the gate, as an async iterator of SSE frames.

    The gate is taken on first iteration, not on creation: a response that is
    never iterated must not hold the chat."""
    turn.carried = await _acquire(turn.conversation_id, carry=turn.origin != "wake")
    # Closed explicitly: a closed window must end the turn now, not at GC time.
    owned = _owned(turn, executor)
    try:
        async for frame in owned:
            yield frame
    finally:
        await owned.aclose()


def _merged_origin(inputs: List[QueuedInput]) -> str:
    origins = {q.origin for q in inputs}
    return origins.pop() if len(origins) == 1 else "background"


def _spawn(conv_id: int, inputs: List[QueuedInput], loop=None) -> None:
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and (loop is None or loop is running):
        task = running.create_task(_drive(conv_id, inputs))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    elif loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(_spawn, conv_id, inputs, loop)
    else:
        logger.error("[turn-gate] conv=%s no loop to run %d queued input(s)", conv_id, len(inputs))
        _record_error(conv_id, _merged_origin(inputs), "no event loop to run the turn")
        with _LOCK:
            g = _GATES.get(conv_id)
            if g is not None:
                g.queued[:0] = inputs
                g.busy, g.loop = False, None


def _error_of(frame: str) -> Optional[str]:
    if not frame.startswith("data: {"):
        return None
    try:
        payload = json.loads(frame[6:])
    except ValueError:
        return None
    if isinstance(payload, dict) and payload.get("type") == "error":
        return str(payload.get("message") or "error")
    return None


async def _drive(conv_id: int, inputs: List[QueuedInput]) -> None:
    """A background turn: the gate is already held for it."""
    turn = TurnInput(conversation_id=conv_id, origin=_merged_origin(inputs), http=False,
                     carried=inputs)
    executor = _EXECUTOR
    try:
        if executor is None:
            raise RuntimeError("no turn executor registered")
        async for frame in _owned(turn, executor):
            err = _error_of(frame)
            if err:
                logger.warning("[turn-gate] conv=%s background turn error: %s", conv_id, err)
                _record_error(conv_id, turn.origin, err)
    except asyncio.CancelledError:
        _record_error(conv_id, turn.origin, "cancelled")
        raise
    except Exception as exc:
        logger.exception("[turn-gate] conv=%s background turn failed", conv_id)
        _record_error(conv_id, turn.origin, f"{type(exc).__name__}: {exc}")
    finally:
        if executor is None:
            _finish(turn)


def start_background_turn(conversation_id: int, text: str, origin: str = "background") -> str:
    """Queue `text` for this chat and run a turn on it as a backend task.

    Returns at once: "started" when the chat was idle, "queued" when a turn is
    running (the input then runs, merged with any others, when it ends),
    "ignored" for an empty input. Must be called on the backend's event loop.
    `origin` "user" stores the text as a user message; any other as `system`.
    """
    text = (text or "").strip()
    if not conversation_id or not text:
        return "ignored"
    if _EXECUTOR is None:
        raise RuntimeError("no turn executor registered (conversation router not created)")
    loop = asyncio.get_running_loop()
    with _LOCK:
        g = _gate(conversation_id)
        g.queued.append(QueuedInput(text, origin))
        if g.busy:
            return "queued"
        g.busy, g.loop = True, loop
        inputs = _take(g)
    _spawn(conversation_id, inputs, loop)
    return "started"


async def shutdown() -> None:
    """Cancel running background turns (backend shutdown)."""
    tasks = [t for t in list(_TASKS) if not t.done()]
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def reset_all() -> None:
    """Forget every gate, queue and error record (tests)."""
    with _LOCK:
        _GATES.clear()
        _ERRORS.clear()
