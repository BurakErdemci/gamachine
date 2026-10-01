"""A paired phone shows and sets the desktop's effort (owner decision, 29 Sep 2026).

Effort is one state of the desktop renderer (`thinkingLevel`). The renderer
reports it (`PUT /remote/desktop-effort`); the backend keeps the last report and
tells phones when it changes (`effort_changed`); `get_config` returns it; and
`set_effort` sends the renderer a `remote_effort` frame, which the renderer
applies. Same harness as test_remote_model.py.
"""
import asyncio
import time

import httpx
import pytest
from fastapi import FastAPI

from providers.effort_caps import EFFORT_LEVELS, get_effort_caps
from remote.desktop_channel import CHANNEL, EFFORT_SET_TYPE
from routes.remote_routes import create_remote_router
from tests.test_remote_bridge import env, make_chat, pair_phone  # noqa: F401  (env is a fixture)

TOKEN = "app-token-for-remote-effort"
H = {"X-Session-Token": TOKEN}
OPUS = ["auto", "low", "medium", "high", "xhigh", "max"]


@pytest.fixture
async def api(env, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(create_remote_router(env.bridge))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def report(api, level="auto", levels=None, headers=H, ultracode=False):
    return await api.put("/remote/desktop-effort", headers=headers,
                         json={"level": level, "levels": OPUS if levels is None else levels,
                               "ultracode": ultracode})


def frames_of(queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


# ── set_effort ─────────────────────────────────────────────────────────────

async def test_set_effort_is_on_the_allow_list(env):
    assert "set_effort" in env.bridge.rpc.handlers


@pytest.mark.parametrize("level", EFFORT_LEVELS)
async def test_every_level_the_registry_can_return_goes_to_the_renderer(env, level):
    phone = await pair_phone(env, name="Burak'ın iPhone'u")
    q = CHANNEL.listen()
    try:
        r = await phone.request("set_effort", level=level)
        frames = frames_of(q)
    finally:
        CHANNEL.unlisten(q)
    assert r["ok"] is True and r["result"] == {"status": "accepted"}
    assert len(frames) == 1
    at = frames[0].pop("at")
    assert isinstance(at, int) and abs(at - time.time() * 1000) < 60_000
    assert frames[0] == {"type": EFFORT_SET_TYPE, "level": level, "by": "phone:Burak'ın iPhone'u"}
    assert EFFORT_SET_TYPE == "remote_effort"


async def test_the_backend_keeps_nothing_of_a_request(env):
    phone = await pair_phone(env)
    q = CHANNEL.listen()
    try:
        await phone.request("set_effort", level="high")
    finally:
        CHANNEL.unlisten(q)
    assert env.bridge.desktop_effort is None
    assert (await phone.request("get_config"))["result"]["desktop_effort"] is None


@pytest.mark.parametrize("level", ["turbo", "HIGH", " high", "high ", "", "None", 3, True, None, ["high"],
                                   {"level": "high"}])
async def test_a_level_off_the_scale_is_refused_and_sent_nowhere(env, level):
    phone = await pair_phone(env)
    q = CHANNEL.listen()
    try:
        r = await phone.request("set_effort", level=level)
        assert r["ok"] is False and r["error"] == "bad_effort", repr(level)
        assert q.empty()
    finally:
        CHANNEL.unlisten(q)


async def test_a_missing_level_is_refused(env):
    phone = await pair_phone(env)
    r = await phone.request("set_effort")
    assert r["ok"] is False and r["error"] == "bad_effort"


async def test_without_a_renderer_stream_the_desktop_is_not_ready(env):
    phone = await pair_phone(env)
    assert not CHANNEL.ready()
    r = await phone.request("set_effort", level="high")
    assert r["ok"] is True and r["result"] == {"status": "desktop_not_ready"}


async def test_a_failing_stream_is_not_ready_either(env):
    phone = await pair_phone(env)
    full = CHANNEL.listen()
    try:
        for _ in range(full.maxsize):
            full.put_nowait({})
        r = await phone.request("set_effort", level="high")
    finally:
        CHANNEL.unlisten(full)
    assert r["result"] == {"status": "desktop_not_ready"}


# ── the report route ───────────────────────────────────────────────────────

async def test_the_report_needs_the_app_token(api, env):
    for headers in ({}, {"X-Session-Token": ""}, {"X-Session-Token": "wrong"}):
        r = await report(api, headers=headers)
        assert r.status_code == 401, headers
    assert env.bridge.desktop_effort is None


async def test_the_report_needs_no_ui_secret(api):
    r = await report(api, "high")
    assert r.status_code == 200 and r.json() == {"changed": True}


async def test_the_report_is_kept_in_canonical_order(api, env):
    r = await report(api, "low", ["max", "low", "auto", "low"])
    assert r.status_code == 200
    assert env.bridge.desktop_effort == {"level": "low", "levels": ["auto", "low", "max"], "ultracode": False}


async def test_the_same_report_again_changes_nothing(api):
    assert (await report(api, "high")).json() == {"changed": True}
    assert (await report(api, "high")).json() == {"changed": False}
    assert (await report(api, "low")).json() == {"changed": True}
    assert (await report(api, "low", ["auto", "low"])).json() == {"changed": True}


@pytest.mark.parametrize("body, code", [
    ({"level": "turbo", "levels": OPUS}, "bad_level"),
    ({"level": "HIGH", "levels": OPUS}, "bad_level"),
    ({"level": 3, "levels": OPUS}, "bad_level"),
    ({"level": None, "levels": OPUS}, "bad_level"),
    ({"levels": OPUS}, "bad_level"),
    ({"level": "high", "levels": "high"}, "bad_levels"),
    ({"level": "high", "levels": []}, "bad_levels"),
    ({"level": "high"}, "bad_levels"),
    ({"level": "high", "levels": ["high", "turbo"]}, "bad_levels"),
    ({"level": "high", "levels": ["high", 4]}, "bad_levels"),
    ({"level": "high", "levels": [["high"]]}, "bad_levels"),
    ({"level": "high", "levels": EFFORT_LEVELS + ["auto"]}, "bad_levels"),
    ({"level": "max", "levels": ["auto", "low"]}, "level_not_offered"),
    ({"level": "high", "levels": OPUS, "ultracode": "yes"}, "bad_ultracode"),
    ({"level": "high", "levels": OPUS, "ultracode": 1}, "bad_ultracode"),
    ({"level": "high", "levels": OPUS, "ultracode": None}, "bad_ultracode"),
    ({}, "bad_level"),
])
async def test_a_bad_report_is_refused_and_clears_the_snapshot(api, env, body, code):
    assert (await report(api, "medium")).status_code == 200
    r = await api.put("/remote/desktop-effort", headers=H, json=body)
    assert r.status_code == 400 and r.json()["detail"] == {"code": code}, body
    # Not kept stale: what the desktop shows is unknown now, and a phone says so.
    assert env.bridge.desktop_effort is None


async def test_a_body_that_is_not_an_object_is_refused(api, env):
    for body in ([], "high", 7, None):
        r = await api.put("/remote/desktop-effort", headers=H, json=body)
        assert r.status_code == 422, body
    assert env.bridge.desktop_effort is None


# ── get_config ─────────────────────────────────────────────────────────────

async def test_get_config_says_unknown_until_the_renderer_reported(env):
    phone = await pair_phone(env)
    assert (await phone.request("get_config"))["result"] == {"approval_mode": "step", "desktop_effort": None, "desktop_ui": None}
    conv = make_chat(env.db, stored=("subscription", "claude-opus-5"))
    got = (await phone.request("get_config", chat_id=str(conv)))["result"]
    assert got["desktop_effort"] is None


async def test_get_config_returns_what_the_renderer_reported(api, env):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "claude-opus-5"))
    await report(api, "xhigh", OPUS)
    without_chat = (await phone.request("get_config"))["result"]
    assert without_chat == {"approval_mode": "step", "desktop_effort": {
        "level": "xhigh", "levels": OPUS, "ultracode": False}, "desktop_ui": None}
    with_chat = (await phone.request("get_config", chat_id=str(conv)))["result"]
    assert with_chat["desktop_effort"] == {"level": "xhigh", "levels": OPUS, "ultracode": False}
    # The chat's own model levels stay as they were, a different thing from the desktop's.
    assert with_chat["effort_levels"] == OPUS

    await report(api, "auto", ["auto"])
    assert (await phone.request("get_config"))["result"]["desktop_effort"] == {
        "level": "auto", "levels": ["auto"], "ultracode": False}


