"""`.mcp.json` is the user's file: the product merges into it, never replaces it."""
import json
import os
import threading
from unittest.mock import Mock

import pytest

from providers import workspace_config


def _provider(monkeypatch, mcp_url=None, cls=None):
    from providers.claude_provider import ClaudeCodeProvider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    provider = (cls or ClaudeCodeProvider)()
    monkeypatch.setattr(provider, "_launcher_path", lambda name: "test-launcher")
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: mcp_url)
    return provider


def _read(ws):
    return json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))


def test_user_entries_survive_and_unityai_added(tmp_path, monkeypatch):
    (tmp_path / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"user-server": {"command": "my-own-server"}},
        "user-notes": "x",
    }), encoding="utf-8")
    _provider(monkeypatch)._write_mcp_config(str(tmp_path))
    data = _read(tmp_path)
    assert data["mcpServers"]["user-server"] == {"command": "my-own-server"}
    assert data["user-notes"] == "x"
    assert data["mcpServers"]["unityai"]["command"] == "test-launcher"


def test_unity_mcp_off_removes_stale_entry(tmp_path, monkeypatch):
    (tmp_path / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"unityMCP": {"command": "stale"}, "mine": {"command": "m"}},
    }), encoding="utf-8")
    _provider(monkeypatch, mcp_url=None)._write_mcp_config(str(tmp_path))
    servers = _read(tmp_path)["mcpServers"]
    assert "unityMCP" not in servers
    assert "mine" in servers and "unityai" in servers


def test_unity_mcp_on_adds_entry(tmp_path, monkeypatch):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"mine": {}}}),
                                        encoding="utf-8")
    _provider(monkeypatch, mcp_url="http://127.0.0.1:1/mcp")._write_mcp_config(str(tmp_path))
    servers = _read(tmp_path)["mcpServers"]
    assert {"mine", "unityai", "unityMCP"} <= set(servers)
    assert servers["unityMCP"]["env"]["UNITY_MCP_URL"] == "http://127.0.0.1:1/mcp"


def test_invalid_json_is_backed_up(tmp_path, monkeypatch):
    (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")
    _provider(monkeypatch)._write_mcp_config(str(tmp_path))
    backups = [p for p in os.listdir(tmp_path) if "gamachine-backup" in p]
    assert backups
    assert (tmp_path / backups[0]).read_text(encoding="utf-8") == "{not json"
    assert set(_read(tmp_path)["mcpServers"]) == {"unityai"}


@pytest.mark.parametrize("value", [["a"], "text", None])
def test_non_object_mcp_servers_is_left_untouched(tmp_path, monkeypatch, value):
    original = json.dumps({"mcpServers": value, "keep": 1})
    (tmp_path / ".mcp.json").write_text(original, encoding="utf-8")
    _provider(monkeypatch)._write_mcp_config(str(tmp_path))
    assert (tmp_path / ".mcp.json").read_text(encoding="utf-8") == original


def _unsafe(tmp_path, kind):
    target = tmp_path / ".mcp.json"
    if kind == "symlink":
        victim = tmp_path.parent / (tmp_path.name + "-victim.json")
        victim.write_text("VICTIM", encoding="utf-8")
        os.symlink(victim, target)
        return victim
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFO support")
    os.mkfifo(target)
    return None


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_unsafe_mcp_json_is_left_untouched(tmp_path, monkeypatch, kind):
    ws = tmp_path / "ws"
    ws.mkdir()
    victim = _unsafe(ws, kind)
    writer = Mock(side_effect=workspace_config.guvenli_config_yaz)
    monkeypatch.setattr(workspace_config, "guvenli_config_yaz", writer)
    provider = _provider(monkeypatch)
    result = {}
    t = threading.Thread(
        target=lambda: result.update(p=provider._write_mcp_config(str(ws))), daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "hung on unsafe .mcp.json"
    assert result["p"] == str(ws / ".mcp.json")
    writer.assert_not_called()
    if victim is not None:
        assert victim.read_text(encoding="utf-8") == "VICTIM"
        assert os.path.islink(ws / ".mcp.json")
    else:
        import stat
        assert stat.S_ISFIFO(os.lstat(ws / ".mcp.json").st_mode)


def test_policy_provider_still_registers_when_unsafe(tmp_path, monkeypatch):
    from providers.cursor_provider import CursorProvider

    ws = tmp_path / "ws"
    ws.mkdir()
    _unsafe(ws, "symlink")
    provider = _provider(monkeypatch, cls=CursorProvider)
    register = Mock(return_value=True)
    monkeypatch.setattr(provider, "_register_mcp", register)
    provider._write_mcp_config(str(ws))
    register.assert_called_once()


def test_ensure_gitignored_fifo_returns(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFO support")
    fifo = tmp_path / ".gitignore"
    os.mkfifo(fifo)
    t = threading.Thread(
        target=lambda: workspace_config.ensure_gitignored(str(tmp_path), [".mcp.json"]),
        daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive(), "ensure_gitignored blocked on FIFO .gitignore"
    import stat
    assert stat.S_ISFIFO(os.lstat(fifo).st_mode)


def test_read_text_normalises_newlines_like_text_mode(tmp_path):
    # The writer is text mode; a kept "\r\n" became "\r\r\n" on Windows.
    path = tmp_path / ".gitignore"
    path.write_bytes(b"# rules\r\n*.secret\r\nold\rmac\n")
    assert workspace_config._read_text(str(path)) == "# rules\n*.secret\nold\nmac\n"
