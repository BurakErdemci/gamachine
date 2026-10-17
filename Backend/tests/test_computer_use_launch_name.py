"""Launch approval names come only from the driver's installed-app rows."""
import asyncio
import json
import sys
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from computer_use import server
from computer_use.driver_client import DriverClient, DriverError, DriverUnavailable


@pytest.fixture
def launch(monkeypatch):
    # No subprocesses: every driver invocation is replaced by an async stub.
    driver = DriverClient(sys.executable, "unused-socket")
    driver.call = AsyncMock(return_value=CallToolResult(content=[]))
    approval = AsyncMock(return_value={"approved": True})
    monkeypatch.setattr(server.approval_bridge, "request_approval", approval)
    handler = server.ComputerServer(driver)
    monkeypatch.setattr(handler, "_runtime_driver", AsyncMock(return_value=driver))
    return handler, driver, approval


def app_result(data, structured=False):
    return CallToolResult(
        content=[] if structured else [TextContent(type="text", text=json.dumps(data))],
        **({"structured_content": data} if structured else {}))


@pytest.mark.parametrize("structured,wrapped", [(False, False), (False, True), (True, True)])
@pytest.mark.parametrize("bundle", ["com.apple.Notes", "COM.APPLE.notes"])
@pytest.mark.parametrize("running,pid", [(False, 0), (True, 77)])
def test_launch_uses_installed_driver_name(launch, structured, wrapped, bundle, running, pid):
    handler, driver, approval = launch
    rows = [{"bundle_id": "com.apple.Other", "name": "Other"},
            {"bundle_id": "com.apple.Notes", "name": "  Notes  ",
             "running": running, "pid": pid}]
    driver.call.side_effect = [app_result({"apps": rows} if wrapped else rows, structured),
                               CallToolResult(content=[])]
    args = {"bundle_id": bundle}
    assert not asyncio.run(handler.call("computer_launch_app", args)).is_error
    params = approval.call_args.kwargs["params"]
    assert params["app_name"] == "Notes"
    assert params["bundle_id"] == bundle
    assert driver.call.await_args_list[0].args == ("list_apps", {})
    assert driver.call.await_args_list[1].args == ("launch_app", {"bundle_id": bundle})


@pytest.mark.parametrize("data", [
    None, 42, "bad", {}, {"apps": None}, {"apps": {}}, [None, 42, "bad"],
    [{"bundle_id": None, "name": "Fake"}],
    [{"bundle_id": "com.apple.Other", "name": "Other"}],
    *[[{"bundle_id": "com.apple.Notes", "name": name}]
      for name in (None, "", "  \t", 42, True, [], {})],
])
def test_launch_unknown_or_malformed_rows_fall_back(launch, data):
    handler, driver, approval = launch
    driver.call.side_effect = [app_result(data), CallToolResult(content=[])]
    assert not asyncio.run(handler.call("computer_launch_app", {"bundle_id": "com.apple.Notes"})).is_error
    assert approval.call_args.kwargs["params"]["app_name"] is None
    assert driver.call.await_args_list[-1].args == ("launch_app", {"bundle_id": "com.apple.Notes"})


@pytest.mark.parametrize("failure", [
    DriverError("offline"), DriverUnavailable("offline"), RuntimeError("offline"),
    TimeoutError("slow"), None,
    CallToolResult(content=[], is_error=True),
    CallToolResult(content=[TextContent(type="text", text="invalid json")]),
])
def test_launch_lookup_failure_still_forwards(launch, failure):
    handler, driver, approval = launch
    driver.call.side_effect = [failure, CallToolResult(content=[])]
    assert not asyncio.run(handler.call("computer_launch_app", {"bundle_id": "com.apple.Notes"})).is_error
    assert approval.call_args.kwargs["params"]["app_name"] is None
    assert [call.args[0] for call in driver.call.await_args_list] == ["list_apps", "launch_app"]


def test_launch_lookup_timeout_keeps_driver_reusable(launch, monkeypatch):
    handler, driver, approval = launch
    monkeypatch.setattr(server, "LAUNCH_NAME_TIMEOUT", 0.05)
    # At most two in-process owner tasks; no subprocess or real driver.
    monkeypatch.delattr(driver, "call")
    tools = []

    async def serve(queue):
        current = await queue.get()
        while current is not None:
            tool, args, future = current
            tools.append(tool)
            if tool == "list_apps":
                await asyncio.Event().wait()
            future.set_result(CallToolResult(content=[]))
            current = await queue.get()

    monkeypatch.setattr(driver, "_serve", serve)

    async def run():
        try:
            result = await asyncio.wait_for(
                handler.call("computer_launch_app", {"bundle_id": "com.apple.Notes"}), 2)
            assert not result.is_error
        finally:
            await handler.close()

    asyncio.run(run())
    assert tools == ["list_apps", "launch_app"]
    assert approval.call_args.kwargs["params"]["app_name"] is None


def test_launch_cancellation_during_lookup_propagates(launch):
    handler, driver, approval = launch
    driver.call.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(handler.call("computer_launch_app", {"bundle_id": "com.apple.Notes"}))
    approval.assert_not_awaited()
    assert driver.call.await_count == 1


@pytest.mark.parametrize("invalid", ["agent_name", "missing_directory"])
def test_launch_refusal_never_looks_up_apps(launch, tmp_path, invalid):
    handler, driver, approval = launch
    args = {"bundle_id": "com.apple.Notes"}
    if invalid == "agent_name":
        args["app_name"] = "Agent supplied name"
    else:
        args["urls"] = [str(tmp_path / "missing")]
    assert asyncio.run(handler.call("computer_launch_app", args)).is_error
    driver.call.assert_not_awaited()
    handler._runtime_driver.assert_not_awaited()
    approval.assert_not_awaited()
