"""One serialized MCP session, owned by a single task until disconnected."""
import asyncio
import ctypes
import json
import logging
import math
import os
import re
import sys
import threading
from contextlib import asynccontextmanager
from functools import cache

import anyio
import httpx
from mcp import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, JSONRPCMessage
from pydantic import TypeAdapter

from unity_ai_mcp import approval_bridge
from . import policy

logger = logging.getLogger(__name__)
_MESSAGE = TypeAdapter(JSONRPCMessage)


class DriverUnavailable(RuntimeError):
    pass


class DriverError(RuntimeError):
    pass


async def _lsappinfo_text(pid) -> str | None:
    if type(pid) is not int or pid <= 0 or sys.platform != "darwin":
        return None
    try:
        executable = os.path.join(os.sep, "usr", "bin", "lsappinfo")
        if not os.path.isfile(executable):
            return None
        # At most one child per lookup; reap it before returning on timeout.
        process = await asyncio.create_subprocess_exec(
            executable, "info", "-only", "bundleid", "-only", "name",
            "-only", "applicationtype", f"pid:{pid}",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 2.0)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            return None
        return stdout.decode("utf-8", errors="replace") if process.returncode == 0 else None
    except Exception:
        return None


def _parse_lsappinfo(text) -> tuple[str, str | None] | None:
    bundles = re.findall(r'bundleID="([^"\n]+)"', text)
    if (len(bundles) != 1 or not policy.valid_bundle_id(bundles[0])
            or re.search(r'type="Foreground"', text) is None
            or len(re.findall(r'\bASN:', text)) > 1):
        return None
    name = re.search(r'^"([^"\n]*)" ASN', text, re.MULTILINE)
    return bundles[0], (name.group(1) or None) if name else None


