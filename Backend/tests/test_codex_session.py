"""Codex app-server oturumu ve onay protokolü regresyon testleri."""
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from providers.codex_session import (
    CodexSession,
    _APP_SERVER_STREAM_LIMIT,
    _trusted_mcp_config,
)


class TestProtocolLimits(unittest.TestCase):
    def test_stream_limit_handles_large_unity_tool_schemas(self):
        self.assertGreater(_APP_SERVER_STREAM_LIMIT, 64 * 1024)
        self.assertLessEqual(_APP_SERVER_STREAM_LIMIT, 8 * 1024 * 1024)


class TestTrustedMcpConfig(unittest.TestCase):
    def _manager_modules(self, running: bool):
        manager_module = MagicMock()
        manager_module.unity_mcp_manager.is_running.return_value = running
        package = MagicMock()
        package.unity_mcp_manager = manager_module.unity_mcp_manager
        return patch.dict(
            sys.modules,
            {
                "unity_ai_mcp": package,
                "unity_ai_mcp.unity_mcp_manager": manager_module,
            },
        )

    def test_unityai_is_approved_at_codex_layer(self):
        with self._manager_modules(running=False), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityai"},
        ):
            config = _trusted_mcp_config()

        self.assertEqual(
            config["mcp_servers"]["unityai"]["default_tools_approval_mode"],
            "approve",
        )
        self.assertNotIn("unityMCP", config["mcp_servers"])

    def test_running_unity_mcp_is_approved_at_codex_layer(self):
        with self._manager_modules(running=True), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityai", "unityMCP"},
        ):
            config = _trusted_mcp_config()

        self.assertEqual(
            config["mcp_servers"]["unityMCP"]["default_tools_approval_mode"],
            "approve",
        )

    def test_unregistered_unity_mcp_does_not_break_thread_config(self):
        with self._manager_modules(running=True), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityai"},
        ):
            config = _trusted_mcp_config()

        self.assertNotIn("unityMCP", config["mcp_servers"])

    def test_forwarded_env_goes_to_registered_servers_only(self):
        # unityMCP registered but the server is down: it still gets the name,
        # so a bridge that connects later names the chat too.
        with self._manager_modules(running=False), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityMCP", "someone_elses"},
        ):
            config = _trusted_mcp_config(forward_env=("GAMACHINE_CONVERSATION_ID",))

        self.assertEqual(config["mcp_servers"], {
            "unityMCP": {"env_vars": ["GAMACHINE_CONVERSATION_ID"]},
        })

    def test_no_forwarded_env_adds_no_env_vars(self):
        with self._manager_modules(running=True), patch(
            "providers.codex_session._configured_codex_mcp_names",
            return_value={"unityai", "unityMCP"},
        ):
            config = _trusted_mcp_config()

        for entry in config["mcp_servers"].values():
            self.assertNotIn("env_vars", entry)


