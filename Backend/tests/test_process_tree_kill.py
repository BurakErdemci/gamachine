"""A cancelled or timed-out job leaves no running descendants (real processes)."""
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from agentic import shell_exec
from connectors.installer import Cancelled, Installer, Job

posix_only = pytest.mark.skipif(os.name == "nt", reason="uses fork and pid checks")

# Prints the descendant's pid, then both processes idle; each self-expires.
SPAWNER = "sleep 30 & echo $!; wait"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_gone(pid: int, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


@posix_only
def test_shell_timeout_kills_the_children(tmp_path, monkeypatch):
    pidfile = tmp_path / "child.pid"
    original = subprocess.Popen.communicate
    state = {"first": True}

    def short(self, input=None, timeout=None):
        # Same code path with a 1s deadline instead of 300s.
        if state["first"] and timeout == 300:
            state["first"] = False
            end = time.monotonic() + 1
            while time.monotonic() < end and not pidfile.exists():
                time.sleep(0.02)
            timeout = 0.3
        return original(self, input, timeout)

    monkeypatch.setattr(subprocess.Popen, "communicate", short)
    with pytest.raises(subprocess.TimeoutExpired):
        shell_exec.run(f"sleep 30 & echo $! > {pidfile}; wait", None, str(tmp_path))
    child = int(pidfile.read_text())
    try:
        assert _wait_gone(child), "the child of the timed-out shell is still running"
    finally:
        if _alive(child):
            os.kill(child, signal.SIGKILL)


@posix_only
def test_shell_run_still_returns_output(tmp_path):
    done = shell_exec.run("echo hi", None, str(tmp_path))
    assert (done.returncode, done.stdout.strip()) == (0, "hi")


@posix_only
def test_connector_cancel_kills_descendants_and_frees_the_worker(tmp_path):
    inst = Installer(root=tmp_path / "installs")
    job = Job({"id": "blender"}, None, False)
    inst._jobs["blender"] = job
    pidfile = tmp_path / "child.pid"
    outcome = []

    def worker():
        try:
            inst._exec(job, ["/bin/sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], None, {}, str(tmp_path))
        except Cancelled:
            outcome.append("cancelled")

    job.thread = threading.Thread(target=worker, daemon=True)
    job.thread.start()
    end = time.monotonic() + 3
    while time.monotonic() < end and not (pidfile.exists() and pidfile.read_text().strip() and job.proc):
        time.sleep(0.02)
    child = int(pidfile.read_text())
    try:
        assert inst.cancel("blender", wait_s=3) is True
        assert not job.thread.is_alive(), "the worker stayed blocked on the output pipe"
        assert outcome == ["cancelled"]
        assert _wait_gone(child), "a descendant of the cancelled job is still running"
    finally:
        if _alive(child):
            os.kill(child, signal.SIGKILL)
        job.thread.join(3)
