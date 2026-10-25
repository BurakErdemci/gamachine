import asyncio
import subprocess
from unittest.mock import AsyncMock, Mock, call

import pytest

from unity_ai_mcp import unity_mcp_manager as module
from unity_ai_mcp import unity_project_presence


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "build_spawn_env", Mock(return_value={"PATH": "system-tools"}))
    pid = Mock(return_value=123)
    run = Mock(return_value=Mock(returncode=0, stdout="/Applications/X/Unity.app/Contents/MacOS/Unity\n"))
    monkeypatch.setattr(unity_project_presence, "editor_pid", pid)
    monkeypatch.setattr(module.subprocess, "run", run)
    return pid, run


def test_activates_the_project_editor_bundle_once(environment):
    pid, run = environment
    module._bring_editor_forward("GameA")
    pid.assert_called_once_with("GameA")
    assert run.call_args_list == [
        call(["ps", "-o", "comm=", "-p", "123"], timeout=5, capture_output=True, text=True, env={"PATH": "system-tools"}),
        call(["open", "-a", "/Applications/X/Unity.app"], timeout=5, env={"PATH": "system-tools"}),
    ]


def test_no_pid_does_not_run_commands(environment):
    pid, run = environment
    pid.return_value = None
    module._bring_editor_forward("GameA")
    run.assert_not_called()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_other_platforms_do_not_probe_or_run(environment, monkeypatch, platform):
    pid, run = environment
    monkeypatch.setattr(module.sys, "platform", platform)
    module._bring_editor_forward("GameA")
    pid.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize("output,returncode", [("Unity", 0), ("/bin/Unity", 0), ("/X.app/Unity", 1)])
def test_invalid_or_failed_ps_does_not_open(environment, output, returncode):
    _, run = environment
    run.return_value = Mock(stdout=output, returncode=returncode)
    module._bring_editor_forward("GameA")
    assert run.call_count == 1


@pytest.mark.parametrize("stage", ["pid", "ps", "open"])
def test_errors_never_escape(environment, stage):
    pid, run = environment
    failure = subprocess.TimeoutExpired("command", 5)
    if stage == "pid":
        pid.side_effect = OSError("unavailable")
    elif stage == "ps":
        run.side_effect = failure
    else:
        run.side_effect = [Mock(returncode=0, stdout="/X/Unity.app/Contents/MacOS/Unity"), failure]
    module._bring_editor_forward("GameA")


@pytest.mark.parametrize("stopped", [False, True])
def test_readiness_brings_forward_only_after_a_successful_write(monkeypatch, stopped):
    manager = module.UnityMCPManager()
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(manager, "is_running", Mock(side_effect=[True, not stopped]))
    actions = Mock()
    monkeypatch.setattr(manager, "_write_autoconnect_script", actions.write)
    monkeypatch.setattr(module, "_bring_editor_forward", actions.forward)
    assert asyncio.run(manager.write_autoconnect_when_ready("GameA", timeout=1)) is (not stopped)
    assert actions.mock_calls == ([] if stopped else [call.write("GameA", auto_start=True), call.forward("GameA")])


def test_failed_write_does_not_bring_forward(monkeypatch):
    manager = module.UnityMCPManager()
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(manager, "is_running", Mock(return_value=True))
    monkeypatch.setattr(manager, "_write_autoconnect_script", Mock(side_effect=OSError("write failed")))
    forward = Mock()
    monkeypatch.setattr(module, "_bring_editor_forward", forward)
    with pytest.raises(OSError, match="write failed"):
        asyncio.run(manager.write_autoconnect_when_ready("GameA", timeout=1))
    forward.assert_not_called()
