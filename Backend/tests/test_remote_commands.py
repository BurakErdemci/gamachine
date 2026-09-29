"""What a paired phone may do beyond watching (owner decision, 28 Sep 2026):
list a chat's slash commands and read / change the approval mode.

Same harness as test_remote_bridge.py (loopback relay, fake phone). The
functions the bridge is handed are the router's real ones wherever the point is
that the route and the phone share code; the CLI catalog itself is faked, a
real one would start Claude or Codex.
"""
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, cards
from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS, register_gate
from providers.agy_provider import AgyStepGateError
from remote import chats
from remote.desktop_channel import CHANNEL
from routes.conversation_routes import create_conversation_router
from tests.test_remote_bridge import env, make_chat, pair_phone  # noqa: F401  (env is a fixture)

CATALOG = {"commands": ["usage", "review"], "skills": ["review"],
           "meta": [{"name": "usage", "description": "Show usage", "argumentHint": "[x]"}]}
COMPACT = chats.APP_COMMAND
# What the phone gets for CATALOG: Gamachine's own /compact leads.
WITH_COMPACT = {"commands": ["compact", "usage", "review"], "skills": ["review"],
                "meta": [COMPACT, {"name": "usage", "description": "Show usage", "argumentHint": "[x]"}]}
ONLY_COMPACT = {"commands": ["compact"], "skills": [], "meta": [COMPACT]}


def fake_catalog(env, catalog=None):
    """Install a catalog function on the bridge; returns the families it was asked for."""
    asked = []

    async def catalog_for(family):
        asked.append(family)
        return catalog if catalog is not None else CATALOG

    env.bridge.list_slash_commands = catalog_for
    return asked


# ── list_slash_commands ────────────────────────────────────────────────────

async def test_list_slash_commands_is_on_the_allow_list(env):
    assert "list_slash_commands" in env.bridge.rpc.handlers


@pytest.mark.parametrize("provider, family", [("claude", "claude"), ("codex", "codex"), ("agy", "agy")])
async def test_list_slash_commands_follows_the_chats_agent(env, provider, family):
    asked = fake_catalog(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db, provider=provider)
    r = await phone.request("list_slash_commands", chat_id=str(conv))
    assert r["ok"] is True and r["result"] == WITH_COMPACT
    assert asked == [family]


@pytest.mark.parametrize("stored", [("anthropic", "claude-sonnet-4-6"), ("openai", "gpt-5.5"),
                                    ("ollama", "llama3"), ("subscription", "opencode:x"),
                                    ("subscription", "cursor-gpt"), ("subscription", "kimi-k2")])
async def test_chats_without_a_catalog_get_only_compact_and_no_lookup(env, stored):
    asked = fake_catalog(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=stored)
    r = await phone.request("list_slash_commands", chat_id=str(conv))
    assert r["result"] == ONLY_COMPACT
    assert asked == []


async def test_a_chats_stored_model_wins_over_its_latest_answer(env):
    asked = fake_catalog(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db, provider="claude", stored=("subscription", "gpt-5.5"))
    r = await phone.request("list_slash_commands", chat_id=str(conv))
    assert r["result"] == WITH_COMPACT and asked == ["codex"]
    chat = next(c for c in (await phone.request("list_chats"))["result"]["chats"]
                if c["chat_id"] == str(conv))
    assert (chat["provider"], chat["model"]) == ("codex", "gpt-5.5")


async def test_an_old_chat_falls_back_to_its_latest_answer_then_the_global_default(env):
    phone = await pair_phone(env)
    answered = make_chat(env.db, provider="codex")
    fresh = env.db.create_conversation(1, "Yeni")
    env.db.save_ai_config(1, "subscription", "claude-opus-4-1", "")
    rows = {c["chat_id"]: c for c in (await phone.request("list_chats"))["result"]["chats"]}
    assert (rows[str(answered)]["provider"], rows[str(answered)]["model"]) == ("codex", "gpt-5.5")
    assert (rows[str(fresh)]["provider"], rows[str(fresh)]["model"]) == ("claude", "claude-opus-4-1")


async def test_an_api_chat_keeps_the_api_provider_label_on_the_phone(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db, stored=("anthropic", "claude-sonnet-4-6"))
    chat = next(c for c in (await phone.request("list_chats"))["result"]["chats"]
                if c["chat_id"] == str(conv))
    assert (chat["provider"], chat["model"]) == ("api-anthropic", "claude-sonnet-4-6")


