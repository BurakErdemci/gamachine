import hashlib
import os
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


def test_matching_digest_does_not_copy(resources):
    manager, _, destination = resources
    manager._ensure_writable_resources()
    sentinel = destination / "MCPForUnity" / "user-sentinel"
    sentinel.write_text("keep", encoding="utf-8")
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
    (root / "unity-mcp" / "MCPForUnity" / "new.cs").write_text("changed", encoding="utf-8")
    original = os.replace
    calls = 0

    def fail_swap(source, target):
        nonlocal calls
        calls += 1
        if calls >= fail_at:
            raise OSError("swap denied")
        return original(source, target)

    monkeypatch.setattr(module.os, "replace", fail_swap)
    manager._ensure_writable_resources()
    assert (bridge / "old-sentinel").read_text() == "usable"
    assert manager.local_mcp_source == str(bridge)
    assert not Path(str(bridge) + ".new").exists()


def test_tree_digest_order_paths_bytes_and_generated_exclusions(tmp_path):
    (tmp_path / "z").write_bytes(b"last")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").write_bytes(b"first")
    for name in ("__pycache__", "generated.egg-info"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "ignored").write_bytes(b"noise")
    (tmp_path / ".gamachine-source-digest").write_bytes(b"ignored")
    expected = hashlib.sha256(b"a/b\0firstz\0last").hexdigest()
    assert module._tree_digest(tmp_path) == expected


def test_non_frozen_does_not_copy(resources, monkeypatch):
    manager, _, destination = resources
    monkeypatch.setattr(sys, "frozen", False)
    manager._ensure_writable_resources()
    assert not destination.exists()
