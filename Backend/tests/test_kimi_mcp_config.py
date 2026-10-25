"""kimi must load only the product's MCP servers, never the project's own .mcp.json."""
import asyncio
import json
import os
import sys

import pytest

from providers.kimi_provider import KimiProvider


@pytest.fixture
def kimi(tmp_path, monkeypatch):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    provider = KimiProvider(binary_name="kimi-k3")
    monkeypatch.setattr(provider, "_launcher_path", lambda name: "test-launcher")
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)
    return provider


def _workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"evil": {"command": "/bin/echo", "args": ["pwned"]}}}),
        encoding="utf-8")
    return ws


def _mcp_file(cmd):
    return cmd[cmd.index("--mcp-config-file") + 1]


def test_kimi_gets_product_only_file(kimi, tmp_path):
    ws = _workspace(tmp_path)
    kimi._write_mcp_config(str(ws))
    cmd = kimi._build_cmd("HELLO", workspace=str(ws))
    path = _mcp_file(cmd)
    try:
        assert os.path.realpath(path) != os.path.realpath(ws / ".mcp.json")
        servers = json.loads(open(path, encoding="utf-8").read())["mcpServers"]
        assert set(servers) == {"unityai"}
    finally:
        kimi._drop_mcp_cfg()


def test_kimi_file_has_unity_mcp_when_on(kimi, tmp_path, monkeypatch):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: "http://127.0.0.1:1/mcp")
    ws = _workspace(tmp_path)
    kimi._write_mcp_config(str(ws))
    path = _mcp_file(kimi._build_cmd("HELLO", workspace=str(ws)))
    try:
        servers = json.loads(open(path, encoding="utf-8").read())["mcpServers"]
        assert set(servers) == {"unityai", "unityMCP"}
    finally:
        kimi._drop_mcp_cfg()


def test_flag_omitted_without_per_turn_file(kimi, tmp_path):
    ws = _workspace(tmp_path)
    cmd = kimi._build_cmd("HELLO", workspace=str(ws))
    assert "--mcp-config-file" not in cmd
    assert str(ws / ".mcp.json") not in cmd


def test_file_deleted_after_analyze_code(kimi, tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    seen = {}
    real_build = KimiProvider._build_cmd

    def build(self, *args, **kwargs):
        cmd = real_build(self, *args, **kwargs)
        seen["path"] = _mcp_file(cmd)
        seen["existed"] = os.path.isfile(seen["path"])
        return [sys.executable, "-c", "pass"]

    monkeypatch.setattr(KimiProvider, "_build_cmd", build)

    async def run():
        return [ev async for ev in kimi.analyze_code("hi", cwd=str(ws))]

    asyncio.run(run())
    assert seen["existed"]
    assert not os.path.exists(seen["path"])
    assert kimi._mcp_cfg_path is None


def test_write_mcp_config_output_unchanged_for_plain_workspace(kimi, tmp_path):
    ws = tmp_path / "plain"
    ws.mkdir()
    kimi._write_mcp_config(str(ws))
    data = json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))
    backend_url = os.environ.get(
        "UNITYAI_URL", os.environ.get("ANTIGRAVITY_URL", "http://localhost:8000"))
    assert data == {"mcpServers": {"unityai": {
        "command": "test-launcher",
        "args": ["--workspace", str(ws)],
        "env": {"UNITYAI_URL": backend_url},
    }}}
    kimi._drop_mcp_cfg()
