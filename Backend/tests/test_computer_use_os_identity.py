"""OS identity fallback tests use fake app records and subprocesses only."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolResult

from computer_use import driver_client, server


UNITY = ('"Unity" ASN:0x0-0x228228: \n'
         '    bundleID="com.unity3d.UnityEditor5.x"\n'
         '    bundle path=[ NULL ] \n    executable path=[ NULL ] \n'
         ' !cgsConnection !signalled type="Foreground" flavor=[ NULL ]  '
         'Version=[ NULL ]  Arch=!!none \n')
BUNDLE = "com.unity3d.UnityEditor5.x"


@pytest.fixture(autouse=True)
def no_real_subprocesses(monkeypatch):
    spawn = AsyncMock(side_effect=AssertionError("real subprocess forbidden"))
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", spawn)
    return spawn


def test_parser_accepts_measured_unity_record():
    assert driver_client._parse_lsappinfo(UNITY) == (BUNDLE, "Unity")


@pytest.mark.parametrize("text", [
    "",
    UNITY.replace('type="Foreground"', ""),
    UNITY.replace('type="Foreground"', 'type="UIElement"'),
    UNITY.replace('type="Foreground"', 'type="BackgroundOnly"'),
    UNITY + 'bundleID="com.other.App"\n',
    UNITY + f'bundleID="{BUNDLE}"\n',
    UNITY.replace(BUNDLE, "invalid bundle"),
    UNITY + '"Other" ASN:0x0-0x123123:\n',
    UNITY + 'ASN:0x0-0x123123:\n',
])
def test_parser_rejects_ambiguous_or_nonforeground_records(text):
    assert driver_client._parse_lsappinfo(text) is None


@pytest.mark.parametrize("text", [
    UNITY.split("\n", 1)[1],
    UNITY.replace('"Unity" ASN', '"" ASN'),
])
def test_parser_allows_missing_or_empty_name(text):
    assert driver_client._parse_lsappinfo(text) == (BUNDLE, None)


def test_identity_prefers_driver_without_os_lookup(monkeypatch):
    driver = SimpleNamespace(list_running_apps=AsyncMock(return_value={93785: (BUNDLE, "Driver name")}))
    lookup = AsyncMock(side_effect=AssertionError("OS lookup unnecessary"))
    monkeypatch.setattr(driver_client, "_lsappinfo_text", lookup)
    assert asyncio.run(driver_client.identify_running_app(driver, 93785)) == (BUNDLE, "Driver name")
    driver.list_running_apps.assert_awaited_once_with()
    lookup.assert_not_called()


@pytest.mark.parametrize("apps,name", [
    ({}, "Unity"),
    ({93785: (None, None)}, "Unity"),
    ({93785: (None, "Driver name")}, "Driver name"),
    ({93785: ("invalid bundle", None)}, "Unity"),
])
def test_identity_falls_back_when_driver_cannot_identify(monkeypatch, apps, name):
    driver = SimpleNamespace(list_running_apps=AsyncMock(return_value=apps))
    lookup = AsyncMock(return_value=UNITY)
    monkeypatch.setattr(driver_client, "_lsappinfo_text", lookup)
    assert asyncio.run(driver_client.identify_running_app(driver, 93785)) == (BUNDLE, name)
    driver.list_running_apps.assert_awaited_once_with()
    lookup.assert_awaited_once_with(93785)


@pytest.mark.parametrize("pid", [0, -1, True, False, 93785.0, "93785", None])
def test_invalid_pid_never_uses_os_lookup(monkeypatch, pid):
    driver = SimpleNamespace(list_running_apps=AsyncMock(return_value={}))
    lookup = AsyncMock()
    monkeypatch.setattr(driver_client, "_lsappinfo_text", lookup)
    assert asyncio.run(driver_client.identify_running_app(driver, pid)) == (None, None)
    driver.list_running_apps.assert_awaited_once_with()
    lookup.assert_not_called()


@pytest.mark.parametrize("output", [None, "", UNITY.replace('"Foreground"', '"UIElement"')])
def test_identity_stays_unknown_without_usable_os_record(monkeypatch, output):
    driver = SimpleNamespace(list_running_apps=AsyncMock(return_value={}))
    monkeypatch.setattr(driver_client, "_lsappinfo_text", AsyncMock(return_value=output))
    assert asyncio.run(driver_client.identify_running_app(driver, 93785)) == (None, None)


def test_identity_catches_os_errors_but_propagates_driver_errors(monkeypatch):
    error = driver_client.DriverError("driver unavailable")
    driver = SimpleNamespace(list_running_apps=AsyncMock(side_effect=error))
    lookup = AsyncMock(side_effect=OSError("OS lookup failed"))
    monkeypatch.setattr(driver_client, "_lsappinfo_text", lookup)
    with pytest.raises(driver_client.DriverError) as raised:
        asyncio.run(driver_client.identify_running_app(driver, 93785))
    assert raised.value is error
    lookup.assert_not_called()
    driver.list_running_apps.side_effect = None
    driver.list_running_apps.return_value = {}
    assert asyncio.run(driver_client.identify_running_app(driver, 93785)) == (None, None)


@pytest.mark.parametrize("records,forwarded", [
    ([UNITY, UNITY], True),
    ([None, None], False),
    ([UNITY.replace('"Foreground"', '"UIElement"')] * 2, False),
    ([UNITY, None], False),
    ([UNITY, UNITY.replace(BUNDLE, "com.other.App")], False),
])
def test_server_identifies_before_and_after_approval(monkeypatch, records, forwarded):
    driver = SimpleNamespace(
        binary="fake", socket="fake-socket",
        list_running_apps=AsyncMock(return_value={}),
        call=AsyncMock(return_value=CallToolResult(content=[])),
    )
    approval = AsyncMock(return_value={"approved": True})
    lookup = AsyncMock(side_effect=records)
    monkeypatch.setattr(driver_client, "_lsappinfo_text", lookup)
    monkeypatch.setattr(server, "fetch_runtime", AsyncMock(return_value=("fake", "fake-socket")))
    monkeypatch.setattr(server.approval_bridge, "request_approval", approval)
    result = asyncio.run(server.ComputerServer(driver).call("computer_list_windows", {"pid": 93785}))
    assert result.is_error is not forwarded
    params = approval.call_args.kwargs["params"]
    assert params["bundle_id"] == (BUNDLE if records[0] == UNITY else None)
    if records[0] == UNITY:
        assert params["app_name"] == "Unity"
    assert lookup.await_count == 2
    assert driver.list_running_apps.await_count == 2
    if forwarded:
        driver.call.assert_awaited_once_with("list_windows", {"pid": 93785})
    else:
        driver.call.assert_not_called()


@pytest.mark.parametrize("pid", [0, -1, True, False, 93785.0, "93785", None])
def test_lsappinfo_rejects_invalid_pid_without_spawning(monkeypatch, no_real_subprocesses, pid):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    assert asyncio.run(driver_client._lsappinfo_text(pid)) is None
    no_real_subprocesses.assert_not_called()


def test_lsappinfo_non_darwin_does_not_spawn(monkeypatch, no_real_subprocesses):
    monkeypatch.setattr(driver_client.sys, "platform", "linux")
    assert asyncio.run(driver_client._lsappinfo_text(93785)) is None
    no_real_subprocesses.assert_not_called()


def test_lsappinfo_missing_executable_does_not_spawn(monkeypatch, no_real_subprocesses):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: False)
    assert asyncio.run(driver_client._lsappinfo_text(93785)) is None
    no_real_subprocesses.assert_not_called()


@pytest.mark.parametrize("returncode,output,expected", [
    (0, UNITY.encode(), UNITY),
    (0, b"bad byte: \xff", "bad byte: \ufffd"),
    (1, UNITY.encode(), None),
])
def test_lsappinfo_uses_bounded_shell_free_process(monkeypatch, returncode, output, expected):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: True)
    process = SimpleNamespace(returncode=returncode, communicate=AsyncMock(return_value=(output, None)))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", spawn)
    assert asyncio.run(driver_client._lsappinfo_text(93785)) == expected
    spawn.assert_awaited_once_with(
        os.path.join(os.sep, "usr", "bin", "lsappinfo"), "info", "-only", "bundleid", "-only", "name",
        "-only", "applicationtype", "pid:93785", stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )


def test_lsappinfo_spawn_failure_returns_none(monkeypatch):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: True)
    spawn = AsyncMock(side_effect=OSError("cannot start"))
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", spawn)
    assert asyncio.run(driver_client._lsappinfo_text(93785)) is None


def test_lsappinfo_timeout_kills_and_reaps_only_its_child(monkeypatch):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: True)
    process = SimpleNamespace(communicate=AsyncMock(), kill=Mock(), wait=AsyncMock())
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", spawn)
    timeouts = []

    async def expire(awaitable, timeout):
        timeouts.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(driver_client.asyncio, "wait_for", expire)
    assert asyncio.run(driver_client._lsappinfo_text(93785)) is None
    assert timeouts == [2.0]
    spawn.assert_awaited_once()
    process.kill.assert_called_once_with()
    process.wait.assert_awaited_once_with()
