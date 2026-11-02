"""On-demand connector install/remove: python -> package -> addon -> check, one job per connector.

Everything lives under `<app data>/connectors/<id>/<version>/` (venv, uv-managed CPython, uv cache),
so Remove deletes it all. The upstream package runs in its own venv and is never imported here.
"""
import glob
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from connectors import catalog, detect
from spawn_env import build_spawn_env

logger = logging.getLogger(__name__)

ADDON_FILENAME = "blender_mcp.py"
ADDON_MODULE = "blender_mcp"
# Same recognition rule as upstream addon_manager._is_blendermcp_addon_file / read_addon_protocol_version.
_ADDON_NAME_RE = re.compile(r"""["']name["']\s*:\s*["'](?:MCP for Blender|Blender MCP)["']""")
_PROTOCOL_RE = re.compile(r"ADDON_PROTOCOL_VERSION\s*=\s*(\d+)")
_OK = "GAMACHINE_OK"
# Written next to the version folders when Remove could not delete an add-on file.
REMOVE_MARKER = "remove-incomplete.json"

# (argv, spawn_env family, env overrides, cwd, register(proc)) -> (returncode, combined output)
Runner = Callable[[List[str], Optional[str], Dict[str, str], str, Callable[[subprocess.Popen], None]],
                  Tuple[int, str]]


class Busy(RuntimeError):
    pass


class StepError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = True):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


class Cancelled(RuntimeError):
    pass


