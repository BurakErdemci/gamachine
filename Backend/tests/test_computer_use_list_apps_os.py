"""App discovery tests use fake OS records and subprocesses only."""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from computer_use import driver_client, server


BUNDLE = "com.unity3d.UnityEditor5.x"
APP_PATH = os.path.join(os.sep, "Applications", "Unity", "Unity.app")
UNITY = (
    '62) "Unity" ASN:0x0-0x228228:\n'
    f'    bundleID="{BUNDLE}"\n'
    f'    bundle path="{APP_PATH}"\n'
    '    pid = 93785 token=[sess=100021 pid=93785 uid:501] type="Foreground"\n'
)
FINDER = {"pid": 651, "name": "Finder", "active": True}


@pytest.fixture(autouse=True)
def no_real_subprocesses(monkeypatch):
    spawn = AsyncMock(side_effect=AssertionError("real subprocess forbidden"))
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", spawn)
    return spawn


def test_parser_keeps_foreground_and_drops_background_blocks():
    records = UNITY
    for index, kind in enumerate(("UIElement", "BackgroundOnly"), 63):
        records += (f'{index}) "Agent" ASN:0x0-0x1:\n'
                    f'    bundleID="com.apple.agent"\n    pid = {index} type="{kind}"\n')
    assert driver_client._parse_lsappinfo_list(records) == [{
        "bundle_id": BUNDLE, "kind": "desktop", "name": "Unity", "pid": 93785,
        "running": True, "launch_path": APP_PATH,
    }]


@pytest.mark.parametrize("text", [
    None, "garbage", "", 42,
    UNITY.replace(BUNDLE, "invalid bundle"),
    UNITY + f'    bundleID="{BUNDLE}"\n',
    UNITY + '    bundleID="com.other.App"\n',
    UNITY.replace("pid = 93785", "pid = 0"),
    UNITY.replace("pid = 93785", "pid = -1"),
    UNITY + '    pid = 123\n',
    UNITY.replace('type="Foreground"', 'type="UIElement"'),
    UNITY.replace('type="Foreground"', ''),
])
def test_parser_drops_invalid_or_ambiguous_records(text):
    assert driver_client._parse_lsappinfo_list(text) == []


def test_parser_ignores_fields_in_quoted_header_name():
    name = 'Unity bundleID="com.injected.App" pid = 8 type="UIElement"'
    text = UNITY.replace('"Unity" ASN:', f'"{name}" ASN:')
    row, = driver_client._parse_lsappinfo_list(text)
    assert row["name"] == name
    assert row["bundle_id"] == BUNDLE
    assert row["pid"] == 93785
    assert driver_client._parse_lsappinfo_list(text.replace(f'    bundleID="{BUNDLE}"\n', "")) == []


def test_parser_does_not_borrow_fields_from_next_block():
    first = '1) "Incomplete" ASN:0x1:\n    bundleID="com.incomplete.App"\n'
    assert driver_client._parse_lsappinfo_list(first + UNITY) == driver_client._parse_lsappinfo_list(UNITY)


def test_parser_allows_empty_name_and_omits_relative_path():
    row, = driver_client._parse_lsappinfo_list(
        UNITY.replace('"Unity" ASN:', '"" ASN:').replace(APP_PATH, "relative.app"))
    assert row["name"] is None
    assert "launch_path" not in row
    assert "active" not in row


def test_parser_caps_rows_at_256():
    records = "".join(UNITY.replace("pid = 93785", f"pid = {index}") for index in range(1, 301))
    assert [row["pid"] for row in driver_client._parse_lsappinfo_list(records)] == list(range(1, 257))


@pytest.mark.parametrize("output", [None, "", "garbage", UNITY])
def test_running_apps_looks_up_os_function_at_call_time(monkeypatch, output):
    lookup = AsyncMock(return_value=output)
    monkeypatch.setattr(driver_client, "_lsappinfo_list_text", lookup)
    rows = asyncio.run(driver_client.running_apps_from_os())
    assert [row["pid"] for row in rows] == ([93785] if output == UNITY else [])
    lookup.assert_awaited_once_with()


def test_running_apps_returns_empty_on_os_exception(monkeypatch):
    monkeypatch.setattr(driver_client, "_lsappinfo_list_text", AsyncMock(side_effect=OSError("unavailable")))
    assert asyncio.run(driver_client.running_apps_from_os()) == []


@pytest.mark.parametrize("platform,exists", [("linux", True), ("darwin", False)])
def test_list_lookup_guards_do_not_spawn(monkeypatch, no_real_subprocesses, platform, exists):
    monkeypatch.setattr(driver_client.sys, "platform", platform)
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: exists)
    assert asyncio.run(driver_client._lsappinfo_list_text()) is None
    no_real_subprocesses.assert_not_called()


