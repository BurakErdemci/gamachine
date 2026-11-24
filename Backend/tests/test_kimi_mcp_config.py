"""kimi gets Gamachine's MCP servers from the user-level $KIMI_CODE_HOME/mcp.json, merged
with the user's own servers; every entry runs a launcher under $KIMI_CODE_HOME/gamachine/ that
starts the real server only inside a Gamachine turn; per-turn values reach the servers through
kimi's env, never the file; the project's own .mcp.json is never handed to kimi."""
import asyncio
import glob
import json
import os
import stat
import subprocess
import sys
import tempfile
import time

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


def _kimi_launcher(home, name="unityai"):
    return str(home / "gamachine" / (name + (".cmd" if sys.platform == "win32" else "")))


def _stub(tmp_path):
    """A real command in a folder with a space: records its args and the env it got."""
    stub = tmp_path / "dir with space" / "stub.py"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text("import json, os, sys\n"
                    "open(sys.argv[1], 'w').write(json.dumps([sys.argv[2:], os.environ.get('WORKSPACE_PATH')]))\n"
                    "print('hello from the real server')\n", encoding="utf-8")
    return stub


def _run_launcher(home, env, name="unityai"):
    start = time.monotonic()
    done = subprocess.run([_kimi_launcher(home, name)], env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)
    return done, time.monotonic() - start


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
    assert data["mcpServers"]["unityai"] == {"command": _kimi_launcher(home), "args": [], "enabled": True}
    assert all(data["mcpServers"][name]["command"] == _kimi_launcher(home, name) for name in OURS)


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


def test_entry_stays_enabled_when_the_real_command_is_gone(home, tmp_path, monkeypatch):
    # The launcher, not the file, copes with a moved or uninstalled Gamachine: the entry only
    # ever names a file Gamachine wrote under the user's kimi home.
    os.remove(_launcher(home))
    spawn = _run(_provider(chat=3), _ws(tmp_path), home, monkeypatch)
    assert _servers(home)["unityai"]["enabled"] is True
    assert os.path.isfile(_kimi_launcher(home))
    assert spawn["env"]["GAMACHINE_KIMI_EXE_UNITYAI"] == _launcher(home)


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


def _kimi_files(home):
    """mcp.json and the launchers (config.toml is the permission block, not under test here)."""
    return {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*")
            if p.is_file() and p.name != "config.toml"}


def test_mcp_json_and_launchers_hold_no_install_path_or_token(home, tmp_path, monkeypatch):
    secret = "token-77aa31c0"
    monkeypatch.setenv("LOCAL_APP_TOKEN", secret)
    _run(_provider(chat=9), _ws(tmp_path, "project-ws-12"), home, monkeypatch)
    texts = {name: data.decode("utf-8") for name, data in _kimi_files(home).items()}
    assert len(texts) == 1 + len(OURS)  # mcp.json and one launcher per server
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name, text in texts.items():
        for forbidden in (secret, "project-ws-12", BACKEND, os.path.dirname(sys.executable),
                          backend_dir, str(tmp_path / "bin")):
            # JSON doubles a Windows path's backslashes, so look for that spelling too.
            for spelling in (forbidden, json.dumps(forbidden)[1:-1]):
                assert spelling not in text, (name, forbidden)


def test_two_chats_leave_mcp_json_and_launchers_byte_identical(home, tmp_path, monkeypatch):
    _run(_provider(chat=1), _ws(tmp_path, "one"), home, monkeypatch)
    before = _kimi_files(home)
    mtimes = {p: os.stat(home / p).st_mtime_ns for p in before}
    _run(_provider(chat=2, read_only=True), _ws(tmp_path, "two"), home, monkeypatch)
    assert _kimi_files(home) == before
    assert {p: os.stat(home / p).st_mtime_ns for p in before} == mtimes  # not even rewritten


def test_launcher_is_restored_when_missing_or_changed(home, tmp_path, monkeypatch):
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    good = _kimi_files(home)
    path = _kimi_launcher(home, "gamachineBrowser")
    with open(path, "ab") as f:  # an older or damaged launcher of ours (it names the marker)
        f.write(b"rem tampered\r\n")
    os.remove(_kimi_launcher(home, "gamachineComputer"))
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    assert _kimi_files(home) == good
    assert not [p for p in (home / "gamachine").iterdir() if p.name.endswith(".tmp")]


