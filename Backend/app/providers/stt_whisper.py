"""Offline dictation through the bundled whisper.cpp server (large-v3-turbo q8_0).

The backend owns ONE ``whisper-server`` child. It is started lazily when the
first dictation session opens and the user's speech hides the ~1.1 s model
load: the audio is buffered in the session while the server comes up, and only
``finish`` waits for it. Starting it with the app instead would hold ~1.2 GB of
VRAM (or ~1.1 GB of RAM on a CPU-only machine) for users who never dictate; for
the same reason it is stopped again after ``IDLE_UNLOAD_S`` without dictation.

Engine choices below are measurements, not defaults — see
``~/.claude/deneyler/dikte/SONUC.md`` (28 Sep 2026, 12 clips of the owner's
voice): ``-bs 5 -bo 5 -nlp`` and ``no_timestamps=true`` gave WER 28.0 %; with
timestamps on the server split words ("ek le"); ``-nlp`` saves one encoder pass
(~0.5 s on GPU). ``audio_ctx`` produced garbage (WER 160-250 %) and must not
be used.

Layout (``<root>/bin/whisper-server[.exe]`` + DLLs, ``<root>/models/<model>``):
frozen: ``<app>/resources/whisper`` — the frozen backend is
``<app>/resources/Backend/backend.exe``, hence the ``..`` hop; dev:
``Backend/vendor/whisper`` (filled by ``Backend/vendor/build_whisper.ps1``).
"""
import atexit
import collections
import contextlib
import io
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

from spawn_env import build_spawn_env

logger = logging.getLogger(__name__)

SUPPORTED_LANGS = ("tr", "en")
SAMPLE_RATE = 16000
MODEL_FILE = "ggml-large-v3-turbo-q8_0.bin"
SERVER_EXE = "whisper-server.exe" if sys.platform == "win32" else "whisper-server"

# The app's own ports; a dictation server that grabbed one would break the app
# on its next start.
_RESERVED_PORTS = frozenset({8888, 5858, 8080})

# Measured 28 Sep 2026: model load 1.1 s warm; a cold disk read of 874 MB and a
# slow CPU make it longer, so the ceiling is generous.
STARTUP_TIMEOUT_S = 90.0
# CPU with auto language needs ~11.6 s for a 5 s clip; a 60 s dictation is
# decoded in two 30 s windows, so minutes are possible on a slow machine.
FINAL_TIMEOUT_S = 300.0
PARTIAL_TIMEOUT_S = 15.0
# Owner decision, 28 Sep 2026: the loaded model holds ~1.2 GB of VRAM (~1.1 GB
# RAM on CPU) next to Unity, so an idle server is stopped. The next dictation
# pays a cold start of a few seconds, hidden by the user's speech.
IDLE_UNLOAD_S = 300.0

# Live text (GPU only). Whisper invents words for the first second of silence
# ("Evet." in 10 of 12 clips), so nothing is decoded before 1.5 s of audio.
LIVE_MIN_AUDIO_BYTES = int(1.5 * SAMPLE_RATE) * 2
LIVE_INTERVAL_S = 1.0
# Auto detection on the first seconds of a clip, measured 28 Sep 2026 on the 12
# clips: right on 6/12 at 1.5 s (Japanese, French, Russian, Spanish, English
# for Turkish speech), 11/12 at 2.0 s, 12/12 at 2.5 s. Live updates therefore
# detect on every decode until 2.5 s of audio, and only a detection made on at
# least that much is reused for the later (twice as fast) updates.
LIVE_PIN_MIN_BYTES = int(2.5 * SAMPLE_RATE) * 2

_GPU_ON_RE = re.compile(r"whisper_backend_init_gpu: using (.+?) backend")
_GPU_OFF_MARK = "whisper_backend_init_gpu: no GPU found"

# whisper answers with the full language name; the renderer speaks in codes.
_LANG_CODES = {"turkish": "tr", "english": "en"}


class SttEngineMissing(Exception):
    """The server binary or the model file is not on this machine."""

    def __init__(self, missing):
        super().__init__("dictation engine files missing: " + ", ".join(missing))
        self.missing = list(missing)


class SttEngineFailed(Exception):
    """The server could not start, died, or answered something unusable."""


# ── Paths ────────────────────────────────────────────────────────────────────

