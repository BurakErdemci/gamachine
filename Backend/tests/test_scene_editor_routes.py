"""Scene editor HTTP boundary; all outbound requests are replaced with fakes."""
import asyncio
import io
import json
import threading
import urllib.error
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from routes import scene_editor_routes as routes
    monkeypatch.setenv("LOCAL_APP_TOKEN", "app-secret")
    monkeypatch.setattr(routes.unity_mcp_manager, "api_headers", lambda: {"X-API-Key": "api-secret"})
    app = FastAPI()
    app.include_router(routes.create_scene_editor_router())
    with TestClient(app) as client:
        yield client, routes


def _reply(body, status=200):
    reply = io.BytesIO(json.dumps(body).encode())
    reply.status = status
    return reply


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), [float("inf"), 1, 2]])
def test_set_field_rejects_non_finite_values(client, monkeypatch, value):
    http, routes = client
    post = Mock()
    monkeypatch.setattr(routes.urllib.request, "urlopen", post)
    response = http.post("/scene-editor/set-field", content=json.dumps({"componentId": 20, "field": "mass", "value": value}),
                         headers={"X-Session-Token": "app-secret", "Content-Type": "application/json"})
    assert response.status_code == 422
    post.assert_not_called()


CASES = [
    ("GET", "/scene-editor/tree", None, "gm_editor_tree", {}),
    ("GET", "/scene-editor/inspect/-3384", None, "gm_editor_inspect", {"id": -3384}),
    ("GET", "/scene-editor/version", None, "gm_editor_version", {}),
    ("POST", "/scene-editor/select", {"id": -3384}, "gm_editor_select", {"id": -3384}),
    ("POST", "/scene-editor/select", {"id": None}, "gm_editor_select", {"id": None}),
    ("GET", "/scene-editor/create-menu", None, "gm_editor_create_menu", {}),
    ("POST", "/scene-editor/create", {"item": "GameObject/3D Object/Cube", "parentId": None},
     "gm_editor_create", {"item": "GameObject/3D Object/Cube", "parentId": None}),
    ("POST", "/scene-editor/create", {"item": "GameObject/Camera", "parentId": -3384},
     "gm_editor_create", {"item": "GameObject/Camera", "parentId": -3384}),
    ("POST", "/scene-editor/rename", {"id": -3384, "name": "x.meta"},
     "gm_editor_rename", {"id": -3384, "name": "x.meta"}),
    ("POST", "/scene-editor/set-active", {"id": -3384, "active": False},
     "gm_editor_set_active", {"id": -3384, "active": False}),
    ("POST", "/scene-editor/duplicate", {"id": -3384}, "gm_editor_duplicate", {"id": -3384}),
    ("POST", "/scene-editor/delete", {"id": -3384}, "gm_editor_delete", {"id": -3384}),
    *[("POST", "/scene-editor/set-field", {"componentId": -42, "field": "m_Name", "value": value},
       "gm_editor_set_field", {"componentId": -42, "field": "m_Name", "value": value})
      for value in [True, 7, 1.5, "x.meta", [1, 2.5, 3]]],
    ("POST", "/scene-editor/component-enable", {"componentId": -42, "enabled": False},
     "gm_editor_component_enable", {"componentId": -42, "enabled": False}),
    ("GET", "/scene-editor/component-menu/-3384", None, "gm_editor_component_menu", {"id": -3384}),
    ("POST", "/scene-editor/add-component", {"id": -3384, "item": "Component/Physics/Rigidbody"},
     "gm_editor_add_component", {"id": -3384, "item": "Component/Physics/Rigidbody"}),
    *[("POST", "/scene-editor/component-action", {"componentId": -42, "action": action},
       "gm_editor_component_action", {"componentId": -42, "action": action})
      for action in ["reset", "remove", "up", "down"]],
]


@pytest.mark.parametrize("method,path,body,command,params", CASES)
def test_endpoints_forward_typed_payload_and_headers(client, monkeypatch, method, path, body, command, params):
    http, routes = client
    data = {"success": True} if command == "gm_editor_select" else {"epoch": "test", "id": -3384}
    if command == "gm_editor_version":
        data = {"epoch": "test", "scene": 11, "hierarchy": 3, "props": 8,
                "selection": 2, "selectedId": -3384, "playing": False, "compiling": False}
    if command == "gm_editor_set_field":
        data = {"componentId": -42, "field": {"path": "m_Name", "label": "Name", "kind": "string",
                                              "value": "x.meta", "readonly": False}}
    elif command == "gm_editor_component_enable":
        data = {"componentId": -42, "enabled": False}
    elif command == "gm_editor_component_menu":
        data = {"items": [{"item": "Component/Physics/Rigidbody", "category": "Physics",
                           "label": "Rigidbody", "present": False}]}
    elif command == "gm_editor_add_component":
        data = {"componentId": -42, "type": "UnityEngine.Rigidbody", "label": "Rigidbody"}
    elif command == "gm_editor_component_action":
        data = {"componentId": -42, "action": body["action"]}
    post = Mock(return_value=_reply({"status": "success", "result": {"success": True, "data": data}}))
    monkeypatch.setattr(routes.urllib.request, "urlopen", post)
    response = http.request(method, path, json=body, headers={"X-Session-Token": "app-secret"})
    assert response.status_code == 200
    assert response.json() == data
    post.assert_called_once()
    request = post.call_args.args[0]
    assert request.full_url == "http://localhost:8080/api/command"
    assert request.method == "POST"
    assert json.loads(request.data) == {"type": command, "params": params}
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["x-unityai-maintenance"] == "app-secret"
    assert headers["x-api-key"] == "api-secret"
    assert headers["content-type"] == "application/json"
    assert post.call_args.kwargs == {"timeout": 10}


