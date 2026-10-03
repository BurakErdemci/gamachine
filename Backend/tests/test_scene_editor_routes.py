"""Scene editor HTTP boundary; all outbound requests are replaced with fakes."""
import asyncio
import io
import json
import threading
import urllib.error
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
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


CASES = [
    ("GET", "/scene-editor/tree", None, "gm_editor_tree", {}),
    ("GET", "/scene-editor/inspect/-3384", None, "gm_editor_inspect", {"id": -3384}),
    ("GET", "/scene-editor/version", None, "gm_editor_version", {}),
    ("POST", "/scene-editor/select", {"id": -3384}, "gm_editor_select", {"id": -3384}),
    ("POST", "/scene-editor/select", {"id": None}, "gm_editor_select", {"id": None}),
]


@pytest.mark.parametrize("method,path,body,command,params", CASES)
def test_endpoints_forward_typed_payload_and_headers(client, monkeypatch, method, path, body, command, params):
    http, routes = client
    data = {"success": True} if command == "gm_editor_select" else {"epoch": "test", "id": -3384}
    if command == "gm_editor_version":
        data = {"epoch": "test", "scene": 11, "hierarchy": 3, "props": 8,
                "selection": 2, "selectedId": -3384, "playing": False, "compiling": False}
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
                   and original in record.getMessage() for record in caplog.records)
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
        assert str(error) in caplog.text
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
        assert original in caplog.text


@pytest.mark.parametrize("body", [[], {"success": True, "data": []}])
def test_invalid_unity_response_is_private(client, monkeypatch, caplog, body):
    http, routes = client
    monkeypatch.setattr(routes.urllib.request, "urlopen", Mock(return_value=_reply(body)))
    response = http.get("/scene-editor/inspect/-3384", headers={"X-Session-Token": "app-secret"})
    assert response.status_code == 502
    assert response.json() == {"detail": "unity_error"}
    assert "Invalid Unity response" in caplog.text
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


def test_logged_upstream_error_masks_the_route_secrets(monkeypatch, caplog):
    """verify-f1: an upstream message that echoes the maintenance token or the API key
    must not carry them into the log."""
    import routes.scene_editor_routes as routes_module
    monkeypatch.setenv("LOCAL_APP_TOKEN", "maint-secret-123")
    monkeypatch.setattr(routes_module.unity_mcp_manager, "api_headers", lambda: {"X-API-Key": "api-secret-456"})
    with caplog.at_level("WARNING"):
        try:
            routes_module._raise_unity_error("boom maint-secret-123 and api-secret-456 " + "x" * 2000, 500)
        except Exception:
            pass
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "maint-secret-123" not in logged and "api-secret-456" not in logged
    assert "<REDACTED>" in logged
    assert len(logged) < 700
