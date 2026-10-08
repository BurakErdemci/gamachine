"""A provider error event must never be saved as a successful result."""
import asyncio
import os
import sys
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from routes import analysis_routes, conversation_routes as routes  # noqa: E402
from schemas import AnalysisRequest  # noqa: E402


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


ERROR = [{"type": "error", "content": "Unknown agy model ID: 'x'"}]


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


def _run_conv(path, provider, call, rag=None):
    db = _conv_db()
    router = routes.create_conversation_router(db, {})
    save = Mock()
    patches = _patched(provider) + [patch.object(routes.memory_manager, "save_memory", save)]
    if rag is not None:
        patches.append(patch.object(routes, "ProjectRAG", return_value=rag))
    for p in patches:
        p.start()
    try:
        outcome = None
        try:
            outcome = asyncio.run(call(_endpoint(router, path)))
        except HTTPException as exc:
            outcome = exc
        return outcome, save, db
    finally:
        for p in patches:
            p.stop()


def _rag():
    rag = Mock(documents=[{"path": "A.cs", "content": "class A {}"}])
    rag.generate_project_report.return_value = "A.cs"
    return rag


def _analyze(ep):
    return ep(7, "tok", None)


def _import(ep):
    return ep(7, {"content": "architecture notes"}, "tok")


def test_project_analysis_error_keeps_memory_and_reports_failure():
    provider = FakeProvider(ERROR)
    out, save, db = _run_conv("/conversations/{conv_id}/analyze-project", provider, _analyze, _rag())
    assert isinstance(out, HTTPException) and out.status_code == 500
    assert "Unknown agy model ID" in out.detail
    save.assert_not_called()
    db.add_message.assert_not_called()
    assert provider.closed


def test_project_analysis_success_still_saves():
    events = [{"type": "delta", "text": "[USER_SUMMARY] hi [TECHNICAL_WISDOM] notes"}]
    out, save, db = _run_conv("/conversations/{conv_id}/analyze-project", FakeProvider(events), _analyze, _rag())
    assert out["status"] == "success"
    save.assert_called_once_with("7", "notes")
    db.add_message.assert_called_once()


def test_memory_import_error_refuses_without_saving():
    provider = FakeProvider(ERROR)
    out, save, _ = _run_conv("/conversations/{conv_id}/import-memory", provider, _import)
    assert isinstance(out, HTTPException) and out.status_code == 500
    save.assert_not_called()
    assert provider.closed


def test_memory_import_safe_verdict_still_saves():
    out, save, _ = _run_conv("/conversations/{conv_id}/import-memory",
                             FakeProvider([{"type": "final", "text": "SAFE"}]), _import)
    assert out == {"status": "success"}
    save.assert_called_once_with("7", "architecture notes")


def _run_analyze(provider, provider_type="subscription"):
    db = Mock()
    db.get_ai_config.return_value = (provider_type, "m", "", False)
    db.get_api_key.return_value = ""
    router = analysis_routes.create_analysis_router(db)
    with patch.object(analysis_routes, "require_user", return_value=(1, {})), \
            patch.object(analysis_routes.AIProviderManager, "get_provider", return_value=provider):
        result = asyncio.run(_endpoint(router, "/analyze")(
            AnalysisRequest(user_id=1, code="public class Example { }"), "tok"))
    return result, db


def test_analyze_error_returns_error_and_writes_no_history():
    provider = FakeProvider(ERROR)
    result, db = _run_analyze(provider)
    assert result["intent"] == "ERROR"
    assert "Unknown agy model ID" in result["ai_suggestion"]
    db.save_analysis.assert_not_called()
    assert provider.closed


def test_analyze_success_still_saves_history():
    result, db = _run_analyze(FakeProvider([{"type": "delta", "text": "fix it"}]))
    assert result["ai_suggestion"] == "fix it"
    db.save_analysis.assert_called_once()


class SyncProvider:
    def __init__(self, answer):
        self.answer = answer

    def analyze_code(self, prompt, max_tokens=2048):
        return self.answer


@pytest.mark.parametrize("events,expected", [
    ([{"type": "delta", "text": "part"}, {"type": "error", "content": "failure"}], None),
    ([{"type": "final", "text": "summary"}], "summary"),
    ([{"type": "delta", "text": "summary"}], "summary"),
    ([], None),
])
def test_compact_falls_back_on_error_and_closes_before_return(events, expected):
    provider = FakeProvider(events)
    db = _conv_db()
    messages = [{"role": "user", "content": f"message {i}"} for i in range(8)]
    db.get_conversation_messages.return_value = messages
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
    db.compact_conversation.assert_called_once_with(7, expected)


