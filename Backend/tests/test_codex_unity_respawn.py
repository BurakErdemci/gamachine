"""Unity MCP changes rebuild the persistent Codex session without a CLI process."""
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentic.agent_runner import AgentRunner
from providers import codex_session
from providers.codex_provider import CodexProvider


@pytest.fixture
def harness(monkeypatch):
    manager = SimpleNamespace(is_running=Mock(return_value=False))
    package = ModuleType("unity_ai_mcp")
    module = ModuleType("unity_ai_mcp.unity_mcp_manager")
    module.unity_mcp_manager = manager
    package.unity_mcp_manager = module
    monkeypatch.setitem(sys.modules, "unity_ai_mcp", package)
    monkeypatch.setitem(sys.modules, "unity_ai_mcp.unity_mcp_manager", module)
    sessions, sent = {}, []
    monkeypatch.setattr(codex_session, "_SESSIONS", sessions)
    close = AsyncMock()
    monkeypatch.setattr(codex_session.CodexSession, "close", close)

    async def stream(session, message, image_paths=None):
        sent.append((session, message))
        yield {"type": "text", "content": "fake reply"}

    monkeypatch.setattr(codex_session.CodexSession, "stream", stream)
    write_config = Mock(return_value="")
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", write_config)
    runner = AgentRunner(
        provider_type="subscription", api_key="", model_name="gpt-5.6-codex",
        workspace_path=".", conversation_id=7305, generation_mode="step",
        context="initial context",
    )
    return SimpleNamespace(manager=manager, sessions=sessions, sent=sent,
                           close=close, write_config=write_config, runner=runner)


async def _turn(harness, message):
    events = [event async for event in harness.runner._run_codex_session(message)]
    assert [(event.type, event.data) for event in events] == [
        ("text", {"content": "fake reply"}),
    ]
    return harness.sessions[harness.runner.conversation_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("initial,desired,reason", [
    (False, True, "unityMCP açıldı"),
    (True, False, "unityMCP kapandı"),
])
async def test_unity_toggle_rebuilds_and_reinjects_context(harness, caplog, initial, desired, reason):
    caplog.set_level(logging.INFO, logger="agentic.agent_runner")
    harness.manager.is_running.return_value = initial
    old = await _turn(harness, "first")
    assert old.unity_mcp is initial
    harness.manager.is_running.return_value = desired
    harness.runner.context = "updated chat context"
    new = await _turn(harness, "next")
    assert new is not old
    assert new.unity_mcp is desired
    harness.close.assert_awaited_once()
    assert harness.write_config.call_count == 2
    assert harness.manager.is_running.call_count == 2
    assert reason in caplog.text
    assert "session yeniden kuruluyor" in caplog.text
    assert harness.sent[-1][0] is new
    assert "updated chat context" in harness.sent[-1][1]
    assert "initial context" not in harness.sent[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("running", [False, True])
async def test_unchanged_unity_reuses_session(harness, caplog, running):
    caplog.set_level(logging.INFO, logger="agentic.agent_runner")
    harness.manager.is_running.return_value = running
    old = await _turn(harness, "first")
    new = await _turn(harness, "next")
    assert new is old
    assert new.unity_mcp is running
    harness.close.assert_not_awaited()
    harness.write_config.assert_called_once()
    assert harness.manager.is_running.call_count == 2
    assert harness.sent[-1][1] == "next"
    assert "session yeniden kuruluyor" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [False, True])
async def test_unity_state_error_is_false_and_logged(harness, caplog, initial):
    caplog.set_level(logging.DEBUG, logger="agentic.agent_runner")
    harness.manager.is_running.return_value = initial
    old = await _turn(harness, "first")
    harness.manager.is_running.side_effect = RuntimeError("state probe failed")
    new = await _turn(harness, "next")
    assert new.unity_mcp is False
    assert (new is not old) is initial
    assert harness.close.await_count == int(initial)
    assert harness.write_config.call_count == 1 + int(initial)
    assert harness.manager.is_running.call_count == 2
    assert any(record.levelno == logging.DEBUG and "state probe failed" in record.message
               for record in caplog.records)


def test_session_defaults_to_unity_off():
    assert codex_session.CodexSession(7305).unity_mcp is False
