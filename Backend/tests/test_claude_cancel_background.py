"""Stop (and "send now") while only background tasks hold the turn open.

Measured 10 Oct 2026: the agent had answered and was waiting on a background bot
run when the user pressed "send now". `cancel_turn` called `interrupt()` on a CLI
that had nothing running, no Result followed, and after the 10 s wait the session
was hard-reset. The CLI died with its background shells, and the next turn opened
on "Background shell command didn't finish before the previous session ended".
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import providers.claude_sdk_session as cs


async def _noop():
    return None


def _session(result_pending: bool):
    sess = cs.ClaudeSDKSession.__new__(cs.ClaudeSDKSession)
    sess.conversation_id = 4242
    sess._turn_active = True
    sess._result_pending = result_pending
    sess._cancel_requested = False
    sess._cancel_event = asyncio.Event()
    sess._active_gate_ids = set()
    sess._active_tasks = {"t1": "bot run"} if result_pending else {}
    sess._done_tasks = []
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


def test_stop_while_waiting_on_background_tasks_keeps_the_session():
    sess = _session(result_pending=True)
    cs._SESSIONS[sess.conversation_id] = sess
    try:
        asyncio.run(sess.cancel_turn())
        assert not sess._turn_active
        sess._client.interrupt.assert_not_awaited()
        sess.close.assert_not_awaited()
        assert cs._SESSIONS.get(sess.conversation_id) is sess
        events = _drain(sess._out_q)
        assert events.count("done") == 1 and "error" not in events, events
        assert events[-1] is None
    finally:
        cs._SESSIONS.pop(sess.conversation_id, None)


def test_stop_during_a_running_model_turn_still_interrupts():
    sess = _session(result_pending=False)

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
    sess = _session(result_pending=True)
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


def test_a_request_while_a_listener_less_turn_only_waits_on_tasks_is_not_held_open():
    """`_result_pending` with no listener: the Result already came, none is owed."""
    sess = _live_session()

    async def run():
        sess._begin_turn()
        sess._result_pending = True  # its Result came; the task has since ended unnoticed
        return await _collect(sess, "status?", [_assistant("Still here."), _result("Still here.")])

    events = asyncio.run(run())
    types = [e.get("type") for e in events]
    assert types.count("done") == 1, types
    assert next(e["content"] for e in events if e.get("type") == "response") == "Still here."


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


def test_a_nudge_result_does_not_end_a_new_user_stream():
    async def run():
        from claude_agent_sdk import TaskNotificationMessage, TaskStartedMessage

        sess = _live_session()
        first = await _start_stream(sess, "first", [])
        try:
            await sess._on_message(TaskStartedMessage("task_started", {}, "task", "worker", "t", "s1"))
            await sess._on_message(_result("first"))
            await sess._on_message(TaskNotificationMessage(
                "task_notification", {}, "task", "completed", "unused", "done", "n", "s1"))
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            sess._cancel_grace()
            await sess._grace_fire(0, "nudge")
            assert sess._client.query.await_args.args == (cs._NUDGE_MESSAGE,)
            assert sess._owed_results == 1
            events = []
            current = await _start_stream(sess, "new user request", events)
            assert sess._owed_results == 2
            await _assert_waiting(sess, current, events, _result("old nudge result"))
            await _answer(sess, current, events)
        finally:
            sess._cancel_grace()
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)

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


def test_watchdog_finish_preserves_the_still_open_cli_turns_result_debt():
    async def run():
        sess = _live_session()
        first_events = []
        first = await _start_stream(sess, "first request", first_events)
        await sess._on_message(_assistant("OLD partial"))
        await sess._grace_fire(0, "finish")
        await asyncio.wait_for(first, timeout=1)
        assert not sess._turn_active and sess._out_q is None
        assert sess._owed_results == 1
        events = []
        current = await _start_stream(sess, "new user request", events)
        await _assert_waiting(sess, current, events, _result("OLD watchdog result"))
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
        for message in ("user", "nudge"):
            for failure in (RuntimeError("offline query failed"), asyncio.CancelledError()):
                sess = _live_session()
                sess._owed_results = 1

                async def fail_query(*args):
                    assert sess._owed_results == 2
                    raise failure

                sess._client.query.side_effect = fail_query
                try:
                    if message == "user":
                        await anext(sess.stream("new query", lock_timeout=0))
                    else:
                        await sess._grace_fire(0, "nudge")
                except (RuntimeError, asyncio.CancelledError) as exc:
                    assert exc is failure
                else:
                    assert message == "nudge" and isinstance(failure, RuntimeError)
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
