"""Unity lanes: a git worktree of the user's Unity project plus its own batch-mode editor.

A lane lives at <project>/.gamachine/lanes/<name> on branch gamachine/<name>. This module only
creates, starts, stops and removes lanes; wiring them into routes or tools happens elsewhere.
"""

import ctypes
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import process_tree
from spawn_env import build_spawn_env

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
GB = 1024 ** 3
# Live measurement on two batch lanes: ~6.5 GB free RAM consumed per lane during import.
PER_LANE_BYTES = 6 * GB
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
    hub_snapshot: dict = field(default_factory=dict, repr=False)


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("Şerit adı küçük harf, rakam ve tireden oluşmalı (en çok 32 karakter, harf ya da rakamla başlamalı).")
    if name in _RESERVED_NAMES:
        raise ValueError(f"Şerit adı olarak '{name}' kullanılamaz (ayrılmış sistem adı).")
    return name


def lanes_root(project: str) -> str:
    return os.path.join(project, ".gamachine", "lanes")


def _lanes_root_inside(project: str) -> bool:
    real_proj = os.path.normcase(os.path.realpath(project))
    real_root = os.path.normcase(os.path.realpath(lanes_root(project)))
    try:
        return os.path.commonpath([real_proj, real_root]) == real_proj and real_root != real_proj
    except ValueError:
        return False


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


def memory_refusal(free_bytes: Optional[int], running_lanes: int, starting_lanes: int = 0) -> Optional[str]:
    if free_bytes is None:
        return "Boş bellek ölçülemedi, bu yüzden yeni şerit başlatılmadı."
    # Ready lanes already hold their memory; lanes still starting have not allocated theirs yet.
    need = PER_LANE_BYTES * (1 + starting_lanes) + HEADROOM_BYTES
    if free_bytes < need:
        return (f"Yeni bir şerit için yeterli boş bellek yok: {free_bytes / GB:.1f} GB boş, "
                f"en az {need / GB:.1f} GB gerekir (şerit başına 6 GB + 2 GB pay); "
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


_UNITY_VERSION = re.compile(r"\d+\.\d+\.\d+[abfpx]\d+(?:c\d+)?")


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


def _rollback(project: str, directory: str, branch: str, *, delete_branch: bool = True) -> None:
    if not _lanes_root_inside(project):
        logger.warning("Şerit geri alma atlandı: şerit kök klasörü proje dışında (%s).", project)
        return
    commands = [("worktree", "remove", "--force", directory)]
    if delete_branch:
        commands.append(("branch", "-D", branch))
    for args in commands:
        try:
            _git(project, *args)
        except (OSError, subprocess.TimeoutExpired):
            pass


def create_lane(project: str, name: str) -> LaneRecord:
    validate_name(name)
    if not all(os.path.isdir(os.path.join(project, d)) for d in ("Assets", "ProjectSettings")):
        raise LaneError("Bu klasör bir Unity projesi değil (Assets ve ProjectSettings yok).")
    if not _lanes_root_inside(project):
        raise LaneError("Şerit kök klasörü proje klasörünün dışına yönlendirilmiş; şerit oluşturulamadı.")
    top = _git_out(project, "rev-parse", "--show-toplevel")
    if not top or os.path.normcase(os.path.realpath(top)) != os.path.normcase(os.path.realpath(project)):
        raise LaneError("Unity projesi, bir git deposunun kök klasörü olmalı.")
    if _git_out(project, "rev-parse", "--verify", "HEAD") is None:
        raise LaneError("Git deposunda henüz hiç commit yok; şerit için en az bir commit gerekir.")
    directory = lane_dir(project, name)
    if os.path.lexists(directory):
        raise LaneError(f"'{name}' şeridi zaten var.")
    branch = f"gamachine/{name}"
    branch_existed = _git_out(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is not None
    _ensure_excluded(project)
    if branch_existed:
        res = _git(project, "worktree", "add", directory, branch)
    else:
        res = _git(project, "worktree", "add", "-b", branch, directory, "HEAD")
    if res.returncode != 0:
        raise LaneError(f"Git worktree açılamadı: {(res.stderr or res.stdout).strip()}")
    try:
        ok = _install_package(directory)
    except Exception as exc:
        _rollback(project, directory, branch, delete_branch=not branch_existed)
        raise LaneError(f"MCP paketi şeride kurulamadı: {exc}")
    if not ok:
        _rollback(project, directory, branch, delete_branch=not branch_existed)
        raise LaneError("MCP paketi şeride kurulamadı; şerit geri alındı.")
    return LaneRecord(name=name, dir=directory, branch=branch)


# ---------------------------------------------------------------- hub registry

_HUB_KEY = r"Software\Unity Technologies\Unity Editor 5.x"
_HUB_PREFIXES = ("LastUsedProjectPath_", "kWorkspacePath_", "kProjectBasePath_")


class _WinRegistry:
    """Thin HKCU registry wrapper for Unity Editor 5.x last-project values; no-op off Windows."""

    def read_all(self) -> Dict[str, bytes]:
        if sys.platform != "win32":
            return {}
        import winreg

        out: Dict[str, bytes] = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HUB_KEY, 0, winreg.KEY_READ) as key:
                idx = 0
                while True:
                    try:
                        name, val, val_type = winreg.EnumValue(key, idx)
                    except OSError:
                        break
                    idx += 1
                    if (
                        name.startswith(_HUB_PREFIXES)
                        and val_type == winreg.REG_BINARY
                        and isinstance(val, (bytes, bytearray))
                    ):
                        out[name] = bytes(val)
        except OSError:
            return {}
        return out

    def write(self, name: str, data: bytes) -> None:
        if sys.platform != "win32":
            return
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HUB_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, bytes(data))


