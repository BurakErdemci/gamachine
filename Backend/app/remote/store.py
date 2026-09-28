"""Persistent state of remote control.

Secrets (the PC's static key, the room key, the VAPID key) sit in
`app_settings` encrypted with the same Fernet key as the API key vault
(`DatabaseManager._encrypt_api_key`). Paired devices get their own table;
a phone's relay token is never stored, only its hash (the relay's hash).
"""
from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from remote import crypto as C

DEFAULT_RELAY_URL = "https://gamachine-relay.erdemciburakemre.workers.dev"

SETTING_ENABLED = "remote.enabled"
SETTING_RELAY_URL = "remote.relay_url"
SETTING_KEEP_AWAKE = "remote.keep_awake"
SETTING_KEYS = "remote.keys"

# The relay keeps at most 50 token hashes per room.
MAX_DEVICES = 50
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Same rule as isRelayOrigin in Frontend/frontend/main/helpers/remote-control.ts.
_RELAY_ORIGIN = re.compile(r"^(https?)://(\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::([0-9]{1,5}))?/?$",
                           re.IGNORECASE | re.ASCII)


@dataclass
class Keys:
    static: C.KeyPair
    room_key: str
    vapid: C.KeyPair

    @property
    def pair_id(self) -> str:
        return C.pair_id_for(self.room_key)


@dataclass
class Device:
    device_id: str
    name: str
    phone_pub: bytes
    token_hash: str
    created: float
    last_seen: Optional[float]
    push_subscription: Optional[dict]

    def public(self) -> dict:
        return {"device_id": self.device_id, "name": self.name,
                "created": int(self.created * 1000),
                "last_seen": int(self.last_seen * 1000) if self.last_seen else None,
                "push": self.push_subscription is not None}


def normalize_relay_url(url: Any) -> str:
    """The relay origin as `https://host[:port]`; plain http only for a
    loopback relay (`wrangler dev --local`, tests). Raises ValueError."""
    if not isinstance(url, str) or len(url) > 512:
        raise ValueError("relay URL must be a string")
    m = _RELAY_ORIGIN.fullmatch(url.strip())
    if not m:
        raise ValueError("relay URL must be an origin: https://host[:port]")
    if m.group(3) is not None and not 1 <= int(m.group(3)) <= 65535:
        raise ValueError("relay URL port is out of range")
    if m.group(2).startswith("["):
        try:
            ipaddress.IPv6Address(m.group(2)[1:-1])
        except ValueError:
            raise ValueError("relay URL has a malformed IPv6 host") from None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or not host:
        raise ValueError("relay URL must start with https://")
    if parts.scheme == "http" and host not in _LOOPBACK:
        raise ValueError("plain http is only allowed for a relay on this machine")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("relay URL must be an origin (no path, query or credentials)")
    netloc = f"[{host}]" if ":" in host else host
    if parts.port:
        netloc += f":{parts.port}"
    return f"{parts.scheme}://{netloc}"


def ws_origin(relay_url: str) -> str:
    return ("wss://" if relay_url.startswith("https://") else "ws://") + relay_url.split("://", 1)[1]


