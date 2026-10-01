"""The balanced approval mode ("Güvenli Otomatik" in the UI) at every approval point.

Owner decision (Burak, 27 Sep 2026): a third global mode between step and
auto. The AI works on its own and only actions action_risk calls critical
raise the card; it is the fresh-install default and a saved mode is never
migrated to it. These tests hold each approval point to that: routine passes
with no card, critical raises exactly one card that carries its reason, and
the refusals that come before the mode (side chat, deleted chat, the fixed
Unity file rule) still come first.
"""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode
from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS, register_gate, release_gate

UI_SECRET = "ui-secret-for-tests"


class _Store:
    def __init__(self, value=None):
        self.rows = {} if value is None else {"approval_mode": value}

    def get_setting(self, key):
        return self.rows.get(key)

    def set_setting(self, key, value):
        self.rows[key] = value


def _client(db=None):
    from routes.conversation_routes import create_conversation_router

    app = FastAPI()
    app.include_router(create_conversation_router(db or MagicMock(), {}))
    return TestClient(app)


def _request(client, gate_id, tool, params, workspace_path=""):
    return client.post("/mcp-approval-request", json={
        "gate_id": gate_id, "tool": tool, "params": params,
        "workspace_path": workspace_path}).json()


@pytest.fixture
def balanced():
    approval_mode.set_mode("balanced", source="test")
    assert approval_mode.current_mode() == "balanced"


@pytest.fixture
def ws(tmp_path):
    for d in ("Assets", "ProjectSettings", "Packages", ".git"):
        (tmp_path / d).mkdir()
    return str(tmp_path)


# ── the mode store ───────────────────────────────────────────────────────────

def test_fresh_install_with_a_ui_secret_is_balanced_and_nothing_is_stored():
    store = _Store()
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "balanced"
    assert approval_mode.is_stored() is False and store.rows == {}


def test_fresh_install_without_a_ui_secret_is_step():
    approval_mode.bind_store(_Store())
    assert approval_mode.current_mode() == "step"


@pytest.mark.parametrize("stored", ["auto", "step", "balanced"])
def test_a_stored_mode_is_never_migrated(stored):
    store = _Store(stored)
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == stored
    assert store.rows == {"approval_mode": stored}


def test_stored_balanced_reads_as_step_without_a_ui_secret():
    """Like a stored auto: a no-secret process could never switch it."""
    store = _Store("balanced")
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "step"
    assert store.rows == {"approval_mode": "balanced"}


@pytest.mark.parametrize("value", ["plan", "Balanced", "balanced ", "", 7])
def test_an_unknown_stored_value_is_step(value):
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(_Store(value))
    assert approval_mode.current_mode() == "step"


def test_set_mode_balanced_persists_and_survives_a_restart():
    store = _Store()
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(store)
    approval_mode.set_mode("step", source="test")
    approval_mode.set_mode("balanced", source="test")
    assert store.rows == {"approval_mode": "balanced"}
    approval_mode._reset_for_tests()
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "balanced"


def test_needs_card_per_mode():
    delete = {"kind": "file_delete", "paths": ["x"]}
    mail = {"kind": "mail"}
    assert approval_mode.needs_card(mail, mode="step").card is True
    assert approval_mode.needs_card(delete, mode="auto").card is False
    assert approval_mode.needs_card(mail, mode="balanced").card is False
    decision = approval_mode.needs_card(delete, mode="balanced")
    assert decision.card is True and decision.reason == "file_delete"
    # A mode this module does not know asks (fail closed).
    assert approval_mode.needs_card(mail, mode="plan").card is True


def test_the_route_accepts_balanced_and_still_refuses_unknown_modes():
    approval_mode.set_ui_secret(UI_SECRET)
    with _client() as client:
        ok = client.post("/approval-mode", json={"mode": "balanced"},
                         headers={"X-Gamachine-UI-Secret": UI_SECRET})
        assert ok.status_code == 200 and ok.json()["mode"] == "balanced"
        assert client.get("/approval-mode").json()["mode"] == "balanced"
        for bad in ("plan", "safe_auto", "BALANCED"):
            resp = client.post("/approval-mode", json={"mode": bad},
                               headers={"X-Gamachine-UI-Secret": UI_SECRET})
            assert resp.status_code == 400
        assert client.post("/approval-mode", json={"mode": "balanced"}).status_code == 403


