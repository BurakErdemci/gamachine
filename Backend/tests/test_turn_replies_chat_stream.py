"""chat-stream records each turn that began with a user row in agentic.turn_replies:
begin with the user row id, end with the assistant row it stored (or None) and how
the turn ended. Wake turns record nothing. Every runner is a fake; no CLI starts."""
import asyncio
import json
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import turn_replies, wake_queue
from database import DatabaseManager
from schemas import ChatRequest

H = {"X-Session-Token": ""}
CLAUDE = "claude-sonnet-5"
CODEX = "gpt-6-luna"


class _Runner:
    """Plays `_Runner.script`: a list of events, an exception, or "hang"."""
    script: list = []

    def __init__(self, **kw):
        self.kw = kw

    async def run(self, message):
        for step in _Runner.script:
            if step == "hang":
                await asyncio.Event().wait()
            if isinstance(step, Exception):
                raise step
            yield ar.AgentEvent(*step)


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    turn_replies.clear()
    yield DatabaseManager(str(tmp_path / "turns.db"))
    turn_replies.clear()


@pytest.fixture
def router(db, monkeypatch):
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    monkeypatch.setattr(cr, "AgentRunner", _Runner)
    from providers import claude_sdk_session

    async def _no_close(cid):
        return None

    monkeypatch.setattr(claude_sdk_session, "close_session", _no_close)
    return cr.create_conversation_router(db, {})


@pytest.fixture
def client(router):
    fast = FastAPI()
    fast.include_router(router)
    with TestClient(fast) as c:
        yield c


def _rows(db, cid):
    return [(m["id"], m["role"]) for m in db.get_conversation_messages(cid)]


def _chat(db, model=CODEX):
    cid = db.create_conversation(1, "Yeni Sohbet")
    db.save_ai_config(1, "subscription", model, "")
    return cid


def _post(client, cid, text="soru", **extra):
    r = client.post("/chat-stream", json={"conversation_id": cid, "message": text, "user_id": 1, **extra},
                    headers=H)
    assert r.status_code == 200
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def test_a_done_turn_records_its_user_row_and_its_assistant_row(db, client):
    cid = _chat(db)
    _Runner.script = [("response", {"content": "CEVAP"}), ("done", {"iterations": 1, "stop_reason": "complete"})]
    _post(client, cid)
    (uid, urole), (aid, arole) = _rows(db, cid)
    assert (urole, arole) == ("user", "assistant")
    assert turn_replies.get(cid, uid) == {"state": "ended", "assistant_row_id": aid, "status": "done"}


def test_an_error_turn_ends_without_a_row(db, client):
    cid = _chat(db)
    _Runner.script = [("error", {"message": "boom"})]
    _post(client, cid)
    [(uid, role)] = _rows(db, cid)
    assert role == "user"
    assert turn_replies.get(cid, uid) == {"state": "ended", "assistant_row_id": None, "status": "error"}


def test_a_runner_that_raises_ends_with_error(db, client):
    cid = _chat(db)
    _Runner.script = [("response", {"content": "yarım"}), RuntimeError("kaboom")]
    frames = _post(client, cid)
    assert any(f["type"] == "error" for f in frames)
    [(uid, _)] = _rows(db, cid)
    assert turn_replies.get(cid, uid) == {"state": "ended", "assistant_row_id": None, "status": "error"}


def test_an_error_after_text_keeps_the_stored_row_and_says_error(db, client):
    cid = _chat(db)
    _Runner.script = [("response", {"content": "kısmi"}), ("error", {"message": "boom"})]
    _post(client, cid)
    (uid, _), (aid, _) = _rows(db, cid)
    assert turn_replies.get(cid, uid) == {"state": "ended", "assistant_row_id": aid, "status": "error"}


