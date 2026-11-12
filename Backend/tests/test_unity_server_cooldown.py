"""A silent Unity MCP server death names its killer, and a run of deaths ends in a retry, not a permanent give-up.

Fake processes and a fake process table only: nothing here starts uvx, taskkill or a real kill.
"""
import asyncio
import json
import os

import pytest

import process_tree
from tests.unity_boundary import unity_os  # noqa: F401
from unity_ai_mcp import unity_mcp_manager as um

MCP_PID = 4_000_001


def run(coro):
    return asyncio.run(coro)


def diag_records(event):
    path = um.unity_diag_path()
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in map(json.loads, f) if r["event"] == event]


@pytest.fixture
def mgr(unity_os, monkeypatch):
    manager = unity_os.manager()
    manager.enabled = True
    manager.process = unity_os.process(returncode=None, pid=MCP_PID)
    monkeypatch.setattr(um, "unity_mcp_manager", manager)
    getattr(process_tree, "_RECENT_KILLS", []).clear()
    return manager, unity_os


def _die_and_supervise(manager, boundary):
    manager.process.exit(1)
    run(manager.supervise_once())


def test_supervisor_retries_after_a_cooldown_instead_of_giving_up_for_good(mgr):
    manager, boundary = mgr
    manager.process.exit(1)
    manager._restart_times = [um.time.monotonic()] * um._RESTART_CAP
    run(manager.supervise_once())
    assert manager._gave_up and len(boundary.popen_calls) == 0
    assert manager.last_error and run(manager._status())["reason_code"] == "supervisor_gave_up"
    assert diag_records("supervisor_gave_up")[-1]["retry_in_s"] == 60
    run(manager.supervise_once())  # still cooling down
    assert len(boundary.popen_calls) == 0
    manager._retry_at = um.time.monotonic() - 1
    run(manager.supervise_once())
    assert len(boundary.popen_calls) == 1 and not manager._gave_up


def test_cooldown_doubles_up_to_ten_minutes(mgr):
    manager, boundary = mgr
    manager._restart_times = [um.time.monotonic()] * um._RESTART_CAP
    waits = []
    for _ in range(6):
        manager.process.exit(1)
        run(manager.supervise_once())
        assert manager._gave_up
        waits.append(manager._cooldown_s)
        manager._retry_at = um.time.monotonic() - 1
        run(manager.supervise_once())  # the retry restart (a fresh live process)
        manager._restart_times = [um.time.monotonic()] * um._RESTART_CAP
    assert waits == [60, 120, 240, 480, 600, 600]


def test_server_healthy_for_five_minutes_resets_the_restart_count(mgr):
    manager, boundary = mgr
    manager._restart_times = [um.time.monotonic()] * um._RESTART_CAP
    manager._cooldown_s = 240
    manager._started_at = um.time.monotonic() - 100
    run(manager.supervise_once())
    assert len(manager._restart_times) == um._RESTART_CAP and manager._cooldown_s == 240
    manager._started_at = um.time.monotonic() - um._HEALTHY_RESET_S - 1
    run(manager.supervise_once())
    assert manager._restart_times == [] and manager._cooldown_s == 0


def test_manual_toggle_resets_the_cooldown(mgr):
    manager, boundary = mgr
    manager.process.exit(1)
    manager._restart_times = [um.time.monotonic()] * um._RESTART_CAP
    run(manager.supervise_once())
    assert manager._gave_up
    manager.reset_supervision()
    assert not manager._gave_up and manager._restart_times == [] and manager._cooldown_s == 0
    run(manager.supervise_once())
    assert len(boundary.popen_calls) == 1  # restarted at once, no cooldown wait


def test_status_during_the_cooldown_says_when_the_retry_comes(mgr):
    manager, boundary = mgr
    manager.process.exit(1)
    manager._restart_times = [um.time.monotonic()] * um._RESTART_CAP
    run(manager.supervise_once())
    status = run(manager._status())
    assert status["reason_code"] == "supervisor_gave_up"
    assert 55 <= status["retry_in_s"] <= um._COOLDOWN_START_S
    manager._retry_at = um.time.monotonic() - 5
    assert run(manager._status())["retry_in_s"] == 0


def test_server_exit_during_the_cooldown_does_not_restart_or_extend_it(mgr):
    manager, boundary = mgr
    manager.process.exit(1)
    manager._restart_times = [um.time.monotonic()] * um._RESTART_CAP
    run(manager.supervise_once())
    retry_at, cooldown = manager._retry_at, manager._cooldown_s
    manager.process = boundary.process(returncode=1, pid=MCP_PID + 1)  # died again while waiting
    run(manager.supervise_once())
    assert len(boundary.popen_calls) == 0 and manager._retry_at == retry_at and manager._cooldown_s == cooldown
    manager._retry_at = um.time.monotonic() - 1
    run(manager.supervise_once())
    assert len(boundary.popen_calls) == 1
    assert [r["pid"] for r in diag_records("server_exit")] == [MCP_PID, MCP_PID + 1]


def _unity_project(path):
    (path / "Assets").mkdir(parents=True)
    (path / "ProjectSettings").mkdir()
    (path / "Packages").mkdir()
    (path / "Packages" / "manifest.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    return str(path)


def test_workspace_switch_ends_the_cooldown(mgr, monkeypatch, tmp_path):
    from unittest.mock import Mock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import mcp_routes, workspace_routes
    from tools import unity_mcp_tools
    from unity_ai_mcp import unity_project_presence
    manager, boundary = mgr
    old = _unity_project(tmp_path / "old")
    new = _unity_project(tmp_path / "new")
    manager.active_workspace = old
    manager._gave_up = True
    manager._cooldown_s = 240
    manager._retry_at = um.time.monotonic() + 240
    manager._retry_due = True
    manager._restart_times = [um.time.monotonic()] * um._RESTART_CAP
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
    assert manager._gave_up is False and manager._cooldown_s == 0
    assert manager._retry_due is False and manager._restart_times == []
    assert manager._retry_at == float("inf")


def test_status_survives_a_give_up_without_a_retry_time(mgr):
    # The status endpoint must answer even if _gave_up is set while _retry_at is still infinite.
    manager, _ = mgr
    manager.enabled = True
    manager._gave_up = True
    manager._retry_at = float("inf")
    status = run(manager._status())
    assert status["reason_code"] == "supervisor_gave_up"
    assert status["retry_in_s"] is None
