#!/usr/bin/env python3
"""
stdio <-> streamable-HTTP MCP bridge for the Unity MCP server.

Why it exists: Codex 0.14x broke on local FastMCP streamable-HTTP servers (it
runs OAuth discovery first and drops before `initialize`; openai/codex #26955,
#26072), while its stdio transport works. Cursor, Copilot, OpenCode, Kimi and
agy use the same bridge so the shared secret never lands in their config files
(K3). The bridge speaks newline-delimited JSON-RPC on stdio and forwards every
message to the ONE running unityMCP HTTP server; it never opens a second Unity
connection.

Server restarts (P3, 25 Sep 2026). A restarted server does not know our
Mcp-Session-Id and answers 404 {"id":"server-error", ... "Session not found"}.
The old bridge forwarded that body unchanged, the client kept waiting for a
response with its own id, and every later call timed out (measured: 4 x 30 s,
then "Connection closed"). Now:
  - 404 on a request that carried a session id -> drop the id, replay the
    client's stored `initialize` + `notifications/initialized`, retry the
    original message ONCE. Safe: a 404 is the session lookup failing, so the
    server never ran the request.
  - Every request gets exactly one response carrying its own id; a server
    error with a foreign id is rewritten, a missing answer is synthesized.
  - Connection refused (server down or restarting) -> a clear error for that
    request; the next request recovers through the 404 path.
  - The negotiated MCP-Protocol-Version header is sent on every later POST.

Scope: client-initiated requests and notifications. Server-initiated messages
that arrive inside a POST's SSE stream are forwarded; a standalone GET stream
is not opened (unityMCP tools are synchronous).

Usage:  UNITY_MCP_URL=http://127.0.0.1:8080/mcp python codex_unitymcp_bridge.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8080/mcp"
HTTP_TIMEOUT_S = 180

_REINIT_ID_PREFIX = "gamachine-bridge-reinit-"

# User-facing (Turkish): these reach the model and, through it, the user.
MSG_UNREACHABLE = ("Unity MCP sunucusuna bağlanılamadı (kapalı ya da yeniden "
                   "başlıyor). Sunucu ayağa kalkınca bir sonraki çağrı kendiliğinden "
                   "yeniden bağlanır.")
MSG_CONNECTION_LOST = ("Unity MCP bağlantısı çağrı sürerken koptu; çağrının Unity'de "
                       "çalışıp çalışmadığı bilinmiyor. Bir sonraki çağrı yeniden bağlanır.")
MSG_TIMEOUT = ("Unity MCP {seconds} sn içinde yanıt vermedi (zaman aşımı); çağrının "
               "Unity'de çalışıp çalışmadığı bilinmiyor.")
MSG_REINIT_FAILED = ("Unity MCP sunucusu yeniden başlamış ve yeni oturum açılamadı: {detail}")
MSG_NO_ANSWER = "Unity MCP sunucusu bu isteğe yanıt vermedi (HTTP {status})."
MSG_BRIDGE_ERROR = "Unity MCP köprü hatası: {detail}"


def bridge_argv() -> list:
    """argv that starts this bridge (dev and frozen builds differ).

    One shared function because it carries a security decision: the secret is
    read INSIDE the bridge from the token file and never appears on a command
    line or in a config. A copied argv would drift, and the stale copy would be
    the one writing the secret back to disk.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "codex-mcp-bridge"]
    main_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return [sys.executable, main_py, "codex-mcp-bridge"]


def _read_shared_secret():
    """Reads the shared secret straight from the token file.

    Not env/argv: the bridge runs on the same machine as the same user, so it
    can read the file anyway; env/argv put the secret into `ps`, `.mcp.json`
    and error logs (four audit findings came from that).
    """
    path = os.path.join(os.path.expanduser("~"), ".unity-mcp", "local-api-token")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


class _Unreachable(Exception):
    """The POST never reached the server (connection refused / reset on connect)."""


class _Reply:
    def __init__(self, status, messages):
        self.status = status
        self.messages = messages


def _error(req_id, message, code=-32603):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _is_request(message):
    return isinstance(message, dict) and "method" in message and message.get("id") is not None


