"""A paired phone switches a chat's provider/model (owner decisions, 28 and 29 Sep
2026): `get_config {chat_id}`, `list_models`, `set_model`. The phone pick has the
effects of the desktop picker's (`chat_model.pick_chat_model`).

Same harness as test_remote_commands.py (loopback relay, fake phone, real
database). Readiness is decided by an API key in the database, or by a patched
CLI probe, so no test depends on what is installed on the machine.
"""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from agentic import chat_model
from providers import model_catalog
from providers.effort_caps import get_effort_caps
from remote.desktop_channel import CHANNEL
from routes.config_routes import create_config_router
from schemas import AIConfigRequest
from tests.test_remote_bridge import env, make_chat, pair_phone  # noqa: F401  (env is a fixture)


def levels(provider_type, model_name):
    return get_effort_caps(provider_type, model_name)["levels"]


# ── get_config {chat_id} ───────────────────────────────────────────────────

async def test_the_new_requests_are_on_the_allow_list(env):
    assert {"list_models", "set_model"} <= set(env.bridge.rpc.handlers)


async def test_get_config_without_a_chat_is_unchanged(env):
    phone = await pair_phone(env)
    # The desktop's effort is null until its renderer reports (test_remote_effort.py).
    unchanged = {"approval_mode": "step", "desktop_effort": None}
    assert (await phone.request("get_config"))["result"] == unchanged
    assert (await phone.request("get_config", chat_id=None))["result"] == unchanged


@pytest.mark.parametrize("stored, family", [
    (("subscription", "claude-opus-5"), "claude"),
    (("subscription", "gpt-6-luna"), "codex"),
    (("subscription", "gemini-3.8-flash"), "agy"),
    (("subscription", "opencode:opencode/big-pickle"), "opencode"),
    (("openai", "gpt-5.5"), None),
    (("ollama", "llama3"), None),
])
async def test_get_config_with_a_chat_reports_its_model_and_effort_levels(env, stored, family):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=stored)
    r = await phone.request("get_config", chat_id=str(conv))
    assert r["ok"] is True
    assert r["result"] == {"approval_mode": "step", "desktop_effort": None, "provider_type": stored[0],
                           "model_name": stored[1], "family": family, "effort_levels": levels(*stored)}


async def test_get_config_follows_the_chats_own_model_not_the_default(env):
    phone = await pair_phone(env)
    env.db.save_ai_config(1, "subscription", "claude-haiku-4-5", "")
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    result = (await phone.request("get_config", chat_id=conv))["result"]
    assert (result["provider_type"], result["model_name"]) == ("subscription", "gpt-6-sol")
    assert "max" in result["effort_levels"] and "minimal" not in result["effort_levels"]


async def test_a_model_without_effort_control_reports_what_the_registry_says(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "claude-haiku-4-5"))
    assert (await phone.request("get_config", chat_id=str(conv)))["result"]["effort_levels"] == ["auto"]


async def test_get_config_with_a_bad_chat_is_refused(env):
    phone = await pair_phone(env)
    assert (await phone.request("get_config", chat_id="999999"))["error"] == "unknown_chat"
    for bad in ("abc", True, 0, -3, [1]):
        assert (await phone.request("get_config", chat_id=bad))["error"] == "bad_chat_id", repr(bad)


# ── list_models ────────────────────────────────────────────────────────────

OR_CATALOG = {"openai/gpt-9": {"name": "OpenAI: GPT-9", "context_length": 400000,
                               "pricing": {"prompt": "0.00001"}}}


def fake_network(forced):
    """The catalog's outbound reads, all faked; `forced` collects each `force` flag."""
    def live(provider, key, force=False):
        forced.append(force)
        return {"claude-opus-5": "Claude Opus 5"} if provider == "anthropic" and key else None

    def openrouter(force=False):
        forced.append(force)
        return OR_CATALOG

    return (patch("providers.model_catalog.list_models", side_effect=live),
            patch("providers.model_catalog.openrouter_catalog", side_effect=openrouter),
            patch("urllib.request.urlopen", side_effect=OSError("closed")))


async def test_list_models_needs_the_desktop_function(env):
    phone = await pair_phone(env)
    r = await phone.request("list_models")
    assert r["ok"] is False and r["error"] == "unavailable"


