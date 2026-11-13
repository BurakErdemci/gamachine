"""An agy child idle for 15 minutes has its process tree stopped; the chat resumes.

Owner's measurement, 12 Nov 2026: nine idle agy chats kept 155 descendant
processes (~3 GB) alive for hours. The fake child below stands in for agy.
"""
import asyncio
import inspect
import subprocess
import time
import unittest
from unittest.mock import patch

import process_tree
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
        # Fake pids may be real processes on the test machine.
        self.shell_work = False
        work = patch.object(agy_session.process_tree, "has_running_shell_work",
                            side_effect=lambda pid: self.shell_work)
        work.start()
        self.addCleanup(work.stop)

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


class _Clock:
    """Stands in for agy_session's `time` module with a settable monotonic()."""

    def __init__(self, value):
        self.value = value

    def monotonic(self):
        return self.value

    def __getattr__(self, name):
        return getattr(time, name)


class TestIdleStopRaces(_Harness):
    async def wait_until(self, predicate):
        for _ in range(500):
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition never became true")

    async def test_a_long_turn_that_just_ended_counts_as_recent_use(self):
        session = await self.live_session()
        clock = _Clock(1000.0)
        release = asyncio.Event()
        with patch.object(agy_session, "time", clock):
            session._last_used = clock.value
            # The live child's next drain blocks until released.
            self.processes[0].release = release
            task = asyncio.create_task(self.collect(session, "long turn"))
            await self.wait_until(lambda: self.processes[0].stdin.drains == 2)
            clock.value += 2000  # the turn runs far longer than the idle limit
            release.set()
            await task
            stopped = await agy_session.stop_idle_children(now=clock.value + 1)
        self.assertEqual(stopped, [])
        self.assertTrue(session.is_live)

    async def test_a_turn_waiting_for_the_lock_counts_as_recent_use(self):
        session = await self.live_session()
        clock = _Clock(1000.0)
        await BaseCLIProvider._AGY_LOCK.acquire()
        with patch.object(agy_session, "time", clock):
            session._last_used = 0
            task = asyncio.create_task(self.collect(session, "queued"))
            await self.wait_until(lambda: session._last_used == clock.value)
        BaseCLIProvider._AGY_LOCK.release()
        await task

    async def test_a_turn_arriving_during_a_sweep_waits_then_respawns_its_child(self):
        session = await self.live_session()
        in_stop = asyncio.Event()
        gate = asyncio.Event()
        original = agy_session.AgyStreamSession._stop_process

        async def slow_stop(this, *, force=False):
            in_stop.set()
            await gate.wait()
            await original(this, force=force)

        with patch.object(agy_session.AgyStreamSession, "_stop_process", slow_stop):
            sweep = asyncio.create_task(
                agy_session.stop_idle_children(now=session._last_used + 901))
            await in_stop.wait()
            turn = asyncio.create_task(self.collect(session, "arrives mid-sweep"))
            for _ in range(20):
                await asyncio.sleep(0.01)
            self.assertFalse(turn.done())
            self.assertEqual(len(self.processes), 1)
            gate.set()
            self.assertEqual(await sweep, [11])
            events = await turn
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(session.is_live)

    async def test_a_child_that_survives_the_kill_is_not_reported_stopped(self):
        session = await self.live_session()

        async def failed_kill(process, *args, **kwargs):
            return False

        with patch.object(agy_session.process_tree, "end_tree", side_effect=failed_kill):
            with self.assertLogs(agy_session.logger, level="WARNING") as logs:
                stopped = await agy_session.stop_idle_children(now=session._last_used + 901)
        self.assertEqual(stopped, [])
        self.assertIsNone(self.processes[0].returncode)
        self.assertTrue(any("idle stop could not end the child conv=11" in line
                            for line in logs.output))
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

    def test_the_loop_sweeps_every_60_seconds_by_default(self):
        default = inspect.signature(agy_session.idle_stop_loop).parameters["interval"].default
        self.assertEqual(default, 60)

    async def test_cancelling_the_loop_mid_stop_releases_the_turn_lock(self):
        session = await self.live_session()
        session._last_used = -10_000
        in_stop = asyncio.Event()

        async def hanging_stop(this, *, force=False):
            in_stop.set()
            await asyncio.Event().wait()

        with patch.object(agy_session.AgyStreamSession, "_stop_process", hanging_stop):
            task = asyncio.create_task(agy_session.idle_stop_loop(interval=0.01))
            await asyncio.wait_for(in_stop.wait(), timeout=5)
            self.assertTrue(BaseCLIProvider._AGY_LOCK.locked())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())


class TestBackgroundWorkGuard(_Harness):
    async def assert_kept(self, session):
        stopped = await agy_session.stop_idle_children(now=session._last_used + 901)
        self.assertEqual(stopped, [])
        self.assertEqual(self.ended, [])
        self.assertTrue(session.is_live)
        self.assertIs(agy_session.peek_session(11), session)
        self.assertEqual(agy_session._RESUME_IDS[(11, session.cwd)], SESSION_ID)
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_background_work_keeps_the_child(self):
        session = await self.live_session()
        self.shell_work = True
        await self.assert_kept(session)

    async def test_unknown_process_table_keeps_the_child(self):
        session = await self.live_session()
        self.shell_work = None
        await self.assert_kept(session)

    async def test_a_failing_check_keeps_the_child(self):
        session = await self.live_session()
        with patch.object(agy_session.process_tree, "has_running_shell_work",
                          side_effect=OSError("boom")):
            await self.assert_kept(session)

    async def test_the_child_is_stopped_once_the_work_has_ended(self):
        session = await self.live_session()
        self.shell_work = True
        await self.assert_kept(session)
        self.shell_work = False
        stopped = await agy_session.stop_idle_children(now=session._last_used + 901)
        self.assertEqual(stopped, [11])
        self.assertFalse(session.is_live)


