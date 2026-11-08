"""Unity MCP supervision: a dead server is restarted by itself, capped, never after a user stop.

The real manager runs, start_server and stop_server included. Nothing here starts uvx, binds 8080 or
touches a real process: processes, the port and the home directory are faked (tests/unity_boundary.py).
"""
import asyncio
import logging
import os

import pytest

from tests.unity_boundary import FakeProcess, unity_os  # noqa: F401
from unity_ai_mcp import unity_mcp_manager as um


class _Calls:
    """start = server launches attempted, stop = server processes terminated; read at the OS boundary."""

    def __init__(self, boundary):
        self._boundary = boundary

    def _counts(self):
        return {"start": len(self._boundary.popen_calls), "stop": self._boundary.kinds().count("terminate")}

    def __getitem__(self, key):
        return self._counts()[key]

    def __eq__(self, other):
        return self._counts() == other


@pytest.fixture
def sup(unity_os):
    log = um._server_log_path()
    os.makedirs(os.path.dirname(log))
    with open(log, "w", encoding="utf-8") as f:
        f.write("\n".join(f"line {i}" for i in range(30)))
    manager = unity_os.manager()
    manager.enabled = True
    manager.process = unity_os.process(returncode=137)
    return manager, _Calls(unity_os), unity_os


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
    manager, calls, boundary = sup
    manager.process = boundary.process()
    for _ in range(5):
        run(manager.supervise_once())
    assert calls["start"] == 0


def test_health_failures_counted_in_a_row(sup):
    manager, calls, boundary = sup
    manager.process = boundary.process()
    boundary.healthy = False
    for _ in range(8):
        run(manager.supervise_once())
    assert calls["start"] == 0  # 8 misses (~80 s) are patience, not death
    boundary.healthy = True  # a good check resets the count
    run(manager.supervise_once())
    boundary.healthy = False
    for _ in range(8):
        run(manager.supervise_once())
    assert calls["start"] == 0
    run(manager.supervise_once())
    assert calls["start"] == 1
    assert set(boundary.health_timeouts) == {um._SUPERVISOR_HEALTH_TIMEOUT_S}


def test_ninth_failure_restarts_and_dead_process_restarts_at_once(sup):
    manager, calls, boundary = sup
    manager.process = boundary.process()
    boundary.healthy = False
    for _ in range(9):
        run(manager.supervise_once())
    assert calls["start"] == 1
    manager.process = boundary.process(returncode=1)
    boundary.healthy = True
    run(manager.supervise_once())
    assert calls["start"] == 2  # an exited process needs no streak


def test_status_poll_success_inside_the_streak_resets_it(sup):
    manager, calls, boundary = sup
    manager.process = boundary.process()
    boundary.healthy = False
    for _ in range(8):
        run(manager.supervise_once())
    manager._last_health_ok = um.time.monotonic()  # a status poll got /health through
    run(manager.supervise_once())
    assert calls["start"] == 0 and manager._health_fails == 1


def test_health_failures_inside_startup_grace_do_not_count(sup):
    manager, calls, boundary = sup
    manager.process = boundary.process()
    manager._started_at = um.time.monotonic()
    boundary.healthy = False
    for _ in range(5):
        run(manager.supervise_once())
    assert calls["start"] == 0


def test_cap_of_three_then_visible_error(sup):
    manager, calls, boundary = sup
    for _ in range(3):
        manager.process = boundary.process(returncode=1)
        run(manager.supervise_once())
    assert calls["start"] == 3 and manager.last_error is None
    manager.process = boundary.process(returncode=1)
    run(manager.supervise_once())
    assert calls["start"] == 3
    assert manager.last_error == "Unity bağlantı sunucusu tekrar tekrar kapandı; Unity MCP'yi kapatıp açın."
    assert manager.enabled
    manager.process = boundary.process(returncode=1)
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
    manager, calls, boundary = sup
    boundary.port_holder = "foreign"
    run(manager.supervise_once())
    assert calls["start"] == 0


