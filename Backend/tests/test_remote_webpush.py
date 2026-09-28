"""Web push (RFC 8291 aes128gcm + RFC 8292 VAPID) against a loopback push service.

The decrypt and the JWT check below are written from the RFCs with
`cryptography` primitives only, independent of remote/webpush.py.
"""
import asyncio
import json
import time

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from agentic import cards, turn_events
from agentic.command_gates import register_gate
from remote import crypto as C
from remote import webpush
from tests.remote_fakes import FakePushService
from tests.test_remote_bridge import env, make_chat, pair_phone, until  # noqa: F401  (fixture)

d = C.from_b64u


def _hkdf(ikm, salt, info, n):
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)


def rfc8291_decrypt(body: bytes, ua: C.KeyPair, auth: bytes) -> bytes:
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
    assert rs == 4096 and idlen == 65
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    shared = ua.private_key.exchange(ec.ECDH(), peer)
    ikm = _hkdf(shared, auth, b"WebPush: info\x00" + ua.public_raw + as_public, 32)
    cek = _hkdf(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(ikm, salt, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(cek).decrypt(nonce, ciphertext, None)
    record = record.rstrip(b"\x00")
    assert record.endswith(b"\x02"), "last record must end with the 0x02 delimiter"
    return record[:-1]


def verify_vapid(header: str, endpoint_origin: str) -> tuple:
    assert header.startswith("vapid ")
    fields = dict(part.strip().split("=", 1) for part in header[6:].split(","))
    token, k = fields["t"], fields["k"]
    head, claims, sig = token.split(".")
    assert json.loads(d(head)) == {"typ": "JWT", "alg": "ES256"}
    body = json.loads(d(claims))
    raw = d(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), d(k))
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert body["aud"] == endpoint_origin
    assert time.time() < body["exp"] <= time.time() + 24 * 3600
    assert body["sub"].startswith(("mailto:", "https://"))
    return body, k


# ── pure functions ─────────────────────────────────────────────────────────

def test_rfc8291_section5_example():
    as_key = C.KeyPair.from_d(d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"))
    ua = C.KeyPair.from_d(d("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"))
    assert C.b64u(ua.public_raw) == ("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZ"
                                     "GH6SRpkNtoIAiw4")
    body = webpush.encrypt(b"When I grow up, I want to be a watermelon", ua.public_raw,
                           d("BTBZMqHH6r4Tts7J_aSIgg"), as_key=as_key, salt=d("DGv6ra1nlYgDCS1FRnbzlw"))
    assert C.b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6P"
        "Bru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
    assert rfc8291_decrypt(body, ua, d("BTBZMqHH6r4Tts7J_aSIgg")) == b"When I grow up, I want to be a watermelon"


def test_encrypt_roundtrip_with_random_keys():
    ua, auth = C.KeyPair.generate(), C.random_b64u(16)
    text = "Onay bekliyor - Codex (Arena): git commit -m \"düzeltme\" 🙂".encode()
    assert rfc8291_decrypt(webpush.encrypt(text, ua.public_raw, d(auth)), ua, d(auth)) == text


def test_vapid_headers_verify():
    key = C.KeyPair.generate()
    headers = webpush.request_headers("https://web.push.apple.com/QGx", key, "high", time.time())
    body, k = verify_vapid(headers["Authorization"], "https://web.push.apple.com")
    assert k == C.b64u(key.public_raw)
    assert headers["Content-Encoding"] == "aes128gcm" and headers["Urgency"] == "high" and headers["TTL"]
    wrong = C.KeyPair.generate()
    forged = headers["Authorization"].replace(k, C.b64u(wrong.public_raw))
    with pytest.raises(InvalidSignature):
        verify_vapid(forged, "https://web.push.apple.com")


@pytest.mark.parametrize("endpoint", [
    "https://127.0.0.1/x", "https://evil.example/x", "http://web.push.apple.com/x",
    "https://web.push.apple.com:8443/x", "https://user:pw@fcm.googleapis.com/x",
    "https://fcm.googleapis.com.evil.example/x",
])
def test_subscription_endpoint_must_be_a_push_service(endpoint):
    keys = {"p256dh": C.b64u(C.KeyPair.generate().public_raw), "auth": C.random_b64u(16)}
    with pytest.raises(ValueError):
        webpush.validate_subscription({"endpoint": endpoint, "keys": keys})


def test_known_push_services_are_accepted():
    keys = {"p256dh": C.b64u(C.KeyPair.generate().public_raw), "auth": C.random_b64u(16)}
    for endpoint in ("https://web.push.apple.com/QGx", "https://fcm.googleapis.com/fcm/send/x",
                     "https://updates.push.services.mozilla.com/wpush/v2/x",
                     "https://wns2-db5p.notify.windows.com/w/?token=x"):
        assert webpush.validate_subscription({"endpoint": endpoint, "keys": keys})["endpoint"] == endpoint


# ── through the bridge ─────────────────────────────────────────────────────

@pytest.fixture
def push_service(monkeypatch):
    monkeypatch.setattr(webpush, "ALLOW_LOOPBACK_FOR_TESTS", True)
    service = FakePushService()
    yield service
    service.close()


async def subscribe(phone, service, name="sub1"):
    ua = C.KeyPair.generate()
    auth = C.random_b64u(16)
    sub = {"endpoint": service.endpoint(name), "keys": {"p256dh": C.b64u(ua.public_raw), "auth": auth}}
    assert (await phone.request("push_subscribe", subscription=sub))["ok"] is True
    return ua, d(auth)


def decode(service, i, ua, auth):
    return json.loads(rfc8291_decrypt(service.requests[i]["body"], ua, auth))


async def test_card_push_is_readable_only_with_the_subscription_keys(env, push_service):
    phone = await pair_phone(env)
    ua, auth = await subscribe(phone, push_service)
    conv = make_chat(env.db, "Arena", provider="codex")
    register_gate("gate-w", conv, tool="git", summary='git commit -m "düzeltme"')
    await until(lambda: len(push_service.requests) == 1)
    req = push_service.requests[0]
    payload = decode(push_service, 0, ua, auth)
    assert payload == {"title": "Onay bekliyor - Codex (Arena)", "body": 'git: git commit -m "düzeltme"',
                       "url": f"/p#chat={conv}", "tag": f"chat-{conv}"}
    _, k = verify_vapid(req["headers"]["Authorization"], f"http://127.0.0.1:{push_service.port}")
    assert k == phone.vapid_pub
    assert req["headers"]["Content-Encoding"] == "aes128gcm" and req["headers"]["Urgency"] == "high"
    with pytest.raises(Exception):
        rfc8291_decrypt(req["body"], C.KeyPair.generate(), auth)


async def test_body_is_capped(env, push_service):
    phone = await pair_phone(env)
    ua, auth = await subscribe(phone, push_service)
    conv = make_chat(env.db)
    register_gate("gate-long", conv, tool="bash", summary="x" * 5000)
    await until(lambda: len(push_service.requests) == 1)
    assert len(decode(push_service, 0, ua, auth)["body"]) <= webpush.BODY_MAX


async def test_turn_finished_failed_and_woken_pushes(env, push_service, monkeypatch):
    monkeypatch.setattr(env.bridge.push, "coalesce_s", 0.0)
    phone = await pair_phone(env)
    ua, auth = await subscribe(phone, push_service)
    conv = make_chat(env.db, "Arena", provider="claude")
    turn_events.append(conv, "turn_start", provider="claude", model="m", origin="user")
    turn_events.text(conv, "Testler geçti.")
    turn_events.append(conv, "turn_end", status="done")
    await until(lambda: len(push_service.requests) == 1)
    assert decode(push_service, 0, ua, auth)["title"] == "İş bitti - Claude (Arena)"
    assert decode(push_service, 0, ua, auth)["body"] == "Testler geçti."
    turn_events.append(conv, "turn_start", provider="claude", model="m", origin="wake")
    await until(lambda: len(push_service.requests) == 2)
    assert decode(push_service, 1, ua, auth)["title"] == "Not geldi - Claude (Arena)"
    turn_events.append(conv, "turn_end", status="error")
    await until(lambda: len(push_service.requests) == 3)
    assert decode(push_service, 2, ua, auth)["title"] == "Hata - Claude (Arena)"
    turn_events.append(conv, "turn_start", provider="claude", model="m", origin="user")
    turn_events.append(conv, "turn_end", status="stopped")
    await asyncio.sleep(0.3)
    assert len(push_service.requests) == 3


async def test_pushes_are_coalesced_per_chat(env, push_service, monkeypatch):
    window = 1.5
    monkeypatch.setattr(env.bridge.push, "coalesce_s", window)
    phone = await pair_phone(env)
    ua, auth = await subscribe(phone, push_service)
    conv = make_chat(env.db, "Arena")
    other = make_chat(env.db, "Lab")
    started = time.monotonic()
    for i in range(3):
        register_gate(f"c-{i}", conv, tool=f"t{i}", summary="s")
    register_gate("o-1", other, tool="o", summary="s")
    await until(lambda: len(push_service.requests) == 2)
    titles = sorted(decode(push_service, i, ua, auth)["title"] for i in range(2))
    assert titles[0].endswith("(Arena)") and titles[1].endswith("(Lab)")
    await until(lambda: len(push_service.requests) == 3, timeout=window + 3)
    # One more for Arena, at the end of its window, carrying the last card.
    assert push_service.requests[2]["at"] - started >= window - 0.05
    assert decode(push_service, 2, ua, auth)["body"] == "t2: s"
    await asyncio.sleep(window)
    assert len(push_service.requests) == 3


async def test_gone_subscription_is_deleted(env, push_service):
    phone = await pair_phone(env)
    await subscribe(phone, push_service)
    push_service.status = 410
    conv = make_chat(env.db)
    register_gate("gate-410", conv, tool="git", summary="x")
    await until(lambda: env.bridge.store.push_targets() == [])
    assert env.bridge.store.get_device(phone.device_id).push_subscription is None


async def test_no_push_while_remote_control_is_off(env, push_service):
    phone = await pair_phone(env)
    await subscribe(phone, push_service)
    await env.bridge.disable()
    conv = make_chat(env.db)
    register_gate("gate-off", conv, tool="git", summary="x")
    turn_events.append(conv, "turn_end", status="done")
    await asyncio.sleep(0.4)
    assert push_service.requests == []
