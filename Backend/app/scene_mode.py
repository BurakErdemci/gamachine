"""Per-project scene writing mode stored in UserSettings/Gamachine/scene_mode.json."""

import json
import os
import tempfile
import time

import unity_lanes

MODES = ("code", "mixed", "team")
DEFAULT = "mixed"
_REPLACE_ATTEMPTS = 80
_REPLACE_DELAY_S = 0.005


def _is_link(path: str) -> bool:
    return os.path.islink(path) or (hasattr(os, "isjunction") and os.path.isjunction(path))


def project_of(workspace: str) -> str:
    """Resolve a Unity lane (<P>/.gamachine/lanes/<name>) to its main project P; else return workspace."""
    if not workspace or not isinstance(workspace, str):
        return workspace
    real = os.path.realpath(workspace)
    name = os.path.basename(real)
    lanes_dir = os.path.dirname(real)
    gamachine_dir = os.path.dirname(lanes_dir)
    project = os.path.dirname(gamachine_dir)
    if (
        project
        and project != gamachine_dir
        and os.path.basename(lanes_dir) == "lanes"
        and os.path.basename(gamachine_dir) == ".gamachine"
    ):
        try:
            unity_lanes.validate_name(name)
            return project
        except ValueError:
            pass
    return workspace


def is_unity_project(workspace: str) -> bool:
    """True when workspace (lane -> main project) has the Assets and ProjectSettings folders."""
    if not workspace or not isinstance(workspace, str):
        return False
    project = project_of(workspace)
    return all(os.path.isdir(os.path.join(project, d)) for d in ("Assets", "ProjectSettings"))


def _settings_path(project: str) -> str:
    return os.path.join(project, "UserSettings", "Gamachine", "scene_mode.json")


def get(workspace: str) -> str:
    """Return the scene writing mode for workspace (lane -> main project), or DEFAULT on any error."""
    try:
        if not workspace or not isinstance(workspace, str):
            return DEFAULT
        path = _settings_path(project_of(workspace))
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            mode = data.get("mode")
            if isinstance(mode, str) and mode in MODES:
                return mode
    except Exception:
        pass
    return DEFAULT


def set(workspace: str, mode: str) -> str:
    """Persist the scene writing mode for workspace's Unity project atomically; return mode."""
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError(f"invalid scene mode: {mode!r} (expected one of {', '.join(MODES)})")
    if not workspace or not isinstance(workspace, str):
        raise ValueError("not a Unity project")
    if not is_unity_project(workspace):
        raise ValueError("not a Unity project")
    project = project_of(workspace)

    real_project = os.path.normcase(os.path.realpath(project))
    user_settings = os.path.join(project, "UserSettings")
    gamachine_dir = os.path.join(user_settings, "Gamachine")
    target = os.path.join(gamachine_dir, "scene_mode.json")
    for path in (user_settings, gamachine_dir, target):
        if _is_link(path):
            raise ValueError(f"refusing symlink or junction: {path}")
        real_path = os.path.normcase(os.path.realpath(path))
        try:
            inside = os.path.commonpath([real_path, real_project]) == real_project and real_path != real_project
        except ValueError:
            inside = False
        if not inside:
            raise ValueError(f"path resolves outside project: {path}")

    for d in (user_settings, gamachine_dir):
        if os.path.exists(d) and not os.path.isdir(d):
            raise ValueError(f"expected directory at {d}")
    if os.path.exists(target) and not os.path.isfile(target):
        raise ValueError(f"scene mode target is not a file: {target}")

    os.makedirs(gamachine_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=gamachine_dir, prefix=".scene_mode-", suffix=".tmp")
    try:
        os.close(fd)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump({"mode": mode}, fh)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp_path, target)
                break
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_DELAY_S)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return mode
