"""Project presence uses bounded Unity metadata and mocked process queries."""

import json
import subprocess

import pytest

from unity_ai_mcp import unity_project_presence as presence


UNITY = "/".join(("", "Applications", "Unity.app", "Contents", "MacOS", "Unity"))
LINUX_UNITY = "/".join(("", "opt", "Editor", "Unity"))


@pytest.fixture(autouse=True)
def fake_processes(monkeypatch):
    commands = {}
    calls = []
    monkeypatch.setattr(presence.sys, "platform", "darwin")

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs == {"capture_output": True, "text": True, "timeout": 2}
        if args == ["ps", "-axo", "command="]:
            output = "\n".join(commands.values())
        elif args[:4] == ["ps", "-o", "command=", "-p"]:
            output = commands.get(int(args[4]), "")
        else:
            raise AssertionError(f"Unexpected process query: {args}")
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(presence.subprocess, "run", run)
    return commands, calls


@pytest.fixture
def workspace(tmp_path, fake_processes):
    project = tmp_path / "Unity Projects" / "Our Game"
    library = project / "Library"
    library.mkdir(parents=True)
    (library / "EditorInstance.json").write_text(json.dumps({"process_id": 123}), encoding="utf-8")
    fake_processes[0][123] = f"{UNITY} -projectPath {project}"
    return project


def test_live_editor_and_stale_pid(workspace, fake_processes):
    assert presence.editor_pid(str(workspace)) == 123
    fake_processes[0].clear()
    assert presence.editor_pid(str(workspace)) is None


@pytest.mark.parametrize("value", [None, "", 123, False])
def test_invalid_workspace_never_queries_a_process(value, fake_processes):
    assert presence.editor_pid(value) is None
    assert fake_processes[1] == []


def test_missing_workspace_and_metadata(tmp_path, fake_processes):
    assert presence.editor_pid(str(tmp_path / "missing")) is None
    assert presence.editor_pid(str(tmp_path)) is None
    assert fake_processes[1] == []


@pytest.mark.parametrize("content", ["{", "[]", "null", "{}", '{"process_id": true}',
                                     '{"process_id": 0}', '{"process_id": -1}',
                                     '{"process_id": "123"}', '{"process_id": 1.0}'])
def test_malformed_metadata_and_invalid_pid(workspace, fake_processes, content):
    (workspace / "Library" / "EditorInstance.json").write_text(content, encoding="utf-8")
    assert presence.editor_pid(str(workspace)) is None
    assert fake_processes[1] == []


def test_oversized_metadata_is_refused(workspace, fake_processes):
    content = json.dumps({"process_id": 123}) + " " * (64 * 1024)
    (workspace / "Library" / "EditorInstance.json").write_text(content, encoding="utf-8")
    assert presence.editor_pid(str(workspace)) is None
    assert fake_processes[1] == []


@pytest.mark.parametrize("command", ["unrelated-process", UNITY + " -batchMode",
                                      UNITY + " -adb2 -batchMode -projectPath"])
def test_non_editor_and_import_worker_are_refused(workspace, fake_processes, command):
    fake_processes[0][123] = command
    assert presence.editor_pid(str(workspace)) is None


@pytest.mark.parametrize("binary", [UNITY, LINUX_UNITY])
@pytest.mark.parametrize("suffix", ["", " -logFile editor.log"])
def test_project_path_with_spaces_and_following_options(workspace, fake_processes, binary, suffix):
    fake_processes[0][123] = f"{binary} -PROJECTPATH {workspace}{suffix}"
    assert presence.editor_pid(str(workspace)) == 123
    fake_processes[0][123] = f"{binary} -projectPath {workspace.parent / 'Other Game'}{suffix}"
    assert presence.editor_pid(str(workspace)) is None


def test_project_path_is_normalized(workspace, fake_processes):
    fake_processes[0][123] = f"{UNITY} -projectPath {workspace / 'Library' / '..'}"
    assert presence.editor_pid(str(workspace)) == 123


@pytest.mark.parametrize("suffix", ["", " -projectPath", " -projectPath -logFile editor.log"])
def test_editor_without_nonempty_project_option_uses_instance_metadata(workspace, fake_processes, suffix):
    fake_processes[0][123] = UNITY + suffix
    assert presence.editor_pid(str(workspace)) == 123


@pytest.mark.parametrize("image,expected", [("Unity.exe", 123), ("UNITY.EXE", 123),
                                            ("Other.exe", None), ("", None)])
