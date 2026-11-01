"""Connecting a project leaves its tracked files alone: package copy, local exclude, data file."""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks

from routes import mcp_routes
from unity_ai_mcp import unity_mcp_manager as module

SETTINGS = ("UserSettings", "Gamachine", "autoconnect.json")
PACKAGE = ("Packages", "com.coplaydev.unity-mcp")
LEGACY = ("Assets", "Editor", "UnityArchitectAIMCPSetup.cs")
LEGACY_TEXT = ("// Gamachine — MCP bağlantı yapılandırması (güncelleme: 1761900000)\n"
               "// Bu dosya Gamachine tarafından oluşturulmuştur. Silmeyin.\n")
EXCLUDE_BLOCK = ["# Gamachine (local, not committed)", "/Packages/com.coplaydev.unity-mcp/", "/UserSettings/Gamachine/"]


def settings(project):
    return json.loads(project.joinpath(*SETTINGS).read_text(encoding="utf-8"))


def files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.fixture(autouse=True)
def unity_project(tmp_path):
    """Gamachine writes only into a Unity project: Assets and ProjectSettings."""
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "bundle" / "unity-mcp" / "MCPForUnity"
    (src / "Editor" / "Gamachine").mkdir(parents=True)
    (src / "package.json").write_text('{"name": "com.coplaydev.unity-mcp"}', encoding="utf-8")
    (src / "Editor" / "Gamachine" / "GamachineAutoConnect.cs").write_text("// bootstrap", encoding="utf-8")
    (src / "Editor" / "__pycache__").mkdir()
    (src / "Editor" / "__pycache__" / "x.pyc").write_bytes(b"generated")
    return src


@pytest.fixture
def manager(monkeypatch, source):
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "_ensure_writable_resources", lambda: None)
    monkeypatch.setattr(manager, "_get_uvx", lambda: "uvx")
    manager.local_mcp_source = str(source)
    return manager


def project_at(path, manifest=None):
    (path / "Packages").mkdir(parents=True)
    (path / "Assets").mkdir()
    (path / "ProjectSettings").mkdir()
    (path / "Packages" / "manifest.json").write_text(
        json.dumps(manifest or {"dependencies": {"com.unity.ugui": "2.0.0"}}, indent=2), encoding="utf-8")
    return path


# ── Data file ────────────────────────────────────────────────────────────────

def test_settings_file_content_and_revision_changes_on_every_write(manager, tmp_path):
    manager.language = "tr"
    manager._write_autoconnect_settings(str(tmp_path), auto_start=True)
    first = settings(tmp_path)
    assert first["autoStart"] is True and first["port"] == 8080 and first["language"] == "tr"
    assert first["uvxPath"] == "" and isinstance(first["revision"], str) and first["revision"]
    manager._write_autoconnect_settings(str(tmp_path), auto_start=True)
    assert settings(tmp_path)["revision"] != first["revision"]
    assert files(tmp_path / "UserSettings") == ["Gamachine/autoconnect.json"]
    assert not (tmp_path / "Assets" / "Editor").exists()


def test_off_write_keeps_the_language_on_disk_until_a_toggle_sets_one(manager, tmp_path):
    manager.language = "tr"
    manager._write_autoconnect_settings(str(tmp_path), auto_start=True)
    fresh = module.UnityMCPManager()  # an app start: no toggle yet
    fresh._get_uvx = lambda: "uvx"
    assert fresh.language is None
    fresh._write_autoconnect_settings(str(tmp_path), auto_start=False)
    assert settings(tmp_path)["language"] == "tr"
    fresh.language = "en"
    fresh._write_autoconnect_settings(str(tmp_path), auto_start=False)
    assert settings(tmp_path)["language"] == "en"


def test_off_write_over_an_invalid_file_falls_back_to_english(manager, tmp_path):
    path = tmp_path.joinpath(*SETTINGS)
    path.parent.mkdir(parents=True)
    path.write_text('{"autoStart": tr', encoding="utf-8")
    manager._write_autoconnect_settings(str(tmp_path), auto_start=False)
    assert settings(tmp_path)["language"] == "en"


@pytest.mark.parametrize("content,resets", [
    ('{"autoStart": true, "port": 8080, "language": "tr", "uvxPath": "", "revision": "a"}', True),
    ('{"autoStart": false, "port": 8080, "language": "tr", "uvxPath": "", "revision": "a"}', False),
    ('{"autoStart": "true"}', False),
    ('{"autoStart": tr', False),
    ('[true]', False),
])
def test_reset_reads_auto_start_from_the_settings_file(manager, tmp_path, monkeypatch, content, resets):
    path = tmp_path.joinpath(*SETTINGS)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    manager._autoconnect_workspaces.add(str(tmp_path))
    writer = Mock(wraps=manager._write_autoconnect_settings)
    monkeypatch.setattr(manager, "_write_autoconnect_settings", writer)
    assert manager.reset_autoconnect_off(str(tmp_path)) is resets
    if resets:
        writer.assert_called_once_with(str(tmp_path), auto_start=False)
        assert settings(tmp_path)["autoStart"] is False and settings(tmp_path)["language"] == "tr"
    else:
        writer.assert_not_called()
        assert path.read_text(encoding="utf-8") == content
    assert str(tmp_path) not in manager._autoconnect_workspaces


@pytest.mark.parametrize("header,autostart,deleted", [
    (LEGACY_TEXT, True, True),
    (LEGACY_TEXT, False, False),
    ("// The user's own setup\n", True, False),
])
def test_reset_deletes_a_legacy_autostart_script_only_if_it_is_ours(manager, tmp_path, header, autostart, deleted):
    legacy = tmp_path.joinpath(*LEGACY)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(header + f'EditorPrefs.SetBool("MCPForUnity.AutoStartOnLoad", {str(autostart).lower()});',
                      encoding="utf-8")
    meta = legacy.with_name(legacy.name + ".meta")
    meta.write_text("guid: 1", encoding="utf-8")
    assert manager.reset_autoconnect_off(str(tmp_path)) is deleted
    assert legacy.exists() is not deleted and meta.exists() is not deleted
    assert (tmp_path / "Assets" / "Editor").is_dir()
    assert not tmp_path.joinpath("UserSettings").exists()


