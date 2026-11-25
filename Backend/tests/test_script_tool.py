"""run_script relays: every inner call must pass the gate a direct call by the same agent passes.

The gates are observed where they live: approval_bridge.request_approval as file_tools/bash_tool
call it, the Unity MCP server (a fake HTTP server on an ephemeral loopback port, reached through
the real codex_unitymcp_bridge code) and ClaudeSDKSession._can_use_tool.
"""
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))
sys.path.insert(0, os.path.dirname(__file__))

import script_tool  # noqa: E402
from agentic import approval_mode  # noqa: E402
from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS  # noqa: E402
from providers import codex_unitymcp_bridge  # noqa: E402
from providers.unity_script_tools import DISALLOWED_UNITY_TOOLS, UNITY_SCRIPT_WRITE_TOOLS  # noqa: E402
from test_script_runner import procs, runtime  # noqa: E402,F401  (fixtures)
from unity_ai_mcp import script_server  # noqa: E402
from unity_ai_mcp.tools import bash_tool, file_tools, script_tools  # noqa: E402

_REAL_BROWSER_ENTRY = script_tools.browser_entry


def run(coro):
    return asyncio.run(coro)


# Loopback ports of the fakes a test started; every other HTTP target fails the test.
_ALLOWED_PORTS: set = set()


@pytest.fixture(autouse=True)
def port_guard(monkeypatch):
    """No HTTP call of this process may leave for an unstubbed target. localhost:8000 is the owner's
    live app, and a turn-2 fixture once reached it through an unstubbed group. Covers httpx (backend,
    mail and browser-settings calls) and http.client (the Unity MCP bridge); the spawned servers get
    the fakes' URLs in their entry env. A blocked call raises and is recorded, and the test fails at
    teardown even if product code swallowed the error."""
    import http.client
    import socket
    import httpx
    blocked = []
    # Ports this process listens on itself (asyncio's socketpair on Windows connects to one).
    listening: set = set()

    def check(host, port):
        if host != "127.0.0.1" or (port not in _ALLOWED_PORTS and port not in listening):
            blocked.append(f"{host}:{port}")
            raise OSError(f"test port guard: a connection to {host}:{port} is not a fake of this test")

    def check_address(address):
        if isinstance(address, tuple) and len(address) >= 2:
            check(address[0], address[1])

    real_listen, real_sock_connect = socket.socket.listen, socket.socket.connect
    real_connect_ex, real_create = socket.socket.connect_ex, socket.create_connection

    def listen(self, *args):
        value = real_listen(self, *args)
        try:
            listening.add(self.getsockname()[1])
        except OSError:
            pass
        return value

    def sock_connect(self, address):
        check_address(address)
        return real_sock_connect(self, address)

    def connect_ex(self, address):
        check_address(address)
        return real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        check_address(address)
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "listen", listen)
    monkeypatch.setattr(socket.socket, "connect", sock_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)

    real_async_send, real_send = httpx.AsyncClient.send, httpx.Client.send
    real_connect = http.client.HTTPConnection.connect

    async def async_send(self, request, *args, **kwargs):
        check(request.url.host, request.url.port)
        return await real_async_send(self, request, *args, **kwargs)

    def send(self, request, *args, **kwargs):
        check(request.url.host, request.url.port)
        return real_send(self, request, *args, **kwargs)

    def connect(self):
        check(self.host, self.port)
        return real_connect(self)

    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", connect)
    yield blocked
    _ALLOWED_PORTS.clear()
    if blocked:
        pytest.fail(f"HTTP left for unstubbed targets: {sorted(set(blocked))}")


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    approval_mode._reset_for_tests()
    script_tool._BRIDGES.clear()
    # The real token file must never be read by a test.
    monkeypatch.setattr(codex_unitymcp_bridge, "_read_shared_secret", lambda: "file-token")
    yield
    script_tool._BRIDGES.clear()
    approval_mode._reset_for_tests()


def test_port_guard_blocks_the_default_backend_url(port_guard):
    import httpx

    async def probe():
        async with httpx.AsyncClient(timeout=1.0) as client:
            await client.get("http://localhost:8000/browser/settings")

    with pytest.raises(OSError, match="test port guard"):
        run(probe())
    assert port_guard == ["localhost:8000"]
    port_guard.clear()  # the block itself is what this test expects


@pytest.mark.parametrize("connect", ["create_connection", "socket_connect"])
def test_port_guard_blocks_raw_sockets_to_the_live_unity_mcp_port(port_guard, connect):
    import socket
    with pytest.raises(OSError, match="test port guard"):
        if connect == "create_connection":
            socket.create_connection(("127.0.0.1", 8080), timeout=1)
        else:
            with socket.socket() as sock:
                sock.connect(("127.0.0.1", 8080))
    assert port_guard == ["127.0.0.1:8080"]
    port_guard.clear()


# ── a fake Unity MCP server ──────────────────────────────────────────────────

