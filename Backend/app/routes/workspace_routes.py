import logging
import os

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from auth_utils import get_current_user, require_user
from schemas import WorkspaceRequest

logger = logging.getLogger(__name__)

# The most rows get_recent_workspaces returns: a paired device picks from the
# saved list it is shown, never a new root.
_SAVED_LOOKUP_LIMIT = 50


# Set by create_workspace_router: routes that have no db of their own (the
# Unity toggle) read the saved list through it.
_db = None


def require_saved_workspace_when_remote(path, token) -> None:
    """A paired device may only act on a workspace root the user already saved;
    authorising a new one stays desktop-only (docs/remote-control-web.md). Remote
    is decided by the dispatcher's REMOTE_DEVICE, which no HTTP client can set.
    Paths compare as save_workspace does."""
    from remote import ui_dispatch
    if ui_dispatch.REMOTE_DEVICE.get() is None:
        return
    user_id, _ = get_current_user(_db, token)
    if _db is None or not any(w["path"] == path for w in _db.get_recent_workspaces(user_id, _SAVED_LOOKUP_LIMIT)):
        raise HTTPException(status_code=403, detail="remote_forbidden")


def create_workspace_router(db):
    global _db
    _db = db
    router = APIRouter()

    @router.post("/save-workspace")
    async def save_workspace(req: WorkspaceRequest, background_tasks: BackgroundTasks,
                             x_session_token: str = Header(alias="X-Session-Token")):
        user_id, _ = require_user(db, x_session_token, req.user_id)
        require_saved_workspace_when_remote(req.path, x_session_token)
        previous_workspace = None
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        # One switch at a time: the persisted workspace order must equal the MCP switch order.
        async with unity_mcp_manager.mcp_lock:
            try:
                previous_workspace = db.get_last_workspace(user_id)
            except Exception as e:
                logger.debug("[Workspace] Previous workspace unavailable: %s", e)
            db.save_workspace(user_id, req.path)
            try:
                from routes.mcp_routes import start_mcp_for, stop_mcp

                # enabled covers the starting state, where is_running() is still False.
                if unity_mcp_manager.enabled or unity_mcp_manager.is_running():
                    active_workspace = unity_mcp_manager.active_workspace
                    if not active_workspace or (
                        os.path.normcase(os.path.realpath(active_workspace))
                        != os.path.normcase(os.path.realpath(req.path))
                    ):
                        stop_mcp()
                        try:
                            await start_mcp_for(req.path, background_tasks, language=unity_mcp_manager.language)
                        except HTTPException as e:
                            return {"status": "success", "mcp": {"state": "off", "reason": e.detail}}
                        return {"status": "success", "mcp": {"state": "switched"}}
                else:
                    unity_mcp_manager.reset_autoconnect_off(
                        unity_mcp_manager.active_workspace or previous_workspace)
            except Exception as e:
                logger.debug("[Workspace] Autoconnect reset skipped: %s", e)
        return {"status": "success"}

    @router.post("/remember-workspace")
    async def remember_workspace(req: WorkspaceRequest, x_session_token: str = Header(alias="X-Session-Token")):
        """Lists a created project in the recents without switching MCP or the agent's workspace."""
        user_id, _ = require_user(db, x_session_token, req.user_id)
        require_saved_workspace_when_remote(req.path, x_session_token)
        db.remember_workspace(user_id, req.path)
        return {"status": "success"}

    @router.get("/last-workspace/{user_id}")
    async def get_last_workspace(user_id: int, x_session_token: str = Header(alias="X-Session-Token")):
        require_user(db, x_session_token, user_id)
        path = db.get_last_workspace(user_id)
        return {"path": path}

    @router.get("/recent-workspaces/{user_id}")
    async def get_recent_workspaces(user_id: int, limit: int = 12,
                                    x_session_token: str = Header(alias="X-Session-Token")):
        require_user(db, x_session_token, user_id)
        return {"workspaces": db.get_recent_workspaces(user_id, limit)}

    @router.post("/remove-workspace")
    async def remove_workspace(req: WorkspaceRequest, x_session_token: str = Header(alias="X-Session-Token")):
        user_id, _ = require_user(db, x_session_token, req.user_id)
        return {"removed": db.remove_workspace(user_id, req.path)}

    return router
