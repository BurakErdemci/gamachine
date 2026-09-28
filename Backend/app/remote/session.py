"""Per-connection phone sessions (docs/remote-control.md, "Session crypto").

`hello` -> `hello_ack` / `hello_reject`, then AES-256-GCM frames under
per-direction keys. One session per phone connection; a new `hello` on the
same connection (the phone sends one after `pc_online`) replaces it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from remote import crypto as C
from remote.store import Device, RemoteStore

logger = logging.getLogger(__name__)

# Plaintext budget of one PC -> phone frame. The relay drops PC frames over
# 1 MiB of UTF-8, measured on the whole `{type:"to", conn, data}` text, and
# base64url grows the sealed bytes by 4/3: 786 000 plaintext bytes is the
# ceiling, this leaves room for the envelope.
FRAME_PLAINTEXT_MAX = 700_000


def handle_hello(msg: dict, conn_token_hash: Optional[str], store: RemoteStore,
                 static_key: C.KeyPair, now: float,
                 eph: Optional[C.KeyPair] = None) -> Tuple[dict, Optional[Tuple[Device, C.Channel]]]:
    """The PC's answer to a `hello` and, when accepted, the device and channel."""
    try:
        device_id = msg.get("device_id")
        if not isinstance(device_id, str) or len(device_id) != 22 or len(C.from_b64u(device_id)) != 16:
            raise ValueError("bad device_id")
        eph_phone = C.from_b64u(msg.get("eph_phone_pub"))
        C.load_public(eph_phone)
        t = msg.get("t")
        if not isinstance(t, int) or isinstance(t, bool) or not 0 <= t < 2 ** 63:
            raise ValueError("bad t")
        tag = C.from_b64u(msg.get("tag"))
        if len(tag) != 32:
            raise ValueError("bad tag")
    except ValueError as exc:
        logger.info("[remote] malformed hello: %s", exc)
        return {"type": "hello_reject", "reason": "unknown_device"}, None
    device = store.get_device(device_id)
    # The relay admitted this socket for one token; the hello must name the
    # device that token belongs to.
    if device is None or conn_token_hash is None or not C.equal(
            device.token_hash.encode(), conn_token_hash.encode()):
        return {"type": "hello_reject", "reason": "unknown_device"}, None
    k_static = static_key.ecdh(device.phone_pub)
    if not C.equal(tag, C.hmac_sha256(k_static, C.hello_tag_input(device_id, eph_phone, t))):
        return {"type": "hello_reject", "reason": "unknown_device"}, None
    if abs(t - now) > C.HELLO_WINDOW_S:
        return {"type": "hello_reject", "reason": "clock"}, None
    eph = eph or C.KeyPair.generate()
    ack_tag = C.hmac_sha256(k_static, C.hello_ack_tag_input(eph_phone, eph.public_raw))
    p2c, c2p = C.session_keys(eph.ecdh(eph_phone), k_static)
    ack = {"type": "hello_ack", "eph_pc_pub": C.b64u(eph.public_raw), "tag": C.b64u(ack_tag)}
    return ack, (device, C.Channel.for_pc(p2c, c2p))


def _size(obj: Any) -> int:
    return len(C.compact_json(obj).encode("utf-8"))


def split_reply(reply: dict, budget: int = FRAME_PLAINTEXT_MAX) -> List[dict]:
    """A reply too big for one frame, as several replies with the same `id`.

    Every list in `result` is cut into consecutive pieces; each part carries
    the scalar fields, its pieces, `part` (1-based) and `parts`. Concatenating
    each list over the parts in order gives the original.
    """
    if _size(reply) <= budget:
        return [reply]
    result = reply.get("result")
    if not isinstance(result, dict):
        raise ValueError("reply too large and not splittable")
    list_keys = [k for k, v in result.items() if isinstance(v, list)]
    base = {k: v for k, v in result.items() if k not in list_keys}
    empty = {**base, **{k: [] for k in list_keys}}
    overhead = _size({**reply, "result": empty, "part": 10 ** 6, "parts": 10 ** 6})
    room = budget - overhead
    parts: List[Dict[str, list]] = []
    current: Dict[str, list] = {k: [] for k in list_keys}
    used = 0
    for key in list_keys:
        for item in result[key]:
            size = _size(item) + 1
            if size > room:
                item, size = {"truncated": True}, _size({"truncated": True}) + 1
            if used + size > room and any(current.values()):
                parts.append(current)
                current = {k: [] for k in list_keys}
                used = 0
            current[key].append(item)
            used += size
    parts.append(current)
    total = len(parts)
    return [{**reply, "result": {**base, **piece}, "part": i + 1, "parts": total}
            for i, piece in enumerate(parts)]


class PhoneSession:
    MAX_TASKS = 16
    MAX_OPEN_CHATS = 8

    def __init__(self, conn: str, device: Device, channel: C.Channel,
                 send_raw: Callable[[str, dict], Awaitable[bool]]):
        self.conn = conn
        self.device = device
        self.channel = channel
        self._send_raw = send_raw
        self._lock = asyncio.Lock()
        self.chats: Dict[int, asyncio.Task] = {}
        self.tasks: Set[asyncio.Task] = set()
        self.closed = False

    @property
    def device_label(self) -> str:
        """What the ledger and `already_answered.by` show for this phone."""
        return f"phone:{self.device.name}"

    async def send(self, obj: dict) -> bool:
        # Seal and send under one lock: frames must reach the phone in counter
        # order, or it drops the one that arrives late.
        async with self._lock:
            if self.closed:
                return False
            return await self._send_raw(self.conn, self.channel.seal(obj))

    async def reply(self, reply: dict) -> None:
        for part in split_reply(reply):
            if not await self.send(part):
                return

    def spawn(self, coro) -> bool:
        if len(self.tasks) >= self.MAX_TASKS:
            coro.close()
            return False
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return True

    def close_chat(self, conv_id: int) -> None:
        task = self.chats.pop(conv_id, None)
        if task is not None:
            task.cancel()

    def close(self) -> None:
        self.closed = True
        for conv_id in list(self.chats):
            self.close_chat(conv_id)
        for task in list(self.tasks):
            task.cancel()
