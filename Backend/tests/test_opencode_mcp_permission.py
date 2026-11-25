"""Tests for OpenCode workspace permission generation and MCP server allowlisting."""
import json
from types import SimpleNamespace

import pytest

from providers import opencode_provider as op
from providers.opencode_provider import OpenCodeProvider


@pytest.fixture(autouse=True)
def _isolated_opencode(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setattr(op, "shadow_supported", lambda: False)
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr("computer_use.settings.enabled_now", lambda: False)
    monkeypatch.setattr("browser_use.settings.enabled_now", lambda: False)
    monkeypatch.setattr("connectors.launcher.ready_ids", lambda db=None: [])
    monkeypatch.setattr(
        "connectors.launcher.installer",
        lambda: SimpleNamespace(root=str(tmp_path / "connectors")),
    )


def _enable_unity_and_computer(monkeypatch, tmp_path):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: "http://127.0.0.1:8080/mcp")
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: True)
    monkeypatch.setattr("computer_use.settings.enabled_now", lambda: True)
    monkeypatch.setattr("browser_use.settings.enabled_now", lambda: True)
    monkeypatch.setattr("connectors.launcher.ready_ids", lambda db=None: ["blender"])
    monkeypatch.setattr(
        "connectors.launcher.installer",
        lambda: SimpleNamespace(root=str(tmp_path / "connectors")),
    )


def _register(ws, existing=None, read_only=False):
    ws.mkdir(exist_ok=True)
    if existing is not None:
        (ws / "opencode.json").write_text(json.dumps(existing), encoding="utf-8")
    provider = OpenCodeProvider("opencode:opencode/big-pickle")
    provider._read_only = read_only
    assert provider._register_mcp("test-launcher", str(ws), "http://localhost:8000") is True
    return json.loads((ws / "opencode.json").read_text(encoding="utf-8"))


EXPECTED_READ_RULE = {
    "*": "allow",
    "*.env": "deny",
    "*.env.*": "deny",
    "*.env.example": "allow",
}


def test_permission_key_order_without_user_permission(tmp_path, monkeypatch):
    cfg = _register(tmp_path / "ws-plain")
    perm = cfg["permission"]
    assert list(perm.keys()) == [
        "*", "read", "glob", "grep", "unityai_*", "edit", "bash",
    ]
    assert perm["*"] == "deny"
    assert perm["read"] == EXPECTED_READ_RULE
    assert perm["glob"] == "allow"
    assert perm["grep"] == "allow"
    assert perm["unityai_*"] == "allow"
    assert perm["edit"] == "deny"
    assert perm["bash"] == "deny"

    _enable_unity_and_computer(monkeypatch, tmp_path)
    cfg_full = _register(tmp_path / "ws-full")
    perm_full = cfg_full["permission"]
    assert list(perm_full.keys()) == [
        "*",
        "read",
        "glob",
        "grep",
        "unityai_*",
        "unityMCP_*",
        "gamachineComputer_*",
        "gamachineBrowser_*",
        "gamachineConnectors_*",
        "edit",
        "bash",
    ]
    for srv in ("unityai", "unityMCP", "gamachineComputer", "gamachineBrowser", "gamachineConnectors"):
        assert perm_full[f"{srv}_*"] == "allow"


def test_permission_key_order_with_user_block_drops_allows_and_keeps_restricts(tmp_path, monkeypatch):
    _enable_unity_and_computer(monkeypatch, tmp_path)
    existing = {
        "permission": {
            "*": "allow",
            "probeother_*": "allow",
            "webfetch": "allow",
            "webfetch2": "deny",
            "read": "deny",
        }
    }
    cfg = _register(tmp_path / "ws", existing=existing)
    perm = cfg["permission"]
    assert list(perm.keys()) == [
        "*",
        "webfetch2",
        "read",
        "glob",
        "grep",
        "unityai_*",
        "unityMCP_*",
        "gamachineComputer_*",
        "gamachineBrowser_*",
        "gamachineConnectors_*",
        "edit",
        "bash",
    ]
    assert perm["*"] == "deny"
    assert perm["webfetch2"] == "deny"
    assert perm["read"] == EXPECTED_READ_RULE
    assert "probeother_*" not in perm
    assert "webfetch" not in perm


