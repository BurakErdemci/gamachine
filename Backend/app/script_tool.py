"""run_script: the model's JavaScript runs in a QuickJS-wasm sandbox and calls Gamachine tools.

The sandbox is `script_runner/runner.mjs`, one process per script, run by Electron's own Node
(GAMACHINE_NODE_PATH from the app, ELECTRON_RUN_AS_NODE=1) or a `node` on PATH. The script can act
only through `tools.<name>(args)`; every such call comes back here as a line on the runner's stdout
and is relayed to a ToolGroup. A group owns one family of Gamachine tools and calls each tool
through the same function, and so the same gate, that a direct call by this agent reaches:

- `unity_ai_mcp.tools.script_tools.LocalToolGroup`: the unityai server's own file and shell tools,
  through MCPServer.call_tool (their approval_bridge cards and guards inside).
- `UnityMCPGroup` below: Unity MCP tools over the same stdio-to-HTTP bridge code the CLI agents use
  (X-API-Key from the token file, X-Gamachine-Conversation), so the Unity MCP server's own gate
  judges every call; for Claude an extra `gate` runs the session's `_can_use_tool` first.

Groups are chosen per chat from what that agent has. Computer-use tools are never a group: every
step there needs the model's eyes on the screen.

Only the script's return value, its console output and the list of calls made go back to the
model, capped at REPLY_CAP_BYTES; the full text then goes to <workspace>/.gamachine/script-runs/.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

TOOL_NAME = "run_script"
NODE_ENV = "GAMACHINE_NODE_PATH"
REPLY_CAP_BYTES = 6000
CALL_LIST_CAP = 1500
DEFAULT_TIMEOUT_S = 600
MAX_TIMEOUT_S = 600
# The calling CLI cancels a long MCP call on its own (measured 25 Nov 2026, from code and binaries):
# Codex 60 s unless the entry sets tool_timeout_sec (Gamachine sets 660 for unityai), agy 180 s fixed,
# Copilot 180 s, Kimi/OpenCode likely 60 s (SDK default; OpenCode's schema text says 5 s), Cursor
# unknown, Claude Code 1e8 ms. The unityai entry names this family's cap in SCRIPT_MAX_ENV where
# Gamachine writes it (Codex 600, agy 150); without it the cap stays under the 60 s default.
SCRIPT_MAX_ENV = "GAMACHINE_SCRIPT_MAX_S"
UNKNOWN_CLI_MAX_S = 50
CODEX_UNITYAI_TOOL_TIMEOUT_S = 660.0
AGY_SCRIPT_MAX_S = 150
CALL_TIMEOUT_S = 300
MAX_CALLS = 200
MEMORY_BYTES = 64 * 1024 * 1024
MAX_PARALLEL_CALLS = 8
# The runner enforces the wall clock itself; this is the margin before the host kills it anyway.
KILL_GRACE_S = 5.0
RUNS_DIR = (".gamachine", "script-runs")

# Turkish text of the Unity MCP server's refusal (unity-mcp/Server/src/transport/approval_gate.py).
UNITY_REFUSAL_MARK = "onay gerektiriyor ve onaylanmadı"


# ── outcomes and groups ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Outcome:
    """What one inner call came to: ok (value to the script), error (thrown in the script,
    which may catch it and go on) or stop (a plain refusal or a stopped turn: the script ends)."""
    kind: str
    value: Any = None
    message: str = ""

    @classmethod
    def ok(cls, value: Any) -> "Outcome":
        return cls("ok", value=value)

    @classmethod
    def error(cls, message: str) -> "Outcome":
        return cls("error", message=message)

    @classmethod
    def stop(cls, message: str) -> "Outcome":
        return cls("stop", message=message)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)


class ToolGroup(Protocol):
    label: str

    async def list_tools(self) -> list[ToolSpec]: ...

    async def call(self, name: str, args: dict) -> Outcome: ...


def parse_tool_text(text: str) -> Any:
    """A tool's text result as the script sees it: parsed JSON when it is JSON, else the text."""
    stripped = text.strip()
    if stripped[:1] in ("{", "[", '"') or stripped in ("true", "false", "null") or _is_number(stripped):
        try:
            return json.loads(stripped)
        except ValueError:
            pass
    return text


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return bool(text)


def content_text(content: Any) -> str:
    """Joins the text blocks of an MCP result (objects or plain dicts)."""
    parts = []
    for block in content or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


