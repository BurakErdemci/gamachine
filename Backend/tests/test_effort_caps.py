import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from providers import effort_caps, model_catalog
from providers.effort_caps import CANON_ORDER, EFFORT_LEVELS, get_effort_caps, map_effort


def test_auto_always_first_and_empty_mapping():
    for p, m in [("subscription", "claude-opus-5"), ("subscription", "claude-opus-4-8"), ("openai", "gpt-5.5"),
                 ("nvidia", "z-ai/glm-5.2"), ("google", "gemini-3.5-flash"),
                 ("subscription", "cursor-composer")]:
        caps = get_effort_caps(p, m)
        assert caps["levels"][0] == "auto"
        assert map_effort(p, m, "auto") == {}


def test_claude_model_gating():
    assert "xhigh" in get_effort_caps("subscription", "claude-fable-5")["levels"]
    assert "xhigh" not in get_effort_caps("subscription", "claude-sonnet-4-6")["levels"]
    assert get_effort_caps("subscription", "claude-haiku-4-5")["levels"] == ["auto"]
    assert map_effort("subscription", "claude-opus-5", "max") == {"sdk_effort": "max"}
    assert map_effort("subscription", "claude-opus-4-8", "max") == {"sdk_effort": "max"}
    # Desteklenmeyen seviye → sessizce auto (haiku'ya max istenirse hiçbir şey gitmez)
    assert map_effort("subscription", "claude-haiku-4-5", "max") == {}


def test_codex_max_only_on_56():
    assert "max" in get_effort_caps("subscription", "gpt-5.6-sol")["levels"]
    assert "max" not in get_effort_caps("subscription", "gpt-5.5")["levels"]
    assert map_effort("subscription", "gpt-5.6-terra", "xhigh") == {
        "cli_config": {"model_reasoning_effort": "xhigh"}}


def test_gemini_level_vs_budget_mutually_exclusive():
    r3 = map_effort("google", "gemini-3.5-flash", "high")
    assert r3 == {"gemini_thinking_level": "high"}
    r25 = map_effort("google", "gemini-2.5-flash", "high")
    assert r25 == {"gemini_thinking_budget": -1}
    assert "gemini_thinking_level" not in r25 and "gemini_thinking_budget" not in r3


def test_nvidia_glm_toggle_and_nemotron_low():
    assert map_effort("nvidia", "z-ai/glm-5.2", "off") == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    low = map_effort("nvidia", "nvidia/nemotron-3-super-120b-a12b", "low")
    assert low["extra_body"]["chat_template_kwargs"] == {"enable_thinking": True, "low_effort": True}


def test_anthropic_extra_body_and_copilot_flags():
    assert map_effort("anthropic", "claude-opus-5", "xhigh") == {
        "anthropic_extra_body": {"output_config": {"effort": "xhigh"}}}
    assert map_effort("anthropic", "claude-opus-4-8", "xhigh") == {
        "anthropic_extra_body": {"output_config": {"effort": "xhigh"}}}
    assert map_effort("subscription", "copilot-claude-sonnet-4.6", "max") == {
        "cli_flags": ["--effort", "max"]}
    # copilot gpt modelinde max listede yok → boş
    assert map_effort("subscription", "copilot-gpt-5.2", "max") == {}


def test_gpt6_drops_none_and_minimal_but_gains_xhigh_and_max():
    api = get_effort_caps("openai", "gpt-6-astra")["levels"]
    assert "none" not in api and "minimal" not in api
    assert api == ["auto", "low", "medium", "high", "xhigh", "max"]
    assert map_effort("openai", "gpt-6-astra", "max") == {
        "request_params": {"reasoning_effort": "max"}}
    # `none` API'den kalktı → istense bile parametre gitmez
    assert map_effort("openai", "gpt-6-astra", "none") == {}
    cli = get_effort_caps("subscription", "gpt-6-astra")["levels"]
    assert "minimal" not in cli and "max" in cli and "xhigh" in cli
    assert map_effort("subscription", "gpt-6-astra", "xhigh") == {
        "cli_config": {"model_reasoning_effort": "xhigh"}}


def test_new_models_share_their_siblings_effort_levels():
    # Codex's model cache lists low..max for both (24 Sep 2026); Opus 5.5 takes low..max.
    for p in ("subscription", "openai"):
        astra = get_effort_caps(p, "gpt-6-astra")["levels"]
        assert get_effort_caps(p, "gpt-6-sol")["levels"] == astra
        assert get_effort_caps(p, "gpt-6.1-sol")["levels"] == astra
        assert get_effort_caps(p, "gpt-6-luna")["levels"] == astra
    for p in ("subscription", "anthropic"):
        assert get_effort_caps(p, "claude-opus-5-5")["levels"] == get_effort_caps(p, "claude-opus-5")["levels"]
        assert get_effort_caps(p, "claude-sonnet-5-5")["levels"] == get_effort_caps(p, "claude-sonnet-5")["levels"]


def test_gemini_37_and_38_have_no_minimal_and_use_thinking_level():
    for m in ("gemini-3.7-flash", "gemini-3.8-flash"):
        caps = get_effort_caps("google", m)
        assert caps["levels"] == ["auto", "low", "medium", "high"], m
        assert map_effort("google", m, "high") == {"gemini_thinking_level": "high"}, m
        # minimal listede yok → hiçbir parametre gönderilmez (bütçe de değil)
        assert map_effort("google", m, "minimal") == {}, m
    # 3.6 minimal'i KAYBETMEDİ — daralma yalnız yeni ikiliye ait
    assert "minimal" in get_effort_caps("google", "gemini-3.6-flash")["levels"]


