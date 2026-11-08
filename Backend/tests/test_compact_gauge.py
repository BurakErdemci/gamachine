"""After an in-place /compact the context gauge shows the post-compact reading,
not the last pre-compact one (Burak, 8 Nov 2026: the bar stayed at 72% until a
later /context). Only the CLI client is a fake; no CLI starts."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import providers.claude_sdk_session as cs
import routes.conversation_routes as cr
from test_compact_spares_bg import H, _CompactingClient, _chat, _live, client, db  # noqa: F401

_BEFORE = {"used": 144000, "window": 200000, "percent": 72.0, "model": "m"}


class _GaugeClient(_CompactingClient):
    """get_context_usage answers 72% until /compact was sent, then 8%."""

    def __init__(self, outcome="success", usage_fails=False):
        super().__init__(outcome)
        self.usage_fails = usage_fails

    async def get_context_usage(self):
        if self.usage_fails:
            raise RuntimeError("no reading")
        if "/compact" in self.queries:
            return {"totalTokens": 16000, "maxTokens": 200000, "percentage": 8.0, "model": "m"}
        return {"totalTokens": 144000, "maxTokens": 200000, "percentage": 72.0, "model": "m"}


def _gauged(cid, **kw):
    sess = _live(cid)
    sess._client = _GaugeClient(**kw)
    sess._client.sess = sess
    sess.context_reading = dict(_BEFORE)
    return sess


def test_compact_leaves_the_post_compact_reading():
    sess = _gauged(4601)
    assert asyncio.run(sess.compact(timeout=2)) is True
    assert sess.context_reading["percent"] == 8.0 and sess.context_reading["used"] == 16000


def test_a_failed_reading_after_compact_drops_the_stale_one():
    sess = _gauged(4602, usage_fails=True)
    assert asyncio.run(sess.compact(timeout=2)) is True
    assert sess.context_reading is None


def test_a_failed_compact_leaves_the_reading_untouched():
    sess = _gauged(4603, outcome="failure")
    assert asyncio.run(sess.compact(timeout=2)) is False
    assert sess.context_reading == _BEFORE


def test_compact_queued_behind_a_running_turn_reads_only_after_the_turn():
    async def main():
        sess = _gauged(4604)
        await sess._turn_lock.acquire()  # a turn is running: its reader may be busy
        try:
            ok = await sess.compact(timeout=2)
            for _ in range(5):
                await asyncio.sleep(0)
            during = dict(sess.context_reading)
        finally:
            sess._turn_lock.release()
        await asyncio.wait_for(sess._compact_read_task, 2)
        return sess, ok, during
    sess, ok, during = asyncio.run(main())
    assert ok is True and during == _BEFORE
    assert sess.context_reading["percent"] == 8.0


def test_the_gauge_route_shows_the_fresh_value_after_compact(db, client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cr, "_current_pair", lambda *a: ("claude-cli", "m"))
    monkeypatch.setattr(cr.chat_model, "cli_family", lambda *a: "claude")
    cid = _chat(db)
    cs._SESSIONS[cid] = _gauged(cid)
    before = client.get(f"/conversations/{cid}/context-usage", headers=H).json()
    assert before["percent"] == 72 and before["estimated"] is False
    assert client.post(f"/conversations/{cid}/compact", headers=H).status_code == 200
    after = client.get(f"/conversations/{cid}/context-usage", headers=H).json()
    assert after["percent"] == 8 and after["estimated"] is False
