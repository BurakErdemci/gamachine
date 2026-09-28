"""Desktop routes of remote control and the renderer hand-off of phone messages."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

import routes.conversation_routes as cr
from agentic import approval_mode
from remote.desktop_channel import CHANNEL
from routes.remote_routes import create_remote_router
from tests.remote_fakes import FakePhone
from tests.test_remote_bridge import env, make_chat, until  # noqa: F401  (fixture)

TOKEN = "app-token-for-remote-routes"
SECRET = "ui-secret-for-remote-routes"
H = {"X-Session-Token": TOKEN}
UI = {**H, "X-Gamachine-UI-Secret": SECRET}


@pytest.fixture
async def api(env, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    approval_mode.set_ui_secret(SECRET)
    app = FastAPI()
    app.include_router(create_remote_router(env.bridge))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_every_route_needs_the_app_token(api):
    for method, path in (("GET", "/remote/status"), ("POST", "/remote/disable"), ("POST", "/remote/forget"),
                         ("GET", "/remote/pair/pending"), ("POST", "/remote/pair/reject"),
                         ("GET", "/remote/devices"), ("DELETE", "/remote/devices/x"),
                         ("DELETE", "/remote/devices"), ("GET", "/remote/relay-url"),
                         ("GET", "/remote/keep-awake"), ("POST", "/remote/enable"),
                         ("POST", "/remote/pair/start"), ("POST", "/remote/pair/approve")):
        assert (await api.request(method, path)).status_code == 401, path


async def test_opening_routes_need_the_ui_secret(api, env):
    for method, path, body in (("POST", "/remote/enable", None), ("POST", "/remote/pair/start", None),
                               ("POST", "/remote/pair/approve", None),
                               ("PUT", "/remote/relay-url", {"url": None})):
        r = await api.request(method, path, headers=H, json=body)
        assert r.status_code == 403, path
    assert env.bridge.store.enabled() is False and env.relay.pc_requests == []


async def test_full_flow_over_the_routes(api, env):
    status = (await api.get("/remote/status", headers=H)).json()
    assert status["enabled"] is False and status["connected"] is False
    assert (await api.post("/remote/pair/start", headers=UI)).status_code == 409
    assert (await api.post("/remote/enable", headers=UI)).json()["enabled"] is True
    await until(lambda: env.bridge.status()["connected"])
    start = (await api.post("/remote/pair/start", headers=UI)).json()
    assert start["qr_url"].startswith(f"{env.relay.url}/p#") and start["expires_at"] > 0
    phone = FakePhone(env.relay, "iPhone")
    task = asyncio.create_task(phone.pair(start["qr_url"]))
    await until(lambda: env.bridge.pending_pairing() is not None)
    pending = (await api.get("/remote/pair/pending", headers=H)).json()["pending"]
    assert pending["sas"] == phone.sas and pending["device_name"] == "iPhone" and pending["source"] == "qr"
    approved = (await api.post("/remote/pair/approve", headers=UI)).json()
    assert "ok" in await task
    devices = (await api.get("/remote/devices", headers=H)).json()["devices"]
    assert [d["device_id"] for d in devices] == [approved["device"]["device_id"]] == [phone.device_id]
    assert (await api.delete(f"/remote/devices/{phone.device_id}", headers=H)).json() == {"removed": phone.device_id}
    assert (await api.delete("/remote/devices/nope", headers=H)).status_code == 404
    assert (await api.post("/remote/pair/approve", headers=UI)).status_code == 404
    r = await api.put("/remote/keep-awake", headers=H, json={"enabled": True})
    assert r.json() == {"keep_awake": True, "keep_awake_active": True}
    assert (await api.put("/remote/keep-awake", headers=H, json={"enabled": "yes"})).status_code == 400
    assert (await api.post("/remote/disable", headers=H)).json()["keep_awake_active"] is False
    forgot = (await api.post("/remote/forget", headers=H)).json()
    assert forgot["enabled"] is False and forgot["pair_id"] is None


async def test_relay_url_setting(api, env):
    r = await api.get("/remote/relay-url", headers=H)
    assert r.json()["relay_url"] == env.relay.url and r.json()["custom"] is True
    for bad in ("http://relay.example", "ftp://x", "https://relay.example/path", "https://u:p@relay.example"):
        assert (await api.put("/remote/relay-url", headers=UI, json={"url": bad})).status_code == 400, bad
    r = await api.put("/remote/relay-url", headers=UI, json={"url": None})
    assert r.json()["relay_url"] == "https://gamachine-relay.erdemciburakemre.workers.dev"
    assert (await api.get("/remote/relay-url", headers=H)).json()["custom"] is False
    r = await api.put("/remote/relay-url", headers=UI, json={"url": "https://relay.example.org/"})
    assert r.json()["relay_url"] == "https://relay.example.org"
    # Setting the URL while off opens no connection.
    assert env.relay.pc_requests == []


async def test_wake_stream_all_carries_phone_messages(env, monkeypatch):
    monkeypatch.setattr(cr, "WAKE_ALL_POLL_S", 0.05)
    router = cr.create_conversation_router(env.db, {})
    endpoint = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/wake-stream-all")
    response = await endpoint(x_session_token="")
    stream = response.body_iterator
    first = asyncio.ensure_future(stream.__anext__())
    await until(lambda: CHANNEL.ready())
    conv = make_chat(env.db)
    frame = {"type": "remote_message", "request_id": "r1", "conversation_id": conv, "text": "merhaba",
             "source": "phone", "device_id": "d", "device_name": "iPhone", "at": 1}
    assert CHANNEL.publish(frame) is True
    chunk = await asyncio.wait_for(first, 5)
    while chunk.startswith(":"):
        chunk = await asyncio.wait_for(stream.__anext__(), 5)
    assert chunk.startswith("data: ") and '"remote_message"' in chunk and '"merhaba"' in chunk
    await stream.aclose()
    assert CHANNEL.ready() is False
