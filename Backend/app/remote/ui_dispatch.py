"""The web remote's UI dispatcher (docs/remote-control-web.md, "Desktop side").

A paired browser runs the desktop's own renderer; its HTTP calls arrive as
`ui_http` / `ui_stream_open` frames and its `window.ipc` calls as `ui_ipc`.
Only the allow-listed routes run, in-process against the FastAPI app with the
desktop's app token added here; the token never travels back. The UI secret is
added only for the two routes in `secret_elevated`. IPC runs in the desktop
renderer (it holds `window.ipc`): the backend publishes a `remote_ipc` frame on
`/wake-stream-all` and the renderer posts the answer to
`/remote/ui-ipc-result/{request_id}`. A card answer (`/mcp-approval-respond`)
never runs the HTTP route: it is the v1 remote answer, recorded as the device's.
The other card routes run with REMOTE_DEVICE set, so they record the device too.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextvars
import json
import logging
import re
import secrets
import time
import weakref
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlencode

from agentic import approval_mode, cards
from local_token_file import read_local_app_token
from remote import crypto as C
from remote import desktop_channel, rpc
from remote.session import FRAME_PLAINTEXT_MAX, PhoneSession

logger = logging.getLogger(__name__)

METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")
PATH_MAX = 2048
QUERY_MAX = 8192
CHUNK_MAX = 64 * 1024
UPLOAD_PART_MAX = 48 * 1024
UPLOAD_TOTAL_MAX = 32 * 1024 * 1024
UPLOAD_TTL_S = 600.0
IPC_TIMEOUT_S = 30.0
HTTP_TIMEOUT_S = 30.0
START_TIMEOUT_S = 60.0
MAX_STREAMS = 4
IPC_ARGS_MAX = 8
# Room left in a reply frame for its envelope (id, ok, status...).
REPLY_ROOM = FRAME_PLAINTEXT_MAX - 4096
# Set on every dispatched request; /wake-stream-all reads it so a browser's
# copy of the stream does not count as a desktop renderer.
REMOTE_HEADER = b"x-gamachine-remote"
# The paired device a dispatched request runs for; set only inside the request's
# own task, so no HTTP client can claim it. Card routes record it as the answerer.
REMOTE_DEVICE: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("remote_device", default=None)

_ID = r"[^/]+"

# (methods, path pattern). The path is the percent-decoded one `_normalise`
# returns, matched whole.
_ALLOW: List[Tuple[Tuple[str, ...], str]] = [
    # chat
    (("POST",), r"/chat-stream"),
    (("POST",), rf"/chat-stop/{_ID}"),
    (("POST",), rf"/conversations/{_ID}/side-stream"),
    (("GET",), r"/wake-stream-all"),
    (("GET",), rf"/conversations/{_ID}/turn-events"),
    (("POST",), rf"/conversations/{_ID}/compact"),
    (("GET",), r"/slash-commands"),
    (("GET",), r"/chat-title-setting"),
    (("POST",), r"/transcribe"),
    (("POST",), r"/transcribe/session"),
    (("POST",), rf"/transcribe/session/{_ID}"),
    (("POST",), rf"/transcribe/session/{_ID}/finish"),
    (("GET",), r"/transcribe/settings"),
    # conversations
    (("POST",), r"/conversations"),
    (("GET",), rf"/conversations/{_ID}"),
    (("GET",), rf"/conversations/{_ID}/(messages|model|context-usage)"),
    (("GET",), rf"/session-report/{_ID}/{_ID}"),
    (("PUT", "DELETE"), rf"/conversations/{_ID}"),
    (("POST", "DELETE"), rf"/conversations/{_ID}/side"),
    (("POST",), rf"/conversations/{_ID}/branch"),
    (("PUT",), rf"/conversations/{_ID}/hidden"),
    # workspace and files
    (("POST",), r"/save-workspace"),
    (("POST",), r"/remove-workspace"),
    (("GET",), rf"/(last-workspace|recent-workspaces)/{_ID}"),
    (("GET",), r"/health/workspace"),
    (("GET",), r"/lsp/diagnostics"),
    (("POST",), r"/lsp/(change|completion|hover|definition)"),
    # scene editor
    (("GET", "POST"), r"/scene-editor/.+"),
    # Unity
    (("GET",), r"/mcp/unity/(status|console)"),
    (("POST",), r"/mcp/unity/toggle"),
    (("POST",), r"/mcp-project-check"),
    # model lists (picking stays the v1 set_model / set_effort RPCs)
    (("GET",), r"/(available-models|effort-capabilities|usage/limits|cli-availability)"),
    (("GET",), rf"/(cli-models|get-ai-config|provider-ready)/{_ID}"),
    # cards and halt
    (("GET",), r"/mcp-pending"),
    (("POST",), rf"/(mcp-approval-respond|command-approval|question-answer)/{_ID}"),
    (("POST",), rf"/computer-use/halt/{_ID}"),
    # read-only status
    (("GET",), r"/approval-mode"),
    (("GET",), r"/computer-use/(settings|activity)"),
    (("GET",), r"/browser/settings"),
    (("GET",), r"/(me|health|health/auth|agent-guide|profile/stats)"),
]

# Checked before the allow-list and wins over it: `None` means every method.
# A prefix denies itself and everything under it.
_DENY: List[Tuple[Optional[Tuple[str, ...]], str]] = [
    (None, "/api-keys"),
    (None, "/cli-install"),
    (None, "/cli-login"),
    (None, "/profile/reset"),
    (None, "/computer-use/grants"),
    (None, "/computer-use/runtime"),
    (("POST", "PUT", "PATCH", "DELETE"), "/computer-use/settings"),
    (None, "/browser/runtime"),
    (("POST", "PUT", "PATCH", "DELETE"), "/browser/settings"),
    (None, "/remote"),
    (("POST", "PUT", "PATCH", "DELETE"), "/approval-mode"),
]

# The only routes that get the UI secret, and only for these methods.
_SECRET = [("POST", re.compile(rf"/mcp-approval-respond/{_ID}")),
           ("POST", re.compile(rf"/computer-use/halt/{_ID}"))]
# Answered here through the v1 card path, never by running the route.
_CARD_ANSWER = re.compile(rf"/mcp-approval-respond/({_ID})")

_ALLOW_RE = [(methods, re.compile(p)) for methods, p in _ALLOW]

# Work-parity channels (main/background.ts). Native dialogs, the app token,
# profile reset, workspace authorisation, remote pairing, approval mode and the
# drop-folder registration stay desktop-only.
IPC_ALLOW = frozenset({
    "read-file", "read-model-file", "read-image-file", "write-file",
    "create-file", "create-folder", "delete-file", "delete-entry", "rename-entry", "move-entry",
    "read-directory", "file-exists", "path-exists", "git-status", "workspace-info",
    "backend-workspace-path", "host-workspace-path",
    "terminal-spawn", "terminal-write", "terminal-resize",
    "computer-use-live", "browser-live",
})
# Channels whose first argument is `{op, ...}`: only these ops cross. The rest
# drive the desktop's own window (panel bounds, visibility, enable/settings).
IPC_OPS = {
    "browser-live": frozenset({"status", "halt", "answer-card"}),
    "computer-use-live": frozenset({"status", "halt", "halted"}),
}
# main -> renderer events a browser may subscribe to. Terminal events carry the
# terminal id in the channel name (`terminal-data-<id>`).
IPC_EVENTS = frozenset({"browser-live", "computer-use-live", "open-conversation"})
_IPC_EVENT_PREFIX = re.compile(r"(terminal-data|terminal-exit)-[A-Za-z0-9_-]{1,64}")

_UPLOAD_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_STREAM_ID = re.compile(r"[0-9a-f]{16}")
IPC_REQUEST_ID = re.compile(r"[0-9a-f]{16}")
_TEXT_TYPES = re.compile(r"^(text/|application/(json|javascript|xml|x-ndjson)|[^;]*\+json)")


def _normalise(path: Any) -> Optional[str]:
    """The decoded path the app would route on, or None when it is not a plain
    absolute path (scheme, host, `//`, backslash, dot segments, query part,
    control characters, double encoding)."""
    if not isinstance(path, str) or not path.startswith("/") or len(path) > PATH_MAX:
        return None
    if any(c in path for c in "\\?#"):
        return None
    try:
        decoded = unquote(path, errors="strict")
    except UnicodeDecodeError:
        return None
    if any(c in decoded for c in "\\?#%") or any(ord(c) < 0x20 or ord(c) == 0x7F for c in decoded):
        return None
    if any(seg in ("", ".", "..") for seg in decoded[1:].split("/")):
        return None
    return decoded


def _method(method: Any) -> Optional[str]:
    if not isinstance(method, str):
        return None
    m = method.upper()
    return m if m in METHODS else None


def _denied(method: str, path: str) -> bool:
    for methods, prefix in _DENY:
        if (methods is None or method in methods) and (path == prefix or path.startswith(prefix + "/")):
            return True
    return False


def http_allowed(method: Any, path: Any) -> bool:
    m, p = _method(method), _normalise(path)
    if m is None or p is None or _denied(m, p):
        return False
    return any(m in methods and rx.fullmatch(p) for methods, rx in _ALLOW_RE)


def secret_elevated(method: Any, path: Any) -> bool:
    if not http_allowed(method, path):
        return False
    m, p = _method(method), _normalise(path)
    return any(m == sm and rx.fullmatch(p) for sm, rx in _SECRET)


def ipc_allowed(channel: Any, args: Any) -> bool:
    if channel not in IPC_ALLOW:
        return False
    ops = IPC_OPS.get(channel)
    if ops is None:
        return True
    first = args[0] if isinstance(args, list) and args else None
    return isinstance(first, dict) and first.get("op") in ops


def ipc_event_allowed(channel: Any) -> bool:
    return isinstance(channel, str) and (channel in IPC_EVENTS or bool(_IPC_EVENT_PREFIX.fullmatch(channel)))


# Frame types a browser's copy of /wake-stream-all may carry; every other
# event is dropped. The command frames (desktop_channel.DESKTOP_ONLY_TYPES)
# are for the desktop renderer alone: a browser acting on `remote_message` or
# `remote_ipc` would send the phone's text again or run the IPC a second time.
BROWSER_FRAME_TYPES = frozenset({"title", desktop_channel.CARD_CLOSED_TYPE, desktop_channel.MODE_CHANGED_TYPE,
                                 desktop_channel.MODEL_CHANGED_TYPE})

# The desktop channel queues that belong to a remote session's copy of
# /wake-stream-all (the route marks them).
_remote_listeners: "weakref.WeakSet[asyncio.Queue]" = weakref.WeakSet()


def mark_remote_listener(q: asyncio.Queue) -> None:
    _remote_listeners.add(q)


def desktop_renderer_ready() -> bool:
    """A desktop renderer listens on /wake-stream-all. DesktopChannel counts
    every listener, and a browser's copy would make a call wait for nobody."""
    return any(q not in _remote_listeners for q in list(desktop_channel.CHANNEL._queues))


