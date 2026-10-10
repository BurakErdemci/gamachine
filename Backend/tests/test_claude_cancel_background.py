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
from unittest.mock import AsyncMock, MagicMock

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