class Bridge:
    def __init__(self, url, secret_reader=_read_shared_secret, timeout=HTTP_TIMEOUT_S):
        self.url = url
        self.session_id = None
        self.protocol_version = None
        self._secret_reader = secret_reader
        self.api_key = secret_reader()
        self.timeout = timeout
        self._init_request = None
        self._initialized_note = None
        self._reinit_seq = 0

    # ── HTTP ────────────────────────────────────────────────────────────
    def _post(self, message):
        """POSTs one JSON-RPC message; returns the status and every JSON-RPC
        message in the body (JSON or SSE). Raises _Unreachable when the server
        was never reached."""
        data = json.dumps(message).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version and message.get("method") != "initialize":
            headers["MCP-Protocol-Version"] = self.protocol_version
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            resp = e  # an error status still carries a JSON-RPC body
        except urllib.error.URLError as e:
            # Only failures before any byte was sent land here (refused,
            # unresolvable); a request that went out cannot have executed yet.
            raise _Unreachable(str(e.reason)) from e

        status = getattr(resp, "status", None) or resp.getcode()
        sid = resp.headers.get("Mcp-Session-Id")
        if sid and status < 400:
            self.session_id = sid
        ctype = (resp.headers.get("Content-Type") or "").lower()

        out = []
        if "text/event-stream" in ctype:
            data_lines = []
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif line == "" and data_lines:
                    self._collect("\n".join(data_lines), out)
                    data_lines = []
            if data_lines:
                self._collect("\n".join(data_lines), out)
        else:
            body = resp.read()
            if body.strip():
                self._collect(body.decode("utf-8", "replace"), out)
        return _Reply(status, out)

    @staticmethod
    def _collect(payload, out):
        try:
            parsed = json.loads(payload)
        except ValueError:
            return
        if isinstance(parsed, list):
            out.extend(p for p in parsed if isinstance(p, dict))
        elif isinstance(parsed, dict):
            out.append(parsed)

    # ── session recovery ───────────────────────────────────────────────
    def _reinitialize(self):
        """Opens a fresh server session by replaying the client's own
        handshake. Returns None on success, else a short failure reason."""
        self.session_id = None
        # The token file may have been created since start-up.
        self.api_key = self._secret_reader() or self.api_key
        self._reinit_seq += 1
        init = dict(self._init_request)
        init["id"] = f"{_REINIT_ID_PREFIX}{self._reinit_seq}"
        reply = self._post(init)
        result = next((m.get("result") for m in reply.messages
                       if m.get("id") == init["id"] and isinstance(m.get("result"), dict)), None)
        if reply.status >= 400 or result is None:
            return f"HTTP {reply.status}"
        version = result.get("protocolVersion")
        if version:
            if self.protocol_version and version != self.protocol_version:
                self._log(f"re-initialize negotiated {version}, client has {self.protocol_version}")
            self.protocol_version = version
        note = self._initialized_note or {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self._post(note)
        self._log("server session lost (404); re-initialized")
        return None

    # ── one client message ─────────────────────────────────────────────
    def handle(self, message):
        """Forwards one client message; returns what goes back to the client."""
        is_request = _is_request(message)
        method = message.get("method") if isinstance(message, dict) else None
        if method == "initialize":
            self._init_request = message
            self.session_id = None
            self.protocol_version = None
        elif method == "notifications/initialized":
            self._initialized_note = message

        try:
            had_session = self.session_id is not None
            reply = self._post(message)
            if (reply.status == 404 and had_session and method != "initialize"
                    and self._init_request is not None):
                failure = self._reinitialize()
                if failure is not None:
                    return [_error(message.get("id"), MSG_REINIT_FAILED.format(detail=failure))
                            ] if is_request else []
                # Exactly one retry; a second 404 is reported, not looped.
                reply = self._post(message)
        except _Unreachable:
            return [_error(message.get("id"), MSG_UNREACHABLE)] if is_request else []
        except TimeoutError:
            return [_error(message.get("id"), MSG_TIMEOUT.format(seconds=self.timeout))
                    ] if is_request else []
        except (ConnectionError, OSError) as e:
            # The request was sent and the link broke while waiting: it may
            # have run in Unity, so this is never retried.
            self._log(f"connection lost mid-request: {e}")
            return [_error(message.get("id"), MSG_CONNECTION_LOST)] if is_request else []
        except Exception as e:  # noqa: BLE001 - the client must always get an answer
            return [_error(message.get("id"), MSG_BRIDGE_ERROR.format(detail=e))] if is_request else []

        if method == "initialize" and is_request:
            for m in reply.messages:
                if m.get("id") == message.get("id") and isinstance(m.get("result"), dict):
                    self.protocol_version = m["result"].get("protocolVersion") or None
        return self._fit(message, reply)

    def _fit(self, message, reply):
        """Keeps server-initiated messages, and makes sure a request gets exactly
        one response with its own id; a notification gets no response at all."""
        out = []
        answered = False
        req_id = message.get("id") if _is_request(message) else None
        for m in reply.messages:
            if "method" in m:
                out.append(m)
                continue
            if req_id is None:
                self._log(f"dropped a response to a notification: {json.dumps(m)[:200]}")
                continue
            if answered:
                self._log(f"dropped an extra response: {json.dumps(m)[:200]}")
                continue
            if m.get("id") != req_id:
                if "error" not in m:
                    self._log(f"dropped a result with a foreign id: {json.dumps(m)[:200]}")
                    continue
                # e.g. {"id":"server-error"} or null: without the rewrite the
                # client waits for its own id until it times out.
                m = dict(m, id=req_id)
            out.append(m)
            answered = True
        if req_id is not None and not answered:
            out.append(_error(req_id, MSG_NO_ANSWER.format(status=reply.status)))
        return out

    # ── stdio loop ─────────────────────────────────────────────────────
    def run(self, stdin=None, stdout=None):
        # Bytes, decoded as UTF-8: text-mode stdin uses the locale code page on
        # Windows (cp1254 here) and would garble non-ASCII tool arguments.
        stdin = stdin or sys.stdin.buffer
        self._stdout = stdout or sys.stdout.buffer
        for raw in stdin:
            line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            for r in self.handle(message):
                self._emit(r)

    def _emit(self, obj):
        self._stdout.write((json.dumps(obj) + "\n").encode("utf-8"))
        self._stdout.flush()

    @staticmethod
    def _log(text):
        # stderr is the stdio transport's log channel; stdout carries JSON-RPC only.
        try:
            sys.stderr.write(f"[unitymcp-bridge] {text}\n")
            sys.stderr.flush()
        except Exception:
            pass


def main():
    # The URL carries no secret (it travels in X-API-Key, read from the token
    # file), so env/argv are fine here.
    url = os.environ.get("UNITY_MCP_URL") or (
        sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    )
    Bridge(url).run()


if __name__ == "__main__":
    main()
