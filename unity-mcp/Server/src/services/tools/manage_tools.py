"""
manage_tools - server-only meta-tool for tool group discovery.

Tool lists are fixed per URL profile (transport.tool_profiles), not toggled
per session. activate / deactivate / reset used to edit FastMCP's
per-session visibility; on the MCP 2026-07-28 protocol there is no session,
and the P1 spike measured `deactivate` answering success while hiding
nothing. They now say so and point at the profile URLs instead.
"""
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from core.config import config
from services.registry import (
    mcp_for_unity_tool,
    TOOL_GROUPS,
    DEFAULT_ENABLED_GROUPS,
    get_group_tool_names,
)
from transport import tool_profiles


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description=(
        "Inspect tool groups. Actions: list_groups (groups, which are available "
        "on the URL this client connected to, and the other profile URLs), "
        "sync (refresh group state from the Unity Editor's tool toggles). "
        "The tool list is fixed per URL: /mcp (enabled groups), "
        "/mcp/gamachine (core + playtest), /mcp/full (every group). "
        "activate / deactivate / reset no longer change anything; they return "
        "an error explaining which URL to connect to instead."
    ),
    annotations=ToolAnnotations(
        title="Manage Tools",
        readOnlyHint=False,
    ),
)
async def manage_tools(
    ctx: Context,
    action: Annotated[
        Literal["list_groups", "activate", "deactivate", "sync", "reset"],
        "Action to perform."
    ],
    group: Annotated[
        str | None,
        "Group name (only used in the error for activate / deactivate). "
        "Valid groups: " + ", ".join(sorted(TOOL_GROUPS.keys()))
    ] = None,
) -> dict[str, Any]:
    if action == "list_groups":
        return _list_groups()

    if action in ("activate", "deactivate", "reset"):
        return {"success": False, "error": _no_session_toggles(action, group)}

    if action == "sync":
        await ctx.info("Syncing tool visibility from Unity Editor...")
        from services.tools import sync_tool_visibility_from_unity
        result = await sync_tool_visibility_from_unity(notify=True)
        if result.get("error"):
            msg = result["error"]
            if result.get("unsupported"):
                msg = (
                    "The connected Unity Editor does not support tool state syncing yet. "
                    "Update the MCPForUnity package to the latest version, then try again."
                )
            else:
                msg = f"Failed to sync tool visibility from Unity. Is Unity running? ({msg})"
            return {"error": msg}
        return {
            "synced": True,
            "enabled_groups": result.get("enabled_groups", []),
            "disabled_groups": result.get("disabled_groups", []),
            "enabled_tool_count": result.get("enabled_tool_count", 0),
            "total_tool_count": result.get("total_tool_count", 0),
            "message": (
                "Tool visibility synced from Unity Editor. "
                f"Enabled groups: {', '.join(result.get('enabled_groups', []))}. "
                f"Disabled groups: {', '.join(result.get('disabled_groups', []) or ['none'])}."
            ),
        }

    return {"error": f"Unknown action '{action}'"}


def _is_http() -> bool:
    return (config.transport_mode or "stdio").lower() == "http"


def _no_session_toggles(action: str, group: str | None) -> str:
    what = f"'{group}'" if group else "a group"
    if action == "reset":
        head = "manage_tools reset has nothing to reset: tool lists are not changed per session."
    else:
        head = f"manage_tools {action} cannot change which tools this client sees ({what})."
    if _is_http():
        return (
            f"{head} The tool list is fixed by the URL the client connects to: "
            f"{tool_profiles.profile_urls_note()}. Reconnect to the profile you need, "
            "for example /mcp/full for every group. A group can also be turned on "
            "for /mcp in the Unity Editor's tool settings, followed by "
            "manage_tools(action='sync')."
        )
    return (
        f"{head} Over stdio every group the Unity Editor has enabled is listed. "
        "Turn groups on or off in the Unity Editor's tool settings, then run "
        "manage_tools(action='sync')."
    )


def _list_groups() -> dict[str, Any]:
    """Groups, whether each is available on the current profile, and how to switch."""
    group_tools = get_group_tool_names()
    profile = tool_profiles.current_profile()
    server_enabled = tool_profiles.server_enabled_groups()
    groups = []
    for name in sorted(TOOL_GROUPS.keys()):
        groups.append({
            "name": name,
            "description": TOOL_GROUPS[name],
            "enabled": profile.allows({name}),
            "enabled_server_wide": name in server_enabled,
            "default_enabled": name in DEFAULT_ENABLED_GROUPS,
            "tools": group_tools.get(name, []),
            "tool_count": len(group_tools.get(name, [])),
        })
    result: dict[str, Any] = {"groups": groups}
    if _is_http():
        result["profile"] = {"name": profile.name, "path": profile.path}
        result["profiles"] = [
            {"name": p.name, "path": p.path, "description": p.description}
            for p in [tool_profiles.DEFAULT_PROFILE, *tool_profiles.PROFILES.values()]
        ]
        result["note"] = (
            f"'enabled' is for this URL ({profile.path}). To see a different set, "
            "reconnect to another profile URL; the list does not change within a "
            "connection. Tools with group=None (server meta-tools) are listed on "
            "/mcp and /mcp/full."
        )
    else:
        result["note"] = (
            "Over stdio 'enabled' follows the Unity Editor's tool settings; change "
            "them there and run manage_tools(action='sync')."
        )
    return result
