"""Compaction is terminal Claude Code's /compact (Burak, 7 Nov 2026): a live
Claude session compacts in place and is never closed, so background
subagents keep running. A 15-minute background subagent died to the old
close (gamachine.log, 7 Nov 2026). Every client is a fake; no CLI starts."""
import asyncio
from collections import defaultdict
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import providers.claude_sdk_session as cs
import routes.conversation_routes as cr
from database import DatabaseManager

H = {"X-Session-Token": ""}


def _status(**data):
    from claude_agent_sdk import SystemMessage
    return SystemMessage(subtype="status", data={"type": "system", "subtype": "status", **data})


def _user(text):
    from claude_agent_sdk import UserMessage
    return UserMessage(content=text, parent_tool_use_id=None)


def _result(num_turns, text=""):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=num_turns, session_id="s", result=text)


class _CompactingClient:
    """Answers /compact the way the CLI did (measured 7 Nov 2026)."""

    def __init__(self, outcome="success"):
        self.queries, self.outcome, self.closed = [], outcome, False
        self.sess = None

    async def query(self, text, *a, **k):
        self.queries.append(text)
        if text == "/compact" and self.outcome is not None:
            asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self._answer()))

    async def _answer(self):
        s = self.sess
        await s._on_message(_user("/compact"))
        await s._on_message(_status(status="compacting"))
        await s._on_message(_status(status=None, compact_result=self.outcome))
        await s._on_message(_user("This session is being continued from a previous conversation..."))
        await s._on_message(_user("Compacted"))
        await s._on_message(_result(0))

    async def get_context_usage(self):
        raise RuntimeError("no context reading in tests")

    async def __aexit__(self, *a):
        self.closed = True


def _live(cid, outcome="success", tasks=None):
    sess = cs.ClaudeSDKSession(conversation_id=cid)
    sess._started = True
    sess._client = _CompactingClient(outcome)
    sess._client.sess = sess
    sess._active_tasks.update(tasks or {})
    return sess


def test_compact_runs_in_place_and_stores_nothing(monkeypatch):
    saved = []
    monkeypatch.setattr(cs, "_DB_SAVE_CB", lambda cid, text, model=None: saved.append(text))
    monkeypatch.setattr(cs, "_INJECT_SAVE_CB", lambda *a, **k: saved.append(a))

    async def main():
        sess = _live(4401, tasks={"a1": "P1b: rooms"})
        ok = await sess.compact(timeout=2)
        return sess, ok
    sess, ok = asyncio.run(main())
    assert ok is True
    assert sess._client.queries == ["/compact"] and not sess._client.closed
    assert sess._active_tasks == {"a1": "P1b: rooms"}
    assert saved == [] and not sess._turn_active and sess._owed_results == 0


def test_compact_queued_behind_a_running_turn_does_not_end_the_next_stream():
    async def main():
        sess = _live(4402)
        sess._client.outcome = None  # the CLI holds it until the turn's Result
        await sess.compact(timeout=0.01)
        events = []

        async def consume():
            async for ev in sess.stream("next"):
                if not ev.get("heartbeat"):
                    events.append(ev)
        task = asyncio.create_task(consume())
        for _ in range(5):
            await asyncio.sleep(0)
        await sess._on_message(_result(0))      # the late /compact Result
        await asyncio.sleep(0.05)
        assert not task.done()
        await sess._on_message(_result(1, "answer"))
        await asyncio.wait_for(task, 2)
        return events
    events = asyncio.run(main())
    assert [e for e in events if e["type"] == "response"] == [{"type": "response", "content": "answer"}]


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return DatabaseManager(str(tmp_path / "compact.db"))


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    closed = []

    async def close_session(cid):
        closed.append(cid)

    monkeypatch.setattr(cs, "close_session", close_session)
    monkeypatch.setattr(cs, "_SESSIONS", {})
    monkeypatch.setattr(cs, "_COMPACT_TIMEOUT_S", 2.0, raising=False)
    monkeypatch.setattr(cr.AIProviderManager, "get_provider",
                        staticmethod(lambda cfg: SimpleNamespace(analyze_code=lambda p, n: "kısa özet")))
    fast = FastAPI()
    fast.include_router(cr.create_conversation_router(db, {}))
    with TestClient(fast) as c:
        c.closed = closed
        yield c


def _chat(db, rows=8):
    cid = db.create_conversation(1, "Yeni Sohbet")
    for i in range(rows):
        db.add_message(cid, "user" if i % 2 == 0 else "assistant", f"m{i}")
    return cid


def _rows(db, cid):
    return [(m["role"], m["content"]) for m in db.get_conversation_messages(cid)]


def test_compact_with_a_live_session_compacts_it_and_never_closes_it(db, client):
    cid = _chat(db)
    sess = cs._SESSIONS[cid] = _live(cid, tasks={"a1": "P1b: rooms"})
    r = client.post(f"/conversations/{cid}/compact", headers=H)
    assert r.status_code == 200 and r.json()["summary"] == "kısa özet"
    assert sess._client.queries == ["/compact"]
    assert client.closed == [] and cs._SESSIONS.get(cid) is sess
    assert sess._active_tasks == {"a1": "P1b: rooms"}


def test_a_failed_compact_with_running_tasks_keeps_the_session_and_says_so(db, client):
    cid = _chat(db)
    sess = cs._SESSIONS[cid] = _live(cid, outcome="failure", tasks={"b1": "npm run dev"})
    r = client.post(f"/conversations/{cid}/compact", headers=H)
    assert r.json()["notice"] == cr._COMPACT_FAILED_KEPT
    assert client.closed == [] and sess._active_tasks == {"b1": "npm run dev"}
    assert ("system", cr._COMPACT_FAILED_KEPT) in _rows(db, cid)


def test_a_failed_compact_without_tasks_falls_back_to_the_reset(db, client):
    cid = _chat(db)
    cs._SESSIONS[cid] = _live(cid, outcome="failure")
    client.post(f"/conversations/{cid}/compact", headers=H)
    assert client.closed == [cid]


def test_compact_without_a_live_session_takes_the_old_path(db, client):
    cid = _chat(db)
    client.post(f"/conversations/{cid}/compact", headers=H)
    assert client.closed == [cid]


def test_a_short_chat_compacts_in_place_too(db, client):
    cid = _chat(db, rows=2)
    sess = cs._SESSIONS[cid] = _live(cid, tasks={"a1": "P1b: rooms"})
    client.post(f"/conversations/{cid}/compact", headers=H)
    assert sess._client.queries == ["/compact"] and client.closed == []
