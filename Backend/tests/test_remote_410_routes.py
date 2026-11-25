"""The phone runs the desktop renderer through the UI dispatcher
(remote/ui_dispatch.py): the 4.1.0 helper strip, fuse Continue, helper Stop
and the chat queue must cross it, with the routes' own ownership checks.
Requests run in-process against the real routers; `network_guard` fails a
test on any connection."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from agentic import approval_mode, subagents
from remote import rpc, ui_dispatch as d
from tests.test_remote_ui_dispatch import FakeSession, call
from tests.test_subagents import CODEX, _Runner, _ep, _idle, _main, env, network_guard  # noqa: F401

TOKEN = "app-token-for-410-routes"


@pytest.mark.parametrize("method,path", [
    ("GET", "/subagents/list"), ("GET", "/subagents/fuse"), ("POST", "/subagents/continue"),
    ("POST", "/subagents/stop"), ("GET", "/conversations/12/queue"),
    ("DELETE", "/conversations/12/queue/881"),
])
def test_the_410_routes_are_allowed(method, path):
    assert d.http_allowed(method, path) is True


@pytest.mark.parametrize("method,path", [
    ("POST", "/subagents/start"), ("POST", "/subagents/message"), ("GET", "/subagents/models"),
    ("GET", "/subagents/result"), ("DELETE", "/subagents/list"), ("POST", "/conversations/12/queue"),
    ("DELETE", "/conversations/12/queue"), ("PUT", "/agent-settings/script-tool"),
])
def test_the_agents_own_routes_stay_closed(method, path):
    assert d.http_allowed(method, path) is False


@pytest.fixture
def phone(env, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    approval_mode.set_ui_secret("ui-secret-for-410-routes")
    app = FastAPI()
    app.include_router(env.router)
    app.include_router(env.srouter)
    bridge = SimpleNamespace()
    disp = rpc.Dispatcher(bridge)
    bridge.rpc = disp
    disp.ui.app = app
    return SimpleNamespace(disp=disp, session=FakeSession())


async def _start(env, parent, name=None):
    """The agent tool's start, with the app token the phone fixture sets."""
    return await _ep(env.srouter, "/subagents/start")(
        body={"conversation_id": parent, "model": CODEX, "effort": "high", "brief": "say",
              "name": name}, x_session_token=TOKEN)


async def _http(phone, method, path, rid, **kw):
    r = await call(phone, rid=rid, type="ui_http", method=method, path=path, **kw)
    assert r["ok"], r
    return r["result"]["status"], json.loads(r["result"]["body"] or "null")


async def test_the_phone_lists_stops_continues_and_withdraws(env, phone):
    parent = _main(env)
    started = await _start(env, parent, name="telefon")
    child = started["chat_id"]
    _Runner.scripts[child] = [("text", {"content": "çalışıyorum"}), "hang"]
    await asyncio.sleep(0.02)
    status, body = await _http(phone, "GET", "/subagents/list", 1, query={"conversation_id": parent})
    assert status == 200 and body["agents"][0]["progress"]["phase"] == "writing"
    assert body["fuse"]["held"] == 0
    status, body = await _http(phone, "GET", "/subagents/fuse", 2, query={"conversation_id": parent})
    assert status == 200 and body["tripped"] is False
    env.in_flight.discard(parent)   # the phone's user, not a turn
    status, body = await _http(phone, "POST", "/subagents/stop", 3,
                               body={"conversation_id": parent, "agent_id": str(started["agent_id"])})
    assert status == 200 and body["status"] == "stopped"
    await _idle(parent, child)
    status, body = await _http(phone, "POST", "/subagents/continue", 4, body={"conversation_id": parent})
    assert status == 409 and body["detail"]["code"] == "nothing_held"

    mid = env.db.add_turn_input(parent, "user", "telefondan sıradaki", "user")
    status, body = await _http(phone, "GET", f"/conversations/{parent}/queue", 5)
    assert status == 200 and [q["id"] for q in body["queue"]] == [mid]
    status, body = await _http(phone, "DELETE", f"/conversations/{parent}/queue/{mid}", 6)
    assert status == 200 and body["status"] == "withdrawn" and body["queue"] == []


async def test_the_routes_check_the_chat_through_the_phone_too(env, phone):
    parent = _main(env)
    started = await _start(env, parent)
    await _idle()
    child = started["chat_id"]
    for rid, (method, path, kw) in enumerate([
            ("GET", "/subagents/list", {"query": {"conversation_id": parent + 999}}),
            ("GET", "/conversations/%d/queue" % (parent + 999), {}),
            ("POST", "/subagents/continue", {"body": {"conversation_id": parent + 999}})], 10):
        status, _ = await _http(phone, method, path, rid, **kw)
        assert status == 404, path
    # A helper's own chat cannot manage helpers (depth 1).
    status, _ = await _http(phone, "GET", "/subagents/list", 20, query={"conversation_id": child})
    assert status == 403
    assert subagents.fuse_state(parent)["held"] == 0
