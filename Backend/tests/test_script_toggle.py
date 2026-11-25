"""Settings > Agent > Script tool (app setting `script_tool_enabled`, on when
unset): its route, and that off removes run_script and its guide line on the
Claude path, the unityai server's tool list and a CLI path's guide. No real
agent, CLI or network: the Claude client and the Codex session are fakes and
`network_guard` fails a test on any connection it did not stub."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import agent_guide
import spawn_env
from database import DatabaseManager
from tests.test_subagents import network_guard  # noqa: F401  (autouse fixture)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A temporary settings database bound as the app's, restored afterwards."""
    db = DatabaseManager(str(tmp_path / "settings.db"))
    monkeypatch.setattr(agent_guide, "_settings_store", db)
    return db


def _route(router, path, method):
    return next(r.endpoint for r in router.routes
                if getattr(r, "path", "") == path and method in r.methods)


def test_the_setting_route_defaults_on_and_stores_off(store, monkeypatch):
    from routes import config_routes
    router = config_routes.create_config_router(store)
    get = _route(router, "/agent-settings/script-tool", "GET")
    put = _route(router, "/agent-settings/script-tool", "PUT")
    assert asyncio.run(get(x_session_token="")) == {"enabled": True}
    assert asyncio.run(put(body={"enabled": False}, x_session_token="")) == {"enabled": False}
    assert store.get_setting("script_tool_enabled") == "0"
    assert asyncio.run(get(x_session_token="")) == {"enabled": False}
    for bad in ({"enabled": "no"}, {}, ["enabled"]):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(put(body=bad, x_session_token=""))
        assert exc.value.status_code == 400
    assert asyncio.run(put(body={"enabled": True}, x_session_token="")) == {"enabled": True}


def test_unset_or_unreadable_means_on(monkeypatch):
    monkeypatch.setattr(agent_guide, "_settings_store", None)
    assert agent_guide.script_tool_enabled() is True

    class Broken:
        def get_setting(self, key):
            raise RuntimeError("db gone")

    monkeypatch.setattr(agent_guide, "_settings_store", Broken())
    assert agent_guide.script_tool_enabled() is True


