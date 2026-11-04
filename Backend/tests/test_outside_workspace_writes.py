"""Agent file writes outside the workspace follow the approval mode, like shell writes.

Owner decision (Burak, 4 Nov 2026): an agent's file-write tool treats a target
outside the workspace exactly like a shell command writing there: auto mode
allows it, balanced and step mode raise the approval card (approve -> written,
reject -> not), and Gamachine's own key/token files are refused in every mode.
Inside-workspace writes keep their old behaviour. One block per provider path.
"""
import asyncio
import io
import json
import os
from types import SimpleNamespace as NS
from unittest import mock

import pytest

from agentic import approval_mode
from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS
import action_risk


@pytest.fixture
def ws(tmp_path_factory):
    return str(tmp_path_factory.mktemp("ws").resolve())


@pytest.fixture
def outside(tmp_path_factory):
    return str(tmp_path_factory.mktemp("elsewhere").resolve() / "note.md")


@pytest.fixture
def home(tmp_path_factory, monkeypatch):
    """A fake home, so the secret list never points at the real profile."""
    root = tmp_path_factory.mktemp("home").resolve()
    (root / ".unity-mcp").mkdir()
    (root / ".unity_architect_ai").mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))
    monkeypatch.delenv("DB_PATH", raising=False)
    return root


@pytest.fixture
def mode(monkeypatch):
    def set_to(value):
        monkeypatch.setattr(approval_mode, "current_mode", lambda: value)
    return set_to


# ── The shared secret list ───────────────────────────────────────────────────


@pytest.mark.parametrize("parts", [
    (".unity-mcp", "local-api-token"),
    (".unity_architect_ai", "local-app-token"),
    (".unity_architect_ai", "api_key_fernet.key"),
    (".unity_architect_ai", "unity_master_v3.db"),
    (".unity_architect_ai", "unity_master_v3.db-wal"),
    (".unity_architect_ai", "unity_master_v3.db-shm"),
    (".unity_architect_ai", "unity_master_v3.db-journal"),
])
def test_every_secret_file_is_recognised(home, parts):
    assert action_risk.gamachine_secret_target(str(home.joinpath(*parts)))


def test_secret_spellings_and_db_path(home, tmp_path_factory, monkeypatch):
    token = home / ".unity-mcp" / "local-api-token"
    assert action_risk.gamachine_secret_target(str(token).upper() if os.name == "nt" else str(token))
    if os.name == "nt":
        assert action_risk.gamachine_secret_target(str(token) + ".")
        assert action_risk.gamachine_secret_target(str(token) + "::$DATA")
    # Relative to a base, the way a workspace-relative "../" target arrives.
    assert action_risk.gamachine_secret_target("../.unity-mcp/local-api-token", str(home / "ws"))
    assert action_risk.gamachine_secret_target(str(home / ".unity-mcp" / "other")) is None
    db_dir = tmp_path_factory.mktemp("db").resolve()
    monkeypatch.setenv("DB_PATH", str(db_dir / "x.db"))
    assert action_risk.gamachine_secret_target(str(db_dir / "x.db-wal"))
    assert action_risk.gamachine_secret_target(str(db_dir / "x.db-journal"))
    assert action_risk.gamachine_secret_target(str(db_dir / "api_key_fernet.key"))


def test_backend_decision_matches_the_shell_rule(ws, outside):
    """The MCP and AGY cards come from this decision in the backend route."""
    action = {"kind": "file_write", "paths": [outside], "workspace": ws}
    assert approval_mode.needs_card(action, mode="auto").card is False
    balanced = approval_mode.needs_card(action, mode="balanced")
    assert balanced.card and balanced.reason == "file_outside_workspace"
    assert approval_mode.needs_card(action, mode="step").card is True
    inside = {"kind": "file_write", "paths": [os.path.join(ws, "A.cs")], "workspace": ws}
    assert approval_mode.needs_card(inside, mode="balanced").card is False


# ── Claude SDK path ──────────────────────────────────────────────────────────


def _claude(ws):
    from providers.claude_sdk_session import ClaudeSDKSession
    s = ClaudeSDKSession(conversation_id=9311, cwd=ws, auto_approve=False, approval_timeout=2.0)
    s._out_q = asyncio.Queue()
    return s


async def _claude_gate(session, path, approve=True):
    task = asyncio.create_task(session._can_use_tool("Write", {"file_path": path, "content": "x"}, None))
    try:
        ev = await asyncio.wait_for(session._out_q.get(), timeout=0.5)
    except asyncio.TimeoutError:
        ev = None
    if ev is not None and ev.get("type") == "command_approval_needed":
        APPROVAL_RESULTS[ev["gate_id"]] = approve
        APPROVAL_GATES[ev["gate_id"]].set()
    return await asyncio.wait_for(task, timeout=3.0), ev