async def _lsappinfo_list_text() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        executable = os.path.join(os.sep, "usr", "bin", "lsappinfo")
        if not os.path.isfile(executable):
            return None
        # At most one child per listing; reap it on timeout and cancellation.
        process = await asyncio.create_subprocess_exec(
            executable, "list", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return None
        return stdout.decode("utf-8", errors="replace") if process.returncode == 0 else None
    except Exception:
        return None


async def _run_lsappinfo(*args) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        executable = os.path.join(os.sep, "usr", "bin", "lsappinfo")
        if not os.path.isfile(executable):
            return None
        # At most one child per call; reap it on timeout and cancellation.
        process = await asyncio.create_subprocess_exec(
            executable, *args, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return None
        return stdout.decode("utf-8", errors="replace") if process.returncode == 0 else None
    except Exception:
        return None


def _parse_lsappinfo_front(front_text, info_text) -> tuple[bool, int | None]:
    if not isinstance(front_text, str) or not isinstance(info_text, str):
        return False, None
    pids = re.findall(r'\bpid\s+=\s+(-?[0-9]+)\b', info_text)
    types = re.findall(r'\btype="([^"\n]*)"', info_text)
    if len(pids) != 1 or types != ["Foreground"]:
        return True, None
    pid = int(pids[0])
    return True, pid if pid > 0 else None


async def _lsappinfo_front() -> tuple[bool, int | None]:
    try:
        front_text = await _run_lsappinfo("front")
        if front_text is None:
            return False, None
        asns = re.findall(r'\bASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+:', front_text)
        if len(asns) != 1:
            return False, None
        info_text = await _run_lsappinfo("info", "-only", "pid", "-only", "applicationtype", asns[0])
        if info_text is None:
            return False, None
        return _parse_lsappinfo_front(front_text, info_text)
    except Exception:
        return False, None


def _parse_lsappinfo_list(text) -> list[dict]:
    if not isinstance(text, str):
        return []
    headers = list(re.finditer(r'^\s*\d+\) "([^\n]*)" ASN:[^\n]*(?:\n|$)', text, re.MULTILINE))
    rows = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        # Fields in a quoted app name are not part of the record body.
        body = text[header.end():end]
        bundles = re.findall(r'\bbundleID="([^"\n]*)"', body)
        # The token also contains pid=N; only the separate pid = N field counts.
        pids = re.findall(r'\bpid\s+=\s+(-?[0-9]+)\b', body)
        types = re.findall(r'\btype="([^"\n]*)"', body)
        if (len(bundles) != 1 or not policy.valid_bundle_id(bundles[0])
                or len(pids) != 1 or types != ["Foreground"]):
            continue
        pid = int(pids[0])
        if pid <= 0:
            continue
        row = {"bundle_id": bundles[0], "kind": "desktop", "name": header.group(1) or None,
               "pid": pid, "running": True}
        path = re.search(r'\bbundle path="([^"\n]*)"', body)
        if path and os.path.isabs(path.group(1)):
            row["launch_path"] = path.group(1)
        rows.append(row)
        if len(rows) == 256:
            break
    return rows


async def running_apps_from_os() -> list[dict]:
    try:
        return _parse_lsappinfo_list(await _lsappinfo_list_text())
    except Exception:
        return []


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


_CG_LOAD_LOCK = threading.Lock()


@cache
def _cg_libraries():
    try:
        frameworks = os.path.join(os.sep, "System", "Library", "Frameworks")
        cg = ctypes.CDLL(os.path.join(frameworks, "CoreGraphics.framework", "CoreGraphics"))
        cf = ctypes.CDLL(os.path.join(frameworks, "CoreFoundation.framework", "CoreFoundation"))
        pointer = ctypes.c_void_p
        signatures = [
            (cg.CGWindowListCopyWindowInfo, [ctypes.c_uint32, ctypes.c_uint32], pointer),
            (cg.CGRectMakeWithDictionaryRepresentation, [pointer, ctypes.POINTER(_CGRect)], ctypes.c_bool),
            (cf.CFArrayGetCount, [pointer], ctypes.c_long),
            (cf.CFArrayGetValueAtIndex, [pointer, ctypes.c_long], pointer),
            (cf.CFDictionaryGetValue, [pointer, pointer], pointer),
            (cf.CFGetTypeID, [pointer], ctypes.c_ulong),
            (cf.CFNumberGetTypeID, [], ctypes.c_ulong),
            (cf.CFBooleanGetTypeID, [], ctypes.c_ulong),
            (cf.CFStringGetTypeID, [], ctypes.c_ulong),
            (cf.CFDictionaryGetTypeID, [], ctypes.c_ulong),
            (cf.CFNumberGetValue, [pointer, ctypes.c_int, pointer], ctypes.c_bool),
            (cf.CFBooleanGetValue, [pointer], ctypes.c_bool),
            (cf.CFStringGetLength, [pointer], ctypes.c_long),
            (cf.CFStringGetMaximumSizeForEncoding, [ctypes.c_long, ctypes.c_uint32], ctypes.c_long),
            (cf.CFStringGetCString, [pointer, pointer, ctypes.c_long, ctypes.c_uint32], ctypes.c_bool),
            (cf.CFRelease, [pointer], None),
        ]
        for function, args, result in signatures:
            function.argtypes = args
            function.restype = result
        keys = {name: pointer.in_dll(cg, name).value for name in (
            "kCGWindowNumber", "kCGWindowOwnerPID", "kCGWindowLayer", "kCGWindowName",
            "kCGWindowOwnerName", "kCGWindowIsOnscreen", "kCGWindowAlpha", "kCGWindowBounds",
        )}
        return cg, cf, keys
    except Exception:
        return None


def _cg_window_list() -> list[dict] | None:
    if sys.platform != "darwin":
        return None
    try:
        # Serialize the first load, including concurrent worker-thread calls.
        with _CG_LOAD_LOCK:
            libraries = _cg_libraries()
        if libraries is None:
            return None
        cg, cf, keys = libraries
        array = cg.CGWindowListCopyWindowInfo(1 | 16, 0)
        if not array:
            return None
        try:
            rows = []
            number_type = cf.CFNumberGetTypeID()
            string_type = cf.CFStringGetTypeID()
            boolean_type = cf.CFBooleanGetTypeID()
            dictionary_type = cf.CFDictionaryGetTypeID()
            utf8 = 0x08000100
            for index in range(cf.CFArrayGetCount(array)):
                dictionary = cf.CFArrayGetValueAtIndex(array, index)
                if not dictionary or cf.CFGetTypeID(dictionary) != dictionary_type:
                    continue
                row = {}
                for name, key in keys.items():
                    value = cf.CFDictionaryGetValue(dictionary, key)
                    if not value:
                        continue
                    kind = cf.CFGetTypeID(value)
                    if name in ("kCGWindowName", "kCGWindowOwnerName") and kind == string_type:
                        size = cf.CFStringGetMaximumSizeForEncoding(cf.CFStringGetLength(value), utf8) + 1
                        buffer = ctypes.create_string_buffer(size)
                        if cf.CFStringGetCString(value, buffer, size, utf8):
                            row[name] = buffer.value.decode("utf-8")
                    elif name == "kCGWindowIsOnscreen" and kind == boolean_type:
                        row[name] = bool(cf.CFBooleanGetValue(value))
                    elif name == "kCGWindowBounds" and kind == dictionary_type:
                        rect = _CGRect()
                        if cg.CGRectMakeWithDictionaryRepresentation(value, ctypes.byref(rect)):
                            row[name] = {"X": rect.origin.x, "Y": rect.origin.y,
                                         "Width": rect.size.width, "Height": rect.size.height}
                    elif name in ("kCGWindowNumber", "kCGWindowOwnerPID", "kCGWindowLayer",
                                  "kCGWindowAlpha") and kind == number_type:
                        # CFNumber types: kCFNumberFloat64Type=6, kCFNumberSInt64Type=4.
                        numeric = ctypes.c_double() if name == "kCGWindowAlpha" else ctypes.c_int64()
                        if cf.CFNumberGetValue(value, 6 if name == "kCGWindowAlpha" else 4,
                                               ctypes.byref(numeric)):
                            row[name] = numeric.value
                rows.append(row)
            return rows
        finally:
            cf.CFRelease(array)
    except Exception:
        return None


def _panel_rows(raw, pid, base_z) -> list[dict]:
    if not isinstance(raw, list) or type(pid) is not int:
        return []
    rows = []
    for item in raw:
        try:
            if not isinstance(item, dict):
                continue
            owner, number, layer = (item.get(key) for key in (
                "kCGWindowOwnerPID", "kCGWindowNumber", "kCGWindowLayer"))
            if (type(owner) is not int or owner != pid or type(number) is not int or number <= 0
                    or type(layer) is not int or not 1 <= layer <= 23
                    or item.get("kCGWindowIsOnscreen") is not True):
                continue
            alpha = item.get("kCGWindowAlpha", 1.0)
            if type(alpha) not in (int, float) or not alpha > 0:
                continue
            bounds = item["kCGWindowBounds"]
            values = [bounds[key] for key in ("X", "Y", "Width", "Height")]
            if (any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
                    or values[2] < 40 or values[3] < 40):
                continue
            name, title = item.get("kCGWindowOwnerName"), item.get("kCGWindowName")
            rows.append({
                "window_id": number, "pid": owner,
                "app_name": name if isinstance(name, str) else None,
                "title": title if isinstance(title, str) else "", "layer": layer,
                "bounds": dict(zip(("x", "y", "width", "height"), map(int, values))),
                "is_on_screen": True, "on_current_space": True,
            })
            if len(rows) == 64:
                break
        except Exception:
            continue
    for position, row in enumerate(rows):
        row["z_index"] = base_z + len(rows) - position
    return rows


async def panel_windows_from_os(pid, base_z) -> list[dict]:
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_cg_window_list), 2.0)
        return _panel_rows(raw, pid, base_z)
    except Exception:
        return []