# Per-family output_config.effort support, claude-api skill "Thinking & Effort"
# table (Sep 2026). Before this table the gate only excluded ids containing
# "haiku"/"4-5", so Opus 4.1 / Sonnet 4.0 / 3.x were offered (and sent) effort.
_FULL = ["auto", "low", "medium", "high", "xhigh", "max"]



@pytest.mark.parametrize("model, levels", [
    ("claude-fable-5-1", _FULL),
    ("claude-fable-5", _FULL),
    ("claude-mythos-5-1", _FULL),
    ("claude-opus-5-5", _FULL),
    ("claude-opus-5", _FULL),
    ("claude-opus-4-8", _FULL),
    ("claude-opus-4-7", _FULL),
    ("claude-sonnet-5-5", _FULL),
    ("claude-sonnet-5", _FULL),
    ("claude-opus-4-6", ["auto", "low", "medium", "high", "max"]),
    ("claude-sonnet-4-6", ["auto", "low", "medium", "high", "max"]),
    ("claude-4-6-sonnet", ["auto", "low", "medium", "high", "max"]),
    ("claude-opus-4-5-20251101", ["auto", "low", "medium", "high"]),
    ("claude-sonnet-4-5-20250929", ["auto"]),
    ("claude-haiku-4-5-20251001", ["auto"]),
    ("claude-opus-4-1-20250805", ["auto"]),
    ("claude-opus-4-20250514", ["auto"]),
    ("claude-sonnet-4-20250514", ["auto"]),
    ("claude-3-7-sonnet-20250219", ["auto"]),
    ("claude-3-haiku-20240307", ["auto"]),
    ("claude-some-future-model", _FULL),
])
def test_anthropic_api_effort_follows_documented_support(model, levels):
    assert get_effort_caps("anthropic", model)["levels"] == levels


@pytest.mark.parametrize("model", [
    "claude-opus-4-1-20250805", "claude-sonnet-4-20250514", "claude-3-7-sonnet-20250219",
    "claude-sonnet-4-5-20250929", "claude-haiku-4-5",
])
def test_unsupported_anthropic_models_are_never_sent_effort(model):
    for level in ("low", "medium", "high", "xhigh", "max"):
        assert map_effort("anthropic", model, level) == {}, (model, level)


def test_effort_levels_beyond_a_family_are_not_sent():
    assert map_effort("anthropic", "claude-opus-4-5", "xhigh") == {}
    assert map_effort("anthropic", "claude-opus-4-5", "high") == {
        "anthropic_extra_body": {"output_config": {"effort": "high"}}}
    assert map_effort("anthropic", "claude-sonnet-4-6", "xhigh") == {}
    assert map_effort("anthropic", "claude-sonnet-4-6", "max") == {
        "anthropic_extra_body": {"output_config": {"effort": "max"}}}


# One id per branch of get_effort_caps / _anthropic_api_effort_levels, tried with
# every provider so a branch is reached whichever provider the table routes it to.
_MODEL_TABLE = [
    "", "unknown-model",
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5",
    "claude-sonnet-5-5", "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-haiku-4-5", "claude-3-7-sonnet-20250219",
    "claude-fable-5", "claude-mythos-5",
    "gpt-5.5", "gpt-5.2", "gpt-5.1-codex-max", "gpt-5.6-sol", "gpt-6-sol", "gpt-oss-120b",
    "copilot-auto", "copilot-gpt-5.5", "copilot-claude-sonnet-5", "opencode:model", "cursor-auto", "kimi-k3",
    "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-pro-preview", "gemini-3-pro", "gemini-2.5-flash",
    "agy-claude-sonnet-5-5", "nvidia/nemotron-x", "z-ai/glm-5.2", "qwen-3", "kimi-k2", "deepseek-v4", "mistral-large",
]


def test_the_registry_only_returns_levels_the_bridge_and_the_phone_know():
    """The bridge validates a renderer's report and a phone's set_effort against
    EFFORT_LEVELS. A level the registry could return but that is not in it makes
    the report refused (`bad_levels`) and the phone stale: this once happened with
    `none` on OpenAI API models, which CANON_ORDER lacks."""
    providers = [*model_catalog.supported_providers(), "subscription", "ollama", "", "unknown-provider"]
    seen = set()
    for provider in providers:
        for model in _MODEL_TABLE:
            caps = get_effort_caps(provider, model)
            assert caps["levels"][0] == "auto", (provider, model)
            assert len(set(caps["levels"])) == len(caps["levels"]), (provider, model)
            unknown = [v for v in caps["levels"] if v not in EFFORT_LEVELS]
            assert not unknown, f"{provider}/{model} offers {unknown}, not in EFFORT_LEVELS"
            seen.update(caps["levels"])
    assert seen == set(EFFORT_LEVELS), "a level nothing offers, or the sweep missed a branch"


def test_every_literal_level_list_in_the_registry_is_in_effort_levels():
    """Independent of the model table above: the level lists written into the source."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(effort_caps))
    stray = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_caps" and node.args:
            first = node.args[0]
            if isinstance(first, ast.List):
                stray += [e.value for e in first.elts
                          if isinstance(e, ast.Constant) and e.value not in EFFORT_LEVELS]
    assert not stray


def test_effort_levels_extend_the_canonical_scale_without_reordering_it():
    assert [v for v in EFFORT_LEVELS if v in CANON_ORDER] == CANON_ORDER
    assert set(EFFORT_LEVELS) - set(CANON_ORDER) == {"none"}
