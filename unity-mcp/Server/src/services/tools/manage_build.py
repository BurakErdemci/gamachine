"""Build management — player builds, platform switching, settings, batch automation."""

import asyncio
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
from time import monotonic
from typing import Annotated, Any, Optional

from fastmcp import Context
from mcp.types import ToolAnnotations

from core.config import config
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_bool, parse_json_payload
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.plugin_hub import PluginHub
from transport.unity_dialog_probe import dialogs_for_project, find_unity_dialogs

POLL_INTERVAL_SECONDS = 2

ALL_ACTIONS = [
    "build",
    "status",
    "wait",
    "platform",
    "settings",
    "scenes",
    "profiles",
    "batch",
    "cancel",
]


async def _target_project_path(ctx: Context) -> str | None:
    """Resolve the targeted editor's root without contacting Unity."""
    try:
        unity_instance = await get_unity_instance_from_context(ctx)
        user_id = None
        if config.http_remote_hosted and ctx is not None:
            user_id = await ctx.get_state("user_id")
        if not unity_instance:
            return await PluginHub.get_project_path(None, user_id=user_id)
        target_hash = unity_instance.rpartition("@")[2] if "@" in unity_instance else unity_instance
        path = await PluginHub.get_project_path(target_hash, user_id=user_id) if target_hash else None
        if path or "@" in unity_instance:
            return path
        sessions = (await PluginHub.get_sessions(user_id=user_id)).sessions.values()
        named = [session for session in sessions if session.project == unity_instance]
        if len(named) == 1:
            return await PluginHub.get_project_path(named[0].hash, user_id=user_id)
    except Exception:
        return None
    return None


async def _target_project_name(ctx: Context) -> str | None:
    # Imported here: a module-level import registers unity_dialog early and reorders the tool list.
    from services.tools.unity_dialog import _target_project

    try:
        return await _target_project(ctx)
    except Exception:
        return None


def _is_process_alive(pid: int) -> bool:
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return ctypes.get_last_error() == 5  # Access denied does not prove exit.
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False


def _read_status_file(project_path: str, job_id: str | None) -> dict | None:
    if job_id is not None and any(part in job_id for part in ("/", "\\", "..", ":")):
        return None
    try:
        path = Path(project_path) / "Library" / "GamachineBuilds" / (
            f"{job_id}.json" if job_id is not None else "latest.json"
        )
        status = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(status, dict):
            return None
        if status.get("state") in ("pending", "building") and status.get("editor_pid"):
            if not _is_process_alive(status["editor_pid"]):
                status["state"] = "interrupted"
                status["error_message"] = "Unity exited before the build finished."
        return status
    except (OSError, ValueError, UnicodeError):
        return None


async def _wait_for_build(ctx: Context, project_path: str, job_id: str | None, timeout_seconds: int | None) -> dict[str, Any]:
    status = _read_status_file(project_path, job_id)
    if status is None:
        return {"success": False, "message": "No build status file found. Start the build with manage_build action=build first."}
    timeout = 600 if timeout_seconds is None else max(1, min(1800, timeout_seconds))
    deadline = monotonic() + timeout
    project_name = await _target_project_name(ctx)
    while True:
        state = status.get("state")
        if state in ("succeeded", "failed", "interrupted"):
            return {"success": state == "succeeded", "message": f"Build {state}.", "data": status}
        if project_name:
            dialogs = dialogs_for_project(await asyncio.to_thread(find_unity_dialogs), project_name)
            if dialogs:
                dialog = dialogs[0]
                buttons = ", ".join(dialog["buttons"]) or "none"
                return {
                    "success": False,
                    "message": f"Unity is showing a dialog '{dialog['title']}' (buttons: {buttons}) and the build cannot continue until it is answered. Answer it with unity_dialog.",
                    "hint": "user_action",
                    "data": {"dialogs": dialogs, "build": status},
                }
        remaining = deadline - monotonic()
        if remaining <= 0:
            return {"success": False, "message": f"Build still {state} after {timeout} s; call wait again.", "data": status}
        await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))
        current = _read_status_file(project_path, job_id)
        if current is not None:
            status = current


async def _send_build_command(
    ctx: Context,
    params_dict: dict[str, Any],
) -> dict[str, Any]:
    unity_instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "manage_build", params_dict
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}