# ── Unity MCP over the CLI bridge ────────────────────────────────────────────

_BRIDGES: dict = {}
_BRIDGES_LOCK = threading.Lock()


class _BridgeClient:
    """One MCP session on the Unity MCP server through `codex_unitymcp_bridge.Bridge`."""

    def __init__(self, bridge):
        self.bridge = bridge
        self._ids = itertools.count(1)
        self._init_lock = threading.Lock()
        self._ready = False

    def _request(self, method: str, params: dict) -> dict:
        req_id = f"gamachine-script-{next(self._ids)}"
        replies = self.bridge.handle({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        for reply in replies:
            if reply.get("id") == req_id and "method" not in reply:
                return reply
        return {"error": {"message": "Unity MCP gave no answer"}}

    def _ensure(self) -> Optional[str]:
        with self._init_lock:
            if self._ready:
                return None
            reply = self._request("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "gamachine-run-script", "version": "1"},
            })
            if "error" in reply:
                return str(reply["error"].get("message") or reply["error"])
            self.bridge.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._ready = True
            return None

    def list_tools(self) -> tuple[list[ToolSpec], Optional[str]]:
        failure = self._ensure()
        if failure:
            return [], failure
        reply = self._request("tools/list", {})
        if "error" in reply:
            return [], str(reply["error"].get("message") or reply["error"])
        specs = []
        for tool in (reply.get("result") or {}).get("tools") or []:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                specs.append(ToolSpec(tool["name"], tool.get("description") or "", tool.get("inputSchema") or {}))
        return specs, None

    def call(self, name: str, args: dict) -> Outcome:
        failure = self._ensure()
        if failure:
            return Outcome.error(f"Unity MCP unavailable: {failure}")
        reply = self._request("tools/call", {"name": name, "arguments": args})
        if "error" in reply:
            return Outcome.error(str(reply["error"].get("message") or reply["error"]))
        result = reply.get("result") or {}
        text = content_text(result.get("content"))
        if result.get("isError"):
            if UNITY_REFUSAL_MARK in text:
                return Outcome.stop(text)
            return Outcome.error(text or f"{name} failed")
        parsed = parse_tool_text(text) if text else None
        if (parsed is None or isinstance(parsed, str)) and isinstance(result.get("structuredContent"), dict):
            return Outcome.ok(result["structuredContent"])
        return Outcome.ok(parsed)


def unity_bridge_client(url: str, *, conversation_id: Optional[int],
                        secret_reader: Optional[Callable[[], str]] = None) -> _BridgeClient:
    """A cached client for (url, conversation); the bridge rebuilds a lost server session itself."""
    from providers import codex_unitymcp_bridge as bridge_mod
    key = (url, conversation_id)
    with _BRIDGES_LOCK:
        client = _BRIDGES.get(key)
        if client is None:
            kwargs = {"conversation_reader": lambda: conversation_id}
            if secret_reader is not None:
                kwargs["secret_reader"] = secret_reader
            client = _BridgeClient(bridge_mod.Bridge(url, **kwargs))
            _BRIDGES[key] = client
        return client


GateFn = Callable[[str, dict], Awaitable[Optional[Outcome]]]


class UnityMCPGroup:
    """Unity MCP tools. `gate` (Claude) returns None to let a call through, else its Outcome."""

    label = "Unity MCP"

    def __init__(self, client_factory: Callable[[], Awaitable[Optional[_BridgeClient]]], *,
                 gate: Optional[GateFn] = None, hidden: Sequence[str] = ()):
        self._client_factory = client_factory
        self._gate = gate
        self._hidden = frozenset(hidden)
        self._client: Optional[_BridgeClient] = None
        self.note: Optional[str] = None

    async def _get_client(self) -> Optional[_BridgeClient]:
        if self._client is None:
            self._client = await self._client_factory()
        return self._client

    async def list_tools(self) -> list[ToolSpec]:
        client = await self._get_client()
        if client is None:
            self.note = "Unity MCP is not running, so no Unity tools were available"
            return []
        specs, failure = await asyncio.to_thread(client.list_tools)
        if failure:
            self.note = f"Unity tools unavailable: {failure}"
        return [s for s in specs if s.name not in self._hidden]

    async def call(self, name: str, args: dict) -> Outcome:
        if name in self._hidden:
            return Outcome.error(f"{name} is not available to this agent")
        if self._gate is not None:
            verdict = await self._gate(name, args)
            if verdict is not None:
                return verdict
        client = await self._get_client()
        if client is None:
            return Outcome.error("Unity MCP is not running")
        return await asyncio.to_thread(client.call, name, args)


# ── stdio MCP servers (browser, connectors, mail) ───────────────────────────

# Tool lists by server entry (the entry carries the chat id): listing starts the server, calls
# only start it when the script makes one.
_STDIO_LISTS: dict = {}
STDIO_START_TIMEOUT_S = 60.0


def _entry_key(entry: dict) -> str:
    return json.dumps({k: entry.get(k) for k in ("command", "args", "env")}, sort_keys=True, default=str)


class _StdioClient:
    """One MCP session on a stdio server child; the child lives in one task, so its streams open and
    close in the same task (anyio's rule), and dies when the run closes the client."""

    def __init__(self, entry: dict):
        from mcp.client.stdio import StdioServerParameters
        self.params = StdioServerParameters(command=entry["command"], args=list(entry.get("args") or []),
                                            env=dict(entry.get("env") or {}) or None)
        self.session = None
        self._error = ""
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task = None

    async def start(self):
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), STDIO_START_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._error = "did not start in time"
        if self.session is None:
            await self.aclose()
            raise RuntimeError(f"{self.params.command}: {self._error or 'did not start'}")

    async def _run(self):
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client
        try:
            async with stdio_client(self.params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                self.session = session
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:  # noqa: BLE001 - reported to the caller through start()/call()
            self._error = f"{type(exc).__name__}: {exc}"
        finally:
            self.session = None
            self._ready.set()

    async def aclose(self):
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 10)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()