def _start_call(session: PhoneSession, app, scope: dict, body: bytes) -> "_AsgiCall":
    # The request's task copies the context as it is created.
    token = REMOTE_DEVICE.set(session.device_label)
    try:
        return _AsgiCall(app, scope, body)
    finally:
        REMOTE_DEVICE.reset(token)


def _too_big(obj: Any) -> bool:
    return len(C.compact_json(obj).encode("utf-8")) > REPLY_ROOM


class _AsgiCall:
    """One request run against the ASGI app, its response read message by
    message. httpx's ASGITransport buffers the whole body, which a stream that
    never ends (/wake-stream-all) cannot use."""

    def __init__(self, app, scope: dict, body: bytes):
        self._q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._gone = asyncio.Event()
        self._body: Optional[bytes] = body
        # The app raised: a body that stopped there is cut short, not complete.
        self.failed = False
        self.task = asyncio.get_running_loop().create_task(self._run(app, scope))

    async def _run(self, app, scope):
        async def receive():
            if self._body is not None:
                body, self._body = self._body, None
                return {"type": "http.request", "body": body, "more_body": False}
            await self._gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            await self._q.put(message)

        try:
            await app(scope, receive, send)
        except Exception:
            logger.exception("[remote] ui request %s %s failed", scope["method"], scope["path"])
            self.failed = True
        finally:
            try:
                self._q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def start(self) -> Tuple[int, str]:
        """Status and content type, once the app has started its response."""
        msg = await self._q.get()
        if msg is None or msg.get("type") != "http.response.start":
            return 500, "text/plain"
        ctype = ""
        for k, v in msg.get("headers") or []:
            if k.lower() == b"content-type":
                ctype = v.decode("latin-1")
        return int(msg["status"]), ctype

    async def chunks(self):
        while True:
            msg = await self._q.get()
            if msg is None:
                if self.failed:
                    raise rpc.RpcError("upstream_failed")
                return
            if msg.get("type") == "http.response.body":
                if msg.get("body"):
                    yield msg["body"]
                if not msg.get("more_body"):
                    return

    def close(self) -> None:
        self._gone.set()
        if not self.task.done():
            self.task.cancel()


