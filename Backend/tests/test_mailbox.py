"""Chat mailbox: the AI of chat A leaves a note for chat B.

Contract under test: a note is sent only from a chat of the local user with
a turn in flight, never from or to a side chat, never to itself or to another
user's chat; step mode raises a card owned by A whose answer queues or refuses
it, auto mode queues at once; loops are bounded by depth and by a per-pair
rate; B's wake turn runs on the note read from the DB (never on the client's
text) and a delivered note is not delivered again; a busy B waits; a note
still `queued` from a run that ended is marked `undelivered` at the next
startup and does NOT wake anyone by itself (owner decision, 28 Sep 2026 - see
`test_startup_sweep_marks_stale_queued_mail_undelivered`); deleting a chat
takes its notes and denies its cards.
"""
import asyncio
import sqlite3
import types
from collections import defaultdict
from contextlib import closing

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import approval_mode, approval_policy, mailbox, wake_queue
from agentic.approval_policy import ambient_turn
from agentic.command_gates import GATE_OWNERS
from database import DatabaseManager
from rag.memory_manager import memory_manager
from schemas import ChatRequest

H = {"X-Session-Token": ""}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    monkeypatch.setattr(cr, "WAKE_ALL_POLL_S", 0.02)
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_manager, "base_dir", mem_dir)
    wake_queue.reset_all()
    approval_policy._TURNS_BY_CONVERSATION.clear()
    db = DatabaseManager(str(tmp_path / "mail.db"))
    db.save_ai_config(1, "subscription", "gpt-5.4", "")

    async def _no_close(_cid):
        return None

    from providers import agy_session, claude_sdk_session, codex_session, oneshot_cli
    monkeypatch.setattr(claude_sdk_session, "close_session", _no_close)
    monkeypatch.setattr(codex_session, "close_session", _no_close)
    monkeypatch.setattr(agy_session, "close_session", _no_close)
    monkeypatch.setattr(oneshot_cli, "close_conversation_sessions", _no_close)
    app = FastAPI()
    router = cr.create_conversation_router(db, {})
    app.include_router(router)
    with TestClient(app) as client:
        yield types.SimpleNamespace(db=db, client=client, router=router)
    wake_queue.reset_all()
    approval_policy._TURNS_BY_CONVERSATION.clear()


def _chat(db, title, user_id=1):
    return db.create_conversation(user_id, title)


def _rows(db, sql, args=()):
    with sqlite3.connect(db.db_path) as conn:
        return conn.execute(sql, args).fetchall()


def _send(client, a, b, body="merhaba", in_flight=True, depth=0):
    """Send from a turn of `a` woken by mail of `depth` (0: not woken by mail)."""
    payload = {"conversation_id": a, "to": b, "body": body}
    if in_flight:
        with ambient_turn(".", "step", a, depth):
            return client.post("/mailbox/send", json=payload, headers=H)
    return client.post("/mailbox/send", json=payload, headers=H)


def _pending(client):
    return client.get("/mcp-pending", headers=H).json()["pending"]


@pytest.fixture
def auto(monkeypatch):
    # conftest resets the mode after every test.
    approval_mode.set_mode("auto", source="test")


# ── who may send, to whom ────────────────────────────────────────────────────

