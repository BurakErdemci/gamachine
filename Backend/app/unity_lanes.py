"""Unity lanes: a git worktree of the user's Unity project plus its own batch-mode editor.

A lane lives at <project>/.gamachine/lanes/<name> on branch gamachine/<name>. This module only
creates, starts, stops and removes lanes; wiring them into routes or tools happens elsewhere.
"""

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

import process_tree
from spawn_env import build_spawn_env

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
GB = 1024 ** 3
PER_LANE_BYTES = 3 * GB
HEADROOM_BYTES = 2 * GB
_GIT_TIMEOUT_S = 120
_EXCLUDE_LINE = ".gamachine/"

# Interface pin with the Unity side: the lane editor reads these two variables.
ENV_ALLOW_BATCH = "UNITY_MCP_ALLOW_BATCH"
ENV_LANE_ID = "GAMACHINE_LANE_ID"


class LaneError(Exception):
    """A lane operation failed; the message is a Turkish reason for the user."""


@dataclass
class LaneRecord:
    name: str
    dir: str
    branch: str
    pid: Optional[int] = None
    state: str = "created"
    proc: Any = field(default=None, repr=False)
    job: Any = field(default=None, repr=False)


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError("Şerit adı küçük harf, rakam ve tireden oluşmalı (en çok 32 karakter, harf ya da rakamla başlamalı).")
    return name


def lanes_root(project: str) -> str:
    return os.path.join(project, ".gamachine", "lanes")


def lane_dir(project: str, name: str) -> str:
    return os.path.join(lanes_root(project), name)


def _git(project: str, *args: str, timeout: int = _GIT_TIMEOUT_S) -> "subprocess.CompletedProcess":
    return subprocess.run(
        ["git", "-C", project, *args],
        capture_output=True, text=True, timeout=timeout,
        env=build_spawn_env(),
    )


def _git_out(project: str, *args: str) -> Optional[str]:
    try:
        res = _git(project, *args)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout.strip() if res.returncode == 0 else None


# ---------------------------------------------------------------- memory

