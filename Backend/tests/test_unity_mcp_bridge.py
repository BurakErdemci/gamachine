"""stdio bridge across Unity MCP server restarts (P3).

Baseline (25 Sep 2026, fastmcp 3.4.7): after a restart the server answered
404 {"id":"server-error"} and the bridge forwarded it unchanged, so the client
waited for its own id and every later call timed out. These tests pin the fix:
re-initialize and retry once on 404, one answer per request id, a clear error
while the server is down, and no tool ever running twice.
"""
import asyncio
import json
import os
import sys
from datetime import timedelta

import pytest

from providers import codex_unitymcp_bridge as cb
from tests.mcp_restart_server import RestartableMCPServer, ScriptedHTTPServer

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "bridge-test", "version": "1"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _call(req_id, tool="echo", args=None):
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": tool, "arguments": args or {"text": f"t{req_id}"}}}


def _bridge(url):
    return cb.Bridge(url, secret_reader=lambda: "", timeout=10)


def _handshake(bridge):
    [init_reply] = bridge.handle(INIT)
    assert init_reply["id"] == 0 and "result" in init_reply
    assert bridge.handle(INITIALIZED) == []


def _text(reply):
    return reply["result"]["content"][0]["text"]


@pytest.fixture
def server():
    srv = RestartableMCPServer()
    srv.start()
    yield srv
    srv.stop()


# ── against the real SDK server ─────────────────────────────────────────────

def test_restart_mid_session_recovers_on_the_first_call(server):
    bridge = _bridge(server.url)
    _handshake(bridge)
    [first] = bridge.handle(_call(1))
    assert _text(first) == "t1"
    old_session = bridge.session_id

    server.restart()

    [after] = bridge.handle(_call(2))
    assert after["id"] == 2
    assert _text(after) == "t2"
    assert bridge.session_id and bridge.session_id != old_session
    # No double execution: one run per call, the 404'd attempt ran nothing.
    assert server.executions["echo"] == 2


def test_mutation_after_restart_runs_exactly_once(server):
    bridge = _bridge(server.url)
    _handshake(bridge)
    server.restart()
    [reply] = bridge.handle(_call(7, "mutate", {"value": "x"}))
    assert _text(reply) == "mutated x"
    assert server.executions["mutate"] == 1


def test_downtime_gives_a_clear_error_then_the_next_call_recovers(server):
    bridge = _bridge(server.url)
    _handshake(bridge)
    server.stop()

    [down] = bridge.handle(_call(3))
    assert down["id"] == 3
    assert down["error"]["message"] == cb.MSG_UNREACHABLE
    assert bridge.handle({"jsonrpc": "2.0", "method": "notifications/cancelled",
                          "params": {"requestId": 3}}) == []

    server.start()
    [up] = bridge.handle(_call(4))
    assert _text(up) == "t4"
    assert server.executions["echo"] == 1


def test_list_tools_after_restart(server):
    bridge = _bridge(server.url)
    _handshake(bridge)
    server.restart()
    [reply] = bridge.handle({"jsonrpc": "2.0", "id": "l1", "method": "tools/list"})
    assert reply["id"] == "l1"
    assert {t["name"] for t in reply["result"]["tools"]} == {"echo", "mutate"}


# ── scripted server: shapes the real one does not produce on demand ─────────

def _init_result(message, session="s1", version="2025-06-18"):
    return (200, {"Content-Type": "application/json", "Mcp-Session-Id": session},
            {"jsonrpc": "2.0", "id": message["id"],
             "result": {"protocolVersion": version, "capabilities": {},
                        "serverInfo": {"name": "scripted", "version": "1"}}})


SESSION_NOT_FOUND = (404, {"Content-Type": "application/json"},
                     {"jsonrpc": "2.0", "id": "server-error",
                      "error": {"code": -32600, "message": "Session not found"}})


def test_a_server_error_with_a_foreign_id_is_rewritten_to_the_request_id():
    def respond(message, headers):
        return (400, {"Content-Type": "application/json"},
                {"jsonrpc": "2.0", "id": "server-error",
                 "error": {"code": -32600, "message": "Bad Request"}})
    srv = ScriptedHTTPServer(respond)
    try:
        [reply] = _bridge(srv.url).handle(_call(11))
    finally:
        srv.close()
    assert reply == {"jsonrpc": "2.0", "id": 11,
                     "error": {"code": -32600, "message": "Bad Request"}}


def test_a_request_without_any_json_answer_still_gets_one_response():
    srv = ScriptedHTTPServer(lambda m, h: (502, {"Content-Type": "text/plain"}, b"bad gateway"))
    try:
        [reply] = _bridge(srv.url).handle(_call(12))
    finally:
        srv.close()
    assert reply["id"] == 12
    assert reply["error"]["message"] == cb.MSG_NO_ANSWER.format(status=502)


