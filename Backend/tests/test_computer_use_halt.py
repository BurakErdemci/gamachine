"""Per-turn computer-use Stop: the route, the deny it causes, and its end."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, approval_policy, cards
from agentic.command_gates import GATE_OWNERS
from computer_use import gate, settings
from routes.conversation_routes import create_conversation_router

SECRET = "computer-use-halt-secret"
UI = {"X-Gamachine-UI-Secret": SECRET}
PARAMS = {"pid": 4242, "bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
          "risky": False, "risk_reason": "", "summary": "Click OK", "foreground": False}


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
        yield SimpleNamespace(db=db, client=client)
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()
    approval_mode.set_ui_secret("")


def request(env, gate_id="cu-first", tool="computer_click", conversation_id=1, **params):
    return env.client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": tool, "conversation_id": conversation_id,
        "params": {**PARAMS, **params},
    }).json()


def halt(env, conversation_id=1, headers=UI):
    return env.client.post(f"/computer-use/halt/{conversation_id}", headers=headers)


def halted(env):
    return env.client.get("/computer-use/settings").json()["halted"]


def test_halt_contract_and_settings_field(env):
    assert halted(env) == []
    response = halt(env, 2)
    assert response.status_code == 200
    assert response.json() == {"halted": True, "conversation_id": 2}
    assert halt(env, 1).status_code == 200
    assert halted(env) == [1, 2]
    settings_now = env.client.get("/computer-use/settings").json()
    assert settings_now["enabled"] is True and settings_now["active"] is True


@pytest.mark.parametrize("headers,code", [
    ({}, 403), ({"X-Gamachine-UI-Secret": "wrong"}, 403),
    ({**UI, "X-UnityAI-Maintenance": "1"}, 403),
])
def test_halt_requires_ui_secret_and_refuses_maintenance(env, headers, code):
    assert halt(env, 1, headers).status_code == code
    assert halted(env) == []


def test_halt_requires_app_token(env, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "token")
    assert halt(env, 1).status_code == 401
    assert halt(env, 1, {**UI, "X-Session-Token": "wrong"}).status_code == 401
    assert halt(env, 1, {**UI, "X-Session-Token": "token"}).status_code == 200


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_halt_rejects_non_positive_ids(env, bad):
    assert halt(env, bad).status_code == 400
    assert halted(env) == []


def test_halt_rejects_non_integer_ids(env):
    assert halt(env, "abc").status_code == 422
    assert halted(env) == []


@pytest.mark.parametrize("mode", ["auto", "balanced", "step"])
@pytest.mark.parametrize("tool,extra", [
    ("computer_click", {}), ("computer_list_apps", {"bundle_id": None, "pid": None}),
    ("computer_clipboard_write", {}), ("computer_list_windows", {}),
])
def test_halted_conversation_is_denied_every_computer_tool(env, mode, tool, extra):
    approval_mode.set_mode(mode, source="test")
    settings.add_grant(env.db, PARAMS["bundle_id"], "TextEdit")
    halt(env, 1)
    response = request(env, tool=tool, **extra)
    assert response["approved"] is False
    assert response["error"] == gate.HALTED_REASON
    assert env.client.get("/mcp-pending").json()["pending"] == {}
    # Another conversation keeps computer use.
    assert request(env, "other", tool=tool, conversation_id=2, **extra)["approved"] is True


def test_unverified_claim_of_a_halted_conversation_is_denied(env):
    halt(env, 7)
    response = request(env, conversation_id=7)
    assert response["approved"] is False and response["error"] == gate.HALTED_REASON


def test_halt_rejects_only_that_conversations_pending_cards(env):
    assert request(env, "cu-one", conversation_id=1)["status"] == "ok"
    assert request(env, "cu-two", conversation_id=2)["status"] == "ok"
    halt(env, 1)
    result = env.client.get("/mcp-approval-result/cu-one").json()
    assert result["approved"] is False and result["error"] == gate.HALTED_REASON
    assert cards.get("cu-one").decision == "computer_halt"
    pending = env.client.get("/mcp-pending").json()["pending"]
    assert "cu-one" not in pending and "cu-two" in pending
    assert env.client.get("/mcp-approval-result/cu-two").json()["status"] == "pending"
    assert settings.grants(env.db) == []


def test_halt_rejects_an_unverified_card_that_claimed_the_conversation(env):
    # Conversation 7 has no turn in flight, so the card is unowned but names chat 7.
    assert request(env, "cu-claimed", conversation_id=7)["status"] == "ok"
    assert request(env, "cu-other", conversation_id=2)["status"] == "ok"
    assert request(env, "cu-unclaimed", conversation_id=8)["status"] == "ok"
    halt(env, 7)
    result = env.client.get("/mcp-approval-result/cu-claimed").json()
    assert result["approved"] is False and result["error"] == gate.HALTED_REASON
    pending = env.client.get("/mcp-pending").json()["pending"]
    assert "cu-claimed" not in pending
    assert "cu-other" in pending and "cu-unclaimed" in pending


def test_halt_leaves_the_setting_on(env):
    halt(env, 1)
    assert settings.enabled(env.db)
    assert env.client.get("/computer-use/settings").json()["enabled"] is True


def _send(env, path, conversation_id=1):
    # add_message raising stops the turn right after the halt was cleared,
    # so no provider runs; the route then answers 500.
    env.db.add_message.side_effect = RuntimeError("stop here")
    with patch("routes.conversation_routes._check_chat_rate_limit"):
        return env.client.post(path, json={"conversation_id": conversation_id, "message": "hi",
                                           "user_id": 1}, headers={"X-Session-Token": ""})


@pytest.mark.parametrize("path", ["/chat-stream"])
def test_next_user_message_lifts_the_halt(env, path):
    halt(env, 1)
    halt(env, 2)
    assert request(env)["error"] == gate.HALTED_REASON
    _send(env, path, 1)
    assert env.db.add_message.called
    assert halted(env) == [2]
    assert request(env, "after")["status"] == "ok"
    assert request(env, "still", conversation_id=2)["error"] == gate.HALTED_REASON


def test_halt_survives_without_a_new_message(env):
    halt(env, 1)
    env.client.post("/chat-stop/1")
    assert halted(env) == [1]
    assert request(env)["approved"] is False


def _batch(env, conversation_id=None, check=False):
    body = {"params": {"bundle_id": "com.apple.TextEdit"}}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    if check:
        body["check"] = True
    return env.client.post("/computer-use/batch-step", json=body).json()["stop"]


def test_check_only_batch_step_records_no_activity(env):
    with patch("routes.conversation_routes.computer_activity.record") as record:
        assert _batch(env, 1, check=True) is None
        record.assert_not_called()
        assert _batch(env, 1) is None
        assert record.call_count == 1
    halt(env, 1)
    assert _batch(env, 1, check=True) == "halted"


def test_halt_only_sticks_to_an_ordinary_chat(env):
    from routes import conversation_routes
    env.db.get_conversation_owner.side_effect = lambda cid: 1 if cid in (1, 5) else None
    env.db.get_conversation_parent.side_effect = lambda cid: 1 if cid == 5 else None
    env.db.get_side_of.side_effect = lambda cid: 1 if cid == 5 else None
    for conversation_id in (5, 99999):
        assert halt(env, conversation_id).json() == {"halted": True, "conversation_id": conversation_id}
    assert halted(env) == []
    assert _batch(env) is None
    assert halt(env, 1).status_code == 200
    assert halted(env) == [1]
    assert _batch(env) == "halted"