# ── Manifest migration ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["own-install", "own-install-slashes", "writable-copy", "writable-copy-slash", "dev-checkout"])
def test_migration_removes_only_gamachines_manifest_entry(manager, tmp_path, monkeypatch, value):
    home = tmp_path / "home"
    monkeypatch.setattr(module.os.path, "expanduser", lambda p: str(home) if p == "~" else p)
    checkout = tmp_path / "old-checkout"
    (checkout / "Backend" / "app" / "unity_ai_mcp").mkdir(parents=True)
    (checkout / "Backend" / "app" / "unity_ai_mcp" / "unity_mcp_manager.py").write_text("", encoding="utf-8")
    copy = str(home / ".unity_architect_ai" / "unity-mcp" / "MCPForUnity")
    value = {
        "own-install": "file:" + manager.local_mcp_source,
        "own-install-slashes": "file:" + manager.local_mcp_source.replace(os.sep, "/") + "/",
        "writable-copy": "file:" + copy,
        "writable-copy-slash": "file:" + copy.replace(os.sep, "/") + "/",
        "dev-checkout": "file:" + str(checkout / "unity-mcp" / "MCPForUnity"),
    }[value]
    project = project_at(tmp_path / "Game", {
        "scopedRegistries": [{"name": "r"}],
        "dependencies": {"com.unity.ugui": "2.0.0", "com.coplaydev.unity-mcp": value, "com.unity.test-framework": "1.4.5"},
        "testables": ["x"],
    })
    manifest = project / "Packages" / "manifest.json"
    assert manager.install_package(str(project), write_autoconnect=False) is True
    expected = {"scopedRegistries": [{"name": "r"}],
                "dependencies": {"com.unity.ugui": "2.0.0", "com.unity.test-framework": "1.4.5"},
                "testables": ["x"]}
    text = manifest.read_text(encoding="utf-8")
    assert text == json.dumps(expected, indent=2)
    assert list(json.loads(text)) == ["scopedRegistries", "dependencies", "testables"]
    assert project.joinpath(*PACKAGE, "package.json").is_file()


def test_migration_keeps_line_endings_and_final_newline(manager, tmp_path):
    project = project_at(tmp_path / "Game")
    manifest = project / "Packages" / "manifest.json"
    original = {"dependencies": {"com.coplaydev.unity-mcp": "file:" + manager.local_mcp_source, "a": "1"}}
    manifest.write_bytes((json.dumps(original, indent=2) + "\n").replace("\n", "\r\n").encode("utf-8"))
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert manifest.read_bytes() == (json.dumps({"dependencies": {"a": "1"}}, indent=2) + "\n").replace(
        "\n", "\r\n").encode("utf-8")


@pytest.mark.parametrize("value", [
    "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity",
    "9.0.1",
    "file:../../my-fork/MCPForUnity-dev",
    "file:/opt/unity-mcp/MCPForUnity",
])
def test_user_chosen_package_source_is_kept_and_copy_skipped(manager, tmp_path, caplog, value):
    project = project_at(tmp_path / "Game", {"dependencies": {"com.coplaydev.unity-mcp": value}})
    manifest = project / "Packages" / "manifest.json"
    before = manifest.read_bytes()
    with caplog.at_level(logging.WARNING):
        assert manager.install_package(str(project), write_autoconnect=False) is False
    assert manifest.read_bytes() == before
    assert not project.joinpath(*PACKAGE).exists()
    assert "Gamachine left it as it is" in caplog.text
    assert manager.last_install_error


def test_legacy_script_is_deleted_only_with_gamachines_header(manager, tmp_path):
    ours = project_at(tmp_path / "Ours")
    legacy = ours.joinpath(*LEGACY)
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"\xef\xbb\xbf" + LEGACY_TEXT.encode("utf-8"))
    legacy.with_name(legacy.name + ".meta").write_text("guid: 1", encoding="utf-8")
    (legacy.parent / "Other.cs").write_text("// user", encoding="utf-8")
    assert manager.install_package(str(ours), write_autoconnect=False) is True
    assert not legacy.exists() and not legacy.with_name(legacy.name + ".meta").exists()
    assert (legacy.parent / "Other.cs").is_file()

    theirs = project_at(tmp_path / "Theirs")
    user_file = theirs.joinpath(*LEGACY)
    user_file.parent.mkdir(parents=True)
    user_file.write_text("// The user's own setup\n" + LEGACY_TEXT, encoding="utf-8")
    user_file.with_name(user_file.name + ".meta").write_text("guid: 2", encoding="utf-8")
    assert manager.install_package(str(theirs), write_autoconnect=False) is True
    assert user_file.is_file() and user_file.with_name(user_file.name + ".meta").is_file()

    empty_folder = project_at(tmp_path / "Folder")
    (empty_folder / "Assets" / "Editor").mkdir(parents=True)
    empty_folder.joinpath(*LEGACY).write_text(LEGACY_TEXT, encoding="utf-8")
    assert manager.install_package(str(empty_folder), write_autoconnect=False) is True
    assert (empty_folder / "Assets" / "Editor").is_dir()


# ── Package copy ─────────────────────────────────────────────────────────────

def test_fresh_copy_then_up_to_date_then_stale(manager, tmp_path, source):
    project = project_at(tmp_path / "Game")
    manifest_before = (project / "Packages" / "manifest.json").read_bytes()
    assert manager.install_package(str(project), write_autoconnect=False) is True
    package = project.joinpath(*PACKAGE)
    assert files(package) == [".gamachine-files.json", "Editor/Gamachine/GamachineAutoConnect.cs", "package.json"]
    record = json.loads((package / ".gamachine-files.json").read_text(encoding="utf-8"))
    assert record["sourceDigest"] == module._tree_digest(str(source))
    assert sorted(record["files"]) == ["Editor/Gamachine/GamachineAutoConnect.cs", "package.json"]
    assert (project / "Packages" / "manifest.json").read_bytes() == manifest_before
    assert files(project / "UserSettings") == ["Gamachine/autoconnect.json"]
    assert settings(project)["autoStart"] is False

    marker = package / "package.json"
    stamp = marker.stat().st_mtime_ns
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert marker.stat().st_mtime_ns == stamp

    (source / "Editor" / "Gamachine" / "GamachineAutoConnect.cs").unlink()
    (source / "Editor" / "New.cs").write_text("// new", encoding="utf-8")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert files(package) == [".gamachine-files.json", "Editor/New.cs", "package.json"]
    record = json.loads((package / ".gamachine-files.json").read_text(encoding="utf-8"))
    assert record["sourceDigest"] == module._tree_digest(str(source))
    assert files(project / "UserSettings") == ["Gamachine/autoconnect.json"]


