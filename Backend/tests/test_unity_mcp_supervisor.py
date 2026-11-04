"""Unity MCP supervision: a dead server is restarted by itself, capped, never after a user stop.

Nothing here starts uvx, binds 8080 or touches a real process: start/stop/health/port probes are faked.
"""
import asyncio
import logging

import pytest

from unity_ai_mcp import unity_mcp_manager as um


class _Proc:
    def __init__(self, code=None):
        self.code = code
        self.pid = 4242

    def poll(self):
        return self.code

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return self.code


@pytest.fixture
def sup(monkeypatch, tmp_path):
    monkeypatch.setattr(um, "_server_log_path", lambda: str(tmp_path / "server.log"))
    (tmp_path / "server.log").write_text("\n".join(f"line {i}" for i in range(30)), encoding="utf-8")
    manager = um.UnityMCPManager()
    calls = {"start": 0, "stop": 0}
    state = {"healthy": True, "occupant": um.ServerIdentity.NONE}

    def start():
        calls["start"] += 1
        manager.process = _Proc(None)
        return True

    async def health():
        return state["healthy"]

    monkeypatch.setattr(manager, "start_server", start)
    monkeypatch.setattr(manager, "stop_server", lambda: calls.__setitem__("stop", calls["stop"] + 1))
    monkeypatch.setattr(manager, "check_health", health)
    monkeypatch.setattr(manager, "port_occupant", lambda: um.Occupant(state["occupant"], "faked"))
    manager.enabled = True
    manager.process = _Proc(code=137)
    return manager, calls, state


def run(coro):
    return asyncio.run(coro)


def test_dead_process_is_restarted_and_exit_code_logged(sup, caplog):
    manager, calls, _ = sup
    with caplog.at_level(logging.WARNING):
        run(manager.supervise_once())
    assert calls["start"] == 1
    assert "process exited with code 137" in caplog.text
    assert "line 29" in caplog.text and "line 10" in caplog.text and "line 9\n" not in caplog.text


def test_live_healthy_process_is_left_alone(sup):
    manager, calls, _ = sup
    manager.process = _Proc(None)
    for _ in range(5):
        run(manager.supervise_once())
    assert calls["start"] == 0


def test_health_failures_counted_in_a_row(sup):
    manager, calls, state = sup
    manager.process = _Proc(None)
    state["healthy"] = False
    run(manager.supervise_once())
    run(manager.supervise_once())
    assert calls["start"] == 0
    state["healthy"] = True  # a good check resets the count
    run(manager.supervise_once())
    state["healthy"] = False
    run(manager.supervise_once())
    run(manager.supervise_once())
    assert calls["start"] == 0
    run(manager.supervise_once())
    assert calls["start"] == 1


def test_health_failures_inside_startup_grace_do_not_count(sup):
    manager, calls, state = sup
    manager.process = _Proc(None)
    manager._started_at = um.time.monotonic()
    state["healthy"] = False
    for _ in range(5):
        run(manager.supervise_once())
    assert calls["start"] == 0


def test_cap_of_three_then_visible_error(sup):
    manager, calls, _ = sup
    for _ in range(3):
        manager.process = _Proc(code=1)
        run(manager.supervise_once())
    assert calls["start"] == 3 and manager.last_error is None
    manager.process = _Proc(code=1)
    run(manager.supervise_once())
    assert calls["start"] == 3
    assert manager.last_error == "Unity bağlantı sunucusu tekrar tekrar kapandı; Unity MCP'yi kapatıp açın."
    assert manager.enabled
    manager.process = _Proc(code=1)
    run(manager.supervise_once())
    assert calls["start"] == 3
    status = run(manager._status())
    assert status["reason"] == manager.last_error


def test_restarts_older_than_window_do_not_count(sup, monkeypatch):
    manager, calls, _ = sup
    now = um.time.monotonic()
    manager._restart_times = [now - um._RESTART_WINDOW_S - 1] * 3
    run(manager.supervise_once())
    assert calls["start"] == 1


def test_user_stop_never_restarts(sup):
    manager, calls, _ = sup
    manager.enabled = False
    run(manager.supervise_once())
    assert calls["start"] == 0


def test_foreign_port_never_restarts(sup):
    manager, calls, state = sup
    state["occupant"] = um.ServerIdentity.FOREIGN
    run(manager.supervise_once())
    assert calls["start"] == 0


def test_surviving_server_behind_dead_wrapper_is_not_restarted(sup):
    manager, calls, state = sup
    state["occupant"] = um.ServerIdentity.OURS
    run(manager.supervise_once())
    assert calls["start"] == 0


