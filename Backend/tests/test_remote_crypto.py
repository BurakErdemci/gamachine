"""The bridge's crypto reproduces every value of relay/test/vectors.json.

The phone page (relay/public/crypto.js) and a node:crypto reference
(relay/test/nodeimpl.mjs) already agree on these vectors; matching them here
means the Python bridge speaks the same bytes.
"""
import json
from pathlib import Path

import pytest

from remote import crypto as C

VECTORS = json.loads((Path(__file__).resolve().parents[2] / "relay" / "test" / "vectors.json")
                     .read_text(encoding="utf-8"))
K = VECTORS["keys"]
R = VECTORS["relay"]
P = VECTORS["pairing"]
S = VECTORS["session"]
d = C.from_b64u


def test_keys_derive_their_public_points():
    for name in ("phone_static", "pc_static", "phone_ephemeral", "pc_ephemeral"):
        kp = C.KeyPair.from_d(d(K[name]["d"]))
        assert C.b64u(kp.public_raw) == K[name]["pub"], name
        assert C.b64u(kp.d) == K[name]["d"]


def test_relay_values():
    assert C.b64u(C.pair_id_input(R["room_key"])) == R["pair_id_input"]
    assert C.pair_id_for(R["room_key"]) == R["pair_id"]
    assert C.token_hash(R["token"]) == R["token_hash"]


def test_pairing_values():
    pc = C.KeyPair.from_d(d(K["pc_static"]["d"]))
    phone = C.KeyPair.from_d(d(K["phone_static"]["d"]))
    secret = d(P["pair_secret"])
    assert P["pair_id"] == R["pair_id"]
    assert f"{P['pair_id']}.{C.b64u(pc.public_raw)}.{P['pair_secret']}" == P["qr_fragment"]
    assert len(P["pair_id"]) == 22 and len(C.b64u(pc.public_raw)) == 87 and len(P["pair_secret"]) == 22

    assert C.b64u(C.pair_mac_input(phone.public_raw, P["device_name"])) == P["mac_input"]
    mac = C.pair_mac(secret, phone.public_raw, P["device_name"])
    assert C.b64u(mac) == P["mac"]
    assert P["pair_request"] == {"type": "pair_request", "phone_pub": K["phone_static"]["pub"],
                                 "device_name": P["device_name"], "mac": P["mac"]}

    k_static = pc.ecdh(phone.public_raw)
    assert k_static == phone.ecdh(pc.public_raw)
    assert C.b64u(k_static) == P["k_static"]
    okm, code = C.sas(k_static, secret)
    assert C.b64u(okm) == P["sas_okm"] and code == P["sas"]
    k_pair = C.pair_key(k_static, secret)
    assert C.b64u(k_pair) == P["k_pair"]

    payload = json.loads(P["pair_ok_plaintext"])
    assert C.compact_json(payload) == P["pair_ok_plaintext"]
    assert payload["token"] == R["token"]
    assert C.pair_ok(k_pair, payload) == P["pair_ok"]
    opened = C.open_frame(k_pair, C.PC_TO_PHONE, P["pair_ok"])
    assert opened.decode("utf-8") == P["pair_ok_plaintext"]


