"""Proxy tests use only a fake stdio driver and a patched approval bridge."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from computer_use.driver_client import DriverClient, DriverError, DriverUnavailable
from computer_use.server import ComputerServer, create_server
from computer_use import server


@pytest.fixture
def fake(tmp_path, monkeypatch):
    # At most two child processes in the reconnect test, one in all others.
    fixture = Path(__file__).parent / "fixtures" / "fake_cua_driver.py"
    executable = tmp_path / "cua-driver"
    executable.write_text(
        "#!" + sys.executable + "\nimport sys, runpy\nsys.dont_write_bytecode = True\n"
        + "runpy.run_path(" + repr(str(fixture)) + ", run_name='__main__')\n")
    executable.chmod(0o700)
    log = tmp_path / "calls.jsonl"
    socket = str(tmp_path / "fake-private-socket")
    driver = DriverClient(str(executable), socket)
    monkeypatch.setattr(server, "fetch_runtime", AsyncMock(return_value=(str(executable), socket)))
    # Test-only instrumentation is not inherited by the production client.
    driver.env["FAKE_CUA_LOG"] = str(log)
    approval = AsyncMock(return_value={"approved": True})
    monkeypatch.setattr(server.approval_bridge, "request_approval", approval)
    return driver, log, approval


def calls(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_image_is_forwarded_and_window_snapshot_survives_later_call(fake):
    driver, log, approval = fake

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_window_state", {"pid": 4242, "window_id": 7})
            assert result.content[0].text == "snapshot"
            assert result.content[1].data == "aGVsbG8="
            assert result.content[1].mime_type == "image/png"
            click = await handler.call("computer_click", {"pid": 4242, "window_id": 7, "x": 1, "y": 2})
            assert not click.is_error
        finally:
            await handler.close()
    asyncio.run(run())
    assert len({row["process"] for row in calls(log)}) == 1
    assert [row["tool"] for row in calls(log)] == ["list_apps", "list_apps", "get_window_state",
                                                 "list_apps", "list_apps", "click"]
    params = approval.call_args.kwargs["params"]
    assert params["bundle_id"] == "com.apple.TextEdit" and params["app_name"] == "TextEdit"


@pytest.mark.parametrize("tool,args", [
    ("computer_click", {"pid": 4242, "debug_image_out": "output"}),
    ("computer_type_text", {"pid": 4242, "text": "x", "scope": "desktop"}),
    ("computer_click", {"pid": 4242, "app_name": "Trusted"}),
    ("computer_run_steps", {"pid": 4242, "steps": [{"click": {}, "expect": [{"app": "Terminal"}]}]}),
])
def test_forbidden_calls_touch_neither_driver_nor_approval(fake, tool, args):
    driver, log, approval = fake
    result = asyncio.run(ComputerServer(driver).call(tool, args))
    assert result.is_error
    assert calls(log) == []
    approval.assert_not_called()


@pytest.mark.parametrize("answer", [False, "true", "false", 1, None])
def test_denied_approval_does_not_forward_action(fake, answer):
    driver, log, approval = fake
    approval.return_value = {"approved": answer, "error": "denied"}

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_click", {"pid": 4242, "element_token": "s0000002a:1"})
            assert result.is_error and result.content[0].text == "denied"
        finally:
            await handler.close()
    asyncio.run(run())
    assert [row["tool"] for row in calls(log)] == ["list_apps"]


def test_denied_discovery_never_connects(fake):
    driver, log, approval = fake
    approval.return_value = {"approved": False}
    assert asyncio.run(ComputerServer(driver).call("computer_list_apps", {})).is_error
    assert calls(log) == []


@pytest.mark.parametrize("approved", [True, False])
def test_bring_to_front_requires_foreground_approval_before_forwarding(fake, approved):
    driver, log, approval = fake
    approval.return_value = {"approved": approved, "error": "denied"}
    args = {"pid": 4242, "window_id": 7}

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_bring_to_front", args)
            assert bool(result.is_error) is not approved
            if not approved:
                assert result.content[0].text == "denied"
        finally:
            await handler.close()
    asyncio.run(run())
    approval.assert_awaited_once()
    assert approval.call_args.args == ("computer_bring_to_front",)
    params = approval.call_args.kwargs["params"]
    assert params["foreground"] is True
    assert params["bundle_id"] == "com.apple.TextEdit"
    assert params["pid"] == 4242 and params["window_id"] == 7
    rows = calls(log)
    if approved:
        assert [row["tool"] for row in rows] == ["list_apps", "list_apps", "bring_to_front"]
        assert rows[-1]["args"] == args
    else:
        assert [row["tool"] for row in rows] == ["list_apps"]


@pytest.mark.parametrize("front,window_id,expected_tools,refusal", [
    (None, 7, ["bring_to_front", "click", "bring_to_front"], None),
    ("1", 7, ["bring_to_front", "bring_to_front"], "could not be verified as the front window"),
    ("once", 7, ["bring_to_front", "bring_to_front", "click", "bring_to_front"], None),
    (None, None, [], "Foreground input needs window_id; nothing was sent."),
])
def test_foreground_click_verifies_window_before_input(fake, front, window_id, expected_tools, refusal):
    driver, log, approval = fake
    if front is not None:
        driver.env["FAKE_CUA_FRONT_UNVERIFIED"] = front
    args = {"pid": 4242, "element_token": "s0000002a:1", "delivery_mode": "foreground"}
    if window_id is not None:
        args["window_id"] = window_id

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_click", args)
            assert bool(result.is_error) is (refusal is not None)
            if refusal:
                assert refusal in result.content[0].text
        finally:
            await handler.close()
    asyncio.run(run())
    rows = calls(log)
    assert [row["tool"] for row in rows] == ["list_apps", "list_apps", "list_apps", *expected_tools]
    if refusal is None:
        assert rows[-1]["args"] == {"pid": 77}
        rows = rows[:-1]
    assert all(row["args"] == {"pid": 4242, "window_id": 7}
               for row in rows if row["tool"] == "bring_to_front")


@pytest.mark.parametrize("active", ["4242", "none"])
def test_foreground_click_skips_restore_without_a_different_frontmost_app(fake, active):
    driver, log, _ = fake
    driver.env["FAKE_CUA_ACTIVE"] = active

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_click", {
                "pid": 4242, "window_id": 7, "element_token": "s0000002a:1",
                "delivery_mode": "foreground"})
            assert not result.is_error
        finally:
            await handler.close()
    asyncio.run(run())
    rows = calls(log)
    assert [row["tool"] for row in rows] == [
        "list_apps", "list_apps", "list_apps", "bring_to_front", "click"]
    assert rows[3]["args"] == {"pid": 4242, "window_id": 7}


@pytest.mark.parametrize("action_error", [False, True])
@pytest.mark.parametrize("restore_error", ["none", "result", "exception", "unexpected"])
def test_fronted_call_preserves_action_result_when_restore_fails(action_error, restore_error):
    driver = AsyncMock()
    action = CallToolResult(content=[TextContent(type="text", text="action result")], is_error=action_error)
    guard = CallToolResult(content=[], structured_content={"exact_window_effect": {"verified": True}})
    handler = ComputerServer(driver)

    async def call(tool, args):
        assert handler._front_lock.locked()
        if tool == "click":
            return action
        if args == {"pid": 77}:
            if restore_error == "exception":
                raise DriverError("restore failed")
            if restore_error == "unexpected":
                raise TimeoutError("restore timed out")
            return CallToolResult(content=[], is_error=restore_error == "result")
        return guard

    async def frontmost():
        assert handler._front_lock.locked()
        return 77

    driver.frontmost_pid.side_effect = frontmost
    driver.call.side_effect = call
    refusal, result = asyncio.run(handler._fronted_call(driver, 4242, 7, "click", {"pid": 4242}))
    assert refusal is None and result is action
    assert [entry.args for entry in driver.call.await_args_list] == [
        ("bring_to_front", {"pid": 4242, "window_id": 7}),
        ("click", {"pid": 4242}), ("bring_to_front", {"pid": 77})]


def test_fronted_call_still_forwards_when_frontmost_lookup_raises():
    driver = AsyncMock()
    driver.frontmost_pid.side_effect = RuntimeError("lookup failed")
    guard = CallToolResult(content=[], structured_content={"exact_window_effect": {"verified": True}})
    action = CallToolResult(content=[TextContent(type="text", text="clicked")])
    driver.call.side_effect = [guard, action]
    refusal, result = asyncio.run(ComputerServer(driver)._fronted_call(driver, 4242, 7, "click", {"pid": 4242}))
    assert refusal is None and result is action
    assert [entry.args for entry in driver.call.await_args_list] == [
        ("bring_to_front", {"pid": 4242, "window_id": 7}), ("click", {"pid": 4242})]


def test_background_click_never_fronts_window(fake):
    driver, log, approval = fake
    driver.env["FAKE_CUA_FRONT_UNVERIFIED"] = "1"

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_click", {
                "pid": 4242, "window_id": 7, "element_token": "s0000002a:1",
                "delivery_mode": "background"})
            assert not result.is_error
        finally:
            await handler.close()
    asyncio.run(run())
    assert [row["tool"] for row in calls(log)] == ["list_apps", "list_apps", "click"]


@pytest.mark.parametrize("shorthand", [False, True])
def test_split_steps_stop_before_unverified_foreground_step(fake, monkeypatch, shorthand):
    driver, log, approval = fake
    driver.env["FAKE_CUA_FRONT_UNVERIFIED"] = "1"
    monkeypatch.setattr(server, "_batch_stop", AsyncMock(return_value=None))
    foreground = {"window_id": 7, "delivery_mode": "foreground"}
    step = {"click": foreground} if shorthand else {"tool": "click", "args": foreground}
    steps = [{"type_text": {"text": "hello"}}, step]

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_run_steps", {"pid": 4242, "window_id": 7, "steps": steps})
            assert result.is_error
            report = json.loads(result.content[0].text)
            assert report["failed_step"] == 1 and report["ok"] is False
            assert report["steps"][1] == {
                "index": 1, "ok": False, "code": "foreground_unverified",
                "message": "Foreground input not sent: window 7 could not be verified as the front window "
                           "(another window, for example Gamachine, may be on top). Ask the user to bring "
                           "the app to the front, or use background delivery with element_token."}
        finally:
            await handler.close()
    asyncio.run(run())
    rows = calls(log)
    assert [row["tool"] for row in rows] == [
        "list_apps", "list_apps", "run_steps", "list_apps", "bring_to_front", "bring_to_front"]
    assert rows[2]["args"]["steps"] == steps[:1]
    assert all(row["args"] == {"pid": 4242, "window_id": 7}
               for row in rows if row["tool"] == "bring_to_front")


def test_split_steps_verify_each_inherited_foreground_window(fake, monkeypatch):
    driver, log, approval = fake
    monkeypatch.setattr(server, "_batch_stop", AsyncMock(return_value=None))

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_run_steps", {
                "pid": 4242, "window_id": 7, "delivery_mode": "foreground",
                "steps": [{"click": {}}, {"type_text": {"text": "hello"}}]})
            assert not result.is_error
        finally:
            await handler.close()
    asyncio.run(run())
    rows = calls(log)
    assert [row["tool"] for row in rows] == [
        "list_apps", "list_apps", "list_apps", "bring_to_front", "run_steps",
        "bring_to_front", "run_steps", "bring_to_front"]
    assert [row["args"] for row in rows if row["tool"] == "bring_to_front"] == [
        {"pid": 4242, "window_id": 7},
        {"pid": 4242, "window_id": 7}, {"pid": 77}]


@pytest.fixture
def batch_driver(monkeypatch):
    driver = AsyncMock(binary="bin", socket="sock")
    driver.list_running_apps.return_value = {4242: ("com.apple.finder", "Finder")}
    handler = ComputerServer(driver)
    state = {"active": 77, "steps": 0, "guards": 0, "refuse": None,
             "error": None, "unreadable": False, "failed": False,
             "lookup_error": False, "restore_error": False, "locked": True}
    events = []

    async def frontmost():
        assert handler._front_lock.locked()
        events.append(("frontmost", state["active"]))
        if state["lookup_error"]:
            raise RuntimeError("lookup failed")
        return state["active"]

    async def call(tool, args):
        assert handler._front_lock.locked() is state["locked"]
        events.append((tool, args))
        if tool == "bring_to_front":
            if "window_id" not in args:
                if state["restore_error"]:
                    raise TimeoutError("restore failed")
                state["active"] = args["pid"]
                return CallToolResult(content=[])
            state["guards"] += 1
            verified = state["guards"] < state["refuse"] if state["refuse"] else True
            if verified:
                state["active"] = args["pid"]
            return CallToolResult(content=[], structured_content={
                "exact_window_effect": {"verified": verified}})
        if tool == "run_steps":
            state["steps"] += 1
            if state["error"] == state["steps"]:
                raise DriverError("lost")
            if state["unreadable"]:
                return CallToolResult(content=[TextContent(type="text", text="invalid")])
            return CallToolResult(content=[], structured_content={
                "ok": not state["failed"], "steps": [{"ok": not state["failed"]}]})
        assert tool == "click"
        return CallToolResult(content=[TextContent(type="text", text="clicked")])

    driver.frontmost_pid.side_effect = frontmost
    driver.call.side_effect = call
    monkeypatch.setattr(server, "fetch_runtime", AsyncMock(return_value=("bin", "sock")))
    monkeypatch.setattr(server.approval_bridge, "request_approval", AsyncMock(return_value={"approved": True}))
    monkeypatch.setattr(server, "_batch_stop", AsyncMock(return_value=None))
    monkeypatch.setattr(server.asyncio, "sleep", AsyncMock())
    return handler, driver, state, events


def foreground_batch():
    return {"pid": 4242, "window_id": 7, "delivery_mode": "foreground", "steps": [
        {"tool": "press_key", "args": {"key": "return"}},
        {"tool": "type_text", "args": {"text": "New name"}},
        {"tool": "press_key", "args": {"key": "return"}},
    ]}


def test_batch_holds_foreground_until_all_three_steps_finish(batch_driver):
    handler, driver, state, events = batch_driver
    result = asyncio.run(handler.call("computer_run_steps", foreground_batch()))
    assert not result.is_error
    assert json.loads(result.content[0].text) == {
        "ok": True, "executed": 3, "total": 3, "failed_step": None,
        "steps": [{"ok": True, "index": i} for i in range(3)]}
    assert [tool for tool, _ in events] == [
        "frontmost", "bring_to_front", "run_steps", "bring_to_front", "run_steps",
        "bring_to_front", "run_steps", "bring_to_front"]
    assert [args for tool, args in events if tool == "bring_to_front"] == [
        {"pid": 4242, "window_id": 7}] * 3 + [{"pid": 77}]
    driver.frontmost_pid.assert_awaited_once()
    assert state["active"] == 77
    assert not handler._front_lock.locked()


@pytest.mark.parametrize("ending,steps,guards,restores", [
    ("error", 2, 2, 1),
    ("refused_second", 1, 3, 1),
    ("refused_first", 0, 2, 0),
    ("halted", 1, 1, 1),
    ("unreadable", 1, 1, 1),
    ("failed", 1, 1, 1),
])
def test_batch_restores_on_early_exit(batch_driver, monkeypatch, ending, steps, guards, restores):
    handler, driver, state, events = batch_driver
    if ending == "error":
        state["error"] = 2
    elif ending.startswith("refused"):
        state["refuse"] = 2 if ending == "refused_second" else 1
    elif ending == "halted":
        monkeypatch.setattr(server, "_batch_stop", AsyncMock(side_effect=[None, "halted"]))
    else:
        state[ending] = True
    result = asyncio.run(handler.call("computer_run_steps", foreground_batch()))
    assert result.is_error
    assert state["steps"] == steps and state["guards"] == guards
    assert events.count(("bring_to_front", {"pid": 77})) == restores
    if restores:
        assert events[-1] == ("bring_to_front", {"pid": 77})
        assert state["active"] == 77
    if ending == "error":
        assert result.content[0].text == "Bilgisayar kullanımı sürücüsüyle bağlantı kesildi; işlem yinelenmedi."
    elif ending.startswith("refused"):
        report = json.loads(result.content[0].text)
        assert report["failed_step"] == steps
        assert report["steps"][-1]["code"] == "foreground_unverified"
    elif ending == "halted":
        assert json.loads(result.content[0].text)["stop_reason"] == "halted"
    elif ending == "unreadable":
        assert result.content[0].text == "Adım 1/3 sonucu okunamadı; kalan adımlar çalıştırılmadı."
    driver.frontmost_pid.assert_awaited_once()
    assert not handler._front_lock.locked()


@pytest.mark.parametrize("previous", [None, 4242, "lookup_error"])
def test_batch_skips_restore_without_previous_app(batch_driver, previous):
    handler, driver, state, events = batch_driver
    if previous == "lookup_error":
        state["lookup_error"] = True
    else:
        state["active"] = previous
    result = asyncio.run(handler.call("computer_run_steps", foreground_batch()))
    assert not result.is_error
    assert state["steps"] == 3
    driver.frontmost_pid.assert_awaited_once()
    assert all("window_id" in args for tool, args in events if tool == "bring_to_front")
    assert not handler._front_lock.locked()


def test_batch_restore_exception_preserves_report(batch_driver):
    handler, _, state, events = batch_driver
    state["restore_error"] = True
    result = asyncio.run(handler.call("computer_run_steps", foreground_batch()))
    assert not result.is_error
    assert json.loads(result.content[0].text)["executed"] == 3
    assert events[-1] == ("bring_to_front", {"pid": 77})
    assert events.count(("bring_to_front", {"pid": 77})) == 1
    assert not handler._front_lock.locked()


def test_batch_background_avoids_front_lock_and_lookup(batch_driver):
    handler, driver, state, events = batch_driver
    state["locked"] = False
    args = foreground_batch()
    del args["delivery_mode"]

    class UnusedLock:
        def locked(self):
            return False

        async def __aenter__(self):
            pytest.fail("background batch acquired the front lock")

        async def acquire(self):
            pytest.fail("background batch acquired the front lock")

    handler._front_lock = UnusedLock()
    result = asyncio.run(handler.call("computer_run_steps", args))
    assert not result.is_error
    driver.frontmost_pid.assert_not_awaited()
    assert [tool for tool, _ in events] == ["run_steps"] * 3


def test_mixed_batch_holds_lock_and_restores_after_last_background_step(batch_driver):
    handler, driver, state, events = batch_driver
    args = foreground_batch()
    del args["delivery_mode"]
    args["window_id"] = 9
    args["steps"][1]["args"].update(delivery_mode="foreground", window_id=9)
    args.update(delay_ms=20, observe={"include_screenshot": True})
    result = asyncio.run(handler.call("computer_run_steps", args))
    assert not result.is_error
    assert [tool for tool, _ in events] == [
        "run_steps", "frontmost", "bring_to_front", "run_steps", "run_steps", "bring_to_front"]
    assert events[2][1] == {"pid": 4242, "window_id": 9}
    assert events[-1] == ("bring_to_front", {"pid": 77})
    step_calls = [args for tool, args in events if tool == "run_steps"]
    assert "observe" not in step_calls[0] and "observe" not in step_calls[1]
    assert step_calls[2]["observe"] == {"include_screenshot": True}
    assert server.asyncio.sleep.await_count == 2
    server.asyncio.sleep.assert_awaited_with(0.02)
    driver.frontmost_pid.assert_awaited_once()
    assert state["active"] == 77
    assert not handler._front_lock.locked()


def test_single_foreground_click_still_restores_immediately(batch_driver):
    handler, driver, state, events = batch_driver
    result = asyncio.run(handler.call("computer_click", {
        "pid": 4242, "window_id": 7, "delivery_mode": "foreground", "x": 1, "y": 2}))
    assert not result.is_error and result.content[0].text == "clicked"
    assert [tool for tool, _ in events] == ["frontmost", "bring_to_front", "click", "bring_to_front"]
    assert events[-1] == ("bring_to_front", {"pid": 77})
    assert state["active"] == 77
    driver.frontmost_pid.assert_awaited_once()
    assert not handler._front_lock.locked()


@pytest.mark.parametrize("unverified", [False, True])
def test_single_step_foreground_fallback_is_guarded(fake, unverified):
    driver, log, approval = fake
    if unverified:
        driver.env["FAKE_CUA_FRONT_UNVERIFIED"] = "1"

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_run_steps", {
                "pid": 4242, "window_id": 7, "foreground_fallback": True,
                "steps": [{"click": {}}]})
            assert bool(result.is_error) is unverified
        finally:
            await handler.close()
    asyncio.run(run())
    expected = ["bring_to_front"] if unverified else ["run_steps", "bring_to_front"]
    assert [row["tool"] for row in calls(log)] == [
        "list_apps", "list_apps", "list_apps", "bring_to_front", *expected]


@pytest.mark.parametrize("window_id", [None, True, False, "7", 7.0])
def test_front_guard_requires_integer_window_without_driver_call(window_id):
    driver = AsyncMock()
    assert asyncio.run(server._front_guard(driver, 4242, window_id)) == (
        "Foreground input needs window_id; nothing was sent.")
    driver.call.assert_not_awaited()


@pytest.mark.parametrize("body,structured,error,verified", [
    ('{"exact_window_effect": {"verified": true}}', None, False, True),
    ("invalid", {"exact_window_effect": {"verified": True}}, False, True),
    ('{"exact_window_effect": {"verified": true}}', {}, False, False),
    ('{"exact_window_effect": {"verified": true}}', None, True, False),
    ("invalid", None, False, False),
    ("[]", None, False, False),
    ('{"exact_window_effect": null}', None, False, False),
    ('{"exact_window_effect": {"verified": 1}}', None, False, False),
])
def test_front_guard_accepts_only_explicit_verified_result(monkeypatch, body, structured, error, verified):
    driver = AsyncMock()
    driver.call.return_value = CallToolResult(
        content=[TextContent(type="text", text=body)], structured_content=structured, is_error=error)
    sleep = AsyncMock()
    monkeypatch.setattr(server.asyncio, "sleep", sleep)
    result = asyncio.run(server._front_guard(driver, 4242, 7))
    assert (result is None) is verified
    assert driver.call.await_count == (1 if verified else 2)
    for call in driver.call.await_args_list:
        assert call.args == ("bring_to_front", {"pid": 4242, "window_id": 7})
    if verified:
        sleep.assert_not_awaited()
    else:
        sleep.assert_awaited_once_with(0.3)


def test_exit_is_not_retried_and_next_call_reconnects(fake):
    driver, log, approval = fake

    async def run():
        handler = ComputerServer(driver)
        try:
            failed = await handler.call("computer_type_text", {"pid": 4242, "text": "exit-mid-call"})
            assert failed.is_error and "yinelenmedi" in failed.content[0].text
            result = await handler.call("computer_type_text", {"pid": 4242, "text": "hello"})
            assert not result.is_error
        finally:
            await handler.close()
    asyncio.run(run())
    rows = calls(log)
    assert len([row for row in rows if row["args"].get("text") == "exit-mid-call"]) == 1
    assert len({row["process"] for row in rows}) == 2


def test_risk_foreground_and_explicit_confirmation(fake):
    driver, log, approval = fake

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_hotkey", {"pid": 4242, "keys": ["Command", "Backspace"]})
            assert not result.is_error and approval.call_args.kwargs["params"]["risky"] is True
            result = await handler.call("computer_confirm_risky_step", {"pid": 4242, "summary": "Send message"})
            assert result.content[0].text == "approved"
            assert approval.call_args.kwargs["params"]["risk_reason"] == "Send message"
            await handler.call("computer_run_steps", {"pid": 4242, "steps": [{"click": {"delivery_mode": "foreground"}}]})
            assert approval.call_args.kwargs["params"]["foreground"] is True
        finally:
            await handler.close()
    asyncio.run(run())
    assert "computer_confirm_risky_step" not in [row["tool"] for row in calls(log)]


def test_mcp_registration_preserves_error_results(fake):
    driver, log, approval = fake

    async def run():
        mcp = create_server(driver)
        try:
            tools = await mcp.list_tools()
            assert {tool.name for tool in tools} == set(server.policy.TOOLS)
            result = await mcp.call_tool("computer_click", {"args": {"pid": 4242, "window_id": 7, "x": 1, "y": 2}})
            assert result.is_error
            assert result.content[0].text == "clicked"
        finally:
            await driver.close()
    asyncio.run(run())


@pytest.mark.parametrize("missing", ["binary", "socket"])
def test_missing_configuration_returns_error(monkeypatch, tmp_path, missing):
    binary = tmp_path / "cua-driver"
    binary.write_text("test driver")
    config = {"binary": str(binary), "socket": str(tmp_path / "driver.sock")}
    config[missing] = ""
    with pytest.raises(DriverUnavailable):
        DriverClient(**config)
    monkeypatch.setattr(server, "fetch_runtime", AsyncMock(side_effect=DriverUnavailable("not running")))
    result = asyncio.run(ComputerServer().call("computer_click", {"pid": 4242}))
    assert result.is_error and "kullanılamıyor" in result.content[0].text


def test_environment_and_command_are_minimal(fake):
    driver, log, approval = fake
    assert driver.command[1:] == ["mcp", "--embedded", "--socket", str(log.parent / "fake-private-socket")]
    assert set(driver.env) <= {"PATH", "HOME", "TMPDIR", "CUA_DRIVER_EMBEDDED", "DO_NOT_TRACK",
                               "CUA_DRIVER_RS_TELEMETRY_ENABLED", "CUA_DRIVER_RS_UPDATE_CHECK", "FAKE_CUA_LOG"}
    assert driver.env["DO_NOT_TRACK"] == "1"


@pytest.mark.parametrize("structured", [False, True])
def test_app_parser_supports_text_and_structured_results(fake, structured):
    driver, log, approval = fake
    data = {"apps": [{"pid": 7, "name": "App", "bundle_id": "com.test.App"},
                     {"pid": 8, "running": False}, {"pid": 0}, {"pid": True}, {"pid": 9}]}
    result = CallToolResult(content=[] if structured else [TextContent(type="text", text=json.dumps(data))],
                            **({"structured_content": data} if structured else {}))
    driver.call = AsyncMock(return_value=result)
    assert asyncio.run(driver.list_running_apps()) == {7: ("com.test.App", "App"), 9: (None, None)}


@pytest.mark.parametrize("channels", ["text", "structured", "both"])
@pytest.mark.parametrize("rows,expected", [
    ([{"pid": 77, "active": True, "running": True}], 77),
    ([{"pid": 77, "active": True}], 77),
    ([{"pid": 77, "active": False}], None),
    ([{"pid": 77, "active": 1}], None),
    ([{"pid": 77, "active": True, "running": False}], None),
    ([{"pid": True, "active": True}], None),
    ([{"pid": "77", "active": True}], None),
    ([{"pid": 0, "active": True}], None),
    ([{"pid": -1, "active": True}], None),
    ([{"pid": 77, "active": True}, {"pid": 4242, "active": True}], None),
    ([], None),
])
def test_frontmost_pid_requires_one_active_running_app(fake, channels, rows, expected):
    driver, _, _ = fake
    data = {"apps": rows}
    result = CallToolResult(
        content=[] if channels == "structured" else [TextContent(type="text", text=json.dumps(data))],
        **({} if channels == "text" else {"structured_content": data}))
    driver.call = AsyncMock(return_value=result)
    assert asyncio.run(driver.frontmost_pid()) == expected
    driver.call.assert_awaited_once_with("list_apps", {})


@pytest.mark.parametrize("error", [None, DriverError("offline"), RuntimeError("unexpected")])
def test_frontmost_pid_returns_none_on_driver_error(fake, error):
    driver, _, _ = fake
    driver.call = AsyncMock(return_value=CallToolResult(content=[], is_error=True), side_effect=error)
    assert asyncio.run(driver.frontmost_pid()) is None
    driver.call.assert_awaited_once_with("list_apps", {})


@pytest.mark.parametrize("reply", [None, True, [], "approved", 1, RuntimeError("offline")])
def test_malformed_or_failed_approval_never_forwards(fake, reply):
    driver, log, approval = fake
    if isinstance(reply, Exception):
        approval.side_effect = reply
    else:
        approval.return_value = reply

    async def run():
        handler = ComputerServer(driver)
        try:
            denied = await handler.call("computer_click", {"pid": 4242})
            assert denied.is_error
            confirmation = await handler.call("computer_confirm_risky_step", {"pid": 4242, "summary": "Send"})
            assert confirmation.is_error and confirmation.content[0].text.startswith("rejected:")
        finally:
            await handler.close()
    asyncio.run(run())
    assert all(row["tool"] == "list_apps" for row in calls(log))


def test_pid_remapped_during_approval_is_not_acted_on(fake):
    driver, log, approval = fake
    driver.env["FAKE_CUA_REMAP"] = "1"

    async def run():
        handler = ComputerServer(driver)
        try:
            denied = await handler.call("computer_click", {"pid": 4242})
            assert denied.is_error and "değişti" in denied.content[0].text
        finally:
            await handler.close()
    asyncio.run(run())
    assert approval.call_args.kwargs["params"]["bundle_id"] == "com.apple.TextEdit"
    assert [row["tool"] for row in calls(log)] == ["list_apps", "list_apps"]


@pytest.mark.parametrize("remapped", [{}, {4242: (None, None)}, {4242: ("com.apple.Terminal", "Terminal")}])
def test_missing_or_changed_pid_after_approval_refuses(fake, remapped):
    driver, log, approval = fake
    driver.list_running_apps = AsyncMock(side_effect=[{4242: ("com.apple.TextEdit", "TextEdit")}, remapped])
    result = asyncio.run(ComputerServer(driver).call("computer_click", {"pid": 4242}))
    assert result.is_error
    assert calls(log) == []


@pytest.mark.parametrize("tool,args,forwarded", [
    ("computer_list_windows", {"pid": 4242}, "list_windows"),
    ("computer_launch_app", {"bundle_id": "com.apple.finder", "urls": ["https://example.com"]}, "launch_app"),
    ("computer_clipboard_read", {"include_text": True}, "clipboard_read"),
    ("computer_clipboard_write", {"text": "hello"}, "clipboard_write"),
])
def test_opened_tools_forward_after_approval(fake, tool, args, forwarded):
    driver, log, approval = fake

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call(tool, args)
            assert not result.is_error
        finally:
            await handler.close()
    asyncio.run(run())
    rows = calls(log)
    assert rows[-1]["tool"] == forwarded
    if "pid" in args:
        assert [row["tool"] for row in rows[:-1]] == ["list_apps", "list_apps"]
        assert approval.call_args.kwargs["params"]["bundle_id"] == "com.apple.TextEdit"
    elif tool == "computer_launch_app":
        assert [row["tool"] for row in rows[:-1]] == ["list_apps"]
        assert approval.call_args.kwargs["params"]["pid"] is None
    else:
        assert len(rows) == 1
        assert approval.call_args.kwargs["params"]["pid"] is None
    for key, value in args.items():
        assert rows[-1]["args"][key] == value
    if tool == "computer_clipboard_read":
        params = approval.call_args.kwargs["params"]
        assert params["bundle_id"] == server.policy.CLIPBOARD_BUNDLE_ID
        assert params["app_name"] == "Clipboard"
    elif tool == "computer_clipboard_write":
        assert approval.call_args.kwargs["params"]["bundle_id"] is None


@pytest.mark.parametrize("suffix", [".app", ".bundle", ".framework", ".plugin", ".appex",
                                   ".prefpane", ".workflow", ".action", ".shortcut", ".scptd",
                                   ".pkg", ".mpkg", ".kext", ".xpc", ".terminal", ".command", ".tool",
                                   ".saver", ".service", ".systemextension", ".wdgt", ".dext",
                                   ".qlgenerator", ".mdimporter", ".component", ".colorpicker",
                                   ".osax", ".menu", ".vst", ".vst3", ".au", ".clap"])
@pytest.mark.parametrize("form", ["path", "inside", "symlink"])
def test_launch_refuses_executable_directory_components_before_approval(fake, tmp_path, suffix, form):
    driver, log, approval = fake
    blocked = tmp_path / ("Target" + suffix.upper())
    blocked.mkdir()
    target = blocked
    if form == "inside":
        target = blocked / "documents"
        target.mkdir()
    elif form == "symlink":
        target = tmp_path / "innocent"
        target.symlink_to(blocked, target_is_directory=True)
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", {
        "bundle_id": "com.apple.finder", "urls": [str(target)]}))
    assert result.is_error
    approval.assert_not_called()
    assert calls(log) == []


@pytest.mark.parametrize("marker", [("Contents", "Info.plist"), ("Contents", "MacOS")])
@pytest.mark.parametrize("form", ["path", "inside"])
def test_launch_refuses_disguised_bundles_including_ancestors(fake, tmp_path, marker, form):
    driver, log, approval = fake
    bundle = tmp_path / "Innocent"
    path = bundle.joinpath(*marker)
    if marker[1] == "MacOS":
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True)
        path.write_text("x")
    target = bundle if form == "path" else bundle / "Contents"
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", {
        "bundle_id": "com.apple.finder", "urls": [str(target)]}))
    assert result.is_error
    approval.assert_not_called()
    assert calls(log) == []


def test_launch_refuses_file_urls_before_approval(fake, tmp_path):
    driver, log, approval = fake
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", {
        "bundle_id": "com.apple.finder", "urls": [tmp_path.as_uri()]}))
    assert result.is_error
    approval.assert_not_called()
    assert calls(log) == []


@pytest.mark.parametrize("kind", ["file", "command", "missing"])
def test_launch_refuses_files_and_missing_paths(fake, tmp_path, kind):
    driver, log, approval = fake
    target = tmp_path / ("script.command" if kind == "command" else "document")
    if kind != "missing":
        target.write_text("data")
    url = str(target)
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", {
        "bundle_id": "com.apple.finder", "urls": [url]}))
    assert result.is_error
    approval.assert_not_called()
    assert calls(log) == []


@pytest.mark.parametrize("name", ["plain-directory", "Unity Projeleri", "unityaıPython"])
def test_launch_plain_directory_forwards_resolved_real_path(fake, tmp_path, name):
    driver, log, approval = fake
    target = tmp_path / name
    target.mkdir()
    link = tmp_path / "link to folder"
    link.symlink_to(target, target_is_directory=True)
    args = {"bundle_id": "com.apple.finder", "urls": [str(link), "https://example.com/a"]}

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_launch_app", args)
            assert not result.is_error
        finally:
            await handler.close()

    asyncio.run(run())
    approval.assert_awaited_once()
    assert calls(log)[-1]["args"]["urls"] == [os.path.realpath(target), "https://example.com/a"]


@pytest.fixture
def document_launch(monkeypatch):
    driver = AsyncMock(spec=DriverClient)
    driver.binary, driver.socket = "fake-binary", "fake-socket"
    driver.installed_app_name.return_value = "TextEdit"
    driver.call.return_value = CallToolResult(content=[TextContent(type="text", text="opened")])
    runtime = AsyncMock(return_value=(driver.binary, driver.socket))
    approval = AsyncMock(return_value={"approved": True})
    monkeypatch.setattr(server, "fetch_runtime", runtime)
    monkeypatch.setattr(server.approval_bridge, "request_approval", approval)
    return driver, approval, runtime


def _assert_document_refused(document_launch, target, bundle_id="com.apple.TextEdit"):
    driver, approval, runtime = document_launch
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", {
        "bundle_id": bundle_id, "urls": [str(target)]}))
    assert result.is_error
    assert result.content[0].text == (
        "urls must target existing directories, or plain document files the app opens "
        "(not scripts, executables, installers or files inside app bundles; not with Finder or a terminal)"
    )
    approval.assert_not_called()
    runtime.assert_not_called()
    assert driver.mock_calls == []


@pytest.mark.parametrize("symlink", [False, True])
def test_document_launch_forwards_plain_file_real_path_after_approval(document_launch, tmp_path, symlink):
    driver, approval, _ = document_launch
    target = tmp_path / "notes.txt"
    target.write_text("hello")
    target.chmod(0o644)
    path = target
    if symlink:
        path = tmp_path / "alias.txt"
        path.symlink_to(target)
    args = {"bundle_id": "com.apple.TextEdit", "urls": [str(path)]}

    async def opened(tool, forwarded):
        approval.assert_awaited_once()
        assert approval.call_args.kwargs["params"]["bundle_id"] == args["bundle_id"]
        return CallToolResult(content=[TextContent(type="text", text="opened")])

    driver.call.side_effect = opened
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", args))
    assert not result.is_error
    driver.call.assert_awaited_once_with("launch_app", {
        **args, "urls": [os.path.realpath(target)]})
    assert server._safe_launch_targets(args["urls"], args["bundle_id"])


def test_document_launch_refuses_symlink_to_runnable(document_launch, tmp_path):
    target = tmp_path / "run.command"
    target.write_text("echo hello")
    target.chmod(0o644)
    link = tmp_path / "notes.txt"
    link.symlink_to(target)
    _assert_document_refused(document_launch, link)


@pytest.mark.parametrize("suffix", [".sh", ".command", ".py", ".scpt", ".webloc", ".pkg",
                                   ".dmg", ".docm", ".SH", ".framework"])
def test_document_launch_refuses_runnable_suffixes(document_launch, tmp_path, suffix):
    target = tmp_path / ("document" + suffix)
    target.write_text("data")
    target.chmod(0o644)
    _assert_document_refused(document_launch, target)


@pytest.mark.parametrize("mode", [0o744, 0o654, 0o645])
def test_document_launch_refuses_any_execute_bit(document_launch, tmp_path, mode):
    target = tmp_path / "notes.txt"
    target.write_text("data")
    target.chmod(mode)
    _assert_document_refused(document_launch, target)


@pytest.mark.parametrize("bundle_id", [
    "com.apple.finder", "COM.APPLE.TERMINAL", "com.googlecode.iterm2", "org.python.PythonLauncher",
    "dev.warp.warp-stable", "com.mitchellh.ghostty", "net.kovidgoyal.kitty", "org.alacritty",
    "co.zeit.hyper", "com.github.wez.wezterm", "com.apple.installer", "com.apple.archiveutility",
    "com.apple.scripteditor2", "com.apple.automator", "com.apple.shortcuts",
])
def test_document_launch_refuses_unsafe_handlers(document_launch, tmp_path, bundle_id):
    target = tmp_path / "notes.txt"
    target.write_text("hello")
    target.chmod(0o644)
    _assert_document_refused(document_launch, target, bundle_id)


@pytest.mark.parametrize("bundle_name,marker", [
    ("Thing.app", None), ("Plain", "MacOS"), ("Plain", "Info.plist"),
])
def test_document_launch_refuses_files_inside_bundles(document_launch, tmp_path, bundle_name, marker):
    bundle = tmp_path / bundle_name
    resources = bundle / "Contents" / "Resources"
    resources.mkdir(parents=True)
    if marker == "MacOS":
        (bundle / "Contents" / marker).mkdir()
    elif marker:
        (bundle / "Contents" / marker).write_text("plist")
    target = resources / "notes.txt"
    target.write_text("hello")
    target.chmod(0o644)
    _assert_document_refused(document_launch, target)


def test_document_launch_refuses_fifo(document_launch, tmp_path):
    target = tmp_path / "notes.txt"
    os.mkfifo(target, 0o644)
    _assert_document_refused(document_launch, target)


def test_document_launch_keeps_directory_and_https_targets(document_launch, tmp_path):
    driver, approval, _ = document_launch
    target = tmp_path / "documents"
    target.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(target, target_is_directory=True)
    args = {"bundle_id": "com.apple.finder", "urls": [str(link), "https://example.com/a"]}
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", args))
    assert not result.is_error
    approval.assert_awaited_once()
    driver.call.assert_awaited_once_with("launch_app", {
        **args, "urls": [os.path.realpath(target), "https://example.com/a"]})


def test_driver_session_timeout_covers_maximum_window_read(fake, monkeypatch):
    from computer_use import driver_client
    original = driver_client.ClientSession
    observed = []

    def session(*streams, **kwargs):
        observed.append(kwargs["read_timeout_seconds"])
        return original(*streams, **kwargs)

    monkeypatch.setattr(driver_client, "ClientSession", session)
    driver, log, approval = fake

    async def run():
        try:
            await driver.list_running_apps()
        finally:
            await driver.close()
    asyncio.run(run())
    assert observed == [130]


def test_public_close_permanently_prevents_transport_restart(fake, monkeypatch):
    from unittest.mock import MagicMock
    from computer_use import driver_client

    driver, log, _ = fake

    async def run():
        try:
            await driver.list_running_apps()
            await driver.close()
            transport = MagicMock(side_effect=AssertionError("closed client started a transport"))
            monkeypatch.setattr(driver_client, "_transport", transport)
            for operation in (driver.list_running_apps, lambda: driver.call("click", {"pid": 4242})):
                with pytest.raises(DriverUnavailable):
                    await operation()
            transport.assert_not_called()
        finally:
            await driver.close()

    asyncio.run(run())
    assert len({row["process"] for row in calls(log)}) == 1


def test_caller_cancellation_keeps_client_reusable(fake, monkeypatch):
    from computer_use import driver_client

    driver, log, _ = fake

    async def run():
        entered = asyncio.Event()
        original = driver_client.DriverClient._serve

        async def pending(queue):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(driver, "_serve", pending)
        # One cancelled owner task, then at most one fake driver process.
        task = asyncio.create_task(driver.call("list_apps", {}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            monkeypatch.setattr(driver, "_serve", original.__get__(driver))
            assert 4242 in await driver.list_running_apps()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await driver.close()

    asyncio.run(run())
    assert len({row["process"] for row in calls(log)}) == 1


@pytest.mark.parametrize("window_id,expected", [(7, 7), (None, None), (True, None), ("7", None)])
def test_gate_params_carry_only_an_integer_window_id(fake, window_id, expected):
    driver, log, approval = fake
    args = {"pid": 4242, "x": 1, "y": 2}
    if window_id is not None:
        args["window_id"] = window_id

    async def run():
        handler = ComputerServer(driver)
        try:
            await handler.call("computer_click", args)
        finally:
            await handler.close()
    asyncio.run(run())
    if approval.call_args is not None:
        assert approval.call_args.kwargs["params"]["window_id"] == expected
    else:
        # The policy refused the malformed id before any gate was asked.
        assert expected is None


def _launch_params(driver, approval, bundle_id, **extra):
    async def run():
        handler = ComputerServer(driver)
        try:
            return await handler.call("computer_launch_app", {"bundle_id": bundle_id, **extra})
        finally:
            await handler.close()
    result = asyncio.run(run())
    return result, approval.call_args.kwargs["params"]


def test_launch_approval_names_not_running_app_case_insensitively(fake):
    driver, log, approval = fake
    result, params = _launch_params(driver, approval, "COM.example.sleeper")
    assert not result.is_error
    assert params["bundle_id"] == "COM.example.sleeper"
    assert params["app_name"] == "Sleeper App"
    assert params["summary"] == "Sleeper App"
    assert [row["tool"] for row in calls(log)] == ["list_apps", "launch_app"]


@pytest.mark.parametrize("bundle_id", ["com.example.Unknown", "com.example.Offline"])
def test_launch_approval_name_is_none_without_a_usable_row(fake, bundle_id):
    driver, log, approval = fake
    result, params = _launch_params(driver, approval, bundle_id)
    assert not result.is_error
    assert params["app_name"] is None
    assert calls(log)[-1]["tool"] == "launch_app"


def test_launch_name_lookup_failure_still_launches(fake, monkeypatch):
    driver, log, approval = fake
    monkeypatch.setattr(driver, "installed_app_name", AsyncMock(side_effect=RuntimeError("boom")))
    result, params = _launch_params(driver, approval, "com.example.Sleeper")
    assert not result.is_error
    assert params["app_name"] is None
    assert [row["tool"] for row in calls(log)] == ["launch_app"]


def test_launch_name_lookup_timeout_still_launches(fake, monkeypatch):
    driver, log, approval = fake

    async def slow(_bundle_id):
        await asyncio.sleep(10)
    monkeypatch.setattr(driver, "installed_app_name", slow)
    monkeypatch.setattr(server, "LAUNCH_NAME_TIMEOUT", 0.05)
    result, params = _launch_params(driver, approval, "com.example.Sleeper")
    assert not result.is_error
    assert params["app_name"] is None
    assert calls(log)[-1]["tool"] == "launch_app"


def test_launch_url_refusal_makes_no_driver_call_for_the_name(fake, tmp_path):
    driver, log, approval = fake
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", {
        "bundle_id": "com.example.Sleeper", "urls": [str(tmp_path / "missing")]}))
    assert result.is_error
    approval.assert_not_awaited()
    assert calls(log) == []
