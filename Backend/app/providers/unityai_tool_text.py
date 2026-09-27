"""Shared lines of the provider instructions that describe the unityai tools.

Measured 27 Sep 2026: while one unityai server held its log, every other one
failed to start; an OpenCode chat left without the tools told the user the
terminal was "disabled by policy", and an agy branch looked for the mail tool
on unityMCP. Every provider's instructions now list the mail tools and say
what to tell the user when the tools are missing. Kept in one place so the
providers cannot drift apart.
"""
from typing import Callable

MAIL_TOOLS = ("list_chats", "send_chat_message")

TOOLS_MISSING_HINT = (
    "If the unityai tools are not in your tool list, tell the user the Gamachine\n"
    "tool server (unityai) could not connect — never say a policy disabled them.\n"
)


def on_unityai_server(tool: str) -> str:
    """For a CLI whose model-facing MCP tool names are not measured here."""
    return f"{tool} (unityai MCP server)"


def mcp_double_underscore(tool: str) -> str:
    """Claude Code, Kimi and Codex show MCP tools as mcp__<server>__<tool>."""
    return f"mcp__unityai__{tool}"


def mail_and_fallback(name: Callable[[str], str]) -> str:
    """The mail line plus the missing-tools sentence, in the CLI's naming."""
    return ("- Notes to other Gamachine chats: "
            + ", ".join(name(t) for t in MAIL_TOOLS) + "\n"
            + TOOLS_MISSING_HINT)
