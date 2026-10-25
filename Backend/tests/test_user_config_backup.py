import json
import logging
import os
import threading
from unittest.mock import Mock

import pytest

from providers import workspace_config


@pytest.fixture(params=["cursor", "opencode"])
def provider_config(request, tmp_path, monkeypatch):
    from providers import cursor_provider, opencode_provider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    if request.param == "cursor":
        module = cursor_provider
        provider = module.CursorProvider("cursor-auto")
        rel_path, servers_key = ".cursor/mcp.json", "mcpServers"
    else:
        module = opencode_provider
        provider = module.OpenCodeProvider("opencode:opencode/test-model")
        monkeypatch.setattr(provider, "_prepare_bash_shadow", lambda: None)
        rel_path, servers_key = "opencode.json", "mcp"
    monkeypatch.setattr(module, "ensure_gitignored", Mock())
    path = tmp_path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return provider, path, servers_key, rel_path, module


def register(provider, workspace):
    return provider._register_mcp("test-launcher", str(workspace), "http://localhost:8000")


def assert_no_backup_ignored(module):
    ignored = [entry for call in module.ensure_gitignored.call_args_list for entry in call.args[1]]
    assert not any("gamachine-backup" in entry for entry in ignored)


def assert_policy_not_written_from_scratch(module, config_writer, result, user_text=b""):
    """An unusable user config is never replaced by a policy-only file.

    OpenCode keeps its policy in the unusable file, so the turn is refused and nothing
    is written. Cursor keeps the policy in cli.json (readable here), so only that is written.
    """
    writes = [(call.args[1], json.loads(call.args[2])) for call in config_writer.call_args_list]
    if module.__name__.endswith("cursor_provider"):
        assert [rel for rel, _ in writes] == [".cursor/cli.json"]
        assert writes[0][1]["permissions"]["deny"] == ["Write(**)", "Shell(**)"]
    else:
        assert writes == []
        assert result is False
    if user_text:
        assert all(user_text.decode() not in call.args[2] for call in config_writer.call_args_list)


def test_missing_config_is_created(provider_config, tmp_path):
    provider, path, servers_key, rel_path, _ = provider_config
    assert workspace_config.read_user_json_config(str(tmp_path), rel_path) == {}

    register(provider, tmp_path)

    assert "unityai" in json.loads(path.read_text(encoding="utf-8"))[servers_key]
    assert not path.with_name(path.name + ".gamachine-backup").exists()


def test_user_dict_is_merged_and_preserved(provider_config, tmp_path):
    provider, path, servers_key, _, _ = provider_config
    user_server = {"command": "my-server", "args": ["custom"]}
    path.write_text(json.dumps({servers_key: {"my-server": user_server},
                                "custom-setting": "keep me"}), encoding="utf-8")

    register(provider, tmp_path)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written[servers_key]["my-server"] == user_server
    assert "unityai" in written[servers_key]
    assert written["custom-setting"] == "keep me"
    assert not path.with_name(path.name + ".gamachine-backup").exists()


@pytest.mark.parametrize("original", [
    '{"mcp": "unfinished şifre"\r\n'.encode("utf-8"),
    '["user entry", "şifre"]\n'.encode("utf-8"),
    b"false", b"42", b'"user entry"',
])
def test_invalid_config_is_backed_up_before_replacement(provider_config, tmp_path, caplog, original):
    provider, path, servers_key, rel_path, _ = provider_config
    path.write_bytes(original)
    older = path.with_name(path.name + ".gamachine-backup")
    older.write_text("older backup", encoding="utf-8")
    backup = path.with_name(path.name + ".gamachine-backup-1")

    with caplog.at_level(logging.WARNING):
        register(provider, tmp_path)

    assert older.read_bytes() == b"older backup"
    assert backup.read_bytes() == original
    assert rel_path + ".gamachine-backup-1" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "unityai" in json.loads(path.read_text(encoding="utf-8"))[servers_key]
    assert any(str(backup) in record.message and record.levelno == logging.WARNING
               for record in caplog.records)


@pytest.mark.parametrize("original", [
    b'{"user": "\xff"}',
    json.dumps({"permission": {"bash": "allow", "edit": "allow"}}).encode("utf-16"),
    b"x" * (1024 * 1024 + 1),
    b"[" * 2000 + b"]" * 2000,
], ids=["non-utf8", "utf16-permissions", "oversized", "deep-json"])
def test_unusable_regular_config_is_renamed_and_replaced(provider_config, tmp_path, original):
    provider, path, servers_key, _, _ = provider_config
    path.write_bytes(original)
    original_inode = path.stat().st_ino

    register(provider, tmp_path)

    backup = path.with_name(path.name + ".gamachine-backup")
    assert backup.read_bytes() == original
    assert backup.stat().st_ino == original_inode
    written = json.loads(path.read_text(encoding="utf-8"))
    assert "unityai" in written[servers_key]
    if servers_key == "mcp":
        assert written["permission"]["bash"] == "deny"
        assert written["permission"]["edit"] == "deny"


