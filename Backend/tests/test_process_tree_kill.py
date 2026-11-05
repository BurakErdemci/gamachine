"""A cancelled connector job leaves no running descendants (real processes)."""
import os
import signal
import sys
import threading
import time

import pytest

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


@posix_only
def test_connector_cancel_ends_a_child_that_outlives_the_leader(tmp_path):
    inst = Installer(root=tmp_path / "installs")
    job = Job({"id": "blender"}, None, False)
    inst._jobs["blender"] = job
    pidfile = tmp_path / "child.pid"
    outcome = []

    def worker():
        try:
            # The leader exits at once; the child keeps the output pipe open.
            inst._exec(job, ["/bin/sh", "-c", f"sleep 30 & echo $! > {pidfile}"], None, {}, str(tmp_path))
        except Cancelled:
            outcome.append("cancelled")
        else:
            outcome.append("finished")

    job.thread = threading.Thread(target=worker, daemon=True)
    job.thread.start()
    end = time.monotonic() + 3
    while time.monotonic() < end and not (pidfile.exists() and pidfile.read_text().strip()):
        time.sleep(0.02)
    child = int(pidfile.read_text())
    time.sleep(0.3)  # let the leader exit
    try:
        assert _alive(child)
        assert inst.cancel("blender", wait_s=3) is True
        assert not job.thread.is_alive(), "the worker stayed blocked on the output pipe"
        assert _wait_gone(child), "the child that outlived the leader is still running"
    finally:
        if _alive(child):
            os.kill(child, signal.SIGKILL)
        job.thread.join(3)
