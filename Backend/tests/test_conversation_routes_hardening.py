"""2026-07-27 denetiminden çıkan üç sertleştirmenin regresyon testleri.

Kapsam:
  1. /chat-stream hata olayı — JSON escape + ham istisna metni sızmaması
  2. MCP gate sözlükleri — okunan sonucun düşmesi ve TTL süpürmesi
  3. ChatRequest — girdi boyutu/eleman sayısı sınırları
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def _make_client(db=None):
    from routes.conversation_routes import create_conversation_router

    app = FastAPI()
    app.include_router(create_conversation_router(db or MagicMock(), {}))
    return TestClient(app)


def _sse_payloads(body: str) -> list:
    """SSE gövdesindeki her 'data:' satırını JSON olarak çözer.

    Framing bozulmuşsa (escape edilmemiş tırnak/satır sonu) burada patlar —
    testin asıl ölçtüğü şey bu.
    """
    payloads = []
    for line in body.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[len("data: "):]))
    return payloads


class TestChatStreamErrorEvent(unittest.TestCase):
    """Hata mesajı SSE framing'ini bozmamalı ve iç detay sızdırmamalı."""

    def _db(self):
        db = MagicMock()
        db.get_ai_config.return_value = ("claude", "claude-opus-5", None, None)
        db.get_api_key.return_value = ""
        db.get_last_workspace.return_value = ""
        db.get_memory.return_value = ""
        db.get_conversation_messages.return_value = []
        return db

    def test_error_event_is_valid_json_and_hides_raw_exception(self):
        # İçinde tırnak VE satır sonu olan bir istisna: eski elle-kurulan JSON
        # kalıbının tam olarak bozulduğu girdi.
        secret = '/Users/gizli/yol.py'
        boom = RuntimeError(f'dosya bulunamadı: "{secret}"\nTraceback satırı')

        class _ExplodingRunner:
            def __init__(self, **kwargs):
                pass

            async def run(self, message):
                raise boom
                yield  # pragma: no cover — fonksiyonu async generator yapar

        with patch("routes.conversation_routes.AgentRunner", _ExplodingRunner):
            with _make_client(self._db()) as client:
                resp = client.post(
                    "/chat-stream",
                    json={"conversation_id": 1, "message": "merhaba", "user_id": 1},
                    headers={"X-Session-Token": ""},
                )

        self.assertEqual(resp.status_code, 200)
        payloads = _sse_payloads(resp.text)
        errors = [p for p in payloads if p.get("type") == "error"]
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0]["message"])
        self.assertNotIn(secret, resp.text)
        self.assertNotIn("Traceback satırı", resp.text)


class TestMcpGateLifecycle(unittest.TestCase):
    """_mcp_results sınırsız büyümemeli: okunan düşer, okunmayan TTL ile süpürülür."""

    def _open_gate(self, client, gate_id):
        resp = client.post("/mcp-approval-request", json={
            "gate_id": gate_id,
            "tool": "bash",
            # A critical command: Safe Auto, the default with no UI secret,
            # opens no card for a routine one such as "echo ok".
            "params": {"command": "rm -rf build"},
            "workspace_path": os.getcwd(),
        })
        self.assertEqual(resp.json()["status"], "ok")

    def test_resolved_result_is_dropped_after_first_read(self):
        with _make_client() as client:
            self._open_gate(client, "g1")
            self.assertEqual(
                client.post("/mcp-approval-respond/g1", json={"approved": True}).json()["status"],
                "ok",
            )
            first = client.get("/mcp-approval-result/g1").json()
            second = client.get("/mcp-approval-result/g1").json()
            # Kayıt düştüğü için ikinci okuma artık gate'i bulamaz; bridge ilk
            # "pending değil" yanıtında polling'i bıraktığı için bu güvenli.
            respond_again = client.post(
                "/mcp-approval-respond/g1", json={"approved": True}
            ).json()

        self.assertEqual(first, {"status": "resolved", "approved": True})
        self.assertEqual(second, {"status": "pending"})
        self.assertEqual(respond_again["status"], "gate_not_found")

    def test_pending_result_survives_read(self):
        """Henüz çözülmemiş gate okunmakla düşmemeli — bridge polling'de."""
        with _make_client() as client:
            self._open_gate(client, "g2")
            self.assertEqual(client.get("/mcp-approval-result/g2").json(), {"status": "pending"})
            # Hâlâ kayıtlı: kart listede ve karar verilebilir durumda.
            self.assertIn("g2", client.get("/mcp-pending").json()["pending"])
            self.assertEqual(
                client.post("/mcp-approval-respond/g2", json={"approved": False}).json()["status"],
                "ok",
            )

    def test_ttl_sweeps_unread_gates(self):
        import routes.conversation_routes as cr

        with _make_client() as client:
            self._open_gate(client, "g3")
            self.assertIn("g3", client.get("/mcp-pending").json()["pending"])
            # TTL 600 sn; saat 601 sn ileri alınınca hem sonuç hem bekleyen kart düşer.
            real_time = cr.time  # patch'ten önce yakala, yoksa lambda kendini çağırır
            with patch.object(cr, "time", lambda: real_time() + 601):
                remaining = client.get("/mcp-pending").json()["pending"]
                # Süpürme sonrası karara varılamaz: iki sözlük birlikte boşalmalı.
                respond = client.post(
                    "/mcp-approval-respond/g3", json={"approved": True}
                ).json()

        self.assertNotIn("g3", remaining)
        self.assertEqual(respond["status"], "gate_not_found")


class TestChatRequestLimits(unittest.TestCase):
    def test_message_length_capped(self):
        from schemas import ChatRequest

        ChatRequest(conversation_id=1, user_id=1, message="x" * 200_000)
        with pytest.raises(ValidationError):
            ChatRequest(conversation_id=1, user_id=1, message="x" * 200_001)

    def test_editor_code_length_capped(self):
        from schemas import ChatRequest

        ChatRequest(conversation_id=1, user_id=1, message="m", editor_code="y" * 400_000)
        with pytest.raises(ValidationError):
            ChatRequest(conversation_id=1, user_id=1, message="m", editor_code="y" * 400_001)

    def test_attachment_counts_capped(self):
        from schemas import ChatRequest

        ChatRequest(conversation_id=1, user_id=1, message="m", images=["d"] * 20)
        with pytest.raises(ValidationError):
            ChatRequest(conversation_id=1, user_id=1, message="m", images=["d"] * 21)

        ChatRequest(conversation_id=1, user_id=1, message="m",
                    videos=[{"kind": "path", "path": "a.mp4"}] * 5)
        with pytest.raises(ValidationError):
            ChatRequest(conversation_id=1, user_id=1, message="m",
                        videos=[{"kind": "path", "path": "a.mp4"}] * 6)


if __name__ == "__main__":
    unittest.main()