class TestCodexSessionNamesItsChat(unittest.IsolatedAsyncioTestCase):
    """Codex hands a stdio MCP child only its default env + `env` + `env_vars`
    (measured, 0.157.0), so the id must be in the app-server's env AND named in
    the thread config, or neither bridge sees it."""

    async def _start(self, conversation_id, **session_kwargs):
        from providers import codex_session as cs

        spawned = {}
        requests = []

        async def fake_spawn(*argv, **kwargs):
            spawned.update(argv=argv, env=kwargs["env"])
            return MagicMock()

        async def fake_request(method, params=None, timeout=None):
            requests.append((method, params))
            if method == "thread/start":
                return {"result": {"thread": {"id": "t1"}, "approvalsReviewer": "user"}}
            return {"result": {}}

        async def no_read_loop():
            return None

        manager = MagicMock()
        manager.unity_mcp_manager.is_running.return_value = True
        session = CodexSession(conversation_id, **session_kwargs)
        with patch.object(cs.asyncio, "create_subprocess_exec", side_effect=fake_spawn), \
                patch.object(session, "_request", side_effect=fake_request), \
                patch.object(session, "_notify", AsyncMock()), \
                patch.object(session, "_read_loop", side_effect=no_read_loop), \
                patch.object(cs, "_configured_codex_mcp_names",
                             return_value={"unityai", "unityMCP"}), \
                patch.dict(sys.modules, {"unity_ai_mcp.unity_mcp_manager": manager}), \
                patch.dict(os.environ, {"GAMACHINE_CONVERSATION_ID": "999"}):
            await session.start()
        self.thread_start = dict(requests)["thread/start"]
        return spawned["env"], self.thread_start["config"]

    async def test_a_chat_session_passes_its_id_to_both_servers(self):
        env, config = await self._start(7)
        self.assertEqual(env["GAMACHINE_CONVERSATION_ID"], "7")
        for name in ("unityai", "unityMCP"):
            self.assertEqual(config["mcp_servers"][name], {
                "default_tools_approval_mode": "approve",
                "env_vars": ["GAMACHINE_CONVERSATION_ID"],
            })

    async def test_throwaway_ids_name_no_chat(self):
        # The backend's own env must not leak through either (999 is set).
        for conversation_id in (0, -3):
            env, config = await self._start(conversation_id)
            self.assertNotIn("GAMACHINE_CONVERSATION_ID", env)
            for entry in config["mcp_servers"].values():
                self.assertNotIn("env_vars", entry)

    async def test_chat_thread_asks_before_every_untrusted_command(self):
        # on-request + read-only left asking to the model, so a failed sandboxed
        # write never reached the step-mode card (owner test, 1 Oct 2026).
        await self._start(7)
        self.assertEqual(self.thread_start["approvalPolicy"], "untrusted")
        self.assertEqual(self.thread_start["sandbox"], "workspace-write")
        self.assertEqual(self.thread_start["approvalsReviewer"], "user")

    async def test_side_chat_thread_keeps_read_only_sandbox(self):
        await self._start(7, read_only=True)
        self.assertEqual(self.thread_start["approvalPolicy"], "on-request")
        self.assertEqual(self.thread_start["sandbox"], "read-only")
        self.assertEqual(self.thread_start["approvalsReviewer"], "user")


class TestCodexApprovalResponses(unittest.IsolatedAsyncioTestCase):
    async def test_client_request_omits_jsonrpc_wire_field(self):
        session = CodexSession(0)
        sent = []

        async def fake_send(message):
            sent.append(message)
            session._pending[message["id"]].set_result({
                "id": message["id"],
                "result": {"ok": True},
            })

        session._send = fake_send

        response = await session._request("config/read", {})

        self.assertEqual(response["result"], {"ok": True})
        self.assertNotIn("jsonrpc", sent[0])
        self.assertEqual(sent[0]["method"], "config/read")

    async def test_client_notification_omits_jsonrpc_wire_field(self):
        session = CodexSession(0)
        session._send = AsyncMock()

        await session._notify("initialized")

        session._send.assert_awaited_once_with({"method": "initialized"})

    async def test_permissions_approval_uses_current_appserver_schema(self):
        session = CodexSession(1)
        requested = {
            "fileSystem": {"read": ["/project"]},
            "network": {"enabled": False},
        }
        session._resolve_approval = AsyncMock(return_value="accept")
        session._send = AsyncMock()

        await session._handle_server_request({
            "id": 7,
            "method": "item/permissions/requestApproval",
            "params": {"permissions": requested, "reason": "MCP tool call"},
        })

        session._send.assert_awaited_once_with({
            "id": 7,
            "result": {
                "permissions": requested,
                "scope": "turn",
            },
        })

    async def test_permissions_rejection_grants_nothing(self):
        session = CodexSession(2)
        session._resolve_approval = AsyncMock(return_value="decline")
        session._send = AsyncMock()

        await session._handle_server_request({
            "id": 8,
            "method": "item/permissions/requestApproval",
            "params": {"permissions": {"network": {"enabled": True}}},
        })

        session._send.assert_awaited_once_with({
            "id": 8,
            "result": {
                "permissions": {},
                "scope": "turn",
            },
        })

    async def test_command_approval_keeps_decision_response(self):
        session = CodexSession(3)
        session._resolve_approval = AsyncMock(return_value="accept")
        session._send = AsyncMock()

        await session._handle_server_request({
            "id": 9,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "git status"},
        })

        session._send.assert_awaited_once_with({
            "id": 9,
            "result": {"decision": "accept"},
        })

    async def test_auto_mode_approval_never_creates_a_ui_gate(self):
        from agentic import approval_mode
        approval_mode.set_mode("auto", source="test")
        session = CodexSession(4, auto_approve=True)
        session._out_q = __import__("asyncio").Queue()

        decision = await session._resolve_approval(
            "item/commandExecution/requestApproval",
            {"command": "touch Assets/test.txt"},
        )

        self.assertEqual(decision, "accept")
        self.assertTrue(session._out_q.empty())

    async def test_auto_mode_structured_question_continues_without_prompting_user(self):
        from agentic import approval_mode
        approval_mode.set_mode("auto", source="test")
        session = CodexSession(5, auto_approve=True)
        session._send = AsyncMock()

        await session._handle_server_request({
            "id": 10,
            "method": "item/tool/requestUserInput",
            "params": {"question": "Should I continue?"},
        })

        session._send.assert_awaited_once_with({
            "id": 10,
            "result": {
                "value": (
                    "Proceed using your best judgment without asking for confirmation."
                ),
            },
        })


