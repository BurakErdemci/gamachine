"""Critical target and dispatch regressions, using fakes only."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from computer_use import platform, policy, server
from computer_use.platform.windows import WindowsPlatform
from computer_use.standalone.approvals import LocalApprovals


@pytest.mark.parametrize("name", ["SİL", "Send1", "btnsend", "sendmessage", "Satın  al",
                                  "GONDER", "Se\u034fnd", "Send\ufe0f", "SENDButton",
                                  "Se\u115fnd", "Se\u3164nd"])
def test_critical_name_variants(name):
    assert policy.critical_name(name)


@pytest.mark.parametrize("name", ["Silver", "Playback", "Border", "Position", "Recorder", "Pencil"])
def test_short_keywords_require_whole_tokens(name):
    assert policy.critical_name(name) == ""


@pytest.mark.parametrize("target", [
    {"element_token": "s1:1", "name": "Send"},
    {"element_token": "s1:1", "target": {"name": "Delete"}},
    {"name": ["Send"]}, {"role": "Button"}, {"nth": 0},
])
def test_every_address_is_checked(monkeypatch, target):
    monkeypatch.setattr(platform, "current", lambda: WindowsPlatform(
        SimpleNamespace(set_dpi_awareness=lambda: None)))
    assert policy.critical_reason("computer_hotkey", target, app_id=None,
                                  resolve=lambda args: (True, "Pencil"))


def test_structured_frames_and_latest_snapshot():
    handler = server.ComputerServer()
    def cache(snapshot, elements):
        handler._cache_snapshot(CallToolResult(content=[], structured_content={
            "snapshot_id": snapshot, "elements": elements,
            "tree_markdown": '- [1] Button "Pencil"'}), {"pid": 77, "window_id": 31})
    cache("s1", [
        {"element_token": "s1:1", "label": "Send", "frame": {"x": 10, "y": 10, "w": 20, "h": 20}},
        {"element_token": "s1:2", "frame": {"x": 0, "y": 0, "w": 100, "h": 100}},
    ])
    args = {"pid": 77, "window_id": 31, "x": 15, "y": 15}
    assert handler._element_name({**args, "element_token": "s1:1"}) == "Send"
    assert handler._element_at(args) == (True, "Send")
    assert handler._element_at({**args, "x": 50}) == (True, "")
    cache("s2", [])
    assert handler._element_at(args) == (True, "")
    assert handler._element_name({**args, "element_token": "s1:1"}) is None


@pytest.mark.parametrize("mode", ["halt", "dead"])
@pytest.mark.parametrize("shape", ["direct", "single", "split", "foreground"])
def test_stop_during_identity_or_fronting_prevents_input(monkeypatch, tmp_path, mode, shape):
    app = "win:c:" + chr(92) + "apps" + chr(92) + "editor.exe"
    hotkey = SimpleNamespace(registered=True, halted_at=None)
    approvals = LocalApprovals(None, hotkey, journal=lambda: tmp_path / "journal.jsonl")
    win = WindowsPlatform(SimpleNamespace(set_dpi_awareness=lambda: None, parent_pids=lambda: {}))
    identities = 0
    def stop():
        if mode == "halt":
            hotkey.halted_at = 1.0
        else:
            hotkey.registered = False
    async def identify(*args):
        nonlocal identities
        identities += 1
        if identities == (3 if shape == "split" else 2) and shape != "foreground":
            stop()
        return app, "Editor", None
    win.identify = identify
    monkeypatch.setattr(platform, "current", lambda: win)
    monkeypatch.setattr("computer_use.standalone.config.load", lambda: {"enabled": True})
    async def front(*args):
        stop()
        return None
    monkeypatch.setattr(server, "_front_guard", front)
    driver = SimpleNamespace(binary="fake", socket="fake", call=AsyncMock(return_value=CallToolResult(
        content=[], structured_content={"ok": True, "steps": [{"ok": True}]})))
    handler = server.ComputerServer(driver, approvals, runtime=AsyncMock(return_value=("fake", "fake")))
    args = {"pid": 77, "text": "hello"}
    tool = "computer_type_text"
    if shape in {"single", "split"}:
        tool = "computer_run_steps"
        args = {"pid": 77, "steps": [{"type_text": {"text": "hello"}}] * (2 if shape == "split" else 1)}
    elif shape == "foreground":
        args.update(window_id=31, delivery_mode="foreground")
    result = asyncio.run(handler.call(tool, args))
    assert result.is_error
    assert driver.call.await_count == (1 if shape == "split" else 0)


def test_dispatch_refusal_backend_has_no_io():
    assert asyncio.run(server.BackendApprovals().dispatch_refusal({})) is None


@pytest.mark.parametrize("tree_format", [None, "markdown", "elements", "both"])
def test_window_state_caches_elements_before_filtering_reply(monkeypatch, tree_format):
    win = WindowsPlatform(SimpleNamespace(set_dpi_awareness=lambda: None))
    win.identify = AsyncMock(return_value=("win:c:" + chr(92) + "editor.exe", "Editor", None))
    monkeypatch.setattr(platform, "current", lambda: win)
    report = {"snapshot_id": "s1", "elements": [{"element_token": "s1:1", "label": "Send",
               "frame": {"x": 10, "y": 10, "w": 20, "h": 20}}]}
    driver = SimpleNamespace(binary="fake", socket="fake", call=AsyncMock(return_value=CallToolResult(
        content=[TextContent(type="text", text=json.dumps(report))], structured_content=report)))
    approvals = server.BackendApprovals()
    approvals.request = AsyncMock(return_value={"approved": True})
    handler = server.ComputerServer(driver, approvals, runtime=AsyncMock(return_value=("fake", "fake")))
    args = {"pid": 77, "window_id": 31}
    if tree_format is not None:
        args["tree_format"] = tree_format
    result = asyncio.run(handler.call("computer_window_state", args))
    driver.call.assert_awaited_once_with("get_window_state", {**args, "tree_format": tree_format or "both"})
    assert not result.is_error
    expected = tree_format in {"elements", "both"}
    assert ("elements" in result.structured_content) is expected
    assert ("elements" in json.loads(result.content[0].text)) is expected
    assert handler._element_name({**args, "element_token": "s1:1"}) == "Send"
    assert handler._element_at({**args, "x": 15, "y": 15}) == (True, "Send")
    assert "elements" in report


@pytest.mark.parametrize("modifier", ["+", "ctrl", "shift"])
def test_risky_chord_cannot_be_hidden_by_an_extra_key(monkeypatch, modifier):
    monkeypatch.setattr(platform, "current", lambda: WindowsPlatform(
        SimpleNamespace(set_dpi_awareness=lambda: None)))
    assert policy.risky_reason("computer_hotkey", {"keys": ["alt", "f4", modifier]})


@pytest.mark.parametrize("alteration", ["none", "tilde", "click_only", "missing_tool", "echo", "comment",
                                       "suffix", "wrong_type", "missing_type", "disabled"])
def test_hook_registration_is_exact_and_covers_every_confirmation(tmp_path, alteration):
    from computer_use.standalone import hookcheck
    command = 'bash "$HOME/.claude/hooks/guard-computer-use-confirm.sh"'
    matcher = "mcp__gamachineComputerLocal__.*"
    hook = {"type": "command", "command": command}
    settings = {"hooks": {"PreToolUse": [{"matcher": matcher, "hooks": [hook]}]}}
    entry = settings["hooks"]["PreToolUse"][0]
    if alteration == "tilde":
        hook["command"] = "bash ~/.claude/hooks/guard-computer-use-confirm.sh"
    elif alteration == "click_only":
        entry["matcher"] = "mcp__gamachineComputerLocal__computer_click"
    elif alteration == "missing_tool":
        entry["matcher"] = "mcp__gamachineComputerLocal__computer_(click|type_text|press_key|hotkey|run_steps|set_value)"
    elif alteration == "echo":
        hook["command"] = "echo " + command
    elif alteration == "comment":
        hook["command"] = "# " + command
    elif alteration == "suffix":
        hook["command"] += " || true"
    elif alteration == "wrong_type":
        hook["type"] = "http"
    elif alteration == "missing_type":
        hook.pop("type")
    elif alteration == "disabled":
        settings["disableAllHooks"] = True
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    assert hookcheck.confirm_hook_installed([path]) is (alteration in {"none", "tilde"})
