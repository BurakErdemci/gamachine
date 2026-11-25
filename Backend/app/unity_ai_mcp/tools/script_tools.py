"""run_script on the unityai server (Codex, agy, OpenCode, Cursor, Kimi, Copilot).

Groups for this agent: the server's own file and shell tools, called through MCPServer.call_tool
so each passes the very function (and approval_bridge card) a direct call runs, and the Unity MCP
tools over the same bridge code the CLI's own unityMCP entry uses, carrying this process's
GAMACHINE_CONVERSATION_ID. The server's mail, image and harness tools are not relayed yet.
"""
import logging
import os
from typing import Callable, Optional, Sequence

import httpx
from mcp.server.mcpserver import MCPServer

import script_tool
from script_tool import Outcome, ToolSpec, UnityMCPGroup
from unity_ai_mcp import approval_bridge

logger = logging.getLogger(__name__)


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
        # These tools report their own refusals (guards, bad paths) as text starting with ❌.
        if text.lstrip().startswith("❌"):
            return Outcome.error(text.lstrip()[1:].strip())
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


def default_groups(mcp: MCPServer, local_names: Sequence[str]) -> list:
    return [LocalToolGroup(mcp, local_names), UnityMCPGroup(_unity_client)]


def register_script_tool(mcp: MCPServer, get_workspace: Callable[[], str], local_names: Sequence[str],
                         groups_factory: Optional[Callable[[], list]] = None) -> None:
    """Registers run_script. `local_names` are tools already on `mcp` that scripts may call."""
    names = [n for n in local_names if n != script_tool.TOOL_NAME]
    registered = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    lines = [script_tool.signature(ToolSpec(n, registered[n].description or "", registered[n].parameters or {}))
             for n in names if n in registered]
    description = script_tool.tool_description(lines, [script_tool.UNITY_TOOLS_LINE])
    make_groups = groups_factory or (lambda: default_groups(mcp, names))

    @mcp.tool(name=script_tool.TOOL_NAME, description=description)
    async def run_script(code: str, timeout: Optional[int] = None) -> str:
        text, _ = await script_tool.run_script(code, make_groups(), get_workspace(), timeout=timeout)
        return text
