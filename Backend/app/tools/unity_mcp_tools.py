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
import logging
import threading
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

logger = logging.getLogger(__name__)

# Tool groups sent to function-calling providers. `playtest` (game_hooks,
# play_session, play_step, play_capture, run_playtest) is enabled by default on
# the server next to `core` (tool_registry.DEFAULT_ENABLED_GROUPS).
EXPORTED_GROUPS = ("core", "playtest")

# A write call waits in the server's approval gate for up to 10 s (POST) + 180 s
# (card) in step mode, so the call budget must be larger or every slow click
# would read as a timeout.
CALL_TIMEOUT_S = 240.0
CONNECT_TIMEOUT_S = 15.0
LIST_TIMEOUT_S = 15.0

_UNREACHABLE_MSG = ("Unity MCP sunucusuna bağlanılamadı. Unity MCP anahtarı açık mı, "
                    "Unity Editor çalışıyor mu?")


class UnityMCPError(RuntimeError):
    """A failure already phrased for the user."""


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
        if isinstance(leaf, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError)):
            return _UNREACHABLE_MSG
    for leaf in leaves:
        if isinstance(leaf, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
            return "Unity MCP yanıt vermedi (zaman aşımı)."
    first = leaves[0] if leaves else exc
    return f"Unity MCP hatası: {type(first).__name__}: {first}"


def _default_session_factory(message_handler):
    """Opens the real transport. Tests inject a factory with the same shape."""
    @contextlib.asynccontextmanager
    async def _open():
        url, headers = _endpoint()
        async with streamablehttp_client(url, headers=headers, timeout=CONNECT_TIMEOUT_S) as (read, write, _):
            async with ClientSession(read, write, message_handler=message_handler) as session:
                yield session, (url, tuple(sorted(headers.items())))
    return _open()


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

    def reusable(self) -> bool:
        return (self.session is not None and not self.broken
                and self.owner is not None and not self.owner.done())


class _UnityMCPClient:
    """One MCP session on one loop thread; every public call is bounded in time."""

    def __init__(self, session_factory: Callable = _default_session_factory,
                 endpoint_key: Callable[[], Optional[tuple]] = _endpoint_key):
        self._factory = session_factory
        self._endpoint_key = endpoint_key
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._conn: Optional[_Connection] = None
        self._retired: set = set()
        self._connect_lock: Optional[asyncio.Lock] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._refresh_again = False
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

    def run(self, coro, timeout: float):
        """Runs `coro` on the client loop from any thread; raises on timeout."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise UnityMCPError(f"Unity MCP çağrısı {int(timeout)} sn içinde bitmedi (zaman aşımı).")

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
            if not ready.done():
                ready.set_exception(exc)
            else:
                logger.warning("[UnityMCP] session closed: %s", _describe_failure(exc))
        finally:
            conn.session = None

    async def _acquire(self, refresh_on_connect: bool = True):
        """Returns (connection, session) with the caller counted as in flight."""
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
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
        conns = [self._conn, *self._retired]
        self._conn = None
        self._retired.clear()
        for conn in conns:
            if conn is not None:
                await self._shutdown(conn)

    async def _on_message(self, message) -> None:
        root = getattr(message, "root", None)
        if type(root).__name__ == "ToolListChangedNotification":
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
        try:
            await asyncio.to_thread(callback)
        except Exception as exc:
            logger.warning("[UnityMCP] tool list refresh failed: %s", exc)

    # ── public (sync, bounded) ───────────────────────────────────────────
    def list_tools(self, timeout: float = LIST_TIMEOUT_S) -> List:
        async def _go():
            conn, session = await self._acquire(refresh_on_connect=False)
            failed = False
            try:
                result = await session.list_tools()
            except Exception:
                failed = True
                raise
            finally:
                await self._release(conn, failed)
            return result.tools
        return self.run(_go(), timeout)

    def call_tool(self, name: str, params: Dict[str, Any], timeout: float = CALL_TIMEOUT_S):
        async def _go():
            conn, session = await self._acquire()
            failed = False
            try:
                return await session.call_tool(
                    name, params, read_timeout_seconds=timedelta(seconds=timeout))
            except McpError:
                # A JSON-RPC error answer: the session itself is healthy.
                raise
            except Exception:
                # No automatic retry: the call may already have reached Unity,
                # and running a mutation twice is worse than reporting a failure.
                # The next call reconnects.
                failed = True
                raise
            finally:
                await self._release(conn, failed)
        # Small margin so the MCP read timeout (a clean McpError) fires first.
        return self.run(_go(), timeout + 2)

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
            images.append((getattr(block, "mimeType", None) or "image/png", block.data))
        elif hasattr(block, "text"):
            text_parts.append(block.text)
    out: Dict[str, Any] = {"success": not result.isError, "result": "\n".join(text_parts)}
    if images:
        mime, data = images[0]
        out["image_base64"] = f"data:{mime};base64,{data}"
        if len(images) > 1:
            # The loops forward one image per tool result.
            out["images_omitted"] = len(images) - 1
    return out


def call_unity_tool(tool_name: str, params: Dict[str, Any],
                    timeout: float = CALL_TIMEOUT_S) -> Dict[str, Any]:
    try:
        return _result_to_dict(_client.call_tool(tool_name, params, timeout=timeout))
    except BaseException as exc:  # noqa: BLE001 - ExceptionGroup is a BaseException subclass too
        # A cancelled caller must stay cancelled; reporting it as a tool failure
        # would let the cancelled turn carry on.
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        message = _describe_failure(exc)
        logger.warning("[UnityMCP] %s failed: %s", tool_name, message)
        return {"success": False, "error": message}


def _make_tool_function(tool_name: str):
    """Verilen tool adı için çağrılabilir bir wrapper fonksiyon üretir."""
    def tool_fn(**kwargs) -> Dict[str, Any]:
        # None değerleri filtrele
        params = {k: v for k, v in kwargs.items() if v is not None}
        return call_unity_tool(tool_name, params)
    tool_fn.__name__ = tool_name
    return tool_fn


def _mcp_schema_to_tool_def(mcp_tool) -> Dict:
    """MCP Tool nesnesini tool_registry formatına çevirir."""
    schema = {}
    if hasattr(mcp_tool, 'inputSchema') and mcp_tool.inputSchema:
        s = mcp_tool.inputSchema
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


def _apply_tool_list(mcp_tools) -> None:
    global _cached_tools, _cached_functions
    exported = _select_exported(mcp_tools)
    _cached_tools = [_mcp_schema_to_tool_def(t) for t in exported]
    _cached_functions = {t.name: _make_tool_function(t.name) for t in exported}
    logger.info(
        "[UnityMCP] %d/%d tool exported (groups %s, ~%d schema tokens): %s",
        len(_cached_tools), len(mcp_tools), "+".join(EXPORTED_GROUPS),
        estimate_schema_tokens(_cached_tools), [t["name"] for t in _cached_tools],
    )


# ── Public API ────────────────────────────────────────────────────────────────

def load_unity_tools() -> bool:
    """Sync versiyon — CLI/test context'inden çağrılır."""
    global _cached_tools, _cached_functions
    try:
        _apply_tool_list(_client.list_tools())
        return True
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        logger.warning(f"[UnityMCP] Tool listesi alınamadı: {_describe_failure(e)}")
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
    global _cached_tools, _cached_functions
    _cached_tools = []
    _cached_functions = {}
    _client.close()
    logger.info("[UnityMCP] Tool'lar kaldırıldı.")


def get_unity_tool_definitions() -> List[Dict]:
    return list(_cached_tools)


def get_unity_tool_functions() -> Dict[str, Any]:
    return dict(_cached_functions)


def is_unity_tool(tool_name: str) -> bool:
    return tool_name in _cached_functions
