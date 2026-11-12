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
