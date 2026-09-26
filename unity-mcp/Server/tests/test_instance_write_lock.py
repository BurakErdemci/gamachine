"""Per-instance write lock in UnityInstanceMiddleware.on_call_tool.

Two chats writing to one editor must not interleave; reads, other editors and a
chat still waiting on its approval card must not queue behind a write.
"""
import asyncio
import json
import logging
import pathlib
import subprocess
import sys
import time
import types

import pytest
from fastmcp.exceptions import ToolError

from services.registry.tool_actions import classify
from transport import approval_gate
from transport import unity_instance_middleware as uim
from transport.unity_instance_middleware import UnityInstanceMiddleware

PROBE = pathlib.Path(__file__).resolve().parent / "_write_lock_probe.py"
WRITE = ("manage_gameobject", {"action": "create", "name": "Cube"})
READ = ("read_console", {"action": "get"})


def test_fixture_tools_classify_as_assumed():
    assert classify(*WRITE) == "write"
    assert classify(*READ) == "read"


class _FastMCPContext:
    def __init__(self, instance):
        self._state = {"unity_instance": instance}

    async def get_state(self, key):
        return self._state.get(key)

    async def set_state(self, key, value, serializable=True):
        self._state[key] = value


def _context(call, instance="Game@aaa"):
    name, arguments = call
    return types.SimpleNamespace(
        message=types.SimpleNamespace(name=name, arguments=dict(arguments)),
        fastmcp_context=_FastMCPContext(instance),
    )


@pytest.fixture
def mw(monkeypatch):
    middleware = UnityInstanceMiddleware()

    async def injected_by_the_test(_context):
        return None

    monkeypatch.setattr(middleware, "_inject_unity_instance", injected_by_the_test)
    return middleware


@pytest.fixture
def approve_all(monkeypatch):
    async def approve(_tool, _params, hedef=None, conversation_id=None):
        return None

    monkeypatch.setattr(approval_gate, "kapiyi_gec", approve)


class _Recorder:
    """A fake call_next that records how many calls were inside it at once."""

    def __init__(self, hold=0.05):
        self.hold = hold
        self.inside = 0
        self.max_inside = 0
        self.spans = []

    def __call__(self, label):
        async def call_next(_context):
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)
            start = time.monotonic()
            await asyncio.sleep(self.hold)
            self.spans.append((label, start, time.monotonic()))
            self.inside -= 1
            return label
        return call_next


def test_two_writes_to_one_instance_run_one_after_the_other(mw, approve_all, caplog):
    rec = _Recorder(hold=0.6)

    async def scenario():
        return await asyncio.gather(
            mw.on_call_tool(_context(WRITE), rec("a")),
            mw.on_call_tool(_context(WRITE), rec("b")),
        )

    with caplog.at_level(logging.INFO, logger="transport.unity_instance_middleware"):
        assert asyncio.run(scenario()) == ["a", "b"]
    assert rec.max_inside == 1
    (_, _, first_end), (_, second_start, _) = sorted(rec.spans, key=lambda s: s[1])
    assert second_start >= first_end
    waited = [r.getMessage() for r in caplog.records if "waited" in r.getMessage()]
    assert len(waited) == 1 and "Game@aaa" in waited[0]


def test_writes_to_different_instances_overlap(mw, approve_all):
    inside = []

    async def scenario():
        together = asyncio.Event()

        def call_next_for(label):
            async def call_next(_context):
                inside.append(label)
                if len(inside) == 2:
                    together.set()
                # Only returns if the other instance's write is running at once.
                await asyncio.wait_for(together.wait(), 2)
                return label
            return call_next

        return await asyncio.gather(
            mw.on_call_tool(_context(WRITE, "Game@aaa"), call_next_for("a")),
            mw.on_call_tool(_context(WRITE, "Other@bbb"), call_next_for("b")),
        )

    assert asyncio.run(scenario()) == ["a", "b"]