async def test_list_slash_commands_validates_the_chat(env):
    asked = fake_catalog(env)
    phone = await pair_phone(env)
    assert (await phone.request("list_slash_commands", chat_id="999999"))["error"] == "unknown_chat"
    for bad in ("abc", True, 0, -3, None):
        r = await phone.request("list_slash_commands", chat_id=bad)
        assert r["error"] == "bad_chat_id", repr(bad)
    assert (await phone.request("list_slash_commands"))["error"] == "bad_chat_id"
    assert asked == []


async def test_list_slash_commands_needs_the_desktop_function(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db, provider="claude")
    r = await phone.request("list_slash_commands", chat_id=str(conv))
    assert r["ok"] is False and r["error"] == "unavailable"


async def test_the_catalog_reaches_the_phone_as_plain_strings_only(env):
    fake_catalog(env, {"commands": ["ok", 7, None, {"x": 1}], "skills": "not a list",
                       "meta": [{"name": "a", "description": "d", "insert": "/a ", "secret": "leak", "argumentHint": 5},
                                {"description": "no name"}, "junk", {"name": 3}]})
    phone = await pair_phone(env)
    conv = make_chat(env.db, provider="claude")
    r = await phone.request("list_slash_commands", chat_id=str(conv))
    assert r["result"] == {"commands": ["compact", "ok"], "skills": [],
                           "meta": [COMPACT, {"name": "a", "description": "d", "insert": "/a "}]}


async def test_the_clis_own_compact_is_not_listed_twice(env):
    fake_catalog(env, {"commands": ["usage", "compact"], "skills": [],
                       "meta": [{"name": "compact", "description": "CLI wording"}]})
    phone = await pair_phone(env)
    conv = make_chat(env.db, provider="claude")
    r = await phone.request("list_slash_commands", chat_id=str(conv))
    assert r["result"]["commands"] == ["usage", "compact"]
    assert r["result"]["meta"] == [{"name": "compact", "description": "CLI wording"}]


async def test_a_long_catalog_is_split_and_arrives_whole(env):
    meta = [{"name": f"skill-{i:04d}", "description": "ç" * 400} for i in range(2500)]
    fake_catalog(env, {"commands": [m["name"] for m in meta], "skills": [], "meta": meta})
    phone = await pair_phone(env)
    conv = make_chat(env.db, provider="claude")
    r = await phone.request("list_slash_commands", chat_id=str(conv), timeout=20)
    assert r["_parts"] >= 2
    assert [m["name"] for m in r["result"]["meta"]] == ["compact"] + [m["name"] for m in meta]
    assert len(r["result"]["commands"]) == 2501


def test_slash_family_is_the_one_place_that_maps_agents():
    def family(provider_type, model_name):
        return chats.slash_family({"provider_type": provider_type, "model_name": model_name})

    assert family("subscription", "claude-sonnet-4-6") == "claude"
    assert family("subscription", "") == "claude"
    assert family("subscription", "gpt-5.5") == "codex"
    assert family("subscription", "gemini-3-pro") == "agy"
    assert family("subscription", "kimi-k2") is None
    assert family("anthropic", "claude-sonnet-4-6") is None
    assert family("ollama", "llama3") is None


# ── the route and the phone share one catalog function ─────────────────────

async def test_route_and_bridge_use_the_same_catalog_function(env, monkeypatch):
    from providers import claude_sdk_session as sdk
    monkeypatch.setattr(sdk, "get_slash_commands", lambda: ["usage", "review"])
    monkeypatch.setattr(sdk, "get_skills", lambda: ["review"])
    monkeypatch.setattr(sdk, "get_commands_meta", lambda: [{"name": "usage", "description": "Shrink"}])
    router = create_conversation_router(env.db, {})
    app = FastAPI()
    app.include_router(router)
    env.bridge.list_slash_commands = router.list_slash_commands
    phone = await pair_phone(env)
    conv = make_chat(env.db, provider="claude")

    via_phone = (await phone.request("list_slash_commands", chat_id=str(conv)))["result"]
    via_route = TestClient(app).get("/slash-commands", params={"provider": "claude"}).json()
    # The route serves the CLI catalog as it is; the phone adds the app's own /compact.
    assert via_route == {"commands": ["usage", "review"], "skills": ["review"],
                         "meta": [{"name": "usage", "description": "Shrink"}]}
    assert via_phone == {"commands": ["compact", "usage", "review"], "skills": ["review"],
                         "meta": [COMPACT, {"name": "usage", "description": "Shrink"}]}
    agy = make_chat(env.db, title="Agy", provider="agy")
    assert (await phone.request("list_slash_commands", chat_id=str(agy)))["result"] == ONLY_COMPACT


