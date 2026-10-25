import asyncio
import json
import os
import threading
from unittest.mock import Mock

import pytest

from providers import workspace_config

REAL_WRITER = workspace_config.guvenli_config_yaz
SECRET = "Test-Product-Secret-0123456789abcdefghijklmnop"


@pytest.fixture(params=["cursor", "opencode"])
def setup(request, tmp_path, monkeypatch):
    from providers import cursor_provider, opencode_provider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    if request.param == "cursor":
        module = cursor_provider
        provider = module.CursorProvider("cursor-auto")
        policy_rel, mcp_rel = ".cursor/cli.json", ".cursor/mcp.json"
    else:
        module = opencode_provider
        provider = module.OpenCodeProvider("opencode:opencode/test-model")
        monkeypatch.setattr(provider, "_prepare_bash_shadow", lambda: None)
        policy_rel = mcp_rel = "opencode.json"
    monkeypatch.setattr(module, "ensure_gitignored", Mock())
    (tmp_path / ".cursor").mkdir(exist_ok=True)
    return provider, module, tmp_path, policy_rel, mcp_rel


def register(provider, workspace):
    return provider._register_mcp("test-launcher", str(workspace), "http://localhost:8000")


def assert_policy(module, workspace, policy_rel):
    data = json.loads((workspace / policy_rel).read_text(encoding="utf-8"))
    if module.__name__.endswith("cursor_provider"):
        deny = data["permissions"]["deny"]
        assert "Write(**)" in deny and "Shell(**)" in deny
    else:
        assert data["permission"]["edit"] == "deny"
        assert data["permission"]["bash"] == "deny"


def test_bigint_config_is_renamed_and_policy_written(setup):
    provider, module, ws, policy_rel, _ = setup
    original = b'{"permission": {"bash": "allow"}, "n": ' + b"9" * 5000 + b"}"
    (ws / policy_rel).write_bytes(original)

    assert register(provider, ws) is True

    assert (ws / (policy_rel + ".gamachine-backup")).read_bytes() == original
    assert_policy(module, ws, policy_rel)


SHAPES = [
    ("cursor", ".cursor/mcp.json", {"mcpServers": []}),
    ("cursor", ".cursor/mcp.json", {"mcpServers": "x"}),
    ("cursor", ".cursor/cli.json", {"permissions": "x"}),
    ("cursor", ".cursor/cli.json", {"permissions": {"deny": 5}}),
    ("cursor", ".cursor/cli.json", {"permissions": {"allow": [1, "Read(**)"], "deny": ["A(**)", None]}}),
    ("opencode", "opencode.json", {"mcp": "x"}),
    ("opencode", "opencode.json", {"mcp": []}),
    ("opencode", "opencode.json", {"permission": []}),
]


