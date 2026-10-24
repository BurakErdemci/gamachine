"""/mcp-project-check and the scene editor's project guard; process queries are patched."""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import DatabaseManager
from routes import conversation_routes, scene_editor_routes as se
from unity_ai_mcp import unity_project_presence as presence

TOKEN = "app-secret"
HEADERS = {"X-Session-Token": TOKEN}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    db = DatabaseManager(str(tmp_path / "p.db"))
    game, other = tmp_path / "GameA", tmp_path / "GameB"
    game.mkdir()
    other.mkdir()
    db.save_workspace(1, str(game))
    chat = db.create_conversation(1, "t", workspace=str(game))
    app = FastAPI()
    app.include_router(conversation_routes.create_conversation_router(db))
    return TestClient(app), chat, str(game), str(other), db


def _check(client, chat, path, headers=HEADERS):
    return client.post("/mcp-project-check", json={"conversation_id": chat, "project_path": path}, headers=headers)


def test_same_project_is_ok(env):
    client, chat, game, _, _ = env
    response = _check(client, chat, game)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "detail": None}


def test_trailing_slash_is_the_same_project(env):
    client, chat, game, _, _ = env
    assert _check(client, chat, os.path.join(game, "")).json()["ok"] is True


def test_other_project_is_refused_with_both_names(env):
    client, chat, _, other, _ = env
    body = _check(client, chat, other).json()
    assert body["ok"] is False
    assert "GameA" in body["detail"] and "GameB" in body["detail"]


@pytest.mark.parametrize("chat_id", [999999, -1, "7", None])
def test_unknown_chat_is_refused(env, chat_id):
    client, _, game, _, _ = env
    body = _check(client, chat_id, game).json()
    assert body == {"ok": False, "detail": "Bu sohbet bulunamadı; Unity'ye istek gönderilmedi."}


def test_empty_project_path_is_refused(env):
    client, chat, _, _, _ = env
    body = _check(client, chat, "").json()
    assert body["ok"] is False and "proje yolunu bildirmedi" in body["detail"]


def test_no_open_workspace_is_refused(env, monkeypatch):
    client, chat, game, _, db = env
    monkeypatch.setattr(db, "get_last_workspace", lambda user_id: None)
    body = _check(client, chat, game).json()
    assert body["ok"] is False and "açık bir Unity projesi yok" in body["detail"]


def test_wrong_token_is_rejected(env):
    client, chat, game, _, _ = env
    assert _check(client, chat, game, {"X-Session-Token": "wrong"}).status_code >= 400


class Clock:
    now = 100.0


@pytest.fixture
def scene(monkeypatch, env):
    _, _, game, _, db = env
    se._presence_cache.clear()
    monkeypatch.setattr(se.time, "monotonic", lambda: Clock.now)
    sent, asked = [], []

    async def fake_call(command, params):
        sent.append(command)
        return {"success": True}

    state = {"pid": None}

    def editor_pid(workspace):
        asked.append(workspace)
        return state["pid"]

    monkeypatch.setattr(se, "_call_unity", fake_call)
    monkeypatch.setattr(presence, "editor_pid", editor_pid)
    monkeypatch.setattr(presence, "open_project_names", lambda: ["GameB"])
    app = FastAPI()
    app.include_router(se.create_scene_editor_router(db))
    return TestClient(app), sent, asked, state


def test_scene_editor_refuses_without_calling_unity(scene):
    client, sent, _, _ = scene
    response = client.post("/scene-editor/rename", json={"id": 1, "name": "x"}, headers=HEADERS)
    assert response.status_code == 409
    assert "GameA" in response.json()["detail"] and "GameB" in response.json()["detail"]
    assert client.get("/scene-editor/tree", headers=HEADERS).status_code == 409
    assert sent == []


def test_scene_editor_passes_when_project_is_open(scene):
    client, sent, _, state = scene
    state["pid"] = 4242
    assert client.post("/scene-editor/rename", json={"id": 1, "name": "x"}, headers=HEADERS).status_code == 200
    assert sent == ["gm_editor_rename"]


def test_scene_editor_caches_presence_for_two_seconds(scene):
    client, _, asked, state = scene
    state["pid"] = 4242
    Clock.now = 100.0
    client.get("/scene-editor/tree", headers=HEADERS)
    Clock.now = 101.5
    client.get("/scene-editor/tree", headers=HEADERS)
    assert len(asked) == 1
    Clock.now = 102.5
    client.get("/scene-editor/tree", headers=HEADERS)
    assert len(asked) == 2


def test_scene_editor_does_not_cache_a_refusal(scene):
    client, _, asked, state = scene
    assert client.get("/scene-editor/tree", headers=HEADERS).status_code == 409
    state["pid"] = 4242
    assert client.get("/scene-editor/tree", headers=HEADERS).status_code == 200
