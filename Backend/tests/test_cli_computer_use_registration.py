"""copilot, cursor, kimi and opencode register the computer-use and agent-browser servers per turn."""
import json
import os

import pytest

from browser_use import registration as browser_reg
from computer_use import registration as computer_reg
from providers.copilot_provider import CopilotProvider
from providers.cursor_provider import CursorProvider
from providers.kimi_provider import KimiProvider
from providers.opencode_provider import OpenCodeProvider
from spawn_env import CONVERSATION_ENV

NAMES = (computer_reg.SERVER_NAME, browser_reg.SERVER_NAME)
CHAT = 77


@pytest.fixture
def switch(monkeypatch):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    state = {"on": True}
    monkeypatch.setattr("computer_use.settings.enabled_now", lambda: state["on"])
    monkeypatch.setattr("browser_use.settings.enabled_now", lambda: state["on"])
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda *a, **k: None)
    return state


def _prepare(provider, monkeypatch, read_only=False):
    # CI has no copilot binary; without one _build_cmd returns before it adds the MCP config.
    monkeypatch.setattr("providers.copilot_provider.resolve_copilot_cmd", lambda: ["copilot"])
    monkeypatch.setattr(provider, "_launcher_path", lambda name: "test-launcher")
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)
    provider._conversation_id = CHAT
    provider._read_only = read_only
    return provider


def _ws(tmp_path, name="ws"):
    ws = tmp_path / name
    ws.mkdir(exist_ok=True)
    return str(ws)


def test_stdio_entry_off_and_chat_id_rules(switch):
    for reg in (computer_reg, browser_reg):
        entry = reg.stdio_entry(CHAT)
        argv = reg.server_argv()
        assert [entry["command"], *entry["args"]] == argv
        assert entry["env"][CONVERSATION_ENV] == str(CHAT)
        assert "UNITYAI_URL" in entry["env"]
        assert CONVERSATION_ENV not in reg.stdio_entry()["env"]
        assert CONVERSATION_ENV not in reg.stdio_entry(0)["env"]
        assert CONVERSATION_ENV not in reg.stdio_entry(True)["env"]
        assert reg.claude_server_entry(CHAT) == {"type": "stdio", **entry}
        assert reg.claude_server_entry(None) is None
    switch["on"] = False
    for reg in (computer_reg, browser_reg):
        assert reg.stdio_entry(CHAT) is None
        assert reg.claude_server_entry(CHAT) is None


def _copilot_turn(provider, ws):
    provider._write_mcp_config(ws)
    cmd = provider._build_cmd("hi", workspace=ws)
    path = cmd[cmd.index("--additional-mcp-config") + 1][1:]
    with open(path, encoding="utf-8") as f:
        servers = json.load(f)["mcpServers"]
    provider._drop_mcp_cfg()
    allowed = {cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "--allow-tool"}
    return servers, allowed


def test_copilot_entries_chat_id_and_allow_flags(switch, tmp_path, monkeypatch):
    provider = _prepare(CopilotProvider("copilot-auto"), monkeypatch)
    servers, allowed = _copilot_turn(provider, _ws(tmp_path))
    for name in NAMES:
        assert servers[name]["type"] == "local"
        assert servers[name]["tools"] == ["*"]
        assert servers[name]["env"][CONVERSATION_ENV] == str(CHAT)
        assert name in allowed
    switch["on"] = False
    servers, allowed = _copilot_turn(provider, _ws(tmp_path))
    assert not set(NAMES) & set(servers)
    assert not set(NAMES) & allowed


def test_copilot_read_only_skips_servers_and_flags(switch, tmp_path, monkeypatch):
    provider = _prepare(CopilotProvider("copilot-auto"), monkeypatch, read_only=True)
    servers, allowed = _copilot_turn(provider, _ws(tmp_path))
    assert not set(NAMES) & set(servers)
    assert not set(NAMES) & allowed


def _cursor_servers(provider, ws):
    provider._register_mcp("test-launcher", ws, "http://localhost:8000")
    with open(os.path.join(ws, ".cursor", "mcp.json"), encoding="utf-8") as f:
        return json.load(f)["mcpServers"]


