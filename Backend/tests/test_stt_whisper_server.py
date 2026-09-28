"""`providers.stt_whisper` — pure helpers and the `WhisperServer` process manager.

WHAT IS PINNED HERE
    The pure functions (path resolution, language selection, the WAV/multipart
    wire format, core counting) in isolation; and the manager's lifecycle
    against a REAL child process: start, health-poll to ready, GPU detection
    from the startup log, request/response wiring, a crash recovering into a
    fresh process, startup failure reporting the exit code, missing-file
    detection, and that `stop()` leaves nothing running.

WHY A REAL CHILD PROCESS
    The manager's job IS process lifecycle (Popen, a background health poll,
    stdout GPU-line sniffing, terminate-then-kill). A mock object cannot
    exercise any of that; only a real process can. `fake_whisper_server.py`
    stands in for whisper-server.exe: same argv contract, same HTTP surface,
    none of the ~874 MB model or the GPU it would need.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from providers import stt_whisper

FAKE = str(Path(__file__).with_name("fake_whisper_server.py"))


def _model_file(tmp_path, name="model.bin"):
    path = tmp_path / name
    path.write_bytes(b"fake-model-bytes")
    return str(path)


def _wait_until(predicate, timeout=2.0, interval=0.01):
    """Polls `predicate` until it is true or `timeout` elapses, returning the
    final result. `proc.poll()`/`is_running()` reflect an OS-level process
    death with a small, measured lag after the socket-level failure a caller
    sees first (see the note on test_crash_on_inference_is_recovered_below) —
    this is that lag's tolerance, not a cover for a hang: a predicate that
    never turns true still fails, just after `timeout` instead of instantly.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def server_factory(tmp_path):
    """Builds a `WhisperServer` wired to the fake script and guarantees
    `stop()` runs even if the test raises."""
    created = []

    def make(fake_flags=(), model_path=None, command=None, threads=2, startup_timeout_s=10):
        resolved_model = model_path or _model_file(tmp_path)
        if command is None:
            argv_prefix = [sys.executable, FAKE, *fake_flags]

            def command():
                return list(argv_prefix)

        server = stt_whisper.WhisperServer(
            command=command,
            model=lambda: resolved_model,
            threads=threads,
            startup_timeout_s=startup_timeout_s,
        )
        created.append(server)
        return server

    yield make
    for server in created:
        server.stop()


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_engine_root_env_override_wins_over_everything_else(tmp_path, monkeypatch):
    """An override, not a fallback: a Docker mount or a test must not be
    silently overtaken by a tree next to the executable."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("GAMACHINE_WHISPER_DIR", str(tmp_path / "chosen"))
    assert stt_whisper.engine_root() == str(tmp_path / "chosen")


def test_engine_root_frozen_layout_resolves_next_to_the_resources_directory(tmp_path, monkeypatch):
    """backend.exe sits at <app>/resources/Backend/; the whisper tree is
    packaged as a sibling at <app>/resources/whisper/ — hence the `..` hop."""
    monkeypatch.delenv("GAMACHINE_WHISPER_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe = tmp_path / "resources" / "Backend" / "backend.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    assert os.path.normpath(stt_whisper.engine_root()) == os.path.normpath(str(tmp_path / "resources" / "whisper"))


def test_engine_root_dev_layout_resolves_into_the_repo_vendor_tree(monkeypatch):
    monkeypatch.delenv("GAMACHINE_WHISPER_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    root = stt_whisper.engine_root()
    assert root.replace("\\", "/").endswith("Backend/vendor/whisper")
    assert os.path.isabs(root)


@pytest.mark.parametrize(
    "gpu, ui_lang, auto_on_cpu, expected",
    [
        (True, "tr", False, "auto"),
        (True, "en", False, "auto"),
        (True, "tr", True, "auto"),      # GPU wins regardless of the CPU setting
        (False, "tr", False, "tr"),
        (False, "en", False, "en"),
        (False, "tr", True, "auto"),     # the CPU opt-in
        (None, "en", False, "en"),       # unknown GPU state counts as CPU
    ],
)
def test_choose_language_table(gpu, ui_lang, auto_on_cpu, expected):
    assert stt_whisper.choose_language(gpu, ui_lang, auto_on_cpu) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("turkish", "tr"),
        ("TURKISH", "tr"),
        ("english", "en"),
        ("klingon", "klingon"),   # unmapped: passed through lower-cased
        (None, None),
        ("", None),
        (123, None),
    ],
)
def test_language_code_mapping(name, expected):
    assert stt_whisper.language_code(name) == expected


def test_pcm_to_wav_round_trip():
    import wave
    import io

    pcm = bytes((i * 37) % 256 for i in range(4000))
    wav_bytes = stt_whisper.pcm_to_wav(pcm)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == stt_whisper.SAMPLE_RATE
        assert wav.readframes(wav.getnframes()) == pcm


def test_physical_cores_returns_a_positive_int():
    cores = stt_whisper.physical_cores()
    assert isinstance(cores, int)
    assert cores >= 1


def test_inference_fields_has_the_exact_contract():
    assert stt_whisper.inference_fields("auto") == {
        "language": "auto",
        "response_format": "verbose_json",
        "no_timestamps": "true",
        "temperature": "0.0",
    }


def test_build_multipart_contains_every_field_and_the_wav_bytes_verbatim():
    wav = stt_whisper.pcm_to_wav(b"\x01\x00" * 50)
    fields = stt_whisper.inference_fields("tr")
    body, ctype = stt_whisper.build_multipart(wav, fields)

    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=", 1)[1]
    marker = ("--" + boundary).encode()
    # 4 text fields + 1 file part opened, plus the closing "--boundary--".
    assert body.count(marker) == 5 + 1
    for name, value in fields.items():
        assert f'name="{name}"'.encode() in body
        assert value.encode() in body
    assert b'name="file"; filename="dictation.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert body.endswith(wav + b"\r\n--" + boundary.encode() + b"--\r\n")


# ── Manager lifecycle (real child process) ───────────────────────────────────

@pytest.mark.parametrize(
    "flag, expected_gpu",
    [("on", True), ("off", False), ("silent", False)],
    ids=["gpu-on", "gpu-off", "gpu-line-silent"],
)
def test_gpu_is_detected_from_the_startup_log(server_factory, flag, expected_gpu):
    # A non-zero startup delay gives the background log reader time to have
    # consumed the GPU line before the first /health poll turns ready,
    # avoiding a race between the two background threads.
    server = server_factory(fake_flags=["--fake-gpu", flag, "--fake-startup-delay", "0.2"])
    server.ensure_started()
    server.wait_ready()
    assert server.is_ready()
    assert server.gpu is expected_gpu


def test_transcribe_round_trips_the_language_and_the_wav_length(server_factory):
    server = server_factory(fake_flags=["--fake-startup-delay", "0.1"])
    server.ensure_started()
    server.wait_ready()

    pcm = b"\x01\x02" * 100
    wav_len = len(stt_whisper.pcm_to_wav(pcm))
    result = server.transcribe(pcm, "tr", timeout=5)

    assert result["language"] == "turkish"
    # The fake echoes a leading space; this proves transcribe() strips it
    # rather than passing the server's JSON through untouched.
    assert result["text"] == f"hello turkish {wav_len}"


def test_paths_outside_the_request_prefix_are_404(server_factory):
    server = server_factory(fake_flags=["--fake-startup-delay", "0.1"])
    server.ensure_started()
    server.wait_ready()

    try:
        urllib.request.urlopen(f"http://127.0.0.1:{server._port}/health", timeout=5)
        pytest.fail("expected the prefix-less path to be refused")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


def test_stop_leaves_no_process(server_factory):
    server = server_factory(fake_flags=["--fake-startup-delay", "0.1"])
    server.ensure_started()
    server.wait_ready()
    proc = server._proc
    pid = proc.pid

    server.stop()

    assert proc.poll() is not None
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        assert str(pid) not in out.stdout


def test_crash_on_inference_is_recovered_by_starting_a_new_process(tmp_path):
    """Measured on this machine: `proc.poll()` still reports the crashed
    child as running for a few ms right after the client already saw the
    connection reset (a ConnectionResetError from the OS reaches the socket
    layer slightly ahead of the process object being signaled exited) — 5/5
    trials of a minimal repro showed `is_running()` still True at zero delay,
    False 3-9 ms later. `_wait_until` tolerates exactly that measured lag; it
    does not paper over a hang, since the loop still fails after `timeout`.
    """
    model_path = _model_file(tmp_path)
    calls = {"n": 0}

    def command():
        calls["n"] += 1
        # ensure_started() asks the command for its argv TWICE per attempt —
        # once in _required_files() (just to measure len(argv)), once again
        # in _spawn() — so one "attempt" spans two calls here. Dividing by 2
        # keeps both calls of the same attempt consistent with each other.
        attempt = (calls["n"] - 1) // 2
        flags = ["--fake-startup-delay", "0.1"]
        if attempt == 0:
            flags.append("--fake-crash-on-inference")
        return [sys.executable, FAKE, *flags]

    server = stt_whisper.WhisperServer(command=command, model=lambda: model_path, threads=2, startup_timeout_s=10)
    try:
        server.ensure_started()
        server.wait_ready()
        first_pid = server.pid

        with pytest.raises(stt_whisper.SttEngineFailed):
            server.transcribe(b"\x01\x02" * 100, "tr", timeout=5)
        assert _wait_until(lambda: server.is_running() is False), "the crashed process was never reaped"

        server.ensure_started()
        server.wait_ready()
        assert server.pid != first_pid

        result = server.transcribe(b"\x01\x02" * 100, "tr", timeout=5)
        assert "hello" in result["text"]
    finally:
        server.stop()


def test_transcribe_final_retries_once_after_a_crash_then_succeeds(tmp_path):
    """The module-level convenience function on top of the manager: one dead
    server must not cost the user a re-spoken dictation."""
    model_path = _model_file(tmp_path)
    calls = {"n": 0}

    def command():
        calls["n"] += 1
        attempt = (calls["n"] - 1) // 2   # see the comment in the test above
        flags = ["--fake-startup-delay", "0.1"]
        if attempt == 0:
            flags.append("--fake-crash-on-inference")
        return [sys.executable, FAKE, *flags]

    server = stt_whisper.WhisperServer(command=command, model=lambda: model_path, threads=2, startup_timeout_s=10)
    stt_whisper.set_server(server)
    try:
        result = stt_whisper.transcribe_final(b"\x01\x02" * 100, "tr", False)
        assert calls["n"] == 4, "expected exactly two spawn attempts (2 command() calls each)"
        assert "hello" in result["text"]
    finally:
        stt_whisper.set_server(None)   # also stops the server (see set_server's docstring)


def test_exit_at_start_makes_wait_ready_report_the_return_code(server_factory):
    server = server_factory(fake_flags=["--fake-exit-at-start", "3"])
    server.ensure_started()
    with pytest.raises(stt_whisper.SttEngineFailed, match="rc=3"):
        server.wait_ready()


class _FakeJob:
    closed = 0

    def __init__(self, fail_at=None):
        self._fail_at = fail_at
        if fail_at == "create":
            raise OSError(5, "CreateJobObjectW failed")

    def assign(self, proc):
        if self._fail_at == "assign":
            raise OSError(5, "AssignProcessToJobObject failed")

    def close(self):
        type(self).closed += 1


@pytest.mark.parametrize("fail_at", ["create", "assign"])
def test_a_failed_lifetime_job_kills_the_child_and_fails_closed(server_factory, monkeypatch, fail_at):
    """An untied child would outlive a crashed backend holding the model, so a
    job that cannot be created or assigned must not leave a server behind.
    The job branch is forced on so this runs on every OS."""
    spawned = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    class Job(_FakeJob):
        closed = 0

        def __init__(self):
            super().__init__(fail_at)

    monkeypatch.setattr(stt_whisper.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(stt_whisper, "_TIE_WITH_JOB", True)
    monkeypatch.setattr(stt_whisper, "_KillOnCloseJob", Job)
    server = server_factory(fake_flags=["--fake-startup-delay", "0.1"])
    try:
        with pytest.raises(stt_whisper.SttEngineFailed, match="lifetime"):
            server.ensure_started()
        with pytest.raises(stt_whisper.SttEngineFailed, match="lifetime"):
            server.wait_ready(timeout=0.1)

        assert len(spawned) == 1
        orphan = spawned[0]
        assert orphan.poll() is not None, "the untied child is still running"
        assert server.pid is None
        assert Job.closed == (1 if fail_at == "assign" else 0)

        # The failure is not sticky: a job that works lets the next call start.
        monkeypatch.setattr(stt_whisper, "_KillOnCloseJob", lambda: _FakeJob())
        server.ensure_started()
        server.wait_ready()
        assert server.is_ready()
    finally:
        server.stop()


def test_missing_model_file_raises_stt_engine_missing(server_factory, tmp_path):
    missing_model = str(tmp_path / "does-not-exist.bin")
    server = server_factory(model_path=missing_model)
    with pytest.raises(stt_whisper.SttEngineMissing):
        server.ensure_started()


def test_default_command_with_a_missing_engine_dir_lists_both_paths(tmp_path, monkeypatch):
    """No `command`/`model` override: the module's own `server_path()`/
    `model_path()` resolvers are exercised, both pointed at an empty dir."""
    empty_dir = tmp_path / "empty-engine"
    empty_dir.mkdir()
    monkeypatch.setenv("GAMACHINE_WHISPER_DIR", str(empty_dir))
    server = stt_whisper.WhisperServer()
    try:
        with pytest.raises(stt_whisper.SttEngineMissing) as exc_info:
            server.ensure_started()
        missing = exc_info.value.missing
        assert stt_whisper.server_path() in missing
        assert stt_whisper.model_path() in missing
        assert len(missing) == 2
    finally:
        server.stop()


def test_child_env_excludes_backend_secrets(server_factory, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "super-secret-token")
    server = server_factory(fake_flags=["--fake-startup-delay", "0.1"])
    server.ensure_started()
    server.wait_ready()

    with urllib.request.urlopen(f"http://127.0.0.1:{server._port}{server._prefix}/env", timeout=5) as res:
        data = json.loads(res.read())

    assert "LOCAL_APP_TOKEN" not in data["keys"]
