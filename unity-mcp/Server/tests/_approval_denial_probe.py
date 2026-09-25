"""Starts the real server with its approval gate pointed at a fake backend that
refuses, and records what a client sees on both protocol eras.

Child interpreter of test_live_approval_denial.py, for the same reasons as
_live_server_probe.py (whose server/session helpers it reuses): the test
process has stub fastmcp/mcp modules installed, this one does not.

THE FAKE BACKEND
    Speaks the two endpoints approval_gate.py uses (POST /mcp-approval-request,
    GET /mcp-approval-result/<gate_id>). What it answers is chosen by the
    `name` argument of the tool call, so one server run covers every way the
    gate can refuse. UNITYAI_URL points the server at it; without that the
    gate would ask http://localhost:8000, which may be the real product.

WHAT IT EMITS
    One JSON object on stdout: per scenario and era, the tool result
    (is_error, text) or the exception the mcp 2 client raised, plus every
    request the fake backend received.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import _live_server_probe as live

APP_TOKEN = "approval-probe-app-token-91c2"

# name argument -> how the fake backend answers.
#   deny_now:   resolved refusal on the POST itself (auto-deny, a rule)
#   deny_later: pending, then refused on the poll (the user clicked Deny)
#   auth:       401 on the POST (token rejected)
#   down:       503 on every POST until the gate's 10 s budget runs out
SCENARIOS = ("deny_now", "deny_later", "auth", "down")


def reason(scenario: str, era: str) -> str:
    return f"fake-backend-refused-{scenario}-{era}"


class FakeBackend:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self.pending: dict[str, str] = {}
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _reply(self, status: int, body: dict) -> None:
                raw = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                params = body.get("params") or {}
                scenario, _, era = str(params.get("name", "")).partition(":")
                with backend.lock:
                    backend.requests.append({
                        "path": self.path, "tool": body.get("tool"), "params": params,
                        "token": self.headers.get("X-Session-Token")})
                if self.path != "/mcp-approval-request":
                    self._reply(404, {})
                elif scenario == "deny_now":
                    self._reply(200, {"status": "resolved", "approved": False,
                                      "error": reason(scenario, era)})
                elif scenario == "deny_later":
                    with backend.lock:
                        backend.pending[body.get("gate_id")] = era
                    self._reply(200, {"status": "pending"})
                elif scenario == "auth":
                    self._reply(401, {"detail": "bad token"})
                else:
                    self._reply(503, {"detail": "backend down"})

            def do_GET(self) -> None:
                gate_id = self.path.rsplit("/", 1)[-1]
                with backend.lock:
                    era = backend.pending.get(gate_id)
                if era is None:
                    self._reply(404, {})
                else:
                    self._reply(200, {"status": "resolved", "approved": False,
                                      "error": reason("deny_later", era)})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.requests)


def old_era_call(base: str, path: str, tool: str, arguments: dict) -> dict:
    with live.OldEraSession(base, path, live.SECRET) as session:
        out = session.call_tool(tool, arguments)
        out["protocol"] = session.protocol
        return out


def new_era_calls(base: str, calls: list[tuple[str, str, str, dict]]) -> dict:
    sdk, why_not = live._new_era_sdk()
    if sdk is None:
        return {"skipped": why_not}
    httpx2, Client, streamable_http_client = sdk

    async def one(path: str, tool: str, arguments: dict) -> dict:
        try:
            async with httpx2.AsyncClient(headers={"X-API-Key": live.SECRET},
                                          timeout=httpx2.Timeout(30, read=60)) as http:
                async with Client(streamable_http_client(base + path, http_client=http),
                                  mode="auto") as client:
                    result = await client.call_tool(tool, arguments)
                    return {"protocol": client.protocol_version, "is_error": result.is_error,
                            "text": " ".join(getattr(b, "text", "") for b in result.content)}
        except BaseException as exc:  # noqa: BLE001 - the failure IS the observation
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            return {"exception": f"{type(exc).__name__}: {exc}"[:400]}

    async def run() -> dict:
        return {label: await one(path, tool, args) for label, path, tool, args in calls}

    return asyncio.run(run())


def _probe(workdir: pathlib.Path, report: dict) -> None:
    backend = FakeBackend()
    backend.start()
    os.environ["UNITYAI_URL"] = backend.url
    os.environ.pop("ANTIGRAVITY_URL", None)
    os.environ["LOCAL_APP_TOKEN"] = APP_TOKEN

    port = live._free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(workdir / "server.out", "wb")
    proc = live.start_server(port, workdir, log)
    try:
        healthy, _ = live.wait_healthy(proc, base)
        report["healthy"] = healthy
        if not healthy:
            report["exit_code"] = proc.poll()
            return

        def write_args(scenario: str, era: str) -> dict:
            return {"action": "create", "name": f"{scenario}:{era}"}

        report["old_era"] = {
            s: old_era_call(base, "/mcp", "manage_gameobject", write_args(s, "old"))
            for s in SCENARIOS}
        report["new_era"] = new_era_calls(base, [
            (s, "/mcp", "manage_gameobject", write_args(s, "new")) for s in SCENARIOS])

        # Neither of these may reach the backend: a read is not gated, and a
        # tool outside the connection's profile is refused before the gate.
        before = len(backend.snapshot())
        report["read_call"] = old_era_call(base, "/mcp", "read_console",
                                           {"action": "get", "count": 1})
        report["outside_profile"] = old_era_call(
            base, "/mcp/gamachine", "manage_vfx",
            {"action": "particle_create", "name": "deny_now:old"})
        report["backend_requests_after_gated_calls"] = backend.snapshot()[before:]
        report["still_running"] = proc.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
        log.close()
        backend.close()
        report["backend_requests"] = backend.snapshot()
        report["server_output_tail"] = (
            (workdir / "server.out").read_bytes()[-4000:].decode("utf-8", "replace"))


def main() -> int:
    report: dict = {"app_token": APP_TOKEN}
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="unity-mcp-approval-probe-"))
    try:
        _probe(workdir, report)
    finally:
        report["workdir_removed"] = live.remove_workdir(workdir)
    json.dump(report, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
