"""Agent-browser backend routes: runtime and settings, the command gateway, halt, one-time grants."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, approval_policy, cards
from agentic.command_gates import GATE_OWNERS
from browser_use import gateway, settings
from computer_use import settings as computer_settings
from routes import conversation_routes
from routes.conversation_routes import create_conversation_router

SECRET = "browser-use-secret"
UI = {"X-Gamachine-UI-Secret": SECRET}
TOKEN = "ab" * 32
PAGE = "https://shop.example/cart"
BUY = {"role": "button", "name": "Buy now", "tag": "button"}
BUY_LATER = {"role": "button", "name": "Buy later", "tag": "button"}
NOTES = {"role": "textbox", "name": "Order notes", "tag": "input", "form_method": "post"}
MORE = {"role": "button", "name": "Show more", "tag": "button"}


class Host:
    """Electron's host as the gateway sees it."""

    def __init__(self):
        self.url = PAGE
        self.elements = {"9": BUY, "8": BUY_LATER, "4": NOTES, "7": MORE}
        self.cmds = []

    async def __call__(self, runtime, op, conversation_id, args):
        self.cmds.append((op, conversation_id, args, runtime))
        if op == "describe":
            element = self.elements.get(args["ref"])
            if element is None:
                return {"ok": False, "error": "stale_snapshot", "url": self.url}
            return {"ok": True, "url": self.url, "title": "t", "element": element}
        return {"ok": True, "url": self.url, "title": "t"}

    def ops(self):
        return [cmd[0] for cmd in self.cmds]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda cid: cid in (1, 2))
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 0.05)
    host = Host()
    monkeypatch.setattr(gateway, "post_cmd", host)
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
        yield SimpleNamespace(db=db, client=client, tmp=tmp_path, host=host)
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()
    approval_mode.set_ui_secret("")


@pytest.fixture
def live(env):
    assert env.client.post("/browser/runtime", json=runtime_body(), headers=UI).status_code == 200
    return env


def runtime_body(**changes):
    return {"running": True, "port": 4711, "token": TOKEN, **changes}


def command(env, op="click", conversation_id=1, **args):
    if op in {"click", "fill", "select"}:
        args = {"snapshot_id": "s1", "ref": "9", **args}
    return env.client.post("/browser/command", json={
        "tool": f"browser_{op}", "args": args, "conversation_id": conversation_id}).json()


def open_card(env):
    """The newest open card's id."""
    return cards.list_pending()[-1]["card_id"]


def answer(env, gate_id, approved, headers=UI):
    return env.client.post(f"/mcp-approval-respond/{gate_id}", json={"approved": approved}, headers=headers)


def approve_click(env, conversation_id=1, **args):
    """A critical step that opens a card the user approves."""
    reply = command(env, conversation_id=conversation_id, **args)
    assert reply["needs_approval"] is True
    gate_id = open_card(env)
    assert answer(env, gate_id, True).status_code == 200
    return gate_id


def test_settings_never_expose_the_host_port_or_token(env):
    first = env.client.get("/browser/settings")
    assert first.json() == {"enabled": True, "running": False, "halted": []}
    assert env.client.post("/browser/runtime", json=runtime_body(), headers=UI).json() == runtime_body()
    view = env.client.get("/browser/settings")
    assert view.json() == {"enabled": True, "running": True, "halted": []}
    assert TOKEN not in view.text and "4711" not in view.text
    switched = env.client.put("/browser/settings", json={"enabled": True}, headers=UI)
    assert TOKEN not in switched.text and "4711" not in switched.text
    stopped = env.client.post("/browser/runtime", json={"running": False, "port": 4711, "token": TOKEN}, headers=UI)
    assert stopped.json() == {"running": False, "port": None, "token": None}
    assert env.client.get("/browser/settings").json()["running"] is False


