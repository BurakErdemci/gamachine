"""The terminal agents' MCP server: reply detection on synthetic events, the
plain-text failures and the client name. No backend, no CLI, no real token files."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from unity_ai_mcp import agent_server as srv  # noqa: E402


def ev(seq, kind, **data):
    return {"seq": seq, "kind": kind, **data}


START = dict(provider="claude", model="opus")


class Clock:
    """Fake time: every sleep advances it, so a timeout takes no real time."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def feeder(batches, epoch="e1", gap=False, messages=None):
    """fetch_events that hands out one batch per poll (the last one repeats)."""
    calls = []

    async def fetch_events(since):
        calls.append(since)
        batch = batches[min(len(calls) - 1, len(batches) - 1)]
        last = max([since] + [e["seq"] for e in batch])
        return {"epoch": epoch, "events": [e for e in batch if e["seq"] > since], "gap": gap, "last_seq": last}

    async def fetch_messages(limit):
        return {"messages": messages or []}

    return fetch_events, fetch_messages, calls


async def run(batches, *, busy=False, wait=30, epoch="e1", served="e1", since=0, **kw):
    clock = Clock()
    fetch_events, fetch_messages, calls = feeder(batches, epoch=served, **kw)
    out = await srv.wait_for_reply(7, since, wait, busy_at_send=busy, epoch=epoch,
                                   fetch_events=fetch_events, fetch_messages=fetch_messages,
                                   sleep=clock.sleep, clock=clock)
    return out, calls


# ── analyze_reply ────────────────────────────────────────────────────────────

def test_idle_chat_takes_the_first_turn():
    v = srv.analyze_reply([ev(1, "turn_start", **START), ev(2, "text", content="Hel"),
                           ev(3, "text", content="lo"), ev(4, "turn_end", status="done")])
    assert (v["started"], v["ended"], v["status"], v["text"]) == (True, True, "done", "Hello")
    assert v["model"] == "opus" and v["waiting_card"] is None


def test_busy_chat_skips_the_running_turn_and_takes_the_next():
    events = [ev(5, "text", content="old tail"), ev(6, "turn_end", status="done"),
              ev(7, "turn_start", **START), ev(8, "text", content="mine"), ev(9, "turn_end", status="error")]
    v = srv.analyze_reply(events, busy_at_send=True)
    assert (v["text"], v["status"], v["ended"]) == ("mine", "error", True)


def test_busy_chat_before_the_old_turn_ends_has_no_reply_yet():
    assert srv.analyze_reply([ev(5, "text", content="old")], busy_at_send=True) == {"started": False, "ended": False}
    old_end_only = [ev(5, "turn_end", status="done")]
    assert srv.analyze_reply(old_end_only, busy_at_send=True)["started"] is False


def test_open_card_is_reported_until_it_closes():
    opened = [ev(1, "turn_start", **START), ev(2, "card_opened", card_id="c1", summary="git push")]
    assert srv.analyze_reply(opened)["waiting_card"] == "git push"
    closed = opened + [ev(3, "card_closed", card_id="c1", decision="approved")]
    assert srv.analyze_reply(closed)["waiting_card"] is None


def test_stopped_turn_reports_its_reason():
    v = srv.analyze_reply([ev(1, "turn_start", **START), ev(2, "turn_end", status="stopped", stop_reason="user")])
    assert (v["status"], v["stop_reason"]) == ("stopped", "user")


def test_clamp_wait():
    assert [srv.clamp_wait(x) for x in (0, 5, 120, 999, "x", None)] == [5, 5, 120, 170, 120, 120]


# ── wait_for_reply ───────────────────────────────────────────────────────────

async def test_idle_chat_reply_is_returned_when_the_turn_ends():
    out, calls = await run([[], [ev(1, "turn_start", **START), ev(2, "text", content="Hi")],
                            [ev(3, "turn_end", status="done")]])
    assert out == "status: done\nmodel: claude/opus\nreply:\nHi"
    assert calls == [0, 0, 2]


