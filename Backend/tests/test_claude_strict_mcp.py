"""The product's Claude sessions load only the MCP servers the product passes.

Measured failure (26 Sep 2026, live app, step mode): with setting source "user"
the Claude CLI loaded every MCP server of the owner's own Claude Code, about
60, among them a second Unity server "UnityMCP" (X-API-Key only), Gmail, Drive
and Vercel. The model called Unity through that server, so the calls carried no
X-Gamachine-Conversation header and both approval cards of the test went
unowned ("no conversation_id in the body"); `mcp__UnityMCP__manage_script` was
also outside the `mcp__unityMCP__manage_script` ban. Every entry point that
opens a Claude session must ask for --strict-mcp-config.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import claude_agent_sdk  # noqa: E402
from claude_agent_sdk import ClaudeAgentOptions  # noqa: E402

from providers import claude_sdk_session  # noqa: E402
from providers.claude_sdk_session import ClaudeSDKSession  # noqa: E402


class _Captured(Exception):
    pass


def _capture_options(monkeypatch) -> list:
    seen = []

    class FakeClient:
        def __init__(self, options=None, **_kw):
            seen.append(options)
            raise _Captured()

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)
    return seen


def test_chat_session_asks_for_strict_mcp_config(monkeypatch):
    seen = _capture_options(monkeypatch)
    session = ClaudeSDKSession(conversation_id=1)
    with pytest.raises(_Captured):
        asyncio.run(session.start())
    assert seen and seen[0].strict_mcp_config is True


def test_warmup_asks_for_strict_mcp_config(monkeypatch):
    seen = _capture_options(monkeypatch)
    monkeypatch.setattr(claude_sdk_session, "_SLASH_COMMANDS_CACHE", [])
    try:
        asyncio.run(claude_sdk_session.warmup_slash_commands())
    except _Captured:
        pass
    assert seen and seen[0].strict_mcp_config is True


def test_the_sdk_turns_it_into_the_cli_flag():
    """The option only matters if the installed SDK emits the flag."""
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    options = ClaudeAgentOptions(strict_mcp_config=True, cli_path="claude",
                                 mcp_servers={"unityMCP": {"type": "http", "url": "http://127.0.0.1:1/mcp"}})
    command = SubprocessCLITransport(prompt="x", options=options)._build_command()
    assert "--strict-mcp-config" in command
    assert "--mcp-config" in command