async def test_the_snapshot_a_phone_gets_cannot_edit_the_backends_copy(api, env):
    await report(api, "high")
    snapshot = env.bridge.current_desktop_effort()
    snapshot["levels"].append("junk")
    snapshot["level"] = "junk"
    assert env.bridge.desktop_effort == {"level": "high", "levels": OPUS, "ultracode": False}


# ── effort_changed ─────────────────────────────────────────────────────────

async def test_every_phone_hears_when_the_desktop_effort_changes(api, env):
    first = await pair_phone(env, "iPhone")
    second = await pair_phone(env, "iPad")
    await report(api, "high")
    for phone in (first, second):
        heard = await phone.next_push(lambda m: m.get("type") == "effort_changed")
        assert heard == {"type": "effort_changed", "desktop_effort": {
            "level": "high", "levels": OPUS, "ultracode": False}}

    await report(api, "max")
    for phone in (first, second):
        heard = await phone.next_push(lambda m: m.get("type") == "effort_changed")
        assert heard["desktop_effort"]["level"] == "max"


async def test_an_unchanged_report_is_not_repeated_to_phones(api, env):
    phone = await pair_phone(env)
    await report(api, "high")
    await phone.next_push(lambda m: m.get("type") == "effort_changed")
    await report(api, "high")
    await report(api, "high")
    with pytest.raises(asyncio.TimeoutError):
        await phone.next_push(lambda m: m.get("type") == "effort_changed", timeout=0.4)


