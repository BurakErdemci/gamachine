"""Live-presence activity: what the desktop polls to mirror the driven window."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, cards, approval_policy
from agentic.command_gates import GATE_OWNERS
from computer_use import activity, settings
from routes.conversation_routes import create_conversation_router

SECRET = "computer-activity-test-secret"
PARAMS = {"pid": 4242, "bundle_id": "com.apple.TextEdit", "app_name": "TextEdit", "window_id": 77,
          "risky": False, "risk_reason": "", "summary": "Click OK", "foreground": False}


@pytest.fixture(autouse=True)
def clean():
    activity.reset()
    yield
    activity.reset()


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
    with TestClient(app) as client:
        yield SimpleNamespace(db=db, rows=rows, client=client)
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()


def request(env, gate_id="cu-first", tool="computer_click", conversation_id=1, **params):
    return env.client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": tool, "conversation_id": conversation_id,
        "params": {**PARAMS, **params},
    }).json()


def grant(env, bundle_id="com.apple.TextEdit"):
    settings.add_grant(env.db, bundle_id, "App")


def test_nothing_recorded_is_inactive():
    assert activity.snapshot() == {"active": False, "conversation_id": None, "started_at": None,
                                   "last": None, "target": None}


def test_allowed_call_is_active_for_fifteen_seconds():
    activity.record(5, "computer_click", PARAMS, "allow", now=100.0)
    snap = activity.snapshot(now=110.0)
    assert snap["active"] is True and snap["conversation_id"] == 5
    assert snap["started_at"] == 100_000
    assert snap["last"] == {"tool": "computer_click", "bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
                            "pid": 4242, "window_id": 77, "at": 100_000, "decision": "allow"}
    assert snap["target"] == {"bundle_id": "com.apple.TextEdit", "app_name": "TextEdit", "pid": 4242, "window_id": 77}
    assert activity.snapshot(now=115.0)["active"] is True
    assert activity.snapshot(now=115.1)["active"] is False


@pytest.mark.parametrize("decision", ["deny", "card", "rejected", "", None])
def test_calls_that_were_not_let_through_are_ignored(decision):
    activity.record(5, "computer_click", PARAMS, decision, now=100.0)
    assert activity.snapshot(now=100.0)["active"] is False


@pytest.mark.parametrize("conversation_id", [None, 0, -1, "5", True, 1.5])
def test_only_positive_int_conversations_are_recorded(conversation_id):
    activity.record(conversation_id, "computer_click", PARAMS, "allow", now=100.0)
    assert activity.snapshot(now=100.0)["active"] is False


def test_malformed_fields_never_leak_through():
    activity.record(5, "computer_click", {"pid": True, "window_id": "9", "bundle_id": 3, "app_name": ""},
                    "approved", now=1.0)
    last = activity.snapshot(now=1.0)["last"]
    assert last["pid"] is None and last["window_id"] is None
    assert last["bundle_id"] is None and last["app_name"] is None
    activity.record(6, None, "not a dict", "allow", now=2.0)
    assert activity.snapshot(now=2.0)["last"]["tool"] is None


def test_pidless_call_keeps_the_window_target():
    activity.record(5, "computer_click", PARAMS, "allow", now=100.0)
    activity.record(5, "computer_clipboard_write", {}, "allow", now=101.0)
    snap = activity.snapshot(now=101.0)
    assert snap["last"]["tool"] == "computer_clipboard_write" and snap["last"]["pid"] is None
    assert snap["target"]["pid"] == 4242 and snap["target"]["window_id"] == 77


def test_streak_start_survives_steady_calls_and_resets_after_a_pause():
    for second in (100.0, 110.0, 120.0):
        activity.record(5, "computer_click", PARAMS, "allow", now=second)
    assert activity.snapshot(now=120.0)["started_at"] == 100_000
    activity.record(5, "computer_click", PARAMS, "allow", now=140.0)
    assert activity.snapshot(now=140.0)["started_at"] == 140_000


def test_most_recent_active_conversation_wins_and_old_ones_are_forgotten():
    activity.record(5, "computer_click", PARAMS, "allow", now=100.0)
    activity.record(6, "computer_click", {**PARAMS, "pid": 1}, "allow", now=105.0)
    assert activity.snapshot(now=106.0)["conversation_id"] == 6
    activity.record(5, "computer_click", PARAMS, "allow", now=107.0)
    assert activity.snapshot(now=108.0)["conversation_id"] == 5
    activity.record(7, "computer_click", PARAMS, "allow", now=107.0 + activity.FORGET_SECONDS + 1)
    assert set(activity._conversations) == {7}


def test_route_requires_app_token(env, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "token")
    assert env.client.get("/computer-use/activity").status_code == 401
    assert env.client.get("/computer-use/activity", headers={"X-Session-Token": "wrong"}).status_code == 401
    response = env.client.get("/computer-use/activity", headers={"X-Session-Token": "token"})
    assert response.status_code == 200 and response.json()["active"] is False


def test_granted_call_through_the_gate_becomes_active(env):
    grant(env)
    assert request(env)["approved"] is True
    snap = env.client.get("/computer-use/activity").json()
    assert snap["active"] is True and snap["conversation_id"] == 1
    assert snap["last"]["decision"] == "allow" and snap["last"]["window_id"] == 77
    assert set(snap) == {"active", "conversation_id", "started_at", "last", "target"}
    assert "secret" not in json.dumps(snap).lower()


def test_card_is_inactive_until_approved_and_rejection_stays_inactive(env):
    assert request(env)["status"] == "ok"
    assert env.client.get("/computer-use/activity").json()["active"] is False
    env.client.post("/mcp-approval-respond/cu-first", headers={"X-Gamachine-UI-Secret": SECRET},
                    json={"approved": True})
    snap = env.client.get("/computer-use/activity").json()
    assert snap["active"] is True and snap["last"]["decision"] == "approved"
    activity.reset()
    assert request(env, "cu-second", bundle_id="com.apple.Notes")["status"] == "ok"
    env.client.post("/mcp-approval-respond/cu-second", headers={"X-Gamachine-UI-Secret": SECRET},
                    json={"approved": False})
    assert env.client.get("/computer-use/activity").json()["active"] is False


def test_denied_and_unowned_calls_never_become_active(env):
    assert request(env, bundle_id="com.apple.Terminal")["approved"] is False
    grant(env)
    assert request(env, "unowned", conversation_id=3)["approved"] is True
    assert env.client.get("/computer-use/activity").json()["active"] is False