# ── /mcp-approval-request ────────────────────────────────────────────────────

@pytest.mark.parametrize("tool,params", [
    ("manage_gameobject", {"action": "create", "name": "Cube"}),
    ("manage_components", {"action": "remove", "component_type": "BoxCollider"}),
    ("batch_execute", {"commands": [{"tool": "manage_components", "params": {"action": "add"}}]}),
    ("write_file", {"path": "Assets/Player.cs", "content": "x"}),
    ("bash", {"command": "npm test"}),
])
def test_routine_requests_resolve_without_a_card(balanced, ws, tool, params):
    with _client() as client:
        result = _request(client, "r-1", tool, params, workspace_path=ws)
        assert result["status"] == "resolved" and result["approved"] is True, result
        assert client.get("/mcp-pending").json()["pending"] == {}


@pytest.mark.parametrize("tool,params,reason", [
    ("manage_gameobject", {"action": "delete", "target": "Player"}, "unity_critical_action"),
    ("batch_execute", {"commands": [{"tool": "manage_components", "params": {"action": "add"}},
                                    {"tool": "manage_asset", "params": {"action": "delete"}}]},
     "unity_critical_action"),
    ("no_such_tool", {}, "unity_unknown_tool"),
    ("write_file", {"path": "ProjectSettings/TagManager.asset", "content": "x"}, "file_protected"),
    ("write_file", {"path": "../escape.cs", "content": "x"}, "file_outside_workspace"),
    ("delete_file", {"path": "Assets/Player.cs"}, "file_delete"),
    ("bash", {"command": "Remove-Item Assets/x.cs"}, "shell_delete_move"),
    ("bash", {"command": "npm install left-pad"}, "shell_installer"),
])
def test_critical_requests_raise_one_card_with_the_reason(balanced, ws, tool, params, reason):
    with _client() as client:
        result = _request(client, "c-1", tool, params, workspace_path=ws)
        assert result == {"status": "ok", "gate_id": "c-1"}
        pending = client.get("/mcp-pending").json()["pending"]
        assert list(pending) == ["c-1"]
        assert pending["c-1"]["risk_reason"] == reason
        assert "risk_detail" in pending["c-1"]
        # The reason is not mixed into what the card shows as the call's params.
        assert "risk_reason" not in pending["c-1"]["params"]
        client.post("/mcp-approval-respond/c-1", json={"approved": False})


def test_step_cards_carry_no_reason(ws):
    with _client() as client:
        assert _request(client, "s-1", "manage_gameobject", {"action": "create"})["status"] == "ok"
        assert "risk_reason" not in client.get("/mcp-pending").json()["pending"]["s-1"]
        client.post("/mcp-approval-respond/s-1", json={"approved": False})


def test_a_deleted_chat_is_refused_before_balanced_approves():
    approval_mode.set_mode("balanced", source="test")
    db = MagicMock()
    db.get_conversation_owner.return_value = None
    with _client(db) as client:
        result = client.post("/mcp-approval-request", json={
            "gate_id": "gone", "tool": "manage_gameobject", "params": {"action": "create"},
            "conversation_id": 99}).json()
    assert result["approved"] is False and "silindi" in result["error"]


# ── switching modes ──────────────────────────────────────────────────────────

def test_step_to_balanced_approves_routine_cards_and_keeps_critical_ones(ws):
    approval_mode.set_ui_secret(UI_SECRET)
    with _client() as client:
        assert _request(client, "routine", "manage_gameobject", {"action": "create"})["status"] == "ok"
        assert _request(client, "critical", "manage_gameobject", {"action": "delete"})["status"] == "ok"
        assert _request(client, "shell-ok", "bash", {"command": "git status"}, ws)["status"] == "ok"

        async def _in_process_gate():
            return register_gate("inproc-b", 7)

        event = asyncio.run(_in_process_gate())
        try:
            resp = client.post("/approval-mode", json={"mode": "balanced"},
                               headers={"X-Gamachine-UI-Secret": UI_SECRET})
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"mode": "balanced", "previous": "step", "approved_pending": 2}
            pending = client.get("/mcp-pending").json()["pending"]
            assert list(pending) == ["critical"]
            assert pending["critical"]["risk_reason"] == "unity_critical_action"
            for gid in ("routine", "shell-ok"):
                got = client.get(f"/mcp-approval-result/{gid}").json()
                assert got["status"] == "resolved" and got["approved"] is True
            assert client.get("/mcp-approval-result/critical").json() == {"status": "pending"}
            # An in-process card holds only display text: it stays pending.
            assert not event.is_set()
            assert "inproc-b" not in APPROVAL_RESULTS or APPROVAL_RESULTS["inproc-b"] is not True
        finally:
            release_gate("inproc-b")
            client.post("/mcp-approval-respond/critical", json={"approved": False})