_registry = _WinRegistry()


def _path_matches_prefix(candidate_norm: str, target_norm: str) -> bool:
    if not target_norm:
        return False
    return candidate_norm == target_norm or candidate_norm.startswith(target_norm + "/")


def restore_hub_values(record: LaneRecord, registry: Any = None) -> int:
    """Write back snapshot Hub values that currently point into the lane folder."""
    snapshot = getattr(record, "hub_snapshot", None)
    if not snapshot:
        return 0
    reg = _registry if registry is None else registry
    try:
        current = reg.read_all() or {}
    except Exception as exc:
        logger.warning("Unity Hub kayıt defteri değerleri okunamadı: %s", exc)
        return 0

    try:
        lane_abs = os.path.abspath(record.dir).replace("\\", "/").rstrip("/")
        lane_norm = lane_abs.casefold()
        parent_norm = os.path.dirname(lane_abs).replace("\\", "/").rstrip("/").casefold()
    except Exception as exc:
        logger.warning("Şerit yolu çözümlenemedi: %s", exc)
        return 0

    written = 0
    for name, old_bytes in snapshot.items():
        if name not in current:
            continue
        cur_bytes = current[name]
        if not isinstance(cur_bytes, (bytes, bytearray)):
            continue
        try:
            decoded = bytes(cur_bytes).decode("utf-8", errors="replace").rstrip("\x00")
            cur_norm = decoded.replace("\\", "/").casefold()
            matches = _path_matches_prefix(cur_norm, lane_norm)
            if not matches and name.startswith("kWorkspacePath_"):
                matches = _path_matches_prefix(cur_norm, parent_norm)
            if not matches:
                continue
            # A snapshot taken while another lane ran can hold that lane's path; never write it back.
            old_norm = bytes(old_bytes).decode("utf-8", errors="replace").replace("\\", "/").casefold()
            if "/.gamachine/lanes/" in old_norm:
                continue
            reg.write(name, bytes(old_bytes))
            written += 1
        except Exception as exc:
            logger.warning("Unity Hub kayıt defteri değeri geri yazılamadı (%s): %s", name, exc)
    return written


