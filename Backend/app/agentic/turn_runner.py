"""One turn per conversation at a time, whoever starts it.

Every turn path passes the same per-conversation gate: the HTTP user turn and
the UI's wake turn (`POST /chat-stream`, through `run_turn`) and turns the
backend starts by itself (`start_background_turn`, no request, no window).

The turn body lives in `routes/conversation_routes.py`: it needs the router's
state (owed replies, turn-reply records, mail claims), and tests patch
`conversation_routes.AgentRunner`. The router registers it here with
`set_executor`, together with the two storage hooks below, so a background
turn runs exactly the code `/chat-stream` runs.

Inputs are stored when they are accepted, never later: a background input is
written as a message plus a pending mark (`turn_inputs` table) by the
`accept` hook before this module looks at the gate, and an HTTP message is
stored by its request. The pending marks ARE the queue: a turn that takes the
gate claims them (user and background turns; wake turns do not), so nothing is
stored twice and nothing is lost to a cancel, a closed window or a restart
(after a restart the chat's next turn claims them).

Gate rules:
- A turn that finds the chat busy waits; nothing ever runs beside it. A waiting
  HTTP turn sends `queued_frame` at once so the renderer can say so.
- When a turn ends the gate goes to a waiting HTTP turn first (it claims the
  pending inputs after the user's message), else to one background turn for
  every pending input, else the chat is idle.
- "turn ended -> anything pending?" and "input accepted -> chat idle?" are each
  decided under `_LOCK` (the input is written before its check), so an input
  accepted as a turn ends is never stranded.
- A Claude turn whose stream closed keeps running in its SDK session: the gate
  stays held for it (`detached`) until the session reports its end
  (`end_detached`), so no background turn starts on, or takes output from, a
  session still answering. A real user message may take such a hold over, as
  it always could (the session then resets the old turn as before).

State is plain data under a threading lock; the only loop-bound objects are a
waiting turn's future, a detached hold's timer and background tasks, so a
test's closed loop leaves nothing that blocks the next one (a gate held by a
closed loop is reclaimed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import deque
from dataclasses import dataclass
from time import time
from typing import AsyncIterator, Callable, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Background turns that fail before claiming their inputs are retried at most
# this many times in a row; then the inputs stay pending until the chat's next
# accepted input or user turn, so a turn that always fails cannot loop.
MAX_FAILED_OPENS = 3

# A detached Claude turn whose session never reports an end releases the
# chat after this long.
DETACHED_HOLD_MAX_S = 3600.0

# Recent background-turn errors kept per conversation.
MAX_ERRORS = 20


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
    # Sent at once by a turn that has to wait for the gate.
    queued_frame: Optional[str] = None
    # Called when an accepted HTTP turn ends without ever holding the gate.
    on_abandon: Optional[Callable[[], None]] = None
    # Set here: the turn holds (or held) the gate / has ended either way.
    started: bool = False
    finished: bool = False
    # Set by the turn body.
    claimed: bool = False      # it took the chat's pending inputs
    open_failed: bool = False  # it failed before taking them
    detached: bool = False     # its provider turn outlives its stream


Executor = Callable[[TurnInput], AsyncIterator[str]]


class _Waiter:
    __slots__ = ("loop", "fut", "handed", "takeover")

    def __init__(self, loop, takeover: bool):
        self.loop = loop
        self.fut = loop.create_future()
        self.handed = False
        # A real user message, which may take a detached turn's hold over.
        self.takeover = takeover


class _Gate:
    __slots__ = ("busy", "loop", "detached", "end_seen", "timer", "failed_opens", "waiters")

    def __init__(self):
        self.busy = False
        self.loop = None
        self.detached = False
        # The provider reported the end of its unheard turn before the turn's
        # stream finished closing (the hold was not taken yet).
        self.end_seen = False
        self.timer = None
        self.failed_opens = 0
        self.waiters: Deque[_Waiter] = deque()


_LOCK = threading.Lock()
_GATES: Dict[int, _Gate] = {}
_TASKS: Set[asyncio.Task] = set()
_ERRORS: Dict[int, Deque[dict]] = {}
_EXECUTOR: Optional[Executor] = None
# accept(conv, text, origin): store a background input and its pending mark.
_ACCEPT: Optional[Callable[[int, str, str], None]] = None
# has_pending(conv) -> bool: does the chat hold accepted, unclaimed inputs?
_HAS_PENDING: Optional[Callable[[int], bool]] = None
_SHUTTING_DOWN = False


def set_executor(executor: Executor, accept=None, has_pending=None) -> None:
    """The turn body and storage hooks (the router's, set at its creation)."""
    global _EXECUTOR, _ACCEPT, _HAS_PENDING
    _EXECUTOR, _ACCEPT, _HAS_PENDING = executor, accept, has_pending


def _gate(conv_id: int) -> _Gate:
    """The chat's gate; caller holds _LOCK."""
    g = _GATES.get(conv_id)
    if g is None:
        g = _GATES[conv_id] = _Gate()
    elif g.busy and g.loop is not None and g.loop.is_closed():
        # Its turn died with its loop (a test's asyncio.run); nothing can release it.
        logger.warning("[turn-gate] conv=%s gate held by a closed loop reclaimed", conv_id)
        g.busy, g.loop, g.detached, g.timer = False, None, False, None
        g.waiters = deque(w for w in g.waiters if not w.loop.is_closed())
    return g


def busy(conv_id: int) -> bool:
    """Is a turn of this chat running, handed the gate, or detached right now?"""
    with _LOCK:
        g = _GATES.get(conv_id)
        return bool(g and g.busy and not (g.loop is not None and g.loop.is_closed()))


def background_errors(conv_id: int) -> List[dict]:
    """Recent errors of this chat's background turns, oldest first."""
    with _LOCK:
        return list(_ERRORS.get(conv_id) or [])


def _record_error(conv_id: int, origin: str, message: str) -> None:
    with _LOCK:
        _ERRORS.setdefault(conv_id, deque(maxlen=MAX_ERRORS)).append(
            {"at": time(), "origin": origin, "error": message})


def _pending(conv_id: int) -> bool:
    if _HAS_PENDING is None:
        return False
    try:
        return bool(_HAS_PENDING(conv_id))
    except Exception:
        logger.exception("[turn-gate] conv=%s pending inputs not read", conv_id)
        return False


def _take(g: _Gate, loop) -> None:
    """Make the caller the gate's owner; caller holds _LOCK."""
    if g.timer is not None:
        g.timer.cancel()
    g.busy, g.loop, g.detached, g.timer, g.end_seen = True, loop, False, None, False


def _hand(g: _Gate, takeover_only: bool = False) -> bool:
    """Hand the gate to the first live waiter (a takeover waiter if asked);
    caller holds _LOCK."""
    for w in list(g.waiters):
        if w.fut.done() or w.loop.is_closed():
            g.waiters.remove(w)  # cancelled while waiting
            continue
        if takeover_only and not w.takeover:
            continue
        g.waiters.remove(w)
        w.handed = True
        g.loop, g.end_seen = w.loop, False
        w.loop.call_soon_threadsafe(_resolve, w)
        return True
    return False


def _resolve(w: _Waiter) -> None:
    if not w.fut.done():
        w.fut.set_result(None)


def _release(conv_id: int) -> None:
    """The gate's turn ended: hand it to a waiting turn, else run the pending
    inputs as one background turn, else the chat is idle."""
    spawn_on = None
    with _LOCK:
        g = _GATES.get(conv_id)
        if g is None or not g.busy:
            return
        if g.timer is not None:
            g.timer.cancel()
        g.detached, g.timer = False, None
        if _hand(g):
            return
        if (not _SHUTTING_DOWN and g.failed_opens < MAX_FAILED_OPENS
                and g.loop is not None and not g.loop.is_closed() and _pending(conv_id)):
            spawn_on = g.loop
        else:
            if g.failed_opens >= MAX_FAILED_OPENS:
                logger.error("[turn-gate] conv=%s background turns keep failing; pending "
                             "inputs wait for the chat's next input", conv_id)
            _GATES.pop(conv_id, None)
    if spawn_on is not None:
        _spawn(conv_id, spawn_on)


def _hold_detached(conv_id: int) -> None:
    """The turn's stream ended but its provider turn runs on: keep the gate."""
    with _LOCK:
        g = _GATES.get(conv_id)
        if g is None or not g.busy:
            return
        live = g.loop is not None and not g.loop.is_closed()
        if g.end_seen or not live:
            hold = False  # its provider turn already ended
        elif _hand(g, takeover_only=True):
            return  # a waiting user message takes it over, as it may at entry
        else:
            # Wake and other waiters wait for the provider turn's end.
            g.detached, hold = True, True
            g.timer = g.loop.call_later(DETACHED_HOLD_MAX_S, end_detached, conv_id)
    if not hold:
        _release(conv_id)


def end_detached(conv_id: int) -> None:
    """A provider turn of this chat that no stream hears ended (or was
    stopped): free the chat. An end that arrives while the turn's stream is
    still closing is remembered, so the hold is never taken for it."""
    with _LOCK:
        g = _GATES.get(conv_id)
        if g is None or not g.busy:
            return
        if not g.detached:
            g.end_seen = True
            return
    _release(conv_id)


def kick(conv_id: int) -> None:
    """Run the chat's pending inputs now if the chat is idle."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # the next turn of the chat claims them
    with _LOCK:
        g = _gate(conv_id)
        if g.busy or _SHUTTING_DOWN or not _pending(conv_id):
            return
        _take(g, loop)
    _spawn(conv_id, loop)


def abandon(turn: TurnInput) -> None:
    """An accepted HTTP turn that never held the gate (its window closed while
    it waited, or its response never streamed): its input becomes pending."""
    if turn.started or turn.finished:
        return
    turn.finished = True
    if turn.on_abandon is not None:
        try:
            turn.on_abandon()
        except Exception:
            logger.exception("[turn-gate] conv=%s abandoned input not queued", turn.conversation_id)
    kick(turn.conversation_id)


async def _owned(turn: TurnInput, executor: Executor) -> AsyncIterator[str]:
    """Run the turn body with the gate held; always releases or holds it."""
    turn.started = True
    try:
        frames = executor(turn)
        try:
            async for frame in frames:
                yield frame
        finally:
            await frames.aclose()
    except BaseException:
        if not turn.claimed:
            turn.open_failed = True
        raise
    finally:
        turn.finished = True
        if not turn.http:
            with _LOCK:
                g = _GATES.get(turn.conversation_id)
                if g is not None:
                    if turn.claimed:
                        g.failed_opens = 0
                    elif turn.open_failed:
                        g.failed_opens += 1
        if turn.detached:
            _hold_detached(turn.conversation_id)
        else:
            _release(turn.conversation_id)


async def run_turn(turn: TurnInput, executor: Executor) -> AsyncIterator[str]:
    """One turn through the gate, as an async iterator of SSE frames.

    The gate is taken on first iteration, not on creation: a response that is
    never iterated must not hold the chat (`abandon` handles its input)."""
    conv = turn.conversation_id
    loop = asyncio.get_running_loop()
    w = None
    with _LOCK:
        g = _gate(conv)
        takeover = turn.http and turn.origin == "user"
        if not g.busy or (takeover and g.detached
                          and not any(w.takeover for w in g.waiters)):
            _take(g, loop)
        else:
            w = _Waiter(loop, takeover)
            g.waiters.append(w)
    if w is not None:
        try:
            if turn.queued_frame:
                yield turn.queued_frame
            await w.fut
        except BaseException:
            with _LOCK:
                handed = w.handed
                if not handed and w in g.waiters:
                    g.waiters.remove(w)
            if handed:
                _release(conv)  # handed over as we left: pass it on
            abandon(turn)
            raise
    # Closed explicitly: a closed window must end the turn now, not at GC time.
    body = _owned(turn, executor)
    try:
        async for frame in body:
            yield frame
    finally:
        await body.aclose()


def _error_of(frame: str) -> Optional[str]:
    """The error a background turn's frame reports: an error, or an answer
    that was not stored."""
    if not frame.startswith("data: {"):
        return None
    try:
        payload = json.loads(frame[6:])
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") == "error":
        return str(payload.get("message") or "error")
    if payload.get("type") == "warning" and payload.get("code") == "turn_not_saved":
        return "turn_not_saved: the answer was not stored"
    return None


def _spawn(conv_id: int, loop) -> None:
    """Start a background turn for the chat; the caller made it the gate's owner."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is None or running is not loop:
        if not loop.is_closed():
            loop.call_soon_threadsafe(_spawn, conv_id, loop)
            return
        running = None
    if running is None or _SHUTTING_DOWN or _EXECUTOR is None:
        # Inputs stay pending: the chat's next turn claims them.
        with _LOCK:
            g = _GATES.get(conv_id)
            if g is not None:
                g.busy, g.loop = False, None
                if not g.waiters:
                    _GATES.pop(conv_id, None)
        return
    turn = TurnInput(conversation_id=conv_id, origin="background", http=False)
    task = running.create_task(_drive(turn, _EXECUTOR))
    _TASKS.add(task)
    task.add_done_callback(lambda t: _task_done(t, turn))


def _task_done(task: asyncio.Task, turn: TurnInput) -> None:
    _TASKS.discard(task)
    if not turn.started:
        # Cancelled before its first step: its body never ran, so nothing
        # released the gate. Its inputs are still pending (not claimed).
        _record_error(turn.conversation_id, turn.origin, "cancelled before it started")
        with _LOCK:
            g = _GATES.get(turn.conversation_id)
            if g is not None:
                g.failed_opens += 1
        _release(turn.conversation_id)


async def _drive(turn: TurnInput, executor: Executor) -> None:
    """A background turn: the gate is already held for it."""
    conv_id = turn.conversation_id
    try:
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


def start_background_turn(conversation_id: int, text: str, origin: str = "background") -> str:
    """Accept `text` for this chat and run a turn on it as a backend task.

    The input is stored (a message plus its pending mark) before this returns;
    a storage failure raises and nothing was accepted. Returns "started" when
    the chat was idle, "queued" when a turn is running (the input then runs,
    with any others, when it ends), "ignored" for an empty input. Must be
    called on the backend's event loop. `origin` "user" stores the text as a
    user message; any other as `system`.
    """
    text = (text or "").strip()
    if not conversation_id or not text:
        return "ignored"
    if _EXECUTOR is None or _ACCEPT is None:
        raise RuntimeError("no turn executor registered (conversation router not created)")
    loop = asyncio.get_running_loop()
    _ACCEPT(conversation_id, text, origin)
    with _LOCK:
        g = _gate(conversation_id)
        g.failed_opens = 0
        if g.busy or _SHUTTING_DOWN:
            return "queued"
        _take(g, loop)
    _spawn(conversation_id, loop)
    return "started"


async def shutdown() -> None:
    """Backend shutdown: cancel background turns, including any a cancellation
    starts, and free every detached hold. Accepted inputs stay pending."""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    while True:
        tasks = [t for t in list(_TASKS) if not t.done()]
        if not tasks:
            break
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    with _LOCK:
        held = [c for c, g in _GATES.items() if g.detached]
    for conv_id in held:
        _release(conv_id)


def forget(conv_id: int) -> None:
    """A deleted chat: drop its error records and free a detached hold."""
    end_detached(conv_id)
    with _LOCK:
        _ERRORS.pop(conv_id, None)


def reset_all() -> None:
    """Forget every gate and error record (tests)."""
    global _SHUTTING_DOWN
    with _LOCK:
        for g in _GATES.values():
            if g.timer is not None:
                g.timer.cancel()
        _GATES.clear()
        _ERRORS.clear()
        _SHUTTING_DOWN = False
