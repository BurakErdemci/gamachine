"""The per-turn live Unity note reaches every agent route that uses the guide.

The guide follows the Unity MCP toggle only; the live state (server down, bridge
dropped, open scene) travels as a note. Before this, only the Claude and Codex
turns carried it, so with the toggle on and the server down the API loop, the
simple run, agy and the one-shot CLIs claimed a connected editor and said nothing
else. Fakes only: no CLI is started and no socket is opened.

Not driven here: the Claude and Codex routes (covered by test_stable_guide.py).
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentic import agent_runner as ar
from unity_ai_mcp import unity_project_presence
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

CID = 987770
NOTE_MARK = "Unity now"


def _unity(monkeypatch, *, toggle=True):
    """Toggle set as given, server down; no socket is opened."""
    monkeypatch.setenv("GAMACHINE_NO_UNITY_MCP", "1")
    monkeypatch.setattr(unity_mcp_manager, "enabled", toggle)
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)

    async def check():
        return False

    monkeypatch.setattr(unity_mcp_manager, "check_unity_connected", check)
    monkeypatch.setattr(unity_project_presence, "active_scene", lambda ws: None)


def _runner(provider_type="subscription", model="gemini-test", **extra):
    return ar.AgentRunner(
        provider_type=provider_type, api_key="k", model_name=model, workspace_path=".",
        language="en", conversation_id=CID, generation_mode="step",
        thinking_level="off", effort_level="off", **extra)


def _prepared(monkeypatch, *, toggle=True, **kwargs):
    _unity(monkeypatch, toggle=toggle)
    runner = _runner(**kwargs)
    asyncio.run(runner._prepare_agent_guide())
    return runner


def test_note_is_computed_only_when_the_toggle_is_on(monkeypatch):
    assert NOTE_MARK in _prepared(monkeypatch)._unity_note
    assert _prepared(monkeypatch, toggle=False)._unity_note == ""


def test_api_system_text_carries_the_note_after_the_guide(monkeypatch):
    runner = _prepared(monkeypatch, provider_type="gemini")
    system = runner._api_system_instruction()
    assert runner._unity_note in system
    assert system.index(runner._agent_guide) < system.index(runner._unity_note)


def test_api_system_text_has_no_note_when_the_toggle_is_off(monkeypatch):
    runner = _prepared(monkeypatch, toggle=False, provider_type="gemini")
    assert NOTE_MARK not in runner._api_system_instruction()


def test_simple_run_message_carries_the_note(monkeypatch):
    import ai_providers
    seen = []

    class _Provider:
        async def analyze_code_with_thinking(self, prompt, **kwargs):
            seen.append(prompt)
            yield {"type": "final", "text": "ok"}

    monkeypatch.setattr(ai_providers.AIProviderManager, "get_provider",
                        staticmethod(lambda cfg: _Provider()))
    runner = _prepared(monkeypatch, model="x")
    runner.context = ""

    async def go():
        return [e async for e in runner._run_simple("do the thing")]

    asyncio.run(go())
    assert len(seen) == 1
    assert runner._unity_note in seen[0]
    assert seen[0].index(runner._unity_note) < seen[0].index("do the thing")


def test_agy_message_carries_the_note(monkeypatch):
    import providers.agy_session as agy_session
    seen = []

    class _Session:
        auto_approve = False
        active_provider = None
        session_id = None
        cwd = "."

        async def stream(self, message, **kwargs):
            seen.append(message)
            yield {"type": "done"}

    monkeypatch.setattr(agy_session, "get_session", lambda cid, **kwargs: _Session())
    runner = _prepared(monkeypatch)
    runner.context = ""

    async def go():
        return [e async for e in runner._run_agy_session("do the thing")]

    asyncio.run(go())
    assert len(seen) == 1
    assert seen[0].startswith(runner._unity_note)
    assert "do the thing" in seen[0]


@pytest.mark.parametrize("toggle", [True, False])
def test_oneshot_prompt_carries_the_note_only_when_the_toggle_is_on(monkeypatch, toggle):
    from providers.oneshot_cli import _SESSIONS
    seen = []

    class _Provider:
        resume_session_id = None

        async def analyze_code(self, prompt, **kwargs):
            seen.append(prompt)
            yield {"type": "final", "text": "ok"}

    _SESSIONS.clear()
    runner = _prepared(monkeypatch, toggle=toggle, model="opencode:opencode-go/kimi-k3")
    runner.context = ""

    async def go():
        return [e async for e in runner._run_oneshot_cli_session("do the thing", "opencode")]

    with patch("ai_providers.AIProviderManager.get_provider", return_value=_Provider()):
        asyncio.run(go())
    _SESSIONS.clear()
    assert len(seen) == 1
    if toggle:
        assert seen[0].startswith(runner._unity_note)
    else:
        assert NOTE_MARK not in seen[0]
