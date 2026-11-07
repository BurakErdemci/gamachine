"""Background shell jobs for the API agent: start a command, poll it, read its tail, stop its tree.

A job runs the same command the blocking `run_command` would run: the protected-file
refusal and the argv-or-shell decision come from `shell_exec`, so a background start
never takes a path the foreground one would not. The approval card is decided by the
loop before the tool runs and looks only at `command`, so `background` cannot skip it.

Output is merged stdout+stderr kept in memory as a bounded tail; nothing is written to disk.
"""
import itertools
import logging
import threading
import time
from collections import OrderedDict

import process_tree
from agentic import shell_exec

logger = logging.getLogger(__name__)

MAX_RUNNING_PER_CONVERSATION = 8
MAX_JOBS_PER_CONVERSATION = 32
OUTPUT_KEEP_BYTES = 256 * 1024
DEFAULT_TAIL_CHARS = 4000
MAX_TAIL_CHARS = 50_000
_READER_JOIN_S = 1.0
STOP_WAIT_S = 3.0
# Descendants are sampled while the job runs: once the shell is gone nothing
# links a grandchild to it on POSIX, and a stop would find nothing to end.
TRACK_FAST_S, TRACK_FAST_INTERVAL_S, TRACK_INTERVAL_S = 10.0, 0.2, 2.0

RUNNING, EXITED, STOPPED = "running", "exited", "stopped"

_lock = threading.Lock()
_jobs: "OrderedDict[str, _Job]" = OrderedDict()
_listeners: list = []
_ids = itertools.count(1)


class JobError(Exception):
    """A refusal the tool shows the model as is."""


class _Job:
    def __init__(self, job_id: str, command: str, conversation_id, proc):
        self.id = job_id
        self.command = command
        self.conversation_id = conversation_id
        self.proc = proc
        self.started = time.time()
        self.ended: float | None = None
        self.status = RUNNING
        self.exit_code: int | None = None
        self.stop_requested = False
        self.buffer = bytearray()
        self.buffer_lock = threading.Lock()
        self.descendants: dict = {}
        self.track_lock = threading.Lock()
        self.done = threading.Event()
        # Windows: holds the shell and everything it starts, even after the shell is gone.
        self.group = process_tree.new_job_object_for(proc)

    def snapshot(self) -> dict:
        end = self.ended or time.time()
        return {
            "job_id": self.id,
            "status": self.status,
            "exit_code": self.exit_code,
            "command": self.command,
            "conversation_id": self.conversation_id,
            "seconds": round(end - self.started, 1),
        }


def add_job_listener(fn) -> None:
    """`fn(snapshot)` is called from a worker thread when a job ends (exited or stopped)."""
    with _lock:
        if fn not in _listeners:
            _listeners.append(fn)


def remove_job_listener(fn) -> None:
    with _lock:
        if fn in _listeners:
            _listeners.remove(fn)


def _read_output(job: _Job) -> None:
    try:
        while True:
            chunk = job.proc.stdout.read1(8192)
            if not chunk:
                break
            with job.buffer_lock:
                job.buffer += chunk
                extra = len(job.buffer) - OUTPUT_KEEP_BYTES
                if extra > 0:
                    del job.buffer[:extra]
    except (OSError, ValueError):
        pass


def _record(job: _Job) -> None:
    with job.track_lock:
        process_tree.record_descendants(getattr(job.proc, "pid", None), job.descendants)


def _end_recorded(job: _Job) -> None:
    with job.track_lock:
        process_tree.end_recorded_sync(job.descendants)


def _track(job: _Job) -> None:
    while not job.done.is_set() and job.status == RUNNING:
        _record(job)
        fast = time.time() - job.started < TRACK_FAST_S
        job.done.wait(TRACK_FAST_INTERVAL_S if fast else TRACK_INTERVAL_S)


def _watch(job: _Job) -> None:
    reader = threading.Thread(target=_read_output, args=(job,), daemon=True)
    reader.start()
    code = job.proc.wait()
    # Windows keeps the parent link of an orphan: the table still shows what the shell started.
    _record(job)
    # A grandchild that outlives the shell keeps the pipe open; the tail read so far is enough.
    reader.join(_READER_JOIN_S)
    with _lock:
        job.exit_code = code
        job.ended = time.time()
        job.status = STOPPED if job.stop_requested else EXITED
        listeners = list(_listeners)
    snapshot = job.snapshot()
    job.done.set()
    for fn in listeners:
        try:
            fn(snapshot)
        except Exception:
            logger.exception("[shell-jobs] listener failed")


