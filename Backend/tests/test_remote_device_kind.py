"""The device kind ("phone" | "web" | unknown) travels from the pair_request to
the stored device and the desktop list. It is a display label, never trusted."""
import json
import sqlite3
from contextlib import closing

import pytest
from cryptography.fernet import Fernet

from database import DatabaseManager
from remote import crypto as C
from remote.bridge import RemoteBridge
from remote.pairing import PairingManager
from remote.store import RemoteStore


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return DatabaseManager(str(tmp_path / "kind.db"))


def _manager():
    sent = []

    async def send_to(conn, msg):
        sent.append((conn, msg))
        return True

    return PairingManager(lambda: C.KeyPair.generate(), send_to), sent


async def _request(client):
    """One valid pair_request carrying `client` (omitted when it is the sentinel)."""
    manager, sent = _manager()
    offer = manager.create_offer()
    key = C.KeyPair.generate()
    msg = {"type": "pair_request", "phone_pub": C.b64u(key.public_raw), "device_name": "iPhone",
           "mac": C.b64u(C.pair_mac(offer.secret, key.public_raw, "iPhone"))}
    if client != "__absent__":
        msg["client"] = client
    reason = await manager.on_request("c1", "1.2.3.4", json.dumps(msg))
    assert reason is None, (reason, sent)
    return manager


@pytest.mark.parametrize("client,expected", [
    ("phone", "phone"), ("web", "web"), ("tablet", None), ("", None), (None, None),
    (7, None), ("__absent__", None),
])
async def test_pair_request_kind_is_phone_web_or_unknown(client, expected):
    manager = await _request(client)
    assert manager.pending.kind == expected
    assert manager.pending_public()["kind"] == expected


def test_store_keeps_and_returns_the_kind(db):
    store = RemoteStore(db)
    store.add_device("d1", "iPhone", b"a" * 65, "h1", "phone")
    store.add_device("d2", "Tarayici", b"b" * 65, "h2", "web")
    store.add_device("d3", "Eski", b"c" * 65, "h3")
    kinds = {d.device_id: d.kind for d in store.list_devices()}
    assert kinds == {"d1": "phone", "d2": "web", "d3": None}
    assert store.get_device("d2").public()["kind"] == "web"
    assert store.get_device("d3").public()["kind"] is None


def test_bridge_devices_list_the_kind(db):
    bridge = RemoteBridge(db, stop_chat=None, plan_locked=lambda provider_type, model_name: False)
    bridge.store.add_device("d1", "iPhone", b"a" * 65, "h1", "phone")
    bridge.store.add_device("d2", "Tarayici", b"b" * 65, "h2", "web")
    bridge.store.add_device("d3", "Eski", b"c" * 65, "h3")
    assert {d["device_id"]: d["kind"] for d in bridge.devices()} == {"d1": "phone", "d2": "web", "d3": None}


def test_old_table_without_the_column_is_migrated_and_keeps_its_rows(db):
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("DROP TABLE IF EXISTS remote_devices")
        conn.execute('''CREATE TABLE remote_devices (
            device_id TEXT PRIMARY KEY, name TEXT NOT NULL, phone_pub TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE, created REAL NOT NULL, last_seen REAL,
            push_subscription TEXT)''')
        conn.execute("INSERT INTO remote_devices VALUES ('old', 'Eski', ?, 'hh', 1.0, NULL, NULL)",
                     (C.b64u(b"p" * 65),))
    store = RemoteStore(db)
    assert [(d.device_id, d.kind) for d in store.list_devices()] == [("old", None)]
    store.add_device("new", "Yeni", b"q" * 65, "h2", "web")
    assert store.get_device("new").kind == "web"
    RemoteStore(db)  # a second start must not try to add the column again
    assert len(store.list_devices()) == 2
