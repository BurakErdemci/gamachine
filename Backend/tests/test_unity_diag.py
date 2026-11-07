"""Temporary Unity MCP diagnostics (unity_diag): JSON-lines records of server start/stop/exit and health changes.

Fakes only: HOME/USERPROFILE point at tmp_path, Popen, port probes and health are stubbed, no uvx, no port 8080.
"""
import asyncio
import json
import subprocess

import pytest

from unity_ai_mcp import unity_mcp_manager as um
from unity_ai_mcp import mcp_port_guard as guard

TOKEN = "s3cret-token-value-xyz"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("GAMACHINE_NO_UNITY_MCP", raising=False)
    return tmp_path


def records(home):
    path = um.unity_diag_path()
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def events(home):
    return [r["event"] for r in records(home)]


class _Proc:
    def __init__(self, code=None, pid=4242):
        self.code = code
        self.pid = pid

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        pass


@pytest.fixture
def mgr(home, monkeypatch):
    monkeypatch.setattr(um, "_server_log_path", lambda: str(home / "server.log"))
    manager = um.UnityMCPManager()
    monkeypatch.setattr(manager, "_ensure_writable_resources", lambda: None)
    monkeypatch.setattr(manager, "_get_uvx", lambda: "uvx")
    monkeypatch.setattr(manager, "_lock_constraints", lambda uvx: None)
    monkeypatch.setattr(manager, "_load_or_create_local_api_token", lambda: TOKEN)
    monkeypatch.setattr(manager, "port_occupant", lambda: um.Occupant(um.ServerIdentity.NONE, "free"))
    monkeypatch.setattr(manager, "is_running", lambda: False)
    monkeypatch.setattr(guard, "foreign_port_owner_infos", lambda port: [])
    return manager


def run(coro):
    return asyncio.run(coro)


def test_record_shape_and_local_timestamp(home):
    um.unity_diag("probe", reason="x", pid=7)
    (rec,) = records(home)
    assert rec["event"] == "probe" and rec["reason"] == "x" and rec["pid"] == 7
    ts = rec["ts"]
    assert ts[4] == "-" and "T" in ts and ts[19] == "." and ts[23] in "+-"  # local ISO time with ms and offset
    assert um.unity_diag_path().startswith(str(home))


def test_diag_never_raises(home, monkeypatch):
    monkeypatch.setattr(um.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))
    um.unity_diag("probe")


def test_diag_log_rotates_keeping_two(home, monkeypatch):
    monkeypatch.setattr(um, "_DIAG_MAX_BYTES", 200)
    for i in range(60):
        um.unity_diag("fill", n=i, pad="x" * 40)
    path = um.unity_diag_path()
    assert (home / ".unity_architect_ai" / "unity_mcp_diag.log.1").exists()
    assert (home / ".unity_architect_ai" / "unity_mcp_diag.log.2").exists()
    assert not (home / ".unity_architect_ai" / "unity_mcp_diag.log.3").exists()
    assert json.loads(open(path, encoding="utf-8").read().splitlines()[-1])["n"] == 59


def test_server_log_over_limit_is_rotated_at_start(mgr, home, monkeypatch):
    monkeypatch.setattr(um, "_SERVER_LOG_MAX_BYTES", 100)
    log = home / "server.log"
    log.write_text("a" * 500, encoding="utf-8")
    (home / "server.log.1").write_text("old", encoding="utf-8")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _Proc())
    assert mgr.start_server(reason="initial")
    assert (home / "server.log.1").read_text(encoding="utf-8") == "a" * 500
    assert log.read_text(encoding="utf-8") == ""


def test_start_server_records_reason_pid_and_no_token(mgr, home, monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd) or _Proc(pid=999))
    assert mgr.start_server(reason="initial")
    (rec,) = [r for r in records(home) if r["event"] == "server_start"]
    assert rec["reason"] == "initial" and rec["pid"] == 999 and rec["cmd"] == seen["cmd"]
    assert rec["port_occupant"] == "none"
    assert rec["token_fp"] and len(rec["token_fp"]) == 8 and rec["token_fp"] != TOKEN
    assert TOKEN not in open(um.unity_diag_path(), encoding="utf-8").read()


def test_start_server_reason_defaults_to_unknown(mgr, home, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _Proc())
    mgr.start_server()
    assert [r["reason"] for r in records(home) if r["event"] == "server_start"] == ["unknown"]