def test_the_route_still_checks_the_token_before_any_catalog_work(monkeypatch):
    from providers import claude_sdk_session as sdk
    touched = []
    monkeypatch.setattr(sdk, "get_slash_commands", lambda: touched.append("catalog") or ["x"])
    monkeypatch.setenv("LOCAL_APP_TOKEN", "app-token")
    app = FastAPI()
    app.include_router(create_conversation_router(MagicMock(), {}))
    client = TestClient(app)
    assert client.get("/slash-commands", params={"provider": "claude"}).status_code == 401
    assert touched == []
    ok = client.get("/slash-commands", params={"provider": "claude"}, headers={"X-Session-Token": "app-token"})
    assert ok.status_code == 200 and ok.json()["commands"] == ["x"] and touched == ["catalog"]


# ── approval mode ──────────────────────────────────────────────────────────

def real_mode_path(env):
    """The bridge gets the router's own apply function, as main.py wires it."""
    router = create_conversation_router(env.db, {})
    env.bridge.apply_approval_mode = router.apply_approval_mode
    return router


def spy_set_mode(monkeypatch):
    seen = []
    real = approval_mode.set_mode

    def spy(mode, source="ui"):
        seen.append((mode, source))
        return real(mode, source=source)

    monkeypatch.setattr(approval_mode, "set_mode", spy)
    return seen


async def test_config_requests_are_on_the_allow_list(env):
    assert {"get_config", "set_approval_mode"} <= set(env.bridge.rpc.handlers)


async def test_get_config_reads_the_live_mode(env):
    phone = await pair_phone(env)
    assert (await phone.request("get_config"))["result"]["approval_mode"] == "step"
    approval_mode.set_mode("balanced", source="test")
    assert (await phone.request("get_config"))["result"]["approval_mode"] == "balanced"


async def test_set_approval_mode_needs_the_desktop_function(env):
    phone = await pair_phone(env)
    r = await phone.request("set_approval_mode", mode="auto")
    assert r["ok"] is False and r["error"] == "unavailable"
    assert approval_mode.current_mode() == "step"


@pytest.mark.parametrize("bad", [None, "", "plan", "AUTO", "auto ", 7, True, ["auto"], {"mode": "auto"}])
async def test_a_bad_mode_is_refused_before_anything_happens(env, bad):
    real_mode_path(env)
    phone = await pair_phone(env)
    q = CHANNEL.listen()
    try:
        r = await phone.request("set_approval_mode", mode=bad)
        assert r["ok"] is False and r["error"] == "bad_mode", repr(bad)
        assert (await phone.request("set_approval_mode"))["error"] == "bad_mode"
        assert approval_mode.current_mode() == "step" and q.empty()
    finally:
        CHANNEL.unlisten(q)


async def test_the_phone_sets_the_mode_through_the_shared_function(env, monkeypatch):
    real_mode_path(env)
    seen = spy_set_mode(monkeypatch)
    phone = await pair_phone(env)
    r = await phone.request("set_approval_mode", mode="balanced")
    assert r["ok"] is True
    assert r["result"] == {"mode": "balanced", "previous": "step", "approved_pending": 0}
    assert seen == [("balanced", "phone")]
    assert approval_mode.current_mode() == "balanced"
    r = await phone.request("set_approval_mode", mode="step")
    assert r["result"] == {"mode": "step", "previous": "balanced", "approved_pending": 0}
    assert (await phone.request("get_config"))["result"]["approval_mode"] == "step"


async def test_the_phones_choice_is_saved_like_the_desktops(env):
    approval_mode.bind_store(env.db)
    real_mode_path(env)
    phone = await pair_phone(env)
    await phone.request("set_approval_mode", mode="auto")
    assert env.db.get_setting("approval_mode") == "auto" and approval_mode.is_stored()


