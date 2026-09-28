"""Remote bridge end to end against loopback fakes (tests/remote_fakes.py):
relay connection, pairing, session crypto, every RPC, reconnects and limits.
No test reaches anything but 127.0.0.1."""
import asyncio
import json
import sqlite3
import threading
import time
from contextlib import closing
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from agentic import cards, turn_events
from agentic.command_gates import (APPROVAL_GATES, APPROVAL_RESULTS, QUESTION_GATES,
                                   QUESTION_RESULTS, register_gate, release_gate)
from database import DatabaseManager
from remote import crypto as C
from remote import pairing as pairing_mod
from remote import session as session_mod
from remote.bridge import BridgeError, RemoteBridge
from remote.desktop_channel import CHANNEL
from remote.relay_client import RelayClient
from tests.remote_fakes import FakePhone, FakeRelay


async def until(cond, timeout=5.0, step=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(step)
    raise AssertionError("condition not reached")


class Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.time() + self.offset


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    for name, value in (("backoff_base_s", 0.05), ("backoff_cap_s", 0.2), ("rate_limit_min_s", 0.5),
                        ("refused_delay_s", 0.3)):
        monkeypatch.setattr(RelayClient, name, value)
    cards.reset()
    turn_events.RING.reset()
    relay = await FakeRelay().start()
    db = DatabaseManager(str(tmp_path / "remote.db"))
    stops = []

    async def stop_chat(conv_id):
        stops.append(conv_id)
        return {"status": "ok"}

    clock = Clock()
    bridge = RemoteBridge(db, stop_chat=stop_chat, clock=clock)
    bridge.store.set_relay_url(relay.url)
    await bridge.startup()
    phones = []
    ns = SimpleNamespace(relay=relay, db=db, bridge=bridge, stops=stops, clock=clock, phones=phones,
                         tmp_path=tmp_path)
    yield ns
    for p in phones:
        await p.close()
    await bridge.shutdown()
    await relay.stop()
    cards.set_ledger(None)
    for gid in list(APPROVAL_GATES) + list(QUESTION_GATES):
        release_gate(gid)
    cards.reset()
    turn_events.RING.reset()


async def enable(env):
    await env.bridge.enable()
    assert await env.bridge.client.wait_connected(5)


async def pair_phone(env, name="iPhone", connect=True) -> FakePhone:
    if not env.bridge.store.enabled():
        await enable(env)
    start = await env.bridge.start_pairing()
    phone = FakePhone(env.relay, name)
    env.phones.append(phone)
    task = asyncio.create_task(phone.pair(start["qr_url"]))
    await until(lambda: env.bridge.pending_pairing() is not None)
    pending = env.bridge.pending_pairing()
    assert pending["sas"] == phone.sas and pending["device_name"] == name
    await env.bridge.approve_pairing()
    result = await asyncio.wait_for(task, 5)
    assert "ok" in result, result
    if connect:
        await phone.connect()
        ack = await phone.hello()
        assert ack["type"] == "hello_ack", ack
    return phone


# ── connection ─────────────────────────────────────────────────────────────

async def test_off_means_no_connection(env):
    await asyncio.sleep(0.4)
    assert env.relay.pc_requests == [] and env.bridge.client is None
    assert env.bridge.status()["enabled"] is False
    await enable(env)
    await env.bridge.disable()
    before = len(env.relay.pc_requests)
    await asyncio.sleep(0.4)
    assert len(env.relay.pc_requests) == before and env.bridge.client is None


async def test_pc_socket_has_no_origin_and_carries_the_room_key(env):
    await enable(env)
    headers = env.relay.pc_requests[0]
    assert "Origin" not in headers
    keys = env.bridge.keys
    assert headers["Sec-WebSocket-Protocol"] == f"gamachine.v1, key.{keys.room_key}"
    assert list(env.relay.rooms) == [keys.pair_id]
    assert len(keys.room_key) == 43


async def test_backend_restart_reconnects_when_on(env):
    await enable(env)
    await env.bridge.shutdown()
    again = RemoteBridge(env.db)
    await again.startup()
    try:
        assert await again.client.wait_connected(5)
        assert again.keys.pair_id == env.bridge.keys.pair_id
    finally:
        await again.shutdown()


async def test_rate_limited_connect_backs_off_longer(env):
    env.relay.refuse_pc.append((429, 0.6))
    await env.bridge.enable()
    await until(lambda: env.bridge.status()["last_error"] == "rate_limited")
    assert await env.bridge.client.wait_connected(5)
    gap = env.relay.connect_times[1] - env.relay.connect_times[0]
    assert gap >= 0.55, gap


async def test_reconnect_after_relay_drops_the_pc(env):
    phone = await pair_phone(env)
    await env.relay.kick_pc(env.bridge.keys.pair_id)
    await until(lambda: env.relay.pc_connects == 2 and env.bridge.client.connected)
    assert (await phone.next_control())["type"] == "pc_offline"
    assert (await phone.next_control())["type"] == "pc_online"
    assert (await phone.hello())["type"] == "hello_ack"
    assert (await phone.request("list_chats"))["ok"] is True


async def test_recreated_room_gets_its_token_hashes_again(env):
    phone = await pair_phone(env, connect=False)
    pair_id = env.bridge.keys.pair_id
    await env.relay.wipe(pair_id, 4007)
    await until(lambda: pair_id in env.relay.rooms and env.relay.rooms[pair_id].tokens)
    assert env.relay.rooms[pair_id].tokens == [C.token_hash(phone.token)]
    await phone.connect()
    assert (await phone.hello())["type"] == "hello_ack"


async def test_a_second_pc_on_the_same_key_is_not_fought(env):
    await enable(env)
    keys = env.bridge.keys
    other = RelayClient(env.relay.ws_url, keys.pair_id, keys.room_key, lambda m: asyncio.sleep(0))
    other.start()
    try:
        await until(lambda: env.bridge.client.gave_up)
        assert env.bridge.status()["last_error"] == "replaced_by_another_pc"
        await asyncio.sleep(0.4)
        assert env.relay.pc_connects == 2
    finally:
        await other.stop()


# ── pairing ────────────────────────────────────────────────────────────────

async def test_pairing_stores_only_the_token_hash(env):
    phone = await pair_phone(env)
    assert len(phone.device_id) == 22 and len(phone.token) == 43
    assert phone.vapid_pub == C.b64u(env.bridge.keys.vapid.public_raw)
    assert env.relay.rooms[env.bridge.keys.pair_id].tokens == [C.token_hash(phone.token)]
    with closing(sqlite3.connect(env.db.db_path)) as conn:
        rows = conn.execute("SELECT * FROM remote_devices").fetchall()
        keys_row = conn.execute("SELECT value FROM app_settings WHERE key = 'remote.keys'").fetchone()
    assert len(rows) == 1 and phone.token not in json.dumps(rows)
    assert keys_row[0].startswith("enc:") and env.bridge.keys.room_key not in keys_row[0]
    assert env.bridge.devices()[0]["name"] == "iPhone" and env.bridge.devices()[0]["online"] is True


async def test_qr_link_layout(env):
    await enable(env)
    start = await env.bridge.start_pairing()
    prefix, frag = start["qr_url"].split("#")
    assert prefix == f"{env.relay.url}/p"
    pair_id, pc_pub, secret = frag.split(".")
    assert (len(pair_id), len(pc_pub), len(secret)) == (22, 87, 22)
    assert pair_id == env.bridge.keys.pair_id and C.from_b64u(pc_pub) == env.bridge.keys.static.public_raw


async def test_rejected_pairing(env):
    await enable(env)
    start = await env.bridge.start_pairing()
    phone = FakePhone(env.relay)
    task = asyncio.create_task(phone.pair(start["qr_url"]))
    await until(lambda: env.bridge.pending_pairing() is not None)
    assert await env.bridge.reject_pairing() is True
    assert await task == {"reject": "rejected"}
    assert env.bridge.store.count_devices() == 0


async def test_pair_secret_is_single_use(env):
    phone = await pair_phone(env, connect=False)
    thief = FakePhone(env.relay, "Thief")
    assert await thief.pair(f"{env.relay.url}/p#{phone.pair_id}.{C.b64u(phone.pc_pub)}."
                            f"{env.bridge.pairing.offer.secret_b64u}") == {"reject": "expired"}


async def test_pair_secret_expires_after_five_minutes(env):
    await enable(env)
    start = await env.bridge.start_pairing()
    env.clock.offset = 301
    assert await FakePhone(env.relay).pair(start["qr_url"]) == {"reject": "expired"}


async def test_unanswered_approval_expires(env, monkeypatch):
    monkeypatch.setattr(pairing_mod, "APPROVAL_TTL_S", 0.3)
    await enable(env)
    start = await env.bridge.start_pairing()
    result = await FakePhone(env.relay).pair(start["qr_url"])
    assert result == {"reject": "expired"}
    assert env.bridge.pending_pairing() is None
    with pytest.raises(BridgeError):
        await env.bridge.approve_pairing()


async def test_bad_mac_is_refused_without_using_the_secret(env):
    await enable(env)
    start = await env.bridge.start_pairing()
    assert await FakePhone(env.relay).pair(start["qr_url"], mac_ok=False) == {"reject": "rejected"}
    phone = FakePhone(env.relay)
    task = asyncio.create_task(phone.pair(start["qr_url"]))
    await until(lambda: env.bridge.pending_pairing() is not None)
    await env.bridge.approve_pairing()
    assert "ok" in await task


async def test_pairing_rate_limit_per_pairing_id(env):
    await enable(env)
    start = await env.bridge.start_pairing()
    results = [await FakePhone(env.relay).pair(start["qr_url"], ip=f"203.0.113.{i}", mac_ok=False)
               for i in range(6)]
    assert results[:5] == [{"reject": "rejected"}] * 5
    assert results[5] == {"reject": "rate_limited"}
    # The window is a minute: a minute later the same id may try again.
    env.clock.offset += 61
    assert await FakePhone(env.relay).pair(start["qr_url"], mac_ok=False) == {"reject": "rejected"}


async def test_pairing_rate_limit_per_ip(env):
    await enable(env)
    start = await env.bridge.start_pairing()
    for batch in range(4):
        for _ in range(5):
            r = await FakePhone(env.relay).pair(start["qr_url"], ip="203.0.113.50", mac_ok=False)
            assert r == {"reject": "rejected"}
        env.clock.offset += 61
        start = await env.bridge.start_pairing()
    assert await FakePhone(env.relay).pair(start["qr_url"], ip="203.0.113.50", mac_ok=False) == \
        {"reject": "rate_limited"}
    assert await FakePhone(env.relay).pair(start["qr_url"], ip="203.0.113.51", mac_ok=False) == \
        {"reject": "rejected"}


async def test_pairing_needs_remote_on(env):
    with pytest.raises(BridgeError) as exc:
        await env.bridge.start_pairing()
    assert exc.value.code == "remote_off"


# ── session ────────────────────────────────────────────────────────────────

async def test_unknown_device_and_clock_skew_are_rejected(env):
    phone = await pair_phone(env)
    assert (await phone.hello(device_id=C.random_b64u(16))) == {"type": "hello_reject", "reason": "unknown_device"}
    assert (await phone.hello(t=int(time.time()) - 400)) == {"type": "hello_reject", "reason": "clock"}
    assert (await phone.hello(t=int(time.time()) + 400)) == {"type": "hello_reject", "reason": "clock"}
    assert (await phone.hello())["type"] == "hello_ack"


async def test_hello_must_match_the_socket_token(env):
    first = await pair_phone(env, "iPhone")
    second = await pair_phone(env, "iPad")
    # iPad's socket, iPhone's device id: refused although both are paired.
    second.k_static = first.k_static
    assert (await second.hello(device_id=first.device_id)) == {"type": "hello_reject", "reason": "unknown_device"}


async def test_removed_device_is_rejected(env):
    phone = await pair_phone(env)
    env.bridge.store.remove_device(phone.device_id)
    assert (await phone.hello()) == {"type": "hello_reject", "reason": "unknown_device"}


async def test_remove_device_drops_its_token_at_the_relay(env):
    phone = await pair_phone(env)
    other = await pair_phone(env, "iPad")
    assert await env.bridge.remove_device(phone.device_id) is True
    await until(lambda: phone.close_code is not None)
    assert phone.close_code == 4001
    assert env.relay.rooms[env.bridge.keys.pair_id].tokens == [C.token_hash(other.token)]
    assert [d["name"] for d in env.bridge.devices()] == ["iPad"]
    assert (await other.request("list_chats"))["ok"] is True


async def test_remove_all_devices(env):
    phone = await pair_phone(env)
    assert await env.bridge.remove_all_devices() == 1
    await until(lambda: phone.close_code is not None)
    assert phone.close_code == 4001 and env.relay.rooms[env.bridge.keys.pair_id].tokens == []


async def test_replayed_and_tampered_frames_are_dropped(env):
    phone = await pair_phone(env)
    assert (await phone.request("list_chats"))["ok"] is True
    replay = phone.sent_frames[-1]
    await phone.send_frame(replay)
    await asyncio.sleep(0.2)
    assert phone.pushes.empty()
    # A forged frame with the next counter does not move the PC's counter:
    # the genuine frame with that same counter is still accepted afterwards.
    genuine = phone.channel.seal({"id": 99, "type": "list_chats"})
    raw = bytearray(C.from_b64u(genuine["d"]))
    raw[-1] ^= 1
    await phone.send_frame({"c": genuine["c"], "d": C.b64u(bytes(raw))})
    await asyncio.sleep(0.2)
    slot = {"parts": [], "event": asyncio.Event()}
    phone._pending[99] = slot
    await phone.send_frame(genuine)
    await asyncio.wait_for(slot["event"].wait(), 5)
    assert slot["parts"][0]["ok"] is True
    assert env.bridge.sessions[next(iter(env.bridge.sessions))].channel.last_recv == genuine["c"]


async def test_forget_resets_the_room_and_deletes_keys(env):
    phone = await pair_phone(env)
    pair_id = env.bridge.keys.pair_id
    result = await env.bridge.forget()
    assert result["relay_reset"] is True and result["enabled"] is False
    await until(lambda: phone.close_code is not None)
    assert phone.close_code == 4006 and pair_id not in env.relay.rooms
    assert env.bridge.store.load_keys() is None and env.bridge.store.count_devices() == 0
    await asyncio.sleep(0.3)
    assert pair_id not in env.relay.rooms


# ── RPC ────────────────────────────────────────────────────────────────────

def make_chat(db, title="Arena", provider="codex", model="gpt-5.5"):
    conv = db.create_conversation(1, title)
    db.add_message(conv, "user", "merhaba")
    db.add_message(conv, "assistant", "selam", provider=provider, model=model)
    return conv


async def test_list_chats_and_pending_cards(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    register_gate("gate-1", conv, tool="git", summary="git commit -m x")
    r = await phone.request("list_chats")
    chat = next(c for c in r["result"]["chats"] if c["chat_id"] == str(conv))
    assert chat["title"] == "Arena" and chat["provider"] == "codex" and chat["model"] == "gpt-5.5"
    assert chat["status"] == "awaiting_card" and isinstance(chat["last_activity"], int)
    r = await phone.request("pending_cards")
    card = r["result"]["cards"][0]
    assert card["card_id"] == "gate-1" and card["chat_id"] == str(conv)
    assert card["title"] == "git" and card["detail"] == "git commit -m x"
    assert "choices" not in card


async def test_answer_card_approve_runs_on_the_loop_thread(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    seen = []
    loop_thread = threading.get_ident()
    cards.open_card("card-a", conversation_id=conv, kind="command", tool="git",
                    resolver=lambda result: seen.append((threading.get_ident(), result)))
    r = await phone.request("answer_card", card_id="card-a", decision="approve")
    assert r["ok"] is True and r["result"]["outcome"] == "approved" and r["result"]["by"] == "phone:iPhone"
    assert seen == [(loop_thread, True)]


async def test_answer_card_resolves_a_real_gate(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    event = register_gate("gate-2", conv, tool="bash", summary="rm -rf build")
    r = await phone.request("answer_card", card_id="gate-2", decision="reject")
    assert r["ok"] is True and event.is_set() and APPROVAL_RESULTS["gate-2"] is False


async def test_first_answer_wins_against_the_desktop(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    register_gate("gate-3", conv, tool="git")
    assert cards.answer_card("gate-3", "approve", device="desktop")["status"] == "ok"
    r = await phone.request("answer_card", card_id="gate-3", decision="reject")
    assert r["ok"] is False and r["error"] == "already_answered" and r["by"] == "desktop" and r["at"]
    assert APPROVAL_RESULTS["gate-3"] is True
    register_gate("gate-4", conv, tool="git")
    assert (await phone.request("answer_card", card_id="gate-4", decision="approve"))["ok"] is True
    late = cards.answer_card("gate-4", "reject", device="desktop")
    assert late["status"] == "already_answered" and late["by"] == "phone:iPhone"


async def test_question_card_choice(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    questions = [{"question": "Hangi motor?", "options": [{"label": "Unity"}, {"label": "Godot"}],
                  "multiSelect": False}]
    event = register_gate("q-1", conv, kind="question", tool="AskUserQuestion",
                          params={"questions": questions}, questions=questions,
                          summary="Hangi motor?")
    card = (await phone.request("pending_cards"))["result"]["cards"][0]
    assert card["choices"] == [{"id": "Unity", "label": "Unity"}, {"id": "Godot", "label": "Godot"}]
    bad = await phone.request("answer_card", card_id="q-1", decision="choice", choice="Unreal")
    assert bad["error"] == "bad_choice"
    assert (await phone.request("answer_card", card_id="q-1", decision="approve"))["error"] == "unsupported_on_phone"
    r = await phone.request("answer_card", card_id="q-1", decision="choice", choice="Godot")
    assert r["ok"] is True and event.is_set() and QUESTION_RESULTS["q-1"] == {"Hangi motor?": "Godot"}


async def test_open_chat_then_live_events_then_close(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    turn_events.append(conv, "turn_start", provider="codex", model="m", origin="user")
    turn_events.append(conv, "tool_call", name="bash", summary="ls")
    r = await phone.request("open_chat", chat_id=str(conv))
    res = r["result"]
    assert [m["text"] for m in res["messages"]] == ["merhaba", "selam"]
    assert [e["kind"] for e in res["events"]] == ["turn_start", "tool_call"]
    assert res["events"][1]["tool"] == "bash" and res["events"][1]["chat_id"] == str(conv)
    turn_events.text(conv, "yarım")
    turn_events.flush(conv)
    ev = await phone.next_push(lambda m: m.get("type") == "event" and m.get("kind") == "text")
    assert ev["text"] == "yarım" and ev["seq"] == res["last_seq"] + 1
    turn_events.append(conv, "turn_end", status="done")
    ev = await phone.next_push(lambda m: m.get("type") == "event" and m.get("kind") == "turn_end")
    assert ev["status"] == "done"
    assert (await phone.request("close_chat", chat_id=str(conv)))["result"] == {}
    turn_events.append(conv, "turn_start", provider="codex", model="m", origin="user")
    with pytest.raises(asyncio.TimeoutError):
        await phone.next_push(lambda m: m.get("type") == "event", timeout=0.4)


async def test_open_chat_since_seq_replays_the_ring(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    for i in range(3):
        turn_events.append(conv, "tool_call", name=f"t{i}", summary="")
    r = await phone.request("open_chat", chat_id=conv, since_seq=1)
    assert [e["seq"] for e in r["result"]["events"]] == [2, 3] and r["result"]["gap"] is False
    r = await phone.request("open_chat", chat_id=conv, since_seq=50)
    assert r["result"]["gap"] is True


async def test_large_reply_is_split_under_the_relay_cap(env):
    phone = await pair_phone(env)
    conv = env.db.create_conversation(1, "Uzun")
    texts = [f"{i:03d}" + "ç" * 20_000 for i in range(40)]
    for t in texts:
        env.db.add_message(conv, "assistant", t)
    r = await phone.request("open_chat", chat_id=str(conv), timeout=15)
    assert r["_parts"] >= 2
    assert [m["text"] for m in r["result"]["messages"]] == texts
    assert max(len(f.encode("utf-8")) for f in env.relay.pc_frames) <= 1024 * 1024
    assert "too_large" not in env.relay.errors_sent


def test_split_reply_keeps_every_item_in_order():
    reply = {"id": 7, "ok": True, "result": {"chat_id": "1", "messages": [{"t": "x" * 50}] * 30,
                                              "events": [{"k": i} for i in range(20)]}}
    parts = session_mod.split_reply(reply, budget=600)
    assert len(parts) > 3
    assert all(len(C.compact_json(p).encode()) <= 600 for p in parts)
    assert [p["part"] for p in parts] == list(range(1, len(parts) + 1))
    assert sum((p["result"]["messages"] for p in parts), []) == reply["result"]["messages"]
    assert sum((p["result"]["events"] for p in parts), []) == reply["result"]["events"]
    assert all(p["id"] == 7 and p["result"]["chat_id"] == "1" for p in parts)


async def test_stop_uses_the_desktop_stop(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    r = await phone.request("stop", chat_id=str(conv))
    assert r["ok"] is True and r["result"]["status"] == "ok" and env.stops == [conv]
    assert (await phone.request("stop", chat_id="999999"))["error"] == "unknown_chat"


async def test_send_message_goes_to_the_renderer(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    r = await phone.request("send_message", chat_id=str(conv), text="Şu testi çalıştır 🙂")
    assert r["result"] == {"status": "desktop_not_ready"}
    q = CHANNEL.listen()
    try:
        r = await phone.request("send_message", chat_id=str(conv), text="Şu testi çalıştır 🙂")
        assert r["result"] == {"status": "accepted"}
        frame = q.get_nowait()
        assert frame["type"] == "remote_message" and frame["conversation_id"] == conv
        assert frame["text"] == "Şu testi çalıştır 🙂" and frame["source"] == "phone"
        assert frame["device_id"] == phone.device_id and frame["device_name"] == "iPhone"
    finally:
        CHANNEL.unlisten(q)
    assert (await phone.request("send_message", chat_id=str(conv), text="  "))["error"] == "bad_text"


async def test_send_message_refuses_slash_commands(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    q = CHANNEL.listen()
    try:
        for text in ("/usage", "  /compact", "\n\t/model x", "\u200b/mode plan", "\ufeff/skill", "\u00a0/x"):
            r = await phone.request("send_message", chat_id=str(conv), text=text)
            assert r["ok"] is False and r["error"] == "commands_not_allowed", repr(text)
        assert q.empty(), "a refused command never reaches the renderer"
        for text in ("a /usage", "yol: /tmp/x", "\\/usage", "\uff0fusage"):
            r = await phone.request("send_message", chat_id=str(conv), text=text)
            assert r["result"] == {"status": "accepted"}, repr(text)
        assert q.qsize() == 4
    finally:
        CHANNEL.unlisten(q)


async def test_push_subscribe_stores_the_subscription(env, monkeypatch):
    from remote import webpush
    monkeypatch.setattr(webpush, "ALLOW_LOOPBACK_FOR_TESTS", False)
    phone = await pair_phone(env)
    ua = C.KeyPair.generate()
    sub = {"endpoint": "https://web.push.apple.com/QGx", "expirationTime": None,
           "keys": {"p256dh": C.b64u(ua.public_raw), "auth": C.random_b64u(16)}}
    assert (await phone.request("push_subscribe", subscription=sub))["result"] == {}
    stored = env.bridge.store.get_device(phone.device_id).push_subscription
    assert stored == {"endpoint": sub["endpoint"], "keys": sub["keys"]}
    for bad in ({**sub, "endpoint": "https://192.168.1.2/x"}, {**sub, "endpoint": "http://web.push.apple.com/x"},
                {**sub, "keys": {"p256dh": "AAAA", "auth": sub["keys"]["auth"]}}, "nope"):
        assert (await phone.request("push_subscribe", subscription=bad))["error"] == "bad_subscription"


async def test_bad_requests_get_errors_and_the_session_survives(env):
    phone = await pair_phone(env)
    assert (await phone.request("delete_everything"))["error"] == "unknown_type"
    assert (await phone.request("open_chat", chat_id="abc"))["error"] == "bad_chat_id"
    assert (await phone.request("open_chat", chat_id=True))["error"] == "bad_chat_id"
    assert (await phone.request("open_chat", chat_id="1", since_seq=-1))["error"] in ("bad_since_seq", "unknown_chat")
    assert (await phone.request("answer_card", card_id="", decision="approve"))["error"] == "bad_card_id"
    assert (await phone.request("answer_card", card_id="x", decision="maybe"))["error"] == "bad_decision"
    assert (await phone.request("answer_card", card_id="nope", decision="approve"))["error"] == "not_found"
    await phone.send_frame(phone.seal({"type": "list_chats"}))  # no id
    await phone.send_frame(phone.seal([1, 2, 3]))
    assert (await phone.request("list_chats"))["ok"] is True


async def test_card_and_chat_pushes_reach_the_phone(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    register_gate("gate-p", conv, tool="git", summary="git push")
    opened = await phone.next_push(lambda m: m.get("type") == "card_opened")
    assert opened["card"]["card_id"] == "gate-p" and opened["card"]["chat_id"] == str(conv)
    changed = await phone.next_push(lambda m: m.get("type") == "chat_changed")
    assert changed["chat"]["status"] == "awaiting_card"
    cards.answer_card("gate-p", "approve", device="desktop")
    closed = await phone.next_push(lambda m: m.get("type") == "card_closed")
    assert closed == {"type": "card_closed", "card_id": "gate-p"}


async def test_busy_replies_stay_bounded_behind_a_slow_phone(env, monkeypatch):
    release = asyncio.Event()
    sent = []

    async def slow_send(conn, obj):
        await release.wait()
        sent.append(obj)
        return True

    class PlainChannel:
        def open(self, frame):
            return {"id": frame["c"], "type": "list_chats"}

        def seal(self, obj):
            return obj

    device = session_mod.Device("dev", "phone", b"", "hash", 0, None, None)
    session = session_mod.PhoneSession("conn", device, PlainChannel(), slow_send)
    for _ in range(session.MAX_TASKS):
        assert session.spawn(release.wait())
    monkeypatch.setitem(env.bridge.sessions, "conn", session)
    monkeypatch.setattr(env.bridge, "_touch", lambda device_id: None)
    before = len(asyncio.all_tasks())
    for counter in range(1, 257):
        await env.bridge._on_phone_data("conn", json.dumps({"c": counter, "d": "x"}))
    await asyncio.sleep(0)
    assert len(asyncio.all_tasks()) - before <= 1
    release.set()
    await until(lambda: sent and not session.tasks)
    assert sent == [{"id": 1, "ok": False, "error": "busy"}]
    # Once that reply is out, the next refused request is answered again.
    release.clear()
    for _ in range(session.MAX_TASKS):
        assert session.spawn(release.wait())
    await env.bridge._on_phone_data("conn", json.dumps({"c": 300, "d": "x"}))
    release.set()
    await until(lambda: len(sent) == 2)
    assert sent[1] == {"id": 300, "ok": False, "error": "busy"}
    session.close()


def ring_listeners(conv):
    ring = turn_events.RING._rings.get(conv)
    return len(ring.subs) if ring is not None else 0


async def test_concurrent_opens_of_one_chat_leave_no_listener_after_close(env):
    phone = await pair_phone(env)
    conv = make_chat(env.db)
    turn_events.append(conv, "turn_start", provider="codex", model="m", origin="user")
    replies = await asyncio.gather(*(phone.request("open_chat", chat_id=str(conv)) for _ in range(6)))
    assert all(r["ok"] for r in replies)
    await until(lambda: ring_listeners(conv) == 1)
    assert (await phone.request("close_chat", chat_id=str(conv)))["ok"] is True
    await until(lambda: ring_listeners(conv) == 0)
    turn_events.append(conv, "turn_end", status="done")
    with pytest.raises(asyncio.TimeoutError):
        await phone.next_push(lambda m: m.get("type") == "event", timeout=0.4)


async def test_session_close_drops_every_listener_it_opened(env):
    phone = await pair_phone(env)
    convs = [make_chat(env.db, f"c{i}") for i in range(3)]
    await asyncio.gather(*(phone.request("open_chat", chat_id=str(c)) for c in convs for _ in range(3)))
    await until(lambda: all(ring_listeners(c) == 1 for c in convs))
    for session in list(env.bridge.sessions.values()):
        session.close()
    await until(lambda: all(ring_listeners(c) == 0 for c in convs))


def test_backoff_stays_finite_after_a_long_outage():
    client = RelayClient("ws://127.0.0.1:1", "id", "key", None)
    for attempt in (0, 16, 1024, 10 ** 6):
        assert 0 < client._backoff(attempt) <= client.backoff_cap_s


async def test_reconnect_survives_a_failing_backoff(monkeypatch):
    from remote import relay_client

    def refused(*args, **kwargs):
        raise OSError("refused")

    calls = []

    def broken_backoff(attempt):
        calls.append(attempt)
        raise OverflowError("boom")

    monkeypatch.setattr(relay_client, "connect", refused)
    client = RelayClient("ws://127.0.0.1:1", "id", "key", None)
    client.backoff_cap_s = 0.01
    monkeypatch.setattr(client, "_backoff", broken_backoff)
    client.start()
    try:
        await until(lambda: len(calls) >= 3)
        assert not client._task.done()
    finally:
        await client.stop()
