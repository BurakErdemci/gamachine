"""Sends a corpus of odd request paths to the real app over a real uvicorn socket.

Runs as a CHILD INTERPRETER, launched by test_transport_path_corpus.py (not a
test module: the name does not match `test_*.py`). A child because
tests/integration/conftest.py installs stub `fastmcp`/`mcp` modules for the
whole pytest session; see _authz_matrix_probe.py for that measurement.

Why a socket and raw bytes: the finding this guards (25 Sep 2026) was
POST /mcp%0A answering initialize with no key. What matters is the path uvicorn
decodes from the request line and hands to the ASGI app, so the request line is
written byte for byte; an HTTP client library could normalise it first.

uvicorn binds port 0 and the probe reads the port back from the bound socket,
so there is no pick-then-bind race and no fixed port. The server runs in a
thread of this child and exits with it.

Emits one JSON object on stdout: {"anonymous"|"keyed": {path: {...}}, "health": {...}}.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time

SECRET = "path-corpus-secret-5b1e"
INIT = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "path-corpus", "version": "0"}},
}).encode()


def _raw(port: int, method: str, path: str, key: str | None, body: bytes = b"") -> dict:
    head = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{port}",
            "Accept: application/json, text/event-stream",
            "Content-Type: application/json", f"Content-Length: {len(body)}",
            "Connection: close"]
    if key is not None:
        head.append(f"X-API-Key: {key}")
    request = ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body
    data = b""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
        conn.sendall(request)
        conn.settimeout(10)
        try:
            while b"protocolVersion" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
    status_line = data.split(b"\r\n", 1)[0].decode("latin-1")
    parts = status_line.split(" ")
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return {"status": status, "initialized": b"protocolVersion" in data}


def main() -> int:
    src_dir, paths = sys.argv[1], sys.argv[2:]
    sys.path.insert(0, src_dir)
    import uvicorn

    from core.config import config
    import main as server_main

    config.local_api_token = SECRET
    config.http_remote_hosted = False
    mcp = server_main.create_mcp_server(project_scoped_tools=False)
    app = mcp.http_app(path=server_main.resolve_http_transport_path(),
                       middleware=server_main.build_transport_middleware())

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        print("uvicorn did not start", file=sys.stderr)
        return 2
    port = server.servers[0].sockets[0].getsockname()[1]

    try:
        report = {
            "port": port,
            "anonymous": {p: _raw(port, "POST", p, None, INIT) for p in paths},
            "keyed": {p: _raw(port, "POST", p, SECRET, INIT) for p in paths},
            "health": {
                "GET /health": _raw(port, "GET", "/health", None),
                "HEAD /health": _raw(port, "HEAD", "/health", None),
                "GET /health%0A": _raw(port, "GET", "/health%0A", None),
                "GET /Health": _raw(port, "GET", "/Health", None),
            },
        }
    finally:
        server.should_exit = True
        thread.join(15)
    json.dump(report, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
