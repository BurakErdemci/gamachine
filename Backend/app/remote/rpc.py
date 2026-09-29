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

from agentic import approval_mode, cards, chat_model, turn_events
from providers.agy_provider import AgyStepGateError
from remote import chats, webpush
from providers.effort_caps import EFFORT_LEVELS
from remote.desktop_channel import (
    CARD_CLOSED_TYPE, CHANNEL, EFFORT_SET_TYPE, FRAME_TYPE, MODE_CHANGED_TYPE, MODEL_CHANGED_TYPE,
)
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
            "list_slash_commands": self.list_slash_commands,
            "get_config": self.get_config,
            "set_approval_mode": self.set_approval_mode,
            "list_models": self.list_models,
            "set_model": self.set_model,
            "set_effort": self.set_effort,
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
        # No await from here until the listener is in `session.chats`: two
        # concurrent opens of one chat could otherwise both close "the old
        # one" and the loser's listener would survive close_chat and close().
        session.close_chat(conv)
        backlog, sub = turn_events.join(conv, since or 0)
        events = backlog["events"] if since is not None else chats.current_turn_events(backlog["events"])
        result = {"chat_id": str(conv), "messages": messages,
                  "events": [chats.phone_event(conv, e) for e in events],
                  "gap": bool(backlog["gap"]) if since is not None else False,
                  "epoch": backlog["epoch"], "last_seq": backlog["last_seq"]}
        while len(session.chats) >= session.MAX_OPEN_CHATS:
            session.close_chat(next(iter(session.chats)))
        replied = asyncio.Event()
        task = asyncio.get_running_loop().create_task(
            self._forward(session, conv, sub, backlog["last_seq"], replied))

        def _closed(done: asyncio.Task) -> None:
            # A callback, not a finally: a task cancelled before its first
            # step never runs its body.
            sub.close()
            if session.chats.get(conv) is done:
                session.chats.pop(conv, None)
        task.add_done_callback(_closed)
        session.chats[conv] = task
        # The reply goes first: the page clears the chat log when it arrives,
        # so a live event sent before it would be wiped.
        try:
            await session.reply({"id": rid, "ok": True, "result": result})
        except BaseException:
            task.cancel()
            raise
        replied.set()
        return _REPLIED

    async def _forward(self, session: PhoneSession, conv: int, sub, last_seq: int,
                       replied: asyncio.Event) -> None:
        try:
            await replied.wait()
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
            # Only the winning answer closes the desktop's copy of the card.
            # The decision is already committed: a failed notification must not
            # turn it into an error the phone would retry into already_answered.
            try:
                CHANNEL.publish({"type": CARD_CLOSED_TYPE, "card_id": card_id,
                                 "conversation_id": card.conversation_id, "by": res.get("by"),
                                 "decision": target, "outcome": res.get("outcome"),
                                 "at": int(time.time() * 1000)})
            except Exception:
                logger.exception("[remote] card_closed publish failed for %s", card_id)
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
        effort = req.get("effort")
        if effort is not None:
            # What this chat's model accepts, from the desktop's own registry.
            if not isinstance(effort, str) or effort not in chats.effort_levels(
                    row["provider_type"], row["model_name"]):
                raise RpcError("bad_effort")
            frame["effort"] = effort
        return {"status": "accepted" if CHANNEL.publish(frame) else "desktop_not_ready"}

    async def list_slash_commands(self, session, req, rid):
        row = self._chat(req.get("chat_id"))
        catalog = self.bridge.list_slash_commands
        if catalog is None:
            raise RpcError("unavailable")
        family = chats.slash_family(row)
        return chats.phone_catalog(None if family is None else await catalog(family))

    async def get_config(self, session, req, rid):
        out = {"approval_mode": approval_mode.current_mode(),
               "desktop_effort": self.bridge.current_desktop_effort()}
        if req.get("chat_id") is not None:
            row = self._chat(req.get("chat_id"))
            out.update(provider_type=row["provider_type"], model_name=row["model_name"],
                       family=chat_model.cli_family(row["provider_type"], row["model_name"]),
                       effort_levels=chats.effort_levels(row["provider_type"], row["model_name"]))
        return out

    async def list_models(self, session, req, rid):
        catalog = self.bridge.list_models
        if catalog is None:
            raise RpcError("unavailable")
        return await catalog(chats.LOCAL_USER_ID)

    async def set_model(self, session: PhoneSession, req, rid):
        # Owner decisions, 28 and 29 Sep 2026: the phone picks a chat's provider
        # and model exactly as the desktop's picker does (`pick_chat_model`: the
        # chat, and the default a new chat opens on); a turn already running
        # finishes on the model it started with. Unlike the desktop, a provider
        # that is not ready is refused: the phone cannot open Settings for a key.
        row = self._chat(req.get("chat_id"))
        conv = row["id"]
        try:
            # Off the loop: the readiness probes read the API key and may ask Ollama.
            result = await asyncio.to_thread(
                chat_model.pick_chat_model, self.db, chats.LOCAL_USER_ID, conv,
                req.get("provider_type"), req.get("model_name"), require_ready=True)
        except chat_model.ChatModelError as exc:
            raise RpcError(exc.code, **exc.extra)
        logger.info("[remote] chat %s model -> %s / %s by %s", conv, result["provider_type"],
                    result["model_name"], session.device_label)
        # Stored already: a failed notification must not turn it into an error
        # the phone would retry.
        try:
            CHANNEL.publish({"type": MODEL_CHANGED_TYPE, "conversation_id": conv,
                             "provider_type": result["provider_type"],
                             "model_name": result["model_name"],
                             "by": session.device_label, "at": int(time.time() * 1000)})
        except Exception:
            logger.exception("[remote] chat_model_changed publish failed")
        return result

    async def set_effort(self, session: PhoneSession, req, rid):
        # The desktop's effort is one state of its renderer (`thinkingLevel`),
        # so the renderer applies it, only if the active model offers the level;
        # what it really has comes back through its own report
        # (`bridge.set_desktop_effort`). Nothing is stored here.
        level = req.get("level")
        if not isinstance(level, str) or level not in EFFORT_LEVELS:
            raise RpcError("bad_effort")
        logger.info("[remote] effort %s requested by %s", level, session.device_label)
        frame = {"type": EFFORT_SET_TYPE, "level": level, "by": session.device_label,
                 "at": int(time.time() * 1000)}
        return {"status": "accepted" if CHANNEL.publish(frame) else "desktop_not_ready"}

    async def set_approval_mode(self, session: PhoneSession, req, rid):
        # Owner decision, 28 Sep 2026: a paired phone may change the mode, and
        # so skips the UI secret the local route demands. That secret guards
        # against the Unity MCP server and model-run children, which can read
        # the app token; this path is reachable only through the paired
        # device's end-to-end session keys.
        mode = req.get("mode")
        if not isinstance(mode, str) or mode not in approval_mode.MODES:
            raise RpcError("bad_mode")
        apply = self.bridge.apply_approval_mode
        if apply is None:
            raise RpcError("unavailable")
        try:
            result = apply(mode, "phone")
        except AgyStepGateError as exc:
            raise RpcError("agy_step_refused", message=str(exc), params=dict(exc.params))
        logger.warning("[remote] approval mode %s -> %s by %s", result["previous"], result["mode"],
                       session.device_label)
        # The change is applied and drained: a failed notification must not
        # turn it into an error the phone would retry.
        try:
            CHANNEL.publish({"type": MODE_CHANGED_TYPE, "mode": result["mode"],
                             "previous": result["previous"], "approved_pending": result["approved_pending"],
                             "by": session.device_label, "at": int(time.time() * 1000)})
        except Exception:
            logger.exception("[remote] approval_mode_changed publish failed")
        return result

    async def push_subscribe(self, session: PhoneSession, req, rid):
        try:
            sub = webpush.validate_subscription(req.get("subscription"))
        except ValueError as exc:
            raise RpcError("bad_subscription", detail=str(exc))
        await asyncio.to_thread(self.bridge.store.set_push_subscription, session.device.device_id, sub)
        session.device.push_subscription = sub
        return {}