# ── Step mode under "untrusted": reads pass, mutations card ─────────────────

import asyncio  # noqa: E402

import pytest  # noqa: E402

from providers.codex_session import _is_read_only_command  # noqa: E402

# The wire shape measured with codex-cli 0.157.0 (probe log, 1 Oct 2026).
_PS = '"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '
_CMD = "item/commandExecution/requestApproval"


@pytest.fixture
def ws(tmp_path):
    for d in ("Assets", "ProjectSettings"):
        (tmp_path / d).mkdir()
    return str(tmp_path)


@pytest.mark.parametrize("command", [
    _PS + "'Get-ChildItem'",
    _PS + "'Get-ChildItem -Name'",
    _PS + "'Get-ChildItem -File -Name'",
    _PS + "'gci -Recurse -Force Assets'",
    _PS + "'Get-ChildItem -Path Assets -Depth 2'",
    _PS + "'Get-Content a.txt'",
    _PS + "'Get-Content Assets\\A.cs'",
    _PS + "'Get-Content -Path Assets/A.cs'",
    _PS + "'Get-Content -LiteralPath Assets\\A.cs'",
    _PS + "'Get-Content -Raw -Encoding UTF8 Assets\\A.cs'",
    _PS + "'Get-Content -TotalCount 20 Assets\\A.cs'",
    _PS + "'Select-String -Pattern x -Path a.txt'",
    _PS + "'Select-String -Pattern Player -Path Assets\\A.cs'",
    _PS + "'rg pattern'",
    _PS + "'rg -n pattern src'",
    _PS + "'rg -n Player Assets'",
    _PS + "'rg --files'",
    _PS + "'ls'",
    _PS + "'cat a.txt'",
    _PS + "'Get-Location'",
    _PS + "'pwd'",
    "/bin/bash -lc 'ls'",
    "/bin/bash -lc 'ls -la Assets'",
    "/bin/bash -lc 'cat a.txt'",
    "/bin/bash -lc 'pwd'",
    "/bin/zsh -lc 'grep -rn Player Assets'",
    "/bin/bash -lc 'rg -n pattern src'",
    "/bin/bash -lc 'tree -L 2 -a Assets'",
    "/bin/bash -lc 'tree -d Assets'",
    "/bin/bash -lc 'find Assets -name A.cs -type f'",
    "/bin/bash -lc 'head -n 5 Assets/A.cs'",
    "/bin/bash -lc 'wc -l Assets/A.cs'",
    ["ls", "Assets"],
])
def test_read_only_command_is_recognised(ws, command):
    assert _is_read_only_command(command, ws, ws) is True


@pytest.mark.parametrize("command", [
    _PS + "\"Set-Content -LiteralPath 'probe.txt' -Value 'hello'\"",
    _PS + "'Remove-Item probe.txt'",
    _PS + "'Get-ChildItem > out.txt'",
    _PS + "'Get-ChildItem | Out-File x.txt'",
    _PS + "'Get-Content A.cs; Remove-Item A.cs'",
    _PS + "'Get-Content $env:TOKEN'",
    _PS + "'Get-Content Env:TOKEN'",
    _PS + "'Get-Content ..\\secret.txt'",
    _PS + "\"Get-Content '..\\secret.txt'\"",
    _PS + "'Get-Content .*\\secret.txt'",
    _PS + "'Get-ChildItem C:\\'",
    _PS + "'Get-ChildItem \\Windows'",
    _PS + "'Get-ChildItem /etc'",
    _PS + "'cat ~\\.ssh\\id_rsa'",
    _PS + "'git status'",
    _PS + "'echo hi'",
    _PS + "'Get-Location Assets'",
    _PS + "'head -n 5 A.cs'",
    "/bin/bash -lc 'cat {..,x}/secret'",
    "/bin/bash -lc 'find . -delete'",
    "/bin/bash -lc 'Get-ChildItem'",
    "cmd.exe /c dir",
    "Get-ChildItem",
    "",
    None,
])
def test_anything_else_is_not_read_only(ws, command):
    assert _is_read_only_command(command, ws, ws) is False


