"""Proxy tests use only a fake stdio driver and a patched approval bridge."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from computer_use.driver_client import DriverClient, DriverUnavailable
from computer_use.server import ComputerServer, create_server
from computer_use import server


@pytest.fixture
def fake(tmp_path, monkeypatch):
    # At most two child processes in the reconnect test, one in all others.
    fixture = Path(__file__).parent / "fixtures" / "fake_cua_driver.py"
    executable = tmp_path / "fake-cua"
    executable.write_text(
        "#!" + sys.executable + "\nimport sys, runpy\nsys.dont_write_bytecode = True\n"
        + "runpy.run_path(" + repr(str(fixture)) + ", run_name='__main__')\n")
    executable.chmod(0o700)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("GAMACHINE_CUA_DRIVER", str(executable))
    monkeypatch.setenv("GAMACHINE_CUA_SOCKET", "fake-private-socket")
    driver = DriverClient()
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
    assert [row["tool"] for row in calls(log)] == ["list_apps", "get_window_state", "list_apps", "click"]
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


@pytest.mark.parametrize("missing", ["GAMACHINE_CUA_DRIVER", "GAMACHINE_CUA_SOCKET"])
def test_missing_configuration_returns_error(monkeypatch, missing):
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(DriverUnavailable):
        DriverClient()
    result = asyncio.run(ComputerServer().call("computer_click", {"pid": 4242}))
    assert result.is_error and "kullanılamıyor" in result.content[0].text


def test_environment_and_command_are_minimal(fake):
    driver, log, approval = fake
    assert driver.command[1:] == ["mcp", "--embedded", "--socket", "fake-private-socket"]
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
