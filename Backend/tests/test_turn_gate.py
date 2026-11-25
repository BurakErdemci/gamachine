"""The per-conversation turn gate (agentic/turn_runner): one turn per chat at a
time, queued background inputs merged into one turn, a waiting user turn first
and carrying them, no lost wake-up, errors recorded and the gate released.
A fake executor stands in for the turn body; no provider runs."""
import asyncio
import random

import pytest

from agentic import turn_runner


class _Fake:
    """Records every turn; measures how many run at once per chat."""

    def __init__(self, delay=0.002):
        self.delay = delay
        self.turns = []          # (conv, origin, http, message, [carried texts])
        self.running = {}
        self.max_running = {}
        self.fail_on = set()     # texts whose turn raises before consuming
        self.on_end = None       # hook(turn) run as the turn finishes

    async def __call__(self, turn):
        conv = turn.conversation_id
        self.running[conv] = self.running.get(conv, 0) + 1
        self.max_running[conv] = max(self.max_running.get(conv, 0), self.running[conv])
        try:
            if any(q.text in self.fail_on for q in turn.carried):
                raise RuntimeError("boom")
            turn.consumed = True
            self.turns.append((conv, turn.origin, turn.http, turn.message,
                               [q.text for q in turn.carried]))
            await asyncio.sleep(self.delay)
            yield 'data: {"type": "done"}\n\n'
            if self.on_end is not None:
                self.on_end(turn)
        finally:
            self.running[conv] -= 1

    def texts(self, conv):
        return [t for c, *_rest, carried in self.turns if c == conv for t in carried]


@pytest.fixture
def fake(monkeypatch):
    turn_runner.reset_all()
    f = _Fake()
    monkeypatch.setattr(turn_runner, "_EXECUTOR", f)
    yield f
    turn_runner.reset_all()


