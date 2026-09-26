"""
Unity MCP Client — unity-mcp sunucusuna MCP protokolüyle bağlanır.

Tool listesi ve tanımları sunucudan dinamik olarak çekilir.
Elle wrapper yazmaya gerek yok — unity-mcp'deki 40+ tool otomatik gelir.

One long-lived MCP session lives on a dedicated event-loop thread (closed-loop.md
§4). It used to be a new streamable-HTTP session plus `initialize` per call, and
the sync wrapper fell back to `asyncio.run` with no timeout, so a stuck call held
an agent turn forever. Now every call has a timeout, a dead session is dropped
and reopened on the next call, and connection failures surface as one Turkish
sentence instead of an `ExceptionGroup` repr.
"""
import asyncio
import concurrent.futures
import contextlib
import contextvars
import json
import logging
import secrets
import threading
from typing import Any, Callable, Dict, List, Optional

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import (CONNECTION_CLOSED, INVALID_REQUEST, REQUEST_TIMEOUT,
                       InputRequiredResult, ToolListChangedNotification)

logger = logging.getLogger(__name__)

# Tool groups sent to function-calling providers. `playtest` (game_hooks,
# play_session, play_step, play_capture, run_playtest) is enabled by default on
# the server next to `core` (tool_registry.DEFAULT_ENABLED_GROUPS).
EXPORTED_GROUPS = ("core", "playtest")
# The _meta key the unity-mcp server reads a call's owning chat from
# (GAMACHINE_CONVERSATION_META_KEY in unity-mcp/Server/src/core/constants.py).
CONVERSATION_META_KEY = "gamachine_conversation"

# A write call waits in the server's approval gate for up to 10 s (POST) + 150 s
# (card) in step mode, so the call budget must be larger or every slow click
# would read as a timeout; the rest is left for the tool itself to run.
CALL_TIMEOUT_S = 240.0
CONNECT_TIMEOUT_S = 15.0
LIST_TIMEOUT_S = 15.0
# Read timeout of the HTTP client: must outlast CALL_TIMEOUT_S, or a slow call
# would fail as a transport read error instead of the MCP request timeout.
# 300 s is what mcp 1.x streamablehttp_client used by default.
HTTP_READ_TIMEOUT_S = 300.0

_UNREACHABLE_MSG = ("Unity MCP sunucusuna bağlanılamadı (kapalı ya da yeniden başlıyor). "
                    "Unity MCP anahtarı açık mı, Unity Editor çalışıyor mu? Sunucu ayağa "
                    "kalkınca sonraki çağrı kendiliğinden yeniden bağlanır.")
_CONNECTION_LOST_MSG = ("Unity MCP bağlantısı çağrı sürerken koptu; çağrının Unity'de "
                        "çalışıp çalışmadığı bilinmiyor. Sonraki çağrı yeniden bağlanır.")
_SESSION_LOST_MSG = ("Unity MCP sunucusu yeniden başlamış; yeni oturum açıldı ama sunucu "
                     "onu da tanımadı.")
_INPUT_REQUIRED_MSG = ("Unity MCP aracı '{name}' tamamlanmadı: sunucu ek girdi istedi "
                       "(input_required) ve Gamachine bu isteği yanıtlayamıyor.")
# A call that timed out may have passed its approval card and run (or still be
# running) in Unity; "zaman aşımı" alone read as "nothing happened" and invited
# a blind retry of a write (audit finding ambiguous-outcome-message, 25 Sep 2026).
_CALL_TIMEOUT_MSG = ("Unity MCP çağrısı {seconds} sn içinde bitmedi (zaman aşımı); sonucu "
                     "bilinmiyor: Unity işlemi uygulamış olabilir. Tekrar denemeden önce "
                     "Unity'deki durumu kontrol et.")
_LIST_TIMEOUT_MSG = "Unity MCP çağrısı {seconds} sn içinde bitmedi (zaman aşımı)."


class UnityMCPError(RuntimeError):
    """A failure already phrased for the user."""


class _RefreshAbandoned(UnityMCPError):
    """A refresh from before close() tried to open a session after it."""


class _SessionLost(UnityMCPError):
    """The server no longer knows our session and did not run the request."""

    def __init__(self):
        super().__init__(_SESSION_LOST_MSG)


