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


def _doctor_copilot(monkeypatch, home):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCAL_APP_TOKEN", "t")
    installed = {"claude": False, "codex": False, "agy": False, "cursor": False,
                 "copilot": True, "opencode": False, "kimi": False}
    with patch("providers.oneshot_cli.installed_clis", return_value=installed):
        return asyncio.run(_route("/cli-doctor")(refresh=False, x_session_token="t"))["copilot"]["loggedIn"]


def test_copilot_without_config_file_is_signed_out(monkeypatch, tmp_path):
    assert _doctor_copilot(monkeypatch, tmp_path) is False


def test_copilot_with_unreadable_config_stays_unknown(monkeypatch, tmp_path):
    (tmp_path / ".copilot").mkdir()
    (tmp_path / ".copilot" / "config.json").write_text("{not json", encoding="utf-8")
    assert _doctor_copilot(monkeypatch, tmp_path) is None


def test_copilot_with_logged_in_user_is_signed_in(monkeypatch, tmp_path):
    (tmp_path / ".copilot").mkdir()
    (tmp_path / ".copilot" / "config.json").write_text('{"lastLoggedInUser": {"login": "x"}}', encoding="utf-8")
    assert _doctor_copilot(monkeypatch, tmp_path) is True
