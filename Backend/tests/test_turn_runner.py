"""A backend-started turn (agentic/turn_runner.start_background_turn) runs the
same turn body as POST /chat-stream: same stored messages, cli_sessions row and
turn events, with no request and no window. Every input is stored when it is
accepted, a turn opens only once it holds the chat's gate, and a closed
window's Claude turn keeps the chat until its session reports the end.
Every runner is a fake; no CLI or provider starts."""
import asyncio
import json
import sqlite3
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from starlette.requests import ClientDisconnect

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import turn_events, turn_replies, turn_runner, wake_queue
from database import DatabaseManager
from schemas import ChatRequest

CODEX = "gpt-6-luna"
CLAUDE = "claude-sonnet-5"
DONE = ("done", {"iterations": 1, "stop_reason": "complete", "session_id": "sess-42"})


class _Runner:
    """Plays `_Runner.script`; a step "hold" waits for `_Runner.hold`, "hang" forever."""
    script: list = []
    hold: asyncio.Event = None
    messages: list = []
    kwargs: list = []
    running = 0
    max_running = 0

    def __init__(self, **kw):
        self.kw = kw
        _Runner.kwargs.append(kw)

    async def run(self, message):
        _Runner.messages.append(message)
        _Runner.running += 1
        _Runner.max_running = max(_Runner.max_running, _Runner.running)
        try:
            for step in _Runner.script:
                if step == "hold":
                    await _Runner.hold.wait()
                    continue
                if step == "hang":
                    await asyncio.Event().wait()
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
    _Runner.kwargs = []
    _Runner.running = _Runner.max_running = 0
    from providers import claude_sdk_session

    async def _no_close(cid):
        return None

    monkeypatch.setattr(claude_sdk_session, "close_session", _no_close)
    for name in ("_EXECUTOR", "_ACCEPT", "_HAS_PENDING"):
        monkeypatch.setattr(turn_runner, name, None, raising=False)
    return cr.create_conversation_router(db, {})


def _chat(db, model=CODEX):
    cid = db.create_conversation(1, "Yeni Sohbet")
    db.save_ai_config(1, "subscription", model, "")
    return cid


def _endpoint(router, path="/chat-stream"):
    return next(r.endpoint for r in router.routes if getattr(r, "path", "") == path)


async def _open(router, cid, text="soru", **kw):
    """The endpoint's response, before Starlette iterates it."""
    return await _endpoint(router)(ChatRequest(conversation_id=cid, message=text, user_id=1, **kw),
                                   x_session_token="", x_ui_lang=None)


async def _frames(resp):
    return [json.loads(c[6:]) for c in [chunk async for chunk in resp.body_iterator]
            if c.startswith("data: ")]


async def _http_turn(router, cid, text="soru", **kw):
    return await _frames(await _open(router, cid, text, **kw))


