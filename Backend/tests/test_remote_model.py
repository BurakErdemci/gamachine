"""A paired phone switches a chat's provider/model (owner decision, 28 Sep 2026):
`get_config {chat_id}`, `list_models`, `set_model`.

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
from tests.test_remote_bridge import env, make_chat, pair_phone  # noqa: F401  (env is a fixture)


def levels(provider_type, model_name):
    return get_effort_caps(provider_type, model_name)["levels"]


# ── get_config {chat_id} ───────────────────────────────────────────────────

async def test_the_new_requests_are_on_the_allow_list(env):
    assert {"list_models", "set_model"} <= set(env.bridge.rpc.handlers)


async def test_get_config_without_a_chat_is_unchanged(env):
    phone = await pair_phone(env)
    assert (await phone.request("get_config"))["result"] == {"approval_mode": "step"}
    assert (await phone.request("get_config", chat_id=None))["result"] == {"approval_mode": "step"}


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
    assert r["result"] == {"approval_mode": "step", "provider_type": stored[0], "model_name": stored[1],
                           "family": family, "effort_levels": levels(*stored)}


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


async def test_set_model_stores_the_chats_model_and_leaves_the_default_alone(env):
    keyed(env)
    env.db.save_ai_config(1, "subscription", "claude-opus-5", "")
    default_before = env.db.get_ai_config(1)
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("subscription", "gpt-6-sol"))
    other = make_chat(env.db, title="Other", stored=("subscription", "gpt-6-luna"))

    r = await switch(phone, conv)
    assert r["ok"] is True and r["result"] == {"provider_type": "openai", "model_name": "gpt-5.5"}

    assert chat_model.chat_model(env.db, 1, conv) == {"provider_type": "openai", "model_name": "gpt-5.5"}
    assert chat_model.chat_model(env.db, 1, other) == {"provider_type": "subscription", "model_name": "gpt-6-luna"}
    assert env.db.get_ai_config(1) == default_before
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
