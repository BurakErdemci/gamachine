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


def _unity_binary_prefix(command) -> bool:
    match = re.match(r"^\S.*?/(?:Unity\.app/Contents/MacOS/Unity|Editor/Unity)(?=\s|$)", command)
    if match is None:
        return False
    prefix = command[:match.end()]
    # Spaces within an installation folder are valid; a separate absolute
    # argument or option before the Unity path identifies another executable.
    return " -" not in prefix and re.search(r"\s/", prefix) is None


def _project_path_from_command(command) -> str | None:
    flag = re.search(r"(?<!\S)-projectPath(?=\s|$)", command, re.IGNORECASE)
    if flag is None:
        return None
    rest = command[flag.end():].strip()
    candidates = [rest] + [rest[:option.start()].strip() for option in re.finditer(r" -[a-zA-Z]", rest)]
    existing = [path for path in candidates if path and os.path.isdir(path)]
    return max(existing, key=len) if existing else rest


def _is_editor_for(pid, workspace_path) -> bool:
    command = _process_command(pid)
    if command is None:
        return False
    if sys.platform == "win32":
        return command.casefold() == "unity.exe"
    if not _unity_binary_prefix(command) or "-batchmode" in command.casefold():
        return False
    project = _project_path_from_command(command)
    if project:
        return _same_dir(project, workspace_path)
    return True


def open_project_names() -> list[str]:
    if sys.platform == "win32":
        return []
    try:
        result = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True, timeout=2)
        if result.returncode != 0:
            return []
        names = set()
        for line in result.stdout.splitlines():
            if _unity_binary_prefix(line) and "-batchmode" not in line.casefold():
                project = _project_path_from_command(line)
                if project and os.path.isdir(project):
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
            entry = re.match(r"^\s*-\s*path:\s*(.+?)\s*$", line)
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