def _allowed(res):
    return type(res).__name__ == "PermissionResultAllow"


async def test_claude_auto_outside_write_is_allowed(ws, outside, home, mode):
    mode("auto")
    res, ev = await _claude_gate(_claude(ws), outside)
    assert _allowed(res) and ev is None


@pytest.mark.parametrize("approve", [True, False])
async def test_claude_balanced_outside_write_shows_the_card(ws, outside, home, mode, approve):
    mode("balanced")
    res, ev = await _claude_gate(_claude(ws), outside, approve=approve)
    assert ev["type"] == "command_approval_needed"
    assert ev["risk_reason"] == "file_outside_workspace" and outside in ev["command"]
    assert _allowed(res) is approve


async def test_claude_secret_write_is_refused_in_auto(ws, home, mode):
    mode("auto")
    res, ev = await _claude_gate(_claude(ws), str(home / ".unity-mcp" / "local-api-token"))
    assert not _allowed(res) and "token" in res.message
    assert ev["type"] == "tool_result" and ev["success"] is False


async def test_claude_inside_write_unchanged(ws, home, mode):
    mode("balanced")
    res, ev = await _claude_gate(_claude(ws), os.path.join(ws, "Assets", "A.cs"))
    assert _allowed(res) and ev is None


# ── AGY native write path ────────────────────────────────────────────────────


@pytest.fixture
def agy(ws, monkeypatch):
    import agy_native_approval as native
    import agy_step_gate as gate
    state = os.path.join(ws, ".agents", "state.json")
    gate.write_state(state, "balanced", "unityai", workspace=ws)
    token = native.state_path.set(state)
    monkeypatch.setenv("UNITYAI_URL", "http://approval.invalid")
    calls, answer = [], {"status": "resolved", "approved": True}

    def urlopen(request, timeout):
        calls.append(json.loads(request.data))
        return io.BytesIO(json.dumps(answer).encode())

    monkeypatch.setattr(native.urllib.request, "urlopen", urlopen)

    def write(target, approved=True):
        answer["approved"] = approved
        return native.request_native_write(
            "write_to_file", {"TargetFile": target, "CodeContent": "x"}, ws)
    yield write, calls, state
    native.state_path.reset(token)


def test_agy_auto_outside_write_is_allowed(ws, outside, agy):
    import agy_step_gate as gate
    _, _, state = agy
    gate.write_state(state, "auto", "unityai", workspace=ws)
    payload = {"toolCall": {"name": "write_to_file", "args": {"TargetFile": outside, "CodeContent": "x"}}}
    with mock.patch.object(gate, "_hook_workspace", return_value=ws):
        assert gate.decide(json.dumps(payload).encode(), state)["decision"] == "allow"


@pytest.mark.parametrize("approved", [True, False])
def test_agy_balanced_outside_write_shows_the_card(outside, home, agy, approved):
    write, calls, _ = agy
    allowed, _reason = write(outside, approved)
    assert allowed is approved
    assert [c["params"]["path"] for c in calls] == [os.path.realpath(outside)]
    assert not os.path.exists(outside)  # the bridge only asks; agy writes


def test_agy_secret_write_is_refused(home, agy):
    write, calls, _ = agy
    allowed, reason = write(str(home / ".unity_architect_ai" / "api_key_fernet.key"))
    assert not allowed and "token" in reason and calls == []


def test_agy_inside_write_unchanged(ws, home, agy):
    write, calls, _ = agy
    assert write("a.txt")[0] is True
    assert calls[0]["params"]["path"] == os.path.join(ws, "a.txt")


# ── API-provider path (agent_runner + tools.file_tools) ──────────────────────


def _anthropic_reply(tool_input=None):
    blocks = [NS(type="text", text="done")]
    if tool_input is not None:
        blocks = [NS(type="tool_use", id="call1", name="write_file", input=tool_input)]
    return NS(content=blocks, stop_reason="tool_use" if tool_input else "end_turn",
              usage=NS(input_tokens=1, output_tokens=1))


