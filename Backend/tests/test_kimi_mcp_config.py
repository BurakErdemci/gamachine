"""kimi gets Gamachine's MCP servers from the user-level $KIMI_CODE_HOME/mcp.json, merged
with the user's own servers; per-turn values reach the servers through kimi's env, never the
file; the project's own .mcp.json is never handed to kimi."""
import asyncio
import glob
import json
import os
import subprocess
import sys
import tempfile

import pytest

from providers.cli_base import BaseCLIProvider
from providers.kimi_provider import KimiProvider
from spawn_env import CONVERSATION_ENV, READ_ONLY_ENV

BACKEND = "http://127.0.0.1:59999"
OURS = {"unityai", "gamachineUnityMCP", "gamachineComputer", "gamachineBrowser", "gamachineConnectors"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    path = tmp_path / "kimi-home"
    monkeypatch.setenv("KIMI_CODE_HOME", str(path))
    monkeypatch.setenv("UNITYAI_URL", BACKEND)
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda *a, **k: None)
    monkeypatch.setattr(BaseCLIProvider, "_cli_installed", staticmethod(lambda name: True))
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", staticmethod(lambda cmd: list(cmd)))
    monkeypatch.setattr(BaseCLIProvider, "_ensure_exec", staticmethod(lambda path: None))
    launcher = tmp_path / "bin" / "run_mcp_server.cmd"
    launcher.parent.mkdir()
    launcher.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(BaseCLIProvider, "_launcher_path", lambda self, name: str(launcher))
    return path


def _launcher(home):
    return str(home.parent / "bin" / "run_mcp_server.cmd")


def _ws(tmp_path, name="ws"):
    ws = tmp_path / name
    ws.mkdir(exist_ok=True)
    return ws


def _servers(home):
    return json.loads((home / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]


def _provider(chat=None, read_only=False):
    provider = KimiProvider(binary_name="kimi-k3")
    provider._conversation_id = chat
    provider._read_only = read_only
    return provider


async def _turn(provider, ws, home, monkeypatch, seen):
    """One real analyze_code turn up to the spawn; records what the kimi process would get
    and the mcp.json bytes at that moment."""
    async def fake_spawn(*argv, **kw):
        seen.append({"argv": list(argv), "env": dict(kw.get("env") or {}), "cwd": kw.get("cwd"),
                     "file": (home / "mcp.json").read_bytes()})
        await asyncio.sleep(0.05)  # let a concurrent turn reach its own spawn first
        raise RuntimeError("spawn intercepted")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)
    try:
        async for _ in provider.analyze_code("prompt", cwd=str(ws)):
            pass
    except RuntimeError as e:
        assert "spawn intercepted" in str(e)


def _run(provider, ws, home, monkeypatch):
    seen = []
    asyncio.run(_turn(provider, ws, home, monkeypatch, seen))
    return seen[0]


def test_user_servers_and_keys_survive_and_gamachine_entries_are_added(home, tmp_path, monkeypatch):
    home.mkdir()
    mine = {"command": "my-tool", "args": ["--keep"], "env": {"K": "v"}}
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"mine": mine, "unityMCP": {"url": "http://u"}},
                                               "note": 1}), encoding="utf-8")
    _run(_provider(chat=5), _ws(tmp_path), home, monkeypatch)
    data = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    assert data["note"] == 1
    assert data["mcpServers"]["mine"] == mine
    assert data["mcpServers"]["unityMCP"] == {"url": "http://u"}  # the user's own Unity entry
    assert OURS <= set(data["mcpServers"])
    assert data["mcpServers"]["unityai"] == {"command": _launcher(home), "args": [], "enabled": True}


def test_file_is_not_rewritten_when_gamachine_entries_match(home, tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    _run(_provider(chat=5), ws, home, monkeypatch)
    # Same content, the user's own formatting: an unchanged set of entries must not touch it.
    compact = json.dumps(json.loads((home / "mcp.json").read_text(encoding="utf-8")),
                         separators=(",", ":")).encode()
    (home / "mcp.json").write_bytes(compact)
    _run(_provider(chat=6), ws, home, monkeypatch)
    assert (home / "mcp.json").read_bytes() == compact


def test_file_holds_no_token_workspace_or_chat(home, tmp_path, monkeypatch):
    secret = "token-3c1f9a77"
    monkeypatch.setenv("LOCAL_APP_TOKEN", secret)
    ws = _ws(tmp_path, "project-ws-81")
    _run(_provider(chat=987654, read_only=True), ws, home, monkeypatch)
    text = (home / "mcp.json").read_text(encoding="utf-8")
    assert secret not in text
    assert "project-ws-81" not in text
    assert "987654" not in text
    assert READ_ONLY_ENV not in text and CONVERSATION_ENV not in text
    assert BACKEND not in text


def test_per_turn_values_reach_kimi_through_its_env(home, tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    spawn = _run(_provider(chat=41, read_only=True), ws, home, monkeypatch)
    env = spawn["env"]
    assert spawn["cwd"] == str(ws)
    assert env["WORKSPACE_PATH"] == str(ws)  # unity_ai_mcp.server's --workspace default
    assert env["UNITYAI_URL"] == BACKEND
    assert env[CONVERSATION_ENV] == "41"
    assert env[READ_ONLY_ENV] == "1"
    assert "LOCAL_APP_TOKEN" not in env


def test_two_concurrent_chats_leave_one_unchanged_file(home, tmp_path, monkeypatch):
    ws1, ws2 = _ws(tmp_path, "one"), _ws(tmp_path, "two")
    _run(_provider(chat=1), ws1, home, monkeypatch)
    before = (home / "mcp.json").read_bytes()
    seen = []

    async def both():
        await asyncio.gather(_turn(_provider(chat=11), ws1, home, monkeypatch, seen),
                             _turn(_provider(chat=22, read_only=True), ws2, home, monkeypatch, seen))

    asyncio.run(both())
    assert [s["file"] for s in seen] == [before, before]
    assert (home / "mcp.json").read_bytes() == before
    by_chat = {s["env"][CONVERSATION_ENV]: s["env"] for s in seen}
    assert by_chat["11"]["WORKSPACE_PATH"] == str(ws1) and READ_ONLY_ENV not in by_chat["11"]
    assert by_chat["22"]["WORKSPACE_PATH"] == str(ws2) and by_chat["22"][READ_ONLY_ENV] == "1"


def test_unity_entry_follows_unity_and_its_url_rides_the_env(home, tmp_path, monkeypatch):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    ws = _ws(tmp_path)
    spawn = _run(_provider(chat=3), ws, home, monkeypatch)
    # Unity off: a listed bridge made every kimi turn wait ~30 s for its handshake (measured).
    assert _servers(home)["gamachineUnityMCP"]["enabled"] is False
    assert "UNITY_MCP_URL" not in spawn["env"]
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: True)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda *a, **k: "http://127.0.0.1:1/mcp")
    spawn = _run(_provider(chat=3), ws, home, monkeypatch)
    entry = _servers(home)["gamachineUnityMCP"]
    assert entry["enabled"] is True and "env" not in entry
    assert spawn["env"]["UNITY_MCP_URL"] == "http://127.0.0.1:1/mcp"