@pytest.mark.parametrize("kind,rel,content", SHAPES)
def test_wrong_typed_fields_are_normalized(tmp_path, monkeypatch, kind, rel, content):
    from providers import cursor_provider, opencode_provider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    if kind == "cursor":
        module, provider = cursor_provider, cursor_provider.CursorProvider("cursor-auto")
        policy_rel = ".cursor/cli.json"
    else:
        module, provider = opencode_provider, opencode_provider.OpenCodeProvider("opencode:opencode/x")
        monkeypatch.setattr(provider, "_prepare_bash_shadow", lambda: None)
        policy_rel = "opencode.json"
    monkeypatch.setattr(module, "ensure_gitignored", Mock())
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(json.dumps(content), encoding="utf-8")

    assert register(provider, tmp_path) is True

    assert_policy(module, tmp_path, policy_rel)
    if kind == "cursor":
        servers = json.loads((tmp_path / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        assert "unityai" in servers
        if content.get("permissions", {}) and isinstance(content["permissions"], dict) \
                and content["permissions"].get("allow"):
            written = json.loads((tmp_path / policy_rel).read_text(encoding="utf-8"))
            assert written["permissions"]["allow"] == ["Read(**)"]
            assert written["permissions"]["deny"][0] == "A(**)"
    else:
        assert "unityai" in json.loads((tmp_path / policy_rel).read_text(encoding="utf-8"))["mcp"]


def test_backup_name_taken_by_directory_uses_next_free_name(setup):
    provider, module, ws, policy_rel, _ = setup
    original = b"{broken"
    (ws / policy_rel).write_bytes(original)
    (ws / (policy_rel + ".gamachine-backup")).mkdir()

    assert register(provider, ws) is True

    assert (ws / (policy_rel + ".gamachine-backup-1")).read_bytes() == original
    assert (ws / (policy_rel + ".gamachine-backup")).is_dir()
    assert_policy(module, ws, policy_rel)
    assert policy_rel + ".gamachine-backup-1" in (ws / ".gitignore").read_text(encoding="utf-8")


@pytest.mark.skipif(not hasattr(os, "link"), reason="hardlinks unavailable")
def test_hardlinked_config_is_not_moved_and_policy_failure_is_reported(setup, tmp_path_factory):
    provider, module, ws, policy_rel, _ = setup
    outside = tmp_path_factory.mktemp("outside-link") / "victim"
    original = b'{"mcp": "outside data"'
    outside.write_bytes(original)
    os.link(outside, ws / policy_rel)

    assert workspace_config.read_user_json_config(str(ws), policy_rel) is None
    assert register(provider, ws) is False

    assert outside.read_bytes() == original
    assert (ws / policy_rel).read_bytes() == original
    assert not (ws / (policy_rel + ".gamachine-backup")).exists()


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="Directory-relative open is unavailable")
def test_file_swapped_between_read_and_rename_is_not_moved(tmp_path, monkeypatch):
    path = tmp_path / "opencode.json"
    path.write_bytes(b"{first")
    real_loads = workspace_config.json.loads
    swaps = []

    def swapping_loads(text, *args, **kwargs):
        replacement = tmp_path / "swap.tmp"
        replacement.write_bytes(b"{swapped %d" % len(swaps))
        os.replace(replacement, path)
        swaps.append(1)
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(workspace_config.json, "loads", swapping_loads)

    assert workspace_config.read_user_json_config(str(tmp_path), "opencode.json") is None

    assert path.read_bytes().startswith(b"{swapped")
    assert not (tmp_path / "opencode.json.gamachine-backup").exists()
    assert len(swaps) == 2


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="Directory-relative open is unavailable")
def test_single_swap_is_retried_and_moves_the_file_that_was_read(tmp_path, monkeypatch):
    path = tmp_path / "opencode.json"
    path.write_bytes(b"{first")
    real_loads = workspace_config.json.loads
    swaps = []

    def swapping_loads(text, *args, **kwargs):
        if not swaps:
            replacement = tmp_path / "swap.tmp"
            replacement.write_bytes(b"{second")
            os.replace(replacement, path)
            swaps.append(1)
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(workspace_config.json, "loads", swapping_loads)

    assert workspace_config.read_user_json_config(str(tmp_path), "opencode.json") == {}

    assert (tmp_path / "opencode.json.gamachine-backup").read_bytes() == b"{second"


def _drive(provider, workspace):
    async def run():
        return [event async for event in provider.analyze_code("hi", cwd=str(workspace))]
    return asyncio.run(run())


def _refused_turn(provider, module, ws, monkeypatch, policy_rel):
    """Run a full turn; assert it is refused with a message naming only the failing file."""
    from providers import cli_base

    config_writer = Mock(side_effect=REAL_WRITER)
    monkeypatch.setattr(module, "guvenli_config_yaz", config_writer)
    monkeypatch.setattr(workspace_config, "guvenli_config_yaz", config_writer)
    monkeypatch.setattr(provider, "_launcher_path", lambda name: "test-launcher")
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)
    monkeypatch.setattr(provider, "_cli_installed", lambda cmd: True)
    monkeypatch.setattr(provider, "_build_cmd", lambda *a, **k: ["fake-cli", "x"])
    spawn = Mock(side_effect=RuntimeError("spawn reached"))
    monkeypatch.setattr(cli_base.asyncio, "create_subprocess_exec", spawn)

    events = _drive(provider, ws)

    spawn.assert_not_called()
    errors = [e["content"] for e in events if e.get("type") == "error"]
    assert errors and "başlatılmadı" in errors[0]
    assert policy_rel in errors[0]
    other = "opencode.json" if policy_rel == ".cursor/cli.json" else ".cursor/cli.json"
    assert other not in errors[0]
    written = [c.args[1] for c in config_writer.call_args_list]
    assert policy_rel not in written
    return written


def test_regular_backup_is_never_overwritten(setup):
    provider, module, ws, policy_rel, _ = setup
    original = b"{broken"
    (ws / policy_rel).write_bytes(original)
    older = ws / (policy_rel + ".gamachine-backup")
    older.write_bytes(b"older backup")

    assert register(provider, ws) is True

    assert older.read_bytes() == b"older backup"
    assert (ws / (policy_rel + ".gamachine-backup-1")).read_bytes() == original
    assert_policy(module, ws, policy_rel)