# ── the Claude path ──────────────────────────────────────────────────────────
def _claude_start(monkeypatch, tmp_path):
    import claude_agent_sdk
    from providers import claude_sdk_session as sdk
    options = []

    class Client:
        def __init__(self, *, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    def make_options(**kwargs):
        options.append(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)
    monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions", make_options)
    monkeypatch.setattr(sdk, "claude_ikilisini_coz", lambda: None)

    async def idle(self):
        return None

    monkeypatch.setattr(sdk.ClaudeSDKSession, "_reader_loop", idle)
    guide = agent_guide.compose("en", project_open=True, unity_state="connected")
    servers = {"gamachineHarness": {"type": "stdio", "command": "x", "args": []}}

    async def scenario():
        session = sdk.ClaudeSDKSession(9, cwd=str(tmp_path), agent_guide=guide, mcp_servers=servers)
        try:
            await session.start()
        finally:
            await session.close()

    asyncio.run(scenario())
    return options[0]


@pytest.mark.parametrize("enabled", [True, False])
def test_claude_session_mounts_run_script_and_its_line_only_when_on(store, monkeypatch, tmp_path,
                                                                    enabled):
    from unity_ai_mcp import script_server
    agent_guide.set_script_tool_enabled(enabled)
    opts = _claude_start(monkeypatch, tmp_path)
    assert (script_server.SERVER_NAME in opts["mcp_servers"]) is enabled
    assert "gamachineHarness" in opts["mcp_servers"]
    assert (agent_guide.SCRIPT_TOOL_LINE_EN in opts["system_prompt"]["append"]) is enabled


# ── the unityai server and the CLI env ───────────────────────────────────────
@pytest.mark.parametrize("flag,listed", [(None, True), ("1", True), ("0", False)])
def test_the_unityai_server_registers_run_script_unless_the_env_says_off(monkeypatch, flag, listed):
    from unity_ai_mcp import server
    if flag is None:
        monkeypatch.delenv(spawn_env.SCRIPT_TOOL_ENV, raising=False)
    else:
        monkeypatch.setenv(spawn_env.SCRIPT_TOOL_ENV, flag)
    names = {t.name for t in asyncio.run(server.create_server(".").list_tools())}
    assert ("run_script" in names) is listed
    assert {"save_file", "agent_list"} <= names


def test_off_reaches_every_cli_child_through_the_chat_env(store):
    assert spawn_env.conversation_env(5) == {spawn_env.CONVERSATION_ENV: "5"}
    agent_guide.set_script_tool_enabled(False)
    assert spawn_env.conversation_env(5) == {spawn_env.CONVERSATION_ENV: "5",
                                             spawn_env.SCRIPT_TOOL_ENV: "0"}
    # One-shot calls (no chat id) carry the switch too, never a chat id.
    assert spawn_env.conversation_env(0) == {spawn_env.SCRIPT_TOOL_ENV: "0"}
    # The one-shot CLIs' spawn env (cli_base merges conversation_env into it).
    env = spawn_env.build_spawn_env("cursor", overrides=spawn_env.conversation_env(5))
    assert env[spawn_env.SCRIPT_TOOL_ENV] == "0"


# ── a CLI path's guide ───────────────────────────────────────────────────────
@pytest.mark.parametrize("enabled", [True, False])
def test_the_codex_guide_has_the_run_script_line_only_when_on(store, monkeypatch, enabled):
    from agentic import agent_runner as ar
    from providers import codex_session
    from providers.codex_provider import CodexProvider
    agent_guide.set_script_tool_enabled(enabled)
    guide = agent_guide.compose("en", project_open=True, unity_state="connected")
    runner = ar.AgentRunner(provider_type="subscription", api_key="", model_name="gpt-6-sol",
                            workspace_path=".", language="en", conversation_id=987654,
                            generation_mode="step", thinking_level="off", effort_level="off")
    runner._agent_guide = guide
    messages = []
    session = SimpleNamespace(_ctx_injected=False, is_live=False)

    async def stream(message, **kwargs):
        messages.append(message)
        session.is_live = True
        yield {"type": "done"}

    session.stream = stream
    monkeypatch.setattr(codex_session, "_SESSIONS", {})
    monkeypatch.setattr(codex_session, "get_session", lambda *args, **kwargs: session)
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", lambda *args: None)
    # Its probe would reach the Unity MCP port, the owner's live app.
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)

    async def scenario():
        return [e async for e in runner._run_codex_session("first")]

    asyncio.run(scenario())
    assert (agent_guide.SCRIPT_TOOL_LINE_EN in messages[0]) is enabled
    assert "[GAMACHINE AGENT GUIDE]" in messages[0]


@pytest.mark.parametrize("enabled", [True, False])
def test_the_agy_guide_has_the_run_script_line_only_when_on(store, enabled):
    from unittest.mock import patch
    from providers.agy_provider import AgyProvider
    from tests import test_agy_stream_session as fixture
    agent_guide.set_script_tool_enabled(enabled)
    guide = agent_guide.compose("en", project_open=True, unity_state="connected")

    async def scenario():
        harness = fixture.TestAgyStreamSession()
        await harness.asyncSetUp()
        try:
            session = fixture.agy_session.get_session(11)
            session.agent_guide = guide
            with patch.object(AgyProvider, "_stream_instructions", return_value="HINT\n"):
                await harness.collect(session=session, message="first")
            import json
            return json.loads(harness.processes[0].stdin.lines[0])["message"]["content"]
        finally:
            await harness.asyncTearDown()

    first = asyncio.run(scenario())
    assert (agent_guide.SCRIPT_TOOL_LINE_EN in first) is enabled
    assert first.endswith("HINT\nfirst")


