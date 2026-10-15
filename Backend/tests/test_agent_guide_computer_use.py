"""Verify desktop guidance follows the tools delivered to each chat session."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import agent_guide as guide
from agentic import agent_runner as ar
from computer_use import registration, settings
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager


def runner(language, **kwargs):
    current = ar.AgentRunner(
        provider_type="subscription", api_key="", model_name="gpt-6-sol",
        workspace_path=".", language=language, conversation_id=987654,
        generation_mode="step", thinking_level="off", effort_level="off", **kwargs)
    current._agent_guide = guide.compose(
        language, project_open=True, unity_state="connected", addendum="CUSTOM_RULE")
    return current


async def collect(events):
    return [event async for event in events]


@pytest.fixture
def computer_flag(monkeypatch):
    values = {settings.ENABLED_KEY: "0"}
    monkeypatch.setattr(settings, "_store", SimpleNamespace(get_setting=values.get))
    return lambda enabled: values.__setitem__(settings.ENABLED_KEY, "1" if enabled else "0")


@pytest.mark.parametrize("language,old,new", [
    ("en", "- Do not send synthetic keyboard or mouse input to the user's desktop.",
     "- Touch the user's desktop only through the gamachineComputer tools; never send keyboard or mouse input any other way (shell, scripts, other tools)."),
    ("tr", "- Kullanıcının masaüstüne yapay klavye ya da fare girdisi gönderme.",
     "- Kullanıcının masaüstüne yalnız gamachineComputer araçlarıyla dokun; klavye ya da fare girdisini başka hiçbir yoldan (kabuk, betik, başka araç) gönderme."),
])
@pytest.mark.parametrize("project_open", [False, True])
@pytest.mark.parametrize("unity_state", ["connected", "off", "not_responding"])
def test_composed_guide_changes_only_the_desktop_line(language, old, new, project_open, unity_state):
    suffix = language.upper()
    assert getattr(guide, "DESKTOP_LINE_" + suffix) == old
    assert getattr(guide, "COMPUTER_USE_LINE_" + suffix) == new
    assert getattr(guide, "ALWAYS_" + suffix).count(old) == 1
    original = guide.compose(language, project_open=project_open, unity_state=unity_state,
                             addendum="CUSTOM_RULE")
    changed = guide.with_computer_use(original)
    assert old not in changed
    assert changed.count(new) == 1
    assert changed.replace(new, "") == original.replace(old, "")
    assert guide.with_computer_use(changed) == changed


@pytest.mark.parametrize("text", ["", "no guide here", "gamachineComputer"])
def test_guide_without_desktop_line_is_unchanged(text):
    assert guide.with_computer_use(text) == text


def test_only_the_first_desktop_line_is_replaced():
    # Later lines (a user addendum, in either language) keep their meaning.
    original = "\n".join([guide.DESKTOP_LINE_EN, guide.DESKTOP_LINE_TR] * 2)
    expected = "\n".join([guide.COMPUTER_USE_LINE_EN, guide.DESKTOP_LINE_TR,
                           guide.DESKTOP_LINE_EN, guide.DESKTOP_LINE_TR])
    assert guide.with_computer_use(original) == expected


@pytest.mark.parametrize("language", ["en", "tr"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("valid_conversation", [False, True])
def test_claude_session_and_rebuild_receive_the_same_guide(
        monkeypatch, computer_flag, language, enabled, read_only, valid_conversation):
    from agentic import mailbox
    from providers import claude_sdk_session as sdk
    from providers.cli_base import BaseCLIProvider

    computer_flag(enabled)
    current = runner(language, read_only=read_only)
    current.model_name = "claude-sonnet-5"
    if not valid_conversation:
        current.conversation_id = 0
    original = current._agent_guide
    captured = []

    async def stream(message):
        yield {"type": "done"}

    def get_session(cid, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(session_id=None, stream=stream)

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(ar, "_remove_project_mcp_json", lambda *args: None)
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", lambda args: args)
    monkeypatch.setattr(ar.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(mailbox, "claude_server_entry", lambda cid: None)
    monkeypatch.setattr(sdk, "_SESSIONS", {})
    monkeypatch.setattr(sdk, "get_session", get_session)
    rebuild = Mock(wraps=ar._oturum_yeniden_kurma_gerekceleri)
    monkeypatch.setattr(ar, "_oturum_yeniden_kurma_gerekceleri", rebuild)

    events = asyncio.run(collect(current._run_claude_session("hello")))
    assert any(event.type == "done" for event in events)
    assert len(captured) == 1
    receives_computer = enabled and not read_only and valid_conversation
    assert (registration.SERVER_NAME in captured[0]["mcp_servers"]) is receives_computer
    expected = guide.with_computer_use(original) if receives_computer else original
    assert captured[0]["agent_guide"] == expected
    rebuild.assert_called_once()
    assert rebuild.call_args.kwargs["agent_guide"] == expected
    assert current._agent_guide == original


@pytest.mark.parametrize("language", ["en", "tr"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("read_only", [False, True])
def test_codex_guide_follows_desired_computer_use_only_on_first_turn(
        monkeypatch, computer_flag, language, enabled, read_only):
    from providers import codex_session
    from providers.codex_provider import CodexProvider

    computer_flag(enabled)
    current = runner(language, read_only=read_only)
    original = current._agent_guide
    messages, options = [], []
    session = SimpleNamespace(_ctx_injected=False, is_live=False)

    async def stream(message, **kwargs):
        messages.append(message)
        session.is_live = True
        yield {"type": "done"}

    def get_session(cid, **kwargs):
        options.append(kwargs)
        return session

    session.stream = stream
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(codex_session, "_SESSIONS", {})
    monkeypatch.setattr(codex_session, "get_session", get_session)
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", lambda *args: None)

    async def scenario():
        await collect(current._run_codex_session("first"))
        await collect(current._run_codex_session("second"))
        session.is_live = False
        await collect(current._run_codex_session("new thread"))

    asyncio.run(scenario())
    desired = enabled and not read_only
    expected = guide.with_computer_use(original) if desired else original
    assert messages == [expected + "\n\nfirst", "second", expected + "\n\nnew thread"]
    assert all(option["computer_use"] is desired for option in options)
    assert current._agent_guide == original


@pytest.mark.parametrize("language", ["en", "tr"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("injected", [False, True])
def test_oneshot_provider_guide_ignores_computer_use_flag(
        monkeypatch, computer_flag, language, enabled, injected):
    computer_flag(enabled)
    monkeypatch.setattr(guide, "_settings_store", None)
    original = guide.compose(language, project_open=True, unity_state="off")
    provider = SimpleNamespace(_language=language)
    if injected:
        provider._agent_guide = original
    assert guide.provider_guide(provider, project_open=True, unity_running=False) == original + "\n\n"
