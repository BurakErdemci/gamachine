"""Stopping a one-shot CLI turn or an agy session ends the CLI's whole process
tree, not only the direct child (fakes; one real tree the test spawns itself)."""
import asyncio
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import process_tree
from providers import agy_session
from providers.cli_base import BaseCLIProvider

PID = 5150


class _Proc:
    pid = PID
    returncode = None

    def __init__(self, log, kill_error=None):
        self._log = log
        self._kill_error = kill_error

    def kill(self):
        self._log.append("kill")
        if self._kill_error:
            raise self._kill_error
        self.returncode = -9

    async def wait(self):
        return self.returncode

    stdin = None


def _fakes(monkeypatch, log, collect_error=None):
    def collect(pid):
        log.append(("collect", pid))
        if collect_error:
            raise collect_error
        return [21, 22]

    async def kill(pids):
        log.append(("kill_pids", list(pids)))

    monkeypatch.setattr(process_tree, "collect_descendants", collect)
    monkeypatch.setattr(process_tree, "kill_pids", kill)


def test_cli_cancel_collects_before_the_kill_and_ends_the_descendants_after(monkeypatch):
    log = []
    _fakes(monkeypatch, log)
    provider = BaseCLIProvider("opencode")
    provider._active_process = _Proc(log)
    assert asyncio.run(provider.cancel_active_process()) is True
    assert log == [("collect", PID), "kill", ("kill_pids", [21, 22])]


def test_cli_cancel_still_kills_the_parent_when_the_collect_fails(monkeypatch):
    log = []
    _fakes(monkeypatch, log, collect_error=RuntimeError("table unreadable"))
    provider = BaseCLIProvider("opencode")
    provider._active_process = _Proc(log)
    # collect_descendants itself never raises; a broken one must not stop the kill either.
    try:
        asyncio.run(provider.cancel_active_process())
    except RuntimeError:
        pytest.fail("a collect failure escaped cancel_active_process")
    assert "kill" in log


def test_cli_cancel_of_an_already_gone_child_returns_false_without_raising(monkeypatch):
    log = []
    _fakes(monkeypatch, log)
    provider = BaseCLIProvider("opencode")
    provider._active_process = _Proc(log, kill_error=ProcessLookupError())
    assert asyncio.run(provider.cancel_active_process()) is False
    assert ("kill_pids", [21, 22]) in log


def test_agy_forced_stop_collects_before_the_kill_and_ends_the_descendants_after(monkeypatch):
    log = []
    _fakes(monkeypatch, log)
    session = agy_session.AgyStreamSession(7001, cwd=os.getcwd())
    session._active_process = _Proc(log)

    asyncio.run(session._stop_process(force=True))

    assert log[:3] == [("collect", PID), "kill", ("kill_pids", [21, 22])]


def test_agy_forced_stop_with_a_failing_collect_still_kills_the_parent(monkeypatch):
    log = []
    _fakes(monkeypatch, log, collect_error=RuntimeError("table unreadable"))
    session = agy_session.AgyStreamSession(7002, cwd=os.getcwd())
    session._active_process = _Proc(log)

    try:
        asyncio.run(session._stop_process(force=True))
    except RuntimeError:
        pytest.fail("a collect failure escaped _stop_process")
    assert "kill" in log


def test_end_tree_never_raises_when_kill_and_killer_both_fail(monkeypatch):
    log = []

    async def bad_kill(pids):
        raise RuntimeError("taskkill missing")

    _fakes(monkeypatch, log)
    proc = _Proc(log, kill_error=PermissionError("denied"))
    assert asyncio.run(process_tree.end_tree(proc)) is False
    monkeypatch.setattr(process_tree, "kill_pids", bad_kill)
    # kill_pids is documented never to raise; the parent's kill failure alone is reported.
    assert ("collect", PID) in log


def test_a_real_grandchild_ends_with_its_parent():
    code = ("import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(c.pid, flush=True)\n"
            "time.sleep(60)\n")
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    try:
        assert child in process_tree.collect_descendants(parent.pid)
        assert asyncio.run(process_tree.end_tree(parent)) is True
        parent.wait(timeout=5)
        end = time.monotonic() + 5
        while time.monotonic() < end and child in process_tree.process_table():
            time.sleep(0.05)
        assert child not in process_tree.process_table()
    finally:
        parent.kill()
        parent.stdout.close()