@mcp_for_unity_tool(
    group="core",
    description=(
        "Manage Unity player builds — trigger builds, switch platforms, configure settings, "
        "manage build scenes and profiles, run batch builds across platforms. "
        "Actions: build, status, wait, platform, settings, scenes, profiles, batch, cancel."
    ),
    annotations=ToolAnnotations(
        title="Manage Build",
        destructiveHint=True,
        readOnlyHint=False,
    ),
)
async def manage_build(
    ctx: Context,
    action: Annotated[str, "Action: build, status, wait, platform, settings, scenes, profiles, batch, cancel"],
    target: Annotated[Optional[str], "Build target: windows64, osx, linux64, android, ios, webgl, uwp, tvos, visionos"] = None,
    output_path: Annotated[Optional[str], "Output path for the build"] = None,
    scenes: Annotated[Optional[str], "JSON array of scene paths, or comma-separated paths"] = None,
    development: Annotated[Optional[str], "Development build (true/false)"] = None,
    options: Annotated[Optional[str], "JSON array of BuildOptions: clean_build, auto_run, deep_profiling, compress_lz4, strict_mode, detailed_report"] = None,
    subtarget: Annotated[Optional[str], "Build subtarget: player or server"] = None,
    scripting_backend: Annotated[Optional[str], "Scripting backend: mono or il2cpp (persistent change)"] = None,
    profile: Annotated[Optional[str], "Build Profile asset path (Unity 6+ only)"] = None,
    property: Annotated[Optional[str], "Settings property: product_name, company_name, version, bundle_id, scripting_backend, defines, architecture"] = None,
    value: Annotated[Optional[str], "Value to set for the property (omit to read)"] = None,
    activate: Annotated[Optional[str], "Activate a build profile (true/false)"] = None,
    targets: Annotated[Optional[str], "JSON array of targets for batch build"] = None,
    profiles: Annotated[Optional[str], "JSON array of profile paths for batch build (Unity 6+)"] = None,
    output_dir: Annotated[Optional[str], "Base output directory for batch builds"] = None,
    job_id: Annotated[Optional[str], "Job ID for status/cancel"] = None,
    timeout_seconds: Annotated[Optional[int], "wait: seconds to wait, default 600, max 1800"] = None,
) -> dict[str, Any]:
    action_lower = action.lower()
    if action_lower not in ALL_ACTIONS:
        return {
            "success": False,
            "message": f"Unknown action '{action}'. Valid actions: {', '.join(ALL_ACTIONS)}",
        }

    if action_lower in ("status", "wait"):
        project_path = await _target_project_path(ctx)
        if action_lower == "wait":
            if not project_path:
                return {"success": False, "message": "Cannot locate the Unity project to watch its build status."}
            return await _wait_for_build(ctx, project_path, job_id, timeout_seconds)
        status = _read_status_file(project_path, job_id) if project_path else None
        if status is not None:
            state = status.get("state")
            if state in ("pending", "building"):
                return {
                    "success": True,
                    "message": f"Build {state} (answered from the build status file; Unity is busy until the build ends). Use action=wait to block until it finishes.",
                    "data": status,
                }
            if state == "interrupted":
                return {"success": False, "message": status["error_message"], "data": status}

    params_dict: dict[str, Any] = {"action": action_lower}

    coerced_development = coerce_bool(development, default=None)
    coerced_activate = coerce_bool(activate, default=None)
    parsed_scenes = parse_json_payload(scenes) if scenes else None
    # Support comma-separated scene paths as an alternative to JSON array
    if isinstance(parsed_scenes, str):
        parsed_scenes = [s.strip() for s in parsed_scenes.split(",") if s.strip()]
    parsed_options = parse_json_payload(options) if options else None
    parsed_targets = parse_json_payload(targets) if targets else None
    parsed_profiles = parse_json_payload(profiles) if profiles else None

    param_map: dict[str, Any] = {
        "target": target,
        "output_path": output_path,
        "scenes": parsed_scenes,
        "development": coerced_development,
        "options": parsed_options,
        "subtarget": subtarget,
        "scripting_backend": scripting_backend,
        "profile": profile,
        "property": property,
        "value": value,
        "activate": coerced_activate,
        "targets": parsed_targets,
        "profiles": parsed_profiles,
        "output_dir": output_dir,
        "job_id": job_id,
    }

    for key, val in param_map.items():
        if val is not None:
            params_dict[key] = val

    return await _send_build_command(ctx, params_dict)
