"""Agent-browser registration and settings switch, without external services."""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from browser_use import registration, settings
from spawn_env import CONVERSATION_ENV


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    monkeypatch.setattr(settings, "_store", None)


def bind_flag(on):
    values = {settings.ENABLED_KEY: "1" if on else "0"}
    db = types.SimpleNamespace(get_setting=values.get, set_setting=values.__setitem__)
    settings.bind_store(db)
    return db


def test_names_and_key():
    assert registration.SERVER_NAME == "gamachineBrowser"
    assert registration.CLAUDE_TOOL_PREFIX == "mcp__gamachineBrowser__"
    assert settings.ENABLED_KEY == "browser_use_enabled"


def test_enabled_now_fails_closed_and_tracks_the_stored_flag():
    assert settings.enabled_now() is False
    broken = MagicMock()
    broken.get_setting.side_effect = RuntimeError("database unavailable")
    settings.bind_store(broken)
    assert settings.enabled_now() is False
    db = bind_flag(False)
    assert settings.enabled_now() is False
    settings.set_enabled(db, True)
    assert settings.enabled_now() is True
    settings.set_enabled(db, False)
    assert settings.enabled_now() is False


@pytest.mark.parametrize("frozen", [False, True])
def test_server_argv_for_dev_and_frozen_builds(monkeypatch, frozen):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    expected = [sys.executable]
    if not frozen:
        expected.append(str(Path(registration.__file__).resolve().parents[1] / "main.py"))
    expected.append("browser-mcp-server")
    assert registration.server_argv() == expected


def test_entries_are_none_while_the_switch_is_off():
    bind_flag(False)
    assert registration.claude_server_entry(5) is None
    assert registration.codex_thread_entry(["A"]) is None


@pytest.mark.parametrize("bad", [None, 0, -1, "5", True, 1.0])
def test_claude_entry_needs_a_positive_integer_chat(bad):
    bind_flag(True)
    assert registration.claude_server_entry(bad) is None


def test_claude_entry_names_the_chat_and_backend(monkeypatch):
    bind_flag(True)
    monkeypatch.delenv("ANTIGRAVITY_URL", raising=False)
    monkeypatch.setenv("UNITYAI_URL", "http://localhost:9999")
    entry = registration.claude_server_entry(5)
    argv = registration.server_argv()
    assert entry == {"type": "stdio", "command": argv[0], "args": argv[1:],
                     "env": {CONVERSATION_ENV: "5", "UNITYAI_URL": "http://localhost:9999"}}


def test_codex_entry_waits_longer_than_the_card_and_forwards_env(monkeypatch):
    bind_flag(True)
    monkeypatch.setenv("UNITYAI_URL", "http://localhost:9999")
    entry = registration.codex_thread_entry(["LOCAL_APP_TOKEN"])
    assert entry["default_tools_approval_mode"] == "approve"
    assert entry["tool_timeout_sec"] >= 180
    assert entry["env_vars"] == ["LOCAL_APP_TOKEN"]
    assert entry["args"][-1] == "browser-mcp-server"
    assert "env_vars" not in registration.codex_thread_entry(None)


def test_codex_thread_config_adds_the_browser_unless_read_only(monkeypatch):
    from providers import codex_session
    bind_flag(True)
    monkeypatch.setattr(codex_session, "_configured_codex_mcp_names", lambda: set())
    name = registration.SERVER_NAME
    assert name in codex_session._trusted_mcp_config(["X"], read_only=False)["mcp_servers"]
    assert name not in codex_session._trusted_mcp_config(["X"], read_only=True)["mcp_servers"]