def test_launchers_are_private_and_runnable(home, tmp_path, monkeypatch):
    from tests.test_cli_read_only_marker import _assert_private

    # A broad folder, so privacy has to come from the write: on Windows a new file would
    # inherit the folder's Users entry.
    home.mkdir()
    if sys.platform == "win32":
        subprocess.run(["icacls", str(home), "/grant", "*S-1-5-32-545:(OI)(CI)R"],
                       capture_output=True, check=True)
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    for name in OURS:
        path = _kimi_launcher(home, name)
        if os.name == "nt":
            _assert_private(path)
        else:
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o700  # owner only, and runnable


def test_launcher_exits_quietly_without_the_gamachine_marker(home, tmp_path, monkeypatch):
    # The user's own kimi sessions read the same mcp.json but carry no Gamachine marker.
    stub, flag = _stub(tmp_path), tmp_path / "ran.json"
    spawn = _run(_provider(chat=7), _ws(tmp_path), home, monkeypatch)
    env = {**os.environ, **spawn["env"], "GAMACHINE_KIMI_EXE_UNITYAI": sys.executable,
           "GAMACHINE_KIMI_CMD_UNITYAI": subprocess.list2cmdline([sys.executable, str(stub), str(flag)])
           if sys.platform == "win32" else f"'{sys.executable}' '{stub}' '{flag}'"}
    env.pop("GAMACHINE_KIMI_TURN")
    done, seconds = _run_launcher(home, env)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    assert not flag.exists()
    assert seconds < 20


def test_launcher_exits_quietly_when_the_real_command_is_gone(home, tmp_path, monkeypatch):
    # Gamachine moved or uninstalled: the entry still sits in the user's file.
    stub, flag = _stub(tmp_path), tmp_path / "ran.json"
    spawn = _run(_provider(chat=7), _ws(tmp_path), home, monkeypatch)
    env = {**os.environ, **spawn["env"], "GAMACHINE_KIMI_EXE_UNITYAI": str(tmp_path / "gone" / "python.exe"),
           "GAMACHINE_KIMI_CMD_UNITYAI": subprocess.list2cmdline([sys.executable, str(stub), str(flag)])
           if sys.platform == "win32" else f"'{sys.executable}' '{stub}' '{flag}'"}
    done, seconds = _run_launcher(home, env)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    assert not flag.exists()
    assert seconds < 20


def test_launcher_runs_the_real_command_inside_a_turn(home, tmp_path, monkeypatch):
    # The turn's own env names the real command; a path with a space and an argument survive.
    stub, flag = _stub(tmp_path), tmp_path / "ran.json"
    ws = _ws(tmp_path, "the ws")
    spawn = _run(_provider(chat=7), ws, home, monkeypatch)
    line = ([sys.executable, str(stub), str(flag), "an arg"])
    env = {**os.environ, **spawn["env"], "GAMACHINE_KIMI_EXE_UNITYAI": sys.executable,
           "GAMACHINE_KIMI_CMD_UNITYAI": subprocess.list2cmdline(line) if sys.platform == "win32"
           else " ".join(f"'{part}'" for part in line)}
    done, _ = _run_launcher(home, env)
    assert done.returncode == 0
    assert "hello from the real server" in done.stdout
    assert json.loads(flag.read_text(encoding="utf-8")) == [["an arg"], str(ws)]


def test_every_launcher_is_wired_to_its_own_server_in_the_turn_env(home, tmp_path, monkeypatch):
    spawn = _run(_provider(chat=7), _ws(tmp_path), home, monkeypatch)
    env = spawn["env"]
    assert env["GAMACHINE_KIMI_TURN"] == "1"
    for name in OURS:
        assert env[f"GAMACHINE_KIMI_EXE_{name.upper()}"]
        assert env[f"GAMACHINE_KIMI_CMD_{name.upper()}"]
    assert env["GAMACHINE_KIMI_EXE_UNITYAI"] == _launcher(home)


