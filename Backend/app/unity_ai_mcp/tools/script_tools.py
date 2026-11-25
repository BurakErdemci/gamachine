"""run_script on the unityai server (Codex, agy, OpenCode, Cursor, Kimi, Copilot).

Groups for this agent, each through its own gate:
- this server's file, shell and chat-mail tools, through MCPServer.call_tool (the very functions a
  direct call runs: approval_bridge cards, file guards, the backend's mail limits);
- Unity MCP over the same bridge code the CLI's own unityMCP entry uses, with this process's
  GAMACHINE_CONVERSATION_ID;
- the browser's read-only tools and the connectors, each by starting the same server the CLI is
  given (its argv from the registration module, this chat's env), so every call reaches that
  server's backend gate (/browser/command, /connectors/gate). The backend owns which of them this
  agent has, so this process asks it: /browser/settings, and /connectors once per process (it runs
  app detection). A read-only turn gets neither, as its CLI does not.
Computer-use tools are never relayed. Image and harness tools are not relayed.
"""
import logging
import os
from typing import Callable, Optional, Sequence

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

import script_tool
from script_tool import Outcome, ToolSpec, UnityMCPGroup
from unity_ai_mcp import approval_bridge

logger = logging.getLogger(__name__)


LOCAL_FAILURE_PREFIX = "Hata:"


class LocalToolGroup:
    """Tools of this MCPServer, by name. A call runs exactly what a direct MCP call runs."""

    label = "unityai"

    def __init__(self, mcp: MCPServer, names: Sequence[str]):
        self._mcp = mcp
        self._names = tuple(names)

    async def list_tools(self) -> list[ToolSpec]:
        listed = {tool.name: tool for tool in await self._mcp.list_tools()}
        return [ToolSpec(n, listed[n].description or "", listed[n].input_schema or {})
                for n in self._names if n in listed]

    async def call(self, name: str, args: dict) -> Outcome:
        if name not in self._names:
            return Outcome.error(f"{name} is not available in scripts")
        decisions: list = []
        token = approval_bridge.DECISIONS.set(decisions)
        try:
            result = await self._mcp.call_tool(name, args)
        except Exception as exc:  # noqa: BLE001 - ToolError and friends: the tool refused the call
            return Outcome.error(str(exc) or type(exc).__name__)
        finally:
            approval_bridge.DECISIONS.reset(token)
        for decision in decisions:
            if decision.get("approved") is True:
                continue
            comment = decision.get("comment")
            if isinstance(comment, str) and comment.strip():
                return Outcome.error(f"{name} was refused by the user: {comment.strip()}")
            why = decision.get("error")
            return Outcome.stop(f"{name}: {why}" if isinstance(why, str) and why else
                                f"{name} was refused by the user")
        text = script_tool.content_text(getattr(result, "content", None))
        if getattr(result, "isError", False) or getattr(result, "is_error", False):
            return Outcome.error(text or f"{name} failed")
        # These tools report their own refusals (guards) with a leading ❌ and their failures
        # (missing file or folder, bad range) with a leading "Hata:" (audit C-03).
        head = text.lstrip()
        if head.startswith("❌"):
            return Outcome.error(head[1:].strip())
        if head.startswith(LOCAL_FAILURE_PREFIX):
            return Outcome.error(head)
        return Outcome.ok(script_tool.parse_tool_text(text))


async def _unity_mcp_url() -> Optional[str]:
    """UNITY_MCP_URL when the CLI passed it (Kimi does), else the port the backend reports."""
    url = os.environ.get("UNITY_MCP_URL", "").strip()
    if url:
        return url
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{approval_bridge.BACKEND_URL}/unity/status",
                                    headers=approval_bridge._get_headers())
        status = resp.json() if resp.status_code == 200 else {}
    except Exception as exc:  # noqa: BLE001 - no backend: no Unity tools, the script still runs
        logger.info("[run_script] /unity/status failed: %s", exc)
        return None
    port = status.get("port") if isinstance(status, dict) else None
    if type(port) is not int or status.get("status") in (None, "stopped", "disabled", "error"):
        return None
    return f"http://127.0.0.1:{port}/mcp"


async def _unity_client():
    url = await _unity_mcp_url()
    if not url:
        return None
    from providers import codex_unitymcp_bridge
    # The bridge's own token-file reader and conversation parser: what the CLI's unityMCP entry sends.
    return script_tool.unity_bridge_client(
        url, conversation_id=approval_bridge._conversation_id_from_env(),
        secret_reader=lambda: codex_unitymcp_bridge._read_shared_secret())


