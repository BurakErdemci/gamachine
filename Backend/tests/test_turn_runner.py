"""A backend-started turn (agentic/turn_runner.start_background_turn) runs the
same turn body as POST /chat-stream: same stored messages, cli_sessions row and
turn events, with no request and no window. Every runner is a fake; no CLI or
provider starts."""
import asyncio
import json
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import turn_events, turn_replies, turn_runner, wake_queue
from database import DatabaseManager
from schemas import ChatRequest

CODEX = "gpt-6-luna"
DONE = ("done", {"iterations": 1, "stop_reason": "complete", "session_id": "sess-42"})


class _Runner:
    """Plays `_Runner.script`; a step "hold" waits for `_Runner.hold` to be set."""
    script: list = []
    hold: asyncio.Event = None
    messages: list = []
    running = 0
    max_running = 0

    def __init__(self, **kw):
        self.kw = kw

    async def run(self, message):
        _Runner.messages.append(message)
        _Runner.running += 1
        _Runner.max_running = max(_Runner.max_running, _Runner.running)
        try:
            for step in _Runner.script:
                if step == "hold":
                    await _Runner.hold.wait()
                    continue
                if isinstance(step, Exception):
                    raise step
                yield ar.AgentEvent(*step)
        finally:
            _Runner.running -= 1


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    turn_replies.clear()
    turn_runner.reset_all()
    wake_queue.reset_all()
    yield DatabaseManager(str(tmp_path / "bg.db"))
    turn_replies.clear()
    turn_runner.reset_all()
    wake_queue.reset_all()


@pytest.fixture
def router(db, monkeypatch):
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    monkeypatch.setattr(cr, "AgentRunner", _Runner)
    _Runner.script = [("response", {"content": "CEVAP"}), DONE]
    _Runner.messages = []
    _Runner.running = _Runner.max_running = 0
    from providers import claude_sdk_session

    async def _no_close(cid):
        return None

    monkeypatch.setattr(claude_sdk_session, "close_session", _no_close)
    monkeypatch.setattr(turn_runner, "_EXECUTOR", None)
    return cr.create_conversation_router(db, {})


def _chat(db, model=CODEX):
    cid = db.create_conversation(1, "Yeni Sohbet")
    db.save_ai_config(1, "subscription", model, "")
    return cid


def _endpoint(router):
    return next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/chat-stream")


async def _http_turn(router, cid, text="soru"):
    resp = await _endpoint(router)(ChatRequest(conversation_id=cid, message=text, user_id=1),
                                   x_session_token="", x_ui_lang=None)
    return [json.loads(c[6:]) for c in [chunk async for chunk in resp.body_iterator]
            if c.startswith("data: ")]


