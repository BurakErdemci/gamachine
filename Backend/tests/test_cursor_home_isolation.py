"""Tests for CursorProvider's isolated Gamachine-owned home directory."""
import asyncio
import json
import logging
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from providers import cli_base
from providers.cursor_provider import CursorProvider, cursor_home_dir


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("USERPROFILE", str(real_home))

    def fake_expanduser(path):
        if path == "~":
            return str(real_home)
        if path.startswith("~/") or path.startswith("~\\"):
            return os.path.join(str(real_home), path[2:])
        return path

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda *a, **k: None)
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr("computer_use.settings.enabled_now", lambda: False)
    monkeypatch.setattr("browser_use.settings.enabled_now", lambda: False)
    monkeypatch.setattr("connectors.launcher.ready_ids", lambda db=None: [])
    monkeypatch.setattr(
        "connectors.launcher.installer",
        lambda: SimpleNamespace(root=str(tmp_path / "connectors")),
    )
    return real_home


class _SpawnCaptured(Exception):
    pass


def test_spawn_env_sets_home_and_userprofile_to_cursor_home(isolated_home, tmp_path, monkeypatch):
    roaming = str(tmp_path / "roaming-appdata")
    local = str(tmp_path / "local-appdata")
    monkeypatch.setenv("APPDATA", roaming)
    monkeypatch.setenv("LOCALAPPDATA", local)

    ws = tmp_path / "ws"
    ws.mkdir()

    provider = CursorProvider("cursor-auto")
    monkeypatch.setattr(provider, "_launcher_path", lambda name: "test-launcher")
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)
    monkeypatch.setattr(provider, "_cli_installed", lambda name: True)
    monkeypatch.setattr(provider, "_resolve_exec", lambda cmd: list(cmd))

    captured = {}

    async def fake_spawn(*args, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        raise _SpawnCaptured()

    monkeypatch.setattr(cli_base.asyncio, "create_subprocess_exec", fake_spawn)

    async def drive():
        try:
            async for _ in provider.analyze_code("hello", cwd=str(ws)):
                pass
        except _SpawnCaptured:
            pass

    asyncio.run(drive())

    expected_home = os.path.join(str(isolated_home), ".unity_architect_ai", "cursor-home")
    assert cursor_home_dir() == expected_home
    assert captured["env"]["HOME"] == expected_home
    assert captured["env"]["USERPROFILE"] == expected_home
    assert captured["env"]["APPDATA"] == roaming
    assert captured["env"]["LOCALAPPDATA"] == local


def test_chats_link_created_when_real_chats_dir_exists(isolated_home, caplog):
    real_chats = isolated_home / ".cursor" / "chats"
    real_chats.mkdir(parents=True)
    (real_chats / "chat1.db").write_text("session-data", encoding="utf-8")

    provider = CursorProvider("cursor-auto")
    with caplog.at_level(logging.WARNING):
        prepared = provider._prepare_cursor_home()

    expected_home = os.path.join(str(isolated_home), ".unity_architect_ai", "cursor-home")
    assert prepared == expected_home
    link = os.path.join(expected_home, ".cursor", "chats")
    if os.path.isdir(link):
        assert os.path.realpath(link) == os.path.realpath(str(real_chats))
        (real_chats / "live.txt").write_text("visible", encoding="utf-8")
        assert (isolated_home / ".unity_architect_ai" / "cursor-home" / ".cursor" / "chats" / "live.txt").read_text(encoding="utf-8") == "visible"
    else:
        assert any(r.levelno == logging.WARNING and "chats" in r.message.lower() for r in caplog.records)


def test_chats_link_failure_logs_warning_and_returns_home(isolated_home, monkeypatch, caplog):
    real_chats = isolated_home / ".cursor" / "chats"
    real_chats.mkdir(parents=True)

    if os.name == "nt":
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="junction denied"),
        )
    else:
        monkeypatch.setattr(os, "symlink", lambda *a, **k: (_ for _ in ()).throw(OSError("symlink denied")))

    provider = CursorProvider("cursor-auto")
    with caplog.at_level(logging.WARNING):
        prepared = provider._prepare_cursor_home()

    expected_home = os.path.join(str(isolated_home), ".unity_architect_ai", "cursor-home")
    assert prepared == expected_home
    assert any(r.levelno == logging.WARNING and "chats" in r.message.lower() for r in caplog.records)


