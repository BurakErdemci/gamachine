from typing import Dict, Any
from .api_providers import GeminiProvider, OllamaProvider, OpenAICompatibleProvider, AnthropicProvider, DEFAULT_GROQ_MODEL
from .claude_provider import ClaudeCodeProvider
from .codex_provider import CodexProvider
from .agy_provider import AgyProvider
from .cursor_provider import CursorProvider
from .copilot_provider import CopilotProvider
from .opencode_provider import OpenCodeProvider
from .kimi_provider import KimiProvider
from .cli_base import BaseCLIProvider as CLIProvider
from .base import AIProvider
from .families import subscription_family


def subscription_provider_class(model_name: str):
    """Looked up at call time so a patched provider name is honoured."""
    return {
        "cursor": CursorProvider,
        "copilot": CopilotProvider,
        "opencode": OpenCodeProvider,
        "codex": CodexProvider,
        "kimi": KimiProvider,
        "agy": AgyProvider,
        "claude": ClaudeCodeProvider,
    }[subscription_family(model_name)]


class AIProviderManager:
    @staticmethod
    def get_provider(config: Dict[str, Any]) -> AIProvider:
        p_type = config.get("provider_type", "")
        m_name = config.get("model_name")
        api_key = config.get("api_key", "")

        if p_type == "anthropic" and api_key:
            return AnthropicProvider(api_key=api_key, model_name=m_name)
        elif p_type == "google" and api_key:
            return GeminiProvider(api_key=api_key, model_name=m_name)
        elif p_type == "openai" and api_key:
            return OpenAICompatibleProvider(api_key=api_key, base_url="https://api.openai.com/v1", model_name=m_name or "gpt-5.5")
        elif p_type == "deepseek" and api_key:
            return OpenAICompatibleProvider(api_key=api_key, base_url="https://api.deepseek.com", model_name=m_name or "deepseek-v4-pro")
        elif p_type == "groq" and api_key:
            return OpenAICompatibleProvider(api_key=api_key, base_url="https://api.groq.com/openai/v1", model_name=m_name or "llama-3.3-70b-versatile")
        elif p_type == "openrouter" and api_key:
            return OpenAICompatibleProvider(api_key=api_key, base_url="https://openrouter.ai/api/v1", model_name=m_name or "openai/gpt-5.5")
        elif p_type == "moonshot" and api_key:
            # Uluslararası endpoint (.ai) — K3 dahil; .cn Çin bölgesine özel
            return OpenAICompatibleProvider(api_key=api_key, base_url="https://api.moonshot.ai/v1", model_name=m_name or "kimi-k3")
        elif p_type == "z-ai" and api_key:
            return OpenAICompatibleProvider(api_key=api_key, base_url="https://api.z.ai/api/paas/v4", model_name=m_name or "glm-5.2")
        elif p_type == "nvidia" and api_key:
            # NVIDIA NIM (build.nvidia.com) — tek nvapi- key ile 100+ ücretsiz model,
            # OpenAI-uyumlu endpoint (~40 RPM free tier)
            return OpenAICompatibleProvider(api_key=api_key, base_url="https://integrate.api.nvidia.com/v1", model_name=m_name or "z-ai/glm-5.2")
        elif p_type == "subscription":
            # m_name burada binary adıdır (claude, codex, agy, cursor-*, copilot-*, opencode:*)
            name = m_name or "claude"
            return subscription_provider_class(name)(binary_name=name)
        elif p_type == "ollama":
            return OllamaProvider(model_name=m_name)

        cloud_providers = ("anthropic", "google", "openai", "deepseek", "groq", "openrouter", "moonshot", "z-ai", "nvidia")
        if p_type in cloud_providers and not api_key:
            raise ValueError(
                f"⚠️ {p_type.capitalize()} için API key girilmedi. "
                f"Lütfen Ayarlar'dan API key'inizi girin."
            )

        return OllamaProvider(model_name=m_name)
