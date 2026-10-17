"""Finished, failed and closed turn streams explicitly end live presence."""
import asyncio

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


@pytest.mark.parametrize("fails", [False, True])
def test_stream_ends_activity_after_inner_cleanup(fails):
    activity.record(7, "computer_click", {"pid": 42}, "allow", now=100.0)
    cleaned = []

    async def frames():
        try:
            yield 'data: {"type": "text"}\n\n'
            if fails:
                raise ValueError("turn failed")
        finally:
            cleaned.append(activity.snapshot()["active"])

    async def consume():
        return [frame async for frame in _tag_sse_stream(frames(), 7)]

    if fails:
        with pytest.raises(ValueError, match="turn failed"):
            asyncio.run(consume())
    else:
        assert asyncio.run(consume()) == ['data: {"type": "text", "conversation_id": 7}\n\n']
    assert cleaned == [True]
    snap = activity.snapshot()
    assert snap["active"] is False and snap["ended"] is True


def test_closing_stream_ends_activity_after_inner_cleanup():
    activity.record(7, "computer_click", {}, "allow", now=100.0)
    cleaned = []

    async def frames():
        try:
            yield "data: {}\n\n"
            yield "data: {}\n\n"
        finally:
            cleaned.append(True)

    async def close():
        stream = _tag_sse_stream(frames(), 7)
        await anext(stream)
        await stream.aclose()

    asyncio.run(close())
    assert cleaned == [True]
    snap = activity.snapshot()
    assert snap["active"] is False and snap["ended"] is True


@pytest.mark.parametrize("fails", [False, True])
def test_end_failure_is_logged_without_masking_stream_exit(monkeypatch, caplog, fails):
    def broken_end(conversation_id):
        assert conversation_id == 7
        raise RuntimeError("activity unavailable")

    monkeypatch.setattr(activity, "end", broken_end)

    async def frames():
        yield "data: {}\n\n"
        if fails:
            raise ValueError("original turn error")

    async def consume():
        return [frame async for frame in _tag_sse_stream(frames(), 7)]

    if fails:
        with pytest.raises(ValueError, match="original turn error"):
            asyncio.run(consume())
    else:
        assert len(asyncio.run(consume())) == 1
    assert "activity unavailable" in caplog.text
    assert any(record.exc_info for record in caplog.records)


def test_stream_keeps_activity_until_other_turn_exits():
    async def frames():
        yield "data: {}\n\n"

    async def consume():
        return [frame async for frame in _tag_sse_stream(frames(), 7)]

    with approval_policy.ambient_turn(".", "auto", 7, 0):
        activity.record(7, "computer_click", {}, "allow", now=100.0)
        assert len(asyncio.run(consume())) == 1
        snap = activity.snapshot()
        assert snap["active"] is True and snap["ended"] is False

    assert len(asyncio.run(consume())) == 1
    snap = activity.snapshot()
    assert snap["active"] is False and snap["ended"] is True


@pytest.mark.parametrize("close_early", [False, True])
def test_stream_ends_activity_after_own_ambient_turn_exits(close_early):
    async def frames():
        with approval_policy.ambient_turn(".", "auto", 7, 0):
            activity.record(7, "computer_click", {}, "allow", now=100.0)
            yield "data: {}\n\n"
            yield "data: {}\n\n"

    async def consume():
        stream = _tag_sse_stream(frames(), 7)
        if close_early:
            await anext(stream)
            assert approval_policy.conversation_turn_in_flight(7)
            await stream.aclose()
        else:
            assert len([frame async for frame in stream]) == 2

    asyncio.run(consume())
    assert not approval_policy.conversation_turn_in_flight(7)
    snap = activity.snapshot()
    assert snap["active"] is False and snap["ended"] is True


def test_in_flight_check_failure_is_logged_without_breaking_stream(monkeypatch, caplog):
    def broken_check(conversation_id):
        assert conversation_id == 7
        raise RuntimeError("turn registry unavailable")

    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", broken_check)
    activity.record(7, "computer_click", {}, "allow", now=100.0)

    async def frames():
        yield "data: {}\n\n"

    async def consume():
        return [frame async for frame in _tag_sse_stream(frames(), 7)]

    assert len(asyncio.run(consume())) == 1
    assert "turn registry unavailable" in caplog.text
    assert any(record.exc_info for record in caplog.records)
    snap = activity.snapshot()
    assert snap["active"] is True and snap["ended"] is False
