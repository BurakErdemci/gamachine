"""Replay the genuine two-turn fixture; tool/failure cases are synthetic.

No CLI, config writer, external service, or filesystem scratch is used here.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from providers import agy_session
from providers.agy_provider import AgyProvider
from providers.cli_base import BaseCLIProvider


REAL_GLOBAL_AUTO_MODE = agy_session._global_auto_mode
FIXTURE = Path(__file__).parent / "fixtures/agy_stream_json_sample.ndjson"
EVENTS = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line]
SESSION_ID = EVENTS[0]["conversation_id"]
TURNS = []
pending = []
for event in EVENTS:
    pending.append(event)
    if event["event"] == "result":
        TURNS.append(pending)
        pending = []


class FakeStdin:
    def __init__(self, process):
        self.process = process
        self.lines = []
        self.closed = False
        self.drains = 0

    def write(self, data):
        if self.closed:
            raise BrokenPipeError("fake closed stdin")
        self.lines.append(data)

    async def drain(self):
        self.drains += 1
        self.process.written.set()
        if self.process.release is not None:
            await self.process.release.wait()
        await asyncio.sleep(0)
        index = self.drains - 1
        if index < len(self.process.turns):
            for event in self.process.turns[index]:
                self.process.stdout.feed_data((json.dumps(event) + "\n").encode("utf-8"))
        if self.process.crash:
            self.process.stderr.feed_data(b"fake agy stderr: process crashed mid-turn\n")
            self.process.finish(27)

    def close(self):
        self.closed = True
        self.process.finish(0)


class FakeProcess:
    def __init__(self, turns=None, *, crash=False, release=None):
        self.turns = copy.deepcopy(TURNS if turns is None else turns)
        self.crash = crash
        self.release = release
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = FakeStdin(self)
        self.written = asyncio.Event()
        self.exited = asyncio.Event()
        self.killed = False

    def finish(self, code):
        if self.returncode is None:
            self.returncode = code
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.exited.set()

    def kill(self):
        self.killed = True
        self.finish(-9)

    async def wait(self):
        await self.exited.wait()
        return self.returncode


class TestAgyStreamSession(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        agy_session._SESSIONS.clear()
        agy_session._RESUME_IDS.clear()
        self.processes = []
        self.spawns = []
        self.plans = []
        self.patches = [
            patch.object(BaseCLIProvider, "_AGY_LOCK", asyncio.Lock()),
            patch.object(AgyProvider, "_agy_binary", return_value="fake-agy"),
            patch.object(AgyProvider, "_resolve_exec", side_effect=lambda command: command),
            patch.object(AgyProvider, "_write_mcp_config", return_value=""),
            patch.object(AgyProvider, "_set_agy_model"),
            patch.object(AgyProvider, "_write_step_gate", return_value=True),
            patch("providers.agy_provider.write_gate_state"),
            patch.object(AgyProvider, "_stream_instructions", return_value=""),
            patch.object(agy_session, "_global_auto_mode", side_effect=lambda: self.auto),
            patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=self.spawn),
        ]
        self.auto = True
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        await agy_session.close_all_sessions()
        for item in reversed(self.patches):
            item.stop()

    async def spawn(self, *argv, **kwargs):
        self.spawns.append((argv, kwargs))
        process = FakeProcess(**(self.plans.pop(0) if self.plans else {}))
        self.processes.append(process)
        return process

    async def collect(self, session=None, message="hello", **kwargs):
        session = session or agy_session.get_session(11)
        return [event async for event in session.stream(message, **kwargs)]

    async def test_two_turns_share_process_conversation_and_keep_stdin_open(self):
        session = agy_session.get_session(11)
        first = await self.collect(session)
        second = await self.collect(session, "second turn")
        self.assertEqual(len(self.processes), 1)
        self.assertEqual([first[-1]["session_id"], second[-1]["session_id"]], [SESSION_ID] * 2)
        self.assertIs(agy_session.peek_session(11), session)
        self.assertTrue(session.is_live)
        self.assertFalse(self.processes[0].stdin.closed)
        self.assertEqual(self.processes[0].stdin.drains, 2)
        self.assertNotIn("--conversation", self.spawns[0][0])
        self.assertEqual([e["content"] for e in first if e["type"] == "text"], ["OK\n"])

    async def test_the_child_env_names_its_chat_only_for_a_real_one(self):
        # agy hands its env to the Unity MCP bridge; a one-shot (< 0) has no
        # chat, and the backend's own value (999) must never pass through.
        with patch.dict("os.environ", {"GAMACHINE_CONVERSATION_ID": "999"}):
            await self.collect(agy_session.get_session(11))
            await self.collect(agy_session.get_session(-7))
        self.assertEqual(self.spawns[0][1]["env"]["GAMACHINE_CONVERSATION_ID"], "11")
        self.assertNotIn("GAMACHINE_CONVERSATION_ID", self.spawns[1][1]["env"])

    async def test_utf8_long_multiline_prompt_only_on_stdin(self):
        message = "private prompt: şİ🙂\n" * 4000
        await self.collect(message=message)
        argv, kwargs = self.spawns[0]
        self.assertFalse(any("private prompt" in argument for argument in argv))
        self.assertIn("-p=", argv)
        self.assertNotIn("-p", argv)
        self.assertEqual(argv[argv.index("--input-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(kwargs["stdin"], asyncio.subprocess.PIPE)
        self.assertEqual(kwargs["stdout"], asyncio.subprocess.PIPE)
        data = self.processes[0].stdin.lines[0]
        self.assertEqual(data.count(b"\n"), 1)
        self.assertEqual(json.loads(data.decode("utf-8")),
                         {"event": "user", "message": {"content": message}})

    async def test_result_usage_is_per_turn_including_cached_and_thinking_tokens(self):
        session = agy_session.get_session(11)
        first = await self.collect(session)
        second = await self.collect(session)
        first_usage = next(e for e in first if e["type"] == "turn_usage")
        second_usage = next(e for e in second if e["type"] == "turn_usage")
        for key in agy_session._USAGE_KEYS:
            self.assertEqual(first_usage[key], TURNS[0][-1]["result"]["usage"][key])
            self.assertEqual(second_usage[key], TURNS[1][-1]["result"]["usage"][key]
                             - TURNS[0][-1]["result"]["usage"][key])
        self.assertEqual(second_usage["input_tokens"], 6512)
        self.assertEqual(second_usage["thinking_tokens"], 127)
        self.assertEqual(second_usage["total_tokens"], 6640)
        self.assertIsNone(second_usage["cost_usd"])

    async def test_synthetic_tool_steps_are_ordered_and_deduplicated(self):
        # Trivial live prompts produced no tools: these tool_info cases are synthetic.
        tool = {"event": "step_update", "step_update": {
            "conversation_id": SESSION_ID, "step_index": 9, "state": "RUNNING",
            "step_type": "tool", "tool_info": {"name": "view_file", "parameters": {"path": "a.cs"}},
        }}
        tool_done = copy.deepcopy(tool)
        tool_done["step_update"].update(state="DONE")
        tool_done["step_update"]["tool_info"]["output"] = "file contents"
        turn = [TURNS[0][0], TURNS[0][2], tool, tool, tool_done, tool_done,
                TURNS[0][2], TURNS[0][-1]]
        self.plans = [{"turns": [turn]}]
        events = await self.collect()
        self.assertEqual([e["type"] for e in events],
                         ["text", "tool_call", "tool_result", "text", "turn_usage", "response", "done"])
        self.assertEqual(events[1]["arguments"], {"path": "a.cs"})
        self.assertEqual(events[2]["summary"], "file contents")
        self.assertTrue(events[2]["success"])

    async def test_synthetic_tool_error_is_reported(self):
        tool = {"event": "step_update", "step_update": {
            "conversation_id": SESSION_ID, "step_index": 7, "state": "DONE",
            "step_type": "tool", "tool_info": {
                "name": "view_file", "parameters": '{"path":"missing.cs"}', "error": "missing",
            },
        }}
        self.plans = [{"turns": [[TURNS[0][0], tool, TURNS[0][-1]]]}]
        events = await self.collect()
        self.assertEqual(events[0]["arguments"], {"path": "missing.cs"})
        self.assertFalse(events[1]["success"])
        self.assertEqual(events[1]["summary"], "missing")

    async def test_process_death_emits_stderr_drops_registry_and_respawns_with_resume(self):
        self.plans = [{"turns": [[TURNS[0][0], TURNS[0][2]]], "crash": True}, {}]
        old = agy_session.get_session(11)
        events = await self.collect(old)
        self.assertEqual([e["type"] for e in events], ["text", "error"])
        self.assertIn("process crashed mid-turn", events[-1]["message"])
        self.assertIsNone(agy_session.peek_session(11))
        self.assertFalse(old.is_live)
        new = agy_session.get_session(11)
        self.assertIsNot(new, old)
        next_events = await self.collect(new)
        self.assertEqual(next_events[-1]["type"], "done")
        argv = self.spawns[1][0]
        self.assertEqual(argv[argv.index("--conversation") + 1], SESSION_ID)

    async def test_model_change_respawns_and_preserves_uuid(self):
        session = agy_session.get_session(11)
        await self.collect(session, model="gemini-3.6-flash")
        first = self.processes[0]
        await self.collect(session, model="gemini-3.8-flash")
        self.assertEqual(len(self.processes), 2)
        self.assertIsNotNone(first.returncode)
        self.assertEqual(session.model, "gemini-3.8-flash")
        argv = self.spawns[1][0]
        self.assertEqual(argv[argv.index("--conversation") + 1], SESSION_ID)
        self.assertEqual(AgyProvider._set_agy_model.call_args.args[0], "Gemini 3.8 Flash (High)")

    async def test_approval_mode_change_keeps_the_process_and_rewrites_its_state(self):
        # Every process has the hook, so a flip needs no respawn: the next turn
        # rewrites the state file the hook reads.
        from providers import agy_provider
        session = agy_session.get_session(11)
        await self.collect(session)
        self.assertEqual(AgyProvider._write_step_gate.call_args.kwargs, {"step_mode": False})
        agy_provider.write_gate_state.reset_mock()
        self.auto = False
        await asyncio.wait_for(self.collect(session, "second turn"), 5)
        self.assertEqual(len(self.processes), 1)
        self.assertIsNone(self.processes[0].returncode)
        agy_provider.write_gate_state.assert_called_once_with(auto=False)
        self.assertFalse(session.auto_approve)

    async def test_flip_to_step_during_an_auto_turn_reaches_the_hook_state(self):
        # The previously accepted window: a process spawned in auto, mid-turn.
        from agentic import approval_mode
        from providers import agy_provider
        release = asyncio.Event()
        self.plans = [{"release": release}]
        session = agy_session.get_session(11)
        turn = asyncio.create_task(self.collect(session))
        await asyncio.wait_for(self.wait_for_process_write(), 5)
        agy_provider.write_gate_state.reset_mock()
        self.auto = False
        approval_mode._propagate_to_live_sessions(False)
        agy_provider.write_gate_state.assert_called_once_with(auto=False)
        release.set()
        events = await asyncio.wait_for(turn, 5)
        self.assertEqual(events[-1]["type"], "done")

    async def wait_for_process_write(self):
        while not self.processes:
            await asyncio.sleep(0)
        await self.processes[0].written.wait()

    async def test_flip_during_spawn_is_written_before_the_first_stdin_line(self):
        # The verification round's window: auto when the state was written,
        # step by the time the process exists (no live process to update yet).
        from providers import agy_provider
        order = []
        agy_provider.write_gate_state.side_effect = lambda auto: order.append(
            ("state", auto, sum(len(p.stdin.lines) for p in self.processes)))
        real_spawn = self.spawn

        async def flipping_spawn(*argv, **kwargs):
            self.auto = False
            return await real_spawn(*argv, **kwargs)

        with patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=flipping_spawn):
            events = await self.collect()
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(AgyProvider._write_step_gate.call_args.kwargs, {"step_mode": False})
        self.assertIn(("state", False, 0), order)

    async def test_stale_state_after_spawn_stops_the_process(self):
        from providers import agy_provider
        real_spawn = self.spawn

        async def flipping_spawn(*argv, **kwargs):
            self.auto = False
            return await real_spawn(*argv, **kwargs)

        with patch.object(agy_session, "_sync_gate_state", return_value=False), \
                patch.object(agy_session.asyncio, "create_subprocess_exec", side_effect=flipping_spawn):
            events = await self.collect()
        self.assertEqual([e["type"] for e in events], ["error"])
        self.assertIn("agy başlatılmadı", events[0]["message"])
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertEqual(self.processes[0].stdin.lines, [])

    async def test_stale_state_at_turn_start_stops_a_kept_process(self):
        session = agy_session.get_session(11)
        await self.collect(session)
        with patch.object(agy_session, "_sync_gate_state", return_value=False):
            events = await self.collect(session, "second turn")
        self.assertEqual([e["type"] for e in events], ["error"])
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertEqual(len(self.processes[0].stdin.lines), 1)

    async def test_setter_kills_a_live_process_whose_state_stays_stale(self):
        self.auto = False
        session = agy_session.get_session(11)
        await self.collect(session)
        with patch.object(agy_session, "_sync_gate_state", return_value=False):
            session.auto_approve = False
        self.assertTrue(self.processes[0].killed)

    async def test_one_shot_session_is_reached_by_a_flip_while_it_runs(self):
        from agentic import approval_mode
        from providers import agy_provider
        release = asyncio.Event()
        self.plans = [{"release": release}]
        session = agy_session.get_session(-7)
        turn = asyncio.create_task(self.collect(session))
        await asyncio.wait_for(self.wait_for_process_write(), 5)
        self.assertIs(agy_session._SESSIONS.get(-7), session)
        self.assertIsNone(agy_session.peek_session(-7))
        agy_provider.write_gate_state.reset_mock()
        self.auto = False
        approval_mode._propagate_to_live_sessions(False)
        agy_provider.write_gate_state.assert_called_once_with(auto=False)
        release.set()
        await asyncio.wait_for(turn, 5)
        await session.close()
        self.assertNotIn(-7, agy_session._SESSIONS)

    async def test_close_all_sessions_stops_a_running_one_shot(self):
        session = agy_session.get_session(-8)
        await self.collect(session)
        self.assertIn(-8, agy_session._SESSIONS)
        await agy_session.close_all_sessions()
        self.assertNotIn(-8, agy_session._SESSIONS)
        self.assertIsNotNone(self.processes[0].returncode)

    async def test_mode_flip_refreshes_hook_state_of_a_live_step_process(self):
        from providers import agy_provider
        self.auto = False
        session = agy_session.get_session(11)
        await self.collect(session)
        agy_provider.write_gate_state.reset_mock()
        # _propagate_to_live_sessions sets the flag; the file follows the
        # GLOBAL mode, not the value that was set.
        self.auto = True
        session.auto_approve = False
        agy_provider.write_gate_state.assert_called_once_with(auto=True)
        self.assertEqual(len(self.processes), 1)

    async def test_step_mode_without_a_verified_gate_never_spawns(self):
        from providers.agy_provider import AgyStepGateError
        self.auto = False
        AgyProvider._write_step_gate.side_effect = AgyStepGateError("kapı kurulamadı: X")
        events = await self.collect()
        self.assertEqual(self.spawns, [])
        self.assertEqual([e["type"] for e in events], ["error"])
        self.assertIn("kapı kurulamadı: X", events[0]["message"])
        # A later turn tries again, and spawns once the gate is in place.
        AgyProvider._write_step_gate.side_effect = None
        events = await self.collect()
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(len(self.spawns), 1)

    async def test_unreadable_mode_spawns_in_step_mode(self):
        from agentic import approval_mode
        with patch.object(approval_mode, "is_auto", side_effect=RuntimeError("store gone")):
            self.assertFalse(REAL_GLOBAL_AUTO_MODE())
        with patch.object(approval_mode, "is_auto", return_value=True):
            self.assertTrue(REAL_GLOBAL_AUTO_MODE())

    async def test_restart_uses_uuid_from_existing_disk_store_interface(self):
        session = agy_session.get_session(11, resume_id=SESSION_ID)
        await self.collect(session)
        argv = self.spawns[0][0]
        self.assertEqual(argv[argv.index("--conversation") + 1], SESSION_ID)
        await self.collect(session)
        self.assertEqual(len(self.spawns), 1)

    async def test_timeout_closes_process_and_emits_one_error(self):
        self.plans = [{"turns": [[TURNS[0][0]]]}]
        with patch.object(BaseCLIProvider, "_AGY_MAX_TOTAL", 0.02):
            events = await self.collect()
        self.assertEqual([e["type"] for e in events], ["error"])
        self.assertIn("timed out", events[0]["message"])
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertTrue(self.processes[0].killed)
        self.assertIsNone(agy_session.peek_session(11))

    async def test_stop_and_stdout_eof_can_close_concurrently(self):
        self.plans = [{"turns": [[TURNS[0][0]]]}]
        session = agy_session.get_session(11)
        task = asyncio.create_task(self.collect(session))
        while not self.processes:
            await asyncio.sleep(0)
        await self.processes[0].written.wait()
        await agy_session.close_session(11)
        events = await asyncio.wait_for(task, timeout=1)
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(self.processes[0].killed)
        self.assertIsNone(session._stderr_task)
        self.assertIsNone(agy_session.peek_session(11))

    async def test_global_lock_covers_turn_but_not_idle_process_lifetime(self):
        release = asyncio.Event()
        self.plans = [{"release": release}, {}]
        first = asyncio.create_task(self.collect(agy_session.get_session(11)))
        while not self.processes:
            await asyncio.sleep(0)
        await self.processes[0].written.wait()
        second = asyncio.create_task(self.collect(agy_session.get_session(12)))
        await asyncio.sleep(0)
        self.assertEqual(len(self.spawns), 1)
        self.assertTrue(BaseCLIProvider._AGY_LOCK.locked())
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
        self.assertEqual(len(self.spawns), 2)
        self.assertIsNone(self.processes[0].returncode)
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_lock_wait_is_subject_to_turn_time_limit(self):
        await BaseCLIProvider._AGY_LOCK.acquire()
        try:
            with patch.object(BaseCLIProvider, "_AGY_MAX_TOTAL", 0.02):
                events = await self.collect()
        finally:
            BaseCLIProvider._AGY_LOCK.release()
        self.assertEqual([event["type"] for event in events], ["status", "error"])
        self.assertEqual(events[0]["code"], "agy_queued")
        self.assertIn("timed out", events[1]["message"])
        self.assertEqual(self.spawns, [])
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_cancel_while_stdin_drain_is_blocked_closes_process(self):
        self.plans = [{"release": asyncio.Event()}]
        session = agy_session.get_session(11)
        task = asyncio.create_task(self.collect(session))
        while not self.processes:
            await asyncio.sleep(0)
        await self.processes[0].written.wait()
        self.assertIn(session, session.iptal_edilecekler())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertIsNone(agy_session.peek_session(11))
        self.assertIsNone(session.active_provider)

    async def test_lock_is_released_when_cleanup_raises_cancelled_error(self):
        session = agy_session.AgyStreamSession(-12, cwd=".")

        async def fail_start(*_args):
            raise RuntimeError("original turn failure")

        async def fail_close(*_args, **_kwargs):
            raise asyncio.CancelledError("cleanup cancellation")

        with patch.object(session, "_start", fail_start), \
             patch.object(session, "close", fail_close):
            events = await self.collect(session)

        self.assertEqual(events, [{"type": "error", "message": "original turn failure"}])
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_result_response_is_redacted_before_it_leaves_the_stream(self):
        result = copy.deepcopy(TURNS[0][-1])
        result["result"]["response"] = "child said X-API-Key: response-secret-abcdefghijkl"
        self.plans = [{"turns": [[TURNS[0][0], result]]}]
        events = await self.collect()
        response = next(event for event in events if event["type"] == "response")
        self.assertNotIn("response-secret", response["content"])
        self.assertIn("<REDACTED>", response["content"])
        self.assertEqual(events[-1]["type"], "done")

    async def test_turn_lock_is_held_through_teardown_and_always_released(self):
        session = agy_session.AgyStreamSession(-13, cwd=".")
        cleanup_started = asyncio.Event()
        allow_cleanup = asyncio.Event()

        async def fail_start(*_args):
            raise RuntimeError("original turn failure")

        async def slow_close(*_args, **_kwargs):
            cleanup_started.set()
            await allow_cleanup.wait()

        with patch.object(session, "_start", fail_start), \
             patch.object(session, "close", slow_close):
            task = asyncio.create_task(self.collect(session))
            await asyncio.wait_for(cleanup_started.wait(), timeout=1)
            queued = asyncio.create_task(self.collect(agy_session.get_session(11)))
            await asyncio.sleep(0)
            self.assertTrue(BaseCLIProvider._AGY_LOCK.locked())
            self.assertEqual(self.spawns, [])
            allow_cleanup.set()
            events = await asyncio.wait_for(task, timeout=1)
            queued_events = await asyncio.wait_for(queued, timeout=1)

        self.assertEqual(events, [{"type": "error", "message": "original turn failure"}])
        self.assertEqual(queued_events[-1]["type"], "done")
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_caller_cancellation_during_cleanup_propagates(self):
        session = agy_session.AgyStreamSession(-14, cwd=".")
        cleanup_started = asyncio.Event()
        closes = 0

        async def fail_start(*_args):
            raise RuntimeError("original turn failure")

        async def blocking_close(*_args, **_kwargs):
            nonlocal closes
            closes += 1
            if closes == 1:
                cleanup_started.set()
                await asyncio.Event().wait()

        with patch.object(session, "_start", fail_start), \
             patch.object(session, "close", blocking_close):
            task = asyncio.create_task(self.collect(session))
            await asyncio.wait_for(cleanup_started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)

        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_closed_queued_session_cannot_spawn(self):
        session = agy_session.get_session(11)
        await BaseCLIProvider._AGY_LOCK.acquire()
        task = asyncio.create_task(self.collect(session))
        await asyncio.sleep(0)
        await agy_session.close_session(11)
        BaseCLIProvider._AGY_LOCK.release()
        events = await task
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.spawns, [])

    # Queued indicator: a turn waiting on another chat's agy turn says so once,
    # then clears when it starts; a free lock says nothing.
    def live_waiters(self):
        waiters = getattr(BaseCLIProvider._AGY_LOCK, "_waiters", None) or ()
        return [waiter for waiter in waiters if not waiter.cancelled()]

    async def test_free_lock_emits_no_queued_status(self):
        events = await self.collect()
        self.assertNotIn("status", [event["type"] for event in events])
        self.assertEqual(events[-1]["type"], "done")

    async def test_turn_waiting_on_held_lock_emits_queued_once_then_started(self):
        release = asyncio.Event()
        self.plans = [{"release": release}, {}]
        first = asyncio.create_task(self.collect(agy_session.get_session(11)))
        while not self.processes:
            await asyncio.sleep(0)
        await self.processes[0].written.wait()

        second_stream = agy_session.get_session(12).stream("second chat")
        queued = await anext(second_stream)
        self.assertEqual(queued, {"type": "status", "code": "agy_queued",
                                  "detail": agy_session._QUEUED_EVENT["detail"]})
        waiting = asyncio.create_task(anext(second_stream))
        await asyncio.sleep(0.01)
        self.assertFalse(waiting.done())  # nothing more while the lock is held
        self.assertEqual(len(self.spawns), 1)

        release.set()
        started = await asyncio.wait_for(waiting, timeout=1)
        self.assertEqual(started["code"], "agy_started")
        rest = [event async for event in second_stream]
        await asyncio.wait_for(first, timeout=1)

        self.assertEqual(rest[-1]["type"], "done")
        self.assertNotIn("status", [event["type"] for event in rest])
        self.assertEqual(len(self.spawns), 2)
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_stop_while_queued_ends_turn_without_taking_the_lock(self):
        session = agy_session.get_session(11)
        await BaseCLIProvider._AGY_LOCK.acquire()
        try:
            stream = session.stream("hello")
            self.assertEqual((await anext(stream))["code"], "agy_queued")
            pending = asyncio.create_task(anext(stream))
            for _ in range(20):
                if self.live_waiters():
                    break
                await asyncio.sleep(0)
            self.assertEqual(len(self.live_waiters()), 1)
            await agy_session.close_session(11)
            # The running turn still holds the lock; Stop must not wait for it.
            event = await asyncio.wait_for(pending, timeout=1)
            self.assertEqual(event, {"type": "error", "message": "agy session was stopped."})
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
            await asyncio.sleep(0)
            self.assertEqual(self.live_waiters(), [])
            self.assertTrue(BaseCLIProvider._AGY_LOCK.locked())  # still the holder's
        finally:
            BaseCLIProvider._AGY_LOCK.release()
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())
        self.assertEqual(self.spawns, [])
        # The lock is usable again: a fresh turn runs to done.
        self.assertEqual((await self.collect(agy_session.get_session(11)))[-1]["type"], "done")
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_abort_while_queued_leaves_no_waiter_or_lock(self):
        await BaseCLIProvider._AGY_LOCK.acquire()
        try:
            task = asyncio.create_task(self.collect(agy_session.get_session(11)))
            for _ in range(20):
                if self.live_waiters():
                    break
                await asyncio.sleep(0)
            self.assertEqual(len(self.live_waiters()), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            self.assertEqual(self.live_waiters(), [])
        finally:
            BaseCLIProvider._AGY_LOCK.release()
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())
        self.assertEqual(self.spawns, [])

    async def test_abandoned_acquire_that_already_won_releases_the_lock(self):
        lock = BaseCLIProvider._AGY_LOCK
        won = asyncio.ensure_future(lock.acquire())
        await won
        agy_session._discard_lock_acquire(won, lock)
        self.assertFalse(lock.locked())

    async def test_early_generator_close_cleans_up(self):
        session = agy_session.get_session(11)
        stream = session.stream("hello")
        self.assertEqual((await anext(stream))["type"], "text")
        await stream.aclose()
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertIsNone(agy_session.peek_session(11))
        self.assertFalse(BaseCLIProvider._AGY_LOCK.locked())

    async def test_runner_emits_native_text_usage_and_done(self):
        from agentic.agent_runner import AgentRunner
        runner = AgentRunner(provider_type="subscription", api_key="", model_name="gemini-3.8-flash",
                             conversation_id=11, workspace_path=".")
        events = [event async for event in runner._run_agy_session("new user turn")]
        self.assertEqual([e.type for e in events], ["text", "turn_usage", "response", "done"])
        self.assertEqual(events[1].data["input_tokens"], 6335)
        self.assertEqual(events[-1].data["session_id"], SESSION_ID)
        self.assertEqual(events[-1].data["stop_reason"], "complete")
        self.assertEqual(self.sent(0), "new user turn")

    async def test_runner_forwards_queued_and_started_status_to_sse(self):
        from agentic.agent_runner import AgentRunner
        runner = AgentRunner(provider_type="subscription", api_key="", model_name="gemini-3.8-flash",
                             conversation_id=11, workspace_path=".")
        await BaseCLIProvider._AGY_LOCK.acquire()
        asyncio.get_running_loop().call_later(0.01, BaseCLIProvider._AGY_LOCK.release)
        events = [event async for event in runner._run_agy_session("queued turn")]
        self.assertEqual([e.type for e in events],
                         ["status", "status", "text", "turn_usage", "response", "done"])
        self.assertEqual([e.data["code"] for e in events[:2]], ["agy_queued", "agy_started"])
        self.assertIn('"code": "agy_queued"', events[0].to_sse())

    # Handoff context: a branch copy or a provider switch reaches agy with a DB
    # transcript but no agy conversation; without it agy starts with no history.
    def runner(self, context="USER: earlier task\nASSISTANT: earlier answer", **kwargs):
        from agentic.agent_runner import AgentRunner
        kwargs.setdefault("workspace_path", ".")
        return AgentRunner(provider_type="subscription", api_key="", model_name="gemini-3.8-flash",
                           conversation_id=11, context=context, **kwargs)

    def sent(self, index, process=0):
        return json.loads(self.processes[process].stdin.lines[index])["message"]["content"]

    async def test_fresh_session_gets_handoff_context_on_first_turn_only(self):
        from agentic.agent_runner import _HANDOFF_HEADER
        runner = self.runner()
        first = [e async for e in runner._run_agy_session("new user turn")]
        second = [e async for e in runner._run_agy_session("second turn")]
        self.assertEqual([first[-1].type, second[-1].type], ["done", "done"])
        self.assertEqual(self.sent(0), f"new user turn\n\n{_HANDOFF_HEADER}\n{runner.context}")
        self.assertEqual(self.sent(0).count("new user turn"), 1)
        self.assertEqual(self.sent(1), "second turn")

    async def test_resumed_session_does_not_get_context_again(self):
        runner = self.runner(resume_id=SESSION_ID)
        events = [e async for e in runner._run_agy_session("new user turn")]
        self.assertEqual(events[-1].type, "done")
        self.assertIn(SESSION_ID, self.spawns[0][0])
        self.assertEqual(self.sent(0), "new user turn")

    async def test_session_resumed_from_store_after_close_does_not_get_context(self):
        agy_session._RESUME_IDS[(11, os.path.abspath("."))] = SESSION_ID
        events = [e async for e in self.runner()._run_agy_session("new user turn")]
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(self.sent(0), "new user turn")

    async def test_empty_context_adds_nothing(self):
        events = [e async for e in self.runner(context="")._run_agy_session("new user turn")]
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(self.sent(0), "new user turn")

    async def test_workspace_change_without_stored_conversation_injects_again(self):
        from agentic.agent_runner import _HANDOFF_HEADER
        await self.collect(agy_session.get_session(11, cwd="."))
        other = str(Path(__file__).parent)
        runner = self.runner(workspace_path=other)
        events = [e async for e in runner._run_agy_session("moved turn")]
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(self.sent(0, process=1), f"moved turn\n\n{_HANDOFF_HEADER}\n{runner.context}")

    async def test_empty_success_response_is_not_replaced_with_fallback(self):
        result = copy.deepcopy(TURNS[0][-1])
        result["result"]["response"] = ""
        self.plans = [{"turns": [[TURNS[0][0], result]]}]
        events = await self.collect()
        self.assertEqual(events[-2], {"type": "response", "content": ""})
        self.assertEqual(events[-1]["type"], "done")

    async def test_closing_runner_iterator_after_done_keeps_process_for_next_turn(self):
        from agentic.agent_runner import AgentRunner
        runner = AgentRunner(provider_type="subscription", api_key="", model_name="gemini-3.8-flash",
                             conversation_id=11, workspace_path=".")
        stream = runner._run_agy_session("hello")
        async for event in stream:
            if event.type == "done":
                break
        await stream.aclose()
        self.assertTrue(agy_session.peek_session(11).is_live)
        events = [event async for event in runner._run_agy_session("second turn")]
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(len(self.spawns), 1)

    async def test_unrelated_or_stale_results_do_not_end_current_turn(self):
        wrong = copy.deepcopy(TURNS[0][-1])
        wrong["result"]["conversation_id"] = "unrelated-conversation"
        self.plans = [{"turns": [TURNS[0], [wrong, TURNS[0][-1]] + TURNS[1]]}]
        session = agy_session.get_session(11)
        await self.collect(session)
        second = await self.collect(session)
        self.assertEqual(next(e for e in second if e["type"] == "turn_usage")["total_tokens"], 6640)

    async def test_failed_result_emits_only_error_terminal(self):
        result = copy.deepcopy(TURNS[0][-1])
        result["result"].update(status="ERROR", response="upstream failed")
        self.plans = [{"turns": [[TURNS[0][0], result]]}]
        events = await self.collect()
        self.assertEqual([e["type"] for e in events], ["error"])
        self.assertIn("upstream failed", events[0]["message"])
        self.assertIsNone(agy_session.peek_session(11))


class TestAnalyzeCodeOneShot:
    """AgyProvider.analyze_code runs one throwaway session turn and closes it.

    Callers without a conversation (analysis routes, compact summary, security
    check) used cli_base's generic one-shot spawn before the stream-json
    migration; that branch is gone, so this is the only path left for them.
    """

    def test_maps_session_events_and_closes(self, monkeypatch):
        import asyncio
        from providers import agy_session
        from providers.agy_provider import AgyProvider

        closed = []

        async def fake_stream(self, message, *, model="x", cwd=None):
            assert message == "summarize"
            assert model == "gemini-3.8-flash"
            yield {"type": "text", "content": "hel"}
            yield {"type": "text", "content": "lo"}
            yield {"type": "response", "content": "hello"}
            yield {"type": "done", "iterations": 1, "session_id": "s"}

        async def fake_close(self, *, preserve_resume=False):
            closed.append(self.conversation_id)

        monkeypatch.setattr(agy_session.AgyStreamSession, "stream", fake_stream)
        monkeypatch.setattr(agy_session.AgyStreamSession, "close", fake_close)

        async def run():
            provider = AgyProvider(binary_name="gemini-3.8-flash")
            return [ev async for ev in provider.analyze_code("summarize", cwd=".")]

        events = asyncio.run(run())
        assert [e["type"] for e in events] == ["delta", "delta", "final"]
        assert events[-1]["text"] == "hello"
        assert len(closed) == 1 and closed[0] < 0

    def test_error_event_is_forwarded_and_session_closed(self, monkeypatch):
        import asyncio
        from providers import agy_session
        from providers.agy_provider import AgyProvider

        closed = []

        async def fake_stream(self, message, *, model="x", cwd=None):
            yield {"type": "error", "message": "boom"}

        async def fake_close(self, *, preserve_resume=False):
            closed.append(True)

        monkeypatch.setattr(agy_session.AgyStreamSession, "stream", fake_stream)
        monkeypatch.setattr(agy_session.AgyStreamSession, "close", fake_close)

        async def run():
            provider = AgyProvider(binary_name="gemini-3.8-flash")
            return [ev async for ev in provider.analyze_code("x", cwd=".")]

        events = asyncio.run(run())
        assert events == [{"type": "error", "content": "boom"}]
        assert closed == [True]
