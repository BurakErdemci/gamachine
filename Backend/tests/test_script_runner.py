"""run_script's QuickJS runner, end to end through script_tool.execute with fake tool groups.

Runs the real Backend/script_runner/runner.mjs on `node` from PATH, else on the lane's Electron
binary (ELECTRON_RUN_AS_NODE=1). Every runner process a test starts is checked to be gone.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

import script_tool  # noqa: E402
from script_tool import Outcome, ToolSpec  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_ELECTRON = {
    "win32": _ROOT / "Frontend/frontend/node_modules/electron/dist/electron.exe",
    "darwin": _ROOT / "Frontend/frontend/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron",
}.get(sys.platform, _ROOT / "Frontend/frontend/node_modules/electron/dist/electron")


def _runtime_or_skip(monkeypatch):
    if shutil.which("node"):
        monkeypatch.delenv(script_tool.NODE_ENV, raising=False)
    elif _ELECTRON.is_file():
        monkeypatch.setenv(script_tool.NODE_ENV, str(_ELECTRON))
    else:
        pytest.skip("no JavaScript runtime: neither `node` on PATH nor the lane's Electron binary")
    runtime = script_tool.find_runtime()
    if runtime is None:
        pytest.skip("node/Electron found but it would not report its version")
    return runtime


@pytest.fixture
def runtime(monkeypatch):
    return _runtime_or_skip(monkeypatch)


@pytest.fixture
def procs(monkeypatch):
    """Every runner process started during the test; the test ends with all of them dead."""
    started = []
    real = subprocess.Popen

    def spy(*args, **kwargs):
        proc = real(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(script_tool.subprocess, "Popen", spy)
    yield started
    alive = [p for p in started if p.poll() is None]
    for p in alive:
        p.kill()
    assert not alive, "a runner process outlived its run"


class FakeGroup:
    label = "fake"

    def __init__(self, **handlers):
        self.handlers = handlers
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def list_tools(self):
        return [ToolSpec(name, f"{name} tool", {"type": "object", "properties": {}}) for name in self.handlers]

    async def call(self, name, args):
        self.calls.append((name, args))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            return await self.handlers[name](args)
        finally:
            self.in_flight -= 1


async def _echo(args):
    return Outcome.ok({"echo": args})


def run(coro):
    return asyncio.run(coro)


def test_sandbox_has_no_require_fetch_process_or_fs(runtime, procs):
    code = """
      const kinds = [typeof require, typeof fetch, typeof process, typeof setTimeout, typeof std,
                     typeof os, typeof globalThis.__gm_call, typeof globalThis.__gm_log,
                     (function(){}).constructor("return typeof process")()];
      let imported = "loaded";
      try { await import("fs"); } catch (e) { imported = "refused"; }
      return { kinds, imported, tools: typeof tools, callTool: typeof callTool };
    """
    result = run(script_tool.execute(code, [FakeGroup(echo=_echo)], runtime=runtime))
    assert result.status == "ok", result.message
    assert set(result.value["kinds"]) == {"undefined"}
    assert result.value["imported"] == "refused"
    assert result.value["tools"] == "object" and result.value["callTool"] == "function"
    assert procs and all(p.poll() is not None for p in procs)


def test_result_console_and_parsed_tool_values(runtime, procs):
    code = 'const r = await tools.echo({a: 1}); console.log("seen", r); return r.echo.a + 1;'
    result = run(script_tool.execute(code, [FakeGroup(echo=_echo)], runtime=runtime))
    assert result.status == "ok" and result.value == 2 and result.has_value
    assert result.logs == ['seen {"echo":{"a":1}}']
    assert [(c.tool, c.status) for c in result.calls] == [("echo", "ok")]


def test_parallel_inner_calls_overlap(runtime, procs):
    async def slow(args):
        await asyncio.sleep(0.4)
        return Outcome.ok(args["i"])

    group = FakeGroup(slow=slow)
    code = "return (await Promise.all([1,2,3,4,5].map(i => tools.slow({i})))).reduce((a, b) => a + b, 0);"
    started = time.monotonic()
    result = run(script_tool.execute(code, [group], runtime=runtime))
    elapsed = time.monotonic() - started
    assert result.status == "ok" and result.value == 15
    assert group.max_in_flight == 5
    assert elapsed < 0.4 * 5, f"calls ran one after another ({elapsed:.2f} s)"


def test_syntax_error_is_reported(runtime, procs):
    result = run(script_tool.execute("return (", [], runtime=runtime))
    assert result.status == "error" and result.kind == "syntax"
    assert "SyntaxError" in result.message


def test_wall_clock_limit_stops_a_busy_loop_and_kills_the_process(runtime, procs):
    started = time.monotonic()
    result = run(script_tool.execute("while (true) {}", [], timeout_s=1, runtime=runtime))
    assert result.status == "error" and result.kind == "timeout"
    assert time.monotonic() - started < 15
    assert procs and procs[0].poll() is not None


def test_wall_clock_limit_stops_a_script_parked_on_a_tool_call(runtime, procs):
    async def hang(args):
        await asyncio.sleep(60)
        return Outcome.ok(None)

    started = time.monotonic()
    result = run(script_tool.execute("await tools.hang({}); return 1;", [FakeGroup(hang=hang)],
                                     timeout_s=1, runtime=runtime))
    assert result.status == "error" and result.kind == "timeout"
    assert time.monotonic() - started < 15
    assert [c.status for c in result.calls] == ["unfinished"]


def test_memory_limit_ends_the_script(runtime, procs):
    code = "const a = []; for (;;) a.push(new Array(100000).fill(1));"
    result = run(script_tool.execute(code, [], runtime=runtime, limits={"memoryBytes": 8 * 1024 * 1024}))
    assert result.status == "error" and result.kind == "memory"
    assert procs[0].poll() is not None


def test_deep_recursion_is_a_clean_error(runtime, procs):
    result = run(script_tool.execute("const f = n => f(n + 1); f(0);", [], runtime=runtime))
    assert result.status == "error" and result.kind == "memory"
    assert "stack overflow" in result.message


def test_per_call_timeout_ends_the_script_and_kills_the_process(runtime, procs):
    async def hang(args):
        await asyncio.sleep(60)
        return Outcome.ok(None)

    started = time.monotonic()
    result = run(script_tool.execute(
        "await tools.echo({}); await tools.hang({}); return 1;", [FakeGroup(echo=_echo, hang=hang)],
        runtime=runtime, limits={"callTimeoutMs": 500}))
    assert result.status == "error" and result.kind == "call_timeout"
    assert time.monotonic() - started < 15
    assert [(c.tool, c.status) for c in result.calls] == [("echo", "ok"), ("hang", "timeout")]
    assert procs[0].poll() is not None


def test_call_limit_ends_the_script(runtime, procs):
    group = FakeGroup(echo=_echo)
    result = run(script_tool.execute("for (let i = 0; i < 10; i++) await tools.echo({i});",
                                     [group], runtime=runtime, limits={"maxCalls": 3}))
    assert result.status == "error" and result.kind == "call_limit"
    assert len(group.calls) == 3


def test_refusal_with_comment_throws_and_the_script_goes_on(runtime, procs):
    async def guarded(args):
        return Outcome.error("save_file was refused by the user: use Assets/B.txt")

    group = FakeGroup(guarded=guarded, echo=_echo)
    code = """
      let why = null;
      try { await tools.guarded({path: "Assets/A.txt"}); } catch (e) { why = e.message; }
      const after = await tools.echo({path: "Assets/B.txt"});
      return {why, after};
    """
    result = run(script_tool.execute(code, [group], runtime=runtime))
    assert result.status == "ok"
    assert "use Assets/B.txt" in result.value["why"]
    assert result.value["after"] == {"echo": {"path": "Assets/B.txt"}}
    assert [(c.tool, c.status) for c in result.calls] == [("guarded", "error"), ("echo", "ok")]


def test_plain_refusal_ends_the_script(runtime, procs):
    async def refused(args):
        return Outcome.stop("save_file was refused by the user")

    group = FakeGroup(refused=refused, echo=_echo)
    code = "try { await tools.refused({}); } catch (e) {} await tools.echo({}); return 1;"
    result = run(script_tool.execute(code, [group], runtime=runtime))
    assert result.status == "error" and result.kind == "refused"
    assert [name for name, _ in group.calls] == ["refused"]
    assert procs[0].poll() is not None


def test_cancel_kills_the_runner(runtime, procs):
    gate = asyncio.Event()

    async def hang(args):
        gate.set()
        await asyncio.sleep(60)
        return Outcome.ok(None)

    async def scenario():
        task = asyncio.create_task(script_tool.execute("await tools.hang({});", [FakeGroup(hang=hang)],
                                                       runtime=runtime))
        await asyncio.wait_for(gate.wait(), 15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert procs and procs[0].poll() is not None


def test_error_reply_lists_completed_calls_and_the_failing_one(runtime, procs, tmp_path):
    async def step(args):
        if args["i"] == 4:
            return Outcome.error("Unity says: object not found")
        return Outcome.ok({"done": args["i"]})

    code = "for (let i = 1; i <= 5; i++) await tools.step({i}); return 'all';"
    result = run(script_tool.execute(code, [FakeGroup(step=step)], runtime=runtime))
    reply = script_tool.format_reply(result, str(tmp_path))
    assert result.kind == "thrown"
    assert "3 of 4 tool call(s) completed" in reply
    assert "1-3 step ok (3)" in reply
    assert "4 step error (Unity says: object not found)" in reply


def test_long_output_is_capped_and_saved_in_full(runtime, procs, tmp_path):
    code = 'for (let i = 0; i < 300; i++) console.log("line " + i); return "x".repeat(20000);'
    result = run(script_tool.execute(code, [], runtime=runtime))
    reply = script_tool.format_reply(result, str(tmp_path))
    assert len(reply.encode("utf-8")) <= script_tool.REPLY_CAP_BYTES
    saved = list((tmp_path / ".gamachine" / "script-runs").glob("run-*.txt"))
    assert len(saved) == 1 and str(saved[0]) in reply
    full = saved[0].read_text(encoding="utf-8")
    assert "x" * 20000 in full and "line 299" in full


def test_unavailable_runtime_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(script_tool, "find_runtime", lambda: None)
    result = run(script_tool.execute("return 1", []))
    assert result.status == "error" and result.kind == "unavailable"
    assert "node" in result.message
