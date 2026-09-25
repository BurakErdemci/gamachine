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
import threading
import time

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
    from mcp.shared.exceptions import MCPError

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
                timeout = 15.0
                first = await session.call_tool("echo", {"text": "a"}, read_timeout_seconds=timeout)
                await asyncio.to_thread(server.restart)
                second = await session.call_tool("echo", {"text": "b"}, read_timeout_seconds=timeout)
                await asyncio.to_thread(server.stop)
                try:
                    await session.call_tool("echo", {"text": "c"}, read_timeout_seconds=timeout)
                    down = None
                except MCPError as exc:
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


# ── a failed replay (audit: bridge-stuck-after-failed-reinit) ───────────────

def _restartable_script(state):
    """s1 is lost once state["restarted"]; replays fail while state["starting"]."""
    def respond(message, headers):
        if message.get("method") == "initialize":
            state["inits"] += 1
            if state.get("starting"):
                return (503, {"Content-Type": "application/json"},
                        {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32603, "message": "starting"}})
            return _init_result(message, session=f"s{state['inits']}")
        if "id" not in message:
            return (202, {}, b"")
        sid = headers.get("Mcp-Session-Id")
        if sid is None:
            return (400, {"Content-Type": "application/json"},
                    {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32600, "message": "Bad Request: Missing session ID"}})
        if sid == "s1" and state.get("restarted"):
            return SESSION_NOT_FOUND
        state["runs"].append(message["id"])
        return (200, {"Content-Type": "application/json"},
                {"jsonrpc": "2.0", "id": message["id"], "result": {"content": []}})
    return respond


def test_a_failed_replay_is_retried_by_the_next_call_and_nothing_runs_twice():
    state = {"inits": 0, "runs": []}
    srv = ScriptedHTTPServer(_restartable_script(state))
    bridge = _bridge(srv.url)
    try:
        _handshake(bridge)
        state.update(restarted=True, starting=True)
        [failed] = bridge.handle(_call(2, "mutate"))
        state["starting"] = False
        answers = [bridge.handle(_call(i, "mutate")) for i in (3, 4)]
    finally:
        srv.close()
    assert failed["id"] == 2
    assert failed["error"]["message"] == cb.MSG_REINIT_FAILED.format(detail="HTTP 503")
    assert answers == [[{"jsonrpc": "2.0", "id": i, "result": {"content": []}}] for i in (3, 4)]
    # Call 2 reached the server once (the 404'd attempt) and ran nowhere; 3 and
    # 4 ran once each; no call ever went out without a session.
    assert state["runs"] == [3, 4]
    calls = [m["id"] for m, _ in srv.requests if m.get("method") == "tools/call"]
    assert calls == [2, 3, 4]
    assert all(h.get("Mcp-Session-Id") for m, h in srv.requests
               if m.get("method") == "tools/call")
    assert state["inits"] == 3  # the client's own, the failed replay, the good one


def test_a_replay_that_cannot_connect_keeps_the_bridge_marked(server):
    bridge = _bridge(server.url)
    _handshake(bridge)
    bridge._needs_reinit = True  # as after a 404 whose replay hit a dead server
    server.stop()
    [down] = bridge.handle(_call(5))
    assert down["error"]["message"] == cb.MSG_UNREACHABLE
    assert bridge._needs_reinit
    server.start()
    [up] = bridge.handle(_call(6))
    assert _text(up) == "t6"
    assert not bridge._needs_reinit
    assert server.executions["echo"] == 1


# ── one overall deadline (audit: unbounded-request-wait) ────────────────────

def _keepalive_forever(stop):
    def chunks():
        while not stop.wait(0.05):
            yield b": keepalive\n\n"
    return chunks()


def test_sse_keepalives_do_not_extend_the_deadline():
    stop = threading.Event()
    srv = ScriptedHTTPServer(
        lambda m, h: (200, {"Content-Type": "text/event-stream"}, _keepalive_forever(stop)))
    bridge = cb.Bridge(srv.url, secret_reader=lambda: "", timeout=0.5)
    try:
        started = time.monotonic()
        [reply] = bridge.handle(_call(7, "mutate"))
        elapsed = time.monotonic() - started
    finally:
        stop.set()
        srv.close()
    assert reply == {"jsonrpc": "2.0", "id": 7, "error": {
        "code": -32603, "message": cb.MSG_TIMEOUT.format(seconds=0.5)}}
    assert "bilinmiyor" in reply["error"]["message"]
    assert 0.45 < elapsed < 2.0, elapsed