async def test_a_refused_report_tells_nobody(api, env):
    phone = await pair_phone(env)
    await report(api, "turbo")
    with pytest.raises(asyncio.TimeoutError):
        await phone.next_push(lambda m: m.get("type") == "effort_changed", timeout=0.4)


async def test_a_report_with_no_phone_online_is_still_kept(api, env):
    assert (await report(api, "low")).status_code == 200
    phone = await pair_phone(env)
    assert (await phone.request("get_config"))["result"]["desktop_effort"] == {
        "level": "low", "levels": OPUS, "ultracode": False}


async def test_a_request_and_its_confirmation_meet_at_the_phone(api, env):
    """The whole loop: the phone asks, the renderer applies and reports, the phone hears it."""
    phone = await pair_phone(env)
    await report(api, "auto")
    await phone.next_push(lambda m: m.get("type") == "effort_changed")
    q = CHANNEL.listen()
    try:
        assert (await phone.request("set_effort", level="high"))["result"] == {"status": "accepted"}
        (frame,) = frames_of(q)
    finally:
        CHANNEL.unlisten(q)
    # What the renderer does with the frame (renderer/lib/remoteControl.ts useRemoteEffort):
    assert frame["level"] in OPUS
    await report(api, frame["level"])
    heard = await phone.next_push(lambda m: m.get("type") == "effort_changed")
    assert heard["desktop_effort"] == {"level": "high", "levels": OPUS, "ultracode": False}
    assert (await phone.request("get_config"))["result"]["desktop_effort"]["level"] == "high"


# ── levels the registry offers beyond the canonical scale ──────────────────

async def test_an_openai_api_report_with_none_is_accepted(api, env):
    """`none` is what the registry returns for OpenAI API models; a report of it
    used to be refused as bad_levels, leaving the phone on another model's levels."""
    phone = await pair_phone(env)
    await report(api, "high")
    await phone.next_push(lambda m: m.get("type") == "effort_changed")
    openai = get_effort_caps("openai", "gpt-5.5")["levels"]
    assert "none" in openai
    r = await report(api, "none", openai)
    assert r.status_code == 200 and r.json() == {"changed": True}
    heard = await phone.next_push(lambda m: m.get("type") == "effort_changed")
    assert heard["desktop_effort"] == {"level": "none", "levels": openai, "ultracode": False}


async def test_none_is_kept_in_scale_order(api, env):
    assert (await report(api, "auto", ["max", "none", "off", "auto"])).status_code == 200
    assert env.bridge.desktop_effort["levels"] == ["auto", "off", "none", "max"]


# ── Ultracode ──────────────────────────────────────────────────────────────

async def test_the_report_carries_whether_ultracode_is_on(api, env):
    phone = await pair_phone(env)
    await report(api, "high")
    await phone.next_push(lambda m: m.get("type") == "effort_changed")
    assert (await report(api, "high", ultracode=True)).json() == {"changed": True}
    heard = await phone.next_push(lambda m: m.get("type") == "effort_changed")
    assert heard["desktop_effort"] == {"level": "high", "levels": OPUS, "ultracode": True}
    assert (await phone.request("get_config"))["result"]["desktop_effort"]["ultracode"] is True
    assert (await report(api, "high", ultracode=True)).json() == {"changed": False}


async def test_a_report_without_the_field_means_ultracode_off(api, env):
    r = await api.put("/remote/desktop-effort", headers=H, json={"level": "high", "levels": OPUS})
    assert r.status_code == 200
    assert env.bridge.desktop_effort["ultracode"] is False


# ── a refused report ───────────────────────────────────────────────────────

async def test_a_refused_report_tells_phones_the_desktop_effort_is_unknown(api, env):
    phone = await pair_phone(env)
    await report(api, "high")
    await phone.next_push(lambda m: m.get("type") == "effort_changed")
    assert (await report(api, "turbo")).status_code == 400
    heard = await phone.next_push(lambda m: m.get("type") == "effort_changed")
    assert heard == {"type": "effort_changed", "desktop_effort": None}
    assert (await phone.request("get_config"))["result"]["desktop_effort"] is None
    # A good report brings it back.
    assert (await report(api, "low")).status_code == 200
    assert (await phone.request("get_config"))["result"]["desktop_effort"]["level"] == "low"
