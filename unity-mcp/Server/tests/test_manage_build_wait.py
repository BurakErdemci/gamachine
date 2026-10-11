"""Build status and waiting must work without the Unity command channel."""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.tools.manage_build as mb
from transport.plugin_hub import PluginHub


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(mb, "_target_project_path", AsyncMock(return_value=str(tmp_path)))
    monkeypatch.setattr(mb, "_target_project_name", AsyncMock(return_value="Game"))
    monkeypatch.setattr(mb, "find_unity_dialogs", lambda: [])
    monkeypatch.setattr(mb, "POLL_INTERVAL_SECONDS", 0)
    return tmp_path


@pytest.fixture
def send(monkeypatch):
    mocked = AsyncMock(return_value={"success": True, "message": "unity"})
    monkeypatch.setattr(mb, "_send_build_command", mocked)
    return mocked


def write_status(project, state, job_id=None, pid=None):
    directory = project / "Library" / "GamachineBuilds"
    directory.mkdir(parents=True, exist_ok=True)
    record = {"job_id": "build-one", "state": state, "editor_pid": pid or os.getpid()}
    path = directory / (f"{job_id}.json" if job_id else "latest.json")
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


@pytest.mark.parametrize("state", ["pending", "building"])
@pytest.mark.parametrize("job_id", [None, "build-one"])
def test_status_from_file(project, send, state, job_id):
    record = write_status(project, state, job_id)
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="status", job_id=job_id))
    assert result["success"] is True
    assert result["data"] == record
    assert "Use action=wait" in result["message"]
    send.assert_not_awaited()


def test_status_forwards_without_file(project, send):
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="status"))
    assert result["message"] == "unity"
    assert send.await_args.args[1] == {"action": "status"}


@pytest.mark.parametrize("state", ["succeeded", "failed"])
def test_status_forwards_terminal_file(project, send, state):
    write_status(project, state, "build-one")
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="status", job_id="build-one"))
    assert result["message"] == "unity"
    assert send.await_args.args[1] == {"action": "status", "job_id": "build-one"}


def test_interrupted_file(project, send, monkeypatch):
    write_status(project, "building", pid=1234)
    monkeypatch.setattr(mb, "_is_process_alive", lambda pid: False)
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="status"))
    assert result["success"] is False
    assert result["data"]["state"] == "interrupted"
    assert result["message"] == "Unity exited before the build finished."
    send.assert_not_awaited()


def test_literal_interrupted_file(project, send):
    record = write_status(project, "interrupted")
    record["error_message"] = "Unity reloaded scripts before the build finished; start the build again."
    (project / "Library" / "GamachineBuilds" / "latest.json").write_text(json.dumps(record))
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="status"))
    assert result == {"success": False, "message": record["error_message"], "data": record}
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="wait"))
    assert result == {"success": False, "message": "Build interrupted.", "data": record}
    send.assert_not_awaited()


def test_wait_stays_with_initial_job_when_latest_moves(project, send, monkeypatch):
    write_status(project, "building")
    write_status(project, "building", "build-one")

    async def transition(_seconds):
        write_status(project, "failed", "build-one")
        directory = project / "Library" / "GamachineBuilds"
        other = {"job_id": "build-two", "state": "succeeded", "editor_pid": os.getpid()}
        for name in ("build-two.json", "latest.json"):
            (directory / name).write_text(json.dumps(other))

    sleep = AsyncMock(side_effect=transition)
    monkeypatch.setattr(mb.asyncio, "sleep", sleep)
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="wait"))
    assert result["success"] is False
    assert result["message"] == "Build failed."
    assert result["data"]["job_id"] == "build-one"
    assert result["data"]["state"] == "failed"
    sleep.assert_awaited_once()
    send.assert_not_awaited()


@pytest.mark.parametrize("job_id", [None, "", 0, [], "..", "sub/job", "sub" + chr(92) + "job", "sub:job"])
def test_wait_keeps_latest_for_unusable_initial_id(project, send, monkeypatch, job_id):
    directory = project / "Library" / "GamachineBuilds"
    record = write_status(project, "building")
    record["job_id"] = job_id
    (directory / "latest.json").write_text(json.dumps(record))

    async def transition(_seconds):
        write_status(project, "succeeded")

    sleep = AsyncMock(side_effect=transition)
    monkeypatch.setattr(mb.asyncio, "sleep", sleep)
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="wait"))
    assert result["success"] is True
    assert result["data"]["state"] == "succeeded"
    sleep.assert_awaited_once()
    send.assert_not_awaited()


def test_wait_observes_transition(project, send):
    write_status(project, "building", "build-one")

    async def exercise():
        async def complete():
            await asyncio.sleep(0)
            write_status(project, "succeeded", "build-one")

        task = asyncio.create_task(complete())
        result = await mb.manage_build(SimpleNamespace(), action="wait", job_id="build-one")
        await task
        return result

    result = asyncio.run(exercise())
    assert result["success"] is True
    assert result["message"] == "Build succeeded."
    assert result["data"]["state"] == "succeeded"
    send.assert_not_awaited()


