"""Starts the real backend as an isolated child process; the tests only see its HTTP surface.

Isolation (Backend/README.md "Isolated dev backend"): every home, data and temp dir points
into one fresh temp root, GAMACHINE_NO_UNITY_MCP=1 keeps it off the Unity MCP port, PATH
holds only fake provider CLIs, the interpreter's dir and the OS dir, and any outbound
proxy-aware request that is not loopback goes to a dead local proxy instead of the internet.
Nothing of Backend/app is put on sys.path: the backend is a program here, not a library.
"""
import base64
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

import sse
from client import Api
from fakes.fake_openai import FakeOpenAI

REPO = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden"
WINDOWS = os.name == "nt"
READY_DEADLINE_S = 30.0
STOP_WAIT_S = 15.0
SESSION_CAP_S = 300.0
START_ATTEMPTS = 3
# Ports this suite must never take: the dev backend, the live Unity MCP server, the relay.
RESERVED_PORTS = {8000, 8080, 8799}
FAKE_CLIS = ("claude", "codex", "opencode", "agy", "gemini", "cursor-agent", "kimi", "copilot")
# A dead local proxy: a stray request to a real host fails at once and is visible in the log.
DEAD_PROXY = "http://127.0.0.1:9"
PORT_8080_CONTACT = re.compile(r"(localhost|127\.0\.0\.1):8080\b")
TOKEN = "contract-suite-token"
DEFAULT_MODEL = "contract-text"

_children = []


def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true", default=False,
                     help="rewrite contract/golden/*.golden from this run instead of comparing")


def pytest_sessionstart(session):
    def _cap():
        sys.stderr.write(f"\ncontract: session passed {SESSION_CAP_S:.0f} s, killing the backend\n")
        for child in list(_children):
            child.kill()

    timer = threading.Timer(SESSION_CAP_S, _cap)
    timer.daemon = True
    timer.start()
    session.config._contract_cap = timer


def pytest_sessionfinish(session, exitstatus):
    timer = getattr(session.config, "_contract_cap", None)
    if timer is not None:
        timer.cancel()


def free_port():
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in RESERVED_PORTS:
            return port
    raise RuntimeError("no free port outside the reserved ones")


def backend_command():
    """CONTRACT_BACKEND_CMD (a JSON list or a command line), else the repo's backend from source."""
    raw = os.environ.get("CONTRACT_BACKEND_CMD", "").strip()
    if raw:
        if raw.startswith("["):
            return [str(part) for part in json.loads(raw)]
        return [part.strip('"') for part in shlex.split(raw, posix=not WINDOWS)]
    venv = REPO / "Backend" / "venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    python = str(venv) if venv.is_file() else sys.executable
    return [python, str(REPO / "Backend" / "app" / "main.py")]


def _system_dir():
    if WINDOWS:
        return str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32")
    return "/usr/bin:/bin"


def _make_fakebin(root):
    fakebin = root / "fakebin"
    fakebin.mkdir()
    stub = HERE / "fakes" / ("cli_stub.cmd" if WINDOWS else "cli_stub.sh")
    for name in FAKE_CLIS:
        target = fakebin / (name + (".cmd" if WINDOWS else ""))
        shutil.copyfile(stub, target)
        if not WINDOWS:
            target.chmod(0o755)
    return fakebin


