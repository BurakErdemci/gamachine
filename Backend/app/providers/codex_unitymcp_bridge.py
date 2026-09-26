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
  - 404 on a request that carried a session id -> replay the client's stored
    `initialize` + `notifications/initialized`, retry the original message
    ONCE. Safe: a 404 is the session lookup failing, so the server never ran
    the request.
  - A replay that fails (server still starting: 503, refused) leaves the
    bridge marked "needs re-init"; every later message retries the replay
    first and is sent only after it succeeds. The first version dropped the
    session id before the replay, so after one failed replay every call went
    out without a session and got 400 "Missing session ID" forever (audit
    finding bridge-stuck-after-failed-reinit, 25 Sep 2026).
  - Every request gets exactly one response carrying its own id; a server
    error with a foreign id is rewritten, a missing answer is synthesized.
  - Connection refused (server down or restarting) -> a clear error for that
    request; the next request recovers through the 404 path.
  - The negotiated MCP-Protocol-Version header is sent on every later POST.

Deadline. Each request has ONE overall deadline (HTTP_TIMEOUT_S), re-armed as
the socket timeout before every read. urllib's timeout was per read and the
server's SSE keepalive comments reset it, so a request with no answer waited
forever (audit finding unbounded-request-wait, 25 Sep 2026). When the deadline
passes the request is answered with "outcome unknown": it may have run in Unity.

Concurrency. The stdin reader sends messages in stdin order: a message's bytes
are fully written before the next line is read. Only the wait for a request's
response runs on a worker thread, so a request parked on an approval card no
longer holds up a notifications/cancelled or a parallel read. `initialize` and
notifications are finished on the reader thread (nothing may overtake the
handshake). Responses can come back out of order; JSON-RPC clients match them
by id. At most MAX_IN_FLIGHT requests wait at once; one more is answered at
once with an error and never sent, so the reader never waits for a worker.

Scope: client-initiated requests and notifications. Server-initiated messages
that arrive inside a POST's SSE stream are forwarded; a standalone GET stream
is not opened (unityMCP tools are synchronous).

Usage:  UNITY_MCP_URL=http://127.0.0.1:8080/mcp python codex_unitymcp_bridge.py
"""
import http.client
import json
import os
import sys
import threading
import time
import urllib.parse

DEFAULT_URL = "http://127.0.0.1:8080/mcp"
# Overall budget per request. The server holds a write for up to 10 s (POST)
# + its approval card wait (180 s today, 150 s after the server change of
# 25 Sep 2026), and the tool still has to run after the click. 240 s leaves
# >= 50 s of run time on either card budget and matches the backend client's
# CALL_TIMEOUT_S, so both clients give up at the same moment.
HTTP_TIMEOUT_S = 240
CONNECT_TIMEOUT_S = 10
# The replayed handshake is two small POSTs; a server that needs longer is not
# up yet, and the reader thread must not sit on the session lock for minutes.
REINIT_TIMEOUT_S = 30
# Notifications are answered 202 at once; they run on the reader thread.
NOTIFY_TIMEOUT_S = 30
# Worker threads waiting on responses at once. A request beyond this is
# answered at once with MSG_BUSY and never sent; the reader never waits here.
MAX_IN_FLIGHT = 16
# JSON-RPC implementation-defined server error: the bridge refused the request.
BUSY_ERROR_CODE = -32000

_REINIT_ID_PREFIX = "gamachine-bridge-reinit-"

# User-facing (Turkish): these reach the model and, through it, the user.
MSG_UNREACHABLE = ("Unity MCP sunucusuna bağlanılamadı (kapalı ya da yeniden "
                   "başlıyor). Sunucu ayağa kalkınca bir sonraki çağrı kendiliğinden "
                   "yeniden bağlanır.")
MSG_CONNECTION_LOST = ("Unity MCP bağlantısı çağrı sürerken koptu; çağrının Unity'de "
                       "çalışıp çalışmadığı bilinmiyor. Bir sonraki çağrı yeniden bağlanır.")
MSG_TIMEOUT = ("Unity MCP {seconds:g} sn içinde yanıt vermedi (zaman aşımı); çağrının "
               "sonucu bilinmiyor: Unity işlemi uygulamış olabilir. Tekrar denemeden "
               "önce Unity'deki durumu kontrol et.")
MSG_REINIT_FAILED = ("Unity MCP sunucusu yeniden başlamış ve yeni oturum henüz açılamadı "
                     "({detail}); bu çağrı Unity'ye gönderilmedi. Bir sonraki çağrı yeniden "
                     "bağlanmayı dener.")
MSG_NO_ANSWER = "Unity MCP sunucusu bu isteğe yanıt vermedi (HTTP {status})."
MSG_BUSY = ("Unity MCP köprüsünde zaten {limit} çağrı yanıt bekliyor; bu çağrı Unity'ye "
            "gönderilmedi. Bekleyen çağrılardan biri bitince yeniden dene.")
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


# Must match GAMACHINE_CONVERSATION_HEADER in unity-mcp/Server/src/core/constants.py.
CONVERSATION_HEADER = "X-Gamachine-Conversation"


def _conversation_from_env():
    """The chat this bridge was spawned for (GAMACHINE_CONVERSATION_ID), or None.

    Same parser as the unityai approval bridge, so both servers of one CLI
    process name the same chat or none. A card owned by the wrong chat is worse
    than an unowned one, so anything but a plain positive int sends no header.
    """
    try:
        from unity_ai_mcp.approval_bridge import _conversation_id_from_env
    except ImportError:
        return None
    return _conversation_id_from_env()


class _Unreachable(Exception):
    """The POST never reached the server (connection refused / connect timeout)."""


class _DeadlineExceeded(Exception):
    """The request's overall deadline passed while waiting for the answer."""


