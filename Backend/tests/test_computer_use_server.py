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
                                   ".pkg", ".mpkg", ".kext", ".xpc", ".terminal", ".command", ".tool"])
@pytest.mark.parametrize("form", ["path", "file-url", "inside", "symlink"])
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
    url = target.as_uri() if form == "file-url" else str(target)
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", {
        "bundle_id": "com.apple.finder", "urls": [url]}))
    assert result.is_error
    approval.assert_not_called()
    assert calls(log) == []


@pytest.mark.parametrize("kind", ["file", "command", "missing"])
@pytest.mark.parametrize("file_url", [False, True])
def test_launch_refuses_files_and_missing_paths(fake, tmp_path, kind, file_url):
    driver, log, approval = fake
    target = tmp_path / ("script.command" if kind == "command" else "document")
    if kind != "missing":
        target.write_text("data")
    url = target.as_uri() if file_url else str(target)
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", {
        "bundle_id": "com.apple.finder", "urls": [url]}))
    assert result.is_error
    approval.assert_not_called()
    assert calls(log) == []


@pytest.mark.parametrize("file_url", [False, True])
def test_launch_plain_directory_forwards_original_target(fake, tmp_path, file_url):
    driver, log, approval = fake
    target = tmp_path / ("plain directory" if file_url else "plain-directory")
    target.mkdir()
    url = target.as_uri() if file_url else str(target)
    args = {"bundle_id": "com.apple.finder", "urls": [url]}

    async def run():
        handler = ComputerServer(driver)
        try:
            result = await handler.call("computer_launch_app", args)
            assert not result.is_error
        finally:
            await handler.close()

    asyncio.run(run())
    approval.assert_awaited_once()
    assert calls(log)[0]["args"]["urls"] == [url]


def test_launch_decodes_file_url_before_resolving_symlinks(fake, tmp_path):
    driver, log, approval = fake
    app = tmp_path / "Hidden.app"
    app.mkdir()
    alias = tmp_path / "plain alias"
    alias.symlink_to(app, target_is_directory=True)
    result = asyncio.run(ComputerServer(driver).call("computer_launch_app", {
        "bundle_id": "com.apple.finder", "urls": [alias.as_uri()]}))
    assert result.is_error
    approval.assert_not_called()
    assert calls(log) == []


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