@pytest.fixture
def api_turn(ws, monkeypatch):
    """One real _api_loop turn on a fake Anthropic client: approval prompt,
    gate and execution are the production code; only the answer is stubbed."""
    import agentic.agent_runner as ar
    monkeypatch.setattr(ar, "get_gemini_tool_declarations", lambda: [{"function_declarations": []}])
    monkeypatch.setattr(ar, "get_openai_tool_declarations", lambda: [])
    monkeypatch.setattr(ar, "_all_tool_definitions", lambda: [])
    monkeypatch.setattr(ar.AgentRunner, "_get_architect_wisdom", lambda self: "")

    def run(file_path, approve=None):
        replies = iter([_anthropic_reply({"file_path": file_path, "content": "hello"}),
                        _anthropic_reply()])

        async def create(**kwargs):
            return next(replies)
        client = NS(messages=NS(create=create))
        monkeypatch.setattr(ar.anthropic, "AsyncAnthropic", lambda **kw: client)
        runner = ar.AgentRunner("anthropic", "k", "m", ws)
        asked = []

        async def answer(gate_id):
            asked.append(gate_id)
            return NS(approved=bool(approve), summary="declined")
        monkeypatch.setattr(runner, "_await_approval", answer)

        async def collect():
            return [e async for e in runner._run_inner("hi")]
        with mock.patch.object(ar.asyncio, "sleep", mock.AsyncMock()):
            events = asyncio.run(collect())
        cards = [e.data for e in events if e.type == "command_approval_needed"]
        results = [e.data for e in events if e.type == "tool_result"]
        return cards, results
    return run


def test_api_auto_outside_write_is_allowed(outside, home, mode, api_turn):
    mode("auto")
    cards, results = api_turn(outside)
    assert cards == [] and results[0]["success"] is True
    with open(outside, encoding="utf-8") as f:
        assert f.read() == "hello"


@pytest.mark.parametrize("current", ["balanced", "step"])
@pytest.mark.parametrize("approve", [True, False])
def test_api_outside_write_shows_the_card(outside, home, mode, api_turn, current, approve):
    mode(current)
    cards, results = api_turn(outside, approve=approve)
    assert len(cards) == 1 and outside in cards[0]["command"]
    assert cards[0]["risk_reason"] == "file_outside_workspace"
    assert results[0]["success"] is approve
    assert os.path.exists(outside) is approve


def test_api_secret_write_is_refused_in_auto(home, mode, api_turn):
    mode("auto")
    secret = home / ".unity-mcp" / "local-api-token"
    cards, results = api_turn(str(secret))
    assert cards == [] and results[0]["success"] is False
    assert not secret.exists()


@pytest.mark.parametrize("current", ["step", "balanced"])
def test_api_inside_write_unchanged(ws, home, mode, api_turn, current):
    mode(current)
    cards, results = api_turn("Assets/A.cs")
    assert cards == [] and results[0]["success"] is True
    assert os.path.exists(os.path.join(ws, "Assets", "A.cs"))


def test_api_file_tool_alone_still_refuses_outside(ws, outside, home):
    """Without the loop's one-path allowance the tool keeps the workspace jail,
    and an allowance for one path does not open another."""
    from tools import file_tools
    assert file_tools.write_file(outside, "x", ws)["success"] is False
    token = file_tools.OUTSIDE_WRITE_ALLOWED.set(os.path.realpath(outside))
    try:
        other = os.path.join(os.path.dirname(outside), "other.md")
        assert file_tools.write_file(other, "x", ws)["success"] is False
        assert file_tools.write_file(outside, "x", ws)["success"] is True
    finally:
        file_tools.OUTSIDE_WRITE_ALLOWED.reset(token)
    assert not os.path.exists(other)


# ── unityai MCP path (save_file and bash redirects) ──────────────────────────


@pytest.fixture
def mcp(ws, monkeypatch):
    import unity_ai_mcp.tools.bash_tool as bash_tool
    import unity_ai_mcp.tools.file_tools as mcp_file_tools
    tools, asked, answer = {}, [], {"approved": True}

    class _FakeMCP:
        def tool(self, *args, **kwargs):
            def deco(fn):
                tools[kwargs.get("name") or fn.__name__] = fn
                return fn
            return deco

    async def request_approval(**kwargs):
        # The backend route answers from approval_mode.needs_card; the auto,
        # balanced and step answers are covered by test_backend_decision_matches_the_shell_rule.
        asked.append(kwargs)
        return dict(answer)

    for module in (mcp_file_tools, bash_tool):
        monkeypatch.setattr(module, "request_approval", request_approval)
    mcp_file_tools.register_file_tools(_FakeMCP(), lambda: ws)
    bash_tool.register_bash_tool(_FakeMCP(), lambda: ws)
    return tools, asked, answer


@pytest.mark.parametrize("approved", [True, False])
async def test_mcp_save_file_outside_goes_to_the_card(outside, home, mcp, approved):
    tools, asked, answer = mcp
    answer["approved"] = approved
    out = await tools["save_file"](outside, "hello")
    assert [a["tool_name"] for a in asked] == ["write_file"]
    assert asked[0]["params"]["path"] == outside
    assert out.startswith("✅") is approved and os.path.exists(outside) is approved