def test_install_keeps_an_existing_settings_file(manager, tmp_path):
    project = project_at(tmp_path / "Game")
    manager.language = "tr"
    manager._write_autoconnect_settings(str(project), auto_start=True)
    before = project.joinpath(*SETTINGS).read_bytes()
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert project.joinpath(*SETTINGS).read_bytes() == before
    assert manager.install_package(str(project)) is True
    assert settings(project)["autoStart"] is True and project.joinpath(*SETTINGS).read_bytes() != before


@pytest.mark.parametrize("kind", ["folder", "file"])
def test_foreign_package_folder_is_left_alone(manager, tmp_path, caplog, kind):
    project = project_at(tmp_path / "Game")
    package = project.joinpath(*PACKAGE)
    if kind == "folder":
        package.mkdir()
        (package / "package.json").write_text('{"name": "mine"}', encoding="utf-8")
    else:
        package.write_text("not a folder", encoding="utf-8")
    before = {p: p.read_bytes() for p in project.joinpath("Packages").rglob("*") if p.is_file()}
    with caplog.at_level(logging.WARNING):
        assert manager.install_package(str(project), write_autoconnect=False) is False
    assert {p: p.read_bytes() for p in project.joinpath("Packages").rglob("*") if p.is_file()} == before
    assert "not made by Gamachine" in caplog.text
    assert "not made by Gamachine" in manager.last_install_error


def test_failed_copy_keeps_the_previous_package(manager, tmp_path, source, monkeypatch):
    project = project_at(tmp_path / "Game")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    package = project.joinpath(*PACKAGE)
    before = {name: (package / name).read_bytes() for name in files(package)}
    (source / "package.json").write_text('{"name": "com.coplaydev.unity-mcp", "v": 2}', encoding="utf-8")
    monkeypatch.setattr(module.shutil, "copytree", Mock(side_effect=OSError("disk full")))
    assert manager.install_package(str(project), write_autoconnect=False) is False
    assert {name: (package / name).read_bytes() for name in files(package)} == before
    assert files(project / "UserSettings") == ["Gamachine/autoconnect.json"]


# ── Local git exclusion ──────────────────────────────────────────────────────

def exclude_lines(git_dir):
    return (git_dir / "info" / "exclude").read_text(encoding="utf-8").splitlines()


def test_exclude_at_repo_root_is_appended_once(manager, tmp_path):
    project = project_at(tmp_path / "Game")
    (project / ".git" / "info").mkdir(parents=True)
    (project / ".git" / "info" / "exclude").write_text("# git ls-files --others --exclude-from=.git/info/exclude\n*.log",
                                                       encoding="utf-8")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert manager.install_package(str(project), write_autoconnect=False) is True
    manager._write_autoconnect_settings(str(project), auto_start=True)
    assert exclude_lines(project / ".git") == [
        "# git ls-files --others --exclude-from=.git/info/exclude", "*.log", *EXCLUDE_BLOCK]
    assert not (project / ".gitignore").exists()


