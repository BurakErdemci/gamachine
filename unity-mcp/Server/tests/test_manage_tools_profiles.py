"""manage_tools after tool profiles: no false success, and it says where to go.

The P1 spike measured `manage_tools deactivate` on the 2026-07-28 protocol
answering success while hiding nothing. A client that believes a toggle worked
goes on to call tools it cannot see, so the toggles now refuse explicitly.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import config
from services.tools import manage_tools as manage_tools_module
from services.tools.manage_tools import manage_tools
from transport import tool_profiles


@pytest.fixture(autouse=True)
def _restore_group_state():
    before = tool_profiles.server_enabled_groups()
    yield
    tool_profiles.set_server_enabled_groups(before)


def _ctx():
    return SimpleNamespace(info=AsyncMock())


@pytest.mark.parametrize("action", ["activate", "deactivate", "reset"])
def test_toggles_fail_explicitly_and_name_the_profile_urls_over_http(monkeypatch, action):
    monkeypatch.setattr(config, "transport_mode", "http")
    result = asyncio.run(manage_tools(_ctx(), action=action, group="vfx"))
    assert result["success"] is False
    assert "/mcp/full" in result["error"]
    assert "/mcp/gamachine" in result["error"]


def test_toggle_over_stdio_points_at_the_editor_settings(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    result = asyncio.run(manage_tools(_ctx(), action="activate", group="vfx"))
    assert result["success"] is False
    assert "Unity Editor" in result["error"]
    assert "sync" in result["error"]


def test_list_groups_reports_the_current_profile_and_how_to_switch(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "http")
    tool_profiles.set_server_enabled_groups({"core", "playtest"})
    monkeypatch.setattr(tool_profiles, "current_profile",
                        lambda: tool_profiles.PROFILES["gamachine"])
    result = asyncio.run(manage_tools(_ctx(), action="list_groups"))
    assert result["profile"] == {"name": "gamachine", "path": "/mcp/gamachine"}
    assert {p["path"] for p in result["profiles"]} == {"/mcp", "/mcp/gamachine", "/mcp/full"}
    enabled = {g["name"]: g["enabled"] for g in result["groups"]}
    assert enabled["core"] and enabled["playtest"]
    assert not enabled["vfx"]


def test_list_groups_on_full_shows_every_group_enabled(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "http")
    tool_profiles.set_server_enabled_groups({"core"})
    monkeypatch.setattr(tool_profiles, "current_profile",
                        lambda: tool_profiles.PROFILES["full"])
    result = asyncio.run(manage_tools(_ctx(), action="list_groups"))
    assert all(g["enabled"] for g in result["groups"])
    in_unity = {g["name"]: g["enabled_server_wide"] for g in result["groups"]}
    assert in_unity["core"] and not in_unity["playtest"]


def test_no_private_fastmcp_session_visibility_api_is_used():
    source = open(manage_tools_module.__file__, encoding="utf-8").read()
    for private in ("_get_visibility_rules", "enable_components",
                    "disable_components", "reset_visibility"):
        assert private not in source
