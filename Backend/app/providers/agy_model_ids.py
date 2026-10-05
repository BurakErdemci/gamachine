"""Read-time replacements for removed subscription agy model IDs."""
from typing import Tuple


AGY_MODEL_REPLACEMENTS = {
    "agy-claude-sonnet-4-6": "agy-claude-sonnet-5-5",
    "agy-claude-opus-4-6": "agy-claude-opus-5-5",
    "gemini-3.5-flash": "gemini-3.8-flash",
    "gemini-3.5-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.5-flash-low": "gemini-3.8-flash-low",
}


def normalize_model(provider_type: str, model_name: str) -> Tuple[str, str]:
    """Migrate removed agy IDs on read, without changing API or other CLI IDs."""
    if provider_type == "subscription":
        model_name = AGY_MODEL_REPLACEMENTS.get(model_name, model_name)
    return provider_type, model_name
