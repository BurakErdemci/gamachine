"""The last running turn ends live presence, including late runner cleanup."""
import asyncio
from contextlib import ExitStack

import pytest

from agentic import approval_policy
from computer_use import activity
from routes.conversation_routes import _tag_sse_stream


@pytest.fixture(autouse=True)
def clean_activity(monkeypatch):
    activity.reset()
    monkeypatch.setattr(activity.time, "time", lambda: 102.0)
    yield
    activity.reset()


@pytest.mark.parametrize("close_early", [False, True])
def test_nested_stream_ends_activity_when_runner_exits(close_early):
    async def runner():
        with approval_policy.ambient_turn(".", "auto", 7, 0):
            activity.record(7, "computer_click", {"pid": 42}, "allow", now=100.0)
            yield 'data: {"type": "text"}\n\n'
            yield 'data: {"type": "done"}\n\n'

    async def event_generator():
        async for frame in runner():
            yield frame

    async def consume():
        stream = _tag_sse_stream(event_generator(), 7)
        if close_early:
            assert await anext(stream) == 'data: {"type": "text", "conversation_id": 7}\n\n'
            assert approval_policy.conversation_turn_in_flight(7)
            assert activity.snapshot()["active"] is True
            await stream.aclose()
            for _ in range(10):
                await asyncio.sleep(0)
        else:
            assert [frame async for frame in stream] == [
                'data: {"type": "text", "conversation_id": 7}\n\n',
                'data: {"type": "done", "conversation_id": 7}\n\n',
            ]
        assert not approval_policy.conversation_turn_in_flight(7)
        snap = activity.snapshot()
        assert snap["active"] is False and snap["ended"] is True

    asyncio.run(consume())


@pytest.mark.parametrize("fails", [False, True])
def test_end_failure_is_logged_without_masking_turn_exit(monkeypatch, caplog, fails):
    def broken_end(conversation_id):
        assert conversation_id == 7
        raise RuntimeError("activity unavailable")

    monkeypatch.setattr(activity, "end", broken_end)

    def run_turn():
        with approval_policy.ambient_turn(".", "auto", 7, 0):
            if fails:
                raise ValueError("original turn error")

    if fails:
        with pytest.raises(ValueError, match="original turn error"):
            run_turn()
    else:
        run_turn()
    assert not approval_policy.conversation_turn_in_flight(7)
    assert "activity unavailable" in caplog.text
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize("close_early", [False, True])
def test_turnless_stream_keeps_activity_until_running_turn_exits(close_early):
    async def frames():
        yield "data: {}\n\n"
        yield "data: {}\n\n"

    async def consume():
        stream = _tag_sse_stream(frames(), 7)
        if close_early:
            await anext(stream)
            await stream.aclose()
        else:
            assert len([frame async for frame in stream]) == 2

    with approval_policy.ambient_turn(".", "auto", 7, 0):
        activity.record(7, "computer_click", {}, "allow", now=100.0)
        asyncio.run(consume())
        snap = activity.snapshot()
        assert snap["active"] is True and snap["ended"] is False

    snap = activity.snapshot()
    assert snap["active"] is False and snap["ended"] is True


@pytest.mark.parametrize("second_depth", [0, 2])
def test_overlapping_turns_end_activity_only_after_last_exit(second_depth):
    with ExitStack() as first, ExitStack() as second:
        first.enter_context(approval_policy.ambient_turn(".", "auto", 7, 0))
        second.enter_context(approval_policy.ambient_turn(".", "auto", 7, second_depth))
        activity.record(7, "computer_click", {}, "allow", now=100.0)

        first.close()
        assert approval_policy.conversation_turn_in_flight(7)
        snap = activity.snapshot()
        assert snap["active"] is True and snap["ended"] is False

        second.close()
        assert not approval_policy.conversation_turn_in_flight(7)
        snap = activity.snapshot()
        assert snap["active"] is False and snap["ended"] is True


def test_turn_without_conversation_never_ends_activity(monkeypatch):
    ended = []
    monkeypatch.setattr(activity, "end", ended.append)

    with approval_policy.ambient_turn(".", "auto", None, 0):
        pass

    assert ended == []


def test_turnless_stream_never_ends_activity():
    activity.record(7, "computer_click", {}, "allow", now=100.0)

    async def frames():
        yield "data: {}\n\n"

    async def consume():
        return [frame async for frame in _tag_sse_stream(frames(), 7)]

    assert len(asyncio.run(consume())) == 1
    snap = activity.snapshot()
    assert snap["active"] is True and snap["ended"] is False
