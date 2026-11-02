"""Connector HTTP routes: auth, strict bodies, 409 rules, plan/install/cancel/remove shapes, remote deny list."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentic import approval_mode
from connectors import catalog, installer as inst
from error_i18n import localized_http_exception_handler
from remote import ui_dispatch
from routes.connector_routes import create_connector_router

TOKEN = "cd" * 32
SECRET = "connectors-secret"
AUTH = {"X-Session-Token": TOKEN}
UI = {**AUTH, "X-Gamachine-UI-Secret": SECRET}
APP = {"version": "5.2.0", "path": "C:/fake/blender.exe"}
ADDON = ('bl_info = {{\n    "name": "MCP for Blender",\n}}\n'
         'ADDON_PROTOCOL_VERSION = {protocol}\n')

WRITES = [
    ("post", "/connectors/blender/plan", None),
    ("post", "/connectors/blender/install", None),
    ("post", "/connectors/blender/install/cancel", None),
    ("post", "/connectors/blender/remove", None),
    ("put", "/connectors/blender/enabled", {"enabled": False}),
    ("put", "/connectors/blender/app-path", {"path": None}),
]


class Runner:
    """Fake uv/Blender; `gate` holds the venv step until set."""

    def __init__(self):
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self, argv, family, env, cwd, register):
        register(MagicMock(poll=lambda: None, kill=self.gate.set))
        if argv[1] == "venv":
            self.gate.wait(10)
            python = Path(argv[2]) / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")
        elif argv[1] == "pip":
            venv = Path(argv[argv.index("--python") + 1]).parents[1]
            (venv / "Scripts" / "mcp-for-blender.exe").write_text("")
            bundled = venv / "Lib" / "site-packages" / "blender_mcp" / "bundled" / "addon.py"
            bundled.parent.mkdir(parents=True)
            bundled.write_text(ADDON.format(protocol=15), encoding="utf-8")
        elif "check(" in argv[-1]:
            return 0, "GAMACHINE_OK True True"
        else:
            return 0, "GAMACHINE_OK True"
        return 0, ""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    approval_mode.set_ui_secret(SECRET)
    rows = {}
    db = MagicMock()
    db.get_setting.side_effect = rows.get
    db.set_setting.side_effect = rows.__setitem__
    runner = Runner()
    installer = inst.Installer(root=tmp_path / "root", runner=runner, uv=lambda: "uv", platform="win32",
                               blender_overrides={"BLENDER_USER_SCRIPTS": str(tmp_path / "scripts")})
    state = SimpleNamespace(detection={"found": [APP], "chosen": APP, "custom_path": None},
                            versions={})
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, localized_http_exception_handler)
    app.include_router(create_connector_router(
        db, installer=installer, entries=catalog.load(),
        detector=lambda minimum, custom: state.detection, connected=lambda cid: False,
        prober=lambda path: state.versions.get(path)))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=db, rows=rows, installer=installer, runner=runner,
                              state=state, tmp=tmp_path)
    runner.gate.set()
    job = installer.job("blender")
    if job and job.thread:
        job.thread.join(10)
    approval_mode._reset_for_tests()


def _install_and_wait(env, body=None):
    response = env.client.post("/connectors/blender/install", headers=UI, json=body)
    assert response.status_code == 202, response.text
    env.installer.job("blender").thread.join(10)
    return response.json()["job"]


def test_every_route_needs_the_session_token(env):
    assert env.client.get("/connectors").status_code == 401
    assert env.client.get("/connectors/blender/install").status_code == 401
    for method, path, body in WRITES:
        assert getattr(env.client, method)(path, json=body, headers={"X-Gamachine-UI-Secret": SECRET}).status_code == 401


@pytest.mark.parametrize("method,path,body", WRITES)
def test_writes_need_the_ui_secret(env, method, path, body):
    response = getattr(env.client, method)(path, json=body, headers=AUTH)
    assert response.status_code == 403
    response = getattr(env.client, method)(path, json=body, headers={**AUTH, "X-Gamachine-UI-Secret": "wrong"})
    assert response.status_code == 403


@pytest.mark.parametrize("method,path,body", WRITES)
def test_writes_are_refused_with_the_maintenance_header(env, method, path, body):
    response = getattr(env.client, method)(path, json=body, headers={**UI, "X-UnityAI-Maintenance": "1"})
    assert response.status_code == 403


def test_list_and_status_shapes(env):
    response = env.client.get("/connectors", headers=AUTH)
    assert response.status_code == 200
    (view,) = response.json()["connectors"]
    assert view["id"] == "blender" and view["state"] == "not_installed"
    assert view["app"]["chosen"] == APP and view["app"]["min_version"] == "4.2"
    assert env.client.get("/connectors/blender/install", headers=AUTH).json() == {"job": None}
    assert env.client.get("/connectors/nope/install", headers=AUTH).status_code == 404
    assert env.client.post("/connectors/nope/plan", headers=UI).status_code == 404


def test_plan_reports_catalog_numbers_without_network(env):
    plan = env.client.post("/connectors/blender/plan", headers=UI).json()
    pkg = catalog.load()["blender"]["package"]
    assert plan["download_mb"] == pkg["approx_download_mb"] and plan["estimated_s"] == pkg["approx_install_s"]
    assert plan["steps"] == ["python", "package", "addon", "check"]
    assert plan["target"] == str(env.installer.version_dir(catalog.load()["blender"]))
    assert plan["app"]["chosen"] == APP and plan["replace_addon"] is None


def test_install_returns_a_job_with_epoch_start_and_is_single_flight(env):
    env.runner.gate.clear()
    response = env.client.post("/connectors/blender/install", headers=UI)
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["state"] == "running" and isinstance(job["started_at"], float) and job["started_at"] > 1.7e9
    assert env.client.post("/connectors/blender/install", headers=UI).status_code == 409
    assert env.client.post("/connectors/blender/remove", headers=UI).status_code == 409
    assert env.client.get("/connectors", headers=AUTH).json()["connectors"][0]["state"] == "installing"
    assert env.client.post("/connectors/blender/install/cancel", headers=UI).json() == {"cancelled": True}
    status = env.client.get("/connectors/blender/install", headers=AUTH).json()["job"]
    assert status["state"] == "cancelled"
    assert not env.installer.version_dir(catalog.load()["blender"]).exists()


def test_install_refused_without_a_supported_app(env):
    env.state.detection = {"found": [], "chosen": None, "custom_path": None}
    assert env.client.post("/connectors/blender/install", headers=UI).status_code == 409
    env.state.detection = {"found": [], "chosen": {"version": "4.1.0", "path": "x"}, "custom_path": "x"}
    assert env.client.post("/connectors/blender/install", headers=UI).status_code == 409


def test_incompatible_addon_needs_replace_flag(env):
    old = env.tmp / "scripts" / "addons" / "blender_mcp.py"
    old.parent.mkdir(parents=True)
    old.write_text(ADDON.format(protocol=9), encoding="utf-8")
    plan = env.client.post("/connectors/blender/plan", headers=UI).json()
    assert plan["replace_addon"] == {"path": str(old), "found_protocol": 9, "needed_protocol": 15}
    response = env.client.post("/connectors/blender/install", headers={**UI, "X-UI-Lang": "en"})
    assert response.status_code == 409 and "confirmation" in response.json()["detail"]
    assert env.client.post("/connectors/blender/install", headers=UI,
                           json={"replace_addon": "yes"}).status_code == 400
    job = _install_and_wait(env, {"replace_addon": True})
    assert env.installer.job("blender").state == "done", job
    assert old.with_suffix(".py.bak").is_file()


@pytest.mark.parametrize("value", ["true", 1, None])
def test_enabled_body_must_be_a_strict_bool(env, value):
    response = env.client.put("/connectors/blender/enabled", headers=UI, json={"enabled": value})
    assert response.status_code == 400


def test_enable_conflicts_unless_installed(env):
    response = env.client.put("/connectors/blender/enabled", headers=UI, json={"enabled": True})
    assert response.status_code == 409
    assert env.client.put("/connectors/blender/enabled", headers=UI, json={"enabled": False}).status_code == 200
    _install_and_wait(env)
    response = env.client.put("/connectors/blender/enabled", headers=UI, json={"enabled": True})
    assert response.status_code == 200 and response.json()["enabled"] is True
    assert env.rows["connector_blender_enabled"] == "1"


def test_enable_is_allowed_when_an_update_is_available(env):
    _install_and_wait(env)
    record = Path(env.installer.installed(catalog.load()["blender"])["dir"]) / "install.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["version"] = "2.2.5"
    record.write_text(json.dumps(data), encoding="utf-8")
    response = env.client.put("/connectors/blender/enabled", headers=UI, json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["state"] == "update_available" and response.json()["enabled"] is True


def test_remove_clears_the_switch_and_returns_the_view(env):
    _install_and_wait(env)
    env.client.put("/connectors/blender/enabled", headers=UI, json={"enabled": True})
    response = env.client.post("/connectors/blender/remove", headers=UI)
    assert response.status_code == 200
    assert response.json()["state"] == "not_installed" and response.json()["enabled"] is False
    assert env.rows["connector_blender_enabled"] == "0"
    assert not (env.tmp / "scripts" / "addons" / "blender_mcp.py").exists()


def test_app_path_validation(env):
    exe = env.tmp / "Blender" / "blender.exe"
    exe.parent.mkdir()
    exe.write_text("")
    for body in ({}, {"path": 3}):
        assert env.client.put("/connectors/blender/app-path", headers=UI, json=body).status_code == 400
    assert env.client.put("/connectors/blender/app-path", headers=UI,
                          json={"path": str(exe)}).status_code == 400
    assert env.client.put("/connectors/blender/app-path", headers=UI,
                          json={"path": "relative/blender.exe"}).status_code == 400
    env.state.versions[str(exe)] = "5.1.0"
    assert env.client.put("/connectors/blender/app-path", headers=UI, json={"path": str(exe)}).status_code == 200
    assert env.rows["connector_blender_app_path"] == str(exe)
    assert env.client.put("/connectors/blender/app-path", headers=UI, json={"path": None}).status_code == 200
    assert env.rows["connector_blender_app_path"] == ""


def test_remote_dispatch_allows_reads_and_denies_writes():
    assert ui_dispatch.http_allowed("GET", "/connectors")
    assert ui_dispatch.http_allowed("GET", "/connectors/blender/install")
    for method, path, _ in WRITES:
        assert not ui_dispatch.http_allowed(method.upper(), path)


def test_remove_that_leaves_files_answers_409_naming_them_and_a_retry_finishes(env, monkeypatch):
    _install_and_wait(env)
    addon = env.tmp / "scripts" / "addons" / "blender_mcp.py"
    real = inst._remove_written
    monkeypatch.setattr(inst, "_remove_written", lambda path, sha, restore: f"{path}: in use")
    response = env.client.post("/connectors/blender/remove", headers=UI)
    assert response.status_code == 409
    assert response.json()["detail"].startswith("Bağlayıcı tamamen kaldırılamadı; kalanlar: ")
    assert str(addon) in response.json()["detail"]
    english = env.client.post("/connectors/blender/remove", headers={**UI, "X-UI-Lang": "en"})
    assert english.json()["detail"].startswith("The connector could not be fully removed; still there: ")
    view = env.client.get("/connectors", headers=AUTH).json()["connectors"][0]
    assert view["state"] == "error" and view["error"]["code"] == "remove_incomplete"
    monkeypatch.setattr(inst, "_remove_written", real)
    response = env.client.post("/connectors/blender/remove", headers=UI)
    assert response.status_code == 200 and response.json()["state"] == "not_installed"
    assert not addon.exists()


def test_install_answers_409_while_remove_runs(env, monkeypatch):
    _install_and_wait(env)
    inside, release = threading.Event(), threading.Event()

    def slow_disable(addon):
        inside.set()
        release.wait(10)
    monkeypatch.setattr(env.installer, "_disable", slow_disable)
    replies = []
    thread = threading.Thread(target=lambda: replies.append(env.client.post("/connectors/blender/remove",
                                                                            headers=UI)))
    thread.start()
    assert inside.wait(5)
    response = env.client.post("/connectors/blender/install", headers=UI)
    assert response.status_code == 409 and response.json()["detail"] == "Bağlayıcı kaldırılırken kurulamaz."
    english = env.client.post("/connectors/blender/install", headers={**UI, "X-UI-Lang": "en"})
    assert english.json()["detail"] == "A connector cannot be installed while it is being removed."
    again = env.client.post("/connectors/blender/remove", headers={**UI, "X-UI-Lang": "en"})
    assert again.status_code == 409 and again.json()["detail"] == "This connector is already being removed."
    release.set()
    thread.join(10)
    assert replies[0].status_code == 200 and replies[0].json()["state"] == "not_installed"


def test_remove_whose_folder_stays_answers_409_and_shows_remove_incomplete(env, monkeypatch):
    _install_and_wait(env)
    real = inst.shutil.rmtree

    def locked(path, *args, **kwargs):
        raise PermissionError(32, "in use", str(path))
    monkeypatch.setattr(inst.shutil, "rmtree", locked)
    response = env.client.post("/connectors/blender/remove", headers=UI)
    assert response.status_code == 409 and "kalanlar: not deleted: " in response.json()["detail"]
    view = env.client.get("/connectors", headers=AUTH).json()["connectors"][0]
    assert view["state"] == "error" and view["error"]["code"] == "remove_incomplete"
    monkeypatch.setattr(inst.shutil, "rmtree", real)
    response = env.client.post("/connectors/blender/remove", headers=UI)
    assert response.status_code == 200 and response.json()["state"] == "not_installed"


def test_plan_reports_another_addons_file_under_our_name(env):
    foreign = env.tmp / "scripts" / "addons" / "blender_mcp.py"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b'bl_info = {"name": "Mine"}\n')
    plan = env.client.post("/connectors/blender/plan", headers=UI).json()
    assert plan["replace_addon"] == {"path": str(foreign), "found_protocol": None, "needed_protocol": 15}
    assert env.client.post("/connectors/blender/install", headers=UI).status_code == 409
    assert foreign.read_bytes() == b'bl_info = {"name": "Mine"}\n'
