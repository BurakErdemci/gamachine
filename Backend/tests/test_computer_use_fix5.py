"""Fail-closed x/y classification gaps found by the fix5 audits, using fakes only."""
import pytest
from mcp.types import CallToolResult

from computer_use import server

KEY = {"pid": 77, "window_id": 31}


def cache(handler, snapshot_id, key=KEY, **report):
    handler._cache_snapshot(CallToolResult(content=[], structured_content={
        "snapshot_id": snapshot_id, **report}), key)


def element(token, label, x, y, w, h, **extra):
    return {"element_token": token, "label": label, "screenshot_frame": {"x": x, "y": y, "w": w, "h": h}, **extra}


def framed(handler, key=KEY):
    cache(handler, "s1", key, elements=[
        element("s1:0", "Untitled - Paint", 0, 0, 800, 600, role="Window", depth=0),
        element("s1:1", "Share", 200, 20, 30, 30, depth=2),
        element("s1:2", "Pencil", 10, 10, 30, 30, depth=1),
    ])


def test_frameless_snapshot_replaces_previous_frames():
    handler = server.ComputerServer()
    framed(handler)
    cache(handler, "s2", elements=[
        {"element_token": "s2:1", "label": "Delete all", "frame": {"x": 10, "y": 10, "w": 30, "h": 30}}])
    assert handler._element_at({**KEY, "x": 20, "y": 20}) == (False, None)


def test_markdown_only_snapshot_keeps_previous_frames():
    handler = server.ComputerServer()
    framed(handler)
    cache(handler, "s2", tree_markdown='- [1] Button "Send"')
    assert handler._element_at({**KEY, "x": 20, "y": 20}) == (True, "Pencil")
    assert handler._element_at({**KEY, "x": 400, "y": 300}) == (True, "")


@pytest.mark.parametrize("window_id", [99, "31", None, 3.5, True])
def test_unknown_window_id_is_unknown(window_id):
    handler = server.ComputerServer()
    framed(handler)
    assert handler._element_at({"pid": 77, "window_id": window_id, "x": 400, "y": 300}) == (False, None)


def test_unknown_pid_is_unknown():
    handler = server.ComputerServer()
    framed(handler)
    assert handler._element_at({"pid": 78, "x": 400, "y": 300}) == (False, None)


def test_point_over_element_without_frame_is_unknown():
    handler = server.ComputerServer()
    cache(handler, "s1", elements=[
        element("s1:0", "Untitled - Paint", 0, 0, 800, 600, role="Window", depth=0),
        element("s1:1", "Pencil", 10, 10, 30, 30, depth=1),
        {"element_token": "s1:2", "label": "Delete all", "depth": 1},
    ])
    assert handler._element_at({**KEY, "x": 400, "y": 300}) == (False, None)
    assert handler._element_at({**KEY, "x": 20, "y": 20}) == (True, "Pencil")


@pytest.mark.parametrize("x, y", [("20", 20), (20, None), (True, 20)])
def test_non_numeric_coordinates_are_unknown(x, y):
    handler = server.ComputerServer()
    framed(handler)
    got = handler._element_at({**KEY, "x": x, "y": y})
    assert got == (False, None)


def test_from_zoom_coordinates_are_unknown():
    handler = server.ComputerServer()
    framed(handler)
    assert handler._element_at({**KEY, "x": 20, "y": 20, "from_zoom": True}) == (False, None)


def test_windowless_markdown_snapshot_does_not_shadow_framed_snapshot():
    handler = server.ComputerServer()
    framed(handler)
    cache(handler, "o1", {"pid": 77}, tree_markdown='- [1] Button "Pencil"')
    assert handler._element_at({"pid": 77, "x": 400, "y": 300}) == (True, "")
    assert handler._element_at({"pid": 77, "x": 210, "y": 30}) == (True, "Share")


def test_windowless_pid_without_any_frames_is_unknown():
    handler = server.ComputerServer()
    cache(handler, "o1", {"pid": 77}, tree_markdown='- [1] Button "Pencil"')
    assert handler._element_at({"pid": 77, "x": 400, "y": 300}) == (False, None)
