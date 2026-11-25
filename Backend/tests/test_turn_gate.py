"""The per-conversation turn gate (agentic/turn_runner): one turn per chat at a
time, pending inputs merged into one turn, a waiting user turn first and
carrying them, no lost wake-up, errors recorded, the gate released on every
exit path, detached turns held, shutdown leaving nothing running or held.
A fake executor and an in-memory input store stand in for the route and the
database; no provider runs."""
import asyncio
import random

import pytest

from agentic import turn_runner

DONE = 'data: {"type": "done"}\n\n'


class _Fake:
    """Turn body + input store: records every turn, measures concurrency."""

    def __init__(self, delay=0.002):
        self.delay = delay
        self.pending = {}        # conv -> [(text, origin)]
        self.turns = []          # (conv, origin, http, message, [claimed texts])
        self.running = {}
        self.max_running = {}
        self.hold = None         # an Event every background turn waits on
        self.fail_open = False   # raise before claiming
        self.on_end = None       # hook(turn) as the turn finishes
        self.entered = 0

    # store hooks
    def accept(self, conv, text, origin):
        self.pending.setdefault(conv, []).append((text, origin))

    def has_pending(self, conv):
        return bool(self.pending.get(conv))

    async def __call__(self, turn):
        conv = turn.conversation_id
        self.entered += 1
        self.running[conv] = self.running.get(conv, 0) + 1
        self.max_running[conv] = max(self.max_running.get(conv, 0), self.running[conv])
        try:
            if self.fail_open:
                raise RuntimeError("boom")
            claimed = []
            if turn.origin != "wake":
                claimed = [t for t, _ in self.pending.pop(conv, [])]
                turn.claimed = True
            self.turns.append((conv, turn.origin, turn.http, turn.message, claimed))
            if not turn.http and self.hold is not None:
                await self.hold.wait()
            await asyncio.sleep(self.delay)
            yield DONE
            if self.on_end is not None:
                self.on_end(turn)
        finally:
            self.running[conv] -= 1

    def texts(self, conv):
        return [t for c, *_rest, claimed in self.turns if c == conv for t in claimed]


@pytest.fixture
def fake(monkeypatch):
    turn_runner.reset_all()
    f = _Fake()
    monkeypatch.setattr(turn_runner, "_EXECUTOR", f)
    monkeypatch.setattr(turn_runner, "_ACCEPT", f.accept, raising=False)
    monkeypatch.setattr(turn_runner, "_HAS_PENDING", f.has_pending, raising=False)
    yield f
    turn_runner.reset_all()


