"""The direct Claude CLI provider loads only the product's own MCP servers.

Background jobs (conversation compaction, architecture analysis, memory-import
security analysis, the code-analysis route) run through `ClaudeCodeProvider`
under bypassPermissions. Its command had no --strict-mcp-config, so the CLI
also loaded every MCP server of the owner's own Claude Code (measured 26 Sep
2026: about 60, Gmail, Drive, Vercel and a second Unity server "UnityMCP").
It also wrote the product's servers into the owner's user scope on every call.

The chat path got the same fix in 95b5e81 (test_claude_strict_mcp.py).
"""
import asyncio
import json
import sys

import pytest

import unity_ai_mcp.unity_mcp_manager as um
from providers import claude_sdk_session
from providers.cli_base import BaseCLIProvider, maskeli_cmd
from providers.claude_provider import ClaudeCodeProvider
from secret_redaction import redact_secrets

SECRET = "k-7f3c9a1e5d2b8c4a6e0f"
UNITY_URL = "http://localhost:8080/mcp"


def _unity(monkeypatch, running: bool):
    monkeypatch.setattr(um.unity_mcp_manager, "is_running", lambda: running)
    monkeypatch.setattr(um.unity_mcp_manager, "mcp_url",
                        lambda *a, **k: UNITY_URL if running else None)
    monkeypatch.setattr(um.unity_mcp_manager, "api_headers",
                        lambda: {"X-API-Key": SECRET} if running else {})


class _Spawned(Exception):
    pass


