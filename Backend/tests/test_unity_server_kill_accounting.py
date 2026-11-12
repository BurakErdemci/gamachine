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


@pytest.fixture
def table(monkeypatch):
    """pid -> parent: the backend (100) started uvx (MCP_PID), which started the server (5000);
    7000 is an unrelated tree and 8000 a shell that started uvx's parent chain's sibling."""
    parents = {100: 1, MCP_PID: 100, 5000: MCP_PID, 7000: 100, 7001: 7000, 8000: 100}
    monkeypatch.setattr(process_tree, "_parent_map_windows", lambda names=None: (names or {}).update(
        {p: "fake.exe" for p in parents}) or parents)
    monkeypatch.setattr(process_tree, "_parent_map_posix", lambda: parents)
    monkeypatch.setattr(process_tree, "_describe_pid", lambda pid, names: {"pid": pid, "image": "fake.exe", "cmd": "fake"})
    return parents


def test_kill_accounting_line_flags_the_mcp_server(mgr, table):
    process_tree.note_kill("site-a", [MCP_PID])
    process_tree.note_kill("site-b", [7000])
    process_tree.note_kill("site-c", [8000, 100])  # 100 is the backend: the MCP server is under it
    lines = {r["site"]: r for r in diag_records("kill_accounting")}
    assert lines["site-a"]["mcp_in_targets"] is True and lines["site-a"]["mcp_pid"] == MCP_PID
    assert lines["site-a"]["targets"] == [{"pid": MCP_PID, "image": "fake.exe", "cmd": "fake"}]
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


def test_command_lines_are_redacted_and_cut():
    text = "uvx.exe --token=abc123SECRETvalue accessToken: s3cr3t-value api_key=zzzTOPzzz " + "x" * 400
    out = process_tree._redact_cmd(text)
    assert "abc123SECRETvalue" not in out and "s3cr3t-value" not in out and "zzzTOPzzz" not in out
    assert len(out) <= 200


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