def test_send_is_refused_from_a_side_row(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    side = env.db.create_side_chat(a, 1)
    r = _send(env.client, side, b)
    assert r.status_code == 403
    assert _rows(env.db, "SELECT * FROM mailbox") == []
    assert wake_queue.pending(b) == 0


def test_send_is_refused_to_a_side_row(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    side = env.db.create_side_chat(b, 1)
    assert _send(env.client, a, side).status_code == 403
    assert _rows(env.db, "SELECT * FROM mailbox") == []


def test_send_is_refused_to_self(env, auto):
    a = _chat(env.db, "A")
    assert _send(env.client, a, a).status_code == 400
    assert _rows(env.db, "SELECT * FROM mailbox") == []


def test_send_is_refused_to_another_users_chat(env, auto):
    a = _chat(env.db, "A")
    foreign = _chat(env.db, "someone else", user_id=2)
    assert _send(env.client, a, foreign).status_code == 404
    assert _rows(env.db, "SELECT * FROM mailbox") == []
    assert wake_queue.pending(foreign) == 0


def test_send_from_another_users_chat_is_404(env, auto):
    foreign = _chat(env.db, "someone else", user_id=2)
    b = _chat(env.db, "B")
    assert _send(env.client, foreign, b).status_code == 404
    assert _rows(env.db, "SELECT * FROM mailbox") == []


def test_send_is_refused_from_a_chat_with_no_turn_in_flight(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, in_flight=False).status_code == 403
    assert _rows(env.db, "SELECT * FROM mailbox") == []


@pytest.mark.parametrize("body", ["", "   ", "x" * (mailbox.MAX_BODY_CHARS + 1)])
def test_send_refuses_an_empty_or_oversized_body(env, auto, body):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, body=body).status_code == 400


# ── step and auto mode ───────────────────────────────────────────────────────

def test_step_mode_raises_a_mail_card_owned_by_the_sender(env):
    a, b = _chat(env.db, "Yazan"), _chat(env.db, "Okuyan")
    r = _send(env.client, a, b, body="build bitti")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "pending"
    gate = data["gate_id"]
    try:
        card = _pending(env.client)[gate]
        assert card["conversation_id"] == a
        assert card["tool"] == "send_chat_message" and card["kind"] == "mail"
        assert card["params"] == {"from_id": a, "from_title": "Yazan", "to_id": b,
                                  "to_title": "Okuyan", "body": "build bitti"}
        assert GATE_OWNERS[gate] == a
        assert _rows(env.db, "SELECT status, gate_id FROM mailbox") == [("pending_approval", gate)]
        assert wake_queue.pending(b) == 0
    finally:
        GATE_OWNERS.pop(gate, None)


def test_step_mode_deny_marks_the_note_rejected_and_wakes_nobody(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    data = _send(env.client, a, b).json()
    r = env.client.post(f"/mcp-approval-respond/{data['gate_id']}", json={"approved": False}, headers=H)
    assert r.json()["status"] == "ok"
    assert _rows(env.db, "SELECT status FROM mailbox") == [("rejected",)]
    assert wake_queue.pending(b) == 0
    assert data["gate_id"] not in _pending(env.client)
    status = env.client.get(f"/mailbox/status/{data['mail_id']}",
                            params={"conversation_id": a}, headers=H).json()
    assert status["status"] == "rejected"


def test_step_mode_approve_queues_the_note_and_wakes_the_recipient(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    data = _send(env.client, a, b).json()
    env.client.post(f"/mcp-approval-respond/{data['gate_id']}", json={"approved": True}, headers=H)
    assert _rows(env.db, "SELECT status FROM mailbox") == [("queued",)]
    assert wake_queue.pending(b) == 1
    assert mailbox.is_mail_notice(wake_queue.drain(b)[0])


def test_the_card_timing_out_refuses_the_note(env, monkeypatch):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    data = _send(env.client, a, b).json()
    # The sending tool gave up waiting: its cancel denies the card.
    r = env.client.post(f"/mailbox/cancel/{data['mail_id']}", json={"conversation_id": a}, headers=H)
    assert r.json()["status"] == "rejected"
    assert data["gate_id"] not in _pending(env.client)
    late = env.client.post(f"/mcp-approval-respond/{data['gate_id']}", json={"approved": True}, headers=H)
    assert late.json()["status"] == "gate_expired"
    assert _rows(env.db, "SELECT status FROM mailbox") == [("rejected",)]
    assert wake_queue.pending(b) == 0


def test_a_card_nobody_answers_is_swept_and_its_note_refused(env, monkeypatch):
    import time as real_time
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    data = _send(env.client, a, b).json()
    # The next poll happens after the card's TTL (nobody left waiting on it).
    monkeypatch.setattr(cr, "time", lambda: real_time.time() + 200)
    assert data["gate_id"] not in _pending(env.client)
    assert _rows(env.db, "SELECT status FROM mailbox") == [("rejected",)]
    assert wake_queue.pending(b) == 0


def test_switching_to_auto_approves_a_waiting_mail_card(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b)
    approve_all = next(c for c in env.router.routes
                       if getattr(c, "path", "") == "/approval-mode" and "POST" in c.methods).endpoint
    # `_approve_all_pending` runs inside the mode route, which needs the UI
    # secret; reach it the way that route does.
    from agentic import approval_mode as am
    am.set_ui_secret("s")
    try:
        asyncio.run(approve_all(body={"mode": "auto"}, x_session_token="", x_ui_secret="s",
                                x_maintenance=""))
    finally:
        am._reset_for_tests()
    assert _rows(env.db, "SELECT status FROM mailbox") == [("queued",)]
    assert wake_queue.pending(b) == 1


def test_stop_in_the_sender_denies_its_mail_card(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    data = _send(env.client, a, b).json()
    env.client.post(f"/chat-stop/{a}", headers=H)
    assert _rows(env.db, "SELECT status FROM mailbox") == [("rejected",)]
    assert data["gate_id"] not in _pending(env.client)


def test_auto_mode_queues_at_once_without_a_card(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    r = _send(env.client, a, b)
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    assert _pending(env.client) == {}
    assert _rows(env.db, "SELECT from_conv, to_conv, status, gate_id, depth FROM mailbox") == [
        (a, b, "queued", None, 1)]
    assert wake_queue.pending(b) == 1


def test_balanced_mode_queues_a_note_without_a_card(env):
    """Owner decision (Burak, 27 Sep 2026): a note between chats is not critical."""
    approval_mode.set_mode("balanced", source="test")
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    r = _send(env.client, a, b)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert _pending(env.client) == {}
    assert wake_queue.pending(b) == 1


def test_balanced_mode_still_refuses_a_side_sender(env):
    approval_mode.set_mode("balanced", source="test")
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    side = env.db.create_side_chat(a, 1)
    assert _send(env.client, side, b).status_code == 403
    assert _rows(env.db, "SELECT COUNT(*) FROM mailbox") == [(0,)]


def test_switching_to_balanced_approves_a_waiting_mail_card(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b)
    route = next(c for c in env.router.routes
                 if getattr(c, "path", "") == "/approval-mode" and "POST" in c.methods).endpoint
    approval_mode.set_ui_secret("s")
    out = asyncio.run(route(body={"mode": "balanced"}, x_session_token="", x_ui_secret="s",
                            x_maintenance=""))
    assert out["approved_pending"] == 1
    assert _rows(env.db, "SELECT status FROM mailbox") == [("queued",)]
    assert wake_queue.pending(b) == 1


# ── loop guards ──────────────────────────────────────────────────────────────

def test_a_reply_to_a_reply_is_depth_2_and_a_third_hop_is_refused(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, depth=1).status_code == 200
    assert _rows(env.db, "SELECT depth FROM mailbox") == [(2,)]
    assert _send(env.client, a, b, depth=2).status_code == 409
    assert len(_rows(env.db, "SELECT * FROM mailbox")) == 1


def test_pair_rate_limit(env, auto):
    a, b, c = _chat(env.db, "A"), _chat(env.db, "B"), _chat(env.db, "C")
    for _ in range(mailbox.PAIR_LIMIT):
        assert _send(env.client, a, b).status_code == 200
    assert _send(env.client, a, b).status_code == 429
    # The limit is per pair: A may still write to C.
    assert _send(env.client, a, c).status_code == 200


def test_turn_depth_is_the_highest_among_the_chats_running_turns():
    approval_policy._TURNS_BY_CONVERSATION.clear()
    try:
        assert mailbox.turn_depth(7) == 0
        with ambient_turn(".", "auto", 7, 2):
            assert mailbox.turn_depth(7) == 2
            with ambient_turn(".", "auto", 7, 0):
                assert mailbox.turn_depth(7) == 2
                with ambient_turn(".", "auto", 8, 1):
                    assert mailbox.turn_depth(8) == 1
                    assert mailbox.turn_depth(7) == 2
                assert mailbox.turn_depth(8) == 0
            assert mailbox.turn_depth(7) == 2
        assert mailbox.turn_depth(7) == 0
        # The depth-2 turn ending first leaves the parallel user turn at 0.
        mail_turn, user_turn = ambient_turn(".", "auto", 7, 2), ambient_turn(".", "auto", 7, 0)
        mail_turn.__enter__()
        user_turn.__enter__()
        mail_turn.__exit__(None, None, None)
        assert mailbox.turn_depth(7) == 0
        assert approval_policy.conversation_turn_in_flight(7)
        user_turn.__exit__(None, None, None)
        assert not approval_policy.conversation_turn_in_flight(7)
    finally:
        approval_policy._TURNS_BY_CONVERSATION.clear()


def test_a_turn_that_raises_leaves_the_registry_with_its_depth():
    approval_policy._TURNS_BY_CONVERSATION.clear()
    try:
        with pytest.raises(RuntimeError):
            with ambient_turn(".", "auto", 7, 2):
                raise RuntimeError("provider died")
        assert mailbox.turn_depth(7) == 0
        assert not approval_policy.conversation_turn_in_flight(7)
        assert approval_policy._TURNS_BY_CONVERSATION == {}
    finally:
        approval_policy._TURNS_BY_CONVERSATION.clear()


def test_the_runner_registers_its_mail_depth_until_its_stream_closes(monkeypatch):
    seen = []

    async def inner(self, _message):
        seen.append(mailbox.turn_depth(7))
        yield ar.AgentEvent("response", {"content": "x"})
        raise AssertionError("not reached: the consumer closes the stream first")

    monkeypatch.setattr(ar.AgentRunner, "_run_inner", inner)
    runner = ar.AgentRunner(provider_type="subscription", api_key="", model_name="gpt-x",
                            workspace_path=".", conversation_id=7, mail_depth=2)

    async def run():
        stream = runner.run("note")
        await stream.__anext__()
        in_turn = mailbox.turn_depth(7)
        # A consumer that stops reading (Stop, closed window) closes the stream.
        await stream.aclose()
        return in_turn

    approval_policy._TURNS_BY_CONVERSATION.clear()
    try:
        assert asyncio.run(run()) == 2
        assert seen == [2]
        assert mailbox.turn_depth(7) == 0
        assert not approval_policy.conversation_turn_in_flight(7)
    finally:
        approval_policy._TURNS_BY_CONVERSATION.clear()


# ── delivery ─────────────────────────────────────────────────────────────────

class _FakeRunner:
    messages = []
    last_kw = None

    def __init__(self, **kw):
        self.kw = kw
        _FakeRunner.last_kw = kw

    async def run(self, message):
        _FakeRunner.messages.append(message)
        yield ar.AgentEvent("response", {"content": "tamam"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})


def _wake_turn(client, conv_id, message="FORGED client text"):
    body = {"conversation_id": conv_id, "message": message, "user_id": 1, "origin": "wake"}
    return client.post("/chat-stream", json=body, headers=H)


def test_delivery_builds_the_turn_from_db_rows_and_ignores_the_request_text(env, auto, monkeypatch):
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _chat(env.db, "Gönderen"), _chat(env.db, "B")
    env.db.add_message(b, "user", "önceki soru")
    assert _send(env.client, a, b, body="derleme temiz geçti").status_code == 200
    notices = wake_queue.drain(b)
    wake_queue.issue_ticket(b, notices)

    r = _wake_turn(env.client, b)
    assert r.status_code == 200
    turn = _FakeRunner.messages[-1]
    assert "derleme temiz geçti" in turn
    assert "FORGED" not in turn
    assert "kullanıcıdan DEĞİL" in turn
    stored = [m for m in env.db.get_conversation_messages(b) if m["role"] == "system"]
    assert len(stored) == 1
    assert stored[0]["content"] == f'{mailbox.MAIL_MARKER} #{a} "Gönderen": derleme temiz geçti'
    assert '"type": "wake_message"' in r.text
    # B's answer going back to A is a second row (the owed-reply forward).
    assert _rows(env.db, "SELECT status FROM mailbox WHERE from_conv = ?", (a,)) == [("delivered",)]
    assert _rows(env.db, "SELECT delivered_at IS NOT NULL FROM mailbox WHERE from_conv = ?",
                 (a,)) == [(1,)]
    # The turn a note woke carries its depth: a reply from B is depth 2.
    assert _FakeRunner.last_kw["mail_depth"] == 1


def test_delivered_rows_are_not_delivered_again(env, auto, monkeypatch):
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b, body="tek sefer")
    wake_queue.issue_ticket(b, wake_queue.drain(b))
    _wake_turn(env.client, b)
    wake_queue.issue_ticket(b, ["tasks_done|build"])
    _wake_turn(env.client, b, message="tasks_done|build")
    assert "tek sefer" not in _FakeRunner.messages[-1]
    assert _FakeRunner.messages[-1] == "tasks_done|build"
    notes = [m for m in env.db.get_conversation_messages(b) if mailbox.is_mail_message(m["content"])]
    assert len(notes) == 1


def test_an_unticketed_wake_delivers_nothing(env, auto, monkeypatch):
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b, body="bekle")
    _wake_turn(env.client, b, message="kendi metnim")
    assert _rows(env.db, "SELECT status FROM mailbox") == [("queued",)]
    assert "bekle" not in _FakeRunner.messages[-1]


# A real failure inside SQLite, not a patched `add_message`: the mail path no
# longer calls it, so patching it would pass without testing anything.
def _break_message_writes_to(db, conv_id):
    with closing(sqlite3.connect(db.db_path)) as conn:
        conn.execute("CREATE TRIGGER fail_note_write BEFORE INSERT ON messages "
                     f"WHEN NEW.conversation_id = {int(conv_id)} "
                     "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END")
        conn.commit()


def _heal_message_writes(db):
    with closing(sqlite3.connect(db.db_path)) as conn:
        conn.execute("DROP TRIGGER fail_note_write")
        conn.commit()


def test_a_failed_note_write_rolls_the_claim_back(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    env.db.add_mail(a, b, "geri alınsın", mailbox.STATUS_QUEUED, None, 1)
    _break_message_writes_to(env.db, b)
    with pytest.raises(sqlite3.DatabaseError):
        env.db.claim_queued_mail(b, note_of=mailbox.stored_text)
    assert _rows(env.db, "SELECT status, delivered_at FROM mailbox") == [("queued", None)]
    assert env.db.get_conversation_messages(b) == []

    _heal_message_writes(env.db)
    rows = env.db.claim_queued_mail(b, note_of=mailbox.stored_text)
    assert [r["body"] for r in rows] == ["geri alınsın"]
    assert [(m["role"], m["content"]) for m in env.db.get_conversation_messages(b)] == [
        ("system", mailbox.stored_text(rows))]


def test_a_wake_whose_note_write_fails_leaves_the_note_for_the_next_wake(env, auto, monkeypatch):
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, body="kaybolmasın").status_code == 200
    wake_queue.issue_ticket(b, wake_queue.drain(b))
    _break_message_writes_to(env.db, b)
    # The request may fail; only what it leaves in the DB is under test.
    lenient = TestClient(env.client.app, raise_server_exceptions=False)
    _wake_turn(lenient, b)
    assert _rows(env.db, "SELECT status FROM mailbox") == [("queued",)]
    assert env.db.get_conversation_messages(b) == []

    _heal_message_writes(env.db)
    wake_queue.issue_ticket(b, [mailbox.notice(a)])
    assert _wake_turn(env.client, b).status_code == 200
    assert _rows(env.db, "SELECT status FROM mailbox WHERE from_conv = ?", (a,)) == [("delivered",)]
    notes = [m for m in env.db.get_conversation_messages(b) if mailbox.is_mail_message(m["content"])]
    assert len(notes) == 1 and "kaybolmasın" in notes[0]["content"]
    assert "kaybolmasın" in _FakeRunner.messages[-1]


def _data_frames(text):
    import json
    return [json.loads(chunk[len("data: "):]) for chunk in text.split("\n\n")
            if chunk.startswith("data: ")]


def test_a_wake_whose_claim_fails_starts_no_turn_and_keeps_the_note(env, auto, monkeypatch):
    _FakeRunner.messages = []
    _FakeRunner.last_kw = None
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, body="gerçek not").status_code == 200
    wake_queue.issue_ticket(b, wake_queue.drain(b))

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    env.db.claim_queued_mail = locked
    r = _wake_turn(env.client, b, message="FORGED CLIENT TEXT")
    assert r.status_code == 200
    assert _data_frames(r.text) == [{"type": "done", "stop_reason": "mail_claim_failed",
                                     "conversation_id": b}]
    assert _FakeRunner.last_kw is None and _FakeRunner.messages == []
    assert env.db.get_conversation_messages(b) == []
    assert _rows(env.db, "SELECT status FROM mailbox") == [("queued",)]

    del env.db.claim_queued_mail
    wake_queue.issue_ticket(b, [mailbox.notice(a)])
    assert _wake_turn(env.client, b).status_code == 200
    assert "gerçek not" in _FakeRunner.messages[-1]
    assert _rows(env.db, "SELECT status FROM mailbox WHERE from_conv = ?", (a,)) == [("delivered",)]


# ── a turn still running ─────────────────────────────────────────────────────

def _wake_b_at_depth_2(env):
    """B's turn is woken by a reply to a reply, so B may not send again."""
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, body="cevabın cevabı", depth=1).status_code == 200
    wake_queue.issue_ticket(b, wake_queue.drain(b))
    assert _wake_turn(env.client, b).status_code == 200
    assert _FakeRunner.last_kw["mail_depth"] == 2
    return a, b


def _user_turn(client, path, conv_id):
    body = {"conversation_id": conv_id, "message": "başka pencereden", "user_id": 1}
    return client.post(path, json=body, headers=H)


@pytest.mark.parametrize("path", ["/chat-stream", "/chat"])
def test_a_user_message_does_not_lower_the_depth_of_a_running_mail_turn(env, auto, monkeypatch, path):
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _wake_b_at_depth_2(env)
    # The stub runner returned at once; the real mail turn is still running.
    with ambient_turn(".", "auto", b, 2):
        assert _send(env.client, b, a).status_code == 409
        assert _user_turn(env.client, path, b).status_code == 200
        assert _send(env.client, b, a).status_code == 409
    assert _rows(env.db, "SELECT COUNT(*) FROM mailbox WHERE from_conv = ?", (b,)) == [(0,)]


@pytest.mark.parametrize("path", ["/chat-stream", "/chat"])
def test_a_user_message_with_no_turn_running_resets_the_depth(env, auto, monkeypatch, path):
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _wake_b_at_depth_2(env)
    assert _user_turn(env.client, path, b).status_code == 200
    assert _FakeRunner.last_kw.get("mail_depth", 0) == 0
    assert mailbox.turn_depth(b) == 0
    assert _send(env.client, b, a).status_code == 200
    assert _rows(env.db, "SELECT depth FROM mailbox WHERE from_conv = ?", (b,)) == [(1,)]


def test_a_user_request_before_a_mail_turn_starts_does_not_lower_its_depth(env, auto, monkeypatch):
    """The mail route returns before its stream starts; a second window's
    message in that gap used to reset the chat's depth to 0 (Codex mailverify,
    27 Sep 2026). The real runner registers the turn, only the provider is
    stubbed."""
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    env.db.add_mail(a, b, "cevabın cevabı", mailbox.STATUS_QUEUED, None, 2)
    wake_queue.issue_ticket(b, [mailbox.notice(a)])
    replies = []
    send = _route(env.router, "/mailbox/send").endpoint

    async def inner(self, message):
        if "cevabın cevabı" in message:
            try:
                await send(body={"conversation_id": b, "to": a, "body": "üçüncü adım"},
                           x_session_token="")
                replies.append(200)
            except HTTPException as exc:
                replies.append(exc.status_code)
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})

    monkeypatch.setattr(ar.AgentRunner, "_run_inner", inner)
    chat_stream = _route(env.router, "/chat-stream").endpoint

    async def run():
        mail_turn = await chat_stream(
            ChatRequest(conversation_id=b, message="x", user_id=1, origin="wake"), "")
        user_turn = await chat_stream(
            ChatRequest(conversation_id=b, message="başka pencereden", user_id=1), "")
        async for _ in user_turn.body_iterator:
            pass
        async for _ in mail_turn.body_iterator:
            pass

    asyncio.run(run())
    assert replies == [409]
    assert _rows(env.db, "SELECT COUNT(*) FROM mailbox WHERE from_conv = ?", (b,)) == [(0,)]