@pytest.mark.parametrize("method,path,body,command,params", CASES)
@pytest.mark.parametrize("token", [None, "wrong"])
def test_all_endpoints_require_session_token(client, monkeypatch, method, path, body, command, params, token):
    http, routes = client
    post = Mock(side_effect=AssertionError("unauthenticated request forwarded"))
    monkeypatch.setattr(routes.urllib.request, "urlopen", post)
    headers = {} if token is None else {"X-Session-Token": token}
    assert http.request(method, path, json=body, headers=headers).status_code == 401
    post.assert_not_called()


@pytest.mark.parametrize("path", ["select", "create", "component-action"])
@pytest.mark.parametrize("token", [None, "wrong"])
@pytest.mark.parametrize("configured", [False, True])
def test_token_gate_precedes_invalid_body_validation(client, monkeypatch, path, token, configured):
    http, routes = client
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    if not configured:
        monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)
    post = Mock(side_effect=AssertionError("unauthenticated invalid body forwarded"))
    monkeypatch.setattr(routes.urllib.request, "urlopen", post)
    headers = {} if token is None else {"X-Session-Token": token}
    response = http.post(f"/scene-editor/{path}", json={}, headers=headers)
    assert response.status_code == (401 if configured else 503)
    post.assert_not_called()


@pytest.mark.parametrize("path,body", [
    ("create", {"item": 123, "parentId": None}),
    ("create", {"item": "GameObject/Camera", "parentId": True}),
    ("create", {"item": "GameObject/Camera", "parentId": "1"}),
    ("create", {"item": "GameObject/Camera", "parentId": 1.5}),
    ("create", {"parentId": None}),
    ("rename", {"id": -3384, "name": 123}),
    ("rename", {"id": -3384}),
    ("set-active", {"id": -3384, "active": "false"}),
    ("set-active", {"id": -3384, "active": 0}),
    ("set-active", {"id": -3384, "active": None}),
    ("set-active", {"id": -3384}),
    *[(path, {"id": value, **extra})
      for path, extra in [("rename", {"name": "test"}), ("set-active", {"active": True}),
                          ("duplicate", {}), ("delete", {})]
      for value in [True, "1", 1.5, None]],
    ("duplicate", {}),
    ("delete", {}),
    *[(path, {"componentId": value, **extra})
      for path, extra in [("set-field", {"field": "m_Name", "value": "x"}),
                          ("component-enable", {"enabled": True}),
                          ("component-action", {"action": "reset"})]
      for value in [True, "1", 1.5, None]],
    ("set-field", {"componentId": -42, "field": 1, "value": "x"}),
    *[("set-field", {"componentId": -42, "field": "m_Name", "value": value})
      for value in [{"a": 1}, None, [True], ["1"], [[1]]]],
    ("set-field", {"componentId": -42, "field": "m_Name"}),
    *[("component-enable", {"componentId": -42, "enabled": value})
      for value in [1, "true", None]],
    ("component-enable", {"componentId": -42}),
    *[("component-action", {"componentId": -42, "action": value})
      for value in ["delete", 1, None]],
    ("component-action", {"componentId": -42}),
    *[("add-component", {"id": value, "item": "Component/Physics/Rigidbody"})
      for value in [True, "1", 1.5, None]],
    ("add-component", {"id": -3384, "item": 1}),
    ("add-component", {"id": -3384}),
])
def test_write_bodies_reject_wrong_types(client, monkeypatch, path, body):
    http, routes = client
    post = Mock(side_effect=AssertionError("invalid body forwarded"))
    monkeypatch.setattr(routes.urllib.request, "urlopen", post)
    response = http.post(f"/scene-editor/{path}", json=body, headers={"X-Session-Token": "app-secret"})
    assert response.status_code == 422
    post.assert_not_called()