# ---------------------------------------------------------------- editor process

def lane_log_path(directory: str) -> str:
    return os.path.join(directory, "Logs", "gamachine-lane.log")


def start_editor(record: LaneRecord, *, free_bytes: Optional[int] = None, running: int = 0,
                 starting: int = 0, popen: Callable = subprocess.Popen) -> LaneRecord:
    if record.proc is not None and record.proc.poll() is None:
        raise LaneError("Bu şeridin editörü zaten çalışıyor.")
    reason = memory_refusal(free_bytes, running, starting_lanes=starting)
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
    try:
        record.hub_snapshot = dict(_registry.read_all() or {})
    except Exception:
        record.hub_snapshot = {}
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


def _has_license_error(directory: str) -> bool:
    try:
        with open(lane_log_path(directory), "r", encoding="utf-8", errors="replace") as fh:
            return "No valid Unity Editor license" in fh.read()
    except OSError:
        return False


def _failure(record: LaneRecord, reason: str) -> str:
    tail = _log_tail(record.dir)
    return f"{reason}\n{tail}" if tail else reason


def wait_ready(record: LaneRecord, list_instances: Callable[[], list], timeout_s: float = 300,
               poll_s: float = 2, *, sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic,
               probe: Optional[Callable[[dict], bool]] = None) -> LaneRecord:
    want = expected_instance_hash(record.dir)
    deadline = clock() + timeout_s
    matched: Optional[dict] = None
    while True:
        proc = record.proc
        if proc is not None and proc.poll() is not None:
            rc = proc.poll()
            record.state = "failed"
            if rc == 198 or _has_license_error(record.dir):
                raise LaneError(
                    "Unity lisansı bulunamadı: Unity Hub'ı açıp oturum açın ve "
                    "lisansın etkin olduğunu kontrol edin, sonra tekrar deneyin."
                )
            raise LaneError(_failure(record, f"Unity süreci beklenmedik biçimde kapandı (kod {rc})."))
        if matched is None:
            try:
                instances = list_instances() or []
            except Exception:
                instances = []
            matched = next((i for i in instances if isinstance(i, dict) and i.get("hash") == want), None)
        if matched is not None:
            ready = probe is None
            if not ready:
                try:
                    ready = bool(probe(matched))
                except Exception:
                    ready = False
            if ready:
                record.state = "ready"
                try:
                    restore_hub_values(record)
                except Exception as exc:
                    logger.warning("Unity Hub son proje yolu geri yüklenemedi: %s", exc)
                return record
        if clock() >= deadline:
            record.state = "failed"
            raise LaneError(_failure(record, f"Şerit editörü {int(timeout_s)} sn içinde MCP'ye bağlanmadı."))
        sleep(poll_s)


def stop_editor(record: LaneRecord, *, sleep: Callable[[float], None] = time.sleep) -> LaneRecord:
    proc, job = record.proc, record.job
    if job is not None:
        try:
            job.terminate()
        except Exception:
            pass
    if proc is not None:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except (subprocess.TimeoutExpired, OSError):
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
    try:
        restore_hub_values(record)
    except Exception as exc:
        logger.warning("Unity Hub son proje yolu geri yüklenemedi: %s", exc)
    lock = os.path.join(record.dir, "Temp", "UnityLockfile")
    for attempt in range(10):
        if not os.path.isfile(lock):
            break
        try:
            os.remove(lock)
            break
        except FileNotFoundError:
            break
        except OSError:
            if attempt < 9:
                sleep(0.5)
    record.state = "stopped"
    return record


# ---------------------------------------------------------------- remove

def remove_lane(project: str, record: LaneRecord, delete_branch: bool = False) -> None:
    validate_name(record.name)
    if not _lanes_root_inside(project):
        raise LaneError("Şerit kök klasörü proje klasörünün dışına yönlendirilmiş; silme reddedildi.")
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
