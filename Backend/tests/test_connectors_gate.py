"""Backend gate for the agent connectors server: POST /connectors/gate and POST /connectors/{id}/open."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, approval_policy, cards
from connectors import catalog, launcher, settings, views
from connectors.installer import Installer
from remote import ui_dispatch
from routes.conversation_routes import create_conversation_router

SECRET = "connectors-secret"
UI = {"X-Gamachine-UI-Secret": SECRET}
BLENDER = {"version": "5.2.0", "path": "C:/Blender/blender.exe"}


def install(root):
    vdir = root / "blender" / "2.2.6"
    exe = vdir / "venv" / "Scripts" / "mcp-for-blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    (vdir / "install.json").write_text(json.dumps({"version": "2.2.6", "entry": str(exe),
                                                   "finished_at": "2026-11-02T00:00:00+00:00"}))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda cid: cid in (1, 2))
    rows = {}
    db = MagicMock()
    db.db_path = str(tmp_path / "db.sqlite")
    db.get_setting.side_effect = rows.get
    db.set_setting.side_effect = rows.__setitem__
    db.get_conversation_owner.return_value = 1
    db.get_conversation_parent.return_value = None
    db.get_side_of.return_value = None
    approval_mode.set_ui_secret(SECRET)
    approval_mode.set_mode("auto", source="test")
    cards.reset()
    state = SimpleNamespace(db=db, app=BLENDER, root=tmp_path / "connectors", launched=[], answers=[])
    launcher.bind(installer=Installer(root=state.root), entries=catalog.load(),
                  detector=lambda *a, **k: {"found": [state.app] if state.app else [], "chosen": state.app,
                                            "custom_path": None})
    settings.set_enabled(db, "blender", True)
    install(state.root)

    def launch(entry, path):
        state.launched.append((entry["id"], path))
        return 4321

    def answers(cid):
        return state.answers.pop(0) if state.answers else False
    monkeypatch.setattr(launcher, "launch_app", launch)
    monkeypatch.setattr(views, "bridge_answers", answers)
    monkeypatch.setattr(launcher, "launched", {})
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app, raise_server_exceptions=False) as client:
        state.client = client
        yield state
    cards.reset()
    approval_mode.set_ui_secret("")
    launcher.bind()


def gate(env, tool="execute_blender_code", connector="blender", conversation_id=1):
    response = env.client.post("/connectors/gate", json={
        "conversation_id": conversation_id, "connector": connector, "tool": tool})
    assert response.status_code == 200
    return response.json()


def open_(env, conversation_id=1, cid="blender"):
    response = env.client.post(f"/connectors/{cid}/open", json={"conversation_id": conversation_id})
    assert response.status_code == 200
    return response.json()


def test_ok_for_a_running_turn_with_the_connector_on_and_installed(env):
    for tool in catalog.load()["blender"]["tools"]["allow"]:
        assert gate(env, tool) == {"ok": True}


@pytest.mark.parametrize("conversation_id", [9, None, 0, "1", True])
def test_unknown_owner_is_refused(env, conversation_id):
    reply = gate(env, conversation_id=conversation_id)
    assert reply["ok"] is False and "turn is running" in reply["error"]
    reply = open_(env, conversation_id=conversation_id)
    assert reply["ok"] is False and env.launched == []


def test_other_users_chat_is_refused(env):
    env.db.get_conversation_owner.return_value = 2
    assert gate(env)["ok"] is False


def test_halted_chat_is_refused_until_the_halt_is_lifted(env):
    assert env.client.post("/computer-use/halt/1", headers=UI).status_code == 200
    reply = gate(env)
    assert reply["ok"] is False and "Stop" in reply["error"]
    assert open_(env)["ok"] is False and env.launched == []
    assert gate(env, conversation_id=2) == {"ok": True}


def test_deleted_chat_is_refused(env):
    env.db.get_conversation_owner.side_effect = [1, None]
    reply = gate(env)
    assert reply["ok"] is False and "deleted" in reply["error"]


def test_switched_off_connector_is_refused(env):
    settings.set_enabled(env.db, "blender", False)
    reply = gate(env)
    assert reply["ok"] is False and "switched off" in reply["error"]
    assert open_(env)["ok"] is False and env.launched == []


def test_not_installed_connector_is_refused(env):
    Installer(root=env.root).remove(catalog.load()["blender"])
    reply = gate(env)
    assert reply["ok"] is False and "not installed" in reply["error"]


@pytest.mark.parametrize("connector,tool", [("photoshop", "look"), (None, "look"), ("blender", "generate_3d"),
                                            ("blender", "import_asset"), ("blender", None)])
def test_unknown_connector_or_tool_is_refused(env, connector, tool):
    assert gate(env, tool=tool, connector=connector)["ok"] is False


def test_unknown_connector_cannot_be_opened(env):
    assert open_(env, cid="photoshop")["ok"] is False and env.launched == []


def test_gate_needs_the_app_token(env, monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "t" * 32)
    response = env.client.post("/connectors/gate", json={"conversation_id": 1, "connector": "blender",
                                                         "tool": "look"})
    assert response.status_code in (401, 403)


def test_open_attaches_when_the_bridge_already_answers(env):
    env.answers = [True]
    assert open_(env) == {"ok": True, "already_running": True, "version": "5.2.0"}
    assert env.launched == []


def test_open_starts_the_chosen_app_and_waits_for_its_bridge(env):
    env.answers = [False, False, True]
    assert open_(env) == {"ok": True, "already_running": False, "version": "5.2.0"}
    assert env.launched == [("blender", BLENDER["path"])]
    assert launcher.launched == {"blender": 4321}


def test_open_times_out_with_a_clear_error(env):
    clock = iter(range(0, 1000, 30))
    reply = launcher.open_app("blender", env.db, sleep=lambda s: None, clock=lambda: next(clock))
    assert reply["ok"] is False and "did not answer within 60 s" in reply["error"]
    assert env.launched == [("blender", BLENDER["path"])]


def test_open_without_the_app_says_so(env):
    env.app = None
    reply = launcher.open_app("blender", env.db)
    assert reply["ok"] is False and "not found" in reply["error"] and env.launched == []


def test_open_reports_a_launch_failure(env):
    def fail(entry, path):
        raise OSError("access denied")
    reply = launcher.open_app("blender", env.db, launch=fail)
    assert reply["ok"] is False and "access denied" in reply["error"]


@pytest.mark.parametrize("path", ["/connectors/gate", "/connectors/blender/open"])
def test_phone_channel_cannot_reach_either_route(path):
    assert ui_dispatch.http_allowed("POST", path) is False
