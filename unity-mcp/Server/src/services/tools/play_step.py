"""play_step: advance a paused play_session by N frames with scripted input."""
from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools._playtest_common import (
    GROUP,
    error,
    is_int,
    is_number,
    parse_list,
    parse_object,
    send,
    shape_image_result,
    string_list,
)
from services.tools.utils import parse_json_payload

MIN_FRAMES = 1
MAX_FRAMES = 3600
INPUT_DO = ("press", "release", "tap")
INPUT_CONTROLS = ("key", "button", "axis")
UNTIL_OPS = ("==", "!=", "<", ">", "<=", ">=")
CAPTURE_MODES = ("none", "end")

# P2 measured 1.1-4.3 ms per stepped frame; autopilot calls and captures add to it.
# 40 ms/frame of budget keeps 3600 frames (~174 s) under the backend's 240 s call cap.
# Sent as timeout_seconds: Unity's own step guard defaults to 25 s (PlaytestStepper.StepSpec)
# and would cut a long step, and the transport wait widens with it.
_BASE_TIMEOUT_S = 30.0
_PER_FRAME_TIMEOUT_S = 0.04


def step_timeout_s(frames: int) -> float:
    return _BASE_TIMEOUT_S + frames * _PER_FRAME_TIMEOUT_S


def _validate_input(items: list, frames: int) -> str | None:
    for i, item in enumerate(items):
        where = f"input[{i}]"
        if not isinstance(item, dict):
            return f"{where} must be an object."
        frame = item.get("frame")
        if not is_int(frame) or not 0 <= frame < frames:
            return f"{where}.frame must be an integer in 0..{frames - 1} (relative, 0-based), got {frame!r}."
        controls = [c for c in INPUT_CONTROLS if item.get(c) is not None]
        if len(controls) != 1:
            return f"{where} needs exactly one of key, button, axis."
        control = item[controls[0]]
        if not isinstance(control, str) or not control.strip():
            return f"{where}.{controls[0]} must be a non-empty string."
        if item.get("do") not in INPUT_DO:
            return f"{where}.do must be one of {', '.join(INPUT_DO)}, got {item.get('do')!r}."
        if item.get("value") is not None and not is_number(item["value"]):
            return f"{where}.value must be a number."
        unknown = set(item) - {"frame", "do", "value", *INPUT_CONTROLS}
        if unknown:
            return f"{where} has unknown keys: {', '.join(sorted(unknown))}."
    return None


def _validate_until(until: dict) -> str | None:
    hook = until.get("hook")
    if not isinstance(hook, str) or not hook.strip():
        return "until.hook must be a non-empty hook name."
    if until.get("op") not in UNTIL_OPS:
        return f"until.op must be one of {' '.join(UNTIL_OPS)}, got {until.get('op')!r}."
    if "value" not in until:
        return "until.value is required."
    return None


def _validate_capture(capture: Any, frames: int) -> tuple[Any, str | None]:
    capture = parse_json_payload(capture)
    if capture is None:
        return "none", None
    if isinstance(capture, str):
        if capture not in CAPTURE_MODES:
            return None, f"capture must be 'none', 'end' or a list of frames, got '{capture}'."
        return capture, None
    if not isinstance(capture, list):
        return None, "capture must be 'none', 'end' or a list of frames."
    if not capture or not all(is_int(f) and 0 <= f < frames for f in capture):
        return None, f"capture frames must be integers in 0..{frames - 1} (relative, 0-based)."
    return capture, None


@mcp_for_unity_tool(
    group=GROUP,
    description=(
        "Advance the paused play_session by exactly `frames` frames (not wall clock), then pause. "
        "input: [{frame (0-based, relative), key|button|axis, do: press|release|tap, value?}]; "
        "key = Input System Key name (space, a, leftArrow), button = mouse0/mouse1 or gamepad "
        "control (buttonSouth), axis = leftStick/x. autopilot: call hook 'autopilot' each frame, its "
        "return is that frame's input. watch: hooks to return in state. until {hook, op, value}: "
        "stop early. capture: none|end|[frames]; 'end' returns the last frame as an image. "
        "-> data {frame, frames_stepped, time, stopped_by: frames|until|error, stop_reason?, "
        "autopilot_error?, autopilot_missing?, state, errors, warnings, first_errors, "
        "captures: [{frame, path}], image?: {frame, path, width, height, full_width, full_height, "
        "mime}}. Failures: {success: false, error}."
    ),
    annotations=ToolAnnotations(title="Play Step", destructiveHint=True),
)
async def play_step(
    ctx: Context,
    frames: Annotated[int, "1..3600"],
    input: Annotated[list[dict[str, Any]] | None, "per-frame input events"] = None,
    autopilot: bool = False,
    watch: Annotated[list[str] | None, "hook names"] = None,
    until: Annotated[dict[str, Any] | None, "{hook, op: == != < > <= >=, value}"] = None,
    capture: Annotated[Literal["none", "end"] | list[int], "none, end, or relative frames"] = "none",
) -> dict[str, Any] | ToolResult:
    if not is_int(frames) or not MIN_FRAMES <= frames <= MAX_FRAMES:
        return error(f"frames must be an integer in {MIN_FRAMES}..{MAX_FRAMES}, got {frames!r}.")
    params: dict[str, Any] = {"frames": frames}

    items, err = parse_list(input, "input")
    if err:
        return error(err)
    if items:
        err = _validate_input(items, frames)
        if err:
            return error(err)
        params["input"] = items

    if not isinstance(autopilot, bool):
        return error("autopilot must be a boolean.")
    if autopilot:
        params["autopilot"] = True

    hooks, err = string_list(watch, "watch")
    if err:
        return error(err)
    if hooks:
        params["watch"] = hooks

    cond, err = parse_object(until, "until")
    if err:
        return error(err)
    if cond is not None:
        err = _validate_until(cond)
        if err:
            return error(err)
        params["until"] = cond

    mode, err = _validate_capture(capture, frames)
    if err:
        return error(err)
    if mode != "none":
        params["capture"] = mode

    params["timeout_seconds"] = step_timeout_s(frames)
    return shape_image_result(await send(ctx, "play_step", params), image_key="image")
