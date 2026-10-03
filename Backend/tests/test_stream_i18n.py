"""Stream localization preserves model text and follows the request language."""
import json
from collections import defaultdict
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from error_i18n import UI_EVENT_TYPES, localize_event, localize_sse, translate_detail


@pytest.mark.parametrize("event_type", sorted(UI_EVENT_TYPES))
@pytest.mark.parametrize("lang", ["en", "tr", None])
def test_ui_fields_are_localized_without_mutating_input(event_type, lang):
    event = {"type": event_type, "code": "kept", "tokens": 3,
             **dict.fromkeys(("message", "content", "detail", "text"), "İşlem durduruldu.")}
    result = localize_event(event, lang)
    expected = "Operation stopped." if lang == "en" else "İşlem durduruldu."
    assert result is not event
    assert result == {**event, **dict.fromkeys(("message", "content", "detail", "text"), expected)}
    assert event["message"] == "İşlem durduruldu."


@pytest.mark.parametrize("event_type", ["token", "delta", "text", "response", "thinking", "tool_call",
    "tool_result", "done", "usage", "turn_usage", "turn_meta", "wake_message", "wake", "unknown"])
async def test_model_and_control_frames_are_byte_identical(event_type):
    event = {"type": event_type, "content": "İşlem durduruldu."}
    frame = "data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n"

    async def source():
        yield frame

    assert localize_event(event, "en") == event
    assert [chunk async for chunk in localize_sse(source(), "en")] == [frame]


async def test_sse_translates_json_and_preserves_non_events():
    chunks = [": keepalive\n\n", "event: status\n\n", "data: {broken\n\n",
              "data: []\n\n", "data: null\n\n", "data: [DONE]\n\n",
              'data: {"type": [], "message": "İşlem durduruldu."}\n\n',
              'data: {"type": "error", "message": "İşlem durduruldu.", "id": 7}\n\n']

    async def source():
        for chunk in chunks:
            yield chunk

    result = [chunk async for chunk in localize_sse(source(), "en")]
    assert result[:-1] == chunks[:-1]
    assert result[-1] == "data: " + json.dumps({"type": "error", "message": "Operation stopped.", "id": 7}) + "\n\n"


def test_non_string_fields_and_unknown_text_are_preserved():
    event = {"type": "error", "message": {"nested": "İşlem durduruldu."}, "content": None,
             "text": "provider diagnostic", "detail": 42}
    assert localize_event(event, "en") == event


@pytest.mark.parametrize("body_lang,header_lang,expected", [
    ("en", "tr", "An error occurred while streaming the response. See the server logs for details."),
    ("tr", "en", "Yanıt akışı sırasında bir hata oluştu. Ayrıntı sunucu loglarında."),
    (None, "en", "An error occurred while streaming the response. See the server logs for details."),
    (None, None, "Yanıt akışı sırasında bir hata oluştu. Ayrıntı sunucu loglarında."),
])
def test_chat_route_localizes_error_using_body_then_header(monkeypatch, body_lang, header_lang, expected):
    import routes.conversation_routes as cr

    class ExplodingRunner:
        def __init__(self, **kwargs):
            pass

        async def run(self, message):
            raise RuntimeError("private diagnostic")
            yield

    db = MagicMock()
    db.get_ai_config.return_value = ("claude", "claude-opus-5", None, None)
    db.get_api_key.return_value = ""
    db.get_last_workspace.return_value = ""
    db.get_memory.return_value = ""
    db.get_conversation_messages.return_value = []
    monkeypatch.setattr(cr, "AgentRunner", ExplodingRunner)
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    app = FastAPI()
    app.include_router(cr.create_conversation_router(db, {}))
    body = {"conversation_id": 1, "message": "hello", "user_id": 1}
    if body_lang is not None:
        body["language"] = body_lang
    headers = {"X-Session-Token": ""}
    if header_lang is not None:
        headers["X-UI-Lang"] = header_lang
    with TestClient(app) as client:
        response = client.post("/chat-stream", json=body, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["message"] == expected
    assert errors[0]["conversation_id"] == 1
    assert "private diagnostic" not in response.text


@pytest.mark.parametrize("source,expected", [
    ("🤖 Görev başladı: build", "🤖 Task started: build"),
    ("✅ Görev bitti: build", "✅ Task finished: build"),
    ("⏳ 2 arka plan görevi sürüyor (build, audit) — bitince devam edilecek",
     "⏳ 2 background tasks running (build, audit) — will resume when finished"),
    ("⏳ 2 arka plan görevi sürüyor", "⏳ 2 background tasks running"),
    ("aşama: bilinmiyor · RuntimeError", "stage: unknown · RuntimeError"),
    ("Codex session hatası: Codex thread/start başarısız: bad request",
     "Codex session error: Codex thread/start failed: bad request"),
    ("❌ CLI hata (rc=2): first\nsecond", "❌ CLI error (rc=2): first\nsecond"),
])
def test_dynamic_notices_preserve_variable_parts(source, expected):
    assert translate_detail(source, "en") == expected
    assert translate_detail(source, "tr") == source
    assert translate_detail(source, None) == source


async def test_closing_wrapper_closes_source():
    closed = []

    async def source():
        try:
            yield ": keepalive\n\n"
            yield ": another\n\n"
        finally:
            closed.append(True)

    wrapper = localize_sse(source(), "en")
    assert await anext(wrapper) == ": keepalive\n\n"
    await wrapper.aclose()
    assert closed == [True]


@pytest.mark.parametrize("lang", ["en", "tr", None])
def test_plain_success_messages_use_header(monkeypatch, lang):
    import routes.config_routes as config
    import routes.mcp_routes as mcp

    terminal = MagicMock()
    install = MagicMock(return_value=True)
    monkeypatch.setattr(config, "_open_visible_terminal", terminal)
    monkeypatch.setattr(mcp.unity_mcp_manager, "install_package", install)
    db = MagicMock()
    app = FastAPI()
    app.include_router(config.create_config_router(db))
    app.include_router(mcp.create_mcp_router())
    headers = {"X-Session-Token": ""}
    if lang is not None:
        headers["X-UI-Lang"] = lang
    cases = [
        ("/cli-install/codex", None, "Kurulum penceresi açıldı.", "Installation window opened."),
        ("/cli-login/codex", None, "Giriş penceresi açıldı.", "Sign-in window opened."),
        ("/api-keys/save", {"provider_type": "openai", "api_key": "test-key"},
         "openai API key kaydedildi.", "openai API key saved."),
        ("/mcp/unity/install", {"workspace_path": "."},
         "Unity MCP paketi başarıyla kuruldu.", "Unity MCP package installed successfully."),
    ]
    with TestClient(app) as client:
        for endpoint, body, turkish, english in cases:
            response = client.post(endpoint, json=body, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["message"] == (english if lang == "en" else turkish)
    assert terminal.call_count == 2
    install.assert_called_once_with(".")
