"""Persisted opt-in for the agent browser."""

ENABLED_KEY = "browser_use_enabled"
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
