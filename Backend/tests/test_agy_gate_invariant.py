"""The step-mode invariant between approval_mode and agy's hook state file.

At every instant at which current_mode() returns "step", every live or
closing agy child's hook state file denies per the step grammar (or is
absent). Verification round 2 (26 Sep 2026) broke it twice: overlapping flips
published step while an auto write still held the gate lock, and close()
deregistered a session before its child stopped. These tests force those
interleavings with events and an instrumented lock, never with sleeps.
"""
import asyncio
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from agentic import approval_mode
from agy_step_gate import decide
from providers import agy_provider, agy_session

WRITE = json.dumps({"toolCall": {"name": "write_to_file", "args": {}}}).encode()
SHELL = json.dumps({"toolCall": {"name": "run_command",
                                 "args": {"CommandLine": "whoami"}}}).encode()


class FakeProcess:
    def __init__(self, *, unkillable=False):
        self.returncode = None
        self.pid = 4242
        self.stdin = None
        self.killed = False
        self.unkillable = unkillable
        self.exited = asyncio.Event() if _loop_running() else None
        if self.exited is not None:
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()

    def kill(self):
        if self.unkillable:
            raise PermissionError("access denied")
        self.killed = True
        self.returncode = -9
        if self.exited is not None:
            self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode


async def _no_sleep(*_args, **_kwargs):
    return None


def _loop_running():
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class WaiterSignallingLock:
    """GATE_LOCK with a signal when a chosen thread starts waiting for it."""

    def __init__(self, inner):
        self.inner = inner
        self.watch = None
        self.waiting = threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if threading.current_thread() is self.watch:
            self.waiting.set()
        return self.inner.acquire(blocking, timeout)

    def release(self):
        self.inner.release()

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()


