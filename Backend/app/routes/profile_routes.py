"""Local maker profile reads and UI-only reset."""
import logging

from fastapi import APIRouter, Header, HTTPException, Query

from agentic import approval_mode
from auth_utils import _check_token
import profile_stats


logger = logging.getLogger(__name__)


def create_profile_router(db):
    router = APIRouter()
    try:
        profile_stats.backfill_once(db)
    except Exception:
        logger.exception("[profile] backfill failed")

    # Sync handlers run blocking SQLite work in the threadpool (profile audit, 2 Oct 2026).
    @router.get("/profile/stats")
    def get_stats(
        range_: str = Query(default="all", alias="range"),
        x_session_token: str = Header(alias="X-Session-Token", default=""),
    ):
        _check_token(x_session_token)
        if range_ not in ("month", "6m", "all"):
            raise HTTPException(status_code=400, detail="Invalid profile range")
        return profile_stats.compute(db, range_)

    @router.post("/profile/reset")
    def reset(
        x_session_token: str = Header(alias="X-Session-Token", default=""),
        x_ui_secret: str = Header(alias="X-Gamachine-UI-Secret", default=""),
        x_maintenance: str = Header(alias="X-UnityAI-Maintenance", default=""),
    ):
        _check_token(x_session_token)
        if x_maintenance or not approval_mode.check_ui_secret(x_ui_secret):
            raise HTTPException(status_code=403, detail="Profile reset requires the app UI")
        cleared = db.clear_activity()
        db.set_setting("profile_achievements_seen", "{}")
        return {"cleared": cleared}

    return router
