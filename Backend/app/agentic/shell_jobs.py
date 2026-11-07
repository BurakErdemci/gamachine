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


def _watch(job: _Job) -> None:
    reader = threading.Thread(target=_read_output, args=(job,), daemon=True)
    reader.start()
    code = job.proc.wait()
    # A grandchild that outlives the shell keeps the pipe open; the tail read so far is enough.
    reader.join(_READER_JOIN_S)
    with _lock:
        job.exit_code = code
        job.ended = time.time()
        job.status = STOPPED if job.stop_requested else EXITED
        listeners = list(_listeners)
    snapshot = job.snapshot()
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
    return job_id


def _get(job_id, conversation_id) -> _Job:
    with _lock:
        job = _jobs.get(job_id)
    if job is None or job.conversation_id != conversation_id:
        raise JobError(f"Unknown job: {job_id}")
    return job


def status(job_id: str, conversation_id=None) -> dict:
    job = _get(job_id, conversation_id)
    with _lock:
        return job.snapshot()


def output(job_id: str, conversation_id=None, tail: int = DEFAULT_TAIL_CHARS) -> str:
    """The last `tail` characters of the job's merged output."""
    job = _get(job_id, conversation_id)
    tail = max(1, min(int(tail), MAX_TAIL_CHARS))
    with job.buffer_lock:
        text = bytes(job.buffer).decode("utf-8", errors="replace")
    return text[-tail:]


def stop(job_id: str, conversation_id=None) -> dict:
    """Ends the job's whole process tree; a job that already ended is returned as it is."""
    job = _get(job_id, conversation_id)
    with _lock:
        if job.status == RUNNING:
            job.stop_requested = True
            live = True
        else:
            live = False
    if live:
        process_tree.end_tree_sync(job.proc)
    with _lock:
        return job.snapshot()


def list_jobs(conversation_id=None) -> list:
    with _lock:
        return [j.snapshot() for j in _jobs.values() if j.conversation_id == conversation_id]
