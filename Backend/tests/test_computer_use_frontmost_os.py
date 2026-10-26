"""Frontmost app tests use fake OS replies and subprocesses only."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from mcp.types import CallToolResult

from computer_use.platform import macos as driver_client


ASN = "ASN:0x0-0x57057:"
FRONT = ASN + "\n"
INFO = '    pid = 10586 !cgsConnection !signalled type="Foreground"\n'


@pytest.fixture(autouse=True)
def no_real_subprocesses(monkeypatch):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    spawn = AsyncMock(side_effect=AssertionError("real subprocess forbidden"))
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", spawn)
    return spawn


def test_parser_returns_foreground_pid():
    assert driver_client._parse_lsappinfo_front(FRONT, INFO) == (True, 10586)


@pytest.mark.parametrize("info", [
    INFO.replace("Foreground", "UIElement"),
    INFO.replace("Foreground", "BackgroundOnly"),
    INFO + "    pid = 42\n",
    INFO + '    type="Foreground"\n',
    INFO.replace("pid = 10586", ""),
    INFO.replace('type="Foreground"', ""),
    INFO.replace("pid = 10586", "pid = 0"),
    INFO.replace("pid = 10586", "pid = -1"),
    "",
])
def test_parser_answers_without_pid_for_unusable_records(info):
    assert driver_client._parse_lsappinfo_front(FRONT, info) == (True, None)


@pytest.mark.parametrize("front,info", [(None, INFO), (42, INFO), (FRONT, None), (FRONT, 42)])
def test_parser_non_string_input_is_not_answered(front, info):
    assert driver_client._parse_lsappinfo_front(front, info) == (False, None)


def test_front_lookup_passes_exact_asn_to_info(monkeypatch):
    lookup = AsyncMock(side_effect=[FRONT, INFO])
    monkeypatch.setattr(driver_client, "_run_lsappinfo", lookup)
    assert asyncio.run(driver_client._lsappinfo_front()) == (True, 10586)
    assert lookup.await_args_list == [
        call("front"), call("info", "-only", "pid", "-only", "applicationtype", ASN),
    ]


@pytest.mark.parametrize("front", [None, "", "garbage", "ASN:0x0-0xzz:", FRONT + FRONT, 42])
def test_front_lookup_requires_exactly_one_asn(monkeypatch, front):
    lookup = AsyncMock(return_value=front)
    monkeypatch.setattr(driver_client, "_run_lsappinfo", lookup)
    assert asyncio.run(driver_client._lsappinfo_front()) == (False, None)
    lookup.assert_awaited_once_with("front")


def test_front_lookup_info_unavailable_is_not_answered(monkeypatch):
    lookup = AsyncMock(side_effect=[FRONT, None])
    monkeypatch.setattr(driver_client, "_run_lsappinfo", lookup)
    assert asyncio.run(driver_client._lsappinfo_front()) == (False, None)
    assert lookup.await_count == 2


@pytest.mark.parametrize("first_call", [False, True])
@pytest.mark.parametrize("exception", [OSError, asyncio.CancelledError])
def test_front_lookup_handles_errors_and_propagates_cancellation(monkeypatch, first_call, exception):
    replies = [exception()] if first_call else [FRONT, exception()]
    monkeypatch.setattr(driver_client, "_run_lsappinfo", AsyncMock(side_effect=replies))
    if exception is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(driver_client._lsappinfo_front())
    else:
        assert asyncio.run(driver_client._lsappinfo_front()) == (False, None)


@pytest.mark.parametrize("answered,pid,expected", [(True, 10586, 10586), (True, None, None),
                                                     (False, None, 77)])
def test_frontmost_pid_uses_os_answer_before_driver(monkeypatch, answered, pid, expected):
    lookup = AsyncMock(return_value=(answered, pid))
    monkeypatch.setattr(driver_client, "_lsappinfo_front", lookup)
    driver = driver_client.DriverClient(__file__, "fake-socket")
    driver.call = AsyncMock(return_value=CallToolResult(
        content=[], structured_content={"apps": [{"pid": 77, "active": True}]}))
    assert asyncio.run(driver.frontmost_pid()) == expected
    lookup.assert_awaited_once_with()
    if answered:
        driver.call.assert_not_awaited()
    else:
        driver.call.assert_awaited_once_with("list_apps", {})


@pytest.mark.parametrize("platform,exists", [("linux", True), ("darwin", False)])
def test_run_guards_do_not_spawn(monkeypatch, no_real_subprocesses, platform, exists):
    monkeypatch.setattr(driver_client.sys, "platform", platform)
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: exists)
    assert asyncio.run(driver_client._run_lsappinfo("front")) is None
    no_real_subprocesses.assert_not_called()


@pytest.mark.parametrize("returncode,output,expected", [
    (0, FRONT.encode(), FRONT), (1, FRONT.encode(), None),
    (0, b"bad byte: \xff", "bad byte: \ufffd"),
])
def test_run_uses_one_shell_free_child(monkeypatch, returncode, output, expected):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: True)
    process = SimpleNamespace(returncode=returncode, communicate=AsyncMock(return_value=(output, None)))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", spawn)
    args = ("info", "-only", "pid", "-only", "applicationtype", ASN)
    assert asyncio.run(driver_client._run_lsappinfo(*args)) == expected
    spawn.assert_awaited_once_with(
        os.path.join(os.sep, "usr", "bin", "lsappinfo"), *args,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


def test_run_spawn_failure_returns_none(monkeypatch):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("failed")))
    assert asyncio.run(driver_client._run_lsappinfo("front")) is None


@pytest.mark.parametrize("exception", [asyncio.TimeoutError, asyncio.CancelledError])
@pytest.mark.parametrize("already_exited", [False, True])
def test_run_kills_and_reaps_on_timeout_or_cancellation(monkeypatch, exception, already_exited):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: True)
    process = SimpleNamespace(
        communicate=AsyncMock(), kill=Mock(side_effect=ProcessLookupError if already_exited else None),
        wait=AsyncMock(),
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", spawn)
    timeouts = []

    async def interrupt(awaitable, timeout):
        timeouts.append(timeout)
        awaitable.close()
        raise exception

    monkeypatch.setattr(driver_client.asyncio, "wait_for", interrupt)
    if exception is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(driver_client._run_lsappinfo("front"))
    else:
        assert asyncio.run(driver_client._run_lsappinfo("front")) is None
    assert timeouts == [2.0]
    spawn.assert_awaited_once()
    process.kill.assert_called_once_with()
    process.wait.assert_awaited_once_with()
