"""Approval cards from the MCP poll know which conversation they belong to.

Parallel chats run at once. Cards raised by the unity-mcp server's gate and by
the `unityai` bridge reach the UI through `/mcp-pending`, and until now they
carried no owner: Stop in one chat denied every such card, and the UI could not
show a card in the chat that raised it.

The carriers now send a claimed `conversation_id`. The backend accepts it only
for a conversation that exists, is the local user's, and has a turn in flight
(counted in `AgentRunner.run` for every provider); anything else stays unowned,
because a card attributed to the wrong chat is worse than an unowned one.

Route tests call the endpoint functions directly against a fake db, like
test_auto_wake.py. No network, no provider, no real MCP server.
"""
import asyncio
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import agentic.agent_runner as ar  # noqa: E402  (before tools: import cycle)
from agentic import approval_policy  # noqa: E402
from agentic.approval_policy import ambient_turn, conversation_turn_in_flight  # noqa: E402
from agentic.command_gates import GATE_OWNERS  # noqa: E402
from routes.conversation_routes import create_conversation_router  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_turns():
    approval_policy._TURNS_BY_CONVERSATION.clear()
    yield
    approval_policy._TURNS_BY_CONVERSATION.clear()


def _db(owners=None):
    """get_conversation_owner: conversation id -> user id (None = no such chat)."""
    owners = {1: 1, 2: 1, 5: 1} if owners is None else owners
    db = MagicMock()
    db.get_conversation_owner.side_effect = lambda cid: owners.get(cid)
    return db


def _routes(db):
    router = create_conversation_router(db, MagicMock())
    return {getattr(r, "path", ""): r for r in router.routes}


def _card(gate_id, conversation_id="absent"):
    body = {"gate_id": gate_id, "tool": "manage_gameobject",
            "params": {"action": "create"}, "workspace_path": ""}
    if conversation_id != "absent":
        body["conversation_id"] = conversation_id
    return body


def _raise(routes, body):
    async def run():
        req = await routes["/mcp-approval-request"].endpoint(body=body, x_session_token="t")
        pending = await routes["/mcp-pending"].endpoint(x_session_token="t")
        return req, pending["pending"]
    return asyncio.run(run())


@pytest.fixture
def gates():
    """Gate ids a test opened; their owner entries are removed afterwards."""
    opened = []
    yield opened
    for gate_id in opened:
        GATE_OWNERS.pop(gate_id, None)


# ── the in-flight registry ───────────────────────────────────────────────────

def test_turn_registry_counts_per_conversation_and_nests():
    assert not conversation_turn_in_flight(5)
    with ambient_turn(".", "step", 5):
        assert conversation_turn_in_flight(5)
        with ambient_turn(".", "auto", 5):
            assert conversation_turn_in_flight(5)
        assert conversation_turn_in_flight(5), "the outer turn is still running"
        assert not conversation_turn_in_flight(6)
    assert not conversation_turn_in_flight(5)
    assert approval_policy._TURNS_BY_CONVERSATION == {}


def test_turn_registry_releases_on_exception():
    with pytest.raises(RuntimeError):
        with ambient_turn(".", "step", 5):
            raise RuntimeError("boom")
    assert not conversation_turn_in_flight(5)


@pytest.mark.parametrize("conversation_id", [None, 0, -1, "5", True])
def test_turn_without_a_usable_id_registers_nothing(conversation_id):
    with ambient_turn(".", "step", conversation_id):
        assert approval_policy._TURNS_BY_CONVERSATION == {}


def test_agent_runner_run_marks_its_conversation_in_flight_until_closed():
    """Every provider's turn goes through AgentRunner.run; closing the stream
    (Stop, disconnect) must end the in-flight mark, not only a clean finish."""
    runner = ar.AgentRunner(provider_type="subscription", api_key="", model_name="claude-x",
                            workspace_path=".", conversation_id=5, generation_mode="step")
    seen = []

    async def inner(_message):
        seen.append(conversation_turn_in_flight(5))
        yield ar.AgentEvent("status", {"detail": "one"})
        yield ar.AgentEvent("status", {"detail": "two"})

    runner._run_inner = inner

    async def run():
        gen = runner.run("hi")
        await gen.__anext__()
        during = conversation_turn_in_flight(5)
        await gen.aclose()
        return during

    assert asyncio.run(run()) is True
    assert seen == [True]
    assert not conversation_turn_in_flight(5)


