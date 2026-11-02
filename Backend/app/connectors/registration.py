"""Stable per-chat MCP entries for the agent connectors server (CONTRACT.md section 5)."""
import os
import sys

from spawn_env import conversation_env
from connectors import launcher

SERVER_NAME = "gamachineConnectors"
CLAUDE_TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
# The server process reads which connectors to serve and where they are installed from these.
IDS_ENV = "GAMACHINE_CONNECTORS"
ROOT_ENV = "GAMACHINE_CONNECTORS_ROOT"


def server_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "connectors-mcp-server"]
    main_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return [sys.executable, main_py, "connectors-mcp-server"]


def _backend_url() -> str:
    return os.environ.get("UNITYAI_URL", os.environ.get("ANTIGRAVITY_URL", "http://localhost:8000"))


def served() -> tuple:
    """(ids, install root) the server would get now; ids is empty when nothing is enabled and installed.
    Codex compares it to rebuild its thread when the set changes."""
    ids = launcher.ready_ids()
    return (tuple(ids), str(launcher.installer().root) if ids else "")


def _server_env() -> dict | None:
    ids, root = served()
    if not ids:
        return None
    return {IDS_ENV: ",".join(ids), ROOT_ENV: root, "UNITYAI_URL": _backend_url()}


def stdio_entry(conversation_id=None) -> dict | None:
    """Generic command/args/env entry for CLIs that adapt it to their own config shape."""
    env = _server_env()
    if env is None:
        return None
    argv = server_argv()
    return {
        "command": argv[0],
        "args": argv[1:],
        "env": {**conversation_env(conversation_id), **env},
    }


def claude_server_entry(conversation_id) -> dict | None:
    if type(conversation_id) is not int or conversation_id <= 0:
        return None
    entry = stdio_entry(conversation_id)
    return None if entry is None else {"type": "stdio", **entry}


def codex_thread_entry(forward_env) -> dict | None:
    env = _server_env()
    if env is None:
        return None
    argv = server_argv()
    entry = {
        "command": argv[0],
        "args": argv[1:],
        "env": env,
        "default_tools_approval_mode": "approve",
        # blender_open waits up to 60 s for the app; a long execute_blender_code gets the rest.
        "tool_timeout_sec": 180.0,
    }
    if forward_env:
        entry["env_vars"] = list(forward_env)
    return entry
