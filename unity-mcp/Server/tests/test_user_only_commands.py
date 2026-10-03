"""User scene-editor commands cannot cross the agent dispatch boundary."""
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from services import protection_rules
from transport import approval_gate, unity_instance_middleware


WRITE_COMMANDS = [
    ("gm_editor_create_menu", {}),
    ("gm_editor_create", {"item": "GameObject/3D Object/Cube", "parentId": None}),
    ("gm_editor_rename", {"id": -4, "name": "x.meta"}),
    ("gm_editor_set_active", {"id": -4, "active": False}),
    ("gm_editor_duplicate", {"id": -4}),
    ("gm_editor_delete", {"id": -4}),
    ("gm_editor_set_field", {"componentId": -42, "field": "m_Name", "value": "x.meta"}),
    ("gm_editor_component_enable", {"componentId": -42, "enabled": False}),
    ("gm_editor_component_menu", {"id": -4}),
    ("gm_editor_add_component", {"id": -4, "item": "Component/Physics/Rigidbody"}),
    ("gm_editor_component_action", {"componentId": -42, "action": "reset"}),
]


@pytest.mark.parametrize("name,params", [
    *WRITE_COMMANDS,
    *[("batch_execute", {"commands": [{"tool": name, "params": params}]})
      for name, params in WRITE_COMMANDS],
    ("gm_editor_tree", {}),
    ("GM_EDITOR_VERSION", None),
    ("batch_execute", {"commands": [{"tool": "gm_editor_select", "params": {"id": -4}}]}),
    ("batch_execute", {"commands": json.dumps([{ "tool": "gm_editor_tree" }])}),
    ("batch_execute", {"Commands": [], "com_mands": [{"Tool": "gm_editor_tree"}]}),
    ("batch_execute", {"commands": [{"tool": "manage_scene", "Tool": "GM_EDITOR_TREE"}]}),
    ("BATCH_EXECUTE", {"commands": [{"tool": "gm_editor_tree"}]}),
    ("execute_custom_tool", {"tool_name": "gm_editor_inspect"}),
    ("execute_custom_tool", {"name": "gm_editor_inspect"}),
    ("execute_custom_tool", {"tool_name": "ordinary", "ToolName": "gm_editor_tree"}),
    ("execute_custom_tool", {"tool_name": "batch_execute", "parameters": {
        "commands": [{"tool": "gm_editor_tree"}]}}),
    ("batch_execute", {"commands": [{"tool": "execute_custom_tool", "params": {
        "name": "gm_editor_inspect"}}]}),
])
def test_refuses_direct_and_nested_calls(name, params):
    assert "user_only" in protection_rules.user_only_refusal(name, params)


@pytest.mark.parametrize("name,params", [
    ("manage_scene", {"action": "get_hierarchy"}),
    ("batch_execute", {"commands": [{"tool": "manage_scene", "params": {}}]}),
    ("execute_custom_tool", {"tool_name": "ordinary"}),
    ("manage_gameobject", {"name": "gm_editor_tree"}),
    ("gm_editorial", {}),
])
def test_ordinary_calls_are_unchanged(name, params):
    assert protection_rules.user_only_refusal(name, params) is None


def test_deep_batch_cannot_escape_refusal():
    name, params = "gm_editor_tree", {}
    for _ in range(25):
        params = {"commands": [{"tool": name, "params": params}]}
        name = "batch_execute"
    assert protection_rules.user_only_refusal(name, params)


@pytest.mark.parametrize("name,params", [
    *WRITE_COMMANDS,
    ("gm_editor_tree", {}),
    ("execute_custom_tool", {"tool_name": "gm_editor_inspect"}),
    ("batch_execute", {"commands": [{"tool": "gm_editor_select"}]}),
])
def test_middleware_refuses_before_approval(monkeypatch, name, params):
    middleware = unity_instance_middleware.UnityInstanceMiddleware()
    inject, approve, forward = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(middleware, "_inject_unity_instance", inject)
    monkeypatch.setattr(middleware, "_require_approval", approve)
    context = SimpleNamespace(message=SimpleNamespace(name=name, arguments=params))
    with pytest.raises(unity_instance_middleware.ToolError, match="user_only"):
        asyncio.run(middleware.on_call_tool(context, forward))
    approve.assert_not_awaited()
    forward.assert_not_awaited()


