"""gamachineConnectors registration: registered only while a connector is switched on and installed."""
import json
import sys
import types
from pathlib import Path

import pytest

from connectors import catalog, launcher, registration, settings
from connectors.installer import Installer
from spawn_env import CONVERSATION_ENV

BLENDER = {"version": "5.2.0", "path": "C:/Blender/blender.exe"}


def detector(app):
    return lambda min_version, custom_path=None: {"found": [app] if app else [], "chosen": app,
                                                  "custom_path": None}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A tmp install root, the real catalog, a fake Blender detection and a settings store."""
    values = {}
    db = types.SimpleNamespace(get_setting=values.get, set_setting=values.__setitem__)
    monkeypatch.setattr(settings, "_store", db)
    state = types.SimpleNamespace(db=db, root=tmp_path / "connectors", app=BLENDER)
    launcher.bind(installer=Installer(root=state.root), entries=catalog.load(),
                  detector=lambda *a, **k: detector(state.app)(*a, **k))
    yield state
    launcher.bind()


def install(root: Path, version: str = "2.2.6") -> Path:
    vdir = root / "blender" / version
    exe = vdir / "venv" / "Scripts" / "mcp-for-blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    (vdir / "install.json").write_text(json.dumps({"version": version, "entry": str(exe),
                                                   "finished_at": "2026-11-02T00:00:00+00:00"}))
    return exe


def test_names():
    assert registration.SERVER_NAME == "gamachineConnectors"
    assert registration.CLAUDE_TOOL_PREFIX == "mcp__gamachineConnectors__"


@pytest.mark.parametrize("frozen", [False, True])
def test_server_argv_for_dev_and_frozen_builds(monkeypatch, frozen):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    expected = [sys.executable]
    if not frozen:
        expected.append(str(Path(registration.__file__).resolve().parents[1] / "main.py"))
    expected.append("connectors-mcp-server")
    assert registration.server_argv() == expected


def test_nothing_is_registered_without_a_store(monkeypatch):
    monkeypatch.setattr(settings, "_store", None)
    assert launcher.ready_ids() == []
    assert registration.stdio_entry(5) is None and registration.codex_thread_entry(["A"]) is None


@pytest.mark.parametrize("enabled,installed,app,expected", [
    (False, True, BLENDER, False),                       # switch off
    (True, False, BLENDER, False),                       # not installed
    (True, True, None, False),                           # Blender gone
    (True, True, {"version": "3.6.0", "path": "x"}, False),  # too old
    (True, True, BLENDER, True),                         # ready
])
def test_registered_only_when_enabled_and_installed(world, enabled, installed, app, expected):
    settings.set_enabled(world.db, "blender", enabled)
    if installed:
        install(world.root)
    world.app = app
    assert (launcher.ready_ids() == ["blender"]) is expected
    assert (registration.claude_server_entry(5) is not None) is expected
    assert (registration.codex_thread_entry(None) is not None) is expected


def test_an_older_installed_version_still_counts(world):
    settings.set_enabled(world.db, "blender", True)
    install(world.root, version="2.2.5")
    assert launcher.state("blender") == "update_available"
    assert launcher.ready_ids() == ["blender"]


def test_a_broken_install_is_not_registered(world):
    settings.set_enabled(world.db, "blender", True)
    install(world.root).unlink()
    assert launcher.ready_ids() == []


@pytest.mark.parametrize("bad", [None, 0, -1, "5", True, 1.0])
def test_claude_entry_needs_a_positive_integer_chat(world, bad):
    settings.set_enabled(world.db, "blender", True)
    install(world.root)
    assert registration.claude_server_entry(bad) is None


def test_entries_name_the_chat_backend_ids_and_root(world, monkeypatch):
    settings.set_enabled(world.db, "blender", True)
    install(world.root)
    monkeypatch.delenv("ANTIGRAVITY_URL", raising=False)
    monkeypatch.setenv("UNITYAI_URL", "http://localhost:9999")
    argv = registration.server_argv()
    env = {"GAMACHINE_CONNECTORS": "blender", "GAMACHINE_CONNECTORS_ROOT": str(world.root),
           "UNITYAI_URL": "http://localhost:9999"}
    assert registration.claude_server_entry(5) == {"type": "stdio", "command": argv[0], "args": argv[1:],
                                                   "env": {CONVERSATION_ENV: "5", **env}}
    assert CONVERSATION_ENV not in registration.stdio_entry()["env"]
    codex = registration.codex_thread_entry(["LOCAL_APP_TOKEN"])
    assert codex["env"] == env and codex["env_vars"] == ["LOCAL_APP_TOKEN"]
    assert codex["default_tools_approval_mode"] == "approve" and codex["tool_timeout_sec"] >= 180
    assert "env_vars" not in registration.codex_thread_entry(None)
    assert registration.served() == (("blender",), str(world.root))


def test_served_is_empty_while_nothing_is_ready(world):
    assert registration.served() == ((), "")


@pytest.mark.parametrize("cid,tool,enabled,installed,needle", [
    ("nope", "look", True, True, "Unknown connector"),
    (None, "look", True, True, "Unknown connector"),
    ("blender", "generate_3d", True, True, "no tool"),
    ("blender", "disable_telemetry", True, True, "no tool"),
    ("blender", 5, True, True, "no tool"),
    ("blender", "look", False, True, "switched off"),
    ("blender", "look", True, False, "not installed"),
    ("blender", "look", True, True, None),
    ("blender", "open", True, True, None),
])
def test_connector_refusal(world, cid, tool, enabled, installed, needle):
    settings.set_enabled(world.db, "blender", enabled)
    if installed:
        install(world.root)
    refusal = launcher.refusal(cid, tool, world.db)
    assert refusal is None if needle is None else needle in refusal
