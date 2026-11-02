"""Agent connectors MCP server: tool filter, gate before upstream, pass-through, restart, upstream env."""
import json
import os
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from connectors import catalog, mcp_server
from routes import conversation_routes
from connectors.mcp_server import ConnectorsServer, exposed_tools
from spawn_env import CONVERSATION_ENV, READ_ONLY_ENV

ENTRY = catalog.load()["blender"]
ALLOWED = ["blender_execute_blender_code", "blender_get_addon_status", "blender_get_scene_info", "blender_look"]
UPSTREAM = ["get_scene_info", "look", "execute_blender_code", "get_addon_status", "generate_3d", "search_assets",
            "import_asset", "record_trajectory_feedback", "disable_telemetry", "something_new"]

# Speaks MCP over stdio like mcp-for-blender: every upstream tool name, look returns an image, die exits.
FAKE_UPSTREAM = textwrap.dedent('''
    import json, os, sys, anyio
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    NAMES = %r
    LOG = sys.argv[1]
    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(
            name=n, description="upstream " + n,
            input_schema={"type": "object", "properties": {"code": {"type": "string"}}}) for n in NAMES])
    async def call_tool(ctx, params):
        with open(LOG, "a") as f:
            f.write(json.dumps([os.getpid(), params.name, params.arguments]) + "\\n")
        if params.name == "die":
            os._exit(3)
        if params.name == "look":
            return types.CallToolResult(content=[types.ImageContent(type="image", data="iVBORw0K", mime_type="image/png")])
        return types.CallToolResult(content=[types.TextContent(type="text", text="pid %%d" %% os.getpid())])
    async def main():
        server = Server("fake-blender", on_list_tools=list_tools, on_call_tool=call_tool)
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())
    anyio.run(main)
''') % (UPSTREAM + ["die"],)


class Recorder:
    def __init__(self, reply=None):
        self.calls, self.reply = [], reply or {"ok": True}

    async def __call__(self, *args):
        self.calls.append(args)
        return self.reply


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    monkeypatch.setenv(CONVERSATION_ENV, "7")
    monkeypatch.delenv(READ_ONLY_ENV, raising=False)
    script = tmp_path / "fake_upstream.py"
    script.write_text(FAKE_UPSTREAM, encoding="utf-8")
    log = tmp_path / "calls.jsonl"
    params = StdioServerParameters(command=sys.executable, args=[str(script), str(log)],
                                   env={"PYTHONPATH": os.pathsep.join(sys.path)})

    def calls():
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    return params, calls


def run(server: ConnectorsServer, body):
    async def go():
        async with Client(server.lowlevel()) as client:
            return await body(client)
    return anyio.run(go)


def make(params, gate=None, opener=None):
    gate, opener = gate or Recorder(), opener or Recorder({"ok": True, "already_running": True, "version": "5.2.0"})
    return ConnectorsServer({"blender": ENTRY}, {"blender": params}, gate=gate, opener=opener), gate, opener


def text(result):
    return " ".join(c.text for c in result.content if c.type == "text")


def test_exposed_tools_keeps_only_the_catalog_allow_list():
    assert sorted(exposed_tools("blender", UPSTREAM)) == ALLOWED
    assert exposed_tools("blender", ["generate_3d", "disable_telemetry", "unknown"]) == []


def test_banned_tools_never_pass_even_if_a_catalog_entry_allowed_them():
    entry = {**ENTRY, "tools": {"allow": ["look", "generate_3d", "import_asset"]}}
    assert exposed_tools("blender", UPSTREAM, entry) == ["blender_look"]


def test_lists_only_allowed_tools_with_upstream_schemas_plus_open(upstream):
    params, _ = upstream
    server, _, _ = make(params)

    async def body(client):
        return (await client.list_tools()).tools
    tools = run(server, body)
    assert sorted(t.name for t in tools) == sorted(ALLOWED + ["blender_open"])
    scene = next(t for t in tools if t.name == "blender_get_scene_info")
    assert scene.input_schema["properties"] == {"code": {"type": "string"}}
    assert scene.description.startswith("upstream get_scene_info")