def test_entry_whose_command_is_missing_is_written_disabled(home, tmp_path, monkeypatch):
    # kimi 2.5.3 dies on spawn ENOENT of any enabled server (measured), user sessions included.
    os.remove(_launcher(home))
    _run(_provider(chat=3), _ws(tmp_path), home, monkeypatch)
    assert _servers(home)["unityai"]["enabled"] is False


@pytest.mark.parametrize("content", ["{broken", '{"mcpServers": []}', "[1, 2]"])
def test_unusable_user_file_is_left_alone(home, tmp_path, monkeypatch, content):
    home.mkdir()
    (home / "mcp.json").write_text(content, encoding="utf-8")
    spawn = _run(_provider(chat=3), _ws(tmp_path), home, monkeypatch)
    assert (home / "mcp.json").read_text(encoding="utf-8") == content
    assert spawn["argv"]  # the turn still runs


def test_written_file_is_private_even_in_a_shared_folder(home, tmp_path, monkeypatch):
    from tests.test_cli_read_only_marker import _assert_private

    # A broad starting point, so privacy has to come from the write: on Windows the new
    # file would inherit the folder's Users entry, on POSIX the user's 0644 would be kept.
    home.mkdir()
    (home / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    if sys.platform == "win32":
        subprocess.run(["icacls", str(home), "/grant", "*S-1-5-32-545:(OI)(CI)R"],
                       capture_output=True, check=True)
    else:
        os.chmod(home / "mcp.json", 0o644)
    _run(_provider(chat=3), _ws(tmp_path), home, monkeypatch)
    _assert_private(str(home / "mcp.json"))


def test_no_per_turn_temp_mcp_file(home, tmp_path, monkeypatch):
    pattern = os.path.join(tempfile.gettempdir(), "uai_kimi_mcp_*")
    before = set(glob.glob(pattern))
    _run(_provider(chat=3), _ws(tmp_path), home, monkeypatch)
    assert set(glob.glob(pattern)) == before


def test_command_never_names_the_project_mcp_file(home, tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    (ws / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"evil": {"command": "/bin/echo", "args": ["pwned"]}}}), encoding="utf-8")
    spawn = _run(_provider(chat=3), ws, home, monkeypatch)
    assert not [a for a in spawn["argv"] if ".mcp.json" in a or a.startswith("--mcp")]
    assert "evil" not in _servers(home)


def test_spawn_does_not_trust_the_project_so_its_mcp_json_stays_unloaded(home, tmp_path, monkeypatch):
    """kimi 2.5.3 loads <git root>/.mcp.json and .kimi-code/mcp.json only in a
    trusted folder; KIMI_CODE_TRUST_WORKSPACE=1 trusts it. The project's servers
    would then start without a prompt, so the variable never reaches the CLI,
    even when the backend itself was started with it."""
    monkeypatch.setenv("KIMI_CODE_TRUST_WORKSPACE", "1")
    spawn = _run(_provider(chat=3), _ws(tmp_path), home, monkeypatch)
    assert "KIMI_CODE_TRUST_WORKSPACE" not in spawn["env"]


def test_write_mcp_config_output_unchanged_for_plain_workspace(home, tmp_path):
    ws = _ws(tmp_path, "plain")
    _provider()._write_mcp_config(str(ws))
    data = json.loads((ws / ".mcp.json").read_text(encoding="utf-8"))
    assert data == {"mcpServers": {"unityai": {
        "command": _launcher(home),
        "args": ["--workspace", str(ws)],
        "env": {"UNITYAI_URL": BACKEND},
    }}}


def test_bad_workspace_mcp_json_is_left_alone_and_kimi_still_gets_its_servers(home, tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    (ws / ".mcp.json").write_text('{"mcpServers": []}', encoding="utf-8")
    _run(_provider(chat=3), ws, home, monkeypatch)
    assert (ws / ".mcp.json").read_text(encoding="utf-8") == '{"mcpServers": []}'
    assert OURS <= set(_servers(home))