async def _backend_get(path: str):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{approval_bridge.BACKEND_URL}{path}", headers=approval_bridge._get_headers())
        return resp.json() if resp.status_code == 200 else None
    except Exception as exc:  # noqa: BLE001 - no backend: the group is simply missing
        logger.info("[run_script] GET %s failed: %s", path, exc)
        return None


def _chat_env() -> dict:
    from spawn_env import conversation_env
    return {**conversation_env(approval_bridge._conversation_id_from_env()),
            "UNITYAI_URL": approval_bridge.BACKEND_URL}


async def browser_entry() -> Optional[dict]:
    """browser_use.registration.stdio_entry as the backend would build it, when the browser is on."""
    settings = await _backend_get("/browser/settings")
    if not (isinstance(settings, dict) and settings.get("enabled") is True):
        return None
    from browser_use import registration
    argv = registration.server_argv()
    return {"command": argv[0], "args": argv[1:], "env": _chat_env()}


_CONNECTOR_IDS: Optional[tuple] = None


async def connectors_entry() -> Optional[dict]:
    """connectors.registration.stdio_entry as the backend would build it: the switched-on, usable
    connectors (launcher.ready_ids), read once per process like the CLI's own connectors server."""
    global _CONNECTOR_IDS
    from connectors import launcher, registration
    if _CONNECTOR_IDS is None:
        listing = await _backend_get("/connectors")
        rows = listing.get("connectors") if isinstance(listing, dict) else None
        if not isinstance(rows, list):
            return None
        _CONNECTOR_IDS = tuple(r["id"] for r in rows if isinstance(r, dict) and r.get("enabled") is True
                               and r.get("state") in launcher.USABLE_STATES)
    if not _CONNECTOR_IDS:
        return None
    from connectors.installer import Installer
    argv = registration.server_argv()
    return {"command": argv[0], "args": argv[1:],
            "env": {**_chat_env(), registration.IDS_ENV: ",".join(_CONNECTOR_IDS),
                    registration.ROOT_ENV: str(Installer().root)}}


def connector_refusal_marks() -> tuple:
    from connectors.mcp_server import USER_REFUSAL_MARKS
    return USER_REFUSAL_MARKS


class _LazyGroup:
    """A group whose server entry is only known at run time (the backend decides)."""

    def __init__(self, label: str, make: Callable):
        self.label = label
        self._make = make
        self._group = None
        self.note = None

    async def list_tools(self):
        self._group = await self._make()
        if self._group is None:
            return []
        specs = await self._group.list_tools()
        self.note = getattr(self._group, "note", None)
        return specs

    async def call(self, name, args):
        if self._group is None:
            return Outcome.error(f"{name} is not available in scripts")
        return await self._group.call(name, args)

    async def aclose(self):
        if self._group is not None:
            await self._group.aclose()


async def _browser_group():
    entry = await browser_entry()
    return None if entry is None else script_tool.StdioServerGroup(
        "Browser", entry, keep=script_tool.read_only_tool)


async def _connectors_group():
    entry = await connectors_entry()
    return None if entry is None else script_tool.StdioServerGroup(
        "Connectors", entry, stop_marks=connector_refusal_marks())


def default_groups(mcp: MCPServer, local_names: Sequence[str]) -> list:
    from spawn_env import read_only_refusal
    groups = [LocalToolGroup(mcp, local_names), UnityMCPGroup(_unity_client)]
    if read_only_refusal() is None:
        groups += [_LazyGroup("Browser", _browser_group), _LazyGroup("Connectors", _connectors_group)]
    return groups


def register_script_tool(mcp: MCPServer, get_workspace: Callable[[], str], local_names: Sequence[str],
                         groups_factory: Optional[Callable[[], list]] = None) -> None:
    """Registers run_script. `local_names` are tools already on `mcp` that scripts may call."""
    names = [n for n in local_names if n != script_tool.TOOL_NAME]
    registered = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    lines = [script_tool.signature(ToolSpec(n, registered[n].description or "", registered[n].parameters or {}))
             for n in names if n in registered]
    max_timeout = script_tool.max_timeout_from_env()
    description = script_tool.tool_description(
        lines, [script_tool.UNITY_TOOLS_LINE, *script_tool.browser_lines(), script_tool.CONNECTORS_TOOLS_LINE],
        max_timeout_s=max_timeout)
    make_groups = groups_factory or (lambda: default_groups(mcp, names))

    @mcp.tool(name=script_tool.TOOL_NAME, description=description)
    async def run_script(code: str, timeout: Optional[float] = None) -> CallToolResult:
        text, is_error = await script_tool.run_script(code, make_groups(), get_workspace(), timeout=timeout,
                                                      max_timeout_s=max_timeout)
        # A failed script is an MCP error result, as the Claude endpoint already reports it.
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)
