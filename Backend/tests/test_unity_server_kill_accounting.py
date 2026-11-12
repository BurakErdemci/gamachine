"""A silent Unity MCP server death names its killer, and a run of deaths ends in a retry, not a permanent give-up.

Fake processes and a fake process table only: nothing here starts uvx, taskkill or a real kill.
"""
import asyncio
import json
import os
import subprocess
import sys

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


@pytest.fixture
def process_table(monkeypatch):
    """pid -> parent: the backend (100) started uvx (MCP_PID), which started the server (5000);
    7000 is an unrelated tree and 8000 a shell that started uvx's parent chain's sibling."""
    parents = {100: 1, MCP_PID: 100, 5000: MCP_PID, 7000: 100, 7001: 7000, 8000: 100}
    monkeypatch.setattr(process_tree, "_parent_map_windows", lambda names=None: (names or {}).update(
        {p: "fake.exe" for p in parents}) or parents)
    monkeypatch.setattr(process_tree, "_parent_map_posix", lambda: parents)
    return parents


@pytest.fixture
def table(process_table, monkeypatch):
    monkeypatch.setattr(process_tree, "_describe_pid", lambda pid, names, parents: {
        "pid": pid, "image": "fake.exe", "exe": "C:/fake.exe", "ppid": parents.get(pid)})
    return process_table


def test_kill_accounting_line_flags_the_mcp_server(mgr, table):
    process_tree.note_kill("site-a", [MCP_PID])
    process_tree.note_kill("site-b", [7000])
    process_tree.note_kill("site-c", [8000, 100])  # 100 is the backend: the MCP server is under it
    lines = {r["site"]: r for r in diag_records("kill_accounting")}
    assert lines["site-a"]["mcp_in_targets"] is True and lines["site-a"]["mcp_pid"] == MCP_PID
    assert lines["site-a"]["targets"] == [{"pid": MCP_PID, "image": "fake.exe", "exe": "C:/fake.exe", "ppid": 100}]
    assert lines["site-b"]["mcp_in_targets"] is False
    assert lines["site-c"]["mcp_in_targets"] is True and MCP_PID in lines["site-c"]["mcp_hit_pids"]


def test_kill_accounting_flags_a_target_whose_descendant_is_the_server(mgr, table):
    table[MCP_PID] = 7001  # uvx is now under the 7000 tree
    process_tree.note_kill("tree", [7000])
    assert diag_records("kill_accounting")[-1]["mcp_in_targets"] is True


def test_kill_site_writes_its_line_before_the_kill(mgr, table, monkeypatch):
    seen = []

    class FakeKiller:
        def __init__(self, args, **kwargs):
            seen.append(("kill", [r["site"] for r in diag_records("kill_accounting")]))

    monkeypatch.setattr(process_tree.subprocess, "Popen", FakeKiller)
    monkeypatch.setattr(process_tree.os, "kill", lambda pid, sig: seen.append(("kill", [
        r["site"] for r in diag_records("kill_accounting")])))
    process_tree._kill_pids_sync([MCP_PID])
    assert seen == [("kill", ["process_tree._kill_pids_sync"])]


def test_exit_line_carries_the_recent_kills(mgr, table):
    manager, boundary = mgr
    process_tree.note_kill("site-a", [MCP_PID])
    manager.process.exit(1)
    manager._note_exit(manager.process, "test")
    (exit_line,) = diag_records("server_exit")
    assert exit_line["pid"] == MCP_PID and exit_line["returncode"] == 1
    assert exit_line["parent_pid"] == os.getpid()
    assert [k["site"] for k in exit_line["recent_kills"]] == ["site-a"]
    assert exit_line["recent_kills"][0]["mcp_in_targets"] is True