async def test_the_route_and_the_phone_share_one_catalog_builder(env):
    env.db.save_api_key(1, "anthropic", "sk-test")
    router = create_config_router(env.db)
    env.bridge.list_models = router.list_models
    route = next(r for r in router.routes if r.path == "/available-models")
    phone = await pair_phone(env)
    forced = []
    p1, p2, p3 = fake_network(forced)
    with p1, p2, p3, patch("routes.config_routes._check_token"), \
            patch("routes.config_routes.get_current_user", return_value=(1, None)):
        via_route = await route.endpoint(refresh=False, x_session_token="t")
        via_phone = (await phone.request("list_models", timeout=20))["result"]
    via_phone.pop("_parts", None)
    assert via_phone == via_route
    assert any(m["id"] == "claude-opus-5" and m["available"] for m in via_phone["cloud"])
    assert {"local", "cloud", "subscription", "cloud_sources"} <= set(via_phone)
    assert via_phone["cloud_sources"]["anthropic"] == "live"
    assert forced and not any(forced), "the phone must never force a refresh"


async def test_the_phone_asks_for_the_local_users_catalog_without_a_refresh(env):
    seen = []

    async def catalog(user_id, refresh=False):
        seen.append((user_id, refresh))
        return {"local": [], "cloud": [], "subscription": []}

    env.bridge.list_models = catalog
    phone = await pair_phone(env)
    assert (await phone.request("list_models"))["result"] == {"local": [], "cloud": [], "subscription": []}
    assert seen == [(1, False)]


async def test_the_desktop_route_still_throttles_and_falls_back_without_a_user(env):
    router = create_config_router(MagicMock())
    route = next(r for r in router.routes if r.path == "/available-models")
    p1, p2, p3 = fake_network([])
    with p1, p2, p3, patch("routes.config_routes._check_token"), \
            patch("routes.config_routes.get_current_user", side_effect=PermissionError("no session")):
        out = await route.endpoint(refresh=False, x_session_token="bad")
    assert out["cloud"] == [] and out["cloud_sources"] == {} and out["subscription"]


# ── set_model ──────────────────────────────────────────────────────────────

def keyed(env, provider="openai"):
    env.db.save_api_key(1, provider, "sk-test")


async def switch(phone, conv, provider_type="openai", model_name="gpt-5.5", **extra):
    return await phone.request("set_model", chat_id=str(conv), provider_type=provider_type,
                               model_name=model_name, **extra)


async def test_set_model_stores_the_chats_model_and_moves_the_default_for_new_chats(env):
    keyed(env)
    env.db.save_ai_config(1, "subscription", "claude-opus-5", "")
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    other = make_chat(env.db, title="Other", stored=("subscription", "gpt-6-luna"))

    r = await switch(phone, conv)
    assert r["ok"] is True and r["result"] == {"provider_type": "openai", "model_name": "gpt-5.5"}

    assert chat_model.chat_model(env.db, 1, conv) == {"provider_type": "openai", "model_name": "gpt-5.5"}
    assert chat_model.chat_model(env.db, 1, other) == {"provider_type": "subscription", "model_name": "gpt-6-luna"}
    assert env.db.get_ai_config(1)[:2] == ("openai", "gpt-5.5")
    got = await phone.request("get_config", chat_id=str(conv))
    assert (got["result"]["provider_type"], got["result"]["model_name"]) == ("openai", "gpt-5.5")
    chat = next(c for c in (await phone.request("list_chats"))["result"]["chats"] if c["chat_id"] == str(conv))
    assert (chat["provider"], chat["model"]) == ("api-openai", "gpt-5.5")


