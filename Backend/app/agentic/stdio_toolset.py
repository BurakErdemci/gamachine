"""Gamachine's computer-use and agent-browser MCP servers for one API chat run.

The API loop has no MCP client of its own, so each run starts the same stdio
servers the Claude path registers (same argv, same env with the chat id), lists
their tools under a per-server prefix and routes calls back to them.
"""
import asyncio
import logging
import re

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import agent_guide
from browser_use import registration as browser_registration
from computer_use import registration as computer_registration
from tools.unity_mcp_tools import _result_to_dict

logger = logging.getLogger(__name__)

# Longer than approval_bridge's 150 s card wait, so a pending card is not cut off.
CALL_TIMEOUT_S = 180.0
START_TIMEOUT_S = 30.0
CLOSE_TIMEOUT_S = 10.0

_SERVERS = (
    ("gm_computer__", computer_registration, agent_guide.with_computer_use),
    ("gm_browser__", browser_registration, agent_guide.with_browser_use),
)
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def exposed_name(prefix: str, tool_name: str) -> str:
    """Prefixed name every provider accepts: [A-Za-z0-9_-]{1,64}."""
    return (prefix + _UNSAFE.sub("_", tool_name))[:64]


class _Server:
    """One stdio server; a single owner task enters and leaves the MCP contexts,
    so closing works from any task (the loop's generator may be closed elsewhere)."""

    def __init__(self, prefix: str, entry: dict):
        self.prefix = prefix
        self.session = None
        self._stop = asyncio.Event()
        self._ready = asyncio.get_running_loop().create_future()
        params = StdioServerParameters(command=entry["command"], args=list(entry["args"]),
                                       env=dict(entry["env"]))
        self._task = asyncio.create_task(self._own(params))

    async def _own(self, params):
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.session = session
                    self._ready.set_result(listed.tools)
                    await self._stop.wait()
        except Exception as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
            else:
                logger.warning("[stdio-toolset] %s server ended: %s", self.prefix, exc)
        finally:
            self.session = None
            if not self._ready.done():
                self._ready.set_exception(RuntimeError("server task ended before it was ready"))

    async def started(self):
        return await asyncio.wait_for(asyncio.shield(self._ready), START_TIMEOUT_S)

    def stop(self) -> None:
        self._stop.set()

    async def closed(self) -> None:
        try:
            # On timeout wait_for cancels the owner task, whose stdio context then kills the process.
            await asyncio.wait_for(self._task, CLOSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("[stdio-toolset] %s server did not stop in time", self.prefix)


class StdioToolset:
    def __init__(self, conversation_id):
        self.conversation_id = conversation_id
        self._servers: dict = {}  # prefix -> _Server
        self._failed: set = set()  # prefixes that failed to start while their switch stayed on
        self._routes: dict = {}  # exposed name -> (prefix, MCP tool name)
        self._definitions: dict = {}  # prefix -> [registry-format definitions]

    async def sync(self) -> None:
        """Open or close each server to match its switch right now."""
        opening = []
        for prefix, registration, _ in _SERVERS:
            entry = registration.stdio_entry(self.conversation_id)
            if entry is None:
                self._failed.discard(prefix)
                if prefix in self._servers:
                    await self._close([prefix])
            elif prefix not in self._servers and prefix not in self._failed:
                opening.append((prefix, entry))
        if opening:
            await asyncio.gather(*(self._open(prefix, entry) for prefix, entry in opening))

    async def _open(self, prefix: str, entry: dict) -> None:
        server = _Server(prefix, entry)
        self._servers[prefix] = server
        try:
            tools = await server.started()
        except Exception as exc:
            logger.warning("[stdio-toolset] %s server did not start: %r", prefix, exc)
            self._failed.add(prefix)
            await self._close([prefix])
            return
        definitions = []
        for tool in tools:
            name = exposed_name(prefix, tool.name)
            if name in self._routes:
                logger.warning("[stdio-toolset] %s skipped: name %s taken", tool.name, name)
                continue
            self._routes[name] = (prefix, tool.name)
            definitions.append({
                "name": name,
                "description": tool.description or tool.name,
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            })
        self._definitions[prefix] = definitions

    async def _close(self, prefixes) -> None:
        servers = [self._servers.pop(p) for p in prefixes if p in self._servers]
        for prefix in prefixes:
            self._definitions.pop(prefix, None)
            self._routes = {n: r for n, r in self._routes.items() if r[0] != prefix}
        # Every server is told to stop before any wait, so a cancel mid-close leaks none.
        for server in servers:
            server.stop()
        await asyncio.gather(*(server.closed() for server in servers))

    def definitions(self) -> list:
        return [d for prefix, _, _ in _SERVERS for d in self._definitions.get(prefix, [])]

    def owns(self, name: str) -> bool:
        return name in self._routes or any(name.startswith(p) for p, _, _ in _SERVERS)

    def with_guide(self, guide: str) -> str:
        """The guide lines the Claude path adds for the servers this run has open."""
        for prefix, _, add_lines in _SERVERS:
            if guide and self._definitions.get(prefix):
                guide = add_lines(guide)
        return guide

    async def call(self, name: str, arguments) -> dict:
        prefix, tool = self._routes.get(name, (None, None))
        server = self._servers.get(prefix)
        if server is None or server.session is None:
            return {"success": False, "error": f"{name} is not available (its switch is off)."}
        try:
            result = await server.session.call_tool(
                tool, arguments if isinstance(arguments, dict) else {},
                read_timeout_seconds=CALL_TIMEOUT_S)
        except Exception as exc:
            logger.warning("[stdio-toolset] %s failed: %r", name, exc)
            return {"success": False, "error": str(exc) or type(exc).__name__}
        return _result_to_dict(result)

    async def aclose(self) -> None:
        await self._close(list(self._servers))