def free_memory_bytes() -> Optional[int]:
    try:
        if sys.platform == "win32":
            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            status = _MemStatus()
            status.dwLength = ctypes.sizeof(_MemStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return int(status.ullAvailPhys)
        if sys.platform == "darwin":
            res = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10,
                                 env=build_spawn_env())
            if res.returncode != 0:
                return None
            page = re.search(r"page size of (\d+) bytes", res.stdout)
            free = re.search(r"Pages free:\s+(\d+)", res.stdout)
            inactive = re.search(r"Pages inactive:\s+(\d+)", res.stdout)
            if not (page and free and inactive):
                return None
            return (int(free.group(1)) + int(inactive.group(1))) * int(page.group(1))
        with open("/proc/meminfo", "r", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        return None
    return None


def memory_refusal(free_bytes: Optional[int], running_lanes: int) -> Optional[str]:
    if free_bytes is None:
        return "Boş bellek ölçülemedi, bu yüzden yeni şerit başlatılmadı."
    # Running lanes already hold their memory, so free_bytes covers only the new one.
    need = PER_LANE_BYTES + HEADROOM_BYTES
    if free_bytes < need:
        return (f"Yeni bir şerit için yeterli boş bellek yok: {free_bytes / GB:.1f} GB boş, "
                f"en az {need / GB:.1f} GB gerekir (şerit başına 3 GB + 2 GB pay); "
                f"şu an çalışan şerit: {running_lanes}.")
    return None


# ---------------------------------------------------------------- unity exe

def _hub_roots() -> List[str]:
    """Hub editor roots in lookup order: the user's secondary install path, then the default."""
    roots: List[str] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            try:
                with open(os.path.join(appdata, "UnityHub", "secondaryInstallPath.json"), "r", encoding="utf-8") as fh:
                    value = json.load(fh)
                if isinstance(value, str) and value.strip():
                    roots.append(value.strip())
            except (OSError, ValueError):
                pass
        roots.append(r"C:\Program Files\Unity\Hub\Editor")
    elif sys.platform == "darwin":
        roots.append("/Applications/Unity/Hub/Editor")
    else:
        roots.append(os.path.expanduser("~/Unity/Hub/Editor"))
    return roots


def _exe_under(root: str, version: str) -> str:
    if sys.platform == "win32":
        return os.path.join(root, version, "Editor", "Unity.exe")
    if sys.platform == "darwin":
        return os.path.join(root, version, "Unity.app", "Contents", "MacOS", "Unity")
    return os.path.join(root, version, "Editor", "Unity")


def project_editor_version(project: str) -> Optional[str]:
    try:
        with open(os.path.join(project, "ProjectSettings", "ProjectVersion.txt"), "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*m_EditorVersion:\s*(\S+)", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


_UNITY_VERSION = re.compile(r"\d+\.\d+\.\d+[abfpx]\d+")


def resolve_unity_exe(project: str) -> str:
    version = project_editor_version(project)
    if not version:
        raise LaneError("Projenin Unity sürümü okunamadı (ProjectSettings/ProjectVersion.txt).")
    # ProjectVersion.txt comes with the project, so it is untrusted: a version like
    # "..\\..\\x" or an absolute path would make os.path.join pick another program to run.
    if not _UNITY_VERSION.fullmatch(version):
        raise LaneError(f"Projenin Unity sürümü geçersiz: {version!r}")
    for root in _hub_roots():
        exe = _exe_under(root, version)
        real_root = os.path.realpath(root)
        try:
            inside = os.path.commonpath([real_root, os.path.realpath(exe)]) == real_root
        except ValueError:
            inside = False
        if inside and os.path.isfile(exe):
            return exe
    raise LaneError(f"Unity {version} bu bilgisayarda kurulu değil; Unity Hub'dan bu sürümü kurun.")


# ---------------------------------------------------------------- instance identity

def expected_instance_hash(lane_directory: str) -> str:
    """The /api/instances hash of a lane's editor.

    ProjectIdentityUtility.ComputeProjectHash hashes Application.dataPath: the absolute
    Assets path with forward slashes and no trailing slash, as UTF-8; SHA1, first 16 hex chars.
    """
    assets = os.path.abspath(os.path.join(lane_directory, "Assets")).replace("\\", "/")
    return hashlib.sha1(assets.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- create

def _install_package(directory: str) -> bool:
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    return bool(unity_mcp_manager.install_package(directory, write_autoconnect=True))


def _ensure_excluded(project: str) -> None:
    common = _git_out(project, "rev-parse", "--git-common-dir")
    if not common:
        raise LaneError("Git dizini bulunamadı.")
    common = common if os.path.isabs(common) else os.path.join(project, common)
    path = os.path.join(common, "info", "exclude")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    if _EXCLUDE_LINE in (ln.strip() for ln in existing.splitlines()):
        return
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(_EXCLUDE_LINE + "\n")


def _rollback(project: str, directory: str, branch: str) -> None:
    for args in (("worktree", "remove", "--force", directory), ("branch", "-D", branch)):
        try:
            _git(project, *args)
        except (OSError, subprocess.TimeoutExpired):
            pass


def create_lane(project: str, name: str) -> LaneRecord:
    validate_name(name)
    if not all(os.path.isdir(os.path.join(project, d)) for d in ("Assets", "ProjectSettings")):
        raise LaneError("Bu klasör bir Unity projesi değil (Assets ve ProjectSettings yok).")
    top = _git_out(project, "rev-parse", "--show-toplevel")
    if not top or os.path.normcase(os.path.realpath(top)) != os.path.normcase(os.path.realpath(project)):
        raise LaneError("Unity projesi, bir git deposunun kök klasörü olmalı.")
    if _git_out(project, "rev-parse", "--verify", "HEAD") is None:
        raise LaneError("Git deposunda henüz hiç commit yok; şerit için en az bir commit gerekir.")
    directory = lane_dir(project, name)
    if os.path.lexists(directory):
        raise LaneError(f"'{name}' şeridi zaten var.")
    branch = f"gamachine/{name}"
    _ensure_excluded(project)
    res = _git(project, "worktree", "add", "-b", branch, directory, "HEAD")
    if res.returncode != 0:
        raise LaneError(f"Git worktree açılamadı: {(res.stderr or res.stdout).strip()}")
    try:
        ok = _install_package(directory)
    except Exception as exc:
        _rollback(project, directory, branch)
        raise LaneError(f"MCP paketi şeride kurulamadı: {exc}")
    if not ok:
        _rollback(project, directory, branch)
        raise LaneError("MCP paketi şeride kurulamadı; şerit geri alındı.")
    return LaneRecord(name=name, dir=directory, branch=branch)


# ---------------------------------------------------------------- editor process

def lane_log_path(directory: str) -> str:
    return os.path.join(directory, "Logs", "gamachine-lane.log")


def start_editor(record: LaneRecord, *, free_bytes: Optional[int] = None, running: int = 0,
                 popen: Callable = subprocess.Popen) -> LaneRecord:
    reason = memory_refusal(free_bytes, running)
    if reason:
        raise LaneError(reason)
    exe = resolve_unity_exe(record.dir)
    log = lane_log_path(record.dir)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    env = build_spawn_env(overrides={ENV_ALLOW_BATCH: "1", ENV_LANE_ID: record.name})
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    else:
        kwargs["start_new_session"] = True
    proc = popen([exe, "-batchmode", "-projectPath", record.dir, "-logFile", log],
                 env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, **kwargs)
    record.proc = proc
    record.pid = getattr(proc, "pid", None)
    record.job = process_tree.new_job_object_for(proc)
    record.state = "starting"
    return record


def _log_tail(directory: str, lines: int = 20) -> str:
    try:
        with open(lane_log_path(directory), "r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:]).strip()
    except OSError:
        return ""


def _failure(record: LaneRecord, reason: str) -> str:
    tail = _log_tail(record.dir)
    return f"{reason}\n{tail}" if tail else reason


def wait_ready(record: LaneRecord, list_instances: Callable[[], list], timeout_s: float = 300,
               poll_s: float = 2, *, sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic) -> LaneRecord:
    want = expected_instance_hash(record.dir)
    deadline = clock() + timeout_s
    while True:
        proc = record.proc
        if proc is not None and proc.poll() is not None:
            record.state = "failed"
            raise LaneError(_failure(record, f"Unity süreci beklenmedik biçimde kapandı (kod {proc.poll()})."))
        try:
            instances = list_instances() or []
        except Exception:
            instances = []
        if any(isinstance(i, dict) and i.get("hash") == want for i in instances):
            record.state = "ready"
            return record
        if clock() >= deadline:
            record.state = "failed"
            raise LaneError(_failure(record, f"Şerit editörü {int(timeout_s)} sn içinde MCP'ye bağlanmadı."))
        sleep(poll_s)


def stop_editor(record: LaneRecord) -> LaneRecord:
    proc, job = record.proc, record.job
    if job is not None:
        try:
            job.terminate()
        except Exception:
            pass
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass
    if job is not None:
        try:
            job.close()
        except Exception:
            pass
    record.job = None
    record.proc = None
    record.pid = None
    lock = os.path.join(record.dir, "Temp", "UnityLockfile")
    try:
        if os.path.isfile(lock):
            os.remove(lock)
    except OSError:
        pass
    record.state = "stopped"
    return record


# ---------------------------------------------------------------- remove

def remove_lane(project: str, record: LaneRecord, delete_branch: bool = False) -> None:
    validate_name(record.name)
    root = os.path.normcase(os.path.realpath(lanes_root(project)))
    target = os.path.normcase(os.path.realpath(record.dir))
    if os.path.dirname(target) != root or os.path.basename(target) != record.name:
        raise LaneError("Şerit klasörü beklenen konumda değil; silme reddedildi.")
    if record.proc is not None or record.job is not None or record.state in ("starting", "ready"):
        stop_editor(record)
    if os.path.lexists(record.dir):
        res = _git(project, "worktree", "remove", "--force", record.dir)
        if res.returncode != 0:
            raise LaneError(f"Şerit silinemedi: {(res.stderr or res.stdout).strip()}")
    else:
        _git(project, "worktree", "prune")
    if delete_branch:
        _git(project, "branch", "-D", record.branch)
    record.state = "removed"