def test_cursor_shared_file_has_no_chat_id_and_drops_servers(switch, tmp_path, monkeypatch):
    provider = _prepare(CursorProvider("cursor-auto"), monkeypatch)
    ws = _ws(tmp_path)
    os.makedirs(os.path.join(ws, ".cursor"))
    with open(os.path.join(ws, ".cursor", "mcp.json"), "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"mine": {"command": "x"}}}, f)
    servers = _cursor_servers(provider, ws)
    for name in NAMES:
        assert servers[name]["command"]
        assert CONVERSATION_ENV not in servers[name]["env"]
    provider._read_only = True
    servers = _cursor_servers(provider, ws)
    assert not set(NAMES) & set(servers)
    assert "mine" in servers
    provider._read_only = False
    assert set(NAMES) <= set(_cursor_servers(provider, ws))
    switch["on"] = False
    servers = _cursor_servers(provider, ws)
    assert not set(NAMES) & set(servers)
    assert "mine" in servers


def _kimi_servers(provider, ws):
    provider._write_mcp_config(ws)
    cmd = provider._build_cmd("hi", workspace=ws)
    path = cmd[cmd.index("--mcp-config-file") + 1]
    with open(path, encoding="utf-8") as f:
        servers = json.load(f)["mcpServers"]
    provider._drop_mcp_cfg()
    return servers


def test_kimi_entries_chat_id_and_switch(switch, tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    provider = _prepare(KimiProvider(binary_name="kimi-k3"), monkeypatch)
    ws = _ws(tmp_path)
    servers = _kimi_servers(provider, ws)
    for name in NAMES:
        assert servers[name]["env"][CONVERSATION_ENV] == str(CHAT)
    provider._read_only = True
    assert not set(NAMES) & set(_kimi_servers(provider, ws))
    provider._read_only = False
    switch["on"] = False
    assert not set(NAMES) & set(_kimi_servers(provider, ws))


def test_kimi_refreshes_a_stale_managed_block(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    provider = KimiProvider(binary_name="kimi-k3")
    path = provider._kimi_config_path()
    os.makedirs(os.path.dirname(path))
    stale = (f"before = 1\n\n{provider._KIMI_MANAGED_MARKER}\n"
             '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__unityai__*"\n'
             f"{provider._KIMI_MANAGED_END}\nafter = 2\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(stale)
    provider._write_kimi_permissions()
    text = open(path, encoding="utf-8").read()
    assert "mcp__gamachineComputer__*" in text and "mcp__gamachineBrowser__*" in text
    assert text.startswith("before = 1\n") and text.endswith("after = 2\n")
    assert text.count(provider._KIMI_MANAGED_MARKER) == 1
    provider._write_kimi_permissions()
    assert open(path, encoding="utf-8").read() == text


def test_kimi_keeps_a_legacy_block(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    provider = KimiProvider(binary_name="kimi-k3")
    path = provider._kimi_config_path()
    os.makedirs(os.path.dirname(path))
    legacy = f"{provider._KIMI_LEGACY_MARKER}\nold = 1\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(legacy)
    provider._write_kimi_permissions()
    assert open(path, encoding="utf-8").read() == legacy


def _opencode_mcp(provider, ws):
    assert provider._register_mcp("test-launcher", ws, "http://localhost:8000")
    with open(os.path.join(ws, "opencode.json"), encoding="utf-8") as f:
        return json.load(f)["mcp"]


def test_opencode_shape_no_chat_id_and_removal(switch, tmp_path, monkeypatch):
    import providers.opencode_provider as mod

    monkeypatch.setattr(mod, "shadow_supported", lambda: False)
    provider = _prepare(OpenCodeProvider("opencode:anthropic/claude-sonnet"), monkeypatch)
    ws = _ws(tmp_path)
    mcp = _opencode_mcp(provider, ws)
    for name, reg in zip(NAMES, (computer_reg, browser_reg)):
        entry = mcp[name]
        assert entry["type"] == "local" and entry["enabled"] is True
        assert entry["command"] == reg.server_argv()
        assert CONVERSATION_ENV not in entry["environment"]
    provider._read_only = True
    assert not set(NAMES) & set(_opencode_mcp(provider, ws))
    provider._read_only = False
    assert set(NAMES) <= set(_opencode_mcp(provider, ws))
    switch["on"] = False
    assert not set(NAMES) & set(_opencode_mcp(provider, ws))


def test_read_only_defaults_to_false():
    assert CopilotProvider("copilot-auto")._read_only is False


def _runner_provider(read_only):
    import asyncio
    from unittest.mock import patch

    import agent_guide
    from agentic.agent_runner import AgentRunner
    from providers.oneshot_cli import _SESSIONS

    seen = {}

    class FakeProvider:
        resume_session_id = None

        async def analyze_code(self, *args, **kwargs):
            seen["read_only"] = self._read_only
            seen["guide"] = self._agent_guide
            seen["chat"] = self._conversation_id
            return
            yield

    _SESSIONS.clear()
    runner = AgentRunner(provider_type="subscription", api_key="",
                         model_name="opencode:opencode-go/kimi-k3",
                         workspace_path=os.getcwd(), context="", conversation_id=CHAT,
                         read_only=read_only)
    runner._agent_guide = agent_guide.compose("en", project_open=True, unity_state="off")
    base = runner._agent_guide

    async def drive():
        async for _ in runner._run_oneshot_cli_session("hi", "opencode"):
            pass

    with patch("ai_providers.AIProviderManager.get_provider", return_value=FakeProvider()):
        asyncio.run(drive())
    _SESSIONS.clear()
    return seen, base


def test_runner_hands_read_only_and_guide_lines_to_the_provider(switch):
    import agent_guide

    seen, base = _runner_provider(read_only=False)
    assert seen["read_only"] is False and seen["chat"] == CHAT
    assert seen["guide"] == agent_guide.with_browser_use(agent_guide.with_computer_use(base))
    assert seen["guide"] != base
    switch["on"] = False
    seen, base = _runner_provider(read_only=False)
    assert seen["guide"] == base
    switch["on"] = True
    seen, base = _runner_provider(read_only=True)
    assert seen["read_only"] is True and seen["guide"] == base
