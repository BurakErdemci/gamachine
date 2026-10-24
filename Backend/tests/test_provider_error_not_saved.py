"""A provider error event must never be saved as a successful result."""
import asyncio
import os
import sys
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from routes import conversation_routes as routes  # noqa: E402


class FakeProvider:
    def __init__(self, events):
        self.events = events
        self.closed = False

    async def analyze_code(self, prompt, max_tokens, cwd=None):
        try:
            for ev in self.events:
                yield ev
        finally:
            self.closed = True


def _endpoint(router, path):
    return next(r.endpoint for r in router.routes if r.path == path)


def _conv_db():
    db = Mock()
    db.get_last_workspace.return_value = os.getcwd()
    db.get_api_key.return_value = ""
    return db


def _patched(provider):
    return [
        patch.object(routes, "require_conversation_owner", return_value=(1, {})),
        patch.object(routes.chat_model, "chat_model",
                     return_value={"provider_type": "subscription", "model_name": "m"}),
        patch.object(routes.AIProviderManager, "get_provider", return_value=provider),
    ]


@pytest.mark.parametrize("events,expected", [
    ([{"type": "delta", "text": "part"}, {"type": "error", "content": "failure"}], None),
    ([{"type": "final", "text": "summary"}], "summary"),
    ([{"type": "delta", "text": "summary"}], "summary"),
    ([], None),
])
def test_compact_falls_back_on_error_and_closes_before_return(events, expected):
    provider = FakeProvider(events)
    db = _conv_db()
    messages = [{"id": i + 1, "role": "user", "content": f"message {i}"} for i in range(8)]
    db.get_conversation_messages.return_value = messages
    db.get_memory.return_value = ""
    ep = _endpoint(routes.create_conversation_router(db, {}), "/conversations/{conv_id}/compact")
    patches = _patched(provider)
    for p in patches:
        p.start()
    try:
        async def run():
            out = await ep(7, "tok", None)
            assert provider.closed
            return out

        out = asyncio.run(run())
    finally:
        for p in patches:
            p.stop()
    if expected is None:
        expected = ("(Otomatik kayıt — AI özeti alınamadı)\nSohbetin son mesajları:\n"
                    + "\n".join(f"- Kullanıcı: message {i}" for i in range(8)))
    assert out == {"status": "success", "summary": expected}
    db.compact_conversation.assert_called_once_with(7, expected, through_id=8)


@pytest.mark.parametrize("exit_kind", ["caller_close", "error", "exhaustion", "cancelled_close"])
def test_agy_one_shot_closes_inner_stream_before_session(monkeypatch, exit_kind):
    from providers.agy_provider import AgyProvider
    from providers import agy_session

    closed = []

    async def stream(self, message, **kwargs):
        try:
            yield {"type": "text", "content": "part"}
            if exit_kind == "error":
                yield {"type": "error", "message": "failure"}
            else:
                yield {"type": "response", "content": "answer"}
        finally:
            closed.append("stream")
            if exit_kind == "cancelled_close":
                raise asyncio.CancelledError

    async def close(self):
        assert closed == ["stream"]
        closed.append("session")

    monkeypatch.setattr(agy_session.AgyStreamSession, "stream", stream)
    monkeypatch.setattr(agy_session.AgyStreamSession, "close", close)

    async def run():
        provider = AgyProvider(binary_name="agy-claude-sonnet-5-5")
        events = provider.analyze_code("hello", cwd=".")
        assert await anext(events) == {"type": "delta", "text": "part"}
        if exit_kind == "cancelled_close":
            with pytest.raises(asyncio.CancelledError):
                await events.aclose()
        elif exit_kind == "caller_close":
            await events.aclose()
        else:
            remaining = [event async for event in events]
            assert remaining == ([{"type": "error", "content": "failure"}] if exit_kind == "error"
                                 else [{"type": "final", "text": "answer"}])
        assert closed == ["stream", "session"]

    asyncio.run(run())
