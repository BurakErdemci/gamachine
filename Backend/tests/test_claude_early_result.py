"""A Result that belongs to an earlier CLI turn (a background-task notification
turn pending when the user sent, measured 7 Nov 2026) arrives before the CLI
echoes the user's query. It must not end the user's stream empty: the reply that
follows the echo belongs to the user. Every client here is a fake."""
import asyncio
from unittest.mock import patch

import pytest

import providers.claude_sdk_session as cs
from agentic import turn_events, wake_queue


@pytest.fixture(autouse=True)
def _clean_state():
    wake_queue.reset_all()
    turn_events.RING.reset()
    yield
    wake_queue.reset_all()
    turn_events.RING.reset()


class _FakeClient:
    def __init__(self):
        self.queries = []

    async def query(self, text, *a, **k):
        self.queries.append(text)

    async def interrupt(self):
        return None

    async def get_context_usage(self):
        raise RuntimeError("no context reading in tests")

    async def __aexit__(self, *a):
        return None


def _session(cid=6161):
    sess = cs.ClaudeSDKSession(conversation_id=cid)
    sess._started = True
    sess._client = _FakeClient()
    return sess


def _assistant(text):
    from claude_agent_sdk import AssistantMessage, TextBlock
    return AssistantMessage(content=[TextBlock(text)], model="claude-opus-5")


def _echo(text):
    from claude_agent_sdk import UserMessage
    return UserMessage(content=text, parent_tool_use_id=None)


def _result(text=""):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id="s1", result=text)


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def _open_stream(sess, message):
    events = []

    async def consume():
        async for ev in sess.stream(message):
            if not ev.get("heartbeat"):
                events.append(ev)

    task = asyncio.create_task(consume())
    await _settle()
    return task, events


def test_result_before_the_echo_is_an_earlier_turns_and_the_reply_stays_on_the_stream():
    async def main():
        saved = []
        sess = _session()
        with patch.object(cs, "_DB_SAVE_CB", lambda *a: saved.append(a)):
            task, events = await _open_stream(sess, "do the thing")
            await sess._on_message(_result())  # the earlier turn's Result, no echo yet
            await _settle()
            assert not task.done()
            assert sess._turn_active
            await sess._on_message(_echo("do the thing"))
            await sess._on_message(_assistant("the answer"))
            await sess._on_message(_result("the answer"))
            await asyncio.wait_for(task, 2)
        assert any(e.get("type") == "response" and e.get("content") == "the answer" for e in events)
        assert events[-1]["type"] == "done"
        assert saved == []
    asyncio.run(main())


def test_slash_command_keeps_ending_on_a_result_without_an_echo():
    async def main():
        sess = _session()
        task, events = await _open_stream(sess, "/compact")
        await sess._on_message(_result("compacted"))
        await asyncio.wait_for(task, 2)
        assert events[-1]["type"] == "done"
        assert sess._owed_results == 0
    asyncio.run(main())


def test_a_missing_echo_still_ends_through_the_idle_fallback():
    async def main():
        sess = _session()
        real_wait_for = asyncio.wait_for

        async def short_heartbeat(awaitable, timeout):
            return await real_wait_for(awaitable, 0.01 if timeout == 20.0 else timeout)

        with patch.object(cs, "_OWED_RESULT_IDLE_S", 0.001), patch.object(asyncio, "wait_for", short_heartbeat):
            task, events = await _open_stream(sess, "no echo ever")
            await sess._on_message(_result())
            await _settle()
            assert not task.done()
            sess._last_cli_msg_at = cs.time.time() - 121
            await real_wait_for(task, 2)
        assert events[-1]["type"] == "done"
        assert sess._owed_results == 0
    asyncio.run(main())
