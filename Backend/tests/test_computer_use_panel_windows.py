"""Panel discovery augments driver windows without changing the original reply."""
import asyncio
import json
import threading
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from computer_use import driver_client, server


def cg_row(**updates):
    return {
        "kCGWindowNumber": 1790, "kCGWindowOwnerPID": 42, "kCGWindowLayer": 3,
        "kCGWindowName": "New Scene", "kCGWindowOwnerName": "Unity",
        "kCGWindowIsOnscreen": True, "kCGWindowAlpha": 1.0,
        "kCGWindowBounds": {"X": 300.8, "Y": -20.2, "Width": 800.5, "Height": 628},
        **updates,
    }


def test_panel_fields_and_front_to_back_order():
    rows = driver_client._panel_rows([
        cg_row(), cg_row(kCGWindowNumber=1791, kCGWindowLayer=23),
    ], 42, 65)
    assert rows == [
        {"window_id": 1790, "pid": 42, "app_name": "Unity", "title": "New Scene",
         "layer": 3, "bounds": {"x": 300, "y": -20, "width": 800, "height": 628},
         "is_on_screen": True, "on_current_space": True, "z_index": 67},
        {"window_id": 1791, "pid": 42, "app_name": "Unity", "title": "New Scene",
         "layer": 23, "bounds": {"x": 300, "y": -20, "width": 800, "height": 628},
         "is_on_screen": True, "on_current_space": True, "z_index": 66},
    ]


@pytest.mark.parametrize("updates", [
    {"kCGWindowOwnerPID": 43}, {"kCGWindowOwnerPID": True},
    {"kCGWindowOwnerPID": "42"}, {"kCGWindowOwnerPID": 42.0},
    {"kCGWindowNumber": 0}, {"kCGWindowNumber": -1},
    {"kCGWindowNumber": True}, {"kCGWindowNumber": "1790"},
    {"kCGWindowNumber": 1790.0},
    {"kCGWindowLayer": 0}, {"kCGWindowLayer": 24},
    {"kCGWindowLayer": True}, {"kCGWindowLayer": "3"}, {"kCGWindowLayer": 3.0},
    {"kCGWindowIsOnscreen": False}, {"kCGWindowIsOnscreen": 1},
    {"kCGWindowAlpha": 0}, {"kCGWindowAlpha": -0.1},
    {"kCGWindowAlpha": "1"}, {"kCGWindowAlpha": None},
    {"kCGWindowAlpha": float("nan")},
    {"kCGWindowBounds": {"X": 0, "Y": 0, "Width": 39.9, "Height": 40}},
    {"kCGWindowBounds": {"X": 0, "Y": 0, "Width": 40, "Height": 39.9}},
    {"kCGWindowBounds": None}, {"kCGWindowBounds": {}},
    {"kCGWindowBounds": {"X": "0", "Y": 0, "Width": 40, "Height": 40}},
    {"kCGWindowBounds": {"X": 0, "Y": float("inf"), "Width": 40, "Height": 40}},
])
def test_panel_filters_skip_bad_row_and_keep_next(updates):
    rows = driver_client._panel_rows([cg_row(**updates), cg_row(kCGWindowNumber=1791)], 42, 0)
    assert [row["window_id"] for row in rows] == [1791]
    assert rows[0]["z_index"] == 1


@pytest.mark.parametrize("raw", [None, {}, (), "windows", 1])
def test_panel_non_list_input(raw):
    assert driver_client._panel_rows(raw, 42, 0) == []


def test_panel_missing_keys_and_malformed_entries():
    raw = cg_row(kCGWindowLayer=1, kCGWindowOwnerName=7)
    del raw["kCGWindowName"]
    del raw["kCGWindowAlpha"]
    raw["kCGWindowBounds"] = {"X": 0, "Y": 0, "Width": 40, "Height": 40}
    rows = driver_client._panel_rows([None, [], "bad", {}, raw], 42, 0)
    assert len(rows) == 1
    assert rows[0]["title"] == ""
    assert rows[0]["app_name"] is None
    assert rows[0]["bounds"]["width"] == 40
    for key in ("kCGWindowNumber", "kCGWindowOwnerPID", "kCGWindowLayer",
                "kCGWindowIsOnscreen", "kCGWindowBounds"):
        missing = cg_row()
        del missing[key]
        assert driver_client._panel_rows([missing], 42, 0) == []


def test_bool_target_pid_rejected():
    assert driver_client._panel_rows([cg_row(kCGWindowOwnerPID=1)], True, 0) == []


def test_panel_limit():
    rows = driver_client._panel_rows([cg_row(kCGWindowNumber=i + 1) for i in range(70)], 42, 5)
    assert len(rows) == 64
    assert [row["window_id"] for row in rows] == list(range(1, 65))
    assert [row["z_index"] for row in rows] == list(range(69, 5, -1))