@pytest.mark.parametrize("shorthand", ["allow", "ask", "deny"])
def test_permission_key_order_with_string_form_permission(tmp_path, monkeypatch, shorthand):
    _enable_unity_and_computer(monkeypatch, tmp_path)
    cfg = _register(tmp_path / f"ws-{shorthand}", existing={"permission": shorthand})
    perm = cfg["permission"]
    assert list(perm.keys()) == [
        "*",
        "read",
        "glob",
        "grep",
        "unityai_*",
        "unityMCP_*",
        "gamachineComputer_*",
        "gamachineBrowser_*",
        "gamachineConnectors_*",
        "edit",
        "bash",
    ]
    assert perm["*"] == "deny"
    assert perm["read"] == EXPECTED_READ_RULE
    assert perm["edit"] == "deny"
    assert perm["bash"] == "deny"


def test_user_managed_keys_and_gamachine_prefixes_are_skipped(tmp_path):
    existing = {
        "permission": {
            "*": "deny",
            "edit": "ask",
            "bash": "deny",
            "read": "deny",
            "glob": "deny",
            "grep": "ask",
            "probeother_*": "deny",
            "unityai_*": "deny",
            "unityai_save_file": "deny",
            "unityMCP_manage_scene": "ask",
            "gamachineComputer_computer_click": "deny",
            "gamachineBrowser_browser_click": "deny",
            "gamachineConnectors_blender": "deny",
            "question": "ask",
            "task": {"*": "deny", "explore": "ask"},
            "mixed": {"a": "deny", "b": "allow"},
        }
    }
    cfg = _register(tmp_path / "ws", existing=existing)
    perm = cfg["permission"]
    assert list(perm.keys()) == [
        "*",
        "question",
        "task",
        "read",
        "glob",
        "grep",
        "unityai_*",
        "edit",
        "bash",
    ]
    assert perm["question"] == "ask"
    assert perm["task"] == {"*": "deny", "explore": "ask"}
    assert perm["read"] == EXPECTED_READ_RULE


def test_agent_and_mode_permissions_keep_only_restricting_entries(tmp_path):
    existing = {
        "agent": {
            "build": {
                "permission": {
                    "*": "allow",
                    "bash": "allow",
                    "edit": "deny",
                    "read": "deny",
                    "glob": "deny",
                    "grep": "deny",
                    "probeother_*": "deny",
                    "unityai_read_file": "deny",
                    "webfetch": "allow",
                    "webfetch2": "deny",
                    "question": {"*": "deny", "sub": "ask"},
                    "mixed": {"a": "deny", "b": "allow"},
                }
            },
            "str_agent": {"permission": "allow"},
        },
        "mode": {
            "plan": {"permission": "ask"},
            "custom": {"permission": {"task": "ask", "webfetch": "allow"}},
        },
    }
    cfg = _register(tmp_path / "ws", existing=existing)
    assert cfg["agent"]["build"]["permission"] == {
        "webfetch2": "deny",
        "question": {"*": "deny", "sub": "ask"},
    }
    assert cfg["agent"]["str_agent"]["permission"] == {}
    assert cfg["mode"]["plan"]["permission"] == {}
    assert cfg["mode"]["custom"]["permission"] == {"task": "ask"}


def test_read_only_excludes_computer_servers_from_permission(tmp_path, monkeypatch):
    _enable_unity_and_computer(monkeypatch, tmp_path)
    cfg = _register(tmp_path / "ws", read_only=True)
    perm = cfg["permission"]
    assert list(perm.keys()) == [
        "*", "read", "glob", "grep", "unityai_*", "unityMCP_*", "edit", "bash",
    ]
    for srv in ("gamachineComputer", "gamachineBrowser", "gamachineConnectors"):
        assert f"{srv}_*" not in perm
        assert srv not in cfg["mcp"]