UNITY_TOOLS = [
    {"name": "find_gameobjects", "description": "Finds objects. More text.",
     "inputSchema": {"type": "object", "properties": {"search_term": {"type": "string"}},
                     "required": ["search_term"]}},
    {"name": "read_console", "description": "Reads the console.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}}}},
    {"name": "manage_gameobject", "description": "Changes objects.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}}}},
    {"name": "batch_execute", "description": "Runs several commands.",
     "inputSchema": {"type": "object", "properties": {"commands": {"type": "array"}}}},
    *({"name": name, "description": "Writes C#.", "inputSchema": {"type": "object"}}
      for name in UNITY_SCRIPT_WRITE_TOOLS),
]


class FakeUnity:
    def __init__(self):
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                headers = {k: v for k, v in self.headers.items()}
                if "id" not in body:
                    self.send_response(202)
                    self.end_headers()
                    return
                method = body["method"]
                if method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {},
                              "serverInfo": {"name": "fake", "version": "1"}}
                elif method == "tools/list":
                    result = {"tools": UNITY_TOOLS}
                else:
                    name, args = body["params"]["name"], body["params"]["arguments"]
                    outer.calls.append((name, args, headers))
                    if args.get("refuse"):
                        result = {"isError": True, "content": [{"type": "text", "text":
                                  f"'{name}' onay gerektiriyor ve onaylanmadı: Kullanıcı onayı verilmedi."}]}
                    else:
                        result = {"content": [{"type": "text", "text": json.dumps({"success": True, "echo": args})}]}
                data = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if method == "initialize":
                    self.send_header("Mcp-Session-Id", "fake-session")
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"
        _ALLOWED_PORTS.add(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def unity():
    fake = FakeUnity()
    yield fake
    fake.close()


# ── unityai (Codex, agy, OpenCode, Cursor, Kimi, Copilot) ────────────────────

@pytest.fixture
def unityai(monkeypatch, tmp_path):
    """The real unityai server on tmp_path; the approval bridge is replaced by a recording gate."""
    from unity_ai_mcp import server
    seen = []
    answers = []

    async def gate(tool_name, params, workspace_path):
        seen.append((tool_name, dict(params), workspace_path))
        return answers.pop(0) if answers else {"approved": True}

    monkeypatch.setattr(file_tools, "request_approval", gate)
    monkeypatch.setattr(bash_tool, "request_approval", gate)
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", "42")

    async def no_unity():
        return None

    monkeypatch.setattr(script_tools, "_unity_mcp_url", no_unity)
    monkeypatch.setattr(script_tools, "browser_entry", no_unity)
    monkeypatch.setattr(script_tools, "connectors_entry", no_unity)
    monkeypatch.setattr(server, "_workspace_path", "")
    mcp = server.create_server(str(tmp_path))
    return SimpleNamespace(mcp=mcp, seen=seen, answers=answers, ws=tmp_path)


def _text(result):
    return script_tool.content_text(result.content)


def test_unityai_lists_run_script_with_signatures_from_the_real_tools(unityai):
    tools = {t.name: t for t in run(unityai.mcp.list_tools())}
    assert "run_script" in tools
    desc = tools["run_script"].description
    assert "tools.read_file({path: string, start_line?: integer, max_lines?: integer})" in desc
    for name in ("save_file", "delete_file", "list_directory", "bash"):
        assert f"tools.{name}(" in desc
    # Chat mail is relayed; image and harness tools are not; the tool is not Unity-only.
    assert "tools.send_chat_message(" in desc and "tools.list_chats(" in desc
    assert "tools.show_image(" not in desc and "tools.harness_search(" not in desc
    assert "tools.browser_navigate(" in desc and "gamachineConnectors" in desc
    assert "tools.browser_click(" not in desc and "computer_" not in desc
    assert "Unity MCP server" in desc and "direct calls first" in desc
    assert "Do not use it for one or two calls" in desc


def test_description_follows_whatever_tools_are_registered():
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer(name="probe")

    @mcp.tool(name="zz_probe_tool")
    async def probe(count: int, label: str = "x") -> str:
        """Probe tool for the description."""
        return "ok"

    script_tools.register_script_tool(mcp, lambda: ".", ["zz_probe_tool"])
    desc = next(t for t in run(mcp.list_tools()) if t.name == "run_script").description
    assert "tools.zz_probe_tool({count: integer, label?: string}) — Probe tool for the description" in desc


def test_unityai_inner_write_reaches_the_same_approval_gate_as_a_direct_call(unityai, runtime, procs):
    direct = _text(run(unityai.mcp.call_tool("save_file", {"path": "a.txt", "content": "one"})))
    assert direct.startswith("✅")
    reply = _text(run(unityai.mcp.call_tool("run_script", {
        "code": 'return await tools.save_file({path: "b.txt", content: "two"});'})))
    assert "run_script finished" in reply
    assert [(t, p["path"], p["content"]) for t, p, _ in unityai.seen] == [
        ("write_file", "a.txt", "one"), ("write_file", "b.txt", "two")]
    assert unityai.seen[0][2] == unityai.seen[1][2] == str(unityai.ws)
    assert (unityai.ws / "b.txt").read_text(encoding="utf-8") == "two"


def test_unityai_inner_shell_command_reaches_the_same_gate(unityai, runtime, procs):
    unityai.answers.extend([{"approved": False}, {"approved": False}])
    direct = _text(run(unityai.mcp.call_tool("bash", {"command": "mkdir made_directly"})))
    reply = _text(run(unityai.mcp.call_tool("run_script", {
        "code": 'await tools.bash({command: "mkdir made_by_script"}); return 1;'})))
    assert direct.startswith("❌")
    assert [(t, p) for t, p, _ in unityai.seen] == [
        ("bash", {"command": "mkdir made_directly"}), ("bash", {"command": "mkdir made_by_script"})]
    assert "run_script stopped: a tool call was refused" in reply
    assert not (unityai.ws / "made_by_script").exists()


def test_unityai_plain_refusal_ends_the_script(unityai, runtime, procs):
    unityai.answers.append({"approved": False})
    reply = _text(run(unityai.mcp.call_tool("run_script", {"code": """
        try { await tools.save_file({path: "a.txt", content: "1"}); } catch (e) {}
        await tools.save_file({path: "b.txt", content: "2"});
        return 1;"""})))
    assert "a tool call was refused" in reply and "1 save_file refused" in reply
    assert [p["path"] for _, p, _ in unityai.seen] == ["a.txt"]
    assert not (unityai.ws / "b.txt").exists()


def test_unityai_refusal_with_comment_throws_and_the_script_continues(unityai, runtime, procs):
    unityai.answers.append({"approved": False, "comment": "write b.txt instead"})
    reply = _text(run(unityai.mcp.call_tool("run_script", {"code": """
        let why = "";
        try { await tools.save_file({path: "a.txt", content: "1"}); } catch (e) { why = e.message; }
        await tools.save_file({path: "b.txt", content: "2"});
        return why;"""})))
    assert "run_script finished" in reply and "write b.txt instead" in reply
    assert [p["path"] for _, p, _ in unityai.seen] == ["a.txt", "b.txt"]
    assert not (unityai.ws / "a.txt").exists() and (unityai.ws / "b.txt").exists()


def test_unityai_unity_file_rule_refuses_inside_a_script_like_a_direct_call(unityai, runtime, procs):
    (unityai.ws / "Assets").mkdir()
    (unityai.ws / "ProjectSettings").mkdir()
    direct = _text(run(unityai.mcp.call_tool("save_file", {"path": "Assets/a.cs.meta", "content": "x"})))
    reply = _text(run(unityai.mcp.call_tool("run_script", {"code": """
        try { await tools.save_file({path: "Assets/b.cs.meta", content: "x"}); return "written"; }
        catch (e) { return "refused: " + e.message; }"""})))
    assert direct.startswith("❌")
    assert "refused: " in reply and "1 save_file error" in reply
    assert unityai.seen == []  # the rule refused before any card, in both paths
    assert not (unityai.ws / "Assets" / "b.cs.meta").exists()


def test_unityai_relays_unity_calls_with_this_chats_header(unityai, unity, runtime, procs, monkeypatch):
    async def fake_url():
        return unity.url

    monkeypatch.setattr(script_tools, "_unity_mcp_url", fake_url)
    reply = _text(run(unityai.mcp.call_tool("run_script", {"code": """
        const rows = await Promise.all(["A", "B"].map(n => tools.find_gameobjects({search_term: n})));
        return rows.map(r => r.echo.search_term).join(",");"""})))
    assert "Result:\nA,B" in reply
    assert len(unity.calls) == 2
    for name, args, headers in unity.calls:
        assert name == "find_gameobjects"
        assert headers.get("X-Gamachine-Conversation") == "42"
        assert headers.get("X-API-Key") == "file-token"


def test_unity_server_refusal_ends_the_script(unityai, unity, runtime, procs, monkeypatch):
    async def fake_url():
        return unity.url

    monkeypatch.setattr(script_tools, "_unity_mcp_url", fake_url)
    reply = _text(run(unityai.mcp.call_tool("run_script", {"code": """
        await tools.manage_gameobject({action: "create"});
        try { await tools.manage_gameobject({action: "delete", refuse: true}); } catch (e) {}
        await tools.manage_gameobject({action: "create"});"""})))
    assert "a tool call was refused" in reply
    assert "1 of 2 tool call(s) completed" in reply
    assert [a.get("action") for _, a, _ in unity.calls] == ["create", "delete"]


def test_unity_tools_absent_is_a_note_not_a_failure(unityai, runtime, procs):
    reply = _text(run(unityai.mcp.call_tool("run_script", {"code": "return 1 + 1;"})))
    assert "run_script finished" in reply and "Result:\n2" in reply
    assert "Unity MCP is not running" in reply


# ── Claude ───────────────────────────────────────────────────────────────────

@pytest.fixture
def claude(monkeypatch, tmp_path, unity):
    from providers.claude_sdk_session import ClaudeSDKSession
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    session = ClaudeSDKSession(conversation_id=7, cwd=str(tmp_path),
                               disallowed_tools=list(DISALLOWED_UNITY_TOOLS))
    session._out_q = asyncio.Queue()
    session.approval_timeout = 5
    gated = []
    real_gate = session._can_use_tool

    async def spy(tool_name, input_data, context):
        verdict = await real_gate(tool_name, input_data, context)
        gated.append((tool_name, type(verdict).__name__))
        return verdict

    monkeypatch.setattr(session, "_can_use_tool", spy)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda host="localhost": unity.url)
    monkeypatch.setattr(unity_mcp_manager, "api_headers", lambda: {"X-API-Key": "manager-token"})
    return SimpleNamespace(session=session, gated=gated, unity=unity, ws=tmp_path)


def _claude_run(claude, code):
    text, is_error = run(script_server.run_for_session(claude.session, {"code": code}))
    return text, is_error


def test_claude_inner_unity_call_passes_can_use_tool_and_carries_the_header(claude, runtime, procs, monkeypatch):
    monkeypatch.setattr(approval_mode, "current_mode", lambda: "auto")
    text, is_error = _claude_run(claude, """
        const r = await tools.find_gameobjects({search_term: "Player"});
        await tools.manage_gameobject({action: "create"});
        return r.echo.search_term;""")
    assert not is_error, text
    assert claude.gated == [("mcp__unityMCP__find_gameobjects", "PermissionResultAllow"),
                            ("mcp__unityMCP__manage_gameobject", "PermissionResultAllow")]
    assert [c[0] for c in claude.unity.calls] == ["find_gameobjects", "manage_gameobject"]
    for _, _, headers in claude.unity.calls:
        assert headers.get("X-Gamachine-Conversation") == "7"
        assert headers.get("X-API-Key") == "manager-token"


@pytest.mark.parametrize("name", UNITY_SCRIPT_WRITE_TOOLS)
def test_claude_script_cannot_reach_a_script_writing_unity_tool(claude, runtime, procs, monkeypatch, name):
    monkeypatch.setattr(approval_mode, "current_mode", lambda: "auto")
    text, _ = _claude_run(claude, f"""
        const out = [];
        try {{ await tools.{name}({{path: "Assets/A.cs"}}); out.push("called"); }} catch (e) {{ out.push(e.message); }}
        try {{ await callTool("{name}", {{path: "Assets/A.cs"}}); out.push("called"); }} catch (e) {{ out.push(e.message); }}
        return out;""")
    assert "called" not in text and f'no tool named \\"{name}\\"' in text
    assert claude.unity.calls == [] and claude.gated == []


def test_claude_batch_carrying_a_script_write_is_refused_by_can_use_tool(claude, runtime, procs, monkeypatch):
    monkeypatch.setattr(approval_mode, "current_mode", lambda: "auto")
    text, is_error = _claude_run(claude, """
        try {
          await tools.batch_execute({commands: [{tool: "manage_script", params: {action: "create"}}]});
          return "ran";
        } catch (e) { return e.message; }""")
    assert not is_error and "batch_execute refused" in text
    assert claude.gated == [("mcp__unityMCP__batch_execute", "PermissionResultDeny")]
    assert claude.unity.calls == []


def test_claude_card_refused_by_the_user_ends_the_script(claude, runtime, procs, monkeypatch):
    monkeypatch.setattr(approval_mode, "needs_card",
                        lambda action, mode=None: SimpleNamespace(card=True, reason="test", detail=""))

    async def scenario():
        task = asyncio.create_task(script_server.run_for_session(claude.session, {"code": """
            try { await tools.manage_gameobject({action: "delete"}); } catch (e) {}
            await tools.manage_gameobject({action: "create"});"""}))
        event = await asyncio.wait_for(claude.session._out_q.get(), 20)
        assert event["type"] == "command_approval_needed"
        assert event["tool"] == "mcp__unityMCP__manage_gameobject"
        APPROVAL_RESULTS[event["gate_id"]] = False
        APPROVAL_GATES[event["gate_id"]].set()
        return await asyncio.wait_for(task, 30)

    text, is_error = run(scenario())
    assert is_error and "a tool call was refused" in text
    assert claude.unity.calls == []
    assert claude.gated == [("mcp__unityMCP__manage_gameobject", "PermissionResultDeny")]


def test_claude_read_only_session_gives_each_inner_call_the_direct_verdict(claude, runtime, procs, monkeypatch):
    monkeypatch.setattr(approval_mode, "current_mode", lambda: "auto")
    claude.session.read_only = True
    probes = [("read_console", {"action": "get"}), ("find_gameobjects", {"search_term": "A"}),
              ("manage_gameobject", {"action": "create"})]
    direct = [type(run(claude.session._can_use_tool("mcp__unityMCP__" + n, dict(a), None))).__name__
              for n, a in probes]
    assert direct == ["PermissionResultAllow", "PermissionResultDeny", "PermissionResultDeny"]
    claude.gated.clear()
    text, _ = _claude_run(claude, """
        const out = [];
        for (const [n, a] of %s) {
          try { await callTool(n, a); out.push(n + ":ran"); } catch (e) { out.push(n + ":refused"); }
        }
        return out;""" % json.dumps([[n, a] for n, a in probes]))
    assert [verdict for _, verdict in claude.gated] == direct
    assert "read_console:ran" in text and "manage_gameobject:refused" in text
    assert [c[0] for c in claude.unity.calls] == ["read_console"]


def test_claude_allows_run_script_itself_in_every_session(tmp_path):
    from providers.claude_sdk_session import ClaudeSDKSession
    for read_only in (False, True):
        session = ClaudeSDKSession(conversation_id=8, cwd=str(tmp_path), read_only=read_only)
        session._out_q = asyncio.Queue()
        verdict = run(session._can_use_tool(script_server.CLAUDE_TOOL_NAME, {"code": "1"}, None))
        assert type(verdict).__name__ == "PermissionResultAllow"


def test_claude_session_start_mounts_the_server_and_the_guide_line(monkeypatch, tmp_path):
    import agent_guide
    import claude_agent_sdk
    from providers import claude_sdk_session as sdk
    options = []

    class Client:
        def __init__(self, *, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    def make_options(**kwargs):
        options.append(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)
    monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions", make_options)
    monkeypatch.setattr(sdk, "claude_ikilisini_coz", lambda: None)

    async def idle(self):
        return None

    monkeypatch.setattr(sdk.ClaudeSDKSession, "_reader_loop", idle)
    guide = agent_guide.compose("en", project_open=True, unity_state="connected")
    servers = {"gamachineHarness": {"type": "stdio", "command": "x", "args": []}}

    async def scenario():
        session = sdk.ClaudeSDKSession(9, cwd=str(tmp_path), agent_guide=guide, mcp_servers=servers)
        try:
            await session.start()
        finally:
            await session.close()
        return session

    session = run(scenario())
    mounted = options[0]["mcp_servers"]
    assert mounted[script_server.SERVER_NAME]["type"] == "sdk"
    assert set(mounted) == {"gamachineHarness", script_server.SERVER_NAME}
    assert session.mcp_servers == servers  # the rebuild comparison never sees the in-process server
    assert agent_guide.SCRIPT_TOOL_LINE_EN in options[0]["system_prompt"]["append"]
    assert options[0]["system_prompt"]["append"].count("[/GAMACHINE AGENT GUIDE]") == 1


def test_claude_description_is_general_and_names_the_banned_writers():
    desc = script_server.description()
    assert "Unity-only" not in desc and "Gamachine tools" in desc
    for name in UNITY_SCRIPT_WRITE_TOOLS:
        assert name in desc


def test_reply_cap_also_cuts_a_long_call_list(tmp_path):
    calls = [script_tool.CallRecord(i, f"tool_{i % 7}", "error", "reason " * 30) for i in range(1, 120)]
    result = script_tool.ScriptResult("error", "thrown", "Error: boom", calls=calls, seconds=1.0)
    reply = script_tool.format_reply(result, str(tmp_path))
    assert len(reply.encode("utf-8")) <= script_tool.REPLY_CAP_BYTES
    assert "full list in the file below" in reply
    saved = next((tmp_path / ".gamachine" / "script-runs").glob("run-*.txt")).read_text(encoding="utf-8")
    assert "119 tool_0 error" in saved and saved.count("reason") >= 119 * 30


# ── turn 2: browser (read-only), chat mail, connectors, per-CLI time limits ───

class FakeBackend:
    """The backend routes the browser, mail and connectors servers are gated by, on a loopback port."""

    def __init__(self, *, browser_on=True, connectors=()):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append(("GET", self.path, None, dict(self.headers.items())))
                if self.path.startswith("/browser/settings"):
                    self._reply({"enabled": browser_on})
                elif self.path.startswith("/connectors"):
                    self._reply({"connectors": list(connectors)})
                elif self.path.startswith("/mailbox/chats"):
                    self._reply({"text": "#3 Other chat"})
                else:
                    self._reply({})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                outer.requests.append(("POST", self.path, body, dict(self.headers.items())))
                if self.path == "/browser/command":
                    self._reply({"ok": True, "text": f"page for {body.get('tool')}"})
                elif self.path == "/mailbox/send":
                    self._reply({"message": "sent"})
                else:
                    self._reply({})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        _ALLOWED_PORTS.add(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def paths(self, method=None):
        return [p for m, p, _, _ in self.requests if method in (None, m)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def backend():
    fake = FakeBackend()
    yield fake
    fake.close()


@pytest.fixture(autouse=True)
def fresh_stdio_lists():
    script_tool._STDIO_LISTS.clear()
    yield
    script_tool._STDIO_LISTS.clear()


def _child_env(backend_url, chat=7):
    # LOCAL_APP_TOKEN in the entry: a spawned server must never read the real token file.
    return {"GAMACHINE_CONVERSATION_ID": str(chat), "UNITYAI_URL": backend_url, "LOCAL_APP_TOKEN": "test-token"}


def _entry(argv, backend_url, chat=7):
    return {"type": "stdio", "command": argv[0], "args": argv[1:], "env": _child_env(backend_url, chat)}


def _browser_entry(backend_url, chat=7):
    from browser_use import registration
    return _entry(registration.server_argv(), backend_url, chat)


def _mail_entry(backend_url, chat=7):
    from agentic import mailbox
    return _entry([mailbox.claude_server_entry(chat)["command"], *mailbox.claude_server_entry(chat)["args"]],
                  backend_url, chat)


def test_browser_server_marks_only_page_reading_tools_read_only():
    from browser_use.server import create_server
    marked = sorted(t.name for t in create_server()._tool_manager.list_tools() if script_tool.read_only_tool(t))
    assert marked == ["browser_navigate", "browser_search", "browser_snapshot", "browser_tabs"]
    lines = "\n".join(script_tool.browser_lines())
    assert "tools.browser_snapshot(" in lines and "tools.browser_click(" not in lines


def test_unityai_browser_read_only_calls_reach_the_backend_gate(unityai, backend, runtime, procs, monkeypatch):
    from unity_ai_mcp import approval_bridge
    monkeypatch.setattr(approval_bridge, "BACKEND_URL", backend.url)
    monkeypatch.setenv("LOCAL_APP_TOKEN", "test-token")
    monkeypatch.setattr(script_tools, "browser_entry", _REAL_BROWSER_ENTRY)
    monkeypatch.setattr(script_tools, "_chat_env", lambda: _child_env(backend.url, 42))
    reply = _text(run(unityai.mcp.call_tool("run_script", {"code": """
        const page = await tools.browser_navigate({url: "https://example.com"});
        let click = "";
        try { await tools.browser_click({snapshot_id: "s", ref: 1}); } catch (e) { click = e.message; }
        return {page, listed: Object.keys(tools).filter(n => n.startsWith("browser_")), click};"""})))
    assert "run_script finished" in reply, reply
    commands = [(b["tool"], b["conversation_id"]) for m, p, b, _ in backend.requests if p == "/browser/command"]
    assert commands == [("browser_navigate", 42)]
    value = json.loads(reply.split("Result:\n", 1)[1].split("\nConsole:", 1)[0].split("\nFull output", 1)[0])
    assert value["click"].startswith('no tool named "browser_click"')
    assert sorted(value["listed"]) == ["browser_navigate", "browser_search", "browser_snapshot", "browser_tabs"]


def test_unityai_mail_inner_call_reaches_the_backend_like_a_direct_call(unityai, backend, runtime, procs,
                                                                        monkeypatch):
    from unity_ai_mcp.tools import mailbox_tools
    monkeypatch.setattr(mailbox_tools, "BACKEND_URL", backend.url)
    monkeypatch.setenv("LOCAL_APP_TOKEN", "test-token")
    direct = _text(run(unityai.mcp.call_tool("list_chats", {})))
    reply = _text(run(unityai.mcp.call_tool("run_script", {"code": """
        const chats = await tools.list_chats({});
        const sent = await tools.send_chat_message({to_chat_id: 3, message: "hi"});
        return {chats, sent};"""})))
    assert "#3 Other chat" in direct and "#3 Other chat" in reply
    gets = [r for r in backend.requests if r[1].startswith("/mailbox/chats")]
    assert len(gets) == 2 and gets[0][1] == gets[1][1]  # same query, same chat
    sends = [r[2] for r in backend.requests if r[1] == "/mailbox/send"]
    assert sends == [{"to": 3, "body": "hi", "conversation_id": 42}]


def test_unityai_connectors_entry_is_the_registration_argv_with_the_usable_ids(backend, monkeypatch):
    from connectors import registration
    from unity_ai_mcp import approval_bridge
    fake = FakeBackend(connectors=[
        {"id": "blender", "enabled": True, "state": "ready"},
        {"id": "krita", "enabled": False, "state": "ready"},
        {"id": "godot", "enabled": True, "state": "not_installed"},
    ])
    try:
        monkeypatch.setattr(approval_bridge, "BACKEND_URL", fake.url)
        monkeypatch.setattr(script_tools, "_CONNECTOR_IDS", None)
        monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", "42")
        entry = run(script_tools.connectors_entry())
        assert [entry["command"], *entry["args"]] == registration.server_argv()
        assert entry["env"][registration.IDS_ENV] == "blender"
        assert entry["env"]["GAMACHINE_CONVERSATION_ID"] == "42"
        run(script_tools.connectors_entry())
        assert fake.paths("GET").count("/connectors") == 1  # read once per process, like the CLI's server
    finally:
        fake.close()


def test_read_only_unityai_turn_gets_no_browser_or_connectors(unityai, monkeypatch):
    monkeypatch.setenv("GAMACHINE_READ_ONLY", "1")
    labels = [g.label for g in script_tools.default_groups(unityai.mcp, ["read_file"])]
    assert labels == ["unityai", "Unity MCP"]


def test_connector_refusal_marks_match_the_backend_texts():
    from connectors.mcp_server import USER_REFUSAL_MARKS
    from routes import conversation_routes as routes
    texts = (routes.BLENDER_DECLINED_REASON, routes.BLENDER_UNANSWERED_REASON, routes.CONNECTOR_HALTED_REASON)
    assert all(text.startswith(mark) for text, mark in zip(texts, USER_REFUSAL_MARKS))


FAKE_CONNECTORS = r'''
import json, os, sys
sys.path.insert(0, os.environ["APP_DIR"])
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
mcp = MCPServer(name="gamachineConnectors")
def log(name, args):
    with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps([name, args, os.environ.get("GAMACHINE_CONVERSATION_ID")]) + "\n")
@mcp.tool(name="blender_get_scene_info")
async def info(detail: int = 0) -> str:
    log("blender_get_scene_info", {"detail": detail})
    return json.dumps({"objects": ["Cube", "Camera"]})
@mcp.tool(name="blender_execute_blender_code")
async def code(code: str) -> CallToolResult:
    log("blender_execute_blender_code", {"code": code})
    return CallToolResult(content=[TextContent(type="text", text="The user declined this Blender code, so it did not run.")], is_error=True)
mcp.run(transport="stdio")
'''


@pytest.fixture
def claude_servers(claude, backend, tmp_path):
    """A Claude session given mail, browser, connectors and computer-use entries, as agent_runner would."""
    server_file = tmp_path / "fake_connectors.py"
    server_file.write_text(FAKE_CONNECTORS, encoding="utf-8")
    log = tmp_path / "connector_calls.jsonl"
    connectors_env = {**_child_env(backend.url), "APP_DIR": os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "app")), "CALL_LOG": str(log)}
    claude.session.mcp_servers = {
        "gamachineMail": _mail_entry(backend.url),
        "gamachineBrowser": _browser_entry(backend.url),
        "gamachineConnectors": {"type": "stdio", "command": sys.executable, "args": [str(server_file)],
                                "env": connectors_env},
        # Never a group: would fail loudly if anything tried to start it.
        "gamachineComputer": {"type": "stdio", "command": "definitely-not-a-binary", "args": [], "env": {}},
    }
    claude.connector_log = log
    claude.backend = backend
    return claude


def test_claude_groups_come_from_the_session_entries_and_never_computer_use(claude_servers):
    labels = [g.label for g in script_server.groups_for(claude_servers.session)]
    assert labels == ["Unity MCP", "Chat mail", "Browser", "Connectors"]


def test_claude_mail_browser_and_connector_calls_pass_can_use_tool_then_their_gate(claude_servers, runtime, procs,
                                                                                    monkeypatch):
    monkeypatch.setattr(approval_mode, "current_mode", lambda: "auto")
    text, is_error = _claude_run(claude_servers, """
        const names = Object.keys(tools);
        const chats = await tools.list_chats({});
        const snap = await tools.browser_snapshot({});
        const scene = await tools.blender_get_scene_info({detail: 1});
        let click = "";
        try { await callTool("browser_click", {snapshot_id: "s", ref: 1}); } catch (e) { click = e.message; }
        return {chats, snap, objects: scene.objects, click,
                computer: names.filter(n => n.startsWith("computer_")).length,
                writes: names.filter(n => ["browser_click", "browser_fill", "browser_download_to_project",
                                           "show_image"].includes(n))};""")
    assert not is_error, text
    gated = [name for name, verdict in claude_servers.gated if verdict == "PermissionResultAllow"]
    assert gated == ["mcp__gamachineMail__list_chats", "mcp__gamachineBrowser__browser_snapshot",
                     "mcp__gamachineConnectors__blender_get_scene_info"]
    value = json.loads(text.split("Result:\n", 1)[1].split("\nConsole:", 1)[0].split("\nFull output", 1)[0])
    assert value["computer"] == 0 and value["writes"] == []
    assert value["click"].startswith('no tool named "browser_click"') and value["objects"] == ["Cube", "Camera"]
    backend = claude_servers.backend
    assert [b["tool"] for m, p, b, _ in backend.requests if p == "/browser/command"] == ["browser_snapshot"]
    assert any(p.startswith("/mailbox/chats") for p in backend.paths("GET"))
    calls = [json.loads(line) for line in claude_servers.connector_log.read_text(encoding="utf-8").splitlines()]
    assert calls == [["blender_get_scene_info", {"detail": 1}, "7"]]


def test_claude_read_only_session_refuses_mail_before_the_server(claude_servers, runtime, procs, monkeypatch):
    monkeypatch.setattr(approval_mode, "current_mode", lambda: "auto")
    claude_servers.session.read_only = True
    text, _ = _claude_run(claude_servers, """
        try { await tools.send_chat_message({to_chat_id: 3, message: "x"}); return "sent"; }
        catch (e) { return e.message; }""")
    assert "sent" not in text.split("Result:")[-1]
    assert ("mcp__gamachineMail__send_chat_message", "PermissionResultDeny") in claude_servers.gated
    assert "/mailbox/send" not in claude_servers.backend.paths("POST")


def test_claude_connector_user_refusal_ends_the_script(claude_servers, runtime, procs, monkeypatch):
    monkeypatch.setattr(approval_mode, "current_mode", lambda: "auto")
    text, is_error = _claude_run(claude_servers, """
        try { await tools.blender_execute_blender_code({code: "x"}); } catch (e) {}
        await tools.blender_get_scene_info({});""")
    assert is_error and "a tool call was refused" in text
    calls = [json.loads(line)[0] for line in claude_servers.connector_log.read_text(encoding="utf-8").splitlines()]
    assert calls == ["blender_execute_blender_code"]


def test_codex_unityai_entry_carries_the_run_script_timeout(monkeypatch):
    from providers import codex_session
    from providers.codex_provider import CodexProvider
    flag = f"mcp_servers.unityai.tool_timeout_sec={script_tool.CODEX_UNITYAI_TOOL_TIMEOUT_S}"
    assert script_tool.CODEX_UNITYAI_TOOL_TIMEOUT_S > script_tool.MAX_TIMEOUT_S
    monkeypatch.setattr(codex_session, "_configured_codex_mcp_names", lambda: {"unityai"})
    assert codex_session.unityai_timeout_override() == ["-c", flag]
    # No registered unityai entry: a bare table would fail Codex's config load, so nothing is passed.
    monkeypatch.setattr(codex_session, "_configured_codex_mcp_names", lambda: set())
    assert codex_session.unityai_timeout_override() == []
    import inspect
    assert "spawn = spawn + unityai_timeout_override()" in inspect.getsource(codex_session)
    # _build_cmd asks whether Unity MCP runs; unstubbed that probed 127.0.0.1:8080 (the owner's live one).
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda *a, **k: False)
    provider = CodexProvider.__new__(CodexProvider)
    for attr, value in (("prompt_via_stdin", True), ("binary_name", "codex")):
        setattr(provider, attr, value)
    monkeypatch.setattr("providers.codex_provider.provider_guide", lambda *a, **k: "")
    try:
        cmd = provider._build_cmd("hi", "off", None)
    except Exception as exc:  # noqa: BLE001 - only the flag is under test
        pytest.skip(f"_build_cmd needs more provider state here: {exc}")
    assert flag in cmd


def test_codex_registration_names_the_cap_for_the_unityai_process(monkeypatch):
    import subprocess as sp
    from providers.codex_provider import CodexProvider
    seen = []
    monkeypatch.setattr(sp, "run", lambda argv, **kw: seen.append(list(argv)) or SimpleNamespace(returncode=0))
    provider = CodexProvider.__new__(CodexProvider)
    provider.binary_name = "codex"
    monkeypatch.setattr(CodexProvider, "_cli_installed", lambda self, name: True)
    monkeypatch.setattr(CodexProvider, "_resolve_exec", staticmethod(lambda argv: argv))
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda host="localhost": None)
    provider._register_mcp("launcher", "ws", "http://127.0.0.1:1")
    add = next(argv for argv in seen if argv[:4] == ["codex", "mcp", "add", "unityai"])
    assert f"{script_tool.SCRIPT_MAX_ENV}={script_tool.MAX_TIMEOUT_S}" in add