async def identify_running_app(driver, pid) -> tuple[str | None, str | None]:
    bundle_id, name = (await driver.list_running_apps()).get(pid, (None, None))
    if policy.valid_bundle_id(bundle_id):
        return bundle_id, name
    if type(pid) is not int or pid <= 0:
        return None, None
    try:
        text = await _lsappinfo_text(pid)
        identity = _parse_lsappinfo(text) if text is not None else None
        if identity is not None:
            return identity[0], name or identity[1]
    except Exception:
        pass
    return None, None


async def fetch_runtime() -> tuple[str, str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{approval_bridge.BACKEND_URL}/computer-use/settings",
                headers=approval_bridge._get_headers(),
            )
            response.raise_for_status()
            data = response.json()
        runtime = data.get("runtime") if isinstance(data, dict) else None
        if not isinstance(runtime, dict) or runtime.get("running") is not True:
            raise DriverUnavailable("Embedded computer-use driver is not running")
        binary, socket = runtime.get("driver_path"), runtime.get("socket_path")
        if (not isinstance(binary, str) or not os.path.isabs(binary)
                or os.path.basename(binary) != "cua-driver" or not os.path.isfile(binary)
                or not isinstance(socket, str) or not os.path.isabs(socket)):
            raise DriverUnavailable("Embedded computer-use driver runtime is invalid")
        return binary, socket
    except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
        raise DriverUnavailable("Embedded computer-use driver runtime is unavailable") from exc


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
        self.command = [binary, "mcp", "--embedded", "--socket", socket]
        self.env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
        self.env.update(CUA_DRIVER_EMBEDDED="1", DO_NOT_TRACK="1",
                        CUA_DRIVER_RS_TELEMETRY_ENABLED="0", CUA_DRIVER_RS_UPDATE_CHECK="0")
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
        try:
            answered, pid = await _lsappinfo_front()
            if answered:
                return pid
            result = await self.call("list_apps", {})
            if result.is_error:
                return None
            # A reply may carry the same rows as structured content and as text.
            active = []
            for row in self._app_rows(result):
                if row.get("active") is True and row.get("running") is not False and row.get("pid") not in active:
                    active.append(row.get("pid"))
            if len(active) != 1:
                return None
            pid = active[0]
            return pid if type(pid) is int and pid > 0 else None
        except Exception:
            return None

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