async def _idle(*convs, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while any(turn_runner.busy(c) for c in convs) or turn_runner._TASKS:
        assert loop.time() < end, "turn never ended"
        await asyncio.sleep(0.005)


def _messages(db, cid):
    return [(m["role"], m["content"], m["provider"], m["model"])
            for m in db.get_conversation_messages(cid)]


def _cli_rows(db, cid):
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        return conn.execute("SELECT provider, session_id, workspace FROM cli_sessions "
                            "WHERE conversation_id = ?", (cid,)).fetchall()


def _kinds(cid, since):
    return [(e["kind"], e.get("origin")) for e in turn_events.since(cid, since)["events"]]


async def test_a_background_turn_stores_what_an_http_turn_stores(db, router):
    http_cid, bg_cid = _chat(db), _chat(db)
    ring_http = turn_events.since(http_cid, 0)["last_seq"]
    ring_bg = turn_events.since(bg_cid, 0)["last_seq"]

    frames = await _http_turn(router, http_cid, "aynı görev")
    assert frames[0]["type"] == "turn_meta" and any(f["type"] == "done" for f in frames)
    assert turn_runner.start_background_turn(bg_cid, "aynı görev", origin="user") == "started"
    await _idle(bg_cid)

    assert _messages(db, bg_cid) == _messages(db, http_cid) == [
        ("user", "aynı görev", None, None), ("assistant", "CEVAP", "codex", CODEX)]
    assert _cli_rows(db, bg_cid) == _cli_rows(db, http_cid) != []
    assert db.get_conversation_title(bg_cid) == db.get_conversation_title(http_cid) == "aynı görev"
    # The UI follows a background turn through the same turn-event ring.
    assert ("turn_start", "user") in _kinds(bg_cid, ring_bg)
    assert ("turn_end", None) in _kinds(bg_cid, ring_bg)
    assert [k for k, _ in _kinds(bg_cid, ring_bg)] == [k for k, _ in _kinds(http_cid, ring_http)]
    # Its user row records the reply row, as an HTTP turn's does.
    uid, aid = [m["id"] for m in db.get_conversation_messages(bg_cid)]
    assert turn_replies.get(bg_cid, uid) == {"state": "ended", "assistant_row_id": aid, "status": "done"}


async def test_a_background_input_is_a_system_row_no_one_typed(db, router):
    cid = _chat(db)
    turn_runner.start_background_turn(cid, "[Alt ajan] sonuç: 3 dosya")
    await _idle(cid)
    assert [(r, c) for r, c, *_ in _messages(db, cid)] == [
        ("system", "[Alt ajan] sonuç: 3 dosya"), ("assistant", "CEVAP")]
    assert _Runner.messages == ["[Alt ajan] sonuç: 3 dosya"]


async def test_a_user_message_on_a_busy_chat_goes_first_and_carries_queued_results(db, router):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", ("response", {"content": "CEVAP"}), DONE]
    assert turn_runner.start_background_turn(cid, "sonuç A") == "started"
    await asyncio.sleep(0.02)
    assert turn_runner.start_background_turn(cid, "sonuç B") == "queued"
    user = asyncio.create_task(_http_turn(router, cid, "kullanıcı mesajı"))
    await asyncio.sleep(0.02)
    # The user's turn waits at the gate; nothing of it is stored yet.
    assert [c for _, c, *_ in _messages(db, cid)] == ["sonuç A"]
    _Runner.hold.set()
    frames = await user
    await _idle(cid)

    assert _Runner.max_running == 1
    assert _Runner.messages == ["sonuç A", "kullanıcı mesajı\n\nsonuç B"]
    assert [(r, c) for r, c, *_ in _messages(db, cid)] == [
        ("system", "sonuç A"), ("assistant", "CEVAP"),
        ("user", "kullanıcı mesajı"), ("system", "sonuç B"), ("assistant", "CEVAP")]
    assert frames[0]["type"] == "turn_meta"
    assert all(f["conversation_id"] == cid for f in frames)


async def test_two_http_turns_on_one_chat_run_one_after_the_other(db, router):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", ("response", {"content": "CEVAP"}), DONE]
    first = asyncio.create_task(_http_turn(router, cid, "bir"))
    await asyncio.sleep(0.02)
    second = asyncio.create_task(_http_turn(router, cid, "iki"))
    await asyncio.sleep(0.02)
    _Runner.hold.set()
    await asyncio.gather(first, second)
    assert _Runner.max_running == 1
    assert [(r, c) for r, c, *_ in _messages(db, cid)] == [
        ("user", "bir"), ("assistant", "CEVAP"), ("user", "iki"), ("assistant", "CEVAP")]


async def test_fifty_background_starts_never_run_two_turns_at_once(db, router):
    cid = _chat(db)
    for i in range(50):
        turn_runner.start_background_turn(cid, f"r{i}")
    await _idle(cid)
    assert _Runner.max_running == 1
    stored = [c for r, c, *_ in _messages(db, cid) if r == "system"]
    assert stored == [f"r{i}" for i in range(50)]
    assert len(_Runner.messages) == 2


async def test_a_failing_background_turn_is_recorded_and_frees_the_chat(db, router):
    cid = _chat(db)
    _Runner.script = [RuntimeError("provider down")]
    turn_runner.start_background_turn(cid, "görev")
    await _idle(cid)
    assert not turn_runner.busy(cid)
    assert turn_runner.background_errors(cid)
    rows = _messages(db, cid)
    assert rows[0][:2] == ("system", "görev")
    assert rows[-1][0] == "system" and "hata" in rows[-1][1]
    _Runner.script = [("response", {"content": "CEVAP"}), DONE]
    frames = await _http_turn(router, cid, "tekrar")
    assert any(f["type"] == "done" for f in frames)


async def test_a_background_turn_that_cannot_open_is_recorded(db, router):
    turn_runner.start_background_turn(987654, "kimsesiz sohbet")
    await _idle(987654)
    assert turn_runner.background_errors(987654)
    assert not turn_runner.busy(987654)


async def test_a_background_turn_refuses_a_side_chat(db, router, monkeypatch):
    cid = _chat(db)
    monkeypatch.setattr(db, "get_side_of", lambda c: 1 if c == cid else None)
    turn_runner.start_background_turn(cid, "yan sohbete")
    await _idle(cid)
    assert _Runner.messages == []
    assert turn_runner.background_errors(cid)


async def test_a_busy_gate_holds_wake_offers(db, router):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", DONE]
    turn_runner.start_background_turn(cid, "uzun iş")
    await asyncio.sleep(0.02)
    wake = next(r for r in router.routes
                if getattr(r, "path", "") == "/conversations/{conv_id}/wake-stream")
    cells = dict(zip(wake.endpoint.__code__.co_freevars,
                     (c.cell_contents for c in wake.endpoint.__closure__)))
    blocked = cells["_wake_blocked"]
    assert blocked(cid) == "turn_running"
    _Runner.hold.set()
    await _idle(cid)
    assert blocked(cid) is None
