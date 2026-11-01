"""Connecting a project leaves its tracked files alone: package copy, local exclude, data file."""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

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
    (path / "Packages" / "manifest.json").write_text(
        json.dumps(manifest or {"dependencies": {"com.unity.ugui": "2.0.0"}}, indent=2), encoding="utf-8")
    return path


# ── Data file ────────────────────────────────────────────────────────────────

def test_settings_file_content_and_revision_changes_on_every_write(manager, tmp_path):
    manager.language = "tr"
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    first = settings(tmp_path)
    assert first["autoStart"] is True and first["port"] == 8080 and first["language"] == "tr"
    assert first["uvxPath"] == "" and isinstance(first["revision"], str) and first["revision"]
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    assert settings(tmp_path)["revision"] != first["revision"]
    assert files(tmp_path / "UserSettings") == ["Gamachine/autoconnect.json"]
    assert not (tmp_path / "Assets").exists()


def test_off_write_keeps_the_language_on_disk_until_a_toggle_sets_one(manager, tmp_path):
    manager.language = "tr"
    manager._write_autoconnect_script(str(tmp_path), auto_start=True)
    fresh = module.UnityMCPManager()  # an app start: no toggle yet
    fresh._get_uvx = lambda: "uvx"
    assert fresh.language is None
    fresh._write_autoconnect_script(str(tmp_path), auto_start=False)
    assert settings(tmp_path)["language"] == "tr"
    fresh.language = "en"
    fresh._write_autoconnect_script(str(tmp_path), auto_start=False)
    assert settings(tmp_path)["language"] == "en"


def test_off_write_over_an_invalid_file_falls_back_to_english(manager, tmp_path):
    path = tmp_path.joinpath(*SETTINGS)
    path.parent.mkdir(parents=True)
    path.write_text('{"autoStart": tr', encoding="utf-8")
    manager._write_autoconnect_script(str(tmp_path), auto_start=False)
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
    writer = Mock(wraps=manager._write_autoconnect_script)
    monkeypatch.setattr(manager, "_write_autoconnect_script", writer)
    assert manager.reset_autoconnect_off(str(tmp_path)) is resets
    if resets:
        writer.assert_called_once_with(str(tmp_path), auto_start=False)
        assert settings(tmp_path)["autoStart"] is False and settings(tmp_path)["language"] == "tr"
    else:
        writer.assert_not_called()
        assert path.read_text(encoding="utf-8") == content
    assert str(tmp_path) not in manager._autoconnect_workspaces


def test_reset_ignores_a_legacy_script_without_settings_file(manager, tmp_path):
    legacy = tmp_path.joinpath(*LEGACY)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(LEGACY_TEXT + 'EditorPrefs.SetBool("MCPForUnity.AutoStartOnLoad", true);', encoding="utf-8")
    assert manager.reset_autoconnect_off(str(tmp_path)) is False
    assert not tmp_path.joinpath("UserSettings").exists()


# ── Manifest migration ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "file:C:\\Users\\burcu\\Downloads\\Unity-Architect-AI-main\\unity-mcp\\MCPForUnity",
    "file:/Users/someone/.unity_architect_ai/unity-mcp/MCPForUnity",
    "file:/Applications/Gamachine.app/Contents/Resources/unity-mcp/MCPForUnity/",
    "own-install",
    "writable-copy",
])
def test_migration_removes_only_gamachines_manifest_entry(manager, tmp_path, monkeypatch, value):
    home = tmp_path / "home"
    monkeypatch.setattr(module.os.path, "expanduser", lambda p: str(home) if p == "~" else p)
    if value == "own-install":
        value = "file:" + manager.local_mcp_source
    elif value == "writable-copy":
        value = "file:" + str(home / ".unity_architect_ai" / "unity-mcp" / "MCPForUnity")
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
    project = tmp_path / "Game"
    (project / "Packages").mkdir(parents=True)
    manifest = project / "Packages" / "manifest.json"
    original = {"dependencies": {"com.coplaydev.unity-mcp": "file:D:/x/unity-mcp/MCPForUnity", "a": "1"}}
    manifest.write_bytes((json.dumps(original, indent=2) + "\n").replace("\n", "\r\n").encode("utf-8"))
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert manifest.read_bytes() == (json.dumps({"dependencies": {"a": "1"}}, indent=2) + "\n").replace(
        "\n", "\r\n").encode("utf-8")


@pytest.mark.parametrize("value", [
    "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity",
    "9.0.1",
    "file:../../my-fork/MCPForUnity-dev",
])
def test_user_chosen_package_source_is_kept_and_copy_skipped(manager, tmp_path, caplog, value):
    project = project_at(tmp_path / "Game", {"dependencies": {"com.coplaydev.unity-mcp": value}})
    manifest = project / "Packages" / "manifest.json"
    before = manifest.read_bytes()
    with caplog.at_level(logging.WARNING):
        assert manager.install_package(str(project), write_autoconnect=False) is False
    assert manifest.read_bytes() == before
    assert not project.joinpath(*PACKAGE).exists()
    assert "the user chose it" in caplog.text


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
    assert files(package) == [".gamachine-source-digest", "Editor/Gamachine/GamachineAutoConnect.cs", "package.json"]
    assert (package / ".gamachine-source-digest").read_text(encoding="utf-8") == module._tree_digest(str(source))
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
    assert files(package) == [".gamachine-source-digest", "Editor/New.cs", "package.json"]
    assert (package / ".gamachine-source-digest").read_text(encoding="utf-8") == module._tree_digest(str(source))
    assert files(project / "UserSettings") == ["Gamachine/autoconnect.json"]


def test_install_keeps_an_existing_settings_file(manager, tmp_path):
    project = project_at(tmp_path / "Game")
    manager.language = "tr"
    manager._write_autoconnect_script(str(project), auto_start=True)
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
    with caplog.at_level(logging.ERROR):
        assert manager.install_package(str(project), write_autoconnect=False) is False
    assert {p: p.read_bytes() for p in project.joinpath("Packages").rglob("*") if p.is_file()} == before
    assert "not made by Gamachine" in caplog.text


def test_failed_copy_keeps_the_previous_package(manager, tmp_path, source, monkeypatch):
    project = project_at(tmp_path / "Game")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    package = project.joinpath(*PACKAGE)
    before = {name: (package / name).read_bytes() for name in files(package)}
    (source / "package.json").write_text('{"name": "com.coplaydev.unity-mcp", "v": 2}', encoding="utf-8")
    monkeypatch.setattr(module, "_complete_digest", lambda path: None)
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
    manager._write_autoconnect_script(str(project), auto_start=True)
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
        "com.coplaydev.unity-mcp": "file:C:\\Users\\a\\.unity_architect_ai\\unity-mcp\\MCPForUnity",
        "com.unity.ugui": "2.0.0"}})
    (project / ".git").mkdir()
    legacy = project.joinpath(*LEGACY)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(LEGACY_TEXT, encoding="utf-8")
    assert manager.install_package(str(project), write_autoconnect=False) is True
    assert json.loads((project / "Packages" / "manifest.json").read_text(encoding="utf-8")) == {
        "dependencies": {"com.unity.ugui": "2.0.0"}}
    assert not legacy.exists()
    assert project.joinpath(*PACKAGE, ".gamachine-source-digest").is_file()
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
