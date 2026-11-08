"""Round 2 of slash commands and the context gauge (audit of b3bc8682 and
13fc6092, 8 Nov 2026): a slash command reaches the agent bare even with a
Stop/reset notice pending at the route, a message that only starts with a path
keeps its notes, and every successful compact leaves the gauge on the
post-compact value, also when it was queued behind a running turn. Every
client is a fake; no CLI starts."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import providers.claude_sdk_session as cs
from agentic import agent_runner as ar
from schemas import ChatRequest
from test_bg_tasks_frames import _db, _post, runner  # noqa: F401  (runner is a fixture)
from test_bg_tasks_reset import NOTICE, _fresh, _with_tasks  # noqa: F401  (_fresh is autouse)
from test_codex_session_caps import live_session
from test_compact_spares_bg import _CompactingClient, _result
from tests.test_slash_command_no_note import NOTE, _claude_harness
from tests.test_stable_guide import _prepare, _runner

_BEFORE = {"used": 144000, "window": 200000, "percent": 72.0, "model": "m"}


@pytest.mark.parametrize("text,expected", [
    ("/context", True), ("/compact", True), ("/usage now", True), ("  /context", True),
    ("/Users/me/x.cs is broken", False), ("/tmp/a", False), ("/", False),
    ("/x.cs", False), ("hello", False), ("", False),
])
def test_only_a_command_name_is_a_slash_command(text, expected):
    assert ar._is_slash_command(text) is expected


# ── Route: the Stop/reset notice waits for the next ordinary message ──────

def test_a_slash_command_after_a_session_close_reaches_the_runner_bare(runner):
    asyncio.run(_with_tasks(1))
    asyncio.run(cs.close_session(1))          # leaves a reset notice
    db = _db()
    _post(db, ChatRequest(conversation_id=1, message="/context", user_id=1))
    assert runner.messages == ["/context"]
    assert not [c for c in db.add_message.call_args_list if c.args[1] == "system"]
    _post(db, ChatRequest(conversation_id=1, message="hi", user_id=1))
    assert runner.messages[-1] == f"{NOTICE}\n\nhi"


# ── Runner: a path is not a command ───────────────────────────────────────

def _turn(runner, text):
    _prepare(runner)

    async def run():
        return [e async for e in runner._run_claude_session(text)]
    asyncio.run(run())


def test_a_message_starting_with_a_path_keeps_the_unity_note(monkeypatch):
    sdk, messages = _claude_harness(monkeypatch)
    _turn(_runner(), "/Users/me/Proj/Assets/Foo.cs is broken, fix it")
    assert messages[0].startswith(NOTE)


def test_a_slash_command_after_leading_spaces_stays_bare(monkeypatch):
    sdk, messages = _claude_harness(monkeypatch)
    _turn(_runner(), "  /context")
    assert messages == ["  /context"]


# ── Claude gauge: ordering of post-compact and end-of-turn readings ───────

class _SlowClient(_CompactingClient):
    """Usage answers 72% until the CLI processed /compact, then 8%; each call
    may be delayed, and `by_call` fixes the answers per call."""

    def __init__(self, outcome="success", delays=(0,)):
        super().__init__(outcome)
        self.processed = False
        self.delays = list(delays)
        self.calls = 0
        self.by_call = None

    async def get_context_usage(self):
        self.calls += 1
        idx = self.calls - 1
        delay = self.delays[min(idx, len(self.delays) - 1)]
        pre = not self.processed
        if delay:
            await asyncio.sleep(delay)
        if self.by_call:
            p = self.by_call[min(idx, len(self.by_call) - 1)]
            return {"totalTokens": int(p * 2000), "maxTokens": 200000, "percentage": float(p), "model": "m"}
        if pre:
            return {"totalTokens": 144000, "maxTokens": 200000, "percentage": 72.0, "model": "m"}
        return {"totalTokens": 16000, "maxTokens": 200000, "percentage": 8.0, "model": "m"}


def _session(cid, **kw):
    s = cs.ClaudeSDKSession(conversation_id=cid)
    s._started = True
    s._client = _SlowClient(**kw)
    s._client.sess = s
    s.context_reading = dict(_BEFORE)
    return s


async def _one_turn(s, text, result_text):
    async def consume():
        async for _ in s.stream(text):
            pass
    t = asyncio.create_task(consume())
    for _ in range(5):
        await asyncio.sleep(0)
    await s._on_message(_result(1, result_text))
    await t


def test_a_compact_queued_behind_a_running_turn_ends_on_the_post_compact_reading():
    async def main():
        s = _session(5101, delays=(0.3,))
        s._client.outcome = None                  # the CLI holds /compact until the turn's Result

        async def consume():
            async for _ in s.stream("work"):
                pass
        t = asyncio.create_task(consume())
        for _ in range(5):
            await asyncio.sleep(0)
        ct = asyncio.create_task(s.compact(timeout=5))
        for _ in range(5):
            await asyncio.sleep(0)
        await s._on_message(_result(1, "answer"))  # the turn's end reading starts (slow, pre-compact)
        await asyncio.sleep(0.05)
        s._client.processed = True                  # the CLI now runs /compact
        s._client.outcome = "success"
        await s._client._answer()
        ok = await ct
        await t
        return s, ok
    s, ok = asyncio.run(main())
    assert ok is True
    assert s.context_reading["percent"] == 8.0


def test_closing_the_session_cancels_a_pending_post_compact_read():
    async def main():
        s = _session(5109)
        await s._turn_lock.acquire()                 # a turn holds the lock
        ok = await s.compact(timeout=2)
        task = s._compact_read_task
        pending = task is not None and not task.done()
        await s.close()
        await asyncio.sleep(0)
        return ok, pending, task, s
    ok, pending, task, s = asyncio.run(main())
    assert ok is True and pending
    assert task.cancelled() and s._compact_read_task is None


def test_a_slow_compact_reading_never_overwrites_a_newer_turn_reading():
    async def main():
        s = _session(5102, delays=(0.5, 0))
        s._client.by_call = [8.0, 30.0]
        s._client.processed = True
        ct = asyncio.create_task(s.compact(timeout=5))
        await asyncio.sleep(0.05)                   # the compact's reading is in flight, slow
        await _one_turn(s, "hi", "ok")              # a turn ends meanwhile with a newer reading
        newer = dict(s.context_reading)
        await ct
        return s, newer
    s, newer = asyncio.run(main())
    assert newer["percent"] == 30.0 and s.context_reading["percent"] == 30.0


def test_two_compacts_share_one_query_and_both_succeed():
    async def main():
        s = _session(5103)
        s._client.processed = True
        a, b = await asyncio.gather(s.compact(timeout=2), s.compact(timeout=2))
        return s, a, b
    s, a, b = asyncio.run(main())
    assert (a, b) == (True, True) and s._client.queries.count("/compact") == 1
    assert s.context_reading["percent"] == 8.0


def test_a_turn_end_reading_without_compact_is_stored():
    async def main():
        s = _session(5105)
        s._client.processed = True
        await _one_turn(s, "hi", "ok")
        return s
    s = asyncio.run(main())
    assert s.context_reading == {"used": 16000, "window": 200000, "percent": 8.0, "model": "m"}
    assert not s._turn_lock.locked()


def test_a_failed_turn_end_reading_keeps_the_old_one():
    async def main():
        s = _session(5106)

        async def boom():
            raise RuntimeError("x")
        s._client.get_context_usage = boom
        await _one_turn(s, "hi", "ok")
        return s
    s = asyncio.run(main())
    assert s.context_reading == _BEFORE and not s._turn_lock.locked()


# ── Codex gauge ───────────────────────────────────────────────────────────

def test_a_codex_compact_drops_the_pre_compact_reading():
    async def main():
        s, _ = live_session()
        s.context_reading = dict(_BEFORE)
        t = asyncio.create_task(s.compact())
        await asyncio.sleep(0.05)
        await s._handle_notification({"method": "thread/compacted", "params": {"threadId": "thr-1", "turnId": "t"}})
        return s, await asyncio.wait_for(t, 2)
    s, ok = asyncio.run(main())
    assert ok is True and s.context_reading is None


def test_a_codex_reading_reported_during_the_compact_is_kept():
    fresh = {"method": "thread/tokenUsage/updated", "params": {
        "threadId": "thr-1", "tokenUsage": {"modelContextWindow": 200000, "last": {"totalTokens": 20000}}}}

    async def main():
        s, _ = live_session()
        s.context_reading = dict(_BEFORE)
        t = asyncio.create_task(s.compact())
        await asyncio.sleep(0.05)
        await s._handle_notification(fresh)
        await s._handle_notification({"method": "thread/compacted", "params": {"threadId": "thr-1", "turnId": "t"}})
        return s, await asyncio.wait_for(t, 2)
    s, ok = asyncio.run(main())
    assert ok is True and s.context_reading["percent"] == 10.0
