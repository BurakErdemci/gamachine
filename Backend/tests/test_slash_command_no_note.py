"""A message that starts with "/" (/context, /usage) reaches the agent unchanged:
no Unity, reset or defer note in front of it, and the notes wait for the next
ordinary message."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentic import agent_runner as ar
from providers import codex_session
from providers.codex_provider import CodexProvider

from tests.test_stable_guide import CID, _prepare, _runner, _unity

NOTE = "Unity now: connected"


@pytest.mark.parametrize("text,expected", [
    ("/context", True), ("  /usage now", True), ("hello", False), ("hi /context", False), ("", False),
])
def test_slash_command_detection(text, expected):
    assert ar._is_slash_command(text) is expected


def _claude_harness(monkeypatch):
    from agentic import mailbox
    from providers import claude_sdk_session as sdk
    from providers.cli_base import BaseCLIProvider

    _unity(monkeypatch, connected=True, scene="Assets/Live.unity")
    messages = []

    async def stream(message):
        messages.append(message)
        yield {"type": "done"}

    monkeypatch.setattr(ar, "_remove_project_mcp_json", lambda *a: None)
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", lambda args: args)
    monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(mailbox, "claude_server_entry", lambda cid: None)
    monkeypatch.setattr(sdk, "_SESSIONS", {})
    monkeypatch.setattr(sdk, "get_session", lambda cid, **kw: SimpleNamespace(session_id=None, stream=stream))
    monkeypatch.setattr(ar, "_DEFERRED_REASONS", {})
    return sdk, messages


def test_claude_slash_turn_is_bare_and_the_reset_notice_waits(monkeypatch):
    sdk, messages = _claude_harness(monkeypatch)
    monkeypatch.setitem(sdk._RESET_KILLED, CID, ["build"])
    runner = _runner()

    def turn(text):
        _prepare(runner)

        async def run():
            return [e async for e in runner._run_claude_session(text)]

        asyncio.run(run())

    turn("/context")
    assert messages == ["/context"]
    assert sdk._RESET_KILLED.get(CID) == ["build"]
    turn("hello")
    assert messages[1].startswith(NOTE)
    assert "build" in messages[1]
    assert messages[1].endswith("hello")
    assert CID not in sdk._RESET_KILLED


def test_claude_slash_turn_leaves_a_deferred_rebuild_for_the_next_message(monkeypatch):
    from tests.test_session_rebuild_defer import Harness, _existing

    h = Harness(monkeypatch, _existing({"t1": "codex exec"}), ["agent guide changed"])
    note = "[Sistem] Ayar değişikliği (agent guide changed) arka plan işleri bitince uygulanacak."
    h.turn("/context")
    assert h.messages[0] == "/context"
    h.turn("hello")
    assert h.messages[1].startswith(note + "\n\n")
    assert h.messages[1].endswith("hello")


def test_codex_slash_message_reaches_the_session_unchanged(monkeypatch):
    _unity(monkeypatch, scene="Assets/Live.unity")
    sent = []
    monkeypatch.setattr(codex_session, "_SESSIONS", {})
    monkeypatch.setattr(codex_session.CodexSession, "close", AsyncMock())

    async def stream(session, message, image_paths=None):
        sent.append(message)
        yield {"type": "text", "content": "fake reply"}

    monkeypatch.setattr(codex_session.CodexSession, "stream", stream)
    monkeypatch.setattr(codex_session.CodexSession, "is_live", property(lambda self: True))
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", Mock(return_value=""))
    runner = ar.AgentRunner(
        provider_type="subscription", api_key="", model_name="gpt-5.6-codex",
        workspace_path=".", language="en", conversation_id=CID + 2, generation_mode="step")
    _prepare(runner)

    async def run(text):
        return [e async for e in runner._run_codex_session(text)]

    # The first turn carries the session guide, so the slash command comes second.
    asyncio.run(run("hello"))
    asyncio.run(run("/context"))
    asyncio.run(run("hello"))
    assert sent[1] == "/context"
    assert sent[2].startswith(NOTE)


def test_agy_slash_message_gets_no_note(monkeypatch):
    from providers import agy_session

    _unity(monkeypatch, scene="Assets/Live.unity")
    sent = []

    async def stream(message, **kwargs):
        sent.append(message)
        yield {"type": "done"}

    session = Mock(session_id=None, cwd=".")
    session.stream = stream
    monkeypatch.setattr(agy_session, "get_session", Mock(return_value=session))
    runner = _runner(cid=CID + 3)
    runner.model_name = "agy-claude-opus-5-5"
    _prepare(runner)

    async def run(text):
        return [e async for e in runner._run_agy_session(text)]

    asyncio.run(run("/context"))
    asyncio.run(run("hello"))
    assert sent[0] == "/context"
    assert sent[1].startswith(NOTE)


def test_oneshot_slash_message_gets_no_note(monkeypatch):
    from providers.oneshot_cli import _SESSIONS

    _unity(monkeypatch, scene="Assets/Live.unity")
    prompts = []

    class Provider:
        async def analyze_code(self, prompt, **kwargs):
            prompts.append(prompt)
            yield {"type": "final", "text": "ok"}

    provider = Provider()
    runner = _runner(cid=CID + 4)
    _prepare(runner)
    _SESSIONS.clear()

    async def run(text):
        with patch("ai_providers.AIProviderManager.get_provider", return_value=provider):
            return [e async for e in runner._run_oneshot_cli_session(text, "kimi")]

    asyncio.run(run("/context"))
    asyncio.run(run("hello"))
    _SESSIONS.clear()
    assert prompts[0] == "/context"
    assert prompts[1].startswith(NOTE)


def test_simple_path_slash_message_gets_no_note(monkeypatch):
    _unity(monkeypatch, scene="Assets/Live.unity")
    prompts = []

    class Provider:
        async def analyze_code_with_thinking(self, prompt, **kwargs):
            prompts.append(prompt)
            yield {"type": "final", "text": "ok"}

    runner = _runner(cid=CID + 5)
    _prepare(runner)

    async def run(text):
        with patch("ai_providers.AIProviderManager.get_provider", return_value=Provider()):
            return [e async for e in runner._run_simple(text)]

    asyncio.run(run("/context"))
    asyncio.run(run("hello"))
    assert prompts[0].endswith("[KULLANICI]\n/context")
    assert f"[KULLANICI]\n{NOTE}" in prompts[1]
