"""Test servers for the Unity MCP restart tests (P3).

`RestartableMCPServer` is the MCP SDK's own FastMCP served over streamable HTTP
on a fixed port. Stopping and starting it again throws away every session,
which is what a Unity MCP restart does: a stale Mcp-Session-Id gets the real
404 {"id":"server-error", ... "Session not found"} from the SDK session manager.
Tool executions are counted, so a test can prove nothing ran twice.

`ScriptedHTTPServer` answers each POST from a test-supplied function and
records the requests, for shapes the real server does not produce on demand.
"""
import collections
import http.server
import json
import socket
import threading
import time

import uvicorn
from mcp.server.fastmcp import FastMCP


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RestartableMCPServer:
    def __init__(self):
        self.port = free_port()
        self.executions = collections.Counter()
        self.generation = 0
        self._server = None
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def _app(self):
        mcp = FastMCP("restart-test")
        counts = self.executions

        @mcp.tool()
        def echo(text: str) -> str:
            counts["echo"] += 1
            return text

        @mcp.tool()
        def mutate(value: str) -> str:
            counts["mutate"] += 1
            return f"mutated {value}"

        return mcp.streamable_http_app()

    def start(self) -> None:
        self.generation += 1
        config = uvicorn.Config(self._app(), host="127.0.0.1", port=self.port,
                                log_level="error", lifespan="on")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("test MCP server did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.should_exit = True
        self._thread.join(15)
        self._server = None

    def restart(self) -> None:
        self.stop()
        self.start()


class ScriptedHTTPServer:
    """`respond(message, headers)` returns (status, extra_headers, body)."""

    def __init__(self, respond):
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                message = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append((message, self.headers))  # case-insensitive
                status, headers, body = respond(message, self.headers)
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/mcp"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