def test_agy_unityai_entry_caps_scripts_under_its_180_s_cancel():
    import inspect
    from providers import agy_provider
    assert script_tool.AGY_SCRIPT_MAX_S <= 180 - 25
    assert "script_tool.SCRIPT_MAX_ENV: str(script_tool.AGY_SCRIPT_MAX_S)" in inspect.getsource(
        agy_provider.AgyProvider._register_mcp)


@pytest.mark.parametrize("env,expected", [(None, script_tool.UNKNOWN_CLI_MAX_S), ("150", 150), ("600", 600),
                                          ("9999", script_tool.UNKNOWN_CLI_MAX_S), ("abc", script_tool.UNKNOWN_CLI_MAX_S)])
def test_unityai_cap_comes_from_the_entry_env_and_is_stated(monkeypatch, env, expected):
    from mcp.server.mcpserver import MCPServer
    if env is None:
        monkeypatch.delenv(script_tool.SCRIPT_MAX_ENV, raising=False)
    else:
        monkeypatch.setenv(script_tool.SCRIPT_MAX_ENV, env)
    assert script_tool.max_timeout_from_env() == expected
    mcp = MCPServer(name="cap-probe")
    script_tools.register_script_tool(mcp, lambda: ".", [])
    desc = next(t for t in run(mcp.list_tools()) if t.name == "run_script").description
    assert f"default and max {expected} here" in desc


