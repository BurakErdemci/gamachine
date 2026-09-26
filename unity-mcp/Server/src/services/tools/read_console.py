"""
Defines the read_console tool for accessing Unity Editor console messages.
"""
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.compile_status import live_verdict, read_compile_status
from services.tools.utils import coerce_int, coerce_bool, parse_json_payload
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


def _strip_stacktrace_from_list(items: list) -> None:
    """Remove stacktrace fields from a list of log entries."""
    for item in items:
        if isinstance(item, dict) and "stacktrace" in item:
            item.pop("stacktrace", None)


@mcp_for_unity_tool(
    description="Gets messages from or clears the Unity Editor console. Defaults to 10 most recent entries. Use page_size/cursor for paging. Note: For maximum client compatibility, pass count as a quoted string (e.g., '5'). The 'get' action is read-only; 'clear' modifies ephemeral UI state (not project data). An empty error list is not proof of a clean compile: when compilation is running, pending, stale (scripts changed on disk and not yet compiled) or unknown, the response carries a top-level compile_state saying so. After writing .cs files with your own tools call refresh_unity or compile_status first.",
    annotations=ToolAnnotations(
        title="Read Console",
    ),
)
async def read_console(
    ctx: Context,
    action: Annotated[Literal['get', 'clear'],
                      "Get or clear the Unity Editor console. Defaults to 'get' if omitted."] | None = None,
    types: Annotated[list[Literal['error', 'warning',
                                  'log', 'all']] | str,
                     "Message types to get (accepts list or JSON string)"] | None = None,
    count: Annotated[int | str,
                     "Max messages to return in non-paging mode (accepts int or string, e.g., 5 or '5'). Ignored when paging with page_size/cursor."] | None = None,
    filter_text: Annotated[str, "Text filter for messages"] | None = None,
    page_size: Annotated[int | str,
                         "Page size for paginated console reads. Defaults to 50 when omitted."] | None = None,
    cursor: Annotated[int | str,
                      "Opaque cursor for paging (0-based offset). Defaults to 0."] | None = None,
    format: Annotated[Literal['plain', 'detailed',
                              'json'], "Output format"] | None = None,
    include_stacktrace: Annotated[bool | str,
                                  "Include stack traces in output (accepts true/false or 'true'/'false')"] | None = None,
) -> dict[str, Any]:
    # Get active instance from session state
    # Removed session_state import
    unity_instance = await get_unity_instance_from_context(ctx)
    # Set defaults if values are None
    action = action if action is not None else 'get'
    
    # Parse types if it's a JSON string (handles client compatibility issue #561)
    if isinstance(types, str):
        types = parse_json_payload(types)
    # Validate types is a list after parsing
    if types is not None and not isinstance(types, list):
        return {
            "success": False,
            "message": (
                f"types must be a list, got {type(types).__name__}. "
                "If passing as JSON string, use format: '[\"error\", \"warning\"]'"
            )
        }
    if types is not None:
        allowed_types = {"error", "warning", "log", "all"}
        normalized_types = []
        for entry in types:
            if not isinstance(entry, str):
                return {
                    "success": False,
                    "message": f"types entries must be strings, got {type(entry).__name__}"
                }
            normalized = entry.strip().lower()
            if normalized not in allowed_types:
                return {
                    "success": False,
                    "message": (
                        f"invalid types entry '{entry}'. "
                        f"Allowed values: {sorted(allowed_types)}"
                    )
                }
            normalized_types.append(normalized)
        types = normalized_types
    else:
        types = ['error', 'warning', 'log']
    
    format = format if format is not None else 'plain'
    # Coerce booleans defensively (strings like 'true'/'false')

    include_stacktrace = coerce_bool(include_stacktrace, default=False)
    coerced_page_size = coerce_int(page_size, default=None)
    coerced_cursor = coerce_int(cursor, default=None)

    # Normalize action if it's a string
    if isinstance(action, str):
        action = action.lower()

    # Coerce count defensively (string/float -> int).
    # Important: leaving count unset previously meant "return all console entries", which can be extremely slow
    # (and can exceed the plugin command timeout when Unity has a large console).
    # To keep the tool responsive by default, we cap the default to a reasonable number of most-recent entries.
    # If a client truly wants everything, it can pass count="all" (or count="*") explicitly.
    if isinstance(count, str) and count.strip().lower() in ("all", "*"):
        count = None
    else:
        count = coerce_int(count)

    if action == "get" and count is None:
        count = 10

    # Prepare parameters for the C# handler
    params_dict = {
        "action": action,
        "types": types,
        "count": count,
        "filterText": filter_text,
        "pageSize": coerced_page_size,
        "cursor": coerced_cursor,
        "format": format.lower() if isinstance(format, str) else format,
        "includeStacktrace": include_stacktrace
    }

    # Remove None values unless it's 'count' (as None might mean 'all')
    params_dict = {k: v for k, v in params_dict.items()
                   if v is not None or k == 'count'}

    # Add count back if it was None, explicitly sending null might be important for C# logic
    if 'count' not in params_dict:
        params_dict['count'] = None

    # Use centralized retry helper with instance routing
    resp = await send_with_unity_instance(async_send_command_with_retry, unity_instance, "read_console", params_dict)
    if isinstance(resp, dict) and resp.get("success") and not include_stacktrace:
        # Strip stacktrace fields from returned lines if present
        try:
            data = resp.get("data")
            if isinstance(data, dict):
                for key in ("lines", "items"):
                    if key in data and isinstance(data[key], list):
                        _strip_stacktrace_from_list(data[key])
                        break
            elif isinstance(data, list):
                _strip_stacktrace_from_list(data)
        except Exception:
            pass
    if isinstance(resp, dict) and resp.get("success") and action == "get":
        compile_state = await _compile_state(unity_instance)
        if compile_state is not None:
            resp["compile_state"] = compile_state
    return resp if isinstance(resp, dict) else {"success": False, "message": str(resp)}


async def _compile_state(unity_instance: str | None) -> dict[str, Any] | None:
    """None when the last compile is final and clean; otherwise why the console
    may not show the real compile result.

    The console is read live, so before or during a compile it reports 0 errors:
    3/3 probes after an agent wrote a broken .cs with its own file tool, and 5/5
    right after refresh_unity on a larger project (11 Sep 2026).
    """
    read = await read_compile_status(
        unity_instance, scan=True, attempts=2,
        send_fn=async_send_command_with_retry, route_fn=send_with_unity_instance)
    verdict = live_verdict(read.status, read.problem)
    if verdict["verdict"] == "clean":
        return None
    state: dict[str, Any] = {"verdict": verdict["verdict"], "note": verdict.get("note")}
    if verdict["verdict"] == "errors":
        count = verdict.get("error_count")
        state["error_count"] = count
        state["errors"] = (verdict.get("errors") or [])[:5]
        if not state["note"]:
            state["note"] = f"the last compile failed with {count} error(s)"
    changed = verdict.get("scripts_changed_since_compile")
    if isinstance(changed, dict) and changed.get("count"):
        state["scripts_changed_since_compile"] = changed
    return state
