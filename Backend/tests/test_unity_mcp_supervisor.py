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