def test_a_server_that_never_sends_a_status_line_hits_the_deadline():
    release = threading.Event()

    def respond(message, headers):
        release.wait(10)
        return (200, {"Content-Type": "application/json"},
                {"jsonrpc": "2.0", "id": message["id"], "result": {}})
    srv = ScriptedHTTPServer(respond)
    try:
        started = time.monotonic()
        [reply] = cb.Bridge(srv.url, secret_reader=lambda: "", timeout=0.4).handle(_call(8))
        elapsed = time.monotonic() - started
    finally:
        release.set()
        srv.close()
    assert reply["error"]["message"] == cb.MSG_TIMEOUT.format(seconds=0.4)
    assert elapsed < 2.0, elapsed


def test_the_default_deadline_outlasts_the_approval_wait():
    # 10 s POST + 180 s card today (150 s after the server change) + run time.
    assert cb.HTTP_TIMEOUT_S >= 10 + 180 + 30


# ── concurrent stdin loop: order of sends, one answer per id ────────────────

class _Stdout:
    def __init__(self):
        self.lines = []
        self._cond = threading.Condition()

    def write(self, data):
        with self._cond:
            self.lines.extend(json.loads(x) for x in data.decode("utf-8").splitlines() if x)
            self._cond.notify_all()

    def flush(self):
        pass

    def wait_for(self, predicate, timeout=10):
        with self._cond:
            assert self._cond.wait_for(lambda: predicate(self.lines), timeout), self.lines


def _line(message):
    return (json.dumps(message) + "\n").encode("utf-8")


def test_a_parked_call_does_not_hold_up_a_cancel_or_a_parallel_call():
    release = threading.Event()
    seen_cancel = threading.Event()

    def respond(message, headers):
        method = message.get("method")
        if method == "initialize":
            return _init_result(message)
        if method == "notifications/cancelled":
            seen_cancel.set()
        if "id" not in message:
            return (202, {}, b"")
        if message["params"]["name"] == "slow":
            release.wait(10)  # e.g. parked on an approval card
        return (200, {"Content-Type": "application/json"},
                {"jsonrpc": "2.0", "id": message["id"],
                 "result": {"content": [{"type": "text", "text": message["params"]["name"]}]}})

    srv = ScriptedHTTPServer(respond)
    out = _Stdout()
    checked = threading.Event()

    def stdin():
        yield _line(INIT)
        out.wait_for(lambda lines: any(m.get("id") == 0 for m in lines))
        yield _line(INITIALIZED)
        yield _line(_call(1, "slow"))
        yield _line(_call(2, "fast"))
        yield _line({"jsonrpc": "2.0", "method": "notifications/cancelled",
                     "params": {"requestId": 1}})
        yield _line(_call(3, "fast"))
        # Call 1 is still parked: the fast calls and the cancel must be through.
        out.wait_for(lambda lines: {2, 3} <= {m.get("id") for m in lines})
        assert seen_cancel.wait(5)
        assert 1 not in {m.get("id") for m in out.lines}
        checked.set()
        release.set()

    try:
        _bridge(srv.url).run(stdin=stdin(), stdout=out)
    finally:
        release.set()
        srv.close()
    assert checked.is_set()
    ids = [m.get("id") for m in out.lines]
    assert sorted(ids) == [0, 1, 2, 3], ids  # exactly one answer per request
    assert ids.index(1) > ids.index(2) and ids.index(1) > ids.index(3)
    assert {m["id"]: m["result"]["content"][0]["text"] for m in out.lines if m["id"]} == {
        1: "slow", 2: "fast", 3: "fast"}
    # Connections were opened in stdin order.
    order = [m.get("id", m.get("method")) for m in srv.accepted]
    assert order == [0, "notifications/initialized", 1, 2, "notifications/cancelled", 3]