@pytest.mark.parametrize("changes", [
    {"running": "yes"}, {"running": 1}, {"port": 0}, {"port": 65536}, {"port": "80"}, {"port": True},
    {"port": None}, {"token": "ab" * 31}, {"token": "AB" * 32}, {"token": "zz" * 32}, {"token": 5},
    {"token": None},
])
def test_runtime_validation_rejects_bad_bodies(env, changes):
    response = env.client.post("/browser/runtime", json=runtime_body(**changes), headers=UI)
    assert response.status_code == 400
    assert env.client.get("/browser/settings").json()["running"] is False


@pytest.mark.parametrize("headers,code", [
    ({}, 403), ({"X-Gamachine-UI-Secret": "wrong"}, 403), ({**UI, "X-UnityAI-Maintenance": "1"}, 403),
])
def test_runtime_and_switch_need_the_ui_secret(env, headers, code):
    assert env.client.post("/browser/runtime", json=runtime_body(), headers=headers).status_code == code
    assert env.client.put("/browser/settings", json={"enabled": False}, headers=headers).status_code == code
    assert env.client.get("/browser/settings").json()["running"] is False
    assert env.client.get("/browser/settings").json()["enabled"] is True


def test_endpoints_need_the_app_token(live, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "token")
    assert live.client.get("/browser/settings").status_code == 401
    assert live.client.post("/browser/runtime", json=runtime_body(), headers=UI).status_code == 401
    assert live.client.put("/browser/settings", json={"enabled": True}, headers=UI).status_code == 401
    body = {"tool": "browser_snapshot", "args": {}, "conversation_id": 1}
    assert live.client.post("/browser/command", json=body).status_code == 401
    assert live.host.cmds == []
    assert live.client.get("/browser/settings", headers={"X-Session-Token": "token"}).status_code == 200
    reply = live.client.post("/browser/command", json=body, headers={"X-Session-Token": "token"}).json()
    assert reply["ok"] is True and live.host.ops() == ["snapshot"]


@pytest.mark.parametrize("bad", ["true", 1, None])
def test_switch_needs_a_real_boolean(env, bad):
    assert env.client.put("/browser/settings", json={"enabled": bad}, headers=UI).status_code == 400


def test_switch_persists_under_its_own_key(env):
    assert env.client.put("/browser/settings", json={"enabled": False}, headers=UI).json()["enabled"] is False
    assert settings.enabled(env.db) is False
    assert env.client.get("/computer-use/settings").json()["enabled"] is False  # untouched key: never set
    assert env.client.put("/browser/settings", json={"enabled": True}, headers=UI).json()["enabled"] is True


def test_command_runs_on_the_reported_host(live):
    reply = command(live, "navigate", url="https://opengameart.org/")
    assert reply["ok"] is True and '"url"' in reply["text"]
    op, conversation_id, args, runtime = live.host.cmds[0]
    assert (op, conversation_id, args) == ("navigate", 1, {"url": "https://opengameart.org/"})
    assert runtime == runtime_body()


@pytest.mark.parametrize("body", [
    {"tool": "navigate", "args": {}, "conversation_id": 1},
    {"tool": 5, "args": {}, "conversation_id": 1},
    {"args": {}, "conversation_id": 1},
])
def test_command_needs_a_browser_tool(live, body):
    assert live.client.post("/browser/command", json=body).status_code == 400
    assert live.host.cmds == []


@pytest.mark.parametrize("conversation_id", [9, None, 0, "1", True])
def test_unknown_owner_is_refused_before_the_host(live, conversation_id):
    for op, args in (("snapshot", {}), ("navigate", {"url": "https://example.com/"})):
        reply = live.client.post("/browser/command", json={
            "tool": f"browser_{op}", "args": args, "conversation_id": conversation_id}).json()
        assert reply["ok"] is False and "turn is running" in reply["error"]
    assert command(live, conversation_id=conversation_id)["ok"] is False
    assert live.host.cmds == [] and cards.list_pending() == []


def test_other_users_chat_is_refused(live):
    live.db.get_conversation_owner.return_value = 2
    assert command(live, "snapshot")["ok"] is False
    assert live.host.cmds == []


