"""Real FastMCP + the real UnityInstanceMiddleware: the per-instance write lock.

CHILD INTERPRETER, launched by test_instance_write_lock.py (the integration
conftest stubs fastmcp for the whole test session; see _authz_matrix_probe.py).

Two questions only the real framework answers: does the held-lock marker
(a ContextVar) survive FastMCP's own call path, so a tool that re-enters the
middleware with ctx.fastmcp.call_tool (middleware runs by default) does not wait
on its own lock; and do two concurrent client calls really serialize.
"""

import asyncio
import json
import sys
import time


def main() -> int:
    sys.path.insert(0, sys.argv[1])

    from fastmcp import Client, Context, FastMCP

    from transport import approval_gate
    from transport import unity_instance_middleware as uim

    # Short enough that a self-deadlock shows up as the busy error, not a hang.
    uim.WRITE_LOCK_WAIT_S = 0.5

    async def approve(_tool, _params, hedef=None, conversation_id=None):
        return None

    approval_gate.kapiyi_gec = approve
    middleware = uim.UnityInstanceMiddleware()

    async def sole_instance(_ctx):
        return "Game@aaa"

    middleware._maybe_autoselect_instance = sole_instance

    mcp = FastMCP(name="write-lock-probe")
    mcp.add_middleware(middleware)
    seen = []
    spans = []

    @mcp.tool
    async def inner_write(ctx: Context) -> str:
        seen.append(await ctx.get_state("unity_instance"))
        return "inner"

    @mcp.tool
    async def outer_write(ctx: Context) -> str:
        seen.append(await ctx.get_state("unity_instance"))
        result = await ctx.fastmcp.call_tool("inner_write", {})
        return "outer+" + result.content[0].text

    @mcp.tool
    async def slow_write(label: str) -> str:
        start = time.monotonic()
        await asyncio.sleep(0.3)
        spans.append((start, time.monotonic()))
        return label

    async def run() -> dict:
        async with Client(mcp) as client:
            started = time.monotonic()
            try:
                nested = (await client.call_tool("outer_write", {})).content[0].text
            except Exception as exc:
                nested = f"{type(exc).__name__}: {exc}"
            nested_s = time.monotonic() - started
            pair = await asyncio.gather(
                client.call_tool("slow_write", {"label": "a"}),
                client.call_tool("slow_write", {"label": "b"}),
            )
        first, second = sorted(spans)
        return {
            "nested_result": nested,
            "nested_seconds": round(nested_s, 3),
            "nested_seen": seen,
            "pair_results": sorted(r.content[0].text for r in pair),
            "pair_overlap_seconds": round(first[1] - second[0], 3),
        }

    json.dump(asyncio.run(run()), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