def _prune(conversation_id) -> None:
    """Drops the oldest finished jobs of a chat beyond the keep limit. Caller holds `_lock`."""
    mine = [j for j in _jobs.values() if j.conversation_id == conversation_id]
    for job in mine[:max(0, len(mine) - MAX_JOBS_PER_CONVERSATION)]:
        if job.status != RUNNING:
            del _jobs[job.id]
            if job.group is not None:
                job.group.close()


def start(command: str, cwd: str, conversation_id=None) -> str:
    """Starts `command` in the background and returns its job id; JobError on a refusal."""
    if not isinstance(command, str) or not command.strip():
        raise JobError("Command is empty.")
    refused = shell_exec.shell_refusal(command, cwd)
    if refused is not None:
        raise JobError(refused)
    with _lock:
        running = sum(1 for j in _jobs.values()
                      if j.conversation_id == conversation_id and j.status == RUNNING)
        if running >= MAX_RUNNING_PER_CONVERSATION:
            raise JobError(f"Too many background jobs running ({running}); stop one first.")
        job_id = f"job-{next(_ids)}"
        try:
            proc = shell_exec.start_background(command, shell_exec.plan(command, cwd), cwd)
        except OSError as e:
            raise JobError(f"Could not start the command: {e}")
        job = _Job(job_id, command, conversation_id, proc)
        _jobs[job_id] = job
        _prune(conversation_id)
    threading.Thread(target=_watch, args=(job,), daemon=True).start()
    threading.Thread(target=_track, args=(job,), daemon=True).start()
    return job_id


def _get(job_id, conversation_id) -> _Job:
    with _lock:
        job = _jobs.get(job_id)
    if job is None or job.conversation_id != conversation_id:
        raise JobError(f"Unknown job: {job_id}")
    return job


def status(job_id: str, conversation_id=None) -> dict:
    job = _get(job_id, conversation_id)
    if job.status == RUNNING:
        _record(job)
    with _lock:
        return job.snapshot()


def output(job_id: str, conversation_id=None, tail: int = DEFAULT_TAIL_CHARS) -> str:
    """The last `tail` characters of the job's merged output."""
    job = _get(job_id, conversation_id)
    if job.status == RUNNING:
        _record(job)
    tail = max(1, min(int(tail), MAX_TAIL_CHARS))
    with job.buffer_lock:
        text = bytes(job.buffer).decode("utf-8", errors="replace")
    return text[-tail:]


def _stop(job: _Job) -> dict:
    with _lock:
        live = job.status == RUNNING
        if live:
            job.stop_requested = True
    if live:
        _record(job)
        process_tree.end_tree_sync(job.proc)
        job.done.wait(STOP_WAIT_S)
    # Also for a job that already ended: what its shell left behind is still ours to end.
    if job.group is not None:
        job.group.terminate()
    _end_recorded(job)
    with _lock:
        return job.snapshot()


def stop(job_id: str, conversation_id=None) -> dict:
    """Ends the job's whole process tree, including descendants the shell left
    running; the answer is the status after the kill (`stopped` for a job that
    was running, as it is for one that already ended)."""
    return _stop(_get(job_id, conversation_id))


def stop_conversation(conversation_id) -> list:
    """Ends every job of one chat (for when the chat is deleted)."""
    with _lock:
        mine = [j for j in _jobs.values() if j.conversation_id == conversation_id]
    return [_stop(job) for job in mine]


def stop_all() -> None:
    """Ends every job of every chat (backend shutdown)."""
    with _lock:
        everyone = list(_jobs.values())
    for job in everyone:
        try:
            _stop(job)
        except Exception:
            logger.exception("[shell-jobs] stop at shutdown failed for %s", job.id)


def list_jobs(conversation_id=None) -> list:
    with _lock:
        return [j.snapshot() for j in _jobs.values() if j.conversation_id == conversation_id]
