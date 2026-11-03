"""Stdio MCP server that lets a terminal agent (Claude Code, Codex, opencode) use Gamachine.

Started by the agent's CLI (`backend agent-mcp-server`), it talks to the running
backend's `/agent-bridge/*` routes. Those routes decide everything (the toggle,
the frame the open window sends, what is never reachable); this file only
carries the calls and waits for the reply. No tool answers a card, changes the
approval mode, halts computer use or stops a turn.

The reply is found by sequence number: the send route returns the chat's last
event `seq` and the process `epoch`, and the reply is the first turn that
starts after it (docs/terminal-agents.md).
"""
import asyncio
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
from mcp.server.mcpserver import Context, MCPServer

from local_token_file import read_backend_url, read_local_app_token

NOT_RUNNING = "Gamachine is not running. Ask the user to open Gamachine."
DISABLED = ("Terminal access is off. Ask the user to turn it on in Gamachine: "
            "Settings > Connectors > Terminal agents.")
NOT_READY = "Gamachine's window is not open or not ready; nothing was sent."
RESTARTED = "Gamachine restarted; the reply may be lost. Use gamachine_read_messages."

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


# ── reply detection (pure: events in, verdict out) ───────────────────────────

def analyze_reply(events: List[dict], busy_at_send: bool = False) -> Dict[str, Any]:
    """Find the reply turn in `events` (everything after the send's `since_seq`).

    Idle chat: the first turn_start. Busy chat: the turn that was running ends
    first, the reply is the next turn_start after that turn_end.
    """
    events = sorted(events, key=lambda e: e.get("seq", 0))
    index = 0
    if busy_at_send:
        end = next((i for i, e in enumerate(events) if e.get("kind") == "turn_end"), None)
        if end is None:
            return {"started": False, "ended": False}
        index = end + 1
    start = next((i for i in range(index, len(events)) if events[i].get("kind") == "turn_start"), None)
    if start is None:
        return {"started": False, "ended": False}
    head = events[start]
    out: Dict[str, Any] = {"started": True, "ended": False, "start_seq": head.get("seq", 0),
                           "provider": head.get("provider"), "model": head.get("model"),
                           "status": "running", "stop_reason": None}
    text: List[str] = []
    opened: Dict[Any, str] = {}
    for event in events[start + 1:]:
        kind = event.get("kind")
        if kind == "text":
            text.append(event.get("content") or "")
        elif kind == "card_opened":
            opened[event.get("card_id")] = event.get("summary") or event.get("tool") or "a risky step"
        elif kind == "card_closed":
            opened.pop(event.get("card_id"), None)
        elif kind == "turn_end":
            out.update(ended=True, status=event.get("status") or "done",
                       stop_reason=event.get("stop_reason"))
            break
    out["text"] = "".join(text)
    out["waiting_card"] = None if out["ended"] or not opened else next(iter(opened.values()))
    return out


def clamp_wait(seconds: Any) -> int:
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        value = 120
    return max(WAIT_MIN, min(WAIT_MAX, value))


def format_reply(chat_id: int, verdict: Dict[str, Any], resume_since: int, reply_text: str) -> str:
    """The text the model reads. `resume_since` is where gamachine_wait_reply must restart."""
    status = verdict["status"] if verdict.get("started") else "running"
    lines = [f"status: {status}"]
    model = "/".join(str(x) for x in (verdict.get("provider"), verdict.get("model")) if x)
    if model:
        lines.append(f"model: {model}")
    if verdict.get("stop_reason"):
        lines.append(f"stop_reason: {verdict['stop_reason']}")
    if verdict.get("waiting_card"):
        lines.append(f"waiting for the user's approval in Gamachine: {verdict['waiting_card']}")
    if not verdict.get("ended"):
        lines.append("the reply turn has started" if verdict.get("started")
                     else "the reply turn has not started yet")
        lines.append(f"still running; call gamachine_wait_reply(chat_id={chat_id}, since={resume_since})")
    if reply_text:
        lines.append("reply:")
        lines.append(reply_text)
    return "\n".join(lines)


async def wait_for_reply(
    chat_id: int, since: int, wait_seconds: Any, *, busy_at_send: bool, epoch: Optional[str],
    fetch_events: Callable[[int], Awaitable[dict]],
    fetch_messages: Callable[[int], Awaitable[dict]],
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Poll the chat's events until the reply turn ends or the time is up."""
    deadline = clock() + clamp_wait(wait_seconds)
    events: List[dict] = []
    cursor, gap = since, False
    while True:
        data = await fetch_events(cursor)
        if epoch is None:
            epoch = data.get("epoch")
        elif data.get("epoch") != epoch:
            return RESTARTED
        gap = gap or bool(data.get("gap"))
        events.extend(data.get("events") or [])
        cursor = max([cursor, data.get("last_seq") or 0] + [e.get("seq", 0) for e in events])
        verdict = analyze_reply(events, busy_at_send)
        if verdict["ended"]:
            text = verdict["text"]
            if gap:
                text = await _last_assistant_text(fetch_messages) or text
            return format_reply(chat_id, verdict, cursor, text)
        if clock() >= deadline:
            # A started turn is collected again from its start, so no text is lost.
            resume = verdict["start_seq"] - 1 if verdict["started"] else cursor
            return format_reply(chat_id, verdict, resume, verdict.get("text", ""))
        await sleep(POLL_S)


async def _last_assistant_text(fetch_messages: Callable[[int], Awaitable[dict]]) -> str:
    data = await fetch_messages(10)
    for message in reversed(data.get("messages") or []):
        if message.get("role") == "assistant":
            return message.get("text") or ""
    return ""


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
    if sent.get("status") != "accepted":
        return NOT_READY
    return await wait_for_reply(
        chat_id, sent.get("since_seq", 0), wait_seconds, busy_at_send=bool(sent.get("busy_at_send")),
        epoch=sent.get("epoch"), fetch_events=_events_fetcher(chat_id), fetch_messages=_messages_fetcher(chat_id))


async def wait_reply(chat_id: int, since: int, wait_seconds: int = 120) -> str:
    return await wait_for_reply(
        chat_id, since, wait_seconds, busy_at_send=False, epoch=None,
        fetch_events=_events_fetcher(chat_id), fetch_messages=_messages_fetcher(chat_id))


async def read_messages(chat_id: int, limit: int = 10) -> str:
    data = await _request("GET", f"/chats/{chat_id}/messages", params={"limit": limit})
    return "\n\n".join(f"[{m.get('role')}] {m.get('text', '')}" for m in data.get("messages", [])) \
        or "No messages."


def _events_fetcher(chat_id: int) -> Callable[[int], Awaitable[dict]]:
    return lambda since: _request("GET", f"/chats/{chat_id}/events", params={"since": since})


def _messages_fetcher(chat_id: int) -> Callable[[int], Awaitable[dict]]:
    return lambda limit: _request("GET", f"/chats/{chat_id}/messages", params={"limit": limit})


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
    async def gamachine_wait_reply(chat_id: int, since: int, wait_seconds: int = 120) -> str:
        """Keep waiting for a reply that was still running; `since` comes from the earlier result."""
        return await _guard(wait_reply(chat_id, since, wait_seconds))

    @mcp.tool()
    async def gamachine_read_messages(chat_id: int, limit: int = 10) -> str:
        """Read the last messages (1-50) of a chat."""
        return await _guard(read_messages(chat_id, limit))

    return mcp


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
