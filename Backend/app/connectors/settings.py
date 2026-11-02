"""Persisted per-connector switch and user-chosen application path."""
from typing import Optional

_store = None


def bind_store(db):
    global _store
    _store = db


def enabled_key(cid: str) -> str:
    return f"connector_{cid}_enabled"


def app_path_key(cid: str) -> str:
    return f"connector_{cid}_app_path"


def enabled_now(cid: str) -> bool:
    if _store is None:
        return False
    try:
        return enabled(_store, cid)
    except Exception:
        return False


def enabled(db, cid: str) -> bool:
    return db.get_setting(enabled_key(cid)) == "1"


def set_enabled(db, cid: str, on: bool) -> None:
    db.set_setting(enabled_key(cid), "1" if on else "0")


def app_path(db, cid: str) -> Optional[str]:
    return db.get_setting(app_path_key(cid)) or None


def set_app_path(db, cid: str, path: Optional[str]) -> None:
    db.set_setting(app_path_key(cid), path or "")
