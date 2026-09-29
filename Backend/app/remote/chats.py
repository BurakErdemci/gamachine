"""What the phone sees of chats, cards and turn events (relay/README.md,
"What the page expects inside the channel"). Chat ids travel as strings:
the page compares them with ids it reads from `#chat=<id>`."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from agentic import cards, chat_model, turn_events

LOCAL_USER_ID = 1
LIST_LIMIT = 200
MESSAGES_LIMIT = 50
MESSAGE_TEXT_MAX = 100_000

AGENT_NAMES = {"claude": "Claude", "codex": "Codex", "agy": "Antigravity", "opencode": "OpenCode",
               "copilot": "Copilot", "cursor": "Cursor", "kimi": "Kimi", "gemini": "Gemini"}
KIND_TITLES = {"mcp": "Unity MCP", "mail": "Sohbetler arası not", "command": "Komut onayı",
               "question": "Soru"}

_CHAT_COLS = ("SELECT c.id, c.title, c.updated_at, c.hidden "
              "FROM conversations c WHERE c.user_id = ? AND c.side_of IS NULL")


SLASH_FAMILIES = ("claude", "codex", "agy")


def slash_family(row: dict) -> Optional[str]:
    """Which `/slash-commands` catalog serves a chat, None when it has none.

    Follows the chat's own model (`chat_model`): a subscription model id
    names its CLI family; API and local providers have no catalog."""
    family = chat_model.cli_family(row["provider_type"], row["model_name"])
    return family if family in SLASH_FAMILIES else None


# The one command Gamachine itself runs (the renderer, on both desktop and
# phone): the desktop's `/` menu lists it ahead of the CLI's, for every chat.
APP_COMMAND = {"name": "compact", "description": "Summarise this chat to free up context"}


def phone_catalog(catalog: Any) -> Dict[str, list]:
    """The slash catalog as the phone gets it: the app's own command first,
    then the CLI's, plain strings only, meta items cut down to the fields the
    picker shows."""
    catalog = catalog if isinstance(catalog, dict) else {}

    def names(value: Any) -> List[str]:
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    commands = names(catalog.get("commands"))
    meta = []
    for item in catalog.get("meta") if isinstance(catalog.get("meta"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            meta.append({k: item[k] for k in ("name", "description", "argumentHint", "insert", "displayName")
                         if isinstance(item.get(k), str)})
    if APP_COMMAND["name"] not in commands:
        commands.insert(0, APP_COMMAND["name"])
    if not any(m["name"] == APP_COMMAND["name"] for m in meta):
        meta.insert(0, dict(APP_COMMAND))
    return {"commands": commands, "skills": names(catalog.get("skills")), "meta": meta}


def agent_name(provider: Optional[str]) -> str:
    if not provider:
        return "Gamachine"
    if provider.startswith("api-"):
        provider = provider[4:]
    return AGENT_NAMES.get(provider, provider[:1].upper() + provider[1:])


def _ms(stamp: Optional[str]) -> Optional[int]:
    try:
        return int(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _row_dict(db, r) -> dict:
    chosen = chat_model.chat_model(db, LOCAL_USER_ID, r[0])
    provider_type, model_name = chosen["provider_type"], chosen["model_name"]
    return {"id": r[0], "title": r[1], "updated_at": r[2], "hidden": bool(r[3]),
            "provider_type": provider_type, "model_name": model_name,
            "provider": chat_model.agent_label(provider_type, model_name),
            "model": model_name or None}


def chat_rows(db, limit: int = LIST_LIMIT) -> List[dict]:
    with closing(sqlite3.connect(db.db_path)) as conn:
        rows = conn.execute(_CHAT_COLS + " ORDER BY c.updated_at DESC LIMIT ?",
                            (LOCAL_USER_ID, limit)).fetchall()
    return [_row_dict(db, r) for r in rows]


def chat_row(db, conv_id: int) -> Optional[dict]:
    """The chat if it is an ordinary chat of the local user (no side chats)."""
    with closing(sqlite3.connect(db.db_path)) as conn:
        r = conn.execute(_CHAT_COLS + " AND c.id = ?", (LOCAL_USER_ID, conv_id)).fetchone()
    return _row_dict(db, r) if r else None


def carded_chats() -> set:
    return {c["conversation_id"] for c in cards.list_pending() if c["conversation_id"] is not None}


def chat_status(conv_id: int, carded: Iterable[int]) -> str:
    if conv_id in carded:
        return "awaiting_card"
    try:
        from agentic.approval_policy import conversation_turn_in_flight
        if conversation_turn_in_flight(conv_id):
            return "running"
    except Exception:
        pass
    return "running" if turn_events.turn_open(conv_id) else "idle"


def summary(row: dict, carded: Iterable[int]) -> dict:
    return {"chat_id": str(row["id"]), "title": row["title"] or "", "provider": row["provider"],
            "model": row["model"], "status": chat_status(row["id"], carded),
            "last_activity": _ms(row["updated_at"]), "hidden": row["hidden"]}


def list_chats(db) -> List[dict]:
    carded = carded_chats()
    out = []
    for row in chat_rows(db):
        s = summary(row, carded)
        if row["hidden"] and s["status"] == "idle":
            continue
        out.append(s)
    return out


def recent_messages(db, conv_id: int, limit: int = MESSAGES_LIMIT) -> List[dict]:
    with closing(sqlite3.connect(db.db_path)) as conn:
        rows = conn.execute(
            "SELECT role, content, timestamp, provider, model FROM messages "
            "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?", (conv_id, limit)).fetchall()
    out = []
    for role, content, stamp, provider, model in reversed(rows):
        text = content or ""
        item = {"role": role, "text": text[:MESSAGE_TEXT_MAX], "at": _ms(stamp)}
        if len(text) > MESSAGE_TEXT_MAX:
            item["truncated"] = True
        if provider:
            item["provider"] = provider
            item["model"] = model
        out.append(item)
    return out


def phone_event(conv_id: int, event: dict) -> dict:
    kind = event.get("kind")
    out: Dict[str, Any] = {"type": "event", "chat_id": str(conv_id), "seq": event.get("seq"),
                           "kind": kind}
    if isinstance(event.get("ts"), (int, float)):
        out["ts"] = int(event["ts"] * 1000)
    if kind == "text":
        out["text"] = event.get("content", "")
    elif kind == "tool_call":
        out["tool"] = event.get("name")
        out["summary"] = event.get("summary")
    elif kind == "turn_start":
        for key in ("provider", "model", "origin"):
            out[key] = event.get(key)
    elif kind == "turn_end":
        out["status"] = event.get("status")
        if event.get("stop_reason"):
            out["stop_reason"] = event["stop_reason"]
    elif kind == "card_opened":
        for key in ("card_id", "card_kind", "tool", "summary"):
            out[key] = event.get(key)
    elif kind == "card_closed":
        for key in ("card_id", "decision", "by", "outcome"):
            out[key] = event.get(key)
    return out


def current_turn_events(events: List[dict]) -> List[dict]:
    """The running turn's events: DB messages already hold finished turns."""
    for i in range(len(events) - 1, -1, -1):
        kind = events[i]["kind"]
        if kind == "turn_end":
            return []
        if kind == "turn_start":
            return events[i:]
    return []


def single_choice_options(card: dict) -> Optional[List[str]]:
    """Options a phone can answer with one tap: one single-select question."""
    questions = card.get("questions") or []
    if len(questions) != 1 or questions[0].get("multi") or not questions[0].get("options"):
        return None
    return list(questions[0]["options"])


def phone_card(card: dict) -> dict:
    conv = card.get("conversation_id")
    kind = card.get("kind")
    out = {"card_id": card["card_id"], "chat_id": None if conv is None else str(conv),
           "kind": kind, "tool": card.get("tool"), "risk": card.get("risk"),
           "created_at": card.get("created_at")}
    if kind == "question":
        questions = card.get("questions") or []
        out["title"] = KIND_TITLES["question"]
        out["detail"] = "\n".join(q["question"] for q in questions) or card.get("summary") or ""
        options = single_choice_options(card)
        if options:
            out["choices"] = [{"id": label, "label": label} for label in options]
    else:
        out["title"] = card.get("tool") or KIND_TITLES.get(kind, "Onay")
        out["detail"] = card.get("summary") or ""
    return out