@pytest.mark.parametrize("approved", [True, False])
async def test_mcp_bash_redirect_outside_goes_to_the_card(outside, home, mcp, approved):
    tools, asked, answer = mcp
    answer["approved"] = approved
    out = await tools["bash"](f"echo 'hello' > {outside}")
    assert [a["tool_name"] for a in asked] == ["write_file"]
    assert out.startswith("✅") is approved and os.path.exists(outside) is approved


async def test_mcp_secret_write_is_refused_without_a_card(home, mcp):
    tools, asked, _ = mcp
    secret = home / ".unity-mcp" / "local-api-token"
    assert "token" in await tools["save_file"](str(secret), "x")
    assert "token" in await tools["bash"](f"echo 'x' > {secret}")
    assert asked == [] and not secret.exists()


async def test_mcp_inside_write_unchanged(ws, home, mcp):
    tools, asked, _ = mcp
    assert (await tools["save_file"]("Assets/A.cs", "x")).startswith("✅")
    assert asked[0]["params"]["path"] == "Assets/A.cs"
    assert os.path.exists(os.path.join(ws, "Assets", "A.cs"))


# ── Closing the remaining gaps (4 Nov 2026, second round) ────────────────────


@pytest.mark.skipif(os.name != "nt", reason="admin-share UNC paths exist on Windows only")
def test_local_admin_share_spellings_of_a_secret_are_refused(home):
    import socket
    bs = chr(92)
    secret = str(home / ".unity_architect_ai" / "api_key_fernet.key")
    drive, rest = secret[0], secret[2:]
    for host in ("localhost", "LOCALHOST", "127.0.0.1", "::1", socket.gethostname()):
        unc = bs + bs + host + bs + drive + "$" + rest
        assert action_risk.gamachine_secret_target(unc), unc
        assert action_risk.write_target(unc, "").lower() == os.path.realpath(secret).lower()
    # Any other UNC target stays what it was: an outside path for the approval policy.
    other = bs + bs + "fileserver" + bs + drive + "$" + rest
    assert action_risk.gamachine_secret_target(other) is None
    assert action_risk.write_target(other, "").startswith(bs + bs)
    plain = bs + bs + "localhost" + bs + drive + "$" + bs + "notes.txt"
    assert action_risk.gamachine_secret_target(plain) is None


@pytest.mark.parametrize("current", ["auto", "step", "balanced"])
def test_agy_secret_write_is_refused_in_every_mode(ws, home, current):
    import agy_step_gate as gate
    state = os.path.join(ws, ".agents", "state.json")
    gate.write_state(state, current, "unityai", workspace=ws)
    secret = str(home / ".unity_architect_ai" / "api_key_fernet.key")
    payload = {"toolCall": {"name": "write_to_file", "args": {"TargetFile": secret, "CodeContent": "x"}}}
    with mock.patch.object(gate, "_hook_workspace", return_value=ws):
        verdict = gate.decide(json.dumps(payload).encode(), state)
    assert verdict["decision"] == "deny" and "token" in verdict["reason"]


@pytest.fixture
def cli(ws, monkeypatch, capsys):
    import unityai_cli
    asked, answer = [], {"approved": True}

    async def request_approval(**kwargs):
        asked.append(kwargs)
        return dict(answer)

    monkeypatch.setattr(unityai_cli, "request_approval", request_approval)
    monkeypatch.setenv("WORKSPACE", ws)

    def save(path, content="hello"):
        args = NS(path=path, content=content, content_stdin=False)
        return unityai_cli.cmd_save_file(args), capsys.readouterr().out
    return save, asked, answer


@pytest.mark.parametrize("approved", [True, False])
def test_unityai_save_file_outside_goes_to_the_card(outside, home, cli, approved):
    save, asked, answer = cli
    answer["approved"] = approved
    rc, _ = save(outside)
    assert [a["tool_name"] for a in asked] == ["write_file"]
    assert asked[0]["params"]["path"] == outside
    assert (rc == 0) is approved and os.path.exists(outside) is approved


def test_unityai_save_file_secret_is_refused_without_a_card(home, cli):
    save, asked, _ = cli
    secret = home / ".unity-mcp" / "local-api-token"
    rc, out = save(str(secret))
    assert rc == 1 and "token" in out and asked == [] and not secret.exists()