class RemoteStore:
    def __init__(self, db):
        self.db = db
        with closing(sqlite3.connect(db.db_path)) as conn, conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS remote_devices (
                device_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                phone_pub TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created REAL NOT NULL,
                last_seen REAL,
                push_subscription TEXT)''')

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db.db_path)

    # ── settings ────────────────────────────────────────────────────────
    def enabled(self) -> bool:
        return self.db.get_setting(SETTING_ENABLED) == "1"

    def set_enabled(self, on: bool) -> None:
        self.db.set_setting(SETTING_ENABLED, "1" if on else "0")

    def relay_url(self) -> str:
        stored = self.db.get_setting(SETTING_RELAY_URL)
        if stored:
            try:
                return normalize_relay_url(stored)
            except ValueError:
                pass
        return DEFAULT_RELAY_URL

    def custom_relay_url(self) -> Optional[str]:
        return self.db.get_setting(SETTING_RELAY_URL) or None

    def set_relay_url(self, url: Optional[str]) -> str:
        """None or "" goes back to the project default."""
        if url in (None, ""):
            self.db.set_setting(SETTING_RELAY_URL, "")
            return DEFAULT_RELAY_URL
        normalized = normalize_relay_url(url)
        self.db.set_setting(SETTING_RELAY_URL, "" if normalized == DEFAULT_RELAY_URL else normalized)
        return normalized

    def keep_awake(self) -> bool:
        return self.db.get_setting(SETTING_KEEP_AWAKE) == "1"

    def set_keep_awake(self, on: bool) -> None:
        self.db.set_setting(SETTING_KEEP_AWAKE, "1" if on else "0")

    # ── keys ────────────────────────────────────────────────────────────
    def load_keys(self) -> Optional[Keys]:
        stored = self.db.get_setting(SETTING_KEYS)
        if not stored:
            return None
        plain = self.db._decrypt_api_key(stored)
        if not plain:
            return None
        data = json.loads(plain)
        return Keys(static=C.KeyPair.from_d(C.from_b64u(data["static_d"])),
                    room_key=data["room_key"],
                    vapid=C.KeyPair.from_d(C.from_b64u(data["vapid_d"])))

    def ensure_keys(self) -> Keys:
        keys = self.load_keys()
        if keys is not None:
            return keys
        keys = Keys(static=C.KeyPair.generate(), room_key=C.random_b64u(32), vapid=C.KeyPair.generate())
        payload = json.dumps({"static_d": C.b64u(keys.static.d), "room_key": keys.room_key,
                              "vapid_d": C.b64u(keys.vapid.d)})
        self.db.set_setting(SETTING_KEYS, self.db._encrypt_api_key(payload))
        return keys

    def delete_keys(self) -> None:
        self.db.set_setting(SETTING_KEYS, "")

    # ── devices ─────────────────────────────────────────────────────────
    @staticmethod
    def _row(r) -> Device:
        sub = None
        if r[6]:
            try:
                sub = json.loads(r[6])
            except ValueError:
                sub = None
        return Device(device_id=r[0], name=r[1], phone_pub=C.from_b64u(r[2]), token_hash=r[3],
                      created=r[4], last_seen=r[5], push_subscription=sub)

    _COLS = "device_id, name, phone_pub, token_hash, created, last_seen, push_subscription"

    def add_device(self, device_id: str, name: str, phone_pub: bytes, token_hash: str) -> Device:
        now = time.time()
        with closing(self._connect()) as conn, conn:
            conn.execute(f"INSERT INTO remote_devices ({self._COLS}) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                         (device_id, name, C.b64u(phone_pub), token_hash, now, now))
        return Device(device_id, name, phone_pub, token_hash, now, now, None)

    def get_device(self, device_id: str) -> Optional[Device]:
        with closing(self._connect()) as conn:
            r = conn.execute(f"SELECT {self._COLS} FROM remote_devices WHERE device_id = ?",
                             (device_id,)).fetchone()
        return self._row(r) if r else None

    def list_devices(self) -> List[Device]:
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT {self._COLS} FROM remote_devices ORDER BY created").fetchall()
        return [self._row(r) for r in rows]

    def token_hashes(self) -> List[str]:
        with closing(self._connect()) as conn:
            return [r[0] for r in conn.execute("SELECT token_hash FROM remote_devices ORDER BY created")]

    def count_devices(self) -> int:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT COUNT(*) FROM remote_devices").fetchone()[0]

    def remove_device(self, device_id: str) -> Optional[Device]:
        device = self.get_device(device_id)
        if device is None:
            return None
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM remote_devices WHERE device_id = ?", (device_id,))
        return device

    def remove_all_devices(self) -> int:
        with closing(self._connect()) as conn, conn:
            return conn.execute("DELETE FROM remote_devices").rowcount

    def touch(self, device_id: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE remote_devices SET last_seen = ? WHERE device_id = ?",
                         (time.time(), device_id))

    def set_push_subscription(self, device_id: str, subscription: Optional[dict]) -> None:
        value = None if subscription is None else json.dumps(subscription, separators=(",", ":"))
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE remote_devices SET push_subscription = ? WHERE device_id = ?",
                         (value, device_id))

    def push_targets(self) -> List[Device]:
        return [d for d in self.list_devices() if d.push_subscription is not None]

    def drop_push_endpoint(self, endpoint: str) -> int:
        """A 404/410 from the push service: every device on that endpoint loses it."""
        dropped = 0
        for device in self.push_targets():
            if device.push_subscription.get("endpoint") == endpoint:
                self.set_push_subscription(device.device_id, None)
                dropped += 1
        return dropped

    def status_snapshot(self) -> Dict[str, Any]:
        return {"relay_url": self.relay_url(), "custom_relay_url": self.custom_relay_url(),
                "keep_awake": self.keep_awake(), "enabled": self.enabled()}
