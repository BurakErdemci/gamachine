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

from . import platform

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
    def __init__(self, binary, socket):
        if not binary or not socket or not os.path.isfile(binary):
            raise DriverUnavailable("Embedded computer-use driver is not configured")
        self.binary = binary
        self.socket = socket
        self.command, self.env = platform.current().driver_command(binary, socket)
        self._lock = asyncio.Lock()
        self._closed = False
        self._task = None
        self._queue = None

    async def _serve(self, queue):
        # Session context managers must enter and exit in the same task; tool
        # invocations arrive on different MCP request tasks, hence this owner.
        current = await queue.get()
        error = None
        try:
            async with _transport(self.command, self.env) as streams:
                async with ClientSession(*streams, read_timeout_seconds=130) as session:
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
            if self._closed:
                raise DriverUnavailable("Computer-use driver client is closed")
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
            self._closed = True
            if self._task is not None and not self._task.done():
                await self._queue.put(None)
                await self._task
            self._task = None

    def _app_rows(self, result):
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
        rows_out = []
        for source in sources:
            rows = source.get("apps", []) if isinstance(source, dict) else source
            if not isinstance(rows, list):
                continue
            rows_out.extend(row for row in rows if isinstance(row, dict))
        return rows_out

    async def list_running_apps(self):
        result = await self.call("list_apps", {})
        if result.is_error:
            raise DriverError("Could not resolve running applications")
        apps = {}
        for row in self._app_rows(result):
            pid = row.get("pid")
            if type(pid) is not int or pid <= 0 or row.get("running") is False:
                continue
            bundle = row.get("bundle_id")
            name = row.get("name")
            apps[pid] = (bundle if isinstance(bundle, str) else None,
                         name if isinstance(name, str) else None)
        return apps

    async def frontmost_pid(self) -> int | None:
        return await platform.current().frontmost_pid(self)

    async def installed_app_name(self, bundle_id):
        """Real name of an installed app (running or not) from list_apps, or None."""
        result = await self.call("list_apps", {})
        if result.is_error:
            return None
        wanted = bundle_id.casefold()
        for row in self._app_rows(result):
            bundle = row.get("bundle_id")
            name = row.get("name")
            if (isinstance(bundle, str) and bundle.casefold() == wanted
                    and isinstance(name, str) and name.strip()):
                return name.strip()
        return None
