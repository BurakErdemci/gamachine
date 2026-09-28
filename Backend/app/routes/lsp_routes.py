"""OmniSharp sidecar'a köprü: canlı diagnostics + IntelliSense uçları.
Eski /lint'in yerini alır — problems formatı birebir korunur:
{file, line, column, endColumn, message, severity} (1 tabanlı)."""
import os

from fastapi import APIRouter, Header
from pydantic import BaseModel

from database import DatabaseManager
from auth_utils import _check_token
from omnisharp.omnisharp_manager import get_omnisharp_manager

router = APIRouter()


class DocReq(BaseModel):
    path: str
    text: str
    line: int | None = None
    column: int | None = None


def create_lsp_router(db: DatabaseManager):
    def _abs(path: str) -> str | None:
        """Resolve `path` against the active workspace; `None` when it would
        land outside it (`..`, an absolute path elsewhere, another drive, UNC).

        Used to return an absolute path unchecked or a naive `os.path.join` for
        a relative one, with no containment check either way. A GET with
        `path=../route-other/Secret.cs` resolved outside the workspace and
        `/lsp/diagnostics` served that file's already-cached diagnostics to any
        session-token holder (Codex omniaudit, 28 Sep 2026, diagnostics-path-escape,
        probes/diagnostics-path.py). `realpath` on both sides: a contained
        symlink still passes, an escaping one still fails.
        """
        ws = db.get_last_workspace(1) or ""
        if not ws:
            return None
        try:
            ws_real = os.path.realpath(ws)
            candidate = path if os.path.isabs(path) else os.path.join(ws, path)
            real = os.path.realpath(candidate)
        except OSError:
            return None
        if real != ws_real and not real.startswith(ws_real + os.sep):
            return None
        return real

    async def _mgr():
        m = get_omnisharp_manager()
        ws = db.get_last_workspace(1)
        if ws:
            await m.ensure_started(ws)
        return m

    def _in_project(m, path: str) -> bool | None:
        # Only meaningful while OmniSharp runs: the editor hint says "syntax
        # errors only are checked", which is false when nothing is checked.
        return m.in_project(path) if m._ready() else None

    @router.get("/lsp/status")
    async def lsp_status(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return get_omnisharp_manager().status

    @router.post("/lsp/change")
    async def lsp_change(req: DocReq, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        m = await _mgr()
        path = _abs(req.path)
        if path is None:
            return {"problems": [], "status": m.status, "inProject": None}
        problems = await m.sync_document(path, req.text)
        return {"problems": problems, "status": m.status, "inProject": _in_project(m, path)}

    @router.get("/lsp/diagnostics")
    async def lsp_diagnostics(path: str = "", x_session_token: str = Header(alias="X-Session-Token", default="")):
        """Diagnostics published after `/lsp/change` answered. Does not start
        OmniSharp: the editor calls this on a timer, `/lsp/change` starts it."""
        # `path` has a default so the token gate runs before validation.
        _check_token(x_session_token)
        m = get_omnisharp_manager()
        if not path:
            return {"problems": [], "status": m.status, "inProject": None}
        apath = _abs(path)
        if apath is None:
            return {"problems": [], "status": m.status, "inProject": None}
        return {"problems": m.latest_diagnostics(apath), "status": m.status,
                "inProject": _in_project(m, apath)}

    @router.post("/lsp/completion")
    async def lsp_completion(req: DocReq, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        m = await _mgr()
        path = _abs(req.path)
        if path is None:
            return {"items": []}
        return {"items": await m.completion(path, req.text, req.line or 1, req.column or 1)}

    @router.post("/lsp/hover")
    async def lsp_hover(req: DocReq, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        m = await _mgr()
        path = _abs(req.path)
        if path is None:
            return {"contents": None}
        return {"contents": await m.hover(path, req.text, req.line or 1, req.column or 1)}

    @router.post("/lsp/definition")
    async def lsp_definition(req: DocReq, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        m = await _mgr()
        path = _abs(req.path)
        if path is None:
            return {"location": None}
        return {"location": await m.definition(path, req.text, req.line or 1, req.column or 1)}

    return router