def test_surviving_server_behind_dead_wrapper_is_not_restarted(sup):
    manager, calls, boundary = sup
    boundary.port_holder = "ours"
    run(manager.supervise_once())
    assert calls["start"] == 0


def test_toggle_resets_counter(sup):
    manager, calls, boundary = sup
    for _ in range(4):
        manager.process = boundary.process(returncode=1)
        run(manager.supervise_once())
    assert calls["start"] == 3
    manager.reset_supervision()
    manager.process = boundary.process(returncode=1)
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
    (path / "Packages").mkdir()
    (path / "Packages" / "manifest.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    return str(path)


def test_restart_keeps_autoconnect_on_and_rewrites_it(sup, monkeypatch, tmp_path):
    manager, calls, _ = sup
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
    workspace = _unity_project(tmp_path / "proj")
    manager._write_autoconnect_settings(workspace, auto_start=True)
    manager.stop_server()
    assert manager._read_autoconnect(workspace)["autoStart"] is False


def test_failed_restart_is_retried_within_the_cap(sup):
    manager, calls, boundary = sup
    boundary.spawn_error = OSError("boom")  # uvx cannot be launched
    for _ in range(3):
        run(manager.supervise_once())
    assert calls["start"] == 3 and manager.process is None and not manager._gave_up
    assert "boom" in manager.last_error
    run(manager.supervise_once())
    assert calls["start"] == 3 and manager._gave_up
    status = run(manager._status())
    assert status["status"] == "blocked" and status["reason"] == manager.last_error


def test_failed_restart_leaves_a_foreign_port_alone(sup):
    manager, calls, boundary = sup
    manager.process = None
    boundary.port_holder = "foreign"
    run(manager.supervise_once())
    assert calls["start"] == 0


@pytest.mark.parametrize("language, expected", [
    (None, "Unity bağlantı sunucusu tekrar tekrar kapandı; Unity MCP'yi kapatıp açın."),
    ("tr", "Unity bağlantı sunucusu tekrar tekrar kapandı; Unity MCP'yi kapatıp açın."),
    ("en", "The Unity connection server kept shutting down; turn Unity MCP off and on again."),
])
def test_gave_up_with_live_process_reports_blocked(sup, language, expected):
    manager, calls, boundary = sup
    manager.language = language
    manager._restart_times = [um.time.monotonic()] * 3
    manager.process = boundary.process(pid=4242)
    boundary.healthy = False
    for _ in range(um._HEALTH_FAILS_TO_RESTART):
        run(manager.supervise_once())
    assert manager._gave_up and calls["start"] == 0
    status = run(manager._status())
    assert status["status"] == "blocked" and status["reason"] == expected
    assert status["reason_code"] == "supervisor_gave_up" and status["pid"] == 4242


def test_server_started_by_a_toggle_meanwhile_is_not_stopped(sup):
    manager, calls, boundary = sup

    async def scenario():
        await manager.mcp_lock.acquire()
        task = asyncio.create_task(manager.supervise_once())
        await asyncio.sleep(0.05)
        manager.process = boundary.process()  # the toggle started a fresh server
        manager.mcp_lock.release()
        await task

    run(scenario())
    assert calls == {"start": 0, "stop": 0}


def test_server_that_recovered_meanwhile_is_not_stopped(sup):
    manager, calls, boundary = sup
    manager.process = boundary.process()
    boundary.healthy = False
    for _ in range(um._HEALTH_FAILS_TO_RESTART - 1):
        run(manager.supervise_once())

    async def scenario():
        await manager.mcp_lock.acquire()
        task = asyncio.create_task(manager.supervise_once())  # last failure: decides "dead"
        await asyncio.sleep(0.05)
        boundary.healthy = True
        manager.mcp_lock.release()
        await task

    run(scenario())
    assert calls == {"start": 0, "stop": 0} and manager._health_fails == 0


def test_cancel_during_restart_waits_for_the_start(sup):
    manager, _, boundary = sup
    boundary.spawn_delay = 0.4

    async def scenario():
        task = asyncio.create_task(manager.supervise_once())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return manager.process  # shutdown's stop_server runs now: the start must be done

    process = run(scenario())
    assert isinstance(process, FakeProcess) and process.poll() is None


def test_cancel_during_stop_never_starts(sup):
    manager, calls, boundary = sup
    boundary.stop_delay = 0.3

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
    server = manager.process

    def terminate():
        seen.append(manager._gave_up)
        FakeProcess.terminate(server)

    server.terminate = terminate  # stop_server terminating the server is the stop
    monkeypatch.setattr(um, "unity_mcp_manager", manager)
    monkeypatch.setattr(mcp_routes, "unity_mcp_manager", manager)
    monkeypatch.setattr(workspace_routes, "require_user", lambda *args: (1, {}))
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


def test_autoconnect_is_written_only_after_health_answers(sup, monkeypatch):
    manager, _, boundary = sup
    manager.process = boundary.process()  # alive but not listening yet
    manager.active_workspace = "proj"
    health_calls = {"n": 0}

    async def health():
        health_calls["n"] += 1
        return health_calls["n"] > 3

    async def no_sleep(delay):
        pass

    writes = []
    monkeypatch.setattr(manager, "is_running", lambda: True)
    monkeypatch.setattr(manager, "check_health", health)
    monkeypatch.setattr(um.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(um, "_bring_editor_forward", lambda ws: None)
    monkeypatch.setattr(manager, "_write_autoconnect_settings",
                        lambda ws, auto_start=True: writes.append((ws, auto_start, health_calls["n"])))
    assert run(manager.write_autoconnect_when_ready("proj", timeout=10)) is True
    assert writes == [("proj", True, 4)]  # not written during the three failing health passes


def _no_editor_log(monkeypatch):
    """_status reads the real Unity Editor.log for bridge errors; /api/instances answers come from the boundary."""
    monkeypatch.setattr(um, "bridge_compile_errors", lambda path: [])


def test_status_holds_last_good_through_one_health_miss(sup, monkeypatch):
    manager, _, boundary = sup
    manager.process = boundary.process()
    _no_editor_log(monkeypatch)
    manager._started_at = um.time.monotonic() - 1000
    boundary.instances = [(200, {"instances": [{"name": "p@1"}]})]
    good = run(manager._status())
    assert good["status"] == "connected"
    boundary.healthy = False
    assert run(manager._status()) == good  # one miss: held
    assert run(manager._status())["status"] == "starting"  # two in a row: starting


def test_status_is_starting_when_server_never_answered_since_start(sup, monkeypatch):
    manager, _, boundary = sup
    manager.process = boundary.process()
    _no_editor_log(monkeypatch)
    boundary.healthy = False
    assert run(manager._status())["status"] == "starting"


def test_status_starting_when_supervisor_streak_is_two(sup, monkeypatch):
    manager, _, boundary = sup
    manager.process = boundary.process()
    _no_editor_log(monkeypatch)
    boundary.instances = [(200, {"instances": [{"name": "p@1"}]})]
    run(manager._status())
    boundary.healthy = False
    manager._health_fails = 2
    assert run(manager._status())["status"] == "starting"


def test_one_instances_failure_keeps_connected_two_downgrade(sup, monkeypatch):
    manager, _, boundary = sup
    manager.process = boundary.process()
    _no_editor_log(monkeypatch)
    boundary.instances = [(200, {"instances": [{"name": "p@1"}]}), OSError("slow"), (500, {}),
                          (200, {"instances": []})]
    assert run(manager._status())["status"] == "connected"
    held = run(manager._status())
    assert held["status"] == "connected" and held["instances"] == [{"name": "p@1"}]
    assert run(manager._status())["status"] == "running"  # second failure in a row
    assert run(manager._status())["status"] == "running"  # explicit empty list


def test_explicit_empty_instances_downgrades_at_once(sup, monkeypatch):
    manager, _, boundary = sup
    manager.process = boundary.process()
    _no_editor_log(monkeypatch)
    boundary.instances = [(200, {"instances": [{"name": "p@1"}]}), (200, {"instances": []})]
    assert run(manager._status())["status"] == "connected"
    assert run(manager._status())["status"] == "running"
