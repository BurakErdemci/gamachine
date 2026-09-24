"""game_hooks: read and call the hooks a game exposes with [GameHook] / GameHooks.State/Action."""
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools._playtest_common import GROUP, error, parse_object, send, string_list

ALL_ACTIONS = ["list", "get", "call"]


@mcp_for_unity_tool(
    group=GROUP,
    description=(
        "Game hooks the game opts into ([GameHook] attribute or GameHooks.State/Action). "
        "list -> data.hooks [{name, kind: state|action, type}]; "
        "get {names?} -> data.values {name: value} (all state hooks if names omitted); "
        "call {name, args?} -> data.result. Vectors/quaternions/colors are arrays, enums are names. "
        "Unknown name -> {success: false, error, data {name, close_matches}}. "
        "Values are live game state in play mode (see play_session)."
    ),
    annotations=ToolAnnotations(title="Game Hooks"),
)
async def game_hooks(
    ctx: Context,
    action: Literal["list", "get", "call"],
    names: Annotated[list[str] | None, "get: hook names"] = None,
    name: Annotated[str | None, "call: action hook name"] = None,
    args: Annotated[dict[str, Any] | None, "call: arguments by parameter name"] = None,
) -> dict[str, Any]:
    action = (action or "").lower()
    if action not in ALL_ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(ALL_ACTIONS)}.")

    params: dict[str, Any] = {"action": action}
    if action == "get":
        parsed, err = string_list(names, "names")
        if err:
            return error(err)
        if parsed:
            params["names"] = parsed
    elif action == "call":
        if not isinstance(name, str) or not name.strip():
            return error("call requires 'name' (an action hook name; use action='list').")
        params["name"] = name.strip()
        parsed_args, err = parse_object(args, "args")
        if err:
            return error(err)
        if parsed_args is not None:
            params["args"] = parsed_args

    return await send(ctx, "game_hooks", params)
