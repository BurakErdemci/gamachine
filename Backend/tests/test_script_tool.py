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


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    approval_mode._reset_for_tests()
    script_tool._BRIDGES.clear()
    # The real token file must never be read by a test.
    monkeypatch.setattr(codex_unitymcp_bridge, "_read_shared_secret", lambda: "file-token")
    yield
    script_tool._BRIDGES.clear()
    approval_mode._reset_for_tests()


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
    # Mail, image and harness tools are not relayed this turn, and the tool is not Unity-only.
    assert "tools.send_chat_message(" not in desc and "tools.harness_search(" not in desc
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