def test_handoff_context_keeps_mail_rows_but_not_wake_rows():
    rows = [
        {"role": "user", "content": "gerçek istek"},
        {"role": "system", "content": "tasks_done|build"},
        {"role": "system", "content": f'{mailbox.MAIL_MARKER} #3 "A": şema değişti'},
        {"role": "user", "content": "son (hariç)"},
    ]
    text = cr._build_handoff_context("", rows)
    assert "şema değişti" in text
    assert "not the user" in text
    assert "tasks_done" not in text


def test_handoff_context_drops_an_undelivered_note_like_any_other_system_row():
    """The grey note must not act as a delivered note for the model (owner
    decision, 28 Sep 2026): it uses a different marker than a real note, so it
    fails `is_mail_message` and the handoff builder drops it exactly like a
    wake row - the model is never handed something to reply to through it."""
    undelivered = mailbox.format_undelivered_recipient_note(
        [{"from_conv": 3, "from_title": "A", "body": "şema değişti"}])
    assert mailbox.is_undelivered_message(undelivered)
    assert not mailbox.is_mail_message(undelivered)
    rows = [
        {"role": "user", "content": "gerçek istek"},
        {"role": "system", "content": undelivered},
        {"role": "system", "content": f'{mailbox.MAIL_MARKER} #3 "A": şema değişti 2'},
        {"role": "user", "content": "son (hariç)"},
    ]
    text = cr._build_handoff_context("", rows)
    assert "şema değişti 2" in text          # a real note is still kept
    assert "teslim edilmedi" not in text     # the undelivered one is not


