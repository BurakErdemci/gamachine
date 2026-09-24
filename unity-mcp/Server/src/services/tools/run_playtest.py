"""run_playtest: run scenario files end to end (session, steps, expectations, perf)."""
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools._playtest_common import (
    GROUP,
    POLL_ACTION,
    error,
    is_int,
    is_transport_failure,
    send_or_transport_failure,
    send_long,
)
from services.tools.preflight import preflight

ALL_ACTIONS = ["start", "status"]
DEFAULT_GLOB = "Assets/Playtests/**/*.playtest.json"

# Every scenario enters play mode (~5-6 s with its domain reload) plus its steps and
# perf segment. The Gamachine backend cuts a call at 240 s; a longer suite keeps
# running in Unity and its results are fetched with action="status".
RUN_TIMEOUT_S = 600.0


def _project_path(value: str, name: str) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, f"{name} must be a non-empty project path."
    value = value.strip().replace("\\", "/")
    if not value.startswith("Assets/") or ".." in value.split("/"):
        return None, f"{name} must stay under Assets/, got '{value}'."
    return value, None


def _job_id(response: dict[str, Any]) -> str | None:
    data = response.get("data")
    return data.get("job_id") if isinstance(data, dict) else None


def _confirm_new_job(previous_job: str | None):
    # After a lost start reply, `status` reports whatever job Unity holds: the new one if
    # the start arrived, the previous one (or none) if it did not.
    def confirm(response: dict[str, Any]) -> dict[str, Any]:
        job = _job_id(response)
        if job is not None and job != previous_job:
            return response
        return {
            "success": False,
            "error": "run_playtest: the start reply was lost and Unity holds no new job; call it again.",
            "data": response.get("data"),
        }
    return confirm


@mcp_for_unity_tool(
    group=GROUP,
    description=(
        "Run playtest scenario files (*.playtest.json: scene, seed, fixed_dt, steps, expect, perf) "
        "in the editor, each in a fresh play session; refused while in play mode. path: one file "
        f"or a folder (all *.playtest.json under it); glob default {DEFAULT_GLOB}. The tool waits "
        "for the job -> data {job_id, status: done, done, total, passed, failed, seconds, results: "
        "per scenario {scenario, passed, failed_expect, frames, sim_time, state_end, state_hash, "
        "errors, warnings, first_errors, perf, captures}}. action=status returns the last job "
        "(waits while it runs). Slow: one play mode entry (~6 s) per scenario."
    ),
    annotations=ToolAnnotations(title="Run Playtest", destructiveHint=True),
)
async def run_playtest(
    ctx: Context,
    action: Annotated[Literal["start", "status"], "start a job (default) or read the last one"] = "start",
    path: Annotated[str | None, "scenario file or folder, Assets/..."] = None,
    glob: Annotated[str | None, "scenario files glob, Assets/..."] = None,
    seed_override: Annotated[int | None, "use this seed for every scenario"] = None,
) -> dict[str, Any]:
    action = (action or "start").lower()
    if action not in ALL_ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(ALL_ACTIONS)}.")
    if action == POLL_ACTION:
        return await send_long(ctx, "run_playtest", {"action": POLL_ACTION}, timeout_s=RUN_TIMEOUT_S)

    if path is not None and glob is not None:
        return error("Give either path or glob, not both.")
    params: dict[str, Any] = {"action": "start"}
    if path is not None:
        path, err = _project_path(path, "path")
        if err:
            return error(err)
        params["path"] = path
    if glob is not None:
        glob, err = _project_path(glob, "glob")
        if err:
            return error(err)
        params["glob"] = glob
    if seed_override is not None:
        if not is_int(seed_override):
            return error("seed_override must be an integer.")
        params["seed_override"] = seed_override

    gate = await preflight(ctx, requires_no_tests=True, wait_for_no_compile=True)
    if isinstance(gate, MCPResponse):
        return gate.model_dump()

    before = await send_or_transport_failure(ctx, "run_playtest", {"action": POLL_ACTION})
    previous_job = None if is_transport_failure(before) else _job_id(before)
    return await send_long(
        ctx, "run_playtest", params,
        timeout_s=RUN_TIMEOUT_S, confirm_after_loss=_confirm_new_job(previous_job),
    )