def test_backup_failure_leaves_original_untouched(provider_config, tmp_path, monkeypatch, caplog):
    provider, path, _, _, module = provider_config
    original = b"{unfinished"
    path.write_bytes(original)
    backup_rename = Mock(side_effect=PermissionError("rename denied"))
    config_writer = Mock()
    monkeypatch.setattr(os, "rename", backup_rename)
    monkeypatch.setattr(os, "replace", backup_rename)
    monkeypatch.setattr(module, "guvenli_config_yaz", config_writer)

    with caplog.at_level(logging.ERROR):
        result = register(provider, tmp_path)

    assert path.read_bytes() == original
    assert backup_rename.call_count == 1
    assert str(backup_rename.call_args.args[1]).endswith(".gamachine-backup")
    assert_no_backup_ignored(module)
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert_policy_not_written_from_scratch(module, config_writer, result)


def test_unreadable_config_is_untouched(provider_config, tmp_path, monkeypatch):
    provider, path, _, rel_path, module = provider_config
    original = b'{"user": "keep"}'
    path.write_bytes(original)
    config_writer = Mock()
    monkeypatch.setattr(module, "guvenli_config_yaz", config_writer)
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", Mock(side_effect=PermissionError("read denied")))
        assert workspace_config.read_user_json_config(str(tmp_path), rel_path) is None
        result = register(provider, tmp_path)

    assert path.read_bytes() == original
    assert config_writer.call_count == 0
    assert result is False
    assert_no_backup_ignored(module)


@pytest.mark.parametrize("original", [b"", b" \r\n\t", b"null", b"  null\n"])
def test_empty_or_null_config_needs_no_backup(provider_config, tmp_path, original):
    provider, path, servers_key, rel_path, _ = provider_config
    path.write_bytes(original)

    assert workspace_config.read_user_json_config(str(tmp_path), rel_path) == {}
    register(provider, tmp_path)

    assert "unityai" in json.loads(path.read_text(encoding="utf-8"))[servers_key]
    assert not path.with_name(path.name + ".gamachine-backup").exists()


def test_symlink_config_cannot_disclose_outside_file(provider_config, tmp_path, tmp_path_factory, monkeypatch):
    provider, path, _, rel_path, module = provider_config
    outside = tmp_path_factory.mktemp("outside") / "secret"
    original = b"private non-JSON content"
    outside.write_bytes(original)
    path.symlink_to(outside)
    config_writer = Mock()
    monkeypatch.setattr(module, "guvenli_config_yaz", config_writer)

    assert workspace_config.read_user_json_config(str(tmp_path), rel_path) is None
    result = register(provider, tmp_path)

    assert path.is_symlink()
    assert outside.read_bytes() == original
    assert not path.with_name(path.name + ".gamachine-backup").exists()
    assert_policy_not_written_from_scratch(module, config_writer, result, original)
    assert_no_backup_ignored(module)


@pytest.mark.parametrize("rel_path", [".cursor/mcp.json", "nested/opencode.json"])
def test_redirected_parent_is_rejected(tmp_path, tmp_path_factory, rel_path):
    outside = tmp_path_factory.mktemp("outside-parent")
    path = tmp_path / rel_path
    path.parent.symlink_to(outside, target_is_directory=True)
    outside_config = outside / path.name
    original = b"private non-JSON content"
    outside_config.write_bytes(original)

    assert workspace_config.read_user_json_config(str(tmp_path), rel_path) is None

    assert outside_config.read_bytes() == original
    assert list(outside.iterdir()) == [outside_config]
    assert not (tmp_path / ".gitignore").exists()


def test_cursor_does_not_write_through_redirected_parent(tmp_path, tmp_path_factory, monkeypatch):
    from providers import cursor_provider

    outside = tmp_path_factory.mktemp("outside-cursor")
    (tmp_path / ".cursor").symlink_to(outside, target_is_directory=True)
    config_writer = Mock()
    monkeypatch.setattr(cursor_provider, "guvenli_config_yaz", config_writer)
    monkeypatch.setattr(cursor_provider, "ensure_gitignored", Mock())

    assert register(cursor_provider.CursorProvider("cursor-auto"), tmp_path) is False

    assert config_writer.call_count == 0
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_unsafe_config_is_not_read_or_replaced(provider_config, tmp_path, monkeypatch, kind):
    provider, path, _, rel_path, module = provider_config
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO creation is unavailable")
        os.mkfifo(path)
    original_stat = path.stat()
    config_writer = Mock()
    monkeypatch.setattr(module, "guvenli_config_yaz", config_writer)

    assert workspace_config.read_user_json_config(str(tmp_path), rel_path) is None
    result = register(provider, tmp_path)

    assert path.stat() == original_stat
    assert not path.with_name(path.name + ".gamachine-backup").exists()
    assert_policy_not_written_from_scratch(module, config_writer, result)
    assert_no_backup_ignored(module)