def test_exclude_for_a_project_in_a_repo_subfolder(manager, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    project = project_at(repo / "games" / "My [Game]")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert exclude_lines(repo / ".git") == [
        "# Gamachine (local, not committed)",
        "/games/My \\[Game]/Packages/com.coplaydev.unity-mcp/",
        "/games/My \\[Game]/UserSettings/Gamachine/",
    ]
    other = project_at(repo / "other")
    assert module.UnityMCPManager._ensure_git_exclude(str(other)) is True
    lines = exclude_lines(repo / ".git")
    assert lines.count("# Gamachine (local, not committed)") == 1
    assert lines[-2:] == ["/other/Packages/com.coplaydev.unity-mcp/", "/other/UserSettings/Gamachine/"]


def test_exclude_for_a_linked_worktree_goes_to_the_common_dir(manager, tmp_path):
    main_git = tmp_path / "main" / ".git"
    worktree_git = main_git / "worktrees" / "feature"
    worktree_git.mkdir(parents=True)
    (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
    project = project_at(tmp_path / "feature")
    (project / ".git").write_text("gitdir: ../main/.git/worktrees/feature\n", encoding="utf-8")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert exclude_lines(main_git) == EXCLUDE_BLOCK
    assert not (worktree_git / "info").exists()


def test_exclude_for_a_submodule_gitdir_file(manager, tmp_path):
    module_git = tmp_path / "super" / ".git" / "modules" / "game"
    module_git.mkdir(parents=True)
    project = project_at(tmp_path / "super" / "game")
    (project / ".git").write_text(f"gitdir: {module_git}\n", encoding="utf-8")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert exclude_lines(module_git) == EXCLUDE_BLOCK


def test_no_repository_means_no_exclude(tmp_path):
    project = project_at(tmp_path / "Game")
    assert module.UnityMCPManager._ensure_git_exclude(str(project)) is False
    assert files(tmp_path) == ["Game/Packages/manifest.json"]


def test_full_migration_of_an_old_project(manager, tmp_path):
    project = project_at(tmp_path / "Game", {"dependencies": {
        "com.coplaydev.unity-mcp": "file:" + manager.local_mcp_source,
        "com.unity.ugui": "2.0.0"}})
    (project / ".git").mkdir()
    legacy = project.joinpath(*LEGACY)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(LEGACY_TEXT, encoding="utf-8")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert json.loads((project / "Packages" / "manifest.json").read_text(encoding="utf-8")) == {
        "dependencies": {"com.unity.ugui": "2.0.0"}}
    assert not legacy.exists()
    assert project.joinpath(*PACKAGE, ".gamachine-files.json").is_file()
    assert exclude_lines(project / ".git") == EXCLUDE_BLOCK


# ── Dialog surfacing ─────────────────────────────────────────────────────────

@pytest.fixture
def status_manager(monkeypatch):
    manager = module.UnityMCPManager()
    manager.enabled = True
    manager.active_workspace = "C:\\Projects\\Game"
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(manager, "_status", AsyncMock(return_value={
        "status": "running", "pid": 1, "port": 8080, "instances": [], "reason": None}))
    return manager


def stub_probe(dialogs, error=None):
    def find():
        if error:
            raise error
        return dialogs
    return SimpleNamespace(
        find_unity_dialogs=Mock(side_effect=find),
        dialogs_for_project=lambda found, name: [d for d in found if d.get("project") == name])


def test_status_reports_the_active_projects_dialog(status_manager):
    status_manager._dialog_probe = stub_probe([
        {"title": "Other", "buttons": ["OK"], "hwnd": 1, "project": "Elsewhere"},
        {"title": "URP Material upgrade", "buttons": ["Proceed", "Cancel"], "hwnd": 2, "project": "Game"},
    ])
    result = asyncio.run(status_manager.get_status())
    assert result["dialog"] == {"title": "URP Material upgrade", "buttons": ["Proceed", "Cancel"]}
    assert asyncio.run(status_manager.get_status())["dialog"]["title"] == "URP Material upgrade"
    status_manager._dialog_probe.find_unity_dialogs.assert_called_once_with()  # at most once per 2 s


def test_status_ignores_another_projects_dialog(status_manager):
    status_manager._dialog_probe = stub_probe([{"title": "Other", "buttons": [], "hwnd": 1, "project": "Game2"}])
    assert "dialog" not in asyncio.run(status_manager.get_status())


def test_probe_error_means_no_dialog(status_manager, caplog):
    status_manager._dialog_probe = stub_probe([], error=OSError("user32 unavailable"))
    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(status_manager.get_status())
    assert "dialog" not in result and result["status"] == "running"
    assert "dialog probe failed" in caplog.text


def test_probe_that_cannot_load_means_no_dialog(status_manager, tmp_path):
    status_manager.server_dir = str(tmp_path / "missing")
    assert "dialog" not in asyncio.run(status_manager.get_status())


def test_probe_is_loaded_from_the_server_dir(status_manager, tmp_path):
    probe = tmp_path / "src" / "transport" / "unity_dialog_probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "def find_unity_dialogs():\n"
        "    return [{'title': 'Import', 'buttons': ['OK'], 'hwnd': 3, 'project': 'Game'}]\n"
        "def dialogs_for_project(dialogs, name):\n"
        "    return [d for d in dialogs if d['project'] == name]\n", encoding="utf-8")
    status_manager.server_dir = str(tmp_path)
    assert asyncio.run(status_manager.get_status())["dialog"] == {"title": "Import", "buttons": ["OK"]}


@pytest.mark.parametrize("case", ["connected", "disabled", "not-windows", "no-workspace"])
def test_no_probe_when_connected_off_or_elsewhere(status_manager, monkeypatch, case):
    status_manager._dialog_probe = stub_probe([{"title": "T", "buttons": [], "hwnd": 1, "project": "Game"}])
    if case == "connected":
        status_manager._status.return_value = {"status": "connected", "pid": 1, "port": 8080,
                                               "instances": [{}], "reason": None}
    elif case == "disabled":
        status_manager.enabled = False
    elif case == "not-windows":
        monkeypatch.setattr(module.sys, "platform", "darwin")
    else:
        status_manager.active_workspace = None
    assert "dialog" not in asyncio.run(status_manager.get_status())
    status_manager._dialog_probe.find_unity_dialogs.assert_not_called()


# ── Audit round (turn 2) ─────────────────────────────────────────────────────

def snapshot(*roots):
    """Every file's bytes and every folder/link below the roots, links not followed."""
    seen = {}
    for root in roots:
        for folder, dirs, names in os.walk(root):
            for name in dirs + names:
                path = os.path.join(folder, name)
                is_file = os.path.isfile(path) and not os.path.islink(path)
                seen[path] = open(path, "rb").read() if is_file else None
    return seen


def make_link(link, target, directory):
    """A junction (Windows) or symlink; the test is skipped where neither can be made."""
    if directory and os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if done.returncode == 0:
            return
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("neither a junction nor a symlink can be created here")


def link_component(project, outside, relative, directory):
    """Replaces project/<relative> by a link to a copy (or an empty decoy) under outside."""
    real = project / relative
    target = outside / relative.replace("/", "_")
    if directory:
        if real.exists():
            shutil.copytree(real, target)
            shutil.rmtree(real)
        else:
            real.parent.mkdir(parents=True, exist_ok=True)
            target.mkdir()
    else:
        if real.exists():
            shutil.copy2(real, target)
            real.unlink()
        else:
            real.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("decoy", encoding="utf-8")
    make_link(real, target, directory)


def old_gamachine_project(tmp_path, manager):
    """Manifest with our old entry, the old Assets script and a git repo: everything an install migrates."""
    project = project_at(tmp_path / "Game", {"dependencies": {"com.coplaydev.unity-mcp": "file:" + manager.local_mcp_source}})
    legacy = project.joinpath(*LEGACY)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(LEGACY_TEXT + 'EditorPrefs.SetBool("MCPForUnity.AutoStartOnLoad", true);', encoding="utf-8")
    legacy.with_name(legacy.name + ".meta").write_text("guid: 1", encoding="utf-8")
    (project / ".git" / "info").mkdir(parents=True)
    (project / ".git" / "info" / "exclude").write_text("*.log\n", encoding="utf-8")
    return project


LINKABLE = [
    ("Assets", True), ("Assets/Editor", True),
    ("Assets/Editor/UnityArchitectAIMCPSetup.cs", False), ("Assets/Editor/UnityArchitectAIMCPSetup.cs.meta", False),
    ("UserSettings", True), ("UserSettings/Gamachine", True), ("UserSettings/Gamachine/autoconnect.json", False),
    ("Packages", True), ("Packages/com.coplaydev.unity-mcp", True),
    (".git", True), (".git/info", True), (".git/info/exclude", False),
]


@pytest.mark.parametrize("relative,directory", LINKABLE)
def test_install_refuses_a_link_on_every_written_path(manager, tmp_path, caplog, relative, directory):
    project = old_gamachine_project(tmp_path, manager)
    outside = tmp_path / "outside"
    outside.mkdir()
    link_component(project, outside, relative, directory)
    before = snapshot(project, outside)
    with caplog.at_level(logging.ERROR):
        assert manager.install_package(str(project)) is False
    assert snapshot(project, outside) == before
    assert "is a link" in caplog.text


@pytest.mark.parametrize("relative,directory", [
    ("UserSettings", True), ("UserSettings/Gamachine", True), ("UserSettings/Gamachine/autoconnect.json", False)])
def test_settings_writer_refuses_a_link(manager, tmp_path, caplog, relative, directory):
    project = project_at(tmp_path / "Game")
    outside = tmp_path / "outside"
    outside.mkdir()
    link_component(project, outside, relative, directory)
    before = snapshot(project, outside)
    with caplog.at_level(logging.ERROR):
        manager._write_autoconnect_settings(str(project), auto_start=True)
    assert snapshot(project, outside) == before
    assert str(project) not in manager._autoconnect_workspaces and "is a link" in caplog.text


def test_reset_leaves_a_legacy_script_behind_a_link_alone(manager, tmp_path):
    project = project_at(tmp_path / "Game")
    outside = tmp_path / "outside"
    victim = outside / "Editor"
    victim.mkdir(parents=True)
    script = victim / "UnityArchitectAIMCPSetup.cs"
    script.write_text(LEGACY_TEXT + 'EditorPrefs.SetBool("MCPForUnity.AutoStartOnLoad", true);', encoding="utf-8")
    make_link(project / "Assets" / "Editor", victim, True)
    before = snapshot(outside)
    assert manager.reset_autoconnect_off(str(project)) is False
    assert snapshot(outside) == before


def test_a_users_own_clone_of_the_package_is_left_alone(manager, tmp_path, caplog):
    clone = tmp_path / "clones" / "unity-mcp" / "MCPForUnity"
    clone.mkdir(parents=True)
    project = old_gamachine_project(tmp_path, manager)
    manifest = project / "Packages" / "manifest.json"
    manifest.write_text(json.dumps({"dependencies": {"com.coplaydev.unity-mcp": "file:" + str(clone)}}), encoding="utf-8")
    before = snapshot(project)
    with caplog.at_level(logging.WARNING):
        assert manager.install_package(str(project), write_autoconnect=False) is False
    assert snapshot(project) == before
    assert not project.joinpath(*PACKAGE).exists() and project.joinpath(*LEGACY).is_file()
    assert manifest.read_text(encoding="utf-8") == json.dumps(
        {"dependencies": {"com.coplaydev.unity-mcp": "file:" + str(clone)}})
    assert "Gamachine left it as it is" in caplog.text
    assert manager.last_install_error


def test_a_failed_copy_changes_neither_manifest_nor_assets(manager, tmp_path, monkeypatch):
    project = old_gamachine_project(tmp_path, manager)
    before = snapshot(project / "Packages", project / "Assets")
    monkeypatch.setattr(module.shutil, "copytree", Mock(side_effect=OSError("disk full")))
    assert manager.install_package(str(project), write_autoconnect=False) is False
    assert snapshot(project / "Packages", project / "Assets") == before
    assert not project.joinpath(*PACKAGE).exists()
    assert not [p for p in (project / "UserSettings" / "Gamachine").iterdir() if p.name.startswith("package.")]


def test_the_manifest_entry_goes_only_after_the_package_is_in_place(manager, tmp_path, monkeypatch):
    project = old_gamachine_project(tmp_path, manager)
    seen = []
    real = module.UnityMCPManager._migrate_manifest

    def migrate(self, manifest_path):
        seen.append(project.joinpath(*PACKAGE, "package.json").is_file() and project.joinpath(*LEGACY).is_file())
        return real(self, manifest_path)

    monkeypatch.setattr(module.UnityMCPManager, "_migrate_manifest", migrate)
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert seen == [True]
    assert not project.joinpath(*LEGACY).exists()


def test_a_package_the_user_edited_is_not_replaced(manager, tmp_path, source, caplog):
    project = project_at(tmp_path / "Game")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    package = project.joinpath(*PACKAGE)
    (package / "package.json").write_text("my own change", encoding="utf-8")
    (package / "Mine.cs").write_text("// mine", encoding="utf-8")
    (source / "package.json").write_text('{"name": "com.coplaydev.unity-mcp", "v": 2}', encoding="utf-8")
    before = snapshot(package)
    with caplog.at_level(logging.WARNING):
        assert manager.install_package(str(project), write_autoconnect=False) is False
    assert snapshot(package) == before
    assert "was changed by hand" in caplog.text
    assert manager.last_install_error == (
        "Packages/com.coplaydev.unity-mcp was changed by hand; Gamachine left it as it is.")


def test_a_deleted_package_file_is_copied_again(manager, tmp_path):
    project = project_at(tmp_path / "Game")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    package = project.joinpath(*PACKAGE)
    (package / "package.json").unlink()
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert files(package) == [".gamachine-files.json", "Editor/Gamachine/GamachineAutoConnect.cs", "package.json"]
    assert (package / "package.json").read_text(encoding="utf-8") == '{"name": "com.coplaydev.unity-mcp"}'


def test_git_exclude_runs_once_per_install_and_the_old_names_are_gone(manager, tmp_path, monkeypatch):
    project = project_at(tmp_path / "Game")
    calls = []
    real = module.UnityMCPManager._ensure_git_exclude
    monkeypatch.setattr(module.UnityMCPManager, "_ensure_git_exclude",
                        staticmethod(lambda workspace: calls.append(workspace) or real(workspace)))
    assert manager.install_package(str(project)) is True
    assert calls == [str(project)]
    assert not hasattr(module, "complete_digest") and not hasattr(manager, "_write_autoconnect_script")


@pytest.mark.parametrize("missing", [("Assets",), ("ProjectSettings",), ("Assets", "ProjectSettings")])
def test_a_folder_that_is_not_a_unity_project_is_never_touched(manager, tmp_path, missing):
    plain = project_at(tmp_path / "Plain", {"dependencies": {"com.coplaydev.unity-mcp": "file:" + manager.local_mcp_source}})
    for name in missing:
        shutil.rmtree(plain / name)
    (plain / ".git" / "info").mkdir(parents=True)
    before = snapshot(plain)
    assert manager.install_package(str(plain)) is False
    manager._write_autoconnect_settings(str(plain), auto_start=True)
    assert module.UnityMCPManager._ensure_git_exclude(str(plain)) is False
    assert snapshot(plain) == before and str(plain) not in manager._autoconnect_workspaces


@pytest.mark.parametrize("indent,bom,tail,eol", [
    ("    ", False, "\n", "\n"),
    ("\t", False, "", "\n"),
    ("  ", True, "\n", "\n"),
    ("    ", True, "\n", "\r\n"),
])
def test_manifest_formatting_survives_the_migration_byte_for_byte(manager, tmp_path, indent, bom, tail, eol):
    project = project_at(tmp_path / "Game")
    manifest = project / "Packages" / "manifest.json"
    full = {"dependencies": {"com.unity.ugui": "2.0.0", "com.coplaydev.unity-mcp": "file:" + manager.local_mcp_source,
                             "com.example.güzel": "1.0.0"},
            "scopedRegistries": [{"name": "Güzel", "scopes": ["com.example"]}]}

    def render(data):
        text = (json.dumps(data, indent=indent, ensure_ascii=False) + tail).replace("\n", eol)
        return (("﻿" if bom else "") + text).encode("utf-8")

    manifest.write_bytes(render(full))
    del full["dependencies"]["com.coplaydev.unity-mcp"]
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert manifest.read_bytes() == render(full)


def test_staging_folders_of_earlier_installs_are_swept_only_where_they_are_ours(manager, tmp_path):
    project = project_at(tmp_path / "Game")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    stage = project / "UserSettings" / "Gamachine"
    for name in ("package.new-1-2", "package.old-3-4", "keep-me"):
        (stage / name).mkdir()
        (stage / name / "x.txt").write_text("x", encoding="utf-8")
    (stage / "package.new-note.txt").write_text("a file", encoding="utf-8")
    (project / "Packages" / "package.new-9-9").mkdir()
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert sorted(p.name for p in stage.iterdir()) == ["autoconnect.json", "keep-me", "package.new-note.txt"]
    assert (project / "Packages" / "package.new-9-9").is_dir()


def test_an_old_staging_folder_is_kept_while_no_package_is_installed(tmp_path):
    project = project_at(tmp_path / "Game")
    stage = project / "UserSettings" / "Gamachine"
    (stage / "package.old-3-4").mkdir(parents=True)
    (stage / "package.new-1-2").mkdir()
    module.UnityMCPManager._sweep_staging(str(project))
    assert [p.name for p in stage.iterdir()] == ["package.old-3-4"]


def test_start_mcp_for_installs_off_the_event_loop(manager, tmp_path, monkeypatch):
    project = project_at(tmp_path / "Game")
    seen = {}

    def install(workspace, write_autoconnect=True):
        seen["thread"] = threading.current_thread()
        seen["args"] = (workspace, write_autoconnect)
        time.sleep(0.4)
        return True

    monkeypatch.setattr(mcp_routes, "unity_mcp_manager", manager)
    monkeypatch.setattr(mcp_routes.unity_project_presence, "mcp_mismatch_detail", lambda workspace: None)
    monkeypatch.setattr(manager, "is_unity_running", lambda: True)
    monkeypatch.setattr(manager, "is_running", lambda: False)
    monkeypatch.setattr(manager, "start_server", lambda: True)
    monkeypatch.setattr(manager, "install_package", install)
    monkeypatch.setattr(manager, "write_autoconnect_when_ready", AsyncMock())
    ticks = []

    async def scenario():
        async def ticker():
            while True:
                await asyncio.sleep(0.05)
                ticks.append(1)

        task = asyncio.create_task(ticker())
        try:
            return await mcp_routes.start_mcp_for(str(project), BackgroundTasks(), "tr")
        finally:
            task.cancel()

    result = asyncio.run(scenario())
    assert result["package_installed"] is True and seen["args"] == (str(project), False)
    assert seen["thread"] is not threading.main_thread()
    assert len(ticks) >= 4  # the loop kept running during the 0.4 s install


def poll(manager):
    # get_status adds `dialog` to the dict _status returned; the real _status builds a new one each time.
    manager._status.return_value = {"status": "running", "pid": 1, "port": 8080, "instances": [], "reason": None}
    return asyncio.run(manager.get_status())


def test_dialog_cache_never_serves_another_projects_value(status_manager):
    status_manager._dialog_probe = stub_probe([
        {"title": "A", "buttons": [], "hwnd": 1, "project": "Game"},
        {"title": "B", "buttons": [], "hwnd": 2, "project": "Other"},
    ])
    root = os.path.join(os.path.abspath(os.sep), "Projects")
    status_manager.active_workspace = os.path.join(root, "Game")
    assert poll(status_manager)["dialog"]["title"] == "A"
    status_manager.active_workspace = os.path.join(root, "Other")
    assert poll(status_manager)["dialog"]["title"] == "B"
    status_manager.active_workspace = os.path.join(root, "Third")
    assert "dialog" not in poll(status_manager)
    status_manager.active_workspace = os.path.join(root, "Game")
    assert poll(status_manager)["dialog"]["title"] == "A"
    assert status_manager._dialog_probe.find_unity_dialogs.call_count == 3


def test_concurrent_polls_share_a_probe_and_no_poll_gets_null(status_manager):
    dialogs = [{"title": "A", "buttons": [], "hwnd": 1, "project": "Game"},
               {"title": "B", "buttons": [], "hwnd": 2, "project": "Other"}]

    def slow():
        time.sleep(0.3)
        return dialogs

    status_manager._dialog_probe = SimpleNamespace(
        find_unity_dialogs=Mock(side_effect=slow),
        dialogs_for_project=lambda found, name: [d for d in found if d["project"] == name])
    root = os.path.join(os.path.abspath(os.sep), "Projects")
    status_manager.active_workspace = os.path.join(root, "Game")
    status_manager._status.side_effect = lambda: {
        "status": "running", "pid": 1, "port": 8080, "instances": [], "reason": None}

    async def scenario():
        first = asyncio.create_task(status_manager.get_status())
        second = asyncio.create_task(status_manager.get_status())
        await asyncio.sleep(0.05)  # both are now waiting on the Game probe
        status_manager.active_workspace = os.path.join(root, "Other")
        third = asyncio.create_task(status_manager.get_status())
        return await asyncio.gather(first, second, third)

    first, second, third = asyncio.run(scenario())
    assert first["dialog"]["title"] == second["dialog"]["title"] == "A"
    assert third["dialog"]["title"] == "B"
    assert status_manager._dialog_probe.find_unity_dialogs.call_count == 2


@pytest.mark.parametrize("relative", [relative for relative, directory in LINKABLE if not directory])
def test_install_refuses_a_file_reported_as_a_link(manager, tmp_path, monkeypatch, caplog, relative):
    """The same refusal where file symlinks cannot be created (Windows without the privilege)."""
    project = old_gamachine_project(tmp_path, manager)
    flagged = os.path.join(str(project), *relative.split("/"))
    monkeypatch.setattr(module, "_is_link", lambda path: path == flagged)
    before = snapshot(project)
    with caplog.at_level(logging.ERROR):
        assert manager.install_package(str(project)) is False
    assert snapshot(project) == before
    assert "is a link" in caplog.text


@pytest.mark.parametrize("value", [
    "file://attacker/share/unity-mcp/MCPForUnity",
    r"file:\\attacker\share\unity-mcp\MCPForUnity",
])
def test_network_manifest_source_is_never_probed(manager, monkeypatch, value):
    probed = []
    monkeypatch.setattr(module.os.path, "isfile", lambda p: probed.append(p) or False)
    assert manager._is_our_manifest_source(value) is False
    assert probed == []


# ── Audit round (turn 3) ─────────────────────────────────────────────────────

def installed_project(manager, tmp_path):
    project = project_at(tmp_path / "Game")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    return project, project.joinpath(*PACKAGE)


def new_source_version(source):
    (source / "package.json").write_text('{"name": "com.coplaydev.unity-mcp", "v": 2}', encoding="utf-8")


def test_a_modified_recorded_file_is_kept_and_the_reason_is_reported(manager, tmp_path, source):
    project, package = installed_project(manager, tmp_path)
    (package / "package.json").write_text("my own change", encoding="utf-8")
    new_source_version(source)
    before = snapshot(package)
    assert manager.install_package(str(project), write_autoconnect=False) is False
    assert snapshot(package) == before
    assert "was changed by hand" in manager.last_install_error
    # Even with an unchanged source a modified file is reported, not silently accepted.
    assert manager.install_package(str(project), write_autoconnect=False) is False


def test_a_missing_recorded_file_is_restored(manager, tmp_path):
    project, package = installed_project(manager, tmp_path)
    (package / "Editor" / "Gamachine" / "GamachineAutoConnect.cs").unlink()
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert (package / "Editor" / "Gamachine" / "GamachineAutoConnect.cs").read_text(encoding="utf-8") == "// bootstrap"
    assert manager.last_install_error is None


def test_extra_files_are_carried_into_an_updated_copy(manager, tmp_path, source):
    project, package = installed_project(manager, tmp_path)
    (package / "package.json.meta").write_text("guid: unity", encoding="utf-8")
    (package / "Docs").mkdir()
    (package / "Docs" / "notes.md").write_text("mine", encoding="utf-8")
    new_source_version(source)
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert (package / "package.json.meta").read_text(encoding="utf-8") == "guid: unity"
    assert (package / "Docs" / "notes.md").read_text(encoding="utf-8") == "mine"
    assert '"v": 2' in (package / "package.json").read_text(encoding="utf-8")
    record = json.loads((package / ".gamachine-files.json").read_text(encoding="utf-8"))
    assert "package.json.meta" not in record["files"] and "Docs/notes.md" not in record["files"]
    # They stay extras: a second install with the same source changes nothing.
    stamp = (package / "Docs" / "notes.md").stat().st_mtime_ns
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert (package / "Docs" / "notes.md").stat().st_mtime_ns == stamp


def test_an_extra_that_collides_with_a_new_source_file_is_refused(manager, tmp_path, source):
    project, package = installed_project(manager, tmp_path)
    (package / "Editor" / "New.cs").write_text("// the user's own", encoding="utf-8")
    (source / "Editor" / "New.cs").write_text("// from the new version", encoding="utf-8")
    before = snapshot(package)
    assert manager.install_package(str(project), write_autoconnect=False) is False
    assert snapshot(package) == before
    assert "Editor/New.cs" in manager.last_install_error
    assert not [p for p in (project / "UserSettings" / "Gamachine").iterdir() if p.name.startswith("package.")]


def turn2_package(manager, project, source):
    """What a turn 1/2 build left: the copy plus one digest marker, no per-file record."""
    package = project.joinpath(*PACKAGE)
    shutil.copytree(source, package, ignore=shutil.ignore_patterns("__pycache__"))
    (package / ".gamachine-source-digest").write_text(module._tree_digest(str(source)), encoding="utf-8")
    return package


def test_a_marker_only_package_updates_when_unmodified(manager, tmp_path, source):
    project = project_at(tmp_path / "Game")
    package = turn2_package(manager, project, source)
    stamp = (package / "package.json").stat().st_mtime_ns
    assert manager.install_package(str(project), write_autoconnect=False) is True  # same source: nothing to do
    assert (package / "package.json").stat().st_mtime_ns == stamp
    new_source_version(source)
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert '"v": 2' in (package / "package.json").read_text(encoding="utf-8")
    assert (package / ".gamachine-files.json").is_file() and not (package / ".gamachine-source-digest").exists()


def test_a_marker_only_package_that_was_changed_is_refused(manager, tmp_path, source):
    project = project_at(tmp_path / "Game")
    package = turn2_package(manager, project, source)
    (package / "package.json").write_text("my own change", encoding="utf-8")
    new_source_version(source)
    before = snapshot(package)
    assert manager.install_package(str(project), write_autoconnect=False) is False
    assert snapshot(package) == before
    assert "was changed by hand" in manager.last_install_error


def test_a_manifest_changed_to_a_users_value_during_the_copy_discards_the_staged_copy(manager, tmp_path, source,
                                                                                   monkeypatch):
    project, package = installed_project(manager, tmp_path)
    manifest = project / "Packages" / "manifest.json"
    new_source_version(source)
    before_package = snapshot(package)
    real_copytree = shutil.copytree

    def flip(*args, **kwargs):
        result = real_copytree(*args, **kwargs)
        manifest.write_text(json.dumps({"dependencies": {"com.coplaydev.unity-mcp": "9.0.1"}}), encoding="utf-8")
        return result

    monkeypatch.setattr(module.shutil, "copytree", flip)
    assert manager.install_package(str(project), write_autoconnect=False) is False
    assert snapshot(package) == before_package
    assert json.loads(manifest.read_text(encoding="utf-8")) == {"dependencies": {"com.coplaydev.unity-mcp": "9.0.1"}}
    assert "manifest.json uses another com.coplaydev.unity-mcp (9.0.1)" in manager.last_install_error
    assert not [p for p in (project / "UserSettings" / "Gamachine").iterdir() if p.name.startswith("package.")]


def test_create_only_never_overwrites_an_existing_settings_file(manager, tmp_path):
    manager.language = "tr"
    manager._write_autoconnect_settings(str(tmp_path), auto_start=True)
    before = tmp_path.joinpath(*SETTINGS).read_bytes()
    # A late install thread finishing after a newer on-write:
    manager._write_autoconnect_settings(str(tmp_path), auto_start=False, create_only=True)
    assert tmp_path.joinpath(*SETTINGS).read_bytes() == before
    assert str(tmp_path) in manager._autoconnect_workspaces
    tmp_path.joinpath(*SETTINGS).unlink()
    manager._write_autoconnect_settings(str(tmp_path), auto_start=False, create_only=True)
    assert settings(tmp_path)["autoStart"] is False


def test_workspaces_with_the_same_folder_name_get_separate_dialog_cache_entries(status_manager, tmp_path):
    status_manager._dialog_probe = stub_probe([{"title": "A", "buttons": [], "hwnd": 1, "project": "Game"}])
    first, second = tmp_path / "one" / "Game", tmp_path / "two" / "Game"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    status_manager.active_workspace = str(first)
    assert poll(status_manager)["dialog"]["title"] == "A"
    status_manager._dialog_probe = stub_probe([])
    status_manager.active_workspace = str(second)
    assert "dialog" not in poll(status_manager)
    assert status_manager._dialog_probe.find_unity_dialogs.call_count == 1
    assert len(status_manager._dialog_cache) == 2
    assert {entry["project"] for entry in status_manager._dialog_cache.values()} == {"Game"}


def test_reset_in_a_folder_that_is_not_a_unity_project_deletes_nothing(manager, tmp_path):
    folder = tmp_path / "plain"
    script = folder / "Assets" / "Editor" / "UnityArchitectAIMCPSetup.cs"
    script.parent.mkdir(parents=True)
    script.write_text(LEGACY_TEXT + 'EditorPrefs.SetBool("MCPForUnity.AutoStartOnLoad", true);', encoding="utf-8")
    (folder / "UserSettings" / "Gamachine").mkdir(parents=True)
    (folder / "UserSettings" / "Gamachine" / "autoconnect.json").write_text('{"autoStart": true}', encoding="utf-8")
    before = snapshot(folder)
    assert manager.reset_autoconnect_off(str(folder)) is False
    assert snapshot(folder) == before


def test_a_gitdir_that_is_itself_a_junction_is_refused(manager, tmp_path, caplog):
    real_git = tmp_path / "elsewhere" / "real.git"
    (real_git / "info").mkdir(parents=True)
    (real_git / "commondir").write_text("../common\n", encoding="utf-8")
    (tmp_path / "elsewhere" / "common" / "info").mkdir(parents=True)
    project = project_at(tmp_path / "Game")
    outside = tmp_path / "outside"
    outside.mkdir()
    make_link(outside / "gitdir-link", real_git, True)
    (project / ".git").write_text(f"gitdir: {outside / 'gitdir-link'}\n", encoding="utf-8")
    before = snapshot(tmp_path / "elsewhere")
    with caplog.at_level(logging.ERROR):
        assert manager.install_package(str(project), write_autoconnect=False) is False
    assert snapshot(tmp_path / "elsewhere") == before
    assert "is a link" in caplog.text and "did not write there" in manager.last_install_error


def test_a_refused_install_stops_the_server_and_answers_409(manager, tmp_path, monkeypatch):
    project = project_at(tmp_path / "Game")
    reason = "Packages/com.coplaydev.unity-mcp was changed by hand; Gamachine left it as it is."

    def refuse(workspace, write_autoconnect=True):
        manager.last_install_error = reason
        return False

    stopped = []
    monkeypatch.setattr(mcp_routes, "unity_mcp_manager", manager)
    monkeypatch.setattr(mcp_routes.unity_project_presence, "mcp_mismatch_detail", lambda workspace: None)
    monkeypatch.setattr(manager, "is_unity_running", lambda: True)
    monkeypatch.setattr(manager, "is_running", lambda: False)
    monkeypatch.setattr(manager, "start_server", lambda: True)
    monkeypatch.setattr(manager, "stop_server", lambda: stopped.append(True))
    monkeypatch.setattr(manager, "install_package", refuse)
    writer = Mock()
    monkeypatch.setattr(manager, "_write_autoconnect_settings", writer)
    monkeypatch.setattr(mcp_routes, "stop_mcp", mcp_routes.stop_mcp)
    tasks = BackgroundTasks()
    with pytest.raises(mcp_routes.HTTPException) as caught:
        asyncio.run(mcp_routes.start_mcp_for(str(project), tasks, "tr"))
    assert caught.value.status_code == 409 and caught.value.detail == reason
    assert stopped == [True]
    assert manager.enabled is False and manager.active_workspace is None
    assert tasks.tasks == []
    writer.assert_not_called()