def test_the_cap_bounds_a_longer_requested_timeout(runtime, procs):
    result = run(script_tool.execute("while (true) {}", [], timeout_s=500, max_timeout_s=1, runtime=runtime))
    assert result.kind == "timeout" and result.seconds < 15


# ── turn 4: audit fixes ──────────────────────────────────────────────────────

import time  # noqa: E402

HELPER_SERVER = r'''
import asyncio, os, sys
with open(os.environ["PID_FILE"], "a") as f:
    f.write(str(os.getpid()) + "\n")
if os.environ.get("SILENT") == "1":
    import time
    time.sleep(120)  # never answers initialize
    sys.exit(0)
sys.path.insert(0, os.environ["APP_DIR"])
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
mcp = MCPServer(name="helper")
@mcp.tool(name="rd", annotations=ToolAnnotations(read_only_hint=True))
async def rd(x: int = 0) -> str:
    return str(x * 11)
@mcp.tool(name="slow", annotations=ToolAnnotations(read_only_hint=True))
async def slow(s: float = 30) -> str:
    await asyncio.sleep(s)
    return "late"
mcp.run(transport="stdio")
'''


def _alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(handle)


def _pids(path) -> list:
    return [int(x) for x in path.read_text().split()] if path.exists() else []


def _wait_dead(pids, seconds=10.0) -> list:
    end = time.monotonic() + seconds
    while time.monotonic() < end and any(_alive(p) for p in pids):
        time.sleep(0.2)
    return [p for p in pids if _alive(p)]


