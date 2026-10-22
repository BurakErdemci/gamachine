import ast
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import workspace_routes
from unity_ai_mcp import mcp_port_guard
from unity_ai_mcp import unity_mcp_manager as manager_module


SCRIPT = Path("Assets") / "Editor" / "UnityArchitectAIMCPSetup.cs"
ON = '"MCPForUnity.AutoStartOnLoad", true'
OFF = '"MCPForUnity.AutoStartOnLoad", false'


@pytest.fixture
def manager(monkeypatch):
    manager = manager_module.UnityMCPManager()
    monkeypatch.setattr(manager, "_get_uvx", lambda: "uvx")
    monkeypatch.setattr(manager, "is_running", lambda: False)
    monkeypatch.setattr(manager_module, "unity_mcp_manager", manager)
    return manager


def test_reset_existing_on_script(manager, tmp_path):
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    assert manager.reset_autoconnect_off(str(tmp_path)) is True
    text = (tmp_path / SCRIPT).read_text(encoding="utf-8")
    assert OFF in text and ON not in text
    assert '"MCPForUnity.ResumeHttpAfterReload", false' in text


@pytest.mark.parametrize("kind", ["missing", "off", "empty", "none", "file", "absent", "path_object"])
def test_reset_noop_never_writes(manager, tmp_path, monkeypatch, kind):
    workspace = str(tmp_path)
    if kind == "off":
        manager._write_autoconnect_script(workspace, auto_start=False)
    elif kind == "empty":
        workspace = ""
    elif kind == "none":
        workspace = None
    elif kind == "file":
        file = tmp_path / "ordinary-file"
        file.write_text("not a directory", encoding="utf-8")
        workspace = str(file)
    elif kind == "absent":
        workspace = str(tmp_path / "absent")
    elif kind == "path_object":
        workspace = tmp_path
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    writer = Mock(side_effect=AssertionError("no-op must not write"))
    monkeypatch.setattr(manager, "_write_autoconnect_script", writer)
    assert manager.reset_autoconnect_off(workspace) is False
    writer.assert_not_called()
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    if kind != "off":
        assert not (tmp_path / "Assets").exists()


@pytest.mark.parametrize("operation", ["read", "write"])
def test_reset_io_error_is_debug_only(manager, tmp_path, monkeypatch, caplog, operation):
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    if operation == "read":
        monkeypatch.setattr("builtins.open", Mock(side_effect=OSError("read denied")))
    else:
        monkeypatch.setattr(manager, "_write_autoconnect_script", Mock(side_effect=OSError("write denied")))
    with caplog.at_level(logging.DEBUG):
        assert manager.reset_autoconnect_off(str(tmp_path)) is False
    assert any(record.levelno == logging.DEBUG and "denied" in record.message for record in caplog.records)


def test_stop_resets_all_tracked_projects_and_clears_tracking(manager, tmp_path, monkeypatch):
    projects = [tmp_path / "first", tmp_path / "second"]
    for project in projects:
        manager._write_autoconnect_script(str(project), auto_start=True)
    assert manager._autoconnect_workspaces == {str(p) for p in projects}
    process = Mock()
    manager.process = process
    monkeypatch.setattr(mcp_port_guard, "collect_owned_listeners", Mock(return_value=set()))
    cleanup = Mock(return_value=Mock(killed=[], refused=[]))
    monkeypatch.setattr(mcp_port_guard, "terminate_port_listeners", cleanup)
    manager.stop_server()
    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    cleanup.assert_called_once_with(manager.mcp_port, owned_pids=set())
    assert all(OFF in (project / SCRIPT).read_text(encoding="utf-8") for project in projects)
    assert manager._autoconnect_workspaces == set()
    reset = Mock()
    monkeypatch.setattr(manager, "reset_autoconnect_off", reset)
    manager.stop_server()
    reset.assert_not_called()


def test_explicit_off_discards_tracking(manager, tmp_path):
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    manager._write_autoconnect_script(str(tmp_path), auto_start=False)
    assert manager._autoconnect_workspaces == set()


def test_failed_on_write_is_not_tracked(manager, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.open", Mock(side_effect=OSError("write denied")))
    with pytest.raises(OSError):
        manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    assert manager._autoconnect_workspaces == set()


@pytest.mark.parametrize("running", [False, True])
def test_save_workspace_resets_only_when_mcp_is_off(manager, tmp_path, monkeypatch, running):
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    monkeypatch.setattr(manager, "is_running", lambda: running)
    monkeypatch.setattr(workspace_routes, "require_user", lambda *args: (1, {}))
    db = Mock()
    app = FastAPI()
    app.include_router(workspace_routes.create_workspace_router(db))
    with TestClient(app) as client:
        response = client.post("/save-workspace", json={"user_id": 1, "path": str(tmp_path)},
                               headers={"X-Session-Token": "test-token"})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    db.save_workspace.assert_called_once_with(1, str(tmp_path))
    assert (ON if running else OFF) in (tmp_path / SCRIPT).read_text(encoding="utf-8")


def test_save_workspace_reset_error_keeps_response(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "reset_autoconnect_off", Mock(side_effect=OSError("reset failed")))
    monkeypatch.setattr(workspace_routes, "require_user", lambda *args: (1, {}))
    db = Mock()
    app = FastAPI()
    app.include_router(workspace_routes.create_workspace_router(db))
    with TestClient(app) as client:
        response = client.post("/save-workspace", json={"user_id": 1, "path": str(tmp_path)},
                               headers={"X-Session-Token": "test-token"})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    db.save_workspace.assert_called_once_with(1, str(tmp_path))


@pytest.fixture
def startup_hook():
    # Load the actual helper without importing main's database and services.
    source = Path(__file__).parents[1] / "app" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_reset_unity_autoconnect_on_startup")
    lifespan = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "lifespan")
    startup = ast.Module(body=lifespan.body[:next(
        i for i, node in enumerate(lifespan.body)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Yield))], type_ignores=[])
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == helper.name for node in ast.walk(startup))
    db = Mock()
    namespace = {"db": db, "logger": Mock()}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[helper.name], db


@pytest.mark.parametrize("running", [False, True])
def test_startup_resets_last_workspace_only_when_off(manager, tmp_path, monkeypatch, startup_hook, running):
    hook, db = startup_hook
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    monkeypatch.setattr(manager, "is_running", lambda: running)
    db.get_last_workspace.return_value = str(tmp_path)
    hook()
    if running:
        db.get_last_workspace.assert_not_called()
    else:
        db.get_last_workspace.assert_called_once_with(1)
    assert (ON if running else OFF) in (tmp_path / SCRIPT).read_text(encoding="utf-8")


def test_startup_database_error_does_not_escape(manager, startup_hook):
    hook, db = startup_hook
    db.get_last_workspace.side_effect = OSError("database unavailable")
    hook()
    db.get_last_workspace.assert_called_once_with(1)
