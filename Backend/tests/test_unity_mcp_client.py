"""Backend Unity MCP client (closed-loop.md §4, P1 part B).

No Unity and no MCP server: the transport is replaced by a fake session
factory, so these tests pin the client's own contract - one reused session,
bounded calls, readable connection errors, image content forwarded, and the
exported tool set (core + playtest, stable order) refreshed on list_changed.
"""
import asyncio
import contextlib
import json
import threading
import time
import types as pytypes
from unittest import mock

import httpx
import pytest
from mcp import types

# `agentic` first: importing `tools` first hits the existing tools <-> agentic
# import cycle (file_tools -> agentic -> agent_runner -> tool_registry).
import agentic.agent_runner  # noqa: F401,E402
from tools import unity_mcp_tools as umt  # noqa: E402


def _tool(name, group=None):
    data = {"name": name, "description": f"{name} tool",
            "inputSchema": {"type": "object", "properties": {"a": {"type": "string"}}}}
    if group:
        data["_meta"] = {"fastmcp": {"tags": [f"group:{group}"]}}
    return types.Tool.model_validate(data)


class _FakeSession:
    def __init__(self, owner):
        self.owner = owner
        self.initialized = 0

    async def initialize(self):
        self.initialized += 1

    async def list_tools(self):
        return pytypes.SimpleNamespace(tools=list(self.owner.tools))

    async def call_tool(self, name, params, read_timeout_seconds=None):
        return await self.owner.call_impl(name, params)


class _FakeServer:
    def __init__(self, tools=(), call_impl=None, fail_connect=None):
        self.tools = list(tools)
        self.call_impl = call_impl or self._text
        self.fail_connect = fail_connect
        self.sessions = []
        self.handlers = []
        self.closed = 0

    @staticmethod
    async def _text(name, params):
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"{name} ok")])

    def factory(self, message_handler):
        server = self

        @contextlib.asynccontextmanager
        async def _open():
            if server.fail_connect is not None:
                raise server.fail_connect
            session = _FakeSession(server)
            server.sessions.append(session)
            server.handlers.append(message_handler)
            try:
                yield session, ("endpoint",)
            finally:
                server.closed += 1
        return _open()


@pytest.fixture
def server():
    fake = _FakeServer()
    client = umt._UnityMCPClient(session_factory=fake.factory, endpoint_key=lambda: ("endpoint",))
    client.on_tools_changed = umt._refresh_after_change
    with mock.patch.object(umt, "_client", client):
        yield fake, client
        client.close()
    umt._cached_tools, umt._cached_functions = [], {}


# ── session reuse, timeouts, errors ─────────────────────────────────────────

def test_calls_reuse_one_session(server):
    fake, _ = server
    assert umt.call_unity_tool("manage_scene", {})["result"] == "manage_scene ok"
    assert umt.call_unity_tool("read_console", {})["success"] is True
    assert len(fake.sessions) == 1
    assert fake.sessions[0].initialized == 1


def test_a_hanging_call_times_out_instead_of_hanging(server):
    fake, _ = server

    async def _never(name, params):
        await asyncio.sleep(3600)

    fake.call_impl = _never
    started = time.monotonic()
    result = umt.call_unity_tool("manage_scene", {}, timeout=0.3)
    elapsed = time.monotonic() - started
    assert result["success"] is False
    assert "zaman aşımı" in result["error"]
    assert elapsed < 10


def test_connection_failure_in_an_exception_group_reads_as_one_sentence(server):
    fake, _ = server
    fake.fail_connect = ExceptionGroup(
        "unhandled errors in a TaskGroup", [httpx.ConnectError("All connection attempts failed")])
    result = umt.call_unity_tool("manage_scene", {})
    assert result == {"success": False, "error": umt._UNREACHABLE_MSG}


