"""Background tasks never hold a Claude turn open (owner decision, 4 Nov 2026).

The model answers, the turn ends, the composer is free; a running task is data
(`bg_tasks`), not a turn. When the last task ends with no turn running, the
CLI's own unprompted turn is the continuation (a background shell, measured
4 Nov 2026); if none comes within the grace (a subagent), ONE wake notice is
left for the renderer. Stop with no model turn running is a no-op for the
session: the hard reset it used to reach killed the background shells
(measured 10 Oct 2026, "Background shell command didn't finish before the
previous session ended").
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import providers.claude_sdk_session as cs
from agentic import turn_events, wake_queue


@pytest.fixture(autouse=True)
def _clean_state():
    wake_queue.reset_all()
    turn_events.RING.reset()
    yield
    wake_queue.reset_all()
    turn_events.RING.reset()


async def _noop():
    return None


def _session(turn_active: bool, tasks: bool):
    sess = cs.ClaudeSDKSession.__new__(cs.ClaudeSDKSession)
    sess.conversation_id = 4242
    sess._turn_active = turn_active
    sess._cancel_requested = False
    sess._cancel_event = asyncio.Event()
    sess._active_gate_ids = set()
    sess._active_tasks = {"t1": "bot run"} if tasks else {}
    sess._task_meta = {}
    sess._done_tasks = []
    sess._stop_killed = []
    sess._drain_state = None
    sess._agent_tap = None
    sess._cancel_at = 0.0
    sess._out_q = asyncio.Queue()
    sess._usage_event = None
    sess._final_text = "Waiting for the bot run."
    sess._last_result_text = ""
    sess.session_id = "s1"
    sess._grace_task = None
    sess._flush_deltas = _noop
    sess._client = MagicMock()
    sess._client.interrupt = AsyncMock()
    sess.close = AsyncMock()
    return sess


def _drain(q: asyncio.Queue) -> list:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        out.append(None if ev is None else ev.get("type"))
    return out


def test_stop_with_no_model_turn_while_tasks_run_is_a_noop():
    sess = _session(turn_active=False, tasks=True)
    cs._SESSIONS[sess.conversation_id] = sess
    try:
        asyncio.run(sess.cancel_turn())
        sess._client.interrupt.assert_not_awaited()
        sess.close.assert_not_awaited()
        assert cs._SESSIONS.get(sess.conversation_id) is sess
        assert sess._active_tasks == {"t1": "bot run"}
        assert not sess._cancel_requested
        assert _drain(sess._out_q) == []
    finally:
        cs._SESSIONS.pop(sess.conversation_id, None)


def test_stop_during_a_running_model_turn_still_interrupts():
    sess = _session(turn_active=True, tasks=False)

    async def _interrupt():
        sess._turn_active = False  # the CLI answers the interrupt with a Result

    sess._client.interrupt = AsyncMock(side_effect=_interrupt)
    cs._SESSIONS[sess.conversation_id] = sess
    try:
        asyncio.run(sess.cancel_turn())
        sess._client.interrupt.assert_awaited_once()
        sess.close.assert_not_awaited()
    finally:
        cs._SESSIONS.pop(sess.conversation_id, None)


def test_the_next_turn_starts_without_the_stop_flag():
    """A later task notification must not be swallowed as 'user stopped it'."""
    sess = _session(turn_active=True, tasks=True)

    async def _interrupt():
        sess._turn_active = False

    sess._client.interrupt = AsyncMock(side_effect=_interrupt)
    asyncio.run(sess.cancel_turn())
    assert sess._cancel_requested
    sess._begin_turn()
    assert not sess._cancel_requested


# ── A new request while an autonomous turn runs (audit of 35057cf) ────────────
# After such a Stop the CLI may continue on its own when a background task ends.
# A request sent during that continuation used to be closed by the continuation's
# Result, with the old answer shown as the new one and the real answer lost.
def _live_session():
    sess = cs.ClaudeSDKSession(conversation_id=4343)
    sess._started = True
    sess._client = MagicMock()
    sess._client.query = AsyncMock()
    return sess


def _assistant(text):
    from claude_agent_sdk import AssistantMessage, TextBlock
    return AssistantMessage(content=[TextBlock(text)], model="claude-opus-5")


def _result(text):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id="s1", result=text)


async def _collect(sess, message, feed):
    events = []

    async def consume():
        async for ev in sess.stream(message):
            events.append(ev)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    for msg in feed:
        await sess._on_message(msg)
        await asyncio.sleep(0)
    await asyncio.wait_for(task, timeout=5)
    return events


def test_a_request_during_an_autonomous_turn_waits_for_its_own_answer():
    sess = _live_session()

    async def run():
        await sess._on_message(_assistant("Bot runs finished, all green."))  # autonomous, nobody listening
        assert sess._turn_active and sess._out_q is None
        return await _collect(sess, "what's the status?", [
            _result("Bot runs finished, all green."),  # the autonomous turn's Result
            _assistant("Status: done."),
            _result("Status: done."),
        ])

    events = asyncio.run(run())
    types = [e.get("type") for e in events]
    assert types.count("done") == 1 and "error" not in types, types
    response = next(e["content"] for e in events if e.get("type") == "response")
    assert "Status: done." in response, response
    assert "Bot runs finished" in response, response


def test_a_request_after_a_finished_autonomous_turn_is_not_held_open():
    """The counter-variant: no CLI turn is open, so the first Result is this request's."""
    sess = _live_session()

    async def run():
        await sess._on_message(_assistant("Bot runs finished."))
        await sess._on_message(_result("Bot runs finished."))  # autonomous turn closed
        return await _collect(sess, "status?", [_assistant("Done."), _result("Done.")])

    events = asyncio.run(run())
    types = [e.get("type") for e in events]
    assert types.count("done") == 1, types
    assert next(e["content"] for e in events if e.get("type") == "response") == "Done."