def test_disabled_or_not_running_browser_is_refused(env):
    reply = command(env, "snapshot")
    assert reply["ok"] is False and "not running" in reply["error"]
    env.client.post("/browser/runtime", json=runtime_body(), headers=UI)
    env.client.put("/browser/settings", json={"enabled": False}, headers=UI)
    reply = command(env, "snapshot")
    assert reply["ok"] is False and "turned off" in reply["error"]
    assert env.host.cmds == []


def test_refused_url_never_reaches_the_host(live):
    reply = command(live, "navigate", url="http://127.0.0.1:8000/")
    assert reply["ok"] is False and reply["error"].startswith("Refused:")
    assert live.host.cmds == []


def test_critical_step_opens_an_owned_card_with_the_reason(live):
    reply = command(live)
    assert reply["ok"] is False and reply["needs_approval"] is True
    assert reply["error"].startswith("needs_approval: ")
    gate_id = open_card(live)
    card = cards.get(gate_id)
    assert card.open and card.tool == "browser_click" and "Buy now" in card.risk and "shop.example" in card.risk
    pending = live.client.get("/mcp-pending").json()["pending"][gate_id]
    assert pending["conversation_id"] == 1
    assert pending["params"]["element_name"] == "Buy now" and pending["params"]["page_url"] == PAGE
    assert live.host.ops() == ["describe"]
    journal = (live.tmp / "browser_actions.jsonl").read_text().splitlines()
    assert len(journal) == 1 and '"decision": "card"' in journal[0] and '"conversation_id": 1' in journal[0]


def test_free_steps_open_no_card(live):
    assert command(live, ref="7")["ok"] is True
    assert live.host.ops() == ["describe", "click"] and cards.list_pending() == []


def test_approval_issues_a_one_time_grant_for_the_identical_retry(live):
    approve_click(live)
    retry = command(live)
    assert retry["ok"] is True and live.host.ops() == ["describe", "describe", "click"]
    # Consumed: the third identical call needs a new card.
    third = command(live)
    assert third["needs_approval"] is True and cards.get(open_card(live)).open
    assert live.host.ops().count("click") == 1


@pytest.mark.parametrize("change", ["op", "url", "element"])
def test_grant_is_keyed_on_op_url_and_element(live, change):
    approve_click(live)
    if change == "op":
        other = command(live, "fill", value="x")
    elif change == "url":
        live.host.url = "https://shop.example/other"
        other = command(live)
        live.host.url = PAGE
    else:
        other = command(live, ref="8")
    assert other["needs_approval"] is True
    # The original key is still granted.
    assert command(live)["ok"] is True


def test_grant_is_bound_to_the_exact_value(live):
    assert command(live, "fill", ref="4", value="a")["needs_approval"] is True
    answer(live, open_card(live), True)
    other = command(live, "fill", ref="4", value="b")
    assert other["needs_approval"] is True and cards.get(open_card(live)).open
    # The approved value's grant is still there and is consumed by the identical retry.
    assert command(live, "fill", ref="4", value="a")["ok"] is True
    assert command(live, "fill", ref="4", value="a")["needs_approval"] is True
    filled = [cmd[2]["value"] for cmd in live.host.cmds if cmd[0] == "fill"]
    assert filled == ["a"]


def test_grant_belongs_to_one_chat(live):
    approve_click(live)
    assert command(live, conversation_id=2)["needs_approval"] is True
    assert command(live)["ok"] is True


