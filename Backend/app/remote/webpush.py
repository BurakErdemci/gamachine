"""Web push from the PC straight to the phone's push service
(docs/remote-control.md, "Web push"): RFC 8291 aes128gcm payload encryption
and RFC 8292 VAPID, with `cryptography` and `httpx`. The relay is not involved.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from remote import crypto as C

logger = logging.getLogger(__name__)

RECORD_SIZE = 4096
TITLE_MAX = 120
BODY_MAX = 180
TTL_S = 3600
# The subscription endpoint is where the PC POSTs, so a paired phone must not
# be able to point it at the LAN or at this machine: only the push services
# browsers use are accepted.
PUSH_HOST_SUFFIXES = ("push.apple.com", "fcm.googleapis.com", "android.googleapis.com",
                      "push.services.mozilla.com", "notify.windows.com")
# Tests only: lets a loopback fake push service in.
ALLOW_LOOPBACK_FOR_TESTS = False
VAPID_SUBJECT = "https://github.com/BurakErdemci/gamachine"


def _host_allowed(parts) -> bool:
    host = (parts.hostname or "").lower()
    if ALLOW_LOOPBACK_FOR_TESTS and host in ("127.0.0.1", "localhost"):
        return parts.scheme in ("http", "https")
    if parts.scheme != "https" or parts.port not in (None, 443):
        return False
    return any(host == s or host.endswith("." + s) for s in PUSH_HOST_SUFFIXES)


def validate_subscription(sub: Any) -> dict:
    """PushSubscription.toJSON() reduced to {endpoint, keys:{p256dh, auth}}."""
    if not isinstance(sub, dict):
        raise ValueError("subscription must be an object")
    endpoint = sub.get("endpoint")
    keys = sub.get("keys")
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or not isinstance(keys, dict):
        raise ValueError("endpoint and keys are required")
    parts = urlsplit(endpoint)
    if parts.username or parts.password or not _host_allowed(parts):
        raise ValueError("endpoint is not a known push service")
    p256dh = C.from_b64u(keys.get("p256dh"))
    C.load_public(p256dh)
    auth = C.from_b64u(keys.get("auth"))
    if len(auth) != 16:
        raise ValueError("auth must be 16 bytes")
    return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}


# ── RFC 8291 ────────────────────────────────────────────────────────────────

def encrypt(plaintext: bytes, ua_public: bytes, auth_secret: bytes,
            as_key: Optional[C.KeyPair] = None, salt: Optional[bytes] = None) -> bytes:
    """One aes128gcm record: salt || rs || idlen || keyid(as_public) || ciphertext."""
    as_key = as_key or C.KeyPair.generate()
    salt = salt or os.urandom(16)
    ecdh_secret = as_key.ecdh(ua_public)
    key_info = b"WebPush: info\x00" + ua_public + as_key.public_raw
    ikm = _hkdf_bytes(ecdh_secret, auth_secret, key_info, 32)
    cek = _hkdf_bytes(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_bytes(ikm, salt, b"Content-Encoding: nonce\x00", 12)
    record = plaintext + b"\x02"
    if len(record) + 16 > RECORD_SIZE:
        raise ValueError("payload too large for one record")
    body = AESGCM(cek).encrypt(nonce, record, None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_key.public_raw)]) + as_key.public_raw
    return header + body


def _hkdf_bytes(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


# ── RFC 8292 ────────────────────────────────────────────────────────────────

def vapid_jwt(audience: str, key: C.KeyPair, expires: int, subject: str = VAPID_SUBJECT) -> str:
    header = C.b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = C.b64u(json.dumps({"aud": audience, "exp": expires, "sub": subject},
                               separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode("ascii")
    r, s = decode_dss_signature(key.private_key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    return f"{header}.{claims}.{C.b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def audience(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def request_headers(endpoint: str, key: C.KeyPair, urgency: str, now: float) -> Dict[str, str]:
    token = vapid_jwt(audience(endpoint), key, int(now) + 12 * 3600)
    return {"Authorization": f"vapid t={token}, k={C.b64u(key.public_raw)}",
            "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
            "TTL": str(TTL_S), "Urgency": urgency}


def clip(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ── sender ──────────────────────────────────────────────────────────────────

class PushSender:
    """One push per chat per `coalesce_s`: the first goes at once, later ones
    in the window replace each other and the last goes when it ends."""

    coalesce_s = 10.0
    timeout_s = 10.0

    def __init__(self, store, vapid_key: Callable[[], Optional[C.KeyPair]],
                 clock: Callable[[], float] = time.monotonic):
        self._store = store
        self._vapid_key = vapid_key
        self._clock = clock
        self._last: Dict[Any, float] = {}
        self._pending: Dict[Any, Tuple[dict, str]] = {}
        self._timers: Dict[Any, asyncio.TimerHandle] = {}
        self._tasks: set = set()
        # Bumped by cancel(): a delivery scheduled under an older generation
        # must not POST, even one already past its target lookup.
        self._generation = 0
        self._client: Optional[httpx.AsyncClient] = None
        self.sent = 0

    def notify(self, key: Any, title: str, body: str, url: str = "/p", tag: Optional[str] = None,
               urgency: str = "normal") -> None:
        payload = {"title": clip(title, TITLE_MAX), "body": clip(body, BODY_MAX), "url": url}
        if tag:
            payload["tag"] = tag
        now = self._clock()
        last = self._last.get(key)
        if last is None or now - last >= self.coalesce_s:
            self._last[key] = now
            self._spawn(payload, urgency)
            return
        self._pending[key] = (payload, urgency)
        if key not in self._timers:
            loop = asyncio.get_running_loop()
            self._timers[key] = loop.call_later(self.coalesce_s - (now - last), self._flush, key)

    def _flush(self, key: Any) -> None:
        self._timers.pop(key, None)
        item = self._pending.pop(key, None)
        if item is not None:
            self._last[key] = self._clock()
            self._spawn(*item)

    def _spawn(self, payload: dict, urgency: str) -> None:
        task = asyncio.get_running_loop().create_task(self._deliver(payload, urgency, self._generation))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, payload: dict, urgency: str, generation: int) -> None:
        key = self._vapid_key()
        if key is None:
            return
        targets = await asyncio.to_thread(self._store.push_targets)
        if not targets or generation != self._generation:
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False,
                                             trust_env=False)
        data = C.compact_json(payload).encode("utf-8")
        for device in targets:
            sub = device.push_subscription
            if generation != self._generation:
                return
            try:
                sub = validate_subscription(sub)
                body = encrypt(data, C.from_b64u(sub["keys"]["p256dh"]), C.from_b64u(sub["keys"]["auth"]))
                resp = await self._client.post(sub["endpoint"], content=body,
                                               headers=request_headers(sub["endpoint"], key, urgency, time.time()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info("[remote] push to %s not sent: %s", device.name, exc)
                continue
            if resp.status_code in (404, 410):
                await asyncio.to_thread(self._store.drop_push_endpoint, sub["endpoint"])
                logger.info("[remote] push subscription of %s is gone (%s); dropped",
                            device.name, resp.status_code)
            elif resp.status_code >= 300:
                logger.info("[remote] push service answered %s for %s", resp.status_code, device.name)
            else:
                self.sent += 1

    def cancel(self) -> None:
        """Remote control went off: nothing queued or in flight may reach the network."""
        self._generation += 1
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        self._pending.clear()
        for task in list(self._tasks):
            task.cancel()

    async def aclose(self) -> None:
        self.cancel()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def drain(self) -> None:
        """Tests: wait for sends in flight."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
