import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest

from unity_ai_mcp import unity_mcp_manager as module


@pytest.fixture
def resources(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    home = tmp_path / "home"
    for sub, marker in (("Server", "pyproject.toml"), ("MCPForUnity", "package.json")):
        source = root / "unity-mcp" / sub
        source.mkdir(parents=True)
        (source / marker).write_text("same version", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(module.os.path, "expanduser", lambda _: str(home))
    manager = module.UnityMCPManager()
    manager.project_root = str(root)
    return manager, root, home / ".unity_architect_ai" / "unity-mcp"


def test_refreshes_added_file_without_marker_mtime_change(resources):
    manager, root, destination = resources
    manager._ensure_writable_resources()
    source = root / "unity-mcp" / "MCPForUnity"
    marker_mtime = (source / "package.json").stat().st_mtime_ns
    (source / "CompileStatus.cs").write_text("new bridge", encoding="utf-8")
    manager._ensure_writable_resources()
    assert (source / "package.json").stat().st_mtime_ns == marker_mtime
    assert (destination / "MCPForUnity" / "CompileStatus.cs").read_text() == "new bridge"
    assert manager.local_mcp_source == str(destination / "MCPForUnity")
    assert manager.unity_mcp_repo == f"file:{manager.local_mcp_source}"


def test_matching_digest_does_not_copy(resources, monkeypatch):
    manager, _, destination = resources
    manager._ensure_writable_resources()
    generated = destination / "MCPForUnity" / "build"
    generated.mkdir()
    sentinel = generated / "user-sentinel"
    sentinel.write_text("keep", encoding="utf-8")

    def unexpected_copy(*args, **kwargs):
        pytest.fail("Matching complete resources must not be copied")

    monkeypatch.setattr(shutil, "copytree", unexpected_copy)
    manager._ensure_writable_resources()
    assert sentinel.read_text() == "keep"


def test_missing_digest_refreshes_existing_copy(resources):
    manager, _, destination = resources
    bridge = destination / "MCPForUnity"
    bridge.mkdir(parents=True)
    (bridge / "package.json").write_text("old copy", encoding="utf-8")
    manager._ensure_writable_resources()
    assert (bridge / "package.json").read_text() == "same version"
    assert (bridge / ".gamachine-source-digest").is_file()


@pytest.mark.parametrize("fail_at", [1, 2])
def test_failed_swap_preserves_old_destination(resources, monkeypatch, fail_at):
    manager, root, destination = resources
    manager._ensure_writable_resources()
    bridge = destination / "MCPForUnity"
    (bridge / "old-sentinel").write_text("usable", encoding="utf-8")
    (bridge / ".gamachine-source-digest").write_text(module._tree_digest(bridge), encoding="utf-8")
    (root / "unity-mcp" / "MCPForUnity" / "new.cs").write_text("changed", encoding="utf-8")
    original = os.replace
    calls = 0

    def fail_swap(source, target):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("swap denied")
        return original(source, target)

    monkeypatch.setattr(module.os, "replace", fail_swap)
    manager._ensure_writable_resources()
    assert (bridge / "old-sentinel").read_text() == "usable"
    assert manager.local_mcp_source == str(bridge)
    assert not Path(str(bridge) + ".new").exists()


def seal(path):
    (path / ".gamachine-source-digest").write_text(module._tree_digest(path), encoding="utf-8")


@pytest.mark.parametrize("incomplete", [False, True])
def test_recovers_newest_complete_backup_before_failed_refresh(resources, monkeypatch, incomplete):
    manager, root, destination = resources
    bridge = destination / "MCPForUnity"
    for name, content in ((".old-1", "older"), (".old-2", "newest"), (".old-3", "broken")):
        backup = Path(str(bridge) + name)
        backup.mkdir(parents=True)
        (backup / "package.json").write_text(content)
        seal(backup)
        os.utime(backup, ns=(int(name[-1]) * 1000000000,) * 2)
        if content == "broken":
            (backup / "partial").write_text("incomplete")
    if incomplete:
        bridge.mkdir()
        (bridge / "partial").write_text("incomplete")
    original = shutil.copytree

    def fail_stage(source, target, *args, **kwargs):
        if str(source) == str(root / "unity-mcp" / "MCPForUnity"):
            raise OSError("disk full")
        return original(source, target, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", fail_stage)
    manager._ensure_writable_resources()
    assert (bridge / "package.json").read_text() == "newest"
    assert manager.local_mcp_source == str(bridge)
    assert (Path(str(bridge) + ".old-1") / "package.json").read_text() == "older"


def test_abrupt_exit_between_renames_is_recovered_on_next_run(resources, monkeypatch):
    manager, root, destination = resources
    manager._ensure_writable_resources()
    bridge = destination / "MCPForUnity"
    (root / "unity-mcp" / "MCPForUnity" / "new.cs").write_text("changed")
    original = os.replace

    class AbruptExit(BaseException):
        pass

    def crash(source, target):
        if str(source).startswith(str(bridge) + ".new-"):
            raise AbruptExit()
        return original(source, target)

    monkeypatch.setattr(module.os, "replace", crash)
    with pytest.raises(AbruptExit):
        manager._ensure_writable_resources()
    assert not bridge.exists()
    assert list(destination.glob("MCPForUnity.old-*"))

    def deny_promotion(source, target):
        if str(source).startswith(str(bridge) + ".new-"):
            raise OSError("promotion denied")
        return original(source, target)

    monkeypatch.setattr(module.os, "replace", deny_promotion)
    manager._ensure_writable_resources()
    assert (bridge / "package.json").read_text() == "same version"
    assert manager.local_mcp_source == str(bridge)


def test_failed_promotion_and_restore_keep_attribute_and_backup(resources, monkeypatch):
    manager, root, destination = resources
    manager._ensure_writable_resources()
    bridge = destination / "MCPForUnity"
    manager.local_mcp_source = "unchanged-source"
    (root / "unity-mcp" / "MCPForUnity" / "new.cs").write_text("changed")
    original = os.replace
    copied = []
    original_copy = shutil.copytree

    def deny(source, target):
        if str(source).startswith((str(bridge) + ".new-", str(bridge) + ".old-")):
            raise OSError("denied")
        return original(source, target)

    def track_copy(source, target, *args, **kwargs):
        copied.append(str(source))
        return original_copy(source, target, *args, **kwargs)

    monkeypatch.setattr(module.os, "replace", deny)
    monkeypatch.setattr(shutil, "copytree", track_copy)
    manager._ensure_writable_resources()
    assert manager.local_mcp_source == "unchanged-source"
    assert not bridge.exists()
    assert any((backup / "package.json").read_text() == "same version"
               for backup in destination.glob("MCPForUnity.old-*"))
    assert not any(".old-" in path for path in copied)


def test_undeletable_leftovers_are_logged_without_preventing_refresh(resources, monkeypatch, caplog):
    manager, root, destination = resources
    manager._ensure_writable_resources()
    bridge = destination / "MCPForUnity"
    (root / "unity-mcp" / "MCPForUnity" / "new.cs").write_text("changed")
    leftovers = [Path(str(bridge) + suffix) for suffix in (".old-locked", ".new-locked")]
    for path in leftovers:
        path.mkdir()
        (path / "locked").write_text("read only")
    original = shutil.rmtree
    retried = []

    def deny(path, *args, **kwargs):
        if Path(path) in leftovers:
            def locked(target):
                retried.append(target)
                raise PermissionError("still locked")
            kwargs["onerror"](locked, str(Path(path) / "locked"), (PermissionError, PermissionError("read only"), None))
            return
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", deny)
    manager._ensure_writable_resources()
    assert (bridge / "new.cs").read_text() == "changed"
    assert len(retried) == 2
    assert all(path.exists() for path in leftovers)
    assert "still locked" in caplog.text


def test_read_only_leftover_is_removed_after_retry(resources):
    import stat

    manager, root, destination = resources
    manager._ensure_writable_resources()
    leftover = destination / "MCPForUnity.new-readonly"
    leftover.mkdir()
    locked = leftover / "locked"
    locked.write_text("read only")
    locked.chmod(stat.S_IREAD)
    try:
        (root / "unity-mcp" / "MCPForUnity" / "new.cs").write_text("changed")
        manager._ensure_writable_resources()
        assert not leftover.exists()
        assert (destination / "MCPForUnity" / "new.cs").read_text() == "changed"
    finally:
        if locked.exists():
            locked.chmod(stat.S_IWRITE)


def test_tree_digest_order_paths_bytes_and_generated_exclusions(tmp_path):
    (tmp_path / "z").write_bytes(b"last")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").write_bytes(b"first")
    for parent in (tmp_path, tmp_path / "a"):
        for name in ("__pycache__", "generated.egg-info", "build", ".venv"):
            (parent / name).mkdir()
            (parent / name / "ignored").write_bytes(b"noise")
    (tmp_path / ".gamachine-source-digest").write_bytes(b"ignored")
    expected = hashlib.sha256(b"a/b\0firstz\0last").hexdigest()
    assert module._tree_digest(tmp_path) == expected


def test_non_frozen_does_not_copy(resources, monkeypatch):
    manager, _, destination = resources
    monkeypatch.setattr(sys, "frozen", False)
    manager._ensure_writable_resources()
    assert not destination.exists()


@pytest.mark.parametrize("damage", ["missing", "changed", "extra"])
def test_stored_digest_does_not_hide_destination_damage(resources, damage):
    manager, _, destination = resources
    manager._ensure_writable_resources()
    bridge = destination / "MCPForUnity"
    if damage == "missing":
        (bridge / "package.json").unlink()
    elif damage == "changed":
        (bridge / "package.json").write_text("damaged")
    else:
        (bridge / "unexpected.cs").write_text("damaged")
    manager._ensure_writable_resources()
    assert (bridge / "package.json").read_text() == "same version"
    assert not (bridge / "unexpected.cs").exists()
    assert (bridge / ".gamachine-source-digest").read_text() == module._tree_digest(bridge)
    assert manager.local_mcp_source == str(bridge)


def test_incomplete_destination_failed_refresh_leaves_attribute_unchanged(resources, monkeypatch):
    manager, _, destination = resources
    manager._ensure_writable_resources()
    bridge = destination / "MCPForUnity"
    (bridge / "package.json").write_text("damaged")
    manager.local_mcp_source = "unchanged-source"

    def deny(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", deny)
    manager._ensure_writable_resources()
    assert manager.local_mcp_source == "unchanged-source"
    assert manager.unity_mcp_repo == "file:unchanged-source"


@pytest.mark.parametrize("missing_source", [False, True])
def test_started_server_backup_recovers_before_bundle_check(resources, monkeypatch, missing_source):
    manager, root, destination = resources
    manager._ensure_writable_resources()
    server = destination / "Server"
    generated = server / "build" / "lib"
    generated.mkdir(parents=True)
    (generated / "built.py").write_text("generated")
    backup = Path(str(server) + ".old-started")
    os.replace(server, backup)
    source = root / "unity-mcp" / "Server"
    if missing_source:
        os.replace(source, root / "unavailable-server")
    else:
        (source / "new.py").write_text("new version")

        def deny(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(shutil, "copytree", deny)
    manager.server_dir = "unchanged-source"
    manager._ensure_writable_resources()
    assert manager.server_dir == str(server)
    assert (server / "pyproject.toml").read_text() == "same version"
    assert (server / "build" / "lib" / "built.py").read_text() == "generated"


def test_started_server_retained_after_failed_promotion(resources, monkeypatch):
    manager, root, destination = resources
    manager._ensure_writable_resources()
    server = destination / "Server"
    (server / "build").mkdir()
    (server / "build" / "built.py").write_text("generated")
    (root / "unity-mcp" / "Server" / "new.py").write_text("new version")
    replace = os.replace

    def deny_promotion(source, target):
        if str(source).startswith(str(server) + ".new-"):
            raise OSError("promotion denied")
        return replace(source, target)

    monkeypatch.setattr(module.os, "replace", deny_promotion)
    manager.server_dir = "unchanged-source"
    manager._ensure_writable_resources()
    assert manager.server_dir == str(server)
    assert (server / "build" / "built.py").read_text() == "generated"
    assert not (server / "new.py").exists()
