"""Routes a terminal agent's stdio MCP server (`agent-mcp-server`) calls.

One toggle ("external agents may access", off by default) gates every route but
/settings. The routes reuse the phone's Dispatcher for sending and model picks,
so a message reaches the open window as the same `remote_message` frame, marked
`source: "agent"`. Nothing here answers a card or touches the approval mode: an
agent must not approve its own risky step (docs/terminal-agents.md).
"""
from __future__ import annotations

import logging
import os
import sys
import time
from types import SimpleNamespace
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from agentic import turn_events
from auth_utils import _check_token
from remote import chats
from remote.desktop_channel import CHANNEL
from remote.rpc import RpcError

logger = logging.getLogger(__name__)

SETTING_KEY = "agent_bridge_enabled"
DISABLED = "agent_bridge_disabled"
CLIENT_MAX = 48
TITLE_MAX = 200
DEFAULT_CLIENT = "Terminal"

# Control characters and the Unicode bidi controls that could reorder a name on screen.
_BIDI = {*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F}

def enabled(db) -> bool:
    try:
        return db.get_setting(SETTING_KEY) == "1"
    except Exception:
        logger.warning("[agent-bridge] setting not read; treated as off")
        return False


def set_enabled(db, on: bool) -> None:
    db.set_setting(SETTING_KEY, "1" if on else "0")


def server_argv() -> List[str]:
    """argv of the stdio MCP server (dev and frozen builds differ)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "agent-mcp-server"]
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return [sys.executable, main_py, "agent-mcp-server"]


def _clean(value: Any, limit: int, default: str) -> str:
    if not isinstance(value, str):
        return default
    kept = "".join(ch for ch in value if ch.isspace() or (ch.isprintable() and ord(ch) not in _BIDI))
    text = " ".join(kept.split())[:limit].strip()
    return text or default


def clean_client(name: Any) -> str:
    return _clean(name, CLIENT_MAX, DEFAULT_CLIENT)


class AgentSession:
    """The few PhoneSession fields send_message, set_model and list_models read."""
    source = "agent"

    def __init__(self, client: Any):
        name = clean_client(client)
        self.device = SimpleNamespace(device_id="agent:" + name, name=name)

    @property
    def device_label(self) -> str:
        return "agent:" + self.device.name


def create_agent_bridge_router(db, bridge) -> APIRouter:
    router = APIRouter(prefix="/agent-bridge")

    def gate(token: str) -> None:
        _check_token(token)
        if not enabled(db):
            raise HTTPException(status_code=403, detail=DISABLED)

    def rpc_error(exc: RpcError) -> JSONResponse:
        return JSONResponse(status_code=404 if exc.error == "unknown_chat" else 400,
                            content={"error": exc.error, **exc.extra})

    def unknown_chat() -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "unknown_chat"})

    @router.get("/settings")
    async def get_settings(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return {"enabled": enabled(db), "server_command": server_argv()}

    @router.put("/settings")
    async def put_settings(body: Dict[str, Any] = Body(...),
                           x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        on = body.get("enabled")
        if not isinstance(on, bool):
            raise HTTPException(status_code=400, detail="enabled must be a boolean")
        set_enabled(db, on)
        return {"enabled": on}

    @router.get("/chats")
    async def list_chats(x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        out = []
        for item in chats.list_chats(db):
            row = chats.chat_row(db, int(item["chat_id"])) or {}
            out.append({"chat_id": int(item["chat_id"]), "title": item["title"],
                        "provider": item["provider"], "provider_type": row.get("provider_type"),
                        "model_name": row.get("model_name"), "status": item["status"],
                        "busy": item["status"] != "idle", "last_activity": item["last_activity"]})
        return {"chats": out}

    @router.get("/models")
    async def list_models(x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        try:
            return await bridge.rpc.list_models(AgentSession(DEFAULT_CLIENT), {}, None)
        except RpcError as exc:
            return rpc_error(exc)

    @router.post("/chats")
    async def new_chat(body: Dict[str, Any] = Body(default={}),
                       x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        title = body.get("title")
        if title is not None and not isinstance(title, str):
            raise HTTPException(status_code=400, detail="bad_title")
        title = _clean(title, TITLE_MAX, "Yeni Sohbet")
        provider_type, model_name = body.get("provider_type"), body.get("model_name")
        # No workspace argument: the chat binds the project open in Gamachine.
        conv = db.create_conversation(chats.LOCAL_USER_ID, title)
        model_error = None
        if provider_type and model_name:
            try:
                await bridge.rpc.set_model(
                    AgentSession(body.get("client")),
                    {"chat_id": conv, "provider_type": provider_type, "model_name": model_name}, None)
            except RpcError as exc:
                model_error = exc.error
        try:
            CHANNEL.publish({"type": "conversation_created", "conversation_id": conv,
                             "at": int(time.time() * 1000)})
        except Exception:
            logger.exception("[agent-bridge] conversation_created publish failed")
        row = chats.chat_row(db, conv) or {}
        reply = {"chat_id": conv, "title": row.get("title", title),
                 "provider_type": row.get("provider_type"), "model_name": row.get("model_name")}
        if model_error:
            reply["model_error"] = model_error
        return reply

    @router.put("/chats/{chat_id}/model")
    async def set_model(chat_id: int, body: Dict[str, Any] = Body(...),
                        x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        try:
            return await bridge.rpc.set_model(
                AgentSession(body.get("client")),
                {"chat_id": chat_id, "provider_type": body.get("provider_type"),
                 "model_name": body.get("model_name")}, None)
        except RpcError as exc:
            return rpc_error(exc)

    @router.post("/chats/{chat_id}/messages")
    async def send_message(chat_id: int, body: Dict[str, Any] = Body(...),
                           x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        # The reply is found by sequence number: no link from the frame to a turn exists.
        info = turn_events.since(chat_id, 0)
        busy = turn_events.turn_open(chat_id)
        try:
            result = await bridge.rpc.send_message(
                AgentSession(body.get("client")), {"chat_id": chat_id, "text": body.get("text")}, None)
        except RpcError as exc:
            return rpc_error(exc)
        return {"status": result["status"], "since_seq": info["last_seq"], "epoch": info["epoch"],
                "busy_at_send": busy}

    @router.get("/chats/{chat_id}/events")
    async def events(chat_id: int, since: int = Query(0, ge=0),
                     x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        if chats.chat_row(db, chat_id) is None:
            return unknown_chat()
        return turn_events.since(chat_id, since)

    @router.get("/chats/{chat_id}/messages")
    async def messages(chat_id: int, limit: int = Query(10, ge=1, le=50),
                       x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        if chats.chat_row(db, chat_id) is None:
            return unknown_chat()
        return {"messages": chats.recent_messages(db, chat_id, limit)}

    return router
