"""Route-level computer-use decisions, grants, and card lifecycle."""
import asyncio
import json
from types import SimpleNamespace
from inspect import getclosurevars
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, cards, approval_policy
from agentic.command_gates import GATE_OWNERS
from computer_use import gate, policy, settings
from routes.conversation_routes import create_conversation_router

SECRET = "computer-use-test-secret"
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
    router = create_conversation_router(db, {})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield SimpleNamespace(db=db, rows=rows, client=client, router=router,
                              log=tmp_path / "computer_use_actions.jsonl")
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()


def request(env, gate_id="cu-first", tool="computer_click", conversation_id=1, **params):
    return env.client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": tool, "conversation_id": conversation_id,
        "params": {**PARAMS, **params},
    }).json()


def answer(env, approved, gate_id="cu-first", **extra):
    return env.client.post("/mcp-approval-respond/" + gate_id,
                           headers={"X-Gamachine-UI-Secret": SECRET},
                           json={"approved": approved, **extra}).json()


def pending(env):
    return env.client.get("/mcp-pending").json()["pending"]


def result(env, gate_id="cu-first"):
    return env.client.get("/mcp-approval-result/" + gate_id).json()


@pytest.mark.parametrize("mode", ["auto", "balanced", "step"])
def test_disabled_refuses_before_opening_cards_in_every_mode(env, mode):
    settings.set_enabled(env.db, False)
    approval_mode.set_mode(mode, source="test")
    response = request(env)
    assert response["approved"] is False
    assert response["error"] == gate.INACTIVE_REASON
    assert pending(env) == {}


def test_inactive_reason_names_only_the_switch():
    assert "otomatik" not in gate.INACTIVE_REASON.casefold()
    assert "mod" not in gate.INACTIVE_REASON.casefold()


@pytest.mark.parametrize("mode", ["auto", "balanced", "step"])
def test_enabled_behaves_the_same_in_every_mode(env, mode):
    approval_mode.set_mode(mode, source="test")
    assert request(env)["status"] == "ok"
    assert pending(env)["cu-first"]["computer_use"] is True
    assert answer(env, True)["status"] == "ok"
    assert request(env, "cu-next")["approved"] is True
    assert request(env, "cu-risky", risky=True, risk_reason="Delete files")["status"] == "ok"
    assert "Delete files" in pending(env)["cu-risky"]["risk_reason"]
    assert request(env, "cu-list", tool="computer_list_apps", bundle_id=None, pid=None)["approved"] is True


@pytest.mark.parametrize("mode", ["auto", "balanced", "step"])
@pytest.mark.parametrize("granted", [False, True])
def test_gate_decision_is_mode_free(granted, mode):
    approval_mode.set_mode(mode, source="test")
    decision = gate.decide("computer_click", PARAMS, enabled=True, granted=granted)
    assert decision.kind == ("allow" if granted else "card")
    assert gate.decide("computer_click", PARAMS, enabled=False, granted=granted).kind == "deny"


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
    assert answer(env, True, remember=True)["status"] == "ok"
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


def test_phone_answer_wins_once_and_grants_only_the_session(env):
    request(env)
    cards.answer_card("cu-first", "approve", device="phone")
    answer(env, True, remember=True)
    assert result(env)["approved"] is True
    assert not settings.is_granted(env.db, PARAMS["bundle_id"])
    assert len(env.log.read_text().splitlines()) == 2
    assert request(env, "cu-next")["approved"] is True
    assert request(env, "cu-other", conversation_id=2)["status"] == "ok"


@pytest.mark.parametrize("granted", [False, True])
def test_risky_card_in_auto_carries_reason(env, granted):
    if granted:
        settings.add_grant(env.db, PARAMS["bundle_id"], "TextEdit")
    assert request(env, risky=True, risk_reason="Delete files")["status"] == "ok"
    assert "Delete files" in pending(env)["cu-first"]["risk_reason"]
    answer(env, True, remember=True)
    assert len(settings.grants(env.db)) == int(granted)
    if not granted:
        assert request(env, "cu-next")["status"] == "ok"


