"""GAMACHINE_NO_UNITY_MCP keeps an isolated dev backend away from the live Unity MCP port.

No test here starts a process or opens a socket: both are replaced by functions that raise.
"""

import socket
import subprocess
from pathlib import Path
from unittest import mock

import pytest

import unity_ai_mcp.mcp_port_guard as guard
import unity_ai_mcp.unity_mcp_manager as m

MAIN_PY = Path(__file__).resolve().parent.parent / "app" / "main.py"


def _forbidden(*args, **kwargs):
    raise AssertionError("process or network access attempted")


def _run(coroutine):
    """Drive a coroutine that must finish without awaiting; an event loop would open a socketpair."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    coroutine.close()
    raise AssertionError("coroutine awaited instead of returning at once")


@pytest.fixture
def hard_guards(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr("os.system", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(guard, "terminate_port_listeners", _forbidden)


@pytest.fixture
def mgr(monkeypatch):
    monkeypatch.setattr(m.unity_mcp_manager, "process", None)
    monkeypatch.setattr(m.unity_mcp_manager, "_starting", False)
    return m.unity_mcp_manager


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes"])
def test_switch_values(monkeypatch, value):
    monkeypatch.setenv("GAMACHINE_NO_UNITY_MCP", value)
    assert m.unity_mcp_disabled()


@pytest.mark.parametrize("value", ["", "0", "no", "false"])
def test_switch_off_values(monkeypatch, value):
    monkeypatch.setenv("GAMACHINE_NO_UNITY_MCP", value)
    assert not m.unity_mcp_disabled()
    monkeypatch.delenv("GAMACHINE_NO_UNITY_MCP")
    assert not m.unity_mcp_disabled()


def test_disabled_manager_touches_nothing(monkeypatch, hard_guards, mgr):
    monkeypatch.setenv("GAMACHINE_NO_UNITY_MCP", "1")
    assert not mgr.start_server()
    mgr.stop_server()
    assert not mgr.is_running()
    assert mgr.port_occupant().identity == m.ServerIdentity.NONE
    assert not _run(mgr.check_health())
    assert not _run(mgr.check_unity_connected())
    assert not _run(mgr.write_autoconnect_when_ready("unused", timeout=1))
    _run(mgr.supervise_once())


def test_enabled_start_server_reaches_old_path(monkeypatch, mgr):
    monkeypatch.delenv("GAMACHINE_NO_UNITY_MCP", raising=False)
    with mock.patch.object(type(mgr), "is_running", return_value=True) as running:
        assert mgr.start_server() is True
    running.assert_called()


def test_enabled_stop_server_reaches_port_cleanup(monkeypatch, mgr):
    monkeypatch.delenv("GAMACHINE_NO_UNITY_MCP", raising=False)
    cleanup = mock.Mock(killed=[], refused=False, message="")
    with mock.patch.object(type(mgr), "is_running", return_value=True), \
            mock.patch.object(guard, "terminate_port_listeners", return_value=cleanup) as terminate:
        mgr.stop_server()
    terminate.assert_called_once()


def test_main_gates_every_unity_mcp_step():
    source = MAIN_PY.read_text(encoding="utf-8")
    assert "unity_mcp_disabled" in source
    assert "no_unity_mcp" in source
    assert "None if no_unity_mcp else asyncio.create_task" in source
    assert "if not no_unity_mcp:" in source
    reset = source.split("def _reset_unity_autoconnect_on_startup", 1)[1].split("def ", 1)[0]
    assert "unity_mcp_disabled()" in reset
