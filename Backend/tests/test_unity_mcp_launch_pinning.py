"""Unity MCP server launch: pinned to uv.lock, no update check (P3).

`uvx --from <dir>` ignored uv.lock, so everything but the two exact pyproject
pins floated; and FastMCP phoned pypi.org for a newer release on every start.
"""
import os
import re
import shutil
import tomllib

import pytest

from unity_ai_mcp import unity_mcp_manager as um


@pytest.fixture
def launch(monkeypatch, tmp_path):
    """Drives start_server() without the real home, port or process."""
    from unity_ai_mcp import mcp_port_guard

    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1))
    monkeypatch.setattr(mcp_port_guard, "foreign_port_owner_infos", lambda port: [])
    monkeypatch.setattr(um, "probe_port_identity",
                        lambda port: um.Occupant(um.ServerIdentity.NONE, "faked in test"))
    monkeypatch.setattr(um.UnityMCPManager, "is_running", lambda self: False)
    monkeypatch.setattr(um.UnityMCPManager, "_ensure_writable_resources", lambda self: None)
    monkeypatch.setattr(um.UnityMCPManager, "_get_uvx", lambda self: "uvx")
    monkeypatch.setattr(um.UnityMCPManager, "_server_source_changed", lambda self: False)
    box = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            box["cmd"], box["env"] = cmd, kwargs.get("env")
            self.pid = 4242

    monkeypatch.setattr(um.subprocess, "Popen", _FakePopen)
    return um.UnityMCPManager(), box, monkeypatch


def test_fastmcp_update_check_is_off(launch):
    manager, box, mp = launch
    mp.setattr(um.UnityMCPManager, "_lock_constraints", lambda self, uvx: None)
    assert manager.start_server() is True
    assert box["env"]["FASTMCP_CHECK_FOR_UPDATES"] == "off"


def test_lock_constraints_go_before_the_uvx_command(launch):
    manager, box, mp = launch
    mp.setattr(um.UnityMCPManager, "_lock_constraints", lambda self, uvx: "/x/c.txt")
    assert manager.start_server() is True
    cmd = box["cmd"]
    at = cmd.index("--constraints")
    assert cmd[at + 1] == "/x/c.txt"
    assert at < cmd.index("--from") < cmd.index("mcp-for-unity")


def test_a_failed_lock_export_still_launches_unpinned(launch):
    manager, box, mp = launch

    def _fail(*args, **kwargs):
        raise FileNotFoundError("uv")

    mp.setattr(um.subprocess, "run", _fail)
    assert manager.start_server() is True
    assert "--constraints" not in box["cmd"]
    assert "mcp-for-unity" in box["cmd"]


def test_uv_is_taken_from_next_to_uvx(tmp_path):
    uv_name = "uv.exe" if os.name == "nt" else "uv"
    (tmp_path / uv_name).write_text("")
    uvx = str(tmp_path / ("uvx.exe" if os.name == "nt" else "uvx"))
    assert um.UnityMCPManager._get_uv(uvx) == str(tmp_path / uv_name)


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_the_exported_constraints_equal_the_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1))
    manager = um.UnityMCPManager()
    path = manager._lock_constraints(shutil.which("uvx") or "uvx")
    assert path is not None

    with open(os.path.join(manager.server_dir, "uv.lock"), "rb") as fh:
        lock = tomllib.load(fh)
    locked = {}
    for pkg in lock["package"]:
        locked.setdefault(_norm(pkg["name"]), set()).add(pkg["version"])

    pins = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
            assert m, f"not an exact pin: {line!r}"
            pins.setdefault(_norm(m.group(1)), set()).add(m.group(2))

    assert pins, "empty constraints file"
    for name, versions in pins.items():
        assert versions <= locked.get(name, set()), (name, versions, locked.get(name))
    for name in ("fastmcp", "mcp"):
        assert pins[name] == locked[name]
    assert "mcpforunityserver" not in pins  # the project itself is not a constraint


def test_a_lock_change_invalidates_the_cached_server(monkeypatch, tmp_path):
    server = tmp_path / "Server"
    (server / "src").mkdir(parents=True)
    (server / "src" / "main.py").write_text("x = 1\n")
    (server / "pyproject.toml").write_text("[project]\nname = 't'\n")
    (server / "uv.lock").write_text("version = 1\n")
    manager = um.UnityMCPManager()
    monkeypatch.setattr(manager, "server_dir", str(server))
    before = manager._compute_server_source_hash()
    (server / "uv.lock").write_text("version = 1\n# fastmcp 4\n")
    assert manager._compute_server_source_hash() != before
