"""Agent-browser one-time grants: one approval runs the step once, whatever device or moment answers it."""
import threading
import time
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


def click(env, conversation_id=1):
    return env.client.post("/browser/command", json={
        "tool": "browser_click", "args": {"snapshot_id": "s1", "ref": "9"},
        "conversation_id": conversation_id}).json()


def open_card():
    return cards.list_pending()[-1]["card_id"]


def clicks(env):
    return env.host.ops().count("click")


def test_answer_inside_the_wait_runs_the_step_once(live, monkeypatch):
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 5.0)

    def approve_when_open():
        for _ in range(200):
            if cards.list_pending():
                break
            time.sleep(0.02)
        live.client.post(f"/mcp-approval-respond/{open_card()}", json={"approved": True}, headers=UI)

    worker = threading.Thread(target=approve_when_open)
    worker.start()
    first = click(live)
    worker.join()
    assert first["ok"] is True and clicks(live) == 1
    retry = click(live)
    assert retry["needs_approval"] is True and clicks(live) == 1


def test_answer_after_the_wait_grants_one_retry(live):
    assert click(live)["needs_approval"] is True
    assert live.client.post(f"/mcp-approval-respond/{open_card()}", json={"approved": True},
                            headers=UI).status_code == 200
    assert click(live)["ok"] is True and clicks(live) == 1
    assert click(live)["needs_approval"] is True and clicks(live) == 1


def test_paired_device_approval_grants_like_the_desktop(live):
    assert click(live)["needs_approval"] is True
    res = cards.answer_card(open_card(), "approve", device="phone:x")
    assert res["status"] == "ok"
    assert click(live)["ok"] is True and clicks(live) == 1
    assert click(live)["needs_approval"] is True and clicks(live) == 1


def test_rejection_leaves_no_grant(live):
    assert click(live)["needs_approval"] is True
    live.client.post(f"/mcp-approval-respond/{open_card()}", json={"approved": False}, headers=UI)
    assert click(live)["needs_approval"] is True
    assert clicks(live) == 0


def closure_var(fn, name):
    return fn.__closure__[fn.__code__.co_freevars.index(name)].cell_contents


def test_halt_drops_the_chats_gate_keys_and_grants(live):
    gate_keys = closure_var(live.router.halt_computer, "_browser_gate_keys")
    grants = closure_var(live.router.halt_computer, "_browser_grants")
    assert click(live)["needs_approval"] is True
    assert len(gate_keys) == 1
    assert live.client.post("/computer-use/halt/1", headers=UI).status_code == 200
    assert gate_keys == {} and grants == {}


def test_delivered_gate_leaves_no_key_behind(live, monkeypatch):
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 5.0)
    gate_keys = closure_var(live.router.halt_computer, "_browser_gate_keys")

    def reject_when_open():
        for _ in range(200):
            if cards.list_pending():
                break
            time.sleep(0.02)
        live.client.post(f"/mcp-approval-respond/{open_card()}", json={"approved": False}, headers=UI)

    worker = threading.Thread(target=reject_when_open)
    worker.start()
    assert click(live)["ok"] is False
    worker.join()
    assert gate_keys == {}
