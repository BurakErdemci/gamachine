"""Display name, 2 Oct 2026: storage, normalization and the session gate."""
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import DatabaseManager
from routes.auth_routes import create_auth_router


H = {"X-Session-Token": "display-name-token"}


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("LOCAL_APP_TOKEN", H["X-Session-Token"])
    db = DatabaseManager(str(tmp_path / "name.db"))
    app = FastAPI()
    app.include_router(create_auth_router(db))
    with TestClient(app) as client:
        yield client, db
    db.flush_ledger()


def test_default_and_persisted_name(client_db):
    client, db = client_db
    initial = client.get("/me", headers=H).json()
    assert initial == {"user_id": 1, "id": 1, "username": "local", "name": "local",
                       "email": "local@localhost", "avatar": ""}
    response = client.put("/me/name", json={"name": "Burak"}, headers=H)
    assert response.status_code == 200 and response.json() == {"name": "Burak"}
    assert db.get_setting("user_display_name") == "Burak"
    assert client.get("/me", headers=H).json() == {**initial, "name": "Burak"}
    app = FastAPI()
    app.include_router(create_auth_router(db))
    with TestClient(app) as other_client:
        assert other_client.get("/me", headers=H).json()["name"] == "Burak"


def test_normalization(client_db):
    client, db = client_db
    response = client.put("/me/name", headers=H,
                          json={"name": "  Bu\x00rak\u202e\u200f\t   Ada\u00a0 \n  "})
    assert response.status_code == 200 and response.json() == {"name": "Burak Ada"}
    assert db.get_setting("user_display_name") == "Burak Ada"


def test_length_after_normalization_and_no_write_on_rejection(client_db):
    client, db = client_db
    response = client.put("/me/name", headers=H, json={"name": "a" * 41})
    assert response.status_code == 422 and response.json() == {"detail": "name_too_long"}
    assert db.get_setting("user_display_name") is None
    name = "a" * 40
    response = client.put("/me/name", headers=H, json={"name": "  " + name + "\u202e  "})
    assert response.status_code == 200 and response.json() == {"name": name}
    assert client.get("/me", headers=H).json()["name"] == name
    assert client.put("/me/name", headers=H, json={"name": "b" * 41}).status_code == 422
    assert db.get_setting("user_display_name") == name


@pytest.mark.parametrize("name", ["", "   ", "\x00\u202e\u200f", "LOCAL", " local ", "LoCaL"])
def test_clear_name(client_db, name):
    client, db = client_db
    db.set_setting("user_display_name", "Burak")
    response = client.put("/me/name", headers=H, json={"name": name})
    assert response.status_code == 200 and response.json() == {"name": ""}
    assert db.get_setting("user_display_name") == ""
    assert client.get("/me", headers=H).json()["name"] == "local"


@pytest.mark.parametrize("name", [None, 123, True, [], {}])
def test_non_string_rejected(client_db, name):
    client, db = client_db
    assert client.put("/me/name", headers=H, json={"name": name}).status_code == 422
    assert db.get_setting("user_display_name") is None


@pytest.mark.parametrize("headers", [{}, {"X-Session-Token": "wrong"}])
def test_token_required(client_db, headers):
    client, db = client_db
    assert client.get("/me", headers=headers).status_code == 401
    assert client.put("/me/name", headers=headers, json={"name": "Burak"}).status_code == 401
    assert db.get_setting("user_display_name") is None
