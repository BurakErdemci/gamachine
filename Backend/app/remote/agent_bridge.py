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
import secrets
import sqlite3
import sys
import time
from contextlib import closing
from types import SimpleNamespace
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from agentic import approval_policy, cards, turn_replies
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
DELIVERY_GRACE_S = 30.0
REPLY_MAX = 200_000
REQUESTS_KEEP = 500
REQUEST_TTL_S = 24 * 3600

# request_id -> {chat_id, after_id, text, created_at, client}; lost on restart (docs/terminal-agents.md).
_REQUESTS: Dict[str, Dict[str, Any]] = {}

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


def _remember_request(entry: Dict[str, Any]) -> str:
    now = time.monotonic()
    for key in [k for k, v in _REQUESTS.items() if now - v["created_at"] > REQUEST_TTL_S]:
        del _REQUESTS[key]
    request_id = secrets.token_hex(8)
    _REQUESTS[request_id] = entry
    while len(_REQUESTS) > REQUESTS_KEEP:
        del _REQUESTS[next(iter(_REQUESTS))]
    return request_id


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
        title = body.get("title", "")
        if not isinstance(title, str):
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

    def last_message_id(chat_id: int) -> int:
        with closing(sqlite3.connect(db.db_path)) as conn:
            row = conn.execute("SELECT MAX(id) FROM messages WHERE conversation_id = ?", (chat_id,)).fetchone()
        return int(row[0] or 0)

    def own_user_row(chat_id: int, after_id: int, text: Any) -> Any:
        """The first user row after the send with exactly the sent text: the one
        the window stored for this request (identical texts match in order)."""
        with closing(sqlite3.connect(db.db_path)) as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE conversation_id = ? AND id > ? AND role = 'user' "
                "AND content = ? ORDER BY id LIMIT 1", (chat_id, after_id, text)).fetchone()
        return int(row[0]) if row else None

    def message_row(chat_id: int, row_id: int) -> Any:
        with closing(sqlite3.connect(db.db_path)) as conn:
            return conn.execute("SELECT content, model FROM messages WHERE conversation_id = ? AND id = ?",
                                (chat_id, row_id)).fetchone()

    @router.post("/chats/{chat_id}/messages")
    async def send_message(chat_id: int, body: Dict[str, Any] = Body(...),
                           x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        after_id = last_message_id(chat_id)
        try:
            result = await bridge.rpc.send_message(
                AgentSession(body.get("client")), {"chat_id": chat_id, "text": body.get("text")}, None)
        except RpcError as exc:
            return rpc_error(exc)
        if result["status"] != "accepted":
            return {"status": result["status"]}
        request_id = _remember_request({
            "chat_id": chat_id, "after_id": after_id, "text": body.get("text"),
            "created_at": time.monotonic(), "client": clean_client(body.get("client"))})
        return {"status": result["status"], "request_id": request_id}

    @router.get("/requests/{request_id}")
    async def request_state(request_id: str,
                            x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        req = _REQUESTS.get(request_id)
        if req is None:
            return JSONResponse(status_code=404, content={"error": "unknown_request"})
        chat_id = req["chat_id"]
        out: Dict[str, Any] = {"request_id": request_id, "chat_id": chat_id, "status": None, "reply": None,
                               "model": None, "waiting_card": None, "truncated": False}
        own = own_user_row(chat_id, req["after_id"], req["text"])
        if own is None:
            # Not sent yet: the window may still be busy with an earlier turn.
            # not_delivered only informs; the MCP keeps waiting (agent_server.py).
            in_flight = approval_policy.conversation_turn_in_flight(chat_id)
            out["status"] = ("queued" if in_flight or time.monotonic() - req["created_at"] < DELIVERY_GRACE_S
                             else "not_delivered")
            return out
        # The reply is what the turn that began with our row recorded
        # (agentic/turn_replies), never a row found by position: an older
        # detached turn can store its reply after our row.
        entry = turn_replies.get(chat_id, own)
        if entry is None or entry.get("state") != "ended":
            pending = cards.list_pending(chat_id)
            out.update(status="running",
                       waiting_card=(pending[0].get("summary") or pending[0].get("tool")) if pending else None)
            return out
        row = message_row(chat_id, entry["assistant_row_id"]) if entry.get("assistant_row_id") else None
        if row is None:
            out.update(status="ended_without_reply", turn_status=entry.get("status"))
            return out
        text = row[0] or ""
        # turn_status tells the agent a stopped or failed turn's stored text may be partial.
        out.update(status="done", reply=text[:REPLY_MAX], model=row[1], truncated=len(text) > REPLY_MAX,
                   turn_status=entry.get("status"))
        return out

    @router.get("/chats/{chat_id}/messages")
    async def messages(chat_id: int, limit: int = Query(10, ge=1, le=50),
                       x_session_token: str = Header(alias="X-Session-Token", default="")):
        gate(x_session_token)
        if chats.chat_row(db, chat_id) is None:
            return unknown_chat()
        return {"messages": chats.recent_messages(db, chat_id, limit)}

    return router
