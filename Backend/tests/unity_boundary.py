"""A real UnityMCPManager whose OS boundary is faked: start_server, stop_server, is_running,
install_package and the rest run as they ship.

Faked, and only these: process spawning and the process table (subprocess, the port-guard
queries), the loopback HTTP/socket traffic to the MCP port, and the user's home (redirected to a
temporary directory). Any other spawn or socket connect raises, and the test fails at teardown
even when product code swallowed the error.

Use the `unity_os` fixture (import it into the test module), then `unity_os.manager()` for a fresh
manager or `unity_os.adopt(manager)` for one built elsewhere.
"""

import io
import itertools
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message

import httpx
import pytest

from unity_ai_mcp import mcp_port_guard
from unity_ai_mcp import unity_mcp_manager as manager_module

MCP_PORT = 8080
# The 401 body of unity-mcp/Server/src/core/local_auth.py (the /mcp gate), as the server sends it.
_OUR_401_BODY = (b'{"success": false, "error": "Missing or invalid X-API-Key. This local server requires '
                 b'the shared secret from ~/.unity-mcp/local-api-token."}')
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


class FakeProcess:
    """A uvx child as Popen returns it. The pid is a real-looking int: a MagicMock pid turned into
    an int is 1, which once made os.killpg target init (Test_Mimarisi rule 11)."""

    def __init__(self, boundary, returncode=None, pid=None):
        self._boundary = boundary
        self.pid = next(boundary._pids) if pid is None else pid
        self.returncode = returncode
        self.argv = None
        self.env_keys = frozenset()
        self.calls = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.calls.append("terminate")
        self._boundary.events.append(("terminate", self))
        if self.returncode is None:
            self.returncode = 1

    def wait(self, timeout=None):
        self.calls.append("wait")
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.argv or "uvx", timeout)
        return self.returncode

    def kill(self):
        self.calls.append("kill")
        self.returncode = -9

    def exit(self, code):
        """The process dies on its own (crash, wrapper exit)."""
        self.returncode = code


