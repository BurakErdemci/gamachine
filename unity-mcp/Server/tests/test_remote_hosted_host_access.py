"""In remote-hosted mode no server-side code may touch the server machine's
desktop (window enumeration, capture, dialog press) or read build status files."""

import asyncio
import importlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.tools.manage_build as mb
import transport.plugin_hub as hub
from core.config import config

cap = importlib.import_module("services.tools.unity_window_capture")
dlg = importlib.import_module("services.tools.unity_dialog")


def _boom(*a, **k):
    raise AssertionError("host desktop or file touched in remote-hosted mode")


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    for mod, names in (
        (cap, ("find_unity_dialogs", "find_unity_main_windows", "capture_window", "_target_project")),
        (dlg, ("find_unity_dialogs", "press_dialog_button", "_target_project")),
        (mb, ("find_unity_dialogs", "_read_status_file", "_target_project_path", "_target_project_name")),
        (hub, ("_find_unity_dialogs",)),
    ):
        for name in names:
            monkeypatch.setattr(mod, name, _boom)
    return SimpleNamespace(get_state=AsyncMock(return_value="user"))


def test_window_capture_refused(remote):
    result = asyncio.run(cap.unity_window_capture(remote))
    assert result["success"] is False
    assert "remote" in json.dumps(result).lower()


@pytest.mark.parametrize("action,button", [("list", None), ("press", "OK")])
def test_dialog_refused(remote, action, button):
    result = asyncio.run(dlg.unity_dialog(remote, action=action, button=button))
    assert result["success"] is False
    assert "remote" in json.dumps(result).lower()


def test_build_wait_refused(remote):
    result = asyncio.run(mb.manage_build(remote, action="wait"))
    assert result["success"] is False
    assert "remote" in json.dumps(result).lower()


@pytest.mark.parametrize("job_id", [None, "build-one"])
def test_build_status_forwards_to_unity(remote, monkeypatch, job_id):
    send = AsyncMock(return_value={"success": True, "message": "unity"})
    monkeypatch.setattr(mb, "_send_build_command", send)
    result = asyncio.run(mb.manage_build(remote, action="status", job_id=job_id))
    assert result == {"success": True, "message": "unity"}
    send.assert_awaited_once()


def test_hub_dialog_scan_is_empty(remote):
    assert hub._scoped_dialogs("Game") == []


def test_local_mode_still_probes(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    seen = []
    monkeypatch.setattr(hub, "_find_unity_dialogs", lambda: seen.append(1) or [{"title": "T", "buttons": [], "project": "Game"}])
    assert hub._scoped_dialogs("Game") == [{"title": "T", "buttons": [], "project": "Game"}]
    assert seen == [1]
