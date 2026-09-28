"""The remote bridge (docs/remote-control.md, "Parts" 3).

Off: no socket, no listener, no push - zero network traffic. On: one
`RelayClient`, pairing, a `PhoneSession` per phone connection, the RPC
dispatcher, live pushes to phones and web push. Everything runs on the
backend's event loop, so `cards.answer_card` is called on the loop thread.

How the relay addresses phones (relay/worker/room.js): every phone or pairing
socket gets a random `conn` id; its frames reach the PC as
`{type:"from", conn, data}` and the PC answers with `{type:"to", conn, data}`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agentic import cards, turn_events
from remote import chats
from remote import crypto as C
from remote.pairing import PairingManager, qr_url
from remote.relay_client import RelayClient, TokenOpError
from remote.rpc import Dispatcher, request_id
from remote.session import PhoneSession, handle_hello
from remote.store import MAX_DEVICES, DEFAULT_RELAY_URL, Keys, RemoteStore, ws_origin
from remote.webpush import PushSender

logger = logging.getLogger(__name__)

TOUCH_EVERY_S = 60.0


class BridgeError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


class RemoteBridge:
    connect_wait_s = 10.0

    def __init__(self, db, stop_chat: Optional[Callable[[int], Awaitable[dict]]] = None,
                 clock: Callable[[], float] = time.time):
        self.db = db
        self.store = RemoteStore(db)
        self.stop_chat = stop_chat
        self.clock = clock
        self.keys: Optional[Keys] = None
        self.client: Optional[RelayClient] = None
        self.pairing = PairingManager(lambda: self.keys.static, self._send_to, clock)
        self.rpc = Dispatcher(self)
        self.push = PushSender(self.store, lambda: self.keys.vapid if self.keys else None)
        self.sessions: Dict[str, PhoneSession] = {}
        self._phones: Dict[str, str] = {}
        self._pairs: Dict[str, str] = {}
        self._agents: Dict[int, str] = {}
        self._touched: Dict[str, float] = {}
        self._pair_ok_sent: Dict[str, str] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._listening = False
        self._tasks: set = set()
        self.relay_error: Optional[str] = None

    # ── lifecycle ──────────────────────────────────────────────────────
    async def startup(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.store.enabled():
            self.keys = self.store.ensure_keys()
            self._start_relay()

    async def shutdown(self) -> None:
        await self._stop_relay()
        await self.push.aclose()

    def _start_relay(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.client is not None:
            return
        self.client = RelayClient(ws_origin(self.store.relay_url()), self.keys.pair_id,
                                  self.keys.room_key, self._on_relay, self._on_disconnect)
        self.client.start()
        if not self._listening:
            turn_events.add_listener(self._ring_listener)
            cards.add_listener(self._card_listener)
            self._listening = True

    async def _stop_relay(self) -> None:
        if self._listening:
            turn_events.remove_listener(self._ring_listener)
            cards.remove_listener(self._card_listener)
            self._listening = False
        self.push.cancel()
        self.pairing.cancel()
        client, self.client = self.client, None
        if client is not None:
            await client.stop()
        self._drop_connections()

    def _drop_connections(self) -> None:
        for session in self.sessions.values():
            session.close()
        self.sessions.clear()
        self._phones.clear()
        self._pairs.clear()
        self._pair_ok_sent.clear()

    async def enable(self) -> dict:
        self.keys = self.store.ensure_keys()
        self.store.set_enabled(True)
        self._start_relay()
        return self.status()

    async def disable(self) -> dict:
        self.store.set_enabled(False)
        await self._stop_relay()
        return self.status()

    async def forget(self) -> dict:
        """Turn off and forget: the relay drops the room (`reset_room`), then
        keys and devices go. A relay that cannot be reached keeps the room
        until its 30-day expiry; nothing can open it without the room key."""
        reset = False
        keys = self.keys or self.store.load_keys()
        if keys is not None:
            client = self.client
            temporary = None
            if client is None:
                temporary = client = RelayClient(ws_origin(self.store.relay_url()), keys.pair_id,
                                                 keys.room_key, self._ignore_frame)
                client.start()
            if await client.wait_connected(self.connect_wait_s):
                reset = await client.send({"type": "reset_room"})
            if temporary is not None:
                await temporary.stop()
        await self._stop_relay()
        self.store.remove_all_devices()
        self.store.delete_keys()
        self.store.set_enabled(False)
        self.keys = None
        return {**self.status(), "relay_reset": reset}

    async def _ignore_frame(self, msg: dict) -> None:
        return None

    def status(self) -> dict:
        client = self.client
        enabled = self.store.enabled()
        keep_awake = self.store.keep_awake()
        offer = self.pairing.live_offer()
        return {
            "enabled": enabled,
            "connected": bool(client and client.connected),
            "relay_url": self.store.relay_url(),
            "default_relay_url": DEFAULT_RELAY_URL,
            "custom_relay": self.store.relay_url() != DEFAULT_RELAY_URL,
            "last_error": (client.last_error if client else None) or self.relay_error,
            "retry_at": int(client.retry_at * 1000) if client and client.retry_at else None,
            "gave_up": bool(client and client.gave_up),
            "pair_id": self.keys.pair_id if self.keys else None,
            "devices": self.store.count_devices(),
            "phones_online": len(self.sessions),
            "keep_awake": keep_awake,
            "keep_awake_active": enabled and keep_awake,
            "pairing": {"offer_expires_at": int(offer.expires * 1000) if offer else None,
                        "pending": self.pairing.pending is not None},
        }

    # ── settings ───────────────────────────────────────────────────────
    async def set_relay_url(self, url: Optional[str]) -> dict:
        before = self.store.relay_url()
        after = self.store.set_relay_url(url)
        if after != before and self.client is not None:
            await self._stop_relay()
            self._start_relay()
        # Phones load their page from the relay they paired on, so a device
        # paired on the old relay cannot reach this one.
        return {"relay_url": after, "devices_need_repair": after != before and self.store.count_devices() > 0}

    def set_keep_awake(self, on: bool) -> dict:
        self.store.set_keep_awake(on)
        return {"keep_awake": on, "keep_awake_active": on and self.store.enabled()}

    # ── pairing ────────────────────────────────────────────────────────
    async def start_pairing(self, source: str = "qr") -> dict:
        if not self.store.enabled() or self.client is None:
            raise BridgeError("remote_off")
        if self.store.count_devices() >= MAX_DEVICES:
            raise BridgeError("too_many_devices")
        # The room must exist when the phone scans (else `no_room`).
        if not await self.client.wait_connected(self.connect_wait_s):
            raise BridgeError("relay_unreachable", 503)
        offer = self.pairing.create_offer(source)
        return {"qr_url": qr_url(self.store.relay_url(), self.keys.pair_id, self.keys.static.public_raw, offer),
                "expires_at": int(offer.expires * 1000), "pair_id": self.keys.pair_id}

    def pending_pairing(self) -> Optional[dict]:
        return self.pairing.pending_public()

    async def approve_pairing(self) -> dict:
        pending = self.pairing.take_pending()
        if pending is None:
            raise BridgeError("no_pending_pairing", 404)
        if pending.conn not in self._pairs or self.client is None:
            raise BridgeError("phone_left")
        device_id = C.random_b64u(16)
        token = C.random_b64u(32)
        token_hash = C.token_hash(token)
        name = pending.device_name
        self.store.add_device(device_id, name, pending.phone_pub, token_hash)
        try:
            # README: register the hash before the pair_ok that carries the token.
            await self.client.token_op({"type": "register_tokens", "hashes": [token_hash]})
        except TokenOpError as exc:
            self.store.remove_device(device_id)
            await self._send_to(pending.conn, {"type": "pair_reject", "reason": "rejected"})
            raise BridgeError(f"relay_{exc}", 503)
        ok = C.pair_ok(pending.k_pair, {"device_id": device_id, "token": token,
                                        "vapid_pub": C.b64u(self.keys.vapid.public_raw)})
        if not await self._send_to(pending.conn, ok):
            await self._forget_device(device_id)
            raise BridgeError("phone_left")
        # The relay answers `gone` when the socket left before `to` reached it.
        self._pair_ok_sent[pending.conn] = device_id
        return {"device": self.store.get_device(device_id).public()}

    async def reject_pairing(self) -> bool:
        return await self.pairing.reject_pending()

    # ── devices ────────────────────────────────────────────────────────
    def devices(self) -> List[dict]:
        online = {s.device.device_id for s in self.sessions.values()}
        return [{**d.public(), "online": d.device_id in online} for d in self.store.list_devices()]

    async def remove_device(self, device_id: str) -> bool:
        return await self._forget_device(device_id)

    async def _forget_device(self, device_id: str) -> bool:
        device = self.store.remove_device(device_id)
        if device is None:
            return False
        for conn, session in list(self.sessions.items()):
            if session.device.device_id == device_id:
                session.close()
                del self.sessions[conn]
        if self.client is not None and self.client.connected:
            try:
                await self.client.token_op({"type": "drop_token", "hash": device.token_hash})
            except TokenOpError as exc:
                # The next welcome re-registers exactly our hashes (replace:true).
                logger.info("[remote] drop_token not confirmed: %s", exc)
        return True

    async def remove_all_devices(self) -> int:
        count = self.store.remove_all_devices()
        for session in self.sessions.values():
            session.close()
        self.sessions.clear()
        if self.client is not None and self.client.connected:
            try:
                await self.client.token_op({"type": "register_tokens", "hashes": [], "replace": True})
            except TokenOpError as exc:
                logger.info("[remote] token reset not confirmed: %s", exc)
        return count

    # ── relay frames ───────────────────────────────────────────────────
    async def _send_to(self, conn: str, obj: dict) -> bool:
        client = self.client
        if client is None:
            return False
        return await client.send({"type": "to", "conn": conn, "data": C.compact_json(obj)})

    async def _on_disconnect(self) -> None:
        self._drop_connections()

    async def _on_relay(self, m: dict) -> None:
        mtype = m.get("type")
        conn = m.get("conn")
        if mtype == "welcome":
            self._drop_connections()
            for p in m.get("phones") or []:
                if isinstance(p, dict) and isinstance(p.get("conn"), str) and isinstance(p.get("token_hash"), str):
                    self._phones[p["conn"]] = p["token_hash"]
            for p in m.get("pairs") or []:
                if isinstance(p, dict) and isinstance(p.get("conn"), str):
                    self._pairs[p["conn"]] = str(p.get("ip") or "unknown")
            # Always the exact list: covers a recreated room (`tokens: 0`), a
            # drop_token lost while offline, and a device removed meanwhile.
            fut = self.client.send_token_op({"type": "register_tokens", "replace": True,
                                             "hashes": self.store.token_hashes()})
            if fut is not None:
                fut.add_done_callback(_log_token_result)
            return
        if not isinstance(conn, str):
            if mtype == "error":
                self.relay_error = str(m.get("error"))
                logger.info("[remote] relay error: %s", m.get("error"))
            return
        if mtype == "phone_open" and isinstance(m.get("token_hash"), str):
            self._phones[conn] = m["token_hash"]
        elif mtype == "pair_open":
            self._pairs[conn] = str(m.get("ip") or "unknown")
        elif mtype in ("phone_close", "pair_close", "gone"):
            undelivered = self._pair_ok_sent.pop(conn, None)
            if mtype == "gone" and undelivered is not None:
                self._spawn(self._forget_device(undelivered))
            self._phones.pop(conn, None)
            self._pairs.pop(conn, None)
            session = self.sessions.pop(conn, None)
            if session is not None:
                session.close()
            self.pairing.on_socket_gone(conn)
        elif mtype == "from" and isinstance(m.get("data"), str):
            if conn in self._pairs:
                self._spawn(self.pairing.on_request(conn, self._pairs[conn], m["data"]))
            elif conn in self._phones:
                await self._on_phone_data(conn, m["data"])

    async def _on_phone_data(self, conn: str, data: str) -> None:
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "hello":
            reply, accepted = handle_hello(msg, self._phones.get(conn), self.store, self.keys.static,
                                           self.clock())
            old = self.sessions.pop(conn, None)
            if old is not None:
                old.close()
            await self._send_to(conn, reply)
            if accepted is not None:
                device, channel = accepted
                self.sessions[conn] = PhoneSession(conn, device, channel, self._send_to)
                self._touch(device.device_id, force=True)
            return
        session = self.sessions.get(conn)
        if session is None or "c" not in msg:
            return
        obj = session.channel.open(msg)
        if obj is None:
            return
        self._touch(session.device.device_id)
        if not session.spawn(self.rpc.handle(session, obj)):
            session.reply_busy(request_id(obj.get("id")) if isinstance(obj, dict) else None)

    def _touch(self, device_id: str, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._touched.get(device_id, 0.0) >= TOUCH_EVERY_S:
            self._touched[device_id] = now
            self.store.touch(device_id)

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── pushes: ring and cards -> phones and web push ──────────────────
    def _call_soon(self, fn, *args) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(fn, *args)

    def _ring_listener(self, conv_id: int, event: dict) -> None:
        if event.get("kind") in ("turn_start", "turn_end"):
            self._call_soon(self._on_turn_event, conv_id, event)

    def _card_listener(self, what: str, card) -> None:
        self._call_soon(self._on_card, what, card.public())

    def _broadcast(self, obj: dict) -> None:
        for session in list(self.sessions.values()):
            self._spawn(session.send(obj))

    def _chat_changed(self, conv_id: Optional[int]) -> None:
        if conv_id is None or not self.sessions:
            return

        async def run():
            row = await asyncio.to_thread(chats.chat_row, self.db, conv_id)
            if row is not None:
                self._broadcast({"type": "chat_changed", "chat": chats.summary(row, chats.carded_chats())})
        self._spawn(run())

    def _on_turn_event(self, conv_id: int, event: dict) -> None:
        kind = event["kind"]
        if kind == "turn_start" and event.get("provider"):
            self._agents[conv_id] = event["provider"]
        self._chat_changed(conv_id)
        if kind == "turn_end" and event.get("status") in ("done", "error"):
            self._push_turn_end(conv_id, event["status"])
        elif kind == "turn_start" and event.get("origin") == "wake":
            self._push_chat(conv_id, "Not geldi", "Başka bir sohbetten not geldi; çalışmaya başladı.")

    def _on_card(self, what: str, card: dict) -> None:
        if what == "opened":
            self._broadcast({"type": "card_opened", "card": chats.phone_card(card)})
            detail = card.get("summary") or ""
            body = f"{card['tool']}: {detail}" if card.get("tool") and detail else (card.get("tool") or detail)
            what_text = "Soru bekliyor" if card.get("kind") == "question" else "Onay bekliyor"
            self._push_chat(card.get("conversation_id"), what_text, body, urgency="high")
        else:
            self._broadcast({"type": "card_closed", "card_id": card["card_id"]})
        self._chat_changed(card.get("conversation_id"))

    def _push_chat(self, conv_id: Optional[int], what: str, body: str, urgency: str = "normal") -> None:
        # Synchronous on purpose: notify() calls must keep the order of the
        # events, or coalescing would keep an older card as "the last one".
        if not self._listening:
            # A ring/card callback queued before disable can still run here;
            # it would schedule a push under the new generation.
            return
        if conv_id is None:
            self.push.notify("global", f"{what} - Gamachine", body, "/p", "gamachine", urgency)
            return
        title = self.db.get_conversation_title(conv_id)
        agent = self._agents.get(conv_id)
        if agent is None:
            row = chats.chat_row(self.db, conv_id)
            agent = row["provider"] if row else None
        self.push.notify(conv_id, f"{what} - {chats.agent_name(agent)} ({title or 'Sohbet'})", body,
                         f"/p#chat={conv_id}", f"chat-{conv_id}", urgency)

    def _push_turn_end(self, conv_id: int, status: str) -> None:
        if status == "error":
            self._push_chat(conv_id, "Hata", "Tur bir hatayla bitti.")
            return
        last_text = ""
        for event in reversed(turn_events.since(conv_id, 0)["events"]):
            if event["kind"] == "turn_start":
                break
            if event["kind"] == "text":
                last_text = event.get("content", "") + last_text
                if len(last_text) > 400:
                    break
        self._push_chat(conv_id, "İş bitti", last_text or "Tur tamamlandı.")


def _log_token_result(fut: asyncio.Future) -> None:
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc is not None:
        logger.info("[remote] token registration not confirmed: %s", exc)
