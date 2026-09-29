"""What a paired phone may do beyond watching (owner decision, 28 Sep 2026):
list a chat's slash commands and read / change the approval mode.

Same harness as test_remote_bridge.py (loopback relay, fake phone). The
functions the bridge is handed are the router's real ones wherever the point is
that the route and the phone share code; the CLI catalog itself is faked, a
real one would start Claude or Codex.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from remote import chats
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


@pytest.mark.parametrize("provider", ["api-anthropic", "api-openai", "opencode", "cursor", "kimi", "gemini"])
async def test_chats_without_a_catalog_get_only_compact_and_no_lookup(env, provider):
    asked = fake_catalog(env)
    phone = await pair_phone(env)
    conv = make_chat(env.db, provider=provider)
    r = await phone.request("list_slash_commands", chat_id=str(conv))
    assert r["result"] == ONLY_COMPACT
    assert asked == []


async def test_a_chat_nobody_answered_in_reads_as_claude(env):
    asked = fake_catalog(env)
    phone = await pair_phone(env)
    conv = env.db.create_conversation(1, "Yeni")
    r = await phone.request("list_slash_commands", chat_id=str(conv))
    assert r["result"] == WITH_COMPACT and asked == ["claude"]


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
    assert chats.slash_family({"provider": "claude"}) == "claude"
    assert chats.slash_family({"provider": None}) == "claude"
    assert chats.slash_family({"provider": "api-claude"}) is None
    assert chats.slash_family({"provider": "codex", "model": "anything"}) == "codex"


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
