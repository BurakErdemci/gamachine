"""Project switches hand MCP over without leaving the old editor on autoconnect."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient

from routes import mcp_routes, workspace_routes
from tests.unity_boundary import unity_os  # noqa: F401
from unity_ai_mcp import mcp_port_guard
from unity_ai_mcp import unity_mcp_manager as manager_module
from unity_ai_mcp import unity_project_presence
from tools import unity_mcp_tools


SCRIPT = "UserSettings/Gamachine/autoconnect.json"
ON = '"autoStart": true'
OFF = '"autoStart": false'
BOOTSTRAP = (Path(__file__).parents[2] / "unity-mcp" / "MCPForUnity" / "Editor" / "Gamachine"
             / "GamachineAutoConnect.cs")


@pytest.fixture(autouse=True)
def unity_project(tmp_path):
    """Gamachine writes only into a Unity project: Assets and ProjectSettings."""
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()


def unity(path):
    """A Unity project folder: Assets, ProjectSettings and a package manifest."""
    (path / "Assets").mkdir(parents=True)
    (path / "ProjectSettings").mkdir()
    (path / "Packages").mkdir()
    (path / "Packages" / "manifest.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    return path


@pytest.fixture
def switch(monkeypatch, tmp_path, unity_os):
    # The real manager: only processes, the port and the home directory are faked (tests/unity_boundary.py).
    manager = unity_os.manager()
    manager.language = "tr"
    old = unity(tmp_path / "old")
    new = unity(tmp_path / "new")
    manager._write_autoconnect_settings(str(old), auto_start=True)
    manager.active_workspace = str(old)
    assert manager.start_server()  # MCP is on for the old project
    server = manager.process
    unity_os.events.clear()

    monkeypatch.setattr(manager_module, "unity_mcp_manager", manager)
    monkeypatch.setattr(mcp_routes, "unity_mcp_manager", manager)
    monkeypatch.setattr(workspace_routes, "require_user", lambda *args: (1, {}))
    monkeypatch.setattr(mcp_routes, "_check_token", lambda token: None)
    monkeypatch.setattr(unity_project_presence, "mcp_mismatch_detail", Mock(return_value=None))
    unload = Mock()
    monkeypatch.setattr(unity_mcp_tools, "unload_unity_tools", unload)
    db = Mock()
    db.get_last_workspace.return_value = str(old)
    app = FastAPI()
    app.include_router(workspace_routes.create_workspace_router(db))
    with TestClient(app) as client:
        yield SimpleNamespace(manager=manager, old=old, new=new, server=server, os=unity_os,
                              unload=unload, db=db, client=client)


def save(switch, path=None):
    response = switch.client.post("/save-workspace", json={
        "user_id": 1, "path": str(path if path is not None else switch.new),
    }, headers={"X-Session-Token": "test-token"})
    assert response.status_code == 200
    return response.json()


def test_switch_stops_old_project_before_starting_new_one(switch):
    assert save(switch) == {"status": "success", "mcp": {"state": "switched"}}
    # The old server was terminated before the new one was launched.
    assert switch.os.kinds() == ["terminate", "spawn"]
    assert switch.os.events[0][1] is switch.server and switch.manager.process is switch.os.events[1][1]
    switch.unload.assert_called_once_with()
    old_script = (switch.old / SCRIPT).read_text(encoding="utf-8")
    assert OFF in old_script and ON not in old_script
    # The package went into the new project; its autoconnect turned on once the new server answered.
    assert (switch.new / "Packages" / "com.coplaydev.unity-mcp" / "package.json").is_file()
    assert ON in (switch.new / SCRIPT).read_text(encoding="utf-8")
    assert switch.manager.active_workspace == str(switch.new)
    assert switch.manager.language == "tr"
    unity_project_presence.mcp_mismatch_detail.assert_called_once_with(str(switch.new))
    switch.db.get_last_workspace.assert_called_once_with(1)
    switch.db.save_workspace.assert_called_once_with(1, str(switch.new))


def test_switch_reports_refusal_and_leaves_mcp_off(switch, monkeypatch):
    detail = "The new project's Unity Editor is not open."
    start = AsyncMock(side_effect=HTTPException(status_code=409, detail=detail))
    monkeypatch.setattr(mcp_routes, "start_mcp_for", start)
    assert save(switch) == {"status": "success", "mcp": {"state": "off", "reason": detail}}
    assert switch.os.kinds() == ["terminate"]
    assert not switch.manager.is_running()
    assert switch.manager.active_workspace is None
    assert ON not in (switch.old / SCRIPT).read_text(encoding="utf-8")
    assert not (switch.new / SCRIPT).exists()
    start.assert_awaited_once()
    assert start.call_args.args[0] == str(switch.new)
    assert start.call_args.kwargs == {"language": "tr"}


@pytest.mark.parametrize("failure", ["closed", "mismatch", "start"])
def test_switch_preserves_shared_start_errors(switch, failure):
    if failure == "closed":
        switch.os.unity_editor_open = False
        detail = "Unity Editor açık değil. Lütfen önce Unity'yi açın."
    elif failure == "mismatch":
        detail = "A different project is open."
        unity_project_presence.mcp_mismatch_detail.return_value = detail
    else:
        # Another program holds the port: start_server refuses and names it.
        switch.os.foreign_owners = [mcp_port_guard.ProcessInfo(pid=5_000_123, ppid=None, command="other-server")]
    result = save(switch)
    if failure == "start":
        detail = switch.manager.last_error
        assert "PID 5000123" in detail
    assert result == {"status": "success", "mcp": {"state": "off", "reason": detail}}
    assert not switch.manager.is_running()
    assert switch.manager.active_workspace is None
    assert switch.os.kinds() == ["terminate"]  # the old server stopped and no new one was launched
    assert not (switch.new / SCRIPT).exists()  # no readiness write for the refused project


@pytest.mark.parametrize("tracked", [False, True])
def test_off_resets_previous_workspace_instead_of_new_workspace(switch, monkeypatch, tracked):
    switch.server.exit(0)  # MCP is off: no live server process
    switch.manager.active_workspace = str(switch.old) if tracked else None
    if tracked:
        switch.db.get_last_workspace.return_value = str(switch.new)
    reset = Mock(wraps=switch.manager.reset_autoconnect_off)
    monkeypatch.setattr(switch.manager, "reset_autoconnect_off", reset)
    assert save(switch) == {"status": "success"}
    reset.assert_called_once_with(str(switch.old))
    assert ON not in (switch.old / SCRIPT).read_text(encoding="utf-8")
    assert not (switch.new / SCRIPT).exists()
    assert switch.os.events == []
    switch.unload.assert_not_called()


def test_same_normalized_path_does_not_restart(switch):
    assert save(switch, str(switch.old) + "/.") == {"status": "success"}
    assert switch.os.events == []
    switch.unload.assert_not_called()
    assert ON in (switch.old / SCRIPT).read_text(encoding="utf-8")


@pytest.mark.parametrize("language", ["tr", "en", "unsupported", None])
def test_toggle_stores_only_supported_languages(switch, language):
    switch.server.exit(0)
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
    assert not switch.manager.is_running()
    switch.unload.assert_called_once_with()


@pytest.mark.parametrize("language,expected", [("tr", "tr"), ("en", "en"), ("other", "en")])
def test_settings_file_carries_what_the_bootstrap_applies(tmp_path, monkeypatch, language, expected):
    manager = manager_module.UnityMCPManager()
    manager.language = language
    uvx = tmp_path / "uvx.exe"
    uvx.write_bytes(b"test")
    monkeypatch.setattr(manager, "_get_uvx", lambda: str(uvx))
    manager._write_autoconnect_settings(str(tmp_path), auto_start=True)
    settings = json.loads((tmp_path / SCRIPT).read_text(encoding="utf-8"))
    assert set(settings) == {"autoStart", "port", "language", "uvxPath", "revision"}
    assert settings["autoStart"] is True and settings["port"] == 8080
    assert settings["language"] == expected
    assert settings["uvxPath"] == uvx.as_posix()
    assert not (tmp_path / "Assets" / "Editor").exists()
    manager._write_autoconnect_settings(str(tmp_path), auto_start=False)
    off = json.loads((tmp_path / SCRIPT).read_text(encoding="utf-8"))
    assert off["autoStart"] is False and off["revision"] != settings["revision"]


def test_bootstrap_probes_server_and_falls_back_to_off():
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "[InitializeOnLoad]" in text
    assert '"UserSettings", "Gamachine", "autoconnect.json"' in text
    assert "if (!File.Exists(SettingsPath)) return;" in text
    assert "using (var client = new TcpClient())" in text
    assert 'client.ConnectAsync("127.0.0.1", port)' in text
    assert "connection.Wait(150) && client.Connected" in text
    assert "bool connect = settings.autoStart && ServerReachable(settings.port);" in text
    assert "EditorPrefs.SetBool(EditorPrefKeys.AutoStartOnLoad, connect);" in text
    assert "EditorPrefs.SetBool(EditorPrefKeys.ResumeHttpAfterReload, connect);" in text
    assert "EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);" in text
    assert 'EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, $"http://127.0.0.1:{settings.port}");' in text
    assert 'EditorPrefs.SetString(UiLanguageKey, settings.language == "tr" ? "tr" : "en");' in text
    assert "if (!string.IsNullOrEmpty(settings.uvxPath))" in text
    assert "EditorApplication.update += Poll;" in text
    assert "EditorUtility.RequestScriptReload()" in text
    assert (BOOTSTRAP.parent / "GamachineAutoConnect.cs.meta").is_file()
    assert (BOOTSTRAP.parent.parent / "Gamachine.meta").is_file()


def test_settings_file_defaults_to_english(tmp_path, monkeypatch):
    manager = manager_module.UnityMCPManager()
    monkeypatch.setattr(manager, "_get_uvx", lambda: "uvx")
    manager._write_autoconnect_settings(str(tmp_path))
    settings = json.loads((tmp_path / SCRIPT).read_text(encoding="utf-8"))
    assert settings["language"] == "en" and settings["uvxPath"] == ""


def test_old_readiness_task_cannot_reenable_previous_workspace(monkeypatch):
    manager = manager_module.UnityMCPManager()
    manager.active_workspace = "old"

    async def sleep(delay):
        if delay == 1:
            manager.active_workspace = "new"

    monkeypatch.setattr(manager_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(manager, "is_running", Mock(return_value=True))
    monkeypatch.setattr(manager, "check_health", AsyncMock(return_value=True))
    writer = Mock()
    monkeypatch.setattr(manager, "_write_autoconnect_settings", writer)
    forward = Mock()
    monkeypatch.setattr(manager_module, "_bring_editor_forward", forward)
    assert asyncio.run(manager.write_autoconnect_when_ready("old", timeout=1)) is False
    writer.assert_not_called()
    forward.assert_not_called()


def _async_client(switch):
    app = FastAPI()
    app.include_router(workspace_routes.create_workspace_router(switch.db))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _post_save(client, path):
    response = await client.post("/save-workspace", json={"user_id": 1, "path": str(path)},
                                 headers={"X-Session-Token": "t"})
    assert response.status_code == 200
    return response.json()


def test_overlapping_saves_leave_mcp_on_the_last_saved_workspace(switch, tmp_path):
    b, c = unity(tmp_path / "b"), unity(tmp_path / "c")
    entered, release = threading.Event(), threading.Event()

    def presence(workspace):
        if workspace == str(b):
            entered.set()
            assert release.wait(5)
        return None

    unity_project_presence.mcp_mismatch_detail.side_effect = presence

    async def run():
        async with _async_client(switch) as client:
            first = asyncio.create_task(_post_save(client, b))
            # Bounded: a first save that fails before the presence check must fail the test, not hang CI.
            assert await asyncio.to_thread(entered.wait, 5), "first save never reached the presence check"
            second = asyncio.create_task(_post_save(client, c))
            await asyncio.sleep(0.1)
            release.set()
            return await first, await second

    first, second = asyncio.run(run())
    assert first["mcp"] == {"state": "switched"} and second["mcp"] == {"state": "switched"}
    assert switch.manager.active_workspace == str(c)
    assert switch.manager.is_running()
    assert switch.db.save_workspace.call_args_list[-1].args == (1, str(c))
    assert OFF in (b / SCRIPT).read_text(encoding="utf-8")
    assert ON in (c / SCRIPT).read_text(encoding="utf-8")


def test_save_while_server_is_still_starting_retargets_mcp(switch):
    # MCP is enabled but is_running() reads False because the toggled server's process is not up
    # (here: it exited before the save), so only `enabled` tells the save that MCP is on.
    switch.server.exit(0)
    switch.manager.active_workspace = None
    tasks = BackgroundTasks()

    async def run():
        await mcp_routes.toggle_unity_mcp(mcp_routes.MCPToggleRequest(
            enabled=True, workspace_path=str(switch.old)), tasks, "test-token")
        switch.manager.process.exit(1)
        assert switch.manager.enabled and not switch.manager.is_running()
        stale_generation = switch.manager.generation
        async with _async_client(switch) as client:
            result = await _post_save(client, switch.new)
        return stale_generation, result

    stale_generation, result = asyncio.run(run())
    assert result == {"status": "success", "mcp": {"state": "switched"}}
    assert switch.os.kinds() == ["spawn", "terminate", "spawn"]
    assert switch.os.events[1][1] is switch.os.events[0][1]  # the toggled server is the one stopped
    assert switch.manager.active_workspace == str(switch.new)
    assert switch.manager.generation > stale_generation
    assert switch.manager.enabled


def test_readiness_task_from_before_a_restart_is_ignored(monkeypatch):
    manager = manager_module.UnityMCPManager()
    manager.active_workspace = "old"
    manager.generation = 1
    manager.enabled = True

    async def sleep(delay):
        if delay == 1:
            # Toggle off, then on again without a workspace path.
            manager.enabled = False
            manager.generation += 1
            manager.active_workspace = None
            manager.enabled = True
            manager.generation += 1

    monkeypatch.setattr(manager_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(manager, "is_running", Mock(return_value=True))
    monkeypatch.setattr(manager, "check_health", AsyncMock(return_value=True))
    writer = Mock()
    monkeypatch.setattr(manager, "_write_autoconnect_settings", writer)
    forward = Mock()
    monkeypatch.setattr(manager_module, "_bring_editor_forward", forward)
    assert asyncio.run(manager.write_autoconnect_when_ready("old", timeout=1, generation=1)) is False
    writer.assert_not_called()
    forward.assert_not_called()


def test_start_and_stop_advance_generation_and_enabled(switch):
    switch.server.exit(0)
    tasks = BackgroundTasks()
    asyncio.run(mcp_routes.start_mcp_for(str(switch.new), tasks, None))
    assert switch.manager.enabled and switch.manager.generation == 1
    assert tasks.tasks[0].kwargs == {"generation": 1}
    mcp_routes.stop_mcp()
    assert not switch.manager.enabled and switch.manager.generation == 2
