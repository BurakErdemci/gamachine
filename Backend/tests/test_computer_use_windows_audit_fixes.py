"""Audit regressions tested with fake Windows facts and no desktop input."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult

from computer_use import platform, server
from computer_use.platform import _win32
from computer_use.platform.windows import WindowsPlatform


class FakeWin32:
    def __init__(self):
        self.windows = [10, 20]
        self.info = {10: self.window(owner=20), 20: self.window()}

    @staticmethod
    def window(**changes):
        return {"visible": True, "cloaked": False, "minimized": False,
                "owner": 0, **changes}

    def set_dpi_awareness(self):
        pass

    def top_level_windows(self):
        return self.windows

    def window_info(self, hwnd):
        return self.info.get(hwnd)

    def window_pid(self, hwnd):
        return 41

    def image_path(self, pid):
        return None


@pytest.fixture
def win(monkeypatch):
    win = WindowsPlatform(win32=FakeWin32())
    monkeypatch.setattr(platform, "current", lambda: win)
    return win


@pytest.fixture
def batch(win, monkeypatch):
    driver = AsyncMock()
    driver.call.return_value = CallToolResult(content=[], structured_content={
        "ok": True, "steps": [{"ok": True}]})
    approvals = AsyncMock()
    approvals.request.return_value = {"approved": True}
    approvals.batch_stop.return_value = None
    handler = server.ComputerServer(driver=driver, approvals=approvals)
    monkeypatch.setattr(handler, "_runtime_driver", AsyncMock(return_value=driver))
    app_id = "win:" + chr(92).join(("c:", "apps", "editor.exe"))
    args = {"pid": 41, "steps": [
        {"type_text": {"text": "first"}},
        {"type_text": {"text": "second"}},
        {"type_text": {"text": "third"}},
    ]}
    return handler, driver, app_id, args


@pytest.mark.parametrize("change", ["different", "invalid", "refused"])
def test_split_batch_stops_when_identity_changes(win, batch, monkeypatch, change):
    handler, driver, app_id, args = batch
    replacement = "win:" + chr(92).join(("c:", "windows", "system32", "cmd.exe"))
    assert win.is_hard_denied(replacement, 41)
    changed = {
        "different": (replacement, "Command Prompt", None),
        "invalid": (None, None, None),
        "refused": (app_id, "Editor", "Token could not be verified"),
    }[change]
    identify = AsyncMock(side_effect=[(app_id, "Editor", None)] * 2 + [changed])
    monkeypatch.setattr(win, "identify", identify)

    result = asyncio.run(handler.call("computer_run_steps", args))

    assert result.is_error is True
    report = json.loads(result.content[0].text)
    assert report["ok"] is False
    assert report["failed_step"] == 1
    assert report["steps"][-1]["index"] == 1
    assert report["steps"][-1]["code"] == "target_changed"
    driver.call.assert_awaited_once_with(
        "run_steps", {"pid": 41, "steps": args["steps"][:1]})
    assert identify.await_count == 3


def test_split_batch_dispatches_all_steps_when_identity_stays_same(win, batch, monkeypatch):
    handler, driver, app_id, args = batch
    dispatch_counts = []

    async def identify(driver_arg, pid):
        assert driver_arg is driver and pid == 41
        dispatch_counts.append(driver.call.await_count)
        return app_id, "Editor", None

    monkeypatch.setattr(win, "identify", identify)

    result = asyncio.run(handler.call("computer_run_steps", args))

    assert not result.is_error
    report = json.loads(result.content[0].text)
    assert report == {
        "ok": True, "executed": 3, "total": 3, "failed_step": None,
        "steps": [{"ok": True, "index": i} for i in range(3)]}
    assert [call.args for call in driver.call.await_args_list] == [
        ("run_steps", {"pid": 41, "steps": [step]}) for step in args["steps"]]
    assert dispatch_counts == [0, 0, 1, 2]


@pytest.mark.parametrize("windows,window_id,expected", [
    ([10, 20], 10, True),
    ([10, 20], 20, True),
    ([20, 10], 10, False),
])
def test_top_window_allows_requested_owned_dialog(win, windows, window_id, expected):
    win.win32.windows = windows
    assert asyncio.run(win.top_window_is(41, window_id)) is expected


def test_self_elevated_refuses_unknown_token(monkeypatch):
    def unreadable(process, information_class):
        assert process == 123 and information_class == 20
        raise OSError("token unavailable")

    monkeypatch.setattr(_win32, "_available", True)
    monkeypatch.setattr(_win32, "_current_process", lambda: 123, raising=False)
    monkeypatch.setattr(_win32, "_read_token", unreadable)
    assert _win32.self_elevated() is True


def test_self_elevated_refuses_failed_token_open(monkeypatch):
    monkeypatch.setattr(_win32, "_available", True)
    monkeypatch.setattr(_win32, "_current_process", lambda: 123, raising=False)
    monkeypatch.setattr(_win32, "_open_token", lambda *args: 0, raising=False)
    assert _win32.self_elevated() is True