# command_safety reads `-Path..\x` as one flag; PowerShell binds `..\x` to
# -Path and reads outside the workspace (measured, finding #1).
@pytest.mark.parametrize("command", [
    _PS + "'Get-Content -Path..\\top.txt'",
    _PS + "'cat -Path..\\top.txt'",
    _PS + "'gc -LiteralPath..\\x'",
    _PS + "'Get-ChildItem -Path..\\'",
    _PS + "'Select-String -Pattern a -Path..\\top.txt'",
    _PS + "'Get-Content -Path..\\/./top.txt'",
    _PS + "'Get-Content -Path:..\\top.txt'",
    _PS + "'Get-Content -Path=a.txt'",
    _PS + "'Get-Content -Pa a.txt'",
    _PS + "'Get-Content -Path'",
    _PS + "'Get-Content -TotalCount x a.txt'",
    _PS + "'Get-Content -TotalCount5 a.txt'",
    _PS + "'Get-Content -Encoding x a.txt'",
    _PS + "'Get-Content -Wait a.txt'",
    _PS + "'Get-Content a..b'",
    _PS + "'Get-Content Assets\\..\\..\\top.txt'",
    _PS + "'Select-String -Pattern -x a.txt'",
    _PS + "'rg -e -x'",
    "/bin/bash -lc 'cat x/\\.\\./\\.\\./top.txt'",
    "/bin/bash -lc 'cat \\x'",
    "/bin/bash -lc 'head -n5 a.txt'",
    "/bin/bash -lc 'head -n ../x'",
    "/bin/bash -lc 'ls -lX'",
    "/bin/bash -lc 'grep -R x Assets'",
    "/bin/bash -lc 'find Assets -type l'",
])
def test_an_attached_or_unknown_option_is_not_read_only(ws, command):
    assert _is_read_only_command(command, ws, ws) is False


def test_an_unresolvable_path_is_not_read_only(ws):
    # realpath raised WinError 267 out of the shortcut (1 Oct 2026).
    open(os.path.join(os.path.dirname(ws), "top.txt"), "w").close()
    assert _is_read_only_command(_PS + "'cat ..\\top.txt/....'", ws, ws) is False


def test_an_os_error_in_the_check_is_not_read_only(ws, monkeypatch):
    from agentic import command_safety

    def boom(*a, **k):
        raise OSError(267, "The directory name is invalid")
    monkeypatch.setattr(command_safety, "is_auto_safe", boom)
    assert _is_read_only_command(_PS + "'Get-ChildItem'", ws, ws) is False


# The shortcut has no card behind it, so the inner script must be plain text
# that command_safety reads the way the shell does.
@pytest.mark.parametrize("command", [
    # PowerShell reads both elements of a comma array.
    _PS + "'Get-Content inside.txt,..\\secret.txt'",
    _PS + "'Get-ChildItem Assets,\\\\host\\share'",
    # PowerShell treats typographic quotes as quotes and strips them.
    _PS + "'Get-Content \N{LEFT SINGLE QUOTATION MARK}..\\secret.txt\N{RIGHT SINGLE QUOTATION MARK}'",
    _PS + "'Get-Content \N{LEFT DOUBLE QUOTATION MARK}..\\secret.txt\N{RIGHT DOUBLE QUOTATION MARK}'",
    # A glob can match a workspace junction or symlink that points outside;
    # command_safety checks the literal pattern.
    _PS + "'Get-Content */secret.txt'",
    _PS + "'Get-Content linked*/SPEC.md'",
    _PS + "'Get-Content Assets/A?.cs'",
    _PS + "'gci -Recurse -Filter *.cs Assets'",
    "/bin/bash -lc 'cat linked*/SPEC.md'",
    "/bin/bash -lc 'cat Assets/[A]/x'",
    _PS + "'Get-Content A.cs #x'",
    _PS + "'Get-Content +A.cs'",
    _PS + "'Get-Content A.cs ~'",
    _PS + "'Get-Content \"A.cs\"'",
    _PS + "'Get-Content \N{LATIN SMALL LETTER E WITH ACUTE}.cs'",
])
def test_a_script_with_anything_but_plain_characters_is_not_read_only(ws, command):
    assert _is_read_only_command(command, ws, ws) is False