def test_a_read_is_not_blocked_by_a_held_write(mw, approve_all):
    async def scenario():
        release = asyncio.Event()
        write_inside = asyncio.Event()

        async def slow_write(_context):
            write_inside.set()
            await release.wait()
            return "write"

        async def read(_context):
            return "read"

        writer = asyncio.create_task(mw.on_call_tool(_context(WRITE), slow_write))
        await write_inside.wait()
        result = await asyncio.wait_for(mw.on_call_tool(_context(READ), read), 1)
        assert not writer.done()
        release.set()
        return result, await writer

    assert asyncio.run(scenario()) == ("read", "write")


def test_a_pending_approval_card_does_not_hold_the_lock(mw, monkeypatch):
    async def scenario():
        card_a_clicked = asyncio.Event()
        card_a_shown = asyncio.Event()

        async def gate(_tool, params, hedef=None, conversation_id=None):
            if params.get("name") == "A":
                card_a_shown.set()
                await card_a_clicked.wait()

        monkeypatch.setattr(approval_gate, "kapiyi_gec", gate)
        order = []

        def call_next_for(label):
            async def call_next(_context):
                order.append(label)
                return label
            return call_next

        chat_a = asyncio.create_task(mw.on_call_tool(
            _context(("manage_gameobject", {"action": "create", "name": "A"})),
            call_next_for("a")))
        await card_a_shown.wait()
        result_b = await asyncio.wait_for(mw.on_call_tool(
            _context(("manage_gameobject", {"action": "create", "name": "B"})),
            call_next_for("b")), 1)
        assert not chat_a.done()
        card_a_clicked.set()
        return result_b, await chat_a, order

    assert asyncio.run(scenario()) == ("b", "a", ["b", "a"])


def test_lock_wait_past_the_budget_is_a_busy_tool_error(mw, approve_all, monkeypatch):
    monkeypatch.setattr(uim, "WRITE_LOCK_WAIT_S", 0.3)
    reached = []

    async def scenario():
        release = asyncio.Event()
        write_inside = asyncio.Event()

        async def slow_write(_context):
            write_inside.set()
            await release.wait()
            return "a"

        async def second(_context):
            reached.append("b")
            return "b"

        holder = asyncio.create_task(mw.on_call_tool(_context(WRITE), slow_write))
        await write_inside.wait()
        started = time.monotonic()
        with pytest.raises(ToolError) as caught:
            await mw.on_call_tool(_context(WRITE), second)
        elapsed = time.monotonic() - started
        release.set()
        await holder
        return str(caught.value), elapsed

    message, elapsed = asyncio.run(scenario())
    assert 0.25 <= elapsed < 1.0
    assert "busy with another write" in message
    assert "Game@aaa" in message
    assert "NOT sent to Unity" in message and "retry" in message
    assert reached == []


def test_time_spent_at_the_card_shrinks_the_lock_budget(mw, monkeypatch):
    """Dispatch never moves past the deadline the approval gate budgets for."""
    monkeypatch.setattr(uim, "WRITE_LOCK_WAIT_S", 30.0)
    monkeypatch.setattr(uim, "WRITE_DISPATCH_DEADLINE_S", 0.4)

    async def scenario():
        release = asyncio.Event()
        write_inside = asyncio.Event()

        async def gate(_tool, params, hedef=None, conversation_id=None):
            if params.get("name") == "B":
                await asyncio.sleep(0.3)

        monkeypatch.setattr(approval_gate, "kapiyi_gec", gate)

        async def slow_write(_context):
            write_inside.set()
            await release.wait()

        async def never(_context):
            raise AssertionError("dispatched past the deadline")

        holder = asyncio.create_task(mw.on_call_tool(
            _context(("manage_gameobject", {"action": "create", "name": "A"})), slow_write))
        await write_inside.wait()
        started = time.monotonic()
        with pytest.raises(ToolError, match="busy with another write"):
            await mw.on_call_tool(
                _context(("manage_gameobject", {"action": "create", "name": "B"})), never)
        elapsed = time.monotonic() - started
        release.set()
        await holder
        return elapsed

    # 0.3 s at the card + the 0.1 s floor, nowhere near the 30 s flat budget.
    assert asyncio.run(scenario()) < 1.0


