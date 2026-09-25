"""Backend Unity MCP client across server restarts (P3).

Baseline (25 Sep 2026, fastmcp 3.4.7): after a restart every call failed with
"McpError: Session terminated" (mcp 1.x) and the client kept the dead session until the
toggle unloaded it; with the server down a call waited its full read timeout.
Pinned here: a lost session reconnects and retries exactly once (its 404 proves
the call never ran), every other failure keeps the no-retry rule, and downtime
answers at once with one Turkish sentence.
"""
import asyncio
import contextlib
import time
import types as pytypes
from unittest import mock

import httpx2
import pytest
from mcp import types
from mcp.shared.exceptions import MCPError

import agentic.agent_runner  # noqa: F401  (tools <-> agentic import cycle, see test_unity_mcp_client)
from tools import unity_mcp_tools as umt  # noqa: E402
from tests.mcp_restart_server import RestartableMCPServer  # noqa: E402

# What mcp 2.x raises for a 404 on a session it holds (streamable_http post_writer).
SESSION_TERMINATED = dict(code=types.INVALID_REQUEST, message="Session terminated")


class _Session:
    def __init__(self, server, index):
        self.server, self.index = server, index

    async def initialize(self):
        pass

    async def list_tools(self):
        return pytypes.SimpleNamespace(tools=[])

    async def call_tool(self, name, params, read_timeout_seconds=None, **_kwargs):
        return await self.server.call(self, name, params)


class _Server:
    """Sessions listed in `lost` answer like a restarted server (404)."""

    def __init__(self):
        self.sessions = []
        self.lost = set()
        self.executions = 0
        self.fail_with = None

    async def call(self, session, name, params):
        if session.index in self.lost:
            raise MCPError(**SESSION_TERMINATED)
        if self.fail_with is not None:
            exc, self.fail_with = self.fail_with, None
            raise exc
        self.executions += 1
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"{name} ok")])

    def factory(self, message_handler):
        server = self

        @contextlib.asynccontextmanager
        async def _open():
            session = _Session(server, len(server.sessions))
            server.sessions.append(session)
            yield session, ("endpoint",)
        return _open()


@pytest.fixture
def fake():
    server = _Server()
    client = umt._UnityMCPClient(session_factory=server.factory, endpoint_key=lambda: ("endpoint",))
    with mock.patch.object(umt, "_client", client):
        yield server
        client.close()


def test_a_lost_session_reconnects_and_the_call_runs_once(fake):
    assert umt.call_unity_tool("manage_scene", {})["success"] is True
    fake.lost.add(0)  # the server restarted: session 0 is unknown now

    result = umt.call_unity_tool("manage_gameobject", {"action": "create"})

    assert result == {"success": True, "result": "manage_gameobject ok"}
    assert len(fake.sessions) == 2
    assert fake.executions == 2  # one per call; the 404'd attempt ran nothing


def test_a_second_lost_session_is_reported_and_not_looped(fake):
    fake.lost.update({0, 1, 2})
    result = umt.call_unity_tool("manage_scene", {})
    assert result == {"success": False, "error": umt._SESSION_LOST_MSG}
    assert len(fake.sessions) == 2
    assert fake.executions == 0


def test_a_json_rpc_error_answer_is_not_retried_and_keeps_the_session(fake):
    fake.fail_with = MCPError(code=-32602, message="bad params")
    result = umt.call_unity_tool("manage_scene", {})
    assert result["success"] is False and "bad params" in result["error"]
    assert umt.call_unity_tool("manage_scene", {})["success"] is True
    assert len(fake.sessions) == 1


def test_a_transport_failure_after_sending_is_never_retried(fake):
    fake.fail_with = httpx2.ReadError("connection reset")
    result = umt.call_unity_tool("manage_gameobject", {"action": "create"})
    assert result["success"] is False
    assert fake.executions == 0 and len(fake.sessions) == 1
    assert umt.call_unity_tool("manage_scene", {})["success"] is True
    assert len(fake.sessions) == 2  # the next call reconnects


