"""Regression tests for Mac run 2 fixes: lane setup noise classification and GAMACHINE_DATA_DIR."""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import spawn_env  # noqa: E402
import unity_lanes as ul  # noqa: E402
from unity_ai_mcp import unity_mcp_manager as um  # noqa: E402

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(cwd, *args, check=True):
    res = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=GIT_ENV)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {args}: {res.stderr}")
    return res


def _make_git_project(tmp_path):
    proj = tmp_path / "UnityProj"
    (proj / "Assets" / "Editor").mkdir(parents=True)
    (proj / "Assets" / "Scenes").mkdir(parents=True)
    (proj / "ProjectSettings").mkdir()
    (proj / "Packages").mkdir()
    (proj / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.1.2f1\n", encoding="utf-8")
    (proj / "Assets" / "Editor" / "UnityArchitectAIMCPSetup.cs").write_text(
        um._LEGACY_SCRIPT_HEADER + "\n// legacy\n", encoding="utf-8"
    )
    (proj / "Assets" / "Editor" / "UnityArchitectAIMCPSetup.cs.meta").write_text("guid: legacy\n", encoding="utf-8")
    (proj / "Packages" / "manifest.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "com.coplaydev.unity-mcp": "file:/opt/gamachine/MCPForUnity",
                    "com.unity.ugui": "2.0.0",
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _git(proj, "init", "-q")
    _git(proj, "config", "user.name", "t")
    _git(proj, "config", "user.email", "t@t")
    _git(proj, "config", "core.autocrlf", "false")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "init")
    return proj


def test_classify_lane_status_treats_gamachine_lane_setup_as_tracked_noise():
    porcelain = "\n".join(
        [
            " D Assets/Editor/UnityArchitectAIMCPSetup.cs",
            " D Assets/Editor/UnityArchitectAIMCPSetup.cs.meta",
            " M Packages/manifest.json",
            "?? Assets/Scenes/LaneA.unity",
            " M Assets/Scripts/Player.cs",
        ]
    )
    tracked, untracked, dirty = ul._classify_lane_status(porcelain)
    assert tracked == [
        "Assets/Editor/UnityArchitectAIMCPSetup.cs",
        "Assets/Editor/UnityArchitectAIMCPSetup.cs.meta",
        "Packages/manifest.json",
    ]
    assert untracked == []
    assert dirty == ["Assets/Scenes/LaneA.unity", "Assets/Scripts/Player.cs"]


@pytest.mark.parametrize(
    "line,expected_path",
    [
        (" M Assets/Editor/UnityArchitectAIMCPSetup.cs", "Assets/Editor/UnityArchitectAIMCPSetup.cs"),
        (" M Assets/Editor/UnityArchitectAIMCPSetup.cs.meta", "Assets/Editor/UnityArchitectAIMCPSetup.cs.meta"),
        ("?? Assets/Editor/UnityArchitectAIMCPSetup.cs", "Assets/Editor/UnityArchitectAIMCPSetup.cs"),
        (" D Packages/manifest.json", "Packages/manifest.json"),
        ("?? Packages/manifest.json", "Packages/manifest.json"),
        ("M  Packages/manifest.json", "Packages/manifest.json"),
        ("D  Assets/Editor/UnityArchitectAIMCPSetup.cs", "Assets/Editor/UnityArchitectAIMCPSetup.cs"),
        (" D Assets/Scripts/Other.cs", "Assets/Scripts/Other.cs"),
        (" M Packages/other.json", "Packages/other.json"),
    ],
)
def test_classify_lane_status_refuses_other_assets_and_packages_changes(line, expected_path):
    tracked, untracked, dirty = ul._classify_lane_status(line)
    assert tracked == []
    assert untracked == []
    assert dirty == [expected_path]


def test_merge_lane_discards_uncommitted_gamachine_setup_and_merges_committed_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(ul, "_install_package", lambda _d: True)
    monkeypatch.setattr(ul, "_yaml_merge_exe", lambda _p: None)
    proj = _make_git_project(tmp_path)
    orig_manifest = (proj / "Packages" / "manifest.json").read_text(encoding="utf-8")

    rec = ul.create_lane(str(proj), "lane-a")
    lane_dir = rec.dir
    scene_rel = os.path.join("Assets", "Scenes", "LaneA.unity")
    os.makedirs(os.path.join(lane_dir, "Assets", "Scenes"), exist_ok=True)
    with open(os.path.join(lane_dir, scene_rel), "w", encoding="utf-8") as fh:
        fh.write("%YAML 1.1\n--- !u!1 &1\n")
    _git(lane_dir, "add", "Assets/Scenes/LaneA.unity")
    _git(lane_dir, "commit", "-q", "-m", "add scene")

    # Simulate Gamachine's own lane setup edits left uncommitted in the lane worktree.
    os.remove(os.path.join(lane_dir, "Assets", "Editor", "UnityArchitectAIMCPSetup.cs"))
    os.remove(os.path.join(lane_dir, "Assets", "Editor", "UnityArchitectAIMCPSetup.cs.meta"))
    with open(os.path.join(lane_dir, "Packages", "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write('{\n  "dependencies": {\n    "com.unity.ugui": "2.0.0"\n  }\n}\n')

    res = ul.merge_lane(str(proj), rec)
    assert res["result"] == "merged"
    assert res["files"] == ["Assets/Scenes/LaneA.unity"]

    # User's branch has the committed scene, and retains the legacy script and manifest untouched.
    assert (proj / "Assets" / "Scenes" / "LaneA.unity").is_file()
    assert (proj / "Assets" / "Editor" / "UnityArchitectAIMCPSetup.cs").is_file()
    assert (proj / "Assets" / "Editor" / "UnityArchitectAIMCPSetup.cs.meta").is_file()
    assert (proj / "Packages" / "manifest.json").read_text(encoding="utf-8") == orig_manifest
    assert _git(proj, "status", "--porcelain").stdout.strip() == ""
    assert not os.path.exists(lane_dir)


def test_gamachine_data_dir_moves_logs_and_constraints_at_call_time(tmp_path, monkeypatch):
    dir1 = tmp_path / "data-one"
    dir2 = tmp_path / "data-two"

    monkeypatch.setenv("GAMACHINE_DATA_DIR", str(dir1))
    assert um._server_log_path() == os.path.join(str(dir1), "unity_mcp_server.log")
    assert um.unity_diag_path() == os.path.join(str(dir1), "unity_mcp_diag.log")

    # Switching GAMACHINE_DATA_DIR in-process without re-importing moves paths immediately.
    monkeypatch.setenv("GAMACHINE_DATA_DIR", str(dir2))
    assert um._server_log_path() == os.path.join(str(dir2), "unity_mcp_server.log")
    assert um.unity_diag_path() == os.path.join(str(dir2), "unity_mcp_diag.log")

    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    manager = um.UnityMCPManager()
    manager.server_dir = str(server_dir)
    monkeypatch.setattr(manager, "_get_uv", lambda _uvx: "uv")

    seen = {}

    def fake_run(cmd, **kw):
        out_idx = cmd.index("--output-file") + 1
        out_file = cmd[out_idx]
        seen["out_file"] = out_file
        with open(out_file, "w", encoding="utf-8") as fh:
            fh.write("mcp==1.0.0\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(um.subprocess, "run", fake_run)
    constraints = manager._lock_constraints("uvx")
    expected_constraints = os.path.join(str(dir2), "unity_mcp_constraints.txt")
    assert constraints == expected_constraints
    assert seen["out_file"] == expected_constraints
    assert os.path.isfile(expected_constraints)


def test_gamachine_data_dir_moves_writable_resources_and_keeps_both_in_ours(tmp_path, monkeypatch):
    fake_home = tmp_path / "fake-home"
    data_dir = tmp_path / "custom-data"
    root = tmp_path / "bundle"
    for sub, marker in (("Server", "pyproject.toml"), ("MCPForUnity", "package.json")):
        src = root / "unity-mcp" / sub
        src.mkdir(parents=True)
        (src / marker).write_text("v1", encoding="utf-8")

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("GAMACHINE_DATA_DIR", str(data_dir))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    manager = um.UnityMCPManager()
    manager.project_root = str(root)
    manager._ensure_writable_resources()

    assert manager.server_dir == os.path.join(str(data_dir), "unity-mcp", "Server")
    assert manager.local_mcp_source == os.path.join(str(data_dir), "unity-mcp", "MCPForUnity")
    assert not (fake_home / ".unity_architect_ai").exists()

    # _is_our_manifest_source accepts both the GAMACHINE_DATA_DIR entry and default ~/.unity_architect_ai entry.
    default_entry = "file:" + os.path.join(str(fake_home), ".unity_architect_ai", "unity-mcp", "MCPForUnity")
    override_entry = "file:" + os.path.join(str(data_dir), "unity-mcp", "MCPForUnity")
    assert manager._is_our_manifest_source(default_entry) is True
    assert manager._is_our_manifest_source(override_entry) is True


def test_gamachine_data_dir_unset_blank_or_relative_keeps_default(tmp_path):
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
    fake_home = str(tmp_path / "fake-home")
    os.makedirs(fake_home, exist_ok=True)
    expected_dir = os.path.join(fake_home, ".unity_architect_ai")
    expected_srv = os.path.join(expected_dir, "unity_mcp_server.log")
    expected_diag = os.path.join(expected_dir, "unity_mcp_diag.log")

    code = (
        "import json, os; "
        "from unity_ai_mcp import unity_mcp_manager as m; "
        "print(json.dumps({'exp': os.path.expanduser('~'), 'srv': m._server_log_path(), 'diag': m.unity_diag_path()}))"
    )

    for val in (None, "", "   ", "relative/data-dir"):
        env = dict(os.environ, HOME=fake_home, USERPROFILE=fake_home)
        if val is None:
            env.pop("GAMACHINE_DATA_DIR", None)
        else:
            env["GAMACHINE_DATA_DIR"] = val
        res = subprocess.run([sys.executable, "-c", code], cwd=app_dir, env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout.strip())
        assert os.path.normcase(data["exp"]) == os.path.normcase(fake_home)
        assert os.path.normcase(data["srv"]) == os.path.normcase(expected_srv)
        assert os.path.normcase(data["diag"]) == os.path.normcase(expected_diag)


def test_spawn_env_passes_gamachine_data_dir_to_children(tmp_path, monkeypatch):
    assert "GAMACHINE_DATA_DIR" in spawn_env._BASE_ENV_ALLOWLIST

    data_dir = str(tmp_path / "data-dir")
    monkeypatch.setenv("GAMACHINE_DATA_DIR", data_dir)
    assert spawn_env.build_spawn_env()["GAMACHINE_DATA_DIR"] == data_dir
    assert spawn_env.build_spawn_env(family="claude")["GAMACHINE_DATA_DIR"] == data_dir
    assert spawn_env.build_spawn_env(family="uvx")["GAMACHINE_DATA_DIR"] == data_dir

    monkeypatch.delenv("GAMACHINE_DATA_DIR", raising=False)
    assert "GAMACHINE_DATA_DIR" not in spawn_env.build_spawn_env()