# ── waking ───────────────────────────────────────────────────────────────────

def _route(router, path):
    return next(r for r in router.routes if getattr(r, "path", "") == path)


async def _next_frame(resp, timeout):
    it = resp.body_iterator
    while True:
        chunk = await asyncio.wait_for(it.__anext__(), timeout=timeout)
        chunk = chunk if isinstance(chunk, str) else chunk.decode("utf-8")
        if chunk.startswith("data: "):
            return chunk


def test_a_busy_recipient_waits_until_its_turn_ends(env, auto):
    """No Claude session is involved: the block comes from the turn registry
    every provider's AgentRunner.run writes to."""
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b)
    route = _route(env.router, "/wake-stream-all")

    async def run():
        resp = await route.endpoint(x_session_token="")
        with ambient_turn(".", "auto", b):
            # Not wait_for: cancelling the pending read would close the stream.
            reader = asyncio.ensure_future(_next_frame(resp, 5.0))
            done, _ = await asyncio.wait({reader}, timeout=0.4)
            assert not done, "woken while its turn was still running"
            assert wake_queue.pending(b) == 1
        frame = await asyncio.wait_for(reader, 3.0)
        await resp.body_iterator.aclose()
        return frame

    frame = asyncio.run(run())
    assert f'"conversation_id": {b}' in frame
    assert wake_queue.ticket_outstanding(b)


