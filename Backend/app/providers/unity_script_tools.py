"""Unity MCP tools a Claude model must not call, as `mcp__unityMCP__<tool>` names.

Script writers are banned on every Claude path (`UNITY_SCRIPT_WRITE_TOOLS` /
`DISALLOWED_UNITY_TOOLS`). C# files go through Claude's own file tools, which
pass the approval card and the backend's file guard (`unity_file_guard`: .meta
and Unity YAML assets); the unity-mcp server only refuses .meta. Every tool
that writes or deletes a .cs file is listed, by the server's own ledger
(unity-mcp/Server/src/services/registry/tool_actions.json), not only
`manage_script`: the ban named that one tool until the 26 Sep 2026 audit found
four aliases reaching the same writes.

`batch_execute` is banned only on the one-shot CLI path
(`CLI_DISALLOWED_UNITY_TOOLS`) and gated by `batch_carries_script_write` on
the chat (SDK) path, since a nested command can carry a script writer.
"""

import re
from collections.abc import Mapping
from typing import Any, Optional

UNITY_SCRIPT_WRITE_TOOLS = (
    "manage_script",
    "apply_text_edits",
    "create_script",
    "delete_script",
    "script_apply_edits",
)

DISALLOWED_UNITY_TOOLS = tuple(f"mcp__unityMCP__{name}" for name in UNITY_SCRIPT_WRITE_TOOLS)

# The one-shot CLI path has no argument gate, so the whole tool stays banned there.
CLI_DISALLOWED_UNITY_TOOLS = DISALLOWED_UNITY_TOOLS + ("mcp__unityMCP__batch_execute",)

_UNITY_PREFIX = "mcp__unityMCP__"
_UNITY_MAX_DEPTH = 8
_PLAIN_TOOL_RE = re.compile(r"[A-Za-z0-9_]+")


def _fold(key: Any) -> Optional[str]:
    return key.replace("_", "").lower() if isinstance(key, str) else None


def batch_carries_script_write(args: Any, _depth: int = 0) -> bool:
    """Return True when a batch_execute payload is malformed or carries a script-writing tool."""
    if not isinstance(args, Mapping) or _depth >= _UNITY_MAX_DEPTH:
        return True
    cmd_lists = [v for k, v in args.items() if _fold(k) == "commands"]
    if len(cmd_lists) != 1 or not isinstance(cmd_lists[0], (list, tuple)) or not cmd_lists[0]:
        return True
    for command in cmd_lists[0]:
        if not isinstance(command, Mapping):
            return True
        tools = [v for k, v in command.items() if _fold(k) == "tool"]
        params_list = [v for k, v in command.items() if _fold(k) == "params"]
        if len(tools) != 1 or not isinstance(tools[0], str) or len(params_list) > 1:
            return True
        params = params_list[0] if params_list else None
        if params is not None and not isinstance(params, Mapping):
            return True
        tool = tools[0]
        bare = tool[len(_UNITY_PREFIX):] if tool.startswith(_UNITY_PREFIX) else tool
        # Refuse non-identifier tool names: the editor resolves "$[i]" references inside nested commands before dispatch.
        if not _PLAIN_TOOL_RE.fullmatch(bare):
            return True
        if bare in UNITY_SCRIPT_WRITE_TOOLS:
            return True
        if bare == "batch_execute":
            if batch_carries_script_write({} if params is None else params, _depth + 1):
                return True
    return False