@pytest.mark.parametrize("mode", ["step", "balanced"])
def test_mode_switch_leaves_computer_cards_pending(env, mode):
    request(env)
    changed = env.client.post("/approval-mode", json={"mode": mode},
                              headers={"X-Gamachine-UI-Secret": SECRET}).json()
    assert "rejected_computer_use" not in changed
    assert changed["approved_pending"] == 0
    assert "cu-first" in pending(env)
    assert result(env)["status"] == "pending"
    assert cards.get("cu-first").open
    assert settings.grants(env.db) == []


def test_auto_drain_leaves_computer_card_pending(env):
    request(env)
    changed = env.client.post("/approval-mode", json={"mode": "auto"},
                              headers={"X-Gamachine-UI-Secret": SECRET}).json()
    assert changed["approved_pending"] == 0
    assert "cu-first" in pending(env)
    assert result(env)["status"] == "pending"


def test_settings_writes_require_secret_and_enabling_works_in_every_mode(env):
    client = env.client
    assert client.get("/computer-use/settings").json() == {
        "enabled": True, "mode": "auto", "active": True, "grants": [],
        "runtime": {"running": False, "driver_path": None, "socket_path": None}, "halted": []}
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
    for mode in ("balanced", "step", "auto"):
        approval_mode.set_mode(mode, source="test")
        assert client.post("/computer-use/settings", json={"enabled": False}).status_code == 200
        assert client.get("/computer-use/settings").json()["active"] is False
        response = client.post("/computer-use/settings", json={"enabled": True}, headers=headers)
        assert response.status_code == 200
        assert response.json()["active"] is True and response.json()["mode"] == mode


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


def test_chat_stop_rejects_pending_without_grant(env):
    request(env)
    response = env.client.post("/chat-stop/1")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert pending(env) == {}
    assert result(env)["approved"] is False
    assert settings.grants(env.db) == []


@pytest.mark.parametrize("remember", [False, None, "true", 1])
def test_desktop_once_grants_only_verified_conversation(env, remember):
    request(env)
    assert pending(env)["cu-first"]["conversation_id"] == 1
    answer(env, True, remember=remember)
    assert settings.grants(env.db) == []
    assert request(env, "same", bundle_id=PARAMS["bundle_id"].upper())["approved"] is True
    assert request(env, "other", conversation_id=2)["status"] == "ok"
    assert request(env, "different-app", bundle_id="com.apple.Notes")["status"] == "ok"


@pytest.mark.parametrize("conversation_id", [None, 3, "1", True])
@pytest.mark.parametrize("remember", [False, True])
def test_unowned_once_approval_grants_only_that_call(env, conversation_id, remember):
    request(env, conversation_id=conversation_id)
    assert pending(env)["cu-first"]["conversation_id"] is None
    answer(env, True, remember=remember)
    assert result(env)["approved"] is True
    assert request(env, "again", conversation_id=conversation_id)["status"] == "ok"
    assert settings.grants(env.db) == []


def test_other_desktop_answer_path_defaults_to_once(env):
    request(env)
    endpoint = next(route.endpoint for route in env.router.routes
                    if route.path == "/mcp-approval-respond/{gate_id}")
    desktop_answer = getclosurevars(endpoint).nonlocals["_desktop_answer"]
    desktop_answer("cu-first", "approve")
    assert settings.grants(env.db) == []
    assert request(env, "again")["approved"] is True


def test_session_grants_are_cleared_when_disabled(env):
    request(env)
    answer(env, True)
    assert request(env, "before")["approved"] is True
    headers = {"X-Gamachine-UI-Secret": SECRET}
    assert env.client.post("/computer-use/settings", json={"enabled": False}).status_code == 200
    assert env.client.post("/computer-use/settings", json={"enabled": True}, headers=headers).status_code == 200
    assert request(env, "after")["status"] == "ok"
    assert settings.grants(env.db) == []


@pytest.mark.parametrize("transition", ["step", "balanced"])
def test_session_grants_survive_a_mode_switch(env, transition):
    request(env)
    answer(env, True)
    env.router.apply_approval_mode(transition, source="test")
    assert request(env, "during")["approved"] is True
    env.router.apply_approval_mode("auto", source="test")
    assert request(env, "after")["approved"] is True