def test_cancel_while_waiting_leaves_no_waiter_and_no_held_lock(mw, approve_all):
    reached = []

    async def scenario():
        release = asyncio.Event()
        write_inside = asyncio.Event()

        async def slow_write(_context):
            write_inside.set()
            await release.wait()
            return "a"

        def mark(label):
            async def call_next(_context):
                reached.append(label)
                return label
            return call_next

        holder = asyncio.create_task(mw.on_call_tool(_context(WRITE), slow_write))
        await write_inside.wait()
        waiter = asyncio.create_task(mw.on_call_tool(_context(WRITE), mark("b")))
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        lock = mw._write_lock("Game@aaa")
        assert not lock._waiters
        release.set()
        await holder
        assert not lock.locked()
        started = time.monotonic()
        third = await mw.on_call_tool(_context(WRITE), mark("c"))
        return third, time.monotonic() - started, lock.locked()

    third, took, still_locked = asyncio.run(scenario())
    assert third == "c" and took < 0.1 and not still_locked
    assert reached == ["c"]


def test_a_denied_write_takes_no_lock(mw, monkeypatch):
    async def deny(_tool, _params, hedef=None, conversation_id=None):
        raise approval_gate.ApprovalDenied("no")

    monkeypatch.setattr(approval_gate, "kapiyi_gec", deny)

    async def scenario():
        async def never(_context):
            raise AssertionError("denied call reached Unity")

        with pytest.raises(ToolError, match="no"):
            await mw.on_call_tool(_context(WRITE), never)
        return mw._write_lock("Game@aaa").locked()

    assert asyncio.run(scenario()) is False


def test_nested_write_on_the_same_instance_does_not_wait_on_itself(mw, approve_all, monkeypatch):
    """A write whose tool dispatches another write through this middleware.

    Short budget so a self-deadlock shows up as the busy ToolError, not a hang.
    A second chat's write must still queue behind the whole outer call.
    """
    monkeypatch.setattr(uim, "WRITE_LOCK_WAIT_S", 0.5)
    events = []

    async def scenario():
        other_chat_started = asyncio.Event()

        async def inner(_context):
            events.append("inner")
            return "inner"

        async def outer(_ctx):
            events.append("outer-start")
            other_chat_started.set()
            await asyncio.sleep(0.2)
            result = await mw.on_call_tool(_context(WRITE), inner)
            events.append("outer-end")
            return result

        async def other(_context):
            events.append("other")
            return "other"

        outer_task = asyncio.create_task(mw.on_call_tool(_context(WRITE), outer))
        await other_chat_started.wait()
        other_task = asyncio.create_task(mw.on_call_tool(_context(WRITE), other))
        return await outer_task, await other_task

    assert asyncio.run(scenario()) == ("inner", "other")
    assert events == ["outer-start", "inner", "outer-end", "other"]


@pytest.fixture(scope="module")
def probe_report():
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(PROBE.parent.parent / "src")],
        cwd=str(PROBE.parent.parent), capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    return json.loads(completed.stdout)


def test_nested_dispatch_through_a_real_fastmcp_server(probe_report):
    """ctx.fastmcp.call_tool runs the middleware again; no tool here uses it
    today, and this pins that it would not wait on its own lock."""
    assert probe_report["nested_result"] == "outer+inner", probe_report
    assert probe_report["nested_seen"] == ["Game@aaa", "Game@aaa"], probe_report
    assert probe_report["nested_seconds"] < 0.5, probe_report


def test_concurrent_client_writes_serialize_on_a_real_server(probe_report):
    assert probe_report["pair_results"] == ["a", "b"], probe_report
    assert probe_report["pair_overlap_seconds"] <= 0, probe_report
