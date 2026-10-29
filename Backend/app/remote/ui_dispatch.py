"""The web remote's UI dispatcher (docs/remote-control-web.md, "Desktop side").

A paired browser runs the desktop's own renderer; its HTTP calls arrive as
`ui_http` / `ui_stream_open` frames and its `window.ipc` calls as `ui_ipc`.
Only the allow-listed routes run, in-process against the FastAPI app with the
desktop's app token added here; the token never travels back. The UI secret is
added only for the two routes in `secret_elevated`. IPC runs in the desktop
renderer (it holds `window.ipc`): the backend publishes a `remote_ipc` frame on
`/wake-stream-all` and the renderer posts the answer to
`/remote/ui-ipc-result/{request_id}`.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import secrets
import sys
import time
import weakref
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlencode

from agentic import approval_mode
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
START_TIMEOUT_S = 60.0
MAX_STREAMS = 4
IPC_ARGS_MAX = 8
# Room left in a reply frame for its envelope (id, ok, status...).
REPLY_ROOM = FRAME_PLAINTEXT_MAX - 4096

_ID = r"[^/]+"

# (methods, path pattern). The path is the percent-decoded one `_normalise`
# returns, matched whole.
_ALLOW: List[Tuple[Tuple[str, ...], str]] = [
    # chat
    (("POST",), r"/chat-stream"),
    (("POST",), rf"/chat-stop/{_ID}"),
    (("POST",), rf"/conversations/{_ID}/side-stream"),
    (("GET",), rf"/conversations/{_ID}/wake-stream"),
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


def _ui_secret() -> str:
    # approval_mode keeps the secret private and has no getter; read it under
    # its lock. Empty means none is configured, and the route then refuses.
    with approval_mode._LOCK:
        return approval_mode._ui_secret.decode("utf-8")


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
    """Drops the desktop-only command frames from a `/wake-stream-all` copy:
    a browser renderer acting on `remote_message` or `remote_ipc` would send
    the phone's text again or run the IPC a second time."""

    def __init__(self):
        self._buf = b""

    def feed(self, data: bytes) -> bytes:
        self._buf += data
        events = self._buf.split(b"\n\n")
        self._buf = events.pop()
        return b"".join(e + b"\n\n" for e in events if not self._desktop_only(e))

    @staticmethod
    def _desktop_only(event: bytes) -> bool:
        for line in event.split(b"\n"):
            if line.startswith(b"data:"):
                try:
                    frame = json.loads(line[5:])
                except ValueError:
                    return False
                return isinstance(frame, dict) and frame.get("type") in desktop_channel.DESKTOP_ONLY_TYPES
        return False


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
        if self.app is not None:
            return self.app
        # main.py runs as __main__ (frozen build) or as `main` (uvicorn
        # reload); the module that built this bridge holds the app.
        for name in ("__main__", "main"):
            mod = sys.modules.get(name)
            if mod is not None and getattr(mod, "remote_bridge", None) is self.bridge:
                app = getattr(mod, "app", None)
                if app is not None:
                    self.app = app
                    return app
        raise rpc.RpcError("unavailable")

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
        if given == ["body"]:
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

        headers = [(b"host", b"127.0.0.1"), (b"content-length", str(len(body)).encode())]
        if ctype:
            headers.append((b"content-type", ctype.encode("latin-1")))
        token = read_local_app_token()
        if token:
            headers.append((b"x-session-token", token.encode("utf-8")))
        if secret_elevated(method, path):
            secret = _ui_secret()
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
        call = _AsgiCall(app, scope, body)
        try:
            status, ctype = await call.start()
            data = b"".join([c async for c in call.chunks()])
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
            raise rpc.RpcError("too_large", size=len(data))
        return out

    async def stream_open(self, session: PhoneSession, req: dict, rid):
        app = self._asgi_app()
        state = self._session_state(session)
        if len(state.streams) >= MAX_STREAMS:
            raise rpc.RpcError("too_many_streams")
        scope, body = self._request(session, req, "ui_stream_open")
        stream_id = secrets.token_hex(8)
        me = asyncio.current_task()
        state.streams[stream_id] = me
        call = _AsgiCall(app, scope, body)
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
        if not desktop_channel.publish_ipc_subscribe(channel):
            raise rpc.RpcError("desktop_not_ready")
        self._subs.setdefault(channel, weakref.WeakSet()).add(session)
        return {}

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