async def test_switching_to_auto_from_the_phone_approves_open_cards(env):
    real_mode_path(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    register_gate("gate-a", conv, tool="git", summary="git push")
    register_gate("gate-b", conv, tool="git", summary="git commit")
    r = await phone.request("set_approval_mode", mode="auto")
    assert r["result"] == {"mode": "auto", "previous": "step", "approved_pending": 2}
    for gate in ("gate-a", "gate-b"):
        assert APPROVAL_GATES[gate].is_set() and APPROVAL_RESULTS[gate] is True
    assert cards.list_pending() == []


async def test_switching_to_balanced_keeps_the_cards_it_cannot_classify(env):
    real_mode_path(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    register_gate("gate-c", conv, tool="git", summary="git push")
    r = await phone.request("set_approval_mode", mode="balanced")
    assert r["result"]["approved_pending"] == 0
    assert not APPROVAL_GATES["gate-c"].is_set() and len(cards.list_pending()) == 1


async def test_a_refused_step_switch_reports_why_and_changes_nothing(env, monkeypatch):
    real_mode_path(env)
    approval_mode.set_mode("auto", source="test")

    def refuse(mode, source="ui"):
        raise AgyStepGateError("Adım adım onay moduna geçilemedi (pid 4242).", code="agy_step_refused",
                               pids="4242")

    monkeypatch.setattr(approval_mode, "set_mode", refuse)
    phone = await pair_phone(env)
    q = CHANNEL.listen()
    try:
        r = await phone.request("set_approval_mode", mode="step")
        assert r["ok"] is False and r["error"] == "agy_step_refused"
        assert r["message"] == "Adım adım onay moduna geçilemedi (pid 4242)."
        assert r["params"] == {"pids": "4242"}
        assert q.empty(), "a refused switch is no news for the renderer"
    finally:
        CHANNEL.unlisten(q)
    monkeypatch.undo()
    assert approval_mode.current_mode() == "auto"


async def test_the_renderer_hears_about_a_phone_switch(env):
    real_mode_path(env)
    phone = await pair_phone(env, name="Burak'ın iPhone'u")
    conv = make_chat(env.db)
    register_gate("gate-d", conv, tool="git", summary="git push")
    q = CHANNEL.listen()
    try:
        await phone.request("set_approval_mode", mode="auto")
        frame = q.get_nowait()
        assert q.empty()
    finally:
        CHANNEL.unlisten(q)
    at = frame.pop("at")
    assert isinstance(at, int) and abs(at - time.time() * 1000) < 60_000
    assert frame == {"type": "approval_mode_changed", "mode": "auto", "previous": "step",
                     "approved_pending": 1, "by": "phone:Burak'ın iPhone'u"}


async def test_a_failed_notification_does_not_undo_or_fail_the_switch(env, monkeypatch):
    real_mode_path(env)

    def broken(frame):
        raise RuntimeError("stream closed")

    monkeypatch.setattr(CHANNEL, "publish", broken)
    phone = await pair_phone(env)
    r = await phone.request("set_approval_mode", mode="auto")
    assert r["ok"] is True and r["result"]["mode"] == "auto"
    assert approval_mode.current_mode() == "auto"


async def test_the_phone_switches_without_the_ui_secret_but_the_route_still_demands_it(env):
    router = real_mode_path(env)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert not approval_mode.ui_secret_configured()
    phone = await pair_phone(env)

    refused = client.post("/approval-mode", json={"mode": "auto"})
    assert refused.status_code == 403 and approval_mode.current_mode() == "step"
    wrong = client.post("/approval-mode", json={"mode": "auto"}, headers={"X-Gamachine-UI-Secret": "guess"})
    assert wrong.status_code == 403 and approval_mode.current_mode() == "step"

    r = await phone.request("set_approval_mode", mode="auto")
    assert r["ok"] is True and approval_mode.current_mode() == "auto"


async def test_route_and_phone_end_in_the_same_function(env, monkeypatch):
    router = real_mode_path(env)
    seen = spy_set_mode(monkeypatch)
    approval_mode.set_ui_secret("ui-secret")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    phone = await pair_phone(env)
    conv = make_chat(env.db)

    register_gate("gate-e", conv, tool="git", summary="git push")
    via_route = client.post("/approval-mode", json={"mode": "auto", "source": "settings"},
                            headers={"X-Gamachine-UI-Secret": "ui-secret"})
    assert via_route.status_code == 200
    assert via_route.json() == {"mode": "auto", "previous": "step", "approved_pending": 1}

    await phone.request("set_approval_mode", mode="step")
    register_gate("gate-f", conv, tool="git", summary="git push")
    via_phone = await phone.request("set_approval_mode", mode="auto")
    assert via_phone["result"] == {"mode": "auto", "previous": "step", "approved_pending": 1}
    assert seen == [("auto", "settings"), ("step", "phone"), ("auto", "phone")]
    assert APPROVAL_RESULTS["gate-e"] is True and APPROVAL_RESULTS["gate-f"] is True


async def test_the_route_answers_an_agy_refusal_as_before(env, monkeypatch):
    router = real_mode_path(env)
    approval_mode.set_ui_secret("ui-secret")

    def refuse(mode, source="ui"):
        raise AgyStepGateError("kapı kurulamadı", code="agy_step_refused", pids="9")

    monkeypatch.setattr(approval_mode, "set_mode", refuse)
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post("/approval-mode", json={"mode": "step"}, headers={"X-Gamachine-UI-Secret": "ui-secret"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "agy_step_refused", "message": "kapı kurulamadı", "pids": "9"}
