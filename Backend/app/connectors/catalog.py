"""Gamachine's own connector catalog: one validated connector.json per connector."""
import json
import re
import sys
from pathlib import Path
from typing import Dict, Optional

# Owner decision: the agent never gets these, whatever a catalog entry says.
BANNED_TOOLS = frozenset({
    "generate_3d", "search_assets", "import_asset", "record_trajectory_feedback", "disable_telemetry",
})
STEPS = ("python", "package", "addon", "check")

_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
_VERSION = re.compile(r"^\d+(\.\d+)*$")


class CatalogError(ValueError):
    pass


def catalog_dir() -> Path:
    """Next to this module from source; under PyInstaller's data root when packaged."""
    bundle = getattr(sys, "_MEIPASS", None)
    return (Path(bundle) / "connectors" if bundle else Path(__file__).resolve().parent) / "catalog"


def _text_pair(entry, key, item_type):
    value = entry.get(key)
    if not isinstance(value, dict) or set(value) != {"tr", "en"}:
        raise CatalogError(f"{key} must have exactly tr and en")
    for lang in ("tr", "en"):
        item = value[lang]
        if item_type is str and not (isinstance(item, str) and item):
            raise CatalogError(f"{key}.{lang} must be a non-empty string")
        if item_type is list and not (isinstance(item, list) and item
                                      and all(isinstance(x, str) and x for x in item)):
            raise CatalogError(f"{key}.{lang} must be a non-empty list of strings")


def validate(entry, folder: Path) -> dict:
    if not isinstance(entry, dict):
        raise CatalogError("entry must be an object")
    cid = entry.get("id")
    if not isinstance(cid, str) or not _ID.match(cid) or cid != folder.name:
        raise CatalogError("id must match its folder name")
    if not isinstance(entry.get("name"), str) or not entry["name"]:
        raise CatalogError("name must be a non-empty string")
    _text_pair(entry, "summary", str)
    _text_pair(entry, "capabilities", list)
    _text_pair(entry, "risk", str)

    app = entry.get("app")
    if (not isinstance(app, dict) or not isinstance(app.get("min_version"), str)
            or not _VERSION.match(app["min_version"])
            or not isinstance(app.get("download_url"), str) or not app["download_url"].startswith("https://")):
        raise CatalogError("app needs min_version and an https download_url")

    pkg = entry.get("package")
    if not isinstance(pkg, dict):
        raise CatalogError("package must be an object")
    for key in ("name", "version", "python", "lock", "entry"):
        if not isinstance(pkg.get(key), str) or not pkg[key]:
            raise CatalogError(f"package.{key} must be a non-empty string")
    for key in ("approx_download_mb", "approx_install_s"):
        value = pkg.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise CatalogError(f"package.{key} must be a positive number")
    lock = folder / pkg["lock"]
    if lock.parent != folder or not lock.is_file():
        raise CatalogError("package.lock must be a file next to connector.json")
    if not _lock_is_hashed(lock.read_text(encoding="utf-8"), pkg["name"], pkg["version"]):
        raise CatalogError("package.lock must pin the package and hash every requirement")

    env = entry.get("env")
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise CatalogError("env must map strings to strings")

    tools = entry.get("tools")
    allow = tools.get("allow") if isinstance(tools, dict) else None
    if not isinstance(allow, list) or not allow or not all(isinstance(t, str) and t for t in allow):
        raise CatalogError("tools.allow must be a non-empty list of strings")
    banned = sorted(set(allow) & BANNED_TOOLS)
    if banned:
        raise CatalogError(f"tools.allow contains banned tools: {', '.join(banned)}")
    return entry


def _lock_is_hashed(text: str, name: str, version: str) -> bool:
    blocks = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", text)
    reqs = [b for b in blocks if re.match(r"^[A-Za-z0-9_.\-]+==", b)]
    pinned = any(b.lower().startswith(f"{name.lower()}=={version}") for b in reqs)
    return pinned and all("--hash=sha256:" in b for b in reqs)


def load(root: Optional[Path] = None) -> Dict[str, dict]:
    """Every catalog entry by id. An invalid entry raises: the catalog ships with the app."""
    root = root or catalog_dir()
    entries = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        path = folder / "connector.json"
        if not path.is_file():
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise CatalogError(f"{path}: {exc}") from exc
        try:
            entries[folder.name] = validate(entry, folder)
        except CatalogError as exc:
            raise CatalogError(f"{path}: {exc}") from exc
    return entries


def lock_path(entry: dict, root: Optional[Path] = None) -> Path:
    return (root or catalog_dir()) / entry["id"] / entry["package"]["lock"]