def test_auto_to_balanced_switches_and_leaves_nothing_pending():
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.set_mode("auto", source="test")
    with _client() as client:
        assert _request(client, "a-1", "manage_gameobject", {"action": "delete"})["approved"] is True
        resp = client.post("/approval-mode", json={"mode": "balanced"},
                           headers={"X-Gamachine-UI-Secret": UI_SECRET})
        assert resp.json() == {"mode": "balanced", "previous": "auto", "approved_pending": 0}
        # From now on the critical call asks.
        assert _request(client, "a-2", "manage_gameobject", {"action": "delete"})["status"] == "ok"
        client.post("/mcp-approval-respond/a-2", json={"approved": False})


# ── API loop (_approval_prompt) ──────────────────────────────────────────────

def _runner(ws):
    from agentic.agent_runner import AgentRunner

    r = AgentRunner.__new__(AgentRunner)
    r.workspace_path = ws
    r.conversation_id = 5
    return r


def test_api_loop_balanced(balanced, ws):
    r = _runner(ws)
    assert r._approval_prompt("run_command", {"command": "npm test"}) is None
    assert r._approval_prompt("run_command", {"command": "git status"}) is None
    assert r._approval_prompt("write_file", {"file_path": "Assets/X.cs", "content": "x"}) is None
    assert r._approval_prompt("read_file", {"file_path": "Assets/X.cs"}) is None
    assert r._approval_prompt("run_command", {"command": "rm Assets/X.cs"}) == "rm Assets/X.cs"
    assert r._approval_risk == ("shell_delete_move", "rm")
    assert r._approval_prompt("delete_file", {"file_path": "Assets/X.cs"})
    assert r._approval_prompt("write_file", {"file_path": ".git/config", "content": "x"})
    assert r._approval_risk[0] == "file_protected"


def test_api_loop_card_event_carries_the_reason(balanced, ws):
    r = _runner(ws)
    text = r._approval_prompt("run_command", {"command": "npm install x"})
    with r._approval_gate(text) as (event, gate_id):
        assert event.type == "command_approval_needed"
        assert event.data["risk_reason"] == "shell_installer"
        assert event.data["command"] == "npm install x"
    # A later card (a step-mode one) does not inherit the reason.
    approval_mode.set_mode("step", source="test")
    text = r._approval_prompt("delete_file", {"file_path": "Assets/X.cs"})
    with r._approval_gate(text) as (event, _gid):
        assert "risk_reason" not in event.data


def test_api_loop_step_mode_is_unchanged(ws):
    r = _runner(ws)
    assert r._approval_prompt("write_file", {"file_path": ".git/config", "content": "x"}) is None
    assert r._approval_prompt("run_command", {"command": "npm test"}) == "npm test"
    assert r._approval_prompt("delete_file", {"file_path": "Assets/X.cs"})


@pytest.mark.parametrize("mode,interactive", [
    ("auto", False), ("balanced", True), ("step", True), ("plan", True), ("", True)])
async def test_simple_run_is_interactive_in_every_mode_but_auto(monkeypatch, mode, interactive):
    """_run_simple used to test == 'step', so an unknown mode ran like auto."""
    import ai_providers

    seen = []

    class _Provider:
        async def analyze_code_with_thinking(self, prompt, **kwargs):
            seen.append(kwargs["interactive"])
            yield {"type": "final", "text": "ok"}

    monkeypatch.setattr(ai_providers.AIProviderManager, "get_provider",
                        staticmethod(lambda cfg: _Provider()))
    r = _runner(".")
    r.provider_type, r.api_key, r.model_name = "subscription", "", "x"
    r.context, r.thinking_level, r.generation_mode = "", "medium", mode
    async for _ in r._run_simple("hi"):
        pass
    assert seen == [interactive]


# ── Claude _can_use_tool ─────────────────────────────────────────────────────

