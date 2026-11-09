"""A Claude chat's unprompted (CLI-continued) turn owns its computer cards and ends its presence."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import providers.claude_sdk_session as css
from agentic import approval_mode, approval_policy, cards
from agentic.command_gates import GATE_OWNERS
from computer_use import activity, settings
from routes.conversation_routes import create_conversation_router

PARAMS = {"pid": 4242, "bundle_id": "com.apple.TextEdit", "app_name": "TextEdit",
          "risky": False, "risk_reason": "", "summary": "Look", "foreground": False}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda cid: False)
    rows = {}
    db = MagicMock()
    db.db_path = str(tmp_path / "db.sqlite")
    db.get_setting.side_effect = rows.get
    db.set_setting.side_effect = rows.__setitem__
    db.get_conversation_owner.return_value = 1
    db.get_conversation_parent.return_value = None
    db.get_side_of.return_value = None
    approval_mode.set_ui_secret("unprompted-owner-secret")
    settings.set_enabled(db, True)
    cards.reset()
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()
    approval_mode.set_ui_secret("")


def owner_of(client, gate):
    r = client.post("/mcp-approval-request", json={
        "gate_id": gate, "tool": "computer_window_state",
        "conversation_id": 7, "params": dict(PARAMS)}).json()
    assert r.get("status") == "ok", r
    return client.get("/mcp-pending").json()["pending"][gate]["conversation_id"]


def _register(monkeypatch, *, reader_done=False, unprompted=True, broken=False, cid=7):
    """A real session in the module registry, no CLI: only its turn state is set."""
    sess = css.ClaudeSDKSession(conversation_id=cid)
    sess._started = True
    sess._client = MagicMock()
    sess._reader_task = SimpleNamespace(done=lambda: reader_done)
    sess._begin_turn(unprompted=unprompted)
    sess._broken = broken
    monkeypatch.setitem(css._SESSIONS, cid, sess)
    return sess


def test_unprompted_turn_owns_its_card(client, monkeypatch):
    _register(monkeypatch)
    assert owner_of(client, "unp-a") == 7


def test_idle_chat_stays_unowned(client, monkeypatch):
    sess = _register(monkeypatch)
    sess._turn_active = False
    assert owner_of(client, "unp-b") is None


def test_turn_after_reader_eof_stays_unowned(client, monkeypatch):
    _register(monkeypatch, reader_done=True)
    assert owner_of(client, "unp-eof") is None


def test_stream_begun_turn_whose_query_raised_stays_unowned(client, monkeypatch):
    # stream() begins the turn, query() raises, the finally clears _out_q: the
    # turn flag stays set on an idle chat.
    sess = _register(monkeypatch, unprompted=False)
    assert sess.unprompted_turn_active
    assert owner_of(client, "unp-q") is None


def test_broken_session_stays_unowned(client, monkeypatch):
    _register(monkeypatch, broken=True)
    assert owner_of(client, "unp-broken") is None


def test_missing_session_stays_unowned(client, monkeypatch):
    monkeypatch.setattr(css, "peek_session", lambda cid: None)
    assert owner_of(client, "unp-c") is None


def test_session_lookup_error_stays_unowned(client, monkeypatch):
    def boom(cid):
        raise RuntimeError("x")
    monkeypatch.setattr(css, "peek_session", boom)
    assert owner_of(client, "unp-d") is None


def _finish_unprompted(cid, monkeypatch, runner_turn):
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda c: runner_turn)
    activity.reset()
    activity.record(cid, "computer_click", PARAMS, "allow")
    assert activity.snapshot()["active"] is True

    async def run():
        session = css.ClaudeSDKSession(conversation_id=cid, cwd=os.getcwd())
        session._begin_turn(unprompted=True)
        assert session.unprompted_turn_active
        await session._finish_turn()

    try:
        asyncio.run(run())
        return activity.snapshot()["active"]
    finally:
        activity.reset()


def test_finishing_an_unprompted_turn_ends_computer_presence(monkeypatch):
    assert _finish_unprompted(7, monkeypatch, runner_turn=False) is False


def test_presence_stays_while_an_agent_runner_turn_is_registered(monkeypatch):
    assert _finish_unprompted(7, monkeypatch, runner_turn=True) is True