def test_cg_list_non_darwin(monkeypatch):
    monkeypatch.setattr(driver_client.sys, "platform", "linux")
    assert driver_client._cg_window_list() is None


def test_os_lookup_runs_patched_function_in_worker_thread(monkeypatch):
    main_thread = threading.get_ident()

    def lookup():
        assert threading.get_ident() != main_thread
        return [cg_row()]

    monkeypatch.setattr(driver_client, "_cg_window_list", lookup)
    rows = asyncio.run(driver_client.panel_windows_from_os(42, 8))
    assert rows[0]["window_id"] == 1790
    assert rows[0]["z_index"] == 9


@pytest.mark.parametrize("failure", [None, [], RuntimeError("OS failed")])
def test_os_lookup_failure(monkeypatch, failure):
    def lookup():
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(driver_client, "_cg_window_list", lookup)
    assert asyncio.run(driver_client.panel_windows_from_os(42, 0)) == []


def test_os_lookup_timeout(monkeypatch):
    async def timeout(awaitable, timeout):
        assert timeout == 2.0
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(driver_client.asyncio, "wait_for", timeout)
    assert asyncio.run(driver_client.panel_windows_from_os(42, 0)) == []


def reply(payload, **kwargs):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))],
                          structured_content=payload, **kwargs)


def test_merge_preserves_driver_and_other_fields_in_both_views(monkeypatch):
    text_payload = {"windows": [{"window_id": 1, "z_index": 3}], "current_space_id": 8}
    structured_payload = {"windows": [{"window_id": 2, "z_index": 65}], "other": "keep"}
    result = reply(text_payload)
    result.structured_content = structured_payload
    result.content.append(TextContent(type="text", text="non-JSON status"))
    before = result.model_dump()
    lookup = AsyncMock(return_value=[{"window_id": 1790, "z_index": 66}])
    monkeypatch.setattr(driver_client, "panel_windows_from_os", lookup)
    merged = asyncio.run(server._merge_panel_windows(result, 42))
    lookup.assert_awaited_once_with(42, 65)
    assert json.loads(merged.content[0].text) == {
        **text_payload, "windows": text_payload["windows"] + lookup.return_value,
    }
    assert merged.structured_content == {
        **structured_payload, "windows": structured_payload["windows"] + lookup.return_value,
    }
    assert merged.content[1] is result.content[1]
    assert result.model_dump() == before


def test_merge_deduplicates_ids_across_views_and_panels(monkeypatch):
    result = reply({"windows": [{"window_id": 1}]})
    result.structured_content = {"windows": [{"window_id": 2}]}
    monkeypatch.setattr(driver_client, "panel_windows_from_os", AsyncMock(return_value=[
        {"window_id": 1}, {"window_id": 2}, {"window_id": 3}, {"window_id": 3},
    ]))
    merged = asyncio.run(server._merge_panel_windows(result, 42))
    assert json.loads(merged.content[0].text)["windows"] == [{"window_id": 1}, {"window_id": 3}]
    assert merged.structured_content["windows"] == [{"window_id": 2}, {"window_id": 3}]


@pytest.mark.parametrize("channel", ["text", "structured"])
def test_merge_single_channel_and_default_z(monkeypatch, channel):
    result = reply({"windows": [{"z_index": True}, {"z_index": "99"}, None]})
    if channel == "text":
        result.structured_content = None
    else:
        result.content = []
    lookup = AsyncMock(return_value=[{"window_id": 1790}])
    monkeypatch.setattr(driver_client, "panel_windows_from_os", lookup)
    merged = asyncio.run(server._merge_panel_windows(result, 42))
    lookup.assert_awaited_once_with(42, 0)
    payload = json.loads(merged.content[0].text) if channel == "text" else merged.structured_content
    assert payload["windows"][-1] == {"window_id": 1790}


@pytest.mark.parametrize("error,rows", [(True, []), (False, []), (False, [{"window_id": 1}])])
def test_merge_unchanged_when_error_or_no_extra(monkeypatch, error, rows):
    result = reply({"windows": [{"window_id": 1}]}, is_error=error)
    lookup = AsyncMock(return_value=rows)
    monkeypatch.setattr(driver_client, "panel_windows_from_os", lookup)
    assert asyncio.run(server._merge_panel_windows(result, 42)) is result
    if error:
        lookup.assert_not_awaited()


def test_merge_os_failure_returns_original(monkeypatch):
    result = reply({"windows": []})
    monkeypatch.setattr(driver_client, "panel_windows_from_os", AsyncMock(side_effect=OSError("failed")))
    assert asyncio.run(server._merge_panel_windows(result, 42)) is result


def test_merge_without_windows_does_not_lookup(monkeypatch):
    result = reply({"other": []})
    lookup = AsyncMock()
    monkeypatch.setattr(driver_client, "panel_windows_from_os", lookup)
    assert asyncio.run(server._merge_panel_windows(result, 42)) is result
    lookup.assert_not_awaited()
