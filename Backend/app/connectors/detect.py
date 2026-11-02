"""Find installed Blender builds without opening the GUI: known install folders + `<exe> --version`."""
import glob as _glob
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from spawn_env import build_spawn_env

# Blender reads these itself; a test or a measured run points them at a throwaway profile.
BLENDER_USER_VARS = ("BLENDER_USER_CONFIG", "BLENDER_USER_SCRIPTS", "BLENDER_USER_RESOURCES",
                     "BLENDER_USER_DATAFILES", "BLENDER_USER_EXTENSIONS")
_VERSION_LINE = re.compile(r"^\s*Blender\s+(\d+\.\d+(?:\.\d+)?)\b")
_VDF_PATH = re.compile(r'"path"\s+"([^"]+)"')

# (path, mtime, size) -> parsed version or None; `--version` costs a process start per build.
_cache: Dict[Tuple[str, float, int], Optional[str]] = {}
_cache_lock = threading.Lock()


def parse_version(output: str) -> Optional[str]:
    lines = (output or "").strip().splitlines()
    match = _VERSION_LINE.match(lines[0]) if lines else None
    return match.group(1) if match else None


def version_tuple(version: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def at_least(version: str, minimum: str) -> bool:
    have, need = version_tuple(version), version_tuple(minimum)
    width = max(len(have), len(need))
    return have + (0,) * (width - len(have)) >= need + (0,) * (width - len(need))


def _steam_roots(env, isfile, read_text) -> List[str]:
    steam = os.path.join(env.get("ProgramFiles(x86)") or r"C:\Program Files (x86)", "Steam")
    roots = [steam]
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    if isfile(vdf):
        try:
            roots += [p.replace("\\\\", "\\") for p in _VDF_PATH.findall(read_text(vdf))]
        except OSError:
            pass
    return roots


def candidate_paths(env=None, platform: str = sys.platform,
                    glob: Callable[[str], Iterable[str]] = _glob.glob,
                    isfile: Callable[[str], bool] = os.path.isfile,
                    read_text: Callable[[str], str] = lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
                    which: Callable[[str], Optional[str]] = None) -> List[str]:
    env = os.environ if env is None else env
    found: List[str] = []
    if platform == "win32":
        for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            if env.get(var):
                found += sorted(glob(os.path.join(env[var], "Blender Foundation", "Blender*", "blender.exe")))
        for root in _steam_roots(env, isfile, read_text):
            found.append(os.path.join(root, "steamapps", "common", "Blender", "blender.exe"))
    elif platform == "darwin":
        home = env.get("HOME") or os.path.expanduser("~")
        for apps in ("/Applications", os.path.join(home, "Applications")):
            found += sorted(glob(os.path.join(apps, "Blender*.app", "Contents", "MacOS", "Blender")))
    else:
        located = (which or shutil.which)("blender")
        if located:
            found.append(located)
    unique: List[str] = []
    seen = set()
    for path in found:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and isfile(path):
            seen.add(key)
            unique.append(path)
    return unique


def run_version(path: str) -> str:
    result = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=30, env=build_spawn_env(overrides=blender_user_env()),
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    return result.stdout


def blender_user_env(overrides: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = {k: os.environ[k] for k in BLENDER_USER_VARS if k in os.environ}
    env.update(overrides or {})
    return env


def probe(path: str, run: Callable[[str], str] = run_version) -> Optional[str]:
    """Version of the build at `path`, or None when it is not a runnable Blender."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.normcase(os.path.abspath(path)), st.st_mtime, st.st_size)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        version = parse_version(run(path))
    except (OSError, subprocess.SubprocessError, ValueError):
        version = None
    with _cache_lock:
        _cache[key] = version
    return version


def detect(min_version: str, custom_path: Optional[str] = None,
           candidates: Callable[[], List[str]] = candidate_paths,
           run: Callable[[str], str] = run_version) -> dict:
    """`found` lists every runnable build; `chosen` is the custom path when it parses,
    else the newest build that meets `min_version`."""
    found = []
    for path in candidates():
        version = probe(path, run)
        if version:
            found.append({"version": version, "path": path})
    found.sort(key=lambda item: version_tuple(item["version"]), reverse=True)
    chosen = None
    if custom_path:
        version = probe(custom_path, run)
        if version:
            chosen = {"version": version, "path": custom_path}
    else:
        chosen = next((item for item in found if at_least(item["version"], min_version)), None)
    return {"found": found, "chosen": chosen, "custom_path": custom_path or None}


def user_addons_dir(version: str, env=None, platform: str = sys.platform) -> Path:
    """`<Blender user config>/<major.minor>/scripts/addons` for `version`; BLENDER_USER_SCRIPTS wins
    because Blender itself honours it."""
    env = os.environ if env is None else env
    if env.get("BLENDER_USER_SCRIPTS"):
        return Path(env["BLENDER_USER_SCRIPTS"]) / "addons"
    major_minor = ".".join(version.split(".")[:2])
    if platform == "win32":
        base = Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Blender Foundation" / "Blender"
    elif platform == "darwin":
        base = Path(env.get("HOME") or Path.home()) / "Library" / "Application Support" / "Blender"
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or Path(env.get("HOME") or Path.home()) / ".config") / "blender"
    return base / major_minor / "scripts" / "addons"
