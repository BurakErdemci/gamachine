"""Persisted opt-in and per-application grants."""
import json
import logging
from datetime import datetime, timezone

from .policy import is_hard_denied, valid_bundle_id

logger = logging.getLogger(__name__)
ENABLED_KEY = "computer_use_enabled"
GRANTS_KEY = "computer_use_app_grants"
CORRUPT_GRANTS_KEY = "computer_use_app_grants_corrupt"
_store = None


def bind_store(db):
    global _store
    _store = db


def enabled_now() -> bool:
    if _store is None:
        return False
    try:
        return enabled(_store)
    except Exception:
        return False


def enabled(db):
    return db.get_setting(ENABLED_KEY) == "1"


def set_enabled(db, on):
    db.set_setting(ENABLED_KEY, "1" if on else "0")


def _load_grants(db, *, require_backup=False):
    try:
        raw = db.get_setting(GRANTS_KEY)
    except Exception:
        logger.exception("Could not read computer-use grants; using an empty list")
        if require_backup:
            raise ValueError("Grants cannot be read; refusing to overwrite them")
        return []
    if raw is None:
        return []
    try:
        result = json.loads(raw)
        if not isinstance(result, list):
            raise ValueError("invalid grants list")
    except (ValueError, TypeError, RecursionError):
        logger.warning("Invalid computer_use_app_grants setting; using an empty list")
        try:
            db.set_setting(CORRUPT_GRANTS_KEY, raw)
        except Exception as exc:
            logger.exception("Could not preserve corrupt computer-use grants backup")
            if require_backup:
                raise ValueError("Corrupt grants backup must be preserved before overwrite") from exc
        return []
    valid = []
    for row in result:
        try:
            if (not isinstance(row, dict) or not valid_bundle_id(row.get("bundle_id"))
                    or not isinstance(row.get("app_name"), str) or not row["app_name"].strip()
                    or not isinstance(row.get("granted_at"), str)):
                raise ValueError("invalid grant row")
            datetime.fromisoformat(row["granted_at"])
        except ValueError:
            logger.warning("Invalid computer_use_app_grants row; skipping it")
            continue
        valid.append(row)
    return valid


def grants(db):
    return _load_grants(db)


def is_granted(db, bundle_id):
    return valid_bundle_id(bundle_id) and any(row["bundle_id"].casefold() == bundle_id.casefold() for row in grants(db))


def add_grant(db, bundle_id, app_name):
    if not valid_bundle_id(bundle_id) or is_hard_denied(bundle_id):
        raise ValueError("bundle_id cannot be granted")
    rows = _load_grants(db, require_backup=True)
    if any(row["bundle_id"].casefold() == bundle_id.casefold() for row in rows):
        return
    rows.append({"bundle_id": bundle_id,
                 "app_name": app_name if isinstance(app_name, str) and app_name.strip() else bundle_id,
                 "granted_at": datetime.now(timezone.utc).isoformat()})
    db.set_setting(GRANTS_KEY, json.dumps(rows))


def remove_grant(db, bundle_id):
    rows = grants(db)
    kept = [row for row in rows if row["bundle_id"].casefold() != bundle_id.casefold()]
    if len(rows) == len(kept):
        return False
    db.set_setting(GRANTS_KEY, json.dumps(kept))
    return True
