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


async def execute(code: str, groups: Sequence[ToolGroup], *, timeout_s: Optional[float] = None,
                  limits: Optional[dict] = None, runtime: Optional[list] = None) -> ScriptResult:
    started = time.monotonic()
    wall_s = float(DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s)
    wall_s = max(1.0, min(float(MAX_TIMEOUT_S), wall_s))
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
        result.seconds = time.monotonic() - started
    for record in calls:
        if record.status == "running":
            record.status = "unfinished"
    return result


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
                     timeout: Optional[float] = None) -> tuple[str, bool]:
    """(reply text, is_error) for one run_script call."""
    if not isinstance(code, str) or not code.strip():
        return "run_script needs `code`: the JavaScript body to run.", True
    result = await execute(code, groups, timeout_s=timeout)
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


def tool_description(tool_lines: Sequence[str], extra_lines: Sequence[str] = ()) -> str:
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
        f"Limits: `timeout` seconds (default and max {MAX_TIMEOUT_S}), {MAX_CALLS} tool calls, "
        f"{MEMORY_BYTES // (1024 * 1024)} MB, {CALL_TIMEOUT_S} s per call. The reply is capped "
        f"at about {REPLY_CAP_BYTES // 1000} KB; the full text is saved under .gamachine/"
        "script-runs/ and the reply names the file. On a stop, the reply lists which calls "
        "already ran, so do not redo them blindly.\n"
        "Functions:\n" + listing + "\n"
        "Example: const rows = await Promise.all(names.map(n => tools.<tool>({ name: n })));\n"
        "         return rows.filter(r => r.success).length;"
    )


UNITY_TOOLS_LINE = (
    "tools.<name>(args) for every tool of the Unity MCP server in your tool list (unityMCP), "
    "by its bare name and with the same arguments as a direct call"
)
