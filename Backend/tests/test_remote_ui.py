"""Desktop UI reports use the same fake phone and relay as effort reports."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

from routes.remote_routes import create_remote_router
from tests.test_remote_bridge import env, make_chat, pair_phone  # noqa: F401

TOKEN = "app-token-for-remote-ui"
H = {"X-Session-Token": TOKEN}


@pytest.fixture
async def api(env, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(create_remote_router(env.bridge))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def report(api, lang="tr", theme="arena", headers=H):
    return await api.put("/remote/desktop-ui", headers=headers, json={"lang": lang, "theme": theme})


@pytest.mark.parametrize("lang", ["tr", "en"])
@pytest.mark.parametrize("theme", ["arena", "sade", "pafta", "atolye"])
async def test_valid_reports_are_kept_and_broadcast(api, env, lang, theme):
    phones = [await pair_phone(env, "iPhone"), await pair_phone(env, "iPad")]
    r = await report(api, lang, theme)
    assert r.status_code == 200 and r.json() == {"changed": True}
    snapshot = {"lang": lang, "theme": theme}
    assert env.bridge.current_desktop_ui() == snapshot
    for phone in phones:
        assert await phone.next_push(lambda m: m.get("type") == "ui_changed") == {
            "type": "ui_changed", "desktop_ui": snapshot}


async def test_same_report_does_not_broadcast(api, env):
    phone = await pair_phone(env)
    await report(api)
    await phone.next_push(lambda m: m.get("type") == "ui_changed")
    assert (await report(api)).json() == {"changed": False}
    with pytest.raises(asyncio.TimeoutError):
        await phone.next_push(lambda m: m.get("type") == "ui_changed", timeout=0.4)
    assert (await report(api, theme="sade")).json() == {"changed": True}
    assert (await phone.next_push(lambda m: m.get("type") == "ui_changed"))["desktop_ui"] == {
        "lang": "tr", "theme": "sade"}


@pytest.mark.parametrize("body, code", [
    ({"lang": value, "theme": "arena"}, "bad_lang")
    for value in ("TR", " tr", "", None, True, 3, [], {})
] + [
    ({"lang": "tr", "theme": value}, "bad_theme")
    for value in ("Arena", "sade ", "", None, True, 3, [], {})
] + [({}, "bad_lang"), ({"lang": "tr"}, "bad_theme")])
async def test_invalid_report_clears_snapshot_and_broadcasts(api, env, body, code):
    phone = await pair_phone(env)
    await report(api)
    await phone.next_push(lambda m: m.get("type") == "ui_changed")
    r = await api.put("/remote/desktop-ui", headers=H, json=body)
    assert r.status_code == 400 and r.json()["detail"] == {"code": code}
    assert env.bridge.current_desktop_ui() is None
    assert await phone.next_push(lambda m: m.get("type") == "ui_changed") == {
        "type": "ui_changed", "desktop_ui": None}
    assert (await phone.request("get_config"))["result"]["desktop_ui"] is None


async def test_invalid_report_without_snapshot_does_not_broadcast(api, env):
    phone = await pair_phone(env)
    assert (await report(api, lang="bad")).status_code == 400
    with pytest.raises(asyncio.TimeoutError):
        await phone.next_push(lambda m: m.get("type") == "ui_changed", timeout=0.4)


async def test_route_requires_app_token(api, env):
    for headers in ({}, {"X-Session-Token": ""}, {"X-Session-Token": "wrong"}):
        assert (await report(api, headers=headers)).status_code == 401
    assert env.bridge.current_desktop_ui() is None


async def test_get_config_includes_unknown_then_reported_ui(api, env):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "claude-opus-5"))
    for args in ({}, {"chat_id": str(conv)}):
        assert (await phone.request("get_config", **args))["result"]["desktop_ui"] is None
    await report(api, "en", "atolye")
    for args in ({}, {"chat_id": str(conv)}):
        assert (await phone.request("get_config", **args))["result"]["desktop_ui"] == {
            "lang": "en", "theme": "atolye"}


async def test_returned_snapshot_is_a_copy(api, env):
    await report(api)
    snapshot = env.bridge.current_desktop_ui()
    snapshot["lang"] = "bad"
    snapshot["theme"] = "bad"
    assert env.bridge.current_desktop_ui() == {"lang": "tr", "theme": "arena"}
