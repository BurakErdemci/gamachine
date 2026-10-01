"""Pairing (docs/remote-control.md, "Pairing").

An `Offer` is the secret half of a pairing: `pair_secret`, its 5-minute life
and its single use. How the phone learned it (today a QR link, later a typed
short code - owner decision 28 Sep 2026) is the offer's `source` and the
caller's business: `PairingManager.create_offer(source)` is the hook a second
presentation plugs into, and everything after it (limits, mac check, SAS,
pending approval, approve / reject / expiry) is shared.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Container, Deque, Dict, Optional

from remote import crypto as C

logger = logging.getLogger(__name__)

OFFER_TTL_S = 300.0
APPROVAL_TTL_S = 300.0
PER_ID_PER_MINUTE = 5
PER_IP_PER_HOUR = 20
DEVICE_NAME_MAX = 64


@dataclass
class Offer:
    secret: bytes
    source: str
    created: float
    expires: float
    used: bool = False

    @property
    def secret_b64u(self) -> str:
        return C.b64u(self.secret)


@dataclass
class PendingPair:
    conn: str
    device_name: str
    phone_pub: bytes
    sas: str
    k_pair: bytes
    source: str
    created: float
    expires: float
    timer: Any = field(default=None, repr=False)

    def public(self) -> dict:
        # The SAS is always part of what the desktop shows: it is the defence
        # against a leaked QR today and the main one for a typed code later.
        return {"device_name": self.device_name, "sas": self.sas, "source": self.source,
                "expires_at": int(self.expires * 1000)}


def qr_url(relay_url: str, pair_id: str, pc_pub: bytes, offer: Offer) -> str:
    return f"{relay_url}/p#{pair_id}.{C.b64u(pc_pub)}.{offer.secret_b64u}"


def _valid_device_name(name: Any) -> bool:
    if not isinstance(name, str) or not name.strip() or len(name) > DEVICE_NAME_MAX:
        return False
    return not any(unicodedata.category(ch)[0] == "C" for ch in name)


class PairingManager:
    def __init__(self, static_key: Callable[[], C.KeyPair],
                 send_to: Callable[[str, dict], Awaitable[bool]],
                 clock: Callable[[], float] = time.time):
        self._static_key = static_key
        self._send_to = send_to
        self._clock = clock
        self.offer: Optional[Offer] = None
        self.pending: Optional[PendingPair] = None
        self._id_hits: Deque[float] = collections.deque()
        self._ip_hits: Dict[str, Deque[float]] = {}

    # ── offers ─────────────────────────────────────────────────────────
    def create_offer(self, source: str = "qr") -> Offer:
        """A new pair_secret; the previous offer and any pending approval end."""
        now = self._clock()
        self._drop_pending("expired")
        self.offer = Offer(secret=secrets.token_bytes(16), source=source, created=now,
                           expires=now + OFFER_TTL_S)
        return self.offer

    def live_offer(self) -> Optional[Offer]:
        offer = self.offer
        if offer is None or offer.used or self._clock() >= offer.expires:
            return None
        return offer

    def cancel(self) -> None:
        self.offer = None
        self._drop_pending("rejected")

    # ── limits ─────────────────────────────────────────────────────────
    def _limited(self, ip: str) -> bool:
        """Counts this attempt; True when it is over a limit."""
        now = self._clock()
        while self._id_hits and now - self._id_hits[0] >= 60.0:
            self._id_hits.popleft()
        hits = self._ip_hits.setdefault(ip or "unknown", collections.deque())
        while hits and now - hits[0] >= 3600.0:
            hits.popleft()
        for key in [k for k, v in self._ip_hits.items() if not v and k != ip]:
            del self._ip_hits[key]
        over = len(self._id_hits) >= PER_ID_PER_MINUTE or len(hits) >= PER_IP_PER_HOUR
        self._id_hits.append(now)
        hits.append(now)
        return over

    # ── the phone's request ────────────────────────────────────────────
    async def on_request(self, conn: str, ip: str, data: str,
                         is_live: Optional[Callable[[str], bool]] = None) -> Optional[str]:
        """Handle one `pair_request`; returns the reject reason, or None when
        the request now waits for the desktop's approval. `is_live` answers
        whether the pairing socket is still there: the caller runs this as a
        task, and the relay connection may have dropped since the frame came."""
        if self._limited(ip):
            await self._reject(conn, "rate_limited")
            return "rate_limited"
        try:
            msg = json.loads(data)
        except ValueError:
            msg = None
        offer = self.live_offer()
        if offer is None:
            await self._reject(conn, "expired")
            return "expired"
        if self.pending is not None:
            await self._reject(conn, "rejected")
            return "rejected"
        try:
            if not isinstance(msg, dict) or msg.get("type") != "pair_request":
                raise ValueError("not a pair_request")
            phone_pub = C.from_b64u(msg.get("phone_pub"))
            C.load_public(phone_pub)
            name = msg.get("device_name")
            if not _valid_device_name(name):
                raise ValueError("bad device name")
            mac = C.from_b64u(msg.get("mac"))
            if not C.equal(mac, C.pair_mac(offer.secret, phone_pub, name)):
                raise ValueError("bad mac")
        except ValueError as exc:
            logger.info("[remote] pairing request refused: %s", exc)
            await self._reject(conn, "rejected")
            return "rejected"
        if is_live is not None and not is_live(conn):
            logger.info("[remote] pairing request ignored: its socket is gone")
            return "gone"
        # Single use: the first request that proves the secret consumes it.
        offer.used = True
        k_static = self._static_key().ecdh(phone_pub)
        _, code = C.sas(k_static, offer.secret)
        now = self._clock()
        pending = PendingPair(conn=conn, device_name=name, phone_pub=phone_pub, sas=code,
                              k_pair=C.pair_key(k_static, offer.secret), source=offer.source,
                              created=now, expires=now + APPROVAL_TTL_S)
        pending.timer = asyncio.get_running_loop().call_later(
            APPROVAL_TTL_S, lambda: asyncio.ensure_future(self._expire(pending)))
        self.pending = pending
        return None

    async def _expire(self, pending: PendingPair) -> None:
        if self.pending is pending:
            self.pending = None
            await self._reject(pending.conn, "expired")

    def take_pending(self) -> Optional[PendingPair]:
        pending = self.pending
        if pending is None:
            return None
        self.pending = None
        if pending.timer is not None:
            pending.timer.cancel()
        if self._clock() >= pending.expires:
            asyncio.ensure_future(self._reject(pending.conn, "expired"))
            return None
        return pending

    async def reject_pending(self) -> bool:
        pending = self.take_pending()
        if pending is None:
            return False
        await self._reject(pending.conn, "rejected")
        return True

    def on_socket_gone(self, conn: str) -> None:
        pending = self.pending
        if pending is not None and pending.conn == conn:
            self.pending = None
            if pending.timer is not None:
                pending.timer.cancel()

    def drop_pending_unless_live(self, live: Container[str]) -> bool:
        """Forget a pending approval whose pairing socket is not in `live`.
        When the PC's relay socket drops, the relay closes the pairing sockets
        itself and no `pair_close` ever reaches us, so this is the only way such
        a pending ends before its timer. Nothing is sent: no socket to hear it."""
        pending = self.pending
        if pending is None or pending.conn in live:
            return False
        self.on_socket_gone(pending.conn)
        return True

    def _drop_pending(self, reason: str) -> None:
        pending = self.pending
        if pending is None:
            return
        self.pending = None
        if pending.timer is not None:
            pending.timer.cancel()
        try:
            asyncio.get_running_loop().create_task(self._reject(pending.conn, reason))
        except RuntimeError:
            pass

    async def _reject(self, conn: str, reason: str) -> None:
        await self._send_to(conn, {"type": "pair_reject", "reason": reason})

    def pending_public(self) -> Optional[dict]:
        pending = self.pending
        if pending is None or self._clock() >= pending.expires:
            return None
        return pending.public()