@pytest.mark.parametrize("name", ["blender_generate_3d", "blender_search_assets", "blender_import_asset",
                                  "blender_record_trajectory_feedback", "blender_disable_telemetry",
                                  "blender_something_new", "blender_die", "generate_3d", "look"])
def test_unlisted_and_banned_tools_are_never_callable(upstream, name):
    params, calls = upstream
    server, gate, _ = make(params)

    async def body(client):
        await client.list_tools()
        return await client.call_tool(name, {})
    result = run(server, body)
    assert result.is_error and "Unknown tool" in text(result)
    assert gate.calls == [] and calls() == []


def test_allowed_call_asks_the_gate_then_reaches_upstream_unchanged(upstream):
    params, calls = upstream
    server, gate, _ = make(params)

    async def body(client):
        return await client.call_tool("blender_execute_blender_code", {"code": "print(1)"})
    result = run(server, body)
    assert not result.is_error and text(result).startswith("pid ")
    assert gate.calls == [(7, "blender", "execute_blender_code", {"code": "print(1)"})]
    assert [c[1:] for c in calls()] == [["execute_blender_code", {"code": "print(1)"}]]


def test_post_gate_sends_the_arguments_and_waits_longer_for_blender_code(monkeypatch):
    sent = []

    async def fake_post(path, body, timeout):
        sent.append((path, body, timeout))
        return {"ok": True}
    monkeypatch.setattr(mcp_server, "_post", fake_post)
    anyio.run(mcp_server.post_gate, 7, "blender", "execute_blender_code", {"code": "x = 1"})
    anyio.run(mcp_server.post_gate, 7, "blender", "get_scene_info", {})
    assert sent[0][:2] == ("/connectors/gate", {"conversation_id": 7, "connector": "blender",
                                                "tool": "execute_blender_code", "args": {"code": "x = 1"}})
    assert sent[1][1]["args"] == {} and sent[1][2] == mcp_server._GATE_TIMEOUT_S
    # The backend may hold this call for the user's card; card and upstream call fit the 180 s tool timeout.
    assert conversation_routes.BLENDER_CARD_WAIT_S < sent[0][2] < mcp_server._TOOL_BUDGET_S
    assert conversation_routes.BLENDER_CARD_WAIT_S + mcp_server._CALL_TIMEOUT_S <= mcp_server._TOOL_BUDGET_S < 180


@pytest.mark.parametrize("reply", [{"ok": False, "error": "The user pressed Stop in this chat."},
                                   {"ok": False}, {"error": "x"}, "not a dict"])
def test_gate_refusal_never_reaches_upstream(upstream, reply):
    params, calls = upstream
    server, gate, _ = make(params, gate=Recorder(reply))

    async def body(client):
        return await client.call_tool("blender_execute_blender_code", {"code": "import os"})
    result = run(server, body)
    assert result.is_error
    if isinstance(reply, dict) and reply.get("error") and reply.get("ok") is False:
        assert text(result) == reply["error"]
    assert calls() == []


def test_backend_down_is_a_refusal(upstream):
    params, calls = upstream

    async def down(*args):
        raise OSError("refused")
    server, _, _ = make(params, gate=down)

    async def body(client):
        return await client.call_tool("blender_get_scene_info", {})
    result = run(server, body)
    assert result.is_error and "did not answer" in text(result) and calls() == []


def test_image_content_passes_through(upstream):
    params, _ = upstream
    server, _, _ = make(params)

    async def body(client):
        return await client.call_tool("blender_look", {})
    result = run(server, body)
    assert not result.is_error
    assert [(c.type, c.data, c.mime_type) for c in result.content] == [("image", "iVBORw0K", "image/png")]