# ── /mcp-approval-request: which claims are accepted ────────────────────────

def test_owner_accepted_for_an_existing_chat_with_a_turn_in_flight(gates):
    routes = _routes(_db())
    gates.append("own-ok")
    with ambient_turn(".", "step", 5):
        req, pending = _raise(routes, _card("own-ok", 5))
    assert req == {"status": "ok", "gate_id": "own-ok"}
    assert GATE_OWNERS["own-ok"] == 5
    assert pending["own-ok"]["conversation_id"] == 5
    # Every field the UI already reads is still there.
    assert pending["own-ok"]["tool"] == "manage_gameobject"
    assert pending["own-ok"]["params"] == {"action": "create"}
    assert pending["own-ok"]["workspace_path"] == ""


def test_no_turn_in_flight_means_unowned(gates):
    routes = _routes(_db())
    gates.append("idle")
    _req, pending = _raise(routes, _card("idle", 5))
    assert GATE_OWNERS.get("idle") is None
    assert pending["idle"]["conversation_id"] is None


def test_a_turn_in_ANOTHER_chat_does_not_vouch_for_this_claim(gates):
    routes = _routes(_db())
    gates.append("other-turn")
    with ambient_turn(".", "step", 2):
        _req, pending = _raise(routes, _card("other-turn", 5))
    assert GATE_OWNERS.get("other-turn") is None
    assert pending["other-turn"]["conversation_id"] is None


@pytest.mark.parametrize("owners", [
    {5: 2},         # someone else's
    {5: "1"},       # not the int the database returns
])
def test_missing_or_foreign_conversation_is_unowned_even_with_a_turn(gates, owners):
    routes = _routes(_db(owners))
    gates.append("foreign")
    with ambient_turn(".", "step", 5):
        _req, pending = _raise(routes, _card("foreign", 5))
    assert GATE_OWNERS.get("foreign") is None
    assert pending["foreign"]["conversation_id"] is None


def test_db_failure_is_unowned_not_an_error(gates):
    db = MagicMock()
    db.get_conversation_owner.side_effect = OSError("db locked")
    routes = _routes(db)
    gates.append("db-fail")
    with ambient_turn(".", "step", 5):
        req, pending = _raise(routes, _card("db-fail", 5))
    assert req["status"] == "ok"
    assert GATE_OWNERS.get("db-fail") is None
    assert pending["db-fail"]["conversation_id"] is None


@pytest.mark.parametrize("claimed", ["absent", None, "5", 5.0, True, 0, -5, [5]])
def test_junk_claims_are_unowned(gates, claimed):
    routes = _routes(_db())
    gates.append("junk")
    with ambient_turn(".", "step", 5), ambient_turn(".", "step", 1):
        _req, pending = _raise(routes, _card("junk", claimed))
    assert GATE_OWNERS.get("junk") is None
    assert pending["junk"]["conversation_id"] is None


def test_auto_mode_still_answers_before_any_owner_check():
    from agentic import approval_mode

    approval_mode.set_mode("auto", source="test")
    db = _db()
    routes = _routes(db)
    req, pending = _raise(routes, _card("auto-own", 5))
    assert req["status"] == "resolved" and req["approved"] is True
    assert pending == {}
    assert "auto-own" not in GATE_OWNERS
    # Only the existence check ran; no ownership verification in auto.
    db.get_conversation_owner.assert_called_once_with(5)


@pytest.mark.parametrize("mode", ["step", "auto"])
def test_a_claim_naming_a_missing_chat_is_refused_without_a_card(gates, mode):
    """A deleted chat's child can outlive its rows; ids are never reused, so a
    claim naming a chat with no row is refused, never shown or auto-approved."""
    from agentic import approval_mode

    approval_mode.set_mode(mode, source="test")
    try:
        routes = _routes(_db({}))
        gates.append("ghost")
        req, pending = _raise(routes, _card("ghost", 45678))
    finally:
        approval_mode.set_mode("step", source="test")
    assert req["status"] == "resolved" and req["approved"] is False
    assert pending == {}
    assert "ghost" not in GATE_OWNERS


# ── Stop, end to end through the routes ──────────────────────────────────────