@pytest.mark.parametrize("tool", ["computer_list_apps", "computer_clipboard_write"])
def test_pidless_tools_are_active_only_and_logged(env, tool):
    assert request(env, tool=tool, bundle_id=None, pid=None)["approved"] is True
    assert pending(env) == {}
    assert json.loads(env.log.read_text())["decision"] == "allow"
    settings.set_enabled(env.db, False)
    assert request(env, "off", tool=tool)["approved"] is False


@pytest.mark.parametrize("bundle", [" com.apple.Terminal", "com.apple.Terminal\n", "com.apple.\u200bTerminal", 7])
def test_malformed_ids_deny_without_cards_or_grants(env, bundle):
    assert request(env, bundle_id=bundle)["error"] == "Hedef uygulama tanınamadı."
    assert pending(env) == {}
    with pytest.raises(ValueError):
        settings.add_grant(env.db, bundle, "App")
    assert not settings.is_granted(env.db, bundle)


@pytest.mark.parametrize("bundle,approved", [("com.apple.TextEdit", True), ("com.apple.Terminal", False),
                                           (None, False), (" com.apple.TextEdit", False)])
def test_window_listing_checks_bundle_but_never_cards(env, bundle, approved):
    assert request(env, tool="computer_list_windows", bundle_id=bundle)["approved"] is approved
    assert pending(env) == {}


@pytest.mark.parametrize("raw", ["broken", "{}", "null"])
def test_newest_corrupt_blob_is_preserved_before_overwrite(env, raw):
    env.rows[settings.GRANTS_KEY] = raw
    settings.add_grant(env.db, "com.apple.TextEdit", "TextEdit")
    assert env.rows[settings.CORRUPT_GRANTS_KEY] == raw
    env.rows[settings.GRANTS_KEY] = "different corruption"
    settings.add_grant(env.db, "com.apple.Notes", "Notes")
    assert env.rows[settings.CORRUPT_GRANTS_KEY] == "different corruption"


def test_one_bad_row_preserves_valid_grants(env, caplog):
    good = {"bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
            "granted_at": "2026-10-15T00:00:00+00:00"}
    bad_rows = [None, 1, {}, {**good, "bundle_id": " bad"}, {**good, "app_name": 7},
                {**good, "app_name": ""}, {**good, "granted_at": "bad"}]
    env.rows[settings.GRANTS_KEY] = json.dumps([good, *bad_rows])
    assert settings.grants(env.db) == [good]
    assert "skipping" in caplog.text
    settings.add_grant(env.db, "com.apple.Notes", 7)
    rows = settings.grants(env.db)
    assert rows[0] == good
    assert rows[1]["app_name"] == "com.apple.Notes"
    assert settings.CORRUPT_GRANTS_KEY not in env.rows


@pytest.mark.parametrize("app_name", [None, 7, "", "   "])
def test_grant_name_falls_back_to_bundle(env, app_name):
    settings.add_grant(env.db, PARAMS["bundle_id"], app_name)
    assert settings.grants(env.db)[0]["app_name"] == PARAMS["bundle_id"]