def test_read_only_and_missing_chat_are_refused_before_the_gate(upstream, monkeypatch):
    params, calls = upstream
    server, gate, opener = make(params)

    async def body(client):
        monkeypatch.setenv(READ_ONLY_ENV, "1")
        read_only = await client.call_tool("blender_get_scene_info", {})
        monkeypatch.delenv(READ_ONLY_ENV)
        monkeypatch.delenv(CONVERSATION_ENV)
        no_chat = await client.call_tool("blender_open", {})
        return read_only, no_chat
    read_only, no_chat = run(server, body)
    assert read_only.is_error and "Read-only" in text(read_only)
    assert no_chat.is_error and "chat" in text(no_chat)
    assert gate.calls == [] and opener.calls == [] and calls() == []


def test_a_dead_upstream_is_restarted_once_for_the_next_call(upstream):
    params, calls = upstream
    server, _, _ = make(params)
    server.entries = {"blender": {**ENTRY, "tools": {"allow": [*ENTRY["tools"]["allow"], "die"]}}}

    async def body(client):
        first = await client.call_tool("blender_get_scene_info", {})
        died = await client.call_tool("blender_die", {})
        after = await client.call_tool("blender_get_scene_info", {})
        return first, died, after
    first, died, after = run(server, body)
    assert died.is_error and "blender_open" in text(died)
    assert not after.is_error and text(after) != text(first)
    pids = [c[0] for c in calls()]
    assert pids[0] == pids[1] != pids[2]


def test_unstartable_upstream_tells_the_agent_to_open(tmp_path, monkeypatch):
    monkeypatch.setenv(CONVERSATION_ENV, "7")
    monkeypatch.setattr(mcp_server, "_START_TIMEOUT_S", 20.0)
    params = StdioServerParameters(command=sys.executable, args=["-c", "import sys; sys.exit(1)"])
    server, _, _ = make(params)

    async def body(client):
        return await client.call_tool("blender_get_scene_info", {})
    result = run(server, body)
    assert result.is_error and "Call blender_open" in text(result)


@pytest.mark.parametrize("reply,expected,error", [
    ({"ok": True, "already_running": True, "version": "5.2.0"}, "already open", False),
    ({"ok": True, "already_running": False, "version": "5.2.0"}, "Started Blender 5.2.0", False),
    ({"ok": False, "error": "Blender was not found."}, "Blender was not found.", True),
])
def test_open_goes_to_the_backend_route_and_reports_its_reply(upstream, reply, expected, error):
    params, calls = upstream
    server, gate, opener = make(params, opener=Recorder(reply))

    async def body(client):
        return await client.call_tool("blender_open", {})
    result = run(server, body)
    assert result.is_error is error and expected in text(result)
    assert opener.calls == [(7, "blender")] and gate.calls == [] and calls() == []


def _install(root: Path, entry_name: str = "mcp-for-blender.exe") -> Path:
    vdir = root / "blender" / "2.2.6"
    exe = vdir / "venv" / "Scripts" / entry_name
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    (vdir / "install.json").write_text(json.dumps({"version": "2.2.6", "entry": str(exe),
                                                   "finished_at": "2026-11-02T00:00:00+00:00"}))
    return exe


def test_upstream_params_use_the_allow_listed_env_plus_the_catalog_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    exe = _install(tmp_path)
    params = mcp_server.upstream_params(ENTRY, tmp_path)
    assert params.command == str(exe) and params.args == []
    assert "OPENAI_API_KEY" not in params.env
    for key, value in ENTRY["env"].items():
        assert params.env[key] == value


def test_upstream_params_refuse_a_missing_or_foreign_entry(tmp_path):
    assert mcp_server.upstream_params(ENTRY, tmp_path) is None
    exe = _install(tmp_path)
    record = tmp_path / "blender" / "2.2.6" / "install.json"
    record.write_text(json.dumps({"version": "2.2.6", "entry": sys.executable, "finished_at": "x"}))
    assert mcp_server.upstream_params(ENTRY, tmp_path) is None
    assert exe.is_file()


def test_from_env_serves_only_named_installed_connectors(tmp_path, monkeypatch):
    _install(tmp_path)
    monkeypatch.setenv("GAMACHINE_CONNECTORS", "blender,unknown")
    monkeypatch.setenv("GAMACHINE_CONNECTORS_ROOT", str(tmp_path))
    assert list(mcp_server.from_env().upstreams) == ["blender"]
    monkeypatch.setenv("GAMACHINE_CONNECTORS", "")
    assert mcp_server.from_env().upstreams == {}