@pytest.mark.parametrize("returncode,output,expected", [
    (0, UNITY.encode(), UNITY), (1, UNITY.encode(), None),
    (0, b"bad byte: \xff", "bad byte: \ufffd"),
])
def test_list_lookup_runs_one_shell_free_child(monkeypatch, returncode, output, expected):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: True)
    process = SimpleNamespace(returncode=returncode, communicate=AsyncMock(return_value=(output, None)))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", spawn)
    assert asyncio.run(driver_client._lsappinfo_list_text()) == expected
    spawn.assert_awaited_once_with(
        os.path.join(os.sep, "usr", "bin", "lsappinfo"), "list",
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


def test_list_lookup_spawn_failure_returns_none(monkeypatch):
    monkeypatch.setattr(driver_client.sys, "platform", "darwin")
    monkeypatch.setattr(driver_client.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(driver_client.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("failed")))
    assert asyncio.run(driver_client._lsappinfo_list_text()) is None


@pytest.mark.parametrize("exception", [asyncio.TimeoutError, asyncio.CancelledError])
@pytest.mark.parametrize("already_exited", [False, True])
def test_list_lookup_kills_and_reaps_on_timeout_or_cancellation(monkeypatch, exception, already_exited):
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
            asyncio.run(driver_client._lsappinfo_list_text())
    else:
        assert asyncio.run(driver_client._lsappinfo_list_text()) is None
    assert timeouts == [2.0]
    spawn.assert_awaited_once()
    process.kill.assert_called_once_with()
    process.wait.assert_awaited_once_with()


@pytest.fixture
def invoke(monkeypatch):
    monkeypatch.setattr(server, "fetch_runtime", AsyncMock(return_value=("fake", "fake-socket")))
    monkeypatch.setattr(server.approval_bridge, "request_approval", AsyncMock(return_value={"approved": True}))
    monkeypatch.setattr(driver_client, "_lsappinfo_list_text", AsyncMock(return_value=UNITY))

    def call(result, tool="computer_list_apps"):
        driver = SimpleNamespace(binary="fake", socket="fake-socket", call=AsyncMock(return_value=result))
        merged = asyncio.run(server.ComputerServer(driver).call(tool, {}))
        driver.call.assert_awaited_once_with("list_apps" if tool == "computer_list_apps" else "clipboard_read", {})
        return merged

    return call


def test_server_adds_unity_to_both_views_without_mutating_driver(invoke):
    payload = {"apps": [FINDER], "source": "driver"}
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload), annotations={"priority": 0.5})],
        structured_content=payload, is_error=False,
    )
    before = result.model_dump()
    merged = invoke(result)
    assert merged is not result
    for view in (json.loads(merged.content[0].text), merged.structured_content):
        assert view["source"] == "driver"
        assert view["apps"][0] == FINDER
        row, = view["apps"][1:]
        assert row["pid"] == 93785 and row["bundle_id"] == BUNDLE and row["running"] is True
        assert "active" not in row
    assert merged.content[0].annotations == result.content[0].annotations
    assert merged.is_error is False
    assert result.model_dump() == before


def test_server_deduplicates_across_every_driver_view_and_os_rows(invoke, monkeypatch):
    result = CallToolResult(content=[
        TextContent(type="text", text=json.dumps({"apps": [{"pid": 11}]})),
        TextContent(type="text", text=json.dumps({"apps": [{"pid": 22}]})),
    ], structured_content={"apps": [{"pid": 33}]})
    monkeypatch.setattr(driver_client, "running_apps_from_os", AsyncMock(return_value=[
        {"pid": pid} for pid in (11, 22, 33, 44, 44)
    ]))
    merged = invoke(result)
    assert [json.loads(block.text)["apps"] for block in merged.content] == [
        [{"pid": 11}, {"pid": 44}], [{"pid": 22}, {"pid": 44}],
    ]
    assert merged.structured_content["apps"] == [{"pid": 33}, {"pid": 44}]


@pytest.mark.parametrize("structured", [False, True])
def test_server_returns_original_when_pid_already_exists(invoke, structured):
    payload = {"apps": [{"pid": 93785}]}
    result = CallToolResult(content=[] if structured else [TextContent(type="text", text=json.dumps(payload))],
                           structured_content=payload if structured else None)
    assert invoke(result) is result


def test_server_preserves_unrelated_content_and_non_dict_rows(invoke):
    result = CallToolResult(content=[
        TextContent(type="text", text="driver diagnostic"),
        TextContent(type="text", text='{"other": []}'),
        TextContent(type="text", text='{"apps": [null, "legacy"]}'),
        ImageContent(type="image", data="aGVsbG8=", mime_type="image/png"),
    ], structured_content={"other": "kept"})
    merged = invoke(result)
    assert merged.content[:2] == result.content[:2]
    assert merged.content[3] == result.content[3]
    assert merged.structured_content == result.structured_content
    assert json.loads(merged.content[2].text)["apps"][:2] == [None, "legacy"]


@pytest.mark.parametrize("output", [None, "", "garbage"])
def test_server_os_unavailable_leaves_original_unchanged(invoke, monkeypatch, output):
    monkeypatch.setattr(driver_client, "_lsappinfo_list_text", AsyncMock(return_value=output))
    result = CallToolResult(content=[TextContent(type="text", text='{"apps": []}')])
    assert invoke(result) is result


def test_server_merge_exception_leaves_original_unchanged(invoke, monkeypatch):
    monkeypatch.setattr(driver_client, "running_apps_from_os", AsyncMock(side_effect=OSError("failed")))
    result = CallToolResult(content=[], structured_content={"apps": []})
    assert invoke(result) is result


@pytest.mark.parametrize("tool,error", [("computer_list_apps", True), ("computer_clipboard_read", False)])
def test_server_error_and_other_tools_skip_os_lookup(invoke, monkeypatch, tool, error):
    lookup = AsyncMock(side_effect=AssertionError("OS lookup unnecessary"))
    monkeypatch.setattr(driver_client, "running_apps_from_os", lookup)
    result = CallToolResult(content=[TextContent(type="text", text='{"apps": []}')], is_error=error)
    assert invoke(result, tool) is result
    lookup.assert_not_called()