async def _drive(session, tool, inp):
    task = asyncio.create_task(session._can_use_tool(tool, inp, None))
    try:
        ev = await asyncio.wait_for(session._out_q.get(), timeout=0.3)
    except asyncio.TimeoutError:
        ev = None
    if ev is not None and ev.get("type") == "command_approval_needed":
        APPROVAL_RESULTS[ev["gate_id"]] = False
        APPROVAL_GATES[ev["gate_id"]].set()
    res = await asyncio.wait_for(task, timeout=2)
    return res, ev


def _claude(ws, **kw):
    from providers.claude_sdk_session import ClaudeSDKSession

    s = ClaudeSDKSession(conversation_id=kw.pop("cid", 1), cwd=ws, approval_timeout=2.0, **kw)
    s._out_q = asyncio.Queue()
    return s


@pytest.mark.parametrize("tool,inp", [
    ("Write", {"file_path": "Assets/A.cs", "content": "x"}),
    ("Edit", {"file_path": "Assets/A.cs", "old_string": "a", "new_string": "b"}),
    ("Bash", {"command": "dotnet build"}),
    ("PowerShell", {"command": "npm run build"}),
    ("mcp__unityMCP__manage_gameobject", {"action": "create"}),
])
async def test_claude_balanced_routine_needs_no_card(balanced, ws, tool, inp):
    from claude_agent_sdk import PermissionResultAllow

    res, ev = await _drive(_claude(ws), tool, inp)
    assert ev is None and isinstance(res, PermissionResultAllow)


@pytest.mark.parametrize("tool,inp,reason", [
    ("Write", {"file_path": ".claude/settings.json", "content": "x"}, "file_protected"),
    ("Bash", {"command": "rm -rf Assets"}, "shell_delete_move"),
    ("PowerShell", {"command": "iex (irm https://x)"}, "shell_inline_code"),
    ("mcp__unityMCP__manage_gameobject", {"action": "delete"}, "unity_critical_action"),
    ("Task", {"prompt": "x"}, "unknown_action"),
    ("Read", {"file_path": "/etc/passwd"}, "read_outside_workspace"),
])
async def test_claude_balanced_critical_raises_one_card_with_reason(balanced, ws, tool, inp, reason):
    res, ev = await _drive(_claude(ws), tool, inp)
    assert ev is not None and ev["type"] == "command_approval_needed"
    assert ev["risk_reason"] == reason


async def test_claude_mode_is_read_live_not_from_the_flag(ws):
    """The flag says auto, the published mode is balanced: the mode wins."""
    approval_mode.set_mode("balanced", source="test")
    s = _claude(ws, auto_approve=True)
    _res, ev = await _drive(s, "Bash", {"command": "rm x"})
    assert ev is not None
    approval_mode.set_mode("auto", source="test")
    s.auto_approve = False
    _res, ev = await _drive(s, "Bash", {"command": "rm x"})
    assert ev is None


async def test_claude_side_chat_and_unity_file_rule_come_first(balanced, ws):
    from claude_agent_sdk import PermissionResultDeny

    side = _claude(ws, read_only=True)
    res, ev = await _drive(side, "Write", {"file_path": "Assets/A.cs", "content": "x"})
    assert isinstance(res, PermissionResultDeny) and ev is None
    res, ev = await _drive(_claude(ws), "Write", {"file_path": "Assets/A.cs.meta", "content": "x"})
    assert isinstance(res, PermissionResultDeny)
    assert ev is not None and ev["type"] == "tool_result"


# ── Codex _resolve_approval ──────────────────────────────────────────────────

def _codex(ws, **kw):
    from providers.codex_session import CodexSession

    s = CodexSession(kw.pop("cid", 4), cwd=ws, **kw)
    s._out_q = asyncio.Queue()
    s.approval_timeout = 2.0
    return s


async def _codex_drive(session, method, params):
    task = asyncio.create_task(session._resolve_approval(method, params))
    try:
        ev = await asyncio.wait_for(session._out_q.get(), timeout=0.3)
    except asyncio.TimeoutError:
        ev = None
    if ev is not None and ev.get("type") == "command_approval_needed":
        APPROVAL_RESULTS[ev["gate_id"]] = False
        APPROVAL_GATES[ev["gate_id"]].set()
    return await asyncio.wait_for(task, timeout=2), ev