def test_toggle_resets_counter(sup):
    manager, calls, _ = sup
    for _ in range(4):
        manager.process = _Proc(code=1)
        run(manager.supervise_once())
    assert calls["start"] == 3
    manager.reset_supervision()
    manager.process = _Proc(code=1)
    run(manager.supervise_once())
    assert calls["start"] == 4


def test_toggle_route_resets_counter(sup, monkeypatch):
    from routes import mcp_routes
    manager, _, _ = sup
    monkeypatch.setattr(mcp_routes, "unity_mcp_manager", manager)
    monkeypatch.setattr(mcp_routes, "_check_token", lambda token: None)
    monkeypatch.setattr(mcp_routes, "stop_mcp", lambda workspace_path=None: {"status": "stopped"})
    manager._gave_up = True
    manager._restart_times = [um.time.monotonic()] * 3
    req = mcp_routes.MCPToggleRequest(enabled=False)
    run(mcp_routes.toggle_unity_mcp(req, None, ""))
    assert manager._gave_up is False and manager._restart_times == []


def _unity_project(path):
    (path / "Assets").mkdir(parents=True)
    (path / "ProjectSettings").mkdir()
    return str(path)


def _real_stop_without_ports(manager, monkeypatch):
    """The real stop_server; its port-guard calls are faked, so no process or port is touched."""
    from unity_ai_mcp import mcp_port_guard as guard
    monkeypatch.setattr(guard, "collect_owned_listeners", lambda *a, **k: set())
    monkeypatch.setattr(guard, "terminate_port_listeners", lambda *a, **k: guard.PortCleanupResult(port=8080))
    monkeypatch.setattr(manager, "_get_uvx", lambda: "")
    monkeypatch.setattr(manager, "stop_server", um.UnityMCPManager.stop_server.__get__(manager))


def test_restart_keeps_autoconnect_on_and_rewrites_it(sup, monkeypatch, tmp_path):
    manager, calls, _ = sup
    _real_stop_without_ports(manager, monkeypatch)
    workspace = _unity_project(tmp_path / "proj")
    manager._write_autoconnect_settings(workspace, auto_start=True)
    manager.active_workspace = workspace
    manager.generation = 7
    writes = []

    async def write(path, generation=None):
        writes.append((path, generation, manager._read_autoconnect(path)["autoStart"]))
        return True

    monkeypatch.setattr(manager, "write_autoconnect_when_ready", write)
    run(manager.supervise_once())
    assert calls["start"] == 1
    assert writes == [(workspace, 7, True)]  # the stop left the file on; the write turns Unity on again
    assert manager._restarting is False


def test_restart_without_active_workspace_writes_nothing(sup, monkeypatch):
    manager, calls, _ = sup
    writes = []

    async def write(path, generation=None):
        writes.append(path)

    monkeypatch.setattr(manager, "write_autoconnect_when_ready", write)
    run(manager.supervise_once())
    assert calls["start"] == 1 and writes == []


def test_user_stop_still_turns_autoconnect_off(sup, monkeypatch, tmp_path):
    manager, _, _ = sup
    _real_stop_without_ports(manager, monkeypatch)
    workspace = _unity_project(tmp_path / "proj")
    manager._write_autoconnect_settings(workspace, auto_start=True)
    manager.stop_server()
    assert manager._read_autoconnect(workspace)["autoStart"] is False


def test_failed_restart_is_retried_within_the_cap(sup, monkeypatch):
    manager, calls, _ = sup
    monkeypatch.setattr(manager, "stop_server", lambda: setattr(manager, "process", None))

    def start_fail():
        calls["start"] += 1
        manager.last_error = "boom"
        return False

    monkeypatch.setattr(manager, "start_server", start_fail)
    for _ in range(3):
        run(manager.supervise_once())
    assert calls["start"] == 3 and manager.process is None and not manager._gave_up
    run(manager.supervise_once())
    assert calls["start"] == 3 and manager._gave_up
    status = run(manager._status())
    assert status["status"] == "blocked" and status["reason"] == manager.last_error


def test_failed_restart_leaves_a_foreign_port_alone(sup):
    manager, calls, state = sup
    manager.process = None
    state["occupant"] = um.ServerIdentity.FOREIGN
    run(manager.supervise_once())
    assert calls["start"] == 0


