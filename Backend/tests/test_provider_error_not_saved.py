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


def _run_analyze(provider):
    db = Mock()
    db.get_ai_config.return_value = ("subscription", "m", "", False)
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
