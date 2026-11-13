"""Model badges travel as data (`tag`), not as Turkish words inside `name`;
a Copilot profile without config.json reads as signed out."""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

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


def _doctor_copilot(monkeypatch, home, env=None):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCAL_APP_TOKEN", "t")
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN", "GH_CONFIG_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    installed = {"claude": False, "codex": False, "agy": False, "cursor": False,
                 "copilot": True, "opencode": False, "kimi": False}
    with patch("routes.config_routes.installed_clis", return_value=installed):
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


@pytest.mark.parametrize("key", ["GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN"])
def test_copilot_without_config_but_token_env_is_not_signed_out(monkeypatch, tmp_path, key):
    assert _doctor_copilot(monkeypatch, tmp_path, {key: "x"}) is None


def test_copilot_without_config_but_gh_login_is_not_signed_out(monkeypatch, tmp_path):
    gh = tmp_path / "AppData" / "Roaming" / "GitHub CLI" if os.name == "nt" else tmp_path / ".config" / "gh"
    gh.mkdir(parents=True)
    (gh / "hosts.yml").write_text("github.com: {}"+chr(10), encoding="utf-8")
    assert _doctor_copilot(monkeypatch, tmp_path) is None


def test_copilot_config_without_login_but_token_env_is_not_signed_out(monkeypatch, tmp_path):
    # Copilot CLI used with a token may write config.json without lastLoggedInUser.
    (tmp_path / ".copilot").mkdir()
    (tmp_path / ".copilot" / "config.json").write_text("{}", encoding="utf-8")
    assert _doctor_copilot(monkeypatch, tmp_path, {"GH_TOKEN": "x"}) is None
    monkeypatch.delenv("GH_TOKEN")
    assert _doctor_copilot(monkeypatch, tmp_path) is False


def test_copilot_gh_login_in_gh_config_dir_is_not_signed_out(monkeypatch, tmp_path):
    gh = tmp_path / "custom-gh"
    gh.mkdir()
    (gh / "hosts.yml").write_text("github.com: {}" + chr(10), encoding="utf-8")
    assert _doctor_copilot(monkeypatch, tmp_path, {"GH_CONFIG_DIR": str(gh)}) is None


@pytest.mark.skipif(os.name == "nt", reason="XDG_CONFIG_HOME is where gh keeps its login outside Windows")
def test_copilot_gh_login_under_xdg_config_home_is_not_signed_out(monkeypatch, tmp_path):
    gh = tmp_path / "xdg" / "gh"
    gh.mkdir(parents=True)
    (gh / "hosts.yml").write_text("github.com: {}" + chr(10), encoding="utf-8")
    assert _doctor_copilot(monkeypatch, tmp_path, {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}) is None
