"""A client cancel reaches a tools/call parked in the approval card wait.

Runs as a CHILD INTERPRETER, launched by test_approval_cancel_live.py (not a
test module). A child because tests/integration/conftest.py installs stub
`fastmcp`/`mcp` modules for the whole pytest session (see
_authz_matrix_probe.py).

The real server app and a fake backend (cards stay pending, every request
recorded) each run on a uvicorn socket bound to port 0. A write call waits on
its card; the client then cancels it the way each protocol era does:

  * session era (2025-06-18, what agy speaks, measured 25 Sep 2026 from the
    live server's log): POST notifications/cancelled for the request id, as
    agy does at its fixed 180 s timeout;
  * 2026-07-28: the mcp 2 SDK client abandons the call, which closes the
    response stream (that revision has no client-to-server cancel message).

Emits one JSON object on stdout: per era, the ordered events with timestamps.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time

WRITE_CALL = ("manage_gameobject", {"action": "create", "name": "CancelProbe"})


def main() -> int:
    sys.path.insert(0, sys.argv[1])
    os.environ["LOCAL_APP_TOKEN"] = "cancel-probe-app-token"
    import httpx
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from core.config import config
    import main as server_main
    import services.tools.manage_gameobject as gameobject_module
    import transport.approval_gate as gate
    import transport.unity_instance_middleware as instance_module

    t0 = time.monotonic()
    events: list[list] = []

    def ev(kind: str, detail: object = "") -> None:
        events.append([round(time.monotonic() - t0, 3), kind, str(detail)[:200]])

    async def card_request(request):
        body = await request.json()
        ev("card_opened", body["gate_id"])
        return JSONResponse({"status": "ok", "gate_id": body["gate_id"]})

    async def card_result(request):
        ev("card_polled", request.path_params["gate_id"])
        return JSONResponse({"status": "pending"})

    async def card_respond(request):
        body = await request.json()
        ev("card_responded", json.dumps({"gate_id": request.path_params["gate_id"], **body}))
        return JSONResponse({"status": "ok"})

    backend = Starlette(routes=[
        Route("/mcp-approval-request", card_request, methods=["POST"]),
        Route("/mcp-approval-result/{gate_id}", card_result),
        Route("/mcp-approval-respond/{gate_id}", card_respond, methods=["POST"]),
    ])

    def serve(app):
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("uvicorn did not start")
        return server, thread, server.servers[0].sockets[0].getsockname()[1]

    config.local_api_token = "cancel-probe-key"
    config.http_remote_hosted = False

    backend_server, backend_thread, backend_port = serve(backend)
    gate._BACKEND_URL = f"http://127.0.0.1:{backend_port}"

    async def fake_send(send_fn, unity_instance, command, payload):
        ev("dispatched_to_unity", command)
        return {"success": True}

    async def sole_instance(self, ctx):
        return "Fake@abc"

    gameobject_module.send_with_unity_instance = fake_send
    instance_module.UnityInstanceMiddleware._maybe_autoselect_instance = sole_instance

    original_gate = gate.kapiyi_gec

    async def traced_gate(*args, **kwargs):
        ev("gate_entered", args[0])
        try:
            result = await original_gate(*args, **kwargs)
        except asyncio.CancelledError:
            ev("gate_cancelled")
            raise
        ev("gate_returned")
        return result

    gate.kapiyi_gec = traced_gate

    mcp = server_main.create_mcp_server(project_scoped_tools=False)
    app = mcp.http_app(path=server_main.resolve_http_transport_path(),
                       middleware=server_main.build_transport_middleware())
    mcp_server, mcp_thread, mcp_port = serve(app)
    url = f"http://127.0.0.1:{mcp_port}/mcp"
    report: dict = {}

    def wait_for(kind: str, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(e[1] == kind for e in events):
                return True
            time.sleep(0.05)
        return False

    def session_era() -> list:
        events.clear()
        headers = {"X-API-Key": config.local_api_token, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        with httpx.Client(timeout=30) as client:
            init = client.post(url, headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "cancel-probe", "version": "0"}}})
            headers["mcp-session-id"] = init.headers["mcp-session-id"]
            headers["mcp-protocol-version"] = "2025-06-18"
            client.post(url, headers=headers,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"})

            def call():
                try:
                    response = client.post(url, headers=headers, json={
                        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": WRITE_CALL[0], "arguments": WRITE_CALL[1]}})
                    ev("client_call_returned", response.status_code)
                except Exception as exc:
                    ev("client_call_returned", repr(exc))

            caller = threading.Thread(target=call, daemon=True)
            caller.start()
            if wait_for("card_polled"):
                time.sleep(1.0)
                cancel = httpx.post(url, headers=headers, timeout=10, json={
                    "jsonrpc": "2.0", "method": "notifications/cancelled",
                    "params": {"requestId": 7, "reason": "timed out after 3m0s"}})
                ev("client_cancelled", cancel.status_code)
            caller.join(10)
            time.sleep(2.0)
        return list(events)

    def modern_era() -> list | str:
        try:
            import importlib.metadata
            if int(importlib.metadata.version("mcp").split(".")[0]) < 2:
                return "skipped: mcp < 2"
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            return f"skipped: {exc}"
        events.clear()

        async def run():
            async with httpx2.AsyncClient(headers={"X-API-Key": config.local_api_token},
                                          timeout=httpx2.Timeout(30, read=60)) as http:
                async with Client(streamable_http_client(url, http_client=http), mode="auto") as client:
                    ev("negotiated", client.protocol_version)
                    call = asyncio.ensure_future(client.call_tool(*WRITE_CALL))
                    for _ in range(200):
                        if any(e[1] == "card_polled" for e in events):
                            break
                        await asyncio.sleep(0.05)
                    await asyncio.sleep(1.0)
                    call.cancel()
                    ev("client_cancelled")
                    try:
                        await call
                    except BaseException:
                        pass
                    await asyncio.sleep(2.0)

        asyncio.run(run())
        return list(events)

    try:
        report["session_era"] = session_era()
        report["modern_era"] = modern_era()
    finally:
        mcp_server.should_exit = True
        backend_server.should_exit = True
        mcp_thread.join(15)
        backend_thread.join(15)
    json.dump(report, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
