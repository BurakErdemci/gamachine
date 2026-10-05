"""Measured agy 1.2.17 catalog, saved-model migration, and effort routing."""
import asyncio
import copy
import sqlite3
from unittest.mock import Mock, patch

import pytest

from agentic.chat_model import chat_model, turn_model
from providers.agy_provider import AgyProvider
from providers.cli_base import BaseCLIProvider
from providers.effort_caps import get_effort_caps


DISPLAY_NAMES = {
    "Gemini 3.8 Flash (High)", "Gemini 3.8 Flash (Medium)", "Gemini 3.8 Flash (Low)",
    "Gemini 3.7 Flash (High)", "Gemini 3.7 Flash (Medium)", "Gemini 3.7 Flash (Low)",
    "Gemini 3.6 Flash (High)", "Gemini 3.6 Flash (Medium)", "Gemini 3.6 Flash (Low)",
    "Gemini 3.1 Pro (High)", "Gemini 3.1 Pro (Low)",
    "Claude Opus 5.5 (Low)", "Claude Opus 5.5 (Medium)", "Claude Opus 5.5 (High)",
    "Claude Sonnet 5.5 (Low)", "Claude Sonnet 5.5 (Medium)", "Claude Sonnet 5.5 (High)",
    "GPT-OSS 120B (Medium)",
}
CLAUDE_MODELS = {
    "agy-claude-opus-5-5": "Claude Opus 5.5",
    "agy-claude-sonnet-5-5": "Claude Sonnet 5.5",
}
REPLACEMENTS = {
    "agy-claude-sonnet-4-6": "agy-claude-sonnet-5-5",
    "agy-claude-opus-4-6": "agy-claude-opus-5-5",
    "gemini-3.5-flash": "gemini-3.8-flash",
    "gemini-3.5-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.5-flash-low": "gemini-3.8-flash-low",
}


@pytest.fixture
def saved_config_db(monkeypatch):
    from database import DatabaseManager

    uri = "file:agy-model-config?mode=memory&cache=shared"
    connect = sqlite3.connect
    keeper = connect(uri, uri=True)
    keeper.execute(
        "CREATE TABLE ai_configs (user_id INTEGER PRIMARY KEY, provider_type TEXT, "
        "model_name TEXT, api_key TEXT, use_multi_agent INTEGER)"
    )
    monkeypatch.setattr(sqlite3, "connect", lambda path: connect(path, uri=True))
    db = DatabaseManager.__new__(DatabaseManager)
    db.db_path = uri
    try:
        yield db, keeper
    finally:
        keeper.close()


@pytest.mark.parametrize("old,new", REPLACEMENTS.items())
def test_default_subscription_config_normalizes_on_read_without_rewriting(saved_config_db, old, new):
    db, stored = saved_config_db
    db.save_ai_config(1, "subscription", old, "saved-key")

    assert db.get_ai_config(1) == ("subscription", new, "saved-key", True)
    assert stored.execute("SELECT model_name FROM ai_configs WHERE user_id = 1").fetchone() == (old,)


@pytest.mark.parametrize("provider,model", [
    *[("subscription", model) for model in REPLACEMENTS.values()],
    *[(provider, model) for provider in ("google", "anthropic", "ollama") for model in REPLACEMENTS],
    ("subscription", "claude-sonnet-4-6"), ("subscription", ""),
])
def test_default_config_preserves_current_and_non_subscription_models(saved_config_db, provider, model):
    db, _ = saved_config_db
    db.save_ai_config(1, provider, model, "saved-key")

    assert db.get_ai_config(1) == (provider, model, "saved-key", True)


def test_missing_default_config_is_preserved(saved_config_db):
    db, _ = saved_config_db
    assert db.get_ai_config(1) == ("subscription", "claude-sonnet-4-6", "", False)


@pytest.mark.parametrize("old,new", [
    (old, new) for old, new in REPLACEMENTS.items() if new in CLAUDE_MODELS
])
def test_analysis_uses_the_replacement_claude_display_name(saved_config_db, old, new):
    from providers.agy_session import AgyStreamSession
    from routes import analysis_routes
    from schemas import AnalysisRequest

    saved, _ = saved_config_db
    saved.save_ai_config(1, "subscription", old, "")
    db = Mock(wraps=saved)
    db.get_api_key.return_value = ""
    db.save_analysis = Mock()
    selected = []

    async def stream(self, message, *, model, thinking_level="auto", **kwargs):
        provider = AgyProvider(binary_name=model)
        provider._build_cmd(thinking_level=thinking_level)
        selected.append(provider._pending_agy_model)
        yield {"type": "text", "content": "analysis answer"}
        yield {"type": "response", "content": "analysis answer"}

    async def close(self, **kwargs):
        return None

    with patch.object(analysis_routes, "require_user", return_value=(1, {})), \
            patch.object(AgyProvider, "_agy_binary", return_value="fake-agy"), \
            patch.object(AgyStreamSession, "stream", stream), \
            patch.object(AgyStreamSession, "close", close):
        router = analysis_routes.create_analysis_router(db)
        analyze = next(route.endpoint for route in router.routes if route.path == "/analyze")
        result = asyncio.run(analyze(AnalysisRequest(user_id=1, code="public class Example { }"), "test-token"))

    assert selected == [f"{CLAUDE_MODELS[new]} (High)"]
    assert result["ai_suggestion"] == "analysis answer"
    db.save_analysis.assert_called_once()


