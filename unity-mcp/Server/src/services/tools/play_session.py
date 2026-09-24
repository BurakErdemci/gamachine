"""play_session: deterministic play mode session for stepping, input injection and capture."""
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools._playtest_common import GROUP, error, is_int, is_number, send, send_long
from services.tools.preflight import preflight

ALL_ACTIONS = ["start", "stop", "status"]

# Unity's own limits: start reads timeout_seconds (default 60, PlaySessionTool.cs),
# stop is fixed at 60 s (PlaytestSession.BeginStop). P2: the game is ready after 5-6 s.
START_TIMEOUT_S = 60.0
STOP_TIMEOUT_S = 60.0

MAX_FIXED_DT = 1.0


def _confirm_start(response: dict[str, Any]) -> dict[str, Any]:
    # A lost reply may have carried the start outcome (Unity hands it over once), so the
    # answer can be a plain status; the session flag says whether the start took effect.
    if response.get("success") is not True:
        return response
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    if data.get("session") is True:
        return response
    return {
        "success": False,
        "error": "play_session start: the reply was lost and no session is running; call start again.",
        "data": data,
    }


def _confirm_stop(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("success") is not True:
        return response
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    if data.get("playing") is False:
        return response
    return {
        "success": False,
        "error": "play_session stop: the reply was lost and Unity is still in play mode; call stop again.",
        "data": data,
    }


@mcp_for_unity_tool(
    group=GROUP,
    description=(
        "Deterministic play mode session. start: optional scene load (refused if the open scene "
        "has unsaved changes), enter play, wait for the domain reload and first frame, fix "
        "time step and seed, call hook level.restart {seed} if present, stay paused -> "
        "data {playing, paused, frame, time, scene, session, hooks, seed, fixed_dt, restarted, "
        "input}; ~6 s, the tool waits. stop: restore settings, exit play -> data {playing: false, "
        "..., restored_scene}. status -> {playing, paused, frame, time, scene, session, hooks}. "
        "Failures: {success: false, error, data?}."
    ),
    annotations=ToolAnnotations(title="Play Session", destructiveHint=True),
)
async def play_session(
    ctx: Context,
    action: Literal["start", "stop", "status"],
    scene: Annotated[str | None, "start: scene to load, Assets/... .unity"] = None,
    seed: Annotated[int, "start: Random.InitState seed"] = 0,
    fixed_dt: Annotated[float, "start: seconds per frame (capture and fixed delta time)"] = 1 / 60,
    paused: Annotated[bool, "start: stay paused after the first frame"] = True,
) -> dict[str, Any]:
    action = (action or "").lower()
    if action not in ALL_ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(ALL_ACTIONS)}.")

    if action == "status":
        return await send(ctx, "play_session", {"action": "status"})
    if action == "stop":
        return await send_long(
            ctx, "play_session", {"action": "stop"},
            timeout_s=STOP_TIMEOUT_S, confirm_after_loss=_confirm_stop,
        )

    params: dict[str, Any] = {"action": "start"}
    if scene is not None:
        scene = scene.strip().replace("\\", "/")
        if not scene.startswith("Assets/") or not scene.endswith(".unity") or ".." in scene.split("/"):
            return error(f"scene must be a project path like 'Assets/Scenes/Main.unity', got '{scene}'.")
        params["scene"] = scene
    if not is_int(seed):
        return error("seed must be an integer.")
    if not is_number(fixed_dt) or not 0 < fixed_dt <= MAX_FIXED_DT:
        return error(f"fixed_dt must be a number in (0, {MAX_FIXED_DT:g}] seconds, got {fixed_dt!r}.")
    if not isinstance(paused, bool):
        return error("paused must be a boolean.")
    params.update({"seed": seed, "fixed_dt": float(fixed_dt), "paused": paused})

    gate = await preflight(ctx, requires_no_tests=True, wait_for_no_compile=True)
    if isinstance(gate, MCPResponse):
        return gate.model_dump()

    return await send_long(
        ctx, "play_session", params,
        timeout_s=START_TIMEOUT_S, unity_timeout_s=START_TIMEOUT_S, confirm_after_loss=_confirm_start,
    )