# Proof that an error came from the server's own session-loss 404 on a POST
# carrying our session id, or from any other 404 there. Minted per process so
# no server answer can carry them.
_SESSION_LOST_MARK = secrets.token_hex(16)
_SESSION_LOST_KEY = "gamachine_http_404"
_UNKNOWN_404_MARK = secrets.token_hex(16)
_UNKNOWN_404_KEY = "gamachine_http_404_unknown"
# The server's own session-loss 404 messages (mcp 2.2.0: session manager
# "Session not found" for an unknown id, transport "Not Found: Session has
# been terminated" and "Not Found: Invalid or expired session ID"). Every one
# is answered before the request reaches a handler.
_SERVER_404_TEXTS = ("Session not found", "Not Found: Session has been terminated",
                     "Not Found: Invalid or expired session ID")
# The exact bytes and Content-Type the server sends for those, measured 26 Sep
# 2026 from mcp 2.2.0's _error_response / _create_error_response over HTTP
# (uvicorn) and rendered in the live Unity MCP server's own environment
# (mcp 2.2.0, starlette 1.7.0, pydantic 2.13.5): compact JSON, no BOM, no
# whitespace, no charset parameter. Matched byte for byte: json.loads also
# accepts a BOM, surrounding whitespace or reordered keys, none of which the
# server sends, so any of them means some other peer wrote the 404.
_SERVER_404_CONTENT_TYPE = "application/json"
_SERVER_404_BODIES = frozenset(
    b'{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"%s"}}' % text.encode("ascii")
    for text in _SERVER_404_TEXTS)
_CALL_404_UNKNOWN_MSG = ("Unity MCP sunucusu çağrıyı HTTP 404 ile yanıtladı, ama bu sunucunun "
                         "'oturum bulunamadı' yanıtı değil; çağrının Unity'de çalışıp çalışmadığı "
                         "bilinmiyor. Tekrar denemeden önce Unity'deki durumu kontrol et.")


def _is_server_session_loss_body(content_type: str, raw: bytes) -> bool:
    """True only for the exact response mcp 2.2.0 answers a lost session with
    (_SERVER_404_BODIES under _SERVER_404_CONTENT_TYPE), byte for byte."""
    return content_type == _SERVER_404_CONTENT_TYPE and bytes(raw) in _SERVER_404_BODIES