class _Recorder:
    """Stands in for subprocess.run and keeps every argv it was given."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **_kw):
        self.calls.append([str(c) for c in cmd])

        class _R:
            returncode = 0
            stdout = b""
            stderr = b""
        return _R()


def _spawn_argv(monkeypatch, tmp_path, running: bool):
    """Runs the real `analyze_code` up to the spawn and returns (argv, run calls).

    Nothing is started: `subprocess.run` records, and the async spawn raises.
    """
    import subprocess
    from providers import cli_base

    _unity(monkeypatch, running)
    monkeypatch.setenv("UNITYAI_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(ClaudeCodeProvider, "_stale_user_scope_cleaned", False)
    monkeypatch.setattr(BaseCLIProvider, "_cli_installed", staticmethod(lambda name: True))
    monkeypatch.setattr(ClaudeCodeProvider, "_resolve_exec", staticmethod(lambda cmd: list(cmd)))
    runs = _Recorder()
    monkeypatch.setattr(subprocess, "run", runs)
    spawned = []

    async def fake_exec(*argv, **_kw):
        spawned.append(list(argv))
        raise _Spawned()

    monkeypatch.setattr(cli_base.asyncio, "create_subprocess_exec", fake_exec)

    async def drive():
        provider = ClaudeCodeProvider("claude-opus-5")
        return [e async for e in provider.analyze_code("summarize this", cwd=str(tmp_path))]

    events = asyncio.run(drive())
    assert spawned, f"the CLI was never spawned: {events}"
    # Writing .mcp.json also runs icacls/git; only the claude calls matter.
    return spawned[0], [c for c in runs.calls if c[:1] == ["claude"]]


def _mcp_config(argv):
    assert argv.count("--mcp-config") == 1, argv
    return json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]


def test_the_spawned_command_is_strict_with_only_unityai_when_unity_is_off(monkeypatch, tmp_path):
    argv, _ = _spawn_argv(monkeypatch, tmp_path, running=False)
    assert "--strict-mcp-config" in argv
    servers = _mcp_config(argv)
    assert set(servers) == {"unityai"}
    assert servers["unityai"] == {
        "command": ClaudeCodeProvider("claude-opus-5")._launcher_path("run_mcp_server"),
        "args": ["--workspace", str(tmp_path)],
        "env": {"UNITYAI_URL": "http://127.0.0.1:8123"},
    }


def test_the_spawned_command_adds_unitymcp_over_http_when_unity_runs(monkeypatch, tmp_path):
    argv, _ = _spawn_argv(monkeypatch, tmp_path, running=True)
    assert "--strict-mcp-config" in argv
    servers = _mcp_config(argv)
    assert set(servers) == {"unityai", "unityMCP"}
    assert servers["unityMCP"] == {"type": "http", "url": UNITY_URL,
                                   "headers": {"X-API-Key": SECRET}}


def test_the_rest_of_the_command_is_unchanged(monkeypatch, tmp_path):
    argv, _ = _spawn_argv(monkeypatch, tmp_path, running=True)
    i = argv.index("--disallowedTools")
    from providers.unity_script_tools import DISALLOWED_UNITY_TOOLS
    assert argv[i + 1] == ",".join(("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
                                    *DISALLOWED_UNITY_TOOLS))
    assert argv[:2] == ["claude", "--model"]
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert argv[-1] == "-p", "the prompt must stay on stdin, not in argv"
    assert "summarize this" not in " ".join(argv)


def test_no_server_is_added_to_the_owners_user_scope(monkeypatch, tmp_path):
    _, runs = _spawn_argv(monkeypatch, tmp_path, running=True)
    assert not [c for c in runs if "add" in c or "add-json" in c], runs
    # Only the product's two stale names, exact case: the owner's own
    # "UnityMCP" must survive.
    assert [c[-3:] for c in runs] == [["unityai", "--scope", "user"],
                                      ["unityMCP", "--scope", "user"]]
    assert all(c[:3] == ["claude", "mcp", "remove"] for c in runs)


def test_the_stale_entry_cleanup_runs_once_per_process(monkeypatch, tmp_path):
    _, first = _spawn_argv(monkeypatch, tmp_path, running=False)
    assert len(first) == 2
    import subprocess
    again = _Recorder()
    monkeypatch.setattr(subprocess, "run", again)
    ClaudeCodeProvider("claude-opus-5")._register_mcp("launcher", str(tmp_path), "http://x")
    assert again.calls == []


def test_the_inline_unityai_entry_matches_the_workspace_mcp_json(monkeypatch, tmp_path):
    """Two places build the unityai entry; they must not drift apart."""
    _unity(monkeypatch, False)
    monkeypatch.setenv("UNITYAI_URL", "http://127.0.0.1:8123")
    provider = ClaudeCodeProvider("claude-opus-5")
    monkeypatch.setattr(provider, "_register_mcp", lambda *a, **k: None)
    with open(provider._write_mcp_config(str(tmp_path)), encoding="utf-8") as fh:
        written = json.load(fh)["mcpServers"]["unityai"]
    assert provider._product_mcp_servers(str(tmp_path))["unityai"] == written


def test_the_api_key_is_masked_in_the_logged_command(monkeypatch, tmp_path):
    _unity(monkeypatch, True)
    cmd = ClaudeCodeProvider("claude-opus-5")._build_cmd("hi there", workspace=str(tmp_path))
    assert SECRET in " ".join(cmd), "precondition: the key is in argv"
    assert SECRET not in redact_secrets(maskeli_cmd(cmd, "hi there"))


class TestWindowsShim:
    """The JSON must never reach cmd.exe, which re-parses batch arguments."""

    def test_the_exe_behind_the_npm_shim_is_spawned(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(claude_sdk_session, "claude_ikilisini_coz",
                            lambda: r"C:\npm\claude-code\bin\claude.exe")
        out = ClaudeCodeProvider._resolve_exec(["claude", "--mcp-config", "{}", "-p"])
        assert out == [r"C:\npm\claude-code\bin\claude.exe", "--mcp-config", "{}", "-p"]

    def test_a_bare_shim_is_refused_rather_than_fed_the_json(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(claude_sdk_session, "claude_ikilisini_coz", lambda: None)
        monkeypatch.setattr(BaseCLIProvider, "_resolve_exec",
                            staticmethod(lambda cmd: ["cmd", "/c", r"C:\npm\claude.cmd", *cmd[1:]]))
        with pytest.raises(RuntimeError):
            ClaudeCodeProvider._resolve_exec(["claude", "--mcp-config", "{}", "-p"])
        # Fixed arguments without JSON may still go through the shim.
        assert ClaudeCodeProvider._resolve_exec(["claude", "mcp", "remove", "unityai"])[:2] == ["cmd", "/c"]
