"""play_capture: render the current play_session frame as one inline image."""
from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.server.server import ToolResult
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools._playtest_common import GROUP, error, is_int, send, shape_image_result

MIN_SIZE = 16
MAX_SIZE = 1280
FORMATS = ("jpeg", "png")


@mcp_for_unity_tool(
    group=GROUP,
    description=(
        "Render the game camera at the current (paused) play_session frame. Returns one image "
        "(longest side <= max_size) plus data {frame, path (full-res PNG under "
        "Library/GamachineCaptures/), width, height, full_width, full_height, camera, paused, "
        "render_ms, mime}. Needs play mode. Failures: {success: false, error}."
    ),
    annotations=ToolAnnotations(title="Play Capture", destructiveHint=False),
)
async def play_capture(
    ctx: Context,
    max_size: Annotated[int, "longest side of the returned image, px, 16..1280"] = 640,
    format: Literal["jpeg", "png"] = "jpeg",
    camera: Annotated[str | None, "camera name; default main camera"] = None,
) -> dict[str, Any] | ToolResult:
    if not is_int(max_size) or not MIN_SIZE <= max_size <= MAX_SIZE:
        return error(f"max_size must be an integer in {MIN_SIZE}..{MAX_SIZE}, got {max_size!r}.")
    fmt = (format or "").lower()
    if fmt not in FORMATS:
        return error(f"format must be one of {', '.join(FORMATS)}, got {format!r}.")
    params: dict[str, Any] = {"max_size": max_size, "format": fmt}
    if camera is not None:
        if not isinstance(camera, str) or not camera.strip():
            return error("camera must be a non-empty camera name.")
        params["camera"] = camera.strip()
    return shape_image_result(await send(ctx, "play_capture", params))
