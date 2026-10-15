"""Route-level computer-use decisions, grants, and card lifecycle."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, cards
from agentic.command_gates import GATE_OWNERS
from computer_use import gate, settings
from routes.conversation_routes import create_conversation_router

SECRET = "computer-use-test-secret"
PARAMS = {"pid": 4242, "bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
          "risky": False, "risk_reason": "", "summary": "Click OK", "foreground": False}


@pytest.fixture
def env(tmp_path):
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
    router = create_conversation_router(db, {})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield SimpleNamespace(db=db, rows=rows, client=client, router=router,
                              log=tmp_path / "computer_use_actions.jsonl")
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()


def request(env, gate_id="cu-first", tool="computer_click", **params):
    return env.client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": tool, "params": {**PARAMS, **params},
    }).json()


def answer(env, approved, gate_id="cu-first"):
    return env.client.post("/mcp-approval-respond/" + gate_id, json={"approved": approved}).json()


def pending(env):
    return env.client.get("/mcp-pending").json()["pending"]


def result(env, gate_id="cu-first"):
    return env.client.get("/mcp-approval-result/" + gate_id).json()


@pytest.mark.parametrize("on,mode", [(False, "auto"), (True, "step"), (True, "balanced")])
def test_inactive_refuses_before_opening_cards(env, on, mode):
    settings.set_enabled(env.db, on)
    approval_mode.set_mode(mode, source="test")
    response = request(env)
    assert response["approved"] is False
    assert response["error"] == gate.INACTIVE_REASON
    assert pending(env) == {}


@pytest.mark.parametrize("bundle", ["com.apple.Terminal", "COM.GAMACHINE.APP", "", None])
def test_hard_denied_or_unknown_apps_refused_even_with_grant(env, bundle):
    if bundle:
        env.rows[settings.GRANTS_KEY] = json.dumps([{"bundle_id": bundle, "app_name": "App", "granted_at": "now"}])
    assert request(env, bundle_id=bundle)["approved"] is False
    assert pending(env) == {}


def test_first_use_approval_persists_grant_and_next_call_is_immediate(env):
    assert request(env)["status"] == "ok"
    entry = pending(env)["cu-first"]
    assert entry["computer_use"] is True
    assert entry["summary"] == "Click OK"
    assert not settings.is_granted(env.db, PARAMS["bundle_id"])
    assert answer(env, True)["status"] == "ok"
    assert result(env)["approved"] is True
    assert settings.is_granted(env.db, PARAMS["bundle_id"].upper())
    assert request(env, "cu-next")["approved"] is True
    assert pending(env) == {}
    lines = [json.loads(line) for line in env.log.read_text().splitlines()]
    assert [line["decision"] for line in lines] == ["card", "approved", "allow"]
    assert all(set(line) == {"ts", "conversation_id", "tool", "bundle_id", "pid", "decision", "reason"} for line in lines)
    assert len(settings.grants(env.db)) == 1


@pytest.mark.parametrize("value", [False, "true", 1])
def test_rejection_never_persists_grant(env, value):
    request(env)
    answer(env, value)
    assert result(env)["approved"] is False
    assert settings.grants(env.db) == []


def test_phone_answer_wins_once_and_grants(env):
    request(env)
    cards.answer_card("cu-first", "approve", device="phone")
    answer(env, False)
    assert result(env)["approved"] is True
    assert settings.is_granted(env.db, PARAMS["bundle_id"])
    assert len(env.log.read_text().splitlines()) == 2


@pytest.mark.parametrize("granted", [False, True])
def test_risky_card_in_auto_carries_reason(env, granted):
    if granted:
        settings.add_grant(env.db, PARAMS["bundle_id"], "TextEdit")
    assert request(env, risky=True, risk_reason="Delete files")["status"] == "ok"
    assert "Delete files" in pending(env)["cu-first"]["risk_reason"]
    answer(env, True)
    assert len(settings.grants(env.db)) == 1


@pytest.mark.parametrize("mode", ["step", "balanced"])
def test_mode_switch_rejects_computer_cards_without_grant(env, mode):
    request(env)
    changed = env.client.post("/approval-mode", json={"mode": mode},
                              headers={"X-Gamachine-UI-Secret": SECRET}).json()
    assert changed["rejected_computer_use"] == 1
    assert changed["approved_pending"] == 0
    assert pending(env) == {}
    assert result(env)["approved"] is False
    assert cards.get("cu-first").decision == "mode_switch"
    assert settings.grants(env.db) == []


def test_auto_drain_leaves_computer_card_pending(env):
    request(env)
    changed = env.client.post("/approval-mode", json={"mode": "auto"},
                              headers={"X-Gamachine-UI-Secret": SECRET}).json()
    assert changed["approved_pending"] == 0
    assert "cu-first" in pending(env)
    assert result(env)["status"] == "pending"


def test_settings_writes_require_secret_and_auto_only_for_enabling(env):
    client = env.client
    assert client.get("/computer-use/settings").json() == {
        "enabled": True, "mode": "auto", "active": True, "grants": []}
    assert client.post("/computer-use/settings", json={"enabled": True}).status_code == 403
    headers = {"X-Gamachine-UI-Secret": SECRET}
    assert client.post("/computer-use/settings", json={"enabled": True}, headers=headers).status_code == 200
    assert client.post("/computer-use/settings", json={"enabled": "true"}, headers=headers).status_code == 400
    assert client.post("/computer-use/settings", json={"enabled": False}, headers={
        **headers, "X-UnityAI-Maintenance": "present"}).status_code == 403
    request(env)
    response = client.post("/computer-use/settings", json={"enabled": False})
    assert response.status_code == 200 and response.json()["rejected_computer_use"] == 1
    assert result(env)["approved"] is False
    assert not settings.enabled(env.db)
    approval_mode.set_mode("balanced", source="test")
    assert client.post("/computer-use/settings", json={"enabled": True}, headers=headers).status_code == 409


def test_settings_and_grant_routes_require_app_token(env, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "token")
    for method, path, body in (("get", "/computer-use/settings", None),
                               ("post", "/computer-use/settings", {"enabled": False}),
                               ("delete", "/computer-use/grants/com.apple.TextEdit", None)):
        response = env.client.request(method, path, json=body)
        assert response.status_code == 401
    assert env.client.get("/computer-use/settings", headers={"X-Session-Token": "token"}).status_code == 200


def test_delete_grant_requires_ui_secret(env):
    settings.add_grant(env.db, PARAMS["bundle_id"], "TextEdit")
    path = "/computer-use/grants/" + PARAMS["bundle_id"]
    assert env.client.delete(path).status_code == 403
    headers = {"X-Gamachine-UI-Secret": SECRET}
    assert env.client.delete(path, headers=headers).json() == {"removed": True}
    assert env.client.delete(path, headers=headers).json() == {"removed": False}


@pytest.mark.parametrize("raw", ["broken", "{}", "null", "[1]"])
def test_corrupt_grants_fail_closed_with_warning(env, caplog, raw):
    env.rows[settings.GRANTS_KEY] = raw
    assert settings.grants(env.db) == []
    assert "Invalid computer_use_app_grants" in caplog.text


def test_grants_idempotence_and_hard_deny(env):
    settings.add_grant(env.db, PARAMS["bundle_id"], "TextEdit")
    settings.add_grant(env.db, PARAMS["bundle_id"].upper(), "Different")
    assert len(settings.grants(env.db)) == 1
    with pytest.raises(ValueError):
        settings.add_grant(env.db, "com.apple.Terminal", "Terminal")
    assert settings.remove_grant(env.db, PARAMS["bundle_id"].upper())


def test_journal_failure_is_best_effort(env, caplog):
    gate.actions_log_append(env.log / "missing" / "file", {})
    assert "Could not append" in caplog.text


def test_deleted_and_side_chat_refusals_precede_computer_gate(env):
    env.db.get_conversation_owner.return_value = None
    response = env.client.post("/mcp-approval-request", json={
        "gate_id": "deleted-cu", "tool": "computer_list_apps", "conversation_id": 999, "params": {}}).json()
    assert response["approved"] is False and "silindi" in response["error"]
    env.db.get_conversation_owner.return_value = 1
    env.db.get_side_of.return_value = 1
    response = env.client.post("/mcp-approval-request", json={
        "gate_id": "side-cu", "tool": "computer_list_apps", "conversation_id": 2, "params": {}}).json()
    assert response["approved"] is False


def test_abort_all_rejects_pending_without_grant(env):
    request(env)
    env.client.post("/mcp-abort-all")
    assert result(env)["approved"] is False
    assert settings.grants(env.db) == []