async def test_set_model_accepts_a_typed_id_and_the_provider_default(env):
    keyed(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    assert (await switch(phone, conv, model_name="some-new-model-7"))["result"]["model_name"] == "some-new-model-7"
    assert (await switch(phone, conv, model_name=""))["result"] == {"provider_type": "openai", "model_name": ""}


async def test_the_renderer_hears_about_a_phone_model_switch(env):
    keyed(env)
    phone = await pair_phone(env, name="Burak'ın iPhone'u")
    conv = make_chat(env.db)
    q = CHANNEL.listen()
    try:
        await switch(phone, conv)
        frame = q.get_nowait()
        assert q.empty()
    finally:
        CHANNEL.unlisten(q)
    at = frame.pop("at")
    assert isinstance(at, int) and abs(at - time.time() * 1000) < 60_000
    assert frame == {"type": "chat_model_changed", "conversation_id": conv, "provider_type": "openai",
                     "model_name": "gpt-5.5", "by": "phone:Burak'ın iPhone'u"}


@pytest.mark.parametrize("extra, error", [
    ({"provider_type": "nope"}, "unknown_provider"),
    ({"provider_type": None}, "unknown_provider"),
    ({"provider_type": ["openai"]}, "unknown_provider"),
    ({"provider_type": 7}, "unknown_provider"),
    ({"model_name": None}, "bad_model"),
    ({"model_name": 5}, "bad_model"),
    ({"model_name": "gpt\n5"}, "bad_model"),
    ({"model_name": "x" * 201}, "bad_model"),
])
async def test_a_bad_choice_is_refused_and_changes_nothing(env, extra, error):
    keyed(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    q = CHANNEL.listen()
    try:
        args = {"provider_type": "openai", "model_name": "gpt-5.5", **extra}
        r = await phone.request("set_model", chat_id=str(conv), **args)
        assert r["ok"] is False and r["error"] == error, r
        assert q.empty()
    finally:
        CHANNEL.unlisten(q)
    assert chat_model.chat_model(env.db, 1, conv) == {"provider_type": "subscription", "model_name": "gpt-6-sol"}


async def test_missing_fields_are_refused(env):
    keyed(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    r = await phone.request("set_model", chat_id=str(conv), model_name="gpt-5.5")
    assert r["error"] == "unknown_provider"
    r = await phone.request("set_model", chat_id=str(conv), provider_type="openai")
    assert r["error"] == "bad_model"


async def test_set_model_validates_the_chat(env):
    keyed(env)
    phone = await pair_phone(env)
    assert (await switch(phone, 999999))["error"] == "unknown_chat"
    for bad in ("abc", True, 0, -3, None):
        r = await phone.request("set_model", chat_id=bad, provider_type="openai", model_name="gpt-5.5")
        assert r["error"] == "bad_chat_id", repr(bad)
    assert (await phone.request("set_model", provider_type="openai", model_name="gpt-5.5"))["error"] == "bad_chat_id"


async def test_a_side_chat_is_not_reachable(env):
    keyed(env)
    phone = await pair_phone(env)
    parent = make_chat(env.db)
    side = env.db.create_side_chat(parent, 1)
    assert (await switch(phone, side))["error"] == "unknown_chat"


async def test_a_provider_that_is_not_ready_is_refused_with_what_it_needs(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    q = CHANNEL.listen()
    try:
        r = await switch(phone, conv, "anthropic", "claude-opus-5")
        assert r["ok"] is False and r["error"] == "not_ready" and r["needs"] == "apikey"
        assert q.empty()
    finally:
        CHANNEL.unlisten(q)
    assert chat_model.chat_model(env.db, 1, conv) == {"provider_type": "subscription", "model_name": "gpt-6-sol"}


@pytest.mark.parametrize("state, needs", [({"installed": False, "loggedIn": None}, "install"),
                                          ({"installed": True, "loggedIn": False}, "login")])
async def test_a_cli_that_is_missing_or_logged_out_is_not_ready(env, monkeypatch, state, needs):
    monkeypatch.setattr(chat_model, "_cli_state", lambda family: state)
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("openai", "gpt-5.5"))
    r = await switch(phone, conv, "subscription", "gpt-6-luna")
    assert r["error"] == "not_ready" and r["needs"] == needs
    assert chat_model.chat_model(env.db, 1, conv)["provider_type"] == "openai"


async def test_a_cli_that_is_ready_switches(env, monkeypatch):
    monkeypatch.setattr(chat_model, "_cli_state", lambda family: {"installed": True, "loggedIn": None})
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("openai", "gpt-5.5"))
    r = await switch(phone, conv, "subscription", "gpt-6-luna")
    assert r["result"] == {"provider_type": "subscription", "model_name": "gpt-6-luna"}


async def test_a_local_model_needs_the_ollama_service(env, monkeypatch):
    monkeypatch.setattr(chat_model, "_ollama_up", lambda: False)
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    r = await switch(phone, conv, "ollama", "llama3")
    assert r["error"] == "not_ready" and r["needs"] == "service"
    monkeypatch.setattr(chat_model, "_ollama_up", lambda: True)
    assert (await switch(phone, conv, "ollama", "llama3"))["result"]["provider_type"] == "ollama"


async def test_a_failed_notification_does_not_undo_or_fail_the_switch(env, monkeypatch):
    keyed(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db)

    def boom(frame):
        raise RuntimeError("stream closed")

    monkeypatch.setattr(CHANNEL, "publish", boom)
    r = await switch(phone, conv)
    monkeypatch.undo()
    assert r["ok"] is True
    assert chat_model.chat_model(env.db, 1, conv)["model_name"] == "gpt-5.5"


async def test_a_running_turn_keeps_the_model_it_started_with(env):
    # A turn resolves its model once, when it starts (`turn_model`); a switch
    # after that lands on the next turn, as on the desktop.
    keyed(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    started = chat_model.turn_model(env.db, 1, conv)
    await switch(phone, conv)
    assert started == ("subscription", "gpt-6-sol")
    assert chat_model.turn_model(env.db, 1, conv) == ("openai", "gpt-5.5")


# ── send_message {effort} ──────────────────────────────────────────────────

async def send(phone, conv, **extra):
    return await phone.request("send_message", chat_id=str(conv), text="hi", **extra)


async def test_send_message_without_effort_is_what_it_was(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "claude-opus-5"))
    q = CHANNEL.listen()
    try:
        assert (await send(phone, conv))["result"] == {"status": "accepted"}
        assert (await send(phone, conv, effort=None))["result"] == {"status": "accepted"}
        frames = [q.get_nowait(), q.get_nowait()]
    finally:
        CHANNEL.unlisten(q)
    assert all("effort" not in f for f in frames)


@pytest.mark.parametrize("stored", [("subscription", "claude-opus-5"), ("subscription", "gpt-6-luna"),
                                    ("openai", "gpt-5.5"), ("subscription", "claude-haiku-4-5")])
async def test_every_level_the_registry_lists_for_the_chat_is_carried(env, stored):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=stored)
    q = CHANNEL.listen()
    try:
        for level in levels(*stored):
            r = await send(phone, conv, effort=level)
            assert r["result"] == {"status": "accepted"}, (stored, level)
            assert q.get_nowait()["effort"] == level
    finally:
        CHANNEL.unlisten(q)


@pytest.mark.parametrize("stored, effort", [
    (("subscription", "claude-haiku-4-5"), "high"),
    (("subscription", "claude-opus-4-6"), "xhigh"),
    (("subscription", "gpt-6-luna"), "minimal"),
    (("subscription", "kimi-k3"), "low"),
    (("subscription", "claude-opus-5"), "turbo"),
    (("subscription", "claude-opus-5"), "HIGH"),
    (("subscription", "claude-opus-5"), " high"),
    (("subscription", "claude-opus-5"), ""),
    (("subscription", "claude-opus-5"), 3),
    (("subscription", "claude-opus-5"), True),
    (("subscription", "claude-opus-5"), ["high"]),
    (("subscription", "claude-opus-5"), {"level": "high"}),
])
async def test_an_effort_the_chats_model_does_not_accept_is_refused(env, stored, effort):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=stored)
    q = CHANNEL.listen()
    try:
        r = await send(phone, conv, effort=effort)
        assert r["ok"] is False and r["error"] == "bad_effort", repr(effort)
        assert q.empty()
    finally:
        CHANNEL.unlisten(q)


