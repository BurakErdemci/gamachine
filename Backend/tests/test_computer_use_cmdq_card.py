import pytest

from computer_use.policy import risky_reason


QUIT_TEXT = "Uygulamadan çıkılsın mı? Kaydedilmemiş iş kaybolabilir."


@pytest.mark.parametrize("tool,args", [
    ("computer_hotkey", {"keys": ["cmd", "q"]}),
    ("computer_press_key", {"key": "q", "modifiers": ["cmd"]}),
    ("computer_hotkey", {"keys": ["Command", "Q"]}),
    ("computer_press_key", {"key": "Q", "modifiers": ["Command"]}),
])
def test_cmdq_returns_quit_card_text(tool, args):
    assert risky_reason(tool, args) == QUIT_TEXT


@pytest.mark.parametrize("step", [
    {"hotkey": {"keys": ["cmd", "q"]}},
    {"tool": "hotkey", "args": {"keys": ["Command", "Q"]}},
    {"press_key": {"key": "q", "modifiers": ["cmd"]}},
    {"tool": "press_key", "args": {"key": "Q", "modifiers": ["Command"]}},
])
def test_batch_cmdq_returns_quit_card_text(step):
    steps = [{"hotkey": {"keys": ["cmd", "s"]}}, step]
    assert risky_reason("computer_run_steps", {"steps": steps}) == QUIT_TEXT


def test_cmd_shift_q_keeps_existing_risky_reason():
    keys = ["cmd", "shift", "q"]
    expected = "Riskli klavye kısayolu: " + "+".join(sorted(keys))
    assert risky_reason("computer_hotkey", {"keys": keys}) == expected


@pytest.mark.parametrize("key", ["w", "s"])
def test_harmless_shortcuts_have_no_risky_reason(key):
    assert risky_reason("computer_hotkey", {"keys": ["cmd", key]}) == ""
