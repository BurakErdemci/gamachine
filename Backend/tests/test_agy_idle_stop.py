"""An agy child idle for 15 minutes has its process tree stopped; the chat resumes.

Owner's measurement, 12 Nov 2026: nine idle agy chats kept 155 descendant
processes (~3 GB) alive for hours. The fake child below stands in for agy.
"""
import asyncio
import time
from unittest.mock import patch

from providers import agy_session
from providers.cli_base import BaseCLIProvider
from tests.test_agy_stream_session import SESSION_ID
from tests import test_agy_stream_session as base


class _Harness(base.TestAgyStreamSession):
    """The stream-session fixture without re-running its tests here."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ended = []

        async def end_tree(process, *args, **kwargs):
            self.ended.append(process)
            process.kill()
            return True

        patcher = patch.object(agy_session.process_tree, "end_tree", side_effect=end_tree)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def live_session(self, conversation_id=11):
        session = agy_session.get_session(conversation_id)
        await self.collect(session)
        self.assertTrue(session.is_live)
        return session


for _name in [n for n in dir(base.TestAgyStreamSession) if n.startswith("test_")]:
    setattr(_Harness, _name, None)


class TestIdleStop(_Harness):
    async def test_idle_child_tree_is_ended_and_the_session_keeps_its_resume_id(self):
        session = await self.live_session()
        stopped = await agy_session.stop_idle_children(now=session._last_used + 901)
        self.assertEqual(stopped, [11])
        self.assertEqual(self.ended, [self.processes[0]])
        self.assertFalse(session.is_live)
        self.assertIs(agy_session.peek_session(11), session)
        self.assertEqual(session.session_id, SESSION_ID)
        self.assertEqual(agy_session._RESUME_IDS[(11, session.cwd)], SESSION_ID)
        self.assertFalse(session._kapandi)

    async def test_a_recently_used_child_is_untouched(self):
        session = await self.live_session()
        stopped = await agy_session.stop_idle_children(now=session._last_used + 840)
        self.assertEqual(stopped, [])
        self.assertEqual(self.ended, [])
        self.assertTrue(session.is_live)

    async def test_the_turn_clock_is_set_by_a_turn(self):
        session = agy_session.get_session(11)
        session._last_used = 0
        before = time.monotonic()
        await self.collect(session)
        self.assertGreaterEqual(session._last_used, before)

    async def test_nothing_is_stopped_while_a_turn_holds_the_lock(self):
        session = await self.live_session()
        await BaseCLIProvider._AGY_LOCK.acquire()
        try:
            stopped = await agy_session.stop_idle_children(now=session._last_used + 5000)
        finally:
            BaseCLIProvider._AGY_LOCK.release()
        self.assertEqual(stopped, [])
        self.assertEqual(self.ended, [])
        self.assertTrue(session.is_live)
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_the_lock_is_released_after_a_sweep(self):
        session = await self.live_session()
        await agy_session.stop_idle_children(now=session._last_used + 901)
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_the_next_message_resumes_the_same_conversation_in_a_new_child(self):
        session = await self.live_session()
        await agy_session.stop_idle_children(now=session._last_used + 901)
        events = await self.collect(session, "after the pause")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(len(self.processes), 2)
        self.assertIsNot(self.processes[1], self.processes[0])
        argv = self.spawns[1][0]
        self.assertEqual(argv[argv.index("--conversation") + 1], SESSION_ID)
        self.assertTrue(session.is_live)

    async def test_a_one_shot_session_is_never_touched(self):
        one_shot = agy_session.get_session(-5)
        await self.collect(one_shot)
        # Its process normally dies with the turn; force one that is still live.
        agy_session._SESSIONS[-5] = one_shot
        process = self.processes[0]
        one_shot._active_process = process
        process.returncode = None
        stopped = await agy_session.stop_idle_children(now=one_shot._last_used + 5000)
        self.assertEqual(stopped, [])
        self.assertEqual(self.ended, [])

    async def test_one_failing_stop_does_not_block_the_others(self):
        first = await self.live_session(11)
        second = await self.live_session(12)
        original = agy_session.AgyStreamSession._stop_process

        async def flaky(session, *, force=False):
            if session is first:
                raise OSError("cannot stop")
            await original(session, force=force)

        with patch.object(agy_session.AgyStreamSession, "_stop_process", flaky):
            stopped = await agy_session.stop_idle_children(
                now=max(first._last_used, second._last_used) + 901)
        self.assertEqual(stopped, [12])
        self.assertFalse(second.is_live)
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())


class TestIdleStopLoop(_Harness):
    async def test_the_loop_sweeps_each_interval_and_survives_a_failure(self):
        calls = []

        async def sweep():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return []

        with patch.object(agy_session, "stop_idle_children", sweep):
            task = asyncio.create_task(agy_session.idle_stop_loop(interval=0.01))
            for _ in range(200):
                if len(calls) >= 3:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertGreaterEqual(len(calls), 3)

    def test_lifespan_starts_the_loop_and_cancels_it_before_the_agy_sessions_close(self):
        import os
        src = open(os.path.join(os.path.dirname(__file__), "..", "app", "main.py"),
                   encoding="utf-8").read()
        lifespan = src[src.index("async def lifespan"):]
        start = lifespan.index("asyncio.create_task(_agy_idle_stop_loop())")
        self.assertLess(start, lifespan.index("    yield\n"))
        cancel = lifespan.index("agy_idle_stopper.cancel()")
        self.assertLess(cancel, lifespan.index("_close_agy_all()"))
