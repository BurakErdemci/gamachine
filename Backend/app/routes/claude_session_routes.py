"""Preview and import an existing Claude Code session into a new chat."""
import logging
import os
import sqlite3
import time
from contextlib import closing
from typing import Any, Dict

from fastapi import APIRouter, Body, Header
from fastapi.responses import JSONResponse

import claude_sessions
from agentic import chat_model
from auth_utils import _check_token
from remote.chats import LOCAL_USER_ID
from remote.desktop_channel import CHANNEL

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "Claude Code oturumu"
FALLBACK_MODEL = "claude-opus-5-5"
RECENT_SECONDS = 600
IMPORT_LIMIT = 300


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, **extra})


def _is_claude_model(model_name: Any) -> bool:
    return (isinstance(model_name, str) and model_name.lower().startswith("claude")
            and chat_model.subscription_family(model_name) == "claude")


def _pick_model(db, session_model: Any) -> str:
    """The session's own model when it is a usable Claude id, else the user's default, else a fixed fallback."""
    if _is_claude_model(session_model):
        try:
            chat_model.check_model_choice("subscription", session_model)
            return session_model
        except chat_model.ChatModelError:
            pass
    provider_type, model_name, _, _ = db.get_ai_config(LOCAL_USER_ID)
    if provider_type == "subscription" and _is_claude_model(model_name):
        return model_name
    return FALLBACK_MODEL


def _modified_recently(modified_at: float) -> bool:
    return (time.time() - modified_at) < RECENT_SECONDS


def create_claude_session_router(db) -> APIRouter:
    router = APIRouter()

    def _load(session_id: Any, limit: int):
        if not isinstance(session_id, str) or not claude_sessions.SESSION_ID_RE.match(session_id):
            return None, _error(400, "bad_session_id")
        path = claude_sessions.find_session(session_id)
        if path is None:
            return None, _error(404, "session_not_found")
        try:
            return claude_sessions.read_session(path, limit=limit), None
        except OSError:
            return None, _error(404, "session_not_found")

    @router.get("/claude-sessions/{session_id}/preview")
    async def preview(session_id: str, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        info, err = _load(session_id, 1)
        if err:
            return err
        cwd = info["cwd"]
        return {"session_id": info["session_id"], "cwd": cwd,
                "cwd_exists": bool(cwd) and os.path.isdir(cwd),
                "title": info["title"], "model": info["model"], "total": info["total"],
                "importable": info["total"],
                "modified_recently": _modified_recently(info["modified_at"]),
                "modified_at": info["modified_at"]}

    @router.post("/claude-sessions/import")
    async def import_session(body: Dict[str, Any] = Body(default={}),
                             x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        info, err = _load(body.get("session_id"), IMPORT_LIMIT)
        if err:
            return err
        cwd = info["cwd"]
        if not cwd or not os.path.isdir(cwd):
            return _error(409, "cwd_missing", cwd=cwd)
        session_id = info["session_id"]
        model = _pick_model(db, info["model"])
        title = info["title"] or DEFAULT_TITLE
        conv = db.create_conversation(LOCAL_USER_ID, title, workspace=cwd)
        # Per chat only: the user's default model for new chats stays as it was.
        db.set_conversation_model(conv, "subscription", model)
        for m in info["messages"]:
            if m["role"] == "assistant":
                db.add_message(conv, "assistant", m["content"], provider="claude", model=model)
            else:
                db.add_message(conv, "user", m["content"])
        n, total = len(info["messages"]), info["total"]
        divider = f"⌨️ Terminal oturumundan aktarıldı · {n} mesaj"
        if n < total:
            divider += f" (son {n}, toplam {total})"
        db.add_message(conv, "system", divider)
        # Late import: conversation_routes pulls in the whole agent stack.
        from routes.conversation_routes import _oturum_saglayici_anahtari
        db.save_cli_session(conv, _oturum_saglayici_anahtari("subscription", model), session_id, cwd)
        try:
            CHANNEL.publish({"type": "conversation_created", "conversation_id": conv,
                             "at": int(time.time() * 1000)})
        except Exception:
            logger.exception("[claude-sessions] conversation_created publish failed")
        return {"chat_id": conv, "workspace": cwd, "title": title, "model_name": model,
                "imported": n, "total": total,
                "modified_recently": _modified_recently(info["modified_at"])}

    @router.get("/conversations/{conv_id}/claude-session")
    async def chat_claude_session(conv_id: int,
                                  x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        stored = db.get_conversation_model(conv_id)
        if not stored or chat_model.cli_family(*stored) != "claude":
            return {"session_id": None}
        with closing(sqlite3.connect(db.db_path)) as conn:
            row = conn.execute("SELECT session_id, workspace FROM cli_sessions "
                               "WHERE conversation_id = ? AND provider = ?", (conv_id, "claude")).fetchone()
        if not row or not row[0]:
            return {"session_id": None}
        return {"session_id": row[0], "workspace": row[1] or None,
                "command": claude_sessions.command_for(row[1], row[0])}

    return router
