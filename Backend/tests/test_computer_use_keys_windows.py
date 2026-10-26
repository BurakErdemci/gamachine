"""Windows key aliases and refusal rules tested without OS calls."""
from types import SimpleNamespace

import pytest

from computer_use import platform, policy
from computer_use.platform.windows import WindowsPlatform


@pytest.fixture
def win():
    return WindowsPlatform(win32=SimpleNamespace(set_dpi_awareness=lambda: None))


@pytest.mark.parametrize("tool, args", [
    ("hotkey", {"keys": ["ctrl", "s"]}),
    ("hotkey", {"keys": ["win", "windows", "lwin", "rwin"]}),
    ("hotkey", {"keys": "cmd+s"}),
    ("press_key", {"key": "backspace", "modifiers": ["ctrl"]}),
    ("press_key", {"key": "windows", "modifiers": ["win"]}),
    ("press_key", {"key": 3, "modifiers": "cmd"}),
    ("type_text", {"keys": ["cmd"]}), ("hotkey", {}),
])
def test_normalize_preserves_identity_when_unchanged(win, tool, args):
    assert win.normalize_keys(tool, args) is args


@pytest.mark.parametrize("alias, mapped", [
    ("cmd", "ctrl"), ("COMMAND", "ctrl"), ("Meta", "ctrl"), ("super", "ctrl"),
    ("opt", "alt"), ("OPTION", "alt"), ("Return", "enter"),
])
def test_hotkey_aliases_copy_only_when_changed(win, alias, mapped):
    untouched = {"nested": True}
    args = {"keys": [alias, "s", 7], "other": untouched}
    result = win.normalize_keys("hotkey", args)
    assert result is not args and result["keys"] is not args["keys"]
    assert result["keys"] == [mapped, "s", 7]
    assert args["keys"] == [alias, "s", 7]
    assert result["other"] is untouched


def test_press_key_aliases_copy_and_keep_input(win):
    args = {"key": "RETURN", "modifiers": ["Cmd", "Option"], "pid": 41}
    result = win.normalize_keys("press_key", args)
    assert result == {"key": "enter", "modifiers": ["ctrl", "alt"], "pid": 41}
    assert result is not args and result["modifiers"] is not args["modifiers"]
    assert args == {"key": "RETURN", "modifiers": ["Cmd", "Option"], "pid": 41}


def test_press_key_changed_key_copies_unchanged_modifier_list(win):
    args = {"key": "return", "modifiers": ["shift"]}
    result = win.normalize_keys("press_key", args)
    assert result == {"key": "enter", "modifiers": ["shift"]}
    assert result["modifiers"] is not args["modifiers"]


@pytest.mark.parametrize("keys, normalized", [
    (["win", "r"], "r+win"), (["windows", "r"], "r+win"),
    (["lwin", "l"], "l+win"), (["rwin", "x"], "win+x"),
    (["CTRL", "ALT", "DELETE"], "alt+ctrl+delete"),
    (["control", "option", "del"], "alt+ctrl+delete"),
    (["ctrl", "alt", "end"], "alt+ctrl+end"),
    (["ctrl", "shift", "esc"], "ctrl+esc+shift"),
    (["command", "shift", "escape"], "ctrl+esc+shift"),
    (["alt", "tab"], "alt+tab"), (["opt", "escape"], "alt+esc"),
    (["ctrl", "esc"], "ctrl+esc"), (["control", "escape"], "ctrl+esc"),
    (["ctrl", "alt", "esc"], "alt+ctrl+esc"),
    (["ctrl", "shift", "escape", "x"], "ctrl+esc+shift+x"),
])
def test_refused_chords_and_aliases(win, keys, normalized):
    assert win.refused_chord(frozenset(keys)) == "Bu kısayol Windows'ta gönderilmez: " + normalized


@pytest.mark.parametrize("keys", [["ctrl", "s"], ["cmd", "s"], ["alt", "f4"], ["ctrl", "w"], ["delete"]])
def test_ordinary_chords_not_refused(win, keys):
    assert win.refused_chord(frozenset(keys)) is None


@pytest.mark.parametrize("keys, reason", [
    (["alt", "f4"], "Uygulamadan çıkılsın mı? Kaydedilmemiş iş kaybolabilir."),
    (["OPTION", "F4"], "Uygulamadan çıkılsın mı? Kaydedilmemiş iş kaybolabilir."),
    (["shift", "delete"], "Riskli klavye kısayolu: delete+shift"),
    (["ctrl", "shift", "delete"], "Riskli klavye kısayolu: ctrl+delete+shift"),
    (["command", "shift", "del"], "Riskli klavye kısayolu: ctrl+delete+shift"),
    (["ctrl", "w"], ""), (["ctrl", "f4"], ""), (["ctrl", "s"], ""),
    (["ctrl", "alt", "f4"], ""),
])
def test_risky_chords(win, keys, reason):
    assert win.risky_chord(frozenset(keys)) == reason


@pytest.mark.parametrize("tool, args", [
    ("computer_hotkey", {"pid": 41, "keys": ["windows", "r"]}),
    ("computer_press_key", {"pid": 41, "key": "escape", "modifiers": ["control"]}),
    ("computer_run_steps", {"pid": 41, "steps": [
        {"type_text": {"text": "before"}}, {"hotkey": {"keys": ["windows", "r"]}},
        {"type_text": {"text": "after"}},
    ]}),
    ("computer_run_steps", {"pid": 41, "steps": [
        {"tool": "hotkey", "args": {"keys": ["ctrl", "alt", "end"]}},
    ]}),
])
def test_policy_refuses_single_and_batch_chords(win, monkeypatch, tool, args):
    monkeypatch.setattr(platform, "current", lambda: win)
    result = policy.validate_call(tool, args)
    assert isinstance(result, policy.Refusal)
    assert result.reason.startswith("Bu kısayol Windows'ta gönderilmez: ")


def test_policy_maps_aliases_inside_batches(win, monkeypatch):
    monkeypatch.setattr(platform, "current", lambda: win)
    args = {"pid": 41, "steps": [{"hotkey": {"keys": ["cmd", "s"]}}]}
    result = policy.validate_call("computer_run_steps", args)
    assert isinstance(result, policy.Validated)
    assert result.args["steps"][0]["hotkey"]["keys"] == ["ctrl", "s"]
    assert args["steps"][0]["hotkey"]["keys"] == ["cmd", "s"]
