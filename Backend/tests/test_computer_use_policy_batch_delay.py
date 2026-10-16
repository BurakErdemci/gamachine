"""The driver's batch delay limit does not constrain character typing delays."""
import pytest

from computer_use import policy


@pytest.mark.parametrize("delay", [0, 2000])
def test_batch_delay_boundaries(delay):
    args = {"pid": 42, "steps": [{"type_text": {"text": "hello"}}], "delay_ms": delay}
    assert isinstance(policy.validate_call("computer_run_steps", args), policy.Validated)


@pytest.mark.parametrize("delay", [-1, 2001, 1.5, "500", True])
def test_invalid_batch_delay(delay):
    args = {"pid": 42, "steps": [{"type_text": {"text": "hello"}}], "delay_ms": delay}
    refusal = policy.validate_call("computer_run_steps", args)
    assert isinstance(refusal, policy.Refusal)
    assert refusal.reason == "delay_ms must be an integer from 0 to 2000"


def test_character_delay_keeps_existing_rules():
    args = {"pid": 42, "text": "hello", "delay_ms": 5000}
    assert isinstance(policy.validate_call("computer_type_text", args), policy.Validated)
    batch = {"pid": 42, "steps": [{"type_text": args}], "delay_ms": 2000}
    assert isinstance(policy.validate_call("computer_run_steps", batch), policy.Validated)