def test_a_stopped_turn_says_stopped(db, client):
    cid = _chat(db)
    _Runner.script = [("response", {"content": "yarım kalan"}),
                      ("done", {"iterations": 1, "stop_reason": "cancelled"})]
    _post(client, cid)
    (uid, _), (aid, _) = _rows(db, cid)
    assert turn_replies.get(cid, uid) == {"state": "ended", "assistant_row_id": aid, "status": "stopped"}
    _Runner.script = [("done", {"iterations": 1, "stop_reason": "cancelled"})]
    _post(client, cid, "ikinci")
    uid2 = _rows(db, cid)[-1][0]
    assert turn_replies.get(cid, uid2) == {"state": "ended", "assistant_row_id": None, "status": "stopped"}


def test_a_wake_turn_records_nothing(db, client):
    cid = _chat(db)
    _Runner.script = [("response", {"content": "uyandım"}), ("done", {"iterations": 1, "stop_reason": "complete"})]
    wake_queue.issue_ticket(cid)
    try:
        _post(client, cid, "tasks_done|x", origin="wake")
    finally:
        wake_queue.reset(cid)
    rows = _rows(db, cid)
    assert [r for _, r in rows] == ["system", "assistant"]
    assert all(turn_replies.get(cid, rid) is None for rid, _ in rows)
    assert not turn_replies._turns


def _endpoint(router):
    return next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/chat-stream")


async def _open_and_close(router, cid, text):
    """Start a turn, read until the runner's text arrives, then drop the stream
    the way a closed window does (no terminal event reached the client)."""
    resp = await _endpoint(router)(ChatRequest(conversation_id=cid, message=text, user_id=1),
                                   x_session_token="", x_ui_lang=None)
    it = resp.body_iterator
    while "ARA" not in await it.__anext__():
        pass
    return it


async def test_a_detached_claude_turn_ends_when_its_reply_is_stored_later(db, router):
    from providers import claude_sdk_session
    cid = _chat(db, CLAUDE)
    _Runner.script = [("response", {"content": "ARA"}), "hang"]
    it = await _open_and_close(router, cid, "uzun iş")
    [(uid, _)] = _rows(db, cid)
    assert turn_replies.get(cid, uid) == {"state": "running"}
    await it.aclose()
    # The Claude session keeps running: the record waits for its stored reply.
    assert turn_replies.get(cid, uid) == {"state": "running"}
    claude_sdk_session._DB_SAVE_CB(cid, "sonradan gelen cevap", "claude-opus-5")
    aid = _rows(db, cid)[-1][0]
    assert turn_replies.get(cid, uid) == {"state": "ended", "assistant_row_id": aid, "status": "done"}


async def test_a_detached_claude_turn_that_never_stores_ends_at_the_next_turn(db, router):
    cid = _chat(db, CLAUDE)
    _Runner.script = [("response", {"content": "ARA"}), "hang"]
    await (await _open_and_close(router, cid, "ilk")).aclose()
    [(uid, _)] = _rows(db, cid)
    assert turn_replies.get(cid, uid) == {"state": "running"}
    await (await _open_and_close(router, cid, "ikinci")).aclose()
    assert turn_replies.get(cid, uid) == {"state": "ended", "assistant_row_id": None, "status": "stopped"}
    uid2 = _rows(db, cid)[-1][0]
    assert turn_replies.get(cid, uid2) == {"state": "running"}


async def test_another_providers_turn_ends_with_its_stream(db, router):
    cid = _chat(db, CODEX)
    _Runner.script = [("response", {"content": "ARA"}), "hang"]
    await (await _open_and_close(router, cid, "soru")).aclose()
    [(uid, _)] = _rows(db, cid)
    assert turn_replies.get(cid, uid) == {"state": "ended", "assistant_row_id": None, "status": "stopped"}


def test_a_registry_failure_never_affects_the_turn(db, client, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("registry down")

    monkeypatch.setattr(turn_replies, "begin", broken)
    monkeypatch.setattr(turn_replies, "end", broken)
    cid = _chat(db)
    _Runner.script = [("response", {"content": "CEVAP"}), ("done", {"iterations": 1, "stop_reason": "complete"})]
    frames = _post(client, cid)
    assert [f["type"] for f in frames if f["type"] in ("done", "error")] == ["done"]
    assert [r for _, r in _rows(db, cid)] == ["user", "assistant"]
