"""Starts the REAL server entry point on a free port and probes it over HTTP.

This runs as a CHILD INTERPRETER, launched by test_live_server_startup.py. It is
not a test module (the name does not match `python_files = test_*.py`).

WHY IT EXISTS
    The P1 spike (25 Sep 2026) moved the server to FastMCP 4 and it crashed at
    startup on a removed private import. The suite was green anyway: nothing in
    it ever started the server. This probe runs `main.py` exactly as the product
    does (`--transport http --project-scoped-tools`, secret in the environment)
    and talks to it over a real socket.

WHY A CHILD, AND WHY main.py IN A GRANDCHILD
    tests/integration/conftest.py installs stub `fastmcp`/`mcp` modules for the
    whole session (see _authz_matrix_probe.py for the measurement), so the test
    process cannot talk MCP with the real SDK. This child has no stubs. The
    server itself runs in its own process so a crash at import or in the
    lifespan shows up as an exit code, the way it would in production.

WHAT IT EMITS
    One JSON object on stdout; see `run()`.

PORTABILITY
    Must work on Windows and macOS: interpreter is sys.executable, paths come
    from pathlib, the server is stopped with Popen.terminate() (TerminateProcess
    on Windows, SIGTERM on POSIX), and nothing assumes a shell.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx

SECRET = "live-probe-secret-7f3a"
OLD_PROTOCOL = "2025-06-18"
STARTUP_TIMEOUT_S = 60.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _parse_rpc_body(response: httpx.Response) -> dict | None:
    """A JSON-RPC reply arrives either as JSON or as one SSE `data:` line."""
    if not response.content:
        return None
    ctype = response.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload:
                    return json.loads(payload)
        return None
    return response.json()


class OldEraSession:
    """initialize -> notifications/initialized, then requests, as an mcp 1.x client does."""

    def __init__(self, base: str, path: str, key: str | None):
        self.url = base + path
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if key is not None:
            self.headers["X-API-Key"] = key
        self.http = httpx.Client(timeout=30.0)
        self.next_id = 1
        self.status: int | None = None
        self.protocol: str | None = None

    def __enter__(self) -> "OldEraSession":
        init = self.http.post(self.url, headers=self.headers, json={
            "jsonrpc": "2.0", "id": self._id(), "method": "initialize",
            "params": {
                "protocolVersion": OLD_PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "live-probe", "version": "0"},
            },
        })
        self.status = init.status_code
        if init.status_code != 200:
            return self
        body = _parse_rpc_body(init) or {}
        session_id = init.headers.get("mcp-session-id")
        if session_id:
            self.headers["mcp-session-id"] = session_id
        self.protocol = (body.get("result") or {}).get("protocolVersion")
        if self.protocol:
            self.headers["mcp-protocol-version"] = self.protocol
        self.http.post(self.url, headers=self.headers,
                       json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *exc) -> None:
        self.http.close()

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def request(self, method: str, params: dict | None = None) -> tuple[int, dict]:
        payload = {"jsonrpc": "2.0", "id": self._id(), "method": method}
        if params is not None:
            payload["params"] = params
        response = self.http.post(self.url, headers=self.headers, json=payload)
        return response.status_code, (_parse_rpc_body(response) or {})

    def call_tool(self, name: str, arguments: dict) -> dict:
        status, body = self.request("tools/call", {"name": name, "arguments": arguments})
        result = body.get("result") or {}
        text = " ".join(
            block.get("text", "") for block in (result.get("content") or [])
            if isinstance(block, dict))
        return {"status": status, "is_error": result.get("isError"), "text": text,
                "structured": result.get("structuredContent"), "error": body.get("error")}


class ListChangedListener:
    """Holds an old-protocol session's standalone SSE stream (GET) open and
    records the JSON-RPC methods that arrive on it."""

    def __init__(self, base: str):
        self.session = OldEraSession(base, "/mcp", SECRET)
        self.methods: list[str] = []
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._listen, daemon=True)

    def start(self) -> None:
        self.session.__enter__()
        self.thread.start()
        self.ready.wait(timeout=10)

    def close(self) -> None:
        self.session.__exit__(None, None, None)

    def _listen(self) -> None:
        headers = dict(self.session.headers)
        headers["Accept"] = "text/event-stream"
        try:
            with httpx.stream("GET", self.session.url, headers=headers,
                              timeout=httpx.Timeout(5.0, read=None)) as response:
                self.ready.set()
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        try:
                            self.methods.append(json.loads(line[5:].strip()).get("method"))
                        except ValueError:
                            pass
        except httpx.HTTPError:
            pass
        finally:
            self.ready.set()


def old_era_list_tools(base: str, path: str, key: str | None) -> dict:
    with OldEraSession(base, path, key) as session:
        if session.status != 200:
            return {"status": session.status}
        status, body = session.request("tools/list")
        result = body.get("result") or {}
        tools = result.get("tools") or []
        return {
            "status": status,
            "protocol": session.protocol,
            "names": [t.get("name") for t in tools],
            "groups": {
                t.get("name"): sorted(
                    tag.split(":", 1)[1]
                    for tag in (((t.get("_meta") or {}).get("fastmcp") or {}).get("tags") or [])
                    if isinstance(tag, str) and tag.startswith("group:"))
                for t in tools
            },
            "result_extra": {k: v for k, v in result.items() if k != "tools"},
            "error": body.get("error"),
        }


def old_era_calls(base: str) -> dict:
    """Tool calls whose answer does not need a Unity Editor."""
    out = {}
    with OldEraSession(base, "/mcp", SECRET) as session:
        out["mcp_activate"] = session.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
    with OldEraSession(base, "/mcp/full", SECRET) as session:
        out["full_list_groups"] = session.call_tool("manage_tools", {"action": "list_groups"})
    with OldEraSession(base, "/mcp/gamachine", SECRET) as session:
        out["gamachine_manage_tools"] = session.call_tool("manage_tools", {"action": "list_groups"})
    return out


class FakeEditors:
    """Two fake Unity Editors on /hub/plugin, answering commands with canned data.

    Runs its own event loop on a thread so the probe itself can stay synchronous.
    Records which editor received each command, which is how routing is observed.
    """

    EDITORS = (("ProbeA", "aaaa1111"), ("ProbeB", "bbbb2222"))
    TOOLS = ["read_console", "manage_scene", "manage_gameobject", "manage_editor",
             "play_step", "play_session", "play_capture", "game_hooks", "run_playtest"]

    def __init__(self, port: int):
        self.url = f"ws://127.0.0.1:{port}/hub/plugin"
        self.received: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=10)

    def commands(self) -> list[tuple[str, str]]:
        with self.lock:
            return list(self.received)

    def _run(self) -> None:
        import asyncio
        asyncio.run(self._main())

    async def _main(self) -> None:
        import asyncio
        await asyncio.gather(*(self._editor(name, h) for name, h in self.EDITORS))

    async def _editor(self, project: str, project_hash: str) -> None:
        import asyncio
        from websockets.asyncio.client import connect

        async with connect(self.url, additional_headers={"X-API-Key": SECRET}) as ws:
            await ws.recv()  # welcome
            await ws.send(json.dumps({"type": "register", "project_name": project,
                                      "project_hash": project_hash,
                                      "unity_version": "6000.0.0f1"}))
            registered = json.loads(await ws.recv())
            session_id = registered.get("session_id")
            await ws.send(json.dumps({"type": "register_tools", "tools": [
                {"name": n, "description": f"fake {n}"} for n in self.TOOLS]}))
            while not self.stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong", "session_id": session_id}))
                elif msg.get("type") == "execute":
                    if msg.get("name") == "ping":
                        # PluginHub's readiness check before fast-fail commands.
                        result = {"status": "success", "result": {"message": "pong"}}
                    else:
                        with self.lock:
                            self.received.append((project, msg.get("name")))
                        result = {"success": True, "message": f"{project} answered",
                                  "data": []}
                    await ws.send(json.dumps({"type": "command_result", "id": msg["id"],
                                              "result": result}))


def wait_for_editors(base: str, count: int) -> bool:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            body = httpx.get(base + "/api/instances", headers={"X-API-Key": SECRET},
                             timeout=2.0).json()
            if len(body.get("instances") or []) >= count:
                return True
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.25)
    return False


def routing_scenario(base: str, editors: FakeEditors) -> dict:
    """Which editor each call reaches, per routing input. See test_live_server_startup."""
    out: dict = {}

    def routed(label: str, path: str, arguments: dict, headers: dict | None = None,
               tool: str = "read_console") -> None:
        before = len(editors.commands())
        with OldEraSession(base, path, SECRET) as session:
            session.headers.update(headers or {})
            call = session.call_tool(tool, arguments)
        time.sleep(0.2)
        call["reached"] = [p for p, _ in editors.commands()[before:]]
        out[label] = call

    routed("query_param", "/mcp?instance=aaaa1111", {"action": "get", "count": 1})
    routed("header", "/mcp", {"action": "get", "count": 1},
           headers={"X-Unity-Instance": "ProbeB@bbbb2222"})
    routed("argument", "/mcp", {"action": "get", "count": 1, "unity_instance": "aaaa"})
    routed("argument_beats_query", "/mcp?instance=aaaa1111",
           {"action": "get", "count": 1, "unity_instance": "ProbeB@bbbb2222"})
    routed("profile_path_keeps_query", "/mcp/gamachine?instance=bbbb2222",
           {"action": "get", "count": 1})
    routed("set_active_instance", "/mcp", {"instance": "ProbeA@aaaa1111"},
           tool="set_active_instance")
    # A different client, after someone else "set" an instance: two editors are
    # connected and this call names none, so it must be refused, not routed.
    routed("unrouted_other_client", "/mcp", {"action": "get", "count": 1})
    routed("unknown_default", "/mcp?instance=ffff0000", {"action": "get", "count": 1})
    return out


def _new_era_sdk():
    """The mcp>=2 client, or the reason it is not available (mcp 1.x installed)."""
    try:
        import importlib.metadata
        if int(importlib.metadata.version("mcp").split(".")[0]) < 2:
            return None, "mcp < 2: no 2026-07-28 client"
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        return None, f"import failed: {exc}"
    return (httpx2, Client, streamable_http_client), None


def new_era(base: str, editors: "FakeEditors | None" = None) -> dict:
    """The same checks over the 2026-07-28 protocol (server/discover, no initialize),
    the one Claude Code 2.1.282 negotiates (P1 spike)."""
    sdk, why_not = _new_era_sdk()
    if sdk is None:
        return {"skipped": why_not}
    httpx2, Client, streamable_http_client = sdk
    import asyncio

    def groups_of(tool) -> list:
        meta = tool.model_dump(by_alias=True).get("_meta") or {}
        tags = (meta.get("fastmcp") or {}).get("tags") or []
        return sorted(t.split(":", 1)[1] for t in tags if isinstance(t, str) and t.startswith("group:"))

    async def session(path: str, key: str | None, work):
        headers = {"X-API-Key": key} if key is not None else {}
        async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=60)) as http:
            async with Client(streamable_http_client(base + path, http_client=http),
                              mode="auto") as client:
                return await work(client)

    async def listing(client):
        raw = await client.session.list_tools()
        dumped = raw.model_dump(by_alias=True)
        return {
            "protocol": client.protocol_version,
            "names": [t.name for t in raw.tools],
            "groups": {t.name: groups_of(t) for t in raw.tools},
            "result_extra": {k: v for k, v in dumped.items() if k != "tools" and v is not None},
        }

    def call(name, arguments):
        async def work(client):
            result = await client.call_tool(name, arguments)
            text = " ".join(getattr(b, "text", "") for b in result.content)
            return {"protocol": client.protocol_version, "is_error": result.is_error, "text": text}
        return work

    async def guarded(path, key, work):
        before = len(editors.commands()) if editors else 0
        try:
            out = await session(path, key, work)
        except BaseException as exc:  # noqa: BLE001 - the failure IS the observation
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            out = {"exception": f"{type(exc).__name__}: {exc}"[:400]}
        if editors:
            await asyncio.sleep(0.2)
            out["reached"] = [p for p, _ in editors.commands()[before:]]
        return out

    async def run():
        if editors is not None:
            return {
                "query_param": await guarded("/mcp?instance=bbbb2222", SECRET,
                                             call("read_console", {"action": "get", "count": 1})),
                "unrouted": await guarded("/mcp", SECRET,
                                          call("read_console", {"action": "get", "count": 1})),
                "gamachine_refuses_meta_tool": await guarded(
                    "/mcp/gamachine", SECRET, call("manage_tools", {"action": "list_groups"})),
            }
        return {
            "lists": {p: await guarded(p, SECRET, listing)
                      for p in ("/mcp", "/mcp/gamachine", "/mcp/full")},
            "no_key": await guarded("/mcp", None, listing),
            "unknown_profile": await guarded("/mcp/not-a-profile", SECRET, listing),
            "full_list_groups": await guarded("/mcp/full", SECRET,
                                              call("manage_tools", {"action": "list_groups"})),
            "activate": await guarded("/mcp", SECRET,
                                      call("manage_tools", {"action": "activate", "group": "vfx"})),
        }

    return asyncio.run(run())


def start_server(port: int, workdir: pathlib.Path, log) -> subprocess.Popen:
    src = pathlib.Path(__file__).resolve().parent.parent / "src"
    env = dict(os.environ)
    env.update({
        "UNITY_MCP_LOCAL_API_TOKEN": SECRET,
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_LOG_DIR": str(workdir / "logs"),
        "DISABLE_TELEMETRY": "true",
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "MCP_DISABLE_TELEMETRY": "true",
        "PYTHONUNBUFFERED": "1",
    })
    env.pop("UNITY_MCP_HTTP_URL", None)
    env.pop("UNITY_MCP_HTTP_PORT", None)
    env.pop("UNITY_MCP_HTTP_HOST", None)
    env.pop("UNITY_MCP_TRANSPORT", None)
    return subprocess.Popen(
        [sys.executable, str(src / "main.py"),
         "--transport", "http",
         "--http-url", f"http://127.0.0.1:{port}",
         "--project-scoped-tools"],
        cwd=str(src), env=env, stdout=log, stderr=subprocess.STDOUT,
    )


def wait_healthy(proc: subprocess.Popen, base: str) -> tuple[bool, float]:
    started = time.monotonic()
    while time.monotonic() - started < STARTUP_TIMEOUT_S:
        if proc.poll() is not None:
            return False, time.monotonic() - started
        try:
            if httpx.get(base + "/health", timeout=1.0).status_code == 200:
                return True, time.monotonic() - started
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    return False, time.monotonic() - started


def stdio_check(workdir: pathlib.Path) -> dict:
    """main.py --transport stdio: initialize, tools/list, one call, over pipes."""
    src = pathlib.Path(__file__).resolve().parent.parent / "src"
    env = dict(os.environ)
    env.update({"UNITY_MCP_SKIP_STARTUP_CONNECT": "1", "DISABLE_TELEMETRY": "true",
                "UNITY_MCP_DISABLE_TELEMETRY": "true", "MCP_DISABLE_TELEMETRY": "true",
                "UNITY_MCP_LOG_DIR": str(workdir / "stdio-logs")})
    env.pop("UNITY_MCP_TRANSPORT", None)
    proc = subprocess.Popen([sys.executable, str(src / "main.py"), "--transport", "stdio"],
                            cwd=str(src), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    result: dict = {}
    reply: dict = {}

    def send(message: dict) -> None:
        proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def receive() -> dict:
        # A blocked readline must not hang the suite if the server dies silently.
        def read() -> None:
            while True:
                line = proc.stdout.readline()
                if not line:
                    return
                message = json.loads(line)
                if "id" in message:
                    reply["message"] = message
                    return
        reply.clear()
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout=60)
        return reply.get("message") or {}

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": OLD_PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "live-probe", "version": "0"}}})
        result["protocol"] = (receive().get("result") or {}).get("protocolVersion")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        result["tool_count"] = len((receive().get("result") or {}).get("tools") or [])
    finally:
        proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
        proc.stdout.close()
    return result


def remove_workdir(path: pathlib.Path) -> bool:
    """Delete the probe's temp dir, waiting for the servers' file handles.

    On Windows `.venv/Scripts/python.exe` is a launcher that runs the real
    interpreter as a child (visible as a parent/child pair with the same
    command line). terminate() stops the launcher; the child follows a moment
    later, still holding its log file, so an immediate rmtree fails with
    WinError 32 (measured 25 Sep 2026). Success here also shows that no server
    process is left holding the directory.
    """
    deadline = time.monotonic() + 20
    while True:
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.25)


def run(paths: list[str]) -> dict:
    report: dict = {}
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="unity-mcp-live-probe-"))
    try:
        _serve_and_probe(workdir, paths, report)
    finally:
        report["workdir_removed"] = remove_workdir(workdir)
    return report


def _serve_and_probe(workdir: pathlib.Path, paths: list[str], report: dict) -> None:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    # Closed before the directory is removed: Windows cannot delete a file
    # that is still open.
    log = open(workdir / "server.out", "wb")
    proc = start_server(port, workdir, log)
    try:
        healthy, waited = wait_healthy(proc, base)
        report["healthy"] = healthy
        report["startup_seconds"] = round(waited, 2)
        if not healthy:
            report["exit_code"] = proc.poll()
            return
        report["old_era"] = {p: old_era_list_tools(base, p, SECRET) for p in paths}
        report["old_era_no_key"] = {p: old_era_list_tools(base, p, None) for p in paths}
        report["old_era_calls"] = old_era_calls(base)
        report["new_era"] = new_era(base)
        listener = ListChangedListener(base)
        listener.start()
        editors = FakeEditors(port)
        editors.start()
        try:
            report["editors_connected"] = wait_for_editors(base, 2)
            if report["editors_connected"]:
                report["with_editors"] = {
                    p: old_era_list_tools(base, p, SECRET)
                    for p in ("/mcp", "/mcp/gamachine", "/mcp/full")}
                report["routing"] = routing_scenario(base, editors)
                report["new_era_routing"] = new_era(base, editors)
            report["list_changed_methods"] = list(listener.methods)
        finally:
            editors.close()
            listener.close()
        report["still_running"] = proc.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
        log.close()
        report["server_output_tail"] = (
            (workdir / "server.out").read_bytes()[-4000:].decode("utf-8", "replace"))
    report["stdio"] = stdio_check(workdir)


def main() -> int:
    paths = sys.argv[1:] or ["/mcp"]
    json.dump(run(paths), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