def mcp_result_outcome(name: str, result: Any, stop_marks: Sequence[str] = ()) -> Outcome:
    """An MCP CallToolResult (object) as an Outcome; images are dropped, scripts only get text."""
    blocks = getattr(result, "content", None) or []
    text = content_text(blocks)
    if any(getattr(b, "type", None) == "image" for b in blocks):
        text = (text + "\n" if text else "") + "[image omitted: scripts get text only]"
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        if any(mark in text for mark in stop_marks):
            return Outcome.stop(text)
        return Outcome.error(text or f"{name} failed")
    parsed = parse_tool_text(text) if text else None
    structured = getattr(result, "structured_content", None)
    if (parsed is None or isinstance(parsed, str)) and isinstance(structured, dict):
        return Outcome.ok(structured)
    return Outcome.ok(parsed)


def read_only_tool(tool) -> bool:
    annotations = getattr(tool, "annotations", None)
    return getattr(annotations, "read_only_hint", None) is True


class StdioServerGroup:
    """The tools of one stdio MCP server, started from the very entry this agent's CLI was given,
    so each call runs that server's own code and reaches its backend gate. `keep` narrows the tools
    (the browser's read-only ones); a tool it drops is neither listed nor callable. `gate` (Claude)
    runs before the call."""

    def __init__(self, label: str, entry: dict, *, keep: Callable[[Any], bool] = lambda tool: True,
                 gate: Optional[GateFn] = None, stop_marks: Sequence[str] = ()):
        self.label = label
        self._entry = entry
        self._keep = keep
        self._gate = gate
        self._stop_marks = tuple(stop_marks)
        self._client: Optional[_StdioClient] = None
        self._names: Optional[frozenset] = None
        self.note: Optional[str] = None

    async def _session(self):
        if self._client is None or self._client.session is None:
            self._client = _StdioClient(self._entry)
            await self._client.start()
        return self._client.session

    async def list_tools(self) -> list[ToolSpec]:
        key = _entry_key(self._entry)
        tools = _STDIO_LISTS.get(key)
        if tools is None:
            try:
                tools = list((await (await self._session()).list_tools()).tools)
            except Exception as exc:  # noqa: BLE001 - this group is just missing for the run
                self.note = f"{self.label} tools unavailable: {exc}"
                self._names = frozenset()
                return []
            _STDIO_LISTS[key] = tools
        kept = [t for t in tools if self._keep(t)]
        self._names = frozenset(t.name for t in kept)
        return [ToolSpec(t.name, t.description or "", getattr(t, "input_schema", None) or {}) for t in kept]

    async def call(self, name: str, args: dict) -> Outcome:
        if self._names is None:
            await self.list_tools()
        if name not in self._names:
            return Outcome.error(f"{name} is not available in scripts")
        if self._gate is not None:
            verdict = await self._gate(name, args)
            if verdict is not None:
                return verdict
        try:
            session = await self._session()
            result = await session.call_tool(name, args, read_timeout_seconds=CALL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - a dead server is an error for the script
            return Outcome.error(f"{self.label}: {type(exc).__name__}: {exc}")
        return mcp_result_outcome(name, result, self._stop_marks)

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── runtime ──────────────────────────────────────────────────────────────────

def runner_path() -> Path:
    """Next to the frozen backend binary (electron-builder copies Backend/script_runner there), else
    Backend/script_runner from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "script_runner" / "runner.mjs"
    return Path(__file__).resolve().parent.parent / "script_runner" / "runner.mjs"


def _app_electron_binary() -> Optional[str]:
    """The packaged app's own executable, found from the frozen backend's place in resources/.

    The CLI agents do not pass the backend's environment on to their MCP servers, so the unityai
    server may never see GAMACHINE_NODE_PATH.
    """
    if not getattr(sys, "frozen", False):
        return None
    resources = Path(sys.executable).resolve().parent.parent
    if sys.platform == "win32":
        candidate = resources.parent / "Gamachine.exe"
    elif sys.platform == "darwin":
        candidate = resources.parent / "MacOS" / "Gamachine"
    else:
        candidate = resources.parent / "gamachine"
    return str(candidate) if candidate.is_file() else None


_VERSIONS: dict = {}


def _child_env() -> dict:
    """The runner gets no secrets: only what Node needs to start."""
    env = {"ELECTRON_RUN_AS_NODE": "1"}
    for key in ("SYSTEMROOT", "SystemRoot", "WINDIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _creationflags() -> int:
    return 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def _node_version(binary: str) -> Optional[tuple]:
    if binary in _VERSIONS:
        return _VERSIONS[binary]
    version = None
    try:
        proc = subprocess.run([binary, "-p", "process.versions.node"], capture_output=True, text=True,
                              timeout=20, env=_child_env(), creationflags=_creationflags())
        parts = proc.stdout.strip().split(".")
        if proc.returncode == 0 and len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            version = (int(parts[0]), int(parts[1]))
    except (OSError, subprocess.SubprocessError):
        version = None
    _VERSIONS[binary] = version
    return version


def _permission_flags(version: tuple, runner_dir: Path) -> list[str]:
    """Node's permission model as a second wall: the host process may read only the runner folder,
    and cannot write files or start processes. Electron 34 (Node 20) knows only the experimental
    spelling with a wildcard; Node 23.5+ renamed it (measured both, 25 Nov 2026)."""
    major, minor = version
    if major > 23 or (major == 23 and minor >= 5):
        return ["--permission", f"--allow-fs-read={runner_dir}"]
    if major >= 20:
        return ["--experimental-permission", f"--allow-fs-read={runner_dir}{os.sep}*"]
    return []


def find_runtime() -> Optional[list[str]]:
    """argv prefix that runs runner.mjs, or None when no JavaScript runtime exists."""
    candidates = []
    env_binary = os.environ.get(NODE_ENV, "").strip()
    if env_binary:
        candidates.append(env_binary)
    app_binary = _app_electron_binary()
    if app_binary:
        candidates.append(app_binary)
    path_node = shutil.which("node")
    if path_node:
        candidates.append(path_node)
    runner_dir = runner_path().parent
    for binary in candidates:
        version = _node_version(binary)
        if version is None:
            continue
        return [binary, "--no-warnings", "--max-old-space-size=256",
                *_permission_flags(version, runner_dir)]
    return None


# ── one run ──────────────────────────────────────────────────────────────────

@dataclass
class CallRecord:
    index: int
    tool: str
    status: str = "running"  # ok | error | refused | timeout | unfinished
    reason: str = ""


@dataclass
class ScriptResult:
    status: str  # ok | error
    kind: str = ""
    message: str = ""
    value: Any = None
    has_value: bool = False
    logs: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    seconds: float = 0.0


async def _collect_tools(groups: Sequence[ToolGroup]) -> tuple[dict, list]:
    table: dict = {}
    notes = []
    for group in groups:
        try:
            specs = await group.list_tools()
        except Exception as exc:  # noqa: BLE001 - one broken group must not sink the others
            notes.append(f"{getattr(group, 'label', 'tools')} unavailable: {exc}")
            continue
        note = getattr(group, "note", None)
        if note:
            notes.append(note)
        for spec in specs:
            table.setdefault(spec.name, group)
    return table, notes


def max_timeout_from_env() -> int:
    """This process's cap on one script's wall clock (see SCRIPT_MAX_ENV)."""
    raw = os.environ.get(SCRIPT_MAX_ENV, "").strip()
    if raw.isdigit() and 1 <= int(raw) <= MAX_TIMEOUT_S:
        return int(raw)
    return UNKNOWN_CLI_MAX_S


async def execute(code: str, groups: Sequence[ToolGroup], *, timeout_s: Optional[float] = None,
                  limits: Optional[dict] = None, runtime: Optional[list] = None,
                  max_timeout_s: float = MAX_TIMEOUT_S) -> ScriptResult:
    started = time.monotonic()
    cap = max(1.0, min(float(MAX_TIMEOUT_S), float(max_timeout_s)))
    wall_s = float(cap if timeout_s is None else timeout_s)
    wall_s = max(1.0, min(cap, wall_s))
    runner = runner_path()
    argv_prefix = runtime if runtime is not None else await asyncio.to_thread(find_runtime)
    if argv_prefix is None or not runner.is_file():
        what = ("no JavaScript runtime was found (the Gamachine app's Electron, or `node` on PATH)"
                if argv_prefix is None else f"the sandbox runner is missing ({runner})")
        return ScriptResult("error", "unavailable", f"run_script is unavailable here: {what}. "
                            "Make the tool calls directly instead.")

    table, notes = await _collect_tools(groups)
    run_limits = {"wallMs": int(wall_s * 1000), "memoryBytes": MEMORY_BYTES,
                  "callTimeoutMs": CALL_TIMEOUT_S * 1000, "maxCalls": MAX_CALLS, **(limits or {})}
    result = ScriptResult("error", notes=notes)
    calls: list[CallRecord] = result.calls

    try:
        proc = subprocess.Popen(
            [*argv_prefix, str(runner)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(runner.parent), env=_child_env(),
            creationflags=_creationflags())
    except OSError as exc:
        await _close_groups(groups)
        result.kind, result.message = "unavailable", f"run_script could not start its sandbox: {exc}"
        return result

    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue = asyncio.Queue()
    stderr_tail: list = []
    write_lock = threading.Lock()

    def post(item):
        try:
            loop.call_soon_threadsafe(inbox.put_nowait, item)
        except RuntimeError:  # the loop is gone; the run is over anyway
            pass

    def read_stdout():
        try:
            for raw in proc.stdout:
                post(raw)
        except (OSError, ValueError):
            pass
        post(None)

    def read_stderr():
        try:
            for raw in proc.stderr:
                stderr_tail.append(raw.decode("utf-8", "replace"))
                del stderr_tail[:-20]
        except (OSError, ValueError):
            pass

    threading.Thread(target=read_stdout, daemon=True, name="run-script-out").start()
    threading.Thread(target=read_stderr, daemon=True, name="run-script-err").start()

    def write_line(obj) -> None:
        data = (json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        with write_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, ValueError):
                pass

    slots = asyncio.Semaphore(MAX_PARALLEL_CALLS)
    tasks: set = set()

    async def relay(record: CallRecord, msg_id: int, args: dict) -> None:
        group = table.get(record.tool)
        async with slots:
            try:
                outcome = (await group.call(record.tool, args) if group is not None
                           else Outcome.error(f"no tool named {record.tool}"))
            except Exception as exc:  # noqa: BLE001 - a broken tool is an error for the script, not a crash
                logger.warning("[run_script] %s raised: %r", record.tool, exc)
                outcome = Outcome.error(f"{type(exc).__name__}: {exc}")
        if outcome.kind == "ok":
            record.status = "ok"
            await asyncio.to_thread(write_line, {"type": "result", "id": msg_id, "ok": True, "value": outcome.value})
        elif outcome.kind == "error":
            record.status, record.reason = "error", outcome.message
            await asyncio.to_thread(write_line, {"type": "result", "id": msg_id, "ok": False, "error": outcome.message})
        else:
            record.status, record.reason = "refused", outcome.message
            inbox.put_nowait({"type": "_stop", "tool": record.tool, "message": outcome.message})

    deadline = started + wall_s + KILL_GRACE_S
    try:
        await asyncio.to_thread(write_line, {"type": "start", "code": code, "tools": sorted(table),
                                             "limits": run_limits})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result.kind, result.message = "timeout", f"script ran past its {wall_s:g} s limit"
                break
            try:
                item = await asyncio.wait_for(inbox.get(), remaining)
            except asyncio.TimeoutError:
                continue
            if item is None:
                tail = "".join(stderr_tail).strip()[-800:]
                result.kind = "crashed"
                result.message = "the sandbox exited without a result" + (f": {tail}" if tail else "")
                break
            if isinstance(item, (bytes, bytearray)):
                try:
                    msg = json.loads(item.decode("utf-8", "replace"))
                except ValueError:
                    continue
            else:
                msg = item
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "call":
                record = CallRecord(len(calls) + 1, str(msg.get("tool")))
                calls.append(record)
                args = msg.get("args") if isinstance(msg.get("args"), dict) else {}
                task = asyncio.create_task(relay(record, msg.get("id"), args))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif kind == "log":
                result.logs.append(str(msg.get("text", "")))
            elif kind == "done":
                result.status, result.kind = "ok", ""
                result.value, result.has_value = msg.get("value"), not msg.get("undefined")
                break
            elif kind == "error":
                result.kind, result.message = str(msg.get("kind") or "thrown"), str(msg.get("message") or "")
                # Runner call ids count calls in the order it sent them, as `calls` does.
                late = msg.get("callId")
                if type(late) is int and 0 < late <= len(calls) and calls[late - 1].status == "running":
                    calls[late - 1].status, calls[late - 1].reason = "timeout", "no answer in time"
                break
            elif kind == "_stop":
                result.kind = "refused"
                result.message = f"{msg['tool']} was refused, so the script was ended there: {msg['message']}"
                break
    finally:
        for task in list(tasks):
            task.cancel()
        _kill(proc)
        await _close_groups(groups)
        result.seconds = time.monotonic() - started
    for record in calls:
        if record.status == "running":
            record.status = "unfinished"
    return result


async def _close_groups(groups: Sequence[ToolGroup]) -> None:
    for group in groups:
        closer = getattr(group, "aclose", None)
        if closer is None:
            continue
        try:
            await closer()
        except Exception as exc:  # noqa: BLE001 - closing must not hide the run's own result
            logger.warning("[run_script] closing %s failed: %r", getattr(group, "label", group), exc)


def _kill(proc: subprocess.Popen) -> None:
    try:
        if proc.stdin:
            proc.stdin.close()
    except OSError:
        pass
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("[run_script] runner pid %s did not exit after kill", proc.pid)


# ── reply ────────────────────────────────────────────────────────────────────

_KIND_TEXT = {
    "syntax": "the script did not compile",
    "thrown": "the script threw an error",
    "timeout": "the script hit its time limit",
    "memory": "the script hit its memory limit",
    "call_timeout": "a tool call did not answer in time",
    "call_limit": f"the script made more than {MAX_CALLS} tool calls",
    "refused": "a tool call was refused",
    "unavailable": "run_script is unavailable",
    "crashed": "the sandbox stopped unexpectedly",
    "protocol": "the sandbox protocol broke",
    "internal": "the sandbox failed",
}


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def calls_line(calls: Sequence[CallRecord], *, full: bool = False) -> str:
    """Calls in order, consecutive ok calls of one tool folded into a range."""
    parts = []
    i = 0
    while i < len(calls):
        rec = calls[i]
        if rec.status == "ok":
            j = i
            while j + 1 < len(calls) and calls[j + 1].status == "ok" and calls[j + 1].tool == rec.tool:
                j += 1
            span = f"{rec.index}" if j == i else f"{rec.index}-{calls[j].index}"
            parts.append(f"{span} {rec.tool} ok" + ("" if j == i else f" ({j - i + 1})"))
            i = j + 1
            continue
        reason = rec.reason if full else _short(rec.reason)
        parts.append(f"{rec.index} {rec.tool} {rec.status}" + (f" ({reason})" if reason else ""))
        i += 1
    line = "; ".join(parts)
    if full or len(line) <= CALL_LIST_CAP:
        return line
    head, tail = line[: CALL_LIST_CAP // 2], line[-CALL_LIST_CAP // 2:]
    return f"{head} … (list shortened; full list in the file below) … {tail}"


def _summary(result: ScriptResult) -> str:
    n = len(result.calls)
    done = sum(1 for c in result.calls if c.status == "ok")
    counts = {}
    for c in result.calls:
        counts[c.status] = counts.get(c.status, 0) + 1
    detail = ", ".join(f"{v} {k}" for k, v in counts.items())
    if result.status == "ok":
        return f"run_script finished in {result.seconds:.1f} s · {n} tool call(s)" + (f" ({detail})" if n else "")
    head = f"run_script stopped: {_KIND_TEXT.get(result.kind, result.kind)}"
    tail = (f"{done} of {n} tool call(s) completed before it stopped; their effects stay"
            f" ({detail})." if n else "No tool call was made.")
    return f"{head} after {result.seconds:.1f} s. {tail}"


def _render(result: ScriptResult, *, full: bool, value_text: str, logs_text: str) -> list[tuple[str, str]]:
    sections = [("", _summary(result))]
    if result.status != "ok" and result.message:
        sections.append(("Error", result.message))
    if result.calls:
        sections.append(("Tool calls in order", calls_line(result.calls, full=full)))
    for note in result.notes:
        sections.append(("Note", note))
    if result.status == "ok":
        sections.append(("Result", value_text if result.has_value else "(the script returned nothing)"))
    if logs_text:
        sections.append(("Console", logs_text))
    return sections


def _join(sections) -> str:
    return "\n".join(f"{title}:\n{body}" if title else body for title, body in sections)


def _value_text(result: ScriptResult) -> str:
    if not result.has_value:
        return ""
    if isinstance(result.value, str):
        return result.value
    return json.dumps(result.value, ensure_ascii=False, default=str)


def _save_full(text: str, workspace: str) -> tuple[Optional[str], Optional[str]]:
    try:
        folder = Path(workspace or os.getcwd()).joinpath(*RUNS_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.txt"
        path.write_text(text, encoding="utf-8")
        return str(path), None
    except OSError as exc:
        return None, str(exc)


def _cut(text: str, budget: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= budget:
        return text
    budget = max(0, budget - 40)
    return raw[:budget].decode("utf-8", "ignore") + f"\n… [cut, {len(raw)} bytes in all]"


def format_reply(result: ScriptResult, workspace: str) -> str:
    value_text = _value_text(result)
    logs_text = "\n".join(result.logs)
    full_text = _join(_render(result, full=True, value_text=value_text, logs_text=logs_text))
    short = _join(_render(result, full=False, value_text=value_text, logs_text=logs_text))
    if len(short.encode("utf-8")) <= REPLY_CAP_BYTES and short == full_text:
        return short
    path, failure = _save_full(full_text, workspace)
    footer = (f"Full output ({len(full_text.encode('utf-8'))} bytes) saved to: {path}" if path
              else f"Full output could not be saved: {failure}")
    fixed = _join(_render(result, full=False, value_text="", logs_text=""))
    budget = REPLY_CAP_BYTES - len(fixed.encode("utf-8")) - len(footer.encode("utf-8")) - 64
    if result.status == "ok" and logs_text:
        value_budget = max(budget * 2 // 3, budget - len(logs_text.encode("utf-8")))
    else:
        value_budget = budget if result.status == "ok" else 0
    value_cut = _cut(value_text, max(0, value_budget)) if result.has_value else value_text
    used = len(value_cut.encode("utf-8")) if result.status == "ok" else 0
    logs_cut = _cut(logs_text, max(0, budget - used)) if logs_text else ""
    sections = _render(result, full=False, value_text=value_cut, logs_text=logs_cut)
    sections.append(("", footer))
    return _join(sections)


async def run_script(code: str, groups: Sequence[ToolGroup], workspace: str,
                     timeout: Optional[float] = None, max_timeout_s: float = MAX_TIMEOUT_S) -> tuple[str, bool]:
    """(reply text, is_error) for one run_script call."""
    if not isinstance(code, str) or not code.strip():
        return "run_script needs `code`: the JavaScript body to run.", True
    result = await execute(code, groups, timeout_s=timeout, max_timeout_s=max_timeout_s)
    return format_reply(result, workspace), result.status != "ok"


# ── description ──────────────────────────────────────────────────────────────

def _type_of(schema: dict) -> str:
    if not isinstance(schema, dict):
        return "any"
    if "enum" in schema and isinstance(schema["enum"], list):
        return "|".join(json.dumps(v) for v in schema["enum"][:6])
    kind = schema.get("type")
    if isinstance(kind, list):
        return "|".join(str(k) for k in kind)
    if kind:
        return str(kind)
    for key in ("anyOf", "oneOf"):
        if isinstance(schema.get(key), list):
            return "|".join(_type_of(s) for s in schema[key] if s.get("type") != "null") or "any"
    return "any"


def signature(spec: ToolSpec) -> str:
    props = (spec.input_schema or {}).get("properties") or {}
    required = set((spec.input_schema or {}).get("required") or [])
    args = ", ".join(f"{k}{'' if k in required else '?'}: {_type_of(v)}" for k, v in props.items())
    first = (spec.description or "").strip().split("\n")[0].split(". ")[0].strip()
    return f"tools.{spec.name}({{{args}}})" + (f" — {_short(first, 100)}" if first else "")


def tool_description(tool_lines: Sequence[str], extra_lines: Sequence[str] = (),
                     max_timeout_s: int = MAX_TIMEOUT_S) -> str:
    listing = "\n".join(f"  {line}" for line in [*tool_lines, *extra_lines]) or "  (none)"
    return (
        "Run a short JavaScript program that calls your Gamachine tools as async functions; "
        "only what the program returns (plus console output and the list of calls it made) comes "
        "back to you, so large intermediate results stay inside the script.\n"
        "Use it for several dependent, repeated or parallel tool calls, or to filter, count or "
        "pick fields out of big tool results. If you do not know yet what a tool returns, look at "
        "one or two results with direct calls first, then script the repetitive, chained, parallel "
        "or filtering part. Do not use it for one or two calls, or for steps where you need to "
        "judge each result before the next call. It does not run C# or Unity editor code "
        "(that is execute_code) and has no shell of its own.\n"
        "Sandbox: no files, network, processes, timers or require; tools are the only way to act, "
        "and each call passes the same approval and safety rules as a direct call (cards may "
        "appear; a plain user refusal ends the script).\n"
        "API: `await tools.<name>({...})` returns the tool's result (parsed JSON, else text); a "
        "failed call throws an Error you can catch and go on. `callTool(name, args)` works too. "
        "`console.log` output is returned. Top-level await and `return <value>` work: return a "
        "small JSON value, not raw tool output.\n"
        f"Limits: `timeout` seconds (default and max {max_timeout_s} here: the longest this agent "
        f"lets one tool call run, with a margin; split longer work), {MAX_CALLS} tool calls, "
        f"{MEMORY_BYTES // (1024 * 1024)} MB, {CALL_TIMEOUT_S} s per call. The reply is capped "
        f"at about {REPLY_CAP_BYTES // 1000} KB; the full text is saved under .gamachine/"
        "script-runs/ and the reply names the file. On a stop, the reply lists which calls "
        "already ran, so do not redo them blindly.\n"
        "Functions:\n" + listing + "\n"
        "Example: const rows = await Promise.all(names.map(n => tools.<tool>({ name: n })));\n"
        "         return rows.filter(r => r.success).length;"
    )


def browser_lines() -> list:
    """The browser server's read-only tools (its own readOnlyHint marks), as description lines."""
    try:
        from browser_use.server import create_server
        tools = create_server()._tool_manager.list_tools()
    except Exception as exc:  # noqa: BLE001 - the description just goes without them
        logger.warning("[run_script] browser tool list failed: %r", exc)
        return []
    lines = [signature(ToolSpec(t.name, t.description or "", t.parameters or {}))
             for t in tools if read_only_tool(t)]
    return ["Browser panel, when it is on (read-only tools only; click, typing, forms and downloads "
            "stay direct calls):", *lines] if lines else []


CONNECTORS_TOOLS_LINE = (
    "tools.<connector>_<tool>(args) for the gamachineConnectors tools in your tool list "
    "(for example blender_get_scene_info), when a connector is switched on"
)

UNITY_TOOLS_LINE = (
    "tools.<name>(args) for every tool of the Unity MCP server in your tool list (unityMCP), "
    "by its bare name and with the same arguments as a direct call"
)
