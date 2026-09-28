"""The allow-listed requests a phone may make (docs/remote-control.md, "Messages").

Anything not in `HANDLERS` is refused. Every field is validated; a bad
request gets `{id, ok:false, error}` and never ends the session.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import TYPE_CHECKING, Any, Optional

from agentic import cards, turn_events
from remote import chats, webpush
from remote.desktop_channel import CHANNEL, FRAME_TYPE
from remote.session import PhoneSession

if TYPE_CHECKING:
    from remote.bridge import RemoteBridge

logger = logging.getLogger(__name__)

TEXT_MAX = 20_000
_REPLIED = object()


class RpcError(Exception):
    def __init__(self, error: str, **extra: Any):
        super().__init__(error)
        self.error = error
        self.extra = extra


def request_id(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and -(2 ** 53) < value < 2 ** 53:
        return value
    if isinstance(value, str) and 0 < len(value) <= 64:
        return value
    return None


def _conv_id(value: Any) -> int:
    if isinstance(value, bool):
        raise RpcError("bad_chat_id")
    if isinstance(value, int):
        conv = value
    elif isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 15:
        conv = int(value)
    else:
        raise RpcError("bad_chat_id")
    if conv < 1:
        raise RpcError("bad_chat_id")
    return conv


class Dispatcher:
    def __init__(self, bridge: "RemoteBridge"):
        self.bridge = bridge
        self.handlers = {
            "list_chats": self.list_chats,
            "open_chat": self.open_chat,
            "close_chat": self.close_chat,
            "pending_cards": self.pending_cards,
            "answer_card": self.answer_card,
            "stop": self.stop,
            "send_message": self.send_message,
            "push_subscribe": self.push_subscribe,
        }

    @property
    def db(self):
        return self.bridge.db

    async def handle(self, session: PhoneSession, obj: Any) -> None:
        rid = request_id(obj.get("id")) if isinstance(obj, dict) else None
        try:
            if not isinstance(obj, dict) or rid is None:
                raise RpcError("bad_request")
            handler = self.handlers.get(obj.get("type"))
            if handler is None:
                raise RpcError("unknown_type")
            result = await handler(session, obj, rid)
            if result is _REPLIED:
                return
            reply = {"id": rid, "ok": True, "result": result}
        except RpcError as exc:
            reply = {"id": rid, "ok": False, "error": exc.error, **exc.extra}
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[remote] %s failed", obj.get("type") if isinstance(obj, dict) else "?")
            reply = {"id": rid, "ok": False, "error": "internal"}
        await session.reply(reply)

    def _chat(self, value: Any) -> dict:
        row = chats.chat_row(self.db, _conv_id(value))
        if row is None:
            raise RpcError("unknown_chat")
        return row

    # ── handlers ───────────────────────────────────────────────────────
    async def list_chats(self, session, req, rid):
        return {"chats": await asyncio.to_thread(chats.list_chats, self.db)}

    async def open_chat(self, session: PhoneSession, req, rid):
        row = self._chat(req.get("chat_id"))
        conv = row["id"]
        since = req.get("since_seq")
        if since is not None and (isinstance(since, bool) or not isinstance(since, int)
                                  or not 0 <= since < 2 ** 53):
            raise RpcError("bad_since_seq")
        messages = await asyncio.to_thread(chats.recent_messages, self.db, conv)
        session.close_chat(conv)
        backlog, sub = turn_events.join(conv, since or 0)
        events = backlog["events"] if since is not None else chats.current_turn_events(backlog["events"])
        result = {"chat_id": str(conv), "messages": messages,
                  "events": [chats.phone_event(conv, e) for e in events],
                  "gap": bool(backlog["gap"]) if since is not None else False,
                  "epoch": backlog["epoch"], "last_seq": backlog["last_seq"]}
        # The reply goes first: the page clears the chat log when it arrives,
        # so a live event sent before it would be wiped.
        try:
            await session.reply({"id": rid, "ok": True, "result": result})
        except BaseException:
            sub.close()
            raise
        while len(session.chats) >= session.MAX_OPEN_CHATS:
            session.close_chat(next(iter(session.chats)))
        task = asyncio.get_running_loop().create_task(
            self._forward(session, conv, sub, backlog["last_seq"]))
        session.chats[conv] = task
        return _REPLIED

    async def _forward(self, session: PhoneSession, conv: int, sub, last_seq: int) -> None:
        try:
            while True:
                event = await sub.get()
                if event is None:
                    # The chat was deleted: the page reloads its list.
                    await session.send({"type": "chat_changed"})
                    return
                if sub.lagged:
                    sub.lagged = False
                    backlog = turn_events.since(conv, last_seq)
                    if backlog["gap"]:
                        await session.send({"type": "gap", "chat_id": str(conv),
                                            "epoch": backlog["epoch"], "last_seq": backlog["last_seq"]})
                    for e in backlog["events"]:
                        if e["seq"] > last_seq:
                            await session.send(chats.phone_event(conv, e))
                            last_seq = e["seq"]
                if event["seq"] <= last_seq:
                    continue
                if not await session.send(chats.phone_event(conv, event)):
                    return
                last_seq = event["seq"]
        except asyncio.CancelledError:
            pass
        finally:
            sub.close()
            if session.chats.get(conv) is asyncio.current_task():
                session.chats.pop(conv, None)

    async def close_chat(self, session, req, rid):
        session.close_chat(_conv_id(req.get("chat_id")))
        return {}

    async def pending_cards(self, session, req, rid):
        return {"cards": [chats.phone_card(c) for c in cards.list_pending()]}

    async def answer_card(self, session: PhoneSession, req, rid):
        card_id = req.get("card_id")
        decision = req.get("decision")
        if not isinstance(card_id, str) or not 0 < len(card_id) <= cards.CARD_ID_MAX:
            raise RpcError("bad_card_id")
        if decision not in ("approve", "reject", "choice"):
            raise RpcError("bad_decision")
        card = cards.get(card_id)
        if card is None:
            raise RpcError("not_found")
        choice = None
        if card.kind == "question":
            if decision == "reject":
                target = "reject"
            elif decision == "choice":
                options = chats.single_choice_options(card.public())
                label = req.get("choice")
                if options is None:
                    raise RpcError("unsupported_on_phone")
                if not isinstance(label, str) or label not in options:
                    raise RpcError("bad_choice")
                target, choice = "answer", {card.questions[0]["question"]: label}
            else:
                raise RpcError("unsupported_on_phone")
        elif decision == "choice":
            raise RpcError("bad_decision")
        else:
            target = decision
        # On the event loop thread: resolvers set asyncio events.
        res = cards.answer_card(card_id, target, choice, device=session.device_label)
        status = res.get("status")
        if status == "ok":
            return {"outcome": res.get("outcome"), "by": res.get("by"), "at": res.get("at")}
        if status == "already_answered":
            raise RpcError("already_answered", by=res.get("by"), at=res.get("at"))
        if status == "not_found":
            raise RpcError("not_found")
        raise RpcError("invalid", detail=res.get("error"))

    async def stop(self, session, req, rid):
        row = self._chat(req.get("chat_id"))
        stop_chat = self.bridge.stop_chat
        if stop_chat is None:
            raise RpcError("unavailable")
        res = await stop_chat(row["id"])
        return {"status": (res or {}).get("status")}

    async def send_message(self, session: PhoneSession, req, rid):
        row = self._chat(req.get("chat_id"))
        text = req.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
            raise RpcError("bad_text")
        frame = {"type": FRAME_TYPE, "request_id": secrets.token_hex(8), "conversation_id": row["id"],
                 "text": text, "source": "phone", "device_id": session.device.device_id,
                 "device_name": session.device.name, "at": int(time.time() * 1000)}
        return {"status": "accepted" if CHANNEL.publish(frame) else "desktop_not_ready"}

    async def push_subscribe(self, session: PhoneSession, req, rid):
        try:
            sub = webpush.validate_subscription(req.get("subscription"))
        except ValueError as exc:
            raise RpcError("bad_subscription", detail=str(exc))
        await asyncio.to_thread(self.bridge.store.set_push_subscription, session.device.device_id, sub)
        session.device.push_subscription = sub
        return {}