class _SessionLossTransport(httpx2.AsyncBaseTransport):
    """Classifies every HTTP 404 answering a POST that carried an Mcp-Session-Id.

    The server's session lookup fails before dispatch with a fixed JSON-RPC
    body (_is_server_session_loss_body), so such a request provably did not
    run - the one failure where retrying a mutation is safe. That body is
    replaced with an error carrying _SESSION_LOST_MARK, and only that mark
    lets the client retry. The SDK hands a 404's JSON-RPC body to the caller
    verbatim, so the retry used to key on message text (audit finding
    missed-session-recovery), and then on the status alone.

    Any other 404 (a proxy, a routing failure after the handler ran: the
    verification round of 26 Sep 2026 saw a write run twice that way) says
    nothing about whether the call ran. Its body is replaced with an error
    carrying _UNKNOWN_404_MARK: reported as an unknown outcome, never retried.
    """

    def __init__(self, inner: httpx2.AsyncBaseTransport):
        self._inner = inner

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await self._inner.handle_async_request(request)
        if (response.status_code != 404 or request.method != "POST"
                or not request.headers.get("mcp-session-id")):
            return response
        try:
            message = json.loads(request.content)
        except (httpx2.RequestNotRead, ValueError, TypeError):
            return response
        if not isinstance(message, dict) or message.get("id") is None:
            return response  # a notification; the SDK expects no answer to it
        raw = await response.aread()
        await response.aclose()
        # aread() undoes a Content-Encoding; the server never sets one, so a
        # decoded body did not come from it, however well it matches.
        lost = (response.headers.get("content-encoding") is None
                and _is_server_session_loss_body(response.headers.get("content-type", ""), raw))
        logger.info("[UnityMCP] HTTP 404 on session POST %r (%s): %r", message.get("method"),
                    "session lost" if lost else "outcome unknown", raw[:200])
        if lost:
            error = {"code": INVALID_REQUEST, "message": "Session not found (HTTP 404)",
                     "data": {_SESSION_LOST_KEY: _SESSION_LOST_MARK}}
        else:
            error = {"code": INVALID_REQUEST, "message": "HTTP 404, outcome unknown",
                     "data": {_UNKNOWN_404_KEY: _UNKNOWN_404_MARK}}
        body = {"jsonrpc": "2.0", "id": message["id"], "error": error}
        return httpx2.Response(404, headers={"content-type": "application/json"},
                               content=json.dumps(body).encode("utf-8"), request=request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _is_session_lost(exc: BaseException, observes_http: bool) -> bool:
    """True only when the request was refused by the server's session-loss 404 (pre-dispatch).

    With the real transport (`observes_http`) only _SessionLossTransport's mark
    counts, and it marks only the server's exact session-loss body; an SDK or
    server wording change can narrow the match (the 404 then reads as an
    unknown outcome, not retried) but never widen it. A factory without HTTP (test doubles)
    has only the error to go by: the server's own 404 texts. The SDK's
    synthesized "Session terminated" is not among them; with the real
    transport it never occurs, as every such 404 body is replaced.
    """
    if not isinstance(exc, MCPError):
        return False
    error = getattr(exc, "error", None)
    if observes_http:
        data = getattr(error, "data", None)
        return isinstance(data, dict) and data.get(_SESSION_LOST_KEY) == _SESSION_LOST_MARK
    return (getattr(error, "code", None) == INVALID_REQUEST
            and getattr(error, "message", None) in _SERVER_404_TEXTS)


def _is_unknown_404(exc: BaseException) -> bool:
    """A 404 on a session POST that was not the server's session-loss answer."""
    data = getattr(getattr(exc, "error", None), "data", None)
    return (isinstance(exc, MCPError) and isinstance(data, dict)
            and data.get(_UNKNOWN_404_KEY) == _UNKNOWN_404_MARK)


def _endpoint() -> tuple[str, dict]:
    """MCP transport adresi — her çağrıda yeniden hesaplanır.

    Sabit olamaz: URL, sunucunun her başlatılışında yenilenen paylaşımlı sırrı
    yol segmentinde taşıyor (bkz. unity_mcp_manager.mcp_url — sır header yerine
    URL'de çünkü hedef CLI'ların hepsinde `headers` alanı desteklenmiyor).
    Modül import anında hesaplansaydı sunucu yeniden başladığında bayat kalırdı.
    """
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    url = unity_mcp_manager.mcp_url(host="127.0.0.1")
    if not url:
        raise UnityMCPError(
            "Unity MCP sunucusu kapalı ya da paylaşımlı sır elimizde değil "
            "(sunucuyu biz başlatmadık) — MCP transport'una bağlanılamaz."
        )
    # Sır başlıkta: URL'de taşımak onu logladığımız her yere sızdırıyordu.
    return url, unity_mcp_manager.api_headers()

# Cache — toggle açıldığında doldurulur
_cached_tools: List[Dict] = []
_cached_functions: Dict[str, Any] = {}
# unload_unity_tools() bumps _generation. The cache is served only while
# _cached_generation matches it, so a refresh that started before an unload and
# finishes after it cannot bring the tools back (external audit 2026-09-25).
_generation = 0
_cached_generation = 0
_cache_lock = threading.Lock()
_refresh_epoch: "contextvars.ContextVar[Optional[int]]" = contextvars.ContextVar(
    "unity_mcp_refresh_epoch", default=None)


def _leaf_exceptions(exc: BaseException) -> List[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        leaves: List[BaseException] = []
        for inner in exc.exceptions:
            leaves.extend(_leaf_exceptions(inner))
        return leaves
    return [exc]


def _describe_failure(exc: BaseException) -> str:
    """One readable sentence for a transport failure, ExceptionGroup unwrapped."""
    leaves = _leaf_exceptions(exc)
    for leaf in leaves:
        if isinstance(leaf, UnityMCPError):
            return str(leaf)
    for leaf in leaves:
        if isinstance(leaf, (httpx2.ConnectError, httpx2.ConnectTimeout, ConnectionError)):
            return _UNREACHABLE_MSG
    for leaf in leaves:
        if isinstance(leaf, (TimeoutError, asyncio.TimeoutError, httpx2.TimeoutException)):
            return "Unity MCP yanıt vermedi (zaman aşımı)."
    first = leaves[0] if leaves else exc
    return f"Unity MCP hatası: {type(first).__name__}: {first}"


def _default_session_factory(message_handler):
    """Opens the real transport. Tests inject a factory with the same shape."""
    @contextlib.asynccontextmanager
    async def _open():
        url, headers = _endpoint()
        timeout = httpx2.Timeout(CONNECT_TIMEOUT_S, read=HTTP_READ_TIMEOUT_S)
        # An explicit transport also drops env proxies, which a 127.0.0.1
        # server never wants.
        transport = _SessionLossTransport(httpx2.AsyncHTTPTransport())
        async with httpx2.AsyncClient(headers=headers, timeout=timeout,
                                      transport=transport) as http:
            async with streamable_http_client(url, http_client=http) as (read, write):
                async with ClientSession(read, write, message_handler=message_handler) as session:
                    yield session, (url, tuple(sorted(headers.items())))
    return _open()


# The client reads this to decide how a lost session is recognized.
_default_session_factory.observes_http_404 = True


def _endpoint_key() -> Optional[tuple]:
    try:
        url, headers = _endpoint()
    except Exception:
        return None
    return (url, tuple(sorted(headers.items())))


class _Connection:
    """One MCP session, the task that owns its transport, and its callers.

    A failed call marks only its own connection broken. Calls still in flight on
    it finish there; new calls get a fresh connection; the broken one is closed
    when its last caller leaves. Closing it at once used to cut off a peer call,
    e.g. a write waiting minutes on an approval card, which then reported a
    failure although Unity could still run it.
    """

    def __init__(self) -> None:
        self.session = None
        self.key: Optional[tuple] = None
        self.owner: Optional[asyncio.Task] = None
        self.stop = asyncio.Event()
        self.active = 0
        self.broken = False
        self.error: Optional[BaseException] = None

    def reusable(self) -> bool:
        return (self.session is not None and not self.broken
                and self.owner is not None and not self.owner.done())


class _UnityMCPClient:
    """One MCP session on one loop thread; every public call is bounded in time."""

    def __init__(self, session_factory: Callable = _default_session_factory,
                 endpoint_key: Callable[[], Optional[tuple]] = _endpoint_key):
        self._factory = session_factory
        self._observes_http = getattr(session_factory, "observes_http_404", False)
        self._endpoint_key = endpoint_key
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._conn: Optional[_Connection] = None
        self._retired: set = set()
        self._connect_lock: Optional[asyncio.Lock] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._refresh_again = False
        # Bumped by close(). A refresh captures it before handing off to its
        # worker thread, and a list_tools from that thread may not open a new
        # session once it has moved on: close() means the user turned the
        # tools off, and a late refresh reconnecting would undo that.
        self._epoch = 0
        self.on_tools_changed: Optional[Callable[[], None]] = None

    # ── loop thread ──────────────────────────────────────────────────────
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run():
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            thread = threading.Thread(target=_run, name="unity-mcp-client", daemon=True)
            thread.start()
            ready.wait(5)
            self._loop, self._thread = loop, thread
            return loop

    def run(self, coro, timeout: float, timeout_message: Optional[str] = None):
        """Runs `coro` on the client loop from any thread; raises on timeout."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise UnityMCPError(timeout_message
                                or _LIST_TIMEOUT_MSG.format(seconds=f"{timeout:g}"))

    # ── session lifecycle (runs on the client loop) ──────────────────────
    async def _session_owner(self, conn: _Connection, ready: asyncio.Future) -> None:
        # anyio cancel scopes must be exited by the task that entered them, so
        # one task owns the transport for its whole life; callers only send.
        try:
            async with self._factory(self._on_message) as (session, key):
                await session.initialize()
                conn.session, conn.key = session, key
                if not ready.done():
                    ready.set_result(session)
                await conn.stop.wait()
        except Exception as exc:  # ExceptionGroup included; reported to the waiter
            conn.error = exc
            if not ready.done():
                ready.set_exception(exc)
            else:
                logger.warning("[UnityMCP] session closed: %s", _describe_failure(exc))
        finally:
            conn.session = None

    async def _acquire(self, refresh_on_connect: bool = True, epoch: Optional[int] = None):
        """Returns (connection, session) with the caller counted as in flight."""
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if epoch is not None and epoch != self._epoch:
                raise _RefreshAbandoned("Unity MCP tools were unloaded; refresh abandoned.")
            current = self._endpoint_key()
            conn = self._conn
            if conn is not None and conn.reusable():
                if current is None or current == conn.key:
                    conn.active += 1
                    return conn, conn.session
                logger.info("[UnityMCP] server endpoint changed; reconnecting")
            await self._retire(conn)
            loop = asyncio.get_running_loop()
            ready: asyncio.Future = loop.create_future()
            conn = _Connection()
            self._conn = conn
            conn.owner = loop.create_task(self._session_owner(conn, ready))
            try:
                session = await asyncio.wait_for(ready, timeout=CONNECT_TIMEOUT_S)
            except BaseException:
                if self._conn is conn:
                    self._conn = None
                await self._shutdown(conn)
                raise
            if refresh_on_connect:
                self._request_refresh()
            conn.active += 1
            return conn, session

    async def _await_on(self, conn: _Connection, coro):
        """Awaits `coro` unless the connection's transport dies first.

        Measured 25 Sep 2026: with the server down, a call's POST failed and the
        transport task died at once, yet the call kept waiting for its full read
        timeout (240 s in production) before reporting anything.
        """
        task = asyncio.ensure_future(coro)
        owner = conn.owner
        try:
            if owner is None:
                return await task
            await asyncio.wait({task, owner}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            task.cancel()
            raise
        if task.done():
            exc = None if task.cancelled() else task.exception()
            if (isinstance(exc, MCPError) and exc.code == CONNECTION_CLOSED
                    and not owner.done()):
                # mcp 2.x fails the pending request with CONNECTION_CLOSED as the
                # transport dies, before its task has finished with the cause.
                # A refused connect means the request was never sent, which the
                # user should read as "unreachable", not "dropped mid-call"
                # (measured 25 Sep 2026 with the server stopped).
                await asyncio.wait({owner}, timeout=2)
            if (isinstance(exc, MCPError) and exc.code == CONNECTION_CLOSED
                    and conn.error is not None
                    and _describe_failure(conn.error) == _UNREACHABLE_MSG):
                raise UnityMCPError(_UNREACHABLE_MSG) from exc
            return task.result()
        task.cancel()
        await asyncio.wait({task})
        raise UnityMCPError(_describe_failure(conn.error) if conn.error is not None
                            else _CONNECTION_LOST_MSG)

    async def _release(self, conn: _Connection, failed: bool) -> None:
        if failed:
            conn.broken = True
        conn.active -= 1
        if conn.active > 0 or (conn is self._conn and not conn.broken):
            return
        if self._conn is conn:
            self._conn = None
        self._retired.discard(conn)
        await self._shutdown(conn)

    async def _retire(self, conn: Optional[_Connection]) -> None:
        """Stops handing `conn` out; closes it now or when its last call ends."""
        if conn is None:
            return
        if self._conn is conn:
            self._conn = None
        if conn.active > 0:
            self._retired.add(conn)
        else:
            await self._shutdown(conn)

    @staticmethod
    async def _shutdown(conn: _Connection) -> None:
        conn.stop.set()
        owner = conn.owner
        if owner is not None and not owner.done():
            try:
                await asyncio.wait_for(owner, timeout=5)
            except BaseException:
                owner.cancel()
        conn.session = None

    async def _close_all(self) -> None:
        self._epoch += 1
        self._refresh_again = False
        if self._refresh_task is not None and not self._refresh_task.done():
            # Stops a refresh that has not reached its worker thread yet; one
            # already there is held off by the epoch check in _acquire.
            self._refresh_task.cancel()
        self._refresh_task = None
        conns = [self._conn, *self._retired]
        self._conn = None
        self._retired.clear()
        for conn in conns:
            if conn is not None:
                await self._shutdown(conn)

    async def _on_message(self, message) -> None:
        # mcp 2.x hands over the notification itself (1.x wrapped it in a
        # ServerNotification root model), or a transport Exception.
        if isinstance(message, ToolListChangedNotification):
            logger.info("[UnityMCP] tools/list_changed received; refreshing tool list")
            self._request_refresh()

    def _request_refresh(self) -> None:
        """At most one refresh runs; changes that arrive during it schedule exactly
        one follow-up, so a notification burst cannot fan out into concurrent
        refreshes that overwrite each other's cache."""
        if self.on_tools_changed is None:
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_again = True
            return
        self._refresh_task = asyncio.get_running_loop().create_task(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        while True:
            self._refresh_again = False
            await self._notify_tools_changed()
            if not self._refresh_again:
                return

    async def _notify_tools_changed(self) -> None:
        callback = self.on_tools_changed
        if callback is None:
            return
        # to_thread copies the context, so list_tools on the worker thread sees
        # the epoch this refresh belongs to.
        _refresh_epoch.set(self._epoch)
        try:
            await asyncio.to_thread(callback)
        except Exception as exc:
            logger.warning("[UnityMCP] tool list refresh failed: %s", exc)

    # ── public (sync, bounded) ───────────────────────────────────────────
    @staticmethod
    async def _retry_if_session_lost(once, what: str):
        """Runs `once`; after a lost session (server restart) runs it exactly
        one more time on a fresh connection. A second loss is reported."""
        try:
            return await once()
        except _SessionLost:
            logger.info("[UnityMCP] server restarted (session not found); "
                        "reconnecting and retrying %s once", what)
        return await once()

    def list_tools(self, timeout: float = LIST_TIMEOUT_S) -> List:
        epoch = _refresh_epoch.get()

        async def _once():
            conn, session = await self._acquire(refresh_on_connect=False, epoch=epoch)
            failed = False
            try:
                result = await self._await_on(conn, session.list_tools())
            except Exception as exc:
                failed = True
                if _is_session_lost(exc, self._observes_http):
                    raise _SessionLost() from exc
                raise
            finally:
                await self._release(conn, failed)
            return result.tools
        return self.run(self._retry_if_session_lost(_once, "tools/list"), timeout)

    def call_tool(self, name: str, params: Dict[str, Any], timeout: float = CALL_TIMEOUT_S,
                  meta: Optional[Dict[str, Any]] = None):
        timed_out = _CALL_TIMEOUT_MSG.format(seconds=f"{timeout:g}")

        async def _once():
            conn, session = await self._acquire()
            failed = False
            try:
                # allow_input_required: without it mcp 2.x raises a bare
                # RuntimeError, which would mark this healthy session broken.
                result = await self._await_on(conn, session.call_tool(
                    name, params, read_timeout_seconds=float(timeout),
                    allow_input_required=True, meta=meta))
            except MCPError as exc:
                if _is_session_lost(exc, self._observes_http):
                    failed = True
                    raise _SessionLost() from exc
                if _is_unknown_404(exc):
                    # Not the server's session lookup: the call may have run.
                    failed = True
                    raise UnityMCPError(_CALL_404_UNKNOWN_MSG) from exc
                if getattr(exc.error, "code", None) == CONNECTION_CLOSED:
                    failed = True
                    raise UnityMCPError(_CONNECTION_LOST_MSG) from exc
                if getattr(exc.error, "code", None) == REQUEST_TIMEOUT:
                    # The SDK gave up waiting and sent a cancel; the session
                    # is healthy, but the call may have run.
                    raise UnityMCPError(timed_out) from exc
                # A JSON-RPC error answer: the session itself is healthy.
                raise
            except Exception:
                # No automatic retry: the call may already have reached Unity,
                # and running a mutation twice is worse than reporting a failure.
                # The next call reconnects. (A lost session is the one exception,
                # above: the server's session-loss 404 proves the call never ran.)
                failed = True
                raise
            finally:
                await self._release(conn, failed)
            if isinstance(result, InputRequiredResult):
                # The server paused the tool for input (elicitation, sampling)
                # that this client cannot give; the tool did not complete.
                raise UnityMCPError(_INPUT_REQUIRED_MSG.format(name=name))
            return result
        # Small margin so the MCP read timeout (a clean MCPError) fires first.
        return self.run(self._retry_if_session_lost(_once, name), timeout + 2,
                        timeout_message=timed_out)

    def close(self, timeout: float = 10.0) -> None:
        if self._loop is None or self._thread is None or not self._thread.is_alive():
            return
        try:
            self.run(self._close_all(), timeout)
        except Exception as exc:
            logger.debug("[UnityMCP] close: %s", exc)


_client = _UnityMCPClient()


# ── Result mapping ────────────────────────────────────────────────────────────

def _result_to_dict(result) -> Dict[str, Any]:
    """MCP CallToolResult → the dict shape the provider loops consume.

    Image content becomes `image_base64` (a data URL with its real mime type),
    the key the three function-calling loops already forward to the model; it
    is popped before the 8000-char text cut, so the cut never eats it.
    """
    if not hasattr(result, "content"):
        return {"success": True, "result": str(result)}
    text_parts: List[str] = []
    images: List[tuple] = []
    for block in result.content or []:
        kind = getattr(block, "type", None)
        if kind == "image" and getattr(block, "data", None):
            images.append((getattr(block, "mime_type", None) or "image/png", block.data))
        elif hasattr(block, "text"):
            text_parts.append(block.text)
    out: Dict[str, Any] = {"success": not result.is_error, "result": "\n".join(text_parts)}
    if images:
        mime, data = images[0]
        out["image_base64"] = f"data:{mime};base64,{data}"
        if len(images) > 1:
            # The loops forward one image per tool result.
            out["images_omitted"] = len(images) - 1
    return out


def call_unity_tool(tool_name: str, params: Dict[str, Any],
                    timeout: float = CALL_TIMEOUT_S,
                    conversation_id: Optional[int] = None) -> Dict[str, Any]:
    # Per call, in _meta: this one MCP session serves every chat, so a
    # connection header could not say which chat an approval card belongs to.
    meta = None
    if type(conversation_id) is int and conversation_id > 0:
        meta = {CONVERSATION_META_KEY: conversation_id}
    try:
        return _result_to_dict(_client.call_tool(tool_name, params, timeout=timeout, meta=meta))
    except BaseException as exc:  # noqa: BLE001 - ExceptionGroup is a BaseException subclass too
        # A cancelled caller must stay cancelled; reporting it as a tool failure
        # would let the cancelled turn carry on.
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        message = _describe_failure(exc)
        logger.warning("[UnityMCP] %s failed: %s", tool_name, message)
        return {"success": False, "error": message}


def call_unity_tool_with_arguments(tool_name: str, arguments: Dict[str, Any],
                                   conversation_id: Optional[int] = None) -> Dict[str, Any]:
    """Model arguments (None values dropped) plus the owning chat, kept apart:
    the chat never travels in the arguments, which the model writes."""
    params = {k: v for k, v in arguments.items() if v is not None}
    return call_unity_tool(tool_name, params, conversation_id=conversation_id)


def _make_tool_function(tool_name: str):
    """Verilen tool adı için çağrılabilir bir wrapper fonksiyon üretir."""
    def tool_fn(**kwargs) -> Dict[str, Any]:
        return call_unity_tool_with_arguments(tool_name, kwargs)
    tool_fn.__name__ = tool_name
    return tool_fn


def _mcp_schema_to_tool_def(mcp_tool) -> Dict:
    """MCP Tool nesnesini tool_registry formatına çevirir."""
    schema = {}
    s = getattr(mcp_tool, "input_schema", None)
    if s:
        schema = {
            "type": s.get("type", "object"),
            "properties": s.get("properties", {}),
            "required": s.get("required", []),
        }
    else:
        schema = {"type": "object", "properties": {}, "required": []}

    return {
        "name": mcp_tool.name,
        "description": mcp_tool.description or f"Unity MCP: {mcp_tool.name}",
        "parameters": schema,
    }


def _tool_groups(mcp_tool) -> set:
    """Group names from FastMCP's `_meta.fastmcp.tags` (`group:<name>`)."""
    meta = getattr(mcp_tool, "meta", None) or {}
    tags = ((meta.get("fastmcp") or {}).get("tags") or []) if isinstance(meta, dict) else []
    return {t.split(":", 1)[1] for t in tags if isinstance(t, str) and t.startswith("group:")}


def _select_exported(mcp_tools) -> List:
    """Only EXPORTED_GROUPS, sorted by name so the prompt prefix stays cacheable."""
    wanted = set(EXPORTED_GROUPS)
    chosen = [t for t in mcp_tools if _tool_groups(t) & wanted]
    return sorted(chosen, key=lambda t: t.name)


def estimate_schema_tokens(tool_defs: List[Dict]) -> int:
    """chars/4 of the JSON the providers receive; a coarse, stable yardstick."""
    import json
    return len(json.dumps(tool_defs, ensure_ascii=False)) // 4


def _apply_tool_list(mcp_tools, generation: int) -> bool:
    global _cached_tools, _cached_functions, _cached_generation
    exported = _select_exported(mcp_tools)
    tools = [_mcp_schema_to_tool_def(t) for t in exported]
    functions = {t.name: _make_tool_function(t.name) for t in exported}
    with _cache_lock:
        if generation != _generation:
            logger.info("[UnityMCP] tools were unloaded during the refresh; list discarded")
            return False
        _cached_tools, _cached_functions = tools, functions
        _cached_generation = generation
    logger.info(
        "[UnityMCP] %d/%d tool exported (groups %s, ~%d schema tokens): %s",
        len(tools), len(mcp_tools), "+".join(EXPORTED_GROUPS),
        estimate_schema_tokens(tools), [t["name"] for t in tools],
    )
    return True


# ── Public API ────────────────────────────────────────────────────────────────

def load_unity_tools() -> bool:
    """Sync versiyon — CLI/test context'inden çağrılır."""
    global _cached_tools, _cached_functions
    generation = _generation
    try:
        return _apply_tool_list(_client.list_tools(), generation)
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(e, _RefreshAbandoned):
            # A refresh from before an unload; the cache is not its to clear.
            return False
        logger.warning(f"[UnityMCP] Tool listesi alınamadı: {_describe_failure(e)}")
        with _cache_lock:
            if generation == _generation:
                _cached_tools = []
                _cached_functions = {}
        return False


def _refresh_after_change() -> None:
    # Runs on a worker thread spawned from the client loop; list_tools hops back.
    load_unity_tools()


_client.on_tools_changed = _refresh_after_change


async def load_unity_tools_async() -> bool:
    """Async versiyon — FastAPI route context'inden çağrılır (event loop çakışmasını önler)."""
    # ⚠️ BURAYA "bağlantı kuruldu" AFİŞİ EKLEME. Eskiden burada Unity
    # konsoluna renkli bir Debug.Log basan bir `execute_code` çağrısı vardı
    # ve canlı testte (31 Tem 2026) şu ölçüldü: onay kapısı devreye girdikten
    # sonra o çağrı, kullanıcı hiçbir şey yazmamışken bile ekrana onay kartı
    # çıkarıyor.
    #
    # Kaldırıldı, muaf tutulmadı. İki gerekçe:
    #   1. Dekoratif bir konsol satırı için kütükteki EN tehlikeli araç
    #      kullanılıyordu (`execute_code` = keyfi C# derleyip çalıştırma).
    #   2. Muafiyet eklemek kapıya yeni bir yüzey açardı; bilgi zaten
    #      kaybolmuyor, bağlantı durumu arayüzdeki UNITY MCP rozetinde var.
    #
    # Ders: anlamsız kartlar refleks-onaya alıştırır ve asıl tehlikeli kartı
    # da okunmadan geçirtir — yani bu bir kozmetik mesele değil.
    return await asyncio.to_thread(load_unity_tools)


def unload_unity_tools():
    """Toggle OFF olduğunda cache'i temizler ve oturumu kapatır."""
    global _cached_tools, _cached_functions, _generation
    with _cache_lock:
        _generation += 1
        _cached_tools = []
        _cached_functions = {}
    _client.close()
    # A refresh that started after the bump above saw the new generation and a
    # still-open client, so its publish was accepted. Bumping again also voids
    # one that has listed but not yet published.
    with _cache_lock:
        _generation += 1
        _cached_tools = []
        _cached_functions = {}
    logger.info("[UnityMCP] Tool'lar kaldırıldı.")


def _cache_is_current() -> bool:
    return _cached_generation == _generation


def get_unity_tool_definitions() -> List[Dict]:
    with _cache_lock:
        return list(_cached_tools) if _cache_is_current() else []


def get_unity_tool_functions() -> Dict[str, Any]:
    with _cache_lock:
        return dict(_cached_functions) if _cache_is_current() else {}


def is_unity_tool(tool_name: str) -> bool:
    with _cache_lock:
        return _cache_is_current() and tool_name in _cached_functions