async def test_codex_balanced_routine_is_accepted_without_a_card(balanced, ws):
    s = _codex(ws)
    assert await _codex_drive(s, "item/commandExecution/requestApproval",
                              {"command": "dotnet test", "cwd": ws}) == ("accept", None)
    assert await _codex_drive(s, "execCommandApproval",
                              {"command": ["npm", "test"], "cwd": ws}) == ("accept", None)
    s._file_changes["item-1"] = [{"path": os.path.join(ws, "Assets", "A.cs"),
                                  "kind": {"type": "update"}}]
    assert await _codex_drive(s, "item/fileChange/requestApproval",
                              {"itemId": "item-1"}) == ("accept", None)
    assert await _codex_drive(s, "applyPatchApproval", {"fileChanges": {
        os.path.join(ws, "Assets", "B.cs"): {"type": "add"}}}) == ("accept", None)


@pytest.mark.parametrize("method,params,reason", [
    ("item/commandExecution/requestApproval", {"command": "git push"}, "shell_git_write"),
    ("item/permissions/requestApproval", {"permissions": {"network": True}}, "permission_request"),
    ("item/fileChange/requestApproval", {"itemId": "unknown-item"}, "unknown_action"),
    ("applyPatchApproval", {"fileChanges": {"Assets/A.cs": {"type": "delete"}}}, "file_delete"),
    ("applyPatchApproval", {"fileChanges": {"Assets/A.cs": {"type": "update",
                                                             "move_path": "Assets/B.cs"}}}, "file_move"),
    ("applyPatchApproval", {"fileChanges": {".git/config": {"type": "update"}}}, "file_protected"),
])
async def test_codex_balanced_critical_raises_one_card(balanced, ws, method, params, reason):
    decision, ev = await _codex_drive(_codex(ws), method, params)
    assert decision == "decline"
    assert ev is not None and ev["type"] == "command_approval_needed"
    assert ev["risk_reason"] == reason


async def test_codex_file_change_mixing_routine_and_critical_asks(balanced, ws):
    s = _codex(ws)
    s._file_changes["i2"] = [
        {"path": os.path.join(ws, "Assets", "A.cs"), "kind": {"type": "update"}},
        {"path": os.path.join(ws, "Assets", "B.cs"), "kind": {"type": "delete"}},
    ]
    decision, ev = await _codex_drive(s, "item/fileChange/requestApproval", {"itemId": "i2"})
    assert ev is not None and ev["risk_reason"] == "file_delete"


_CODEX_PS = '"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '


async def test_codex_balanced_wrapped_read_passes_and_wrapped_write_asks(balanced, ws):
    # The shape codex-cli 0.157.0 sends under "untrusted" (probe log, 1 Oct 2026).
    s = _codex(ws)
    assert await _codex_drive(s, "item/commandExecution/requestApproval", {
        "command": _CODEX_PS + "'Get-ChildItem -File -Name'", "cwd": ws}) == ("accept", None)
    decision, ev = await _codex_drive(s, "item/commandExecution/requestApproval", {
        "command": _CODEX_PS + "'Remove-Item Assets\\A.cs'", "cwd": ws})
    assert decision == "decline"
    assert ev is not None and ev["type"] == "command_approval_needed"


@pytest.mark.parametrize("command", [
    _CODEX_PS + "'dotnet build'",
    _CODEX_PS + '"dotnet build"',
    _CODEX_PS + "'dotnet test'",
    _CODEX_PS + "'python -m pytest'",
    _CODEX_PS + "'python -m pytest -q'",
    _CODEX_PS + "'npm test'",
    _CODEX_PS + "'npm run build'",
    "/bin/bash -lc 'dotnet build'",
    "/bin/bash -lc 'dotnet test'",
    "/bin/bash -lc 'python -m pytest -q'",
    "/bin/bash -lc 'npm test'",
    "/bin/bash -lc 'npm run build'",
])
async def test_codex_balanced_wrapped_build_runs_without_a_card(balanced, ws, command):
    assert await _codex_drive(_codex(ws), "item/commandExecution/requestApproval",
                              {"command": command, "cwd": ws}) == ("accept", None)