@pytest.fixture
def helper(tmp_path):
    """A stdio helper server entry (like browser/connectors/mail) that logs its pid."""
    server = tmp_path / "helper_server.py"
    server.write_text(HELPER_SERVER, encoding="utf-8")
    pid_file = tmp_path / "helper_pids.txt"
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))

    def entry(silent=False):
        env = {"PID_FILE": str(pid_file), "APP_DIR": app_dir, "SILENT": "1" if silent else "0"}
        return {"command": sys.executable, "args": [str(server)], "env": env}

    yield SimpleNamespace(entry=entry, pid_file=pid_file)
    for pid in _pids(pid_file):
        if _alive(pid):
            subprocess_kill(pid)


def subprocess_kill(pid):
    import subprocess
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        os.kill(pid, 9)


def test_anyio_scope_cancel_ends_the_run_and_its_helper(helper, runtime, procs):
    """How the MCP SDK drops a request: a level-triggered anyio scope (audit L-01)."""
    import anyio
    group = script_tool.StdioServerGroup("Helper", helper.entry(), keep=script_tool.read_only_tool)
    seen = {}

    async def scenario():
        started = time.monotonic()
        with anyio.CancelScope() as scope:
            async def killer():
                while not _pids(helper.pid_file):
                    await anyio.sleep(0.1)
                await anyio.sleep(1.5)  # the script is parked in tools.slow by now
                scope.cancel()
            async with anyio.create_task_group() as tg:
                tg.start_soon(killer)
                await script_tool.execute("await tools.slow({s: 60});", [group], runtime=runtime)
        seen["seconds"] = time.monotonic() - started

    asyncio.run(asyncio.wait_for(scenario(), 60))
    assert seen["seconds"] < 30
    assert _wait_dead(_pids(helper.pid_file)) == []