async def test_effort_is_judged_against_the_model_the_chat_has_now(env):
    keyed(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "claude-opus-5"))
    q = CHANNEL.listen()
    try:
        assert (await send(phone, conv, effort="max"))["result"] == {"status": "accepted"}
        await switch(phone, conv, "openai", "gpt-5.5")
        assert "max" not in levels("openai", "gpt-5.5")
        assert (await send(phone, conv, effort="max"))["error"] == "bad_effort"
    finally:
        CHANNEL.unlisten(q)


async def test_a_bad_effort_does_not_hide_the_other_refusals(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "claude-opus-5"))
    assert (await send(phone, 999999, effort="high"))["error"] == "unknown_chat"
    r = await phone.request("send_message", chat_id=str(conv), text="  ", effort="high")
    assert r["error"] == "bad_text"


# ── the phone pick is the desktop pick ─────────────────────────────────────

def _save_route(db):
    router = create_config_router(db)
    return next(r for r in router.routes if r.path == "/save-ai-config").endpoint


async def test_a_new_chat_opens_on_the_model_a_phone_picked(env):
    keyed(env)
    env.db.save_ai_config(1, "subscription", "claude-opus-5", "")
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    assert (await switch(phone, conv))["ok"] is True
    fresh = env.db.create_conversation(1, "Fresh")
    assert chat_model.chat_model(env.db, 1, fresh) == {"provider_type": "openai", "model_name": "gpt-5.5"}
    got = (await phone.request("get_config", chat_id=str(fresh)))["result"]
    assert (got["provider_type"], got["model_name"]) == ("openai", "gpt-5.5")


