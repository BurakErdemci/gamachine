"""Match a project's Unity editor process and read its active scene."""

import csv
import json
import os
import re
import subprocess
import sys


_MAX_FILE_BYTES = 64 * 1024


def _read_library_file(workspace_path, filename):
    path = os.path.join(workspace_path, "Library", filename)
    if os.path.getsize(path) > _MAX_FILE_BYTES:
        return None
    with open(path, "rb") as source:
        data = source.read(_MAX_FILE_BYTES + 1)
    return data.decode("utf-8") if len(data) <= _MAX_FILE_BYTES else None


def editor_pid(workspace_path) -> int | None:
    try:
        if not isinstance(workspace_path, str) or not workspace_path or not os.path.isdir(workspace_path):
            return None
        content = _read_library_file(workspace_path, "EditorInstance.json")
        if content is None:
            return None
        pid = json.loads(content).get("process_id")
        if type(pid) is int and pid > 0 and _is_editor_for(pid, workspace_path):
            return pid
    except Exception:
        pass
    return None


def _process_command(pid) -> str | None:
    try:
        args = (["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]
                if sys.platform == "win32" else ["ps", "-o", "command=", "-p", str(pid)])
        result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        if result.returncode != 0:
            return None
        if sys.platform == "win32":
            row = next(csv.reader(result.stdout.splitlines()), [])
            return row[0] if row and row[0] else None
        return result.stdout.strip() or None
    except Exception:
        return None


def _same_dir(a, b) -> bool:
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except Exception:
        pass
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _process_executable(pid) -> str | None:
    try:
        result = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        pass
    return None


def _is_unity_editor_executable(path) -> bool:
    return isinstance(path, str) and path.endswith(("/Unity.app/Contents/MacOS/Unity", "/Editor/Unity"))


def _is_import_worker(command) -> bool:
    return re.search(r"(?<!\S)-batchmode(?=\s|$)", command, re.IGNORECASE) is not None


def _project_value(command) -> str | None:
    flag = re.search(r"(?<!\S)-projectPath(?=\s|$)", command, re.IGNORECASE)
    return command[flag.end():].strip() if flag is not None else None


def _project_candidates(command) -> list[str]:
    rest = _project_value(command)
    if not rest:
        return []
    candidates = [rest] + [rest[:option.start()].strip() for option in re.finditer(r" -[a-zA-Z]", rest)]
    return [path for path in candidates if path and os.path.isdir(path)]


def _project_of(pid, command) -> str | None:
    matches = []
    for candidate in _project_candidates(command):
        try:
            content = _read_library_file(candidate, "EditorInstance.json")
            if content is None:
                continue
            recorded_pid = json.loads(content).get("process_id")
            if type(recorded_pid) is int and recorded_pid == pid:
                matches.append(candidate)
        except Exception:
            continue
    return matches[0] if len(matches) == 1 else None


def _is_editor_for(pid, workspace_path) -> bool:
    if sys.platform != "win32" and not _is_unity_editor_executable(_process_executable(pid)):
        return False
    command = _process_command(pid)
    if command is None:
        return False
    if sys.platform == "win32":
        return command.casefold() == "unity.exe"
    if _is_import_worker(command):
        return False
    if not _project_value(command):
        return True
    project = _project_of(pid, command)
    return project is not None and _same_dir(project, workspace_path)


def open_project_names() -> list[str]:
    if sys.platform == "win32":
        return []
    try:
        result = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=2)
        if result.returncode != 0:
            return []
        names = set()
        for line in result.stdout.splitlines():
            process = re.match(r"^\s*(\d+)\s+(.+?)\s*$", line)
            if process is None or not _is_unity_editor_executable(process.group(2)):
                continue
            pid = int(process.group(1))
            command = _process_command(pid)
            if command is None or _is_import_worker(command):
                continue
            project = _project_of(pid, command)
            if project is not None:
                names.add(os.path.basename(project))
        return sorted(names)
    except Exception:
        return []


def active_scene(workspace_path) -> str | None:
    try:
        if editor_pid(workspace_path) is None:
            return None
        content = _read_library_file(workspace_path, "LastSceneManagerSetup.txt")
        if content is None:
            return None
        scene = None
        for line in content.splitlines():
            entry = re.match(r"^\s*-\s*path:\s*(.*?)\s*$", line)
            if entry:
                scene = entry.group(1)
            elif scene is not None and re.match(r"^\s*isActive:\s*1\s*$", line):
                if (not re.fullmatch(r"Assets/[^\r\n]{1,250}\.unity", scene)
                        or any(segment in (".", "..") for segment in scene.split("/"))
                        or any(char in scene for char in "[]`<>")):
                    return None
                path = os.path.join(workspace_path, scene)
                root = os.path.realpath(workspace_path)
                if os.path.isfile(path) and os.path.commonpath((root, os.path.realpath(path))) == root:
                    return scene
                return None
    except Exception:
        pass
    return None


def project_mismatch_reason(workspace_path, target_pid) -> str | None:
    if not workspace_path:
        return "Gamachine'de açık bir Unity projesi yok; Unity'ye girdi gönderilmedi."
    expected = editor_pid(workspace_path)
    name = os.path.basename(os.path.normpath(workspace_path))
    if expected is None:
        return (f"Unity'de Gamachine'deki proje ({name}) açık değil; Unity'ye girdi gönderilmedi. "
                "Unity'de ve Gamachine'de aynı projeyi aç.")
    if target_pid != expected:
        return (f"Bu Unity penceresi Gamachine'deki proje ({name}) değil, başka bir projeye ait; "
                "girdi gönderilmedi. Unity'de ve Gamachine'de aynı projeyi aç.")
    return None


def mcp_mismatch_detail(workspace_path) -> str | None:
    if editor_pid(workspace_path) is not None:
        return None
    name = os.path.basename(os.path.normpath(workspace_path))
    others = [n for n in open_project_names() if n != name]
    return (f"Unity'de bu proje açık değil: {name}."
            + (f" Unity'de açık olan: {', '.join(others)}." if others else "")
            + " Unity'de ve Gamachine'de aynı projeyi açıp tekrar dene.")


def project_scope_detail(workspace_path, project_path) -> str | None:
    if not workspace_path:
        return "Gamachine'de açık bir Unity projesi yok; Unity'ye istek gönderilmedi."
    if not project_path:
        return "Hedef Unity editörü proje yolunu bildirmedi; istek gönderilmedi."
    if _same_dir(project_path, workspace_path):
        return None
    target = os.path.basename(os.path.normpath(project_path))
    name = os.path.basename(os.path.normpath(workspace_path))
    return (f"Bu istek Unity'de açık olan projeye ({target}) gidiyor, Gamachine'de açık proje ise {name}; "
            "istek gönderilmedi. Unity'de ve Gamachine'de aynı projeyi aç.")
