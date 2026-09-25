"""URL tool profiles (/mcp, /mcp/gamachine, /mcp/full) at the unit level.

The same behaviour is checked end to end, against the real server over a real
socket, in test_live_server_startup.py. This file pins the pieces that probe
cannot reach without a Unity Editor: the Unity-registered-tool filter, the
server group state Unity drives, and refusal ordering.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.config import config
from services.registry import DEFAULT_ENABLED_GROUPS, TOOL_GROUPS
from transport import tool_profiles
from transport.plugin_hub import PluginHub
from transport.tool_profiles import (
    DEFAULT_PROFILE,
    PROFILE_SCOPE_KEY,
    PROFILES,
    ProfilePathMiddleware,
    ToolProfileMiddleware,
    set_server_enabled_groups,
)
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.fixture(autouse=True)
def _restore_group_state():
    before = tool_profiles.server_enabled_groups()
    yield
    set_server_enabled_groups(before)


def _tool(name, *groups):
    return SimpleNamespace(name=name, tags={f"group:{g}" for g in groups})


def _run_asgi(path, scope_type="http"):
    seen = {}

    async def inner(scope, receive, send):
        seen["scope"] = scope

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": scope_type, "path": path, "raw_path": path.encode(), "headers": []}
    asyncio.run(ProfilePathMiddleware(inner)(scope, receive, send))
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    return seen.get("scope"), status


# ── ASGI path rewrite ────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(PROFILES))
def test_profile_path_is_served_by_the_one_transport_route(name):
    scope, status = _run_asgi(f"/mcp/{name}")
    assert status is None
    assert scope["path"] == "/mcp"
    assert scope["raw_path"] == b"/mcp"
    assert scope["state"][PROFILE_SCOPE_KEY] == name


def test_trailing_slash_selects_the_same_profile():
    scope, _ = _run_asgi("/mcp/full/")
    assert scope["state"][PROFILE_SCOPE_KEY] == "full"


def test_unknown_profile_is_a_404_not_the_default_list():
    """A typo in a client config must fail loudly, not quietly serve /mcp."""
    scope, status = _run_asgi("/mcp/gamachin")
    assert scope is None
    assert status == 404


# Starlette's ^/mcp$ route regex also matches "/mcp\n": any /mcp spelling this
# middleware does not recognise exactly must be answered here, never passed on
# to be served as the default transport (audit 25 Sep 2026, POST /mcp%0A).
@pytest.mark.parametrize("path", [
    "/mcp\n", "/mcp\r", "/mcp\r\n", "/mcp\x00", "/mcpx", "/mcp//", "/mcp//full",
    "/mcp/full\n", "/mcp/full//", "/mcp/full/x", "/mcp/../mcp", "/mcp/FULL",
    "/mcp/hub/plugin",
])
def test_inexact_transport_spellings_are_404(path):
    scope, status = _run_asgi(path)
    assert scope is None, f"{path!r} was passed on to the transport"
    assert status == 404


@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/health", "/api/instances"])
def test_other_paths_pass_through_untouched(path):
    scope, status = _run_asgi(path)
    assert status is None
    assert scope["path"] == path
    assert PROFILE_SCOPE_KEY not in (scope.get("state") or {})


def test_plugin_websocket_under_the_transport_prefix_is_not_a_profile():
    scope, status = _run_asgi("/mcp/hub/plugin", scope_type="websocket")
    assert status is None
    assert scope["path"] == "/mcp/hub/plugin"


def test_no_profile_is_named_hub():
    assert "hub" not in PROFILES


# ── Which tools each profile allows ─────────────────────────────────────────

def test_gamachine_profile_is_exactly_the_backend_export_rule():
    """Backend/app/tools/unity_mcp_tools.py exports tools tagged with an
    EXPORTED_GROUPS group ("core", "playtest") and nothing ungrouped."""
    set_server_enabled_groups(DEFAULT_ENABLED_GROUPS)
    gamachine = PROFILES["gamachine"]
    assert gamachine.groups == frozenset({"core", "playtest"})
    assert gamachine.allows({"core"}) and gamachine.allows({"playtest"})
    assert not gamachine.allows(set())
    assert not gamachine.allows({"vfx"})


def test_gamachine_follows_a_group_the_editor_turned_off():
    """Today the backend filters /mcp, which hides a group Unity disabled; the
    profile must hide it too or the two lists diverge."""
    set_server_enabled_groups({"core"})
    assert not PROFILES["gamachine"].allows({"playtest"})


def test_default_profile_follows_server_group_state():
    set_server_enabled_groups(DEFAULT_ENABLED_GROUPS)
    assert DEFAULT_PROFILE.allows(set())
    assert DEFAULT_PROFILE.allows({"core"})
    assert not DEFAULT_PROFILE.allows({"vfx"})
    set_server_enabled_groups(DEFAULT_ENABLED_GROUPS | {"vfx"})
    assert DEFAULT_PROFILE.allows({"vfx"})


def test_full_profile_ignores_server_group_state():
    set_server_enabled_groups(set())
    full = PROFILES["full"]
    assert full.allows(set())
    for group in TOOL_GROUPS:
        assert full.allows({group})


def test_unknown_group_names_never_enter_the_state():
    set_server_enabled_groups({"core", "not-a-group"})
    assert tool_profiles.server_enabled_groups() == frozenset({"core"})


# ── FastMCP middleware ──────────────────────────────────────────────────────

def _list_context():
    return SimpleNamespace(fastmcp_context=SimpleNamespace(), message=SimpleNamespace())


@pytest.mark.parametrize(
    "profile_name, expected",
    [
        (None, ["manage_scene", "play_step", "manage_tools"]),
        ("gamachine", ["manage_scene", "play_step"]),
        ("full", ["manage_scene", "manage_vfx", "play_step", "manage_tools"]),
    ],
)
def test_list_filter_per_profile_keeps_fastmcp_order(monkeypatch, profile_name, expected):
    set_server_enabled_groups(DEFAULT_ENABLED_GROUPS)
    profile = PROFILES.get(profile_name, DEFAULT_PROFILE)
    monkeypatch.setattr(tool_profiles, "current_profile", lambda: profile)
    tools = [_tool("manage_scene", "core"), _tool("manage_vfx", "vfx"),
             _tool("play_step", "playtest"), _tool("manage_tools")]

    async def call_next(_):
        return tools

    listed = asyncio.run(ToolProfileMiddleware().on_list_tools(_list_context(), call_next))
    assert [t.name for t in listed] == expected


def _call_context(tool):
    fastmcp = SimpleNamespace(get_tool=AsyncMock(return_value=tool))
    return SimpleNamespace(
        message=SimpleNamespace(name=getattr(tool, "name", "missing"), arguments={}),
        fastmcp_context=SimpleNamespace(fastmcp=fastmcp),
    )


def test_out_of_profile_call_is_refused_before_anything_downstream_runs(monkeypatch):
    """Downstream is UnityInstanceMiddleware, whose approval gate would show
    the user a card for a call the profile does not even offer."""
    monkeypatch.setattr(tool_profiles, "current_profile", lambda: PROFILES["gamachine"])
    call_next = AsyncMock()
    from fastmcp.exceptions import ToolError
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(ToolProfileMiddleware().on_call_tool(
            _call_context(_tool("manage_tools")), call_next))
    call_next.assert_not_called()
    assert "/mcp/gamachine" in str(excinfo.value)
    assert "/mcp/full" in str(excinfo.value)


def test_default_profile_refuses_a_tool_of_a_disabled_group(monkeypatch):
    """Before profiles FastMCP itself refused these (the group was a disabled
    transform); the group state moved here, so the refusal did too."""
    set_server_enabled_groups(DEFAULT_ENABLED_GROUPS)
    monkeypatch.setattr(tool_profiles, "current_profile", lambda: DEFAULT_PROFILE)
    call_next = AsyncMock()
    from fastmcp.exceptions import ToolError
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(ToolProfileMiddleware().on_call_tool(
            _call_context(_tool("manage_vfx", "vfx")), call_next))
    call_next.assert_not_called()
    assert "manage_tools(action='sync')" in str(excinfo.value)


def test_in_profile_and_unknown_tools_reach_the_next_layer(monkeypatch):
    monkeypatch.setattr(tool_profiles, "current_profile", lambda: PROFILES["full"])
    call_next = AsyncMock(return_value="ok")
    ctx = _call_context(_tool("manage_vfx", "vfx"))
    assert asyncio.run(ToolProfileMiddleware().on_call_tool(ctx, call_next)) == "ok"
    # Unknown tool: FastMCP's own not-found error is the right answer.
    ctx = _call_context(None)
    assert asyncio.run(ToolProfileMiddleware().on_call_tool(ctx, call_next)) == "ok"


def test_current_profile_outside_http_is_the_default():
    assert tool_profiles.current_profile() is DEFAULT_PROFILE


# ── Group state is driven by Unity's register_tools ─────────────────────────

def test_register_tools_replaces_the_enabled_group_set(monkeypatch):
    monkeypatch.setattr(
        "services.registry.get_group_tool_names",
        lambda: {"core": ["manage_scene"], "vfx": ["manage_vfx"], "playtest": ["play_step"]},
    )
    set_server_enabled_groups(DEFAULT_ENABLED_GROUPS)
    PluginHub._sync_server_tool_visibility([{"name": "manage_scene"}, {"name": "manage_vfx"}])
    assert tool_profiles.server_enabled_groups() == frozenset({"core", "vfx"})
    PluginHub._sync_server_tool_visibility([SimpleNamespace(name="play_step")])
    assert tool_profiles.server_enabled_groups() == frozenset({"playtest"})


# ── The Unity registered-tool filter respects a static profile ──────────────

def test_full_profile_skips_the_unity_registered_tool_filter(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(PluginHub, "is_configured", classmethod(lambda cls: True))
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(tool_profiles, "current_profile", lambda: DEFAULT_PROFILE)
    assert middleware._should_filter_tool_listing() is True
    monkeypatch.setattr(tool_profiles, "current_profile", lambda: PROFILES["gamachine"])
    assert middleware._should_filter_tool_listing() is True
    monkeypatch.setattr(tool_profiles, "current_profile", lambda: PROFILES["full"])
    assert middleware._should_filter_tool_listing() is False