class _SseFilter:
    """A browser's copy of `/wake-stream-all`: parses the event stream (CR, LF
    or CRLF line ends, data lines joined) and passes only the events whose
    frame type is in BROWSER_FRAME_TYPES, re-serialised; a comment-only block
    passes as a keepalive. Anything else, unparseable included, is dropped."""

    def __init__(self):
        self._buf = b""
        self._lines: List[bytes] = []

    def feed(self, data: bytes) -> bytes:
        self._buf += data
        out = []
        while True:
            cr, lf = self._buf.find(b"\r"), self._buf.find(b"\n")
            end = min(i for i in (cr, lf) if i >= 0) if cr >= 0 or lf >= 0 else -1
            if end < 0 or (end == cr and end == len(self._buf) - 1):
                break  # no line end yet, or a CR whose LF may still come
            line = self._buf[:end]
            self._buf = self._buf[end + (2 if self._buf[end:end + 2] == b"\r\n" else 1):]
            if line:
                self._lines.append(line)
            elif self._lines:
                out.append(self._event(self._lines))
                self._lines = []
        return b"".join(out)

    @staticmethod
    def _event(lines: List[bytes]) -> bytes:
        data = []
        for line in lines:
            if line.startswith(b":"):
                continue
            name, _, value = line.partition(b":")
            if name == b"data":
                data.append(value[1:] if value.startswith(b" ") else value)
        if not data:
            return b": keepalive\n\n" if all(line.startswith(b":") for line in lines) else b""
        try:
            frame = json.loads(b"\n".join(data).decode("utf-8"))
        except ValueError:
            return b""
        if not isinstance(frame, dict) or frame.get("type") not in BROWSER_FRAME_TYPES:
            return b""
        return b"data: " + json.dumps(frame).encode("utf-8") + b"\n\n"


