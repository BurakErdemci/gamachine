"""Tests for per-project scene writing mode storage, guide integration, and workspace routes."""

import asyncio
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_guide
import scene_mode
from agentic import agent_runner
from database import DatabaseManager
from routes.workspace_routes import create_workspace_router
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager


def _make_unity_project(path):
    (path / "Assets").mkdir(parents=True, exist_ok=True)
    (path / "ProjectSettings").mkdir(parents=True, exist_ok=True)
    return path


def test_defaults_and_resilience(tmp_path):
    assert scene_mode.MODES == ("code", "mixed", "team")
    assert scene_mode.DEFAULT == "mixed"

    project = _make_unity_project(tmp_path / "proj")
    assert scene_mode.get(str(project)) == "mixed"
    assert scene_mode.get("") == "mixed"
    assert scene_mode.get(str(tmp_path / "missing")) == "mixed"

    gamachine_dir = project / "UserSettings" / "Gamachine"
    gamachine_dir.mkdir(parents=True)
    mode_file = gamachine_dir / "scene_mode.json"

    mode_file.write_text("{not valid json", encoding="utf-8")
    assert scene_mode.get(str(project)) == "mixed"

    for bad_payload in ({"mode": "bogus"}, {"mode": 123}, {"mode": None}, ["team"], {}):
        mode_file.write_text(json.dumps(bad_payload), encoding="utf-8")
        assert scene_mode.get(str(project)) == "mixed"

    # Unreadable target (directory at file path) must still never raise.
    mode_file.unlink()
    mode_file.mkdir()
    assert scene_mode.get(str(project)) == "mixed"


def test_set_get_round_trip_and_atomic_write(tmp_path):
    project = _make_unity_project(tmp_path / "proj")
    for mode in scene_mode.MODES:
        assert scene_mode.set(str(project), mode) == mode
        assert scene_mode.get(str(project)) == mode
        gamachine_dir = project / "UserSettings" / "Gamachine"
        entries = [p.name for p in gamachine_dir.iterdir()]
        assert entries == ["scene_mode.json"]
        data = json.loads((gamachine_dir / "scene_mode.json").read_text(encoding="utf-8"))
        assert data == {"mode": mode}


def test_lane_maps_to_main_project(tmp_path):
    project = _make_unity_project(tmp_path / "proj")
    lane = project / ".gamachine" / "lanes" / "helper-1"
    lane.mkdir(parents=True)

    assert os.path.normcase(scene_mode.project_of(str(lane))) == os.path.normcase(os.path.realpath(str(project)))

    assert scene_mode.set(str(project), "team") == "team"
    assert scene_mode.get(str(lane)) == "team"

    assert scene_mode.set(str(lane), "code") == "code"
    assert scene_mode.get(str(project)) == "code"
    assert not (lane / "UserSettings").exists()

    # Invalid lane name (uppercase) must not map to parent project.
    bad_lane = project / ".gamachine" / "lanes" / "INVALID"
    bad_lane.mkdir(parents=True)
    assert scene_mode.project_of(str(bad_lane)) == str(bad_lane)


def test_refuses_invalid_mode_and_non_unity_folder(tmp_path):
    project = _make_unity_project(tmp_path / "proj")
    with pytest.raises(ValueError):
        scene_mode.set(str(project), "bogus")
    with pytest.raises(ValueError):
        scene_mode.set(str(project), None)

    plain = tmp_path / "not_unity"
    plain.mkdir()
    with pytest.raises(ValueError, match="not a Unity project"):
        scene_mode.set(str(plain), "code")

    only_assets = tmp_path / "only_assets"
    (only_assets / "Assets").mkdir(parents=True)
    with pytest.raises(ValueError, match="not a Unity project"):
        scene_mode.set(str(only_assets), "code")


def test_refuses_junction_usersettings_gamachine(tmp_path):
    if os.name != "nt":
        pytest.skip("NTFS junction test applies on Windows")

    project = _make_unity_project(tmp_path / "proj")
    outside = tmp_path / "outside"
    outside.mkdir()
    user_settings = project / "UserSettings"
    user_settings.mkdir()
    gamachine_dir = user_settings / "Gamachine"

    res = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(gamachine_dir), str(outside)],
        capture_output=True, text=True, timeout=15,
    )
    if res.returncode != 0:
        pytest.skip(f"mklink /J unavailable: {(res.stderr or res.stdout).strip()}")

    with pytest.raises(ValueError):
        scene_mode.set(str(project), "team")
    assert not (outside / "scene_mode.json").exists()


def test_refuses_symlink_or_outside_realpath(tmp_path, monkeypatch):
    project = _make_unity_project(tmp_path / "proj")
    gamachine_dir = project / "UserSettings" / "Gamachine"
    gamachine_dir.mkdir(parents=True)
    target = gamachine_dir / "scene_mode.json"

    monkeypatch.setattr(scene_mode.os.path, "islink", lambda p: os.path.normcase(p) == os.path.normcase(str(gamachine_dir)))
    with pytest.raises(ValueError):
        scene_mode.set(str(project), "code")
    assert not target.exists()


