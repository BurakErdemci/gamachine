"""URL tool profiles: one server, several fixed tool lists.

    /mcp            the default list: enabled groups plus server meta-tools
                    (byte-identical in names and order to the list served
                    before profiles existed; pinned by a test)
    /mcp/gamachine  the core + playtest tools the Gamachine backend exports
    /mcp/full       every tool of every group, regardless of group state

Why profiles instead of per-session toggles: the MCP 2026-07-28 protocol has
no session, so a toggle stored per session either leaks state per request or
does nothing (measured in the P1 spike: `manage_tools deactivate` answered
success and hid nothing). And Claude Code never surfaces a tool added after it
connected, so a list that changes mid-conversation is invisible to the model
anyway. A profile is chosen once, in the URL, and stays fixed.

Server-level group state (which groups the Unity Editor has enabled) lives
here too. It used to be FastMCP visibility transforms edited through the
private `mcp._transforms` list; that state hid disabled groups BEFORE any
middleware ran, which made a profile that shows every group impossible. It is
now a plain set that the middleware below reads.

The profile travels from the ASGI layer to the MCP layer in the request scope:
`ProfilePathMiddleware` rewrites /mcp/<profile> to /mcp (so FastMCP serves one
transport) and records the name; `ToolProfileMiddleware` reads it back from
the current HTTP request. No request, as in stdio, means the default profile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.constants import MCP_TRANSPORT_BASE_PATH
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from services.registry import TOOL_GROUPS
from services.registry.tool_actions import nested_tool_names

logger = logging.getLogger("mcp-for-unity-server")

PROFILE_SCOPE_KEY = "unity_mcp_tool_profile"


@dataclass(frozen=True)
class ToolProfile:
    name: str
    description: str
    # None means "whatever groups the server has enabled right now".
    groups: frozenset[str] | None
    include_ungrouped: bool
    # True: honour server group state and the Unity Editor's registered-tool
    # filter, like /mcp always has. False: a static list, independent of both.
    follows_unity: bool

    @property
    def path(self) -> str:
        if self.name == DEFAULT_PROFILE_NAME:
            return MCP_TRANSPORT_BASE_PATH
        return f"{MCP_TRANSPORT_BASE_PATH}/{self.name}"

    def effective_groups(self) -> frozenset[str]:
        enabled = server_enabled_groups()
        if self.groups is None:
            return enabled
        if self.follows_unity:
            return self.groups & enabled
        return self.groups

    def allows(self, tool_groups: Iterable[str]) -> bool:
        groups = set(tool_groups)
        if not groups:
            return self.include_ungrouped
        return bool(groups & self.effective_groups())


DEFAULT_PROFILE_NAME = "default"

DEFAULT_PROFILE = ToolProfile(
    name=DEFAULT_PROFILE_NAME,
    description="Enabled tool groups plus the server meta-tools.",
    groups=None,
    include_ungrouped=True,
    follows_unity=True,
)

# "hub" must never be a profile name: /mcp/hub/plugin is the Unity Editor's
# WebSocket route under the transport prefix.
PROFILES: dict[str, ToolProfile] = {
    "gamachine": ToolProfile(
        name="gamachine",
        description=(
            "core + playtest, no meta-tools: exactly what the Gamachine backend "
            "exports to its models (Backend/app/tools/unity_mcp_tools.py "
            "EXPORTED_GROUPS / _select_exported)."
        ),
        groups=frozenset({"core", "playtest"}),
        include_ungrouped=False,
        follows_unity=True,
    ),
    "full": ToolProfile(
        name="full",
        description="Every tool of every group, independent of which groups are enabled.",
        groups=frozenset(TOOL_GROUPS),
        include_ungrouped=True,
        follows_unity=False,
    ),
}


# ── Server-level group state ────────────────────────────────────────────────

_server_enabled_groups: frozenset[str] = frozenset(TOOL_GROUPS)


def server_enabled_groups() -> frozenset[str]:
    return _server_enabled_groups


def set_server_enabled_groups(groups: Iterable[str]) -> None:
    global _server_enabled_groups
    _server_enabled_groups = frozenset(g for g in groups if g in TOOL_GROUPS)


def tool_groups(tool: Any) -> set[str]:
    """Group names from a FastMCP tool's `group:<name>` tags."""
    tags = getattr(tool, "tags", None) or set()
    return {t.split(":", 1)[1] for t in tags if isinstance(t, str) and t.startswith("group:")}


# ── Which profile is this request on ─────────────────────────────────────────

def current_profile() -> ToolProfile:
    try:
        from fastmcp.server.dependencies import get_http_request
        request = get_http_request()
    except Exception:
        # stdio, in-memory clients and anything outside an HTTP request.
        return DEFAULT_PROFILE
    state = request.scope.get("state") or {}
    name = state.get(PROFILE_SCOPE_KEY) if isinstance(state, dict) else None
    return PROFILES.get(name, DEFAULT_PROFILE)


