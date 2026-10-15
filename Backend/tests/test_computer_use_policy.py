"""Permission-boundary validation, independent of driver availability."""
import pytest

from computer_use import policy as p


def refused(tool, args, key=None):
    result = p.validate_call(tool, args)
    assert isinstance(result, p.Refusal)
    if key:
        assert key in result.reason


@pytest.mark.parametrize("tool,args,key", [
    ("computer_run_script", {}, None), ("computer_click", [], "args"),
    ("computer_click", {}, "pid"),
    *[("computer_click", {"pid": value}, "pid") for value in (True, False, 0, -1, "4", 1.5, None)],
    *[("computer_click", {"pid": 4, key: "x"}, key) for key in ("session", "scope", "target", "debug_image_out", "app", "bundle_id")],
    ("computer_window_state", {"pid": 4, "window_id": 1, "screenshot_out_file": "output"}, "screenshot_out_file"),
    ("computer_window_state", {"pid": 4}, "window_id"),
    *[("computer_launch_app", {"bundle_id": "com.apple.TextEdit", key: "x"}, key) for key in ("name", "urls", "additional_arguments", "webkit_inspector_port", "session")],
    ("computer_launch_app", {"bundle_id": ""}, "bundle_id"),
    *[("computer_confirm_risky_step", {"pid": 4, "summary": value}, "summary") for value in ("", "x" * 501, 4, None)],
])
def test_refusals(tool, args, key):
    refused(tool, args, key)


def test_schema_table_matches_pinned_surface():
    assert len(p.TOOLS) == 17
    assert "capture_id" in p.TOOLS["computer_click"].allowed
    assert "capture_id" not in p.TOOLS["computer_double_click"].allowed
    assert "capture_id" not in p.TOOLS["computer_right_click"].allowed
    assert p.validate_call("computer_list_apps", {}).args == {}
    for name, spec in p.TOOLS.items():
        assert not spec.allowed.intersection({"session", "scope", "target", "debug_image_out", "screenshot_out_file"})
    for bundle_id in p.HARD_DENY_BUNDLE_IDS:
        assert p.is_hard_denied(bundle_id.upper())
    assert not p.is_hard_denied("com.apple.TextEdit")


@pytest.mark.parametrize("step", [
    {"tool": "click", "args": {"app": "Terminal"}},
    {"click": {"window": "Terminal"}},
    {"click": {"scope": "desktop"}},
    {"click": {"target": {"kind": "desktop"}}},
    {"click": {"pid": 5}}, {"click": {"window_id": 8}},
    {"click": {}, "expect": [{"app": "Terminal"}]},
    {"click": {}, "wait_for": {"window": "x"}},
    {"wait_for": [{"pid": 5}]}, {"expect": [{"window_id": 8}]},
    {"expect": [{}] * 5}, {"wait_for": "text"},
    {"click": {}, "double_click": {}},
    {"tool": "click", "args": {}, "click": {}},
    {"move_cursor": {"x": 1}}, {"tool": "move_cursor", "args": {}},
    {"tool": []}, {"click": []}, {"app": "x", "expect": {}},
    {"args": {"app": "Terminal"}, "expect": {}}, {}, "bad", None,
    {"tool": "type_text", "args": {}},
])
def test_nested_step_refusals(step):
    refused("computer_run_steps", {"pid": 4, "window_id": 7, "steps": [step]})


@pytest.mark.parametrize("args,key", [
    ({"steps": []}, "steps"), ({"steps": [{}] * 65}, "steps"),
    ({"steps": {}}, "steps"), ({"app": "Terminal"}, "app"),
    ({"window": "Terminal"}, "window"),
    ({"observe": {"pid": 5}}, "pid"),
    ({"observe": {"app": "Terminal"}}, "app"),
    ({"observe": {"screenshot_out_file": "output"}}, "screenshot_out_file"),
])
def test_batch_refusals(args, key):
    refused("computer_run_steps", {"pid": 4, "steps": [{"click": {}}], **args}, key)


def test_batch_accepts_shorthand_explicit_and_check_only_unchanged():
    steps = [
        {"click": {"role": "button", "name": "OK", "pid": 4, "window_id": 7}},
        {"tool": "type_text", "args": {"text": "hello", "nth": 2, "timeout_ms": 500}},
        {"wait_for": {"text": "hello"}, "expect": [{"selected": True}]},
    ]
    args = {"pid": 4, "window_id": 7, "steps": steps, "observe": {"include_screenshot": True}}
    assert p.validate_call("computer_run_steps", args).args is args


@pytest.mark.parametrize("keys", [
    ["delete", "COMMAND"], ["Shift", "Command", "Backspace"],
    ["cmd", "alt", "shift", "delete"], ["escape", "option", "cmd"],
    ["esc", "cmd", "opt", "shift"], ["q", "CONTROL", "cmd"],
    ["q", "shift", "cmd"], ["q", "shift", "cmd", "option"],
])
def test_risky_alias_order_and_batch_detection(keys):
    assert p.risky_reason("computer_hotkey", {"keys": keys})
    assert p.risky_reason("computer_press_key", {"key": keys[0], "modifiers": keys[1:]})
    for step in ({"hotkey": {"keys": keys}}, {"tool": "hotkey", "args": {"keys": keys}}):
        assert p.risky_reason("computer_run_steps", {"steps": [step]})


def test_risk_is_exact_key_set():
    for keys in (["cmd", "s"], ["cmd", "delete", "ctrl"], ["delete"]):
        assert p.risky_reason("computer_hotkey", {"keys": keys}) == ""
    assert p.risky_reason("computer_confirm_risky_step", {"summary": "Send message"}) == "Send message"
