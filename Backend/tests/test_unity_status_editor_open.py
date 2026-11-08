"""GET /mcp/unity/status?workspace=... reports whether that project's editor is open."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import routes.mcp_routes as mr
from unity_ai_mcp import unity_project_presence as upp


@pytest.fixture
def client(monkeypatch):
    async def fake_status():
        return {"status": "off"}

    monkeypatch.setattr(mr.unity_mcp_manager, "get_status", fake_status)
    monkeypatch.setattr(mr, "_check_token", lambda token: None)
    app = FastAPI()
    app.include_router(mr.router)
    return TestClient(app)


def test_editor_open_follows_the_process_table(client, monkeypatch):
    seen = []
    pids = {"value": 4242}

    def fake_pid(workspace):
        seen.append(workspace)
        return pids["value"]

    monkeypatch.setattr(upp, "editor_pid", fake_pid)
    monkeypatch.setattr(mr, "require_saved_workspace_when_remote", lambda *a, **k: None)
    assert client.get("/mcp/unity/status", params={"workspace": "C:/proj"}).json()["editor_open"] is True
    pids["value"] = None
    assert client.get("/mcp/unity/status", params={"workspace": "C:/proj"}).json()["editor_open"] is False
    assert seen == ["C:/proj", "C:/proj"]


def test_no_workspace_keeps_the_old_shape(client, monkeypatch):
    def boom(workspace):
        raise AssertionError("must not probe the process table without a workspace")

    monkeypatch.setattr(upp, "editor_pid", boom)
    assert client.get("/mcp/unity/status").json() == {"status": "off"}


def test_remote_token_with_unsaved_workspace_is_refused_like_the_toggle(client, monkeypatch):
    calls = []

    def refuse(path, token):
        calls.append((path, token))
        raise HTTPException(status_code=403, detail="unsaved")

    monkeypatch.setattr(mr, "require_saved_workspace_when_remote", refuse)
    monkeypatch.setattr(upp, "editor_pid", lambda workspace: 1)
    r = client.get("/mcp/unity/status", params={"workspace": "C:/other"}, headers={"X-Session-Token": "remote-tok"})
    assert r.status_code == 403
    assert calls == [("C:/other", "remote-tok")]
