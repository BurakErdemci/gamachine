"""Cached uvx launches stay fresh through source and package-data cache keys."""

import fnmatch
import os
from pathlib import Path, PurePath
import tomllib

import pytest

from unity_ai_mcp import unity_mcp_manager as um


SERVER = Path(__file__).resolve().parents[2] / "unity-mcp" / "Server"


@pytest.fixture
def server_config():
    with (SERVER / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


@pytest.mark.parametrize("marker_content", [None, "", "old-source-hash", "arbitrary content\n"])
def test_launch_always_uses_cache(monkeypatch, tmp_path, marker_content):
    """An absent or stale legacy marker must never disable dependency caching."""
    from unity_ai_mcp import mcp_port_guard

    marker = tmp_path / ".unity_architect_ai" / "mcp_server_src.hash"
    if marker_content is not None:
        marker.parent.mkdir()
        marker.write_text(marker_content, encoding="utf-8")
    else:
        assert not marker.exists()

    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1))
    monkeypatch.setattr(mcp_port_guard, "foreign_port_owner_infos", lambda port: [])
    monkeypatch.setattr(um, "probe_port_identity",
                        lambda port: um.Occupant(um.ServerIdentity.NONE, "faked in test"))
    monkeypatch.setattr(um.UnityMCPManager, "is_running", lambda self: False)
    monkeypatch.setattr(um.UnityMCPManager, "_ensure_writable_resources", lambda self: None)
    monkeypatch.setattr(um.UnityMCPManager, "_get_uvx", lambda self: "uvx")
    monkeypatch.setattr(um.UnityMCPManager, "_lock_constraints", lambda self, uvx: None)
    box = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            box["cmd"] = cmd
            self.pid = 4242

    monkeypatch.setattr(um.subprocess, "Popen", _FakePopen)
    assert um.UnityMCPManager().start_server() is True
    assert box["cmd"][0] == "uvx"
    assert "mcp-for-unity" in box["cmd"]
    assert "--no-cache" not in box["cmd"]
    if marker_content is None:
        assert not marker.exists()
    else:
        assert marker.read_text(encoding="utf-8") == marker_content


def test_cache_keys_cover_source_and_build_inputs(server_config):
    keys = {key["file"] for key in server_config["tool"]["uv"]["cache-keys"] if "file" in key}
    assert {"pyproject.toml", "uv.lock", "src/**/*.py", "src/**/*.json"} <= keys


def test_all_packaged_data_files_have_a_cache_key(server_config):
    """New package-data types must opt into uv's build invalidation too."""
    keys = [key["file"] for key in server_config["tool"]["uv"]["cache-keys"] if "file" in key]
    source = SERVER / "src"
    matched = set()
    for package, patterns in server_config["tool"]["setuptools"]["package-data"].items():
        if package in ("", "*"):
            package_dirs = [source, *(path for path in source.rglob("*") if path.is_dir())]
        else:
            package_dirs = list(source.glob(package.replace(".", "/")))
        for package_dir in package_dirs:
            for pattern in patterns:
                for path in package_dir.glob(pattern):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(SERVER).as_posix()
                    matched.add(relative)
                    full_match = getattr(PurePath(relative), "full_match", None)
                    assert any(
                        full_match(key) if full_match else fnmatch.fnmatchcase(relative, key)
                        for key in keys
                    ), f"Packaged file has no uv cache key: {relative}"
    assert "src/services/registry/tool_actions.json" in matched
