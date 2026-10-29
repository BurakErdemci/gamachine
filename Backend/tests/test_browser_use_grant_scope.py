"""Agent-browser grant scope: one approval covers one element, one page visit and one moment."""
import asyncio
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
    router = create_conversation_router(db, {})
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(db=db, client=client, tmp=tmp_path, host=host, router=router)
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()
    approval_mode.set_ui_secret("")


def runtime_body():
    return {"running": True, "port": 4711, "token": TOKEN}


@pytest.fixture
def live(env):
    assert env.client.post("/browser/runtime", json=runtime_body(), headers=UI).status_code == 200
    return env


def command(env, op="click", ref="9", **args):
    if op in {"click", "fill", "select"}:
        args = {"snapshot_id": "s1", "ref": ref, **args}
    return env.client.post("/browser/command", json={
        "tool": f"browser_{op}", "args": args, "conversation_id": 1}).json()


def open_card():
    return cards.list_pending()[-1]["card_id"]


def approve(env, **kwargs):
    assert command(env, **kwargs)["needs_approval"] is True
    assert env.client.post(f"/mcp-approval-respond/{open_card()}", json={"approved": True},
                           headers=UI).status_code == 200


def request_gate(env, gate_id):
    return env.client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": "manage_gameobject", "conversation_id": 1,
        "params": {"action": "create"}, "workspace_path": ""})


def closure_var(fn, name):
    return fn.__closure__[fn.__code__.co_freevars.index(name)].cell_contents


def test_request_route_cannot_replace_an_open_browser_gate(live):
    assert command(live)["needs_approval"] is True
    gate_id = open_card()
    approval_mode.set_mode("step", source="test")
    assert request_gate(live, gate_id).status_code == 409
    assert cards.is_open(gate_id)
    assert live.client.post(f"/mcp-approval-respond/{gate_id}", json={"approved": True}).status_code == 403


def test_request_route_never_makes_browser_gates_or_reuses_ids(live):
    approval_mode.set_mode("step", source="test")
    assert request_gate(live, "browser-abc").status_code == 409
    assert request_gate(live, "plain-1").status_code == 200
    assert request_gate(live, "plain-1").status_code == 409
    cards.answer_card("plain-1", "reject", device="desktop")
    assert request_gate(live, "plain-1").status_code == 409


def test_halt_after_the_approval_still_stops_the_step():
    state = {"enabled": True, "halted": False,
             "runtime": {"running": True, "port": 4711, "token": TOKEN}}
    cmds = []

    async def host(runtime, op, conversation_id, args):
        cmds.append(op)
        return {"ok": True, "url": PAGE, "element": BUY}

    def wait_card(gate_id):
        state["halted"] = True  # Stop lands right after the user's approval
        return {"status": "resolved", "approved": True}

    handler = gateway.BrowserGateway(host, lambda cid, tool, params: {"status": "ok", "gate_id": "g"}, wait_card)
    reply = asyncio.run(handler.call(1, "click", {"snapshot_id": "s1", "ref": "9"}, lambda: state))
    assert reply["ok"] is False and reply["error"] == gateway.HALTED
    assert "click" not in cmds


def test_grant_covers_only_the_approved_ref(live):
    approve(live, ref="9")
    assert command(live, ref="8")["needs_approval"] is True
    assert command(live, ref="9")["ok"] is True
    assert live.host.ops().count("click") == 1


def test_grant_covers_only_the_approved_snapshot(live):
    approve(live)
    other = live.client.post("/browser/command", json={
        "tool": "browser_click", "args": {"snapshot_id": "s2", "ref": "9"}, "conversation_id": 1}).json()
    assert other["needs_approval"] is True


@pytest.mark.parametrize("nav", ["navigate", "back", "forward", "reload"])
def test_navigation_drops_the_grant(live, nav):
    approve(live)
    args = {"url": "https://shop.example/other"} if nav == "navigate" else {}
    assert command(live, nav, **args)["ok"] is True
    live.host.url = PAGE
    assert command(live)["needs_approval"] is True
    assert live.host.ops().count("click") == 0


def test_navigation_leaves_open_cards_answerable(live):
    assert command(live)["needs_approval"] is True
    gate_keys = closure_var(live.router.halt_computer, "_browser_gate_keys")
    assert command(live, "reload")["ok"] is True
    assert len(gate_keys) == 1 and cards.is_open(open_card())


def test_retry_does_not_attach_to_a_closed_card(live):
    assert command(live)["needs_approval"] is True
    gate_id = open_card()
    # Answered elsewhere (another thread) and not yet delivered: its pending entry is still there.
    pending = closure_var(live.router.halt_computer, "_browser_gate_keys")
    assert gate_id in pending
    cards.close_card(gate_id, "timed_out")
    assert command(live)["needs_approval"] is True
    assert open_card() != gate_id