def test_the_all_chats_stream_skips_side_rows_and_other_users(env):
    a = _chat(env.db, "A")
    side = env.db.create_side_chat(a, 1)
    foreign = _chat(env.db, "x", user_id=2)
    wake_queue.enqueue(side, "tasks_done|x")
    wake_queue.enqueue(foreign, "tasks_done|y")
    route = _route(env.router, "/wake-stream-all")

    async def run():
        resp = await route.endpoint(x_session_token="")
        try:
            with pytest.raises(asyncio.TimeoutError):
                await _next_frame(resp, 0.3)
        finally:
            await resp.body_iterator.aclose()

    asyncio.run(run())
    assert wake_queue.pending(side) == 1 and wake_queue.pending(foreign) == 1


def test_queued_notes_are_marked_undelivered_after_a_restart_and_wake_nobody(env, auto, tmp_path):
    """The bug this replaces (owner's live test, 28 Sep ~01:00): a restart used
    to re-arm B's wake for a note still `queued` from the dead process, so B
    woke on its own and answered a note nobody was there to have sent it a
    fresh copy of. Option A: the note is marked `undelivered` instead and
    nothing wakes."""
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b, body="restart öncesi")
    # A note still waiting on its card cannot be answered after a restart.
    env.db.add_mail(a, b, "kartta kaldı", "pending_approval", "old-gate", 1)
    wake_queue.reset_all()                       # the process died
    cr._MAIL_SWEEPS.clear()
    router = cr.create_conversation_router(env.db, {})   # and came back
    assert _rows(env.db, "SELECT body, status FROM mailbox ORDER BY id") == [
        ("restart öncesi", "undelivered"), ("kartta kaldı", "rejected")]
    assert wake_queue.pending(b) == 0
    route = _route(router, "/conversations/{conv_id}/wake-stream")

    async def run():
        resp = await route.endpoint(conv_id=b, x_session_token="")
        try:
            with pytest.raises(asyncio.TimeoutError):
                await _next_frame(resp, 0.3)
        finally:
            await resp.body_iterator.aclose()

    asyncio.run(run())
    assert wake_queue.pending(b) == 0


# ── the startup sweep of notes stale from a previous run (owner decision,
# 28 Sep 2026) ────────────────────────────────────────────────────────────────

def test_startup_sweep_marks_stale_queued_mail_undelivered(env, auto):
    a, b, c = _chat(env.db, "Gönderen A"), _chat(env.db, "Alıcı B"), _chat(env.db, "Gönderen C")
    _send(env.client, a, b, body="ilk not")
    _send(env.client, a, b, body="ikinci not")
    _send(env.client, c, b, body="başka gönderenden")
    _send(env.client, a, c, body="A'dan C'ye de gitti")
    before_a = _rows(env.db, "SELECT updated_at FROM conversations WHERE id = ?", (a,))[0][0]
    wake_queue.reset_all()

    n = env.db.sweep_undelivered_mail(
        mailbox.format_undelivered_recipient_note, mailbox.format_undelivered_sender_note)

    assert n == 4
    assert {r[0] for r in _rows(env.db, "SELECT DISTINCT status FROM mailbox")} == {"undelivered"}
    # One system message per affected recipient chat (b, c), not one per note.
    b_sys = [m for m in env.db.get_conversation_messages(b) if m["role"] == "system"]
    c_sys = [m for m in env.db.get_conversation_messages(c) if m["role"] == "system"]
    assert len(b_sys) == 1
    assert mailbox.is_undelivered_message(b_sys[0]["content"])
    assert not mailbox.is_mail_message(b_sys[0]["content"])
    assert "ilk not" in b_sys[0]["content"] and "ikinci not" in b_sys[0]["content"]
    assert "başka gönderenden" in b_sys[0]["content"]
    assert f'#{a}' in b_sys[0]["content"] and f'#{c}' in b_sys[0]["content"]
    # c is both a recipient (of a's note) and a sender (to b), so it gets one
    # message of each kind, not one merged into the other.
    assert len(c_sys) == 2
    assert all(mailbox.is_undelivered_message(m["content"]) for m in c_sys)
    c_recipient = [m for m in c_sys
                   if "İstersen bu sohbete kendin yazarak devam edebilirsin." in m["content"]]
    c_sender = [m for m in c_sys if "gönderdiğin not" in m["content"]]
    assert len(c_recipient) == 1 and "A'dan C'ye de gitti" in c_recipient[0]["content"]
    assert len(c_sender) == 1 and f'#{b}' in c_sender[0]["content"]
    # One system message per affected SENDER chat too (a sent to both b and c).
    a_sys = [m for m in env.db.get_conversation_messages(a) if m["role"] == "system"]
    assert len(a_sys) == 1
    assert mailbox.is_undelivered_message(a_sys[0]["content"])
    assert f'#{b}' in a_sys[0]["content"] and f'#{c}' in a_sys[0]["content"]
    after_a = _rows(env.db, "SELECT updated_at FROM conversations WHERE id = ?", (a,))[0][0]
    assert after_a >= before_a