async def _start_stream(sess, message, events):
    async def consume():
        async for ev in sess.stream(message, lock_timeout=0):
            events.append(ev)

    queries = sess._client.query.await_count
    task = asyncio.create_task(consume())
    for _ in range(200):
        if sess._client.query.await_count > queries:
            return task
        await asyncio.sleep(0.001)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    raise AssertionError("The offline client never received the query")


async def _assert_waiting(sess, task, events, old_result):
    await sess._on_message(old_result)
    await asyncio.sleep(0.01)
    assert not task.done(), events
    assert not any(ev["type"] in ("response", "done", "error") for ev in events), events
    assert sess._owed_results == 1


async def _answer(sess, task, events, text="OWN answer"):
    await sess._on_message(_assistant(text))
    await sess._on_message(_result(text))
    await asyncio.wait_for(task, timeout=1)
    assert [ev["type"] for ev in events if ev["type"] in ("done", "error")] == ["done"]
    assert text in next(ev["content"] for ev in events if ev["type"] == "response")
    assert sess._owed_results == 0


def test_a_result_with_tasks_running_ends_the_turn():
    async def run():
        from claude_agent_sdk import TaskStartedMessage

        sess = _live_session()
        events = []
        current = await _start_stream(sess, "start the bot run", events)
        await sess._on_message(TaskStartedMessage("task_started", {}, "t", "bot run", "u", "s1",
                                                  task_type="local_bash"))
        await _answer(sess, current, events, "Started it in the background.")
        assert not sess._turn_active and sess._active_tasks == {"t": "bot run"}
        assert not hasattr(sess, "_result_pending")
        assert [t["kind"] for t in sess.bg_tasks()] == ["bash"]

    asyncio.run(run())


def test_a_user_message_while_tasks_run_is_a_normal_turn():
    async def run():
        sess = _live_session()
        sess._active_tasks = {"t": "subagent"}
        sess._client.interrupt = AsyncMock()
        sess.close = AsyncMock()
        events = []
        current = await _start_stream(sess, "meanwhile, a question", events)
        await _answer(sess, current, events, "An answer.")
        assert sess._client.query.await_args.args == ("meanwhile, a question",)
        sess._client.interrupt.assert_not_awaited()
        sess.close.assert_not_awaited()
        assert sess._active_tasks == {"t": "subagent"}

    asyncio.run(run())


