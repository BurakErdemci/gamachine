"""Batch heartbeats refresh only verified, enabled, non-halted conversations."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, approval_policy, cards
from agentic.command_gates import GATE_OWNERS
from computer_use import activity, gate, settings
from routes.conversation_routes import create_conversation_router

TOKEN = {"X-Session-Token": "batch-token"}
UI = {**TOKEN, "X-Gamachine-UI-Secret": "batch-ui-secret"}
PARAMS = {"bundle_id": "com.apple.TextEdit", "app_name": "TextEdit", "pid": 4242, "window_id": 7}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "batch-token")
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda cid: cid in (1, 2))
    rows = {}
    db = MagicMock()
    db.db_path = str(tmp_path / "db.sqlite")
    db.get_setting.side_effect = rows.get
    db.set_setting.side_effect = rows.__setitem__
    db.get_conversation_owner.return_value = 1
    db.get_conversation_parent.return_value = None
    db.get_side_of.return_value = None
    settings.set_enabled(db, True)
    approval_mode.set_ui_secret("batch-ui-secret")
    approval_mode.set_mode("auto", source="test")
    cards.reset()
    activity.reset()
    journal = MagicMock()
    monkeypatch.setattr(gate, "actions_log_append", journal)
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(db=db, client=client, journal=journal)
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()
    activity.reset()
    approval_mode.set_ui_secret("")


def heartbeat(env, body=None, headers=TOKEN):
    return env.client.post("/computer-use/batch-step", headers=headers,
                           json=body if body is not None else {"conversation_id": 1, "params": PARAMS})


def halt(env, cid):
    assert env.client.post(f"/computer-use/halt/{cid}", headers=UI).status_code == 200


def test_verified_owner_refreshes_activity_without_journal_or_pending_changes(env):
    response = heartbeat(env)
    assert response.status_code == 200 and response.json() == {"stop": None}
    live = activity.snapshot()
    assert live["active"] is True and live["conversation_id"] == 1
    assert live["target"] == PARAMS
    assert live["last"]["tool"] == "computer_run_steps"
    assert live["last"]["decision"] == "allow"
    env.journal.assert_not_called()
    assert env.client.get("/mcp-pending", headers=TOKEN).json()["pending"] == {}


@pytest.mark.parametrize("cid", [1, 7])
def test_halted_owner_or_unverified_claim_cannot_refresh(env, cid):
    halt(env, cid)
    assert heartbeat(env, {"conversation_id": cid, "params": PARAMS}).json() == {"stop": "halted"}
    assert activity.snapshot()["active"] is False
    env.journal.assert_not_called()


def test_unclaimed_batch_stops_when_any_conversation_halted(env):
    halt(env, 2)
    assert heartbeat(env, {"params": PARAMS}).json() == {"stop": "halted"}
    assert activity.snapshot()["active"] is False


def test_another_conversations_halt_allows_verified_owner(env):
    halt(env, 2)
    assert heartbeat(env).json() == {"stop": None}
    assert activity.snapshot()["conversation_id"] == 1


@pytest.mark.parametrize("halted", [False, True])
def test_disabled_and_halt_precedence(env, halted):
    if halted:
        halt(env, 1)
    settings.set_enabled(env.db, False)
    assert heartbeat(env).json() == {"stop": "halted" if halted else "disabled"}
    assert activity.snapshot()["active"] is False


@pytest.mark.parametrize("cid", [None, 7, True, "1"])
def test_unverified_owner_cannot_create_activity(env, cid):
    assert heartbeat(env, {"conversation_id": cid, "params": PARAMS}).json() == {"stop": None}
    assert activity.snapshot()["active"] is False


@pytest.mark.parametrize("params", [None, [], "invalid"])
def test_invalid_params_are_treated_as_empty(env, params):
    assert heartbeat(env, {"conversation_id": 1, "params": params}).json() == {"stop": None}
    live = activity.snapshot()
    assert live["active"] is True and live["target"] is None


@pytest.mark.parametrize("headers", [{}, {"X-Session-Token": "wrong"}])
def test_requires_app_token(env, headers):
    assert heartbeat(env, headers=headers).status_code == 401
    assert activity.snapshot()["active"] is False


def test_heartbeat_does_not_resolve_pending_cards(env):
    response = env.client.post("/mcp-approval-request", headers=TOKEN, json={
        "gate_id": "batch-pending", "tool": "computer_click", "conversation_id": 1,
        "params": {**PARAMS, "summary": "Click OK"},
    })
    assert response.json()["status"] == "ok"
    before = env.client.get("/mcp-pending", headers=TOKEN).json()["pending"]
    assert "batch-pending" in before
    env.journal.reset_mock()
    assert heartbeat(env).json() == {"stop": None}
    assert env.client.get("/mcp-pending", headers=TOKEN).json()["pending"] == before
    env.journal.assert_not_called()
