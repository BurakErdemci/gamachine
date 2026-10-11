"""Read the target editor's window and dialogs outside Unity's main loop."""

import asyncio
import sys
from typing import Annotated, Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from core.config import config
from services.registry import mcp_for_unity_tool
from services.tools._playtest_common import shape_image_result
from services.tools.unity_dialog import _NO_PROJECT, _target_project
from transport.unity_dialog_probe import dialogs_for_project, find_unity_dialogs
from transport.unity_window_capture import CaptureError, capture_window, find_unity_main_windows


@mcp_for_unity_tool(
    name="unity_window_capture",
    unity_target=None,
    group="core",
    description=(
        "Image of the target project's Unity editor window, captured outside Unity so it works "
        "while the editor is blocked (dialogs, builds). Shows Console, Inspector, Game view "
        "and layout. Also lists the project's open native dialogs. Writes no file."
    ),
    annotations=ToolAnnotations(title="Unity Window Capture", readOnlyHint=True, destructiveHint=False),
)
async def unity_window_capture(
    ctx: Context,
    max_size: Annotated[int, Field(ge=256, le=1920, description="Longest side of the returned image, in pixels")] = 1280,
) -> Any:
    # In remote-hosted mode, the server machine is not the caller's machine.
    if config.http_remote_hosted:
        return {"success": False, "error": "unity_window_capture is disabled when the server is remote-hosted: it would read the server machine's desktop, not yours."}
    if sys.platform != "win32":
        return {"success": False, "error": "unity_window_capture is only implemented on Windows so far."}
    if isinstance(max_size, bool) or not isinstance(max_size, int) or not 256 <= max_size <= 1920:
        return {"success": False, "error": "max_size must be an integer between 256 and 1920."}
    project = await _target_project(ctx)
    if not project:
        return {"success": False, "error": _NO_PROJECT}
    windows = await asyncio.to_thread(find_unity_main_windows, project)
    if len(windows) > 1:
        return {
            "success": False,
            "error": f"Several Unity editor windows show project '{project}'; close the other editor or rename one project before capturing.",
        }
    if not windows:
        return {"success": False, "error": f"No Unity editor window found for project '{project}'."}
    window = windows[0]
    dialogs = [
        {"title": dialog["title"], "buttons": dialog["buttons"]}
        for dialog in dialogs_for_project(await asyncio.to_thread(find_unity_dialogs), project)
    ]
    try:
        capture = await asyncio.to_thread(capture_window, window["hwnd"], max_size)
    except CaptureError as exc:
        return {"success": False, "error": str(exc), "data": {"dialogs": dialogs}}
    return shape_image_result({
        "success": True,
        "message": "Captured the Unity editor window.",
        "data": {**capture, "title": window["title"], "dialogs": dialogs},
    })