def test_parent_stream_events_establish_an_autonomous_result_debt():
    async def run():
        from claude_agent_sdk import StreamEvent

        sess = _live_session()
        await sess._on_message(StreamEvent("m", "s1", {"type": "message_start"}))
        await sess._on_message(StreamEvent("d", "s1", {
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": "OLD visible delta"}}))
        assert sess._owed_results == 1
        events = []
        current = await _start_stream(sess, "new user request", events)
        await sess._on_message(_assistant("OLD visible delta"))
        assert sess._owed_results == 2
        await _assert_waiting(sess, current, events, _result("OLD visible delta"))
        await _answer(sess, current, events)
        assert "OLD visible delta" in next(ev["content"] for ev in events if ev["type"] == "response")

    asyncio.run(run())


def test_an_earlier_error_result_is_content_without_ending_the_new_stream():
    async def run():
        from claude_agent_sdk import ResultMessage

        sess = _live_session()
        await sess._on_message(_assistant("OLD autonomous partial"))
        events = []
        current = await _start_stream(sess, "new user request", events)
        assert events[0] == {"type": "text", "content": "OLD autonomous partial"}
        error = ResultMessage("error_during_execution", 1, 1, True, 1, "s1",
                              result="OLD failed", errors=["offline old-turn failure"])
        await _assert_waiting(sess, current, events, error)
        assert any(ev["type"] == "text" and "OLD failed" in ev["content"] for ev in events)
        await _answer(sess, current, events)
        response = next(ev["content"] for ev in events if ev["type"] == "response")
        assert "OLD autonomous partial" in response and "OLD failed" in response

    asyncio.run(run())


def test_stop_during_takeover_preserves_the_queued_requests_result_debt():
    async def run():
        sess = _live_session()
        await sess._on_message(_assistant("OLD autonomous"))
        stopped_events = []
        stopped = await _start_stream(sess, "queued request before Stop", stopped_events)

        async def interrupt():
            await sess._on_message(_result("OLD interrupted"))

        sess._client.interrupt = AsyncMock(side_effect=interrupt)
        sess.close = AsyncMock()
        await sess.cancel_turn()
        await asyncio.wait_for(stopped, timeout=1)
        sess.close.assert_not_awaited()
        assert sess._owed_results == 1
        assert [ev["type"] for ev in stopped_events if ev["type"] in ("done", "error")] == ["done"]
        events = []
        current = await _start_stream(sess, "latest user request", events)
        await sess._on_message(_assistant("STOPPED queued answer"))
        await _assert_waiting(sess, current, events, _result("STOPPED queued answer"))
        await _answer(sess, current, events, "LATEST own answer")

    asyncio.run(run())


def test_a_stream_that_went_away_preserves_the_still_open_cli_turns_result_debt():
    async def run():
        sess = _live_session()
        first_events = []
        first = await _start_stream(sess, "first request", first_events)
        await sess._on_message(_assistant("OLD partial"))
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert sess._turn_active and sess._out_q is None and sess.unprompted_turn_active
        assert sess._owed_results == 1
        events = []
        current = await _start_stream(sess, "new user request", events)
        await _assert_waiting(sess, current, events, _result("OLD result"))
        await _answer(sess, current, events)

    asyncio.run(run())


def test_an_ordinary_idle_stream_waits_for_its_own_result():
    async def run():
        sess = _live_session()
        assert sess._absorbed_result_at == 0.0
        sess._absorbed_result_at = cs.time.time() - 1  # An earlier stream's marker must reset.
        real_wait_for = asyncio.wait_for

        async def short_heartbeat(awaitable, timeout):
            return await real_wait_for(awaitable, 0.01 if timeout == 20.0 else timeout)

        with patch.object(cs, "_OWED_RESULT_IDLE_S", 0.001), patch.object(asyncio, "wait_for", short_heartbeat):
            stream = sess.stream("ordinary request", lock_timeout=0)
            try:
                sess._last_cli_msg_at = cs.time.time() - 121
                heartbeat = await real_wait_for(anext(stream), timeout=1)
                assert heartbeat["type"] == "status" and heartbeat["heartbeat"]
                assert sess._turn_active and sess._owed_results == 1
                assert sess._absorbed_result_at == 0.0
                await sess._on_message(_assistant("OWN answer"))
                await sess._on_message(_result("OWN answer"))
                events = [ev async for ev in stream]
                assert [ev["type"] for ev in events if ev["type"] in ("done", "error")] == ["done"]
                assert next(ev["content"] for ev in events if ev["type"] == "response") == "OWN answer"
            finally:
                await stream.aclose()

    asyncio.run(run())


def test_owed_result_idle_safety_valve_requires_absorption_and_no_backoff_or_tasks():
    async def run():
        sess = _live_session()
        real_wait_for = asyncio.wait_for

        async def short_heartbeat(awaitable, timeout):
            return await real_wait_for(awaitable, 0.01 if timeout == 20.0 else timeout)

        async def absorb_earlier_result(message):
            assert sess._owed_results == 2
            await sess._on_message(_result("OLD result"))
            assert sess._absorbed_result_at == 1000.0
            sess._last_cli_msg_at = 999.0

        sess._client.query.side_effect = absorb_earlier_result
        with patch.object(cs.time, "time", return_value=1000.0), \
                patch.object(cs, "_OWED_RESULT_IDLE_S", 0.001), \
                patch.object(asyncio, "wait_for", short_heartbeat):
            await sess._on_message(_assistant("OLD autonomous answer"))
            stream = sess.stream("merged queued prompt", lock_timeout=0)
            try:
                carried = await real_wait_for(anext(stream), timeout=1)
                assert carried == {"type": "text", "content": "OLD autonomous answer"}
                heartbeat = await real_wait_for(anext(stream), timeout=1)
                assert heartbeat["type"] == "status" and heartbeat["heartbeat"]
                assert sess._turn_active and sess._owed_results == 1
                sess._absorbed_result_at = 999.0
                sess._last_cli_msg_at = 1000.0
                heartbeat = await real_wait_for(anext(stream), timeout=1)
                assert heartbeat["heartbeat"] and sess._turn_active
                sess._last_cli_msg_at = 999.0
                sess._active_tasks = {"task": "background worker"}
                heartbeat = await real_wait_for(anext(stream), timeout=1)
                assert heartbeat["heartbeat"] and sess._turn_active
                sess._active_tasks.clear()
                sess._rate_limit = {"status": "rejected"}
                heartbeat = await real_wait_for(anext(stream), timeout=1)
                assert heartbeat["heartbeat"] and sess._turn_active
                assert sess._owed_results == 1
                sess._rate_limit = None

                async def collect_remaining():
                    return [ev async for ev in stream]

                events = await real_wait_for(collect_remaining(), timeout=1)
                assert not sess._turn_active and sess._owed_results == 0
                assert [ev["type"] for ev in events if ev["type"] in ("done", "error")] == ["done"]
            finally:
                await stream.aclose()

    asyncio.run(run())


def test_query_failures_roll_back_only_the_new_result_debt():
    async def run():
        for message in ("user",):
            for failure in (RuntimeError("offline query failed"), asyncio.CancelledError()):
                sess = _live_session()
                sess._owed_results = 1

                async def fail_query(*args):
                    assert sess._owed_results == 2
                    raise failure

                sess._client.query.side_effect = fail_query
                with pytest.raises((RuntimeError, asyncio.CancelledError)) as exc:
                    await anext(sess.stream("new query", lock_timeout=0))
                assert exc.value is failure
                assert sess._owed_results == 1
                assert not sess._turn_lock.locked() and sess._out_q is None

    asyncio.run(run())


def test_subagent_output_does_not_open_or_close_an_owed_cli_turn():
    async def run():
        from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock

        sess = _live_session()
        for owed in (0, 2):
            sess._owed_results = owed
            await sess._on_message(AssistantMessage([TextBlock("child text")], "offline",
                                                    parent_tool_use_id="child"))
            await sess._on_message(StreamEvent("m", "s1", {"type": "message_start"},
                                               parent_tool_use_id="child"))
            result = _result("child result")
            result.parent_tool_use_id = "child"
            await sess._on_message(result)
            assert sess._owed_results == owed
            assert not sess._turn_active and not sess._final_text

    asyncio.run(run())


def test_closing_the_session_clears_all_owed_results():
    async def run():
        sess = _live_session()
        sess._client.__aexit__ = AsyncMock()
        sess._owed_results = 3
        await sess.close()
        assert sess._owed_results == 0 and sess._client is None

    asyncio.run(run())


# ── Wake exactly once when background tasks finish ───────────────────────────
def _started(task_id="t", desc="worker", task_type="local_agent"):
    from claude_agent_sdk import TaskStartedMessage
    return TaskStartedMessage("task_started", {}, task_id, desc, "u", "s1", task_type=task_type)


def _notified(task_id="t", status="completed"):
    from claude_agent_sdk import TaskNotificationMessage
    return TaskNotificationMessage("task_notification", {}, task_id, status, "out", "done", "n", "s1")


def _stream_event(event):
    from claude_agent_sdk import StreamEvent
    return StreamEvent("e", "s1", event)


GRACE = 0.05


def test_an_unprompted_cli_turn_is_recorded_and_not_followed_by_a_wake():
    async def run():
        sess = _live_session()
        saver = MagicMock()
        with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE), patch.object(cs, "_DB_SAVE_CB", saver):
            await sess._on_message(_started("b", "npm run build", "local_bash"))
            await sess._on_message(_notified("b"))
            assert sess._drain_state == "grace"
            # ~50 ms later the CLI answers the task result by itself (case A).
            await sess._on_message(_stream_event({"type": "message_start"}))
            assert sess.unprompted_turn_active
            await sess._on_message(_stream_event({
                "type": "content_block_delta", "delta": {"type": "text_delta", "text": "Build is green."}}))
            await sess._on_message(_assistant("Build is green."))
            await sess._on_message(_result("Build is green."))
            await asyncio.sleep(GRACE * 4)
        assert not sess._turn_active and sess._owed_results == 0
        assert wake_queue.pending(sess.conversation_id) == 0
        saver.assert_called_once()
        assert saver.call_args.args[1] == "Build is green."
        turn_events.RING.flush(sess.conversation_id)
        events = turn_events.RING.since(sess.conversation_id)["events"]
        kinds = [e["kind"] for e in events]
        assert kinds[0] == "turn_start" and kinds[-1] == "turn_end", kinds
        assert events[0]["origin"] == "agent"
        assert events[-1]["status"] == "done"
        assert "Build is green." in "".join(e.get("content", "") for e in events if e["kind"] == "text")

    asyncio.run(run())


def test_a_subagent_end_with_no_unprompted_turn_wakes_exactly_once():
    async def run():
        sess = _live_session()
        with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE):
            await sess._on_message(_started("a", "audit the scene"))
            await sess._on_message(_notified("a"))
            await asyncio.sleep(GRACE / 5)
            assert wake_queue.pending(sess.conversation_id) == 0  # still in the grace
            await asyncio.sleep(GRACE * 4)
            assert wake_queue.drain(sess.conversation_id) == ["tasks_done|audit the scene"]
            await asyncio.sleep(GRACE * 4)
            assert wake_queue.pending(sess.conversation_id) == 0
            assert sess._drain_state is None and sess._done_tasks == []

    asyncio.run(run())


def test_tasks_ending_during_a_user_turn_apply_the_rule_once_after_it():
    async def run():
        sess = _live_session()
        with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE):
            await sess._on_message(_started("a", "audit"))
            events = []
            current = await _start_stream(sess, "meanwhile", events)
            await sess._on_message(_notified("a"))
            assert sess._drain_state == "after_turn"
            await asyncio.sleep(GRACE * 3)
            assert wake_queue.pending(sess.conversation_id) == 0  # the turn sees it in-session
            await _answer(sess, current, events)
            assert sess._drain_state == "grace"
            await asyncio.sleep(GRACE * 4)
            assert wake_queue.drain(sess.conversation_id) == ["tasks_done|audit"]

    asyncio.run(run())


