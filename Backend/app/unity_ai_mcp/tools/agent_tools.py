"""Sub-agent tools for the MCP servers (unityai and the Claude agents server).

The tools only carry the call: the backend's `/subagents/*` routes decide
everything (who may start, which model and effort, depth 1) and word the
answer, so this file imports nothing from `agentic`. The calling chat comes
from GAMACHINE_CONVERSATION_ID, never from the model.
"""
import logging
from typing import Optional

import httpx
from mcp.server.mcpserver import MCPServer

from spawn_env import read_only_refusal
from unity_ai_mcp.approval_bridge import BACKEND_URL, _conversation_id_from_env, _get_headers

logger = logging.getLogger(__name__)

# Same names as agentic.subagents.TOOLS (a test holds them equal).
TOOL_MODELS = "agent_models"
TOOL_START = "agent_start"
TOOL_MESSAGE = "agent_message"
TOOL_STOP = "agent_stop"
TOOL_LIST = "agent_list"
TOOL_RESULT = "agent_result"
TOOLS = (TOOL_MODELS, TOOL_START, TOOL_MESSAGE, TOOL_STOP, TOOL_LIST, TOOL_RESULT)

_NO_CHAT = "This tool works only from inside a Gamachine chat."
_UNREACHABLE = "Gamachine could not be reached; nothing was done."

# Shared by every tool description: the rules the backend enforces or the
# model must keep on its own.
_RULES = (
    "Sub-agents run other connected CLI models in the background, in this chat's folder. "
    "Depth 1: a sub-agent cannot start sub-agents. There is no count limit, but start only "
    "what the job needs. Before starting two or more sub-agents that write files in the same "
    "folder, ask the user (offer a separate worktree). A result arrives in this chat by "
    "itself when the sub-agent finishes; check agent_list when you decide what to do next "
    "or the user asks how it goes, and do not poll agent_list in a loop.")


def _detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = None
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail or f"HTTP {resp.status_code}")


async def _call(method: str, path: str, *, params: Optional[dict] = None,
                body: Optional[dict] = None, timeout: float = 30.0) -> str:
    conversation_id = _conversation_id_from_env()
    if conversation_id is None:
        return _NO_CHAT
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                resp = await client.get(f"{BACKEND_URL}{path}", headers=_get_headers(),
                                        params={"conversation_id": conversation_id, **(params or {})})
            else:
                resp = await client.post(f"{BACKEND_URL}{path}", headers=_get_headers(),
                                         json={"conversation_id": conversation_id, **(body or {})})
    except Exception as e:
        logger.warning("[agents] %s failed: %s", path, e)
        return _UNREACHABLE
    if resp.status_code != 200:
        return f"Refused: {_detail(resp)}"
    try:
        return str(resp.json().get("text") or "")
    except Exception:
        return _UNREACHABLE


async def agent_models() -> str:
    return await _call("GET", "/subagents/models")


async def agent_start(model: str, brief: str, effort: str = "auto", name: str = "") -> str:
    # A helper runs as an ordinary turn, so a read-only turn must not start or steer one.
    if (refusal := read_only_refusal()) is not None:
        return refusal
    return await _call("POST", "/subagents/start",
                       body={"model": model, "brief": brief, "effort": effort, "name": name})


async def agent_message(agent_id: int, text: str) -> str:
    if (refusal := read_only_refusal()) is not None:
        return refusal
    return await _call("POST", "/subagents/message", body={"agent_id": agent_id, "text": text})


async def agent_stop(agent_id: int) -> str:
    return await _call("POST", "/subagents/stop", body={"agent_id": agent_id})


async def agent_list() -> str:
    return await _call("GET", "/subagents/list")


async def agent_result(agent_id: int) -> str:
    return await _call("GET", "/subagents/result", params={"agent_id": agent_id})


def register_agent_tools(mcp: MCPServer) -> None:
    @mcp.tool(name=TOOL_MODELS, description=(
        "Lists the connected CLI providers' models and the effort levels each accepts, for "
        "agent_start. " + _RULES))
    async def agent_models_tool() -> str:
        return await agent_models()

    @mcp.tool(name=TOOL_START, description=(
        "Starts a sub-agent: a hidden helper chat on `model` (an id from agent_models) with "
        "`effort` (one of that model's levels, default auto), working on `brief`. Write the "
        "brief so it stands alone (goal, files, limits, what to report); the sub-agent ends "
        "with a self-contained report, which comes back here as its result (long results are "
        "cut; agent_result gives the full text). `name` is an optional short label. " + _RULES))
    async def agent_start_tool(model: str, brief: str, effort: str = "auto", name: str = "") -> str:
        return await agent_start(model, brief, effort, name)

    @mcp.tool(name=TOOL_MESSAGE, description=(
        "Sends `text` to sub-agent `agent_id`. A running sub-agent gets it at its next turn "
        "boundary (it is not interrupted); a finished, stopped or interrupted one continues "
        "with it. " + _RULES))
    async def agent_message_tool(agent_id: int, text: str) -> str:
        return await agent_message(agent_id, text)

    @mcp.tool(name=TOOL_STOP, description=(
        "Stops sub-agent `agent_id`: its running turn and its queued messages. Its approval "
        "cards are refused. agent_message can continue it later."))
    async def agent_stop_tool(agent_id: int) -> str:
        return await agent_stop(agent_id)

    @mcp.tool(name=TOOL_LIST, description=(
        "Lists this chat's sub-agents with their model, effort, status (running, done, idle, "
        "error, stopped, interrupted) and the start of their last result; under a running one, "
        "what it is doing now (phase, current tool and target, steps done and failed, elapsed "
        "time, its last line, and 'no activity for N min' after 90 s without an event). Check "
        "it when you decide what to do next or the user asks. Results arrive on their own; do "
        "not call this in a loop to wait."))
    async def agent_list_tool() -> str:
        return await agent_list()

    @mcp.tool(name=TOOL_RESULT, description=(
        "Returns the full last result of sub-agent `agent_id` (the result sent to this chat "
        "is cut at about 6 KB). For a running one: its live progress (phase, current step, "
        "the last 5 steps, counts, elapsed time, its last line); its result still arrives by "
        "itself, so do not call this in a loop."))
    async def agent_result_tool(agent_id: int) -> str:
        return await agent_result(agent_id)