def test_nothing_created_under_real_cursor_when_chats_dir_missing(isolated_home):
    provider = CursorProvider("cursor-auto")
    prepared = provider._prepare_cursor_home()

    expected_home = os.path.join(str(isolated_home), ".unity_architect_ai", "cursor-home")
    assert prepared == expected_home
    assert not (isolated_home / ".cursor").exists()
    assert os.path.isdir(os.path.join(expected_home, ".cursor"))
    assert not os.path.lexists(os.path.join(expected_home, ".cursor", "chats"))

    (isolated_home / ".cursor").mkdir()
    (isolated_home / ".cursor" / "mcp.json").write_text("{}", encoding="utf-8")
    provider._prepare_cursor_home()
    assert os.listdir(isolated_home / ".cursor") == ["mcp.json"]
    assert not os.path.lexists(os.path.join(expected_home, ".cursor", "chats"))


def test_existing_cursor_home_chats_is_left_alone(isolated_home):
    real_chats = isolated_home / ".cursor" / "chats"
    real_chats.mkdir(parents=True)
    (real_chats / "real.txt").write_text("real", encoding="utf-8")

    cursor_chats = isolated_home / ".unity_architect_ai" / "cursor-home" / ".cursor" / "chats"
    cursor_chats.mkdir(parents=True)
    (cursor_chats / "existing.txt").write_text("keep", encoding="utf-8")

    provider = CursorProvider("cursor-auto")
    prepared = provider._prepare_cursor_home()

    assert prepared == os.path.join(str(isolated_home), ".unity_architect_ai", "cursor-home")
    assert (cursor_chats / "existing.txt").read_text(encoding="utf-8") == "keep"
    assert not (cursor_chats / "real.txt").exists()


def test_prepare_cursor_home_none_refuses_register_mcp_and_resets_cursor_home(isolated_home, tmp_path, monkeypatch, caplog):
    ws = tmp_path / "ws"
    ws.mkdir()

    provider = CursorProvider("cursor-auto")
    provider._cursor_home = "stale-home"
    monkeypatch.setattr(provider, "_prepare_cursor_home", lambda: None)

    with caplog.at_level(logging.ERROR):
        ok = provider._register_mcp("test-launcher", str(ws), "http://localhost:8000")

    assert ok is False
    assert provider._cursor_home is None
    assert provider._turn_spawn_env() == {}
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_prepare_cursor_home_returns_none_when_mkdir_fails(isolated_home, monkeypatch):
    def fail_makedirs(*args, **kwargs):
        raise OSError("disk read-only")

    monkeypatch.setattr(os, "makedirs", fail_makedirs)
    provider = CursorProvider("cursor-auto")
    assert provider._prepare_cursor_home() is None


def test_command_line_keeps_force_and_approve_mcps(isolated_home, monkeypatch):
    provider = CursorProvider("cursor-auto")
    monkeypatch.setattr("providers.cursor_provider.resolve_cursor_cmd", lambda: ["agent"])
    cmd = provider._build_cmd("hello world")
    assert "--trust" in cmd
    assert "--approve-mcps" in cmd
    assert "--force" in cmd


def test_mcp_entries_carry_real_home_while_spawn_env_keeps_cursor_home(isolated_home, tmp_path, monkeypatch):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda *a, **k: "http://127.0.0.1:8080/mcp")
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: True)
    monkeypatch.setattr("computer_use.settings.enabled_now", lambda: True)
    monkeypatch.setattr("browser_use.settings.enabled_now", lambda: True)
    monkeypatch.setattr("connectors.launcher.ready_ids", lambda db=None: ["blender"])
    monkeypatch.setattr(
        "connectors.launcher.installer",
        lambda: SimpleNamespace(root=str(tmp_path / "connectors")),
    )

    ws = tmp_path / "ws"
    cursor_dir = ws / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"userServer": {"command": "my-mcp", "env": {"FOO": "bar"}}}}),
        encoding="utf-8",
    )

    provider = CursorProvider("cursor-auto")
    assert provider._register_mcp("test-launcher", str(ws), "http://localhost:8000") is True

    written = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))
    servers = written["mcpServers"]
    real_home = str(isolated_home)
    for name in (
        "unityai",
        "unityMCP",
        "gamachineComputer",
        "gamachineBrowser",
        "gamachineConnectors",
    ):
        assert servers[name]["env"]["HOME"] == real_home
        assert servers[name]["env"]["USERPROFILE"] == real_home

    assert servers["userServer"] == {"command": "my-mcp", "env": {"FOO": "bar"}}
    assert "HOME" not in servers["userServer"]["env"]
    assert "USERPROFILE" not in servers["userServer"]["env"]

    expected_cursor_home = os.path.join(real_home, ".unity_architect_ai", "cursor-home")
    assert provider._turn_spawn_env() == {
        "HOME": expected_cursor_home,
        "USERPROFILE": expected_cursor_home,
    }