async def _idle(*convs, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while any(turn_runner.busy(c) for c in convs) or turn_runner._TASKS:
        assert loop.time() < end, "turn never ended"
        await asyncio.sleep(0.005)


def _messages(db, cid):
    return [(m["role"], m["content"], m["provider"], m["model"])
            for m in db.get_conversation_messages(cid)]


def _rows(db, cid):
    return [(m["role"], m["content"]) for m in db.get_conversation_messages(cid)]


def _cli_rows(db, cid):
    with sqlite3.connect(db.db_path) as conn:
        return conn.execute("SELECT provider, session_id, workspace FROM cli_sessions "
                            "WHERE conversation_id = ?", (cid,)).fetchall()


def _kinds(cid, since):
    return [(e["kind"], e.get("origin")) for e in turn_events.since(cid, since)["events"]]


# ── parity ──────────────────────────────────────────────────────────────
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
    assert [k for k, _ in _kinds(bg_cid, ring_bg)] == [k for k, _ in _kinds(http_cid, ring_http)]
    uid, aid = [m["id"] for m in db.get_conversation_messages(bg_cid)]
    assert turn_replies.get(bg_cid, uid) == {"state": "ended", "assistant_row_id": aid, "status": "done"}


async def test_a_background_turn_uses_the_last_http_turn_settings(db, router):
    a, b = _chat(db), _chat(db)
    settings = dict(language="en", thinking_level="high", effort_level="max", ultracode=True)
    await _http_turn(router, a, "x", **settings)
    http_kw = _Runner.kwargs[-1]
    # b never had an HTTP turn (a sub-agent's new chat): the user's latest apply.
    turn_runner.start_background_turn(b, "x", origin="user")
    await _idle(b)
    bg_kw = _Runner.kwargs[-1]
    keys = ("language", "thinking_level", "effort_level", "ultracode")
    assert {k: bg_kw[k] for k in keys} == {k: http_kw[k] for k in keys} == settings
    # A chat's own last settings win over another chat's newer ones.
    await _http_turn(router, b, "y", language="de", thinking_level="low")
    await _http_turn(router, a, "z", language="en")
    turn_runner.start_background_turn(b, "again")
    await _idle(b)
    assert (_Runner.kwargs[-1]["language"], _Runner.kwargs[-1]["thinking_level"]) == ("de", "low")


async def test_a_background_input_is_a_system_row_no_one_typed(db, router):
    cid = _chat(db)
    turn_runner.start_background_turn(cid, "[Alt ajan] sonuç: 3 dosya")
    await _idle(cid)
    assert _rows(db, cid) == [("system", "[Alt ajan] sonuç: 3 dosya"), ("assistant", "CEVAP")]
    assert _Runner.messages == ["[Alt ajan] sonuç: 3 dosya"]


async def test_a_background_answer_that_is_not_stored_is_a_recorded_failure(db, router, monkeypatch):
    cid = _chat(db)
    orig = db.add_message

    def no_answers(conv, role, *a, **kw):
        if role == "assistant":
            raise OSError("disk full")
        return orig(conv, role, *a, **kw)

    monkeypatch.setattr(db, "add_message", no_answers)
    turn_runner.start_background_turn(cid, "görev", origin="user")
    await _idle(cid)
    uid = db.get_conversation_messages(cid)[0]["id"]
    assert turn_replies.get(cid, uid)["status"] != "done"
    assert any("turn_not_saved" in e["error"] for e in turn_runner.background_errors(cid))


# ── inputs stored when accepted, never twice, never lost ────────────────
async def test_a_user_message_on_a_busy_chat_is_stored_at_once_and_goes_first(db, router):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", ("response", {"content": "CEVAP"}), DONE]
    assert turn_runner.start_background_turn(cid, "sonuç A") == "started"
    await asyncio.sleep(0.02)
    assert turn_runner.start_background_turn(cid, "sonuç B") == "queued"
    user = asyncio.create_task(_http_turn(router, cid, "kullanıcı mesajı"))
    await asyncio.sleep(0.02)
    # Stored the moment it arrived, while it waits at the gate.
    assert [c for _, c in _rows(db, cid)] == ["sonuç A", "sonuç B", "kullanıcı mesajı"]
    _Runner.hold.set()
    frames = await user
    await _idle(cid)

    assert _Runner.max_running == 1
    # The user's message goes first in the turn and carries B (one turn).
    assert _Runner.messages == ["sonuç A", "kullanıcı mesajı\n\nsonuç B"]
    assert frames[0] == {"type": "status", "code": "turn_queued", "conversation_id": cid,
                         "detail": frames[0]["detail"]}
    assert frames[1]["type"] == "turn_meta"
    assert all(f["conversation_id"] == cid for f in frames)


async def test_a_waiting_message_whose_window_closes_is_still_answered(db, router):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", ("response", {"content": "C"}), DONE]
    first = asyncio.create_task(_http_turn(router, cid, "bir"))
    await asyncio.sleep(0.02)
    second = asyncio.create_task(_http_turn(router, cid, "iki"))
    await asyncio.sleep(0.02)
    assert "iki" in [c for _, c in _rows(db, cid)]
    second.cancel()  # window closed while it waited
    await asyncio.gather(second, return_exceptions=True)
    _Runner.hold.set()
    await first
    await _idle(cid)
    assert _Runner.messages == ["bir", "iki"]
    assert [c for _, c in _rows(db, cid)].count("iki") == 1


async def test_a_response_that_never_streams_hands_its_message_back(db, router):
    cid = _chat(db)
    resp = await _open(router, cid, "hiç akmadı")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(event):
        raise OSError("client gone")

    with pytest.raises((OSError, ClientDisconnect)):
        await resp({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    await _idle(cid)
    assert _Runner.messages == ["hiç akmadı"]


async def test_a_failed_input_write_is_not_accepted_and_never_stored_twice(db, router, monkeypatch):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", ("response", {"content": "C"}), DONE]
    turn_runner.start_background_turn(cid, "A")
    await asyncio.sleep(0.02)
    turn_runner.start_background_turn(cid, "B1")
    orig = db.insert_message_in
    failed = []

    def flaky(conn, conv, role, content, *a, **kw):
        if content == "B2" and not failed:
            failed.append(1)
            raise sqlite3.OperationalError("disk I/O error")
        return orig(conn, conv, role, content, *a, **kw)

    monkeypatch.setattr(db, "insert_message_in", flaky)
    try:
        turn_runner.start_background_turn(cid, "B2")
    except sqlite3.OperationalError:
        turn_runner.start_background_turn(cid, "B2")  # the caller retries a refused input
    _Runner.hold.set()
    await _idle(cid)
    assert failed == [1]
    assert [c for r, c in _rows(db, cid) if r == "system"] == ["A", "B1", "B2"]
    assert _Runner.messages == ["A", "B1\n\nB2"]


async def test_slash_commands_leave_a_pending_input_for_its_own_turn(db, router):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", ("response", {"content": "C"}), DONE]
    turn_runner.start_background_turn(cid, "hold")
    await asyncio.sleep(0.02)
    turn_runner.start_background_turn(cid, "SONUÇ")
    slashes = [asyncio.create_task(_http_turn(router, cid, "/status")) for _ in range(4)]
    await asyncio.sleep(0.02)
    _Runner.hold.set()
    await asyncio.gather(*slashes)
    await _idle(cid)
    assert _Runner.messages.count("SONUÇ") == 1
    assert _Runner.messages.count("/status") == 4
    assert [c for _, c in _rows(db, cid)].count("SONUÇ") == 1


async def test_inputs_pending_from_a_previous_run_ride_the_next_turn_once(db, router):
    cid = _chat(db)
    # Accepted by an earlier backend process that stopped before answering.
    db.add_turn_input(cid, "system", "önceki süreçten sonuç", "background")
    await _http_turn(router, cid, "merhaba")
    await _http_turn(router, cid, "tekrar")
    assert _Runner.messages == ["merhaba\n\nönceki süreçten sonuç", "tekrar"]


# ── the gate is held from opening to the real end ───────────────────────
async def test_two_idle_requests_open_one_after_the_other(db, router):
    cid = _chat(db)
    _Runner.script = [("response", {"content": "R1"}), DONE]
    r1 = await _open(router, cid, "bir")
    r2 = await _open(router, cid, "iki")   # the chat still looks idle
    await _frames(r1)
    _Runner.script = [("response", {"content": "R2"}), DONE]
    await _frames(r2)
    # The second turn opened after the first answer was stored.
    assert "R1" in _Runner.kwargs[1]["context"]
    assert _Runner.max_running == 1


async def test_a_background_turn_between_an_opened_request_and_its_stream(db, router):
    cid = _chat(db)
    _Runner.script = [("response", {"content": "R"}), DONE]
    r1 = await _open(router, cid, "kullanıcı")
    assert turn_runner.start_background_turn(cid, "bgjob") == "started"
    await _frames(r1)
    await _idle(cid)
    rows = _rows(db, cid)
    assert rows.index(("user", "kullanıcı")) < rows.index(("system", "bgjob"))
    assert _Runner.max_running == 1 and sorted(_Runner.messages) == ["bgjob", "kullanıcı"]


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
    assert _Runner.max_running == 1 and _Runner.messages == ["bir", "iki"]


async def test_fifty_background_starts_never_run_two_turns_at_once(db, router):
    cid = _chat(db)
    for i in range(50):
        turn_runner.start_background_turn(cid, f"r{i}")
    await _idle(cid)
    assert _Runner.max_running == 1
    assert [c for r, c in _rows(db, cid) if r == "system"] == [f"r{i}" for i in range(50)]
    assert "\n\n".join(_Runner.messages).split("\n\n") == [f"r{i}" for i in range(50)]


async def test_a_wake_turn_does_not_carry_pending_inputs(db, router):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", ("response", {"content": "C"}), DONE]
    turn_runner.start_background_turn(cid, "ilk")
    await asyncio.sleep(0.02)
    turn_runner.start_background_turn(cid, "bekleyen sonuç")
    wake_queue.issue_ticket(cid, ["tasks_done|derleme"])
    wake = asyncio.create_task(_http_turn(router, cid, "x", origin="wake"))
    await asyncio.sleep(0.02)
    _Runner.hold.set()
    await wake
    await _idle(cid)
    assert len(_Runner.messages) == 3
    assert "bekleyen sonuç" not in _Runner.messages[1]
    assert _Runner.messages[2] == "bekleyen sonuç"


async def test_a_waiting_turn_of_a_deleted_chat_runs_nothing(db, router):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", ("response", {"content": "C"}), DONE]
    first = asyncio.create_task(_http_turn(router, cid, "bir"))
    await asyncio.sleep(0.02)
    second = asyncio.create_task(_http_turn(router, cid, "iki"))
    await asyncio.sleep(0.02)
    await _endpoint(router, "/conversations/{conv_id}")(conv_id=cid, x_session_token="")
    _Runner.hold.set()
    await asyncio.gather(first, second, return_exceptions=True)
    await _idle(cid)
    # The waiting turn ran no provider turn and stored nothing (the running
    # turn's own late answer row is outside this: it was already running).
    assert _Runner.messages == ["bir"]
    assert ("user", "iki") not in _rows(db, cid)
    assert (await second)[-1]["stop_reason"] == "conversation_deleted"


async def test_an_asgi_send_disconnect_frees_the_chat(db, router):
    cid = _chat(db)
    _Runner.script = [("text", {"content": "partial"}), "hang"]
    resp = await _open(router, cid, "soru")

    async def send(event):
        if event["type"] == "http.response.body" and b"partial" in event.get("body", b""):
            raise OSError("transport closed")

    async def receive():
        await asyncio.Future()

    with pytest.raises((OSError, ClientDisconnect)):
        await asyncio.wait_for(resp({"type": "http", "asgi": {"spec_version": "2.4"}},
                                    receive, send), 3)
    assert not turn_runner.busy(cid)


async def test_a_closed_window_claude_turn_holds_the_chat_until_its_session_ends(db, router):
    from providers import claude_sdk_session
    cid = _chat(db, CLAUDE)
    _Runner.script = [("response", {"content": "ARA"}), "hang"]
    resp = await _open(router, cid, "uzun iş")
    it = resp.body_iterator
    while "ARA" not in await it.__anext__():
        pass
    await it.aclose()   # window closed; the SDK session runs on
    assert turn_runner.busy(cid)
    _Runner.script = [("response", {"content": "SONRA"}), DONE]
    assert turn_runner.start_background_turn(cid, "[arka plan sonucu]") == "queued"
    await asyncio.sleep(0.02)
    assert _Runner.messages == ["uzun iş"]
    # The session finishes the old turn with no listener: it stores its
    # answer, then reports the end; only then does the queued turn start.
    claude_sdk_session._DB_SAVE_CB(cid, "eski turun cevabı", "claude-opus-5")
    claude_sdk_session._UNHEARD_TURN_END_CB(cid)
    await _idle(cid)
    assert _Runner.messages == ["uzun iş", "[arka plan sonucu]"]
    uid = db.get_conversation_messages(cid)[0]["id"]
    assert turn_replies.get(cid, uid)["status"] == "done"
    assert [c for r, c in _rows(db, cid) if r == "assistant"] == ["eski turun cevabı", "SONRA"]


async def test_a_failing_background_turn_is_recorded_and_frees_the_chat(db, router):
    cid = _chat(db)
    _Runner.script = [RuntimeError("provider down")]
    turn_runner.start_background_turn(cid, "görev")
    await _idle(cid)
    assert not turn_runner.busy(cid)
    assert turn_runner.background_errors(cid)
    rows = _rows(db, cid)
    assert rows[0] == ("system", "görev")
    assert rows[-1][0] == "system" and "hata" in rows[-1][1]
    _Runner.script = [("response", {"content": "CEVAP"}), DONE]
    frames = await _http_turn(router, cid, "tekrar")
    assert any(f["type"] == "done" for f in frames)


async def test_an_input_for_a_missing_or_side_chat_is_refused(db, router, monkeypatch):
    with pytest.raises(ValueError):
        turn_runner.start_background_turn(987654, "kimsesiz sohbet")
    cid = _chat(db)
    monkeypatch.setattr(db, "get_side_of", lambda c: 1 if c == cid else None)
    with pytest.raises(ValueError):
        turn_runner.start_background_turn(cid, "yan sohbete")
    assert _Runner.messages == [] and not turn_runner.busy(cid)


async def test_shutdown_stops_background_turns_and_keeps_their_inputs(db, router):
    cid = _chat(db)
    _Runner.hold = asyncio.Event()
    _Runner.script = ["hold", ("response", {"content": "C"}), DONE]
    turn_runner.start_background_turn(cid, "A", origin="user")
    await asyncio.sleep(0.02)
    turn_runner.start_background_turn(cid, "B")
    await turn_runner.shutdown()
    await asyncio.sleep(0.02)
    assert not turn_runner.busy(cid) and not turn_runner._TASKS and _Runner.running == 0
    uid = db.get_conversation_messages(cid)[0]["id"]
    assert turn_replies.get(cid, uid)["state"] == "ended"
    # B is stored and still pending: the chat's next turn answers it.
    assert db.count_turn_inputs(cid) == 1


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
