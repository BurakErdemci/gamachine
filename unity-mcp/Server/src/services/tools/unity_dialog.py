"""Inspect and answer blocking native dialogs without the Unity main loop."""

import asyncio
from time import monotonic
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from core.config import config
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.plugin_hub import PluginHub
from transport.unity_dialog_probe import dialogs_for_project, find_unity_dialogs, press_dialog_button

_NO_PROJECT = (
    "No Unity project is targeted: pass unity_instance (Name@hash) or connect "
    "exactly one Unity editor."
)


async def _target_project(ctx: Context) -> str | None:
    """Project name of the editor this call targets, or None when unknown."""
    unity_instance = await get_unity_instance_from_context(ctx)
    user_id = None
    if config.http_remote_hosted and ctx is not None:
        user_id = await ctx.get_state("user_id")
    sessions = (await PluginHub.get_sessions(user_id=user_id)).sessions.values()
    if not unity_instance:
        only = list(sessions)
        return only[0].project if len(only) == 1 and only[0].project else None
    target_hash = unity_instance.rpartition("@")[2] if "@" in unity_instance else unity_instance
    for session in sessions:
        if target_hash and session.hash == target_hash:
            return session.project or None
    if "@" not in unity_instance:
        named = [session for session in sessions if session.project == unity_instance]
        if len(named) == 1:
            return named[0].project
    return None


@mcp_for_unity_tool(
    name="unity_dialog",
    unity_target=None,
    group="core",
    description=(
        "Lists or answers a native Unity editor dialog that blocks the editor. "
        "Pressing a button is a decision on the user's work and can save or discard changes."
    ),
    annotations=ToolAnnotations(title="Unity Dialog", destructiveHint=True),
)
async def unity_dialog(
    ctx: Context,
    action: Literal["list", "press"],
    title: Annotated[str | None, "press: exact dialog title; required when several are open"] = None,
    button: Annotated[str | None, "press: exact button name, case-insensitive"] = None,
) -> dict[str, Any]:
    if action not in ("list", "press"):
        return {"success": False, "error": "Unknown action. Valid actions: list, press."}
    if action == "press" and not button:
        return {"success": False, "error": "A button name is required for press."}

    project = await _target_project(ctx)
    if not project:
        if action == "list":
            return {"success": True, "data": {"dialogs": [], "note": _NO_PROJECT}}
        return {"success": False, "error": _NO_PROJECT}

    async def scoped_dialogs() -> list[dict]:
        return dialogs_for_project(await asyncio.to_thread(find_unity_dialogs), project)

    dialogs = await scoped_dialogs()
    if action == "list":
        return {"success": True, "data": {"dialogs": [
            {"title": dialog["title"], "buttons": dialog["buttons"], "project": dialog["project"]}
            for dialog in dialogs
        ]}}
    if not dialogs:
        return {"success": False, "error": "No Unity dialogs are open."}
    if title is not None:
        matches = [dialog for dialog in dialogs if dialog["title"] == title]
        if not matches:
            return {"success": False, "error": f'Dialog title "{title}" was not found.'}
        if len(matches) != 1:
            return {"success": False, "error": f'Several dialogs have title "{title}"; selection is ambiguous.'}
        dialog = matches[0]
    elif len(dialogs) != 1:
        return {"success": False, "error": "Several Unity dialogs are open; supply an exact title."}
    else:
        dialog = dialogs[0]

    choices = ", ".join(dialog["buttons"]) or "none"
    listed_button = next((text for text in dialog["buttons"] if text.casefold() == button.casefold()), None)
    if listed_button is None:
        return {"success": False, "error": f'Button "{button}" is not an exact match. Available buttons: {choices}.'}
    if not await asyncio.to_thread(press_dialog_button, dialog["hwnd"], listed_button):
        return {"success": False, "error": f'Could not press "{listed_button}". Available buttons: {choices}.'}

    deadline = monotonic() + 3.0
    closed = False
    while True:
        current = await scoped_dialogs()
        if not any(item["hwnd"] == dialog["hwnd"] for item in current):
            closed = True
            break
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.1, remaining))
    return {"success": True, "data": {
        "pressed": listed_button, "title": dialog["title"], "closed": closed,
    }}