class UnityBoundary:
    def __init__(self, monkeypatch, home):
        self.home = str(home)
        self._pids = itertools.count(4_000_001)
        self._manager = None
        self._local = threading.local()
        self.events = []         # ("spawn" | "terminate", FakeProcess) in order
        self.popen_calls = []    # argv of every launch attempt, failed ones included
        self.health_timeouts = []
        self.violations = []
        # Knobs a test turns:
        self.healthy = True              # a listening server answers /health with 200
        self.port_holder = None          # None | "ours" | "foreign": who listens when our process is not alive
        self.foreign_owners = []         # mcp_port_guard.ProcessInfo of foreign port owners
        self.unity_editor_open = True    # the process table shows a Unity Editor
        self.spawn_error = None          # an exception Popen raises
        self.spawn_delay = 0.0           # seconds Popen takes (uvx resolving its environment)
        self.stop_delay = 0.0            # seconds the port cleanup takes (taskkill / SIGTERM)
        self.instances = []              # queued /api/instances answers: (status, json) or an Exception
        self._install(monkeypatch)

    # ── wiring ───────────────────────────────────────────────────────────────

    def _install(self, monkeypatch):
        platform.uname()  # cached now: on Windows a cold uname() may run `ver` through subprocess
        monkeypatch.setenv("HOME", self.home)
        monkeypatch.setenv("USERPROFILE", self.home)
        bin_dir = os.path.join(self.home, ".local", "bin")
        os.makedirs(bin_dir)
        for name in ("uvx", "uv"):
            path = os.path.join(bin_dir, name + (".exe" if sys.platform == "win32" else ""))
            with open(path, "wb") as f:
                f.write(b"fake")
            os.chmod(path, 0o755)
        monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ.get("PATH", ""))

        monkeypatch.setattr(subprocess, "Popen", self._popen)
        monkeypatch.setattr(subprocess, "run", self._run)
        monkeypatch.setattr(os, "system", lambda *a, **k: self._refuse("os.system", a))
        real_socketpair = socket.socketpair

        def socketpair(*args, **kwargs):
            # On Windows every asyncio loop builds its self-pipe by connecting to its own listener.
            self._local.socketpair = True
            try:
                return real_socketpair(*args, **kwargs)
            finally:
                self._local.socketpair = False

        monkeypatch.setattr(socket, "socketpair", socketpair)
        monkeypatch.setattr(socket.socket, "connect", self._guard_connect(socket.socket.connect))
        monkeypatch.setattr(socket.socket, "connect_ex", self._guard_connect(socket.socket.connect_ex))
        monkeypatch.setattr(socket, "create_connection", self._create_connection)
        monkeypatch.setattr(urllib.request, "urlopen", self._urlopen)

        boundary, real_client = self, httpx.AsyncClient

        class AsyncClient(real_client):
            def __init__(self, *args, **kwargs):
                if "transport" not in kwargs:
                    kwargs["transport"] = httpx.MockTransport(boundary._http)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", AsyncClient)
        # The port guard reads the process table (lsof/netstat/ps) and kills by pid.
        monkeypatch.setattr(mcp_port_guard, "foreign_port_owner_infos", lambda port=MCP_PORT: list(self.foreign_owners))
        monkeypatch.setattr(mcp_port_guard, "collect_owned_listeners", lambda port=MCP_PORT, ancestor_pid=None: set())
        monkeypatch.setattr(mcp_port_guard, "terminate_port_listeners", self._terminate_port_listeners)

    def manager(self):
        return self.adopt(manager_module.UnityMCPManager())

    def adopt(self, manager):
        """The manager whose own process is the server on the port."""
        self._manager = manager
        return manager

    def process(self, returncode=None, pid=None):
        """A server process as a test hands it to the manager (not a launch)."""
        return FakeProcess(self, returncode, pid)

    def kinds(self):
        """The launch/terminate history in order, e.g. ["terminate", "spawn"]."""
        return [kind for kind, _ in self.events]

    def check(self):
        assert not self.violations, "outside the Unity OS boundary: " + "; ".join(self.violations)

    # ── the server on the port ───────────────────────────────────────────────

    def _serving(self):
        process = getattr(self._manager, "process", None)
        own = isinstance(process, FakeProcess) and process.poll() is None
        return own or self.port_holder == "ours"

    def _refuse(self, what, detail):
        message = f"{what} {detail!r}"
        self.violations.append(message)
        raise AssertionError("not through the Unity OS boundary: " + message)

    # ── processes ────────────────────────────────────────────────────────────

    def _popen(self, cmd, *args, **kwargs):
        argv = [str(part) for part in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)]
        if "mcp-for-unity" not in argv:
            self._refuse("subprocess.Popen", argv)
        self.popen_calls.append(argv)
        time.sleep(self.spawn_delay)
        if self.spawn_error is not None:
            raise self.spawn_error
        process = FakeProcess(self)
        process.argv = argv
        process.env_keys = frozenset(kwargs.get("env") or ())
        self.events.append(("spawn", process))
        return process

    def _inside_home(self, path):
        home = os.path.normcase(os.path.realpath(self.home))
        return os.path.normcase(os.path.realpath(path)).startswith(home + os.sep)

    def _run(self, cmd, *args, **kwargs):
        argv = [str(part) for part in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)]
        name = os.path.splitext(os.path.basename(argv[0]))[0].lower() if argv else ""
        done = lambda code, out="": subprocess.CompletedProcess(argv, code, out, "")
        if name == "tasklist" and "IMAGENAME eq Unity.exe" in argv:
            return done(0, "Unity.exe  31337 Console 1 512,000 K\n" if self.unity_editor_open
                        else "INFO: No tasks are running which match the specified criteria.\n")
        if name == "pgrep" and any("Unity.app" in part for part in argv):
            return done(0, "31337\n") if self.unity_editor_open else done(1)
        # The editor-loading scan (routes.mcp_routes) reads the process table: no editor is opening a project here.
        if (name == "powershell" and any("Win32_Process" in part for part in argv)) or (name == "ps" and "-axo" in argv):
            return done(0)
        if name == "uv" and "export" in argv and "--output-file" in argv:
            out = argv[argv.index("--output-file") + 1]
            if not self._inside_home(out):
                self._refuse("uv export outside the test home", argv)
            with open(out, "w", encoding="utf-8") as f:
                f.write("mcp==1.0.0\n")
            return done(0)
        if name == "icacls" and len(argv) > 1 and self._inside_home(argv[1]):
            return done(1)  # ACL not hardened: logged, the token is still written
        self._refuse("subprocess.run", argv)

    def _terminate_port_listeners(self, port=MCP_PORT, owned_pids=()):
        time.sleep(self.stop_delay)
        return mcp_port_guard.PortCleanupResult(port=port)

    # ── network ──────────────────────────────────────────────────────────────

    def _guard_connect(self, real):
        boundary = self

        def connect(sock, address, *args):
            if getattr(boundary._local, "socketpair", False):
                return real(sock, address, *args)
            boundary._refuse("socket connect", address)

        return connect

    def _create_connection(self, address, *args, **kwargs):
        host, port = address[0], address[1]
        if host not in _LOOPBACK or port != MCP_PORT:
            self._refuse("socket.create_connection", address)
        if not (self._serving() or self.port_holder == "foreign"):
            raise ConnectionRefusedError(10061, "connection refused")
        return _NullSocket()

    def _urlopen(self, request, *args, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        parts = urllib.parse.urlsplit(url)
        if parts.hostname not in _LOOPBACK or parts.port != MCP_PORT or parts.path != "/mcp":
            self._refuse("urllib.request.urlopen", url)
        if self._serving():
            raise urllib.error.HTTPError(url, 401, "Unauthorized", Message(), io.BytesIO(_OUR_401_BODY))
        if self.port_holder == "foreign":
            return _Response(200, b"{}")
        raise urllib.error.URLError(ConnectionRefusedError(10061, "connection refused"))

    def _http(self, request):
        url = request.url
        if url.host not in _LOOPBACK or url.port != MCP_PORT:
            self._refuse("httpx request", str(url))
        if not self._serving():
            if self.port_holder == "foreign":
                return httpx.Response(404)
            raise httpx.ConnectError("connection refused", request=request)
        if url.path == "/health":
            self.health_timeouts.append(request.extensions.get("timeout", {}).get("read"))
            return httpx.Response(200 if self.healthy else 503, json={"status": "ok"})
        if url.path == "/api/instances":
            if not self.instances:
                return httpx.Response(200, json={"instances": []})
            item = self.instances.pop(0)
            if isinstance(item, Exception):
                raise item
            status, data = item
            return httpx.Response(status, json=data)
        self._refuse("httpx request", str(url))


class _NullSocket:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class _Response(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture
def unity_os(monkeypatch, tmp_path_factory):
    """The Unity OS boundary for one test; its home is a fresh temporary directory."""
    boundary = UnityBoundary(monkeypatch, tmp_path_factory.mktemp("unity-home"))
    yield boundary
    boundary.check()