def _vendor_dir() -> str:
    # providers/ → app/ → Backend/
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(backend, "vendor", "whisper")


def engine_root() -> str:
    """The env var is an override, not a fallback: when set it is the only
    candidate, so a test or a Docker mount cannot be overtaken by a stale tree."""
    env = os.environ.get("GAMACHINE_WHISPER_DIR", "").strip()
    if env:
        return env
    if getattr(sys, "frozen", False):
        return os.path.normpath(os.path.join(os.path.dirname(sys.executable), "..", "whisper"))
    return _vendor_dir()


def server_path() -> str:
    return os.path.join(engine_root(), "bin", SERVER_EXE)


def model_path() -> str:
    return os.path.join(engine_root(), "models", MODEL_FILE)


def missing_files() -> "list[str]":
    return [p for p in (server_path(), model_path()) if not os.path.isfile(p)]


# ── Pure helpers ─────────────────────────────────────────────────────────────

def physical_cores() -> int:
    """Physical cores, not logical threads.

    Measured 28 Sep 2026 on a Ryzen 5 7500F (6C/12T), CPU only, q8_0, one clip:
    -t 6 → 7.6 s, -t 12 → 6.2 s. Physical is the owner's setting: it leaves the
    other hardware threads to Unity and the app while a CPU decode runs.
    """
    try:
        if sys.platform == "win32":
            n = _windows_physical_cores()
        elif sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True,
                                 timeout=5, env=build_spawn_env())
            n = int(out.stdout.strip())
        else:
            n = _linux_physical_cores()
        if n and n > 0:
            return n
    except Exception:                                # noqa: BLE001 — any failure falls back
        pass
    logical = os.cpu_count() or 4
    return max(1, logical // 2)


def _windows_physical_cores() -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    relation_processor_core = 0
    size = wintypes.DWORD(0)
    kernel32.GetLogicalProcessorInformationEx(relation_processor_core, None, ctypes.byref(size))
    buf = (ctypes.c_byte * size.value)()
    if not kernel32.GetLogicalProcessorInformationEx(relation_processor_core, buf, ctypes.byref(size)):
        return 0
    count, offset = 0, 0
    while offset < size.value:
        # Each record starts with Relationship (DWORD) and Size (DWORD).
        rec_size = int.from_bytes(bytes(buf[offset + 4:offset + 8]), "little", signed=False)
        if rec_size <= 0:
            break
        count += 1
        offset += rec_size
    return count


def _linux_physical_cores() -> int:
    cores = set()
    phys = core = None
    with open("/proc/cpuinfo", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("physical id"):
                phys = line.split(":", 1)[1].strip()
            elif line.startswith("core id"):
                core = line.split(":", 1)[1].strip()
            elif not line.strip():
                if core is not None:
                    cores.add((phys, core))
                phys = core = None
    return len(cores)


def choose_language(gpu, ui_lang: str, auto_on_cpu: bool) -> str:
    """Owner decision, 28 Sep 2026. GPU: auto (~1.0 s). CPU: the UI language
    (~5.8 s) unless the user switched auto on (~11.6 s — auto runs the encoder
    twice). An unknown GPU state counts as CPU: the fixed language is the one
    that stays fast either way."""
    if gpu is True or auto_on_cpu:
        return "auto"
    return ui_lang


def language_code(name) -> "str | None":
    if not isinstance(name, str) or not name:
        return None
    return _LANG_CODES.get(name.lower(), name.lower())


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return buf.getvalue()


def inference_fields(language: str) -> dict:
    return {
        "language": language,
        "response_format": "verbose_json",
        "no_timestamps": "true",
        "temperature": "0.0",
    }


def build_multipart(wav: bytes, fields: dict) -> "tuple[bytes, str]":
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="dictation.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode()
    )
    parts.append(wav)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _free_port() -> int:
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in _RESERVED_PORTS:
            return port
    raise SttEngineFailed("no free loopback port")


# ── Windows: the child dies with the backend ─────────────────────────────────

# A module flag rather than an inline platform check so the fail-closed path
# is testable on every OS.
_TIE_WITH_JOB = sys.platform == "win32"


class _KillOnCloseJob:
    """A job object with KILL_ON_JOB_CLOSE. The OS closes our handle when the
    backend exits in ANY way (crash, TerminateProcess), and that kills the
    server. Electron's `taskkill /T` covers the normal quit; this covers the
    rest, where lifespan shutdown and atexit never run."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic),
                        ("IoInfo", _Io),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.CreateJobObjectW.restype = wintypes.HANDLE
        self._k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self._k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self._k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        extended_limit_information = 9
        if not self._k32.SetInformationJobObject(self.handle, extended_limit_information,
                                                 ctypes.byref(info), ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            self.close()
            raise OSError(err, "SetInformationJobObject failed")

    def assign(self, proc: subprocess.Popen) -> None:
        import ctypes

        if not self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


# ── The server manager ───────────────────────────────────────────────────────

def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            logger.warning("[stt] whisper-server (pid %s) did not exit after kill.", proc.pid)


class WhisperServer:
    """One whisper-server child: started on demand, restarted on demand after
    it dies or after an idle unload, stopped with the backend.

    ``command`` returns the argv prefix that launches the server; tests swap in
    ``[python, fake_server.py]`` so the lifecycle runs against a real process.
    ``has_sessions`` says whether a live dictation holds the server (default:
    this module's session registry).

    Idle unload: every use marks itself under ``self._lock`` (``lease`` for a
    request, ``touch`` for session traffic, ``ensure_started`` for a start),
    and the idle timer decides and kills under that same lock. A request
    therefore either leased first (no unload) or comes after the unload and
    spawns a fresh child in ``ensure_started``, which the lock serialises too.
    """

    def __init__(self, command=None, model=None, threads=None, startup_timeout_s=STARTUP_TIMEOUT_S,
                 idle_unload_s=IDLE_UNLOAD_S, has_sessions=None):
        self._command = command or (lambda: [server_path()])
        self._model = model or model_path
        self._threads = threads
        self._startup_timeout_s = startup_timeout_s
        self._idle_unload_s = idle_unload_s
        # 15 s for the 5-minute limit: late by at most 5 %, and no busy loop
        # for the sub-second limits tests use.
        self._idle_check_s = max(0.05, min(15.0, idle_unload_s / 20))
        self._has_sessions = has_sessions or _has_open_sessions
        self._busy = 0
        self._last_used = time.monotonic()
        self._idle_thread = None
        self._idle_wake = None
        self._lock = threading.RLock()
        self._proc = None
        self._job = None
        self._port = None
        self._prefix = None
        self._gpu = None
        self._ready = threading.Event()
        self._done = threading.Event()        # ready, exited or timed out
        self._failure = None
        self._log = collections.deque(maxlen=200)
        self._started_at = 0.0
        self._watch_gen = 0
        # No proxy, ever: this is loopback, and an HTTP(S)_PROXY in the user's
        # environment must not route dictation audio through anything.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # -- state ---------------------------------------------------------------

    @property
    def gpu(self):
        """True: a GPU backend was taken. False: "no GPU found". None: not known yet."""
        return self._gpu

    @property
    def pid(self):
        proc = self._proc
        return proc.pid if proc is not None else None

    def is_running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def is_ready(self) -> bool:
        return self._ready.is_set() and self.is_running()

    def died(self, grace_s: float = 0.5) -> bool:
        """Whether the server process has exited, waiting up to ``grace_s``.

        A crashed child's connection reset reaches the client 3-9 ms before
        ``poll()`` reports the exit on Windows (measured 5/5, 28 Sep 2026), so
        an immediate ``poll()`` right after a failed request says "running".
        """
        proc = self._proc
        if proc is None:
            return True
        try:
            proc.wait(grace_s)
            return True
        except subprocess.TimeoutExpired:
            return False

    def log_tail(self, lines: int = 20) -> str:
        return "\n".join(list(self._log)[-lines:])

    # -- lifecycle -----------------------------------------------------------

    def ensure_started(self) -> None:
        """Starts the server if it is not running. Never waits for it.

        A server still loading when its startup window ran out gets a fresh
        window instead of a kill: a cold disk or a slow CPU is not a crash, and
        without this every later wait_ready() would fail on the stale timeout.
        """
        with self._lock:
            self._last_used = time.monotonic()
            if self.is_running():
                if self._done.is_set() and not self._ready.is_set():
                    logger.info("[stt] whisper-server (pid %s) still loading; waiting again.", self._proc.pid)
                    self._failure = None
                    self._done.clear()
                    self._started_at = time.monotonic()
                    self._start_watch(self._proc)
                return
            if self._proc is not None:
                logger.warning("[stt] whisper-server exited (rc=%s); starting a new one.\n%s",
                               self._proc.returncode, self.log_tail(8))
                self._reap()
            missing = [p for p in self._required_files() if not os.path.isfile(p)]
            if missing:
                raise SttEngineMissing(missing)
            self._spawn()

    def _required_files(self):
        argv = self._command()
        # Only the real binary is checked; a test's python + script prefix is
        # validated by starting it.
        files = [self._model()]
        if len(argv) == 1:
            files.insert(0, argv[0])
        return files

    def _spawn(self) -> None:
        self._port = _free_port()
        # The server has an unauthenticated POST /load (load another model file)
        # and answers every origin with CORS "*". A random request path turns
        # every endpoint into a capability only this backend knows.
        self._prefix = "/" + secrets.token_urlsafe(18)
        self._gpu = None
        self._failure = None
        self._ready.clear()
        self._done.clear()
        self._log.clear()
        threads = self._threads or physical_cores()
        argv = self._command() + [
            "-m", self._model(),
            "--host", "127.0.0.1",
            "--port", str(self._port),
            "-t", str(threads),
            "-bs", "5", "-bo", "5", "-nlp",
            "--request-path", self._prefix,
        ]
        exe = argv[0]
        windows = sys.platform == "win32"
        self._started_at = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # Next to the DLLs; the server's default static dir is relative
                # and does not exist there, so it serves no files.
                cwd=os.path.dirname(os.path.abspath(exe)) if os.path.isabs(exe) else None,
                # The repo-wide allowlist (spawn_env): OS basics the Vulkan
                # loader needs, never the app token or provider keys.
                env=build_spawn_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if windows else 0,
                start_new_session=not windows,
            )
        except OSError as exc:
            self._failure = f"could not start whisper-server: {exc}"
            self._done.set()
            raise SttEngineFailed(self._failure) from exc
        self._proc = proc
        if _TIE_WITH_JOB:
            job = None
            try:
                job = _KillOnCloseJob()
                job.assign(proc)
                self._job = job
            except Exception as exc:                 # noqa: BLE001 — fail closed on any failure
                # An untied child outlives a crashed backend with ~1.2 GB of
                # model/VRAM, and every restart would add another one.
                if job is not None:
                    job.close()
                _terminate(proc)
                self._reap()
                self._failure = f"could not tie whisper-server to the backend's lifetime ({exc}); not started"
                logger.warning("[stt] %s", self._failure)
                self._done.set()
                raise SttEngineFailed(self._failure) from exc
        threading.Thread(target=self._read_output, args=(proc,), name="whisper-log", daemon=True).start()
        self._start_watch(proc)
        self._start_idle_timer()
        logger.info("[stt] whisper-server starting (pid %s, port %s, %s threads).", proc.pid, self._port, threads)

    def _start_watch(self, proc) -> None:
        """Called with ``self._lock`` held. The generation retires any earlier
        watcher, so only the newest one may report on the current window."""
        self._watch_gen += 1
        threading.Thread(target=self._watch_ready, args=(proc, self._watch_gen),
                         name="whisper-ready", daemon=True).start()

    def _start_idle_timer(self) -> None:
        """Called with ``self._lock`` held. At most one timer per manager; it
        ends itself once no child is left, and the next ``_spawn`` starts one."""
        if self._idle_thread is not None:
            return
        wake = threading.Event()
        self._idle_wake = wake
        self._idle_thread = threading.Thread(target=self._idle_loop, args=(wake,),
                                             name="whisper-idle", daemon=True)
        self._idle_thread.start()

    def _idle_loop(self, wake) -> None:
        me = threading.current_thread()
        while not wake.wait(self._idle_check_s):
            with self._lock:
                if self._idle_thread is not me:
                    return
                if self._unload_if_idle():
                    self._idle_thread = self._idle_wake = None
                    return

    def _unload_if_idle(self) -> bool:
        """Called with ``self._lock`` held. True once no child is left.

        A child whose startup window is still open is in use by whoever waits
        on it; one that timed out loading has nobody waiting and goes like an
        idle one.
        """
        if self._proc is None:
            return True
        if self._busy or not self._done.is_set():
            return False
        idle = time.monotonic() - self._last_used
        # Sessions last: the check purges expired ones, which is a write.
        if idle < self._idle_unload_s or self._has_sessions():
            return False
        proc = self._proc
        was_running = proc.poll() is None
        _terminate(proc)
        self._reap()
        self._failure = "whisper-server was stopped while idle"
        if was_running:
            logger.info("[stt] whisper-server idle for %.1f min; stopped to free memory (pid %s).",
                        idle / 60, proc.pid)
        else:
            logger.warning("[stt] whisper-server exited (rc=%s) while idle.\n%s", proc.returncode, self.log_tail(8))
        return True

    def _is_current_watch(self, proc, gen) -> bool:
        return self._proc is proc and self._watch_gen == gen

    def _read_output(self, proc) -> None:
        stream = proc.stdout
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                self._log.append(line)
                if self._proc is proc and self._gpu is None:
                    if _GPU_OFF_MARK in line:
                        self._gpu = False
                    elif _GPU_ON_RE.search(line):
                        self._gpu = True
        except (OSError, ValueError):
            pass

    def _watch_ready(self, proc, gen) -> None:
        deadline = self._started_at + self._startup_timeout_s
        url = f"http://127.0.0.1:{self._port}{self._prefix}/health"
        while time.monotonic() < deadline:
            if not self._is_current_watch(proc, gen):
                return
            if proc.poll() is not None:
                with self._lock:
                    if self._is_current_watch(proc, gen):
                        self._failure = f"whisper-server exited during startup (rc={proc.returncode})"
                        logger.warning("[stt] %s\n%s", self._failure, self.log_tail())
                        self._done.set()
                return
            try:
                with self._opener.open(url, timeout=1.0) as res:
                    if res.status == 200:
                        # The GPU line is printed while the model loads, i.e.
                        # before health turns 200; a server that never printed
                        # it is treated as CPU (see choose_language).
                        if self._gpu is None:
                            self._gpu = False
                        self._ready.set()
                        self._done.set()
                        logger.info("[stt] whisper-server ready in %.2f s (%s).",
                                    time.monotonic() - self._started_at, "GPU" if self._gpu else "CPU")
                        return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.1)
        with self._lock:
            if self._is_current_watch(proc, gen) and not self._ready.is_set():
                self._failure = f"whisper-server did not become ready in {self._startup_timeout_s:.0f} s"
                logger.warning("[stt] %s\n%s", self._failure, self.log_tail())
                self._done.set()

    def wait_ready(self, timeout: "float | None" = None) -> None:
        if timeout is None:
            timeout = self._startup_timeout_s
        if not self._done.wait(timeout) or not self._ready.is_set():
            raise SttEngineFailed(self._failure or "whisper-server is not ready")
        if not self.is_running():
            raise SttEngineFailed(f"whisper-server exited (rc={self._proc.returncode if self._proc else None})")

    def _reap(self) -> None:
        proc, self._proc = self._proc, None
        self._ready.clear()
        if proc is not None:
            try:
                proc.stdout and proc.stdout.close()
            except OSError:
                pass
        if self._job is not None:
            self._job.close()
            self._job = None

    def stop(self) -> None:
        with self._lock:
            if self._proc is not None:
                _terminate(self._proc)
            self._reap()
            self._done.set()
            wake, self._idle_wake, self._idle_thread = self._idle_wake, None, None
        if wake is not None:
            wake.set()

    # -- use -----------------------------------------------------------------

    @contextlib.contextmanager
    def lease(self):
        """Marks a request in flight; the idle timer never unloads under one.
        Idle time counts from the moment the last lease ends."""
        with self._lock:
            self._busy += 1
        try:
            yield self
        finally:
            with self._lock:
                self._busy -= 1
                self._last_used = time.monotonic()

    def touch(self) -> None:
        with self._lock:
            self._last_used = time.monotonic()

    # -- requests ------------------------------------------------------------

    def transcribe(self, pcm: bytes, language: str, timeout: float = FINAL_TIMEOUT_S) -> dict:
        """POSTs the audio to /inference. Returns ``{"text", "language"}``."""
        with self.lease():
            return self._post_inference(pcm, language, timeout)

    def _post_inference(self, pcm: bytes, language: str, timeout: float) -> dict:
        proc = self._proc
        if proc is None or proc.poll() is not None or not self._ready.is_set():
            raise SttEngineFailed("whisper-server is not running")
        body, ctype = build_multipart(pcm_to_wav(pcm), inference_fields(language))
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._port}{self._prefix}/inference",
            data=body, headers={"Content-Type": ctype}, method="POST",
        )
        try:
            with self._opener.open(req, timeout=timeout) as res:
                raw = res.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode("utf-8", "replace")
            raise SttEngineFailed(f"whisper-server HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            if self.died():
                raise SttEngineFailed(f"whisper-server exited (rc={proc.returncode})") from exc
            raise SttEngineFailed(f"whisper-server unreachable: {exc}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise SttEngineFailed("whisper-server answered non-JSON") from exc
        if not isinstance(data, dict) or "error" in data:
            raise SttEngineFailed(f"whisper-server error: {data.get('error') if isinstance(data, dict) else data!r}")
        text = data.get("text")
        return {
            "text": text.strip() if isinstance(text, str) else "",
            "language": data.get("language") if isinstance(data.get("language"), str) else None,
        }


_server = None
_server_lock = threading.Lock()


def get_server() -> WhisperServer:
    global _server
    with _server_lock:
        if _server is None:
            _server = WhisperServer()
        return _server


def set_server(server) -> None:
    """Replaces the process-wide server. Exists for tests."""
    global _server
    with _server_lock:
        old, _server = _server, server
    if old is not None and old is not server:
        old.stop()


def shutdown() -> None:
    with _server_lock:
        server = _server
    if server is not None:
        server.stop()


atexit.register(shutdown)


def transcribe_final(pcm: bytes, ui_lang: str, auto_on_cpu: bool) -> dict:
    """The whole recording, with the owner's language rule. One restart and
    retry if the server died under the request: the user cannot re-speak."""
    server = get_server()
    # One lease over start, wait and request, so an idle unload cannot land
    # between wait_ready() and the POST.
    with server.lease():
        attempts = 2
        for attempt in range(attempts):
            server.ensure_started()
            server.wait_ready()
            gpu = server.gpu
            language = choose_language(gpu, ui_lang, auto_on_cpu)
            try:
                result = server.transcribe(pcm, language) if pcm else {"text": "", "language": None}
            except SttEngineFailed:
                if attempt + 1 < attempts and server.died():
                    continue
                raise
            return {
                "text": result["text"],
                "language": language_code(result["language"]) if language == "auto" else language,
                "language_mode": "auto" if language == "auto" else "fixed",
                "gpu": gpu is True,
            }
    raise SttEngineFailed("unreachable")


# ── Sessions ─────────────────────────────────────────────────────────────────
#
# A session is the audio of one dictation, buffered while the user speaks. The
# renderer posts it in ~500 ms chunks so that, on a GPU, the backend can decode
# what it has so far and hand back live text.

MAX_SESSIONS = 4
SESSION_TTL_S = 90.0
# Independent of idle time: an empty chunk refreshes `last_seen`, so a caller
# sending one every 89 s could otherwise hold a slot forever (audit finding,
# 3 Sep 2026). The renderer's own ceiling is 60 s (MAX_RECORD_MS).
SESSION_MAX_LIFETIME_S = 300.0

_now = time.monotonic


class SttNoSession(Exception):
    """No live session with this id (never existed, finished, or expired)."""


class SttBusy(Exception):
    """MAX_SESSIONS dictations are already open."""


class SttTooLarge(Exception):
    """Feeding this chunk would push the session over its byte budget."""


class _Session:
    __slots__ = ("id", "lang", "pcm", "lock", "created", "last_seen",
                 "partial", "partial_at", "live_lang", "decoding")

    def __init__(self, session_id: str, lang: str):
        self.id = session_id
        self.lang = lang
        self.pcm = bytearray()
        self.lock = threading.Lock()
        now = _now()
        self.created = now
        self.last_seen = now
        self.partial = ""
        self.partial_at = None
        self.live_lang = None
        self.decoding = False


_sessions: "dict[str, _Session]" = {}
_sessions_lock = threading.Lock()


def reset_sessions() -> None:
    """Drops every live session. Exists for tests."""
    with _sessions_lock:
        _sessions.clear()


def _has_open_sessions() -> bool:
    """An open dictation holds the server even while the user is silent; an
    abandoned one stops counting once it expires."""
    purge_expired()
    with _sessions_lock:
        return bool(_sessions)


def purge_expired(now=None) -> "list[str]":
    if now is None:
        now = _now()
    dropped = []
    with _sessions_lock:
        for session_id, session in list(_sessions.items()):
            if now - session.last_seen > SESSION_TTL_S or now - session.created > SESSION_MAX_LIFETIME_S:
                del _sessions[session_id]
                dropped.append(session_id)
    return dropped


def open_session(lang: str) -> str:
    """Registers a dictation and starts the server if needed, WITHOUT waiting
    for it: the user's speech covers the model load. Missing files are
    reported here, on the call the renderer can still show an error for."""
    with _sessions_lock:
        if len(_sessions) >= MAX_SESSIONS:
            raise SttBusy()
    get_server().ensure_started()
    session_id = secrets.token_urlsafe(16)
    with _sessions_lock:
        if len(_sessions) >= MAX_SESSIONS:
            raise SttBusy()
        _sessions[session_id] = _Session(session_id, lang)
    return session_id


def _get(session_id: str) -> _Session:
    with _sessions_lock:
        session = _sessions.get(session_id)
    if session is None:
        raise SttNoSession(session_id)
    return session


def session_bytes(session_id: str) -> int:
    return len(_get(session_id).pcm)


def feed(session_id: str, pcm: bytes, cap: "int | None" = None) -> str:
    """Appends one chunk and returns the live text so far ("" without a GPU).

    The cap check and the append share one critical section: two chunks
    checked against the same stale total both passed the cap when they did
    not (audit finding, 3 Sep 2026). A refused chunk changes nothing.
    """
    session = _get(session_id)
    with session.lock:
        with _sessions_lock:
            if _sessions.get(session_id) is not session:
                raise SttNoSession(session_id)
        if cap is not None and len(session.pcm) + len(pcm) > cap:
            raise SttTooLarge()
        session.pcm.extend(pcm)
        session.last_seen = _now()
        snapshot = _live_snapshot(session)
    get_server().touch()
    if snapshot is not None:
        _decode_partial(session, snapshot)
    return session.partial


def _live_snapshot(session: _Session) -> "bytes | None":
    """The audio to decode for live text, or None when it is not time yet.
    Called with ``session.lock`` held."""
    if session.decoding or len(session.pcm) < LIVE_MIN_AUDIO_BYTES:
        return None
    if session.partial_at is not None and _now() - session.partial_at < LIVE_INTERVAL_S:
        return None
    server = get_server()
    if server.gpu is not True or not server.is_ready():
        return None
    session.decoding = True
    # Start to start, so a 0.5 s decode still refreshes about once a second.
    session.partial_at = _now()
    return bytes(session.pcm)


def _decode_partial(session: _Session, pcm: bytes) -> None:
    # A pinned language makes an update ~0.5 s instead of ~1.0 s (measured).
    # The final decode detects again on the whole recording either way.
    language = session.live_lang or "auto"
    try:
        result = get_server().transcribe(pcm, language, timeout=PARTIAL_TIMEOUT_S)
    except SttEngineFailed as exc:
        # Live text is a preview; losing one must not end the dictation.
        logger.info("[stt] live text skipped: %s", exc)
        result = None
    with session.lock:
        session.decoding = False
        if result is None:
            return
        if session.live_lang is not None:
            session.partial = result["text"]
            return
        # An early misdetection writes in another script ("コミュタッパー" for
        # Turkish at 1.5 s); such a guess is not shown, the previous one stays.
        if language_code(result["language"]) not in SUPPORTED_LANGS:
            return
        session.partial = result["text"]
        if len(pcm) >= LIVE_PIN_MIN_BYTES:
            session.live_lang = result["language"]


def finish(session_id: str, auto_on_cpu: bool = False, discard: bool = False) -> dict:
    """Removes the session and transcribes everything it heard."""
    with _sessions_lock:
        session = _sessions.pop(session_id, None)
    if session is None:
        raise SttNoSession(session_id)
    if discard:
        get_server().touch()
        return {"text": "", "duration_ms": 0}
    with session.lock:
        pcm = bytes(session.pcm)
    result = transcribe_final(pcm, session.lang, auto_on_cpu)
    result["duration_ms"] = len(pcm) // 2 * 1000 // SAMPLE_RATE
    return result
