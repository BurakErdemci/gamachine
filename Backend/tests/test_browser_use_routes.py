"""Agent-browser backend routes: runtime and settings, halt, one-time grants."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, approval_policy, cards
from agentic.command_gates import GATE_OWNERS
from browser_use import settings
from computer_use import settings as computer_settings
from routes import conversation_routes
from routes.conversation_routes import create_conversation_router

SECRET = "browser-use-secret"
UI = {"X-Gamachine-UI-Secret": SECRET}
TOKEN = "ab" * 32
PARAMS = {"page_url": "https://shop.example/cart", "fingerprint": "button|buy now|",
          "element_name": "Buy now", "value_preview": "",
          "risky": True, "risk_reason": 'click on "Buy now" (page shop.example)'}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda cid: cid in (1, 2))
    rows = {}
    db = MagicMock()
    db.db_path = str(tmp_path / "db.sqlite")
    db.get_setting.side_effect = rows.get
    db.set_setting.side_effect = rows.__setitem__
    db.get_conversation_owner.return_value = 1
    db.get_conversation_parent.return_value = None
    db.get_side_of.return_value = None
    approval_mode.set_ui_secret(SECRET)
    approval_mode.set_mode("auto", source="test")
    settings.set_enabled(db, True)
    cards.reset()
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(db=db, client=client, tmp=tmp_path)
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()
    approval_mode.set_ui_secret("")


def request(env, gate_id="b-first", op="click", conversation_id=1, tool=None, **params):
    return env.client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": tool or f"browser_{op}",
        "conversation_id": conversation_id, "params": {**PARAMS, **params},
    }).json()


def answer(env, gate_id, approved, headers=UI):
    return env.client.post(f"/mcp-approval-respond/{gate_id}", json={"approved": approved}, headers=headers)


def runtime_body(**changes):
    return {"running": True, "port": 4711, "token": TOKEN, **changes}


def test_runtime_roundtrip_and_settings_view(env):
    first = env.client.get("/browser/settings").json()
    assert first == {"enabled": True, "halted": [], "runtime": {"running": False, "port": None, "token": None}}
    assert env.client.post("/browser/runtime", json=runtime_body(), headers=UI).json() == runtime_body()
    assert env.client.get("/browser/settings").json()["runtime"] == runtime_body()
    stopped = env.client.post("/browser/runtime", json={"running": False, "port": 4711, "token": TOKEN}, headers=UI)
    assert stopped.json() == {"running": False, "port": None, "token": None}


@pytest.mark.parametrize("changes", [
    {"running": "yes"}, {"running": 1}, {"port": 0}, {"port": 65536}, {"port": "80"}, {"port": True},
    {"port": None}, {"token": "ab" * 31}, {"token": "AB" * 32}, {"token": "zz" * 32}, {"token": 5},
    {"token": None},
])
def test_runtime_validation_rejects_bad_bodies(env, changes):
    response = env.client.post("/browser/runtime", json=runtime_body(**changes), headers=UI)
    assert response.status_code == 400
    assert env.client.get("/browser/settings").json()["runtime"]["running"] is False


@pytest.mark.parametrize("headers,code", [
    ({}, 403), ({"X-Gamachine-UI-Secret": "wrong"}, 403), ({**UI, "X-UnityAI-Maintenance": "1"}, 403),
])
def test_runtime_and_switch_need_the_ui_secret(env, headers, code):
    assert env.client.post("/browser/runtime", json=runtime_body(), headers=headers).status_code == code
    assert env.client.put("/browser/settings", json={"enabled": False}, headers=headers).status_code == code
    assert env.client.get("/browser/settings").json()["runtime"]["running"] is False
    assert env.client.get("/browser/settings").json()["enabled"] is True


def test_endpoints_need_the_app_token(env, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "token")
    assert env.client.get("/browser/settings").status_code == 401
    assert env.client.post("/browser/runtime", json=runtime_body(), headers=UI).status_code == 401
    assert env.client.put("/browser/settings", json={"enabled": True}, headers=UI).status_code == 401
    assert env.client.get("/browser/settings", headers={"X-Session-Token": "token"}).status_code == 200


@pytest.mark.parametrize("bad", ["true", 1, None])
def test_switch_needs_a_real_boolean(env, bad):
    assert env.client.put("/browser/settings", json={"enabled": bad}, headers=UI).status_code == 400


def test_switch_persists_under_its_own_key(env):
    assert env.client.put("/browser/settings", json={"enabled": False}, headers=UI).json()["enabled"] is False
    assert settings.enabled(env.db) is False
    assert env.client.get("/computer-use/settings").json()["enabled"] is False  # untouched key: never set
    assert env.client.put("/browser/settings", json={"enabled": True}, headers=UI).json()["enabled"] is True


def test_critical_step_opens_an_owned_card_with_the_reason(env):
    assert request(env) == {"status": "ok", "gate_id": "b-first"}
    card = cards.get("b-first")
    assert card.open and card.tool == "browser_click" and card.risk == PARAMS["risk_reason"]
    pending = env.client.get("/mcp-pending").json()["pending"]["b-first"]
    assert pending["conversation_id"] == 1
    assert env.client.get("/mcp-approval-result/b-first").json()["status"] == "pending"
    journal = (env.tmp / "browser_actions.jsonl").read_text().splitlines()
    assert len(journal) == 1 and '"decision": "card"' in journal[0]


def test_invalid_requests_are_denied_without_a_card(env):
    for kwargs in ({"op": ""}, {"op": "navigate"}, {"page_url": 5}, {"fingerprint": None}):
        response = request(env, **kwargs)
        assert response["approved"] is False and response["status"] == "resolved"
    assert env.client.get("/mcp-pending").json()["pending"] == {}


def test_switch_off_denies_and_turning_it_off_rejects_open_cards(env):
    assert request(env)["status"] == "ok"
    off = env.client.put("/browser/settings", json={"enabled": False}, headers=UI).json()
    assert off["rejected_browser_use"] == 1
    assert env.client.get("/mcp-approval-result/b-first").json()["approved"] is False
    denied = request(env, "b-second")
    assert denied["approved"] is False and "turned off" in denied["error"]


def test_approval_issues_a_one_time_grant_for_the_identical_retry(env):
    assert request(env)["status"] == "ok"
    assert answer(env, "b-first", True).status_code == 200
    assert env.client.get("/mcp-approval-result/b-first").json()["approved"] is True
    retry = request(env, "b-retry")
    assert retry["status"] == "resolved" and retry["approved"] is True
    # Consumed: the third identical call needs a new card.
    assert request(env, "b-third")["status"] == "ok"
    assert cards.get("b-third").open


@pytest.mark.parametrize("change", [
    {"op": "fill"}, {"page_url": "https://shop.example/other"}, {"fingerprint": "button|buy later|"},
])
def test_grant_is_keyed_on_op_url_and_element(env, change):
    assert request(env)["status"] == "ok"
    answer(env, "b-first", True)
    assert request(env, "b-other", **change)["status"] == "ok"
    # The original key is still granted.
    assert request(env, "b-same")["approved"] is True


def test_grant_belongs_to_one_chat(env):
    assert request(env)["status"] == "ok"
    answer(env, "b-first", True)
    other = request(env, "b-chat2", conversation_id=2)
    assert other["status"] == "ok"
    assert request(env, "b-same")["approved"] is True


def test_grant_needs_a_verified_owner(env):
    assert request(env, conversation_id=9)["status"] == "ok"  # no turn in flight: unowned
    answer(env, "b-first", True)
    assert request(env, "b-retry", conversation_id=9)["status"] == "ok"


def test_grant_expires_after_ten_minutes(env, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(conversation_routes, "time", lambda: clock[0])
    assert request(env)["status"] == "ok"
    answer(env, "b-first", True)
    clock[0] += conversation_routes.BROWSER_GRANT_TTL - 1
    # Just inside the window: still usable (and consumed).
    assert request(env, "b-in")["approved"] is True
    assert request(env, "b-fresh")["status"] == "ok"
    answer(env, "b-fresh", True)
    clock[0] += conversation_routes.BROWSER_GRANT_TTL + 1
    expired = request(env, "b-late")
    assert expired["status"] == "ok" and cards.get("b-late").open


def test_rejection_issues_no_grant(env):
    assert request(env)["status"] == "ok"
    answer(env, "b-first", False)
    assert env.client.get("/mcp-approval-result/b-first").json()["approved"] is False
    assert request(env, "b-after")["status"] == "ok"
    assert cards.get("b-after").open


def test_rejecting_a_later_card_for_the_same_step_leaves_no_grant(env):
    assert request(env)["status"] == "ok"
    answer(env, "b-first", True)
    assert request(env, "b-second")["approved"] is True  # grant consumed
    assert request(env, "b-third")["status"] == "ok"
    answer(env, "b-third", False)
    assert request(env, "b-fourth")["status"] == "ok"
    assert cards.get("b-fourth").open


def test_approving_needs_the_ui_secret(env):
    assert request(env)["status"] == "ok"
    assert answer(env, "b-first", True, headers={}).status_code == 403
    assert cards.get("b-first").open
    assert request(env, "b-retry")["gate_id"] == "b-first"


def test_a_retry_while_the_card_is_open_does_not_open_a_second_card(env):
    assert request(env)["status"] == "ok"
    again = request(env, "b-dup")
    assert again == {"status": "ok", "gate_id": "b-first"}
    assert [c["card_id"] for c in cards.list_pending()] == ["b-first"]


def test_switching_approval_mode_never_approves_a_browser_card(env):
    assert request(env)["status"] == "ok"
    for mode in ("balanced", "auto", "step"):
        env.client.post("/approval-mode", json={"mode": mode}, headers=UI)
        assert cards.get("b-first").open
    assert env.client.get("/mcp-approval-result/b-first").json()["status"] == "pending"


def test_halt_denies_browser_calls_and_rejects_only_that_chats_cards(env):
    assert request(env, "b-one", conversation_id=1)["status"] == "ok"
    assert request(env, "b-two", conversation_id=2)["status"] == "ok"
    assert env.client.post("/computer-use/halt/1", headers=UI).status_code == 200
    result = env.client.get("/mcp-approval-result/b-one").json()
    assert result["approved"] is False and "stopped the browser" in result["error"]
    assert cards.get("b-one").decision == "computer_halt"
    assert cards.get("b-two").open
    denied = request(env, "b-three", conversation_id=1)
    assert denied["approved"] is False and "stopped the browser" in denied["error"]
    assert 1 in env.client.get("/browser/settings").json()["halted"]


def test_halt_leaves_other_chats_grants_alone(env):
    assert request(env, "b-one", conversation_id=1)["status"] == "ok"
    answer(env, "b-one", True)
    assert request(env, "b-two", conversation_id=2)["status"] == "ok"
    answer(env, "b-two", True)
    env.client.post("/computer-use/halt/1", headers=UI)
    assert request(env, "b-two-retry", conversation_id=2)["approved"] is True


def test_turning_the_switch_off_and_on_clears_grants(env):
    assert request(env)["status"] == "ok"
    answer(env, "b-first", True)
    env.client.put("/browser/settings", json={"enabled": False}, headers=UI)
    env.client.put("/browser/settings", json={"enabled": True}, headers=UI)
    assert request(env, "b-after")["status"] == "ok"


def test_computer_cards_are_untouched_by_browser_halt_and_switch(env):
    computer_settings.set_enabled(env.db, True)
    computer = env.client.post("/mcp-approval-request", json={
        "gate_id": "cu-one", "tool": "computer_click", "conversation_id": 2,
        "params": {"pid": 1, "bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
                   "risky": True, "risk_reason": "risky", "summary": "x", "foreground": False}}).json()
    assert computer["status"] == "ok"
    env.client.put("/browser/settings", json={"enabled": False}, headers=UI)
    assert cards.get("cu-one").open