async def _idle(*convs, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while any(turn_runner.busy(c) for c in convs) or turn_runner._TASKS:
        assert loop.time() < end, "gate never went idle"
        await asyncio.sleep(0.001)


async def test_fifty_starts_on_an_idle_chat_never_overlap_and_all_run(fake):
    results = [turn_runner.start_background_turn(1, f"r{i}") for i in range(50)]
    assert results[0] == "started" and set(results[1:]) == {"queued"}
    await _idle(1)
    assert fake.max_running[1] == 1
    assert sorted(fake.texts(1)) == sorted(f"r{i}" for i in range(50))
    # The first runs alone; the 49 queued behind it run as ONE merged turn.
    assert [len(t[4]) for t in fake.turns] == [1, 49]


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
        turn.consumed = True
        await asyncio.wait_for(barrier.wait(), timeout=5)
        yield 'data: {"type": "done"}\n\n'

    turn_runner.set_executor(together)
    for conv in (11, 12, 13):
        assert turn_runner.start_background_turn(conv, "go") == "started"
    await _idle(11, 12, 13)
    assert sorted(seen) == [11, 12, 13]


async def test_a_waiting_user_turn_goes_first_and_carries_the_queued_inputs(fake):
    release_first = asyncio.Event()
    order = []

    async def body(turn):
        turn.consumed = True
        order.append((turn.origin, turn.message, [q.text for q in turn.carried]))
        if turn.message == "" and not release_first.is_set():
            await release_first.wait()
        yield 'data: {"type": "done"}\n\n'

    turn_runner.set_executor(body)
    turn_runner.start_background_turn(5, "first")
    await asyncio.sleep(0)
    assert turn_runner.start_background_turn(5, "result-a") == "queued"
    user = turn_runner.TurnInput(conversation_id=5, message="hello", origin="user")

    async def user_turn():
        return [f async for f in turn_runner.run_turn(user, body)]

    task = asyncio.create_task(user_turn())
    await asyncio.sleep(0.01)
    assert turn_runner.start_background_turn(5, "result-b") == "queued"
    release_first.set()
    await task
    await _idle(5)
    assert order == [("background", "", ["first"]),
                     ("user", "hello", ["result-a", "result-b"])]


async def test_a_wake_turn_leaves_the_queued_inputs_for_their_own_turn(fake):
    gate_open = asyncio.Event()

    async def body(turn):
        turn.consumed = True
        fake.turns.append((turn.conversation_id, turn.origin, turn.http, turn.message,
                           [q.text for q in turn.carried]))
        if turn.carried == [] and turn.origin == "wake":
            await gate_open.wait()
        yield 'data: {"type": "done"}\n\n'

    turn_runner.set_executor(body)
    wake = turn_runner.TurnInput(conversation_id=6, message="w", origin="wake")
    task = asyncio.create_task(_drain(turn_runner.run_turn(wake, body)))
    await asyncio.sleep(0.01)
    assert turn_runner.start_background_turn(6, "later") == "queued"
    gate_open.set()
    await task
    await _idle(6)
    assert [(t[1], t[4]) for t in fake.turns] == [("wake", []), ("background", ["later"])]


async def _drain(frames):
    return [f async for f in frames]


async def test_an_input_queued_as_a_turn_ends_is_never_stranded(fake):
    # Enqueue from inside the ending turn, after its last frame: the very
    # moment between "turn ended" and "anything queued?".
    def chain(turn):
        n = int(turn.carried[-1].text)
        if n < 300:
            turn_runner.start_background_turn(turn.conversation_id, str(n + 1))

    fake.delay = 0
    fake.on_end = chain
    turn_runner.start_background_turn(3, "0")
    await _idle(3)
    assert fake.texts(3) == [str(i) for i in range(301)]
    assert fake.max_running[3] == 1


async def test_enqueue_racing_turn_end_many_times_loses_nothing(fake):
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
    assert turn_runner.queued(4) == 0


async def test_an_exception_is_recorded_and_the_gate_released(fake):
    async def broken(turn):
        turn.consumed = True
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover

    turn_runner.set_executor(broken)
    turn_runner.start_background_turn(8, "go")
    await _idle(8)
    errors = turn_runner.background_errors(8)
    assert len(errors) == 1 and "provider exploded" in errors[0]["error"]
    assert not turn_runner.busy(8)
    turn_runner.set_executor(fake)
    assert turn_runner.start_background_turn(8, "next") == "started"
    await _idle(8)
    assert fake.texts(8) == ["next"]


async def test_an_error_frame_of_a_background_turn_is_recorded(fake):
    async def erroring(turn):
        turn.consumed = True
        yield 'data: {"type": "error", "message": "rate limited"}\n\n'

    turn_runner.set_executor(erroring)
    turn_runner.start_background_turn(9, "go")
    await _idle(9)
    assert [e["error"] for e in turn_runner.background_errors(9)] == ["rate limited"]


async def test_an_input_whose_turn_fails_before_storing_it_is_retried_then_dropped(fake):
    fake.fail_on = {"poison"}
    turn_runner.start_background_turn(10, "poison")
    await _idle(10)
    errors = [e["error"] for e in turn_runner.background_errors(10)]
    assert errors.count("RuntimeError: boom") == turn_runner.MAX_ATTEMPTS
    assert any("dropped" in e for e in errors)
    assert turn_runner.queued(10) == 0 and not turn_runner.busy(10)


async def test_a_user_turn_cancelled_while_waiting_does_not_hold_the_gate(fake):
    hold = asyncio.Event()

    async def body(turn):
        turn.consumed = True
        fake.turns.append((turn.conversation_id, turn.origin, turn.http, turn.message,
                           [q.text for q in turn.carried]))
        if turn.message == "" and not hold.is_set():
            await hold.wait()
        yield 'data: {"type": "done"}\n\n'

    turn_runner.set_executor(body)
    turn_runner.start_background_turn(14, "running")
    await asyncio.sleep(0)
    gone = asyncio.create_task(_drain(turn_runner.run_turn(
        turn_runner.TurnInput(conversation_id=14, message="gone"), body)))
    await asyncio.sleep(0.01)
    gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gone
    turn_runner.start_background_turn(14, "after")
    hold.set()
    await _idle(14)
    assert [t[3] or t[4] for t in fake.turns] == [["running"], ["after"]]


async def test_a_turn_handed_the_gate_as_it_is_cancelled_passes_it_on(fake):
    turn_runner.start_background_turn(15, "running")
    gone = asyncio.create_task(_drain(turn_runner.run_turn(
        turn_runner.TurnInput(conversation_id=15, message="gone"), fake)))
    await asyncio.sleep(0)
    turn_runner.start_background_turn(15, "carried")
    # Cancel in the same loop step the running turn hands the gate over.
    while turn_runner._GATES[15].waiters:
        await asyncio.sleep(0)
    gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gone
    await _idle(15)
    # The input the cancelled turn would have carried still ran.
    assert "carried" in fake.texts(15)
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


def test_start_without_executor_refuses_loudly(monkeypatch):
    monkeypatch.setattr(turn_runner, "_EXECUTOR", None)
    with pytest.raises(RuntimeError):
        turn_runner.start_background_turn(1, "x")
    assert turn_runner.start_background_turn(1, "  ") == "ignored"
