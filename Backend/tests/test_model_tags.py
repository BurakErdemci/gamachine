"""Model badges travel as data (`tag`), not as Turkish words inside `name`;
a Copilot profile without config.json reads as signed out."""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from routes.config_routes import _parse_opencode_models, create_config_router


def _route(path):
    router = create_config_router(MagicMock())
    return next(r for r in router.routes if r.path == path).endpoint


def _available():
    with patch("providers.model_catalog.list_models", return_value=None), \
         patch("providers.model_catalog.openrouter_catalog", return_value={}), \
         patch("urllib.request.urlopen", side_effect=OSError("closed")):
        return asyncio.run(_route("/available-models")(refresh=False))


def test_no_model_name_carries_turkish_label_words():
    models = _available()["subscription"] + _parse_opencode_models("opencode/big-pickle-free\n")
    assert models
    for m in models:
        assert "Önerilen" not in m["name"] and "Ücretsiz" not in m["name"]


def test_recommended_models_are_tagged_as_data():
    by_id = {m["id"]: m for m in _available()["subscription"]}
    assert by_id["gemini-3.8-flash"]["name"] == "Gemini 3.8 Flash"
    assert by_id["gemini-3.8-flash"]["tag"] == "recommended"
    assert by_id["copilot-auto"]["name"] == "Copilot Auto"
    assert by_id["copilot-auto"]["tag"] == "recommended"


def test_free_opencode_model_is_tagged_free():
    free, go = _parse_opencode_models("opencode/big-pickle-free\nopencode-go/big-pickle-free\n")
    assert free["name"] == "Big Pickle" and free["tag"] == "free"
    assert "tag" not in go