def profile_urls_note() -> str:
    names = ", ".join(f"{p.path} ({p.name})" for p in PROFILES.values())
    return f"{MCP_TRANSPORT_BASE_PATH} (default), {names}"


class ProfilePathMiddleware:
    """ASGI: serve /mcp/<profile> from the one /mcp transport.

    Unknown /mcp/<name> answers 404 rather than falling through, so a typo in a
    client config fails loudly instead of silently getting the default list.
    Must sit INSIDE LocalTokenHeaderMiddleware so that an unauthenticated
    caller gets 401 for every /mcp/* path and learns nothing about which
    profile names exist. WebSocket scopes pass untouched (/mcp/hub/plugin).

    Matching is exact: "/mcp" itself, "/mcp/" (left to FastMCP), or
    "/mcp/<profile>" with at most one trailing slash, the name looked up by
    dict equality. Every other path starting with "/mcp" is a 404 HERE,
    because Starlette's route regex ^/mcp$ also matches "/mcp<LF>" (Python `$`
    matches before a trailing newline) and would serve the default transport
    for a spelling nothing else recognised (audit 25 Sep 2026, POST /mcp%0A).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        base = MCP_TRANSPORT_BASE_PATH
        if path in (base, base + "/") or not path.startswith(base):
            await self.app(scope, receive, send)
            return
        rest = path[len(base):]
        name = rest[1:-1] if rest.endswith("/") else rest[1:]
        profile = PROFILES.get(name) if rest.startswith("/") else None
        if profile is None:
            response = JSONResponse(
                {"success": False,
                 "error": f"Unknown tool profile path {path!r}. Available: {profile_urls_note()}."},
                status_code=404,
            )
            await response(scope, receive, send)
            return
        state = dict(scope.get("state") or {})
        state[PROFILE_SCOPE_KEY] = profile.name
        rewritten = dict(scope)
        rewritten["path"] = MCP_TRANSPORT_BASE_PATH
        rewritten["raw_path"] = MCP_TRANSPORT_BASE_PATH.encode("ascii")
        rewritten["state"] = state
        await self.app(rewritten, receive, send)


class ToolProfileMiddleware(Middleware):
    """Filters tools/list and refuses tools/call outside the request's profile.

    Registered BEFORE UnityInstanceMiddleware so an out-of-profile call is
    refused before the approval gate shows the user a card for it.
    """

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        profile = current_profile()
        return [t for t in tools if profile.allows(tool_groups(t))]

    async def on_call_tool(self, context, call_next):
        message = getattr(context, "message", None)
        name = getattr(message, "name", None)
        if isinstance(name, str) and name:
            profile = current_profile()
            server = context.fastmcp_context.fastmcp
            tool = await server.get_tool(name)
            if tool is not None:
                groups = tool_groups(tool)
                if not profile.allows(groups):
                    raise ToolError(_refusal(name, groups, profile))
            await _check_nested_calls(server, name, getattr(message, "arguments", None), profile)
        return await call_next(context)


async def _check_nested_calls(server: Any, name: str, arguments: Any, profile: ToolProfile) -> None:
    """Refuse a batch whose sub-calls are not all on the profile, before any runs.

    Audit 25 Sep 2026: on /mcp/gamachine manage_vfx was refused as a direct
    call but ran inside batch_execute, because only the outer name was checked.
    A sub-call name the server does not know counts as ungrouped: it passes
    where ungrouped tools do (/mcp, /mcp/full) and is refused on gamachine.
    """
    sub_names = nested_tool_names(name, arguments if isinstance(arguments, dict) else {})
    if sub_names is None:
        raise ToolError(
            f"'{name}' was refused before running anything: its calls are nested "
            f"too deeply to check against {profile.path} ({profile.name} profile).")
    refused: list[str] = []
    for sub_name in dict.fromkeys(sub_names):
        sub_tool = await server.get_tool(sub_name)
        groups = tool_groups(sub_tool) if sub_tool is not None else set()
        if not profile.allows(groups):
            refused.append(_refusal(sub_name, groups, profile))
    if refused:
        raise ToolError(
            f"'{name}' was refused before running anything: "
            + " ".join(refused))


def _refusal(name: str, groups: set[str], profile: ToolProfile) -> str:
    where = f"{profile.path} ({profile.name} profile)"
    group_note = f" (group: {', '.join(sorted(groups))})" if groups else ""
    full = PROFILES["full"].path
    if profile.groups is None:
        return (
            f"Tool '{name}'{group_note} is not enabled on {where}. Its group is off "
            f"in the Unity Editor's tool settings; enable it there and run "
            f"manage_tools(action='sync'), or connect to {full} for every tool."
        )
    return (
        f"Tool '{name}'{group_note} is not part of {where}. "
        f"Connect to {full} for every tool."
    )
