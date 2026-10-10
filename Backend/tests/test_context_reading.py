"""CLI context readings must survive turn boundaries without blocking readers."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import providers.claude_sdk_session as claude
import providers.codex_session as codex
from routes.conversation_routes import _live_context_reading


def _claude_session():
    session = claude.ClaudeSDKSession(7001)
    session._started = True
    session._client = MagicMock()
    session._client.query = AsyncMock()
    return session


async def _finish_stream(session):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    events = []
    queried = asyncio.Event()

    async def query(message):
        queried.set()

    session._client.query.side_effect = query

    async def consume():
        async for event in session.stream("hello"):
            events.append(event)

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(queried.wait(), 1)
        await session._on_message(AssistantMessage(content=[TextBlock("answer")], model="m"))
        session._client.get_context_usage.assert_not_awaited()
        await session._on_message(ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", result="answer",
        ))
        session._client.get_context_usage.assert_not_awaited()
        await asyncio.wait_for(task, 1)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    return events


@pytest.mark.parametrize("raw_window, expected_window", [(200_000, 200_000), (None, 180_000)])
def test_claude_reads_after_the_reader_finishes_and_before_unlock(raw_window, expected_window):
    session = _claude_session()
    assert session.context_reading is None

    async def read():
        assert not session._turn_active
        assert session._out_q.empty()
        assert session._turn_lock.locked()
        return {
            "totalTokens": 112_400, "maxTokens": 180_000, "rawMaxTokens": raw_window,
            "percentage": 56.2, "model": "claude-test",
        }

    session._client.get_context_usage = AsyncMock(side_effect=read)
    events = asyncio.run(_finish_stream(session))
    assert any(event["type"] == "done" for event in events)
    assert session.context_reading == {
        "used": 112_400, "window": expected_window, "percent": 56.2, "model": "claude-test",
    }
    session._client.get_context_usage.assert_awaited_once()
    assert not session._turn_lock.locked()


@pytest.mark.parametrize("failure", ["exception", "timeout"])
def test_claude_failed_reading_preserves_previous_value_and_ends_stream(monkeypatch, failure):
    session = _claude_session()
    previous = {"used": 100, "window": 200_000, "percent": 0.05, "model": "m"}
    session.context_reading = previous
    wait_for = asyncio.wait_for

    async def short_timeout(awaitable, timeout):
        return await wait_for(awaitable, 0.01 if timeout == 5.0 else timeout)

    async def read():
        if failure == "exception":
            raise RuntimeError("context unavailable")
        await asyncio.Event().wait()

    monkeypatch.setattr(claude.asyncio, "wait_for", short_timeout)
    session._client.get_context_usage = AsyncMock(side_effect=read)
    events = asyncio.run(_finish_stream(session))
    assert any(event["type"] == "done" for event in events)
    assert session.context_reading is previous
    session._client.get_context_usage.assert_awaited_once()
    assert not session._turn_lock.locked()


def test_claude_refreshes_best_effort_when_query_raises():
    session = _claude_session()
    session._client.query.side_effect = RuntimeError("query failed")
    session._client.get_context_usage = AsyncMock(return_value={
        "totalTokens": 10, "maxTokens": 100, "percentage": 10, "model": None,
    })

    async def run():
        with pytest.raises(RuntimeError, match="query failed"):
            async for _ in session.stream("hello"):
                pass

    asyncio.run(run())
    assert session.context_reading == {"used": 10, "window": 100, "percent": 10.0, "model": ""}
    assert not session._turn_lock.locked()


def _notification(usage, turn="old-turn"):
    return {"method": "thread/tokenUsage/updated", "params": {
        "threadId": "thread", "turnId": turn, "tokenUsage": usage,
    }}


@pytest.mark.parametrize("active_queue", [True, False])
def test_codex_reading_bypasses_stale_turn_filters_without_emitting(active_queue):
    session = codex.CodexSession(7002)
    assert session.context_reading is None
    session._current_turn_id = "new-turn"
    session._retired_turn_ids.append("old-turn")
    queue = asyncio.Queue()
    session._out_q = queue if active_queue else None
    asyncio.run(session._handle_notification(_notification({
        "modelContextWindow": 272_000, "total": {"totalTokens": 999_999},
        "last": {"totalTokens": 51_000},
    })))
    assert session.context_reading == {
        "used": 51_000, "window": 272_000, "percent": 100 * 51_000 / 272_000, "model": "",
    }
    assert queue.empty()


@pytest.mark.parametrize("usage", [
    None, [], {}, {"modelContextWindow": None, "last": {"totalTokens": 1}},
    {"modelContextWindow": 0, "last": {"totalTokens": 1}},
    {"modelContextWindow": True, "last": {"totalTokens": 1}},
    {"modelContextWindow": "200000", "last": {"totalTokens": 1}},
    {"modelContextWindow": 200_000, "last": None},
    {"modelContextWindow": 200_000, "last": {"totalTokens": "1"}},
    {"modelContextWindow": 200_000, "last": {"totalTokens": True}},
])
def test_codex_malformed_reading_is_ignored(usage):
    session = codex.CodexSession(7002)
    session._out_q = asyncio.Queue()
    asyncio.run(session._handle_notification(_notification(usage)))
    assert session.context_reading is None
    assert session._out_q.empty()


@pytest.mark.parametrize("provider", [claude, codex])
def test_live_lookup_uses_existing_live_sessions_only(monkeypatch, provider):
    monkeypatch.setattr(claude, "_SESSIONS", {})
    monkeypatch.setattr(codex, "_SESSIONS", {})
    assert _live_context_reading(7003) is None
    assert not claude._SESSIONS and not codex._SESSIONS
    session = provider.ClaudeSDKSession(7003) if provider is claude else provider.CodexSession(7003)
    session.context_reading = {"used": 10, "window": 100, "percent": 10, "model": ""}
    provider._SESSIONS[7003] = session
    assert _live_context_reading(7003) is None
    session._started = True
    assert _live_context_reading(7003) is session.context_reading


def test_live_lookup_never_raises(monkeypatch):
    monkeypatch.setattr(claude, "peek_session", MagicMock(side_effect=RuntimeError("unavailable")))
    monkeypatch.setattr(codex, "peek_session", MagicMock(side_effect=RuntimeError("unavailable")))
    assert _live_context_reading(7003) is None