class GateStateCase:
    """A private home for the gate state file, and a clean agy registry."""

    def set_up_home(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        real_expand = os.path.expanduser
        self.home_patch = patch.object(
            agy_provider.os.path, "expanduser",
            side_effect=lambda p: p.replace("~", home, 1) if p.startswith("~") else real_expand(p))
        self.home_patch.start()
        self.state = agy_provider.gate_state_path()
        agy_session._SESSIONS.clear()
        agy_session._GATED_CHILDREN.clear()
        agy_session._RETIRED.clear()
        approval_mode.set_ui_secret("ui-secret")

    def tear_down_home(self):
        agy_session._SESSIONS.clear()
        agy_session._GATED_CHILDREN.clear()
        agy_session._RETIRED.clear()
        self.home_patch.stop()
        self.tmp.cleanup()

    def file_mode(self):
        try:
            with open(self.state, encoding="utf-8") as f:
                return json.load(f)["mode"]
        except FileNotFoundError:
            return None

    def live_session(self, conversation_id=31, **process_kwargs):
        session = agy_session.AgyStreamSession(conversation_id, cwd=self.tmp.name)
        session._active_process = FakeProcess(**process_kwargs)
        agy_session._SESSIONS[conversation_id] = session
        return session

    def record_publishes(self):
        """(published mode, state file mode) at the instant each flip publishes."""
        seen = []
        real = approval_mode._propagate_to_live_sessions

        def observed(auto):
            seen.append((approval_mode.current_mode(), self.file_mode()))
            return real(auto)
        return seen, patch.object(approval_mode, "_propagate_to_live_sessions", side_effect=observed)


class TestFlipOrdering(GateStateCase, unittest.TestCase):
    def setUp(self):
        self.set_up_home()

    def tearDown(self):
        self.tear_down_home()

    def test_step_is_refused_when_the_gate_can_be_neither_tightened_nor_the_child_stopped(self):
        """Verification round 3: with the step write, the removal and the kill
        all failing, step was published over a child whose hook still allowed."""
        approval_mode.set_mode("auto")
        self.live_session(unkillable=True)
        agy_provider.write_gate_state(auto=True)
        seen, observe = self.record_publishes()
        with observe, \
                patch.object(agy_provider, "write_gate_state", side_effect=PermissionError("locked")), \
                patch.object(agy_session, "_remove_gate_state", return_value=False):
            with self.assertRaises(agy_provider.AgyStepGateError) as refused:
                approval_mode.set_mode("step")
        self.assertEqual(refused.exception.code, "agy_step_refused")
        self.assertEqual(refused.exception.params, {"pids": "4242"})
        self.assertEqual(seen, [])
        self.assertEqual(approval_mode.current_mode(), "auto")

    def test_a_kill_that_works_on_the_second_listing_does_not_refuse_step(self):
        """Verification round 4: a child is listed once per registry, and a
        first kill that raised kept it a survivor after the second one worked."""
        approval_mode.set_mode("auto")
        session = self.live_session()
        agy_session._GATED_CHILDREN[object()] = session._active_process
        process, real_kill, calls = session._active_process, session._active_process.kill, []

        def flaky_kill():
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("transient")
            real_kill()
        process.kill = flaky_kill
        with patch.object(agy_provider, "write_gate_state", side_effect=PermissionError("locked")),                 patch.object(agy_session, "_remove_gate_state", return_value=False):
            approval_mode.set_mode("step")
        self.assertEqual(len(calls), 2)
        self.assertEqual(approval_mode.current_mode(), "step")

    def test_a_failed_save_puts_the_gate_back_to_the_published_mode(self):
        """Verification round 4: the gate was tightened, the save failed, and
        the hook kept denying an agy child that runs in auto mode."""
        approval_mode.set_mode("auto")
        self.live_session()
        agy_provider.write_gate_state(auto=True)

        class FailingStore:
            def set_setting(self, key, value):
                raise OSError("disk full")
        with patch.object(approval_mode, "_store", FailingStore()):
            with self.assertRaises(OSError):
                approval_mode.set_mode("step")
        self.assertEqual(approval_mode.current_mode(), "auto")
        self.assertEqual(decide(WRITE, self.state)["decision"], "allow")

    def test_step_is_never_published_while_an_auto_write_holds_the_gate(self):
        """The round-2 probe's interleaving: flip A (auto) is inside its state
        write when flip B (step) starts. B must not publish step until it has
        written step itself."""
        self.live_session()
        approval_mode.set_mode("step")
        in_auto_write, release_auto = threading.Event(), threading.Event()
        real_write = agy_provider.write_gate_state

        def blocking_write(auto):
            real_write(auto=auto)
            if auto and not in_auto_write.is_set():
                in_auto_write.set()
                assert release_auto.wait(10)

        lock = WaiterSignallingLock(approval_mode.GATE_LOCK)
        seen, observe = self.record_publishes()
        errors = []

        def flip(mode):
            try:
                approval_mode.set_mode(mode)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        with patch.object(agy_provider, "write_gate_state", side_effect=blocking_write), \
                patch.object(approval_mode, "GATE_LOCK", lock), observe:
            first = threading.Thread(target=flip, args=("auto",))
            first.start()
            self.assertTrue(in_auto_write.wait(10))
            self.assertEqual(self.file_mode(), "auto")
            second = threading.Thread(target=flip, args=("step",))
            lock.watch = second
            second.start()
            self.assertTrue(lock.waiting.wait(10))
            # B is waiting for the gate: step is not published, and the hook's
            # "allow" matches the published auto.
            self.assertEqual(approval_mode.current_mode(), "auto")
            release_auto.set()
            first.join(10)
            second.join(10)
        self.assertEqual(errors, [])
        # Auto publishes, then loosens; step tightens, then publishes.
        self.assertEqual(seen, [("auto", "step"), ("step", "step")])
        self.assertEqual(approval_mode.current_mode(), "step")
        self.assertEqual(decide(SHELL, self.state)["decision"], "deny")

    def test_flip_to_step_writes_the_file_before_it_publishes(self):
        session = self.live_session()
        approval_mode.set_mode("auto")
        self.assertEqual(self.file_mode(), "auto")
        seen, observe = self.record_publishes()
        with observe:
            approval_mode.set_mode("step")
        self.assertEqual(seen, [("step", "step")])
        self.assertFalse(session._active_process.killed)

    def test_failed_step_write_removes_the_file_before_publishing(self):
        self.live_session()
        approval_mode.set_mode("auto")
        seen, observe = self.record_publishes()
        with observe, patch.object(agy_provider, "write_gate_state", side_effect=OSError("locked")):
            approval_mode.set_mode("step")
        self.assertEqual(seen, [("step", None)])
        self.assertEqual(decide(WRITE, self.state)["decision"], "deny")

    def test_file_that_can_be_neither_written_nor_removed_kills_before_publishing(self):
        session = self.live_session()
        approval_mode.set_mode("auto")
        killed_at_publish = []
        real = approval_mode._propagate_to_live_sessions

        def observed(auto):
            killed_at_publish.append((approval_mode.current_mode(), session._active_process.killed))
            return real(auto)
        with patch.object(agy_provider, "write_gate_state", side_effect=OSError("locked")), \
                patch.object(agy_session.os, "remove", side_effect=PermissionError("locked")), \
                patch.object(approval_mode, "_propagate_to_live_sessions", side_effect=observed):
            approval_mode.set_mode("step")
        self.assertEqual(killed_at_publish, [("step", True)])

    def test_a_setter_cannot_write_auto_after_step_is_published(self):
        """agent_runner sets auto_approve=True from a request; the file follows
        the published mode, read under the same lock."""
        session = self.live_session()
        approval_mode.set_mode("step")
        session.auto_approve = True
        self.assertEqual(self.file_mode(), "step")

    def test_a_secret_change_that_makes_step_effective_tightens_first(self):
        class FreshStore:
            def get_setting(self, key):
                return None

            def set_setting(self, key, value):  # pragma: no cover
                raise AssertionError("not saved")
        approval_mode.bind_store(FreshStore())
        self.assertEqual(approval_mode.current_mode(), "auto")  # fresh install + secret
        self.live_session()
        agy_provider.write_gate_state(auto=True)
        observed = []
        real = agy_session.tighten_gate_state

        def tighten():
            observed.append(approval_mode.current_mode())
            real()
        with patch.object(agy_session, "tighten_gate_state", side_effect=tighten):
            approval_mode.set_ui_secret("")
        self.assertEqual(observed, ["auto"])  # tightened before step took effect
        self.assertEqual(approval_mode.current_mode(), "step")
        self.assertEqual(self.file_mode(), "step")

    def test_no_agy_child_means_no_write(self):
        agy_session._SESSIONS[5] = agy_session.AgyStreamSession(5, cwd=self.tmp.name)
        with patch.object(agy_provider, "write_gate_state") as write:
            approval_mode.set_mode("step")
        write.assert_not_called()


class TestSpawnAndClose(GateStateCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.set_up_home()

    async def asyncTearDown(self):
        self.tear_down_home()

    async def test_a_one_shot_being_spawned_is_tightened_by_a_flip(self):
        """A one-shot is not in _SESSIONS until its process exists; it is
        gated from its state write on, so a flip during the spawn reaches it."""
        from providers.agy_provider import AgyProvider
        approval_mode.set_mode("auto")
        spawning, release = asyncio.Event(), asyncio.Event()
        seen, observe = self.record_publishes()

        async def spawn(*argv, **kwargs):
            spawning.set()
            await release.wait()
            return FakeProcess()
        patches = [
            patch.object(AgyProvider, "_agy_binary", return_value="fake-agy"),
            patch.object(AgyProvider, "_resolve_exec", side_effect=lambda c: c),
            patch.object(AgyProvider, "_write_mcp_config", return_value=""),
            patch.object(AgyProvider, "_set_agy_model"),
            patch.object(AgyProvider, "_step_gate_command", return_value=__file__),
            patch.object(AgyProvider, "_stream_instructions", return_value=""),
            patch("providers.workspace_config.guvenli_config_yaz", return_value=True),
            patch.object(AgyProvider, "_step_gate_problem", return_value=None),
            patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=spawn),
            observe,
        ]
        for p in patches:
            p.start()
        try:
            session = agy_session.AgyStreamSession(-3, cwd=self.tmp.name)
            start = asyncio.create_task(session._start("gemini-3.6-flash", self.tmp.name))
            await spawning.wait()
            self.assertEqual(self.file_mode(), "auto")
            self.assertNotIn(-3, agy_session._SESSIONS)
            approval_mode.set_mode("step")
            release.set()
            await start
            self.assertEqual(seen[-1], ("step", "step"))
            await session.close()
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(agy_session._GATED_CHILDREN, {})

    async def _close_until_deregistered(self, session):
        """Start close() while the child cannot be reaped yet (stop lock held)."""
        await session._stop_lock.acquire()
        close = asyncio.create_task(session.close())
        for _ in range(1000):
            if session.conversation_id not in agy_session._SESSIONS and session._kapandi:
                return close
            if close.done():
                close.result()  # surfaces the error that ended close() early
            await asyncio.sleep(0)
        self.fail("close() never reached its stop")

    async def test_a_lone_closing_child_is_denied_everything_before_it_leaves_the_registry(self):
        approval_mode.set_mode("auto")
        session = self.live_session(unkillable=True)
        agy_provider.write_gate_state(auto=True)
        deregistered_with = []

        class Registry(dict):
            def pop(inner, key, *default):
                deregistered_with.append(self.file_mode())
                return dict.pop(inner, key, *default)
        registry = Registry(agy_session._SESSIONS)
        with patch.object(agy_session, "_SESSIONS", registry):
            close = await self._close_until_deregistered(session)
            self.assertEqual(deregistered_with, ["closed"])
            self.assertEqual(decide(WRITE, self.state)["decision"], "deny")
            # The kill failed and the child lives on: a flip still reaches it.
            approval_mode.set_mode("step")
            self.assertEqual(self.file_mode(), "step")
            self.assertEqual(decide(SHELL, self.state)["decision"], "deny")
            session._active_process.unkillable = False
            session._stop_lock.release()
            await close
        self.assertTrue(session._active_process is None)
        self.assertEqual(agy_session._GATED_CHILDREN, {})

    async def _close_until_retired(self, session):
        """Start close() and hold it before its stop, past _retire_gate."""
        await session._stop_lock.acquire()
        close = asyncio.create_task(session.close())
        for _ in range(1000):
            if session._kapandi:
                return close
            if close.done():
                close.result()
            await asyncio.sleep(0)
        self.fail("close() never reached its stop")

    async def test_closing_next_to_another_live_child_keeps_the_file_following_the_mode(self):
        """The file is shared: "closed" would deny the other child too, so only
        the kill ends the closing child's access. While that kill fails it
        stays registered and gated (verification round 3: it was deregistered
        while still able to write), and the next flip to step reaches it."""
        approval_mode.set_mode("auto")
        other = self.live_session(conversation_id=32)
        closing = self.live_session(conversation_id=33, unkillable=True)
        agy_provider.write_gate_state(auto=True)
        close = await self._close_until_retired(closing)
        self.assertEqual(self.file_mode(), "auto")
        self.assertIs(agy_session._SESSIONS.get(33), closing)
        self.assertIn(closing._gate_token, agy_session._GATED_CHILDREN)
        seen, observe = self.record_publishes()
        with observe:
            approval_mode.set_mode("step")
        self.assertEqual(seen, [("step", "step")])
        closing._active_process.unkillable = False
        closing._stop_lock.release()
        await close
        self.assertNotIn(33, agy_session._SESSIONS)
        self.assertNotIn(closing._gate_token, agy_session._GATED_CHILDREN)
        self.assertFalse(other._active_process.killed)

    async def test_a_closed_session_kept_by_a_failed_kill_is_replaced_on_reopen(self):
        approval_mode.set_mode("auto")
        self.live_session(conversation_id=32)
        closing = self.live_session(conversation_id=33, unkillable=True)
        agy_provider.write_gate_state(auto=True)
        close = await self._close_until_retired(closing)
        reopened = agy_session.get_session(33, cwd=self.tmp.name)
        self.assertIsNot(reopened, closing)
        self.assertFalse(reopened._kapandi)
        closing._active_process.unkillable = False
        closing._stop_lock.release()
        await close
        self.assertIs(agy_session._SESSIONS.get(33), reopened)

    async def test_a_later_close_retries_the_kill_of_a_child_that_survived_close(self):
        """Verification round 4: the failed reap dropped the handle, so no later
        close could ever stop the child."""
        approval_mode.set_mode("auto")
        self.live_session(conversation_id=32)
        closing = self.live_session(conversation_id=33, unkillable=True)
        agy_provider.write_gate_state(auto=True)
        with patch.object(agy_session.asyncio, "sleep", side_effect=_no_sleep):
            await closing.close()
        self.assertIsNotNone(closing._active_process)
        self.assertIs(agy_session._SESSIONS.get(33), closing)
        closing._active_process.unkillable = False
        await agy_session.close_session(33)
        self.assertNotIn(33, agy_session._SESSIONS)
        self.assertEqual(agy_session._RETIRED, set())

    async def test_no_new_child_starts_while_a_closed_child_survives(self):
        """Verification round 4: a lone closed child was denied by a "closed"
        file, and the next spawn's auto write gave it its tools back."""
        approval_mode.set_mode("auto")
        closing = self.live_session(conversation_id=33, unkillable=True)
        agy_provider.write_gate_state(auto=True)
        close = await self._close_until_retired(closing)
        self.assertEqual(self.file_mode(), "closed")
        reopened = agy_session.get_session(33, cwd=self.tmp.name)
        with patch.object(agy_provider.AgyProvider, "_write_mcp_config", return_value=""),                 patch.object(agy_provider.AgyProvider, "_set_agy_model"),                 patch.object(agy_provider.AgyProvider, "_resolve_exec", side_effect=lambda c: c),                 patch.object(agy_provider.AgyProvider, "_agy_binary", return_value="fake-agy"):
            with self.assertRaises(agy_provider.AgyStepGateError) as refused:
                await reopened._start("gemini-3.6-flash", self.tmp.name)
        self.assertEqual(refused.exception.code, "agy_closed_child_alive")
        self.assertEqual(decide(WRITE, self.state)["decision"], "deny")
        closing._active_process.unkillable = False
        closing._stop_lock.release()
        await close

    async def test_close_kills_the_child_before_deregistering(self):
        session = self.live_session()
        close = await self._close_until_deregistered(session)
        self.assertTrue(session._active_process.killed)
        session._stop_lock.release()
        await close


class TestClosedState(unittest.TestCase):
    def test_closed_state_denies_every_call_even_the_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state.json")
            with open(state, "w", encoding="utf-8") as f:
                json.dump({"mode": "closed", "launcher": ""}, f)
            launcher = "unityai"
            bridge = json.dumps({"toolCall": {"name": "run_command", "args": {
                "CommandLine": f"{launcher} delete-file --path a.txt"}}}).encode()
            for payload in (WRITE, SHELL, bridge):
                decision = decide(payload, state, windows=False)
                self.assertEqual(decision["decision"], "deny")
                self.assertIn("closed", decision["reason"])


if __name__ == "__main__":
    unittest.main()
