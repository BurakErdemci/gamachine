"""
Middleware that routes each MCP request to a Unity instance.

Routing is decided per request, in this order:
  1. the `unity_instance` argument of the tool call (popped before validation)
  2. the connection's default: ?instance=... on the MCP URL, or the
     X-Unity-Instance header
  3. the sole connected instance, when exactly one is connected (local mode)
The result goes into request-scoped state (set_state(..., serializable=False)),
which tools read via ctx.get_state("unity_instance") for that request only.

There is deliberately no server-side "active instance" per session. It used to
exist, keyed by client_id and otherwise by the constant "global", so in local
mode one client's set_active_instance re-routed every other client, and on the
2026-07-28 protocol there is no session to key it by at all.
"""
from threading import RLock
import asyncio
import logging
import time
import weakref

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

from core.config import config
from core.constants import (
    GAMACHINE_CONVERSATION_HEADER,
    GAMACHINE_CONVERSATION_META_KEY,
    GAMACHINE_CONVERSATION_QUERY_PARAM,
    UNITY_INSTANCE_HEADER,
    UNITY_INSTANCE_QUERY_PARAM,
)
from services.protection_rules import meta_refusal
from services.registry import get_registered_tools
from services.registry.tool_actions import READ, classify
from transport.approval_gate import ApprovalDenied, parse_conversation_id
from transport.plugin_hub import PluginHub

logger = logging.getLogger("mcp-for-unity-server")
# Separate logger that propagates to root -> stderr so diagnostics show in console
_diag = logging.getLogger("transport.unity_instance_middleware")

# Store a global reference to the middleware instance so tools can interact
# with it to set or clear the active unity instance.
_unity_instance_middleware = None
_middleware_lock = RLock()

# Write calls are serialized per Unity instance, so two chats never interleave
# mutations on one editor. Module-level so tests can shrink them.
#
# A write that ran after its client gave up is the double-write the approval
# gate already budgets against: agy cancels every call at 180 s, and the gate
# ends its card wait by 10 + 150 = 160 s to leave Unity 20 s (approval_gate.py).
# The lock wait is capped by the same line, counted from the call's arrival, so
# queueing never pushes a dispatch past it. Within that, 30 s: one queued write
# normally holds the lock for seconds and at most one PluginHub command timeout
# (30 s); a script write waiting for its compile can hold it for the 90 s compile
# wait, and waiting that out would eat most of the Gamachine backend's 240 s
# CALL_TIMEOUT_S, so such a call is told to retry instead.
WRITE_LOCK_WAIT_S = 30.0
WRITE_DISPATCH_DEADLINE_S = 160.0
WRITE_WAIT_LOG_S = 0.5
# Unresolved calls (no instance connected, or several and none chosen) share one
# key: the tool then picks its own target or fails, so serializing them together
# is the conservative choice.
_NO_INSTANCE_KEY = "<unresolved>"


def get_unity_instance_middleware() -> 'UnityInstanceMiddleware':
    """Get the global Unity instance middleware."""
    global _unity_instance_middleware
    if _unity_instance_middleware is None:
        with _middleware_lock:
            if _unity_instance_middleware is None:
                # Auto-initialize if not set (lazy singleton) to handle import order or test cases
                _unity_instance_middleware = UnityInstanceMiddleware()

    return _unity_instance_middleware


def set_unity_instance_middleware(middleware: 'UnityInstanceMiddleware') -> None:
    """Replace the global middleware instance.

    This is a test seam: production code uses ``get_unity_instance_middleware()``
    which lazy-initialises the singleton.  Tests call this function to inject a
    mock or pre-configured middleware before exercising tool/resource code.
    """
    global _unity_instance_middleware
    _unity_instance_middleware = middleware