@pytest.mark.parametrize("command", [
    # tree -o writes the listing to a file; -R reruns tree with -o 00Tree.html.
    "/bin/bash -lc 'tree -o .git/hooks/pre-commit'",
    "/bin/bash -lc 'tree -o=out.txt'",
    "/bin/bash -lc 'tree -ao out.txt'",
    "/bin/bash -lc 'tree -oout.txt'",
    "/bin/bash -lc 'tree --output out.txt'",
    "/bin/bash -lc 'tree \"-o\" out.txt'",
    "/bin/bash -lc 'tree -R -L 1'",
    _PS + "'tree -o out.txt'",
    # find predicates that write or run a program.
    "/bin/bash -lc 'find . -fprint out.txt'",
    "/bin/bash -lc 'find . -fprint0 out.txt'",
    "/bin/bash -lc 'find . -fprintf out.txt x'",
    "/bin/bash -lc 'find . -fls out.txt'",
    "/bin/bash -lc 'find . -ok rm'",
    "/bin/bash -lc 'find . -okdir rm'",
    "/bin/bash -lc 'find . -exec rm'",
    "/bin/bash -lc 'find . -execdir rm'",
    "/bin/bash -lc 'find . \"-delete\"'",
    # ripgrep flags that start a program, also when quoted.
    _PS + "'rg --pre sh Player'",
    _PS + "'rg -nz Player'",
    _PS + "'rg \"--pre\" sh Player'",
    "/bin/bash -lc 'rg \"--pre=sh\" Player'",
    "/bin/bash -lc 'rg -\"z\" Player'",
    "/bin/bash -lc 'rg --search-zip Player'",
    "/bin/bash -lc 'rg --hostname-bin=x Player'",
])
def test_a_read_verb_with_a_write_or_exec_option_is_not_read_only(ws, command):
    assert _is_read_only_command(command, ws, ws) is False


