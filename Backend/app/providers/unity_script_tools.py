"""Unity MCP tools a Claude model must not call, as `mcp__unityMCP__<tool>` names.

C# files go through Claude's own file tools, which pass the approval card and the
backend's file guard (`unity_file_guard`: .meta and Unity YAML assets); the
unity-mcp server only refuses .meta. Every tool that writes or deletes a .cs file
is listed, by the server's own ledger (unity-mcp/Server/src/services/registry/
tool_actions.json), not only `manage_script`: the ban named that one tool until
the 26 Sep 2026 audit found four aliases reaching the same writes.
`batch_execute` is listed because it can carry any of them as a nested command.
One list for every Claude entry point, so the chat and the CLI paths cannot drift.
"""

UNITY_SCRIPT_WRITE_TOOLS = (
    "manage_script",
    "apply_text_edits",
    "create_script",
    "delete_script",
    "script_apply_edits",
    "batch_execute",
)

DISALLOWED_UNITY_TOOLS = tuple(f"mcp__unityMCP__{name}" for name in UNITY_SCRIPT_WRITE_TOOLS)