# ── turn 2: every spawn path and the harness note ────────────────────────────
def test_every_spawned_child_carries_the_switch_while_off(store):
    assert spawn_env.SCRIPT_TOOL_ENV not in spawn_env.build_spawn_env("opencode")
    agent_guide.set_script_tool_enabled(False)
    for family in (None, "opencode", "kimi", "agy", "claude", "codex"):
        assert spawn_env.build_spawn_env(family)[spawn_env.SCRIPT_TOOL_ENV] == "0", family
    agent_guide.set_script_tool_enabled(True)
    assert spawn_env.SCRIPT_TOOL_ENV not in spawn_env.build_spawn_env("kimi")


@pytest.mark.parametrize("enabled", [True, False])
def test_the_one_shot_and_cursor_unityai_entries_carry_the_switch(store, monkeypatch, tmp_path, enabled):
    from providers import cursor_provider
    from providers.cli_base import BaseCLIProvider
    from providers.cursor_provider import CursorProvider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    agent_guide.set_script_tool_enabled(enabled)
    expected = {} if enabled else {spawn_env.SCRIPT_TOOL_ENV: "0"}

    servers = BaseCLIProvider._product_mcp_servers_stdio(
        SimpleNamespace(), "launcher", str(tmp_path), "http://backend.test")
    env = servers["unityai"]["env"]
    assert {k: v for k, v in env.items() if k == spawn_env.SCRIPT_TOOL_ENV} == expected

    written = {}
    provider = CursorProvider.__new__(CursorProvider)
    provider._cursor_home = None
    monkeypatch.setattr(CursorProvider, "_prepare_cursor_home", lambda self: str(tmp_path / "home"))
    monkeypatch.setattr(CursorProvider, "_write_cli_policy", lambda self, ws: True)
    monkeypatch.setattr(CursorProvider, "_apply_computer_servers", lambda self, *a, **k: [])
    monkeypatch.setattr(cursor_provider, "read_user_json_config", lambda ws, rel: {})
    monkeypatch.setattr(cursor_provider, "ensure_gitignored", lambda *a: None)

    def capture(ws, rel, text):
        written[rel] = __import__("json").loads(text)
        return True

    monkeypatch.setattr(cursor_provider, "guvenli_config_yaz", capture)
    assert provider._register_mcp("launcher", str(tmp_path), "http://backend.test")
    env = written[".cursor/mcp.json"]["mcpServers"]["unityai"]["env"]
    assert {k: v for k, v in env.items() if k == spawn_env.SCRIPT_TOOL_ENV} == expected


@pytest.mark.parametrize("off_by", ["setting", "env"])
def test_the_script_tool_note_is_not_offered_while_off(store, monkeypatch, off_by):
    import harness_notes
    assert harness_notes.search("run_script")[0]["id"] == "script-tool"
    assert harness_notes.read("script-tool")["success"] is True
    if off_by == "setting":   # the backend (API loop tools)
        agent_guide.set_script_tool_enabled(False)
    else:                     # a server process the spawner marked
        monkeypatch.setenv(spawn_env.SCRIPT_TOOL_ENV, "0")
    assert "script-tool" not in [r["id"] for r in harness_notes.search("run_script javascript")]
    got = harness_notes.read("script-tool")
    assert got["success"] is False and "script-tool" not in got["known_ids"]
    assert harness_notes.read("subagents")["success"] is True
    assert "script-tool" in harness_notes.notes()   # the files stay; only the offer changes


def test_the_claude_harness_server_entry_carries_the_switch(store):
    import harness_notes
    assert spawn_env.SCRIPT_TOOL_ENV not in harness_notes.claude_server_entry(5)["env"]
    agent_guide.set_script_tool_enabled(False)
    assert harness_notes.claude_server_entry(5)["env"][spawn_env.SCRIPT_TOOL_ENV] == "0"