def test_picker_and_map_match_the_measured_catalog(monkeypatch):
    from routes import config_routes

    # Keep the picker read entirely offline, including its local Ollama probe.
    monkeypatch.setattr(config_routes.urllib.request, "urlopen", Mock(side_effect=OSError("offline")))
    router = config_routes.create_config_router(Mock())
    models = asyncio.run(router.list_models(None))["subscription"]
    agy_models = [m for m in models if m["id"].startswith(("gemini-", "agy-"))]
    claude = {m["id"]: m["name"] for m in agy_models if m["id"].startswith("agy-claude-")}
    assert claude == CLAUDE_MODELS
    assert not set(REPLACEMENTS) & {m["id"] for m in models}
    assert not set(REPLACEMENTS) & BaseCLIProvider._AGY_MODEL_MAP.keys()
    assert set(BaseCLIProvider._AGY_MODEL_MAP.values()) <= DISPLAY_NAMES
    for model in agy_models:
        assert BaseCLIProvider._AGY_MODEL_MAP[model["id"]] in DISPLAY_NAMES


@pytest.mark.parametrize("model,name", CLAUDE_MODELS.items())
@pytest.mark.parametrize("level,tier", [
    ("low", "Low"), ("medium", "Medium"), ("high", "High"),
    ("auto", "High"), (None, "High"), ("none", "High"), ("unknown", "High"),
])
def test_claude_effort_selects_the_display_name(model, name, level, tier):
    provider = AgyProvider(binary_name=model)
    with patch.object(AgyProvider, "_agy_binary", return_value="fake-agy"):
        command = provider._build_cmd(thinking_level=level)
    assert provider._pending_agy_model == f"{name} ({tier})"
    assert provider._pending_agy_model in DISPLAY_NAMES
    assert "--model" not in command


@pytest.mark.parametrize("model", CLAUDE_MODELS)
def test_claude_caps_and_unselected_effort(model):
    assert get_effort_caps("subscription", model)["levels"] == ["auto", "low", "medium", "high"]
    provider = AgyProvider(binary_name=model)
    with patch.object(AgyProvider, "_agy_binary", return_value="fake-agy"):
        provider._build_cmd()
    assert provider._pending_agy_model == f"{CLAUDE_MODELS[model]} (High)"


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-3.8-flash-medium", "gemini-3.8-flash-low"])
def test_gemini_keeps_its_model_tier_without_an_effort_picker(model):
    assert get_effort_caps("subscription", model)["levels"] == ["auto"]
    provider = AgyProvider(binary_name=model)
    with patch.object(AgyProvider, "_agy_binary", return_value="fake-agy"):
        provider._build_cmd(thinking_level="low")
    assert provider._pending_agy_model == BaseCLIProvider._AGY_MODEL_MAP[model]


@pytest.mark.parametrize("old,new", REPLACEMENTS.items())
@pytest.mark.parametrize("source", ["conversation", "message", "config"])
def test_saved_subscription_models_are_resolved_before_a_turn(old, new, source):
    db = Mock()
    db.get_conversation_model.return_value = ("subscription", old) if source == "conversation" else None
    db.get_latest_message_agent.return_value = ("agy", old) if source == "message" else None
    db.get_ai_config.return_value = ("subscription", old, "", None)
    db.set_conversation_model.return_value = True
    assert chat_model(db, 1, 7) == {"provider_type": "subscription", "model_name": new}
    assert turn_model(db, 1, 7) == ("subscription", new)
    if source != "conversation":
        db.set_conversation_model.assert_called_once_with(7, "subscription", new, only_if_unset=True)


@pytest.mark.parametrize("provider,model", [
    ("google", "gemini-3.5-flash"), ("google", "gemini-3.5-flash-medium"),
    ("anthropic", "claude-sonnet-4-6"), ("subscription", "claude-sonnet-4-6"),
])
@pytest.mark.parametrize("source", ["conversation", "message", "config"])
def test_other_provider_models_are_preserved(provider, model, source):
    db = Mock()
    db.get_conversation_model.return_value = (provider, model) if source == "conversation" else None
    agent = "claude" if provider == "subscription" else f"api-{provider}"
    db.get_latest_message_agent.return_value = (agent, model) if source == "message" else None
    db.get_ai_config.return_value = (provider, model, "", None)
    assert turn_model(db, 1, 7) == (provider, model)