class SlowUpstream:
    """An upstream whose call runs past its read timeout, as the MCP client reports it."""
    cid = "blender"

    def __init__(self):
        self.timeouts = []

    async def responsive(self):
        return True

    async def stop(self):
        pass

    async def call(self, tool, arguments, timeout_s):
        from mcp import MCPError
        from mcp_types import REQUEST_TIMEOUT
        self.timeouts.append(timeout_s)
        raise MCPError(code=REQUEST_TIMEOUT, message="Request 'tools/call' timed out")


def test_the_upstream_call_gets_what_the_card_left_of_the_budget_and_says_when_time_ran_out(upstream, monkeypatch):
    params, _ = upstream
    monkeypatch.setattr(mcp_server, "_TOOL_BUDGET_S", 50.0)

    async def slow_gate(*args):
        await anyio.sleep(0.3)  # the user answering the card
        return {"ok": True}
    server, _, _ = make(params, gate=slow_gate)
    slow = SlowUpstream()
    server.upstreams["blender"], server._started = slow, True

    async def body(client):
        return await client.call_tool("blender_execute_blender_code", {"code": "print(1)"})
    result = run(server, body)
    assert len(slow.timeouts) == 1 and 45 < slow.timeouts[0] <= 49.8
    assert result.is_error and "ran out of time after 49 s" in text(result)
    assert "may or may not have finished" in text(result)


class DeadUpstream:
    """A child that died while the card was open: the restart takes `restart_s` of virtual time."""
    cid = "blender"

    def __init__(self, clock, restart_s):
        self.clock, self.restart_s, self.timeouts = clock, restart_s, []

    async def responsive(self):
        return False

    async def stop(self):
        pass

    async def start(self, task_group):
        self.clock[0] += self.restart_s

    async def call(self, tool, arguments, timeout_s):
        self.timeouts.append(timeout_s)
        self.clock[0] += timeout_s
        return mcp_server._text("done")


@pytest.mark.parametrize("card_s,restart_s,called", [(60.0, 60.0, True), (110.0, 60.0, False)])
def test_an_upstream_restart_counts_against_the_budget(monkeypatch, card_s, restart_s, called):
    clock = [0.0]
    monkeypatch.setattr(mcp_server, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(mcp_server, "read_only_refusal", lambda: None)

    async def gate(*args):
        clock[0] += card_s
        return {"ok": True}
    server = mcp_server.ConnectorsServer({"blender": ENTRY}, {}, gate=gate, conversation_id=1)
    dead = DeadUpstream(clock, restart_s)
    server.upstreams["blender"], server._started = dead, True
    params = SimpleNamespace(name="blender_execute_blender_code", arguments={"code": "print(1)"})
    result = anyio.run(server.call_tool, None, params)
    assert clock[0] <= mcp_server._TOOL_BUDGET_S
    if called:
        assert dead.timeouts == [mcp_server._TOOL_BUDGET_S - card_s - restart_s] and not result.is_error
    else:
        assert dead.timeouts == [] and result.is_error
        assert "ran out of time" in text(result) and "did not run" in text(result)


def test_card_wait_and_upstream_call_fit_the_providers_tool_timeout(monkeypatch):
    from agentic import stdio_toolset
    from connectors import registration
    monkeypatch.setattr(registration, "_server_env", lambda: {"X": "1"})
    limit = min(stdio_toolset.CALL_TIMEOUT_S, registration.codex_thread_entry(None)["tool_timeout_sec"])
    assert conversation_routes.BLENDER_CARD_WAIT_S + mcp_server._CALL_TIMEOUT_S <= mcp_server._TOOL_BUDGET_S < limit
    assert mcp_server._CARD_GATE_TIMEOUT_S + mcp_server._CALL_TIMEOUT_S > mcp_server._TOOL_BUDGET_S  # capped per call