def test_a_user_turn_during_the_grace_defers_the_rule_to_its_end():
    async def run():
        sess = _live_session()
        with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE):
            await sess._on_message(_started("a", "audit"))
            await sess._on_message(_notified("a"))
            events = []
            current = await _start_stream(sess, "hi", events)
            assert sess._drain_state == "after_turn"
            await asyncio.sleep(GRACE * 3)
            assert wake_queue.pending(sess.conversation_id) == 0
            await _answer(sess, current, events)
            await asyncio.sleep(GRACE * 4)
            assert wake_queue.drain(sess.conversation_id) == ["tasks_done|audit"]

    asyncio.run(run())


def test_a_stop_that_kills_tasks_tells_the_next_turn_and_wakes_nothing():
    async def run():
        from claude_agent_sdk import TaskUpdatedMessage

        sess = _live_session()
        sess.close = AsyncMock()
        with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE):
            await sess._on_message(_started("a", "audit"))
            await sess._on_message(_started("b", "dev server", "local_bash"))
            events = []
            current = await _start_stream(sess, "go", events)

            async def interrupt():
                await sess._on_message(_notified("a", "stopped"))
                await sess._on_message(TaskUpdatedMessage("task_updated", {}, "b", {"status": "killed"},
                                                          status="killed"))
                await sess._on_message(_result("stopped"))

            sess._client.interrupt = AsyncMock(side_effect=interrupt)
            await sess.cancel_turn()
            await asyncio.wait_for(current, timeout=1)
            sess.close.assert_not_awaited()
            await asyncio.sleep(GRACE * 4)
        assert not sess._active_tasks
        assert wake_queue.pending(sess.conversation_id) == 0
        notice = sess.take_stop_notice()
        assert notice == "[Sistem] Durdurma şu arka plan işlerini kesti: audit, dev server. Gerekirse yeniden başlat."
        assert sess.take_stop_notice() == ""

    asyncio.run(run())