def test_entry_is_disabled_when_its_launcher_cannot_be_written(home, tmp_path, monkeypatch):
    # A file where the launcher folder should be: an enabled entry would point at nothing and
    # crash the user's kimi (spawn ENOENT), so it is written disabled and the turn still runs.
    home.mkdir()
    (home / "gamachine").write_text("in the way", encoding="utf-8")
    spawn = _run(_provider(chat=3), _ws(tmp_path), home, monkeypatch)
    assert spawn["argv"]
    assert all(entry["enabled"] is False for entry in _servers(home).values())


@pytest.mark.parametrize("folder", ["srv&x", "srv &x (1)"])
def test_launcher_starts_a_server_from_a_path_with_shell_syntax(home, tmp_path, monkeypatch, folder):
    # The turn's own env values (not hand-made ones) must start the server when the install
    # path holds `&` (cmd would read a bare one as a second command) and spaces.
    server = tmp_path / folder / ("run_mcp_server.cmd" if sys.platform == "win32" else "run_mcp_server.sh")
    server.parent.mkdir()
    flag = tmp_path / "ran.txt"
    if sys.platform == "win32":
        server.write_text(f'@echo off\r\necho %WORKSPACE_PATH%> "{flag}"\r\necho server up\r\n', encoding="ascii")
    else:
        server.write_text(f'#!/bin/sh\necho "$WORKSPACE_PATH" > \'{flag}\'\necho server up\n', encoding="ascii")
        server.chmod(0o755)
    monkeypatch.setattr(BaseCLIProvider, "_launcher_path", lambda self, name: str(server))
    ws = _ws(tmp_path)
    spawn = _run(_provider(chat=7), ws, home, monkeypatch)
    done, _ = _run_launcher(home, {**os.environ, **spawn["env"]})
    assert (done.returncode, done.stderr) == (0, "")
    assert "server up" in done.stdout
    assert flag.read_text(encoding="utf-8").strip() == str(ws)


async def _events(provider, ws, home, monkeypatch):
    """Every event of one turn up to the spawn, and the prompt argument kimi would get."""
    seen, events = [], []

    async def fake_spawn(*argv, **kw):
        seen.append(list(argv))
        raise RuntimeError("spawn intercepted")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)
    try:
        async for event in provider.analyze_code("prompt", cwd=str(ws)):
            events.append(event)
    except RuntimeError as e:
        assert "spawn intercepted" in str(e)
    return events, seen[0][seen[0].index("-p") + 1]


def test_a_normal_turn_has_no_notice_and_names_the_gamachine_tools(home, tmp_path, monkeypatch):
    events, prompt = asyncio.run(_events(_provider(chat=3), _ws(tmp_path), home, monkeypatch))
    assert not [e for e in events if e.get("code") == "kimi_no_gamachine_tools"]
    assert "mcp__unityai__save_file" in prompt


@pytest.mark.parametrize("broken", ["bad json", "home is a file"])
def test_a_turn_without_registration_says_so_and_does_not_promise_the_tools(home, tmp_path, monkeypatch, broken):
    if broken == "bad json":
        home.mkdir()
        (home / "mcp.json").write_text("{broken", encoding="utf-8")
    else:
        home.write_text("not a folder", encoding="utf-8")
        # The deny rules cannot be written: the turn is refused, nothing spawns.
        spawned = []

        async def refused_turn():
            async def fake_spawn(*argv, **kw):
                spawned.append(argv)
                raise RuntimeError("spawn intercepted")

            monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)
            return [e async for e in _provider(chat=3).analyze_code("prompt", cwd=str(_ws(tmp_path)))]

        events = asyncio.run(refused_turn())
        assert spawned == []
        assert [(e["type"], e["content"]) for e in events] == [
            ("error", KimiProvider._policy_refusal_message)], events
        return
    events, prompt = asyncio.run(_events(_provider(chat=3), _ws(tmp_path), home, monkeypatch))
    notices = [e for e in events if e.get("code") == "kimi_no_gamachine_tools"]
    assert len(notices) == 1, events
    assert notices[0]["type"] == "warning" and "without Gamachine tools" in notices[0]["message"]
    assert "mcp__unityai__save_file" not in prompt
    assert "NOT\navailable" in prompt


