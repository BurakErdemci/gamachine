import logging
import os

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from auth_utils import require_user
from schemas import WorkspaceRequest

logger = logging.getLogger(__name__)


def create_workspace_router(db):
    router = APIRouter()

    @router.post("/save-workspace")
    async def save_workspace(req: WorkspaceRequest, background_tasks: BackgroundTasks,
                             x_session_token: str = Header(alias="X-Session-Token")):
        user_id, _ = require_user(db, x_session_token, req.user_id)
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
