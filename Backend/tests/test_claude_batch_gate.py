"""Unit and _can_use_tool gate tests for batch_execute script-write filtering."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from agentic import approval_mode  # noqa: E402
from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS  # noqa: E402
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny  # noqa: E402
from providers.claude_sdk_session import ClaudeSDKSession  # noqa: E402
from providers.unity_script_tools import (  # noqa: E402
    UNITY_SCRIPT_WRITE_TOOLS,
    batch_carries_script_write,
)


def _mk_session(**kwargs):
    s = ClaudeSDKSession(conversation_id=kwargs.pop("cid", 1), **kwargs)
    s._out_q = asyncio.Queue()
    return s


async def _drive_gate(session, tool_name, input_data, *, approve=True, timeout=2.0):
    task = asyncio.create_task(session._can_use_tool(tool_name, input_data, None))
    try:
        ev = await asyncio.wait_for(session._out_q.get(), timeout=0.3)
    except asyncio.TimeoutError:
        ev = None
    if ev is not None and ev.get("type") == "command_approval_needed":
        gid = ev["gate_id"]
        APPROVAL_RESULTS[gid] = approve
        APPROVAL_GATES[gid].set()
    res = await asyncio.wait_for(task, timeout=timeout)
    return res, ev


@pytest.fixture
def reset_approval_mode():
    approval_mode._reset_for_tests()
    try:
        yield
    finally:
        approval_mode._reset_for_tests()


# ── Unit tests of batch_carries_script_write ─────────────────────────────────


def test_harmless_batch_returns_false():
    payload = {
        "commands": [
            {"tool": "manage_gameobject", "params": {"action": "create", "name": "Cube"}},
            {"tool": "manage_components", "params": {"action": "add", "component_type": "Rigidbody"}},
        ]
    }
    assert batch_carries_script_write(payload) is False


@pytest.mark.parametrize("tool_name", UNITY_SCRIPT_WRITE_TOOLS)
def test_each_bare_script_tool_is_refused(tool_name):
    payload = {"commands": [{"tool": tool_name, "params": {"path": "Assets/A.cs"}}]}
    assert batch_carries_script_write(payload) is True


def test_prefixed_script_tool_is_refused():
    payload = {
        "commands": [
            {"tool": "manage_gameobject", "params": {"action": "get"}},
            {"tool": "mcp__unityMCP__create_script", "params": {"path": "Assets/A.cs"}},
        ]
    }
    assert batch_carries_script_write(payload) is True


def test_key_case_and_underscore_variants():
    script_payload = {
        "Commands": [
            {"TOOL": "delete_script", "Params": {"path": "Assets/A.cs"}},
        ]
    }
    harmless_payload = {
        "Commands": [
            {"TOOL": "manage_gameobject", "Params": {"action": "create", "name": "A"}},
        ]
    }
    assert batch_carries_script_write(script_payload) is True
    assert batch_carries_script_write(harmless_payload) is False


def test_nested_batch_execute_two_levels_deep():
    bad = {
        "commands": [
            {"tool": "manage_gameobject", "params": {"action": "get"}},
            {
                "tool": "batch_execute",
                "params": {
                    "commands": [
                        {"tool": "apply_text_edits", "params": {"path": "Assets/A.cs"}},
                    ]
                },
            },
        ]
    }
    good = {
        "commands": [
            {"tool": "manage_gameobject", "params": {"action": "get"}},
            {
                "tool": "mcp__unityMCP__batch_execute",
                "params": {
                    "commands": [
                        {"tool": "manage_components", "params": {"action": "get"}},
                    ]
                },
            },
        ]
    }
    assert batch_carries_script_write(bad) is True
    assert batch_carries_script_write(good) is False


def test_nesting_nine_levels_deep_is_refused():
    node = {"commands": [{"tool": "manage_gameobject", "params": {"action": "get"}}]}
    # Wrap 8 times so the innermost batch sits at depth 8 (9 levels total).
    for _ in range(8):
        node = {"commands": [{"tool": "batch_execute", "params": node}]}
    assert batch_carries_script_write(node) is True


@pytest.mark.parametrize("bad_args", [
    "not a mapping",
    None,
    123,
    {},
    {"commands": [], "Commands": [{"tool": "manage_gameobject"}]},
    {"commands": [{"tool": "manage_gameobject"}], "Commands": [{"tool": "manage_gameobject"}]},
    {"commands": []},
    {"commands": "not a list"},
    {"commands": ["not a mapping"]},
    {"commands": [{"params": {}}]},
    {"commands": [{"tool": "manage_gameobject", "TOOL": "manage_gameobject"}]},
    {"commands": [{"tool": 123}]},
    {"commands": [{"tool": "manage_gameobject", "params": ["not a mapping"]}]},
    {"commands": [{"tool": "batch_execute"}]},
    {"commands": [{"tool": "batch_execute", "params": None}]},
])
def test_malformed_inputs_are_refused(bad_args):
    assert batch_carries_script_write(bad_args) is True


# ── Gate tests through _can_use_tool ─────────────────────────────────────────


async def test_auto_mode_denies_script_batch_without_card(tmp_path, reset_approval_mode):
    approval_mode.set_mode("auto")
    s = _mk_session(cwd=str(tmp_path), auto_approve=True, approval_timeout=2.0)
    payload = {"commands": [{"tool": "manage_script", "params": {"action": "create"}}]}
    res, ev = await _drive_gate(s, "mcp__unityMCP__batch_execute", payload, approve=True)
    assert ev is None
    assert isinstance(res, PermissionResultDeny)


async def test_auto_mode_allows_harmless_batch(tmp_path, reset_approval_mode):
    approval_mode.set_mode("auto")
    s = _mk_session(cwd=str(tmp_path), auto_approve=True, approval_timeout=2.0)
    payload = {"commands": [{"tool": "manage_gameobject", "params": {"action": "create", "name": "A"}}]}
    res, ev = await _drive_gate(s, "mcp__unityMCP__batch_execute", payload, approve=False)
    assert ev is None
    assert isinstance(res, PermissionResultAllow)


async def test_balanced_mode_denies_script_batch_without_card(tmp_path, reset_approval_mode):
    approval_mode.set_mode("balanced")
    s = _mk_session(cwd=str(tmp_path), auto_approve=False, approval_timeout=2.0)
    payload = {"commands": [{"tool": "delete_script", "params": {"path": "Assets/A.cs"}}]}
    res, ev = await _drive_gate(s, "mcp__unityMCP__batch_execute", payload, approve=True)
    assert ev is None
    assert isinstance(res, PermissionResultDeny)


async def test_read_only_session_denies_script_batch_with_batch_message(tmp_path, reset_approval_mode):
    approval_mode.set_mode("auto")
    s = _mk_session(cwd=str(tmp_path), auto_approve=True, approval_timeout=2.0, read_only=True)
    payload = {"commands": [{"tool": "create_script", "params": {"path": "Assets/A.cs"}}]}
    res, ev = await _drive_gate(s, "mcp__unityMCP__batch_execute", payload, approve=True)
    assert ev is None
    assert isinstance(res, PermissionResultDeny)
    assert "batch_execute refused" in res.message


def test_chaining_reference_and_non_identifier_tool_names_are_refused():
    b1 = {
        "commands": [
            {"tool": "manage_gameobject", "params": {"action": "create", "name": "manage_script"}},
            {
                "tool": "batch_execute",
                "params": {
                    "commands": [
                        {"tool": "$[0].data.name", "params": {"action": "create", "path": "Assets/A.cs"}},
                    ]
                },
            },
        ]
    }
    assert batch_carries_script_write(b1) is True
    assert batch_carries_script_write({"commands": [{"tool": "$[0].data.name", "params": {}}]}) is True
    assert batch_carries_script_write({"commands": [{"tool": "manage-script", "params": {}}]}) is True
    assert batch_carries_script_write({"commands": [{"tool": "manage script", "params": {}}]}) is True


def test_duplicate_folded_params_keys_are_refused():
    payload = {"commands": [{"tool": "manage_gameobject", "params": {}, "Params": {}}]}
    assert batch_carries_script_write(payload) is True


def test_script_tool_without_params_or_with_none_params_is_refused():
    assert batch_carries_script_write({"commands": [{"tool": "delete_script"}]}) is True
    assert batch_carries_script_write({"commands": [{"tool": "delete_script", "params": None}]}) is True


async def test_balanced_mode_allows_routine_batch_without_card(tmp_path, reset_approval_mode):
    approval_mode.set_mode("balanced")
    s = _mk_session(cwd=str(tmp_path), auto_approve=False, approval_timeout=2.0)
    payload = {"commands": [{"tool": "manage_gameobject", "params": {"action": "create", "name": "A"}}]}
    res, ev = await _drive_gate(s, "mcp__unityMCP__batch_execute", payload, approve=False)
    assert ev is None
    assert isinstance(res, PermissionResultAllow)