class _ReinitFailed(Exception):
    """The session could not be rebuilt; the message was not sent."""


class _Reply:
    def __init__(self, status, messages):
        self.status = status
        self.messages = messages


class _Pending:
    """A POST whose bytes are on the wire and whose answer is still unread."""

    def __init__(self, message, conn, sock, deadline, session_id):
        self.message = message
        self.conn = conn
        self.sock = sock
        self.deadline = deadline
        self.session_id = session_id


def _error(req_id, message, code=-32603):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _is_request(message):
    return isinstance(message, dict) and "method" in message and message.get("id") is not None


def _answers(out, req_id):
    return any("method" not in m and m.get("id") == req_id for m in out)


class Bridge:
    def __init__(self, url, secret_reader=_read_shared_secret, timeout=HTTP_TIMEOUT_S,
                 conversation_reader=_conversation_from_env):
        self.url = url
        self.session_id = None
        self.protocol_version = None
        self._secret_reader = secret_reader
        self.api_key = secret_reader()
        # Read once: the process belongs to one chat for its whole life.
        self.conversation_id = conversation_reader()
        self.timeout = timeout
        self._init_request = None
        self._initialized_note = None
        self._reinit_seq = 0
        # True from a lost session until a replayed handshake succeeds; while
        # set, nothing but the replay is sent.
        self._needs_reinit = False
        # Guards the session state above; held across a replay so no request
        # goes out on a half-built session.
        self._lock = threading.RLock()
        self._emit_lock = threading.Lock()
        self._stdout = None

    # ── HTTP ────────────────────────────────────────────────────────────
    def _send(self, message, deadline):
        """Connects and writes one POST; returns a _Pending. Raises _Unreachable
        when nothing reached the server."""
        parts = urllib.parse.urlsplit(self.url)
        conn_cls = (http.client.HTTPSConnection if parts.scheme == "https"
                    else http.client.HTTPConnection)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        is_init = message.get("method") == "initialize"
        with self._lock:
            session_id = None if is_init else self.session_id
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            if self.api_key:
                headers["X-API-Key"] = self.api_key
            if self.conversation_id is not None:
                headers[CONVERSATION_HEADER] = str(self.conversation_id)
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            if self.protocol_version and not is_init:
                headers["MCP-Protocol-Version"] = self.protocol_version
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _DeadlineExceeded()
        conn = conn_cls(parts.hostname, parts.port,
                        timeout=min(CONNECT_TIMEOUT_S, remaining))
        try:
            conn.connect()
        except OSError as e:
            conn.close()
            raise _Unreachable(str(e)) from e
        # Kept separately: http.client drops conn.sock once it hands out a
        # response that closes the connection, but that response reads from it.
        sock = conn.sock
        try:
            conn.request("POST", path, body=json.dumps(message).encode("utf-8"),
                         headers=headers)
        except BaseException:
            conn.close()
            raise
        return _Pending(message, conn, sock, deadline, session_id)

    @staticmethod
    def _arm(pending):
        remaining = pending.deadline - time.monotonic()
        if remaining <= 0:
            raise _DeadlineExceeded()
        # -1: http.client closed it after the last body byte of a closing
        # response; the next read returns b"" without touching the socket.
        if pending.sock.fileno() != -1:
            pending.sock.settimeout(remaining)

    def _receive(self, pending):
        """Reads the answer to a sent POST within its deadline: the status and
        every JSON-RPC message in the body (JSON or SSE)."""
        try:
            self._arm(pending)
            resp = pending.conn.getresponse()
            status = resp.status
            sid = resp.getheader("Mcp-Session-Id")
            if sid and status < 400 and pending.message.get("method") == "initialize":
                # Only a handshake assigns the session. With concurrent calls a
                # late answer on an old session echoes that session's id and
                # must not bring it back.
                with self._lock:
                    self.session_id = sid
            ctype = (resp.getheader("Content-Type") or "").lower()
            req_id = pending.message.get("id") if _is_request(pending.message) else None

            out = []
            if "text/event-stream" in ctype:
                data_lines = []
                while True:
                    self._arm(pending)
                    raw = resp.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line == "" and data_lines:
                        self._collect("\n".join(data_lines), out)
                        data_lines = []
                        if req_id is not None and _answers(out, req_id):
                            break  # our answer is in; the SDK client stops here too
                if data_lines:
                    self._collect("\n".join(data_lines), out)
            else:
                chunks = []
                while True:
                    self._arm(pending)
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                body = b"".join(chunks)
                if body.strip():
                    self._collect(body.decode("utf-8", "replace"), out)
            return _Reply(status, out)
        except TimeoutError as e:  # socket.timeout: the re-armed deadline hit
            raise _DeadlineExceeded() from e
        finally:
            pending.conn.close()

    def _post(self, message, deadline):
        return self._receive(self._send(message, deadline))

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
    def _reinitialize(self, deadline):
        """Opens a fresh server session by replaying the client's own
        handshake. Caller holds self._lock. Returns None on success, else a
        short failure reason; on failure the bridge stays marked, so the next
        message tries again. Raises _Unreachable when the server is down."""
        self._needs_reinit = True
        self.session_id = None
        # The token file may have been created since start-up.
        self.api_key = self._secret_reader() or self.api_key
        deadline = min(deadline, time.monotonic() + REINIT_TIMEOUT_S)
        self._reinit_seq += 1
        init = dict(self._init_request)
        init["id"] = f"{_REINIT_ID_PREFIX}{self._reinit_seq}"
        try:
            reply = self._post(init, deadline)
            result = next((m.get("result") for m in reply.messages
                           if m.get("id") == init["id"] and isinstance(m.get("result"), dict)),
                          None)
            if reply.status >= 400 or result is None or not self.session_id:
                self.session_id = None
                return f"HTTP {reply.status}"
            version = result.get("protocolVersion")
            if version:
                if self.protocol_version and version != self.protocol_version:
                    self._log(f"re-initialize negotiated {version}, client has {self.protocol_version}")
                self.protocol_version = version
            note = self._initialized_note or {"jsonrpc": "2.0", "method": "notifications/initialized"}
            note_reply = self._post(note, deadline)
            if note_reply.status >= 400:
                self.session_id = None
                return f"HTTP {note_reply.status}"
        except _Unreachable:
            raise
        except _DeadlineExceeded:
            self.session_id = None
            return "zaman aşımı"
        except (OSError, http.client.HTTPException) as e:
            self.session_id = None
            return f"bağlantı koptu: {e}"
        self._needs_reinit = False
        self._log("server session lost (404); re-initialized")
        return None

    def _ensure_session(self, deadline):
        """Rebuilds a lost session before a message goes out."""
        with self._lock:
            if self._needs_reinit and self._init_request is not None:
                failure = self._reinitialize(deadline)
                if failure is not None:
                    raise _ReinitFailed(failure)

    def _recover(self, lost_session, deadline):
        """After a 404 on `lost_session`: rebuild the session, unless a
        concurrent request already did."""
        with self._lock:
            if self.session_id == lost_session or self.session_id is None:
                self._needs_reinit = True
        self._ensure_session(deadline)

    # ── one client message ─────────────────────────────────────────────
    def _start(self, message):
        """The ordered half: records handshake state, rebuilds a lost session
        and writes the POST. Returns a _Pending, or the final answer (a list)
        when nothing was sent."""
        method = message.get("method")
        timeout = self.timeout if _is_request(message) else min(self.timeout, NOTIFY_TIMEOUT_S)
        deadline = time.monotonic() + timeout
        with self._lock:
            if method == "initialize":
                self._init_request = message
                self.session_id = None
                self.protocol_version = None
                self._needs_reinit = False
            elif method == "notifications/initialized":
                self._initialized_note = message
        try:
            if method != "initialize":
                self._ensure_session(deadline)
            return self._send(message, deadline)
        except Exception as e:  # noqa: BLE001 - the client must always get an answer
            return self._failure(message, e)

    def _finish(self, pending):
        """The concurrent half: reads the answer, recovers a lost session once."""
        message = pending.message
        method = message.get("method")
        try:
            reply = self._receive(pending)
            if (reply.status == 404 and pending.session_id is not None
                    and method != "initialize" and self._init_request is not None):
                self._recover(pending.session_id, pending.deadline)
                # Exactly one retry; a second 404 is reported, not looped.
                retry = self._send(message, pending.deadline)
                reply = self._receive(retry)
                if reply.status == 404 and retry.session_id is not None:
                    with self._lock:
                        if self.session_id == retry.session_id:
                            self._needs_reinit = True
        except Exception as e:  # noqa: BLE001 - the client must always get an answer
            return self._failure(message, e)

        if method == "initialize" and _is_request(message):
            for m in reply.messages:
                if m.get("id") == message.get("id") and isinstance(m.get("result"), dict):
                    with self._lock:
                        self.protocol_version = m["result"].get("protocolVersion") or None
        return self._fit(message, reply)

    def handle(self, message):
        """Forwards one client message; returns what goes back to the client."""
        step = self._start(message)
        return step if isinstance(step, list) else self._finish(step)

    def _failure(self, message, exc):
        if not _is_request(message):
            return []
        req_id = message.get("id")
        if isinstance(exc, _Unreachable):
            return [_error(req_id, MSG_UNREACHABLE)]
        if isinstance(exc, _ReinitFailed):
            return [_error(req_id, MSG_REINIT_FAILED.format(detail=exc))]
        if isinstance(exc, _DeadlineExceeded):
            self._log(f"request {req_id!r} passed its {self.timeout:g} s deadline")
            return [_error(req_id, MSG_TIMEOUT.format(seconds=self.timeout))]
        if isinstance(exc, (OSError, http.client.HTTPException)):
            # The request was sent and the link broke while waiting: it may
            # have run in Unity, so this is never retried.
            self._log(f"connection lost mid-request: {exc}")
            return [_error(req_id, MSG_CONNECTION_LOST)]
        return [_error(req_id, MSG_BRIDGE_ERROR.format(detail=exc))]

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
        slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)
        workers = []

        def finish(pending):
            try:
                self._emit_all(self._finish(pending))
            finally:
                slots.release()

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
            concurrent = _is_request(message) and message.get("method") != "initialize"
            if concurrent and not slots.acquire(blocking=False):
                # Waiting here parked the reader, so a notifications/cancelled
                # behind this line was not forwarded until a worker finished
                # (verification round, 26 Sep 2026). Refused, not queued: it
                # was never sent, so its one answer can say so.
                self._log(f"request {message.get('id')!r} refused: {MAX_IN_FLIGHT} in flight")
                self._emit(_error(message.get("id"), MSG_BUSY.format(limit=MAX_IN_FLIGHT),
                                  code=BUSY_ERROR_CODE))
                continue
            step = self._start(message)
            if isinstance(step, list) or not concurrent:
                if concurrent:
                    slots.release()
                self._emit_all(step if isinstance(step, list) else self._finish(step))
                continue
            worker = threading.Thread(target=finish, args=(step,), daemon=True,
                                      name=f"bridge-{message.get('id')}")
            worker.start()
            workers = [w for w in workers if w.is_alive()] + [worker]
        # EOF: every request already read still gets its answer; each worker is
        # bounded by its own deadline.
        for worker in workers:
            worker.join()

    def _emit_all(self, objs):
        # One lock for the whole batch: a request's server-initiated messages
        # stay next to its response, and lines never interleave.
        with self._emit_lock:
            for obj in objs:
                try:
                    self._stdout.write((json.dumps(obj) + "\n").encode("utf-8"))
                    self._stdout.flush()
                except (OSError, ValueError) as e:  # the client is gone
                    self._log(f"stdout closed: {e}")
                    return

    def _emit(self, obj):
        self._emit_all([obj])

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
