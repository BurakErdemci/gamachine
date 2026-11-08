"""GAMACHINE_NO_UNITY_MCP also covers the C# sync, the scene editor, a workspace save and the toggle.

No test here starts a process or contacts port 8080: process starts, socket connects to
8080 and HTTP requests are replaced by functions that raise or record.
"""

import json
import socket
import subprocess
import urllib.request
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import omnisharp.omnisharp_manager as omnisharp
import unity_ai_mcp.unity_mcp_manager as m
from routes import mcp_routes, scene_editor_routes, workspace_routes

TOKEN = "gap-test-token"
AUTOCONNECT = ("UserSettings", "Gamachine", "autoconnect.json")
DISABLED_DETAIL = "Unity MCP is disabled for this backend (GAMACHINE_NO_UNITY_MCP)."


def _forbidden(*args, **kwargs):
    raise AssertionError("process or network access attempted")


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setenv("GAMACHINE_NO_UNITY_MCP", "1")
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr("os.system", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)


@pytest.fixture
def requests_seen(monkeypatch):
    """Every urlopen call is recorded and refused, so a swallowed refusal still shows up."""
    seen = []

    def record(request, *args, **kwargs):
        seen.append(getattr(request, "full_url", request))
        raise AssertionError("HTTP request attempted")

    monkeypatch.setattr(urllib.request, "urlopen", record)
    return seen


@pytest.fixture
def mgr(monkeypatch):
    for name in ("is_running", "check_unity_connected", "check_health", "is_unity_running", "start_server"):
        if name in vars(m.unity_mcp_manager):
            monkeypatch.delattr(m.unity_mcp_manager, name)
    monkeypatch.setattr(m.unity_mcp_manager, "process", None)
    monkeypatch.setattr(m.unity_mcp_manager, "_starting", False)
    monkeypatch.setattr(m.unity_mcp_manager, "enabled", False)
    monkeypatch.setattr(m.unity_mcp_manager, "active_workspace", None)
    return m.unity_mcp_manager


def _unity_project(path):
    (path / "Assets").mkdir(parents=True)
    (path / "ProjectSettings").mkdir()
    autoconnect = path.joinpath(*AUTOCONNECT)
    autoconnect.parent.mkdir(parents=True)
    autoconnect.write_text(json.dumps({"autoStart": True, "revision": "live"}), encoding="utf-8")
    return path


def _snapshot(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def _client(db):
    app = FastAPI()
    app.include_router(workspace_routes.create_workspace_router(db))
    app.include_router(mcp_routes.router)
    return TestClient(app)


def test_unity_api_probe_is_off_under_switch(switch_on):
    assert omnisharp._unity_api_up() is False


def test_csproj_sync_sends_no_request_under_switch(switch_on, requests_seen, tmp_path):
    hint = omnisharp.OmniSharpManager()._maybe_sync_csproj(str(tmp_path), "forced by the test")
    assert requests_seen == []
    assert hint  # still the "projects are stale" hint the user sees when Unity is not reachable


def test_scene_editor_answers_unavailable_without_request_under_switch(switch_on, requests_seen):
    with pytest.raises(HTTPException) as refused:
        scene_editor_routes._post_unity("scene_tree", {}, "C:/project")
    assert (refused.value.status_code, refused.value.detail) == (503, "unity_unavailable")
    assert requests_seen == []


def test_save_workspace_leaves_project_files_under_switch(switch_on, mgr, tmp_path):
    project = _unity_project(tmp_path / "project")
    before = _snapshot(project)
    db = Mock()
    db.get_last_workspace.return_value = str(project)
    response = _client(db).post("/save-workspace", json={"user_id": 1, "path": str(project)},
                                headers={"X-Session-Token": TOKEN})
    assert response.status_code == 200, response.text
    db.save_workspace.assert_called_once_with(1, str(project))
    assert _snapshot(project) == before


def test_toggle_on_is_refused_and_starts_nothing_under_switch(switch_on, mgr, monkeypatch):
    monkeypatch.setattr(mgr, "start_server", _forbidden)
    monkeypatch.setattr(mgr, "is_unity_running", _forbidden)
    response = _client(SimpleNamespace()).post("/mcp/unity/toggle", json={"enabled": True},
                                               headers={"X-Session-Token": TOKEN})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == DISABLED_DETAIL
    assert mgr.enabled is False


def test_toggle_off_still_answers_stopped_under_switch(switch_on, mgr):
    response = _client(SimpleNamespace()).post("/mcp/unity/toggle", json={"enabled": False},
                                               headers={"X-Session-Token": TOKEN})
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "stopped"}


def test_toggle_off_with_a_workspace_leaves_its_autoconnect_file_under_switch(switch_on, mgr, tmp_path):
    project = _unity_project(tmp_path / "project")
    before = _snapshot(project)
    response = _client(SimpleNamespace()).post(
        "/mcp/unity/toggle", json={"enabled": False, "workspace_path": str(project)},
        headers={"X-Session-Token": TOKEN})
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "stopped"}
    assert _snapshot(project) == before


def test_toggle_off_with_a_workspace_still_writes_auto_start_false_without_switch(monkeypatch, mgr, tmp_path):
    monkeypatch.delenv("GAMACHINE_NO_UNITY_MCP", raising=False)
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    project = _unity_project(tmp_path / "project")
    response = _client(SimpleNamespace()).post(
        "/mcp/unity/toggle", json={"enabled": False, "workspace_path": str(project)},
        headers={"X-Session-Token": TOKEN})
    assert response.status_code == 200, response.text
    written = json.loads(project.joinpath(*AUTOCONNECT).read_text(encoding="utf-8"))
    assert written["autoStart"] is False
