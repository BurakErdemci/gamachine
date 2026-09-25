"""Real FastMCP + the real UnityInstanceMiddleware: does routing state leak?

CHILD INTERPRETER, launched by test_request_scoped_state.py (the integration
conftest stubs fastmcp for the whole test session; see _authz_matrix_probe.py).

The P1 spike measured, on the 2026-07-28 protocol, 200 tool calls -> 802 new
entries in FastMCP's session state store (24 h TTL, unbounded MemoryStore):
the middleware wrote unity_instance / unity_session_id / user_id with the
default session-scoped set_state, and without a session every request got a
fresh key prefix. This probe hands FastMCP a store that counts writes (the
public `session_state_store` constructor argument), makes N calls, and
reports what the tool saw and how many writes reached the store.
"""

import asyncio
import json
import sys

CALLS = 50


def main() -> int:
    sys.path.insert(0, sys.argv[1])

    from fastmcp import Client, Context, FastMCP
    from key_value.aio.stores.memory import MemoryStore

    from services.tools import get_unity_instance_from_context
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    class CountingStore(MemoryStore):
        puts = 0

        async def put(self, *args, **kwargs):
            CountingStore.puts += 1
            return await super().put(*args, **kwargs)

    middleware = UnityInstanceMiddleware()
    # Routing inputs are fixed per call below; discovery and the approval gate
    # are not what this probe measures.
    # A value, not a queue: the middleware also runs on the client's own
    # tools/list, which would consume a queued selection.
    selection = {"value": None}

    async def autoselect(_ctx):
        return selection["value"]

    async def no_gate(_context):
        return None

    middleware._maybe_autoselect_instance = autoselect
    middleware._require_approval = no_gate

    mcp = FastMCP(name="request-state-probe", session_state_store=CountingStore())
    mcp.add_middleware(middleware)

    @mcp.tool
    async def probe_state(ctx: Context) -> dict:
        return {"unity_instance": await get_unity_instance_from_context(ctx)}

    async def run() -> dict:
        seen = []
        async with Client(mcp) as client:
            for i in range(CALLS):
                selection["value"] = f"Proj{i}@hash{i}"
                result = await client.call_tool("probe_state", {})
                seen.append(result.structured_content["unity_instance"])
            # No selection this time: a request-scoped value must not survive
            # into the next request.
            selection["value"] = None
            result = await client.call_tool("probe_state", {})
            after = result.structured_content["unity_instance"]
        return {
            "calls": CALLS,
            "store_puts": CountingStore.puts,
            "seen_expected": seen == [f"Proj{i}@hash{i}" for i in range(CALLS)],
            "value_after_unrouted_call": after,
        }

    json.dump(asyncio.run(run()), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
