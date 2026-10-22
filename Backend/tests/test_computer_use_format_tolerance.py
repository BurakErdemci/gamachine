"""Near-miss input formats preserve validation and shortcut approval."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult

from computer_use import policy, server


KEYBOARD_CASES = [
    ("hotkey", {"keys": " + command + + s + "}, {"keys": ["command", "s"]}),
    ("hotkey", {"key": "s", "modifiers": ["command"]}, {"keys": ["command", "s"]}),
    ("hotkey", {"key": "s", "modifiers": " + command + shift + "},
     {"keys": ["command", "shift", "s"]}),
    ("hotkey", {"key": "s"}, {"keys": ["s"]}),
    ("hotkey", {"keys": "s"}, {"keys": ["s"]}),
    ("hotkey", {"keys": "+ +"}, {"keys": []}),
    ("press_key", {"key": "s", "modifiers": " + control + option + "},
     {"key": "s", "modifiers": ["control", "option"]}),
]


def step_for(form, tool, args):
    return {"tool": tool, "args": args} if form == "explicit" else {tool: args}


@pytest.mark.parametrize("tool,near,canonical", KEYBOARD_CASES)
@pytest.mark.parametrize("form", ["top", "explicit", "shorthand"])
def test_keyboard_formats_copy_only_when_normalizing(tool, near, canonical, form):
    if form == "top":
        name = "computer_" + tool
        args = {"pid": 4, **deepcopy(near)}
        expected = {"pid": 4, **deepcopy(canonical)}
    else:
        name = "computer_run_steps"
        args = {"pid": 4, "steps": [step_for(form, tool, deepcopy(near))]}
        expected = {"pid": 4, "steps": [step_for(form, tool, deepcopy(canonical))]}
    original = deepcopy(args)
    result = policy.validate_call(name, args)
    assert isinstance(result, policy.Validated)
    assert result.args == expected
    assert result.args is not args
    assert args == original
    canonical_result = policy.validate_call(name, expected)
    assert isinstance(canonical_result, policy.Validated)
    assert canonical_result.args is expected
    assert canonical_result == result


@pytest.mark.parametrize("observe", [True, False])
def test_observe_boolean_is_normalized_on_a_deep_copy(observe):
    args = {"pid": 4, "steps": [{"click": {"role": "button"}}], "observe": observe}
    original = deepcopy(args)
    result = policy.validate_call("computer_run_steps", args)
    assert isinstance(result, policy.Validated)
    expected = {"pid": 4, "steps": [{"click": {"role": "button"}}]}
    if observe:
        expected["observe"] = {}
    assert result.args == expected
    assert result.args is not args
    assert result.args["steps"] is not args["steps"]
    result.args["steps"][0]["click"]["role"] = "text field"
    assert args == original


@pytest.mark.parametrize("observe", ["yes", 1, 0, None, []])
def test_other_observe_values_still_refused(observe):
    assert policy.validate_call("computer_run_steps", {
        "pid": 4, "steps": [{"click": {}}], "observe": observe,
    }) == policy.Refusal("observe must be an object")


def test_all_steps_are_normalized_without_sharing_nested_data():
    args = {"pid": 4, "steps": [
        {"hotkey": {"keys": "cmd+s"}},
        {"tool": "hotkey", "args": {"key": "p", "modifiers": ["command"]}},
        {"press_key": {"key": "s", "modifiers": "shift"}},
        {"tool": "press_key", "args": {"key": "p", "modifiers": "option"}},
        {"click": {"role": "button"}},
    ], "observe": True}
    original = deepcopy(args)
    result = policy.validate_call("computer_run_steps", args)
    assert isinstance(result, policy.Validated)
    assert result.args["steps"] == [
        {"hotkey": {"keys": ["cmd", "s"]}},
        {"tool": "hotkey", "args": {"keys": ["command", "p"]}},
        {"press_key": {"key": "s", "modifiers": ["shift"]}},
        {"tool": "press_key", "args": {"key": "p", "modifiers": ["option"]}},
        {"click": {"role": "button"}},
    ]
    assert result.args["observe"] == {}
    result.args["steps"][1]["args"]["keys"].append("shift")
    result.args["steps"][4]["click"]["role"] = "text field"
    assert args == original


@pytest.mark.parametrize("key", ["element", "element_index", "element_id", "element_ref", "index"])
@pytest.mark.parametrize("form", ["top", "explicit", "shorthand"])
def test_wrong_element_argument_names_the_token(key, form):
    action = {key: 39, "value": "hello"}
    name = "computer_set_value" if form == "top" else "computer_run_steps"
    args = ({"pid": 4, **action} if form == "top" else
            {"pid": 4, "steps": [step_for(form, "set_value", action)]})
    assert policy.validate_call(name, args) == policy.Refusal(
        f'Forbidden argument: {key}. Use element_token: "<snapshot_id>:<row>" '
        'from computer_window_state, e.g. row [39] of snapshot s00000029 is "s00000029:39".')


@pytest.mark.parametrize("tool,args,reason", [
    ("computer_set_value", {"pid": 4, "value": "x", "extra": 1}, "Forbidden argument: extra"),
    ("computer_list_windows", {"pid": 4, "index": 39}, "Forbidden argument: index"),
    ("computer_hotkey", {"pid": 4, "keys": ["cmd", "s"], "key": "s"}, "Forbidden argument: key"),
    ("computer_hotkey", {"pid": 4, "keys": "cmd+s", "key": "s"}, "Forbidden argument: key"),
    ("computer_hotkey", {"pid": 4, "key": "s", "modifiers": 1}, "Forbidden argument: key"),
    ("computer_hotkey", {"pid": 4, "key": "s", "modifiers": [1]}, "Forbidden argument: key"),
    ("computer_hotkey", {"pid": 4, "keys": 1}, "keys must be a list of strings"),
    ("computer_press_key", {"pid": 4, "key": "s", "modifiers": 1}, "modifiers must be a list of strings"),
    ("computer_press_key", {"pid": 4, "key": 1, "modifiers": ["cmd"]}, "key must be a string"),
    ("computer_run_steps", {"pid": 4, "steps": [{"tool": [], "args": {"keys": "cmd+s"}}]},
     "Unsupported step tool: []. Use one of: " + ", ".join(sorted(policy.STEP_TOOLS)) + " (no computer_ prefix)"),
    ("computer_run_steps", {"pid": 4, "steps": [{"hotkey": "cmd+s"}]}, "Step args must be an object"),
    ("computer_run_steps", {"pid": 4, "steps": [{"hotkey": {}, "press_key": {}}]},
     "Step must contain exactly one tool"),
])
def test_other_validation_behavior_is_unchanged(tool, args, reason):
    original = deepcopy(args)
    assert policy.validate_call(tool, args) == policy.Refusal(reason)
    assert args == original


@pytest.fixture
def proxy(monkeypatch):
    driver = AsyncMock()
    driver.binary, driver.socket = "bin", "sock"
    driver.call.return_value = CallToolResult(content=[], structuredContent={"ok": True, "steps": [{"ok": True}]})
    monkeypatch.setattr(server, "fetch_runtime", AsyncMock(return_value=("bin", "sock")))
    monkeypatch.setattr(server.driver_client, "identify_running_app", AsyncMock(
        return_value=("com.apple.TextEdit", "TextEdit")))
    monkeypatch.setattr(server, "_batch_stop", AsyncMock(return_value=None))
    approval = AsyncMock(return_value={"approved": False})
    monkeypatch.setattr(server.approval_bridge, "request_approval", approval)
    return driver, approval


@pytest.mark.parametrize("near,canonical", [
    ({"keys": "cmd+q"}, {"keys": ["cmd", "q"]}),
    ({"keys": "cmd+delete"}, {"keys": ["cmd", "delete"]}),
    ({"key": "q", "modifiers": "cmd"}, {"keys": ["cmd", "q"]}),
])
@pytest.mark.parametrize("form", ["top", "explicit", "shorthand"])
def test_risky_hotkey_formats_require_identical_approval(proxy, near, canonical, form):
    driver, approval = proxy
    params = []
    for action in (near, canonical):
        driver.call.reset_mock()
        approval.reset_mock()
        name = "computer_hotkey" if form == "top" else "computer_run_steps"
        args = ({"pid": 4, **action} if form == "top" else
                {"pid": 4, "steps": [step_for(form, "hotkey", action)]})
        result = asyncio.run(server.ComputerServer(driver).call(name, args))
        assert result.is_error
        approval.assert_awaited_once()
        params.append(approval.call_args.kwargs["params"])
        assert params[-1]["risky"] is True
        assert params[-1]["risk_reason"]
        driver.call.assert_not_awaited()
    assert params[0] == params[1]


@pytest.mark.parametrize("form", ["top", "explicit", "shorthand"])
def test_press_key_string_modifiers_preserve_risky_approval(proxy, form):
    driver, approval = proxy
    params = []
    for modifiers in ("cmd", ["cmd"]):
        action = {"key": "q", "modifiers": modifiers}
        name = "computer_press_key" if form == "top" else "computer_run_steps"
        args = ({"pid": 4, **action} if form == "top" else
                {"pid": 4, "steps": [step_for(form, "press_key", action)]})
        assert asyncio.run(server.ComputerServer(driver).call(name, args)).is_error
        params.append(approval.call_args.kwargs["params"])
        assert params[-1]["risky"] is True
        driver.call.assert_not_awaited()
    assert params[0] == params[1]


@pytest.mark.parametrize("form", ["top", "explicit", "shorthand", "split"])
def test_server_forwards_normalized_args(proxy, form):
    driver, approval = proxy
    approval.return_value = {"approved": True}
    if form == "top":
        name, args = "computer_hotkey", {"pid": 4, "keys": "cmd+s"}
    else:
        name = "computer_run_steps"
        args = {"pid": 4, "steps": [step_for(form, "hotkey", {"keys": "cmd+s"})], "observe": True}
        if form == "split":
            args["steps"].append({"press_key": {"key": "s", "modifiers": "shift"}})
    original = deepcopy(args)
    assert not asyncio.run(server.ComputerServer(driver).call(name, args)).is_error
    sent = driver.call.await_args_list
    if form == "top":
        assert sent[0].args == ("hotkey", {"pid": 4, "keys": ["cmd", "s"]})
    else:
        first = sent[0].args[1]["steps"][0]
        assert (first["args"] if form == "explicit" else first["hotkey"]) == {"keys": ["cmd", "s"]}
        assert sent[-1].args[1]["observe"] == {}
        if form == "split":
            assert len(sent) == 2
            assert sent[1].args[1]["steps"] == [{"press_key": {"key": "s", "modifiers": ["shift"]}}]
    assert args == original
