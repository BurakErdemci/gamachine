"""
Compile verdicts from the Editor's epoch-based compile tracker (get_compile_status).

Every verdict is one of VERDICTS. "clean" is only ever produced from a status that
proves it: the last compile finished without errors, the domain reload after it
has happened, and no script file on disk is newer than that compile's start. An
unreadable status is "unknown", never idle -- a failed status read used to count
as "not busy" and ended the compile wait early.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
import transport.legacy.unity_connection as _legacy_conn
import transport.unity_transport as unity_transport

VERDICTS = ("errors", "clean", "compiling", "pending", "stale", "timeout", "unknown")

COMMAND = "get_compile_status"

# Bounds of the post-mutation wait. Module-level so tests can shrink them.
MAX_WAIT_S = 90.0
# A requested compile starts within ~0.1 s (measured on 6000.4, LoopLab); a script
# write through the MCP tools imports and starts one within a second or two.
START_WINDOW_S = 10.0
POLL_S = 0.4
SNAPSHOT_ATTEMPTS = 2
RETRY_DELAY_S = 0.25


@dataclass
class StatusRead:
    status: dict[str, Any] | None
    # None when readable; otherwise "unsupported" (the Editor plugin has no
    # get_compile_status handler) or "unreadable: <reason>".
    problem: str | None = None

    @property
    def unsupported(self) -> bool:
        return self.problem == "unsupported"


def _is_unsupported(resp: dict[str, Any]) -> bool:
    text = " ".join(str(resp.get(k) or "") for k in ("error", "message")).lower()
    return "unknown or unsupported command" in text


async def read_compile_status(
    unity_instance: str | None,
    *,
    scan: bool = True,
    attempts: int = 1,
    send_fn: Callable[..., Awaitable[Any]] | None = None,
    route_fn: Callable[..., Awaitable[Any]] | None = None,
) -> StatusRead:
    """One status read, retried `attempts` times on transport failures.

    retry_on_reload=False: on HTTP a fast-fail command with retry_on_reload=True
    first waits up to 12 s for a ping, which would stall every poll of a wait
    loop through a domain reload. The retries here are short and bounded instead.
    """
    problem = "unreadable: no attempt made"
    for attempt in range(max(1, attempts)):
        if attempt:
            await asyncio.sleep(RETRY_DELAY_S)
        try:
            resp = await (route_fn or unity_transport.send_with_unity_instance)(
                send_fn or _legacy_conn.async_send_command_with_retry,
                unity_instance,
                COMMAND,
                {"scan_changes": bool(scan)},
                retry_on_reload=False,
            )
        except Exception as exc:
            problem = f"unreadable: {type(exc).__name__}: {exc}"
            continue
        if hasattr(resp, "model_dump"):
            resp = resp.model_dump()
        if not isinstance(resp, dict):
            problem = f"unreadable: unexpected response {type(resp).__name__}"
            continue
        if not resp.get("success"):
            if _is_unsupported(resp):
                return StatusRead(None, "unsupported")
            problem = "unreadable: " + str(resp.get("error") or resp.get("message") or "request failed")
            continue
        data = resp.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("epoch"), int):
            return StatusRead(None, "unsupported")
        return StatusRead(data)
    return StatusRead(None, problem)


def _changed(status: dict[str, Any]) -> dict[str, Any] | None:
    changed = status.get("scripts_changed_since_compile")
    return changed if isinstance(changed, dict) else None


def _changed_count(status: dict[str, Any]) -> int:
    changed = _changed(status)
    try:
        return int(changed.get("count") or 0) if changed else 0
    except (TypeError, ValueError):
        return 0


def _base(verdict: str, note: str | None, status: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"verdict": verdict}
    if note:
        out["note"] = note
    if status is not None:
        out["epoch"] = status.get("epoch")
        out["finished_epoch"] = status.get("finished_epoch")
        changed = _changed(status)
        if changed is not None:
            out["scripts_changed_since_compile"] = changed
    return out


def unknown_verdict(problem: str | None) -> dict[str, Any]:
    if problem == "unsupported":
        note = ("compile status unavailable: the Unity plugin does not answer get_compile_status "
                "(update the MCP for Unity package); compile result unknown")
    else:
        note = f"compile status could not be read ({problem or 'no response'}); compile result unknown"
    return _base("unknown", note, None)


def _errors_verdict(status: dict[str, Any], note: str | None = None) -> dict[str, Any]:
    out = _base("errors", note, status)
    out["errors"] = status.get("errors") or []
    out["error_count"] = status.get("error_count")
    out["warning_count"] = status.get("warning_count")
    return out


def live_verdict(status: dict[str, Any] | None, problem: str | None = None) -> dict[str, Any]:
    """Verdict for the Editor's state right now, from one get_compile_status read."""
    if status is None:
        return unknown_verdict(problem)

    epoch = status.get("epoch") or 0
    finished = status.get("finished_epoch") or 0
    if status.get("is_compiling") or epoch > finished:
        return _base("compiling", "compilation in progress - error list is not final", status)
    if status.get("is_updating"):
        return _base("pending", "asset import in progress - a compile may follow", status)

    changed = _changed_count(status)
    if changed:
        out = _base(
            "stale",
            f"{changed} script file(s) changed on disk since the last compile started - "
            "call refresh_unity to compile them",
            status,
        )
        if status.get("last_failed"):
            out["last_compile_errors"] = status.get("errors") or []
        return out

    if epoch == 0:
        if status.get("compilation_failed_now"):
            out = _base(
                "errors",
                "Unity reports failed script compilation from before compiles were tracked in this "
                "session; call refresh_unity(compile='request') to list the compiler errors",
                status,
            )
            out["errors"] = []
            out["error_count"] = None
            return out
        return _base("clean", "no compile this session and no scripts changed since the domain loaded", status)

    if status.get("last_failed"):
        return _errors_verdict(status)
    if not status.get("reload_done_after_finish"):
        return _base("pending", "compile finished without errors; domain reload not done yet", status)
    out = _base("clean", None, status)
    out["error_count"] = 0
    out["warning_count"] = status.get("warning_count")
    return out


