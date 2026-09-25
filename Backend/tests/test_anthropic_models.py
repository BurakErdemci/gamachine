import asyncio
import re
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from providers.api_providers import AnthropicProvider
from routes.config_routes import create_config_router


@patch("providers.api_providers.anthropic.Anthropic")
def test_opus_5_and_4_8_keep_distinct_api_model_ids(anthropic_client):
    anthropic_client.return_value = MagicMock()

    assert AnthropicProvider("test-key", "claude-opus-5").model_name == "claude-opus-5"
    # "opus-5-5" also contains "opus-5": the 5.5 branch must win, not collapse to Opus 5.
    assert AnthropicProvider("test-key", "claude-opus-5-5").model_name == "claude-opus-5-5"
    assert AnthropicProvider("test-key", "claude-opus-4-8").model_name == "claude-opus-4-8"


# Owner decision 25 Sep 2026: on the API path every Opus generation is a
# separate choice. Ids and families per the claude-api skill model table.
_OPUS_API = [
    # id, thinking param, effort levels offered, agent-loop max_tokens
    ("claude-opus-5-5", {"type": "adaptive", "display": "summarized"},
     ["auto", "low", "medium", "high", "xhigh", "max"], 16000),
    ("claude-opus-5", {"type": "adaptive", "display": "summarized"},
     ["auto", "low", "medium", "high", "xhigh", "max"], 16000),
    ("claude-opus-4-8", {"type": "adaptive", "display": "summarized"},
     ["auto", "low", "medium", "high", "xhigh", "max"], 4096),
]


@pytest.mark.parametrize("model_id, thinking, effort_levels, loop_max_tokens", _OPUS_API)
@patch("providers.api_providers.anthropic.Anthropic")
def test_each_opus_generation_reaches_the_api_as_itself(
        anthropic_client, model_id, thinking, effort_levels, loop_max_tokens):
    from agentic.agent_runner import _anthropic_loop_max_tokens
    from providers.api_providers import anthropic_thinking_param
    from providers.effort_caps import get_effort_caps

    anthropic_client.return_value = MagicMock()

    sent = AnthropicProvider("test-key", model_id).model_name
    assert sent == model_id
    assert anthropic_thinking_param(sent) == thinking
    assert get_effort_caps("anthropic", sent)["levels"] == effort_levels
    assert _anthropic_loop_max_tokens(sent) == loop_max_tokens


@pytest.mark.parametrize("choice, expected", [
    # OpenRouter's spelling, which the keyless fallback list used to offer.
    ("claude-opus-5.5", "claude-opus-5-5"),
    ("claude-opus-4.8", "claude-opus-4-8"),
    ("claude-opus-5.5:batch", "claude-opus-5-5"),
    # Older generations the account may list are not rewritten to 4.8.
    ("claude-opus-4-7", "claude-opus-4-7"),
    ("claude-opus-4-6", "claude-opus-4-6"),
    ("claude-opus-4-5-20251101", "claude-opus-4-5-20251101"),
    # A free-form name still resolves.
    ("opus", "claude-opus-4-8"),
    ("Opus", "claude-opus-4-8"),
])
@patch("providers.api_providers.anthropic.Anthropic")
def test_opus_choices_are_never_collapsed_into_another_generation(anthropic_client, choice, expected):
    anthropic_client.return_value = MagicMock()

    assert AnthropicProvider("test-key", choice).model_name == expected


def test_opus_5_is_selectable_on_the_claude_code_side():
    """Abonelik (CLI) listesi hâlâ elle yazılı ve Opus 5 orada olmalı.

    Bu testin bulut yarısı 30 Ağu 2026'da KALDIRILDI: elle yazılı bulut
    kataloğu silindi ve liste artık sağlayıcının kendi `/v1/models`inden
    geliyor. "Katalogda şu model yazıyor" diye bir iddia artık ölçülebilir
    bir şey söylemiyor — o sözleşmenin yerini
    `test_available_models_merge.py` aldı.

    CLI tarafında listeleme yolu YOK (Claude Code'un `--help`inde model
    listeleyen alt komut yok, ölçüldü 30 Ağu 2026), o yüzden orası elle
    yazılı kalıyor ve bu test hâlâ bir şey koruyor.
    """
    router = create_config_router(MagicMock())
    route = next(route for route in router.routes if route.path == "/available-models")
    catalog = asyncio.run(route.endpoint())

    subscription = {model["id"]: model for model in catalog["subscription"]}

    assert "claude-opus-5" in subscription
    assert "claude-opus-4-8" in subscription
    assert subscription["claude-opus-5"]["provider"] == "subscription"
    assert subscription["claude-opus-5-5"]["name"] == "Claude Opus 5.5 (CLI)"