def test_exit_line_keeps_only_the_last_five_kills_of_the_last_two_minutes(mgr, table):
    manager, _ = mgr
    for n in range(7):
        process_tree.note_kill(f"site-{n}", [7000])
    old = process_tree._RECENT_KILLS[0]
    process_tree._RECENT_KILLS[0] = (old[0] - 500, old[1])  # site-0 happened long ago
    manager.process.exit(1)
    manager._note_exit(manager.process, "test")
    sites = [k["site"] for k in diag_records("server_exit")[0]["recent_kills"]]
    assert sites == [f"site-{n}" for n in range(2, 7)]




SECRET_CMDLINES = ('uvx.exe https://user:pass@host/repo.git ghp_abcdefghijklmnop123456 '
                   '--token "a b c" --api-key sk-ant-zzz accessToken=s3cr3t')


def test_kill_accounting_line_carries_no_command_line_arguments(mgr, process_table, monkeypatch):
    """The line names the executable and parent, never argv (it can hold URLs with credentials and tokens)."""
    monkeypatch.setattr(process_tree, "_cmdline_windows", lambda pid: SECRET_CMDLINES, raising=False)
    monkeypatch.setattr(process_tree, "_exe_path_windows", lambda pid: "C:/tools/uvx.exe", raising=False)

    def fake_ps(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="uvx " + SECRET_CMDLINES, stderr="")

    monkeypatch.setattr(process_tree.subprocess, "run", fake_ps)
    process_tree.note_kill("argv-site", [MCP_PID, 7000])
    path = um.unity_diag_path()
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    for fragment in ("user:pass", "ghp_", "a b c", "sk-ant", "s3cr3t", "--token", "--api-key"):
        assert fragment not in raw
    target = diag_records("kill_accounting")[-1]["targets"][0]
    assert set(target) == {"pid", "image", "exe", "ppid"} and target["ppid"] == 100


def sites_so_far():
    return [r["site"] for r in diag_records("kill_accounting")]


class FakeKillBoundary:
    """Stands in for every kill primitive; records which accounting lines existed when it fired."""

    def __init__(self, monkeypatch):
        self.seen = []

        class Result:
            returncode = 0

            async def wait(inner):
                return 0

        async def spawn(*args, **kwargs):
            self.seen.append(sites_so_far())
            return Result()

        def popen(*args, **kwargs):
            self.seen.append(sites_so_far())
            return Result()

        monkeypatch.setattr(process_tree.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(process_tree.subprocess, "Popen", popen)
        monkeypatch.setattr(process_tree.os, "kill", lambda pid, sig: self.seen.append(sites_so_far()))


class FakeTarget:
    pid = 7000

    def kill(self):
        pass


def test_kill_pids_writes_its_line_before_killing(mgr, table, monkeypatch):
    boundary = FakeKillBoundary(monkeypatch)
    run(process_tree.kill_pids([7000]))
    assert boundary.seen == [["process_tree.kill_pids"]]


def test_end_tree_writes_its_line_before_killing(mgr, table, monkeypatch):
    FakeKillBoundary(monkeypatch)
    target = FakeTarget()
    seen = []
    target.kill = lambda: seen.append(sites_so_far())
    monkeypatch.setattr(process_tree, "_descendants_of", lambda proc: [])
    run(process_tree.end_tree(target))
    assert seen == [["process_tree.end_tree"]]


def test_end_tree_sync_writes_its_line_before_killing(mgr, table, monkeypatch):
    FakeKillBoundary(monkeypatch)
    target = FakeTarget()
    seen = []
    target.kill = lambda: seen.append(sites_so_far())
    monkeypatch.setattr(process_tree, "_descendants_of", lambda proc: [])
    process_tree.end_tree_sync(target)
    assert seen == [["process_tree.end_tree_sync"]]


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill sites are Windows paths")
def test_usage_limits_probe_kill_is_accounted(mgr, table, monkeypatch):
    import usage_limits as ul
    seen = []

    class Proc:
        pid = 7000
        returncode = None
        stdin = None

        def kill(self):
            pass

        async def wait(self):
            self.returncode = 0

    monkeypatch.setattr(ul.subprocess, "run", lambda *a, **k: seen.append(sites_so_far()))
    run(ul._kill_probe(Proc()))
    assert seen == [["usage_limits._kill_probe"]]


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill sites are Windows paths")
def test_chat_titles_kill_is_accounted(mgr, table, monkeypatch):
    from agentic import chat_titles as ct
    seen = []

    class Killer:
        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        seen.append(sites_so_far())
        return Killer()

    class Proc:
        pid = 7000
        returncode = 0

        async def wait(self):
            return 0

    monkeypatch.setattr(ct.asyncio, "create_subprocess_exec", spawn)
    run(ct._kill_tree(Proc()))
    assert seen == [["chat_titles._kill_tree"]]


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill sites are Windows paths")
def test_installer_kill_is_accounted(mgr, table, monkeypatch):
    from connectors import installer as inst
    seen = []

    class Proc:
        pid = 7000

        def poll(self):
            return None

        def kill(self):
            pass

    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: seen.append(sites_so_far()))
    inst.kill_tree(Proc())
    assert seen == [["installer.kill_tree"]]


