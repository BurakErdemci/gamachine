"""ConnectorView (CONTRACT.md section 3) from catalog, detection, install record and job."""
import os
import socket
from pathlib import Path
from typing import Callable

from connectors import detect, settings
from connectors.catalog import STEPS
from connectors.installer import Installer, addon_entries

# Where each connector's app-side bridge listens (upstream add-on default port).
BRIDGE_PORTS = {"blender": 9876}


def bridge_answers(cid: str, timeout: float = 0.2) -> bool:
    port = BRIDGE_PORTS.get(cid)
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def app_view(entry: dict, detection: dict) -> dict:
    return {"found": detection["found"], "chosen": detection["chosen"],
            "custom_path": detection["custom_path"], "min_version": entry["app"]["min_version"],
            "download_url": entry["app"]["download_url"]}


def _has_addon_for(installer: Installer, record: dict, chosen: dict) -> bool:
    # The add-on is per Blender major.minor, so a newly selected build needs its own copy.
    here = os.path.normcase(os.path.abspath(installer.addons_dir(chosen)))
    return any(os.path.normcase(os.path.abspath(os.path.dirname(a["path"]))) == here and os.path.isfile(a["path"])
               for a in addon_entries(record))


def state_of(entry: dict, detection: dict, installer: Installer) -> tuple:
    """(state, installed record or None, error or None)."""
    cid = entry["id"]
    record = installer.installed(entry)
    job = installer.job(cid)
    if job and job.state == "running":
        return "installing", record, None
    usable = bool(record) and Path(record.get("entry", "")).is_file()
    # A failed repair or update leaves the previous install working: it stays usable, the job shows the error.
    if job and job.state == "failed" and not usable:
        return "error", record, {"code": job.error["code"], "message": job.error["message"]}
    left = installer.remove_problems(entry)
    if left:
        return "error", record, {"code": "remove_incomplete",
                                 "message": "Remove did not finish; still there: " + "; ".join(left)[:400]}
    if record and not usable:
        return "error", record, {"code": "broken_install", "message": "The installed package is missing files."}
    chosen = detection["chosen"]
    if not chosen:
        too_old = detection["found"] and not detection["custom_path"]
        return ("app_too_old" if too_old else "app_missing"), record, None
    if not detect.at_least(chosen["version"], entry["app"]["min_version"]):
        return "app_too_old", record, None
    if not record:
        return "not_installed", None, None
    if record.get("version") != entry["package"]["version"] or not _has_addon_for(installer, record, chosen):
        return "update_available", record, None
    return "ready", record, None


def connector_view(entry: dict, db, installer: Installer, detection: dict,
                   connected: Callable[[str], bool] = bridge_answers) -> dict:
    cid = entry["id"]
    state, record, error = state_of(entry, detection, installer)
    installed = None
    if record:
        installed = {"version": record.get("version"), "addon_path": (record.get("addon") or {}).get("path"),
                     "addon_owner": record.get("addon_owner", "gamachine")}
    return {
        "id": cid, "name": entry["name"], "summary": entry["summary"],
        "capabilities": entry["capabilities"], "risk": entry["risk"],
        "state": state,
        "enabled": bool(record) and settings.enabled(db, cid),
        "connected": bool(record) and connected(cid),
        "app": app_view(entry, detection),
        "installed": installed,
        "error": error,
    }


def detect_for(entry: dict, db, detector: Callable = detect.detect) -> dict:
    return detector(entry["app"]["min_version"], settings.app_path(db, entry["id"]))


def plan_view(entry: dict, installer: Installer, detection: dict) -> dict:
    pkg = entry["package"]
    return {"download_mb": pkg["approx_download_mb"], "estimated_s": pkg["approx_install_s"],
            "target": str(installer.version_dir(entry)), "steps": list(STEPS),
            "app": app_view(entry, detection),
            "replace_addon": installer.addon_conflict(entry, detection["chosen"])}