class _Upload:
    __slots__ = ("data", "next_seq", "created", "done")

    def __init__(self, now: float):
        self.data = bytearray()
        self.next_seq = 0
        self.created = now
        self.done = False


class _SessionState:
    def __init__(self):
        self.streams: Dict[str, asyncio.Task] = {}
        self.uploads: Dict[str, _Upload] = {}


class UiDispatcher:
    def __init__(self, bridge, app=None, clock=time.monotonic):
        self.bridge = bridge
        self.app = app
        self.clock = clock
        self._state: "weakref.WeakKeyDictionary[PhoneSession, _SessionState]" = weakref.WeakKeyDictionary()
        self._pending: Dict[str, asyncio.Future] = {}
        self._subs: Dict[str, "weakref.WeakSet[PhoneSession]"] = {}
        self._tasks: set = set()

    # ── plumbing ───────────────────────────────────────────────────────
    def _asgi_app(self):
        # main.py hands the app over when it builds the bridge.
        if self.app is None:
            raise rpc.RpcError("unavailable")
        return self.app

    def _session_state(self, session: PhoneSession) -> _SessionState:
        state = self._state.get(session)
        if state is None:
            state = self._state[session] = _SessionState()
        return state

    def _expire_uploads(self, state: _SessionState) -> None:
        cutoff = self.clock() - UPLOAD_TTL_S
        for uid in [u for u, up in state.uploads.items() if up.created < cutoff]:
            del state.uploads[uid]

    def _request(self, session: PhoneSession, req: dict, kind: str) -> Tuple[dict, bytes]:
        method = _method(req.get("method"))
        path = _normalise(req.get("path"))
        shown = path if path is not None else "<bad path>"
        if method is None or path is None or not http_allowed(method, path):
            logger.info("[remote] %s %s %s %s refused", kind, session.device_label, method or "?", shown)
            raise rpc.RpcError("remote_forbidden", status=403)
        logger.info("[remote] %s %s %s %s", kind, session.device_label, method, path)
        query = req.get("query")
        if query is None:
            qs = ""
        elif (isinstance(query, str) and query.isascii() and query.isprintable() and "#" not in query
              and len(query) <= QUERY_MAX):
            qs = query
        elif isinstance(query, dict):
            for v in query.values():
                vals = v if isinstance(v, list) else [v]
                if not all(isinstance(x, (str, int, float)) for x in vals):
                    raise rpc.RpcError("bad_query")
            qs = urlencode({str(k): v for k, v in query.items()}, doseq=True)
            if len(qs) > QUERY_MAX:
                raise rpc.RpcError("bad_query")
        else:
            raise rpc.RpcError("bad_query")

        given = [k for k in ("body", "body_b64", "upload_id") if req.get(k) is not None]
        if len(given) > 1:
            raise rpc.RpcError("bad_body")
        ctype = req.get("content_type")
        if ctype is not None and (not isinstance(ctype, str) or len(ctype) > 200
                                  or not all(0x20 <= ord(c) < 0x7F for c in ctype)):
            raise rpc.RpcError("bad_content_type")
        body = b""
        if given == ["body"] and isinstance(req["body"], str):
            # The page sends text bodies (JSON included) as the text itself.
            body = req["body"].encode("utf-8")
            ctype = ctype or "text/plain;charset=utf-8"
        elif given == ["body"]:
            body = json.dumps(req["body"]).encode("utf-8")
            ctype = "application/json"
        elif given == ["body_b64"]:
            try:
                body = base64.b64decode(req["body_b64"], validate=True)
            except (binascii.Error, ValueError, TypeError):
                raise rpc.RpcError("bad_body")
            ctype = ctype or "application/octet-stream"
        elif given == ["upload_id"]:
            body = self._take_upload(session, req["upload_id"])
            ctype = ctype or "application/octet-stream"

        headers = [(b"host", b"127.0.0.1"), (b"content-length", str(len(body)).encode()), (REMOTE_HEADER, b"1")]
        if ctype:
            headers.append((b"content-type", ctype.encode("latin-1")))
        token = read_local_app_token()
        if token:
            headers.append((b"x-session-token", token.encode("utf-8")))
        if secret_elevated(method, path):
            secret = approval_mode.ui_secret()
            if secret:
                headers.append((b"x-gamachine-ui-secret", secret.encode("utf-8")))
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path, "raw_path": quote(path).encode("ascii"),
            "root_path": "", "query_string": qs.encode("ascii"), "headers": headers, "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 80),
        }
        return scope, body

    # ── HTTP ───────────────────────────────────────────────────────────
    async def http(self, session: PhoneSession, req: dict, rid):
        app = self._asgi_app()
        scope, body = self._request(session, req, "ui_http")
        card = _CARD_ANSWER.fullmatch(scope["path"]) if scope["method"] == "POST" else None
        if card is not None:
            return self._answer_card(session, card.group(1), body)
        call = _start_call(session, app, scope, body)
        try:
            status, ctype, data = await asyncio.wait_for(self._read_reply(call), HTTP_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise rpc.RpcError("timeout")
        finally:
            call.close()
        out: Dict[str, Any] = {"status": status, "content_type": ctype}
        text = None
        if _TEXT_TYPES.match(ctype.lower()):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                pass
        if text is not None:
            out["body"] = text
        elif data:
            out["body_b64"] = base64.b64encode(data).decode("ascii")
        if _too_big(out):
            # One reply frame cannot carry it; ui_stream_open can, in chunks.
            raise rpc.RpcError("too_large")
        return out

    @staticmethod
    async def _read_reply(call: _AsgiCall) -> Tuple[int, str, bytes]:
        status, ctype = await call.start()
        if ctype.split(";")[0].strip().lower() == "text/event-stream":
            # It may never end; such routes are read through ui_stream_open.
            raise rpc.RpcError("stream_only")
        data = bytearray()
        async for chunk in call.chunks():
            data += chunk
            # The reply cannot be smaller than the raw body: stop reading.
            if len(data) > REPLY_ROOM:
                raise rpc.RpcError("too_large")
        return status, ctype, bytes(data)

    def _answer_card(self, session: PhoneSession, card_id: str, body: bytes) -> dict:
        """POST /mcp-approval-respond/{id} from a browser, answered like the v1
        answer_card RPC (the ledger records the device) with the route's reply."""
        try:
            payload = json.loads(body) if body else None
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return {"status": 422, "content_type": "application/json", "body": json.dumps({"detail": "bad_body"})}
        # Only `true` approves, as in the route: a broken answer is a rejection.
        decision = "approve" if payload.get("approved") is True else "reject"
        card = cards.get(card_id)
        res = rpc.answer_card_as(session.device_label, card, decision) if card is not None else {"status": "not_found"}
        if res.get("status") == "ok":
            res = {"status": "ok"}
        elif res.get("status") == "not_found":
            res = {"status": "gate_not_found"}
        return {"status": 200, "content_type": "application/json", "body": json.dumps(res)}

    async def stream_open(self, session: PhoneSession, req: dict, rid):
        app = self._asgi_app()
        state = self._session_state(session)
        if len(state.streams) >= MAX_STREAMS:
            raise rpc.RpcError("too_many_streams")
        scope, body = self._request(session, req, "ui_stream_open")
        if scope["method"] == "POST" and _CARD_ANSWER.fullmatch(scope["path"]):
            raise rpc.RpcError("remote_forbidden", status=403)
        stream_id = secrets.token_hex(8)
        me = asyncio.current_task()
        state.streams[stream_id] = me
        call = _start_call(session, app, scope, body)
        sse = _SseFilter() if scope["path"] == "/wake-stream-all" else None
        error = None
        try:
            try:
                status, ctype = await asyncio.wait_for(call.start(), START_TIMEOUT_S)
            except asyncio.TimeoutError:
                raise rpc.RpcError("timeout")
            # The reply goes first: the page opens its ReadableStream on it.
            await session.reply({"id": rid, "ok": True,
                                 "result": {"stream_id": stream_id, "status": status, "content_type": ctype}})
            seq = 0
            try:
                async for chunk in call.chunks():
                    if sse is not None:
                        chunk = sse.feed(chunk)
                    for i in range(0, len(chunk), CHUNK_MAX):
                        piece = chunk[i:i + CHUNK_MAX]
                        if not await session.send({"type": "ui_stream_chunk", "stream_id": stream_id, "seq": seq,
                                                   "data_b64": base64.b64encode(piece).decode("ascii")}):
                            return rpc._REPLIED
                        seq += 1
            except rpc.RpcError as exc:
                error = exc.error
            except Exception:
                logger.exception("[remote] ui stream %s failed", scope["path"])
                error = "internal"
            end = {"type": "ui_stream_end", "stream_id": stream_id}
            if error:
                end["error"] = error
            await session.send(end)
            return rpc._REPLIED
        finally:
            call.close()
            if state.streams.get(stream_id) is me:
                del state.streams[stream_id]

    async def stream_close(self, session: PhoneSession, req: dict, rid):
        stream_id = req.get("stream_id")
        if not isinstance(stream_id, str) or not _STREAM_ID.fullmatch(stream_id):
            raise rpc.RpcError("bad_stream_id")
        task = self._session_state(session).streams.pop(stream_id, None)
        if task is not None:
            task.cancel()
        return {}

    # ── uploads ────────────────────────────────────────────────────────
    async def upload_part(self, session: PhoneSession, req: dict, rid):
        uid, seq, last = req.get("upload_id"), req.get("seq"), req.get("last")
        if not isinstance(uid, str) or not _UPLOAD_ID.fullmatch(uid):
            raise rpc.RpcError("bad_upload_id")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0 or not isinstance(last, bool):
            raise rpc.RpcError("bad_part")
        try:
            data = base64.b64decode(req.get("data_b64") or "", validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise rpc.RpcError("bad_part")
        if len(data) > UPLOAD_PART_MAX:
            raise rpc.RpcError("part_too_large")
        state = self._session_state(session)
        self._expire_uploads(state)
        up = state.uploads.get(uid)
        if up is None:
            if seq != 0:
                raise rpc.RpcError("unknown_upload")
            up = state.uploads[uid] = _Upload(self.clock())
        if up.done:
            raise rpc.RpcError("upload_finished")
        if seq != up.next_seq:
            raise rpc.RpcError("bad_seq", expected=up.next_seq)
        held = sum(len(u.data) for u in state.uploads.values())
        if held + len(data) > UPLOAD_TOTAL_MAX:
            del state.uploads[uid]
            raise rpc.RpcError("upload_too_large")
        up.data += data
        up.next_seq += 1
        up.done = last
        return {"received": len(up.data)}

    def _take_upload(self, session: PhoneSession, uid: Any) -> bytes:
        if not isinstance(uid, str) or not _UPLOAD_ID.fullmatch(uid):
            raise rpc.RpcError("bad_upload_id")
        state = self._session_state(session)
        self._expire_uploads(state)
        up = state.uploads.get(uid)
        if up is None or not up.done:
            raise rpc.RpcError("unknown_upload")
        del state.uploads[uid]
        return bytes(up.data)

    # ── IPC through the desktop renderer ───────────────────────────────
    async def ipc(self, session: PhoneSession, req: dict, rid):
        # `args` is the whole argument list; a frame without it carries the
        # single argument as `payload`.
        channel, args, payload = req.get("channel"), req.get("args"), req.get("payload")
        if args is None:
            args = [] if payload is None else [payload]
        if not isinstance(channel, str) or not isinstance(args, list) or len(args) > IPC_ARGS_MAX:
            raise rpc.RpcError("bad_request")
        if not ipc_allowed(channel, args):
            logger.info("[remote] ui_ipc %s %s refused", session.device_label, channel[:64])
            raise rpc.RpcError("remote_unavailable")
        logger.info("[remote] ui_ipc %s %s", session.device_label, channel)
        if not desktop_renderer_ready():
            raise rpc.RpcError("desktop_not_ready")
        if req.get("upload_id") is not None:
            # An argument too large for one frame (a file being written)
            # arrives as an upload and takes its place here.
            index = req.get("upload_arg_index")
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(args):
                raise rpc.RpcError("bad_upload_arg_index")
            data = self._take_upload(session, req["upload_id"])
            try:
                args = [*args[:index], data.decode("utf-8"), *args[index + 1:]]
            except UnicodeDecodeError:
                raise rpc.RpcError("bad_upload")
            if not ipc_allowed(channel, args):
                raise rpc.RpcError("remote_unavailable")
        request_id = secrets.token_hex(8)
        fut = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        try:
            if not desktop_channel.publish_ipc(request_id, channel, args):
                raise rpc.RpcError("desktop_not_ready")
            try:
                answer = await asyncio.wait_for(fut, IPC_TIMEOUT_S)
            except asyncio.TimeoutError:
                raise rpc.RpcError("desktop_not_ready")
        finally:
            self._pending.pop(request_id, None)
        if not answer.get("ok"):
            error = answer.get("error")
            raise rpc.RpcError("ipc_failed", message=str(error)[:500] if error is not None else "")
        result = answer.get("result")
        if _too_big({"result": result}):
            raise rpc.RpcError("too_large")
        return result

    def resolve_ipc(self, request_id: str, answer: Any) -> bool:
        """The renderer's answer to a `remote_ipc` frame; False for an unknown
        or already answered id."""
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(answer if isinstance(answer, dict) else {"ok": False, "error": "bad_answer"})
        return True

    async def ipc_subscribe(self, session: PhoneSession, req: dict, rid):
        channel = req.get("channel")
        if not ipc_event_allowed(channel):
            raise rpc.RpcError("remote_unavailable")
        logger.info("[remote] ui_ipc_subscribe %s %s", session.device_label, channel)
        if not desktop_renderer_ready() or not desktop_channel.publish_ipc_subscribe(channel):
            raise rpc.RpcError("desktop_not_ready")
        self._subs.setdefault(channel, weakref.WeakSet()).add(session)
        return {}

    def subscribed_channels(self) -> List[str]:
        """The event channels a live session still listens to; a desktop
        renderer that reloaded attaches to them again."""
        return sorted(ch for ch, subs in self._subs.items() if any(not s.closed for s in list(subs)))

    def forward_event(self, channel: str, args: list) -> bool:
        """Sends a renderer event to the sessions subscribed to its channel.
        False when none is left, so the renderer drops its listener."""
        subs = self._subs.get(channel)
        live = [s for s in (subs or ()) if not s.closed]
        if not live:
            self._subs.pop(channel, None)
            return False
        frame = {"type": "ui_ipc_event", "channel": channel, "args": args}
        if _too_big(frame):
            logger.warning("[remote] ui_ipc_event on %s too large; dropped", channel)
            return True
        loop = asyncio.get_running_loop()
        for session in live:
            task = loop.create_task(session.send(frame))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return True
