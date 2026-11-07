"""CodexSession live-thread capabilities: steer, in-place compaction, per-turn settings, tree kill.

A fake JSON-RPC transport stands in for the app-server; no real codex is started.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from providers import codex_session as cx
from providers.codex_session import CodexSession


class FakeTransport:
    """Records every request; `replies` maps method -> response message."""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}

    async def request(self, method, params, timeout=60):
        self.calls.append((method, params))
        reply = self.replies.get(method)
        if isinstance(reply, Exception):
            raise reply
        return reply if reply is not None else {"id": 1, "result": {}}

    def methods(self):
        return [m for m, _ in self.calls]


def live_session(replies=None, **kwargs):
    session = CodexSession(7, **kwargs)
    transport = FakeTransport(replies)
    session._request = transport.request
    session._started = True
    session.thread_id = "thr-1"
    session._thread_cwd = kwargs.get("cwd")
    return session, transport


def running_turn(session, turn_id="turn-9"):
    session._current_turn_id = turn_id
    session._out_q = asyncio.Queue()
    session._terminal_sent = False


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


class TestSteer(unittest.IsolatedAsyncioTestCase):
    async def test_steer_sends_turn_steer_and_never_interrupts(self):
        session, transport = live_session()
        running_turn(session)

        self.assertTrue(await session.steer("also check X", "cid-1"))

        self.assertEqual(transport.calls, [("turn/steer", {
            "threadId": "thr-1",
            "expectedTurnId": "turn-9",
            "input": [{"type": "text", "text": "also check X"}],
            "clientUserMessageId": "cid-1",
        })])
        self.assertNotIn("turn/interrupt", transport.methods())
        self.assertEqual(drain(session._out_q), [{"type": "user_injected", "client_id": "cid-1", "text": "also check X"}])

    async def test_steer_emits_user_injected_exactly_once_with_echo(self):
        session, _ = live_session()
        running_turn(session)

        await session.steer("hello", "cid-2")
        await session._handle_notification({"method": "item/started", "params": {
            "turnId": "turn-9", "item": {"type": "userMessage", "id": "u1", "clientId": "cid-2",
                                         "content": [{"type": "text", "text": "hello"}]}}})
        await session._handle_notification({"method": "item/completed", "params": {
            "turnId": "turn-9", "item": {"type": "userMessage", "id": "u1", "clientId": "cid-2",
                                         "content": [{"type": "text", "text": "hello"}]}}})

        events = [e for e in drain(session._out_q) if e["type"] == "user_injected"]
        self.assertEqual(events, [{"type": "user_injected", "client_id": "cid-2", "text": "hello"}])

    async def test_steer_error_returns_false_and_emits_nothing(self):
        for error in ({"code": -32601, "message": "method not found"},
                      {"code": -32600, "message": "expected turn id mismatch"}):
            with self.subTest(error=error):
                session, transport = live_session({"turn/steer": {"id": 1, "error": error}})
                running_turn(session)

                self.assertFalse(await session.steer("x", "cid-3"))

                self.assertNotIn("turn/interrupt", transport.methods())
                self.assertEqual(drain(session._out_q), [])
                self.assertEqual(session._steer_pending, {})

    async def test_steer_transport_failure_returns_false(self):
        session, _ = live_session({"turn/steer": asyncio.TimeoutError()})
        running_turn(session)

        self.assertFalse(await session.steer("x", "cid-4"))

    async def test_steer_without_running_turn_sends_nothing(self):
        session, transport = live_session()

        self.assertFalse(await session.steer("x", "cid-5"))

        session._current_turn_id = "turn-1"
        session._out_q = asyncio.Queue()
        session._terminal_sent = True
        self.assertFalse(await session.steer("x", "cid-5"))
        self.assertEqual(transport.calls, [])


class TestCompact(unittest.IsolatedAsyncioTestCase):
    async def test_compact_sends_request_waits_for_notification_and_keeps_process(self):
        session, transport = live_session()
        session.close = AsyncMock()

        task = asyncio.create_task(session.compact())
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        await session._handle_notification({"method": "thread/compacted",
                                            "params": {"threadId": "thr-1", "turnId": "t"}})

        self.assertTrue(await asyncio.wait_for(task, 2))
        self.assertEqual(transport.calls, [("thread/compact/start", {"threadId": "thr-1"})])
        session.close.assert_not_awaited()
        self.assertTrue(session._started)
        self.assertFalse(session._turn_lock.locked())

    async def test_compact_unsupported_method_returns_false(self):
        session, _ = live_session({"thread/compact/start": {"id": 1, "error": {"code": -32601,
                                                                               "message": "method not found"}}})
        session.close = AsyncMock()

        self.assertFalse(await session.compact())

        session.close.assert_not_awaited()
        self.assertFalse(session._turn_lock.locked())

    async def test_compact_returns_false_when_no_notification_arrives(self):
        session, _ = live_session()
        with patch.object(cx, "_COMPACT_TIMEOUT_S", 0.05):
            self.assertFalse(await session.compact())
        self.assertFalse(session._turn_lock.locked())

    async def test_compact_waits_for_the_turn_lock(self):
        session, transport = live_session()
        await session._turn_lock.acquire()
        task = asyncio.create_task(session.compact())
        await asyncio.sleep(0.05)
        self.assertEqual(transport.calls, [])
        session._turn_lock.release()
        await asyncio.sleep(0.05)
        self.assertEqual(transport.methods(), ["thread/compact/start"])
        session._compacted_event.set()
        self.assertTrue(await asyncio.wait_for(task, 2))


class TestPerTurnSettings(unittest.IsolatedAsyncioTestCase):
    async def _turn_params(self, session, transport, **kwargs):
        transport.replies["turn/start"] = {"id": 1, "result": {"turn": {"id": "turn-1"}}}

        async def finish():
            while session._out_q is None:
                await asyncio.sleep(0.01)
            await session._emit_terminal({"type": "done"})

        asyncio.create_task(finish())
        async for _ in session.stream("hi", **kwargs):
            pass
        return [p for m, p in transport.calls if m == "turn/start"][0]

    async def test_turn_start_has_no_overrides_by_default(self):
        session, transport = live_session(model="gpt-x", effort="low")
        params = await self._turn_params(session, transport)
        self.assertEqual(params, {"threadId": "thr-1", "input": [{"type": "text", "text": "hi"}]})

    async def test_stream_kwargs_carry_model_effort_cwd(self):
        session, transport = live_session()
        params = await self._turn_params(session, transport, model="gpt-y", effort="high", cwd="C:/proj")
        self.assertEqual((params["model"], params["effort"], params["cwd"]), ("gpt-y", "high", "C:/proj"))

    async def test_set_turn_overrides_persist_across_turns(self):
        session, transport = live_session()
        session.set_turn_overrides(model="gpt-z", effort="medium")
        for _ in range(2):
            params = await self._turn_params(session, transport)
            self.assertEqual((params["model"], params["effort"]), ("gpt-z", "medium"))
            self.assertNotIn("cwd", params)

    async def test_changed_session_cwd_is_sent(self):
        session, transport = live_session(cwd="C:/old")
        session.cwd = "C:/new"
        params = await self._turn_params(session, transport)
        self.assertEqual(params["cwd"], "C:/new")


class TestCloseKillsDescendants(unittest.IsolatedAsyncioTestCase):
    async def test_close_collects_before_kill_and_kills_descendants_after(self):
        order = []
        proc = MagicMock()
        proc.pid = 4242
        proc.kill = MagicMock(side_effect=lambda: order.append("proc.kill"))
        proc.wait = AsyncMock()
        session = CodexSession(7)
        session._proc = proc
        session._started = True

        def collect(pid):
            order.append(("collect", pid))
            return [11, 12]

        async def kill_pids(pids):
            order.append(("kill_pids", list(pids)))

        with patch.object(cx.process_tree, "collect_descendants", collect), \
                patch.object(cx.process_tree, "kill_pids", kill_pids):
            await session.close()

        self.assertEqual(order, [("collect", 4242), "proc.kill", ("kill_pids", [11, 12])])
        self.assertIsNone(session._proc)

    async def test_close_never_raises_when_tree_helpers_fail(self):
        proc = MagicMock()
        proc.pid = 1
        proc.wait = AsyncMock()
        session = CodexSession(7)
        session._proc = proc

        async def boom(pids):
            raise RuntimeError("nope")

        with patch.object(cx.process_tree, "collect_descendants", side_effect=RuntimeError("x")):
            await session.close()
        session._proc = proc
        with patch.object(cx.process_tree, "collect_descendants", return_value=[5]), \
                patch.object(cx.process_tree, "kill_pids", boom):
            await session.close()
        proc.kill.assert_called()


if __name__ == "__main__":
    unittest.main()