async def test_busy_at_send_waits_for_the_next_turn():
    batches = [[ev(1, "text", content="old"), ev(2, "turn_end", status="done")],
               [ev(3, "turn_start", **START), ev(4, "text", content="new"), ev(5, "turn_end", status="done")]]
    out, _ = await run(batches, busy=True)
    assert out.endswith("reply:\nnew") and "old" not in out


async def test_card_waiting_is_said_when_time_runs_out():
    batch = [ev(1, "turn_start", **START), ev(2, "text", content="part"),
             ev(3, "card_opened", card_id="c", summary="rm build/")]
    out, _ = await run([batch], wait=5)
    assert "status: running" in out
    assert "waiting for the user's approval in Gamachine: rm build/" in out
    assert "the reply turn has started" in out
    assert 'gamachine_wait_reply(chat_id=7, since=0, epoch="e1")' in out and "part" in out


async def test_timeout_before_the_turn_starts_resumes_from_the_last_seq_seen():
    out, _ = await run([[ev(4, "text", content="x")]], busy=True, wait=5, since=3)
    assert "status: running" in out and "has not started yet" in out
    assert "since=4," in out


async def test_epoch_change_says_the_reply_may_be_lost():
    out, _ = await run([[]], epoch="old", served="new", since=2)
    assert out == srv.RESTARTED


async def test_gap_takes_the_last_assistant_message_after_turn_end():
    batch = [ev(9, "turn_start", **START), ev(10, "text", content="partial"), ev(11, "turn_end", status="done")]
    messages = [{"role": "assistant", "text": "older"}, {"role": "user", "text": "q"},
                {"role": "assistant", "text": "full answer"}, {"role": "user", "text": "later"}]
    out, _ = await run([batch], gap=True, messages=messages)
    assert out.endswith("reply:\nfull answer")


async def test_wait_reply_without_an_epoch_adopts_the_first_one():
    out, _ = await run([[ev(1, "turn_start", **START), ev(2, "turn_end", status="done")]], epoch=None)
    assert out.startswith("status: done")


WAKE = dict(provider="claude", model="opus", origin="wake")
USER = dict(provider="claude", model="opus", origin="user")


def test_wake_turn_is_never_the_reply_on_an_idle_chat():
    events = [ev(1, "turn_start", **WAKE), ev(2, "text", content="WAKE"), ev(3, "turn_end", status="done"),
              ev(4, "turn_start", **USER), ev(5, "text", content="MINE"), ev(6, "turn_end", status="done")]
    assert srv.analyze_reply(events)["text"] == "MINE"
    assert srv.analyze_reply(events[:3])["started"] is False


def test_wake_turn_between_the_old_turn_and_the_reply_is_skipped_when_busy():
    events = [ev(1, "turn_end", status="done"),
              ev(2, "turn_start", **WAKE), ev(3, "text", content="WAKE"), ev(4, "turn_end", status="done"),
              ev(5, "turn_start", **USER), ev(6, "text", content="MINE"), ev(7, "turn_end", status="done")]
    verdict = srv.analyze_reply(events, busy_at_send=True)
    assert verdict["text"] == "MINE" and verdict["ended"]


async def test_gap_with_the_turn_start_evicted_returns_the_stored_reply():
    batch = [ev(30, "tool_call", tool="t"), ev(31, "turn_end", status="done")]
    out, _ = await run([batch], gap=True, messages=[{"role": "assistant", "text": "stored answer"}])
    assert out.startswith("status: done")
    assert srv.STORED_NOTE in out and out.endswith("reply:\nstored answer")


async def test_gap_with_the_turn_still_open_keeps_waiting():
    batch = [ev(30, "tool_call", tool="t"), ev(31, "tool_call", tool="t")]
    out, _ = await run([batch], gap=True, wait=5, messages=[{"role": "assistant", "text": "old"}])
    assert "status: running" in out and "old" not in out
    assert 'gamachine_wait_reply(chat_id=7, since=31, epoch="e1")' in out


async def test_wait_reply_with_another_epoch_reports_the_restart():
    out, _ = await run([[ev(1, "turn_start", **START), ev(2, "turn_end", status="done")]],
                       epoch="old", served="new")
    assert out == srv.RESTARTED


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
