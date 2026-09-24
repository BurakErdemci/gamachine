"""Pins the `thinking` payload sent to the Anthropic Messages API per model family.

budget_tokens returns HTTP 400 on Opus 4.7+, Opus 5.x, Sonnet 5 and Fable/Mythos;
those take adaptive thinking. Pre-4.6 models still need budget_tokens.
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from providers.api_providers import (  # noqa: E402
    ANTHROPIC_THINKING_BUDGET,
    ANTHROPIC_THINKING_HEADROOM,
    AnthropicProvider,
    anthropic_thinking_param,
)

ADAPTIVE_SUMMARIZED = {"type": "adaptive", "display": "summarized"}
BUDGET = {"type": "enabled", "budget_tokens": ANTHROPIC_THINKING_BUDGET}


@pytest.mark.parametrize("model", [
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-mythos-5-1",
    "anthropic.claude-opus-4-8",
    "claude-some-future-model",
])
def test_adaptive_only_families_never_get_budget_tokens(model):
    assert anthropic_thinking_param(model) == ADAPTIVE_SUMMARIZED


@pytest.mark.parametrize("model", [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-4-6-sonnet",  # the id AnthropicProvider maps plain "sonnet" to
])
def test_4_6_family_uses_adaptive_without_display(model):
    assert anthropic_thinking_param(model) == {"type": "adaptive"}


@pytest.mark.parametrize("model", [
    "claude-haiku-4-5",
    "claude-4-5-haiku",
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-5",
    "claude-opus-4-1-20250805",
    "claude-opus-4-20250514",
    "claude-3-7-sonnet-20250219",
])
def test_pre_4_6_models_keep_budget_tokens(model):
    assert anthropic_thinking_param(model) == BUDGET


@pytest.mark.parametrize("model", ["claude-3-5-haiku-20241022", "claude-3-haiku-20240307"])
def test_models_without_extended_thinking_get_none(model):
    assert anthropic_thinking_param(model) is None


def _provider(model_name):
    with patch("providers.api_providers.anthropic.Anthropic") as client_cls:
        client = MagicMock()
        client_cls.return_value = client
        provider = AnthropicProvider("test-key", model_name)
    client.messages.create.return_value = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking="reasoning summary"),
        SimpleNamespace(type="text", text="answer"),
    ])
    return provider, client


@pytest.mark.parametrize("model_name, expected", [
    ("claude-opus-5-5", ADAPTIVE_SUMMARIZED),
    ("claude-opus-5", ADAPTIVE_SUMMARIZED),
    ("claude-opus-4-8", ADAPTIVE_SUMMARIZED),
    ("claude-sonnet-5", ADAPTIVE_SUMMARIZED),
    ("claude-fable-5-1", ADAPTIVE_SUMMARIZED),
    ("claude-sonnet-4-6", {"type": "adaptive"}),
    ("claude-haiku-4-5", BUDGET),
])
def test_analyze_code_with_thinking_request_payload(model_name, expected):
    provider, client = _provider(model_name)

    text, thinking, _ = provider.analyze_code_with_thinking("prompt", max_tokens=4096)

    assert client.messages.create.call_count == 1
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["thinking"] == expected
    assert kwargs["max_tokens"] == 4096 + ANTHROPIC_THINKING_HEADROOM
    for rejected in ("temperature", "top_p", "top_k", "tool_choice"):
        assert rejected not in kwargs
    assert (text, thinking) == ("answer", "reasoning summary")


def test_budget_stays_below_max_tokens():
    provider, client = _provider("claude-haiku-4-5")
    provider.analyze_code_with_thinking("prompt", max_tokens=1)
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["thinking"]["budget_tokens"] < kwargs["max_tokens"]
