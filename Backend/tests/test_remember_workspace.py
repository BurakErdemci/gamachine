"""remember_workspace lists a project without making it the current one (Hub-created projects)."""
import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from database import DatabaseManager
from routes import workspace_routes
from tests.test_workspace_mcp_switch import switch  # noqa: F401


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return DatabaseManager(str(tmp_path / "remember.db"))


def test_remembered_path_never_becomes_the_current_workspace(db):
    db.save_workspace(1, "/a")
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("UPDATE workspaces SET last_accessed = '2020-01-01 00:00:00.000000' WHERE path = '/a'")
    db.save_workspace(1, "/b")
    db.remember_workspace(1, "/new")
    assert db.get_last_workspace(1) == "/b"
    assert [w["path"] for w in db.get_recent_workspaces(1)] == ["/b", "/new", "/a"]


def test_remembering_a_known_path_changes_nothing(db):
    db.save_workspace(1, "/a")
    db.save_workspace(1, "/b")
    before = db.get_recent_workspaces(1)
    db.remember_workspace(1, "/a")
    assert db.get_recent_workspaces(1) == before
    assert db.get_last_workspace(1) == "/b"


def test_remembering_into_an_empty_list_makes_it_current(db):
    db.remember_workspace(1, "/only")
    assert db.get_last_workspace(1) == "/only"


def test_remember_route_does_not_touch_running_mcp(switch):  # noqa: F811
    response = switch.client.post("/remember-workspace", json={
        "user_id": 1, "path": str(switch.new),
    }, headers={"X-Session-Token": "test-token"})
    assert response.status_code == 200 and response.json() == {"status": "success"}
    assert switch.events == []
    assert switch.manager.active_workspace == str(switch.old)
    switch.db.remember_workspace.assert_called_once_with(1, str(switch.new))


def test_remember_route_requires_a_valid_session(monkeypatch):
    def deny(*args):
        raise HTTPException(status_code=401, detail="unauthorized")

    monkeypatch.setattr(workspace_routes, "require_user", deny)
    db = Mock()
    app = FastAPI()
    app.include_router(workspace_routes.create_workspace_router(db))
    with TestClient(app) as client:
        response = client.post("/remember-workspace", json={"user_id": 1, "path": "/x"},
                               headers={"X-Session-Token": "bad"})
    assert response.status_code == 401
    db.remember_workspace.assert_not_called()
