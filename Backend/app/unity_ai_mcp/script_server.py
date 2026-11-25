"""run_script for Claude SDK chats: the `gamachineScript` server, in-process.

In-process (claude_agent_sdk.create_sdk_mcp_server), not a stdio child like gamachineMail:
Claude's gate is the chat session's `_can_use_tool`, which lives in this backend process, and a
script's inner call has to pass that very function. `ClaudeSDKSession.start` adds one server bound
to itself. Each Unity MCP call runs `session._can_use_tool("mcp__unityMCP__<name>", args)` (cards,
balanced-mode risk, the batch script ban, the read-only refusal) and then goes to the Unity MCP
server with the headers the session's own unityMCP entry sends, so the server's gate judges it as
well. Tools in the session's `disallowed_tools`, the C# script writers among them, are neither
listed nor callable.
"""
import logging
from typing import Any, Optional

import script_tool
from script_tool import Outcome, UnityMCPGroup

logger = logging.getLogger(__name__)

SERVER_NAME = "gamachineScript"
CLAUDE_TOOL_NAME = f"mcp__{SERVER_NAME}__{script_tool.TOOL_NAME}"
UNITY_PREFIX = "mcp__unityMCP__"

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "JavaScript body; may use await and return."},
        "timeout": {"type": "integer", "minimum": 1, "maximum": script_tool.MAX_TIMEOUT_S,
                    "description": f"Seconds, default {script_tool.DEFAULT_TIMEOUT_S}."},
    },
    "required": ["code"],
}


def hidden_unity_tools(session) -> list:
    """Unity MCP tools this session may not call: the script writers and its disallowed_tools."""
    from providers.unity_script_tools import UNITY_SCRIPT_WRITE_TOOLS
    hidden = set(UNITY_SCRIPT_WRITE_TOOLS)
    for name in getattr(session, "disallowed_tools", None) or ():
        if isinstance(name, str) and name.startswith(UNITY_PREFIX):
            hidden.add(name[len(UNITY_PREFIX):])
    return sorted(hidden)


def claude_gate(session, server: str = "unityMCP"):
    """The session's own `_can_use_tool` for `mcp__<server>__<tool>`, as a run_script gate."""
    from providers.claude_sdk_session import USER_DENIED_MESSAGE
    prefix = f"mcp__{server}__"

    async def gate(name: str, args: dict) -> Optional[Outcome]:
        if getattr(session, "_cancel_requested", False):
            return Outcome.stop("the turn was stopped")
        verdict = await session._can_use_tool(prefix + name, args, None)
        if type(verdict).__name__ == "PermissionResultAllow":
            updated = getattr(verdict, "updated_input", None)
            if isinstance(updated, dict) and updated is not args:
                args.clear()
                args.update(updated)
            return None
        message = getattr(verdict, "message", "") or "refused"
        if message == USER_DENIED_MESSAGE or getattr(session, "_cancel_requested", False):
            return Outcome.stop(f"{name} was refused by the user")
        return Outcome.error(f"{name} refused: {message}")

    return gate


def _unity_client_factory(conversation_id: Optional[int]):
    async def factory():
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        url = unity_mcp_manager.mcp_url()
        if not url:
            return None
        return script_tool.unity_bridge_client(
            url, conversation_id=conversation_id,
            secret_reader=lambda: unity_mcp_manager.api_headers().get("X-API-Key", ""))
    return factory


def _stdio_groups(session) -> list:
    """Mail, browser (read-only tools) and connectors, from the very entries this session was given.
    Any other server in `mcp_servers` (computer use among them) is never a group."""
    from agentic import mailbox
    from browser_use import registration as browser_registration
    from connectors import registration as connectors_registration
    from unity_ai_mcp.tools.script_tools import connector_refusal_marks
    servers = getattr(session, "mcp_servers", None) or {}
    plan = (
        (mailbox.CLAUDE_SERVER_NAME, "Chat mail", {"keep": lambda tool: tool.name in mailbox.MAIL_TOOLS}),
        (browser_registration.SERVER_NAME, "Browser", {"keep": script_tool.read_only_tool}),
        (connectors_registration.SERVER_NAME, "Connectors", {"stop_marks": connector_refusal_marks()}),
    )
    groups = []
    for server, label, options in plan:
        entry = servers.get(server)
        if isinstance(entry, dict) and entry.get("command"):
            groups.append(script_tool.StdioServerGroup(label, entry, gate=claude_gate(session, server), **options))
    return groups


def groups_for(session) -> list:
    """Claude's groups (its file and shell tools live inside its CLI, out of reach)."""
    cid = getattr(session, "conversation_id", None)
    cid = cid if type(cid) is int and cid > 0 else None
    return [UnityMCPGroup(_unity_client_factory(cid), gate=claude_gate(session),
                          hidden=hidden_unity_tools(session)), *_stdio_groups(session)]


def description() -> str:
    from providers.unity_script_tools import UNITY_SCRIPT_WRITE_TOOLS
    line = (script_tool.UNITY_TOOLS_LINE + " (mcp__unityMCP__<name>), except the C# script writers ("
            + ", ".join(UNITY_SCRIPT_WRITE_TOOLS) + "): write .cs files with Write/Edit")
    return script_tool.tool_description(
        [], [line, *_mail_lines(), *script_tool.browser_lines(), script_tool.CONNECTORS_TOOLS_LINE])


def _mail_lines() -> list:
    """The gamachineMail server's mail tools, from their own registration."""
    from mcp.server.mcpserver import MCPServer
    from unity_ai_mcp.tools.mailbox_tools import register_mailbox_tools
    probe = MCPServer(name="mail-probe")
    register_mailbox_tools(probe)
    return [script_tool.signature(script_tool.ToolSpec(t.name, t.description or "", t.parameters or {}))
            for t in probe._tool_manager.list_tools()]


async def run_for_session(session, args: Any) -> tuple:
    args = args if isinstance(args, dict) else {}
    return await script_tool.run_script(args.get("code"), groups_for(session),
                                        getattr(session, "cwd", "") or "", timeout=args.get("timeout"))


def session_entry(session) -> Optional[dict]:
    """The in-process `mcp_servers` entry for one Claude session, or None without a chat id."""
    cid = getattr(session, "conversation_id", None)
    if type(cid) is not int or cid <= 0:
        return None
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except ImportError:
        return None

    @tool(script_tool.TOOL_NAME, description(), INPUT_SCHEMA)
    async def run_script(args):
        text, is_error = await run_for_session(session, args)
        return {"content": [{"type": "text", "text": text}], "is_error": is_error}

    return create_sdk_mcp_server(SERVER_NAME, tools=[run_script])
