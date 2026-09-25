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
import socket
import subprocess
import sys
import tempfile
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


def run(paths: list[str]) -> dict:
    report: dict = {}
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="unity-mcp-live-probe-") as tmp:
        workdir = pathlib.Path(tmp)
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
                return report
            report["old_era"] = {p: old_era_list_tools(base, p, SECRET) for p in paths}
            report["old_era_no_key"] = {p: old_era_list_tools(base, p, None) for p in paths}
            report["old_era_calls"] = old_era_calls(base)
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
    return report


def main() -> int:
    paths = sys.argv[1:] or ["/mcp"]
    json.dump(run(paths), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