def _table(rows):
    """rows: (pid, ppid, image) -> the (parents, images) pair process_table_with_names returns."""
    return ({pid: ppid for pid, ppid, _ in rows}, {pid: image for pid, _, image in rows})


class TestHasRunningShellWork(unittest.TestCase):
    def check(self, rows, root=100, platform="win32"):
        # The rule differs per OS, so each test names the OS whose tree it describes.
        with patch.object(process_tree.sys, "platform", platform), \
                patch.object(process_tree, "process_table_with_names", return_value=_table(rows)):
            return process_tree.has_running_shell_work(root)

    def test_the_measured_background_ping_chain_is_work(self):
        rows = [(100, 1, "agy.exe"), (101, 100, "conhost.exe"), (102, 100, "powershell.exe"),
                (103, 102, "cmd.exe"), (104, 103, "backend.exe"), (105, 104, "cmd.exe"),
                (106, 105, "ping.exe")]
        self.assertIs(self.check(rows), True)

    def test_agys_own_helper_tree_is_not_work(self):
        rows = [(100, 1, "agy.exe"), (101, 100, "conhost.exe"), (102, 100, "backend.exe"),
                (103, 100, "backend.exe"), (104, 100, "cmd.exe"), (105, 104, "node.exe"),
                (106, 100, "node.exe"), (107, 106, "cmd.exe"), (108, 107, "node.exe")]
        self.assertIs(self.check(rows), False)

    def test_a_shell_with_only_a_conhost_child_is_not_work(self):
        rows = [(100, 1, "agy.exe"), (102, 100, "powershell.exe"), (103, 102, "conhost.exe")]
        self.assertIs(self.check(rows), False)

    def test_an_empty_table_is_unknown(self):
        self.assertIsNone(self.check([]))

    def test_a_missing_root_pid_is_unknown(self):
        self.assertIsNone(self.check([(100, 1, "agy.exe")], root=None))

    def test_every_listed_shell_running_a_command_is_work(self):
        for shell in ("pwsh.exe", "bash.exe", "zsh.exe", "bash", "zsh", "sh", "fish", "dash"):
            with self.subTest(shell=shell):
                rows = [(100, 1, "agy"), (102, 100, shell), (103, 102, "sleep")]
                self.assertIs(self.check(rows), True)

    def posix_table(self, returncode, stdout):
        done = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with patch.object(process_tree.sys, "platform", "darwin"):
            with patch.object(process_tree.subprocess, "run", return_value=done):
                return process_tree.has_running_shell_work(100)

    def test_a_posix_login_shell_running_a_command_is_work(self):
        out = "  100     1 /usr/local/bin/agy\n  102   100 -zsh\n  103   102 /bin/sleep\n"
        self.assertIs(self.posix_table(0, out), True)

    def test_a_failed_posix_table_read_is_unknown(self):
        self.assertIsNone(self.posix_table(1, "  100     1 agy\n"))

    def posix_check(self, rows, root=100):
        with patch.object(process_tree.sys, "executable",
                          "/Applications/Gamachine.app/Contents/Resources/Backend/backend"):
            return self.check(rows, root, platform="darwin")

    def test_posix_measured_idle_tree_is_not_work(self):
        rows = [(100, 1, "agy"), (101, 100, "backend"), (102, 100, "backend"), (103, 100, "backend")]
        self.assertIs(self.posix_check(rows), False)

    def test_posix_measured_busy_tree_with_a_direct_sleep_is_work(self):
        rows = [(100, 1, "agy"), (101, 100, "backend"), (102, 100, "backend"), (103, 100, "backend"),
                (104, 100, "sleep")]
        self.assertIs(self.posix_check(rows), True)

    def test_posix_a_user_mcp_server_child_fails_safe_as_work(self):
        rows = [(100, 1, "agy"), (101, 100, "backend"), (102, 100, "node")]
        self.assertIs(self.posix_check(rows), True)

    def test_posix_a_shell_child_with_a_sleep_grandchild_is_work(self):
        rows = [(100, 1, "agy"), (101, 100, "backend"), (102, 100, "zsh"), (103, 102, "sleep")]
        self.assertIs(self.posix_check(rows), True)

    def test_posix_agy_with_no_children_is_not_work(self):
        self.assertIs(self.posix_check([(100, 1, "agy"), (200, 1, "launchd")]), False)

    def test_posix_an_empty_table_is_unknown(self):
        self.assertIsNone(self.posix_check([]))

    def test_a_shell_that_is_only_a_grandchild_is_not_work(self):
        rows = [(100, 1, "agy.exe"), (101, 100, "cmd.exe"), (102, 101, "powershell.exe"),
                (103, 102, "ping.exe")]
        self.assertIs(self.check(rows), False)

    def test_the_real_table_reads_and_contains_this_process(self):
        parents, images = process_tree.process_table_with_names()
        self.assertIn(__import__("os").getpid(), parents)
        self.assertEqual(set(parents), set(images))
        self.assertTrue(all(name == name.lower() for name in images.values()))