def test_bom_object_is_read_without_backup(tmp_path):
    path = tmp_path / "opencode.json"
    path.write_bytes(b'\xef\xbb\xbf{"custom": "keep"}')

    assert workspace_config.read_user_json_config(str(tmp_path), path.name) == {"custom": "keep"}
    assert not path.with_name(path.name + ".gamachine-backup").exists()


@pytest.mark.parametrize("cli_config", [
    b"{invalid",
    b'["not an object"]',
    b'{"user": "\xff"}',
])
@pytest.mark.parametrize("provider_config", ["cursor"], indirect=True)
def test_cursor_cli_invalid_config_is_renamed(provider_config, tmp_path, cli_config):
    provider, path, _, _, _ = provider_config
    cli_path = path.with_name("cli.json")
    cli_path.write_bytes(cli_config)

    register(provider, tmp_path)

    assert cli_path.with_name("cli.json.gamachine-backup").read_bytes() == cli_config
    deny = json.loads(cli_path.read_text(encoding="utf-8"))["permissions"]["deny"]
    assert "Write(**)" in deny
    assert "Shell(**)" in deny
    assert ".cursor/cli.json.gamachine-backup" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


@pytest.mark.parametrize("provider_config", ["cursor"], indirect=True)
def test_cursor_cli_valid_config_preserves_custom_rules(provider_config, tmp_path):
    provider, path, _, _, _ = provider_config
    cli_path = path.with_name("cli.json")
    cli_path.write_text(json.dumps({"custom": True, "permissions": {
        "allow": ["Read(**)"], "deny": ["Custom(**)"]}}), encoding="utf-8")

    register(provider, tmp_path)

    written = json.loads(cli_path.read_text(encoding="utf-8"))
    assert written["custom"] is True
    assert written["permissions"] == {
        "allow": ["Read(**)"], "deny": ["Custom(**)", "Write(**)", "Shell(**)"]}


@pytest.mark.parametrize("provider_config", ["cursor"], indirect=True)
def test_cursor_cli_unsafe_target_is_not_written(provider_config, tmp_path, tmp_path_factory, monkeypatch):
    provider, path, _, _, module = provider_config
    outside = tmp_path_factory.mktemp("outside-cli") / "secret"
    outside.write_bytes(b"outside secret")
    cli_path = path.with_name("cli.json")
    cli_path.symlink_to(outside)
    config_writer = Mock(return_value=True)
    monkeypatch.setattr(module, "guvenli_config_yaz", config_writer)

    assert register(provider, tmp_path) is False

    assert config_writer.call_count == 0
    assert cli_path.is_symlink()
    assert outside.read_bytes() == b"outside secret"
    assert not cli_path.with_name("cli.json.gamachine-backup").exists()


@pytest.mark.parametrize("rel_path", ["../escape.json", "nested/../escape.json"])
def test_parent_traversal_is_rejected(tmp_path, rel_path):
    assert workspace_config.read_user_json_config(str(tmp_path), rel_path) is None


def test_absolute_config_path_is_rejected(tmp_path):
    assert workspace_config.read_user_json_config(str(tmp_path), str(tmp_path / "config.json")) is None


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="Directory-relative open is unavailable")
@pytest.mark.parametrize("outside_bytes", [b"outside secret", b'{"outside": "secret"}'])
def test_parent_swap_never_reads_or_moves_outside_config(tmp_path, tmp_path_factory, outside_bytes):
    parent = tmp_path / "config"
    parent.mkdir()
    parked = tmp_path / "parked"
    inside_bytes = b'{"inside": true}'
    (parent / "config.json").write_bytes(inside_bytes)
    outside = tmp_path_factory.mktemp("outside-race")
    outside_file = outside / "config.json"
    outside_file.write_bytes(outside_bytes)
    outside_stat = outside_file.stat()
    stop = threading.Event()
    ready = threading.Event()
    errors = []

    def flip_parent():
        try:
            for _ in range(10000):
                if stop.is_set():
                    break
                parent.rename(parked)
                try:
                    parent.symlink_to(outside, target_is_directory=True)
                    ready.set()
                finally:
                    if parent.is_symlink():
                        parent.unlink()
                    parked.rename(parent)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=flip_parent)
    thread.start()
    try:
        assert ready.wait(5)
        for _ in range(200):
            result = workspace_config.read_user_json_config(str(tmp_path), "config/config.json")
            assert result is None or result in ({}, {"inside": True})
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    assert outside_file.stat() == outside_stat
    assert outside_file.read_bytes() == outside_bytes
    assert list(outside.iterdir()) == [outside_file]
    assert (parent / "config.json").read_bytes() == inside_bytes
    for backup in tmp_path.rglob("*.gamachine-backup"):
        assert backup.read_bytes() != outside_bytes
