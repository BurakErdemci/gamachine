"""A batch step whose chat lookup raised answers halted, whatever a later lookup returns."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, approval_policy, cards
from computer_use import activity, settings
from routes.conversation_routes import create_conversation_router

TOKEN = {"X-Session-Token": "refusal-token"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "refusal-token")
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda cid: cid == 1)
    rows = {}
    db = MagicMock()
    db.db_path = str(tmp_path / "db.sqlite")
    db.get_setting.side_effect = rows.get
    db.set_setting.side_effect = rows.__setitem__
    db.get_conversation_owner.return_value = 1
    db.get_conversation_parent.return_value = None
    db.get_side_of.return_value = None
    settings.set_enabled(db, True)
    approval_mode.set_mode("auto", source="test")
    cards.reset()
    activity.reset()
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(db=db, client=client)
    cards.reset()
    activity.reset()


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("lookups", [
    [RuntimeError("transient db lock"), 1],
    [1, RuntimeError("transient db lock")],
])
def test_a_raised_lookup_halts_even_when_another_recovers(env, check, lookups):
    env.db.get_conversation_owner.side_effect = lookups + [1, 1]
    response = env.client.post("/computer-use/batch-step", headers=TOKEN,
                               json={"conversation_id": 1, "check": check})
    assert response.json() == {"stop": "halted"}
    assert activity.snapshot()["active"] is False


@pytest.mark.parametrize("check", [False, True])
def test_a_clean_lookup_still_proceeds(env, check):
    response = env.client.post("/computer-use/batch-step", headers=TOKEN,
                               json={"conversation_id": 1, "check": check})
    assert response.json() == {"stop": None}