def test_a_concurrent_saved_model_winner_is_also_normalized():
    db = Mock()
    db.get_conversation_model.side_effect = [None, ("subscription", "agy-claude-opus-4-6")]
    db.get_latest_message_agent.return_value = None
    db.get_ai_config.return_value = ("subscription", "gemini-3.8-flash", "", None)
    db.set_conversation_model.return_value = False
    assert turn_model(db, 1, 7) == ("subscription", "agy-claude-opus-5-5")


@pytest.mark.parametrize("effort,thinking,expected", [("low", "high", "low"), (None, "medium", "medium"), (None, None, "auto")])
def test_runner_passes_the_chosen_effort(monkeypatch, effort, thinking, expected):
    from agentic.agent_runner import AgentRunner
    from providers import agy_session

    async def stream(message, **kwargs):
        assert kwargs["thinking_level"] == expected
        yield {"type": "done"}

    session = Mock(session_id=None, cwd=".")
    session.stream = stream
    monkeypatch.setattr(agy_session, "get_session", Mock(return_value=session))
    runner = AgentRunner(provider_type="subscription", api_key="", model_name="agy-claude-opus-5-5",
                         workspace_path=".", conversation_id=7, effort_level=effort, thinking_level=thinking)

    async def run():
        return [event async for event in runner._run_agy_session("hello")]

    events = asyncio.run(run())
    assert [event.type for event in events] == ["done"]


def test_one_shot_passes_effort_and_closes(monkeypatch):
    from providers import agy_session

    closed = []

    async def stream(self, message, **kwargs):
        assert kwargs["model"] == "agy-claude-sonnet-5-5"
        assert kwargs["thinking_level"] == "low"
        yield {"type": "response", "content": "answer"}

    async def close(self):
        closed.append(self.conversation_id)

    monkeypatch.setattr(agy_session.AgyStreamSession, "stream", stream)
    monkeypatch.setattr(agy_session.AgyStreamSession, "close", close)

    async def run():
        provider = AgyProvider(binary_name="agy-claude-sonnet-5-5")
        return [event async for event in provider.analyze_code("hello", thinking_level="low", cwd=".")]

    assert asyncio.run(run()) == [{"type": "final", "text": "answer"}]
    assert len(closed) == 1 and closed[0] < 0


@pytest.mark.parametrize("model,levels,names,spawn_count", [
    ("agy-claude-opus-5-5", ["low", "medium", "high", "auto", None, "unknown"],
     ["Claude Opus 5.5 (Low)", "Claude Opus 5.5 (Medium)", "Claude Opus 5.5 (High)"], 3),
    ("gemini-3.8-flash-medium", ["low", "high"], ["Gemini 3.8 Flash (Medium)"], 1),
])
def test_session_restarts_only_when_the_effective_display_name_changes(monkeypatch, model, levels, names, spawn_count):
    from providers import agy_provider, agy_session
    from tests.test_agy_stream_session import FakeProcess, SESSION_ID, TURNS

    processes = []
    commands = []
    set_model = Mock()

    async def spawn(*argv, **kwargs):
        commands.append(argv)
        turns = [copy.deepcopy(TURNS[0]) for _ in levels]
        for index, turn in enumerate(turns, 1):
            turn[-1]["result"]["num_turns"] = index
        process = FakeProcess(turns=turns)
        processes.append(process)
        return process

    monkeypatch.setattr(AgyProvider, "_agy_binary", Mock(return_value="fake-agy"))
    monkeypatch.setattr(AgyProvider, "_resolve_exec", staticmethod(lambda command: command))
    monkeypatch.setattr(AgyProvider, "_write_mcp_config", Mock())
    monkeypatch.setattr(AgyProvider, "_set_agy_model", set_model)
    monkeypatch.setattr(AgyProvider, "_write_step_gate", Mock(return_value=True))
    monkeypatch.setattr(AgyProvider, "_stream_instructions", Mock(return_value=""))
    monkeypatch.setattr(agy_provider, "write_gate_state", Mock())
    monkeypatch.setattr(agy_session, "_global_auto_mode", lambda: True)
    monkeypatch.setattr(agy_session.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(agy_session, "_SESSIONS", {})
    monkeypatch.setattr(agy_session, "_RESUME_IDS", {})

    async def run():
        monkeypatch.setattr(BaseCLIProvider, "_AGY_LOCK", asyncio.Lock())
        session = agy_session.get_session(7)
        try:
            async def collect(level):
                return [event async for event in session.stream("hello", model=model, thinking_level=level)]

            for level in levels:
                events = await asyncio.wait_for(collect(level), timeout=2)
                assert events[-1]["type"] == "done"
            assert session.model == model
            assert len(processes) == spawn_count
            assert [call.args[0] for call in set_model.call_args_list] == names
            for command in commands[1:]:
                assert command[command.index("--conversation") + 1] == SESSION_ID
            assert all(process.returncode is not None for process in processes[:-1])
        finally:
            await session.close()

    asyncio.run(run())
    assert all(process.returncode is not None for process in processes)