def test_every_task_start_and_end_is_published():
    async def run():
        sess = _live_session()
        seen = []

        def listener(cid, tasks):
            seen.append((cid, [t["desc"] for t in tasks]))

        cs.add_bg_tasks_listener(listener)
        try:
            with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE):
                await sess._on_message(_started("a", "audit"))
                await sess._on_message(_started("b", "build", "local_bash"))
                await sess._on_message(_notified("a"))
                await sess._on_message(_notified("b"))
                sess._cancel_grace()
        finally:
            cs.remove_bg_tasks_listener(listener)
        cid = sess.conversation_id
        assert seen == [(cid, ["audit"]), (cid, ["audit", "build"]), (cid, ["build"]), (cid, [])]
        assert sess.bg_tasks_version == 4

    asyncio.run(run())


# ── Audit fix round (4 Nov 2026) ────────────────────────────────────────────
def _updated(task_id="t", status="completed"):
    from claude_agent_sdk import TaskUpdatedMessage
    return TaskUpdatedMessage("task_updated", {}, task_id, {"status": status}, status=status)


def test_the_two_end_events_of_one_task_count_once():
    """The CLI sends TaskUpdated(terminal) AND TaskNotification for one task."""
    async def run():
        sess = _live_session()
        with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE):
            await sess._on_message(_started("a", "audit the scene"))
            await sess._on_message(_updated("a"))
            await sess._on_message(_notified("a"))
            await asyncio.sleep(GRACE * 4)
        return wake_queue.drain(sess.conversation_id)

    assert asyncio.run(run()) == ["tasks_done|audit the scene"]


