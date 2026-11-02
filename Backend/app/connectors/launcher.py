"""What the agent may use now (enabled + installed connectors) and opening a connector's app for it."""
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from connectors import catalog, detect, settings, views
from connectors.installer import Installer
from spawn_env import build_spawn_env

logger = logging.getLogger(__name__)

USABLE_STATES = ("ready", "update_available")
OPEN_TIMEOUT_S = 60.0
# Same opt-out the installer passes: the add-on must not reach the network on start.
_APP_ENV = {"BLENDERMCP_NO_UPDATE_CHECK": "1"}

_installer: Optional[Installer] = None
_entries: Optional[dict] = None
_detector: Callable = detect.detect
_lock = threading.Lock()
# Connector id -> pid of the app Gamachine opened for the agent (kept running when the backend exits).
launched: Dict[str, int] = {}


def bind(installer: Optional[Installer] = None, entries: Optional[dict] = None,
         detector: Optional[Callable] = None) -> None:
    """main shares the routes' Installer; tests pass fakes."""
    global _installer, _entries, _detector
    _installer, _entries = installer, entries
    _detector = detector or detect.detect


def installer() -> Installer:
    global _installer
    with _lock:
        if _installer is None:
            _installer = Installer()
        return _installer


def entries() -> dict:
    global _entries
    with _lock:
        if _entries is None:
            _entries = catalog.load()
        return _entries


def _db(db):
    return settings._store if db is None else db


def state(cid: str, db=None) -> Optional[str]:
    """ConnectorView state of `cid`, or None when it is unknown or cannot be read."""
    entry = entries().get(cid)
    db = _db(db)
    if entry is None or db is None:
        return None
    try:
        detection = views.detect_for(entry, db, _detector)
        return views.state_of(entry, detection, installer())[0]
    except Exception:
        logger.exception("[connectors] state of %s failed", cid)
        return None


def ready_ids(db=None) -> List[str]:
    """Connectors switched on and installed: the ones the agent server exposes."""
    db = _db(db)
    if db is None:
        return []
    try:
        ids = [cid for cid in entries() if settings.enabled(db, cid)]
    except Exception:
        return []
    return [cid for cid in ids if state(cid, db) in USABLE_STATES]


def refusal(cid, tool, db) -> Optional[str]:
    """Why the connector itself refuses `tool` now (chat checks are the route's), else None."""
    entry = entries().get(cid) if isinstance(cid, str) else None
    if entry is None:
        return "Unknown connector."
    if not isinstance(tool, str) or (tool != "open" and tool not in entry["tools"]["allow"]):
        return f"{entry['name']} has no tool named {tool!r} for the agent."
    if not settings.enabled(db, cid):
        return (f"The {entry['name']} connector is switched off (Settings > Connectors). "
                "Tell the user; do not retry.")
    if state(cid, db) not in USABLE_STATES:
        return (f"The {entry['name']} connector is not installed or its app is missing "
                "(Settings > Connectors). Tell the user; do not retry.")
    return None


def launch_app(entry: dict, app_path: str) -> int:
    """Start the app detached from the backend; returns its pid."""
    env = build_spawn_env(overrides={**entry.get("env", {}), **_APP_ENV,
                                     **detect.blender_user_env(installer().blender_overrides)})
    if sys.platform == "win32":
        detached = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        # Outside the backend's job object first, so closing Gamachine leaves the user's Blender open;
        # a job that forbids breakaway refuses that flag.
        attempts = [detached | subprocess.CREATE_BREAKAWAY_FROM_JOB, detached]
    else:
        attempts = [0]
    for tried, flags in enumerate(attempts, start=1):
        try:
            return subprocess.Popen([app_path], env=env, cwd=str(Path(app_path).parent),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags,
                                    start_new_session=sys.platform != "win32").pid
        except OSError:
            if tried == len(attempts):
                raise


def open_app(cid: str, db, launch: Optional[Callable[[dict, str], int]] = None,
             answers: Optional[Callable[[str], bool]] = None, sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic, timeout_s: Optional[float] = None) -> dict:
    """Attach when the bridge already answers, else start the chosen app and wait for its bridge."""
    # Resolved per call so the route's caller (and its tests) can swap the module's launcher and probe.
    launch, answers = launch or launch_app, answers or views.bridge_answers
    timeout_s = OPEN_TIMEOUT_S if timeout_s is None else timeout_s
    entry = entries()[cid]
    detection = views.detect_for(entry, db, _detector)
    chosen = detection["chosen"]
    version = chosen["version"] if chosen else None
    if answers(cid):
        return {"ok": True, "already_running": True, "version": version}
    if not chosen:
        return {"ok": False, "error": f"{entry['name']} was not found on this computer (Settings > Connectors)."}
    try:
        pid = launch(entry, chosen["path"])
    except OSError as exc:
        return {"ok": False, "error": f"{entry['name']} could not be started: {exc}"}
    launched[cid] = pid
    logger.info("[connectors] opened %s %s (pid %s)", cid, version, pid)
    deadline = clock() + timeout_s
    while clock() < deadline:
        if answers(cid):
            return {"ok": True, "already_running": False, "version": version}
        sleep(1.0)
    return {"ok": False, "error": (f"{entry['name']} {version} was started but its MCP add-on did not answer "
                                   f"within {int(timeout_s)} s. Ask the user to check that the add-on is "
                                   "enabled in Blender, then call blender_open again.")}
