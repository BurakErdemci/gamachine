from typing import Annotated, Any
from types import SimpleNamespace

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from transport.legacy.unity_connection import get_unity_connection_pool
from transport.plugin_hub import PluginHub
from core.config import config


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description=(
        "Deprecated: does NOT pin routing any more. Checks a Unity instance identifier "
        "(Name@hash, hash prefix, project name, or port number in stdio mode) and returns "
        "the exact Name@hash plus how to route calls to it: pass unity_instance on each "
        "tool call, or connect with ?instance=<Name@hash> on the MCP URL. With one "
        "instance connected, calls reach it without any selection."
    ),
    annotations=ToolAnnotations(
        title="Set Active Instance",
    ),
)
async def set_active_instance(
        ctx: Context,
        instance: Annotated[str, "Target instance (Name@hash, hash prefix, project name, or port number in stdio mode)"]
) -> dict[str, Any]:
    transport = (config.transport_mode or "stdio").lower()

    # Port number shorthand (stdio only) — resolve to Name@hash via pool discovery
    value = (instance or "").strip()
    if value.isdigit():
        if transport == "http":
            return {
                "success": False,
                "error": f"Port-based targeting ('{value}') is not supported in HTTP transport mode. "
                         "Use Name@hash or a hash prefix. Read mcpforunity://instances for available instances."
            }
        port_int = int(value)
        pool = get_unity_connection_pool()
        instances = pool.discover_all_instances(force_refresh=True)
        match = next((inst for inst in instances if getattr(inst, "port", None) == port_int), None)
        if match is None:
            available = ", ".join(
                f"{inst.id} (port {getattr(inst, 'port', '?')})" for inst in instances
            ) or "none"
            return {
                "success": False,
                "error": f"No Unity instance found on port {value}. Available: {available}."
            }
        return _not_pinned(match.id)

    # Discover running instances based on transport
    if transport == "http":
        # In remote-hosted mode, filter sessions by user_id
        user_id = (await ctx.get_state(
            "user_id")) if config.http_remote_hosted else None
        sessions_data = await PluginHub.get_sessions(user_id=user_id)
        sessions = sessions_data.sessions
        instances = []
        for session_id, session in sessions.items():
            project = session.project or "Unknown"
            hash_value = session.hash
            if not hash_value:
                continue
            inst_id = f"{project}@{hash_value}"
            instances.append(SimpleNamespace(
                id=inst_id,
                hash=hash_value,
                name=project,
                session_id=session_id,
            ))
    else:
        pool = get_unity_connection_pool()
        instances = pool.discover_all_instances(force_refresh=True)

    if not instances:
        return {
            "success": False,
            "error": "No Unity instances are currently connected. Start Unity and press 'Start Session'."
        }
    ids = {inst.id: inst for inst in instances if getattr(inst, "id", None)}

    value = (instance or "").strip()
    if not value:
        return {
            "success": False,
            "error": "Instance identifier is required. "
                     "Use mcpforunity://instances to copy a Name@hash or provide a hash prefix."
        }
    resolved = None
    if "@" in value:
        resolved = ids.get(value)
        if resolved is None:
            return {
                "success": False,
                "error": f"Instance '{value}' not found. "
                "Use mcpforunity://instances to copy an exact Name@hash."
            }
    else:
        lookup = value.lower()
        matches = []
        for inst in instances:
            if not getattr(inst, "id", None):
                continue
            inst_hash = getattr(inst, "hash", "")
            # Düz proje adı da kabul edilir ("MyGame" → "MyGame@abc123") —
            # stdio pool'da .name olmayabilir, id'nin @ öncesinden türet.
            inst_name = getattr(inst, "name", None) or (
                inst.id.split("@")[0] if "@" in inst.id else "")
            if (inst_hash and inst_hash.lower().startswith(lookup)) or \
               (inst_name and inst_name.lower().startswith(lookup)):
                matches.append(inst)
        if not matches:
            return {
                "success": False,
                "error": f"'{value}' does not match any running Unity editor's hash or project name. "
                "Use mcpforunity://instances to confirm the available instances."
            }
        if len(matches) > 1:
            matching_ids = ", ".join(
                inst.id for inst in matches if getattr(inst, "id", None)
            ) or "multiple instances"
            return {
                "success": False,
                "error": f"'{value}' is ambiguous ({matching_ids}). "
                "Provide the full Name@hash from mcpforunity://instances."
            }
        resolved = matches[0]

    if resolved is None:
        # Should be unreachable due to logic above, but satisfies static analysis
        return {
            "success": False,
            "error": "Internal error: Instance resolution failed."
        }

    return _not_pinned(resolved.id)


def _not_pinned(instance_id: str) -> dict[str, Any]:
    """success=False on purpose: the call's name promises a pin that no longer happens.

    It used to store the selection under the caller's client_id, and without
    one under the constant key "global" -- so in local mode one client's call
    re-routed every other client connected to the server. The 2026-07-28
    protocol has no session to store it under at all.
    """
    return {
        "success": False,
        "error": (
            f"set_active_instance does not pin routing any more; nothing was changed. "
            f"'{instance_id}' is a valid instance. Route calls to it by passing "
            f"unity_instance='{instance_id}' on each tool call, or connect with "
            f"?instance={instance_id} on the MCP URL (or an X-Unity-Instance header). "
            "With only one instance connected, calls reach it automatically."
        ),
        "data": {"instance": instance_id, "pinned": False},
    }
