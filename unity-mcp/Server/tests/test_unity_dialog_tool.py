"""Server-only dialog decisions and approval classification."""

import asyncio
import importlib
from unittest.mock import Mock

import pytest

from services.registry import get_registered_tools
from services.registry.tool_actions import classify

tool = importlib.import_module("services.tools.unity_dialog")
FIRST = {"title": "Save scene", "buttons": ["Save", "Don't Save", "Cancel"], "hwnd": 123}
SECOND = {"title": "Build", "buttons": ["Yes", "No"], "hwnd": 456}


@pytest.fixture
def native_probe(monkeypatch):
    probe = Mock(return_value=[FIRST])
    press = Mock(return_value=True)
    monkeypatch.setattr(tool, "find_unity_dialogs", probe)
    monkeypatch.setattr(tool, "press_dialog_button", press)
    return probe, press


@pytest.mark.asyncio
@pytest.mark.parametrize("dialogs", [[], [FIRST], [FIRST, SECOND]])
async def test_list_hides_native_handles(native_probe, dialogs):
    probe, press = native_probe
    probe.return_value = dialogs
    assert await tool.unity_dialog(None, "list") == {
        "success": True,
        "data": {"dialogs": [{"title": d["title"], "buttons": d["buttons"]} for d in dialogs]},
    }
    press.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("dialogs,title,button,message", [
    ([FIRST], None, None, "button"),
    ([], None, "Save", "No Unity"),
    ([FIRST, SECOND], None, "Save", "title"),
    ([FIRST], "save scene", "Save", "not found"),
    ([FIRST], None, "Sa", "Save, Don't Save, Cancel"),
])
async def test_invalid_decisions_do_not_press(native_probe, dialogs, title, button, message):
    probe, press = native_probe
    probe.return_value = dialogs
    result = await tool.unity_dialog(None, "press", title=title, button=button)
    assert result["success"] is False
    assert message in result["error"]
    press.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("title,dialogs,selected,button", [
    (None, [FIRST], FIRST, "sAvE"),
    ("Build", [FIRST, SECOND], SECOND, "no"),
])
async def test_press_waits_for_selected_handle_to_disappear(native_probe, title, dialogs, selected, button):
    probe, press = native_probe
    probe.side_effect = [dialogs, dialogs, [d for d in dialogs if d is not selected]]
    result = await tool.unity_dialog(None, "press", title=title, button=button)
    listed = next(b for b in selected["buttons"] if b.casefold() == button.casefold())
    assert result == {"success": True, "data": {
        "pressed": listed, "title": selected["title"], "closed": True,
    }}
    press.assert_called_once_with(selected["hwnd"], listed)
    assert probe.call_count == 3


@pytest.mark.asyncio
async def test_failed_native_press_reports_choices(native_probe):
    _, press = native_probe
    press.return_value = False
    result = await tool.unity_dialog(None, "press", button="Save")
    assert result["success"] is False
    assert "Save, Don't Save, Cancel" in result["error"]


@pytest.mark.asyncio
async def test_press_reports_closed_false_after_three_seconds(native_probe, monkeypatch):
    elapsed = 0.0

    async def sleep(seconds):
        nonlocal elapsed
        assert 0 < seconds <= 0.1
        elapsed += seconds

    monkeypatch.setattr(tool, "monotonic", lambda: elapsed)
    monkeypatch.setattr(tool.asyncio, "sleep", sleep)
    result = await tool.unity_dialog(None, "press", button="Save")
    assert result["success"] is True
    assert result["data"]["closed"] is False
    assert elapsed == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_probe_and_press_run_outside_event_loop(native_probe, monkeypatch):
    import threading

    loop_thread = threading.get_ident()
    threads = []

    def probe():
        threads.append(threading.get_ident())
        return [FIRST] if len(threads) == 1 else []

    def press(*args):
        threads.append(threading.get_ident())
        return True

    monkeypatch.setattr(tool, "find_unity_dialogs", probe)
    monkeypatch.setattr(tool, "press_dialog_button", press)
    assert (await tool.unity_dialog(None, "press", button="Save"))["data"]["closed"] is True
    assert len(threads) == 3
    assert all(t != loop_thread for t in threads)


def test_dialog_tool_is_server_only_and_exported_in_core():
    registered = next(t for t in get_registered_tools() if t["name"] == "unity_dialog")
    assert registered["unity_target"] is None
    assert registered["group"] == "core"


@pytest.mark.parametrize("params,expected", [
    ({"action": "list"}, "read"), ({"action": "press"}, "write"), ({}, "write"),
])
def test_dialog_action_classification(params, expected):
    assert classify("unity_dialog", params) == expected