@pytest.mark.parametrize("language, expected", [
    (None, "Unity bağlantı sunucusu tekrar tekrar kapandı; Unity MCP'yi kapatıp açın."),
    ("tr", "Unity bağlantı sunucusu tekrar tekrar kapandı; Unity MCP'yi kapatıp açın."),
    ("en", "The Unity connection server kept shutting down; turn Unity MCP off and on again."),
])
def test_gave_up_with_live_process_reports_blocked(sup, language, expected):
    manager, calls, state = sup
    manager.language = language
    manager._restart_times = [um.time.monotonic()] * 3
    manager.process = _Proc(None)
    state["healthy"] = False
    for _ in range(3):
        run(manager.supervise_once())
    assert manager._gave_up and calls["start"] == 0
    status = run(manager._status())
    assert status["status"] == "blocked" and status["reason"] == expected
    assert status["reason_code"] == "supervisor_gave_up" and status["pid"] == 4242


def test_server_started_by_a_toggle_meanwhile_is_not_stopped(sup):
    manager, calls, _ = sup

    async def scenario():
        await manager.mcp_lock.acquire()
        task = asyncio.create_task(manager.supervise_once())
        await asyncio.sleep(0.05)
        manager.process = _Proc(None)  # the toggle started a fresh server
        manager.mcp_lock.release()
        await task

    run(scenario())
    assert calls == {"start": 0, "stop": 0}


def test_server_that_recovered_meanwhile_is_not_stopped(sup):
    manager, calls, state = sup
    manager.process = _Proc(None)
    state["healthy"] = False
    run(manager.supervise_once())
    run(manager.supervise_once())

    async def scenario():
        await manager.mcp_lock.acquire()
        task = asyncio.create_task(manager.supervise_once())  # third failure: decides "dead"
        await asyncio.sleep(0.05)
        state["healthy"] = True
        manager.mcp_lock.release()
        await task

    run(scenario())
    assert calls == {"start": 0, "stop": 0} and manager._health_fails == 0


def test_cancel_during_restart_waits_for_the_start(sup, monkeypatch):
    manager, _, _ = sup

    def slow_start():
        um.time.sleep(0.4)
        manager.process = _Proc(None)
        return True

    monkeypatch.setattr(manager, "start_server", slow_start)

    async def scenario():
        task = asyncio.create_task(manager.supervise_once())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return manager.process  # shutdown's stop_server runs now: the start must be done

    assert isinstance(run(scenario()), _Proc)


def test_cancel_during_stop_never_starts(sup, monkeypatch):
    manager, calls, _ = sup
    monkeypatch.setattr(manager, "stop_server", lambda: um.time.sleep(0.3))

    async def scenario():
        task = asyncio.create_task(manager.supervise_once())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert calls["start"] == 0 and manager._restarting is False


def test_workspace_switch_resets_supervision(sup, monkeypatch, tmp_path):
    from unittest.mock import Mock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import mcp_routes, workspace_routes
    from tools import unity_mcp_tools
    from unity_ai_mcp import unity_project_presence
    manager, _, _ = sup
    old = _unity_project(tmp_path / "old")
    new = _unity_project(tmp_path / "new")
    manager.active_workspace = old
    manager._gave_up = True
    manager._restart_times = [um.time.monotonic()] * 3
    seen = []
    monkeypatch.setattr(um, "unity_mcp_manager", manager)
    monkeypatch.setattr(mcp_routes, "unity_mcp_manager", manager)
    monkeypatch.setattr(workspace_routes, "require_user", lambda *args: (1, {}))
    monkeypatch.setattr(manager, "stop_server", lambda: seen.append(manager._gave_up))
    monkeypatch.setattr(manager, "is_unity_running", lambda: True)
    monkeypatch.setattr(manager, "is_running", lambda: False)
    monkeypatch.setattr(manager, "install_package", lambda *a, **k: True)

    async def write(*args, **kwargs):
        return True

    monkeypatch.setattr(manager, "write_autoconnect_when_ready", write)
    monkeypatch.setattr(unity_project_presence, "mcp_mismatch_detail", lambda path: None)
    monkeypatch.setattr(unity_mcp_tools, "unload_unity_tools", lambda: None)
    db = Mock()
    db.get_last_workspace.return_value = old
    app = FastAPI()
    app.include_router(workspace_routes.create_workspace_router(db))
    with TestClient(app) as client:
        response = client.post("/save-workspace", json={"user_id": 1, "path": new},
                               headers={"X-Session-Token": "test-token"})
    assert response.json() == {"status": "success", "mcp": {"state": "switched"}}
    assert seen == [False]  # reset before the stop, like the toggle
    assert manager._gave_up is False and manager._restart_times == []
