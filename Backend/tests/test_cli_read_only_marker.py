"""A read-only turn marks its CLI process; the computer and browser servers refuse on the mark."""
import asyncio
import os
import stat
from unittest.mock import AsyncMock, MagicMock

import pytest

from browser_use.server import BrowserServer
from computer_use.server import ComputerServer
from providers.cli_base import BaseCLIProvider
from providers.copilot_provider import CopilotProvider
from providers.cursor_provider import CursorProvider
from providers.kimi_provider import KimiProvider
from providers.opencode_provider import OpenCodeProvider
from spawn_env import READ_ONLY_ENV, read_only_refusal

PROVIDERS = (
    ("copilot", lambda: CopilotProvider("copilot-auto")),
    ("cursor", lambda: CursorProvider("cursor-auto")),
    ("kimi", lambda: KimiProvider("kimi-k3")),
    ("opencode", lambda: OpenCodeProvider("opencode:anthropic/claude-sonnet")),
)


def _spawn_env(make, read_only, tmp_path, monkeypatch):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda *a, **k: None)
    monkeypatch.setattr(BaseCLIProvider, "_cli_installed", staticmethod(lambda name: True))
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", staticmethod(lambda cmd: list(cmd)))
    monkeypatch.setattr(BaseCLIProvider, "_ensure_exec", staticmethod(lambda path: None))
    provider = make()
    monkeypatch.setattr(provider, "_launcher_path", lambda name: "test-launcher")
    provider._conversation_id = 77
    provider._read_only = read_only
    seen = {}

    async def fake_spawn(*argv, **kw):
        seen["env"] = dict(kw.get("env") or {})
        raise RuntimeError("spawn intercepted")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)
    ws = tmp_path / f"ws-{read_only}"
    ws.mkdir()

    async def drive():
        try:
            async for _ in provider.analyze_code("prompt", cwd=str(ws)):
                pass
        except RuntimeError as e:
            assert "spawn intercepted" in str(e)

    asyncio.run(drive())
    return seen["env"]


@pytest.mark.parametrize("name,make", PROVIDERS)
def test_only_a_read_only_turn_carries_the_marker(name, make, tmp_path, monkeypatch):
    assert _spawn_env(make, True, tmp_path, monkeypatch).get(READ_ONLY_ENV) == "1"
    assert READ_ONLY_ENV not in _spawn_env(make, False, tmp_path, monkeypatch)


def test_refusal_helper_follows_the_environment(monkeypatch):
    monkeypatch.delenv(READ_ONLY_ENV, raising=False)
    assert read_only_refusal() is None
    monkeypatch.setenv(READ_ONLY_ENV, "0")
    assert read_only_refusal() is None
    monkeypatch.setenv(READ_ONLY_ENV, "1")
    assert read_only_refusal()


def test_browser_server_refuses_only_with_the_marker(monkeypatch):
    command = AsyncMock(return_value={"ok": True, "text": "done"})
    server = BrowserServer(command=command, conversation_id=7)
    monkeypatch.setenv(READ_ONLY_ENV, "1")
    refused = asyncio.run(server.call("navigate", {"url": "https://example.com/"}))
    assert refused.is_error and refused.content[0].text == read_only_refusal()
    command.assert_not_called()
    monkeypatch.delenv(READ_ONLY_ENV)
    allowed = asyncio.run(server.call("navigate", {"url": "https://example.com/"}))
    assert not allowed.is_error and allowed.content[0].text == "done"
    command.assert_called_once()


def test_computer_server_refuses_only_with_the_marker(monkeypatch):
    driver = MagicMock()
    args = {"pid": 4242, "text": "x", "scope": "desktop"}
    monkeypatch.setenv(READ_ONLY_ENV, "1")
    refused = asyncio.run(ComputerServer(driver).call("computer_click", args))
    assert refused.is_error and refused.content[0].text == read_only_refusal()
    monkeypatch.delenv(READ_ONLY_ENV)
    other = asyncio.run(ComputerServer(driver).call("computer_click", args))
    assert other.content[0].text != read_only_refusal()


def test_kimi_refresh_keeps_the_file_mode_and_leaves_no_temp_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    provider = KimiProvider(binary_name="kimi-k3")
    path = provider._kimi_config_path()
    os.makedirs(os.path.dirname(path))
    stale = f'key = "secret"\n{provider._KIMI_MANAGED_MARKER}\nold = 1\n{provider._KIMI_MANAGED_END}\n'
    with open(path, "w", encoding="utf-8") as f:
        f.write(stale)
    os.chmod(path, 0o600)
    provider._write_kimi_permissions()
    assert "old = 1" not in open(path, encoding="utf-8").read()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert os.listdir(os.path.dirname(path)) == [os.path.basename(path)]


def test_kimi_first_write_creates_a_private_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    provider = KimiProvider(binary_name="kimi-k3")
    provider._write_kimi_permissions()
    assert stat.S_IMODE(os.stat(provider._kimi_config_path()).st_mode) == 0o600


@pytest.mark.parametrize("chat,refused", [(None, True), ("abc", True), ("0", True), ("-3", True), ("5", False)])
def test_computer_server_entry_needs_a_valid_chat_id(monkeypatch, chat, refused):
    from computer_use import platform
    from computer_use.server import create_server

    monkeypatch.delenv(READ_ONLY_ENV, raising=False)
    if chat is None:
        monkeypatch.delenv("GAMACHINE_CONVERSATION_ID", raising=False)
    else:
        monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", chat)
    tool = next(n for n in __import__("computer_use.policy", fromlist=["TOOLS"]).TOOLS
                if n not in platform.current().unsupported_tools)
    result = asyncio.run(create_server(MagicMock(), require_chat=True).call_tool(tool, {"args": {}}))
    text = result.content[0].text
    assert ("needs a chat" in text) is refused
