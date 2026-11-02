"""The Blender guide block reaches a chat only when its run registers the connectors server."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import agent_guide as guide
from agentic import agent_runner as ar
from connectors import registration
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

CHAT = 987654
ROOT = "C:/gm/connectors"


def runner(language, **kwargs):
    current = ar.AgentRunner(
        provider_type="subscription", api_key="", model_name="gpt-6-sol",
        workspace_path=".", language=language, conversation_id=CHAT,
        generation_mode="step", thinking_level="off", effort_level="off", **kwargs)
    current._agent_guide = guide.compose(
        language, project_open=True, unity_state="connected", addendum="CUSTOM_RULE")
    return current


async def collect(events):
    return [event async for event in events]


@pytest.fixture
def ready(monkeypatch):
    """Which connectors are switched on and installed; nothing else is."""
    state = {"ids": []}
    monkeypatch.setattr("connectors.launcher.ready_ids", lambda db=None: list(state["ids"]))
    monkeypatch.setattr("connectors.launcher.installer", lambda: SimpleNamespace(root=ROOT))
    monkeypatch.setattr("computer_use.settings.enabled_now", lambda: False)
    monkeypatch.setattr("browser_use.settings.enabled_now", lambda: False)
    return state


@pytest.mark.parametrize("language,heading,addendum", [("en", "## Blender\n", "User addendum ("),
                                                       ("tr", "## Blender\n", "Kullanıcı eki (")])
def test_block_sits_before_the_user_addendum_in_the_guide_language(language, heading, addendum):
    original = guide.compose(language, project_open=True, unity_state="off", addendum="CUSTOM_RULE")
    changed = guide.with_connectors(original)
    block = guide.CONNECTORS_GUIDE_EN if language == "en" else guide.CONNECTORS_GUIDE_TR
    assert changed.count(block) == 1 and block.startswith(heading)
    assert changed.index(block) < changed.index(addendum)
    assert changed.replace("\n\n" + block, "", 1) == original


@pytest.mark.parametrize("block", [guide.CONNECTORS_GUIDE_EN, guide.CONNECTORS_GUIDE_TR])
def test_block_names_the_exposed_tools_and_the_rules(block):
    for name in ("blender_open", "blender_execute_blender_code", "blender_look", "blender_get_scene_info",
                 ".blend"):
        assert name in block
    for banned in ("generate_3d", "search_assets", "import_asset", "telemetry"):
        assert banned not in block


@pytest.mark.parametrize("block,word", [(guide.CONNECTORS_GUIDE_EN, "approval"),
                                        (guide.CONNECTORS_GUIDE_TR, "onay")])
def test_block_says_risky_blender_code_may_need_approval(block, word):
    assert word in block and "bpy.ops.wm.*" in block


def test_browser_block_is_unchanged_by_the_shared_helper():
    original = guide.compose("en", project_open=True, unity_state="off", addendum="CUSTOM_RULE")
    changed = guide.with_browser_use(original)
    assert changed.count(guide.BROWSER_GUIDE_EN) == 1
    assert changed.index(guide.BROWSER_GUIDE_EN) < changed.index("User addendum (")


@pytest.mark.parametrize("on", [False, True])
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("valid_conversation", [False, True])
def test_claude_session_gets_server_and_block_together(monkeypatch, ready, on, read_only, valid_conversation):
    from agentic import mailbox
    from providers import claude_sdk_session as sdk
    from providers.cli_base import BaseCLIProvider

    ready["ids"] = ["blender"] if on else []
    current = runner("en", read_only=read_only)
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

    asyncio.run(collect(current._run_claude_session("hello")))
    registered = on and not read_only and valid_conversation
    servers = captured[0]["mcp_servers"]
    assert (registration.SERVER_NAME in servers) is registered
    if registered:
        env = servers[registration.SERVER_NAME]["env"]
        assert env["GAMACHINE_CONNECTORS"] == "blender" and env["GAMACHINE_CONNECTORS_ROOT"] == ROOT
        assert env["GAMACHINE_CONVERSATION_ID"] == str(CHAT)
    expected = guide.with_connectors(original) if registered else original
    assert captured[0]["agent_guide"] == expected
    assert rebuild.call_args.kwargs["agent_guide"] == expected


@pytest.mark.parametrize("read_only", [False, True])
def test_claude_connector_tool_permission_defers_to_the_gate_except_read_only(read_only):
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    from providers.claude_sdk_session import ClaudeSDKSession

    async def run():
        session = ClaudeSDKSession(conversation_id=4343, cwd=".", auto_approve=False,
                                   approval_timeout=1.0, read_only=read_only)
        session._out_q = asyncio.Queue()
        args = {"code": "print(1)"}
        result = await session._can_use_tool(
            registration.CLAUDE_TOOL_PREFIX + "blender_execute_blender_code", args, None)
        assert session._out_q.empty()
        assert isinstance(result, PermissionResultDeny if read_only else PermissionResultAllow)

    assert registration.CLAUDE_TOOL_PREFIX == "mcp__gamachineConnectors__"
    asyncio.run(run())


@pytest.mark.parametrize("read_only", [False, True])
def test_codex_guide_flag_and_rebuild_follow_the_connectors_set(monkeypatch, ready, read_only):
    from providers import codex_session
    from providers.codex_provider import CodexProvider

    ready["ids"] = ["blender"]
    current = runner("en", read_only=read_only)
    original = current._agent_guide
    messages, options, closed = [], [], []
    sessions = {}

    def get_session(cid, **kwargs):
        options.append(kwargs)
        if cid not in sessions:
            session = SimpleNamespace(_ctx_injected=False, is_live=False, **kwargs)

            async def stream(message, **kw):
                messages.append(message)
                session.is_live = True
                yield {"type": "done"}
            session.stream = stream
            sessions[cid] = session
        return sessions[cid]

    async def close_session(cid):
        closed.append(cid)
        sessions.pop(cid, None)

    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(codex_session, "_SESSIONS", sessions)
    monkeypatch.setattr(codex_session, "get_session", get_session)
    monkeypatch.setattr(codex_session, "close_session", close_session)
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", lambda *args: None)

    async def scenario():
        await collect(current._run_codex_session("first"))
        await collect(current._run_codex_session("second"))
        ready["ids"] = []
        await collect(current._run_codex_session("switched off"))

    asyncio.run(scenario())
    on = (("blender",), ROOT)
    if read_only:
        assert [o["connectors"] for o in options] == [((), "")] * 3
        assert closed == [] and messages[0] == original + "\n\nfirst"
    else:
        assert [o["connectors"] for o in options] == [on, on, ((), "")]
        assert closed == [CHAT]
        assert messages == [guide.with_connectors(original) + "\n\nfirst", "second",
                            original + "\n\nswitched off"]


def test_codex_thread_config_adds_connectors_unless_read_only(monkeypatch, ready):
    from providers import codex_session
    monkeypatch.setattr(codex_session, "_configured_codex_mcp_names", lambda: set())
    ready["ids"] = ["blender"]
    assert registration.SERVER_NAME in codex_session._trusted_mcp_config(["X"], read_only=False)["mcp_servers"]
    assert registration.SERVER_NAME not in codex_session._trusted_mcp_config(["X"], read_only=True)["mcp_servers"]
    ready["ids"] = []
    assert registration.SERVER_NAME not in codex_session._trusted_mcp_config(["X"], read_only=False)["mcp_servers"]


@pytest.mark.parametrize("on", [False, True])
def test_agy_guide_follows_the_connectors_entry(monkeypatch, ready, on):
    from providers import agy_session
    ready["ids"] = ["blender"] if on else []
    assert agy_session._switch_state() == (False, False, on)