def test_all_backup_names_taken_refuses_the_turn(setup, monkeypatch):
    provider, module, ws, policy_rel, _ = setup
    original = b"{broken"
    (ws / policy_rel).write_bytes(original)
    suffixes = [".gamachine-backup"] + [f".gamachine-backup-{n}" for n in range(1, 21)]
    for suffix in suffixes:
        (ws / (policy_rel + suffix)).write_bytes(b"occupied " + suffix.encode())

    assert workspace_config.read_user_json_config(str(ws), policy_rel) is None
    written = _refused_turn(provider, module, ws, monkeypatch, policy_rel)

    assert (ws / policy_rel).read_bytes() == original
    for suffix in suffixes:
        assert (ws / (policy_rel + suffix)).read_bytes() == b"occupied " + suffix.encode()
    assert not (ws / (policy_rel + ".gamachine-backup-21")).exists()
    assert policy_rel not in written


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_fifo_policy_file_refuses_registration_quickly(setup, monkeypatch):
    provider, module, ws, policy_rel, _ = setup
    os.mkfifo(ws / policy_rel)
    result = []
    thread = threading.Thread(target=lambda: result.append(register(provider, ws)), daemon=True)
    thread.start()
    thread.join(2)

    assert not thread.is_alive(), "registration blocked on the FIFO"
    assert result == [False]
    _refused_turn(provider, module, ws, monkeypatch, policy_rel)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_guvenli_config_yaz_does_not_block_on_a_fifo(tmp_path):
    os.mkfifo(tmp_path / "target.json")
    result = []
    thread = threading.Thread(
        target=lambda: result.append(REAL_WRITER(str(tmp_path), "target.json", "{}")), daemon=True)
    thread.start()
    thread.join(2)

    assert not thread.is_alive(), "guvenli_config_yaz blocked on the FIFO"
    assert result == [False]


def test_symlinked_policy_file_refuses_the_turn(setup, tmp_path_factory, monkeypatch):
    provider, module, ws, policy_rel, _ = setup
    outside = tmp_path_factory.mktemp("outside-policy") / "victim"
    outside.write_bytes(b'{"permission": {"bash": "allow"}}')
    (ws / policy_rel).symlink_to(outside)

    written = _refused_turn(provider, module, ws, monkeypatch, policy_rel)

    assert (ws / policy_rel).is_symlink()
    assert outside.read_bytes() == b'{"permission": {"bash": "allow"}}'
    assert written == [".mcp.json"]


@pytest.mark.skipif(not hasattr(os, "link"), reason="hardlinks unavailable")
def test_hardlinked_policy_file_refuses_the_turn(setup, tmp_path_factory, monkeypatch):
    provider, module, ws, policy_rel, _ = setup
    outside = tmp_path_factory.mktemp("outside-hard") / "victim"
    original = b'{"permission": {"bash": "allow"}}'
    outside.write_bytes(original)
    os.link(outside, ws / policy_rel)

    written = _refused_turn(provider, module, ws, monkeypatch, policy_rel)

    assert outside.read_bytes() == original
    assert (ws / policy_rel).read_bytes() == original
    assert written == [".mcp.json"]
    assert not list(ws.glob("*gamachine-backup*"))


@pytest.mark.parametrize("policy_fails", [True, False])
def test_policy_write_failure_never_spawns_the_cli(setup, monkeypatch, policy_fails):
    provider, module, ws, policy_rel, _ = setup
    from providers import cli_base

    def writer(workspace, rel, content, **kwargs):
        if policy_fails and rel == policy_rel:
            return False
        return REAL_WRITER(workspace, rel, content, **kwargs)

    monkeypatch.setattr(module, "guvenli_config_yaz", writer)
    monkeypatch.setattr(workspace_config, "guvenli_config_yaz", writer)
    monkeypatch.setattr(provider, "_launcher_path", lambda name: "test-launcher")
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)
    monkeypatch.setattr(provider, "_cli_installed", lambda cmd: True)
    monkeypatch.setattr(provider, "_build_cmd", lambda *a, **k: ["fake-cli", "x"])
    spawn = Mock(side_effect=RuntimeError("spawn reached"))
    monkeypatch.setattr(cli_base.asyncio, "create_subprocess_exec", spawn)

    events = _drive(provider, ws)

    errors = [e for e in events if e.get("type") == "error"]
    assert errors
    if policy_fails:
        spawn.assert_not_called()
        assert "başlatılmadı" in errors[0]["content"]
    else:
        spawn.assert_called()


@pytest.mark.parametrize("url,expected", [
    ("https://remote.example/mcp?token=" + SECRET, False),
    ("https://remote.example/docs/" + SECRET, False),
    ("http://localhost.evil.example/mcp?k=" + SECRET, False),
    ("http://localhost:8080/mcp?k=" + SECRET + "-extra", False),
    ("http://localhost:8080/mcp", False),
    ("http://localhost:8080/mcp?key=" + SECRET, True),
    ("http://127.0.0.1:8080/mcp?a=1&key=" + SECRET, True),
    ("http://[::1]:8080/mcp?key=" + SECRET, True),
    ("http://localhost:8080/mcp/" + SECRET, True),
])
def test_url_ownership_needs_loopback_and_exact_token(url, expected):
    from agentic.agent_runner import _urunun_kaydi_mi

    assert _urunun_kaydi_mi("entry", {"url": url}, SECRET) is expected
