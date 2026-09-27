"""Each assistant message records the agent and model that wrote it (Burak,
27 Sep 2026): the header showed the chat's CURRENT model on every answer, so a
chat moved from OpenCode to Codex relabelled OpenCode's answers. Every runner
here is a fake; no CLI or vendor API starts."""
import json
import sqlite3
import types
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import wake_queue
from database import DatabaseManager
from rag.memory_manager import memory_manager

H = {"X-Session-Token": ""}
OPENCODE = "opencode:opencode/big-pickle"
CODEX = "gpt-6-luna"


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return DatabaseManager(str(tmp_path / "labels.db"))


class _FakeRunner:
    last = None

    def __init__(self, **kw):
        self.kw = kw
        _FakeRunner.last = self

    async def run(self, message):
        yield ar.AgentEvent("response", {"content": f"cevap:{self.kw['model_name']}"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_manager, "base_dir", mem_dir)
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    from providers import claude_sdk_session

    async def _no_close(cid):
        return None

    monkeypatch.setattr(claude_sdk_session, "close_session", _no_close)
    fast = FastAPI()
    fast.include_router(cr.create_conversation_router(db, {}))
    with TestClient(fast) as c:
        yield c


def _assistant_labels(db, cid):
    return [(m["provider"], m["model"]) for m in db.get_conversation_messages(cid)
            if m["role"] == "assistant"]


def _frames(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


# ── Migration ───────────────────────────────────────────────────────────────

def test_old_database_gets_nullable_columns_and_old_rows_stay_null(tmp_path):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
                     "title TEXT DEFAULT 'Yeni Sohbet', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, "
                     "role TEXT NOT NULL, content TEXT NOT NULL, smells_json TEXT DEFAULT '[]', "
                     "timestamp TEXT NOT NULL)")
        conn.execute("INSERT INTO conversations (user_id, title, created_at, updated_at) VALUES (1, 'eski', 'x', 'x')")
        conn.execute("INSERT INTO messages (conversation_id, role, content, timestamp) "
                     "VALUES (1, 'assistant', 'eski cevap', 'x')")
    db = DatabaseManager(path)
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(messages)")}
    assert {"provider", "model"} <= cols
    assert db.get_conversation_messages(1) == [
        {"id": 1, "role": "assistant", "content": "eski cevap", "smells": [], "timestamp": "x",
         "provider": None, "model": None}]
    DatabaseManager(path)  # a second start must not fail on existing columns
    db.add_message(1, "assistant", "yeni", provider="codex", model=CODEX)
    assert _assistant_labels(db, 1) == [(None, None), ("codex", CODEX)]


def test_branch_copy_keeps_each_messages_label(db):
    cid = db.create_conversation(1, "Ana")
    db.add_message(cid, "user", "s")
    db.add_message(cid, "assistant", "a", provider="opencode", model=OPENCODE)
    db.add_message(cid, "assistant", "b")
    branch = db.create_branch(cid)
    assert _assistant_labels(db, branch["id"]) == [("opencode", OPENCODE), (None, None)]


# ── Agent family ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider,model,agent", [
    ("subscription", "claude-sonnet-5", "claude"),
    ("subscription", CODEX, "codex"),
    ("subscription", "gemini-3.8-flash", "agy"),
    ("subscription", "agy-gpt-oss-120b", "agy"),
    ("subscription", OPENCODE, "opencode"),
    ("subscription", "copilot-gpt-5.5", "copilot"),
    ("subscription", "cursor-auto", "cursor"),
    ("subscription", "kimi-k3", "kimi"),
    ("subscription", "", "claude"),
    ("openai", "gpt-5.5", "api-openai"),
    ("ollama", "qwen2.5-coder:7b", "api-ollama"),
    ("anthropic", "claude-opus-5", "api-anthropic"),
])
def test_message_agent_names_the_family_the_turn_ran_on(provider, model, agent):
    assert cr._message_agent(provider, model) == agent


# ── Store paths ─────────────────────────────────────────────────────────────