def test_a_foreign_entry_under_a_managed_name_is_backed_up_once(home, tmp_path, monkeypatch):
    home.mkdir()
    foreign = {"command": "my-unity-server", "args": ["--keep"], "env": {"K": "v"}}
    original = json.dumps({"mcpServers": {"unityai": foreign, "mine": {"command": "m"}}, "note": 1})
    (home / "mcp.json").write_text(original, encoding="utf-8")
    backup = home / "mcp.json.gamachine-backup"
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    assert backup.read_text(encoding="utf-8") == original
    assert _servers(home)["unityai"] == {"command": _kimi_launcher(home), "args": [], "enabled": True}
    assert _servers(home)["mine"] == {"command": "m"}
    # The user puts a different entry back: the first backup stays as it was.
    data = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    data["mcpServers"]["unityai"] = {"command": "another"}
    (home / "mcp.json").write_text(json.dumps(data), encoding="utf-8")
    _run(_provider(chat=2), _ws(tmp_path), home, monkeypatch)
    assert backup.read_text(encoding="utf-8") == original


def test_no_backup_without_a_collision(home, tmp_path, monkeypatch):
    home.mkdir()
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "m"}}}), encoding="utf-8")
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    _run(_provider(chat=2), _ws(tmp_path), home, monkeypatch)  # now holds our own launcher entries
    assert not (home / "mcp.json.gamachine-backup").exists()


def test_users_non_ascii_text_stays_readable_in_the_file(home, tmp_path, monkeypatch):
    home.mkdir()
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "m", "env": {"K": "Ünicode ✓ ş"}}}}),
                                   encoding="utf-8")
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    text = (home / "mcp.json").read_text(encoding="utf-8")
    assert "Ünicode ✓ ş" in text and (chr(92) + "u0") not in text


def _notices(events):
    return [e for e in events if e.get("code") == "kimi_no_gamachine_tools"]


def _no_temp_files(home):
    return [str(p) for p in home.rglob(".gamachine-*.tmp")]


def test_mcp_json_keeps_its_two_space_indent(home, tmp_path, monkeypatch):
    home.mkdir()
    (home / "mcp.json").write_text('{"mcpServers": {"mine": {"command": "m"}}}', encoding="utf-8")
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    assert (home / "mcp.json").read_text(encoding="utf-8").startswith('{\n  "mcpServers": {\n    "mine"')


@pytest.mark.parametrize("damaged", [b"", b'{"mcpServers": {"unityai": {"comm'])
def test_an_interrupted_backup_is_redone_and_never_trusted(home, tmp_path, monkeypatch, damaged):
    # A backup cut off by a crash must not let the original be replaced: it counts as missing.
    home.mkdir()
    original = json.dumps({"mcpServers": {"unityai": {"command": "foreign-server"}}, "note": 1})
    (home / "mcp.json").write_text(original, encoding="utf-8")
    (home / "mcp.json.gamachine-backup").write_bytes(damaged)
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    assert (home / "mcp.json.gamachine-backup").read_text(encoding="utf-8") == original
    assert _servers(home)["unityai"]["command"] == _kimi_launcher(home)
    assert not _no_temp_files(home)


def test_a_complete_earlier_backup_is_never_overwritten(home, tmp_path, monkeypatch):
    home.mkdir()
    earlier = json.dumps({"mcpServers": {"first": {"command": "f"}}})
    (home / "mcp.json.gamachine-backup").write_text(earlier, encoding="utf-8")
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"unityai": {"command": "foreign"}}}),
                                   encoding="utf-8")
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    assert (home / "mcp.json.gamachine-backup").read_text(encoding="utf-8") == earlier
    assert _servers(home)["unityai"]["command"] == _kimi_launcher(home)


