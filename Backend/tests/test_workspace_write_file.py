"""POST /write-file writes only under the workspace's Assets/Scripts, at the path it checked."""
import os
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import workspace_routes

HEADERS = {"X-Session-Token": "test-token"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(workspace_routes, "get_current_user", lambda *args: (1, {}))
    app = FastAPI()
    app.include_router(workspace_routes.create_workspace_router(Mock()))
    with TestClient(app) as http:
        yield http


@pytest.fixture
def layout(tmp_path, monkeypatch):
    workspace = tmp_path / "Game"
    (workspace / "Assets" / "Scripts").mkdir(parents=True)
    elsewhere = tmp_path / "backend-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return workspace, elsewhere


def _post(client, file_path, workspace):
    return client.post("/write-file", headers=HEADERS,
                       json={"file_path": file_path, "content": "class X {}", "workspace_path": str(workspace)})


def test_relative_path_is_written_under_the_workspace(client, layout):
    workspace, elsewhere = layout
    response = _post(client, "Assets/Scripts/Player.cs", workspace)
    assert response.status_code == 200
    written = workspace / "Assets" / "Scripts" / "Player.cs"
    assert written.read_text(encoding="utf-8") == "class X {}"
    assert response.json()["path"] == os.path.realpath(written)
    assert not (elsewhere / "Assets").exists()


def test_absolute_path_inside_is_written(client, layout):
    workspace, _ = layout
    target = workspace / "Assets" / "Scripts" / "Enemy.cs"
    assert _post(client, str(target), workspace).status_code == 200
    assert target.is_file()


@pytest.mark.parametrize("file_path", [
    "../Other/Assets/Scripts/X.cs",
    "Assets/Prefabs/X.cs",
    "Assets/Scripts/X.txt",
    "~/Assets/Scripts/X.cs",
])
def test_paths_outside_assets_scripts_are_refused(client, layout, file_path):
    workspace, elsewhere = layout
    assert _post(client, file_path, workspace).status_code == 403
    assert not any(elsewhere.rglob("X.*"))


def test_symlink_out_of_scripts_is_refused(client, layout, tmp_path):
    workspace, _ = layout
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "Assets" / "Scripts" / "link").symlink_to(outside)
    assert _post(client, "Assets/Scripts/link/X.cs", workspace).status_code == 403
    assert not (outside / "X.cs").exists()
