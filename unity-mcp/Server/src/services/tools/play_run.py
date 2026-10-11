"""Start and inspect a background play of the current deterministic session."""
import asyncio
import math
import time
from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools._playtest_common import (
    GROUP, error, is_int, is_number, parse_list, parse_object, send,
    shape_image_result, string_list,
)

monotonic = time.monotonic
sleep = asyncio.sleep


def _number(value: Any, low: float, high: float | None = None) -> bool:
    try:
        return (is_number(value) and value >= low and (high is None or value <= high)
                and math.isfinite(value))
    except OverflowError:
        return False


def _images(response):
    return shape_image_result(response, extra_image_keys=("last_frame",))


@mcp_for_unity_tool(
    group=GROUP,
    description=(
        "Background play of an active play_session, up to 1800 game seconds per run. "
        "start returns immediately; status reads progress; wait polls for at most 200 wall "
        "seconds; stop finishes the current job. Optional seeds (1..50) restart through "
        "level.restart, with a 3600 game-second batch limit. Returns timelines, errors, "
        "editor-side timing, final state, a contact sheet and the final frame. "
        "Input is applied only in each run's first segment (at most 600 frames, "
        "possibly shorter at the first sample or capture)."
    ),
    annotations=ToolAnnotations(title="Play Run", destructiveHint=True),
)
async def play_run(
    ctx: Context,
    action: Literal["start", "status", "wait", "stop"] = "start",
    seconds: Annotated[float, "game seconds per run, 1..1800"] = 120,
    seeds: list[int] | None = None,
    autopilot: bool = True,
    input: list[dict[str, Any]] | None = None,
    until: dict[str, Any] | None = None,
    stop_on_error: bool = True,
    watch: list[str] | None = None,
    sample_seconds: float = 5,
    capture_every_seconds: float | None = None,
    camera: str | None = None,
    timeout_seconds: float = 180,
) -> dict[str, Any] | ToolResult:
    if not isinstance(action, str) or action.lower() not in ("start", "status", "wait", "stop"):
        return error("action must be start, status, wait or stop.")
    action = action.lower()
    if action in ("status", "stop"):
        return _images(await send(ctx, "play_run", {"action": action}))
    if action == "wait":
        if not _number(timeout_seconds, -math.inf):
            return error("timeout_seconds must be a finite number.")
        budget = max(1, min(200, timeout_seconds))
        deadline = monotonic() + budget
        last = {"success": True, "data": {"status": "running"}}
        while monotonic() < deadline:
            try:
                response = await asyncio.wait_for(
                    send(ctx, "play_run", {"action": "status"}),
                    timeout=max(0.001, deadline - monotonic()),
                )
            except asyncio.TimeoutError:
                break
            last = response
            data = response.get("data")
            status = data.get("status") if isinstance(data, dict) else None
            if response.get("success") is False or status == "done":
                return _images(response)
            if response.get("_mcp_status") != "pending" and status != "running":
                return _images(response)
            await sleep(min(1, max(0, deadline - monotonic())))
        return _images({**last, "message": f"play_run still running after {budget:g} s; call wait again."})

    if not _number(seconds, 1, 1800):
        return error("seconds must be a finite number in 1..1800.")
    batch, err = parse_list(seeds, "seeds")
    if err:
        return error(err)
    if batch is not None:
        if not 1 <= len(batch) <= 50 or not all(is_int(s) and -(2**31) <= s < 2**31 for s in batch):
            return error("seeds must contain 1..50 signed 32-bit integers.")
        if seconds * len(batch) > 3600:
            return error("seconds * number of seeds must be <= 3600.")
    if not isinstance(autopilot, bool) or not isinstance(stop_on_error, bool):
        return error("autopilot and stop_on_error must be booleans.")
    if not _number(sample_seconds, 0.5, 60):
        return error("sample_seconds must be a finite number in 0.5..60.")
    if capture_every_seconds is not None and not _number(capture_every_seconds, 1):
        return error("capture_every_seconds must be a finite number >= 1.")
    if camera is not None and (not isinstance(camera, str) or not camera.strip()):
        return error("camera must be a non-empty string.")
    hooks, err = string_list(watch, "watch")
    if err or (hooks is not None and len(hooks) > 20):
        return error(err or "watch must contain at most 20 hook names.")
    items, err = parse_list(input, "input")
    if err:
        return error(err)
    # Import only at call time: importing another tool during discovery changes registration order.
    from services.tools.play_step import _validate_input, _validate_until

    if items:
        err = _validate_input(items, 600)
        if err:
            return error(err)
        if any(item.get("value") is not None and not _number(item["value"], -3.402823466e38, 3.402823466e38) for item in items):
            return error("input.value must be a finite float.")
    condition, err = parse_object(until, "until")
    if err:
        return error(err)
    if condition is not None:
        err = _validate_until(condition)
        if err:
            return error(err)
    params = {"action": "start", "seconds": seconds, "autopilot": autopilot,
              "stop_on_error": stop_on_error, "sample_seconds": sample_seconds}
    for key, value in (("seeds", batch), ("input", items), ("until", condition),
                       ("watch", hooks), ("capture_every_seconds", capture_every_seconds), ("camera", camera)):
        if value is not None:
            params[key] = value
    return _images(await send(ctx, "play_run", params))