@pytest.mark.parametrize("state", ["failed", "interrupted"])
def test_wait_terminal_failure(project, send, state):
    write_status(project, state)
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="wait"))
    assert result["success"] is False
    assert result["message"] == f"Build {state}."
    send.assert_not_awaited()


@pytest.mark.parametrize("timeout, bounded", [(None, 600), (0, 1), (1900, 1800), (2, 2)])
def test_wait_timeout(project, send, monkeypatch, timeout, bounded):
    write_status(project, "building")
    times = iter([10, 10, 10 + bounded])
    monkeypatch.setattr(mb, "monotonic", lambda: next(times))
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="wait", timeout_seconds=timeout))
    assert result["success"] is False
    assert result["message"] == f"Build still building after {bounded} s; call wait again."
    assert result["data"]["state"] == "building"
    send.assert_not_awaited()


def test_wait_dialog(project, send, monkeypatch):
    record = write_status(project, "building")
    dialog = {"title": "Save changes?", "buttons": ["Save", "Cancel"], "project": "Game"}
    other = {"title": "Other project", "buttons": ["OK"], "project": "Other"}
    monkeypatch.setattr(mb, "find_unity_dialogs", lambda: [other, dialog])
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="wait"))
    assert result["success"] is False
    assert result["hint"] == "user_action"
    assert "'Save changes?' (buttons: Save, Cancel)" in result["message"]
    assert "Answer it with unity_dialog." in result["message"]
    assert result["data"] == {"dialogs": [dialog], "build": record}
    send.assert_not_awaited()


def test_wait_no_file(project, send):
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="wait"))
    assert result == {"success": False, "message": "No build status file found. Start the build with manage_build action=build first."}
    send.assert_not_awaited()


def test_wait_no_project(send, monkeypatch):
    monkeypatch.setattr(mb, "_target_project_path", AsyncMock(return_value=None))
    result = asyncio.run(mb.manage_build(SimpleNamespace(), action="wait"))
    assert result == {"success": False, "message": "Cannot locate the Unity project to watch its build status."}
    send.assert_not_awaited()


@pytest.mark.parametrize("job_id", ["..", "../latest", "sub/job", "sub" + chr(92) + "job"])
def test_read_rejects_traversal(project, job_id):
    write_status(project, "building")
    assert mb._read_status_file(str(project), job_id) is None


@pytest.mark.parametrize("payload", ["{", "[]", "null"])
def test_invalid_status_file(project, payload):
    write_status(project, "building")
    (project / "Library" / "GamachineBuilds" / "latest.json").write_text(payload)
    assert mb._read_status_file(str(project), None) is None


def test_get_project_path(monkeypatch, tmp_path):
    first = SimpleNamespace(project_hash="hash-one", project_path=str(tmp_path / "one"))
    second = SimpleNamespace(project_hash="hash-two", project_path=str(tmp_path / "two"))
    registry = SimpleNamespace(list_sessions=AsyncMock(return_value={"one": first, "two": second}))
    monkeypatch.setattr(PluginHub, "_registry", registry)
    assert asyncio.run(PluginHub.get_project_path("hash-two", user_id="user")) == second.project_path
    registry.list_sessions.assert_awaited_with(user_id="user")
    assert asyncio.run(PluginHub.get_project_path("missing")) is None
    assert asyncio.run(PluginHub.get_project_path(None)) is None
    registry.list_sessions.return_value = {"one": first}
    assert asyncio.run(PluginHub.get_project_path(None)) == first.project_path
    registry.list_sessions.return_value = {}
    assert asyncio.run(PluginHub.get_project_path(None)) is None
    monkeypatch.setattr(PluginHub, "_registry", None)
    assert asyncio.run(PluginHub.get_project_path("hash-one")) is None


@pytest.mark.parametrize("instance", ["Game@hash-one", "hash-one", "Game", None])
def test_target_path_resolution(monkeypatch, instance, tmp_path):
    monkeypatch.setattr(mb, "get_unity_instance_from_context", AsyncMock(return_value=instance))
    monkeypatch.setattr(mb.config, "http_remote_hosted", True)
    ctx = SimpleNamespace(get_state=AsyncMock(return_value="user"))
    session = SimpleNamespace(hash="hash-one", project="Game")
    monkeypatch.setattr(PluginHub, "get_sessions", AsyncMock(return_value=SimpleNamespace(sessions={"one": session})))
    path = AsyncMock(side_effect=lambda project_hash, user_id=None: str(tmp_path) if project_hash in ("hash-one", None) else None)
    monkeypatch.setattr(PluginHub, "get_project_path", path)
    assert asyncio.run(mb._target_project_path(ctx)) == str(tmp_path)
    path.assert_awaited_with(None if instance is None else "hash-one", user_id="user")


def test_target_path_missing_context(monkeypatch):
    monkeypatch.setattr(mb.config, "http_remote_hosted", True)
    assert asyncio.run(mb._target_project_path(SimpleNamespace())) is None


def test_current_process_alive():
    assert mb._is_process_alive(os.getpid()) is True