def test_a_late_second_end_event_after_the_unprompted_turn_wakes_nothing():
    async def run():
        sess = _live_session()
        with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE), patch.object(cs, "_DB_SAVE_CB", MagicMock()):
            await sess._on_message(_started("b", "npm run build", "local_bash"))
            await sess._on_message(_updated("b"))
            await sess._on_message(_stream_event({"type": "message_start"}))
            await sess._on_message(_notified("b"))
            await sess._on_message(_assistant("Build is green."))
            await sess._on_message(_result("Build is green."))
            await asyncio.sleep(GRACE * 4)
        return wake_queue.drain(sess.conversation_id), sess._done_tasks

    assert asyncio.run(run()) == ([], [])


async def _stopped_turn(sess):
    """Run a turn and Stop it; the interrupt ends it with a Result."""
    current = await _start_stream(sess, "go", [])

    async def interrupt():
        await sess._on_message(_result("stopped"))

    sess._client.interrupt = AsyncMock(side_effect=interrupt)
    await sess.cancel_turn()
    sess.close.assert_not_awaited()
    await asyncio.wait_for(current, timeout=1)


def test_a_task_started_after_a_stop_fails_on_its_own():
    async def run():
        sess = _live_session()
        sess.close = AsyncMock()
        with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE):
            await _stopped_turn(sess)
            await sess._on_message(_started("b", "tests", "local_bash"))
            await sess._on_message(_notified("b", "failed"))
            await asyncio.sleep(GRACE * 4)
        return wake_queue.drain(sess.conversation_id), sess.take_stop_notice()

    assert asyncio.run(run()) == (["tasks_done|tests"], "")


def test_a_new_turn_ends_the_previous_stops_window():
    async def run():
        sess = _live_session()
        sess.close = AsyncMock()
        with patch.object(cs, "_TASKS_DONE_GRACE_S", GRACE):
            await sess._on_message(_started("b", "tests", "local_bash"))
            await _stopped_turn(sess)
            current = await _start_stream(sess, "next", [])
            await sess._on_message(_result("ok"))
            await asyncio.wait_for(current, timeout=1)
            await sess._on_message(_notified("b", "failed"))
            await asyncio.sleep(GRACE * 4)
        return wake_queue.drain(sess.conversation_id), sess.take_stop_notice()

    assert asyncio.run(run()) == (["tasks_done|tests"], "")
