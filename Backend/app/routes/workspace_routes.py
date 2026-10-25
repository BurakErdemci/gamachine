import logging

from fastapi import APIRouter, Header

from auth_utils import require_user
from schemas import WorkspaceRequest

logger = logging.getLogger(__name__)


def create_workspace_router(db):
    router = APIRouter()

    @router.post("/save-workspace")
    async def save_workspace(req: WorkspaceRequest, x_session_token: str = Header(alias="X-Session-Token")):
        user_id, _ = require_user(db, x_session_token, req.user_id)
        db.save_workspace(user_id, req.path)
        try:
            from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
            if not unity_mcp_manager.is_running():
                unity_mcp_manager.reset_autoconnect_off(req.path)
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