def test_a_failed_call_drops_the_session_and_the_next_call_reconnects(server):
    fake, _ = server
    calls = {"n": 0}

    async def _flaky(name, params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("connection reset")
        return types.CallToolResult(content=[types.TextContent(type="text", text="back")])

    fake.call_impl = _flaky
    first = umt.call_unity_tool("manage_scene", {})
    second = umt.call_unity_tool("manage_scene", {})
    assert first["success"] is False
    assert second == {"success": True, "result": "back"}
    assert len(fake.sessions) == 2, "the failed call must not be retried on the same session"
    assert calls["n"] == 2, "a failed call is reported, never re-sent (it may have reached Unity)"


def test_a_failing_call_does_not_close_the_session_under_a_call_in_flight(server):
    """A write waiting on an approval card must not lose its session because a
    peer call failed; the failed session is dropped once the write is done."""
    fake, client = server
    a_started, b_failed = threading.Event(), threading.Event()
    closed_while_a_ran = []

    async def _impl(name, params):
        if name == "A":
            a_started.set()
            await asyncio.to_thread(b_failed.wait, 5)
            closed_while_a_ran.append(fake.closed)
            return types.CallToolResult(content=[types.TextContent(type="text", text="A done")])
        if name == "B":
            raise httpx.ReadError("connection reset")
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"{name} ok")])

    fake.call_impl = _impl
    result = {}
    worker = threading.Thread(target=lambda: result.update(a=umt.call_unity_tool("A", {}, timeout=10)))
    worker.start()
    try:
        assert a_started.wait(5)
        assert umt.call_unity_tool("B", {})["success"] is False
        # A new call goes to a fresh session while A still runs on the old one.
        assert umt.call_unity_tool("C", {}) == {"success": True, "result": "C ok"}
        assert len(fake.sessions) == 2
        assert fake.closed == 0
    finally:
        b_failed.set()
        worker.join(10)
    assert result["a"] == {"success": True, "result": "A done"}
    assert closed_while_a_ran == [0]
    deadline = time.monotonic() + 5
    while fake.closed < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert fake.closed == 1, "the broken session closes once its last call is done"
    assert umt.call_unity_tool("D", {})["success"] is True
    assert len(fake.sessions) == 2, "the healthy session keeps being reused"


def test_cancellation_propagates_out_of_call_unity_tool():
    class _Cancelled:
        def call_tool(self, *args, **kwargs):
            raise asyncio.CancelledError()

    with mock.patch.object(umt, "_client", _Cancelled()):
        with pytest.raises(asyncio.CancelledError):
            umt.call_unity_tool("play_step", {})


def test_server_restart_is_detected_by_endpoint_change():
    fake = _FakeServer()
    key = {"v": ("url-1",)}
    client = umt._UnityMCPClient(session_factory=fake.factory, endpoint_key=lambda: key["v"])
    try:
        client.call_tool("x", {})
        # The fake always reports "endpoint" as the opened key, so any other
        # current value reads as a restarted server.
        client.call_tool("x", {})
        assert len(fake.sessions) == 2
    finally:
        client.close()


# ── image content ───────────────────────────────────────────────────────────

def test_image_content_becomes_image_base64_with_its_mime():
    result = types.CallToolResult(content=[
        types.TextContent(type="text", text='{"success": true}'),
        types.ImageContent(type="image", data="iVBORw0KGgo=", mimeType="image/png"),
        types.ImageContent(type="image", data="AAAA", mimeType="image/png"),
    ])
    out = umt._result_to_dict(result)
    assert out["image_base64"] == "data:image/png;base64,iVBORw0KGgo="
    assert out["result"] == '{"success": true}'
    assert out["images_omitted"] == 1


class _RecordingAnthropic:
    def __init__(self):
        self.requests = []
        self.messages = pytypes.SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        usage = pytypes.SimpleNamespace(input_tokens=1, output_tokens=1)
        if len(self.requests) > 1:
            return pytypes.SimpleNamespace(content=[pytypes.SimpleNamespace(type="text", text="bitti")], usage=usage)
        return pytypes.SimpleNamespace(content=[pytypes.SimpleNamespace(
            type="tool_use", id="t0", name="manage_camera", input={"action": "screenshot"})], usage=usage)


class _RecordingOpenAI:
    def __init__(self):
        self.requests = []
        self.base_url = None
        self.chat = pytypes.SimpleNamespace(completions=pytypes.SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        usage = pytypes.SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        if len(self.requests) > 1:
            message = pytypes.SimpleNamespace(content="bitti", tool_calls=None)
        else:
            message = pytypes.SimpleNamespace(content=None, tool_calls=[pytypes.SimpleNamespace(
                id="t0", function=pytypes.SimpleNamespace(
                    name="manage_camera", arguments='{"action": "screenshot"}'))])
        return pytypes.SimpleNamespace(choices=[pytypes.SimpleNamespace(message=message)], usage=usage)


class _RecordingGemini:
    def __init__(self):
        self.requests = []
        self.models = pytypes.SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) > 1:
            parts = [pytypes.SimpleNamespace(function_call=None, text="bitti", thought=False)]
        else:
            parts = [pytypes.SimpleNamespace(
                function_call=pytypes.SimpleNamespace(name="manage_camera", args={"action": "screenshot"}),
                text=None, thought=False)]
        return pytypes.SimpleNamespace(
            candidates=[pytypes.SimpleNamespace(content=pytypes.SimpleNamespace(parts=parts))],
            usage_metadata=pytypes.SimpleNamespace(prompt_token_count=1, candidates_token_count=1))