@pytest.mark.parametrize("code", [
    "locked", "compiling", "prefab_part", "invalid_name", "invalid_value",
    "invalid_item", "create_failed", "write_failed",
    "already_present", "required", "add_failed",
])
@pytest.mark.parametrize("status", [200, 409])
def test_write_error_codes_pass_through(client, monkeypatch, code, status):
    http, routes = client
    body = {"status": "success", "result": {"success": False, "message": code}}
    if status >= 400:
        post = Mock(side_effect=urllib.error.HTTPError("mock", status, "failure", {}, _reply(body)))
    else:
        post = Mock(return_value=_reply(body))
    monkeypatch.setattr(routes.urllib.request, "urlopen", post)
    response = http.post("/scene-editor/delete", json={"id": -3384},
                         headers={"X-Session-Token": "app-secret"})
    assert response.status_code == 409
    assert response.json() == {"detail": code}


def test_unknown_write_error_stays_private(client, monkeypatch):
    http, routes = client
    monkeypatch.setattr(routes.urllib.request, "urlopen", Mock(return_value=_reply({
        "success": False, "message": "private write failure"})))
    response = http.post("/scene-editor/delete", json={"id": -3384},
                         headers={"X-Session-Token": "app-secret"})
    assert response.status_code == 502
    assert response.json() == {"detail": "unity_error"}


@pytest.mark.parametrize("body,status,expected,detail", [
    ({"success": False, "error": "not_found"}, 200, 404, "not_found"),
    ({"status": "success", "result": {"success": False, "error": "not_found"}}, 200, 404, "not_found"),
    ({"success": False, "error": "No Unity instances connected"}, 200, 503, "unity_unavailable"),
    ({"success": False, "error": "No Unity instances connected"}, 503, 503, "unity_unavailable"),
    ({"success": False, "error": "broken"}, 200, 502, "unity_error"),
    ({"success": False, "error": "user_only"}, 403, 502, "unity_error"),
    ({"detail": "bad gateway"}, 500, 502, "unity_error"),
    ({"detail": "gateway failure"}, 504, 504, "unity_timeout"),
    ({"status": "success", "result": {"success": True, "data": {}}}, 504, 504, "unity_timeout"),
    ({"success": False, "error": "timeout"}, 200, 504, "unity_timeout"),
    ({"status": "error", "error": "Unity TIMEOUT waiting for editor"}, 502, 504, "unity_timeout"),
    ({"success": False, "message": "Command timeout"}, 200, 504, "unity_timeout"),
])
def test_unity_error_mappings(client, monkeypatch, caplog, body, status, expected, detail):
    http, routes = client
    if status >= 400:
        error = urllib.error.HTTPError("mock", status, "HTTP failure", {}, _reply(body))
        post = Mock(side_effect=error)
    else:
        post = Mock(return_value=_reply(body, status))
    monkeypatch.setattr(routes.urllib.request, "urlopen", post)
    response = http.get("/scene-editor/tree", headers={"X-Session-Token": "app-secret"})
    assert response.status_code == expected
    assert response.json() == {"detail": detail}
    if expected == 502:
        original = body.get("error") or body.get("detail")
        assert original not in response.text
        assert any(record.name == routes.__name__ and record.levelname == "WARNING"
                   and record.getMessage() == f"Unity scene editor error (HTTP {status})" for record in caplog.records)
        assert original not in caplog.text
        assert "app-secret" not in caplog.text
        assert "api-secret" not in caplog.text


@pytest.mark.parametrize("error,status,detail", [
    (urllib.error.URLError(ConnectionRefusedError("down")), 503, "unity_unavailable"),
    (TimeoutError("slow"), 504, "unity_timeout"),
    (urllib.error.URLError(TimeoutError("slow")), 504, "unity_timeout"),
    (ValueError("invalid response"), 502, "unity_error"),
])
def test_transport_error_mappings(client, monkeypatch, caplog, error, status, detail):
    http, routes = client
    monkeypatch.setattr(routes.urllib.request, "urlopen", Mock(side_effect=error))
    response = http.get("/scene-editor/version", headers={"X-Session-Token": "app-secret"})
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    if status == 502:
        assert str(error) not in response.text
        assert str(error) not in caplog.text
        assert "Unity scene editor error: ValueError" in caplog.text
        assert any(record.name == routes.__name__ and record.levelname == "WARNING"
                   for record in caplog.records)


@pytest.mark.parametrize("status,detail", [(500, "unity_error"), (504, "unity_timeout")])
def test_non_json_http_errors_do_not_echo_body(client, monkeypatch, caplog, status, detail):
    http, routes = client
    original = "upstream private failure"
    error = urllib.error.HTTPError("mock", status, "HTTP failure", {}, io.BytesIO(original.encode()))
    monkeypatch.setattr(routes.urllib.request, "urlopen", Mock(side_effect=error))
    response = http.get("/scene-editor/tree", headers={"X-Session-Token": "app-secret"})
    assert response.status_code == (502 if status == 500 else 504)
    assert response.json() == {"detail": detail}
    assert original not in response.text
    if status == 500:
        assert original not in caplog.text
        assert "Unity scene editor error (HTTP 500)" in caplog.text


