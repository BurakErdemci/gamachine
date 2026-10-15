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
    assert len(p.TOOLS) == 19
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


@pytest.mark.parametrize("value", [None, 7, "", " com.apple.Terminal", "com.apple.Terminal ",
                                  "com.apple.Terminal\n", "com.apple.\u200bTerminal", "a b", "a" * 256])
def test_malformed_bundle_ids(value):
    assert not p.valid_bundle_id(value)
    refused("computer_launch_app", {"bundle_id": value}, "bundle_id")


@pytest.mark.parametrize("value", ["a", "com.apple.TextEdit", "A-0." + "a" * 251])
def test_valid_bundle_id_boundaries(value):
    assert p.valid_bundle_id(value)


@pytest.mark.parametrize("bundle", [
    "com.1password.1password", "com.bitwarden.desktop", "com.dashlane.dashlanephonefinal",
    "com.lastpass.LastPass", "com.nordsec.nordpass", "com.apple.Passwords",
    "com.apple.keychainaccess", "com.apple.ScriptEditor2", "com.apple.Automator",
    "com.apple.shortcuts", "com.apple.ScreenContinuity", "com.apple.SecurityAgent",
    "com.apple.LocalAuthentication.UIAgent",
])
def test_sensitive_and_scripting_apps_are_hard_denied(bundle):
    assert p.is_hard_denied(bundle.upper())


def test_window_listing_requires_pid():
    refused("computer_list_windows", {}, "pid")
    assert isinstance(p.validate_call("computer_list_windows", {"pid": 4}), p.Validated)


@pytest.mark.parametrize("urls", [["https://example.com", "http://example.com", "file:///document"],
                                 ["/Users/me/Documents"], ["https://example.com"] * 16])
def test_launch_accepts_only_opened_url_capabilities(urls):
    args = {"bundle_id": "com.apple.finder", "urls": urls}
    assert p.validate_call("computer_launch_app", args).args is args


@pytest.mark.parametrize("urls", [[], ["https://example.com"] * 17, "https://example.com", [7],
                                 ["relative/path"], ["javascript:alert(1)"],
                                 ["x-apple.systempreferences:settings"], ["https://[bad"]])
def test_launch_refuses_bad_urls(urls):
    refused("computer_launch_app", {"bundle_id": "com.apple.finder", "urls": urls}, "urls")


def test_clipboard_is_text_only():
    assert isinstance(p.validate_call("computer_clipboard_read", {"include_text": True}), p.Validated)
    assert isinstance(p.validate_call("computer_clipboard_write", {"text": ""}), p.Validated)
    refused("computer_clipboard_write", {}, "text")
    refused("computer_clipboard_write", {"text": 7}, "text")
    for key in ("file_path", "image_path", "session", "pid"):
        refused("computer_clipboard_write", {"text": "hello", key: "value"}, key)


# Independently pinned numeric fields. Exercise every exposed occurrence, plus
# the same fields in action steps and window-state observations.
NUMERIC_FIELDS = "pid window_id count x y x1 y1 x2 y2 from_x from_y to_x to_y duration_ms amount delay_ms max_depth max_elements max_image_dimension max_dimension timeout_ms".split()
NUMERIC_CASES = [(tool, key) for tool, spec in p.TOOLS.items()
                 for key in NUMERIC_FIELDS if key in spec.allowed]
NUMERIC_CASES.append(("computer_drag", "steps"))


@pytest.mark.parametrize("tool,key", NUMERIC_CASES)
@pytest.mark.parametrize("value", [True, False])
def test_booleans_refused_for_all_numeric_arguments(tool, key, value):
    args = {name: 1 for name in p.TOOLS[tool].required}
    args.update(pid=4)
    if tool == "computer_run_steps":
        args["steps"] = [{"click": {}}]
    if tool == "computer_hotkey":
        args["keys"] = ["cmd", "s"]
    args[key] = value
    refused(tool, args, key)
    driver_tool = p.TOOLS[tool].driver_tool
    if driver_tool in p.STEP_TOOLS:
        refused("computer_run_steps", {"pid": 4, "window_id": 7,
                                       "steps": [{driver_tool: args}]}, key)
    if tool == "computer_window_state":
        refused("computer_run_steps", {"pid": 4, "window_id": 7,
                                       "steps": [{"click": {}}], "observe": args}, key)


@pytest.mark.parametrize("step", [{"click": {"nth": True}}, {"click": {"timeout_ms": False}},
                                 {"click": {}, "timeout_ms": True},
                                 {"expect": {"timeout_ms": True}}])
def test_named_target_and_check_numbers_refuse_booleans(step):
    refused("computer_run_steps", {"pid": 4, "steps": [step]})