def test_chat_stream_labels_each_answer_with_its_own_agent(db, client):
    cid = db.create_conversation(1, "Yeni Sohbet")
    db.save_ai_config(1, "subscription", OPENCODE, "")
    r1 = client.post("/chat-stream", json={"conversation_id": cid, "message": "ilk", "user_id": 1}, headers=H)
    db.save_ai_config(1, "subscription", CODEX, "")
    r2 = client.post("/chat-stream", json={"conversation_id": cid, "message": "ikinci", "user_id": 1}, headers=H)

    # The live answer gets the turn's agent before any content.
    for r, agent, model in ((r1, "opencode", OPENCODE), (r2, "codex", CODEX)):
        frames = _frames(r.text)
        assert frames[0]["type"] == "turn_meta"
        assert (frames[0]["provider"], frames[0]["model"]) == (agent, model)

    assert _assistant_labels(db, cid) == [("opencode", OPENCODE), ("codex", CODEX)]
    listed = client.get(f"/conversations/{cid}/messages", headers=H).json()
    assert [(m["role"], m["provider"], m["model"]) for m in listed] == [
        ("user", None, None), ("assistant", "opencode", OPENCODE),
        ("user", None, None), ("assistant", "codex", CODEX)]


def test_wake_turn_answer_is_labelled(db, client):
    cid = db.create_conversation(1, "Yeni Sohbet")
    db.save_ai_config(1, "subscription", "gemini-3.8-flash", "")
    wake_queue.issue_ticket(cid)
    try:
        r = client.post("/chat-stream", json={"conversation_id": cid, "message": "tasks_done|x",
                                              "user_id": 1, "origin": "wake"}, headers=H)
    finally:
        wake_queue.reset(cid)
    assert r.status_code == 200
    roles = [m["role"] for m in db.get_conversation_messages(cid)]
    assert roles == ["system", "assistant"]
    assert _assistant_labels(db, cid) == [("agy", "gemini-3.8-flash")]


def test_non_streaming_chat_labels_the_answer(db, client):
    cid = db.create_conversation(1, "Yeni Sohbet")
    db.save_ai_config(1, "openai", "gpt-5.5", "")
    r = client.post("/chat", json={"conversation_id": cid, "message": "soru", "user_id": 1}, headers=H)
    assert r.status_code == 200
    assert _assistant_labels(db, cid) == [("api-openai", "gpt-5.5")]


def test_side_chat_answer_is_labelled(db, client):
    main = db.create_conversation(1, "Ana")
    db.add_message(main, "user", "m0")
    db.save_ai_config(1, "subscription", "claude-sonnet-5", "")
    side = client.post(f"/conversations/{main}/side", headers=H).json()["side_id"]
    r = client.post(f"/conversations/{side}/side-stream", json={"message": "neden?"}, headers=H)
    assert r.status_code == 200
    assert _assistant_labels(db, side) == [("claude", "claude-sonnet-5")]


def test_detached_claude_session_turn_is_labelled_with_the_sessions_model(db, client):
    # A Claude SDK turn that finishes after the stream closed is written by
    # the saver the router registers; the session passes its own model.
    from providers import claude_sdk_session
    cid = db.create_conversation(1, "Yeni Sohbet")
    claude_sdk_session._DB_SAVE_CB(cid, "otonom cevap", "claude-opus-5")
    assert _assistant_labels(db, cid) == [("claude", "claude-opus-5")]


def test_project_analysis_summary_is_labelled(db, client, monkeypatch, tmp_path):
    class _Rag:
        def __init__(self, path):
            self.documents = ["a.cs"]

        def scan_project(self):
            pass

        def generate_project_report(self):
            return "rapor"

    class _Provider:
        def analyze_code(self, prompt, max_tokens):
            return "[USER_SUMMARY] özet [TECHNICAL_WISDOM] teknik"

    monkeypatch.setattr(cr, "ProjectRAG", _Rag)
    monkeypatch.setattr(cr.AIProviderManager, "get_provider", staticmethod(lambda cfg: _Provider()))
    cid = db.create_conversation(1, "Yeni Sohbet")
    db.save_ai_config(1, "google", "gemini-3.8-flash", "")
    db.save_workspace(1, str(tmp_path))
    r = client.post(f"/conversations/{cid}/analyze-project", headers=H)
    assert r.status_code == 200, r.text
    assert _assistant_labels(db, cid) == [("api-google", "gemini-3.8-flash")]