def test_a_request_beyond_the_cap_is_refused_at_once_and_a_cancel_still_flows():
    """verification round 26 Sep 2026 (cancellation-head-of-line-blocking):
    with MAX_IN_FLIGHT calls parked, request 17 used to park the stdin reader,
    so the cancel behind it was not forwarded until a worker finished."""
    limit = cb.MAX_IN_FLIGHT
    release = threading.Event()
    seen_cancel = threading.Event()

    def respond(message, headers):
        method = message.get("method")
        if method == "initialize":
            return _init_result(message)
        if method == "notifications/cancelled":
            seen_cancel.set()
        if "id" not in message:
            return (202, {}, b"")
        if message["params"]["name"] == "slow":
            release.wait(10)
        return (200, {"Content-Type": "application/json"},
                {"jsonrpc": "2.0", "id": message["id"],
                 "result": {"content": [{"type": "text", "text": message["params"]["name"]}]}})

    srv = ScriptedHTTPServer(respond)
    out = _Stdout()
    checked = threading.Event()
    over = limit + 1

    def stdin():
        yield _line(INIT)
        out.wait_for(lambda lines: any(m.get("id") == 0 for m in lines))
        yield _line(INITIALIZED)
        for i in range(1, limit + 1):
            yield _line(_call(i, "slow"))
        yield _line(_call(over, "mutate"))
        yield _line({"jsonrpc": "2.0", "method": "notifications/cancelled",
                     "params": {"requestId": 1}})
        # All parked calls are still held: the refusal and the cancel are through.
        out.wait_for(lambda lines: any(m.get("id") == over for m in lines))
        assert seen_cancel.wait(5)
        assert not ({m.get("id") for m in out.lines} & set(range(1, limit + 1)))
        checked.set()
        release.set()
        out.wait_for(lambda lines: set(range(1, limit + 1)) <= {m.get("id") for m in lines})
        # A freed slot takes the next request as before.
        yield _line(_call(over + 1, "fast"))

    try:
        _bridge(srv.url).run(stdin=stdin(), stdout=out)
    finally:
        release.set()
        srv.close()
    assert checked.is_set()
    ids = [m.get("id") for m in out.lines]
    assert sorted(ids) == list(range(0, over + 2)), ids  # exactly one answer per request
    [refused] = [m for m in out.lines if m.get("id") == over]
    assert refused["error"]["code"] == cb.BUSY_ERROR_CODE
    assert refused["error"]["message"] == cb.MSG_BUSY.format(limit=limit)
    sent = [m.get("id", m.get("method")) for m in srv.accepted]
    assert over not in sent  # refused, never sent to the server
    assert sent == ([0, "notifications/initialized"] + list(range(1, limit + 1))
                    + ["notifications/cancelled", over + 1])


def test_concurrent_404s_replay_the_handshake_once():
    state = {"inits": 0, "runs": [], "restarted": True}
    gate = threading.Barrier(2, timeout=5)
    base = _restartable_script(state)

    def respond(message, headers):
        if message.get("method") == "tools/call" and headers.get("Mcp-Session-Id") == "s1":
            gate.wait()  # both calls are in flight on s1 before either sees its 404
        return base(message, headers)

    srv = ScriptedHTTPServer(respond)
    out = _Stdout()

    def stdin():
        yield _line(INIT)
        out.wait_for(lambda lines: any(m.get("id") == 0 for m in lines))
        yield _line(INITIALIZED)
        yield _line(_call(1, "mutate"))
        yield _line(_call(2, "mutate"))

    try:
        _bridge(srv.url).run(stdin=stdin(), stdout=out)
    finally:
        srv.close()
    assert sorted(m["id"] for m in out.lines if m["id"]) == [1, 2]
    assert all("result" in m for m in out.lines)
    assert sorted(state["runs"]) == [1, 2]
    assert state["inits"] == 2  # the client's own + exactly one replay


def test_a_late_answer_on_the_old_session_does_not_bring_it_back():
    release = threading.Event()
    state = {"inits": 0, "runs": []}
    base = _restartable_script(state)

    def respond(message, headers):
        if message.get("method") == "tools/call" and message["params"]["name"] == "slow":
            state["restarted"] = True  # the server restarts while this one runs
            release.wait(10)
            return (200, {"Content-Type": "application/json", "Mcp-Session-Id": "s1"},
                    {"jsonrpc": "2.0", "id": message["id"], "result": {"content": []}})
        return base(message, headers)

    srv = ScriptedHTTPServer(respond)
    out = _Stdout()
    bridge = _bridge(srv.url)

    def stdin():
        yield _line(INIT)
        out.wait_for(lambda lines: any(m.get("id") == 0 for m in lines))
        yield _line(INITIALIZED)
        yield _line(_call(1, "slow"))
        yield _line(_call(2, "mutate"))
        out.wait_for(lambda lines: any(m.get("id") == 2 for m in lines))
        release.set()
        out.wait_for(lambda lines: any(m.get("id") == 1 for m in lines))
        yield _line(_call(3, "mutate"))

    try:
        bridge.run(stdin=stdin(), stdout=out)
    finally:
        release.set()
        srv.close()
    assert bridge.session_id == "s2"
    assert state["runs"] == [2, 3] and state["inits"] == 2