def test_connection_closed_is_a_clear_turkish_error_and_is_not_retried(fake):
    fake.fail_with = MCPError(code=types.CONNECTION_CLOSED, message="Connection closed")
    result = umt.call_unity_tool("manage_gameobject", {"action": "create"})
    assert result == {"success": False, "error": umt._CONNECTION_LOST_MSG}
    assert len(fake.sessions) == 1
    assert umt.call_unity_tool("manage_scene", {})["success"] is True
    assert len(fake.sessions) == 2


def test_only_the_404_rendering_counts_as_a_lost_session():
    assert umt._is_session_lost(MCPError(**SESSION_TERMINATED))
    assert umt._is_session_lost(MCPError(code=-32600, message="Session not found"))
    assert not umt._is_session_lost(MCPError(code=-32600, message="Bad Request"))
    assert not umt._is_session_lost(MCPError(code=types.REQUEST_TIMEOUT, message="Timed out"))
    assert not umt._is_session_lost(RuntimeError("Session terminated"))


def test_input_required_is_a_clear_error_and_keeps_the_session(fake):
    """mcp 2.x can answer tools/call with InputRequiredResult; this client
    cannot supply the input, so it must never read as a success."""
    fake.fail_with = None
    original = fake.call

    async def _input_required(session, name, params):
        fake.call = original
        return types.InputRequiredResult(request_state="opaque")

    fake.call = _input_required
    result = umt.call_unity_tool("manage_scene", {})
    assert result == {"success": False,
                      "error": umt._INPUT_REQUIRED_MSG.format(name="manage_scene")}
    assert umt.call_unity_tool("manage_scene", {})["success"] is True
    assert len(fake.sessions) == 1, "the session is healthy; only the call failed"


# ── the real transport against the MCP SDK's own server ─────────────────────

@pytest.fixture
def real():
    srv = RestartableMCPServer()
    srv.start()
    client = umt._UnityMCPClient()
    with mock.patch.object(umt, "_endpoint", lambda: (srv.url, {})), \
            mock.patch.object(umt, "_client", client):
        yield srv
        client.close()
    srv.stop()


def _text(result):
    assert result["success"] is True, result
    return result["result"]


def test_first_call_after_a_restart_works(real):
    assert _text(umt.call_unity_tool("echo", {"text": "a"}, timeout=15)) == "a"
    real.restart()
    assert _text(umt.call_unity_tool("echo", {"text": "b"}, timeout=15)) == "b"
    assert _text(umt.call_unity_tool("echo", {"text": "c"}, timeout=15)) == "c"
    assert real.executions["echo"] == 3


def test_a_mutation_after_a_restart_runs_exactly_once(real):
    assert _text(umt.call_unity_tool("echo", {"text": "a"}, timeout=15)) == "a"
    real.restart()
    assert _text(umt.call_unity_tool("mutate", {"value": "x"}, timeout=15)) == "mutated x"
    assert real.executions["mutate"] == 1


def test_downtime_answers_at_once_and_the_next_call_recovers(real):
    assert _text(umt.call_unity_tool("echo", {"text": "a"}, timeout=15)) == "a"
    real.stop()

    started = time.monotonic()
    down = umt.call_unity_tool("echo", {"text": "b"}, timeout=30)
    elapsed = time.monotonic() - started
    assert down == {"success": False, "error": umt._UNREACHABLE_MSG}
    assert elapsed < 10, f"waited {elapsed:.1f} s; the transport was already dead"

    again = umt.call_unity_tool("echo", {"text": "c"}, timeout=30)
    assert again == {"success": False, "error": umt._UNREACHABLE_MSG}

    real.start()
    assert _text(umt.call_unity_tool("echo", {"text": "d"}, timeout=15)) == "d"
    assert real.executions["echo"] == 2


def test_tool_list_reloads_after_a_restart(real):
    names = {t.name for t in umt._client.list_tools()}
    assert names == {"echo", "mutate"}
    real.restart()
    assert {t.name for t in umt._client.list_tools()} == names
