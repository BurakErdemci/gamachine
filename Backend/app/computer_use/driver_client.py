"""One serialized MCP session, owned by a single task until disconnected."""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import anyio
from mcp import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, JSONRPCMessage
from pydantic import TypeAdapter

logger = logging.getLogger(__name__)
_MESSAGE = TypeAdapter(JSONRPCMessage)


class DriverUnavailable(RuntimeError):
    pass


class DriverError(RuntimeError):
    pass


@asynccontextmanager
async def _transport(command, env):
    """Stdio with an exact environment (the SDK transport adds inherited keys).

    At most one child and two pipe tasks per connection; all are joined on exit.
    The SDK ClientSession still owns MCP framing semantics and request handling.
    """
    process = await asyncio.create_subprocess_exec(
        *command, env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, limit=32 * 1024 * 1024,
    )
    incoming, read = anyio.create_memory_object_stream(0)
    write, outgoing = anyio.create_memory_object_stream(0)

    async def reader():
        async with incoming:
            try:
                while line := await process.stdout.readline():
                    await incoming.send(SessionMessage(_MESSAGE.validate_json(line)))
            except (OSError, ValueError) as exc:
                await incoming.send(exc)

    async def writer():
        try:
            async with outgoing:
                async for message in outgoing:
                    process.stdin.write((message.message.model_dump_json(by_alias=True, exclude_unset=True) + "\n").encode())
                    await process.stdin.drain()
        except (OSError, ConnectionError):
            await incoming.aclose()

    tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
    try:
        yield read, write
    finally:
        for task in tasks:
            task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                logger.warning("Computer-use stdio pipe failed: %r", outcome)
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        await read.aclose()
        await write.aclose()


class DriverClient:
    def __init__(self):
        binary = os.environ.get("GAMACHINE_CUA_DRIVER", "")
        socket = os.environ.get("GAMACHINE_CUA_SOCKET", "")
        if not binary or not socket or not os.path.isfile(binary):
            raise DriverUnavailable("Embedded computer-use driver is not configured")
        self.command = [binary, "mcp", "--embedded", "--socket", socket]
        self.env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
        self.env.update(CUA_DRIVER_EMBEDDED="1", DO_NOT_TRACK="1",
                        CUA_DRIVER_RS_TELEMETRY_ENABLED="0", CUA_DRIVER_RS_UPDATE_CHECK="0")
        self._lock = asyncio.Lock()
        self._task = None
        self._queue = None

    async def _serve(self, queue):
        # Session context managers must enter and exit in the same task; tool
        # invocations arrive on different MCP request tasks, hence this owner.
        current = await queue.get()
        error = None
        try:
            async with _transport(self.command, self.env) as streams:
                async with ClientSession(*streams, read_timeout_seconds=30) as session:
                    await session.initialize()
                    while current is not None:
                        tool, args, future = current
                        result = await session.call_tool(tool, args)
                        if not future.done():
                            future.set_result(result)
                        current = await queue.get()
        except Exception as exc:
            logger.warning("Computer-use driver disconnected: %s", exc, exc_info=True)
            error = DriverError("Computer-use driver transport failed")
        finally:
            if current is not None:
                future = current[2]
                if not future.done():
                    future.set_exception(error or DriverError("Computer-use driver closed"))

    async def call(self, tool, args) -> CallToolResult:
        async with self._lock:
            if self._task is None or self._task.done():
                self._queue = asyncio.Queue(maxsize=1)
                self._task = asyncio.create_task(self._serve(self._queue))
            future = asyncio.get_running_loop().create_future()
            await self._queue.put((tool, args, future))
            try:
                await asyncio.wait((future, self._task), return_when=asyncio.FIRST_COMPLETED)
                if future.done():
                    return future.result()
                future.cancel()
                raise DriverError("Computer-use driver disconnected before the call completed")
            except asyncio.CancelledError:
                # Do not allow another action to overlap a cancelled caller's
                # still-running action. Closing also prevents an implicit retry.
                future.cancel()
                await self._close()
                raise

    async def _close(self):
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def close(self):
        async with self._lock:
            if self._task is not None and not self._task.done():
                await self._queue.put(None)
                await self._task
            self._task = None

    async def list_running_apps(self):
        result = await self.call("list_apps", {})
        if result.is_error:
            raise DriverError("Could not resolve running applications")
        sources = []
        structured = result.structured_content
        if structured is not None:
            sources.append(structured)
        for block in result.content:
            if block.type == "text":
                try:
                    sources.append(json.loads(block.text))
                except (ValueError, TypeError):
                    continue
        apps = {}
        for source in sources:
            rows = source.get("apps", []) if isinstance(source, dict) else source
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                pid = row.get("pid")
                if type(pid) is not int or pid <= 0 or row.get("running") is False:
                    continue
                bundle = row.get("bundle_id")
                name = row.get("name")
                apps[pid] = (bundle if isinstance(bundle, str) else None,
                             name if isinstance(name, str) else None)
        return apps