async def test_the_phone_pick_and_the_desktop_pick_have_the_same_effects(env):
    keyed(env)
    phone = await pair_phone(env)
    desktop_chat = make_chat(env.db, "Desktop", stored=("subscription", "gpt-6-sol"))
    phone_chat = make_chat(env.db, "Phone", stored=("subscription", "gpt-6-sol"))
    bystander = make_chat(env.db, "Bystander", stored=("subscription", "gpt-6-luna"))

    def effects(conv):
        fresh = env.db.create_conversation(1, "Fresh")
        return {"chat": env.db.get_conversation_model(conv), "default": env.db.get_ai_config(1)[:2],
                "new chat": chat_model.chat_model(env.db, 1, fresh),
                "bystander": env.db.get_conversation_model(bystander)}

    env.db.save_ai_config(1, "subscription", "claude-opus-5", "")
    await _save_route(env.db)(AIConfigRequest(user_id=1, provider_type="openai", model_name="gpt-5.5",
                                              api_key="", conversation_id=desktop_chat), x_session_token="")
    via_desktop = effects(desktop_chat)

    env.db.save_ai_config(1, "subscription", "claude-opus-5", "")
    assert (await switch(phone, phone_chat))["ok"] is True
    via_phone = effects(phone_chat)

    assert via_phone == via_desktop
    assert via_phone["default"] == ("openai", "gpt-5.5") and via_phone["bystander"] == ("subscription", "gpt-6-luna")


async def test_a_refused_phone_pick_leaves_the_default_alone(env):
    env.db.save_ai_config(1, "subscription", "claude-opus-5", "")
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    assert (await switch(phone, conv, "anthropic", "claude-opus-5"))["error"] == "not_ready"
    assert (await switch(phone, conv, "nope", "x"))["error"] == "unknown_provider"
    assert (await switch(phone, conv, "openai", "gpt\n5"))["error"] == "bad_model"
    assert env.db.get_ai_config(1)[:2] == ("subscription", "claude-opus-5")


async def test_every_phone_hears_of_a_pick_whoever_made_it(env):
    keyed(env)
    picker = await pair_phone(env, "iPhone")
    other = await pair_phone(env, "iPad")
    conv = make_chat(env.db)

    await switch(picker, conv)
    for phone in (picker, other):
        heard = await phone.next_push(lambda m: m.get("type") == "chat_model_changed")
        assert heard == {"type": "chat_model_changed", "chat_id": str(conv), "provider_type": "openai",
                         "model_name": "gpt-5.5"}
        changed = await phone.next_push(lambda m: m.get("type") == "chat_changed")
        assert changed["chat"]["chat_id"] == str(conv) and changed["chat"]["model"] == "gpt-5.5"

    # The desktop's own pick reaches the phones the same way.
    await _save_route(env.db)(AIConfigRequest(user_id=1, provider_type="subscription", model_name="gpt-6-sol",
                                              api_key="", conversation_id=conv), x_session_token="")
    for phone in (picker, other):
        heard = await phone.next_push(lambda m: m.get("type") == "chat_model_changed")
        assert (heard["provider_type"], heard["model_name"]) == ("subscription", "gpt-6-sol")


