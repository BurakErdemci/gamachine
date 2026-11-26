"""Tests for GAMACHINE_MCP_PORT override across manager, startup, OmniSharp, and scene editor."""

import io
import json
import logging
import os
import socket
import urllib.request
from types import SimpleNamespace

import pytest

import omnisharp.omnisharp_manager as omnisharp_manager
import unity_ai_mcp.mcp_port_guard as mcp_port_guard
import unity_ai_mcp.unity_mcp_manager as unity_mcp_mod
from routes import scene_editor_routes


def _fake_urlopen_response(payload: dict):
    resp = io.BytesIO(json.dumps(payload).encode("utf-8"))
    resp.status = 200
    return resp


def test_configured_mcp_port_unset_and_blank(monkeypatch, caplog):
    monkeypatch.delenv("GAMACHINE_MCP_PORT", raising=False)
    with caplog.at_level(logging.WARNING):
        assert unity_mcp_mod.configured_mcp_port() == 8080
    assert caplog.records == []

    for blank in ("", "   "):
        caplog.clear()
        monkeypatch.setenv("GAMACHINE_MCP_PORT", blank)
        with caplog.at_level(logging.WARNING):
            assert unity_mcp_mod.configured_mcp_port() == 8080
        assert caplog.records == []


@pytest.mark.parametrize("raw,expected", [("1024", 1024), ("18080", 18080), ("65535", 65535)])
def test_configured_mcp_port_valid(monkeypatch, caplog, raw, expected):
    monkeypatch.setenv("GAMACHINE_MCP_PORT", raw)
    with caplog.at_level(logging.WARNING):
        assert unity_mcp_mod.configured_mcp_port() == expected
    assert caplog.records == []


@pytest.mark.parametrize("raw", ["abc", "8080.5", "0", "-1", "1023", "65536"])
def test_configured_mcp_port_invalid_or_out_of_range_logs_once(monkeypatch, caplog, raw):
    monkeypatch.setenv("GAMACHINE_MCP_PORT", raw)
    with caplog.at_level(logging.WARNING, logger=unity_mcp_mod.logger.name):
        assert unity_mcp_mod.configured_mcp_port() == 8080
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_fresh_manager_and_mcp_url_use_env_port(monkeypatch):
    monkeypatch.delenv("GAMACHINE_MCP_PORT", raising=False)
    default_mgr = unity_mcp_mod.UnityMCPManager()
    assert default_mgr.mcp_port == 8080

    monkeypatch.setenv("GAMACHINE_MCP_PORT", "18080")
    override_mgr = unity_mcp_mod.UnityMCPManager()
    assert override_mgr.mcp_port == 18080

    override_mgr.local_api_token = "test-secret"
    monkeypatch.setattr(override_mgr, "is_running", lambda: True)
    assert override_mgr.mcp_url() == "http://localhost:18080/mcp"
    assert override_mgr.mcp_url("127.0.0.1") == "http://127.0.0.1:18080/mcp"


def test_main_startup_cleanup_receives_manager_port(monkeypatch, tmp_path):
    import local_token_file

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", os.path.join(str(tmp_path), "local-app-token"))

    import main as backend_main

    recorded = []

    def fake_terminate(port=mcp_port_guard.DEFAULT_MCP_PORT, owned_pids=()):
        recorded.append(port)
        return mcp_port_guard.PortCleanupResult(port=port)

    monkeypatch.setattr(mcp_port_guard, "terminate_port_listeners", fake_terminate)
    monkeypatch.setattr(unity_mcp_mod.unity_mcp_manager, "mcp_port", 18080)

    backend_main._cleanup_mcp_port_on_startup()
    assert recorded == [18080]


def test_omnisharp_probe_and_sync_use_manager_port(monkeypatch, tmp_path):
    monkeypatch.delenv("GAMACHINE_NO_UNITY_MCP", raising=False)
    monkeypatch.setattr(unity_mcp_mod.unity_mcp_manager, "mcp_port", 18080)
    monkeypatch.setattr(unity_mcp_mod.unity_mcp_manager, "local_api_token", "tok")

    probed = []

    def fake_connect(address, *args, **kwargs):
        probed.append(address)
        raise OSError("no real socket")

    monkeypatch.setattr(socket, "create_connection", fake_connect)
    assert omnisharp_manager._unity_api_up() is False
    assert probed == [("localhost", 18080)]

    seen_urls = []

    def fake_urlopen(req, *args, **kwargs):
        seen_urls.append(req.full_url)
        return _fake_urlopen_response({"status": "success", "result": {"success": True, "data": {"csproj_count": 1}}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(omnisharp_manager, "_csproj_sync_reason", lambda _ws: None)

    mgr = omnisharp_manager.OmniSharpManager()
    mgr._maybe_sync_csproj(str(tmp_path), reason="forced")
    assert seen_urls == ["http://localhost:18080/api/command"]


def test_scene_editor_routes_use_manager_port(monkeypatch):
    monkeypatch.delenv("GAMACHINE_NO_UNITY_MCP", raising=False)
    monkeypatch.setattr(unity_mcp_mod.unity_mcp_manager, "mcp_port", 18080)
    monkeypatch.setattr(unity_mcp_mod.unity_mcp_manager, "local_api_token", "tok")

    seen_urls = []

    def fake_urlopen(req, *args, **kwargs):
        seen_urls.append(req.full_url)
        return _fake_urlopen_response({"status": "success", "result": {"success": True, "data": {"ok": True}}})

    monkeypatch.setattr(scene_editor_routes.urllib.request, "urlopen", fake_urlopen)

    result = scene_editor_routes._post_unity("gm_editor_version", {}, "")
    assert result == {"ok": True}
    assert seen_urls == ["http://localhost:18080/api/command"]
