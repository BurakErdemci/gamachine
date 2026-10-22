"""Foreground panel input requires fresh app and window verification under the lock."""
import asyncio
import itertools
import json
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolResult, TextContent

from computer_use import driver_client, server


PID, PANEL, MAIN = 4242, 404, 7
PANEL_REFUSAL = (
    f"Foreground input not sent: panel window {PANEL} could not be verified as the top window "
    "of the frontmost app (another window or app may be on top). Read the app's windows "
    "again with computer_list_windows.")
WINDOW_REFUSAL = (
    f"Foreground input not sent: window {PANEL} could not be verified as the front window "
    "(another window, for example Gamachine, may be on top). Ask the user to bring the app "
    "to the front, or use background delivery with element_token.")
FRONT_CALLS = [
    ("bring_to_front", {"pid": PID, "window_id": PANEL}),
    ("bring_to_front", {"pid": PID, "window_id": PANEL}),
    ("bring_to_front", {"pid": PID}),
]


def cg_row(number=PANEL, layer=8, **updates):
    return {
        "kCGWindowNumber": number, "kCGWindowOwnerPID": PID, "kCGWindowLayer": layer,
        "kCGWindowIsOnscreen": True, "kCGWindowAlpha": 1.0,
        "kCGWindowBounds": {"X": 300, "Y": 200, "Width": 880, "Height": 448},
        **updates,
    }


@pytest.fixture
def panel_driver(monkeypatch):
    driver = AsyncMock()
    handler = server.ComputerServer(driver)
    top = [cg_row(), cg_row(MAIN, 0)]
    state = {"snapshots": itertools.repeat(top), "front": PID, "flags": {}}
    action = CallToolResult(content=[TextContent(type="text", text="input sent")])

    def lookup():
        assert handler._front_lock.locked()
        value = next(state["snapshots"])
        if isinstance(value, Exception):
            raise value
        return value

    async def frontmost():
        assert handler._front_lock.locked()
        value = state["front"]
        if isinstance(value, Exception):
            raise value
        return value

    async def call(tool, args):
        assert handler._front_lock.locked()
        if tool == "bring_to_front":
            return CallToolResult(content=[], structured_content=state["flags"], is_error=True)
        if tool == "run_steps":
            return CallToolResult(content=[], structured_content={
                "ok": True, "steps": [{"ok": True}]})
        assert tool in ("click", "press_key", "list_windows")
        return action

    async def sleep(delay):
        assert handler._front_lock.locked()
        assert delay == 0.3

    lookup_mock = Mock(side_effect=lookup)
    monkeypatch.setattr(driver_client, "_cg_window_list", lookup_mock)
    monkeypatch.setattr(driver_client, "identify_running_app", AsyncMock(
        return_value=("com.unity3d.UnityEditor5.x", "Unity")))
    monkeypatch.setattr(handler, "_runtime_driver", AsyncMock(return_value=driver))
    monkeypatch.setattr(server.approval_bridge, "request_approval", AsyncMock(
        return_value={"approved": True}))
    monkeypatch.setattr(server, "_batch_stop", AsyncMock(return_value=None))
    monkeypatch.setattr(server.asyncio, "sleep", AsyncMock(side_effect=sleep))
    driver.frontmost_pid.side_effect = frontmost
    driver.call.side_effect = call
    return handler, driver, state, lookup_mock, action


def send(handler, tool):
    args = {"pid": PID, "window_id": PANEL}
    if tool == "run_steps":
        args["steps"] = [
            {"press_key": {"key": "n", "delivery_mode": "foreground"}},
            {"press_key": {"key": "return"}},
        ]
    else:
        args.update(delivery_mode="foreground")
        args.update({"x": 1, "y": 2} if tool == "click" else {"key": "n"})
    result = asyncio.run(handler.call("computer_" + tool, args))
    assert not handler._front_lock.locked()
    return result


def assert_refused(result, tool, message):
    assert result.is_error
    if tool == "run_steps":
        report = json.loads(result.content[0].text)
        assert report["failed_step"] == 0
        assert report["steps"] == [{"index": 0, "ok": False,
                                    "code": "foreground_unverified", "message": message}]
    else:
        assert result.content[0].text == message


@pytest.mark.parametrize("tool", ["click", "press_key", "run_steps"])
def test_verified_panel_sends_input_under_lock(panel_driver, tool):
    handler, driver, state, lookup, action = panel_driver
    # Even dialog-focus flags must yield to a verified panel target.
    state["flags"] = {"process_activated": True, "focused": False}
    result = send(handler, tool)
    assert not result.is_error
    calls = [entry.args for entry in driver.call.await_args_list]
    assert calls[:3] == FRONT_CALLS
    assert [name for name, _ in calls[3:]] == ([tool] * 2 if tool == "run_steps" else [tool])
    if tool != "run_steps":
        assert result is action
    driver.frontmost_pid.assert_awaited_once_with()
    assert lookup.call_count == 2
    server.asyncio.sleep.assert_awaited_once_with(0.3)


@pytest.mark.parametrize("tool", ["click", "press_key", "run_steps"])
@pytest.mark.parametrize("failure", ["other_app", "no_app", "other_window", "offscreen",
                                     "transparent", "cg_raises", "cg_none", "front_raises"])
