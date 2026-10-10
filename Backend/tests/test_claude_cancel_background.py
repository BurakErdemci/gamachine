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
