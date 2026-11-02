"""The per-feature 'approval cards off' switches: they remove cards only, every deny stays a deny."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, approval_policy, cards
from agentic.command_gates import GATE_OWNERS
from browser_use import gateway, settings as browser_settings
from computer_use import settings as computer_settings
from routes.conversation_routes import create_conversation_router

SECRET = "cards-off-secret"
UI = {"X-Gamachine-UI-Secret": SECRET}
TOKEN = "ab" * 32
BUY = {"role": "button", "name": "Buy now", "tag": "button"}
PARAMS = {"pid": 4242, "bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
          "risky": False, "risk_reason": "", "summary": "Click OK", "foreground": False}


class Host:
    async def __call__(self, runtime, op, conversation_id, args):
        if op == "describe":
            return {"ok": True, "url": "https://shop.example/cart", "title": "t", "element": BUY}
        return {"ok": True, "url": "https://shop.example/cart", "title": "t"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda cid: cid in (1, 2))
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 0.05)
    monkeypatch.setattr(gateway, "post_cmd", Host())
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
    browser_settings.set_enabled(db, True)
    computer_settings.set_enabled(db, True)
    cards.reset()
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/browser/runtime", json={"running": True, "port": 4711, "token": TOKEN},
                           headers=UI).status_code == 200
        yield SimpleNamespace(db=db, rows=rows, client=client)
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()
    approval_mode.set_ui_secret("")


def browser_click(env, conversation_id=1):
    return env.client.post("/browser/command", json={
        "tool": "browser_click", "args": {"snapshot_id": "s1", "ref": "9"},
        "conversation_id": conversation_id}).json()


def computer(env, gate_id, conversation_id=1, **params):
    return env.client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": "computer_click", "conversation_id": conversation_id,
        "params": {**PARAMS, **params}}).json()


def set_browser_cards(env, off, headers=UI):
    return env.client.put("/browser/settings/cards", json={"cards_off": off}, headers=headers)


def set_computer_cards(env, off, headers=UI):
    return env.client.post("/computer-use/settings/cards", json={"cards_off": off}, headers=headers)


def test_default_is_off_in_both_gets(env):
    assert env.client.get("/browser/settings").json()["cards_off"] is False
    assert env.client.get("/computer-use/settings").json()["cards_off"] is False


@pytest.mark.parametrize("path,method", [("/browser/settings/cards", "put"), ("/computer-use/settings/cards", "post")])
def test_switch_routes_refuse_without_secret_bad_bool_and_maintenance(env, path, method):
    send = getattr(env.client, method)
    assert send(path, json={"cards_off": True}).status_code == 403
    assert send(path, json={"cards_off": True}, headers={"X-Gamachine-UI-Secret": "wrong"}).status_code == 403
    assert send(path, json={"cards_off": True}, headers={**UI, "X-UnityAI-Maintenance": "1"}).status_code == 403
    for bad in ("true", 1, None, [True]):
        assert send(path, json={"cards_off": bad}, headers=UI).status_code == 400
    assert send(path, json={}, headers=UI).status_code == 400
    assert env.client.get("/browser/settings").json()["cards_off"] is False
    assert env.client.get("/computer-use/settings").json()["cards_off"] is False


def test_switches_persist_under_their_own_keys(env):
    assert set_browser_cards(env, True).json()["cards_off"] is True
    assert browser_settings.cards_off(env.db) is True
    assert computer_settings.cards_off(env.db) is False
    assert env.rows[browser_settings.CARDS_OFF_KEY] == "1"
    assert set_computer_cards(env, True).json()["cards_off"] is True
    assert env.rows[computer_settings.CARDS_OFF_KEY] == "1"
    assert set_browser_cards(env, False).json()["cards_off"] is False
    assert computer_settings.cards_off(env.db) is True
    assert env.client.get("/browser/settings").json()["enabled"] is True


def test_browser_critical_step_runs_without_a_card_and_cards_come_back(env):
    assert browser_click(env)["needs_approval"] is True
    assert len(cards.list_pending()) == 1
    cards.reset()
    set_browser_cards(env, True)
    reply = browser_click(env)
    assert reply.get("needs_approval") is not True and reply.get("ok") is True
    assert cards.list_pending() == []
    set_browser_cards(env, False)
    assert browser_click(env)["needs_approval"] is True


def test_computer_cards_off_skips_first_access_and_risky_cards_without_a_grant(env):
    set_computer_cards(env, True)
    first = computer(env, "cu-first")
    assert first["status"] == "resolved" and first["approved"] is True
    risky = computer(env, "cu-risky", risky=True, risk_reason="Delete files")
    assert risky["status"] == "resolved" and risky["approved"] is True
    assert cards.list_pending() == []
    assert computer_settings.grants(env.db) == []
    set_computer_cards(env, False)
    assert computer(env, "cu-back")["status"] == "ok"
    assert len(cards.list_pending()) == 1


def test_hard_denied_app_still_denied_with_cards_off(env):
    set_computer_cards(env, True)
    reply = computer(env, "cu-term", bundle_id="com.apple.Terminal")
    assert reply["approved"] is False and reply["status"] == "resolved"
    assert cards.list_pending() == []


def test_disabled_features_still_deny_with_cards_off(env):
    set_computer_cards(env, True)
    set_browser_cards(env, True)
    computer_settings.set_enabled(env.db, False)
    browser_settings.set_enabled(env.db, False)
    assert computer(env, "cu-off")["approved"] is False
    reply = browser_click(env)
    assert reply.get("ok") is False and "turned off" in reply["error"]


def test_halted_chat_still_denies_with_cards_off(env):
    set_computer_cards(env, True)
    set_browser_cards(env, True)
    assert env.client.post("/computer-use/halt/1", headers=UI).status_code == 200
    assert computer(env, "cu-halt")["approved"] is False
    reply = browser_click(env)
    assert reply.get("ok") is False and "stopped the browser" in reply["error"]
    assert cards.list_pending() == []


@pytest.mark.parametrize("conversation_id", [None, 999999])
def test_unverified_sender_keeps_its_card_with_cards_off(env, conversation_id):
    # A request no chat with a turn in flight can be tied to must not run unasked.
    set_computer_cards(env, True)
    result = computer(env, f"cu-unowned-{conversation_id}", conversation_id=conversation_id)
    assert result.get("approved") is not True
    assert result["status"] == "ok" and len(cards.list_pending()) == 1
