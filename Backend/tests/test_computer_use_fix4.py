"""Critical-action classification gaps found by the fix4 audits, using fakes only."""
import json

import pytest
from mcp.types import CallToolResult

from computer_use import policy, server

APP = "win:c:" + chr(92) + "apps" + chr(92) + "editor.exe"


@pytest.mark.parametrize("name", ["DELETEall", "SENDmessage", "PAYnow", "BUYnow", "SHAREnow", "sEND", "gOnDeR"])
def test_mixed_case_labels_are_critical(name):
    assert policy.critical_name(name)


@pytest.mark.parametrize("name", ["Pencil", "Silver", "Settings", "Close Tab", "Display", "Border"])
def test_ordinary_labels_stay_non_critical(name):
    assert policy.critical_name(name) == ""


def cache(handler, snapshot_id, key, **report):
    handler._cache_snapshot(CallToolResult(content=[], structured_content={
        "snapshot_id": snapshot_id, **report}), key)


def element(token, label, x, y, w, h, **extra):
    return {"element_token": token, "label": label, "screenshot_frame": {"x": x, "y": y, "w": w, "h": h}, **extra}


def share_snapshot(handler, key=None):
    cache(handler, "s5", key or {"pid": 77, "window_id": 31}, elements=[
        element("s5:0", "Order list.xlsx", 0, 0, 800, 600, role="Window", depth=0),
        element("s5:1", "Share", 700, 10, 60, 30, depth=1),
        element("s5:2", "Pencil", 10, 10, 30, 30, depth=1),
    ])


def reason(handler, args, tool="computer_click"):
    return policy.critical_reason(tool, args, app_id=APP, resolve=handler._resolver(args))


def test_window_root_is_ignored_by_the_hit_test():
    handler = server.ComputerServer()
    share_snapshot(handler)
    assert handler._element_at({"pid": 77, "window_id": 31, "x": 400, "y": 300}) == (True, "")
    assert handler._element_at({"pid": 77, "window_id": 31, "x": 710, "y": 20}) == (True, "Share")


def test_xy_without_window_id_uses_the_latest_frames_of_the_pid():
    handler = server.ComputerServer()
    share_snapshot(handler)
    assert reason(handler, {"pid": 77, "x": 710, "y": 20})
    assert not reason(handler, {"pid": 77, "x": 20, "y": 20})
    assert reason(handler, {"pid": 78, "x": 710, "y": 20})


def test_token_and_xy_are_both_checked():
    handler = server.ComputerServer()
    share_snapshot(handler)
    args = {"pid": 77, "window_id": 31, "element_token": "s5:2", "x": 710, "y": 20}
    assert reason(handler, args)
    assert not reason(handler, {**args, "x": 20, "y": 20})


def test_xy_beside_a_name_is_checked():
    handler = server.ComputerServer()
    share_snapshot(handler)
    assert reason(handler, {"pid": 77, "window_id": 31, "name": "Pencil", "x": 710, "y": 20})


def test_markdown_only_snapshot_keeps_frames_and_updates_names():
    handler = server.ComputerServer()
    share_snapshot(handler)
    cache(handler, "s6", {"pid": 77, "window_id": 31}, tree_markdown='- [1] Button "Send"')
    assert handler._element_at({"pid": 77, "window_id": 31, "x": 710, "y": 20}) == (True, "Share")
    assert handler._element_name({"pid": 77, "window_id": 31, "element_token": "s6:1"}) == "Send"


def test_xy_over_a_snapshot_with_no_frames_is_unverified():
    handler = server.ComputerServer()
    cache(handler, "s1", {"pid": 77, "window_id": 31}, tree_markdown='- [1] Button "Send"')
    assert handler._element_at({"pid": 77, "window_id": 31, "x": 5, "y": 5}) == (False, None)


@pytest.mark.parametrize("elements", [None, {"element_token": "s1:1", "label": "Pencil"}, []])
def test_unusable_elements_fall_back_to_markdown_rows(elements):
    handler = server.ComputerServer()
    cache(handler, "s1", {"pid": 77, "window_id": 31}, elements=elements, tree_markdown='- [1] Button "Pencil"')
    assert handler._element_name({"pid": 77, "window_id": 31, "element_token": "s1:1"}) == "Pencil"


def write_settings(path, settings):
    path.write_text(json.dumps(settings), encoding="utf-8")
    return path


def hook_settings(matcher):
    return {"hooks": {"PreToolUse": [{"matcher": matcher, "hooks": [
        {"type": "command", "command": 'bash "$HOME/.claude/hooks/guard-computer-use-confirm.sh"'}]}]}}


def test_hook_matcher_must_cover_every_tool(tmp_path):
    from computer_use.standalone import hookcheck
    prefix = "mcp__gamachineComputerLocal__"
    seven = prefix + "computer_(click|type_text|press_key|hotkey|run_steps|set_value|confirm_risky_step)"
    assert hookcheck.confirm_hook_installed([write_settings(tmp_path / "a.json", hook_settings(seven))]) is False
    assert hookcheck.confirm_hook_installed(
        [write_settings(tmp_path / "b.json", hook_settings(prefix + ".*"))]) is True


def test_disable_all_hooks_in_any_file_vetoes_the_guard(tmp_path):
    from computer_use.standalone import hookcheck
    good = write_settings(tmp_path / "settings.json", hook_settings("mcp__gamachineComputerLocal__.*"))
    off = write_settings(tmp_path / "settings.local.json", {"disableAllHooks": True})
    assert hookcheck.confirm_hook_installed([good]) is True
    assert hookcheck.confirm_hook_installed([good, off]) is False
    assert hookcheck.confirm_hook_installed([off, good]) is False
