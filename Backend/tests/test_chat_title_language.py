"""AI chat titles come out in the conversation's language, and in the UI
language when the chat does not tell (12 Nov 2026: an English chat on Codex
got a Turkish title). Every runner is a fake; no CLI or vendor API starts."""
import asyncio
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import chat_titles as ct
from database import DatabaseManager

H = {"X-Session-Token": ""}


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(ct, "_EVENTS", ct.deque(maxlen=256))
    return DatabaseManager(str(tmp_path / "titles.db"))


class _Recorder:
    def __init__(self):
        self.prompts = []

    async def __call__(self, choice, prompt, ctx):
        self.prompts.append(prompt)
        return "Some Title"


def _first_title_prompt(db, monkeypatch, user_text, ui_language, model="gpt-6.1-sol", runner="codex"):
    rec = _Recorder()
    monkeypatch.setitem(ct.RUNNERS, runner, rec)
    cid = db.create_conversation(1, "New chat")
    db.add_message(cid, "user", user_text)
    db.add_message(cid, "assistant", "Done.")

    async def go():
        task = ct.after_reply(db, cid, "subscription", model, "", language=ui_language)
        assert task is not None
        await task
    asyncio.run(go())
    assert len(rec.prompts) == 1
    return rec.prompts[0]


def test_english_chat_gets_an_english_title_under_a_turkish_ui(db, monkeypatch):
    # The measured case: English chat, Codex GPT-6.1 Sol; the UI may say anything.
    prompt = _first_title_prompt(db, monkeypatch, "Add a sphere on top of the cup in Blender", "tr")
    assert "Write the title in English." in prompt
    assert "Write the title in Turkish." not in prompt


def test_turkish_chat_gets_a_turkish_title_under_an_english_ui(db, monkeypatch):
    prompt = _first_title_prompt(db, monkeypatch, "Blender'da kupanın üstüne bir küre ekle", "en",
                                 model="claude-opus-5", runner="claude")
    assert "Write the title in Turkish." in prompt
    assert "Write the title in English." not in prompt


@pytest.mark.parametrize("ui,name", [("en", "English"), ("tr", "Turkish")])
def test_an_ambiguous_first_message_falls_back_to_the_ui_language(db, monkeypatch, ui, name):
    prompt = _first_title_prompt(db, monkeypatch, "Unity 6000.4.4f1", ui)
    assert f"Write the title in {name}." in prompt


def test_the_instruction_no_longer_shows_only_a_turkish_example():
    assert "an English chat gets an English title" in ct.build_title_prompt("User: hi")


@pytest.mark.parametrize("messages,ui,expected", [
    ([{"role": "user", "content": "How do I make the player jump higher?"}], None, "en"),
    ([{"role": "user", "content": "Oyuncu nasıl daha yükseğe zıplar?"}], None, "tr"),
    # Only the user's own rows decide: a Turkish reply does not make the chat Turkish.
    ([{"role": "user", "content": "fix the script please"},
      {"role": "assistant", "content": "Tamam, şimdi düzeltiyorum ve bir test ekliyorum."}], "tr", "en"),
    ([{"role": "user", "content": "ok"}], "xx", None),
])
def test_title_language(messages, ui, expected):
    assert ct.title_language(messages, ui) == expected


# ── The chat route hands the UI language to the title job ──────────────────

class _FakeRunner:
    def __init__(self, **kw):
        self.kw = kw

    async def run(self, message):
        yield ar.AgentEvent("response", {"content": "ok"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})


def _wait_for(pred, timeout=5.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.mark.parametrize("body_lang,header,name", [("en", None, "English"), (None, "en", "English"),
                                                    ("tr", None, "Turkish")])
def test_chat_stream_passes_the_ui_language(db, monkeypatch, body_lang, header, name):
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    rec = _Recorder()
    monkeypatch.setitem(ct.RUNNERS, "claude", rec)
    db.save_ai_config(1, "subscription", "claude-opus-5", "")
    fast = FastAPI()
    fast.include_router(cr.create_conversation_router(db, {}))
    cid = db.create_conversation(1, "New chat")
    body = {"conversation_id": cid, "message": "Unity 6000.4.4f1", "user_id": 1}
    if body_lang:
        body["language"] = body_lang
    headers = dict(H, **({"X-UI-Lang": header} if header else {}))
    with TestClient(fast) as client:
        r = client.post("/chat-stream", json=body, headers=headers)
        assert r.status_code == 200
        assert _wait_for(lambda: len(rec.prompts) == 1)
    assert f"Write the title in {name}." in rec.prompts[0]