def test_stop_in_A_denies_A_and_unowned_cards_but_not_B(gates):
    routes = _routes(_db())
    gates.extend(["a-card", "b-card", "nobody-card"])

    async def run():
        req = routes["/mcp-approval-request"].endpoint
        for body in (_card("a-card", 1), _card("b-card", 2), _card("nobody-card")):
            assert (await req(body=body, x_session_token="t"))["status"] == "ok"
        # Copied: called directly, the route returns its live dict.
        before = dict((await routes["/mcp-pending"].endpoint(x_session_token="t"))["pending"])
        stop = await routes["/chat-stop/{conversation_id}"].endpoint(
            conversation_id=1, x_session_token="t")
        after = (await routes["/mcp-pending"].endpoint(x_session_token="t"))["pending"]
        result = routes["/mcp-approval-result/{gate_id}"].endpoint
        results = {g: await result(gate_id=g, x_session_token="t")
                   for g in ("a-card", "b-card", "nobody-card")}
        return before, stop, after, results

    with ambient_turn(".", "step", 1), ambient_turn(".", "step", 2):
        before, stop, after, results = asyncio.run(run())

    assert {g: e["conversation_id"] for g, e in before.items()} == {
        "a-card": 1, "b-card": 2, "nobody-card": None}
    assert stop["status"] == "ok"
    assert set(after) == {"b-card"}
    assert after["b-card"]["conversation_id"] == 2
    assert results["a-card"] == {"status": "resolved", "approved": False}
    assert results["nobody-card"] == {"status": "resolved", "approved": False}
    assert results["b-card"] == {"status": "pending"}


# ── carriers ─────────────────────────────────────────────────────────────────

class _FakeClaudeSession:
    def __init__(self, captured, conversation_id, kwargs):
        captured.append((conversation_id, kwargs))
        self.session_id = None
        self.auto_approve = False

    async def stream(self, _message):
        return
        yield  # pragma: no cover - makes this an async generator


def _claude_session_kwargs(monkeypatch, tmp_path, conversation_id):
    import subprocess

    from providers import claude_sdk_session
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    captured = []
    monkeypatch.setattr(unity_mcp_manager, "mcp_url",
                        lambda host="localhost": "http://127.0.0.1:9/mcp/gamachine")
    monkeypatch.setattr(unity_mcp_manager, "api_headers", lambda: {"X-API-Key": "k"})
    # `claude mcp remove unityai` must not really run.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(claude_sdk_session, "get_session",
                        lambda cid, **kw: _FakeClaudeSession(captured, cid, kw))
    runner = ar.AgentRunner(provider_type="subscription", api_key="", model_name="claude-x",
                            workspace_path=str(tmp_path), conversation_id=conversation_id,
                            generation_mode="step")

    async def run():
        async for _ev in runner._run_claude_session("hi"):
            pass

    asyncio.run(run())
    assert captured, "get_session was never reached"
    return captured[0][1]


def test_claude_session_names_its_conversation_to_the_unity_mcp_server(monkeypatch, tmp_path):
    kwargs = _claude_session_kwargs(monkeypatch, tmp_path, 7)
    unity = kwargs["mcp_servers"]["unityMCP"]
    assert unity["headers"] == {"X-API-Key": "k", "X-Gamachine-Conversation": "7"}
    assert unity["type"] == "http"
    # The backend's own unityai server stays out of the Claude session.
    assert set(kwargs["mcp_servers"]) == {"unityMCP"}


@pytest.mark.parametrize("conversation_id", [None, 0])
def test_claude_session_without_a_conversation_sends_no_header(monkeypatch, tmp_path,
                                                               conversation_id):
    kwargs = _claude_session_kwargs(monkeypatch, tmp_path, conversation_id)
    assert kwargs["mcp_servers"]["unityMCP"]["headers"] == {"X-API-Key": "k"}


def _recording_client(calls):
    from tools import unity_mcp_tools as umt

    class _Client:
        def call_tool(self, name, params, timeout=umt.CALL_TIMEOUT_S, meta=None):
            calls.append((name, dict(params), meta))
            return types.SimpleNamespace(content=[], is_error=False)

    return _Client()


def test_cloud_loop_sends_the_conversation_as_meta_not_as_an_argument(monkeypatch):
    from tools import tool_registry
    from tools import unity_mcp_tools as umt

    calls = []
    monkeypatch.setattr(umt, "_client", _recording_client(calls))
    monkeypatch.setattr(tool_registry, "is_unity_tool", lambda name: True)
    monkeypatch.setattr(tool_registry, "get_unity_tool_functions",
                        lambda: {"manage_gameobject": object()})

    result = tool_registry.execute_tool(
        "manage_gameobject",
        {"action": "create", "name": None, "conversation_id": 99},
        ".", 7)

    assert result["success"] is True
    name, params, meta = calls[0]
    assert name == "manage_gameobject"
    assert meta == {"gamachine_conversation": 7}
    # The model's own argument goes as an argument, never as the owner; None drops.
    assert params == {"action": "create", "conversation_id": 99}


