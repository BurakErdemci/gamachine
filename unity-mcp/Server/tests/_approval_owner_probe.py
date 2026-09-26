"""Starts the real server with its approval gate pointed at a fake backend and
records which `conversation_id` each carrier puts in the approval request.

Child interpreter of test_live_approval_owner.py, for the same reasons as
_live_server_probe.py: the test process has stub fastmcp/mcp modules installed,
this one does not. The fake backend is _approval_denial_probe's; every call is
named "deny_now:<case>" so it is refused on the POST and never polls.

WHAT IT EMITS
    One JSON object on stdout: every request the fake backend received (with
    `has_conversation` / `conversation_id`), plus the client-side results.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
import sys
import tempfile
import os

import _live_server_probe as live
from _approval_denial_probe import APP_TOKEN, FakeBackend

HEADER = "X-Gamachine-Conversation"


def args(case: str, **extra) -> dict:
    return {"action": "create", "name": f"deny_now:{case}", **extra}


def old_era(base: str, case: str, *, path: str = "/mcp", headers: dict | None = None,
            meta: dict | None = None, arguments: dict | None = None) -> dict:
    session = live.OldEraSession(base, path, live.SECRET)
    session.headers.update(headers or {})
    with session:
        params = {"name": "manage_gameobject", "arguments": arguments or args(case)}
        if meta is not None:
            params["_meta"] = meta
        status, body = session.request("tools/call", params)
        result = body.get("result") or {}
        return {"status": status, "is_error": result.get("isError"),
                "protocol": session.protocol, "error": body.get("error")}


def new_era(base: str, cases: list[tuple[str, str, dict, dict | None]]) -> dict:
    sdk, why_not = live._new_era_sdk()
    if sdk is None:
        return {"skipped": why_not}
    httpx2, Client, streamable_http_client = sdk

    async def one(path: str, headers: dict, meta: dict | None, case: str) -> dict:
        try:
            async with httpx2.AsyncClient(headers={"X-API-Key": live.SECRET, **headers},
                                          timeout=httpx2.Timeout(30, read=60)) as http:
                async with Client(streamable_http_client(base + path, http_client=http),
                                  mode="auto") as client:
                    result = await client.call_tool("manage_gameobject", args(case), meta=meta)
                    return {"protocol": client.protocol_version, "is_error": result.is_error}
        except BaseException as exc:  # noqa: BLE001 - the failure IS the observation
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            return {"exception": f"{type(exc).__name__}: {exc}"[:400]}

    async def run() -> dict:
        return {case: await one(path, headers, meta, case)
                for case, path, headers, meta in cases}

    return asyncio.run(run())


def _probe(workdir: pathlib.Path, report: dict) -> None:
    backend = FakeBackend()
    backend.start()
    os.environ["UNITYAI_URL"] = backend.url
    os.environ.pop("ANTIGRAVITY_URL", None)
    os.environ["LOCAL_APP_TOKEN"] = APP_TOKEN

    port = live._free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(workdir / "server.out", "wb")
    proc = live.start_server(port, workdir, log)
    try:
        healthy, _ = live.wait_healthy(proc, base)
        report["healthy"] = healthy
        if not healthy:
            report["exit_code"] = proc.poll()
            return
        report["old_era"] = {
            "old_header": old_era(base, "old_header", headers={HEADER: "41"}),
            "old_query": old_era(base, "old_query", path="/mcp?conv=42"),
            "old_meta": old_era(base, "old_meta", meta={"gamachine_conversation": 43}),
            "old_args": old_era(base, "old_args", arguments=args(
                "old_args", conversation_id=44, gamachine_conversation=44)),
            "old_junk": old_era(base, "old_junk", headers={HEADER: "7abc"}),
            "old_conflict": old_era(base, "old_conflict", headers={HEADER: "45"},
                                    meta={"gamachine_conversation": 46}),
            "old_none": old_era(base, "old_none"),
        }
        report["new_era"] = new_era(base, [
            ("new_header", "/mcp", {HEADER: "51"}, None),
            ("new_query", "/mcp?conv=52", {}, None),
            ("new_meta", "/mcp", {}, {"gamachine_conversation": 53}),
            ("new_none", "/mcp", {}, None),
        ])
        report["still_running"] = proc.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
        log.close()
        backend.close()
        report["backend_requests"] = backend.snapshot()
        report["server_output_tail"] = (
            (workdir / "server.out").read_bytes()[-4000:].decode("utf-8", "replace"))


def main() -> int:
    report: dict = {}
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="unity-mcp-owner-probe-"))
    try:
        _probe(workdir, report)
    finally:
        report["workdir_removed"] = live.remove_workdir(workdir)
    json.dump(report, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