def test_grant_expires_after_ten_minutes(live, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(conversation_routes, "time", lambda: clock[0])
    approve_click(live)
    clock[0] += conversation_routes.BROWSER_GRANT_TTL - 1
    # Just inside the window: still usable (and consumed).
    assert command(live)["ok"] is True
    approve_click(live)
    clock[0] += conversation_routes.BROWSER_GRANT_TTL + 1
    assert command(live)["needs_approval"] is True


def test_rejection_issues_no_grant(live):
    assert command(live)["needs_approval"] is True
    answer(live, open_card(live), False)
    assert command(live)["needs_approval"] is True
    assert "click" not in live.host.ops()


def test_rejecting_a_later_card_for_the_same_step_leaves_no_grant(live):
    approve_click(live)
    assert command(live)["ok"] is True  # grant consumed
    assert command(live)["needs_approval"] is True
    answer(live, open_card(live), False)
    assert command(live)["needs_approval"] is True


def test_approving_needs_the_ui_secret(live):
    assert command(live)["needs_approval"] is True
    gate_id = open_card(live)
    assert answer(live, gate_id, True, headers={}).status_code == 403
    assert cards.get(gate_id).open
    assert command(live)["needs_approval"] is True
    assert [c["card_id"] for c in cards.list_pending()] == [gate_id]


def test_switching_approval_mode_never_approves_a_browser_card(live):
    assert command(live)["needs_approval"] is True
    gate_id = open_card(live)
    for mode in ("balanced", "auto", "step"):
        live.client.post("/approval-mode", json={"mode": mode}, headers=UI)
        assert cards.get(gate_id).open
    assert command(live)["needs_approval"] is True


def test_side_chat_cannot_run_a_critical_step(live):
    live.db.get_side_of.return_value = 5
    reply = command(live)
    assert reply["ok"] is False and "needs_approval" not in reply and "salt okunur" in reply["error"]
    assert cards.list_pending() == [] and "click" not in live.host.ops()


def test_approval_request_route_no_longer_grants_browser_steps(live):
    """The old card path is gone: a browser_* card request there cannot mint a gateway grant."""
    for mode in ("auto", "step"):
        approval_mode.set_mode(mode, source="test")
        live.client.post("/mcp-approval-request", json={
            "gate_id": f"b-old-{mode}", "tool": "browser_click", "conversation_id": 1,
            "params": {"page_url": PAGE, "fingerprint": "button|buy now|", "value_hash": ""}})
        if cards.get(f"b-old-{mode}") is not None:
            answer(live, f"b-old-{mode}", True)
    assert command(live)["needs_approval"] is True
    assert "click" not in live.host.ops()


def test_halt_denies_browser_calls_and_rejects_only_that_chats_cards(live):
    assert command(live, conversation_id=1)["needs_approval"] is True
    one = open_card(live)
    assert command(live, conversation_id=2)["needs_approval"] is True
    two = open_card(live)
    assert live.client.post("/computer-use/halt/1", headers=UI).status_code == 200
    assert cards.get(one).decision == "computer_halt"
    assert cards.get(two).open
    denied = command(live, "snapshot", conversation_id=1)
    assert denied["ok"] is False and "stopped the browser" in denied["error"]
    assert command(live, "snapshot", conversation_id=2)["ok"] is True
    assert 1 in live.client.get("/browser/settings").json()["halted"]


def test_halt_leaves_other_chats_grants_alone(live):
    approve_click(live, conversation_id=1)
    approve_click(live, conversation_id=2)
    live.client.post("/computer-use/halt/1", headers=UI)
    assert command(live, conversation_id=2)["ok"] is True


def test_switch_off_rejects_open_cards_and_turning_it_back_on_clears_grants(live):
    approve_click(live)
    assert command(live, ref="8")["needs_approval"] is True
    off = live.client.put("/browser/settings", json={"enabled": False}, headers=UI).json()
    assert off["rejected_browser_use"] == 1
    denied = command(live)
    assert denied["ok"] is False and "turned off" in denied["error"]
    live.client.put("/browser/settings", json={"enabled": True}, headers=UI)
    assert command(live)["needs_approval"] is True


def test_computer_cards_are_untouched_by_browser_halt_and_switch(live):
    computer_settings.set_enabled(live.db, True)
    computer = live.client.post("/mcp-approval-request", json={
        "gate_id": "cu-one", "tool": "computer_click", "conversation_id": 2,
        "params": {"pid": 1, "bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
                   "risky": True, "risk_reason": "risky", "summary": "x", "foreground": False}}).json()
    assert computer["status"] == "ok"
    live.client.put("/browser/settings", json={"enabled": False}, headers=UI)
    assert cards.get("cu-one").open
