from typing import Any
import os
import sys

from core.telemetry import get_package_version

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from transport.unity_instance_middleware import get_unity_instance_middleware
from transport.plugin_hub import PluginHub
from transport.tool_profiles import current_profile


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description="Return the current FastMCP request context details (client_id, session_id, and meta dump).",
    annotations=ToolAnnotations(
        title="Debug Request Context",
        readOnlyHint=True,
    ),
)
async def debug_request_context(ctx: Context) -> dict[str, Any]:
    # Check request_context properties
    rc = getattr(ctx, "request_context", None)
    rc_client_id = getattr(rc, "client_id", None)
    rc_session_id = getattr(rc, "session_id", None)
    meta = getattr(rc, "meta", None)

    # Check direct ctx properties (per latest FastMCP docs)
    ctx_session_id = getattr(ctx, "session_id", None)
    ctx_client_id = getattr(ctx, "client_id", None)

    meta_dump = None
    if meta is not None:
        try:
            dump_fn = getattr(meta, "model_dump", None)
            if callable(dump_fn):
                meta_dump = dump_fn(exclude_none=False)
            elif isinstance(meta, dict):
                meta_dump = dict(meta)
        except Exception as e:
            meta_dump = {"_error": str(e)}

    # List all ctx attributes for debugging
    ctx_attrs = [attr for attr in dir(ctx) if not attr.startswith("_")]

    # Routing is resolved per request (see UnityInstanceMiddleware); there is
    # no per-session store to dump any more.
    middleware = get_unity_instance_middleware()
    routed_instance = await ctx.get_state("unity_instance")
    routed_session_id = await ctx.get_state("unity_session_id")
    connection_default = middleware._request_default_instance()

    # Debugging PluginHub state
    plugin_hub_configured = PluginHub.is_configured()

    return {
        "success": True,
        "data": {
            "server": {
                "version": get_package_version(),
                "cwd": os.getcwd(),
                "argv": list(sys.argv),
            },
            "request_context": {
                "client_id": rc_client_id,
                "session_id": rc_session_id,
                "meta": meta_dump,
            },
            "direct_properties": {
                "session_id": ctx_session_id,
                "client_id": ctx_client_id,
            },
            "routing": {
                "unity_instance": routed_instance,
                "unity_session_id": routed_session_id,
                "connection_default_instance": connection_default,
                "tool_profile": current_profile().name,
                "plugin_hub_configured": plugin_hub_configured,
                "middleware_id": id(middleware),
            },
            "available_attributes": ctx_attrs,
        },
    }