@pytest.mark.parametrize("secret", [None, "wrong"])
@pytest.mark.parametrize("remember", [False, True])
def test_computer_approval_requires_ui_secret_without_resolving_card(env, secret, remember):
    request(env)
    headers = {} if secret is None else {"X-Gamachine-UI-Secret": secret}
    response = env.client.post("/mcp-approval-respond/cu-first",
                               json={"approved": True, "remember": remember}, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "Bilgisayar kullanımı yalnız uygulama arayüzünden onaylanabilir."
    assert "cu-first" in pending(env)
    assert cards.get("cu-first").open
    assert result(env)["status"] == "pending"
    assert settings.grants(env.db) == []
    assert len(env.log.read_text().splitlines()) == 1
    assert env.client.post("/mcp-approval-respond/cu-first", json={"approved": False}).json() == {"status": "ok"}
    assert result(env)["approved"] is False
    assert not cards.get("cu-first").open


def test_noncomputer_approval_does_not_require_ui_secret(env):
    approval_mode.set_mode("step", source="test")
    assert request(env, tool="read_file")["status"] == "ok"
    response = env.client.post("/mcp-approval-respond/cu-first", json={"approved": True})
    assert response.status_code == 200
    assert result(env)["approved"] is True


@pytest.mark.parametrize("remember", [False, True])
@pytest.mark.parametrize("device", ["desktop", "phone"])
def test_clipboard_read_requires_first_use_card_and_respects_grant_scope(env, remember, device):
    params = {"tool": "computer_clipboard_read", "bundle_id": policy.CLIPBOARD_BUNDLE_ID,
              "app_name": "Clipboard", "pid": None}
    assert request(env, **params)["status"] == "ok"
    assert pending(env)["cu-first"]["summary"] == "Click OK"
    if device == "desktop":
        assert answer(env, True, remember=remember)["status"] == "ok"
    else:
        cards.answer_card("cu-first", "approve", device="authenticated-phone")
    assert result(env)["approved"] is True
    assert settings.is_granted(env.db, policy.CLIPBOARD_BUNDLE_ID) is (remember and device == "desktop")
    assert request(env, "again", **params)["approved"] is True
    other = request(env, "other-chat", conversation_id=2, **params)
    assert (other.get("approved") is True) is (remember and device == "desktop")


def test_clipboard_read_reject_does_not_grant_and_inactive_denies(env):
    params = {"tool": "computer_clipboard_read", "bundle_id": policy.CLIPBOARD_BUNDLE_ID,
              "app_name": "Clipboard", "pid": None}
    assert request(env, **params)["status"] == "ok"
    answer(env, False, remember=True)
    assert not settings.is_granted(env.db, policy.CLIPBOARD_BUNDLE_ID)
    assert request(env, "again", **params)["status"] == "ok"
    settings.set_enabled(env.db, False)
    assert request(env, "inactive", **params)["approved"] is False


@pytest.mark.parametrize("raw", ["broken", "{}", "null"])
def test_corrupt_backup_failure_is_readable_but_never_overwritten(env, caplog, raw):
    env.rows[settings.GRANTS_KEY] = raw

    def write(key, value):
        if key == settings.CORRUPT_GRANTS_KEY:
            raise OSError("backup write failed")
        env.rows[key] = value

    env.db.set_setting.side_effect = write
    assert settings.grants(env.db) == []
    assert not settings.is_granted(env.db, PARAMS["bundle_id"])
    assert env.client.get("/computer-use/settings").json()["grants"] == []
    with pytest.raises(ValueError):
        settings.add_grant(env.db, PARAMS["bundle_id"], "TextEdit")
    assert request(env)["status"] == "ok"
    assert answer(env, True, remember=True)["status"] == "ok"
    assert result(env)["approved"] is True
    assert request(env, "again")["status"] == "ok"
    assert env.rows[settings.GRANTS_KEY] == raw
    assert settings.CORRUPT_GRANTS_KEY not in env.rows
    assert "backup" in caplog.text.lower()


@pytest.mark.parametrize("tool", ["computer_launch_app", "computer_click", "computer_list_windows",
                                  "computer_window_state", "computer_confirm_risky_step"])
@pytest.mark.parametrize("bundle", [policy.CLIPBOARD_BUNDLE_ID, policy.CLIPBOARD_BUNDLE_ID.upper()])
def test_clipboard_id_is_reserved_for_the_clipboard_tool(env, tool, bundle):
    params = {**PARAMS, "bundle_id": bundle, "app_name": "Clipboard"}
    decision = gate.decide(tool, params, enabled=True, granted=True)
    assert decision.kind == "deny"
    response = request(env, tool=tool, bundle_id=bundle, app_name="Clipboard")
    assert response["approved"] is False
    assert pending(env) == {}
    assert not settings.is_granted(env.db, policy.CLIPBOARD_BUNDLE_ID)
    read = gate.decide("computer_clipboard_read", params, enabled=True, granted=False)
    assert read.kind == "card" and read.grant is True


def test_grants_survive_a_raising_get_setting(env, caplog):
    env.db.get_setting.side_effect = OSError("db unavailable")
    assert settings.grants(env.db) == []
    assert not settings.is_granted(env.db, PARAMS["bundle_id"])
    with pytest.raises(ValueError):
        settings.add_grant(env.db, PARAMS["bundle_id"], "TextEdit")
    assert caplog.text
