"""Project switches hand MCP over without leaving the old editor on autoconnect."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient

from routes import mcp_routes, workspace_routes
from unity_ai_mcp import unity_mcp_manager as manager_module
from unity_ai_mcp import unity_project_presence
from tools import unity_mcp_tools


SCRIPT = "Assets/Editor/UnityArchitectAIMCPSetup.cs"
ON = '"MCPForUnity.AutoStartOnLoad", true'
OFF = '"MCPForUnity.AutoStartOnLoad", false'


@pytest.fixture
def switch(monkeypatch, tmp_path):
    manager = manager_module.UnityMCPManager()
    manager.language = "tr"
    old = tmp_path / "old"
    new = tmp_path / "new"
    manager._get_uvx = Mock(return_value="uvx")
    manager._write_autoconnect_script(str(old), auto_start=True)
    manager.active_workspace = str(old)
    state = SimpleNamespace(running=True)
    events = []

    def stop():
        events.append("stop")
        state.running = False
        for workspace in list(manager._autoconnect_workspaces):
            manager.reset_autoconnect_off(workspace)
        manager._autoconnect_workspaces.clear()

    def start():
        events.append("start")
        state.running = True
        return True

    async def write(workspace):
        manager._write_autoconnect_script(workspace, auto_start=True)

    monkeypatch.setattr(manager_module, "unity_mcp_manager", manager)
    monkeypatch.setattr(mcp_routes, "unity_mcp_manager", manager)
    monkeypatch.setattr(workspace_routes, "require_user", lambda *args: (1, {}))
    monkeypatch.setattr(mcp_routes, "_check_token", lambda token: None)
    monkeypatch.setattr(manager, "is_running", Mock(side_effect=lambda: state.running))
    monkeypatch.setattr(manager, "is_unity_running", Mock(return_value=True))
    monkeypatch.setattr(manager, "stop_server", Mock(side_effect=stop))
    monkeypatch.setattr(manager, "start_server", Mock(side_effect=start))
    monkeypatch.setattr(manager, "install_package", Mock(return_value=True))
    monkeypatch.setattr(manager, "write_autoconnect_when_ready", AsyncMock(side_effect=write))
    monkeypatch.setattr(unity_project_presence, "mcp_mismatch_detail", Mock(return_value=None))
    unload = Mock()
    monkeypatch.setattr(unity_mcp_tools, "unload_unity_tools", unload)
    db = Mock()
    db.get_last_workspace.return_value = str(old)
    app = FastAPI()
    app.include_router(workspace_routes.create_workspace_router(db))
    with TestClient(app) as client:
        yield SimpleNamespace(manager=manager, old=old, new=new, state=state,
                              events=events, unload=unload, db=db, client=client)


def save(switch, path=None):
    response = switch.client.post("/save-workspace", json={
        "user_id": 1, "path": str(path if path is not None else switch.new),
    }, headers={"X-Session-Token": "test-token"})
    assert response.status_code == 200
    return response.json()


def test_switch_stops_old_project_before_starting_new_one(switch):
    assert save(switch) == {"status": "success", "mcp": {"state": "switched"}}
    assert switch.events == ["stop", "start"]
    switch.unload.assert_called_once_with()
    old_script = (switch.old / SCRIPT).read_text(encoding="utf-8")
    assert OFF in old_script and ON not in old_script
    assert ON in (switch.new / SCRIPT).read_text(encoding="utf-8")
    assert switch.manager.active_workspace == str(switch.new)
    assert switch.manager.language == "tr"
    switch.manager.install_package.assert_called_once_with(str(switch.new), write_autoconnect=False)
    switch.manager.write_autoconnect_when_ready.assert_awaited_once_with(str(switch.new))
    unity_project_presence.mcp_mismatch_detail.assert_called_once_with(str(switch.new))
    switch.db.get_last_workspace.assert_called_once_with(1)
    switch.db.save_workspace.assert_called_once_with(1, str(switch.new))


def test_switch_reports_refusal_and_leaves_mcp_off(switch, monkeypatch):
    detail = "The new project's Unity Editor is not open."
    start = AsyncMock(side_effect=HTTPException(status_code=409, detail=detail))
    monkeypatch.setattr(mcp_routes, "start_mcp_for", start)
    assert save(switch) == {"status": "success", "mcp": {"state": "off", "reason": detail}}
    assert switch.events == ["stop"]
    assert not switch.state.running
    assert switch.manager.active_workspace is None
    assert ON not in (switch.old / SCRIPT).read_text(encoding="utf-8")
    assert not (switch.new / SCRIPT).exists()
    start.assert_awaited_once()
    assert start.call_args.args[0] == str(switch.new)
    assert start.call_args.kwargs == {"language": "tr"}


@pytest.mark.parametrize("failure", ["closed", "mismatch", "start"])
def test_switch_preserves_shared_start_errors(switch, failure):
    if failure == "closed":
        switch.manager.is_unity_running.return_value = False
        detail = "Unity Editor açık değil. Lütfen önce Unity'yi açın."
    elif failure == "mismatch":
        detail = "A different project is open."
        unity_project_presence.mcp_mismatch_detail.return_value = detail
    else:
        detail = "Port already occupied."
        switch.manager.last_error = detail
        switch.manager.start_server.side_effect = None
        switch.manager.start_server.return_value = False
    assert save(switch) == {"status": "success", "mcp": {"state": "off", "reason": detail}}
    assert not switch.state.running
    assert switch.manager.active_workspace is None
    switch.manager.write_autoconnect_when_ready.assert_not_awaited()


@pytest.mark.parametrize("tracked", [False, True])
def test_off_resets_previous_workspace_instead_of_new_workspace(switch, monkeypatch, tracked):
    switch.state.running = False
    switch.manager.active_workspace = str(switch.old) if tracked else None
    if tracked:
        switch.db.get_last_workspace.return_value = str(switch.new)
    reset = Mock(wraps=switch.manager.reset_autoconnect_off)
    monkeypatch.setattr(switch.manager, "reset_autoconnect_off", reset)
    assert save(switch) == {"status": "success"}
    reset.assert_called_once_with(str(switch.old))
    assert ON not in (switch.old / SCRIPT).read_text(encoding="utf-8")
    assert not (switch.new / SCRIPT).exists()
    assert switch.events == []
    switch.unload.assert_not_called()


def test_same_normalized_path_does_not_restart(switch):
    assert save(switch, str(switch.old) + "/.") == {"status": "success"}
    assert switch.events == []
    switch.unload.assert_not_called()
    assert ON in (switch.old / SCRIPT).read_text(encoding="utf-8")


@pytest.mark.parametrize("language", ["tr", "en", "unsupported", None])
def test_toggle_stores_only_supported_languages(switch, language):
    switch.state.running = False
    tasks = BackgroundTasks()
    result = asyncio.run(mcp_routes.toggle_unity_mcp(mcp_routes.MCPToggleRequest(
        enabled=True, workspace_path=str(switch.new), language=language), tasks, "test-token"))
    assert result == {"status": "started", "port": 8080, "package_installed": True}
    assert switch.manager.language == (language if language in ("tr", "en") else "tr")
    assert switch.manager.active_workspace == str(switch.new)


def test_toggle_off_clears_active_workspace(switch):
    result = asyncio.run(mcp_routes.toggle_unity_mcp(mcp_routes.MCPToggleRequest(
        enabled=False, workspace_path=str(switch.old)), BackgroundTasks(), "test-token"))
    assert result == {"status": "stopped"}
    assert switch.manager.active_workspace is None
    assert not switch.state.running
    switch.unload.assert_called_once_with()


@pytest.mark.parametrize("language,expected", [("tr", "tr"), ("en", "en"), ("other", "en")])
def test_setup_script_probes_server_and_falls_back_to_off(tmp_path, monkeypatch, language, expected):
    manager = manager_module.UnityMCPManager()
    manager.language = language
    uvx = tmp_path / "uvx.exe"
    uvx.write_bytes(b"test")
    monkeypatch.setattr(manager, "_get_uvx", lambda: str(uvx))
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    text = (tmp_path / SCRIPT).read_text(encoding="utf-8")
    assert "namespace UnityArchitectAI" in text
    assert "static MCPAutoSetup()" in text
    assert "using (var client = new System.Net.Sockets.TcpClient())" in text
    assert 'client.ConnectAsync("127.0.0.1", 8080)' in text
    assert "connection.Wait(150) && client.Connected" in text
    assert "catch { }" in text
    assert "if (serverReachable)" in text
    fallback = text.split("else", 1)[1]
    assert OFF in fallback
    assert '"MCPForUnity.ResumeHttpAfterReload", false' in fallback
    assert f'EditorPrefs.SetString("Gamachine.UiLanguage", "{expected}");' in text
    assert f'EditorPrefs.SetString("MCPForUnity.UvxPath", "{uvx.as_posix()}");' in text
    manager._write_autoconnect_script(str(tmp_path), auto_start=False)
    off_text = (tmp_path / SCRIPT).read_text(encoding="utf-8")
    assert OFF in off_text and ON not in off_text
    assert "ConnectAsync" not in off_text


def test_setup_script_defaults_to_english(tmp_path, monkeypatch):
    manager = manager_module.UnityMCPManager()
    monkeypatch.setattr(manager, "_get_uvx", lambda: "uvx")
    manager._write_autoconnect_script(str(tmp_path))
    assert 'EditorPrefs.SetString("Gamachine.UiLanguage", "en");' in (
        tmp_path / SCRIPT).read_text(encoding="utf-8")


def test_old_readiness_task_cannot_reenable_previous_workspace(monkeypatch):
    manager = manager_module.UnityMCPManager()
    manager.active_workspace = "old"

    async def sleep(delay):
        if delay == 1:
            manager.active_workspace = "new"

    monkeypatch.setattr(manager_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(manager, "is_running", Mock(return_value=True))
    writer = Mock()
    monkeypatch.setattr(manager, "_write_autoconnect_script", writer)
    forward = Mock()
    monkeypatch.setattr(manager_module, "_bring_editor_forward", forward)
    assert asyncio.run(manager.write_autoconnect_when_ready("old", timeout=1)) is False
    writer.assert_not_called()
    forward.assert_not_called()
