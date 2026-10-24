"""Compaction preserves snapshot boundaries, prior memory, and independent resets."""
import asyncio
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from database import DatabaseManager
from routes import conversation_routes as routes


SUMMARY_PREFIX = "📝 **Sohbet özetlendi.**\n\n"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return DatabaseManager(str(tmp_path / "compact.db"))


def test_snapshot_compaction_preserves_newer_messages_and_order(db):
    cid = db.create_conversation(1, "snapshot")
    other = db.create_conversation(1, "unrelated")
    db.add_message(other, "user", "leave this alone")
    unrelated = db.get_conversation_messages(other)
    db.add_message(cid, "user", "old question")
    through_id = db.add_message(cid, "user", "snapshot end", smells=["old"],
                                provider="openai", model="old-model")
    newer_id = db.add_message(cid, "user", "arrived while summarizing",
                              provider="openai", model="new-model")
    newer = db.get_conversation_messages(cid)[-1]

    db.compact_conversation(cid, "saved decisions", through_id=through_id)

    messages = db.get_conversation_messages(cid)
    assert [m["id"] for m in messages] == [through_id, newer_id]
    assert messages[1] == newer
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == SUMMARY_PREFIX + "saved decisions"
    assert messages[0]["smells"] == []
    assert messages[0]["provider"] is None
    assert messages[0]["model"] is None
    assert db.get_memory(cid) == "saved decisions"
    assert db.get_conversation_messages(other) == unrelated


def test_deleted_snapshot_end_inserts_summary_and_preserves_newer_rows(db):
    cid = db.create_conversation(1, "deleted boundary")
    db.add_message(cid, "user", "old question")
    through_id = db.add_message(cid, "assistant", "snapshot end")
    db.add_message(cid, "user", "newer question")
    newer = db.get_conversation_messages(cid)[-1]
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("DELETE FROM messages WHERE id = ?", (through_id,))

    db.compact_conversation(cid, "summary", through_id=through_id)

    messages = db.get_conversation_messages(cid)
    assert len(messages) == 2
    assert messages[0] == newer
    assert messages[1]["id"] > newer["id"]
    assert messages[1]["content"] == SUMMARY_PREFIX + "summary"
    assert messages[1]["role"] == "assistant"
    assert db.get_memory(cid) == "summary"


@pytest.mark.parametrize("message_count", [0, 3])
def test_two_argument_compaction_covers_all_current_rows(db, message_count):
    cid = db.create_conversation(1, "legacy call")
    for i in range(message_count):
        db.add_message(cid, "user", f"message {i}")

    db.compact_conversation(cid, "legacy summary")

    messages = db.get_conversation_messages(cid)
    assert len(messages) == 1
    assert messages[0]["content"] == SUMMARY_PREFIX + "legacy summary"
    assert messages[0]["role"] == "assistant"
    assert db.get_memory(cid) == "legacy summary"


@pytest.mark.parametrize("result", ["success", "error", "empty"])
def test_route_carries_prior_summary_into_prompt_and_fallback(db, monkeypatch, result):
    cid = db.create_conversation(1, "prior memory")
    prior = "Keep the earlier decision. " * 40
    db.compact_conversation(cid, prior)
    for i in range(24):
        db.add_message(cid, "user", f"recent message {i}")
    through_id = db.get_conversation_messages(cid)[-1]["id"]
    prompts = []

    class Provider:
        async def analyze_code(self, prompt, max_tokens, cwd=None):
            prompts.append(prompt)
            db.add_message(cid, "user", "arrived during summary")
            if result == "error":
                yield {"type": "error", "content": "injected failure"}
            else:
                yield {"type": "final", "text": prior if result == "success" else ""}

    monkeypatch.setattr(routes, "require_conversation_owner", lambda *args: (1, {}))
    monkeypatch.setattr(routes.chat_model, "chat_model",
                        lambda *args: {"provider_type": "openai", "model_name": "fake"})
    monkeypatch.setattr(routes.AIProviderManager, "get_provider", lambda *args: Provider())
    reset = AsyncMock()
    monkeypatch.setattr(routes, "_cli_bagalamini_sifirla", reset)
    router = routes.create_conversation_router(db, {})
    endpoint = next(r.endpoint for r in router.routes
                    if r.path == "/conversations/{conv_id}/compact")

    outcome = asyncio.run(endpoint(cid, "token", None))

    block = f"ÖNCEKİ ÖZET (kararlarını ve önemli detaylarını yeni özete taşı):\n{prior}\n\n"
    assert len(prompts) == 1
    assert "Max 300 kelime. Türkçe yaz.\n\n" + block + "SOHBET:" in prompts[0]
    assert SUMMARY_PREFIX not in prompts[0]
    assert "recent message 0\n" not in prompts[0]
    if result == "success":
        expected = prior
    else:
        expected = ("(Otomatik kayıt — AI özeti alınamadı)\n"
                    + f"Önceki özet:\n{prior}\nSohbetin son mesajları:\n"
                    + "\n".join(f"- Kullanıcı: recent message {i}" for i in range(12, 24)))
    assert outcome == {"status": "success", "summary": expected}
    assert db.get_memory(cid) == expected
    messages = db.get_conversation_messages(cid)
    assert len(messages) == 2
    assert messages[0]["id"] == through_id
    assert messages[0]["content"] == SUMMARY_PREFIX + expected
    assert messages[1]["content"] == "arrived during summary"
    reset.assert_awaited_once_with(db, cid)


@pytest.mark.parametrize("failing_provider", ["claude", "codex", "agy"])
def test_provider_close_failure_does_not_skip_later_resets(db, monkeypatch, failing_provider):
    from agentic import agent_runner, wake_queue
    from providers import agy_session, claude_sdk_session, codex_session

    cid = db.create_conversation(1, "reset")
    db.save_cli_session(cid, "claude", "old-session")
    last_provider = {cid: "codex"}
    monkeypatch.setattr(agent_runner, "_LAST_SUB_PROVIDER", last_provider)
    steps = []

    def close_provider(name):
        async def close(conv_id):
            assert conv_id == cid
            assert db.get_cli_session(cid, "claude") is None
            steps.append(name)
            if name == failing_provider:
                raise RuntimeError("injected close failure")
        return close

    monkeypatch.setattr(claude_sdk_session, "close_session", close_provider("claude"))
    monkeypatch.setattr(codex_session, "close_session", close_provider("codex"))
    monkeypatch.setattr(agy_session, "close_session", close_provider("agy"))

    def reset(conv_id):
        assert conv_id == cid
        assert cid not in last_provider
        steps.append("wake")

    monkeypatch.setattr(wake_queue, "reset", reset)

    asyncio.run(routes._cli_bagalamini_sifirla(db, cid))

    assert steps == ["claude", "codex", "agy", "wake"]