@pytest.mark.parametrize("script", [
    # tree -o writes its listing into a protected path; -R reruns with -o.
    "tree -o .git/config",
    "tree -R",
    "tree -ao x",
    'tree "-o" x',
    "tree --output=x",
    "tree -oout.txt",
    # A glob can match a workspace junction that points outside.
    "cat lin*\\secret.txt",
    "ls lin*",
    "grep SECRET lin*\\*",
    "head l?nk\\x",
    "cat [l]ink\\x",
    "cat link\\..\\..\\x",
])
@pytest.mark.parametrize("wrap", [
    lambda s: "/bin/bash -lc '" + s + "'",
    lambda s: _CODEX_PS + "'" + s + "'",
], ids=["bash", "powershell"])
async def test_codex_balanced_inner_tree_write_or_glob_asks(balanced, ws, script, wrap):
    decision, ev = await _codex_drive(_codex(ws), "item/commandExecution/requestApproval",
                                      {"command": wrap(script), "cwd": ws})
    assert decision == "decline"
    assert ev is not None and ev["type"] == "command_approval_needed"


@pytest.mark.parametrize("command,reason", [
    (_CODEX_PS + "'Remove-Item -Recurse Assets'", "shell_delete_move"),
    (_CODEX_PS + "'powershell -Command dotnet build'", "shell_inline_code"),
    (_CODEX_PS + "'python -c \"print(1)\"'", "shell_inline_code"),
    (_CODEX_PS + "'iex x'", "shell_inline_code"),
    (_CODEX_PS + "'Invoke-Expression x'", "shell_inline_code"),
    (_CODEX_PS + "'dotnet build; Remove-Item x'", "shell_delete_move"),
    (_CODEX_PS + "'dotnet build & del x'", "shell_delete_move"),
    # Unparseable or unsure wrappers keep the raw string, which reads as inline code.
    (_CODEX_PS + "'dotnet build\"", "shell_inline_code"),
    (_CODEX_PS + "\"dotnet build \\\"x\\\"\"", "shell_inline_code"),
    ("cmd.exe /c dotnet build", "shell_inline_code"),
    # PowerShell reads a comma array and strips curly quotes; action_risk
    # would read one in-workspace name, so the raw wrapper is classified.
    (_CODEX_PS + "'cat inside.txt,..\\secret.txt'", "shell_inline_code"),
    (_CODEX_PS + "'ls Assets,\\\\host\\share'", "shell_inline_code"),
    (_CODEX_PS + "'cat \N{LEFT SINGLE QUOTATION MARK}..\\secret.txt"
                 "\N{RIGHT SINGLE QUOTATION MARK}'", "shell_inline_code"),
    (_CODEX_PS + "'cat \N{LATIN SMALL LETTER E WITH ACUTE}.txt'", "shell_inline_code"),
    # A shell outside the system locations may be a workspace binary.
    (".\\tools\\sh.exe -c 'ls'", "shell_inline_code"),
    ("C:\\Users\\x\\evil\\pwsh.exe -Command 'ls'", "shell_inline_code"),
    ("/tmp/bash -lc 'dotnet build'", "shell_metachar"),
    # A bare name resolves through cwd and PATH.
    ("powershell.exe -Command 'dotnet build'", "shell_inline_code"),
    ("pwsh -Command 'dotnet build'", "shell_inline_code"),
    ("bash -lc 'dotnet build'", "shell_metachar"),
    ('"C:\\Program Files\\PowerShell\\powershell.exe" -Command \'dotnet build\'',
     "shell_inline_code"),
    # PowerShell strips these quotes, so the read leaves the workspace.
    (_CODEX_PS + "\"cat '..\\secret.txt'\"", "shell_inline_code"),
    ("/bin/bash -lc 'cat {..,x}/secret'", "shell_metachar"),
])
async def test_codex_balanced_wrapped_risky_command_asks(balanced, ws, command, reason):
    decision, ev = await _codex_drive(_codex(ws), "item/commandExecution/requestApproval",
                                      {"command": command, "cwd": ws})
    assert decision == "decline"
    assert ev is not None and ev["risk_reason"] == reason


async def test_codex_side_session_declines_before_balanced(balanced, ws):
    s = _codex(ws, read_only=True)
    assert await _codex_drive(s, "item/commandExecution/requestApproval",
                              {"command": "npm test"}) == ("decline", None)


async def test_codex_structured_question_continues_in_balanced(balanced):
    from providers.codex_session import CodexSession

    s = CodexSession(6)
    s._send = AsyncMock()
    await s._handle_server_request({"id": 1, "method": "item/tool/requestUserInput",
                                    "params": {}})
    value = s._send.await_args.args[0]["result"]["value"]
    assert value.startswith("Proceed")


