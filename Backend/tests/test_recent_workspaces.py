import sqlite3
from contextlib import closing
from datetime import datetime
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import DatabaseManager
import database
from routes.conversation_routes import create_conversation_router
from routes.workspace_routes import create_workspace_router


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return DatabaseManager(str(tmp_path / "recent.db"))


def query(db, sql, args=()):
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        return conn.execute(sql, args).fetchall()


def workspace_of(db, conversation_id):
    return query(db, "SELECT workspace FROM conversations WHERE id = ?", (conversation_id,))[0][0]


def test_migration_preserves_old_chats_without_backfill(db):
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("DROP TABLE conversations")
        conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, user_id INTEGER, "
                     "title TEXT, created_at TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO conversations VALUES (1, 1, 'old', '2000', '2000')")
    db.save_workspace(1, "old-project")
    migrated = DatabaseManager(db.db_path)
    columns = {row[1]: row for row in query(migrated, "PRAGMA table_info(conversations)")}
    assert columns["workspace"][2] == "TEXT"
    assert columns["workspace"][3] == 0
    assert workspace_of(migrated, 1) is None
    assert migrated.get_recent_workspaces(1)[0]["chat_count"] == 0


def test_new_chat_without_workspace_is_null(db):
    assert workspace_of(db, db.create_conversation(1)) is None


def test_new_chat_stamps_latest_workspace_for_its_user(db):
    db.save_workspace(1, "older")
    db.save_workspace(1, "latest")
    db.save_workspace(2, "other-user")
    query(db, "UPDATE workspaces SET last_accessed = '2000' WHERE path = 'older'")
    assert workspace_of(db, db.create_conversation(1)) == "latest"
    query(db, "UPDATE workspaces SET last_accessed = '2000' WHERE user_id = 1")
    assert workspace_of(db, db.create_conversation(1)) == "latest"


def test_explicit_workspace_overrides_latest_saved_row(db):
    db.save_workspace(1, "newer-project")
    assert workspace_of(db, db.create_conversation(1, workspace="open-project")) == "open-project"


@pytest.mark.parametrize("workspace", [None, ""])
def test_empty_workspace_keeps_latest_saved_fallback(db, workspace):
    db.save_workspace(1, "saved-project")
    assert workspace_of(db, db.create_conversation(1, workspace=workspace)) == "saved-project"


def test_same_second_resave_stamps_reopened_workspace(db, monkeypatch):
    times = iter(datetime(2026, 10, 2, 12, 0, 0, microsecond) for microsecond in (1, 2, 3, 4))

    class Clock(datetime):
        @classmethod
        def now(cls):
            return next(times)

    monkeypatch.setattr(database, "datetime", Clock)
    db.save_workspace(1, "A")
    db.save_workspace(1, "B")
    db.save_workspace(1, "A")
    assert workspace_of(db, db.create_conversation(1)) == "A"


def test_recent_workspace_timestamp_hides_stored_microseconds(db):
    db.save_workspace(1, "project")
    stored = query(db, "SELECT last_accessed FROM workspaces")[0][0]
    assert len(stored) == 26
    assert db.get_recent_workspaces(1)[0]["last_accessed"] == stored[:19]
    assert len(db.get_recent_workspaces(1)[0]["last_accessed"]) == 19


def test_recent_workspace_truncates_existing_fractional_timestamp(db):
    db.save_workspace(1, "project")
    query(db, "UPDATE workspaces SET last_accessed = '2026-10-02 12:00:00.123456'")
    assert db.get_recent_workspaces(1)[0]["last_accessed"] == "2026-10-02 12:00:00"


@pytest.fixture
def conversation_client(db):
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app) as client:
        yield client


def test_conversation_route_forwards_explicit_workspace(db, conversation_client, monkeypatch):
    db.save_workspace(1, "newer-project")
    create = Mock(wraps=db.create_conversation)
    monkeypatch.setattr(db, "create_conversation", create)
    response = conversation_client.post("/conversations", headers={"X-Session-Token": ""},
                                        json={"user_id": 1, "title": "explicit", "workspace": "open-project"})
    assert response.status_code == 200
    create.assert_called_once_with(1, "explicit", workspace="open-project")
    assert workspace_of(db, response.json()["id"]) == "open-project"


def test_conversation_route_rejects_overlong_workspace(db, conversation_client, monkeypatch):
    create = Mock(wraps=db.create_conversation)
    monkeypatch.setattr(db, "create_conversation", create)
    response = conversation_client.post("/conversations", headers={"X-Session-Token": ""},
                                        json={"user_id": 1, "workspace": "x" * 4097})
    assert response.status_code == 422
    create.assert_not_called()


