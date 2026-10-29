"""Stable per-chat MCP entries; driver locations are resolved by the server."""
import os
import sys

from spawn_env import conversation_env
from . import settings

SERVER_NAME = "gamachineComputer"
CLAUDE_TOOL_PREFIX = f"mcp__{SERVER_NAME}__"


def server_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "computer-mcp-server"]
    main_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return [sys.executable, main_py, "computer-mcp-server"]


def stdio_entry(conversation_id=None) -> dict | None:
    """Generic command/args/env entry for CLIs that adapt it to their own config shape."""
    if not settings.enabled_now():
        return None
    argv = server_argv()
    return {
        "command": argv[0],
        "args": argv[1:],
        "env": {
            **conversation_env(conversation_id),
            "UNITYAI_URL": os.environ.get(
                "UNITYAI_URL", os.environ.get("ANTIGRAVITY_URL", "http://localhost:8000")),
        },
    }


def claude_server_entry(conversation_id) -> dict | None:
    if type(conversation_id) is not int or conversation_id <= 0:
        return None
    entry = stdio_entry(conversation_id)
    return None if entry is None else {"type": "stdio", **entry}


def codex_thread_entry(forward_env) -> dict | None:
    if not settings.enabled_now():
        return None
    argv = server_argv()
    entry = {
        "command": argv[0],
        "args": argv[1:],
        "env": {
            "UNITYAI_URL": os.environ.get(
                "UNITYAI_URL", os.environ.get("ANTIGRAVITY_URL", "http://localhost:8000")),
        },
        "default_tools_approval_mode": "approve",
        # Longer than approval_bridge's 150 s card wait so Codex does not give up on a pending card.
        "tool_timeout_sec": 180.0,
    }
    if forward_env:
        entry["env_vars"] = list(forward_env)
    return entry