@pytest.fixture
def system_dirs(monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", "C:\\WINDOWS")
    monkeypatch.setenv("ProgramFiles", "C:\\Program Files")
    monkeypatch.delenv("ProgramW6432", raising=False)


@pytest.mark.parametrize("command", [
    _PS + "'Get-ChildItem'",
    '"c:\\windows\\system32\\windowspowershell\\v1.0\\POWERSHELL.EXE" -Command \'Get-ChildItem\'',
    "C:/Windows/SysWOW64/WindowsPowerShell/v1.0/powershell.exe -Command 'Get-ChildItem'",
    '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -Command \'Get-ChildItem\'',
    "powershell -Command 'Get-ChildItem'",
    "pwsh.exe -Command 'Get-ChildItem'",
    "bash -lc 'ls'",
    "/usr/bin/bash -lc 'ls'",
    "/usr/local/bin/zsh -lc 'ls'",
    "/opt/homebrew/bin/bash -lc 'ls'",
    "/bin/sh -c 'ls'",
])
def test_a_system_shell_is_unwrapped(ws, system_dirs, command):
    assert _is_read_only_command(command, ws, ws) is True


@pytest.mark.parametrize("command", [
    # A shell outside the system locations may be a workspace binary.
    ".\\tools\\sh.exe -c 'ls'",
    "tools/bash -lc 'ls'",
    "./bash -lc 'ls'",
    "C:\\Users\\x\\evil\\pwsh.exe -Command 'Get-ChildItem'",
    '"C:\\Windows\\Temp\\powershell.exe" -Command \'Get-ChildItem\'',
    '"C:\\Windows\\System32\\spool\\drivers\\color\\powershell.exe" -Command \'Get-ChildItem\'',
    '"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\..\\..\\..\\Temp\\powershell.exe"'
    " -Command 'Get-ChildItem'",
    '"\\\\host\\share\\powershell.exe" -Command \'Get-ChildItem\'',
    '"C:\\Program Files\\Evil\\pwsh.exe" -Command \'Get-ChildItem\'',
    "/tmp/bash -lc 'ls'",
    "/usr/bin/../../tmp/bash -lc 'ls'",
    "/home/x/bin/zsh -lc 'ls'",
    # Right directory, wrong program.
    '"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\bash.exe" -lc \'ls\'',
    "/usr/bin/python -c 'ls'",
])
def test_a_shell_outside_the_system_locations_is_not_unwrapped(ws, system_dirs, command):
    assert _is_read_only_command(command, ws, ws) is False


def test_the_windows_directory_comes_from_the_environment(ws, monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", "D:\\Win")
    shell = "D:\\Win\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -Command 'Get-ChildItem'"
    assert _is_read_only_command(shell, ws, ws) is True
    assert _is_read_only_command(_PS + "'Get-ChildItem'", ws, ws) is False


@pytest.mark.parametrize("name", ["powershell.exe", "bash"])
def test_a_bare_shell_name_shadowed_in_cwd_is_not_unwrapped(ws, name):
    open(os.path.join(ws, name), "w").close()
    shell = name.removesuffix(".exe")
    flag = "-Command 'Get-ChildItem'" if shell == "powershell" else "-lc 'ls'"
    assert _is_read_only_command(f"{shell} {flag}", ws, ws) is False


def test_a_read_from_outside_the_workspace_is_not_read_only(ws, tmp_path_factory):
    outside = str(tmp_path_factory.mktemp("outside"))
    assert _is_read_only_command(_PS + "'Get-ChildItem'", outside, ws) is False
    assert _is_read_only_command(_PS + "'Get-ChildItem'", ws, "") is False


def _session(ws, **kw):
    s = CodexSession(kw.pop("cid", 11), cwd=ws, **kw)
    s._out_q = asyncio.Queue()
    s.approval_timeout = 2.0
    return s


async def _drive(session, method, params, answer=False):
    from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS

    task = asyncio.create_task(session._resolve_approval(method, params))
    try:
        ev = await asyncio.wait_for(session._out_q.get(), timeout=0.3)
    except asyncio.TimeoutError:
        ev = None
    if ev is not None and ev.get("type") == "command_approval_needed":
        APPROVAL_RESULTS[ev["gate_id"]] = answer
        APPROVAL_GATES[ev["gate_id"]].set()
    return await asyncio.wait_for(task, timeout=2), ev


async def test_step_mode_cards_a_write_request(ws):
    from agentic import approval_mode
    assert approval_mode.current_mode() == "step"
    params = {"command": _PS + "\"Set-Content -LiteralPath 'probe.txt' -Value 'hello'\"",
              "cwd": ws}
    decision, ev = await _drive(_session(ws), _CMD, params, answer=True)
    assert ev is not None and ev["type"] == "command_approval_needed"
    assert decision == "accept"


async def test_step_mode_accepts_a_read_only_command_without_a_card(ws):
    decision, ev = await _drive(_session(ws), _CMD,
                                {"command": _PS + "'Get-ChildItem -File -Name'", "cwd": ws})
    assert (decision, ev) == ("accept", None)


async def test_step_mode_cards_a_read_outside_the_workspace(ws, tmp_path_factory):
    outside = str(tmp_path_factory.mktemp("outside"))
    decision, ev = await _drive(_session(ws), _CMD,
                                {"command": _PS + "'Get-ChildItem'", "cwd": outside})
    assert ev is not None and ev["type"] == "command_approval_needed"
    assert decision == "decline"


async def test_auto_mode_accepts_a_write_request_without_a_card(ws):
    from agentic import approval_mode
    approval_mode.set_mode("auto", source="test")
    params = {"command": _PS + "'Remove-Item probe.txt'", "cwd": ws}
    assert await _drive(_session(ws), _CMD, params) == ("accept", None)


async def test_side_chat_still_declines_a_read_only_command(ws):
    from agentic import approval_mode
    approval_mode.set_mode("auto", source="test")
    params = {"command": _PS + "'Get-ChildItem'", "cwd": ws}
    assert await _drive(_session(ws, read_only=True), _CMD, params) == ("decline", None)
