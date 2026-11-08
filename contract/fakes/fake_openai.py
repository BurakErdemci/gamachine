"""A fake OpenAI-compatible model server on 127.0.0.1 that replays files from contract/fixtures/.

The backend's `openai` provider builds its SDK client without a base URL, so the SDK takes
OPENAI_BASE_URL from the environment; the suite points that at this server. The model name
of the chat picks the scenario: `contract-<name>` replays `fixtures/openai_<name>.*`.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
STALL_LIMIT_S = 20.0


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type="application/json"):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, json.dumps({"object": "list", "data": []}))
        self._send(404, json.dumps({"error": {"message": "not faked", "type": "not_found"}}))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append({"path": self.path, "body": request,
                                     "authorization": self.headers.get("Authorization", "")})
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, json.dumps({"error": {"message": "not faked", "type": "not_found"}}))
        scenario = str(request.get("model", "")).removeprefix("contract-")
        error = FIXTURES / f"openai_{scenario}.json"
        stream = FIXTURES / f"openai_{scenario}.sse"
        if error.is_file():
            recorded = json.loads(error.read_text(encoding="utf-8"))
            return self._send(recorded["status"], json.dumps(recorded["body"]))
        if not stream.is_file():
            return self._send(404, json.dumps({"error": {"message": f"no fixture for {scenario!r}",
                                                         "type": "not_found"}}))
        self._stream(stream.read_text(encoding="utf-8"))

    def _stream(self, text):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        for block in text.replace("\r\n", "\n").split("\n\n"):
            block = block.strip()
            if not block:
                continue
            if block == ": stall":
                self._stall()
                return
            self.wfile.write((block + "\n\n").encode("utf-8"))
            self.wfile.flush()

    def _stall(self):
        """Hold the stream open until the backend hangs up (or the limit passes)."""
        deadline = time.monotonic() + STALL_LIMIT_S
        while time.monotonic() < deadline and not self.server.closing.is_set():
            try:
                self.wfile.write(b": still here\n\n")
                self.wfile.flush()
            except OSError:
                self.server.hung_up.set()
                return
            time.sleep(0.1)


class FakeOpenAI:
    """Start with `start()`, read `base_url`, stop with `stop()`."""

    def __init__(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.requests = []
        self._server.hung_up = threading.Event()
        self._server.closing = threading.Event()
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.1}, daemon=True)

    @property
    def base_url(self):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    @property
    def requests(self):
        return self._server.requests

    @property
    def hung_up(self):
        return self._server.hung_up

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.closing.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