class UnityInstanceMiddleware(Middleware):
    """
    Middleware that manages per-session Unity instance selection.

    Stores active instance per session_id and injects it into request state
    for all tool and resource calls.
    """

    def __init__(self):
        super().__init__()
        self._metadata_lock = RLock()
        self._unity_managed_tool_names: set[str] = set()
        self._tool_alias_to_unity_target: dict[str, str] = {}
        self._server_only_tool_names: set[str] = set()
        self._tool_visibility_signature: tuple[tuple[str, str], ...] = ()
        self._last_tool_visibility_refresh = 0.0
        self._tool_visibility_refresh_interval_seconds = 0.5
        self._has_logged_empty_registry_warning = False
        # Keyed by event loop: an asyncio.Lock binds to the first loop that
        # waits on it, and this singleton outlives loops (each asyncio.run in tests).
        self._write_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        # loop -> {instance key: the task holding that lock}. A write whose tool
        # dispatched another write through this middleware (server.call_tool
        # from inside a tool) would otherwise wait on its own lock. Keyed by the
        # task, not a ContextVar: a ContextVar is copied into every child task,
        # so a task spawned during a write kept the marker after the lock went
        # to another chat and skipped it (Codex s2audit, contextvar-child-bypass).
        self._write_holders: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._write_locks_guard = RLock()

    def _write_lock(self, key: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._write_locks_guard:
            per_loop = self._write_locks.setdefault(loop, {})
            lock = per_loop.get(key)
            if lock is None:
                lock = per_loop[key] = asyncio.Lock()
            return lock

    @staticmethod
    def _request_default_instance() -> str | None:
        """The instance this connection asked for in its URL or headers."""
        try:
            from fastmcp.server.dependencies import get_http_request
            request = get_http_request()
        except Exception:
            # stdio or outside an HTTP request.
            return None
        value = request.query_params.get(UNITY_INSTANCE_QUERY_PARAM)
        if not value:
            value = request.headers.get(UNITY_INSTANCE_HEADER)
        value = (value or "").strip()
        return value or None

    @staticmethod
    def _request_conversation_id() -> int | None:
        """The Gamachine conversation this call says it works for, or None.

        Sources: the connection (X-Gamachine-Conversation header, ?conv= on the
        URL) and the call's own _meta, for a client that shares one session
        across chats. Every source present must parse and agree; one junk or
        disagreeing value drops the claim, because a card shown in the wrong
        chat is worse than an unowned one.
        """
        claims = []
        try:
            from fastmcp.server.dependencies import get_http_request
            request = get_http_request()
        except Exception:
            request = None
        if request is not None:
            claims.append(request.headers.get(GAMACHINE_CONVERSATION_HEADER))
            claims.append(request.query_params.get(GAMACHINE_CONVERSATION_QUERY_PARAM))
        try:
            # The raw _meta of the tools/call request. The middleware's own
            # context.message is rebuilt by FastMCP and keeps only its version key.
            from fastmcp.server.dependencies import fastmcp_request_ctx
            request_context = fastmcp_request_ctx.get()
            meta = request_context.meta if request_context is not None else None
        except Exception:
            meta = None
        if isinstance(meta, dict):
            claims.append(meta.get(GAMACHINE_CONVERSATION_META_KEY))
        # An empty value is a source that is present and does not parse, so it
        # drops the claim like any junk value (Codex s2audit, empty-owner-source).
        present = [claim for claim in claims if claim is not None]
        if not present:
            return None
        parsed = {parse_conversation_id(claim) for claim in present}
        if len(parsed) == 1 and None not in parsed:
            return parsed.pop()
        _diag.warning("on_call_tool: conversation claim dropped (%r); the card goes unowned",
                      present)
        return None

    async def _discover_instances(self, ctx) -> list:
        """
        Return running Unity instances across both HTTP (PluginHub) and stdio transports.

        Returns a list of objects with .id (Name@hash) and .hash attributes.
        """
        from types import SimpleNamespace
        transport = (config.transport_mode or "stdio").lower()
        results: list = []

        if PluginHub.is_configured():
            try:
                user_id = None
                get_state_fn = getattr(ctx, "get_state", None)
                if callable(get_state_fn) and config.http_remote_hosted:
                    user_id = await get_state_fn("user_id")
                sessions_data = await PluginHub.get_sessions(user_id=user_id)
                sessions = sessions_data.sessions or {}
                for session_info in sessions.values():
                    project = getattr(session_info, "project", None) or "Unknown"
                    hash_value = getattr(session_info, "hash", None)
                    if hash_value:
                        results.append(SimpleNamespace(
                            id=f"{project}@{hash_value}",
                            hash=hash_value,
                            name=project,
                        ))
            except Exception as exc:
                if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                    raise
                logger.debug("PluginHub instance discovery failed (%s)", type(exc).__name__, exc_info=True)

        if not results and transport != "http":
            try:
                from transport.legacy.unity_connection import get_unity_connection_pool
                pool = get_unity_connection_pool()
                results = pool.discover_all_instances(force_refresh=True)
            except Exception as exc:
                if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                    raise
                logger.debug("Stdio instance discovery failed (%s)", type(exc).__name__, exc_info=True)

        return results

    async def _resolve_instance_value(self, value: str, ctx) -> str:
        """
        Resolve a unity_instance string to a validated instance identifier.

        Accepts:
          - Bare port number like "6401" (stdio only) -> resolved Name@hash
          - "Name@hash" exact match
          - Hash prefix (unique prefix match against running instances)

        Raises ValueError with a user-friendly message on failure.
        """
        value = value.strip()
        if not value:
            raise ValueError("unity_instance value must not be empty.")

        transport = (config.transport_mode or "stdio").lower()

        # Port number (stdio only) — resolve to Name@hash via status file lookup
        if value.isdigit():
            if transport == "http":
                raise ValueError(
                    f"Port-based targeting ('{value}') is not supported in HTTP transport mode. "
                    "Use Name@hash or a hash prefix. Read mcpforunity://instances for available instances."
                )
            port_int = int(value)
            instances = await self._discover_instances(ctx)
            for inst in instances:
                if getattr(inst, "port", None) == port_int:
                    return inst.id
            available = ", ".join(
                f"{getattr(i, 'id', '?')} (port {getattr(i, 'port', '?')})"
                for i in instances
            ) or "none"
            raise ValueError(
                f"No Unity instance found on port {value}. Available: {available}."
            )

        instances = await self._discover_instances(ctx)
        ids = {
            getattr(inst, "id", None): inst
            for inst in instances
            if getattr(inst, "id", None)
        }

        # Exact Name@hash match
        if "@" in value:
            if value in ids:
                return value
            available = ", ".join(ids) or "none"
            raise ValueError(
                f"Instance '{value}' not found. Available: {available}. "
                "Read mcpforunity://instances for current sessions."
            )

        # Hash prefix match
        lookup = value.lower()
        matches = [
            inst for inst in instances
            if getattr(inst, "hash", "") and getattr(inst, "hash", "").lower().startswith(lookup)
        ]
        if len(matches) == 1:
            return matches[0].id
        if len(matches) > 1:
            ambiguous = ", ".join(getattr(m, "id", "?") for m in matches)
            raise ValueError(
                f"Hash prefix '{value}' is ambiguous ({ambiguous}). "
                "Provide the full Name@hash from mcpforunity://instances."
            )
        available = ", ".join(ids) or "none"
        raise ValueError(
            f"No running Unity instance matches '{value}'. Available: {available}. "
            "Read mcpforunity://instances for current sessions."
        )

    async def _maybe_autoselect_instance(self, ctx) -> str | None:
        """
        The sole connected Unity instance, or None.

        Evaluated on every request and never remembered: when a second
        instance connects, requests stop being routed implicitly instead of
        silently sticking to whichever one happened to be first.
        """
        try:
            transport = (config.transport_mode or "stdio").lower()
            # This implicit behavior works well for solo-users, but is dangerous for multi-user setups
            if transport == "http" and config.http_remote_hosted:
                return None
            if PluginHub.is_configured():
                try:
                    sessions_data = await PluginHub.get_sessions()
                    sessions = sessions_data.sessions or {}
                    ids: list[str] = []
                    for session_info in sessions.values():
                        project = getattr(
                            session_info, "project", None) or "Unknown"
                        hash_value = getattr(session_info, "hash", None)
                        if hash_value:
                            ids.append(f"{project}@{hash_value}")
                    if len(ids) == 1:
                        logger.debug("Routing to sole Unity instance via PluginHub: %s", ids[0])
                        return ids[0]
                    if len(ids) > 1:
                        logger.info(
                            "Multiple Unity instances found (%d). Pass unity_instance on the tool "
                            "call or connect with ?instance=<Name@hash>. Available: %s",
                            len(ids), ", ".join(ids),
                        )
                except (ConnectionError, ValueError, KeyError, TimeoutError, AttributeError) as exc:
                    logger.debug(
                        "PluginHub auto-select probe failed (%s); falling back to stdio",
                        type(exc).__name__,
                        exc_info=True,
                    )
                except Exception as exc:
                    if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                        raise
                    logger.debug(
                        "PluginHub auto-select probe failed with unexpected error (%s); falling back to stdio",
                        type(exc).__name__,
                        exc_info=True,
                    )

            if transport != "http":
                try:
                    # Import here to avoid circular imports in legacy transport paths.
                    from transport.legacy.unity_connection import get_unity_connection_pool

                    pool = get_unity_connection_pool()
                    instances = pool.discover_all_instances(force_refresh=True)
                    ids = [getattr(inst, "id", None) for inst in instances]
                    ids = [inst_id for inst_id in ids if inst_id]
                    if len(ids) == 1:
                        logger.debug("Routing to sole Unity instance via stdio discovery: %s", ids[0])
                        return ids[0]
                    if len(ids) > 1:
                        logger.info(
                            "Multiple Unity instances found (%d). Pass unity_instance on the tool "
                            "call. Available: %s",
                            len(ids), ", ".join(ids),
                        )
                except (ConnectionError, ValueError, KeyError, TimeoutError, AttributeError) as exc:
                    logger.debug(
                        "Stdio auto-select probe failed (%s)",
                        type(exc).__name__,
                        exc_info=True,
                    )
                except Exception as exc:
                    if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                        raise
                    logger.debug(
                        "Stdio auto-select probe failed with unexpected error (%s)",
                        type(exc).__name__,
                        exc_info=True,
                    )
        except Exception as exc:
            if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                raise
            logger.debug(
                "Auto-select path encountered an unexpected error (%s)",
                type(exc).__name__,
                exc_info=True,
            )

        return None

    async def _resolve_user_id(self) -> str | None:
        """Extract user_id from the current HTTP request's API key."""
        if not config.http_remote_hosted:
            return None
        # Lazy import to avoid circular dependencies (same pattern as _maybe_autoselect_instance).
        from transport.unity_transport import _resolve_user_id_from_request
        return await _resolve_user_id_from_request()

    async def _inject_unity_instance(self, context: MiddlewareContext) -> None:
        """Inject active Unity instance and user_id into context if available."""
        ctx = context.fastmcp_context

        # Resolve user_id from the HTTP request's API key header
        user_id = await self._resolve_user_id()
        if config.http_remote_hosted and user_id is None:
            raise RuntimeError(
                "API key authentication required. Provide a valid X-API-Key header."
            )
        # Request-scoped (serializable=False) on purpose. The default session
        # store keys every entry by session id with a 24 h TTL; on the
        # 2026-07-28 protocol each request is its own "session", so the P1
        # spike measured 200 calls -> 802 stored entries that nothing ever
        # read again. These values are recomputed on every request anyway.
        if user_id:
            await ctx.set_state("user_id", user_id, serializable=False)

        # Per-call routing: check if this tool call explicitly specifies unity_instance.
        # context.message.arguments is a mutable dict on CallToolRequestParams; resource
        # reads use ReadResourceRequestParams which has no .arguments, so this is a no-op for them.
        # We pop the key here so Pydantic's type_adapter.validate_python() never sees it.
        active_instance: str | None = None
        msg_args = getattr(getattr(context, "message", None), "arguments", None)
        if isinstance(msg_args, dict) and "unity_instance" in msg_args:
            raw = msg_args.pop("unity_instance")
            if raw is not None:
                raw_str = str(raw).strip()
                if raw_str:
                    # Raises ValueError with a user-friendly message on invalid input.
                    active_instance = await self._resolve_instance_value(raw_str, ctx)
                    logger.debug("Per-call unity_instance resolved to: %s", active_instance)

        if not active_instance:
            default = self._request_default_instance()
            if default:
                # Same resolution and errors as the per-call argument.
                active_instance = await self._resolve_instance_value(default, ctx)
        if not active_instance:
            active_instance = await self._maybe_autoselect_instance(ctx)
        if active_instance:
            # If using HTTP transport (PluginHub configured), validate session
            # But for stdio transport (no PluginHub needed or maybe partially configured),
            # we should be careful not to clear instance just because PluginHub can't resolve it.
            # The 'active_instance' (Name@hash) might be valid for stdio even if PluginHub fails.

            session_id: str | None = None
            # Only validate via PluginHub if we are actually using HTTP transport.
            # For stdio transport, skip PluginHub entirely - we only need the instance ID.
            from transport.unity_transport import _is_http_transport
            if _is_http_transport() and PluginHub.is_configured():
                try:
                    # resolving session_id might fail if the plugin disconnected
                    # We only need session_id for HTTP transport routing.
                    # For stdio, we just need the instance ID.
                    # Pass user_id for remote-hosted mode session isolation
                    session_id = await PluginHub._resolve_session_id(active_instance, user_id=user_id)
                except (ConnectionError, ValueError, KeyError, TimeoutError) as exc:
                    # If resolution fails, it means the Unity instance is not reachable via HTTP/WS.
                    # If we are in stdio mode, this might still be fine if the user is just setting state?
                    # But usually if PluginHub is configured, we expect it to work.
                    # Let's LOG the error but NOT clear the instance immediately to avoid flickering,
                    # or at least debug why it's failing.
                    logger.debug(
                        "PluginHub session resolution failed for %s: %s; leaving active_instance unchanged",
                        active_instance,
                        exc,
                        exc_info=True,
                    )
                except Exception as exc:
                    # Re-raise unexpected system exceptions to avoid swallowing critical failures
                    if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                        raise
                    logger.error(
                        "Unexpected error during PluginHub session resolution for %s: %s",
                        active_instance,
                        exc,
                        exc_info=True
                    )

            await ctx.set_state("unity_instance", active_instance, serializable=False)
            if session_id is not None:
                await ctx.set_state("unity_session_id", session_id, serializable=False)

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """Inject active Unity instance into tool context if available."""
        arrived_at = time.monotonic()
        try:
            await self._inject_unity_instance(context)
        except ValueError as exc:
            # A bad unity_instance / ?instance= is the caller's mistake and the
            # model must see it as a tool result. FastMCP 3 turned a ValueError
            # here into one; FastMCP 4 answers with a JSON-RPC protocol error
            # instead (measured with the live probe, 25 Sep 2026).
            raise ToolError(str(exc)) from exc
        # A fixed rule, not a card: before the gate, so it holds in auto mode
        # and step mode shows no card for a call that would be refused anyway.
        mesaj = getattr(context, "message", None)
        refusal = meta_refusal(getattr(mesaj, "name", None) or "",
                               getattr(mesaj, "arguments", None))
        if refusal:
            raise ToolError(refusal)
        try:
            await self._require_approval(context)
        except ApprovalDenied as exc:
            # The model must read WHY it was refused, or in step mode it just
            # retries. Raised as-is, the 2026-07-28 runner masked it to
            # "Internal server error" and the old era got a JSON-RPC error
            # instead of a tool result (tests/test_live_approval_denial.py).
            raise ToolError(str(exc)) from exc
        # Taken only now: holding it while a step-mode card waits would stall
        # every other chat's write to this editor behind a human.
        return await self._call_serialized(context, call_next, arrived_at)

    async def _call_serialized(self, context: MiddlewareContext, call_next, arrived_at: float):
        """Run a write under its instance's lock; reads go straight through."""
        mesaj = getattr(context, "message", None)
        params = getattr(mesaj, "arguments", None)
        if classify(getattr(mesaj, "name", None) or "",
                    params if isinstance(params, dict) else {}) == READ:
            return await call_next(context)

        instance = None
        try:
            instance = await context.fastmcp_context.get_state("unity_instance")
        except Exception:
            pass
        key = instance if isinstance(instance, str) and instance else _NO_INSTANCE_KEY

        def refuse_if_late() -> float:
            # Past the line the client may already have given up; neither a free
            # lock, a lock that frees up late, nor the re-entry pass may turn
            # that into a late write (Codex s2audit, s2verify).
            remaining = WRITE_DISPATCH_DEADLINE_S - (time.monotonic() - arrived_at)
            if remaining <= 0:
                _diag.warning("on_call_tool: write %s on %s not sent, %.1f s past its "
                              "dispatch deadline", getattr(mesaj, "name", None), key, -remaining)
                raise ToolError(
                    "The approval took too long for this write to be sent safely. This "
                    "call was NOT sent to Unity and changed nothing; retry it.")
            return remaining

        remaining = refuse_if_late()
        task = asyncio.current_task()
        holders = self._write_holders.setdefault(asyncio.get_running_loop(), {})
        if task is not None and holders.get(key) is task:
            return await call_next(context)

        lock = self._write_lock(key)
        budget = min(WRITE_LOCK_WAIT_S, remaining)
        wait_started = time.monotonic()
        try:
            # wait_for, not asyncio.timeout: the server still supports 3.10.
            # A cancelled Lock.acquire never holds the lock and drops its waiter.
            await asyncio.wait_for(lock.acquire(), budget)
        except asyncio.TimeoutError:
            waited = time.monotonic() - wait_started
            _diag.warning("on_call_tool: write %s on %s gave up after %.2f s waiting for "
                          "another write to finish", getattr(mesaj, "name", None), key, waited)
            target = f"Unity instance '{key}'" if key != _NO_INSTANCE_KEY else "The Unity editor"
            raise ToolError(
                f"{target} is busy with another write call and did not free "
                f"up within {waited:.0f} s. This call was NOT sent to Unity and changed "
                "nothing; retry it in a moment.") from None
        try:
            refuse_if_late()
        except ToolError:
            lock.release()
            raise
        waited = time.monotonic() - wait_started
        if waited >= WRITE_WAIT_LOG_S:
            _diag.info("on_call_tool: write %s on %s waited %.2f s for another write",
                       getattr(mesaj, "name", None), key, waited)
        holders[key] = task
        try:
            return await call_next(context)
        finally:
            holders.pop(key, None)
            lock.release()

    async def _require_approval(self, context: MiddlewareContext) -> None:
        """Mutasyon araçları kullanıcı onayından geçmeden Unity'ye ulaşmaz.

        Buraya konmasının sebebi ölçüldü: MCP trafiğinin TAMAMI bu noktadan
        geçiyor, dinamik custom tool'lar dahil, ve 9 sağlayıcının 9'u da aynı
        uca gidiyor. Sağlayıcı bayraklarını tek tek kapatmak (Cursor `--trust`,
        Copilot `--allow-tool`, Codex `approval_mode="approve"`) altı ayrı yerde
        aynı kapıyı kurmak olurdu ve biri unutulduğunda sessizce açılırdı.

        Sıra önemli: `_inject_unity_instance`'DAN SONRA, `call_next`'ten ÖNCE.
        Enjeksiyon `unity_instance` argümanını mesajdan `pop` ediyor, yani kapı
        onun ardından çalışınca kullanıcıya gösterilen parametreler Unity'ye
        gerçekten gidecek olanlarla aynı oluyor — denetlenen ile çalıştırılanın
        ayrışması bu depoda daha önce gerçek bir bulgu üretti.
        """
        mesaj = getattr(context, "message", None)
        tool_name = getattr(mesaj, "name", None)
        if not tool_name:
            # Adı okunamayan bir çağrıyı sınıflandıramayız. Kütük fail-closed
            # olduğu için burada da fail-closed davranmak tutarlı olurdu, ama
            # bu dal yalnız MCP mesaj şekli değiştiğinde oluşur ve o durumda
            # bütün araçları kilitlemek ürünü çalışmaz hale getirir. Gürültülü
            # bırakıp geçiyoruz: sessiz kalmak, kapının kaybolduğunu gizlerdi.
            _diag.error(
                "on_call_tool: araç adı okunamadı (message=%r) — onay kapısı bu "
                "çağrı için ÇALIŞMADI.", type(mesaj).__name__,
            )
            return
        params = getattr(mesaj, "arguments", None)
        # Çözülmüş hedef state'ten geri okunuyor: `_inject_unity_instance` onu
        # mesajdan `pop` etti, yani karta yazılacak tek kaynak burası. Okunamazsa
        # `None` geçiyoruz — kart hedefsiz çıkar ama ÇIKAR; kartı hiç çıkarmamak
        # kullanıcıyı 180 sn'lik sessiz bir redde kilitlerdi.
        hedef = None
        try:
            hedef = await context.fastmcp_context.get_state("unity_instance")
        except Exception as exc:
            _diag.warning("on_call_tool: hedef okunamadı (%s), kart hedefsiz çıkacak", exc)
        from transport.approval_gate import kapiyi_gec
        await kapiyi_gec(
            tool_name,
            params if isinstance(params, dict) else {},
            hedef=hedef if isinstance(hedef, str) else None,
            conversation_id=self._request_conversation_id(),
        )

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        """Inject active Unity instance into resource context if available."""
        await self._inject_unity_instance(context)
        return await call_next(context)

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        """Filter MCP tool listing to the Unity-enabled set when session data is available."""
        try:
            await self._inject_unity_instance(context)
        except Exception as exc:
            # Re-raise authentication errors so callers get a proper auth failure
            if isinstance(exc, RuntimeError) and "authentication" in str(exc).lower():
                raise
            _diag.warning(
                "on_list_tools: _inject_unity_instance failed (%s: %s), continuing without instance",
                type(exc).__name__, exc,
            )

        tools = await call_next(context)

        tool_names_from_fastmcp = sorted(getattr(t, "name", "?") for t in tools)
        _diag.debug(
            "on_list_tools: FastMCP returned %d tools: %s",
            len(tools), tool_names_from_fastmcp,
        )

        if not self._should_filter_tool_listing():
            _diag.debug("on_list_tools: skipping middleware filter (not HTTP or PluginHub not configured)")
            return tools

        self._refresh_tool_visibility_metadata_from_registry()
        enabled_tool_names = await self._resolve_enabled_tool_names_for_context(context)
        if enabled_tool_names is None:
            _diag.debug("on_list_tools: no Unity session data, returning %d tools from FastMCP as-is", len(tools))
            return tools

        filtered = []
        for tool in tools:
            tool_name = getattr(tool, "name", None)
            if self._is_tool_visible(tool_name, enabled_tool_names):
                filtered.append(tool)

        _diag.debug(
            "on_list_tools: filtered %d/%d tools visible (Unity register_tools). "
            "enabled_names=%s",
            len(filtered), len(tools), sorted(enabled_tool_names),
        )
        return filtered

    def _should_filter_tool_listing(self) -> bool:
        transport = (config.transport_mode or "stdio").lower()
        if not (transport == "http" and PluginHub.is_configured()):
            return False
        # A static profile (/mcp/full) lists tools independent of what the
        # Unity Editor has registered.
        from transport.tool_profiles import current_profile
        return current_profile().follows_unity

    async def _resolve_enabled_tool_names_for_context(
        self,
        context: MiddlewareContext,
    ) -> set[str] | None:
        ctx = context.fastmcp_context
        user_id = (await ctx.get_state("user_id")) if config.http_remote_hosted else None
        active_instance = await ctx.get_state("unity_instance")
        project_hashes = self._resolve_candidate_project_hashes(active_instance)
        try:
            sessions_data = await PluginHub.get_sessions(user_id=user_id)
            sessions = sessions_data.sessions if sessions_data else {}
        except Exception as exc:
            logger.debug(
                "Failed to fetch sessions for tool filtering (user_id=%s, %s)",
                user_id,
                type(exc).__name__,
                exc_info=True,
            )
            return None

        session_hashes = {
            getattr(session, "hash", None)
            for session in sessions.values()
            if getattr(session, "hash", None)
        }

        if project_hashes:
            active_hash = project_hashes[0]
            # Stale active_instance should not hide all Unity-managed tools.
            if active_hash not in session_hashes:
                return None
        else:
            if not sessions:
                return None

            if len(sessions) == 1:
                only_session = next(iter(sessions.values()))
                only_hash = getattr(only_session, "hash", None)
                if only_hash:
                    project_hashes = [only_hash]
            else:
                # Multiple sessions without explicit selection: use a union so we don't
                # hide tools that are valid in at least one visible Unity instance.
                project_hashes = [hash_value for hash_value in session_hashes if hash_value]

        if not project_hashes:
            return None

        enabled_tool_names: set[str] = set()
        resolved_any_project = False
        for project_hash in project_hashes:
            try:
                registered_tools = await PluginHub.get_tools_for_project(project_hash, user_id=user_id)
                # Only mark as resolved if tools are actually registered.
                # An empty list means register_tools hasn't been sent yet.
                if registered_tools:
                    resolved_any_project = True
            except Exception as exc:
                logger.debug(
                    "Failed to fetch tools for project hash %s (user_id=%s, %s)",
                    project_hash,
                    user_id,
                    type(exc).__name__,
                    exc_info=True,
                )
                continue

            for tool in registered_tools:
                tool_name = getattr(tool, "name", None)
                if isinstance(tool_name, str) and tool_name:
                    enabled_tool_names.add(tool_name)

        if not resolved_any_project:
            return None

        return enabled_tool_names

    def _refresh_tool_visibility_metadata_from_registry(self) -> None:
        now = time.monotonic()
        if now - self._last_tool_visibility_refresh < self._tool_visibility_refresh_interval_seconds:
            return

        with self._metadata_lock:
            now = time.monotonic()
            if now - self._last_tool_visibility_refresh < self._tool_visibility_refresh_interval_seconds:
                return

            try:
                registry_tools = get_registered_tools()
            except Exception:
                logger.warning(
                    "Failed to refresh tool visibility metadata from registry; keeping previous metadata.",
                    exc_info=True,
                )
                self._last_tool_visibility_refresh = now
                return

            if not registry_tools and not self._has_logged_empty_registry_warning:
                logger.warning(
                    "Tool registry is empty during tool-list filtering; treating tools as unknown/visible."
                )
                self._has_logged_empty_registry_warning = True
            elif registry_tools:
                self._has_logged_empty_registry_warning = False

            unity_managed_tool_names: set[str] = set()
            tool_alias_to_unity_target: dict[str, str] = {}
            server_only_tool_names: set[str] = set()
            signature_entries: list[tuple[str, str]] = []

            for tool_info in registry_tools:
                tool_name = tool_info.get("name")
                if not isinstance(tool_name, str) or not tool_name:
                    continue

                unity_target = tool_info.get("unity_target", tool_name)
                if unity_target is None:
                    server_only_tool_names.add(tool_name)
                    signature_entries.append((tool_name, "<server-only>"))
                    continue

                if not isinstance(unity_target, str) or not unity_target:
                    logger.debug(
                        "Skipping tool visibility metadata with invalid unity_target: %s",
                        tool_info,
                    )
                    continue

                if unity_target == tool_name:
                    unity_managed_tool_names.add(tool_name)
                    signature_entries.append((tool_name, unity_target))
                    continue

                tool_alias_to_unity_target[tool_name] = unity_target
                unity_managed_tool_names.add(unity_target)
                signature_entries.append((tool_name, unity_target))

            signature = tuple(sorted(signature_entries, key=lambda item: item[0]))
            if signature == self._tool_visibility_signature:
                self._last_tool_visibility_refresh = now
                return

            self._unity_managed_tool_names = unity_managed_tool_names
            self._tool_alias_to_unity_target = tool_alias_to_unity_target
            self._server_only_tool_names = server_only_tool_names
            self._tool_visibility_signature = signature
            self._last_tool_visibility_refresh = now

    @staticmethod
    def _resolve_candidate_project_hashes(active_instance: str | None) -> list[str]:
        if not active_instance:
            return []

        if "@" in active_instance:
            _, _, suffix = active_instance.rpartition("@")
            return [suffix] if suffix else []

        return [active_instance]

    def _is_tool_visible(self, tool_name: str | None, enabled_tool_names: set[str]) -> bool:
        if not isinstance(tool_name, str) or not tool_name:
            return True

        if tool_name in self._server_only_tool_names:
            return True

        if tool_name in enabled_tool_names:
            return True

        unity_target = self._tool_alias_to_unity_target.get(tool_name)
        if unity_target:
            return unity_target in enabled_tool_names

        # Keep unknown tools visible for forward compatibility.
        if tool_name not in self._unity_managed_tool_names:
            return True

        return False
