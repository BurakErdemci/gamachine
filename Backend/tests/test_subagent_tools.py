"""The agent_* MCP tools: thin HTTP callers of /subagents/* on both surfaces
(the unityai server for every CLI family, the gamachineAgents server for
Claude), the Claude entry and allow-list, and the guide line. The backend is
stubbed with httpx.MockTransport; `network_guard` fails a test on any other
connection."""
import asyncio
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import httpx
import pytest

import agent_guide
from agentic import subagents
from database import DatabaseManager
from tests.test_subagents import network_guard  # noqa: F401  (autouse fixture)
from unity_ai_mcp import agent_tools_server
from unity_ai_mcp import server as unityai
from unity_ai_mcp.tools import agent_tools

STUB_URL = "http://backend.stub"


@pytest.fixture
def backend(monkeypatch):
    """Every agent tool call lands here; replies come from `replies[path]`."""
    calls = []
    replies = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.url.params), body))
        status, payload = replies.get(request.url.path, (200, {"text": "OK " + request.url.path}))
        return httpx.Response(status, json=payload)

    real = httpx.AsyncClient

    def client(*a, **kw):
        return real(*a, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(agent_tools, "BACKEND_URL", STUB_URL)
    monkeypatch.setattr(agent_tools.httpx, "AsyncClient", client)
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", "42")
    return types.SimpleNamespace(calls=calls, replies=replies)


@pytest.fixture
def db(tmp_path):
    database = DatabaseManager(str(tmp_path / "tools.db"))
    subagents.configure(database)
    yield database
    subagents.reset()
    from agentic import cards
    cards.set_tagger(None)


def _tools(server):
    return {t.name: t for t in asyncio.run(server.list_tools())}


def test_both_surfaces_register_the_same_six_tools():
    assert agent_tools.TOOLS == subagents.TOOLS
    claude = _tools(agent_tools_server.create_server())
    assert set(claude) == set(subagents.TOOLS)
    assert set(subagents.TOOLS) <= set(_tools(unityai.create_server(".")))
    assert agent_tools_server.SERVER_NAME == subagents.CLAUDE_SERVER_NAME
    assert subagents.CLAUDE_TOOL_NAMES == {f"mcp__gamachineAgents__{t}" for t in subagents.TOOLS}


def test_tool_descriptions_carry_the_rules():
    tools = _tools(agent_tools_server.create_server())
    start = tools["agent_start"].description
    for rule in ("Depth 1", "no count limit", "ask the user", "do not poll agent_list"):
        assert rule in start, rule
    assert "next turn" in tools["agent_message"].description
    assert "do not call this in a loop" in tools["agent_list"].description
    params = tools["agent_start"].input_schema["properties"]
    assert set(params) == {"model", "brief", "effort", "name"}


def test_tools_carry_the_call_with_the_spawned_chat(backend):
    asyncio.run(agent_tools.agent_start("gpt-6-luna", "say files", "high", "n1"))
    asyncio.run(agent_tools.agent_message(7, "more"))
    asyncio.run(agent_tools.agent_stop(7))
    asyncio.run(agent_tools.agent_list())
    asyncio.run(agent_tools.agent_result(7))
    asyncio.run(agent_tools.agent_models())
    assert backend.calls == [
        ("POST", "/subagents/start", {}, {"conversation_id": 42, "model": "gpt-6-luna",
                                          "brief": "say files", "effort": "high", "name": "n1"}),
        ("POST", "/subagents/message", {}, {"conversation_id": 42, "agent_id": 7, "text": "more"}),
        ("POST", "/subagents/stop", {}, {"conversation_id": 42, "agent_id": 7}),
        ("GET", "/subagents/list", {"conversation_id": "42"}, None),
        ("GET", "/subagents/result", {"conversation_id": "42", "agent_id": "7"}, None),
        ("GET", "/subagents/models", {"conversation_id": "42"}, None),
    ]


def test_tools_return_the_backends_words_and_refusals(backend, monkeypatch):
    backend.replies["/subagents/start"] = (409, {"detail": {"message": "codex is not ready (login)",
                                                            "code": "not_ready"}})
    backend.replies["/subagents/list"] = (200, {"text": "#1 \"a\" gpt: done"})
    assert asyncio.run(agent_tools.agent_start("gpt-6-luna", "b")) == \
        "Refused: codex is not ready (login)"
    assert asyncio.run(agent_tools.agent_list()) == "#1 \"a\" gpt: done"
    monkeypatch.delenv("GAMACHINE_CONVERSATION_ID")
    assert "inside a Gamachine chat" in asyncio.run(agent_tools.agent_list())
    assert len(backend.calls) == 2



def test_a_read_only_turn_cannot_start_or_steer_a_helper(backend, monkeypatch):
    monkeypatch.setenv("GAMACHINE_READ_ONLY", "1")
    assert "Read-only" in asyncio.run(agent_tools.agent_start("gpt-6-luna", "b"))
    assert "Read-only" in asyncio.run(agent_tools.agent_message(1, "go on"))
    assert backend.calls == []

def test_an_unreachable_backend_is_said_plainly(monkeypatch):
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", "42")

    def failing(*a, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(agent_tools.httpx, "AsyncClient", failing)
    assert asyncio.run(agent_tools.agent_list()) == agent_tools._UNREACHABLE


def test_the_claude_entry_is_for_ordinary_chats_only(db, monkeypatch):
    main = db.create_conversation(1, "Ana")
    entry = subagents.claude_server_entry(main)
    assert entry["type"] == "stdio" and entry["args"][-2:] == ["mcp-server", "--agents-only"]
    assert entry["env"]["GAMACHINE_CONVERSATION_ID"] == str(main)
    assert entry["args"][0].endswith("main.py") and Path(entry["args"][0]).is_file()
    assert entry == subagents.claude_server_entry(main), "must not change between turns"
    row = db.create_subagent(main, 1, "a", "subscription", "gpt-6-luna", "auto", "running")
    side = db.create_side_chat(main, 1)
    for cid in (row["child_conv"], side, 999999, None, 0):
        assert subagents.claude_server_entry(cid) is None, cid
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert subagents.agents_server_argv() == [sys.executable, "mcp-server", "--agents-only"]


def test_the_mcp_server_subcommand_starts_the_agents_server_with_its_flag(monkeypatch):
    started = []

    class _Fake:
        def __init__(self, name):
            self.name = name

        def run(self, transport):
            started.append((self.name, transport))

    monkeypatch.setattr(unityai, "_redirect_stderr_to_log", lambda: None)
    monkeypatch.setattr(agent_tools_server, "create_server", lambda: _Fake("agents"))
    monkeypatch.setattr(unityai, "create_server", lambda: _Fake("unityai"))
    monkeypatch.setattr("sys.argv", ["backend", "--agents-only"])
    unityai.main()
    monkeypatch.setattr("sys.argv", ["backend"])
    unityai.main()
    assert started == [("agents", "stdio"), ("unityai", "stdio")]


@pytest.mark.parametrize("mode", ["auto", "balanced"])
def test_claude_allows_the_agent_tools_except_in_read_only(monkeypatch, tmp_path, mode):
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    from agentic import approval_mode
    from providers.claude_sdk_session import ClaudeSDKSession
    monkeypatch.setattr(approval_mode, "current_mode", lambda: mode)
    live = ClaudeSDKSession(77, cwd=str(tmp_path), auto_approve=(mode == "auto"))
    side = ClaudeSDKSession(78, cwd=str(tmp_path), auto_approve=True, read_only=True)
    for name in sorted(subagents.CLAUDE_TOOL_NAMES):
        assert isinstance(asyncio.run(live._can_use_tool(name, {}, None)), PermissionResultAllow)
        assert isinstance(asyncio.run(side._can_use_tool(name, {}, None)), PermissionResultDeny)


def _claude_session(monkeypatch, tmp_path, conversation_id, read_only=False):
    from agentic import agent_runner
    from providers import claude_sdk_session
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    captured = []

    class _Sess:
        session_id = None
        auto_approve = False

        def __init__(self, kw):
            captured.append(kw)

        async def stream(self, _m):
            return
            yield  # pragma: no cover

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda host="localhost": None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(claude_sdk_session, "get_session", lambda cid, **kw: _Sess(kw))
    runner = agent_runner.AgentRunner(provider_type="subscription", api_key="", model_name="claude-x",
                                      workspace_path=str(tmp_path), conversation_id=conversation_id,
                                      generation_mode="auto", read_only=read_only)
    runner._agent_guide = agent_guide.compose("en", project_open=True, unity_state="off")

    async def run():
        async for _ in runner._run_claude_session("hi"):
            pass

    asyncio.run(run())
    return captured[0]


def test_a_claude_main_agent_gets_the_server_and_the_guide_line_a_child_gets_neither(
        db, monkeypatch, tmp_path):
    main = db.create_conversation(1, "Ana")
    child = db.create_subagent(main, 1, "a", "subscription", "claude-x", "auto", "running")["child_conv"]
    kw = _claude_session(monkeypatch, tmp_path, main)
    assert subagents.CLAUDE_SERVER_NAME in kw["mcp_servers"]
    assert agent_guide.SUBAGENTS_LINE_EN in kw["agent_guide"]
    for cid, read_only in ((child, False), (main, True)):
        kw = _claude_session(monkeypatch, tmp_path, cid, read_only)
        assert subagents.CLAUDE_SERVER_NAME not in kw["mcp_servers"], (cid, read_only)
        assert agent_guide.SUBAGENTS_LINE_EN not in kw["agent_guide"], (cid, read_only)


def test_the_guide_line_is_one_line_in_the_guides_language():
    for lang, line in (("en", agent_guide.SUBAGENTS_LINE_EN), ("tr", agent_guide.SUBAGENTS_LINE_TR)):
        guide = agent_guide.compose(lang, project_open=True, unity_state="off", addendum="EK")
        added = agent_guide.with_subagents(guide)
        assert added.count(line) == 1 and "\n" not in line
        assert added.index(line) < added.index("EK")
        for word in ("agent_start", "worktree"):
            assert word in line


def test_the_harness_note_answers_sub_agent_questions():
    import harness_notes
    assert harness_notes.search("alt ajan")[0]["id"] == "subagents"
    assert harness_notes.search("sub-agent")[0]["id"] == "subagents"
    note = harness_notes.read("subagents")["content"]
    for fact in ("Depth 1", "Fuse", "10", "gamachineAgents", "interrupted", "quota exhausted"):
        assert fact in note, fact
