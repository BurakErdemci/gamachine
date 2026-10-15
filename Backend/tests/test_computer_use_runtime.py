"""Only the desktop main process can publish ephemeral driver runtime state."""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode
from routes.conversation_routes import create_conversation_router


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "runtime-token")
    approval_mode.set_ui_secret("runtime-secret")
    db = MagicMock()
    db.db_path = str(tmp_path / "db.sqlite")
    db.get_setting.return_value = None
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app) as client:
        yield client, db
    approval_mode.set_ui_secret("")


AUTH = {"X-Session-Token": "runtime-token", "X-Gamachine-UI-Secret": "runtime-secret"}
STOPPED = {"running": False, "driver_path": None, "socket_path": None}


def test_runtime_round_trip_is_not_persisted(runtime, tmp_path):
    client, db = runtime
    assert client.get("/computer-use/settings", headers=AUTH).json()["runtime"] == STOPPED
    binary = tmp_path / "driver"
    binary.write_bytes(b"fake")
    body = {"running": True, "driver_path": str(binary), "socket_path": str(tmp_path / "driver.sock")}
    response = client.post("/computer-use/runtime", json=body, headers=AUTH)
    assert response.status_code == 200
    assert client.get("/computer-use/settings", headers=AUTH).json()["runtime"] == body
    assert client.post("/computer-use/runtime", json=STOPPED, headers=AUTH).status_code == 200
    assert client.get("/computer-use/settings", headers=AUTH).json()["runtime"] == STOPPED
    db.set_setting.assert_not_called()
    fresh = FastAPI()
    fresh.include_router(create_conversation_router(db, {}))
    with TestClient(fresh) as second:
        assert second.get("/computer-use/settings", headers=AUTH).json()["runtime"] == STOPPED


@pytest.mark.parametrize("headers,code", [
    ({}, 401), ({"X-Session-Token": "wrong", "X-Gamachine-UI-Secret": "runtime-secret"}, 401),
    ({"X-Session-Token": "runtime-token"}, 403),
    ({**AUTH, "X-Gamachine-UI-Secret": "wrong"}, 403),
    ({**AUTH, "X-UnityAI-Maintenance": "1"}, 403),
])
def test_runtime_auth(runtime, headers, code):
    client, _ = runtime
    assert client.post("/computer-use/runtime", json=STOPPED, headers=headers).status_code == code


@pytest.mark.parametrize("body", [
    {}, {"running": "false"}, {"running": 1},
    {"running": True, "driver_path": "relative", "socket_path": "relative"},
    {"running": True, "driver_path": None, "socket_path": None},
    {"running": False, "driver_path": 3, "socket_path": None},
])
def test_runtime_rejects_bad_shape(runtime, body):
    client, _ = runtime
    assert client.post("/computer-use/runtime", json=body, headers=AUTH).status_code == 400


def test_running_requires_existing_file_and_absolute_socket(runtime, tmp_path):
    client, _ = runtime
    binary = tmp_path / "driver"
    body = {"running": True, "driver_path": str(binary), "socket_path": str(tmp_path / "socket")}
    assert client.post("/computer-use/runtime", json=body, headers=AUTH).status_code == 400
    binary.mkdir()
    assert client.post("/computer-use/runtime", json=body, headers=AUTH).status_code == 400
    binary.rmdir()
    binary.write_bytes(b"fake")
    assert client.post("/computer-use/runtime", json={**body, "socket_path": "relative"}, headers=AUTH).status_code == 400
    assert client.post("/computer-use/runtime", json={**body, "socket_path": 1}, headers=AUTH).status_code == 400
