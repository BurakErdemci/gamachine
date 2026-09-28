"""Protocol crypto of the remote bridge (docs/remote-control.md, "Byte layouts").

Mirrors relay/public/crypto.js and relay/test/nodeimpl.mjs byte for byte;
tests/test_remote_crypto.py reproduces every value of relay/test/vectors.json.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import re
import secrets
from typing import Any, Optional, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization as _ser
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOM_LABEL = "gamachine-remote-v1 room"
INFO_SAS = "gamachine-remote-v1 sas"
INFO_PAIR = "gamachine-remote-v1 pair"
INFO_SESSION = "gamachine-remote-v1 session"
PHONE_TO_PC = 1
PC_TO_PHONE = 2
HELLO_WINDOW_S = 300
# The phone checks counters with Number.isSafeInteger.
MAX_COUNTER = 2 ** 53 - 1

_B64U_RE = re.compile(r"^[A-Za-z0-9_-]*$")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_b64u(text: Any) -> bytes:
    """Strict unpadded base64url; raises ValueError on anything else."""
    if not isinstance(text, str) or not _B64U_RE.match(text) or len(text) % 4 == 1:
        raise ValueError("bad base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    return _hmac.new(key, data, hashlib.sha256).digest()


def hkdf(ikm: bytes, salt: bytes, info: str, length: int) -> bytes:
    # An empty salt is the same HMAC key as HashLen zero bytes (RFC 5869), so
    # this matches WebCrypto's HKDF with salt = new Uint8Array(0).
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt or None,
                info=info.encode("utf-8")).derive(ikm)


def equal(a: bytes, b: bytes) -> bool:
    return _hmac.compare_digest(a, b)


def random_b64u(nbytes: int) -> str:
    return b64u(secrets.token_bytes(nbytes))


# ── relay ───────────────────────────────────────────────────────────────────

def pair_id_input(room_key: str) -> bytes:
    return ROOM_LABEL.encode("utf-8") + room_key.encode("ascii")


def pair_id_for(room_key: str) -> str:
    return b64u(sha256(pair_id_input(room_key))[:16])


def token_hash(token: str) -> str:
    """What the relay stores for a phone token: SHA-256 over the token's UTF-8 text."""
    return b64u(sha256(token.encode("utf-8")))


# ── keys ────────────────────────────────────────────────────────────────────