def test_panel_verification_failure_never_sends_input(panel_driver, tool, failure):
    handler, driver, state, lookup, _ = panel_driver
    top = [cg_row(), cg_row(MAIN, 0)]
    fresh = top
    if failure == "other_app":
        state["front"] = 77
    elif failure == "no_app":
        state["front"] = None
    elif failure == "other_window":
        fresh = [cg_row(MAIN, 0), cg_row()]
    elif failure == "offscreen":
        fresh = [cg_row(kCGWindowIsOnscreen=False), cg_row(MAIN, 0)]
    elif failure == "transparent":
        fresh = [cg_row(kCGWindowAlpha=0), cg_row(MAIN, 0)]
    elif failure == "cg_raises":
        fresh = RuntimeError("CG verification failed")
    elif failure == "cg_none":
        fresh = None
    elif failure == "front_raises":
        state["front"] = RuntimeError("frontmost lookup failed")
    state["snapshots"] = iter([top, fresh, fresh])
    state["flags"] = {"process_activated": True, "focused": False}
    assert_refused(send(handler, tool), tool, PANEL_REFUSAL)
    assert [entry.args for entry in driver.call.await_args_list] == FRONT_CALLS
    assert lookup.call_count >= 1


@pytest.mark.parametrize("raw", [
    [cg_row(kCGWindowOwnerPID=77)], [cg_row(layer=0)], [cg_row(number=999)],
    [cg_row(layer=24)], [cg_row(kCGWindowIsOnscreen=False)], [cg_row(kCGWindowAlpha=0)],
    None, RuntimeError("initial CG lookup failed"),
])
@pytest.mark.parametrize("dialog", [False, True])
def test_ineligible_panel_keeps_existing_refusals(panel_driver, raw, dialog):
    handler, driver, state, lookup, _ = panel_driver
    state["snapshots"] = iter([raw])
    if dialog:
        state["flags"] = {"process_activated": True, "focused": False}
    expected = (
        "Foreground input not sent: the app is in front, but another of its windows has focus, "
        f"not window {PANEL} (usually a dialog or panel such as Go to Folder, Open or Save). "
        "Call computer_list_windows for this pid and send the input to that window_id."
    ) if dialog else WINDOW_REFUSAL
    assert_refused(send(handler, "click"), "click", expected)
    assert [entry.args for entry in driver.call.await_args_list] == (
        FRONT_CALLS[:2] + ([("list_windows", {"pid": PID})] if dialog else []))
    driver.frontmost_pid.assert_not_awaited()
    assert lookup.call_count == 1


@pytest.mark.parametrize("first_failure", ["app", "window"])
def test_panel_verification_retries_with_fresh_snapshot(panel_driver, first_failure):
    handler, driver, state, lookup, _ = panel_driver
    top = [cg_row(), cg_row(MAIN, 0)]
    buried = [cg_row(MAIN, 0), cg_row()]
    state["snapshots"] = iter([top, buried if first_failure == "window" else top, top])
    if first_failure == "app":
        async def frontmost():
            assert handler._front_lock.locked()
            return 77 if driver.frontmost_pid.await_count == 1 else PID
        driver.frontmost_pid.side_effect = frontmost
    result = send(handler, "press_key")
    assert not result.is_error
    assert [entry.args for entry in driver.call.await_args_list][:3] == FRONT_CALLS
    assert lookup.call_count == 3
    assert driver.frontmost_pid.await_count == 2
    assert server.asyncio.sleep.await_count == 2


def test_top_window_filter_ignores_ineligible_rows(panel_driver):
    handler, _, state, _, _ = panel_driver
    top = [cg_row(), cg_row(MAIN, 0)]
    ignored = [cg_row(90, 24), cg_row(91, -1), cg_row(92, 0, kCGWindowOwnerPID=77),
               cg_row(93, 0, kCGWindowIsOnscreen=False), cg_row(94, 0, kCGWindowAlpha=0)]
    state["snapshots"] = iter([top, ignored + top])
    assert not send(handler, "click").is_error


def test_each_foreground_batch_step_verifies_panel_again(panel_driver):
    handler, driver, state, _, _ = panel_driver
    top = [cg_row(), cg_row(MAIN, 0)]
    buried = [cg_row(MAIN, 0), cg_row()]
    state["snapshots"] = iter([top, top, top, buried, buried])
    result = asyncio.run(handler.call("computer_run_steps", {
        "pid": PID, "window_id": PANEL, "delivery_mode": "foreground",
        "steps": [{"press_key": {"key": "n"}}, {"press_key": {"key": "return"}}]}))
    report = json.loads(result.content[0].text)
    assert result.is_error and report["failed_step"] == 1
    assert report["steps"][0]["ok"] is True
    assert report["steps"][1]["message"] == PANEL_REFUSAL
    calls = [entry.args for entry in driver.call.await_args_list]
    assert calls[:3] == FRONT_CALLS and calls[4:] == FRONT_CALLS
    assert calls[3][0] == "run_steps"
    assert not handler._front_lock.locked()
