"""Stdio MCP server that lets a terminal agent (Claude Code, Codex, opencode) use Gamachine.

Started by the agent's CLI (`backend agent-mcp-server`), it talks to the running
backend's `/agent-bridge/*` routes. Those routes decide everything (the toggle,
the frame the open window sends, what is never reachable); this file only
carries the calls and waits for the reply. No tool answers a card, changes the
approval mode, halts computer use or stops a turn.

The send route returns a request id; the reply is read from the stored messages
through `GET /agent-bridge/requests/{id}` (docs/terminal-agents.md).
"""
import asyncio
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
from mcp.server.mcpserver import Context, MCPServer

from local_token_file import read_backend_url, read_local_app_token

NOT_RUNNING = "Gamachine is not running. Ask the user to open Gamachine."
DISABLED = ("Terminal access is off. Ask the user to turn it on in Gamachine: "
            "Settings > Connectors > Terminal agents.")
NOT_READY = "Gamachine's window is not open or not ready; nothing was sent."
UNKNOWN_REQUEST = ("Gamachine restarted or the request expired; "
                   "use gamachine_read_messages(chat_id=...) to read the chat.")
ENDED_NO_REPLY = "the turn ended without a reply (an error or a stop in Gamachine)"
NOT_DELIVERED = "Gamachine did not send the message; is its window open?"
TERMINAL = ("done", "ended_without_reply")
TRUNCATED_NOTE = "note: the reply was cut; read the full text in Gamachine"
PARTIAL_NOTE = "note: the turn ended as '{}' in Gamachine; this reply may be incomplete"

REQUEST_ID_RE = re.compile(r"[0-9a-f]{16}")  # secrets.token_hex(8) in remote/agent_bridge.py

POLL_S = 1.0
WAIT_MIN, WAIT_MAX = 5, 170
DEFAULT_CLIENT = "Terminal"

INSTRUCTIONS = (
    "Gamachine is the user's game-dev desktop app. Messages sent here appear in the user's "
    "Gamachine window marked as coming from the terminal. Risky steps wait for the user's "
    "approval in Gamachine and cannot be approved from here. Check `busy` in "
    "gamachine_list_chats before sending, and send one message per turn."
)


class Failure(Exception):
    """A problem the tool reports to the model as its plain-text result."""


# ── client name ──────────────────────────────────────────────────────────────