def test_unityai_save_file_inside_unchanged(ws, home, cli):
    save, asked, _ = cli
    assert save("Assets/A.cs")[0] == 0
    assert asked[0]["params"]["path"] == "Assets/A.cs"
    assert os.path.exists(os.path.join(ws, "Assets", "A.cs"))


def test_api_delete_file_refuses_a_secret_inside_the_workspace(ws, home, monkeypatch):
    from tools import file_tools
    key = os.path.join(ws, "api_key_fernet.key")
    with open(key, "w", encoding="utf-8") as f:
        f.write("k")
    monkeypatch.setenv("DB_PATH", os.path.join(ws, "x.db"))
    result = file_tools.delete_file(key, ws)
    assert result["success"] is False and "token" in result["error"]
    assert os.path.exists(key)


def test_api_write_file_accepts_an_absolute_path_inside_the_workspace(ws, home):
    from tools.tool_registry import execute_tool
    target = os.path.join(ws, "Assets", "a.txt")
    result = execute_tool("write_file", {"file_path": target, "content": "x"}, ws)
    assert result["success"] is True
    with open(target, encoding="utf-8") as f:
        assert f.read() == "x"
    assert execute_tool("write_file", {"file_path": "b.txt", "content": "y"}, ws)["success"] is True
    assert os.path.exists(os.path.join(ws, "b.txt"))


# ── Secrets recognised by file identity ──────────────────────────────────────


@pytest.fixture
def real_secret(home):
    secret = home / ".unity_architect_ai" / "api_key_fernet.key"
    secret.write_text("fake")
    return secret


def test_hard_link_to_a_secret_is_a_secret(home, real_secret, tmp_path_factory):
    link = tmp_path_factory.mktemp("links").resolve() / "innocent.txt"
    os.link(real_secret, link)
    assert action_risk.gamachine_secret_target(str(link))
    # A relative spelling resolved against a base is the same file.
    assert action_risk.gamachine_secret_target("innocent.txt", str(link.parent))


def test_a_different_file_is_not_a_secret(home, real_secret, tmp_path_factory):
    other = tmp_path_factory.mktemp("other").resolve() / "other.txt"
    other.write_text("fake")
    assert action_risk.gamachine_secret_target(str(other)) is None
    assert action_risk.gamachine_secret_target(str(other.parent / "missing.txt")) is None


@pytest.mark.skipif(os.name != "nt", reason="UNC spellings exist on Windows only")
@pytest.mark.parametrize("style", ["localhost", "dev_unc", "qmark_unc", "ipv6"])
def test_unc_spellings_of_an_existing_secret_are_secret(home, real_secret, style):
    bs = chr(92)
    secret = str(real_secret)
    drive, rest = secret[0], secret[2:]
    spelling = {
        "localhost": bs + bs + "localhost" + bs + drive + "$" + rest,
        "dev_unc": bs + bs + "." + bs + "UNC" + bs + "localhost" + bs + drive + "$" + rest,
        "qmark_unc": "//?/UNC/localhost/" + drive + "$" + rest.replace(bs, "/"),
        "ipv6": bs + bs + "0-0-0-0-0-0-0-1.ipv6-literal.net" + bs + drive + "$" + rest,
    }[style]
    if not os.path.exists(spelling):
        pytest.skip("admin share unreachable on this machine")
    assert action_risk.gamachine_secret_target(spelling)


@pytest.fixture
def home_in_ws(ws, monkeypatch):
    root = os.path.join(ws, "h")
    os.makedirs(os.path.join(root, ".unity_architect_ai"))
    monkeypatch.setenv("HOME", root)
    monkeypatch.setenv("USERPROFILE", root)
    monkeypatch.delenv("DB_PATH", raising=False)
    secret = os.path.join(root, ".unity_architect_ai", "api_key_fernet.key")
    with open(secret, "w") as fh:
        fh.write("fake")
    return secret


async def test_mcp_delete_refuses_a_secret(ws, home_in_ws, mcp):
    tools, asked, _ = mcp
    link = os.path.join(ws, "innocent.txt")
    os.link(home_in_ws, link)
    for target in (home_in_ws, link):
        out = await tools["delete_file"](target)
        assert out.startswith("❌") and os.path.exists(target)
    assert asked == []


def test_unityai_delete_refuses_a_secret(ws, home_in_ws, monkeypatch, capsys):
    import unityai_cli
    monkeypatch.setenv("WORKSPACE", ws)
    link = os.path.join(ws, "innocent.txt")
    os.link(home_in_ws, link)
    for target in (home_in_ws, link):
        assert unityai_cli.cmd_delete_file(NS(path=target)) == 1
        assert "❌" in capsys.readouterr().out and os.path.exists(target)