@pytest.mark.parametrize("conversation_id", [None, 0, "7"])
def test_cloud_loop_without_a_usable_conversation_sends_no_meta(monkeypatch, conversation_id):
    from tools import tool_registry
    from tools import unity_mcp_tools as umt

    calls = []
    monkeypatch.setattr(umt, "_client", _recording_client(calls))
    monkeypatch.setattr(tool_registry, "is_unity_tool", lambda name: True)
    monkeypatch.setattr(tool_registry, "get_unity_tool_functions",
                        lambda: {"manage_gameobject": object()})
    tool_registry.execute_tool("manage_gameobject", {"action": "create"}, ".", conversation_id)
    assert calls[0][2] is None


def test_the_runner_hands_its_conversation_to_execute_tool(monkeypatch):
    seen = []

    def fake_execute(name, args, workspace, conversation_id):
        seen.append(conversation_id)
        return {"success": True}

    monkeypatch.setattr(ar, "execute_tool", fake_execute)
    runner = ar.AgentRunner(provider_type="openai", api_key="k", model_name="m",
                            workspace_path=".", conversation_id=7)
    asyncio.run(runner._execute_tool_with_approval("manage_gameobject", {"action": "create"}))
    assert seen == [7]


def test_meta_reaches_the_mcp_session_call(monkeypatch):
    """Through _UnityMCPClient: the per-call meta is handed to ClientSession.call_tool."""
    from mcp import types as mcp_types

    from tools import unity_mcp_tools as umt

    seen = []

    class _Session:
        async def initialize(self):
            return None

        async def list_tools(self):
            return types.SimpleNamespace(tools=[])

        async def call_tool(self, name, params, read_timeout_seconds=None, **kwargs):
            seen.append(kwargs.get("meta"))
            return mcp_types.CallToolResult(content=[], isError=False)

    import contextlib

    @contextlib.asynccontextmanager
    async def _cm():
        yield _Session(), ("endpoint",)

    client = umt._UnityMCPClient(session_factory=lambda _h: _cm(),
                                 endpoint_key=lambda: ("endpoint",))
    monkeypatch.setattr(umt, "_client", client)
    try:
        assert umt.call_unity_tool("manage_gameobject", {}, conversation_id=7)["success"] is True
        assert umt.call_unity_tool("manage_gameobject", {})["success"] is True
    finally:
        client.close()
    assert seen == [{"gamachine_conversation": 7}, None]


def test_session_call_tool_accepts_meta():
    """The installed mcp ClientSession.call_tool takes `meta` (mcp 2.2)."""
    import inspect

    from mcp import ClientSession

    assert "meta" in inspect.signature(ClientSession.call_tool).parameters


# ── the unityai bridge ───────────────────────────────────────────────────────

class _BridgeClient:
    bodies = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _BridgeClient.bodies.append(json)
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "resolved", "approved": False, "error": "test"})


@pytest.mark.parametrize("env,expected", [
    ("7", 7), (None, "absent"), ("", "absent"), ("0", "absent"), ("-7", "absent"),
    ("7x", "absent"), (" 7", "absent"), ("٧", "absent"),
])
def test_bridge_reads_GAMACHINE_CONVERSATION_ID(monkeypatch, env, expected):
    from unity_ai_mcp import approval_bridge

    if env is None:
        monkeypatch.delenv("GAMACHINE_CONVERSATION_ID", raising=False)
    else:
        monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", env)
    monkeypatch.setattr(approval_bridge, "_get_headers", lambda: {})
    monkeypatch.setattr(approval_bridge.httpx, "AsyncClient", _BridgeClient)
    _BridgeClient.bodies = []

    asyncio.run(approval_bridge.request_approval("bash", {"command": "echo"}, "/ws"))

    body = _BridgeClient.bodies[0]
    assert body["gate_id"] and body["tool"] == "bash" and body["workspace_path"] == "/ws"
    assert "approval_turn_token" in body
    if expected == "absent":
        assert "conversation_id" not in body
    else:
        assert body["conversation_id"] == expected
