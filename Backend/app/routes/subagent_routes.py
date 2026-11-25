"""Sub-agent routes: what the agent_* MCP tools call (agentic/subagents.py).

The unityai server and the Claude agents server call these with the app token
and the chat they were spawned for, like the mailbox routes. The caller chat
must be one of the local user's ordinary chats, not a side chat and not a
sub-agent's own chat (depth 1); a sub-agent is only reachable from the chat
that started it. Calls that change something also need a turn of the caller
in flight: a tool call only comes out of a running turn.

The desktop UI calls list, fuse and continue with the same app token; continue
is the user's own click, so it needs no running turn. The JSON contract is in
SUBAGENTS_API.md next to this file.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Header, HTTPException

from agentic import subagents
from auth_utils import _check_token, get_current_user

logger = logging.getLogger(__name__)

_NOT_FOUND = "Sohbet bulunamadı."
_DEPTH = "A sub-agent cannot start or manage sub-agents (depth 1). / Alt ajan alt ajan yönetemez."


def _turn_in_flight(conv_id: int) -> bool:
    from agentic.approval_policy import conversation_turn_in_flight
    if conversation_turn_in_flight(conv_id):
        return True
    try:
        # A Claude continuation turn never enters AgentRunner.run (same rule
        # as conversation_routes._verified_mcp_owner).
        from providers.claude_sdk_session import peek_session
        sess = peek_session(conv_id)
        return sess is not None and sess.trusted_unprompted_turn is True
    except Exception:
        return False


def _as_id(value: Any) -> Any:
    """A chat or agent id as JSON may carry it: an int, or a string of digits
    (the desktop renderer sends ids it read from the DOM as strings)."""
    if type(value) is int:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def create_subagent_router(db: Any,
                           list_models: Optional[Callable[..., Awaitable[dict]]] = None) -> APIRouter:
    router = APIRouter()

    def _caller(token: str, conv_id: Any, live: bool = False) -> int:
        """The local user id when `conv_id` may manage sub-agents."""
        user_id, _ = get_current_user(db, token)
        try:
            owner = db.get_conversation_owner(conv_id) if type(conv_id) is int else None
        except Exception:
            owner = None
        if type(owner) is not int or owner != user_id:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        if db.get_side_of(conv_id) is not None:
            raise HTTPException(status_code=403, detail="A side chat cannot use sub-agents.")
        # Read directly: a failed lookup must refuse (500), not pass as "not a child".
        if db.get_agent_of(conv_id) is not None:
            raise HTTPException(status_code=403, detail=_DEPTH)
        if live and not _turn_in_flight(conv_id):
            raise HTTPException(status_code=403,
                                detail="Sub-agents are managed only from a chat's running turn.")
        return user_id

    async def _catalog() -> dict:
        if list_models is None:
            return {}
        try:
            # No cloud merge: sub-agents are CLI models only.
            return await list_models(None)
        except Exception:
            logger.exception("[subagents] model catalog not read")
            return {}

    def _refused(exc: subagents.SubagentError) -> HTTPException:
        detail: Any = exc.message
        if exc.extra:
            detail = {"message": exc.message, "code": exc.code, **exc.extra}
        return HTTPException(status_code=exc.status, detail=detail)

    @router.get("/subagents/models")
    async def subagent_models(conversation_id: Optional[int] = None,
                              x_session_token: str = Header(alias="X-Session-Token", default="")):
        user_id = _caller(x_session_token, conversation_id)
        return {"text": subagents.models_text(user_id, await _catalog())}

    @router.post("/subagents/start")
    async def subagent_start(body: dict,
                             x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        parent = _as_id(body.get("conversation_id"))
        user_id = _caller(x_session_token, parent, live=True)
        catalog = await _catalog()
        try:
            return subagents.start(parent, user_id, model=body.get("model"),
                                   effort=body.get("effort"), brief=body.get("brief"),
                                   name=body.get("name"), catalog=catalog)
        except subagents.SubagentError as exc:
            raise _refused(exc)

    @router.post("/subagents/message")
    async def subagent_message(body: dict,
                               x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        parent = _as_id(body.get("conversation_id"))
        _caller(x_session_token, parent, live=True)
        try:
            return subagents.message(parent, _as_id(body.get("agent_id")), body.get("text"))
        except subagents.SubagentError as exc:
            raise _refused(exc)

    @router.post("/subagents/stop")
    async def subagent_stop(body: dict,
                            x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        parent = _as_id(body.get("conversation_id"))
        # No running turn needed: the desktop's and the phone's Stop buttons call this
        # with the same app token as the agent tool, and a stop is always safe.
        _caller(x_session_token, parent)
        try:
            return await subagents.stop(parent, _as_id(body.get("agent_id")))
        except subagents.SubagentError as exc:
            raise _refused(exc)

    @router.get("/subagents/list")
    async def subagent_list(conversation_id: Optional[int] = None,
                            x_session_token: str = Header(alias="X-Session-Token", default="")):
        # The desktop UI polls this too: same app token, no running turn needed.
        _caller(x_session_token, conversation_id)
        rows = db.list_subagents(conversation_id)
        for row in rows:
            row["progress"] = subagents.row_progress(row)
        return {"agents": rows, "fuse": subagents.fuse_state(conversation_id),
                "text": subagents.list_text(conversation_id)}

    @router.get("/subagents/fuse")
    async def subagent_fuse(conversation_id: Optional[int] = None,
                            x_session_token: str = Header(alias="X-Session-Token", default="")):
        _caller(x_session_token, conversation_id)
        return subagents.fuse_state(conversation_id)

    @router.post("/subagents/continue")
    async def subagent_continue(body: dict,
                                x_session_token: str = Header(alias="X-Session-Token", default="")):
        """The fuse notice's Continue button (desktop UI): no turn of the chat
        needs to run; the user's click is the go-ahead."""
        _check_token(x_session_token)
        parent = _as_id(body.get("conversation_id")) if isinstance(body, dict) else None
        _caller(x_session_token, parent)
        try:
            return subagents.continue_held(parent, in_flight=_turn_in_flight)
        except subagents.SubagentError as exc:
            # The UI tells the refusals apart by code.
            raise HTTPException(status_code=exc.status,
                                detail={"message": exc.message, "code": exc.code, **exc.extra})

    @router.get("/subagents/result")
    async def subagent_result(conversation_id: Optional[int] = None, agent_id: Optional[int] = None,
                              x_session_token: str = Header(alias="X-Session-Token", default="")):
        _caller(x_session_token, conversation_id)
        try:
            return subagents.result(conversation_id, agent_id)
        except subagents.SubagentError as exc:
            raise _refused(exc)

    return router