async def test_nobody_is_told_of_a_refused_pick(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    await switch(phone, conv, "anthropic", "claude-opus-5")
    with pytest.raises(asyncio.TimeoutError):
        await phone.next_push(lambda m: m.get("type") == "chat_model_changed", timeout=0.4)


async def test_the_bridge_listens_to_picks_only_while_it_is_on(env):
    assert env.bridge._pick_listener not in chat_model._pick_listeners
    await pair_phone(env)
    assert env.bridge._pick_listener in chat_model._pick_listeners
    await env.bridge.disable()
    assert env.bridge._pick_listener not in chat_model._pick_listeners


async def test_a_listener_that_fails_does_not_undo_the_pick(env):
    keyed(env)
    conv = make_chat(env.db)

    def boom(*args):
        raise RuntimeError("listener broke")

    chat_model.add_pick_listener(boom)
    try:
        result = chat_model.pick_chat_model(env.db, 1, conv, "openai", "gpt-5.5")
    finally:
        chat_model.remove_pick_listener(boom)
    assert result == {"provider_type": "openai", "model_name": "gpt-5.5"}
    assert env.db.get_conversation_model(conv) == ("openai", "gpt-5.5")
    assert env.db.get_ai_config(1)[:2] == ("openai", "gpt-5.5")


# ── a pick is two writes, and the key comes first ──────────────────────────

def _fail(*args, **kwargs):
    raise RuntimeError("database is locked")


async def test_a_failing_key_write_moves_neither_the_chat_nor_the_default(env, monkeypatch):
    env.db.save_ai_config(1, "subscription", "claude-opus-5", "")
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    monkeypatch.setattr(env.db, "save_api_key", _fail)
    with pytest.raises(RuntimeError):
        await _save_route(env.db)(AIConfigRequest(user_id=1, provider_type="openai", model_name="gpt-5.5",
                                                  api_key="sk-new", conversation_id=conv), x_session_token="")
    assert env.db.get_conversation_model(conv) == ("subscription", "gpt-6-sol")
    assert env.db.get_ai_config(1)[:2] == ("subscription", "claude-opus-5")
    with pytest.raises(asyncio.TimeoutError):
        await phone.next_push(lambda m: m.get("type") == "chat_model_changed", timeout=0.4)


async def test_the_chat_row_changed_then_the_default_write_failed_phones_are_still_told(env, monkeypatch):
    keyed(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    monkeypatch.setattr(env.db, "save_ai_config", _fail)
    r = await switch(phone, conv)
    assert r["ok"] is False and r["error"] == "internal"
    assert env.db.get_conversation_model(conv) == ("openai", "gpt-5.5")
    heard = await phone.next_push(lambda m: m.get("type") == "chat_model_changed")
    assert (heard["chat_id"], heard["model_name"]) == (str(conv), "gpt-5.5")
    # What the phone re-reads is what the chat really has.
    assert (await phone.request("get_config", chat_id=str(conv)))["result"]["model_name"] == "gpt-5.5"


def test_pick_chat_model_tells_listeners_once_when_the_default_write_raises(env, monkeypatch):
    keyed(env)
    conv = make_chat(env.db)
    calls = []
    listener = lambda *a: calls.append(a)  # noqa: E731
    chat_model.add_pick_listener(listener)
    monkeypatch.setattr(env.db, "save_ai_config", _fail)
    try:
        with pytest.raises(RuntimeError, match="database is locked"):
            chat_model.pick_chat_model(env.db, 1, conv, "openai", "gpt-5.5")
    finally:
        chat_model.remove_pick_listener(listener)
    assert calls == [(conv, "openai", "gpt-5.5")]


def test_a_refused_pick_tells_no_listener(env):
    calls = []
    listener = lambda *a: calls.append(a)  # noqa: E731
    chat_model.add_pick_listener(listener)
    conv = make_chat(env.db)
    try:
        with pytest.raises(chat_model.ChatModelError):
            chat_model.pick_chat_model(env.db, 1, conv, "nope", "x")
    finally:
        chat_model.remove_pick_listener(listener)
    assert calls == []