def test_cancel_during_discovery_closes_the_helper(helper, runtime, procs):
    group = script_tool.StdioServerGroup("Helper", helper.entry(silent=True))

    async def scenario():
        task = asyncio.create_task(script_tool.execute("return 1;", [group], runtime=runtime))
        while not _pids(helper.pid_file):
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        task.cancel()
        started = time.monotonic()
        with pytest.raises(asyncio.CancelledError):
            await task
        took = time.monotonic() - started
        # Checked inside this loop: asyncio.run's own shutdown would reap a leaked helper later.
        end = time.monotonic() + 3
        while time.monotonic() < end and any(_alive(p) for p in _pids(helper.pid_file)):
            await asyncio.sleep(0.2)
        return took, [p for p in _pids(helper.pid_file) if _alive(p)]

    took, alive = run(asyncio.wait_for(scenario(), 60))
    assert took < 15 and alive == []
    assert procs == []  # the runner never started


def test_the_time_limit_covers_a_helper_that_never_answers(helper, runtime, procs):
    group = script_tool.StdioServerGroup("Helper", helper.entry(silent=True))
    started = time.monotonic()
    result = run(script_tool.execute("return 1;", [group], timeout_s=2, runtime=runtime))
    assert result.kind == "timeout" and "finding the tools" in result.message
    assert time.monotonic() - started < 15
    assert _wait_dead(_pids(helper.pid_file)) == []