@pytest.mark.parametrize("name,params", [WRITE_COMMANDS[1], WRITE_COMMANDS[2],
                                       WRITE_COMMANDS[6], WRITE_COMMANDS[9]])
def test_write_values_are_not_file_paths(name, params):
    assert protection_rules.meta_refusal(name, params) is None


@pytest.fixture
def raw_route(monkeypatch):
    # Compile the actual route alone: constructing this environment's FastMCP
    # fails on existing cache arguments, and importing main configures disk logs.
    source = Path(__file__).parent.parent / "src" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    handler = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == "cli_command_route")
    handler.decorator_list = []
    send = AsyncMock(return_value={"status": "success", "result": {
        "success": True, "data": {"epoch": "test", "version": 1}}})
    sessions = AsyncMock(return_value=SimpleNamespace(sessions={"s1": SimpleNamespace(
        project="Test", hash="abc")}))
    gate = AsyncMock(side_effect=AssertionError("approval must not be called"))
    monkeypatch.setattr(approval_gate, "kapiyi_gec", gate)
    monkeypatch.setenv("LOCAL_APP_TOKEN", "app-secret")
    namespace = {
        "Request": Request, "JSONResponse": JSONResponse,
        "_require_local_token": lambda request: None,
        "PluginHub": SimpleNamespace(get_sessions=sessions, send_command=send),
        "_normalize_instance_token": lambda token: (None, token),
        "logger": SimpleNamespace(exception=lambda *args: None),
    }
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["cli_command_route"], send, sessions, gate


def _request(body, maintenance=None):
    encoded = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": encoded, "more_body": False}

    headers = [] if maintenance is None else [(b"x-unityai-maintenance", maintenance.encode())]
    return Request({"type": "http", "method": "POST", "path": "/api/command",
                    "headers": headers}, receive)


@pytest.mark.parametrize("maintenance", [None, "wrong", ""])
@pytest.mark.parametrize("name,params", [("gm_editor_tree", {}), *WRITE_COMMANDS])
def test_raw_route_refuses_without_valid_maintenance(raw_route, maintenance, name, params):
    route, send, sessions, _ = raw_route
    response = asyncio.run(route(_request({"type": name, "params": params}, maintenance)))
    assert response.status_code == 403
    assert json.loads(response.body) == {"success": False, "error": "user_only"}
    send.assert_not_awaited()
    sessions.assert_not_awaited()


@pytest.mark.parametrize("name,params", [
    ("gm_editor_tree", {}), ("gm_editor_inspect", {"id": -3384}),
    ("gm_editor_version", {}), ("gm_editor_select", {"id": None}),
    *WRITE_COMMANDS,
])
def test_raw_route_forwards_user_calls_unchanged(raw_route, name, params):
    route, send, _, gate = raw_route
    response = asyncio.run(route(_request({"type": name, "params": params}, "app-secret")))
    assert response.status_code == 200
    assert json.loads(response.body)["result"]["data"]["epoch"] == "test"
    send.assert_awaited_once_with("s1", name, params)
    gate.assert_not_awaited()


@pytest.mark.parametrize("name,params", [
    *[("batch_execute", {"commands": [{"tool": name, "params": params}]})
      for name, params in WRITE_COMMANDS],
    ("batch_execute", {"commands": [{"tool": "gm_editor_tree"}]}),
    ("batch_execute", {"commands": json.dumps([{ "tool": "gm_editor_tree" }])}),
    ("execute_custom_tool", {"tool_name": "gm_editor_inspect"}),
])
def test_raw_route_refuses_nested_calls_even_with_maintenance(raw_route, name, params):
    route, send, sessions, _ = raw_route
    response = asyncio.run(route(_request({"type": name, "params": params}, "app-secret")))
    assert response.status_code == 403
    assert json.loads(response.body)["error"] == "user_only"
    send.assert_not_awaited()
    sessions.assert_not_awaited()