def test_stop_server_records_reason_and_killed(mgr, home, monkeypatch):
    class Cleanup:
        killed = [31, 32]
        refused = False
        message = ""

    monkeypatch.setattr(guard, "collect_owned_listeners", lambda port: set())
    monkeypatch.setattr(guard, "terminate_port_listeners", lambda port, owned_pids=None: Cleanup())
    mgr.process = _Proc(pid=555)
    mgr.stop_server(reason="toggle_off")
    (rec,) = [r for r in records(home) if r["event"] == "server_stop"]
    assert rec["reason"] == "toggle_off" and rec["process_pid"] == 555 and rec["killed_pids"] == [31, 32]


def test_server_exit_record_has_returncode(mgr, home):
    mgr.enabled = True
    mgr.process = _Proc(code=137, pid=77)
    mgr._started_at = um.time.monotonic() - 12
    mgr.port_occupant = lambda: um.Occupant(um.ServerIdentity.FOREIGN, "x")  # supervisor leaves a foreign port alone
    run(mgr.supervise_once())
    run(mgr.supervise_once())
    (rec,) = [r for r in records(home) if r["event"] == "server_exit"]
    assert rec["returncode"] == 137 and rec["pid"] == 77 and 11 <= rec["uptime_s"] <= 60


def test_supervise_once_records_restart_decision_and_reasons(mgr, home, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _Proc(pid=888))
    monkeypatch.setattr(guard, "collect_owned_listeners", lambda port: set())
    monkeypatch.setattr(guard, "terminate_port_listeners",
                        lambda port, owned_pids=None: type("C", (), {"killed": [], "refused": False, "message": ""})())

    async def no_health(timeout=2.0):
        return False

    monkeypatch.setattr(mgr, "check_health", no_health)
    mgr.enabled = True
    mgr.process = _Proc(code=1, pid=11)
    run(mgr.supervise_once())
    names = events(home)
    assert names.index("server_exit") < names.index("supervisor_restart_decision") < names.index("server_stop") \
        < names.index("server_start")
    by = {r["event"]: r for r in records(home)}
    assert by["supervisor_restart_decision"]["exit_code"] == 1
    assert by["supervisor_restart_decision"]["threshold"] == um._HEALTH_FAILS_TO_RESTART
    assert by["server_stop"]["reason"] == "supervisor_restart"
    assert by["server_start"]["reason"] == "supervisor_restart"
    assert mgr._diag_reason is None


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def _fake_client(outcomes):
    """httpx.AsyncClient whose get() pops the next outcome: a _Resp is returned, an exception is raised."""
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            out = outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
            return out

    return Client


def test_health_logged_only_on_change(mgr, home, monkeypatch):
    httpx = um.httpx
    outcomes = [_Resp(200), _Resp(200), httpx.ConnectError("no"), httpx.ConnectError("no"),
                httpx.ReadTimeout("slow"), _Resp(503), _Resp(200), _Resp(200)]
    monkeypatch.setattr(um.httpx, "AsyncClient", _fake_client(outcomes))
    results = [run(mgr.check_health()) for _ in range(8)]
    assert results == [True, True, False, False, False, False, True, True]
    health = [r for r in records(home) if r["event"] == "health"]
    assert [(r["ok"], r["error"]) for r in health] == [(True, None), (False, "connection_refused"), (True, None)]
    assert health[1]["consecutive_failures"] == 1 and "latency_ms" in health[1]


def test_health_error_classes(mgr, home, monkeypatch):
    httpx = um.httpx
    for exc, name in [(httpx.ReadTimeout("t"), "timeout"), (RuntimeError("boom"), "other")]:
        mgr._diag_health_ok = True
        monkeypatch.setattr(um.httpx, "AsyncClient", _fake_client([exc]))
        assert not run(mgr.check_health())
        assert records(home)[-1]["error"] == name
    mgr._diag_health_ok = True
    monkeypatch.setattr(um.httpx, "AsyncClient", _fake_client([_Resp(500)]))
    run(mgr.check_health())
    assert records(home)[-1]["error"] == "http_500"


def test_unity_link_logged_only_on_flip(mgr, home, monkeypatch):
    up, down = _Resp(200, {"instances": [{"name": "p"}]}), _Resp(200, {"instances": []})
    monkeypatch.setattr(um.httpx, "AsyncClient", _fake_client([down, up, up, down, down]))
    assert [run(mgr.check_unity_connected()) for _ in range(5)] == [False, True, True, False, False]
    assert [r["connected"] for r in records(home) if r["event"] == "unity_link"] == [False, True, False]


def test_disabled_writes_no_diag(mgr, home, monkeypatch):
    monkeypatch.setenv("GAMACHINE_NO_UNITY_MCP", "1")
    assert not mgr.start_server()
    mgr.stop_server()
    run(mgr.supervise_once())
    assert not run(mgr.check_health())
    assert not (home / ".unity_architect_ai" / "unity_mcp_diag.log").exists()
