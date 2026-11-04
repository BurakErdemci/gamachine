"""`turn_live` frames on /wake-stream-all (docs/remote-control-web.md).

Every renderer follows a turn it did not start: the turn-event ring's events
reach each stream of the chat's owner as id-only frames; another user's
stream gets nothing, and the stream's ring listener goes when it closes.
"""
import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from agentic import turn_events
from routes.conversation_routes import create_conversation_router

MINE, THEIRS, BROKEN = 9101, 9102, 9103


@pytest.fixture(autouse=True)
def _fresh_ring():
    turn_events.RING.reset()
    yield
    turn_events.RING.reset()


def _owner(conv_id):
    if conv_id == BROKEN:
        raise RuntimeError("db down")
    return 1 if conv_id == MINE else 2


def _route():
    db = MagicMock()
    db.get_conversation_owner.side_effect = _owner
    db.get_conversation_title.return_value = None
    router = create_conversation_router(db, MagicMock())
    return db, next(r for r in router.routes if getattr(r, "path", "") == "/wake-stream-all")


async def _open(route, remote="", user_id=1):
    with patch("routes.conversation_routes.get_current_user", return_value=(user_id, None)):
        resp = await route.endpoint(x_session_token="t", x_ui_lang=None, x_gamachine_remote=remote)
    it = resp.body_iterator
    frames = []

    async def read():
        async for chunk in it:
            text = chunk if isinstance(chunk, str) else chunk.decode()
            for line in text.splitlines():
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:]))

    reader = asyncio.create_task(read())
    await asyncio.sleep(0.2)

    async def close():
        reader.cancel()
        try:
            await reader
        except BaseException:
            pass
        await it.aclose()
        await asyncio.sleep(0.05)

    return frames, close


async def _until(frames, pred, timeout=4.0):
    for _ in range(int(timeout / 0.05)):
        if pred(frames):
            return
        await asyncio.sleep(0.05)


def _live(frames):
    return [f for f in frames if f.get("type") == "turn_live"]


def test_owner_streams_get_turn_live_frames_and_others_do_not():
    async def run():
        _db, route = _route()
        before = len(turn_events.RING._listeners)
        desktop, close_desktop = await _open(route)
        browser, close_browser = await _open(route, remote="1")
        stranger, close_stranger = await _open(route, user_id=3)
        assert len(turn_events.RING._listeners) == before + 3

        TurnTap = turn_events.TurnTap
        TurnTap(THEIRS, "p", "m").start()
        TurnTap(BROKEN, "p", "m").start()
        tap = TurnTap(MINE, "p", "m")
        tap.start()
        # Text is flushed by the ring's timer thread: the frame has to hop
        # onto the stream's loop.
        turn_events.RING.text(MINE, "hello")
        await _until(desktop, lambda fs: any(f["kind"] == "text" for f in _live(fs)))
        tap.end("done")
        done = lambda fs: any(f["kind"] == "turn_end" for f in _live(fs))
        await _until(desktop, done)
        await _until(browser, done)
        await asyncio.sleep(0.2)

        for close in (close_desktop, close_browser, close_stranger):
            await close()
        return before, desktop, browser, stranger

    before, desktop, browser, stranger = asyncio.run(run())
    for frames in (desktop, browser):
        live = _live(frames)
        assert [(f["conversation_id"], f["kind"]) for f in live] == [
            (MINE, "turn_start"), (MINE, "text"), (MINE, "turn_end")]
        for f in live:
            assert set(f) == {"type", "conversation_id", "kind", "seq"}
            assert isinstance(f["seq"], int)
        assert [f["seq"] for f in live] == sorted(f["seq"] for f in live)
    assert _live(stranger) == []
    assert len(turn_events.RING._listeners) == before


def test_owner_lookup_is_cached_per_stream():
    async def run():
        db, route = _route()
        frames, close = await _open(route)
        tap = turn_events.TurnTap(MINE, "p", "m")
        tap.start()
        tap.end("done")
        await _until(frames, lambda fs: any(f["kind"] == "turn_end" for f in _live(fs)))
        await close()
        return db, frames

    db, frames = asyncio.run(run())
    assert len(_live(frames)) == 2
    assert [c.args for c in db.get_conversation_owner.call_args_list].count((MINE,)) == 1