def test_session_values():
    pc = C.KeyPair.from_d(d(K["pc_static"]["d"]))
    phone = C.KeyPair.from_d(d(K["phone_static"]["d"]))
    eph_phone = C.KeyPair.from_d(d(K["phone_ephemeral"]["d"]))
    eph_pc = C.KeyPair.from_d(d(K["pc_ephemeral"]["d"]))
    k_static = pc.ecdh(phone.public_raw)

    tag_input = C.hello_tag_input(S["device_id"], eph_phone.public_raw, S["t"])
    assert C.b64u(tag_input) == S["hello_tag_input"]
    hello = {"type": "hello", "device_id": S["device_id"], "eph_phone_pub": C.b64u(eph_phone.public_raw),
             "t": S["t"], "tag": C.b64u(C.hmac_sha256(k_static, tag_input))}
    assert hello == S["hello"]

    ack_input = C.hello_ack_tag_input(eph_phone.public_raw, eph_pc.public_raw)
    assert C.b64u(ack_input) == S["hello_ack_tag_input"]
    ack = {"type": "hello_ack", "eph_pc_pub": C.b64u(eph_pc.public_raw),
           "tag": C.b64u(C.hmac_sha256(k_static, ack_input))}
    assert ack == S["hello_ack"]

    shared = eph_pc.ecdh(eph_phone.public_raw)
    assert shared == eph_phone.ecdh(eph_pc.public_raw)
    assert C.b64u(shared) == S["eph_shared"]
    assert C.b64u(C.session_ikm(shared, k_static)) == S["session_ikm"]
    assert C.b64u(C.hkdf(C.session_ikm(shared, k_static), b"", C.INFO_SESSION, 64)) == S["session_okm"]
    p2c, c2p = C.session_keys(shared, k_static)
    assert C.b64u(p2c) == S["key_phone_to_pc"]
    assert C.b64u(c2p) == S["key_pc_to_phone"]


def test_frames():
    keys = {1: d(S["key_phone_to_pc"]), 2: d(S["key_pc_to_phone"])}
    assert len(VECTORS["frames"]) == 5
    for f in VECTORS["frames"]:
        assert C.b64u(C.nonce(f["direction"], f["c"])) == f["nonce"]
        sealed = C.seal(keys[f["direction"]], f["direction"], f["c"], f["plaintext"].encode("utf-8"))
        assert sealed == f["frame"]
        assert C.open_frame(keys[f["direction"]], f["direction"], f["frame"]).decode("utf-8") == f["plaintext"]
        # Plaintexts are compact JSON with raw UTF-8, as the README says.
        assert C.compact_json(json.loads(f["plaintext"])) == f["plaintext"]


def test_channel_follows_the_vector_frames_and_drops_replays():
    p2c, c2p = d(S["key_phone_to_pc"]), d(S["key_pc_to_phone"])
    pc = C.Channel.for_pc(p2c, c2p)
    incoming = [f for f in VECTORS["frames"] if f["direction"] == 1]
    outgoing = [f for f in VECTORS["frames"] if f["direction"] == 2]
    for f in incoming:
        assert pc.open(f["frame"]) == json.loads(f["plaintext"])
    assert pc.open(incoming[0]["frame"]) is None  # replay
    assert pc.last_recv == 3
    for f in outgoing:
        assert pc.seal(json.loads(f["plaintext"])) == f["frame"]


def test_channel_tampered_frame_does_not_advance():
    p2c, c2p = C.random_b64u(32), C.random_b64u(32)
    pc = C.Channel.for_pc(d(p2c), d(c2p))
    phone = C.Channel.for_phone(d(p2c), d(c2p))
    good = phone.seal({"id": 1})
    raw = bytearray(d(good["d"]))
    raw[0] ^= 1
    assert pc.open({"c": good["c"], "d": C.b64u(bytes(raw))}) is None
    assert pc.last_recv == 0
    assert pc.open(good) == {"id": 1}
    for bad in ({"c": True, "d": good["d"]}, {"c": 0, "d": good["d"]}, {"c": 2 ** 53, "d": good["d"]},
                {"c": 5, "d": "!!"}, [], None):
        assert pc.open(bad) is None


@pytest.mark.parametrize("raw", [b"", b"\x04" + b"\x00" * 64, b"\x02" + b"\x01" * 32, b"\x04" * 66])
def test_invalid_points_are_refused(raw):
    with pytest.raises(ValueError):
        C.load_public(raw)


def test_from_b64u_is_strict():
    for bad in ("a+b", "ab=", "a", 5, None):
        with pytest.raises(ValueError):
            C.from_b64u(bad)