@pytest.mark.parametrize("body", [[], {"success": True, "data": []}])
def test_invalid_unity_response_is_private(client, monkeypatch, caplog, body):
    http, routes = client
    monkeypatch.setattr(routes.urllib.request, "urlopen", Mock(return_value=_reply(body)))
    response = http.get("/scene-editor/inspect/-3384", headers={"X-Session-Token": "app-secret"})
    assert response.status_code == 502
    assert response.json() == {"detail": "unity_error"}
    assert "Invalid Unity response" not in caplog.text
    assert "Unity scene editor error" in caplog.text
    assert "Invalid Unity response" not in response.text


def test_http_call_runs_off_event_loop(client, monkeypatch):
    _, routes = client
    loop_thread = threading.get_ident()
    worker_threads = []

    def post(request, timeout):
        worker_threads.append(threading.get_ident())
        return _reply({"success": True, "data": {"epoch": "test"}})

    monkeypatch.setattr(routes.urllib.request, "urlopen", post)
    assert asyncio.run(routes._call_unity("gm_editor_version", {})) == {"epoch": "test"}
    assert worker_threads and worker_threads[0] != loop_thread


@pytest.mark.parametrize("message", [
    "boom maint-secret-123 and api-secret-456",
    "boom %6Daint-secret-123 and %61pi-secret-456",
    'boom maint\\u002dsecret-123 and api\\u002dsecret-456',
    "boom maint- secret-123 and api- secret-456",
])
def test_logged_upstream_error_omits_text_and_credentials(monkeypatch, caplog, message):
    import routes.scene_editor_routes as routes_module
    monkeypatch.setenv("LOCAL_APP_TOKEN", "maint-secret-123")
    monkeypatch.setattr(routes_module.unity_mcp_manager, "api_headers", lambda: {"X-API-Key": "api-secret-456"})
    with caplog.at_level("WARNING"):
        with pytest.raises(HTTPException) as caught:
            routes_module._raise_unity_error(message, 500)
    assert caught.value.status_code == 502
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "maint-secret-123" not in logged and "api-secret-456" not in logged
    assert message not in logged
    assert logged == "Unity scene editor error (HTTP 500)"


@pytest.mark.parametrize("headers", [None, {"X-API-Key": 123}])
def test_error_logging_does_not_read_headers(client, monkeypatch, caplog, headers):
    http, routes = client
    api_headers = Mock(side_effect=[{"X-API-Key": "api-secret"}, headers])
    monkeypatch.setattr(routes.unity_mcp_manager, "api_headers", api_headers)
    monkeypatch.setattr(routes.urllib.request, "urlopen", Mock(side_effect=ValueError("private api-secret app-secret")))
    response = http.get("/scene-editor/tree", headers={"X-Session-Token": "app-secret"})
    assert response.status_code == 502
    assert response.json() == {"detail": "unity_error"}
    api_headers.assert_called_once()
    assert "private" not in caplog.text and "api-secret" not in caplog.text and "app-secret" not in caplog.text


def test_api_headers_raising_during_error_handling_still_returns_502(client, monkeypatch, caplog):
    http, routes = client
    api_headers = Mock(side_effect=[{"X-API-Key": "api-secret"}, RuntimeError("private api-secret")])
    monkeypatch.setattr(routes.unity_mcp_manager, "api_headers", api_headers)
    monkeypatch.setattr(routes.urllib.request, "urlopen", Mock(return_value=_reply({"success": False, "error": "private app-secret"})))
    response = http.get("/scene-editor/tree", headers={"X-Session-Token": "app-secret"})
    assert response.status_code == 502
    assert response.json() == {"detail": "unity_error"}
    api_headers.assert_called_once()
    assert "private" not in caplog.text and "api-secret" not in caplog.text and "app-secret" not in caplog.text


@pytest.mark.parametrize("upstream", [False, True])
def test_logging_failure_cannot_change_error_response(client, monkeypatch, upstream):
    http, routes = client
    monkeypatch.setattr(routes.logger, "warning", Mock(side_effect=RuntimeError("broken handler")))
    post = Mock(return_value=_reply({"success": False, "error": "private"})) if upstream else Mock(side_effect=ValueError("private"))
    monkeypatch.setattr(routes.urllib.request, "urlopen", post)
    response = http.get("/scene-editor/tree", headers={"X-Session-Token": "app-secret"})
    assert response.status_code == 502
    assert response.json() == {"detail": "unity_error"}