async def _idle(*convs, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while any(turn_runner.busy(c) for c in convs) or turn_runner._TASKS:
        assert loop.time() < end, "gate never went idle"
        await asyncio.sleep(0.001)


async def _drain(frames):
    return [f async for f in frames]


def _user(conv, text="hello", **kw):
    return turn_runner.TurnInput(conversation_id=conv, message=text, origin="user", **kw)


async def test_fifty_starts_on_an_idle_chat_never_overlap_and_all_run(fake):
    results = [turn_runner.start_background_turn(1, f"r{i}") for i in range(50)]
    assert results[0] == "started" and set(results[1:]) == {"queued"}
    await _idle(1)
    assert fake.max_running[1] == 1
    assert fake.texts(1) == [f"r{i}" for i in range(50)]
    # All 50 were accepted before the started turn's first step: it claims
    # them all, ONE merged turn.
    assert [len(t[4]) for t in fake.turns] == [50]


async def test_fifty_concurrent_starters_under_load_keep_one_turn_per_chat(fake):
    async def starter(i):
        await asyncio.sleep(random.random() * 0.02)
        turn_runner.start_background_turn(7, f"x{i}")

    await asyncio.gather(*(starter(i) for i in range(50)))
    await _idle(7)
    assert fake.max_running[7] == 1
    assert sorted(fake.texts(7)) == sorted(f"x{i}" for i in range(50))


async def test_different_chats_still_run_at_the_same_time(fake):
    barrier = asyncio.Barrier(3)
    seen = []

    async def together(turn):
        seen.append(turn.conversation_id)
        fake.pending.pop(turn.conversation_id, None)
        turn.claimed = True
        await asyncio.wait_for(barrier.wait(), timeout=5)
        yield DONE

    turn_runner._EXECUTOR = together
    for conv in (11, 12, 13):
        assert turn_runner.start_background_turn(conv, "go") == "started"
    await _idle(11, 12, 13)
    assert sorted(seen) == [11, 12, 13]


async def test_a_waiting_user_turn_goes_first_and_carries_the_pending_inputs(fake):
    fake.hold = asyncio.Event()
    turn_runner.start_background_turn(5, "first")
    await asyncio.sleep(0)
    assert turn_runner.start_background_turn(5, "result-a") == "queued"
    task = asyncio.create_task(_drain(turn_runner.run_turn(_user(5), fake)))
    await asyncio.sleep(0.01)
    assert turn_runner.start_background_turn(5, "result-b") == "queued"
    fake.hold.set()
    await task
    await _idle(5)
    assert [(t[1], t[3], t[4]) for t in fake.turns] == [
        ("background", "", ["first"]), ("user", "hello", ["result-a", "result-b"])]


async def test_a_waiting_turn_says_it_is_queued_at_once(fake):
    fake.hold = asyncio.Event()
    turn_runner.start_background_turn(6, "running")
    await asyncio.sleep(0)
    frames = turn_runner.run_turn(_user(6, queued_frame="QUEUED"), fake)
    assert await asyncio.wait_for(frames.__anext__(), 1) == "QUEUED"
    fake.hold.set()
    assert [f async for f in frames] == [DONE]
    await _idle(6)


async def test_an_input_accepted_as_a_turn_ends_is_never_stranded(fake):
    # Accept from inside the ending turn, after its last frame: the very
    # moment between "turn ended" and "anything pending?".
    def chain(turn):
        n = int(fake.turns[-1][4][-1])
        if n < 300:
            turn_runner.start_background_turn(turn.conversation_id, str(n + 1))

    fake.delay = 0
    fake.on_end = chain
    turn_runner.start_background_turn(3, "0")
    await _idle(3)
    assert fake.texts(3) == [str(i) for i in range(301)]
    assert fake.max_running[3] == 1


async def test_accepts_racing_turn_end_many_times_lose_nothing(fake):
    fake.delay = 0
    loop = asyncio.get_running_loop()
    sent = []
    for i in range(300):
        turn_runner.start_background_turn(4, f"a{i}")
        sent.append(f"a{i}")
        # A second input lands a random number of loop steps later: before,
        # at, or after the moment the first turn ends and the gate is decided.
        for _ in range(random.randint(0, 4)):
            await asyncio.sleep(0)
        loop.call_soon(turn_runner.start_background_turn, 4, f"b{i}")
        sent.append(f"b{i}")
        for _ in range(random.randint(0, 4)):
            await asyncio.sleep(0)
    await asyncio.sleep(0)
    await _idle(4)
    assert sorted(fake.texts(4)) == sorted(sent)
    assert fake.max_running[4] == 1
    assert not fake.has_pending(4)


async def test_an_exception_is_recorded_and_the_gate_released(fake):
    async def broken(turn):
        fake.pending.pop(turn.conversation_id, None)
        turn.claimed = True
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover

    turn_runner._EXECUTOR = broken
    turn_runner.start_background_turn(8, "go")
    await _idle(8)
    errors = turn_runner.background_errors(8)
    assert len(errors) == 1 and "provider exploded" in errors[0]["error"]
    assert not turn_runner.busy(8)
    turn_runner._EXECUTOR = fake
    assert turn_runner.start_background_turn(8, "next") == "started"
    await _idle(8)
    assert fake.texts(8) == ["next"]


@pytest.mark.parametrize("frame, expected", [
    ('data: {"type": "error", "message": "rate limited"}\n\n', "rate limited"),
    ('data: {"type": "warning", "code": "turn_not_saved"}\n\n', "turn_not_saved"),
])
async def test_a_failure_frame_of_a_background_turn_is_recorded(fake, frame, expected):
    async def failing(turn):
        fake.pending.pop(turn.conversation_id, None)
        turn.claimed = True
        yield frame

    turn_runner._EXECUTOR = failing
    turn_runner.start_background_turn(9, "go")
    await _idle(9)
    assert [expected in e["error"] for e in turn_runner.background_errors(9)] == [True]


async def test_a_turn_that_keeps_failing_before_claiming_stops_retrying(fake):
    fake.fail_open = True
    turn_runner.start_background_turn(10, "poison")
    await _idle(10)
    assert fake.entered == turn_runner.MAX_FAILED_OPENS
    assert not turn_runner.busy(10)
    # The input is still pending, not lost; a new input tries again.
    assert fake.pending[10] == [("poison", "background")]
    fake.fail_open = False
    turn_runner.start_background_turn(10, "fresh")
    await _idle(10)
    assert fake.texts(10) == ["poison", "fresh"]


async def test_a_user_turn_cancelled_while_waiting_hands_its_input_back(fake):
    fake.hold = asyncio.Event()
    turn_runner.start_background_turn(14, "running")
    await asyncio.sleep(0)
    gone = _user(14, "gone")
    gone.on_abandon = lambda: fake.accept(14, "gone", "user")
    task = asyncio.create_task(_drain(turn_runner.run_turn(gone, fake)))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fake.hold.set()
    await _idle(14)
    # Its message is answered by a background turn instead of being lost.
    assert [(t[1], t[4]) for t in fake.turns] == [("background", ["running"]),
                                                   ("background", ["gone"])]


async def test_a_waiting_stream_closed_at_its_queued_frame_leaves_the_queue(fake):
    fake.hold = asyncio.Event()
    turn_runner.start_background_turn(17, "running")
    await asyncio.sleep(0)
    frames = turn_runner.run_turn(_user(17, queued_frame="QUEUED"), fake)
    assert await frames.__anext__() == "QUEUED"
    await frames.aclose()   # window closed while it waited
    assert not turn_runner._GATES[17].waiters
    fake.hold.set()
    await _idle(17)
    assert not turn_runner.busy(17)


async def test_a_turn_handed_the_gate_as_it_is_cancelled_passes_it_on(fake):
    turn_runner.start_background_turn(15, "running")
    gone = asyncio.create_task(_drain(turn_runner.run_turn(_user(15, "gone"), fake)))
    await asyncio.sleep(0)
    turn_runner.start_background_turn(15, "pending")
    while turn_runner._GATES[15].waiters:
        await asyncio.sleep(0)
    gone.cancel()
    await asyncio.gather(gone, return_exceptions=True)
    await _idle(15)
    assert "pending" in fake.texts(15)
    assert not turn_runner.busy(15)


async def test_a_gate_left_by_a_closed_loop_is_reclaimed(fake):
    dead = asyncio.new_event_loop()
    dead.close()
    with turn_runner._LOCK:
        g = turn_runner._gate(16)
        g.busy, g.loop = True, dead
    assert not turn_runner.busy(16)
    assert turn_runner.start_background_turn(16, "fresh") == "started"
    await _idle(16)
    assert fake.texts(16) == ["fresh"]


async def test_a_background_task_cancelled_before_its_first_step_frees_the_chat(fake):
    turn_runner.start_background_turn(18, "A")
    [task] = list(turn_runner._TASKS)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # Not held, and the input was not lost: it still runs.
    frames = await asyncio.wait_for(_drain(turn_runner.run_turn(_user(18), fake)), 2)
    assert frames == [DONE]
    await _idle(18)
    assert fake.texts(18) == ["A"]
    assert any("before it started" in e["error"] for e in turn_runner.background_errors(18))


async def test_shutdown_cancels_running_turns_and_starts_no_new_one(fake):
    fake.hold = asyncio.Event()
    turn_runner.start_background_turn(19, "A")
    await asyncio.sleep(0.01)
    assert turn_runner.start_background_turn(19, "B") == "queued"
    await turn_runner.shutdown()
    await asyncio.sleep(0.02)
    assert not turn_runner._TASKS
    assert not turn_runner.busy(19)
    assert fake.texts(19) == ["A"] and fake.running[19] == 0
    # B was accepted, so it is not lost: it is still pending for the next turn.
    assert fake.pending[19] == [("B", "background")]


async def test_shutdown_right_after_a_start_leaves_no_gate_held(fake):
    turn_runner.start_background_turn(20, "A")
    await turn_runner.shutdown()
    assert not turn_runner.busy(20) and not turn_runner._TASKS
    assert fake.entered == 0 and fake.pending[20] == [("A", "background")]


async def test_a_detached_turn_holds_the_chat_until_it_ends(fake):
    async def detaching(turn):
        turn.detached = True
        yield DONE

    turn_runner._EXECUTOR = detaching
    await _drain(turn_runner.run_turn(_user(21), detaching))
    assert turn_runner.busy(21)
    turn_runner._EXECUTOR = fake
    assert turn_runner.start_background_turn(21, "result") == "queued"
    await asyncio.sleep(0.01)
    assert fake.entered == 0
    turn_runner.end_detached(21)
    await _idle(21)
    assert fake.texts(21) == ["result"]


async def test_a_user_message_takes_over_a_detached_hold(fake):
    async def detaching(turn):
        turn.detached = True
        yield DONE

    await _drain(turn_runner.run_turn(_user(22), detaching))
    assert turn_runner.busy(22)
    assert await asyncio.wait_for(_drain(turn_runner.run_turn(_user(22, "next"), fake)), 1) == [DONE]
    # A wake turn does not: it waits for the detached turn.
    await _drain(turn_runner.run_turn(_user(22), detaching))
    wake = turn_runner.TurnInput(conversation_id=22, message="w", origin="wake")
    task = asyncio.create_task(_drain(turn_runner.run_turn(wake, fake)))
    await asyncio.sleep(0.02)
    assert not task.done()
    turn_runner.end_detached(22)
    await task
    await _idle(22)


async def test_a_detached_hold_never_reported_ends_by_itself(fake, monkeypatch):
    monkeypatch.setattr(turn_runner, "DETACHED_HOLD_MAX_S", 0.05)

    async def detaching(turn):
        turn.detached = True
        yield DONE

    await _drain(turn_runner.run_turn(_user(23), detaching))
    assert turn_runner.busy(23)
    await asyncio.sleep(0.1)
    assert not turn_runner.busy(23)


async def test_shutdown_frees_a_detached_hold(fake):
    async def detaching(turn):
        turn.detached = True
        yield DONE

    await _drain(turn_runner.run_turn(_user(24), detaching))
    await turn_runner.shutdown()
    assert not turn_runner.busy(24)


async def test_a_rejected_input_is_not_accepted_and_leaves_the_chat_alone(fake):
    def refuse(conv, text, origin):
        raise ValueError("no such chat")

    turn_runner._ACCEPT = refuse
    with pytest.raises(ValueError):
        turn_runner.start_background_turn(25, "x")
    assert not turn_runner.busy(25) and not turn_runner._TASKS


def test_start_without_executor_refuses_loudly(monkeypatch):
    monkeypatch.setattr(turn_runner, "_EXECUTOR", None)
    with pytest.raises(RuntimeError):
        turn_runner.start_background_turn(1, "x")
    assert turn_runner.start_background_turn(1, "  ") == "ignored"