@dataclass
class CompileBaseline:
    epoch: int | None
    problem: str | None = None


async def snapshot_compile_epoch(unity_instance: str | None) -> CompileBaseline:
    """Read the compile epoch BEFORE a mutation, so a later wait can tell the
    compile of this change from one that finished earlier."""
    read = await read_compile_status(unity_instance, scan=False, attempts=SNAPSHOT_ATTEMPTS)
    if read.status is None:
        return CompileBaseline(None, read.problem)
    return CompileBaseline(int(read.status["epoch"]))


async def await_compile_verdict(
    unity_instance: str | None,
    baseline: CompileBaseline,
    *,
    max_wait_s: float | None = None,
    start_window_s: float | None = None,
) -> dict[str, Any]:
    """Wait for the compile that follows a change and return its verdict.

    Waits for an epoch newer than the baseline to finish; a failed compile returns
    its compiler errors, a clean one is only reported after the domain reload
    that follows it. If no compile starts within the start window, the live
    verdict decides: nothing changed on disk -> clean ("no compile needed"),
    files changed -> stale.
    """
    if baseline.problem == "unsupported":
        return unknown_verdict("unsupported")

    max_wait = MAX_WAIT_S if max_wait_s is None else max_wait_s
    start_window = START_WINDOW_S if start_window_s is None else start_window_s
    t0 = time.monotonic()
    deadline = t0 + max_wait
    start_deadline = t0 + min(start_window, max_wait)
    e0 = baseline.epoch
    seen_busy = False
    last_status: dict[str, Any] | None = None
    last_problem: str | None = baseline.problem

    def _finish(out: dict[str, Any]) -> dict[str, Any]:
        out["epoch_before"] = e0
        out["compilation_observed"] = seen_busy or (
            e0 is not None and last_status is not None and (last_status.get("epoch") or 0) > e0)
        out["waited_s"] = round(time.monotonic() - t0, 2)
        return out

    while True:
        read = await read_compile_status(unity_instance, scan=False)
        now = time.monotonic()
        status = read.status
        if status is None:
            last_problem = read.problem
            if read.unsupported:
                return _finish(unknown_verdict("unsupported"))
        else:
            last_status = status
            epoch = status.get("epoch") or 0
            finished = status.get("finished_epoch") or 0
            busy = bool(status.get("is_compiling") or status.get("is_updating") or epoch > finished)
            seen_busy = seen_busy or busy
            started = (e0 is not None and epoch > e0) or (e0 is None and seen_busy)

            if started and not busy:
                if e0 is not None and status.get("last_failed"):
                    return _finish(_errors_verdict(status))
                if status.get("last_failed") or status.get("reload_done_after_finish"):
                    final = await read_compile_status(unity_instance, scan=True, attempts=2)
                    verdict = live_verdict(final.status, final.problem)
                    if verdict["verdict"] not in ("compiling", "pending"):
                        return _finish(verdict)
            elif not started and not busy and now >= start_deadline:
                final = await read_compile_status(unity_instance, scan=True, attempts=2)
                verdict = live_verdict(final.status, final.problem)
                if verdict["verdict"] == "clean":
                    verdict["note"] = ("no compile needed: no compile started after the change "
                                       "and no script changed on disk since the last compile")
                    return _finish(verdict)
                if verdict["verdict"] not in ("compiling", "pending"):
                    return _finish(verdict)

        if now >= deadline:
            break
        await asyncio.sleep(POLL_S)

    if last_status is None or (status is None and last_problem):
        out = unknown_verdict(last_problem)
        out["note"] += f"; gave up after {max_wait:.0f}s"
        return _finish(out)
    out = _base("timeout", f"compile did not reach a final state within {max_wait:.0f}s", last_status)
    out["is_compiling"] = last_status.get("is_compiling")
    out["reload_done_after_finish"] = last_status.get("reload_done_after_finish")
    return _finish(out)


@mcp_for_unity_tool(
    # A resource on the Unity side, so there is no Unity tool of this name to
    # follow; it is visible whenever read_console is.
    unity_target="read_console",
    description=(
        "Live script-compile verdict from the Unity Editor. Read-only: never refreshes or compiles. "
        "verdict is one of: errors (compiler errors with CS code, file, line), clean (last compile "
        "finished, domain reloaded, no script changed on disk since), compiling, pending (import or "
        "domain reload still running), stale (script files changed on disk since the last compile - "
        "call refresh_unity to compile), unknown (status unreadable - not the same as clean). "
        "After writing .cs files with your own file tools, call refresh_unity (it returns this verdict) "
        "or this tool; read_console alone can show 0 errors before Unity has compiled."
    ),
    annotations=ToolAnnotations(
        title="Compile Status",
        readOnlyHint=True,
    ),
)
async def compile_status(ctx: Context) -> dict[str, Any]:
    unity_instance = await get_unity_instance_from_context(ctx)
    read = await read_compile_status(unity_instance, scan=True, attempts=3)
    verdict = live_verdict(read.status, read.problem)
    if read.status is not None:
        verdict["status"] = read.status
    return {"success": True, "message": f"compile verdict: {verdict['verdict']}", "data": verdict}