def load_public(raw: bytes) -> ec.EllipticCurvePublicKey:
    """A 65-byte uncompressed P-256 point; raises ValueError for anything else
    (compressed points and points off the curve included)."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 65 or raw[0] != 4:
        raise ValueError("public key must be a 65-byte uncompressed P-256 point")
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes(raw))


class KeyPair:
    """A P-256 key pair; `d` is the 32-byte private scalar as stored."""

    def __init__(self, private: ec.EllipticCurvePrivateKey):
        self._private = private
        self.public_raw = private.public_key().public_bytes(
            encoding=_ser.Encoding.X962, format=_ser.PublicFormat.UncompressedPoint)

    @classmethod
    def generate(cls) -> "KeyPair":
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @classmethod
    def from_d(cls, d: bytes) -> "KeyPair":
        if len(d) != 32:
            raise ValueError("private scalar must be 32 bytes")
        return cls(ec.derive_private_key(int.from_bytes(d, "big"), ec.SECP256R1()))

    @property
    def d(self) -> bytes:
        return self._private.private_numbers().private_value.to_bytes(32, "big")

    @property
    def private_key(self) -> ec.EllipticCurvePrivateKey:
        return self._private

    def ecdh(self, peer_raw: bytes) -> bytes:
        return self._private.exchange(ec.ECDH(), load_public(peer_raw))


# ── pairing ─────────────────────────────────────────────────────────────────

def pair_mac_input(phone_pub: bytes, device_name: str) -> bytes:
    return phone_pub + device_name.encode("utf-8")


def pair_mac(pair_secret: bytes, phone_pub: bytes, device_name: str) -> bytes:
    return hmac_sha256(pair_secret, pair_mac_input(phone_pub, device_name))


def sas(k_static: bytes, pair_secret: bytes) -> Tuple[bytes, str]:
    okm = hkdf(k_static, pair_secret, INFO_SAS, 4)
    return okm, "%04d" % (int.from_bytes(okm, "big") % 10000)


def pair_key(k_static: bytes, pair_secret: bytes) -> bytes:
    return hkdf(k_static, pair_secret, INFO_PAIR, 32)


def pair_ok(k_pair: bytes, payload: dict) -> dict:
    """The `pair_ok` message: sealed with K_pair, PC -> phone, counter 1."""
    return {"type": "pair_ok", **seal(k_pair, PC_TO_PHONE, 1, compact_json(payload).encode("utf-8"))}


# ── session ─────────────────────────────────────────────────────────────────

def hello_tag_input(device_id: str, eph_phone_pub: bytes, t: int) -> bytes:
    return b"hello" + device_id.encode("utf-8") + eph_phone_pub + t.to_bytes(8, "big")


def hello_ack_tag_input(eph_phone_pub: bytes, eph_pc_pub: bytes) -> bytes:
    return b"hello_ack" + eph_phone_pub + eph_pc_pub


def session_ikm(eph_shared: bytes, k_static: bytes) -> bytes:
    return eph_shared + k_static


def session_keys(eph_shared: bytes, k_static: bytes) -> Tuple[bytes, bytes]:
    """(phone->PC key, PC->phone key)."""
    okm = hkdf(session_ikm(eph_shared, k_static), b"", INFO_SESSION, 64)
    return okm[:32], okm[32:]


# ── frames ──────────────────────────────────────────────────────────────────

def nonce(direction: int, counter: int) -> bytes:
    return direction.to_bytes(4, "big") + counter.to_bytes(8, "big")


def seal(key: bytes, direction: int, counter: int, plaintext: bytes) -> dict:
    return {"c": counter, "d": b64u(AESGCM(key).encrypt(nonce(direction, counter), plaintext, None))}


def valid_counter(c: Any) -> bool:
    return isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= MAX_COUNTER


def open_frame(key: bytes, direction: int, frame: Any) -> bytes:
    """Plaintext of a frame; raises on a malformed or forged one."""
    if not isinstance(frame, dict) or not valid_counter(frame.get("c")):
        raise ValueError("bad frame")
    body = from_b64u(frame.get("d"))
    return AESGCM(key).decrypt(nonce(direction, frame["c"]), body, None)


class Channel:
    """One direction pair of a connection. Counters start at 1; a received
    counter not greater than the last accepted one is dropped, and a frame
    that fails to decrypt leaves the counter where it was."""

    def __init__(self, send_key: bytes, recv_key: bytes, send_dir: int, recv_dir: int):
        self._send_key = send_key
        self._recv_key = recv_key
        self._send_dir = send_dir
        self._recv_dir = recv_dir
        self.send_counter = 0
        self.last_recv = 0

    @classmethod
    def for_pc(cls, phone_to_pc: bytes, pc_to_phone: bytes) -> "Channel":
        return cls(pc_to_phone, phone_to_pc, PC_TO_PHONE, PHONE_TO_PC)

    @classmethod
    def for_phone(cls, phone_to_pc: bytes, pc_to_phone: bytes) -> "Channel":
        return cls(phone_to_pc, pc_to_phone, PHONE_TO_PC, PC_TO_PHONE)

    def seal(self, obj: Any) -> dict:
        self.send_counter += 1
        return seal(self._send_key, self._send_dir, self.send_counter,
                    compact_json(obj).encode("utf-8"))

    def seal_text(self, text: str) -> dict:
        self.send_counter += 1
        return seal(self._send_key, self._send_dir, self.send_counter, text.encode("utf-8"))

    def open(self, frame: Any) -> Optional[Any]:
        """The decoded object, or None for a replayed, forged or malformed frame."""
        if not isinstance(frame, dict) or not valid_counter(frame.get("c")):
            return None
        if frame["c"] <= self.last_recv:
            return None
        try:
            obj = json.loads(open_frame(self._recv_key, self._recv_dir, frame).decode("utf-8"))
        except Exception:
            return None
        self.last_recv = frame["c"]
        return obj