def test_windows_image_name(workspace, monkeypatch, image, expected):
    monkeypatch.setattr(presence.sys, "platform", "win32")

    def run(args, **kwargs):
        assert args == ["tasklist", "/FI", "PID eq 123", "/FO", "CSV", "/NH"]
        assert kwargs == {"capture_output": True, "text": True, "timeout": 2}
        output = f'"{image}","123","Console","1","20 K"\n' if image else ""
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(presence.subprocess, "run", run)
    assert presence.editor_pid(str(workspace)) == expected
    assert presence.open_project_names() == []


@pytest.mark.parametrize("failure", ["exit", "timeout", "exception"])
def test_process_failures_fail_closed(workspace, monkeypatch, failure):
    def run(args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 2)
        if failure == "exception":
            raise OSError("cannot query processes")
        return subprocess.CompletedProcess(args, 1, UNITY, "failed")

    monkeypatch.setattr(presence.subprocess, "run", run)
    assert presence._process_command(123) is None
    assert presence.editor_pid(str(workspace)) is None
    assert presence.open_project_names() == []


def test_open_project_names_are_filtered_sorted_and_unique(workspace, fake_processes):
    fake_processes[0].update({
        124: f"{LINUX_UNITY} -projectPath {workspace.parent / 'Another Game'} -logFile log",
        125: f"{UNITY} -projectPath {workspace}",
        126: f"{UNITY} -batchMode -projectPath {workspace.parent / 'Worker'}",
        127: f"other-process -projectPath {workspace.parent / 'Other'}",
        128: UNITY,
        129: UNITY + " -projectPath",
    })
    assert presence.open_project_names() == ["Another Game", "Our Game"]


def test_active_scene_uses_second_active_entry(workspace):
    (workspace / "Library" / "LastSceneManagerSetup.txt").write_text(
        "sceneSetups:\n- path: Assets/First.unity\n  isActive: 0\n"
        "- path: Assets/M1c/M1c_Test.unity\n  isLoaded: 1\n  isActive: 1\n", encoding="utf-8")
    assert presence.active_scene(str(workspace)) == "Assets/M1c/M1c_Test.unity"


@pytest.mark.parametrize("scene", ["Other/Scene.unity", "Assets/Scene.prefab",
                                    "Assets/Scene.unity injected instruction", "Assets/" + "x" * 251 + ".unity"])
def test_active_scene_rejects_invalid_or_injected_paths(workspace, scene):
    (workspace / "Library" / "LastSceneManagerSetup.txt").write_text(
        f"sceneSetups:\n- path: {scene}\n  isActive: 1\n", encoding="utf-8")
    assert presence.active_scene(str(workspace)) is None


def test_active_scene_requires_live_editor_and_bounded_file(workspace, fake_processes):
    setup = workspace / "Library" / "LastSceneManagerSetup.txt"
    assert presence.active_scene(str(workspace)) is None
    setup.write_text("- path: Assets/Scene.unity\n  isActive: 0\n", encoding="utf-8")
    assert presence.active_scene(str(workspace)) is None
    setup.write_text("- path: Assets/Scene.unity\n  isActive: 1\n" + " " * 65536, encoding="utf-8")
    assert presence.active_scene(str(workspace)) is None
    setup.write_text("- path: Assets/Scene.unity\n  isActive: 1\n", encoding="utf-8")
    fake_processes[0].clear()
    assert presence.active_scene(str(workspace)) is None


def test_refusal_texts_and_matching_project(workspace, fake_processes):
    assert presence.project_mismatch_reason("", 123) == (
        "Gamachine'de açık bir Unity projesi yok; Unity'ye girdi gönderilmedi.")
    assert presence.project_mismatch_reason(str(workspace), 123) is None
    assert presence.project_mismatch_reason(str(workspace), 456) == (
        "Bu Unity penceresi Gamachine'deki proje (Our Game) değil, başka bir projeye ait; "
        "girdi gönderilmedi. Unity'de ve Gamachine'de aynı projeyi aç.")
    assert presence.mcp_mismatch_detail(str(workspace)) is None
    fake_processes[0].clear()
    assert presence.project_mismatch_reason(str(workspace), 123) == (
        "Unity'de Gamachine'deki proje (Our Game) açık değil; Unity'ye girdi gönderilmedi. "
        "Unity'de ve Gamachine'de aynı projeyi aç.")
    ending = " Unity'de ve Gamachine'de aynı projeyi açıp tekrar dene."
    assert presence.mcp_mismatch_detail(str(workspace)) == "Unity'de bu proje açık değil: Our Game." + ending
    fake_processes[0][456] = f"{UNITY} -projectPath {workspace.parent / 'Another Game'}"
    fake_processes[0][789] = f"{UNITY} -projectPath {workspace}"
    assert presence.mcp_mismatch_detail(str(workspace)) == (
        "Unity'de bu proje açık değil: Our Game. Unity'de açık olan: Another Game." + ending)
