"""Per-chat model (owner request): each chat keeps its own provider/model and a
turn of chat X runs with X's model whatever any window shows; the global
ai_configs row is only the default for a new chat (the last pick). API keys
stay per provider. Every runner here is a fake; no CLI or vendor API starts.
"""
import json
import sqlite3
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import chat_model as cm
from agentic import wake_queue
from database import DatabaseManager
from rag.memory_manager import memory_manager
from routes.config_routes import create_config_router

H = {"X-Session-Token": ""}
CLAUDE = "claude-opus-5"
CODEX = "gpt-6-luna"
OPENCODE = "opencode:opencode/big-pickle"


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    d = DatabaseManager(str(tmp_path / "chatmodel.db"))
    d.save_ai_config(1, "subscription", CLAUDE, "")
    return d


class _FakeRunner:
    runs = []

    def __init__(self, **kw):
        self.kw = kw

    async def run(self, message):
        _FakeRunner.runs.append((self.kw["conversation_id"], self.kw["provider_type"],
                                 self.kw["model_name"]))
        yield ar.AgentEvent("response", {"content": "cevap"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    _FakeRunner.runs = []
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
    fast.include_router(create_config_router(db))
    with TestClient(fast) as c:
        yield c


def _send(client, cid, text="merhaba", **extra):
    r = client.post("/chat-stream", headers=H,
                    json={"conversation_id": cid, "message": text, "user_id": 1, **extra})
    assert r.status_code == 200, r.text
    frames = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    return frames[0]


def _pick(client, provider, model, cid=None, key=""):
    body = {"user_id": 1, "provider_type": provider, "model_name": model, "api_key": key}
    if cid is not None:
        body["conversation_id"] = cid
    return client.post("/save-ai-config", headers=H, json=body)


# ── storage ─────────────────────────────────────────────────────────────────

def test_old_database_gets_nullable_model_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
                     "title TEXT DEFAULT 'Yeni Sohbet', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO conversations (user_id, title, created_at, updated_at) VALUES (1, 'eski', 'x', 'x')")
    d = DatabaseManager(path)
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(conversations)")}
    assert {"provider_type", "model_name"} <= cols
    assert d.get_conversation_model(1) is None
    DatabaseManager(path)  # a second start must not fail on existing columns
    assert d.set_conversation_model(1, "subscription", CODEX)
    assert d.get_conversation_model(1) == ("subscription", CODEX)


def test_set_does_not_touch_updated_at_and_stamp_never_overwrites(db):
    cid = db.create_conversation(1, "A")
    before = db.get_user_conversations(1)[0]["updated_at"]
    assert db.set_conversation_model(cid, "subscription", CODEX, only_if_unset=True)
    assert not db.set_conversation_model(cid, "openai", "gpt-5.5", only_if_unset=True)
    assert db.get_conversation_model(cid) == ("subscription", CODEX)
    assert db.get_user_conversations(1)[0]["updated_at"] == before
    assert not db.set_conversation_model(9999, "subscription", CODEX)


def test_a_branch_starts_on_its_sources_model(db):
    cid = db.create_conversation(1, "Ana")
    db.add_message(cid, "user", "s")
    db.set_conversation_model(cid, "subscription", CODEX)
    branch = db.create_branch(cid)
    assert db.get_conversation_model(branch["id"]) == ("subscription", CODEX)


# ── resolution and fallback ─────────────────────────────────────────────────

def test_stored_model_wins_over_messages_and_global(db):
    cid = db.create_conversation(1, "A")
    db.add_message(cid, "assistant", "a", provider="opencode", model=OPENCODE)
    db.set_conversation_model(cid, "openai", "gpt-5.5")
    assert cm.chat_model(db, 1, cid) == {"provider_type": "openai", "model_name": "gpt-5.5"}


@pytest.mark.parametrize("agent,model,expected", [
    ("opencode", OPENCODE, ("subscription", OPENCODE)),
    ("codex", CODEX, ("subscription", CODEX)),
    ("agy", "gemini-3.8-flash", ("subscription", "gemini-3.8-flash")),
    ("claude", "claude-sonnet-5", ("subscription", "claude-sonnet-5")),
    ("api-openai", "gpt-5.5", ("openai", "gpt-5.5")),
    ("api-ollama", "qwen2.5-coder:7b", ("ollama", "qwen2.5-coder:7b")),
])
def test_unstamped_chat_falls_back_to_its_latest_message(db, agent, model, expected):
    cid = db.create_conversation(1, "eski")
    db.add_message(cid, "assistant", "önceki", provider="claude", model="claude-haiku-4-5")
    db.add_message(cid, "assistant", "son", provider=agent, model=model)
    db.add_message(cid, "assistant", "etiketsiz")  # no label: skipped
    got = cm.chat_model(db, 1, cid)
    assert (got["provider_type"], got["model_name"]) == expected


@pytest.mark.parametrize("agent,model", [
    ("claude", CODEX),            # family does not match the model id
    ("codex", "claude-opus-5"),
    ("api-unknown", "gpt-5.5"),   # not a provider ai_configs knows
    ("api-subscription", "x"),
    ("api-openai", "gpt‮5"),  # an id no provider publishes
])
def test_a_message_that_does_not_map_cleanly_falls_back_to_global(db, agent, model):
    cid = db.create_conversation(1, "eski")
    db.add_message(cid, "assistant", "son", provider=agent, model=model)
    assert cm.chat_model(db, 1, cid) == {"provider_type": "subscription", "model_name": CLAUDE}


def test_an_empty_chat_follows_the_global_default_until_its_first_turn(db):
    cid = db.create_conversation(1, "yeni")
    assert cm.chat_model(db, 1, cid)["model_name"] == CLAUDE
    db.save_ai_config(1, "subscription", CODEX, "")
    assert cm.chat_model(db, 1, cid)["model_name"] == CODEX
    assert db.get_conversation_model(cid) is None  # reading never stamps


def test_turn_model_stamps_once(db):
    cid = db.create_conversation(1, "yeni")
    assert cm.turn_model(db, 1, cid) == ("subscription", CLAUDE)
    db.save_ai_config(1, "subscription", CODEX, "")
    assert cm.turn_model(db, 1, cid) == ("subscription", CLAUDE)
    assert db.get_conversation_model(cid) == ("subscription", CLAUDE)


# ── /chat-stream runs the chat's own model ──────────────────────────────────

def test_first_turn_runs_the_global_default_and_stamps_the_chat(db, client):
    cid = db.create_conversation(1, "yeni")
    meta = _send(client, cid)
    assert (meta["provider"], meta["model"]) == ("claude", CLAUDE)
    assert db.get_conversation_model(cid) == ("subscription", CLAUDE)


def test_a_pick_in_another_chat_does_not_move_a_started_chat(db, client):
    a = db.create_conversation(1, "A")
    b = db.create_conversation(1, "B")
    _send(client, a)
    assert _pick(client, "subscription", CODEX, cid=b).status_code == 200
    assert (_send(client, a)["model"], _send(client, b)["model"]) == (CLAUDE, CODEX)
    # A new chat starts on the last pick.
    c = db.create_conversation(1, "C")
    assert _send(client, c)["model"] == CODEX


def test_an_old_chat_resumes_on_the_model_of_its_latest_answer(db, client):
    cid = db.create_conversation(1, "eski")
    db.add_message(cid, "user", "soru")
    db.add_message(cid, "assistant", "cevap", provider="opencode", model=OPENCODE)
    meta = _send(client, cid)
    assert (meta["provider"], meta["model"]) == ("opencode", OPENCODE)
    assert db.get_conversation_model(cid) == ("subscription", OPENCODE)


def test_a_wake_turn_runs_the_woken_chats_model(db, client):
    cid = db.create_conversation(1, "B")
    db.set_conversation_model(cid, "subscription", CODEX)
    wake_queue.issue_ticket(cid)
    try:
        meta = _send(client, cid, "tasks_done|x", origin="wake")
    finally:
        wake_queue.reset(cid)
    assert (meta["provider"], meta["model"]) == ("codex", CODEX)
    assert _FakeRunner.runs == [(cid, "subscription", CODEX)]


def test_the_api_key_stays_per_provider(db, client, monkeypatch):
    db.save_api_key(1, "openai", "sk-openai")
    cid = db.create_conversation(1, "A")
    db.set_conversation_model(cid, "openai", "gpt-5.5")
    seen = {}

    class _KeyRunner(_FakeRunner):
        def __init__(self, **kw):
            super().__init__(**kw)
            seen["key"] = kw["api_key"]

    monkeypatch.setattr(cr, "AgentRunner", _KeyRunner)
    _send(client, cid)
    assert seen["key"] == "sk-openai"


def test_a_side_question_runs_on_its_main_chats_model(db, client):
    main = db.create_conversation(1, "Ana")
    db.add_message(main, "user", "m0")
    db.set_conversation_model(main, "subscription", CODEX)
    side = client.post(f"/conversations/{main}/side", headers=H).json()["side_id"]
    r = client.post(f"/conversations/{side}/side-stream", json={"message": "neden?"}, headers=H)
    assert r.status_code == 200
    assert _FakeRunner.runs == [(side, "subscription", CODEX)]


# ── what a window reads when it opens a chat ────────────────────────────────

def test_get_chat_model_reads_the_resolved_model_and_key_state(db, client):
    cid = db.create_conversation(1, "A")
    db.set_conversation_model(cid, "openai", "gpt-5.5")
    got = client.get(f"/conversations/{cid}/model", headers=H).json()
    assert got == {"provider_type": "openai", "model_name": "gpt-5.5", "has_key": False}
    db.save_api_key(1, "openai", "sk-x")
    assert client.get(f"/conversations/{cid}/model", headers=H).json()["has_key"] is True


def test_get_chat_model_refuses_a_side_chat_and_a_missing_chat(db, client):
    main = db.create_conversation(1, "Ana")
    side = db.create_side_chat(main, 1)
    assert client.get(f"/conversations/{side}/model", headers=H).status_code == 400
    assert client.get("/conversations/9999/model", headers=H).status_code == 404


# ── /save-ai-config: the desktop pick ───────────────────────────────────────

def test_a_pick_with_a_chat_sets_that_chat_and_the_global_default(db, client):
    a = db.create_conversation(1, "A")
    b = db.create_conversation(1, "B")
    db.set_conversation_model(b, "subscription", OPENCODE)
    # Keyless cloud pick: accepted, the desktop then asks for the key.
    assert _pick(client, "openai", "gpt-5.5", cid=a).status_code == 200
    assert db.get_conversation_model(a) == ("openai", "gpt-5.5")
    assert db.get_ai_config(1)[:2] == ("openai", "gpt-5.5")
    assert db.get_conversation_model(b) == ("subscription", OPENCODE)


def test_a_pick_without_a_chat_sets_only_the_global_default(db, client):
    a = db.create_conversation(1, "A")
    assert _pick(client, "subscription", CODEX).status_code == 200
    assert db.get_ai_config(1)[:2] == ("subscription", CODEX)
    assert db.get_conversation_model(a) is None


@pytest.mark.parametrize("provider,model,code", [
    ("claude", CLAUDE, "unknown_provider"),
    ("", CLAUDE, "unknown_provider"),
    ("subscription", "gpt‮-6", "bad_model"),
    ("subscription", "x" * 201, "bad_model"),
])
def test_a_refused_pick_writes_nothing(db, client, provider, model, code):
    a = db.create_conversation(1, "A")
    r = _pick(client, provider, model, cid=a, key="sk-new")
    assert (r.status_code, r.json()["detail"]) == (400, code)
    assert db.get_conversation_model(a) is None
    assert db.get_ai_config(1)[:2] == ("subscription", CLAUDE)


def test_a_pick_for_a_missing_or_side_chat_writes_nothing(db, client):
    main = db.create_conversation(1, "Ana")
    side = db.create_side_chat(main, 1)
    assert _pick(client, "openai", "gpt-5.5", cid=9999, key="sk-x").status_code == 404
    assert _pick(client, "openai", "gpt-5.5", cid=side, key="sk-x").status_code == 404
    assert db.get_conversation_model(side) is None
    assert db.get_api_key(1, "openai") is None
    assert db.get_ai_config(1)[:2] == ("subscription", CLAUDE)


def test_an_empty_model_still_means_provider_default(db, client):
    a = db.create_conversation(1, "A")
    assert _pick(client, "subscription", "", cid=a).status_code == 200
    assert db.get_conversation_model(a) == ("subscription", "")


# ── set_chat_model: the one writer the phone bridge will call ───────────────

def test_set_chat_model_stores_only_that_chat(db):
    a = db.create_conversation(1, "A")
    b = db.create_conversation(1, "B")
    db.save_api_key(1, "openai", "sk-x")
    assert cm.set_chat_model(db, 1, a, "openai", "gpt-5.5") == {
        "provider_type": "openai", "model_name": "gpt-5.5"}
    assert db.get_conversation_model(a) == ("openai", "gpt-5.5")
    assert db.get_conversation_model(b) is None
    assert db.get_ai_config(1)[:2] == ("subscription", CLAUDE)


def test_set_chat_model_refusals_are_value_errors_with_short_codes(db):
    main = db.create_conversation(1, "Ana")
    other = db.create_conversation(2, "başkasının")
    side = db.create_side_chat(main, 1)
    cases = [
        ((9999, "openai", "gpt-5.5"), "unknown_chat"),
        ((other, "openai", "gpt-5.5"), "unknown_chat"),
        ((side, "openai", "gpt-5.5"), "unknown_chat"),
        ((True, "openai", "gpt-5.5"), "unknown_chat"),
        ((main, "claude", CLAUDE), "unknown_provider"),
        ((main, None, CLAUDE), "unknown_provider"),
        ((main, "subscription", "a\u0000b"), "bad_model"),
        ((main, "subscription", None), "bad_model"),
        ((main, "subscription", "m" * 201), "bad_model"),
    ]
    for (conv, provider, model), code in cases:
        with pytest.raises(ValueError) as exc:
            cm.set_chat_model(db, 1, conv, provider, model, require_ready=False)
        assert str(exc.value) == code and exc.value.code == code
    assert db.get_conversation_model(main) is None


def test_set_chat_model_refuses_a_provider_that_is_not_ready(db, monkeypatch):
    from providers import oneshot_cli
    cid = db.create_conversation(1, "A")
    with pytest.raises(cm.ChatModelError) as exc:
        cm.set_chat_model(db, 1, cid, "openai", "gpt-5.5")
    assert (exc.value.code, exc.value.extra) == ("not_ready", {"needs": "apikey"})

    monkeypatch.setattr(oneshot_cli, "installed_clis", lambda resolve=None: {"codex": False})
    with pytest.raises(cm.ChatModelError) as exc:
        cm.set_chat_model(db, 1, cid, "subscription", CODEX)
    assert exc.value.extra == {"needs": "install"}

    monkeypatch.setattr(cm, "_ollama_up", lambda: False)
    with pytest.raises(cm.ChatModelError) as exc:
        cm.set_chat_model(db, 1, cid, "ollama", "qwen2.5-coder:7b")
    assert exc.value.extra == {"needs": "service"}
    assert db.get_conversation_model(cid) is None

    monkeypatch.setattr(oneshot_cli, "installed_clis", lambda resolve=None: {"codex": True})
    assert cm.set_chat_model(db, 1, cid, "subscription", CODEX)["model_name"] == CODEX
    # The desktop's optimistic pick skips readiness.
    assert cm.set_chat_model(db, 1, cid, "openai", "gpt-5.5", require_ready=False)
    assert db.get_conversation_model(cid) == ("openai", "gpt-5.5")


def test_set_chat_model_takes_an_injected_readiness_probe(db):
    cid = db.create_conversation(1, "A")
    asked = []

    def probe(d, user_id, provider, model):
        asked.append((user_id, provider, model))
        return {"ready": False, "needs": "login"}

    with pytest.raises(cm.ChatModelError) as exc:
        cm.set_chat_model(db, 1, cid, "subscription", "cursor-auto", readiness=probe)
    assert asked == [(1, "subscription", "cursor-auto")]
    assert exc.value.extra == {"needs": "login"}


# ── /provider-ready answers for the pair a window shows ─────────────────────

def test_provider_ready_answers_for_an_explicit_pair(db, client):
    r = client.get("/provider-ready/1", headers=H,
                   params={"provider_type": "openai", "model_name": "gpt-5.5"})
    assert r.json() == {"ready": False, "kind": "api", "provider": "openai", "needs": "apikey"}
    db.save_api_key(1, "openai", "sk-x")
    r = client.get("/provider-ready/1", headers=H,
                   params={"provider_type": "openai", "model_name": "gpt-5.5"})
    assert r.json()["ready"] is True
    bad = client.get("/provider-ready/1", headers=H,
                     params={"provider_type": "claude", "model_name": CLAUDE})
    assert (bad.status_code, bad.json()["detail"]) == (400, "unknown_provider")


def test_a_capitalised_subscription_id_has_one_cli_family():
    seen = []
    state = cm.provider_readiness(
        None, 1, "subscription", "GPT-6-LUNA",
        cli_state=lambda fam: seen.append(fam) or {"installed": True, "loggedIn": None})
    assert seen == ["codex"] and state["provider"] == "codex"
    assert cr._oturum_saglayici_anahtari("subscription", "GPT-6-LUNA") == "codex"
