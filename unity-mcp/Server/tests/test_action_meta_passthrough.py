"""Unity's per-action ``undo`` and ``warnings`` fields must reach the MCP client.

McpActionJournal (C#) adds them to the result of every mutating command. Several tool
wrappers rebuild their success dict and would drop them; carry_action_meta re-attaches.
"""
import asyncio

import pytest

import transport.unity_transport as unity_transport
from models.unity_response import normalize_unity_response
from transport.unity_transport import carry_action_meta, send_with_unity_instance

UNDO = {"action_id": "ab12cd34", "group": 57, "name": "MCP: manage_gameobject delete #ab12cd34",
        "undoable": False, "note": "file changes"}
WARNINGS = ["prefab_link: deleting 'Wheel' removes an object that comes from prefab instance 'Car'"]


def test_normalize_keeps_meta_of_an_mcp_shaped_result():
    raw = {"status": "success", "result": {"success": True, "message": "ok", "data": {"x": 1},
                                           "undo": UNDO, "warnings": WARNINGS}}
    out = normalize_unity_response(raw)
    assert out["undo"] == UNDO
    assert out["warnings"] == WARNINGS


def test_normalize_lifts_meta_out_of_data_for_a_plain_result():
    raw = {"status": "success", "result": {"message": "ok", "count": 2, "undo": UNDO, "warnings": WARNINGS}}
    out = normalize_unity_response(raw)
    assert out["undo"] == UNDO
    assert out["warnings"] == WARNINGS
    assert out["data"] == {"count": 2}


def test_normalize_without_meta_is_unchanged():
    raw = {"status": "success", "result": {"message": "ok", "count": 2}}
    assert normalize_unity_response(raw) == {
        "success": True, "message": "ok", "error": None, "data": {"count": 2}}


def _rebuilding_tool(send_fn):
    """Mimics manage_editor/manage_gameobject: rebuilds the success dict from three keys."""
    async def tool():
        response = await send_with_unity_instance(send_fn, None, "manage_gameobject", {"action": "delete"})
        return {"success": True, "message": response.get("message"), "data": response.get("data")}
    return tool


@pytest.fixture
def stdio(monkeypatch):
    monkeypatch.setattr(unity_transport, "_is_http_transport", lambda: False)


def test_rebuilt_response_gets_undo_and_warnings_back(stdio):
    async def send_fn(command, params, **kwargs):
        return {"success": True, "message": "deleted", "data": None, "undo": UNDO, "warnings": WARNINGS}

    bare = asyncio.run(_rebuilding_tool(send_fn)())
    assert "undo" not in bare  # what the wrapper alone does

    result = asyncio.run(carry_action_meta(_rebuilding_tool(send_fn))())
    assert result["undo"] == UNDO
    assert result["warnings"] == WARNINGS


def test_meta_survives_the_http_path(monkeypatch):
    monkeypatch.setattr(unity_transport, "_is_http_transport", lambda: True)

    async def fake_resolve():
        return None

    async def fake_send_for_instance(unity_instance, command_type, params, **kwargs):
        return {"status": "success", "result": {"success": True, "message": "deleted",
                                                "undo": UNDO, "warnings": WARNINGS}}

    monkeypatch.setattr(unity_transport, "_resolve_user_id_from_request", fake_resolve)
    monkeypatch.setattr(unity_transport.PluginHub, "send_command_for_instance", fake_send_for_instance)
    result = asyncio.run(carry_action_meta(_rebuilding_tool(None))())
    assert result["undo"] == UNDO
    assert result["warnings"] == WARNINGS


def test_tool_warnings_are_merged_not_replaced(stdio):
    async def send_fn(command, params, **kwargs):
        return {"success": True, "warnings": WARNINGS}

    async def tool():
        await send_with_unity_instance(send_fn, None, "manage_asset", {})
        return {"success": True, "warnings": ["own warning"]}

    result = asyncio.run(carry_action_meta(tool)())
    assert result["warnings"] == ["own warning"] + WARNINGS


def test_a_call_without_meta_is_left_alone(stdio):
    async def send_fn(command, params, **kwargs):
        return {"success": True, "message": "found"}

    result = asyncio.run(carry_action_meta(_rebuilding_tool(send_fn))())
    assert "undo" not in result and "warnings" not in result


def test_meta_does_not_leak_between_calls(stdio):
    responses = iter([{"success": True, "undo": UNDO}, {"success": True}])

    async def send_fn(command, params, **kwargs):
        return next(responses)

    wrapped = carry_action_meta(_rebuilding_tool(send_fn))
    assert asyncio.run(wrapped())["undo"] == UNDO
    assert "undo" not in asyncio.run(wrapped())


class _RecordingMCP:
    def tool(self, **_kwargs):
        return lambda fn: fn


def test_every_registered_tool_is_wrapped(monkeypatch):
    from pathlib import Path
    import services.registry.tool_registry as registry_module
    import services.tools as tools_pkg
    from utils.module_discovery import discover_modules

    list(discover_modules(Path(tools_pkg.__file__).parent, "services.tools"))

    seen = []
    monkeypatch.setattr(tools_pkg, "carry_action_meta", lambda fn: (seen.append(fn), fn)[1])
    # register_all_tools swaps each entry's func in place; other tests need the raw ones.
    saved = [(entry, entry["func"]) for entry in registry_module._tool_registry]
    try:
        tools_pkg.register_all_tools(_RecordingMCP())
    finally:
        for entry, func in saved:
            entry["func"] = func
    raw = [func for _, func in saved]
    assert raw and all(any(f is s for s in seen) for f in raw)