def default_root(env=None, platform: str = sys.platform) -> Path:
    env = os.environ if env is None else env
    if platform == "win32":
        base = Path(env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Gamachine"
    elif platform == "darwin":
        base = Path(env.get("HOME") or Path.home()) / "Library" / "Application Support" / "Gamachine"
    else:
        base = Path(env.get("XDG_DATA_HOME") or Path(env.get("HOME") or Path.home()) / ".local" / "share") / "Gamachine"
    return base / "connectors"


def default_uv() -> str:
    # The bundled uv/ folder the Unity MCP launcher already resolves; imported late, it is heavy.
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    return unity_mcp_manager._get_uv(unity_mcp_manager._get_uvx())


def run_process(argv, family, overrides, cwd, register) -> Tuple[int, str]:
    env = build_spawn_env(family=family, overrides=overrides)
    proc = subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    register(proc)
    out, _ = proc.communicate()
    return proc.returncode, out or ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tail(output: str, limit: int = 600) -> str:
    lines = [line for line in (output or "").splitlines() if line.strip()]
    return "\n".join(lines[-4:])[-limit:]


def venv_bin(venv: Path, name: str, platform: str = sys.platform) -> Path:
    if platform == "win32":
        return venv / "Scripts" / (name + ".exe")
    return venv / "bin" / name


def read_protocol(path: Path) -> Optional[int]:
    try:
        match = _PROTOCOL_RE.search(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return None
    return int(match.group(1)) if match else None


def find_addons(addons_dir: Path) -> List[Path]:
    """MCP for Blender add-on files in `addons_dir` (single file or package __init__)."""
    found = []
    if not addons_dir.is_dir():
        return found
    for path in sorted(addons_dir.iterdir()):
        target = path / "__init__.py" if path.is_dir() else path
        if target.is_file() and target.suffix == ".py":
            try:
                if _ADDON_NAME_RE.search(target.read_text(encoding="utf-8", errors="ignore")):
                    found.append(target)
            except OSError:
                continue
    return found


def module_name(addon_file: Path) -> str:
    return addon_file.parent.name if addon_file.name == "__init__.py" else addon_file.stem


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Job:
    def __init__(self, entry: dict, app: Optional[dict], replace_addon: bool):
        self.id = uuid.uuid4().hex
        self.entry, self.app, self.replace_addon = entry, app, replace_addon
        self.state, self.step, self.steps_done = "running", catalog.STEPS[0], []
        self.downloaded_mb = None
        self.started_at = time.time()
        self.error: Optional[dict] = None
        self.cancel_event = threading.Event()
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.written: List[dict] = []
        self.previous: List[dict] = []

    def view(self) -> dict:
        return {"id": self.id, "state": self.state, "step": self.step, "steps_done": list(self.steps_done),
                "downloaded_mb": self.downloaded_mb, "started_at": self.started_at,
                "error": dict(self.error) if self.error else None}


class Installer:
    def __init__(self, root: Optional[Path] = None, runner: Runner = run_process,
                 uv: Callable[[], str] = default_uv, blender_overrides: Optional[Dict[str, str]] = None,
                 catalog_root: Optional[Path] = None, platform: str = sys.platform):
        self.root = Path(root) if root else default_root()
        self.runner, self.uv = runner, uv
        self.blender_overrides = blender_overrides or {}
        self.catalog_root = catalog_root
        self.platform = platform
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    # ---------- layout ----------
    def version_dir(self, entry: dict, version: Optional[str] = None) -> Path:
        return self.root / entry["id"] / (version or entry["package"]["version"])

    def installed(self, entry: dict) -> Optional[dict]:
        """install.json of the finished install (newest), with its folder under `dir`."""
        records = self._records(entry)
        return records[-1] if records else None

    def _records(self, entry: dict) -> List[dict]:
        records = []
        for path in sorted((self.root / entry["id"]).glob("*/install.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict):
                record["dir"] = str(path.parent)
                records.append(record)
        return sorted(records, key=lambda r: str(r.get("finished_at", "")))

    def addons_dir(self, app: dict) -> Path:
        env = dict(os.environ, **self.blender_overrides)
        return detect.user_addons_dir(app["version"], env=env, platform=self.platform)

    def addon_conflict(self, entry: dict, app: Optional[dict]) -> Optional[dict]:
        """An incompatible add-on Gamachine did not write, which only an explicit replace may overwrite."""
        if not app:
            return None
        decision = self._addon_decision(entry, app)
        return decision.get("conflict")

    def _addon_decision(self, entry: dict, app: dict, records: Optional[List[dict]] = None,
                        bundled_sha: Optional[str] = None) -> dict:
        needed = int(entry["package"].get("addon_protocol") or 0)
        records = self._records(entry) if records is None else records
        ours = {f.get("sha256"): f for r in records for f in r.get("addon_files", [])}
        if bundled_sha:
            # The pinned version's own add-on with no record: an install that died after writing it.
            ours.setdefault(bundled_sha, {})
        others = []
        for path in find_addons(self.addons_dir(app)):
            try:
                digest = sha256_file(path)
            except OSError:
                continue
            if digest in ours:
                # Gamachine's own file from an earlier install: overwritten in place, its backup carried on.
                return {"ours": path, "backup": ours[digest].get("backup")}
            others.append(path)
        for path in others:
            protocol = read_protocol(path)
            if protocol is not None and protocol >= needed:
                return {"user": path}
        if others:
            return {"conflict": {"path": str(others[0]), "found_protocol": read_protocol(others[0]),
                                 "needed_protocol": needed}}
        target = self.addons_dir(app) / ADDON_FILENAME
        if target.exists():
            # Another add-on's file under the name Gamachine writes: replaced only with consent and a backup.
            return {"conflict": {"path": str(target), "found_protocol": None, "needed_protocol": needed}}
        return {}

    # ---------- jobs ----------
    def job(self, cid: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(cid)

    def running(self, cid: str) -> bool:
        job = self.job(cid)
        return bool(job and job.state == "running")

    def start(self, entry: dict, app: Optional[dict], replace_addon: bool = False) -> Job:
        with self._lock:
            current = self._jobs.get(entry["id"])
            if current and current.state == "running":
                raise Busy(entry["id"])
            job = Job(entry, app, replace_addon)
            self._jobs[entry["id"]] = job
        job.thread = threading.Thread(target=self._run, args=(job,), name=f"connector-install-{entry['id']}",
                                      daemon=True)
        job.thread.start()
        return job

    def cancel(self, cid: str, wait_s: float = 15.0) -> bool:
        job = self.job(cid)
        if not job or job.state != "running":
            return False
        job.cancel_event.set()
        proc = job.proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if job.thread:
            # The worker deletes the partial folder; answer after it has.
            job.thread.join(wait_s)
        return True

    def _exec(self, job: Optional[Job], argv: List[str], family: Optional[str], overrides: Dict[str, str],
              cwd: str) -> Tuple[int, str]:
        def register(proc):
            if job is None:
                return
            job.proc = proc
            if job.cancel_event.is_set():
                proc.kill()
        rc, out = self.runner(argv, family, overrides, cwd, register)
        if job is not None:
            job.proc = None
            if job.cancel_event.is_set():
                raise Cancelled()
        return rc, out

    @staticmethod
    def _uv_overrides(entry: dict, vdir: Path) -> Dict[str, str]:
        return {"UV_PYTHON_INSTALL_DIR": str(vdir / "python"), "UV_CACHE_DIR": str(vdir / "cache"),
                **entry.get("env", {})}

    def _blender(self, job: Optional[Job], app_path: str, expr: str) -> Tuple[int, str]:
        overrides = {"BLENDERMCP_NO_UPDATE_CHECK": "1", **detect.blender_user_env(self.blender_overrides)}
        argv = [app_path, "--background", "--python-exit-code", "3", "--python-expr", expr]
        return self._exec(job, argv, None, overrides, str(Path(app_path).parent))

    def _build_dir(self, job: Job) -> Path:
        """An empty folder for this job. A repair builds next to the working install, which stays in use
        until the new one is finished."""
        vdir = self.version_dir(job.entry)
        if any(r.get("dir") == str(vdir) for r in job.previous):
            vdir = vdir.with_name(f"{vdir.name}-{job.id[:8]}")
        elif vdir.exists():
            shutil.rmtree(vdir)  # the leftover of an interrupted install
        vdir.mkdir(parents=True)
        return vdir

    def _run(self, job: Job) -> None:
        entry = job.entry
        vdir = None
        try:
            job.previous = self._records(entry)
            vdir = self._build_dir(job)
            record = self._install(job, vdir)
            # install.json last: its presence is what "installed" means.
            tmp = vdir / "install.json.tmp"
            tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
            os.replace(tmp, vdir / "install.json")
            for older in job.previous:
                # The record first: a folder a running server still holds may only partly go.
                try:
                    (Path(older["dir"]) / "install.json").unlink(missing_ok=True)
                except OSError:
                    logger.warning("[connectors] old install record kept: %s", older["dir"])
            for older in (self.root / entry["id"]).iterdir():
                if older != vdir:
                    if older.is_dir():
                        shutil.rmtree(older, ignore_errors=True)
                    elif older.name == REMOVE_MARKER:
                        older.unlink(missing_ok=True)
            job.state = "done"
        except Cancelled:
            self._undo(job, vdir)
            job.state = "cancelled"
        except StepError as exc:
            self._undo(job, vdir)
            job.error = {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
            job.state = "failed"
        except Exception as exc:  # the job must always end in a final state
            logger.exception("[connectors] install of %s failed", entry["id"])
            self._undo(job, vdir)
            job.error = {"code": "internal", "message": str(exc)[:600], "retryable": True}
            job.state = "failed"

    def _undo(self, job: Job, vdir: Optional[Path]) -> None:
        """Put back the add-on file this job replaced and drop its folder; the previous install, built
        elsewhere, is untouched."""
        for item in reversed(job.written):
            _remove_written(item["path"], item["sha256"], item.get("undo"))
        if vdir is not None and not (vdir / "install.json").is_file():
            shutil.rmtree(vdir, ignore_errors=True)

    def _step(self, job: Job, step: str) -> None:
        if job.cancel_event.is_set():
            raise Cancelled()
        job.step = step

    def _install(self, job: Job, vdir: Path) -> dict:
        entry, pkg = job.entry, job.entry["package"]
        if not job.app:
            raise StepError("app_missing", "No supported application was found.", retryable=False)
        uv = self.uv()
        uv_env = self._uv_overrides(entry, vdir)
        venv = vdir / "venv"

        self._step(job, "python")
        rc, out = self._exec(job, [uv, "venv", str(venv), "--python", pkg["python"], "--managed-python",
                                   "--no-config", "--quiet"], "uvx", uv_env, str(vdir))
        python = venv_bin(venv, "python", self.platform)
        if rc != 0 or not python.is_file():
            raise StepError("python_failed", _tail(out) or f"uv venv exited {rc}")
        job.steps_done.append("python")

        self._step(job, "package")
        lock = catalog.lock_path(entry, self.catalog_root)
        rc, out = self._exec(job, [uv, "pip", "install", "--python", str(python), "--require-hashes",
                                   "--no-deps", "--only-binary", ":all:", "--no-config", "-r", str(lock)],
                             "uvx", uv_env, str(vdir))
        entry_bin = venv_bin(venv, pkg["entry"], self.platform)
        if rc != 0 or not entry_bin.is_file():
            raise StepError("package_failed", _tail(out) or f"uv pip install exited {rc}")
        # The unpacked wheels are only needed during the install.
        shutil.rmtree(vdir / "cache", ignore_errors=True)
        job.steps_done.append("package")

        self._step(job, "addon")
        addon = self._addon_step(job, venv)
        job.steps_done.append("addon")

        self._step(job, "check")
        rc, out = self._blender(job, job.app["path"],
                                "import addon_utils; l, e = addon_utils.check(%r); "
                                "print(%r, l, e)" % (addon["module"], _OK))
        if rc != 0 or f"{_OK} True True" not in out:
            raise StepError("check_failed", _tail(out) or f"Blender exited {rc}")
        job.steps_done.append("check")

        app = {"path": job.app["path"], "version": job.app["version"]}
        current = {"path": addon["path"], "module": addon["module"], "sha256": addon["sha256"],
                   "backup": addon.get("backup")}
        # Add-ons an earlier install wrote for another Blender build stay on the record, so Remove finds them.
        addons = {a["path"]: a for r in job.previous for a in addon_entries(r)}
        addons[addon["path"]] = {**current, "owner": addon["owner"], "app": app}
        files = {f.get("path"): f for r in job.previous for f in r.get("addon_files", [])}
        files.update({w["path"]: {"path": w["path"], "sha256": w["sha256"], "backup": w["backup"]}
                      for w in job.written})
        return {
            "version": pkg["version"],
            "lock_sha256": sha256_file(lock),
            "python": str(python),
            "entry": str(entry_bin),
            "app": app,
            "addon_owner": addon["owner"],
            "addon": current,
            "addons": list(addons.values()),
            "addon_files": list(files.values()),
            "finished_at": _now(),
        }

    def _addon_step(self, job: Job, venv: Path) -> dict:
        source = self._bundled_addon(venv)
        decision = self._addon_decision(job.entry, job.app, job.previous, sha256_file(source))
        if "user" in decision:
            target = decision["user"]
            owner = "user"
        else:
            conflict = decision.get("conflict")
            if conflict and not job.replace_addon:
                raise StepError("addon_conflict", "Another add-on's file is in the way of the MCP for Blender "
                                "add-on; replacing it needs your confirmation.", retryable=False)
            backup = undo = None
            if "ours" in decision:
                target, backup = decision["ours"], decision.get("backup")
                undo = venv.parent / "addon-previous.py"
                shutil.copy2(target, undo)
            elif conflict:
                target = Path(conflict["path"])
                backup = undo = str(_backup_path(target))
                shutil.copy2(target, backup)
            else:
                target = self.addons_dir(job.app) / ADDON_FILENAME
                target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            job.written.append({"path": str(target), "sha256": sha256_file(target),
                                "backup": str(backup) if backup else None, "undo": str(undo) if undo else None})
            owner = "gamachine"
        module = module_name(target)
        rc, out = self._blender(job, job.app["path"],
                                "import addon_utils, bpy; "
                                "m = addon_utils.enable(%r, default_set=True, persistent=True); "
                                "bpy.ops.wm.save_userpref(); print(%r, bool(m))" % (module, _OK))
        if rc != 0 or f"{_OK} True" not in out:
            raise StepError("addon_failed", _tail(out) or f"Blender exited {rc}")
        written = job.written[-1] if owner == "gamachine" else None
        return {"owner": owner, "path": str(target), "module": module,
                "sha256": written["sha256"] if written else sha256_file(target),
                "backup": written["backup"] if written else None}

    def _bundled_addon(self, venv: Path) -> Path:
        pattern = os.path.join(str(venv), "**", "site-packages", "blender_mcp", "bundled", "addon.py")
        matches = glob.glob(pattern, recursive=True)
        if not matches:
            raise StepError("addon_failed", "The package has no bundled add-on.", retryable=False)
        return Path(matches[0])

    # ---------- remove ----------
    def remove_problems(self, entry: dict) -> List[str]:
        """What an earlier Remove could not delete (it is retried by the next Remove)."""
        try:
            data = json.loads((self.root / entry["id"] / REMOVE_MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        problems = data.get("problems") if isinstance(data, dict) else None
        return [str(p) for p in problems] if isinstance(problems, list) else []

    def remove(self, entry: dict) -> List[str]:
        """Disable and delete what Gamachine wrote, then the connector folder. Returns what is left; an
        add-on file that could not go keeps the records (and a marker the view shows) for the next Remove."""
        if self.running(entry["id"]):
            raise Busy(entry["id"])
        records = self._records(entry)
        problems = []
        for record in records:
            for addon in addon_entries(record):
                if addon.get("owner") == "gamachine":  # never disable or delete a user's own add-on
                    self._disable(addon)
            for item in record.get("addon_files", []):
                problem = _remove_written(item.get("path", ""), item.get("sha256"), item.get("backup"))
                if problem:
                    problems.append(problem)
        folder = self.root / entry["id"]
        if problems:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / REMOVE_MARKER).write_text(json.dumps({"problems": problems}, indent=2), encoding="utf-8")
        elif folder.exists():
            try:
                # The records first, so a folder a running server still holds reads as not installed.
                for record in records:
                    (Path(record["dir"]) / "install.json").unlink(missing_ok=True)
                shutil.rmtree(folder)
            except OSError as exc:
                problems.append(f"not deleted: {getattr(exc, 'filename', None) or folder}")
        with self._lock:
            job = self._jobs.get(entry["id"])
            if job and job.state != "running":
                del self._jobs[entry["id"]]
        return problems

    def _disable(self, addon: dict) -> None:
        app_path = (addon.get("app") or {}).get("path")
        if not (addon.get("module") and app_path and os.path.isfile(app_path)):
            return
        # A failure is logged, not fatal: with its file gone Blender cannot load the add-on anyway.
        try:
            rc, out = self._blender(None, app_path,
                                    "import addon_utils, bpy; addon_utils.disable(%r, default_set=True); "
                                    "bpy.ops.wm.save_userpref(); print(%r)" % (addon["module"], _OK))
            if rc != 0 or _OK not in out:
                logger.warning("[connectors] disable of %s failed: %s", addon["module"], _tail(out))
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("[connectors] disable of %s failed: %s", addon["module"], exc)


def addon_entries(record: dict) -> List[dict]:
    """Every add-on an install record lists, each with its owner and app; older records hold one."""
    if isinstance(record.get("addons"), list):
        return [a for a in record["addons"] if isinstance(a, dict) and a.get("path")]
    addon = record.get("addon")
    if not isinstance(addon, dict) or not addon.get("path"):
        return []
    return [{**addon, "owner": record.get("addon_owner"), "app": record.get("app")}]


def _backup_path(target: Path) -> Path:
    """`<name>.bak`, or a numbered one when that name is taken: an existing file is never overwritten."""
    backup, n = target.with_name(target.name + ".bak"), 1
    while backup.exists():
        backup, n = target.with_name(f"{target.name}.{n}.bak"), n + 1
    return backup


def _remove_written(path: str, sha256: Optional[str], restore: Optional[str]) -> Optional[str]:
    """Delete one file Gamachine wrote if its content is still ours; put `restore` back in its place.
    Returns the problem when a file that should go could not."""
    path = Path(path)
    try:
        if path.is_file():
            if sha256_file(path) != sha256:
                # The user changed it: it is theirs now and stays, like any file Gamachine did not write.
                logger.info("[connectors] kept changed file: %s", path)
                return None
            path.unlink()
        if restore and Path(restore).is_file() and not path.exists():
            os.replace(restore, path)
    except OSError as exc:
        return f"{path}: {exc}"
    return None
