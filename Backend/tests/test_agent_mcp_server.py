"""The terminal agents' MCP server: request polling on synthetic request states, the
plain-text failures and the client name. No backend, no CLI, no real token files."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from unity_ai_mcp import agent_server as srv  # noqa: E402


class Clock:
    """Fake time: every sleep is recorded and advances `now`, so a timeout takes no real time."""

    def __init__(self):
        self.slept = []
        self.t = 0.0

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def states(*batch):
    """fetch_state that hands out one state per poll (the last one repeats)."""
    calls = []

    async def fetch_state(request_id):
        calls.append(request_id)
        return batch[min(len(calls) - 1, len(batch) - 1)]

    return fetch_state, calls


async def run(*batch, wait=30):
    clock = Clock()
    fetch_state, calls = states(*batch)
    out = await srv.wait_for_reply("rid1", wait, fetch_state=fetch_state, sleep=clock.sleep, clock=clock.now)
    return out, calls, clock


def test_clamp_wait():
    assert srv.clamp_wait(1) == srv.WAIT_MIN
    assert srv.clamp_wait(10_000) == srv.WAIT_MAX
    assert srv.clamp_wait("x") == 120 and srv.clamp_wait(None) == 120
    assert srv.clamp_wait(60) == 60


async def test_a_done_request_returns_status_model_and_reply():
    out, calls, _ = await run({"status": "done", "reply": "MINE", "model": "claude-opus-5-5"})
    assert out == "status: done\nmodel: claude-opus-5-5\nreply:\nMINE"
    assert calls == ["rid1"]


async def test_polling_continues_through_queued_and_running_until_done():
    out, calls, clock = await run({"status": "queued"}, {"status": "running"},
                                  {"status": "done", "reply": "late"})
    assert "status: done" in out and out.endswith("late")
    assert len(calls) == 3 and clock.slept == [srv.POLL_S, srv.POLL_S]


async def test_a_card_waiting_is_said_and_the_wait_reply_hint_names_the_request():
    out, _, clock = await run({"status": "running", "waiting_card": "delete_file"}, wait=5)
    assert "status: running" in out
    assert "waiting for the user's approval in Gamachine: delete_file" in out
    assert 'still running; call gamachine_wait_reply(request_id="rid1")' in out
    assert "reply:" not in out and len(clock.slept) == 5


async def test_timeout_while_queued_gives_the_same_hint():
    out, _, _ = await run({"status": "queued"}, wait=5)
    assert out.startswith("status: queued") and 'gamachine_wait_reply(request_id="rid1")' in out


async def test_the_clamped_wait_bounds_the_polling():
    _, calls, _ = await run({"status": "running"}, wait=1)
    assert len(calls) == srv.WAIT_MIN + 1


async def test_ended_without_reply_has_its_sentence():
    out, calls, _ = await run({"status": "ended_without_reply", "reply": None})
    assert "status: ended_without_reply" in out
    assert "the turn ended without a reply (an error or a stop in Gamachine)" in out and len(calls) == 1


async def test_a_delivery_gap_is_not_terminal():
    # Queued behind a long turn: not_delivered between turns, then sent and answered.
    out, calls, _ = await run({"status": "queued"}, {"status": "not_delivered"}, {"status": "not_delivered"},
                              {"status": "running"}, {"status": "done", "reply": "LATE"})
    assert "status: done" in out and out.endswith("LATE") and len(calls) == 5


async def test_timeout_while_not_delivered_says_so_with_the_wait_hint():
    out, calls, _ = await run({"status": "not_delivered"}, wait=5)
    assert "Gamachine did not send the message; is its window open?" in out
    assert 'gamachine_wait_reply(request_id="rid1")' in out and len(calls) == srv.WAIT_MIN + 1


async def test_slow_polls_cannot_stretch_the_wait_past_its_budget():
    clock = Clock()
    calls = []

    async def slow_fetch(request_id):
        calls.append(clock.now())
        clock.t += 3.0  # each poll takes 3 s of wall-clock time
        return {"status": "running"}

    out = await srv.wait_for_reply("rid1", 5, fetch_state=slow_fetch, sleep=clock.sleep, clock=clock.now)
    assert "still running" in out and calls == [0.0, 4.0] and clock.t <= 5 + 3.0


async def test_a_stopped_or_failed_turn_marks_its_reply_as_maybe_incomplete():
    for status in ("stopped", "error"):
        out, _, _ = await run({"status": "done", "reply": "half", "turn_status": status})
        assert "reply:\nhalf" in out and srv.PARTIAL_NOTE.format(status) in out
    out, _, _ = await run({"status": "done", "reply": "whole", "turn_status": "done"})
    assert "may be incomplete" not in out


async def test_a_truncated_reply_says_so():
    out, _, _ = await run({"status": "done", "reply": "abc", "truncated": True})
    assert "reply:\nabc" in out and srv.TRUNCATED_NOTE in out


async def test_unknown_request_says_gamachine_restarted(monkeypatch):
    patch_files(monkeypatch)
    fake_client(monkeypatch, FakeResponse(404, {"error": "unknown_request"}))
    out = await srv._guard(srv.wait_reply("f" * 16, 5))
    assert out == srv.UNKNOWN_REQUEST and "restarted" in out


async def test_wait_reply_refuses_a_malformed_request_id_without_any_request(monkeypatch):
    seen = []

    async def fake_request(method, path, **kw):
        seen.append(path)
        return {}

    monkeypatch.setattr(srv, "_request", fake_request)
    for bad in ("../../remote/devices", "ABCDEF0123456789", "0123456789abcde", "0123456789abcdef/../x", 5):
        assert await srv._guard(srv.wait_reply(bad, 5)) == srv.UNKNOWN_REQUEST
    assert seen == []


async def test_send_message_posts_then_polls_the_request(monkeypatch):
    seen = []

    async def fake_request(method, path, **kw):
        seen.append((method, path))
        if method == "POST":
            return {"status": "accepted", "request_id": "0123456789abcdef"}
        return {"status": "done", "reply": "hi back", "model": "m"}

    monkeypatch.setattr(srv, "_request", fake_request)
    out = await srv.send_message(None, 4, "hi", 10)
    assert seen == [("POST", "/chats/4/messages"), ("GET", "/requests/0123456789abcdef")]
    assert "status: done" in out and "hi back" in out


async def test_send_message_without_a_request_id_is_not_ready(monkeypatch):
    async def fake_request(method, path, **kw):
        return {"status": "desktop_not_ready"}

    monkeypatch.setattr(srv, "_request", fake_request)
    assert await srv.send_message(None, 4, "hi", 10) == srv.NOT_READY


async def test_read_messages_clamps_the_limit(monkeypatch):
    seen = []

    async def fake_request(method, path, **kw):
        seen.append(kw["params"]["limit"])
        return {"messages": []}

    monkeypatch.setattr(srv, "_request", fake_request)
    for given in (100, 0, -3, "x", None):
        await srv.read_messages(1, given)
    assert seen == [50, 1, 1, 10, 10]


# ── HTTP failures become plain sentences ─────────────────────────────────────

def patch_files(monkeypatch, url="http://127.0.0.1:1", token="t"):
    monkeypatch.setattr(srv, "read_backend_url", lambda: url)
    monkeypatch.setattr(srv, "read_local_app_token", lambda: token)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def fake_client(monkeypatch, response=None, error=None):
    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kw):
            if error:
                raise error
            Client.last = (method, url, kw)
            return response

    monkeypatch.setattr(srv.httpx, "AsyncClient", Client)
    return Client


@pytest.mark.parametrize("url, token", [("", "t"), ("http://x", "")])
async def test_missing_url_or_token_means_not_running(monkeypatch, url, token):
    patch_files(monkeypatch, url, token)
    assert await srv._guard(srv.list_chats()) == srv.NOT_RUNNING


async def test_connection_refused_means_not_running(monkeypatch):
    patch_files(monkeypatch)
    fake_client(monkeypatch, error=srv.httpx.ConnectError("refused"))
    assert await srv._guard(srv.list_chats()) == srv.NOT_RUNNING


async def test_disabled_and_other_errors_are_short_lines(monkeypatch):
    patch_files(monkeypatch)
    fake_client(monkeypatch, FakeResponse(403, {"detail": "agent_bridge_disabled"}))
    assert await srv._guard(srv.list_chats()) == srv.DISABLED
    assert "Settings > Connectors > Terminal agents" in srv.DISABLED
    fake_client(monkeypatch, FakeResponse(404, {"error": "unknown_chat"}))
    assert await srv._guard(srv.read_messages(5)) == "Gamachine answered HTTP 404: unknown_chat"


async def test_request_carries_the_token_and_the_client_name(monkeypatch):
    patch_files(monkeypatch)
    client = fake_client(monkeypatch, FakeResponse(200, {"status": "desktop_not_ready"}))
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        client_info=SimpleNamespace(name="codex-cli"))))
    assert await srv.send_message(ctx, 3, "hi") == srv.NOT_READY
    method, url, kw = client.last
    assert (method, url) == ("POST", "http://127.0.0.1:1/agent-bridge/chats/3/messages")
    assert kw["headers"] == {"X-Session-Token": "t"} and kw["json"] == {"text": "hi", "client": "codex-cli"}


# ── client name ──────────────────────────────────────────────────────────────

def test_client_name_order_declared_then_env_then_terminal():
    assert srv.resolve_client_name("claude-code", {"GAMACHINE_AGENT_NAME": "x"}) == "claude-code"
    assert srv.resolve_client_name(None, {"GAMACHINE_AGENT_NAME": "from-env"}) == "from-env"
    assert srv.resolve_client_name("  ", {"GAMACHINE_AGENT_NAME": "from-env"}) == "from-env"
    assert srv.resolve_client_name(None, {}) == "Terminal"


def test_declared_name_is_read_from_the_initialize_params():
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        client_info=SimpleNamespace(name="opencode"))))
    assert srv.declared_client_name(ctx) == "opencode"
    assert srv.declared_client_name(SimpleNamespace(session=SimpleNamespace(client_params=None))) is None


def test_the_real_mcp_context_exposes_the_declared_name():
    """The accessor must exist on the installed mcp package's types."""
    from mcp.server.session import ServerSession
    from mcp_types import InitializeRequestParams
    assert isinstance(ServerSession.client_params, property)
    assert "client_info" in InitializeRequestParams.model_fields
    from mcp.server.mcpserver import Context
    assert isinstance(Context.session, property)


def test_the_server_lists_exactly_the_seven_tools():
    import asyncio
    tools = asyncio.run(srv.create_server().list_tools())
    assert sorted(t.name for t in tools) == sorted([
        "gamachine_list_chats", "gamachine_list_models", "gamachine_new_chat", "gamachine_set_model",
        "gamachine_send_message", "gamachine_wait_reply", "gamachine_read_messages"])
    for tool in tools:
        if tool.name != "gamachine_wait_reply":
            assert "ctx" not in tool.input_schema.get("properties", {})
