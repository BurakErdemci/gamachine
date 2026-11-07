"""Closing a Claude session that still has background tasks ends the CLI's
whole process tree, and the notice names why it closed (fakes only)."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import process_tree
from providers import claude_sdk_session as cs
from test_bg_tasks_frames import _live, _started

CONV = 9401
CLI_PID = 4242


class _Proc:
    pid = CLI_PID


class _Transport:
    _process = _Proc()


class _Client:
    def __init__(self, log):
        self._transport = _Transport()
        self._log = log

    async def __aexit__(self, *exc):
        self._log.append("aexit")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    cs._RESET_KILLED.clear()
    cs._RESET_REASONS.clear()
    yield
    cs._SESSIONS.pop(CONV, None)
    cs._RESET_KILLED.clear()
    cs._RESET_REASONS.clear()


def _fakes(monkeypatch, log, kill_error=None):
    def collect(pid):
        log.append(("collect", pid))
        return [11, 12]

    async def kill(pids):
        log.append(("kill", list(pids)))
        if kill_error:
            raise kill_error

    monkeypatch.setattr(process_tree, "collect_descendants", collect)
    monkeypatch.setattr(process_tree, "kill_pids", kill)


async def _session_with_task(log):
    sess = _live(CONV)
    sess._client = _Client(log)
    await sess._on_message(_started("b", "dev server", "local_bash"))
    return sess


def test_running_tasks_collect_descendants_before_the_close_and_kill_them_after(monkeypatch):
    log = []
    _fakes(monkeypatch, log)

    async def run():
        sess = await _session_with_task(log)
        await sess.close()

    asyncio.run(run())
    assert log == [("collect", CLI_PID), "aexit", ("kill", [11, 12])]


def test_a_session_retired_first_still_kills_the_tree(monkeypatch):
    # close_session retires (clears the task row) before it awaits close().
    log = []
    _fakes(monkeypatch, log)

    async def run():
        await _session_with_task(log)
        await cs.close_session(CONV)

    asyncio.run(run())
    assert log == [("collect", CLI_PID), "aexit", ("kill", [11, 12])]


def test_a_close_with_no_tasks_kills_nothing(monkeypatch):
    log = []
    _fakes(monkeypatch, log)

    async def run():
        sess = _live(CONV)
        sess._client = _Client(log)
        await sess.close()

    asyncio.run(run())
    assert log == ["aexit"]


def test_a_killer_error_never_escapes_close(monkeypatch):
    log = []
    _fakes(monkeypatch, log, kill_error=RuntimeError("taskkill missing"))

    async def run():
        sess = await _session_with_task(log)
        await sess.close()
        return sess

    sess = asyncio.run(run())
    assert ("kill", [11, 12]) in log
    assert sess._client is None


def test_an_unreadable_pid_skips_the_kill_without_failing(monkeypatch):
    log = []
    _fakes(monkeypatch, log)

    async def run():
        sess = await _session_with_task(log)
        sess._client._transport = None
        await sess.close()

    asyncio.run(run())
    assert log == ["aexit"]


def test_the_notice_carries_the_reason_the_closer_passed():
    async def run():
        sess = _live(CONV)
        await sess._on_message(_started("b", "dev server", "local_bash"))
        await sess.close("durdurma")

    asyncio.run(run())
    assert cs.take_reset_notice(CONV) == (
        "[Sistem] Oturum kapatıldı (durdurma); şu arka plan işleri sonlandırıldı: "
        "dev server. Gerekirse yeniden başlat.")
    assert cs.take_reset_notice(CONV) == ""


def test_the_notice_defaults_to_a_plain_close():
    async def run():
        sess = _live(CONV)
        await sess._on_message(_started("b", "dev server", "local_bash"))
        await sess.close()

    asyncio.run(run())
    assert "(oturum kapatıldı)" in cs.take_reset_notice(CONV)


def test_a_real_orphaned_grandchild_is_found_and_ended():
    import subprocess
    import time

    code = ("import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(c.pid, flush=True)\n"
            "time.sleep(60)\n")
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    try:
        assert child in process_tree.collect_descendants(parent.pid)
        parent.kill()
        parent.wait(timeout=5)
        asyncio.run(process_tree.kill_pids([child]))
        end = time.monotonic() + 5
        while time.monotonic() < end and child in process_tree.process_table():
            time.sleep(0.05)
        assert child not in process_tree.process_table()
    finally:
        parent.kill()
        parent.stdout.close()
