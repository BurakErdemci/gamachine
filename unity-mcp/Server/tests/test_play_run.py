"""Contract for background play_run transport, validation and images."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import ImageContent, TextContent

import services.tools.play_run as tool


CTX = SimpleNamespace()


@pytest.fixture
def sender(monkeypatch):
    fake = AsyncMock(return_value={"success": True, "data": {"status": "running"}})
    monkeypatch.setattr(tool, "send", fake)
    return fake


@pytest.mark.parametrize("kwargs", [
    {"seconds": 0}, {"seconds": 1801}, {"seconds": True}, {"seconds": float("nan")},
    {"seeds": []}, {"seeds": list(range(51))}, {"seeds": [True]},
    {"seconds": 1800, "seeds": [1, 2, 3]},
    {"until": {"hook": "hp", "op": "contains", "value": 0}},
    {"until": {"hook": "hp", "op": "=="}},
    {"sample_seconds": 0}, {"sample_seconds": 61},
    {"capture_every_seconds": 0.5}, {"watch": ["hp"] * 21},
    {"autopilot": 1}, {"stop_on_error": "true"}, {"camera": 1},
    {"input": [{"frame": 0, "do": "invalid", "key": "space"}]},
    {"input": [{"frame": 0, "do": "press", "axis": "leftStick/x", "value": float("inf")}]},
    {"seconds": 10**1000},
])
def test_invalid_start_never_sends(sender, kwargs):
    result = asyncio.run(tool.play_run(CTX, **kwargs))
    assert result["success"] is False
    sender.assert_not_called()


def test_start_forwards_parameters_once(sender):
    params = dict(seconds=60, seeds=[7, 9], autopilot=False,
                  input=[{"frame": 0, "key": "space", "do": "tap"}],
                  until={"hook": "hp", "op": "<=", "value": 0}, stop_on_error=False,
                  watch=["hp"], sample_seconds=2, camera="Main Camera")
    asyncio.run(tool.play_run(CTX, **params))
    sender.assert_awaited_once_with(CTX, "play_run", {"action": "start", **params})


def test_batch_with_capture_interval_never_sends(sender):
    result = asyncio.run(tool.play_run(CTX, seeds=[1, 2], capture_every_seconds=10))
    assert result["success"] is False
    assert result["error"] == (
        "capture_every_seconds applies to a single run; a batch of seeds captures only each run's final frame. "
        "Remove capture_every_seconds or run one seed."
    )
    sender.assert_not_called()


def test_single_seed_with_capture_interval_is_forwarded(sender):
    asyncio.run(tool.play_run(CTX, seeds=[1], capture_every_seconds=10))
    sender.assert_awaited_once_with(CTX, "play_run", {
        "action": "start", "seconds": 120, "seeds": [1], "autopilot": True,
        "stop_on_error": True, "sample_seconds": 5, "capture_every_seconds": 10,
    })


def test_wait_polls_to_done(sender, monkeypatch):
    pending = {"success": True, "_mcp_status": "pending", "data": {"status": "running"}}
    done = {"success": True, "data": {"status": "done", "results": []}}
    sender.side_effect = [pending, pending, done]
    monkeypatch.setattr(tool, "sleep", AsyncMock())
    assert asyncio.run(tool.play_run(CTX, action="wait")) == done
    assert sender.await_count == 3
    assert all(c.args == (CTX, "play_run", {"action": "status"}) for c in sender.await_args_list)


def test_wait_timeout_keeps_last_status(sender, monkeypatch):
    tick = [0.0]
    monkeypatch.setattr(tool, "monotonic", lambda: tick[0])

    async def advance(seconds):
        tick[0] += seconds

    monkeypatch.setattr(tool, "sleep", advance)
    result = asyncio.run(tool.play_run(CTX, action="wait", timeout_seconds=1))
    assert result["data"]["status"] == "running"
    assert result["message"] == "play_run still running after 1 s; call wait again."


@pytest.mark.parametrize("requested, expected", [(-5, 1), (0, 1), (1000, 200)])
def test_wait_clamps_deadline(sender, monkeypatch, requested, expected):
    tick = [0.0]
    monkeypatch.setattr(tool, "monotonic", lambda: tick[0])

    async def advance(_seconds):
        tick[0] += expected

    monkeypatch.setattr(tool, "sleep", advance)
    result = asyncio.run(tool.play_run(CTX, action="wait", timeout_seconds=requested))
    assert result["message"] == f"play_run still running after {expected} s; call wait again."


def test_done_shapes_both_images_without_base64_in_text(sender):
    sender.return_value = {"success": True, "data": {
        "status": "done", "image_base64": "/9j/sheet", "mime": "image/jpeg",
        "last_frame": {"image_base64": "iVBORframe", "mime": "image/png", "path": "frame.png"},
    }}
    result = asyncio.run(tool.play_run(CTX, action="status"))
    images = [c for c in result.content if isinstance(c, ImageContent)]
    assert [c.data for c in images] == ["/9j/sheet", "iVBORframe"]
    text = next(c.text for c in result.content if isinstance(c, TextContent))
    assert "image_base64" not in text
    assert json.loads(text)["data"]["last_frame"]["path"] == "frame.png"
    assert "image_base64" in sender.return_value["data"]["last_frame"]


@pytest.mark.parametrize("action", ["stop", "status"])
def test_single_send_actions(sender, action):
    asyncio.run(tool.play_run(CTX, action=action))
    sender.assert_awaited_once_with(CTX, "play_run", {"action": action})