def test_conversation_route_accepts_workspace_at_length_limit(db, conversation_client):
    workspace = "x" * 4096
    response = conversation_client.post("/conversations", headers={"X-Session-Token": ""},
                                        json={"user_id": 1, "workspace": workspace})
    assert response.status_code == 200
    assert workspace_of(db, response.json()["id"]) == workspace


@pytest.mark.parametrize("path", [None, "source-project"])
def test_branch_and_side_chat_inherit_source_workspace(db, path):
    if path:
        db.save_workspace(1, path)
    root = db.create_conversation(1)
    db.save_workspace(1, "current-project")
    branch = db.create_branch(root)["id"]
    assert workspace_of(db, branch) == path
    assert workspace_of(db, db.create_branch(branch)["id"]) == path
    assert workspace_of(db, db.create_side_chat(root, 1)) == path
    assert workspace_of(db, db.create_side_chat(branch, 1)) == path


def test_recent_workspace_counts_only_visible_root_chats_for_same_user_and_path(db):
    db.save_workspace(1, "first")
    roots = [db.create_conversation(1) for _ in range(2)]
    db.create_branch(roots[0])
    side = db.create_side_chat(roots[0], 1)
    db.set_conversation_hidden(side, False)
    hidden = db.create_conversation(1)
    db.set_conversation_hidden(hidden, True)
    db.save_workspace(2, "first")
    db.create_conversation(2)
    db.save_workspace(2, "private")
    db.save_workspace(1, "second")
    db.create_conversation(1)
    db.save_workspace(1, "empty")
    rows = db.get_recent_workspaces(1)
    assert [r["path"] for r in rows] == ["empty", "second", "first"]
    assert [r["chat_count"] for r in rows] == [0, 1, 2]
    assert all(set(r) == {"path", "last_accessed", "chat_count"} for r in rows)
    assert all(isinstance(r["last_accessed"], str) for r in rows)


def test_recent_order_and_limit_clamp(db):
    db._ensure_workspace_table()
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.executemany("INSERT INTO workspaces (user_id, path, last_accessed) VALUES (1, ?, ?)",
                         [(f"project-{i}", "2000") for i in range(55)])
        conn.execute("UPDATE workspaces SET last_accessed = '2001' WHERE path = 'project-0'")
    assert len(db.get_recent_workspaces(1)) == 12
    assert len(db.get_recent_workspaces(1, 100)) == 50
    assert len(db.get_recent_workspaces(1, 0)) == 1
    assert len(db.get_recent_workspaces(1, -10)) == 1
    assert [r["path"] for r in db.get_recent_workspaces(1, 3)] == [
        "project-0", "project-54", "project-53"]


def test_workspace_methods_ensure_table(db):
    query(db, "DROP TABLE IF EXISTS workspaces")
    assert db.get_recent_workspaces(1) == []
    query(db, "DROP TABLE workspaces")
    assert db.remove_workspace(1, "missing") is False


def test_remove_deletes_exact_user_path_and_preserves_chats(db):
    db.save_workspace(1, "same")
    chat = db.create_conversation(1)
    db.save_workspace(1, "same-prefix")
    db.save_workspace(2, "same")
    query(db, "INSERT INTO workspaces (user_id, path, last_accessed) VALUES (1, 'same', '2000')")
    assert db.remove_workspace(1, "same") is True
    assert db.remove_workspace(1, "same") is False
    assert query(db, "SELECT user_id, path FROM workspaces ORDER BY id") == [
        (1, "same-prefix"), (2, "same")]
    assert workspace_of(db, chat) == "same"


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(create_workspace_router(db))
    with TestClient(app) as client:
        yield client


def test_routes_return_recent_and_removal_results(db, client):
    db.save_workspace(1, "project")
    db.create_conversation(1)
    headers = {"X-Session-Token": ""}
    response = client.get("/recent-workspaces/1?limit=1", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"workspaces": db.get_recent_workspaces(1, 1)}
    for expected in (True, False):
        response = client.post("/remove-workspace", headers=headers,
                               json={"user_id": 1, "path": "project"})
        assert response.status_code == 200
        assert response.json() == {"removed": expected}


def test_routes_reject_wrong_session_token(db, client, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "correct-token")
    db.save_workspace(1, "project")
    headers = {"X-Session-Token": "wrong-token"}
    assert client.get("/recent-workspaces/1", headers=headers).status_code == 401
    assert client.post("/remove-workspace", headers=headers,
                       json={"user_id": 1, "path": "project"}).status_code == 401
    assert db.get_last_workspace(1) == "project"


def test_routes_reject_other_user(client):
    headers = {"X-Session-Token": ""}
    assert client.get("/recent-workspaces/2", headers=headers).status_code == 403
    assert client.post("/remove-workspace", headers=headers,
                       json={"user_id": 2, "path": "project"}).status_code == 403