def test_a_backup_that_cannot_be_made_leaves_the_file_unchanged_and_says_so(home, tmp_path, monkeypatch):
    home.mkdir()
    original = json.dumps({"mcpServers": {"unityai": {"command": "foreign-server"}}})
    (home / "mcp.json").write_text(original, encoding="utf-8")
    (home / "mcp.json.gamachine-backup").mkdir()  # nothing can be written under that name
    events, prompt = asyncio.run(_events(_provider(chat=1), _ws(tmp_path), home, monkeypatch))
    assert (home / "mcp.json").read_text(encoding="utf-8") == original
    assert len(_notices(events)) == 1 and "could not be backed up" in _notices(events)[0]["message"]
    assert "mcp__unityai__save_file" not in prompt
    assert not _no_temp_files(home)


@pytest.mark.parametrize("name", ["gamachineBrowser", "gamachineUnityMCP"])
def test_every_gamachine_prefixed_name_counts_as_ours_and_is_backed_up(home, tmp_path, monkeypatch, name):
    home.mkdir()
    original = json.dumps({"mcpServers": {name: {"command": "theirs"}}})
    (home / "mcp.json").write_text(original, encoding="utf-8")
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    assert (home / "mcp.json.gamachine-backup").read_text(encoding="utf-8") == original
    assert _servers(home)[name]["command"] == _kimi_launcher(home, name)


def test_a_foreign_launcher_file_is_kept_as_a_backup_before_it_is_replaced(home, tmp_path, monkeypatch):
    home.mkdir()
    launcher = home / "gamachine" / os.path.basename(_kimi_launcher(home))
    launcher.parent.mkdir()
    mine = b"@echo off\r\nrem the user's own script\r\n"
    launcher.write_bytes(mine)
    spawn = _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    assert (launcher.parent / (launcher.name + ".gamachine-backup")).read_bytes() == mine
    assert b"GAMACHINE_KIMI_TURN" in launcher.read_bytes()
    assert _servers(home)["unityai"]["enabled"] is True and spawn["argv"]
    # Our own launcher, later replaced by a newer text, needs no backup of its own.
    launcher.write_bytes(launcher.read_bytes() + b"rem old\r\n")
    (launcher.parent / (launcher.name + ".gamachine-backup")).unlink()
    _run(_provider(chat=2), _ws(tmp_path), home, monkeypatch)
    assert not (launcher.parent / (launcher.name + ".gamachine-backup")).exists()


def test_a_foreign_launcher_that_cannot_be_backed_up_is_left_alone_and_says_so(home, tmp_path, monkeypatch):
    home.mkdir()
    launcher = home / "gamachine" / os.path.basename(_kimi_launcher(home))
    launcher.parent.mkdir()
    mine = b"rem the user's own script\r\n"
    launcher.write_bytes(mine)
    (launcher.parent / (launcher.name + ".gamachine-backup")).mkdir()
    events, _ = asyncio.run(_events(_provider(chat=1), _ws(tmp_path), home, monkeypatch))
    assert launcher.read_bytes() == mine
    assert len(_notices(events)) == 1 and "could not write the launcher" in _notices(events)[0]["message"]
    assert _servers(home)["unityai"]["enabled"] is False
    assert not _no_temp_files(home)


def test_a_launcher_that_cannot_be_written_is_reported_in_the_turn(home, tmp_path, monkeypatch):
    home.mkdir()
    (home / "gamachine").write_text("in the way", encoding="utf-8")
    events, prompt = asyncio.run(_events(_provider(chat=3), _ws(tmp_path), home, monkeypatch))
    assert len(_notices(events)) == 1 and "could not write the launcher" in _notices(events)[0]["message"]
    assert "mcp__unityai__save_file" not in prompt


def test_a_failed_write_of_mcp_json_is_reported_and_leaves_no_temp_file(home, tmp_path, monkeypatch):
    home.mkdir()
    original = '{"mcpServers": {"mine": {"command": "m"}}}'
    (home / "mcp.json").write_text(original, encoding="utf-8")
    real = os.replace

    def refuse(src, dst, *a, **k):
        if os.path.basename(str(dst)) == "mcp.json":
            raise PermissionError("injected")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", refuse)
    events, prompt = asyncio.run(_events(_provider(chat=3), _ws(tmp_path), home, monkeypatch))
    assert (home / "mcp.json").read_text(encoding="utf-8") == original
    assert len(_notices(events)) == 1 and "could not be written" in _notices(events)[0]["message"]
    assert "mcp__unityai__save_file" not in prompt
    assert not _no_temp_files(home)