# ── agy hook (agy_step_gate.decide) ──────────────────────────────────────────

LAUNCHER = "C:\\Gamachine\\unityai.cmd"


@pytest.fixture
def agy_ws(ws, tmp_path_factory):
    import agy_step_gate as gate

    agents = os.path.join(ws, ".agents")
    os.makedirs(agents)
    with open(os.path.join(agents, "hooks.json"), "w", encoding="utf-8") as f:
        json.dump({gate.STEP_GATE_KEY: {"PreToolUse": []}}, f)
    state_dir = tmp_path_factory.mktemp("state")
    return ws, str(state_dir / "step-gate.json")


def _decide(state, mode, payload, cwd):
    import agy_step_gate as gate

    gate.write_state(state, mode, LAUNCHER)
    return gate.decide(json.dumps({"toolCall": payload}).encode(), state, windows=True, cwd=cwd)


def _run(command, cwd=None):
    args = {"CommandLine": command}
    if cwd:
        args["Cwd"] = cwd
    return {"name": "run_command", "args": args}


def _write(path):
    return {"name": "write_to_file", "args": {"TargetFile": path, "CodeContent": "x"}}


def test_agy_balanced_allows_routine_calls(agy_ws):
    ws, state = agy_ws
    for payload in (_run("npm test", ws), _run("git status"), _run("dotnet build -c Release", ws),
                    _write(os.path.join(ws, "Assets", "A.cs"))):
        assert _decide(state, "balanced", payload, ws)["decision"] == "allow", payload


CRITICAL_AGY_CALLS = [
    _run("Remove-Item Assets/A.cs"),
    _run("ri Assets/A.cs"),
    _run("npm install x"),
    _run("git push --force"),
    _run("powershell -EncodedCommand ZQA="),
    _run("npm test; rm x"),
    _run("whoami"),
    _write(".git/config"),
    _write("ProjectSettings/ProjectSettings.asset"),
    _write(".agents/hooks.json"),
    _write("../outside.cs"),
    {"name": "send_command_input", "args": {"Input": "y"}},
    {"name": "notebook_edit", "args": {}},
    {"name": "write_to_file", "args": {}},
]


@pytest.mark.parametrize("payload", CRITICAL_AGY_CALLS)
def test_agy_balanced_denies_critical_calls_and_points_to_the_bridge(agy_ws, payload):
    ws, state = agy_ws
    out = _decide(state, "balanced", payload, ws)
    assert out["decision"] == "deny"
    assert "unityai" in out["reason"]


@pytest.mark.parametrize("payload", CRITICAL_AGY_CALLS)
def test_agy_balanced_is_never_looser_than_step_for_critical_calls(agy_ws, payload):
    ws, state = agy_ws
    assert _decide(state, "step", payload, ws)["decision"] == "deny"
    assert _decide(state, "balanced", payload, ws)["decision"] == "deny"


def test_agy_balanced_still_allows_the_bridge_call_shapes(agy_ws):
    ws, state = agy_ws
    bridge = _run(f'& "{LAUNCHER}" bash --command "git status"')
    assert _decide(state, "step", bridge, ws)["decision"] == "allow"
    assert _decide(state, "balanced", bridge, ws)["decision"] == "allow"


def test_agy_balanced_without_its_hooks_file_allows_nothing_it_must_confine(ws, tmp_path_factory):
    state = str(tmp_path_factory.mktemp("s") / "step-gate.json")
    assert _decide(state, "balanced", _run("npm test", ws), ws)["decision"] == "deny"
    assert _decide(state, "balanced", _write(os.path.join(ws, "Assets", "A.cs")), ws)["decision"] == "deny"


def test_agy_balanced_applies_the_unity_file_rule_first(agy_ws):
    ws, state = agy_ws
    out = _decide(state, "balanced", _write(os.path.join(ws, "Assets", "A.cs.meta")), ws)
    assert out["decision"] == "deny" and ".meta" in out["reason"]


def test_agy_cwd_outside_the_workspace_is_critical(agy_ws, tmp_path_factory):
    ws, state = agy_ws
    elsewhere = str(tmp_path_factory.mktemp("elsewhere"))
    assert _decide(state, "balanced", _run("npm test", elsewhere), ws)["decision"] == "deny"