@pytest.mark.parametrize("language,first_lines", [
    ("en", {
        "code": "Scene writing mode: Code (fastest).",
        "mixed": "Scene writing mode: Mixed (systems and behaviour in scripts; level layout in scenes and prefabs; tuning values as [SerializeField] fields in the Inspector).",
        "team": "Scene writing mode: Team (work like a Unity developer in a team).",
    }),
    ("tr", {
        "code": "Sahne yazım modu: Kod (en hızlı).",
        "mixed": "Sahne yazım modu: Karma (sistemler ve davranış script'lerde; bölüm yerleşimi sahnede ve prefab'larda; ayar değerleri Inspector'da [SerializeField] alanlar olarak).",
        "team": "Sahne yazım modu: Ekip (ekipteki bir Unity geliştiricisi gibi çalış).",
    }),
])
def test_guide_compose_and_sections_follow_scene_mode(language, first_lines):
    for mode, expected_line in first_lines.items():
        composed = agent_guide.compose(language, project_open=True, unity_state="off", scene_mode=mode)
        assert expected_line in composed
        for other_mode, other_line in first_lines.items():
            if other_mode != mode:
                assert other_line not in composed

        sec_map = {item["id"]: item["text"] for item in agent_guide.sections(language, scene_mode=mode)}
        assert sec_map["PROJECT"].startswith(expected_line)

    # Default and unknown mode -> mixed.
    default_guide = agent_guide.compose(language, project_open=True, unity_state="off")
    mixed_guide = agent_guide.compose(language, project_open=True, unity_state="off", scene_mode="mixed")
    unknown_guide = agent_guide.compose(language, project_open=True, unity_state="off", scene_mode="bogus")
    assert default_guide == mixed_guide == unknown_guide
    assert first_lines["mixed"] in default_guide

    # Existing PROJECT body remains present for mixed.
    existing_body = (
        "You work in the user's Unity project folder. Keep game objects real:"
        if language == "en" else
        "Kullanıcının Unity proje klasöründe çalışıyorsun. Oyun nesneleri gerçek olsun:"
    )
    assert existing_body in mixed_guide

    # provider_guide passes scene_mode through.
    provider = SimpleNamespace(_language=language)
    pg = agent_guide.provider_guide(provider, project_open=True, unity_running=False, scene_mode="team")
    assert first_lines["team"] in pg


def test_agent_runner_passes_workspace_scene_mode_and_tolerates_errors(tmp_path, monkeypatch):
    project = _make_unity_project(tmp_path / "proj")
    scene_mode.set(str(project), "team")

    monkeypatch.setattr(unity_mcp_manager, "enabled", False)
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(agent_runner, "get_addendum", lambda: None)

    runner = agent_runner.AgentRunner.__new__(agent_runner.AgentRunner)
    runner.workspace_path = str(project)
    runner.language = "en"
    asyncio.run(runner._prepare_agent_guide())
    assert "Scene writing mode: Team" in runner._agent_guide

    # Error inside scene_mode.get falls back to None (mixed) without failing.
    monkeypatch.setattr(scene_mode, "get", lambda ws: (_ for _ in ()).throw(RuntimeError("boom")))
    asyncio.run(runner._prepare_agent_guide())
    assert "Scene writing mode: Mixed" in runner._agent_guide


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db = DatabaseManager(str(tmp_path / "scene_mode.db"))
    app = FastAPI()
    app.include_router(create_workspace_router(db))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=db, tmp_path=tmp_path)


def test_workspace_scene_mode_routes_round_trip_and_errors(route_env, monkeypatch):
    client = route_env.client
    db = route_env.db

    # No workspace open: GET returns default + null workspace; POST returns 409.
    res = client.get("/workspace/scene-mode")
    assert res.status_code == 200
    assert res.json() == {"mode": "mixed", "modes": ["code", "mixed", "team"], "workspace": None}

    res = client.post("/workspace/scene-mode", json={"mode": "code"})
    assert res.status_code == 409

    # Non-Unity workspace open: POST returns 400 with reason.
    non_unity = route_env.tmp_path / "plain"
    non_unity.mkdir()
    db.save_workspace(1, str(non_unity))
    res = client.post("/workspace/scene-mode", json={"mode": "code"})
    assert res.status_code == 400
    assert "not a Unity project" in res.json()["detail"]

    # Valid Unity workspace open: GET/POST round trip and 400 on invalid mode/body.
    project = _make_unity_project(route_env.tmp_path / "proj")
    db.save_workspace(1, str(project))

    res = client.get("/workspace/scene-mode")
    assert res.status_code == 200
    assert res.json() == {"mode": "mixed", "modes": ["code", "mixed", "team"], "workspace": str(project)}

    res = client.post("/workspace/scene-mode", json={"mode": "team"})
    assert res.status_code == 200
    assert res.json()["mode"] == "team"
    assert scene_mode.get(str(project)) == "team"

    res = client.get("/workspace/scene-mode")
    assert res.status_code == 200
    assert res.json() == {"mode": "team", "modes": ["code", "mixed", "team"], "workspace": str(project)}

    bad = client.post("/workspace/scene-mode", json={"mode": "bogus"})
    assert bad.status_code == 400
    assert "invalid scene mode" in bad.json()["detail"]

    bad_body = client.post("/workspace/scene-mode", json=["code"])
    assert bad_body.status_code == 400

    # Auth guard matches other workspace routes.
    monkeypatch.setenv("LOCAL_APP_TOKEN", "secret-token")
    assert client.get("/workspace/scene-mode").status_code == 401
    assert client.post("/workspace/scene-mode", json={"mode": "code"}).status_code == 401
    ok = client.get("/workspace/scene-mode", headers={"X-Session-Token": "secret-token"})
    assert ok.status_code == 200
    assert ok.json()["mode"] == "team"