def resolve_client_name(declared: Optional[str], env: Optional[Dict[str, str]] = None) -> str:
    """The MCP client's declared name, else GAMACHINE_AGENT_NAME, else "Terminal"."""
    env = os.environ if env is None else env
    for candidate in (declared, env.get("GAMACHINE_AGENT_NAME")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return DEFAULT_CLIENT


def declared_client_name(ctx: Any) -> Optional[str]:
    """clientInfo.name of the initialize request (`session.client_params.client_info`)."""
    try:
        return ctx.session.client_params.client_info.name
    except AttributeError:
        return None


# ── reply polling ────────────────────────────────────────────────────────────

def clamp_wait(seconds: Any) -> int:
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        value = 120
    return max(WAIT_MIN, min(WAIT_MAX, value))


def format_reply(request_id: str, state: Dict[str, Any]) -> str:
    """The text the model reads for one state of GET /requests/{id}."""
    status = state.get("status")
    lines = [f"status: {status}"]
    if state.get("model"):
        lines.append(f"model: {state['model']}")
    if state.get("waiting_card"):
        lines.append(f"waiting for the user's approval in Gamachine: {state['waiting_card']}")
    if status == "done":
        lines.append("reply:")
        lines.append(state.get("reply") or "")
        if state.get("truncated"):
            lines.append(TRUNCATED_NOTE)
        if state.get("turn_status") in ("stopped", "error"):
            lines.append(PARTIAL_NOTE.format(state["turn_status"]))
    elif status == "ended_without_reply":
        lines.append(ENDED_NO_REPLY)
    elif status == "not_delivered":
        # Only said when the wait ran out: a busy window sends the message later.
        lines.append(NOT_DELIVERED)
        lines.append(f'it may still be sent; call gamachine_wait_reply(request_id="{request_id}")')
    else:
        lines.append(f'still running; call gamachine_wait_reply(request_id="{request_id}")')
    return "\n".join(lines)


async def wait_for_reply(
    request_id: str, wait_seconds: Any, *,
    fetch_state: Callable[[str], Awaitable[dict]],
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Poll the request until its reply is stored, its turn ended, or the time is up.

    queued and not_delivered are not terminal: a message waiting behind a long
    turn is sent when that turn ends. The budget is wall-clock time, so slow
    polls cannot stretch it (one poll still runs to the HTTP timeout)."""
    deadline = clock() + clamp_wait(wait_seconds)
    while True:
        try:
            state = await fetch_state(request_id)
        except Failure as exc:
            if str(exc).endswith("unknown_request"):
                return UNKNOWN_REQUEST
            raise
        left = deadline - clock()
        if state.get("status") in TERMINAL or left <= 0:
            return format_reply(request_id, state)
        await sleep(min(POLL_S, left))


# ── HTTP ─────────────────────────────────────────────────────────────────────

async def _request(method: str, path: str, *, params: Optional[dict] = None,
                   json: Optional[dict] = None) -> Any:
    """One call to the running backend; a Failure carries the sentence for the model."""
    url, token = read_backend_url(), read_local_app_token()
    if not url or not token:
        raise Failure(NOT_RUNNING)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, f"{url}/agent-bridge{path}", params=params,
                                        json=json, headers={"X-Session-Token": token})
    except httpx.TransportError:
        raise Failure(NOT_RUNNING)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code == 200:
        return body
    code = body.get("error") or body.get("detail") if isinstance(body, dict) else None
    if resp.status_code == 403 and code == "agent_bridge_disabled":
        raise Failure(DISABLED)
    raise Failure(f"Gamachine answered HTTP {resp.status_code}: {code or 'no error code'}")


def _client(ctx: Any) -> str:
    return resolve_client_name(declared_client_name(ctx))


# ── tools ────────────────────────────────────────────────────────────────────

async def list_chats() -> str:
    data = await _request("GET", "/chats")
    lines = []
    for chat in data.get("chats", []):
        model = "/".join(str(x) for x in (chat.get("provider_type"), chat.get("model_name")) if x)
        lines.append(f"#{chat['chat_id']} {chat.get('title') or '(untitled)'} | {model or 'no model'} "
                     f"| {chat.get('status')}")
    return "\n".join(lines) or "No chats."


async def list_models() -> str:
    data = await _request("GET", "/models")
    groups: Dict[str, List[str]] = {}
    for key, items in data.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                provider = str(item.get("provider") or key)
                label = item["id"] + (f" ({item['name']})" if item.get("name") else "")
                groups.setdefault(provider, []).append(label + (" [unavailable]" if item.get("disabled") else ""))
    return "\n".join(f"{provider}: " + ", ".join(models) for provider, models in groups.items()) \
        or "No models."


async def new_chat(ctx: Any, title: str = "", provider_type: str = "", model_name: str = "") -> str:
    body: Dict[str, Any] = {"client": _client(ctx)}
    if title:
        body["title"] = title
    if provider_type and model_name:
        body.update(provider_type=provider_type, model_name=model_name)
    data = await _request("POST", "/chats", json=body)
    model = "/".join(str(x) for x in (data.get("provider_type"), data.get("model_name")) if x)
    line = f"Created chat #{data['chat_id']} ({model or 'default model'})."
    if data.get("model_error"):
        line += f" The model was not set: {data['model_error']}."
    return line


async def set_model(ctx: Any, chat_id: int, provider_type: str, model_name: str) -> str:
    data = await _request("PUT", f"/chats/{chat_id}/model", json={
        "client": _client(ctx), "provider_type": provider_type, "model_name": model_name})
    return (f"Chat #{chat_id} now uses {data.get('provider_type', provider_type)}/"
            f"{data.get('model_name', model_name)}.")


async def send_message(ctx: Any, chat_id: int, text: str, wait_seconds: int = 120) -> str:
    sent = await _request("POST", f"/chats/{chat_id}/messages", json={"text": text, "client": _client(ctx)})
    if sent.get("status") != "accepted" or not sent.get("request_id"):
        return NOT_READY
    return await wait_for_reply(sent["request_id"], wait_seconds, fetch_state=_state_fetcher)


async def wait_reply(request_id: str, wait_seconds: int = 120) -> str:
    # The id comes from the model and is joined into a URL: anything but the backend's
    # own 16-hex form could walk out of /agent-bridge (e.g. "../../remote/devices").
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        return UNKNOWN_REQUEST
    return await wait_for_reply(request_id, wait_seconds, fetch_state=_state_fetcher)


async def read_messages(chat_id: int, limit: int = 10) -> str:
    try:
        limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        limit = 10
    data = await _request("GET", f"/chats/{chat_id}/messages", params={"limit": limit})
    return "\n\n".join(f"[{m.get('role')}] {m.get('text', '')}" for m in data.get("messages", [])) \
        or "No messages."


async def _state_fetcher(request_id: str) -> dict:
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise Failure(UNKNOWN_REQUEST)
    return await _request("GET", f"/requests/{request_id}")


async def _guard(call: Awaitable[str]) -> str:
    try:
        return await call
    except Failure as exc:
        return str(exc)


def create_server() -> MCPServer:
    mcp = MCPServer(name="gamachine", instructions=INSTRUCTIONS)

    @mcp.tool()
    async def gamachine_list_chats() -> str:
        """List the user's Gamachine chats: id, title, provider/model, status (busy while a turn runs)."""
        return await _guard(list_chats())

    @mcp.tool()
    async def gamachine_list_models() -> str:
        """List the models a chat can use, grouped by provider (provider_type)."""
        return await _guard(list_models())

    @mcp.tool()
    async def gamachine_new_chat(ctx: Context, title: str = "", provider_type: str = "",
                                 model_name: str = "") -> str:
        """Open a new chat in the project currently open in Gamachine, optionally with a model."""
        return await _guard(new_chat(ctx, title, provider_type, model_name))

    @mcp.tool()
    async def gamachine_set_model(ctx: Context, chat_id: int, provider_type: str, model_name: str) -> str:
        """Set the model of a chat (provider_type and model_name as listed by gamachine_list_models)."""
        return await _guard(set_model(ctx, chat_id, provider_type, model_name))

    @mcp.tool()
    async def gamachine_send_message(ctx: Context, chat_id: int, text: str, wait_seconds: int = 120) -> str:
        """Send a message to a chat as if typed in Gamachine, then wait for the reply (5-170 s)."""
        return await _guard(send_message(ctx, chat_id, text, wait_seconds))

    @mcp.tool()
    async def gamachine_wait_reply(request_id: str, wait_seconds: int = 120) -> str:
        """Keep waiting for a reply that was still running; `request_id` comes from the earlier result."""
        return await _guard(wait_reply(request_id, wait_seconds))

    @mcp.tool()
    async def gamachine_read_messages(chat_id: int, limit: int = 10) -> str:
        """Read the last messages (1-50) of a chat."""
        return await _guard(read_messages(chat_id, limit))

    return mcp


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