def test_memory_import_rejects_error_after_final_before_saving():
    provider = FakeProvider([{"type": "final", "text": "SAFE"},
                             {"type": "error", "content": "late failure"}])

    async def call(ep):
        try:
            return await _import(ep)
        finally:
            assert provider.closed

    out, save, db = _run_conv("/conversations/{conv_id}/import-memory", provider, call)
    assert isinstance(out, HTTPException) and out.status_code == 500
    assert out.detail == "Hafıza güvenlik denetimi yapılamadı."
    save.assert_not_called()
    db.add_message.assert_not_called()


@pytest.mark.parametrize("verdict,status", [
    ("", 500), ("   ", 500), ("OK", 500), ("UNSAFE", 500),
    ("SAFE", 200), ("safe.", 200), ("**SAFE**", 200),
    ("DANGEROUS: x", 400), ("safe but dangerous", 400),
])
@pytest.mark.parametrize("streaming", [True, False])
def test_memory_import_requires_explicit_safe_verdict(verdict, status, streaming, caplog):
    provider = (FakeProvider([{"type": "final", "text": verdict}])
                if streaming else SyncProvider(verdict))

    async def call(ep):
        try:
            return await _import(ep)
        finally:
            if streaming:
                assert provider.closed

    out, save, db = _run_conv("/conversations/{conv_id}/import-memory", provider, call)
    if status == 200:
        assert out == {"status": "success"}
        save.assert_called_once_with("7", "architecture notes")
    else:
        assert isinstance(out, HTTPException) and out.status_code == status
        save.assert_not_called()
        if status == 500:
            assert out.detail == "Hafıza güvenlik denetimi yapılamadı."
            assert any(record.levelname == "WARNING" and repr(verdict) in record.getMessage()
                       for record in caplog.records)
    db.add_message.assert_not_called()


@pytest.mark.parametrize("answer", ["", "   "])
@pytest.mark.parametrize("streaming", [True, False])
def test_project_analysis_empty_answer_never_saves(answer, streaming):
    provider = (FakeProvider([] if not answer else [{"type": "delta", "text": answer}])
                if streaming else SyncProvider(answer))
    out, save, db = _run_conv("/conversations/{conv_id}/analyze-project", provider, _analyze, _rag())
    assert isinstance(out, HTTPException) and out.status_code == 500
    assert "provider returned an empty answer" in out.detail
    save.assert_not_called()
    db.add_message.assert_not_called()
    if streaming:
        assert provider.closed


@pytest.mark.parametrize("answer", ["", "   "])
@pytest.mark.parametrize("branch", ["stream", "sync", "ollama"])
def test_analyze_empty_answer_returns_error_without_saving(answer, branch):
    provider = (FakeProvider([] if not answer else [{"type": "delta", "text": answer}])
                if branch == "stream" else SyncProvider(answer))
    result, db = _run_analyze(provider, "ollama" if branch == "ollama" else "subscription")
    assert result == {"intent": "ERROR", "ai_suggestion": "provider returned an empty answer",
                      "static_results": {"smells": []}}
    db.save_analysis.assert_not_called()
    if branch == "stream":
        assert provider.closed


@pytest.mark.parametrize("exit_kind", ["caller_close", "error", "exhaustion"])
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

    async def close(self):
        assert closed == ["stream"]
        closed.append("session")

    monkeypatch.setattr(agy_session.AgyStreamSession, "stream", stream)
    monkeypatch.setattr(agy_session.AgyStreamSession, "close", close)

    async def run():
        provider = AgyProvider(binary_name="agy-claude-sonnet-5-5")
        events = provider.analyze_code("hello", cwd=".")
        assert await anext(events) == {"type": "delta", "text": "part"}
        if exit_kind == "caller_close":
            await events.aclose()
        else:
            remaining = [event async for event in events]
            assert remaining == ([{"type": "error", "content": "failure"}] if exit_kind == "error"
                                 else [{"type": "final", "text": "answer"}])
        assert closed == ["stream", "session"]

    asyncio.run(run())