def test_a_lone_surrogate_in_the_users_file_is_written_escaped_not_lost(home, tmp_path, monkeypatch):
    # UTF-8 cannot carry it, so the ensure_ascii=False write fails; the retry escapes it.
    home.mkdir()
    (home / "mcp.json").write_text('{"mcpServers": {"mine": {"command": "m", "env": {"K": "a\\ud800b ş"}}}}',
                                   encoding="utf-8")
    events, prompt = asyncio.run(_events(_provider(chat=1), _ws(tmp_path), home, monkeypatch))
    assert not _notices(events)
    assert _servers(home)["mine"]["env"]["K"] == "a\ud800b ş"
    assert _servers(home)["unityai"]["enabled"] is True
    assert not _no_temp_files(home)


def test_every_file_is_private_before_it_takes_its_final_name(home, tmp_path, monkeypatch):
    from tests.test_cli_read_only_marker import _assert_private

    # Replace/link are the moments a file becomes visible; whatever is renamed must already be private.
    home.mkdir()
    original = json.dumps({"mcpServers": {"unityai": {"command": "foreign"}}})
    (home / "mcp.json").write_text(original, encoding="utf-8")
    (home / "gamachine").mkdir()
    (home / "gamachine" / os.path.basename(_kimi_launcher(home))).write_bytes(b"rem foreign\r\n")
    if sys.platform == "win32":
        subprocess.run(["icacls", str(home), "/grant", "*S-1-5-32-545:(OI)(CI)R"],
                       capture_output=True, check=True)
    published = []
    real_replace, real_link = os.replace, os.link

    def owner_only(path):
        # On POSIX a launcher is 0700 (it must run) and a data file 0600; both are private.
        if sys.platform == "win32":
            _assert_private(path)
        else:
            assert stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0, oct(os.stat(path).st_mode)

    def replace(src, dst, *a, **k):
        owner_only(str(src))
        published.append(os.path.basename(str(dst)))
        return real_replace(src, dst, *a, **k)

    def link(src, dst, *a, **k):
        owner_only(str(src))
        published.append(os.path.basename(str(dst)))
        return real_link(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "link", link)
    _run(_provider(chat=1), _ws(tmp_path), home, monkeypatch)
    # the mcp.json backup, the launcher backup, the launchers and mcp.json itself
    assert "mcp.json.gamachine-backup" in published and "mcp.json" in published
    assert any(name.endswith(".gamachine-backup") and name != "mcp.json.gamachine-backup" for name in published)


def test_a_double_quote_in_a_server_command_is_refused_and_the_entry_stays_off(home, tmp_path, monkeypatch):
    from providers import codex_unitymcp_bridge

    with pytest.raises(ValueError):
        KimiProvider._real_command(["server", 'a"b'])
    # A harmless stand-in for the Unity bridge command that holds a quote.
    monkeypatch.setattr(codex_unitymcp_bridge, "bridge_argv", lambda: ["bridge-server", 'x"y'])
    events, prompt = asyncio.run(_events(_provider(chat=1), _ws(tmp_path), home, monkeypatch))
    assert len(_notices(events)) == 1 and "gamachineUnityMCP" in _notices(events)[0]["message"]
    assert _servers(home)["gamachineUnityMCP"]["enabled"] is False
    assert 'x"y' not in (home / "mcp.json").read_text(encoding="utf-8")
    assert "mcp__unityai__save_file" not in prompt


def test_the_reason_in_the_notice_follows_the_chat_language(home, tmp_path, monkeypatch):
    home.mkdir()
    (home / "mcp.json").write_text("{broken", encoding="utf-8")
    provider = _provider(chat=3)
    provider.language = "tr"
    events, _ = asyncio.run(_events(provider, _ws(tmp_path), home, monkeypatch))
    (notice,) = _notices(events)
    assert "geçerli bir mcp.json değil" in notice["message"] and "olmadan" in notice["message"]