def test_parallel_calls_after_cached_discovery_start_one_helper(helper, runtime, procs):
    entry = helper.entry()
    first = run(script_tool.execute("return 1;", [script_tool.StdioServerGroup("Helper", entry)], runtime=runtime))
    assert first.status == "ok"
    second = run(script_tool.execute("return await Promise.all([tools.rd({x: 1}), tools.rd({x: 2})]);",
                                     [script_tool.StdioServerGroup("Helper", entry)], runtime=runtime))
    assert second.status == "ok" and second.value == [11, 22]
    pids = _pids(helper.pid_file)
    assert len(pids) == 2  # one helper per run, none extra from the parallel first calls
    assert _wait_dead(pids) == []


def test_a_unity_mcp_that_accepts_and_never_answers_is_bounded_by_the_time_limit(runtime, procs):
    import socket
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    _ALLOWED_PORTS.add(port)
    held = []

    def accept_and_stay_silent():
        try:
            while True:
                held.append(server.accept())
        except OSError:  # the test closed the socket
            pass

    threading.Thread(target=accept_and_stay_silent, daemon=True).start()
    url = f"http://127.0.0.1:{port}/mcp"

    async def factory():
        return script_tool.unity_bridge_client(url, conversation_id=None, secret_reader=lambda: "k")

    try:
        started = time.monotonic()
        result = run(script_tool.execute("return 1;", [script_tool.UnityMCPGroup(factory)], timeout_s=2,
                                         runtime=runtime))
        assert result.kind == "timeout" and time.monotonic() - started < 10
    finally:
        server.close()