def test_startup_sweep_leaves_other_statuses_alone(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    env.db.add_mail(a, b, "beklemede", "pending_approval", "gate-1", 1)
    env.db.add_mail(a, b, "teslim edildi", "delivered", None, 1)
    env.db.add_mail(a, b, "reddedildi", "rejected", None, 1)

    n = env.db.sweep_undelivered_mail(
        mailbox.format_undelivered_recipient_note, mailbox.format_undelivered_sender_note)

    assert n == 0
    assert sorted(r[0] for r in _rows(env.db, "SELECT status FROM mailbox")) == [
        "delivered", "pending_approval", "rejected"]
    assert env.db.get_conversation_messages(b) == []


def test_startup_sweep_rolls_back_on_a_failed_message_write(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b, body="geri alınsın")
    _break_message_writes_to(env.db, b)

    with pytest.raises(sqlite3.DatabaseError):
        env.db.sweep_undelivered_mail(
            mailbox.format_undelivered_recipient_note, mailbox.format_undelivered_sender_note)

    assert _rows(env.db, "SELECT status FROM mailbox") == [("queued",)]
    assert env.db.get_conversation_messages(b) == []
    assert env.db.get_conversation_messages(a) == []

    _heal_message_writes(env.db)
    n = env.db.sweep_undelivered_mail(
        mailbox.format_undelivered_recipient_note, mailbox.format_undelivered_sender_note)
    assert n == 1
    assert _rows(env.db, "SELECT status FROM mailbox") == [("undelivered",)]


def test_requeue_arms_nothing_once_swept(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b, body="restart sonrası uyanmasın")
    wake_queue.reset_all()
    cr._MAIL_SWEEPS.clear()                      # a new process
    router = cr.create_conversation_router(env.db, {})   # runs the sweep
    assert env.db.queued_mail_targets() == []
    route = _route(router, "/wake-stream-all")

    async def run():
        resp = await route.endpoint(x_session_token="")
        try:
            with pytest.raises(asyncio.TimeoutError):
                await _next_frame(resp, 0.3)
        finally:
            await resp.body_iterator.aclose()

    asyncio.run(run())
    assert wake_queue.pending(b) == 0


def test_a_later_real_send_still_queues_and_delivers_normally_after_a_sweep(
        env, auto, monkeypatch):
    """The sweep runs once at router construction; mail sent DURING the run
    that follows must be unaffected (owner decision, 28 Sep 2026)."""
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    env.db.add_mail(a, b, "eski, taşınacak", "queued", None, 1)
    wake_queue.reset_all()
    cr._MAIL_SWEEPS.clear()
    cr.create_conversation_router(env.db, {})   # simulates the restart; runs the sweep
    assert _rows(env.db, "SELECT status FROM mailbox") == [("undelivered",)]

    result = _send(env.client, a, b, body="restart sonrası taze not")
    assert result.status_code == 200
    assert result.json()["status"] == "queued"
    # Scoped to a's own two rows: the fake runner's reply also owes b a note
    # back to a (the auto-forward mechanism), a third row this test is not about.
    assert sorted(r[0] for r in _rows(
        env.db, "SELECT status FROM mailbox WHERE from_conv = ?", (a,))) == [
        "queued", "undelivered"]

    wake_queue.issue_ticket(b, wake_queue.drain(b))
    resp = _wake_turn(env.client, b)
    assert resp.status_code == 200
    assert "restart sonrası taze not" in _FakeRunner.messages[-1]
    assert sorted(row[0] for row in _rows(
        env.db, "SELECT status FROM mailbox WHERE from_conv = ?", (a,))) == [
        "delivered", "undelivered"]


# Codex queueaudit, 28 Sep 2026: the sweep ran on every router construction,
# and a failed sweep left the previous run's notes free to wake chats.

def _restarted(db):
    """A router as a new process builds it: no sweep recorded for this DB."""
    wake_queue.reset_all()
    cr._MAIL_SWEEPS.clear()
    router = cr.create_conversation_router(db, {})
    app = FastAPI()
    app.include_router(router)
    return router, TestClient(app)


def _wake_frames(router, timeout=0.3):
    route = _route(router, "/wake-stream-all")

    async def run():
        resp = await route.endpoint(x_session_token="")
        frames = []
        try:
            while True:
                try:
                    frames.append(await _next_frame(resp, timeout))
                except asyncio.TimeoutError:
                    return frames
        finally:
            await resp.body_iterator.aclose()

    return asyncio.run(run())


def _woken(frames):
    import json as _json
    return sorted(f["conversation_id"] for f in (_json.loads(x[len("data: "):]) for x in frames)
                  if f.get("type") == "wake")


def test_a_router_rebuild_in_the_same_process_does_not_resweep_live_notes(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    live = env.db.add_mail(a, b, "canlı not", "queued", None, 1)
    cr.create_conversation_router(env.db, {})
    assert env.db.get_mail(live)["status"] == "queued"
    assert env.db.get_conversation_messages(a) == []
    assert env.db.get_conversation_messages(b) == []


def test_a_router_rebuild_in_the_same_process_keeps_a_live_card_s_note_pending(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    live = env.db.add_mail(a, b, "kartta bekliyor", "pending_approval", "live-gate", 1)
    cr.create_conversation_router(env.db, {})
    assert env.db.get_mail(live)["status"] == "pending_approval"


def test_a_router_over_another_database_still_gets_its_own_sweep(env, tmp_path):
    other = DatabaseManager(str(tmp_path / "other.db"))
    a, b = _chat(other, "A"), _chat(other, "B")
    stale = other.add_mail(a, b, "eski", "queued", None, 1)
    cr.create_conversation_router(other, {})
    assert other.get_mail(stale)["status"] == "undelivered"


def test_a_failed_startup_sweep_is_retried_before_any_wake_is_armed(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    stale = env.db.add_mail(a, b, "eski not", "queued", None, 1)
    _break_message_writes_to(env.db, b)
    router, _client = _restarted(env.db)
    assert env.db.get_mail(stale)["status"] == "queued"
    _heal_message_writes(env.db)

    assert _woken(_wake_frames(router)) == []
    assert env.db.get_mail(stale)["status"] == "undelivered"
    assert wake_queue.pending(b) == 0


def test_while_the_sweep_keeps_failing_old_notes_are_held_and_new_ones_work(
        env, auto, monkeypatch):
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b, c = _chat(env.db, "A"), _chat(env.db, "B"), _chat(env.db, "C")
    stale = env.db.add_mail(a, b, "eski not", "queued", None, 1)
    stale_to_c = env.db.add_mail(a, c, "c'ye eski not", "queued", None, 1)

    def broken(*_a, **_kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(env.db, "sweep_undelivered_mail", broken)
    router, client = _restarted(env.db)
    fresh = env.db.add_mail(a, c, "yeni not", "queued", None, 1)

    assert _woken(_wake_frames(router)) == [c]
    assert wake_queue.pending(b) == 0

    wake_queue.issue_ticket(c, [mailbox.notice(a)])
    assert _wake_turn(client, c).status_code == 200
    assert "yeni not" in _FakeRunner.messages[-1]
    assert "c'ye eski not" not in _FakeRunner.messages[-1]
    assert env.db.get_mail(fresh)["status"] == "delivered"
    assert env.db.get_mail(stale)["status"] == "queued"
    assert env.db.get_mail(stale_to_c)["status"] == "queued"

    # Once the sweep succeeds it takes the old notes only.
    monkeypatch.delattr(env.db, "sweep_undelivered_mail")
    later = env.db.add_mail(a, b, "sonraki not", "queued", None, 1)
    _wake_frames(router)
    assert env.db.get_mail(stale)["status"] == "undelivered"
    assert env.db.get_mail(stale_to_c)["status"] == "undelivered"
    assert env.db.get_mail(later)["status"] == "queued"


def test_with_the_startup_note_id_unknown_nothing_is_woken_or_claimed(env, auto, monkeypatch):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    stale = env.db.add_mail(a, b, "eski not", "queued", None, 1)

    def broken(*_a, **_kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(env.db, "max_mail_id", broken)
    monkeypatch.setattr(env.db, "sweep_undelivered_mail", broken)
    router, client = _restarted(env.db)

    assert _woken(_wake_frames(router)) == []
    wake_queue.issue_ticket(b, [mailbox.notice(a)])
    assert _data_frames(_wake_turn(client, b).text)[-1]["stop_reason"] == "mail_claim_failed"
    assert env.db.get_mail(stale)["status"] == "queued"


def test_undelivered_mail_survives_family_delete_like_other_statuses(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b, body="silinecek aile")
    env.db.sweep_undelivered_mail(
        mailbox.format_undelivered_recipient_note, mailbox.format_undelivered_sender_note)
    assert _rows(env.db, "SELECT status FROM mailbox") == [("undelivered",)]
    env.client.delete(f"/conversations/{b}", headers=H)
    assert _rows(env.db, "SELECT * FROM mailbox") == []


def test_a_note_dropped_by_a_user_message_is_re_armed(env, auto, monkeypatch):
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _send(env.client, a, b)
    body = {"conversation_id": b, "message": "benim mesajım", "user_id": 1}
    env.client.post("/chat-stream", json=body, headers=H)
    assert wake_queue.pending(b) == 0            # the user message drained it
    route = _route(env.router, "/wake-stream-all")

    async def run():
        resp = await route.endpoint(x_session_token="")
        try:
            return await _next_frame(resp, 2.0)
        finally:
            await resp.body_iterator.aclose()

    assert f'"conversation_id": {b}' in asyncio.run(run())


# ── deleting ─────────────────────────────────────────────────────────────────

def test_deleting_the_recipient_removes_its_notes_and_denies_the_card(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    data = _send(env.client, a, b).json()
    assert env.client.delete(f"/conversations/{b}", headers=H).status_code == 200
    assert _rows(env.db, "SELECT * FROM mailbox") == []
    assert data["gate_id"] not in _pending(env.client)
    result = env.client.get(f"/mcp-approval-result/{data['gate_id']}", headers=H).json()
    assert result["approved"] is False


def test_deleting_a_root_family_removes_notes_of_every_member(env, auto):
    a = _chat(env.db, "A")
    env.db.add_message(a, "user", "x")
    branch = env.db.create_branch(a)["id"]
    c = _chat(env.db, "C")
    _send(env.client, branch, c)
    _send(env.client, c, a)
    assert len(_rows(env.db, "SELECT * FROM mailbox")) == 2
    env.client.delete(f"/conversations/{a}", headers=H)
    assert _rows(env.db, "SELECT * FROM mailbox") == []


def test_a_branch_copies_no_mail(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    env.db.add_message(a, "user", "x")
    _send(env.client, b, a)
    branch = env.db.create_branch(a)["id"]
    assert _rows(env.db, "SELECT COUNT(*) FROM mailbox WHERE to_conv = ? OR from_conv = ?",
                 (branch, branch)) == [(0,)]


# ── ownership of the mailbox routes ──────────────────────────────────────────

def test_status_and_cancel_answer_only_the_sending_chat(env):
    a, b, c = _chat(env.db, "A"), _chat(env.db, "B"), _chat(env.db, "C")
    foreign = _chat(env.db, "x", user_id=2)
    data = _send(env.client, a, b).json()
    mid = data["mail_id"]
    get = lambda conv: env.client.get(f"/mailbox/status/{mid}", params={"conversation_id": conv}, headers=H)
    assert get(a).status_code == 200 and get(a).json()["status"] == "pending_approval"
    assert get(c).status_code == 404          # another chat of the same user
    assert get(b).status_code == 404          # even the recipient
    assert get(foreign).status_code == 404    # another user's chat
    for conv in (c, foreign):
        r = env.client.post(f"/mailbox/cancel/{mid}", json={"conversation_id": conv}, headers=H)
        assert r.status_code == 404
    assert data["gate_id"] in _pending(env.client)
    assert _rows(env.db, "SELECT status FROM mailbox") == [("pending_approval",)]
    GATE_OWNERS.pop(data["gate_id"], None)


def test_chat_list_excludes_the_caller_and_side_rows_and_refuses_foreign_ids(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    side = env.db.create_side_chat(a, 1)
    foreign = _chat(env.db, "x", user_id=2)
    with ambient_turn(".", "step", b):
        r = env.client.get("/mailbox/chats", params={"conversation_id": a}, headers=H)
    assert r.status_code == 200
    chats = r.json()["chats"]
    assert [c["id"] for c in chats] == [b]
    assert chats[0]["busy"] is True
    assert f"#{b}" in r.json()["text"]
    assert side not in [c["id"] for c in chats] and foreign not in [c["id"] for c in chats]
    assert env.client.get("/mailbox/chats", params={"conversation_id": foreign},
                          headers=H).status_code == 404


# ── carriers ─────────────────────────────────────────────────────────────────

def test_claude_mail_server_entry_is_identical_across_two_turns(monkeypatch, tmp_path):
    from tests.test_mcp_approval_owner import _claude_session_kwargs
    first = _claude_session_kwargs(monkeypatch, tmp_path, 7)["mcp_servers"]
    second = _claude_session_kwargs(monkeypatch, tmp_path, 7)["mcp_servers"]
    assert first == second
    entry = first[mailbox.CLAUDE_SERVER_NAME]
    assert entry["env"]["GAMACHINE_CONVERSATION_ID"] == "7"
    assert entry["args"][-1] == "mail-mcp-server"
    assert "LOCAL_APP_TOKEN" not in entry["env"]
    cached = types.SimpleNamespace(effort=None, model=None, cwd=str(tmp_path),
                                   mcp_servers=first, read_only=False)
    assert ar._oturum_yeniden_kurma_gerekceleri(
        cached, model=None, effort=None, workspace=str(tmp_path), mcp_servers=second) == []


def test_a_read_only_claude_session_gets_no_mail_server(monkeypatch, tmp_path):
    import subprocess
    from providers import claude_sdk_session
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    captured = []

    class _Sess:
        session_id = None
        auto_approve = False

        def __init__(self, kw):
            captured.append(kw)

        async def stream(self, _m):
            return
            yield  # pragma: no cover

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda host="localhost": None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(claude_sdk_session, "get_session", lambda cid, **kw: _Sess(kw))
    runner = ar.AgentRunner(provider_type="subscription", api_key="", model_name="claude-x",
                            workspace_path=str(tmp_path), conversation_id=9,
                            generation_mode="auto", read_only=True)

    async def run():
        async for _ in runner._run_claude_session("hi"):
            pass

    asyncio.run(run())
    assert mailbox.CLAUDE_SERVER_NAME not in captured[0]["mcp_servers"]


def test_claude_allows_the_mail_tools_without_its_own_card_except_read_only():
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    from providers.claude_sdk_session import ClaudeSDKSession

    async def decide(read_only):
        sess = ClaudeSDKSession(conversation_id=4343, cwd=".", auto_approve=False,
                                approval_timeout=1.0, read_only=read_only)
        sess._out_q = asyncio.Queue()
        out = await sess._can_use_tool("mcp__gamachineMail__send_chat_message",
                                       {"to_chat_id": 2, "message": "x"}, None)
        return out, sess._out_q.qsize()

    allowed, cards = asyncio.run(decide(False))
    assert isinstance(allowed, PermissionResultAllow) and cards == 0
    denied, cards = asyncio.run(decide(True))
    assert isinstance(denied, PermissionResultDeny) and cards == 0


def test_tool_registry_send_path_uses_the_runner_chat_not_a_model_argument(env, auto):
    from tools import tool_registry
    a, b, c = _chat(env.db, "A"), _chat(env.db, "B"), _chat(env.db, "C")

    async def run():
        with ambient_turn(".", "auto", a):
            return await tool_registry.execute_tool_async(
                "send_chat_message", {"to_chat_id": b, "message": "api", "conversation_id": c},
                ".", a)

    result = asyncio.run(run())
    assert result["success"] is True, result
    assert _rows(env.db, "SELECT from_conv, to_conv, body, status FROM mailbox") == [
        (a, b, "api", "queued")]
    listed = asyncio.run(tool_registry.execute_tool_async("list_chats", {}, ".", a))
    assert listed["success"] and f"#{b}" in listed["content"] and f"#{a} " not in listed["content"]


def test_tool_registry_send_path_waits_for_the_card_in_step_mode(env, monkeypatch):
    from tools import tool_registry
    a, b = _chat(env.db, "A"), _chat(env.db, "B")

    async def run():
        with ambient_turn(".", "step", a):
            task = asyncio.create_task(tool_registry.execute_tool_async(
                "send_chat_message", {"to_chat_id": b, "message": "onaylı"}, ".", a))
            for _ in range(50):
                await asyncio.sleep(0.02)
                pending = await _route(env.router, "/mcp-pending").endpoint(x_session_token="")
                if pending["pending"]:
                    break
            gate = next(iter(pending["pending"]))
            await _route(env.router, "/mcp-approval-respond/{gate_id}").endpoint(
                gate_id=gate, body={"approved": True}, x_session_token="")
            return await asyncio.wait_for(task, 5.0)

    result = asyncio.run(run())
    assert result["success"] is True, result
    assert _rows(env.db, "SELECT status FROM mailbox") == [("queued",)]


def test_a_read_only_runner_may_list_but_not_send():
    runner = ar.AgentRunner(provider_type="openai", api_key="", model_name="gpt-x",
                            workspace_path=".", conversation_id=5, read_only=True)
    names = [t["name"] for t in runner._tool_definitions()]
    assert "list_chats" in names and "send_chat_message" not in names
    result, _ = asyncio.run(runner._execute_tool_with_approval(
        "send_chat_message", {"to_chat_id": 6, "message": "x"}))
    assert result["success"] is False


def test_the_mail_server_exposes_only_the_two_mail_tools_and_unityai_has_them_too():
    from unity_ai_mcp.mail_server import create_server as mail_server
    from unity_ai_mcp.server import create_server as unityai_server

    async def names(server):
        return sorted(t.name for t in await server.list_tools())

    assert asyncio.run(names(mail_server())) == ["list_chats", "send_chat_message"]
    assert {"list_chats", "send_chat_message"} <= set(asyncio.run(names(unityai_server("."))))


def test_the_frozen_build_can_start_the_mail_server():
    import os
    backend = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(backend, "backend.spec"), encoding="utf-8") as f:
        spec = f.read()
    with open(os.path.join(backend, "app", "main.py"), encoding="utf-8") as f:
        main = f.read()
    assert "'unity_ai_mcp.mail_server'" in spec and "'unity_ai_mcp.tools.mailbox_tools'" in spec
    assert '"mail-mcp-server"' in main and "unity_ai_mcp.mail_server" in main


# ── the wake turn's framing (27 Sep 2026) ────────────────────────────────────

def test_a_mail_wake_names_the_receivers_tool_and_does_not_say_continue(env, auto, monkeypatch):
    # An agy branch woken by a note spent 23 steps re-checking old work, and
    # called send_chat_message on unityMCP.
    _FakeRunner.messages = []
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    env.db.save_ai_config(1, "subscription", "gemini-3-pro", "")
    a, b = _chat(env.db, "Gönderen"), _chat(env.db, "B")
    env.db.add_message(b, "user", "önceki soru")
    env.db.add_message(b, "assistant", "önceki cevap")
    assert _send(env.client, a, b, body="build geçti mi?").status_code == 200
    wake_queue.issue_ticket(b, wake_queue.drain(b))

    assert _wake_turn(env.client, b).status_code == 200
    turn = _FakeRunner.messages[-1]
    assert f"#{a} sohbetinden gelen bir not" in turn
    assert "`call_mcp_tool` ile (sunucu `unityai`" in turn
    context = _FakeRunner.last_kw["context"]
    assert mailbox.MAIL_WAKE_HISTORY_HEADER in context
    assert "kaldığın yerden devam et" not in context

    # A user turn keeps the usual header.
    r = env.client.post("/chat-stream", headers=H, json={
        "conversation_id": b, "message": "şimdi ne durumda?", "user_id": 1})
    assert r.status_code == 200
    assert "kaldığın yerden devam et" in _FakeRunner.last_kw["context"]
    assert mailbox.MAIL_WAKE_HISTORY_HEADER not in _FakeRunner.last_kw["context"]
