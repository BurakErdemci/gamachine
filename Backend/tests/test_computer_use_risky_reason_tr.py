import pytest

from computer_use.policy import risky_reason


def test_risky_hotkey_reason_is_turkish():
    assert risky_reason("computer_hotkey", {"keys": ["cmd", "delete"]}) == "Riskli klavye kısayolu: cmd+delete"


@pytest.mark.parametrize("step", [
    {"hotkey": {"keys": ["cmd", "delete"]}},
    {"tool": "hotkey", "args": {"keys": ["cmd", "delete"]}},
])
def test_batch_risky_hotkey_reason_is_turkish(step):
    assert risky_reason("computer_run_steps", {"steps": [step]}) == "Riskli klavye kısayolu: cmd+delete"


def test_harmless_hotkey_has_no_risky_reason():
    assert risky_reason("computer_hotkey", {"keys": ["cmd", "s"]}) == ""