def test_an_error_answer_to_a_notification_is_not_forwarded():
    srv = ScriptedHTTPServer(lambda m, h: SESSION_NOT_FOUND)
    try:
        assert _bridge(srv.url).handle(INITIALIZED) == []
    finally:
        srv.close()


def test_protocol_version_header_follows_the_negotiated_version():
    def respond(message, headers):
        if message.get("method") == "initialize":
            return _init_result(message, version="2025-11-25")
        if "id" not in message:
            return (202, {}, b"")
        return (200, {"Content-Type": "application/json"},
                {"jsonrpc": "2.0", "id": message["id"], "result": {"content": []}})
    srv = ScriptedHTTPServer(respond)
    bridge = _bridge(srv.url)
    try:
        _handshake(bridge)
        bridge.handle(_call(1))
    finally:
        srv.close()
    init_headers = srv.requests[0][1]
    call_headers = srv.requests[-1][1]
    assert "MCP-Protocol-Version" not in init_headers
    assert call_headers["MCP-Protocol-Version"] == "2025-11-25"
    assert call_headers["Mcp-Session-Id"] == "s1"


def test_a_second_404_is_reported_once_and_not_looped():
    """The retry is exactly one; a server that keeps losing the session gets
    one clear error, not an endless re-initialize loop."""
    state = {"inits": 0}

    def respond(message, headers):
        if message.get("method") == "initialize":
            state["inits"] += 1
            return _init_result(message, session=f"s{state['inits']}")
        if "id" not in message:
            return (202, {}, b"")
        return SESSION_NOT_FOUND
    srv = ScriptedHTTPServer(respond)
    bridge = _bridge(srv.url)
    try:
        _handshake(bridge)
        [reply] = bridge.handle(_call(5))
    finally:
        srv.close()
    assert reply["id"] == 5 and reply["error"]["message"] == "Session not found"
    assert state["inits"] == 2  # the client's own + one replay
    calls = [m for m, _ in srv.requests if m.get("method") == "tools/call"]
    assert len(calls) == 2


def test_the_replayed_initialize_uses_a_bridge_id_and_is_not_forwarded():
    state = {"lost": True}

    def respond(message, headers):
        if message.get("method") == "initialize":
            return _init_result(message, session="fresh" if message["id"] != 0 else "s1")
        if "id" not in message:
            return (202, {}, b"")
        if headers.get("Mcp-Session-Id") == "s1" and state["lost"]:
            return SESSION_NOT_FOUND
        return (200, {"Content-Type": "application/json"},
                {"jsonrpc": "2.0", "id": message["id"], "result": {"content": []}})
    srv = ScriptedHTTPServer(respond)
    bridge = _bridge(srv.url)
    try:
        _handshake(bridge)
        out = bridge.handle(_call(9))
    finally:
        srv.close()
    assert out == [{"jsonrpc": "2.0", "id": 9, "result": {"content": []}}]
    replay = [m for m, _ in srv.requests if m.get("method") == "initialize"][1]
    assert str(replay["id"]).startswith("gamachine-bridge-reinit-")
    assert replay["params"] == INIT["params"]


# ── end to end: the bridge as a stdio child, driven like Codex drives it ────

def test_stdio_client_survives_a_restart_through_the_bridge_process(server, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import McpError

    argv = cb.bridge_argv()
    env = dict(os.environ)
    env["UNITY_MCP_URL"] = server.url
    # Keep the real token file out of a test run.
    env["HOME"] = env["USERPROFILE"] = str(tmp_path)

    async def scenario():
        params = StdioServerParameters(command=argv[0], args=argv[1:], env=env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                timeout = timedelta(seconds=15)
                first = await session.call_tool("echo", {"text": "a"}, read_timeout_seconds=timeout)
                await asyncio.to_thread(server.restart)
                second = await session.call_tool("echo", {"text": "b"}, read_timeout_seconds=timeout)
                await asyncio.to_thread(server.stop)
                try:
                    await session.call_tool("echo", {"text": "c"}, read_timeout_seconds=timeout)
                    down = None
                except McpError as exc:
                    down = exc.error.message
                await asyncio.to_thread(server.start)
                third = await session.call_tool("echo", {"text": "ğüş"}, read_timeout_seconds=timeout)
                return first, second, down, third

    first, second, down, third = asyncio.run(scenario())
    assert first.content[0].text == "a"
    assert second.content[0].text == "b"
    assert down == cb.MSG_UNREACHABLE
    assert third.content[0].text == "ğüş"
    assert server.executions["echo"] == 3
