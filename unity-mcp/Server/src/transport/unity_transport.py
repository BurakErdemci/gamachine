"""Transport helpers for routing commands to Unity."""
from __future__ import annotations

import contextvars
import functools
import inspect
import logging
from typing import Any, Awaitable, Callable, TypeVar

from transport.plugin_hub import PluginHub
from core.config import config
from core.constants import API_KEY_HEADER
from services.api_key_service import ApiKeyService
from models.models import MCPResponse
from models.unity_response import ACTION_META_KEYS, normalize_unity_response

logger = logging.getLogger(__name__)
T = TypeVar("T")


# Per tool call: the undo/warnings fields of the Unity responses it received. A dict
# (mutated, never re-set) so sends made from child tasks still reach it.
_action_meta: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "unity_action_meta", default=None)


def _record_action_meta(response: Any) -> None:
    holder = _action_meta.get()
    if holder is None or not isinstance(response, dict):
        return
    for key in ACTION_META_KEYS:
        if response.get(key) is not None:
            holder[key] = response[key]


def _merge_action_meta(result: Any, meta: dict[str, Any]) -> Any:
    if not meta or not isinstance(result, dict):
        return result
    if "undo" in meta:
        result.setdefault("undo", meta["undo"])
    warnings = meta.get("warnings")
    if isinstance(warnings, list) and warnings:
        existing = result.get("warnings")
        if existing is None:
            result["warnings"] = list(warnings)
        elif isinstance(existing, list):
            existing.extend(w for w in warnings if w not in existing)
    return result


def carry_action_meta(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Re-attach Unity's ``undo``/``warnings`` fields to a tool's return value.

    Many tool wrappers rebuild the success dict (``{"success", "message", "data"}``) and
    would silently drop them; doing it here covers every tool instead of each wrapper.
    """
    if not inspect.iscoroutinefunction(func):
        return func

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        token = _action_meta.set({})
        try:
            result = await func(*args, **kwargs)
            meta = _action_meta.get() or {}
        finally:
            _action_meta.reset(token)
        return _merge_action_meta(result, meta)

    return wrapper


def _is_http_transport() -> bool:
    return config.transport_mode.lower() == "http"


async def _resolve_user_id_from_request() -> str | None:
    """Extract user_id from the current HTTP request's API key header."""
    if not config.http_remote_hosted:
        return None
    if not ApiKeyService.is_initialized():
        return None
    try:
        from fastmcp.server.dependencies import get_http_headers
        headers = get_http_headers(include_all=True)
        api_key = headers.get(API_KEY_HEADER.lower())
        if not api_key:
            return None
        service = ApiKeyService.get_instance()
        result = await service.validate(api_key)
        return result.user_id if result.valid else None
    except Exception as e:
        logger.debug("Failed to resolve user_id from HTTP request: %s", e)
        return None


async def send_with_unity_instance(
    send_fn: Callable[..., Awaitable[T]],
    unity_instance: str | None,
    *args,
    user_id: str | None = None,
    **kwargs,
) -> T:
    if _is_http_transport():
        if not args:
            raise ValueError("HTTP transport requires command arguments")
        command_type = args[0]
        params = args[1] if len(args) > 1 else kwargs.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise TypeError(
                "Command parameters must be a dict for HTTP transport")

        # Auto-resolve user_id from HTTP request API key (remote-hosted mode)
        if user_id is None:
            user_id = await _resolve_user_id_from_request()

        # Auth check
        if config.http_remote_hosted and not user_id:
            return normalize_unity_response(
                MCPResponse(
                    success=False,
                    error="auth_required",
                    message="API key required",
                ).model_dump()
            )

        retry_on_reload = kwargs.pop("retry_on_reload", True)
        if not isinstance(retry_on_reload, bool):
            retry_on_reload = True

        try:
            raw = await PluginHub.send_command_for_instance(
                unity_instance,
                command_type,
                params,
                user_id=user_id,
                retry_on_reload=retry_on_reload,
            )
            normalized = normalize_unity_response(raw)
            _record_action_meta(normalized)
            return normalized
        except Exception as exc:
            # NOTE: asyncio.TimeoutError has an empty str() by default, which is confusing for clients.
            err = str(exc) or f"{type(exc).__name__}"
            # Fail fast with a retry hint instead of hanging for COMMAND_TIMEOUT.
            # The client can decide whether retrying is appropriate for the command.
            return normalize_unity_response(
                MCPResponse(success=False, error=err,
                            hint="retry").model_dump()
            )

    if unity_instance:
        kwargs.setdefault("instance_id", unity_instance)
    response = await send_fn(*args, **kwargs)
    _record_action_meta(response)
    return response