def test_port_guard_kill_is_accounted(mgr, table, monkeypatch):
    from unity_ai_mcp import mcp_port_guard as guard
    seen = []
    monkeypatch.setattr(guard, "_run", lambda argv, *a, **k: seen.append(sites_so_far()) or "")
    monkeypatch.setattr(guard.os, "kill", lambda pid, sig: seen.append(sites_so_far()))
    assert guard._terminate(MCP_PID) is True
    assert seen == [["mcp_port_guard._terminate"]]
    assert diag_records("kill_accounting")[-1]["mcp_in_targets"] is True


def test_stop_server_terminate_and_kill_are_accounted(mgr, table):
    manager, boundary = mgr
    seen = []
    server = manager.process
    server.terminate = lambda: seen.append(("terminate", sites_so_far()))  # never exits: wait times out

    def kill():
        seen.append(("kill", sites_so_far()))
        server.returncode = -9

    def wait(timeout=None):
        if server.returncode is None:
            raise subprocess.TimeoutExpired("uvx", timeout)
        return server.returncode

    server.kill = kill
    server.wait = wait
    manager.stop_server("test")
    assert seen == [("terminate", ["unity_mcp_manager.stop_server.terminate"]),
                    ("kill", ["unity_mcp_manager.stop_server.terminate", "unity_mcp_manager.stop_server.kill"])]
    assert diag_records("kill_accounting")[0]["mcp_in_targets"] is True


def test_recent_kills_drop_records_older_than_the_window(mgr, table):
    process_tree.note_kill("old", [7000])
    process_tree.note_kill("new", [7000])
    first = process_tree._RECENT_KILLS[0]
    process_tree._RECENT_KILLS[0] = (first[0] - 500, first[1])
    assert [k["site"] for k in process_tree.recent_kills(120, 5)] == ["new"]
    assert [k["site"] for k in process_tree.recent_kills(1000, 5)] == ["old", "new"]


@pytest.mark.parametrize("broken", ["diag", "table", "describe", "pids"])
def test_note_kill_never_raises_into_the_kill_path(mgr, table, monkeypatch, broken):
    def boom(*args, **kwargs):
        raise RuntimeError("accounting broke")

    if broken == "diag":
        monkeypatch.setattr(um, "unity_diag", boom)
    elif broken == "table":
        monkeypatch.setattr(process_tree, "_parent_map_windows", boom)
        monkeypatch.setattr(process_tree, "_parent_map_posix", boom)
    elif broken == "describe":
        monkeypatch.setattr(process_tree, "_describe_pid", boom)
    boundary = FakeKillBoundary(monkeypatch)
    pids = [None, "x"] if broken == "pids" else [7000]
    process_tree._kill_pids_sync(pids)
    run(process_tree.kill_pids(pids))
    process_tree.note_kill("direct", pids)
    if broken != "pids":
        assert len(boundary.seen) == 2  # both kills still fired
