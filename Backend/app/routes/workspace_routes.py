import os
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException

from auth_utils import get_current_user, is_allowed_unity_script_path, require_user
from schemas import WorkspaceRequest, WriteFileRequest


def create_workspace_router(db):
    router = APIRouter()

    @router.post("/save-workspace")
    async def save_workspace(req: WorkspaceRequest, x_session_token: str = Header(alias="X-Session-Token")):
        user_id, _ = require_user(db, x_session_token, req.user_id)
        db.save_workspace(user_id, req.path)
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

    @router.post("/write-file")
    async def write_file(req: WriteFileRequest, x_session_token: str = Header(alias="X-Session-Token")):
        get_current_user(db, x_session_token)
        if not is_allowed_unity_script_path(req.file_path, req.workspace_path):
            raise HTTPException(403, "Dosya yalnızca workspace içindeki Assets/Scripts altına yazılabilir.")

        try:
            abs_file = str(Path(req.file_path).resolve(strict=False))
            os.makedirs(os.path.dirname(abs_file), exist_ok=True)
            with open(abs_file, "w", encoding="utf-8") as file:
                file.write(req.content)
            return {"status": "success", "path": abs_file}
        except Exception as exc:
            raise HTTPException(500, str(exc))

    return router