class Backend:
    """One backend child on a temp root; `restart()` keeps the root, so its data must survive."""

    def __init__(self, root, fake_url):
        self.root = root
        self.home = root / "home"
        self.db_path = root / "data" / "gamachine.db"
        self.log_path = root / "backend.log"
        self.fakebin = _make_fakebin(root)
        self.command = backend_command()
        self.fake_url = fake_url
        # One key per session, the same across a restart, so saved secrets stay readable;
        # set here so the backend never falls back to the OS keyring.
        self.fernet_key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
        self.token = TOKEN
        self.proc = None
        self.port = None
        self._lock = threading.Lock()
        for sub in ("AppData/Roaming", "AppData/Local", ".claude", ".kimi", ".codex", "tmp"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def env(self):
        keep = (("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE", "OS",
                 "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "USERNAME")
                if WINDOWS else ("USER", "LOGNAME"))
        env = {key: os.environ[key] for key in keep if key in os.environ}
        home, tmp = str(self.home), str(self.home / "tmp")
        interpreter_dir = os.path.dirname(self.command[0]) if os.path.isabs(self.command[0]) else ""
        env.update({
            "PATH": os.pathsep.join(p for p in (str(self.fakebin), interpreter_dir, _system_dir()) if p),
            "USERPROFILE": home, "HOME": home,
            "APPDATA": str(self.home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(self.home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "CLAUDE_CONFIG_DIR": str(self.home / ".claude"), "KIMI_CODE_HOME": str(self.home / ".kimi"),
            "CODEX_HOME": str(self.home / ".codex"),
            "TMP": tmp, "TEMP": tmp, "TMPDIR": tmp,
            "DB_PATH": str(self.db_path),
            "HOST": "127.0.0.1", "PORT": str(self.port),
            "LOCAL_APP_TOKEN": self.token,
            "GAMACHINE_NO_UNITY_MCP": "1",
            "OPENAI_BASE_URL": self.fake_url,
            "API_KEY_ENCRYPTION_KEY": self.fernet_key,
            "HTTP_PROXY": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY, "ALL_PROXY": DEAD_PROXY,
            "NO_PROXY": "127.0.0.1,localhost",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
        })
        if not WINDOWS:
            env.update({"http_proxy": DEAD_PROXY, "https_proxy": DEAD_PROXY, "all_proxy": DEAD_PROXY,
                        "no_proxy": "127.0.0.1,localhost", "LANG": "C.UTF-8"})
        return env

    def start(self):
        failures = []
        for _ in range(START_ATTEMPTS):
            self.port = free_port()
            problem = self._start_once()
            if problem is None:
                return
            failures.append(problem)
            self.kill()
        pytest.fail("backend did not start:\n" + "\n".join(failures) + "\n--- log tail ---\n" + self.log_tail())

    def _start_once(self):
        with self._lock:
            log = open(self.log_path, "ab")
            try:
                self.proc = subprocess.Popen(
                    self.command, cwd=str(self.root), env=self.env(), stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0,
                    start_new_session=not WINDOWS)
            finally:
                log.close()
            _children.append(self)
        deadline = time.monotonic() + READY_DEADLINE_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return f"port {self.port}: child exited with {self.proc.returncode}"
            try:
                answer = httpx.get(self.url + "/health", timeout=1.0, trust_env=False)
                if answer.status_code == 200 and answer.json().get("service") == "gamachine":
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            return f"port {self.port}: /health not ready within {READY_DEADLINE_S:.0f} s"
        if not self.db_path.is_file():
            pytest.fail(f"backend is up but its DB_PATH file {self.db_path} does not exist")
        return None

    def kill(self):
        """Kill the child's whole process tree, by the child's own pid only, with a bounded wait."""
        with self._lock:
            proc, self.proc = self.proc, None
            if self in _children:
                _children.remove(self)
        if proc is None or proc.poll() is not None:
            return
        try:
            if WINDOWS:
                subprocess.run([str(Path(_system_dir()) / "taskkill.exe"), "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True, timeout=STOP_WAIT_S)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
        try:
            proc.wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=STOP_WAIT_S)

    def restart(self):
        self.kill()
        self.start()

    def log_text(self):
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def log_tail(self, lines=60):
        return "\n".join(self.log_text().splitlines()[-lines:])

    def boundary_violations(self):
        """Things the suite promises never happen: a provider CLI start or a contact with 8080."""
        found = []
        spawned = self.fakebin / "spawned.log"
        if spawned.exists():
            found.append("provider CLI started: " + spawned.read_text(encoding="utf-8", errors="replace").strip())
        found += [f"8080 contact in log: {line.strip()}" for line in self.log_text().splitlines()
                  if PORT_8080_CONTACT.search(line)]
        return found


@pytest.fixture(scope="session")
def fake_openai():
    fake = FakeOpenAI().start()
    yield fake
    fake.stop()


@pytest.fixture(scope="session")
def backend(fake_openai):
    root = Path(tempfile.mkdtemp(prefix="gamachine-contract-"))
    server = Backend(root, fake_openai.base_url)
    try:
        server.start()
        api = Api(server.url, server.token)
        try:
            # Titles would be asked of a second model; the default model must be the fake.
            api.ok("POST", "/chat-title-setting", json={"enabled": False})
            api.ok("POST", "/save-ai-config", json={"user_id": 1, "provider_type": "openai",
                                                     "model_name": DEFAULT_MODEL,
                                                     "api_key": "sk-contract-fake-key"})
        finally:
            api.close()
        yield server
    finally:
        server.kill()
        violations = server.boundary_violations()
        tail = server.log_tail()
        if os.environ.get("CONTRACT_KEEP_ROOT") == "1":
            sys.stderr.write(f"\ncontract: temp root kept at {root}\n")
        else:
            shutil.rmtree(root, ignore_errors=True)
    assert not violations, "\n".join(violations) + "\n--- log tail ---\n" + tail


@pytest.fixture
def api(backend):
    client = Api(backend.url, backend.token)
    yield client
    client.close()


@pytest.fixture
def golden(request):
    """`golden(name, events)`: compare normalized events with contract/golden/<name>.sse.golden."""
    update = request.config.getoption("--update-golden")

    def check(name, events):
        path = GOLDEN / f"{name}.sse.golden"
        actual = sse.dumps(sse.normalize(events))
        if update:
            path.write_text(actual, encoding="utf-8", newline="\n")
            return
        assert path.is_file(), f"{path.name} missing; run `python -m pytest contract --update-golden` and review it"
        assert actual == path.read_text(encoding="utf-8"), (
            f"stream differs from {path.name}; if the change is intended, rerun with --update-golden "
            f"and review the diff\n--- actual ---\n{actual}")

    return check
