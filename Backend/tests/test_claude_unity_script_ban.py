"""Claude must not reach any unityMCP tool that writes a .cs file.

The ban named only `manage_script` until the 26 Sep 2026 audit (Codex strictmcp)
showed `apply_text_edits`, `create_script`, `delete_script` and a nested
`batch_execute` reaching the same writes; `script_apply_edits` was a fifth.
"""
import asyncio
import json
import os
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from providers.unity_script_tools import DISALLOWED_UNITY_TOOLS, UNITY_SCRIPT_WRITE_TOOLS  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = os.path.join(REPO, "unity-mcp", "Server", "src", "services", "registry", "tool_actions.json")
SCRIPT_SOURCES = ("services/tools/manage_script.py", "services/tools/script_apply_edits.py")


def test_every_script_writer_in_the_server_ledger_is_banned():
    with open(LEDGER, encoding="utf-8") as fh:
        tools = json.load(fh)["tools"]
    writers = {
        name for name, entry in tools.items()
        if any(src in entry.get("evidence", "") for src in SCRIPT_SOURCES)
        and (entry.get("tool_level") == "write" or entry.get("write_actions"))
    }
    assert writers, "ledger shape changed; the test no longer sees any script tool"
    assert writers <= set(UNITY_SCRIPT_WRITE_TOOLS), sorted(writers - set(UNITY_SCRIPT_WRITE_TOOLS))
    # A nested call can carry any of them.
    assert tools["batch_execute"].get("recursive_field")
    assert "batch_execute" in UNITY_SCRIPT_WRITE_TOOLS


class _Captured(Exception):
    pass


def test_the_chat_session_gets_the_whole_ban(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from agentic import agent_runner
    from providers import _attachments, claude_sdk_session
    from providers.cli_base import BaseCLIProvider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    seen = {}

    def capture(conversation_id, **kwargs):
        seen.update(kwargs)
        raise _Captured()

    runner = agent_runner.AgentRunner(
        provider_type="subscription", api_key="", model_name="claude-sonnet-4-6",
        workspace_path=str(tmp_path), conversation_id=42, images=[])
    with patch.object(unity_mcp_manager, "mcp_url", return_value="http://127.0.0.1:1/mcp"), \
         patch.object(unity_mcp_manager, "api_headers", return_value={"X-API-Key": "k"}), \
         patch.object(agent_runner, "_remove_project_mcp_json", return_value=None), \
         patch.object(agent_runner, "_oturum_yeniden_kurma_gerekceleri", return_value=[]), \
         patch.object(BaseCLIProvider, "_resolve_exec", return_value=["claude"]), \
         patch.object(subprocess, "run", return_value=None), \
         patch.object(_attachments, "materialize_images", return_value=([], None)), \
         patch.object(claude_sdk_session, "get_session", side_effect=capture):
        try:
            asyncio.run(runner._run_claude_session("x").__anext__())
        except _Captured:
            pass

    assert "unityMCP" in seen["mcp_servers"]
    assert set(DISALLOWED_UNITY_TOOLS) <= set(seen["disallowed_tools"])
