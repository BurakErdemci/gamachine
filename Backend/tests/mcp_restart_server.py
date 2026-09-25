"""Test servers for the Unity MCP restart tests (P3).

`RestartableMCPServer` is the MCP SDK's own MCPServer served over streamable HTTP
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
from mcp.server.mcpserver import MCPServer


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
        mcp = MCPServer("restart-test")
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
                                log_level="error", lifespan="on",
                                # A real restart kills open streams; waiting on
                                # the client's GET stream cost 15 s per stop.
                                timeout_graceful_shutdown=0.5)
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
    """`respond(message, headers)` returns (status, extra_headers, body).

    `body` is a dict (sent as JSON), bytes, or an iterator of bytes chunks,
    which is streamed without Content-Length (e.g. SSE keepalives) until it
    ends. POSTs are handled concurrently, so `respond` may block.
    `accepted` lists the messages in the order their connections were
    accepted, which is the order a client opened them.
    """

    def __init__(self, respond):
        self.requests = []
        self._accept_seq = []
        self._by_address = {}
        self._lock = threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                message = json.loads(self.rfile.read(length) or b"{}")
                with outer._lock:
                    outer.requests.append((message, self.headers))  # case-insensitive
                    outer._by_address[self.client_address] = message
                status, headers, body = respond(message, self.headers)
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                if isinstance(body, (bytes, dict, list)) or body is None:
                    payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for chunk in body:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_GET(self):
                # No standalone SSE stream here; an SDK client probes for one.
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_DELETE(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def process_request(self, request, client_address):
                # Runs on the accept loop, one connection at a time.
                with outer._lock:
                    outer._accept_seq.append(client_address)
                super().process_request(request, client_address)

        self._httpd = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/mcp"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def accepted(self) -> list:
        with self._lock:
            return [self._by_address[a] for a in self._accept_seq if a in self._by_address]

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