def test_gpt_6_sol_and_luna_are_offered_on_the_codex_side():
    router = create_config_router(MagicMock())
    route = next(route for route in router.routes if route.path == "/available-models")
    catalog = asyncio.run(route.endpoint())
    subscription = {model["id"]: model for model in catalog["subscription"]}

    assert subscription["gpt-6-sol"]["name"] == "Codex (GPT-6 Sol)"
    assert subscription["gpt-6-luna"]["name"] == "Codex (GPT-6 Luna)"
    assert subscription["gpt-6-astra"]["name"] == "Codex (GPT-6 Astra)"


@patch("providers.api_providers.anthropic.Anthropic")
def test_fable_5_1_and_fable_5_keep_distinct_api_model_ids(anthropic_client):
    """Measured 3 Sep 2026: `claude --model claude-fable-5-1 -p ...` answers with
    modelUsage `claude-fable-5-1`, and `claude-fable-5` still answers as itself —
    two models, not an alias, so the generic `"fable"` branch must not swallow
    the 5.1 id."""
    anthropic_client.return_value = MagicMock()

    assert AnthropicProvider("test-key", "claude-fable-5-1").model_name == "claude-fable-5-1"
    assert AnthropicProvider("test-key", "claude-fable-5").model_name == "claude-fable-5"


def test_fable_5_1_is_selectable_on_the_claude_code_side():
    router = create_config_router(MagicMock())
    route = next(route for route in router.routes if route.path == "/available-models")
    catalog = asyncio.run(route.endpoint())
    subscription = {model["id"]: model for model in catalog["subscription"]}

    assert "claude-fable-5-1" in subscription
    assert "claude-fable-5" in subscription
    assert subscription["claude-fable-5-1"]["name"] == "Claude Fable 5.1 (CLI)"


# Valid ids per the claude-api skill model table (Sep 2026). The provider used to
# emit "claude-4-6-sonnet" / "claude-4-5-haiku", which are not API ids, so every
# call on a Sonnet 4.6 or Haiku choice 404'd.
_VALID_ANTHROPIC_IDS = {
    "claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5",
    "claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-4-5",
}


@pytest.mark.parametrize("choice, expected", [
    ("claude-sonnet-4-6", "claude-sonnet-4-6"),
    ("claude-sonnet-5", "claude-sonnet-5"),
    # A Sonnet without a version is the current generation (skill: "sonnet" -> claude-sonnet-5).
    ("sonnet", "claude-sonnet-5"),
    ("claude-sonnet-4-5-20250929", "claude-sonnet-5"),
    ("haiku", "claude-haiku-4-5"),
    ("claude-haiku-4-5", "claude-haiku-4-5"),
    ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
    # Names stored while the provider emitted the invalid ids still resolve.
    ("claude-4-6-sonnet", "claude-sonnet-4-6"),
    ("claude-4-5-haiku", "claude-haiku-4-5"),
    (None, "claude-sonnet-4-6"),
    ("", "claude-sonnet-4-6"),
])
@patch("providers.api_providers.anthropic.Anthropic")
def test_sonnet_and_haiku_choices_map_to_valid_api_ids(anthropic_client, choice, expected):
    anthropic_client.return_value = MagicMock()

    model_name = AnthropicProvider("test-key", choice).model_name

    assert model_name == expected
    assert model_name in _VALID_ANTHROPIC_IDS


@pytest.mark.parametrize("choice", [
    "sonnet", "claude-sonnet-4-6", "haiku", "claude-4-6-sonnet", "claude-4-5-haiku",
    "opus", "fable", "claude-opus-5-5", None,
])
@patch("providers.api_providers.anthropic.Anthropic")
def test_provider_never_emits_a_version_first_id(anthropic_client, choice):
    anthropic_client.return_value = MagicMock()

    assert not re.match(r"claude-\d", AnthropicProvider("test-key", choice).model_name)
