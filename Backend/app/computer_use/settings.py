"""Persisted opt-in and per-application grants."""
import json
import logging
from datetime import datetime, timezone

from .policy import is_hard_denied

logger = logging.getLogger(__name__)
ENABLED_KEY = "computer_use_enabled"
GRANTS_KEY = "computer_use_app_grants"


def enabled(db):
    return db.get_setting(ENABLED_KEY) == "1"


def set_enabled(db, on):
    db.set_setting(ENABLED_KEY, "1" if on else "0")


def grants(db):
    raw = db.get_setting(GRANTS_KEY)
    if raw is None:
        return []
    try:
        result = json.loads(raw)
        if not isinstance(result, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("bundle_id"), str)
            or not row["bundle_id"].strip() or not isinstance(row.get("app_name"), str)
            or not isinstance(row.get("granted_at"), str) for row in result
        ):
            raise ValueError("invalid grants list")
        return result
    except (ValueError, TypeError):
        logger.warning("Invalid computer_use_app_grants setting; using an empty list")
        return []


def is_granted(db, bundle_id):
    return isinstance(bundle_id, str) and any(row["bundle_id"].casefold() == bundle_id.casefold() for row in grants(db))


def add_grant(db, bundle_id, app_name):
    if not isinstance(bundle_id, str) or not bundle_id.strip() or is_hard_denied(bundle_id):
        raise ValueError("bundle_id cannot be granted")
    rows = grants(db)
    if any(row["bundle_id"].casefold() == bundle_id.casefold() for row in rows):
        return
    rows.append({"bundle_id": bundle_id, "app_name": app_name or bundle_id,
                 "granted_at": datetime.now(timezone.utc).isoformat()})
    db.set_setting(GRANTS_KEY, json.dumps(rows))


def remove_grant(db, bundle_id):
    rows = grants(db)
    kept = [row for row in rows if row["bundle_id"].casefold() != bundle_id.casefold()]
    if len(rows) == len(kept):
        return False
    db.set_setting(GRANTS_KEY, json.dumps(kept))
    return True
