"""get_provider picks the subscription provider class by the runner's own rule
(chat_model.subscription_family), so a mixed-case id cannot land on another CLI."""
import pytest

from agentic.chat_model import subscription_family
from providers import families
from providers.manager import AIProviderManager

IDS = [
    "cursor-auto", "copilot-gpt-5", "opencode:opencode/big-pickle",
    "kimi-k3", "gpt-6-luna", "gemini-3-pro", "agy-flash", "claude-opus-4-8",
]


FAMILY_CLASS = {
    "cursor": "CursorProvider", "copilot": "CopilotProvider",
    "opencode": "OpenCodeProvider", "codex": "CodexProvider",
    "kimi": "KimiProvider", "agy": "AgyProvider", "claude": "ClaudeCodeProvider",
}


def _variants(model_id):
    return [model_id, model_id.upper(), model_id.title()]


def test_family_reexported_from_chat_model():
    assert subscription_family is families.subscription_family


@pytest.mark.parametrize("model_id", IDS)
def test_mixed_case_ids_get_the_runners_family(model_id):
    for variant in _variants(model_id):
        provider = AIProviderManager.get_provider(
            {"provider_type": "subscription", "model_name": variant})
        expected = FAMILY_CLASS[subscription_family(variant)]
        assert type(provider).__name__ == expected, variant
        assert type(provider) is type(AIProviderManager.get_provider(
            {"provider_type": "subscription", "model_name": model_id.lower()}))
        assert provider.binary_name == variant


def test_default_subscription_model_is_claude():
    provider = AIProviderManager.get_provider({"provider_type": "subscription"})
    assert type(provider).__name__ == "ClaudeCodeProvider"
    assert provider.binary_name == "claude"