def _run_loop(provider_type, client):
    import agentic.agent_runner as ar

    png = "iVBORw0KGgo="

    async def _image_tool(name, params):
        return types.CallToolResult(content=[
            types.TextContent(type="text", text='{"success": true, "message": "captured"}'),
            types.ImageContent(type="image", data=png, mimeType="image/png"),
        ])

    fake = _FakeServer(call_impl=_image_tool)
    mcp_client = umt._UnityMCPClient(session_factory=fake.factory, endpoint_key=lambda: ("endpoint",))
    tool_fn = umt._make_tool_function("manage_camera")

    def _execute(name, args, workspace, conversation_id):
        return tool_fn(**args)

    async def _no_sleep(_s):
        return None

    runner = ar.AgentRunner(provider_type=provider_type, api_key="k", model_name="m", workspace_path=".")
    patches = [
        mock.patch.object(umt, "_client", mcp_client),
        mock.patch.object(ar, "execute_tool", _execute),
        mock.patch.object(ar, "_all_tool_definitions", lambda: [
            {"name": "manage_camera", "description": "d", "parameters": {"type": "object", "properties": {}}}]),
        mock.patch.object(ar, "get_openai_tool_declarations", lambda: []),
        mock.patch.object(ar, "get_gemini_tool_declarations", lambda: [{"function_declarations": []}]),
        mock.patch.object(ar.asyncio, "sleep", _no_sleep),
        mock.patch.object(ar.anthropic, "AsyncAnthropic", lambda **kw: client),
        mock.patch.object(ar.openai, "AsyncOpenAI", lambda **kw: client),
        mock.patch.object(ar.genai, "Client", lambda **kw: client),
    ]
    for p in patches:
        p.start()
    try:
        asyncio.run(_collect(runner))
    finally:
        for p in reversed(patches):
            p.stop()
        mcp_client.close()
    return png


async def _collect(runner):
    return [e async for e in runner._run_inner("ekrana bak")]


def test_image_reaches_the_anthropic_payload():
    client = _RecordingAnthropic()
    png = _run_loop("anthropic", client)
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    image = tool_result["content"][1]
    assert image["type"] == "image"
    assert image["source"] == {"type": "base64", "media_type": "image/png", "data": png}
    assert png not in tool_result["content"][0]["text"], "base64 must not ride in the text part"


def test_image_reaches_the_openai_payload():
    client = _RecordingOpenAI()
    png = _run_loop("openai", client)
    last = client.requests[1]["messages"][-1]
    assert last["role"] == "user"
    assert last["content"][1]["image_url"]["url"] == f"data:image/png;base64,{png}"


def test_image_reaches_the_gemini_payload():
    import base64

    client = _RecordingGemini()
    png = _run_loop("google", client)
    contents = client.requests[1]["contents"]
    blobs = [p.inline_data for c in contents for p in (getattr(c, "parts", None) or [])
             if getattr(p, "inline_data", None) is not None]
    assert blobs, "no image part in the second Gemini request"
    assert blobs[-1].mime_type == "image/png"
    assert blobs[-1].data == base64.b64decode(png)


# ── exported tool set ───────────────────────────────────────────────────────

def test_only_core_and_playtest_are_exported_in_stable_order(server):
    fake, _ = server
    fake.tools = [_tool("manage_vfx", "vfx"), _tool("read_console", "core"),
                  _tool("manage_tools"), _tool("play_step", "playtest"), _tool("manage_scene", "core")]
    assert umt.load_unity_tools() is True
    assert [t["name"] for t in umt.get_unity_tool_definitions()] == [
        "manage_scene", "play_step", "read_console"]
    assert not umt.is_unity_tool("manage_vfx")


def test_missing_playtest_group_is_fine(server):
    fake, _ = server
    fake.tools = [_tool("read_console", "core")]
    assert umt.load_unity_tools() is True
    assert [t["name"] for t in umt.get_unity_tool_definitions()] == ["read_console"]


def test_tools_list_changed_refreshes_the_cache(server):
    fake, client = server
    fake.tools = [_tool("read_console", "core")]
    assert umt.load_unity_tools() is True
    fake.tools.append(_tool("play_step", "playtest"))

    note = types.ServerNotification(
        types.ToolListChangedNotification(method="notifications/tools/list_changed"))
    client.run(fake.handlers[-1](note), 5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not umt.is_unity_tool("play_step"):
        time.sleep(0.05)
    assert umt.is_unity_tool("play_step")


def test_schema_token_estimate_is_chars_over_four():
    defs = [{"name": "a", "description": "b", "parameters": {}}]
    assert umt.estimate_schema_tokens(defs) == len(json.dumps(defs)) // 4
