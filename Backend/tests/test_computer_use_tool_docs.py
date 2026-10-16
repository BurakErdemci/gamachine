"""Published tool guidance and refusals describe the policy's actual format."""
import asyncio
import json

import pytest

from computer_use import policy, server


def test_instructions_explain_args_envelope():
    mcp = server.create_server()
    assert (
        'Every tool takes exactly one parameter named args; put all arguments inside it, '
        'e.g. {"args": {"pid": 123, "window_id": 45}}.'
    ) in mcp.instructions


def test_run_steps_description_has_valid_example_and_policy_names():
    tools = asyncio.run(server.create_server().list_tools())
    assert {tool.name for tool in tools} == set(policy.TOOLS)
    for tool in tools:
        spec = policy.TOOLS[tool.name]
        prefix = (
            "Computer-use operation. Allowed args: " + ", ".join(sorted(spec.allowed))
            + ". Required: " + ", ".join(sorted(spec.required)) + "."
        )
        if tool.name != "computer_run_steps":
            assert tool.description == prefix
            continue
        description = tool.description
        assert description.startswith(prefix)
        assert 'Each step is {"tool": NAME, "args": {...}}' in description
        assert 'shorthand {NAME: {...}}' in description
        assert ", ".join(sorted(policy.STEP_TOOLS)) in description
        check_keys = ", ".join(sorted(policy._CHECK_KEYS - {"pid", "window_id"}))
        assert f"a list of up to 4: {check_keys}" in description
        assert "(no computer_ prefix)" in description
        assert "inherit pid/window_id from the batch" in description
        assert "1-64 steps; delay_ms 0-2000 between steps" in description
        example = json.loads(description.split("Example: ", 1)[1])
        assert example == {"args": {"pid": 123, "window_id": 45, "steps": [
            {"tool": "type_text", "args": {"text": "hello "}},
            {"tool": "press_key", "args": {"key": "return"}},
        ]}}
        assert isinstance(policy.validate_call("computer_run_steps", example["args"]), policy.Validated)


@pytest.mark.parametrize("step,key", [
    ({"action": "type_text"}, "action"),
    ({"type": "type_text"}, "type"),
    ({"tool": "type_text", "args": {"text": "hello"}, "extra": True}, "extra"),
    ({"type_text": {"text": "hello"}, "args": {}}, "args"),
])
def test_forbidden_step_key_explains_format(step, key):
    refusal = policy.validate_call("computer_run_steps", {"pid": 123, "steps": [step]})
    assert isinstance(refusal, policy.Refusal)
    assert refusal.reason == (
        f'Forbidden step key: {key}. A step is {{"tool": "type_text", "args": {{...}}}} '
        'or {"type_text": {...}}; tools: ' + ", ".join(sorted(policy.STEP_TOOLS))
    )


@pytest.mark.parametrize("tool", ["computer_type_text", "unknown", 5, ["x" * 80]])
def test_unsupported_step_tool_explains_names(tool):
    refusal = policy.validate_call("computer_run_steps", {"pid": 123, "steps": [{"tool": tool}]})
    assert isinstance(refusal, policy.Refusal)
    value = tool if isinstance(tool, str) else repr(tool)[:40]
    expected = (
        f"Unsupported step tool: {value}. Use one of: "
        + ", ".join(sorted(policy.STEP_TOOLS)) + " (no computer_ prefix)"
    )
    if tool == "computer_type_text":
        expected += '. Did you mean "type_text"?'
    assert refusal.reason == expected


def test_empty_step_explains_format():
    refusal = policy.validate_call("computer_run_steps", {"pid": 123, "steps": [{}]})
    assert isinstance(refusal, policy.Refusal)
    assert refusal.reason == (
        'Step requires a tool, wait_for or expect. A step is {"tool": "type_text", "args": {...}}'
    )


@pytest.mark.parametrize("args", [
    {"pid": 123, "steps": [{"type_text": {"text": "hello"}}], "extra": True},
    {"pid": 123, "steps": [{"type_text": {"text": "hello", "extra": True}}]},
])
def test_non_step_keys_keep_original_refusal(args):
    refusal = policy.validate_call("computer_run_steps", args)
    assert isinstance(refusal, policy.Refusal)
    assert refusal.reason == "Forbidden argument: extra"