def test_local_tool_failure_text_is_an_error_that_throws(unityai, runtime, procs):
    reply = _text(run(unityai.mcp.call_tool("run_script", {"code": """
        try { await tools.read_file({path: "missing.txt"}); return "read"; }
        catch (e) { return "threw: " + e.message; }"""})))
    assert "threw: Hata:" in reply and "1 read_file error" in reply


def test_unawaited_calls_do_not_land_after_the_reply(runtime, procs):
    effects = []

    async def slow(args):
        await asyncio.sleep(0.6)
        effects.append(("slow", time.monotonic()))
        return Outcome.ok("applied")

    async def fast(args):
        await asyncio.sleep(0.2)
        return Outcome.ok("fast")

    group = FakeGroup(slow=slow, fast=fast)

    async def scenario(code):
        result = await script_tool.execute(code, [group], runtime=runtime)
        ended = time.monotonic()
        await asyncio.sleep(1.0)
        return result, ended

    # Returned before the call started: it is not run at all.
    result, _ = run(scenario("tools.slow({}); return 'done';"))
    assert result.status == "ok" and [(c.tool, c.status) for c in result.calls] == [("slow", "not run")]
    assert effects == [] and any("not awaited" in note for note in result.notes)
    # Started while the script awaited something else: waited for before the reply.
    result, ended = run(scenario("tools.slow({}); await tools.fast({}); return 'done';"))
    assert result.status == "ok"
    assert [(c.tool, c.status, c.unawaited) for c in result.calls] == [("slow", "ok", True), ("fast", "ok", False)]
    assert effects and effects[0][1] <= ended
    assert "(not awaited)" in script_tool.format_reply(result, ".")


class FakeGroup:
    label = "fake"

    def __init__(self, **handlers):
        self.handlers = handlers

    async def list_tools(self):
        return [script_tool.ToolSpec(n, n, {"type": "object", "properties": {}}) for n in self.handlers]

    async def call(self, name, args):
        return await self.handlers[name](args)


def test_nan_and_infinity_in_a_tool_result_reach_the_script_as_null(runtime, procs):
    from script_tool import Outcome as O

    async def text(args):
        return O.ok(script_tool.parse_tool_text('{"v": NaN, "w": [1, Infinity], "x": 1e999}'))

    async def raw(args):
        return O.ok({"nan": float("nan")})

    result = run(script_tool.execute("return [await tools.text({}), await tools.raw({})];",
                                     [FakeGroup(text=text, raw=raw)], runtime=runtime))
    assert result.status == "ok", result.message
    assert result.value == [{"v": None, "w": [1, None], "x": None}, {"nan": None}]


Outcome = script_tool.Outcome


@pytest.mark.parametrize("code,extra", [('throw new Error("x".repeat(20000));', None),
                                        ("return 1;", "N" * 20000)])
def test_reply_cap_holds_for_large_errors_and_notes(tmp_path, runtime, procs, code, extra):
    class NoteGroup(FakeGroup):
        note = extra

    groups = [NoteGroup(echo=lambda a: None)] if extra else []
    result = run(script_tool.execute(code, groups, runtime=runtime))
    reply = script_tool.format_reply(result, str(tmp_path))
    assert len(reply.encode("utf-8")) <= script_tool.REPLY_CAP_BYTES
    saved = next((tmp_path / ".gamachine" / "script-runs").glob("run-*.txt")).read_text(encoding="utf-8")
    assert ("x" * 20000 if extra is None else "N" * 20000) in saved


def test_unityai_endpoint_marks_a_failed_script_as_an_mcp_error(unityai, runtime, procs):
    good = run(unityai.mcp.call_tool("run_script", {"code": "return 1;"}))
    bad = run(unityai.mcp.call_tool("run_script", {"code": "return ("}))
    assert getattr(good, "is_error", None) is False
    assert getattr(bad, "is_error", None) is True and "did not compile" in _text(bad)


@pytest.mark.parametrize("message", ["import interrupted by user", "worker reported out of memory",
                                     "parser reported stack overflow"])
def test_ordinary_errors_are_not_read_as_limits(runtime, procs, message):
    result = run(script_tool.execute(f"throw new Error({json.dumps(message)});", [], runtime=runtime))
    assert result.kind == "thrown" and message in result.message


@pytest.mark.parametrize("timeout", ["abc", True, 0, -3, 601, float("nan")])
def test_bad_timeout_gets_a_clear_error(tmp_path, timeout):
    text, is_error = run(script_tool.run_script("return 1;", [], str(tmp_path), timeout=timeout,
                                                max_timeout_s=600))
    assert is_error and "`timeout` must be a number of seconds from 1 to 600" in text


def test_saved_runs_keep_the_newest_50_and_never_use_the_cwd(tmp_path, monkeypatch):
    big = script_tool.ScriptResult("ok", value="v" * 9000, has_value=True)
    ws = tmp_path / "ws"
    for _ in range(55):
        script_tool.format_reply(big, str(ws))
    assert len(list((ws / ".gamachine" / "script-runs").glob("run-*.txt"))) == script_tool.KEEP_RUN_FILES
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    monkeypatch.chdir(tmp_path)
    reply = script_tool.format_reply(big, "")
    assert str(tmp_path / "temp" / "Gamachine" / "script-runs") in reply
    assert not (tmp_path / ".gamachine").exists()
