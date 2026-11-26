"""Lane sub-agent chats are scoped to their lane folder; every other chat keeps the main workspace."""
import os
import sqlite3
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import DatabaseManager
from routes import conversation_routes
from unity_lane_paths import scope_workspace

TOKEN = "app-secret"
HEADERS = {"X-Session-Token": TOKEN}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    db = DatabaseManager(str(tmp_path / "p.db"))
    main = tmp_path / "Main"
    (main / ".gamachine" / "lanes").mkdir(parents=True)
    db.save_workspace(1, str(main))
    return db, main, tmp_path


def _agent_chat(db, workspace):
    chat = db.create_conversation(1, "agent", workspace=str(workspace))
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE conversations SET agent_of = ? WHERE id = ?", (999, chat))
    return chat


def test_normal_chat_with_own_workspace_gets_main(env):
    db, main, tmp = env
    other = tmp / "Other"
    other.mkdir()
    chat = db.create_conversation(1, "t", workspace=str(other))
    assert scope_workspace(db, chat, 1) == str(main)


def test_normal_chat_inside_lanes_still_gets_main(env):
    db, main, _ = env
    lane = main / ".gamachine" / "lanes" / "x"
    lane.mkdir()
    chat = db.create_conversation(1, "t", workspace=str(lane))
    assert scope_workspace(db, chat, 1) == str(main)


def test_agent_chat_in_lane_gets_lane(env):
    db, main, _ = env
    lane = main / ".gamachine" / "lanes" / "x"
    lane.mkdir()
    chat = _agent_chat(db, lane)
    assert scope_workspace(db, chat, 1) == str(lane)


def test_agent_chat_elsewhere_gets_main(env):
    db, main, tmp = env
    other = tmp / "Elsewhere"
    other.mkdir()
    assert scope_workspace(db, _agent_chat(db, other), 1) == str(main)


def test_agent_chat_in_nested_lane_folder_gets_main(env):
    db, main, _ = env
    deep = main / ".gamachine" / "lanes" / "x" / "sub"
    deep.mkdir(parents=True)
    assert scope_workspace(db, _agent_chat(db, deep), 1) == str(main)


def test_agent_chat_without_workspace_gets_main(env):
    db, main, _ = env
    chat = db.create_conversation(1, "agent")
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE conversations SET agent_of = 999 WHERE id = ?", (chat,))
    assert scope_workspace(db, chat, 1) == str(main)


def test_lane_junction_escaping_lanes_is_rejected(env):
    if sys.platform != "win32":
        pytest.skip("junction test is Windows-only")
    db, main, tmp = env
    outside = tmp / "Outside"
    outside.mkdir()
    link = main / ".gamachine" / "lanes" / "evil"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("cannot create a junction here")
    try:
        assert scope_workspace(db, _agent_chat(db, link), 1) == str(main)
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


def test_db_error_in_lane_branch_returns_main(env):
    db, main, _ = env

    class Broken:
        get_last_workspace = staticmethod(lambda user_id: str(main))

        @staticmethod
        def get_agent_of(conv_id):
            raise RuntimeError("boom")

    assert scope_workspace(Broken, 1, 1) == str(main)


def test_routes_use_scoped_workspace(env):
    db, main, tmp = env
    lane = main / ".gamachine" / "lanes" / "x"
    lane.mkdir()
    agent = _agent_chat(db, lane)
    normal = db.create_conversation(1, "t", workspace=str(tmp))
    app = FastAPI()
    app.include_router(conversation_routes.create_conversation_router(db))
    client = TestClient(app)

    def ws(chat):
        return client.post("/mcp-chat-workspace", json={"conversation_id": chat}, headers=HEADERS).json()["workspace"]

    assert ws(agent) == str(lane)
    assert ws(normal) == str(main)

    def check(chat, path):
        return client.post("/mcp-project-check", json={"conversation_id": chat, "project_path": path},
                           headers=HEADERS).json()

    assert check(agent, str(lane))["ok"] is True
    assert check(agent, str(main))["ok"] is False
    assert check(normal, str(main))["ok"] is True


def test_lane_chat_keeps_lane_after_project_switch(env):
    db, main, tmp = env
    lane = main / ".gamachine" / "lanes" / "x"
    lane.mkdir()
    chat = _agent_chat(db, lane)
    other = tmp / "OtherProject"
    other.mkdir()
    db.save_workspace(1, str(other))
    assert scope_workspace(db, chat, 1) == str(lane)


def test_agent_chat_invalid_lane_shapes_get_main(env):
    db, main, tmp = env
    notlanes = tmp / "proj" / "notlanes" / "x"
    notlanes.mkdir(parents=True)
    nested = tmp / "proj" / ".gamachine" / "lanes" / "x" / "sub"
    nested.mkdir(parents=True)
    missing = tmp / "proj" / ".gamachine" / "lanes" / "missing"
    bad_name = tmp / "proj" / ".gamachine" / "lanes" / "BadName"
    bad_name.mkdir(parents=True)
    for ws in (notlanes, nested, missing, bad_name):
        assert scope_workspace(db, _agent_chat(db, ws), 1) == str(main)


def test_normal_chat_with_lane_shaped_workspace_gets_main(env):
    db, main, tmp = env
    other = tmp / "OtherProject"
    lane = other / ".gamachine" / "lanes" / "alpha"
    lane.mkdir(parents=True)
    chat = db.create_conversation(1, "t", workspace=str(lane))
    assert scope_workspace(db, chat, 1) == str(main)
