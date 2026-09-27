"""A note that asks something gets its answer back to the sender.

Measured 27 Sep 2026: chat #113 asked #111 for a colour; #111 wrote
"Turkuaz" only in its own chat, which #113 cannot see, and never called the
send tool. #113 kept re-sending; #111's consecutive-wake counter was already
full of those depth-1 notes, so the next one stayed queued
(`wake_chain_exhausted`).

Owner decision (Burak, 27 Sep 2026): a note sent from a turn the user started
(depth 1) always expects a reply; if the receiver's woken turn ends without
having sent anything to that sender, its last message is forwarded to it,
marked as auto-forwarded. Under test: the framing says the sender cannot see
the receiver's chat, `expects_reply`, the forward and every case that must
not forward, and the chain counter.
"""
import asyncio

import pytest

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import approval_mode, mailbox, wake_queue
from agentic.approval_policy import ambient_turn
from schemas import ChatRequest
from tests.test_mailbox import H, _chat, _route, _rows, _send, _wake_turn, auto, env  # noqa: F401


# ── a scripted runner per chat ───────────────────────────────────────────────

def _ev(kind, **data):
    return ar.AgentEvent(kind, data)


DONE = _ev("done", iterations=1, stop_reason="complete")


class _Runner:
    """Plays `scripts[conversation_id]`: a list of events, or a callable
    (kw, message) -> async generator of events."""
    scripts = {}
    turns = []

    def __init__(self, **kw):
        self.kw = kw

    async def run(self, message):
        cid = self.kw["conversation_id"]
        _Runner.turns.append((cid, message, self.kw))
        script = _Runner.scripts.get(cid, [_ev("response", content="tamam"), DONE])
        if callable(script):
            async for event in script(self.kw, message):
                yield event
        else:
            for event in script:
                yield event


@pytest.fixture
def runner(monkeypatch):
    _Runner.scripts = {}
    _Runner.turns = []
    monkeypatch.setattr(cr, "AgentRunner", _Runner)
    return _Runner


def _answers(text):
    return [_ev("text", content=text), _ev("response", content=text), DONE]


def _wake(env, conv_id):
    wake_queue.issue_ticket(conv_id, wake_queue.drain(conv_id))
    r = _wake_turn(env.client, conv_id)
    assert r.status_code == 200
    return r


def _mail(env, sql_where="1=1", args=()):
    return _rows(env.db, "SELECT from_conv, to_conv, body, status, depth, expects_reply, "
                         f"auto_forwarded FROM mailbox WHERE {sql_where} ORDER BY id", args)


def _asked(env, a, b, body="hangi renk?"):
    """A user-started turn of `a` asks `b` (depth 1), and `b` is woken."""
    assert _send(env.client, a, b, body=body).status_code == 200


# ── framing ──────────────────────────────────────────────────────────────────

_ROWS = [{"from_conv": 113, "from_title": "Renk", "body": "Bir renk seç."}]


@pytest.mark.parametrize("provider_type,model,tool", [
    ("subscription", "claude-opus-5", "`mcp__gamachineMail__send_chat_message`"),
    ("subscription", "opencode:opencode-go/kimi-k3", "`unityai_send_chat_message`"),
    ("subscription", "gemini-3-pro", "`call_mcp_tool` (server `unityai`, tool `send_chat_message`"),
    ("subscription", "gpt-5.4", "`send_chat_message` on the `unityai` MCP server"),
    ("anthropic", "claude-sonnet-4-6", "`send_chat_message`"),
])
def test_the_wake_says_the_sender_cannot_see_this_chat_for_every_provider(provider_type, model, tool):
    text = mailbox.turn_text(_ROWS, (), provider_type, model)
    assert "#113 sohbeti bu sohbette yazdıklarını GÖREMEZ" in text
    assert "cevabın oraya YALNIZCA böyle ulaşır" in text
    assert (f"Chat #113 CANNOT see what you write in this chat: if the note asks a question "
            f"or asks for something, your answer reaches #113 ONLY if you send it with {tool}") in text
    # Not conditional on the note's wording any more.
    assert "if a reply is requested" not in text and "Cevap isteniyorsa" not in text


def test_the_history_header_says_the_sender_cannot_see_this_chat():
    assert "GÖREMEZ" in mailbox.MAIL_WAKE_HISTORY_HEADER
    assert "CANNOT see this chat" in mailbox.MAIL_WAKE_HISTORY_HEADER
    assert "kaldığın yerden devam et" not in mailbox.MAIL_WAKE_HISTORY_HEADER


def test_every_send_tool_description_carries_the_reply_rule():
    from tools.tool_registry import TOOL_DEFINITIONS
    from unity_ai_mcp.mail_server import create_server as mail_server
    from unity_ai_mcp.server import create_server as unityai_server

    api = next(t for t in TOOL_DEFINITIONS if t["name"] == mailbox.TOOL_SEND)
    assert mailbox.SEND_TOOL_REPLY_RULE in api["description"]

    async def description(server):
        tools = await server.list_tools()
        return next(t.description for t in tools if t.name == mailbox.TOOL_SEND)

    # mailbox_tools imports nothing from agentic, so its copy is held equal here.
    for server in (mail_server(), unityai_server(".")):
        assert mailbox.SEND_TOOL_REPLY_RULE in " ".join(asyncio.run(description(server)).split())
    assert "GÖREMEZ" in mail_server().instructions


# ── expects_reply ────────────────────────────────────────────────────────────

def test_only_a_note_from_a_user_started_turn_expects_a_reply(env, auto):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, depth=0).status_code == 200
    assert _send(env.client, a, b, depth=1).status_code == 200
    assert _mail(env) == [(a, b, "merhaba", "queued", 1, 1, 0),
                          (a, b, "merhaba", "queued", 2, 0, 0)]


def test_a_note_waiting_on_its_card_keeps_expects_reply(env):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    data = _send(env.client, a, b).json()
    assert data["status"] == "pending"
    assert _rows(env.db, "SELECT depth, expects_reply FROM mailbox") == [(1, 1)]
    env.client.post(f"/mcp-approval-respond/{data['gate_id']}", json={"approved": False}, headers=H)


# ── the forward ──────────────────────────────────────────────────────────────

def test_a_receiver_that_answers_only_in_its_own_chat_is_forwarded(env, auto, runner):
    a, b = _chat(env.db, "Soran"), _chat(env.db, "Cevaplayan")
    _asked(env, a, b)
    runner.scripts[b] = _answers("Turkuaz")
    _wake(env, b)

    assert _mail(env, "from_conv = ?", (b,)) == [(b, a, "Turkuaz", "queued", 2, 0, 1)]
    assert mailbox.is_mail_notice(wake_queue.drain(a)[0])
    # B's own chat keeps its answer.
    assert [m["content"] for m in env.db.get_conversation_messages(b)
            if m["role"] == "assistant"] == ["Turkuaz"]


@pytest.mark.parametrize("status", [mailbox.STATUS_DELIVERED, mailbox.STATUS_QUEUED,
                                    mailbox.STATUS_PENDING, mailbox.STATUS_REJECTED])
def test_a_receiver_that_wrote_to_the_sender_in_any_status_is_not_forwarded(env, auto, runner, status):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)

    async def sends_itself(kw, message):
        # A rejected card counts too: the forward must not go around it.
        env.db.add_mail(b, a, "kendi cevabım", status, None, 2)
        for event in _answers("Turkuaz, gönderdim"):
            yield event

    runner.scripts[b] = sends_itself
    _wake(env, b)
    assert _mail(env, "from_conv = ?", (b,)) == [(b, a, "kendi cevabım", status, 2, 0, 0)]


@pytest.mark.parametrize("script", [
    [_ev("text", content="yarım"), _ev("done", iterations=1, stop_reason="cancelled",
                                        stop_message="⏹ Tur durduruldu.")],
    [_ev("text", content="yarım"), _ev("done", iterations=3, stop_reason="max_iterations",
                                        stop_message="adım sayısı doldu")],
    [_ev("text", content="yarım"), _ev("error", message="boom")],
    [_ev("response", content=""), DONE],
    [_ev("text", content="   "), DONE],
], ids=["stopped", "cut-short", "error", "empty", "blank"])
def test_a_turn_that_did_not_end_normally_or_said_nothing_is_not_forwarded(env, auto, runner, script):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)
    runner.scripts[b] = script
    _wake(env, b)
    assert _mail(env, "from_conv = ?", (b,)) == []
    assert wake_queue.pending(a) == 0


def test_stop_pressed_during_the_turn_forwards_nothing(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)
    stop = _route(env.router, "/chat-stop/{conversation_id}").endpoint

    async def stopped(kw, message):
        yield _ev("text", content="Turk")
        await stop(conversation_id=b, x_session_token="")
        # A provider that still reports `complete` after an interrupt.
        yield DONE

    runner.scripts[b] = stopped
    _wake(env, b)
    assert _mail(env, "from_conv = ?", (b,)) == []


def test_step_mode_forwards_nothing(env, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    # Queued as if its card had been approved.
    env.db.add_mail(a, b, "hangi renk?", mailbox.STATUS_QUEUED, None, 1, expects_reply=True)
    wake_queue.enqueue(b, mailbox.notice(a))
    runner.scripts[b] = _answers("Turkuaz")
    _wake(env, b)
    assert _mail(env, "from_conv = ?", (b,)) == []
    # No card either: a forward never raises one.
    assert env.client.get("/mcp-pending", headers=H).json()["pending"] == {}


def test_a_reply_note_is_never_answered_automatically(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, depth=1).status_code == 200  # depth 2
    runner.scripts[b] = _answers("teşekkürler")
    _wake(env, b)
    assert _mail(env, "from_conv = ?", (b,)) == []


def test_only_the_last_assistant_message_is_forwarded(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)
    runner.scripts[b] = [
        _ev("text", content="Önce paleti okuyayım."),
        _ev("tool_call", tool="read_file", arguments={}),
        _ev("tool_result", tool="read_file", success=True),
        _ev("text", content="Turkuaz"),
        _ev("response", content="Önce paleti okuyayım.Turkuaz"),
        DONE,
    ]
    _wake(env, b)
    assert _rows(env.db, "SELECT body FROM mailbox WHERE from_conv = ?", (b,)) == [("Turkuaz",)]


def test_a_provider_without_streamed_text_forwards_its_response(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)
    runner.scripts[b] = [_ev("response", content="Turkuaz"), DONE]
    _wake(env, b)
    assert _rows(env.db, "SELECT body FROM mailbox WHERE from_conv = ?", (b,)) == [("Turkuaz",)]


def test_a_long_answer_is_capped_to_the_note_limit(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)
    runner.scripts[b] = _answers("x" * (mailbox.MAX_BODY_CHARS + 500))
    _wake(env, b)
    (body,), = _rows(env.db, "SELECT body FROM mailbox WHERE from_conv = ?", (b,))
    assert len(body) == mailbox.MAX_BODY_CHARS and body.endswith("…")


def test_the_forward_obeys_the_pair_limit(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)
    for _ in range(mailbox.PAIR_LIMIT):
        # Written before B's turn: not "B answered during the turn".
        env.db.add_mail(b, a, "eski", mailbox.STATUS_DELIVERED, None, 1)
    runner.scripts[b] = _answers("Turkuaz")
    _wake(env, b)
    assert _rows(env.db, "SELECT COUNT(*) FROM mailbox WHERE from_conv = ? AND body = 'Turkuaz'",
                 (b,)) == [(0,)]


def test_a_deleted_sender_gets_nothing_and_breaks_nothing(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)

    async def sender_goes(kw, message):
        env.db.delete_conversation_family_and_sides(a)
        for event in _answers("Turkuaz"):
            yield event

    runner.scripts[b] = sender_goes
    r = _wake(env, b)
    assert '"type": "error"' not in r.text
    assert _mail(env, "from_conv = ?", (b,)) == []


def test_two_senders_owed_skips_the_forward(env, auto, runner):
    a, c, b = _chat(env.db, "A"), _chat(env.db, "C"), _chat(env.db, "B")
    _asked(env, a, b)
    _asked(env, c, b)
    runner.scripts[b] = _answers("Turkuaz ve 42")
    _wake(env, b)
    assert _mail(env, "from_conv = ?", (b,)) == []


def test_two_senders_one_answered_forwards_to_the_other(env, auto, runner):
    a, c, b = _chat(env.db, "A"), _chat(env.db, "C"), _chat(env.db, "B")
    _asked(env, a, b)
    _asked(env, c, b)

    async def answers_a(kw, message):
        env.db.add_mail(b, a, "A'ya cevap", mailbox.STATUS_QUEUED, None, 2)
        for event in _answers("42"):
            yield event

    runner.scripts[b] = answers_a
    _wake(env, b)
    assert _rows(env.db, "SELECT to_conv, body, auto_forwarded FROM mailbox WHERE from_conv = ? "
                         "ORDER BY id", (b,)) == [(a, "A'ya cevap", 0), (c, "42", 1)]


def test_the_forwarded_note_is_marked_and_wakes_the_sender(env, auto, runner):
    a, b = _chat(env.db, "Soran"), _chat(env.db, "Cevaplayan")
    _asked(env, a, b)
    runner.scripts[b] = _answers("Turkuaz")
    _wake(env, b)
    runner.scripts[a] = _answers("Tamam, turkuaz.")
    r = _wake(env, a)

    note = [m["content"] for m in env.db.get_conversation_messages(a) if m["role"] == "system"]
    assert note == [f'{mailbox.MAIL_MARKER} #{b} {mailbox.AUTO_FORWARD_TAG} "Cevaplayan": Turkuaz']
    assert '"type": "wake_message"' in r.text and mailbox.AUTO_FORWARD_TAG in r.text
    cid, turn, kw = runner.turns[-1]
    assert cid == a and "[OTOMATİK İLETİLDİ]" in turn and "Turkuaz" in turn
    # A reply: A's turn runs at depth 2, and owes nothing back.
    assert kw["mail_depth"] == 2
    assert _mail(env, "from_conv = ? AND to_conv = ?", (a, b)) == [
        (a, b, "hangi renk?", "delivered", 1, 1, 0)]


# ── a Claude turn whose stream went away ─────────────────────────────────────

class _FakeClaudeSession:
    def __init__(self, last, cancelled=False):
        self.last_reply_text = last
        self._cancel_requested = cancelled


def _hanging(kw, message):
    async def gen():
        yield _ev("text", content="çalışıyorum")
        await asyncio.Event().wait()
    return gen()


def _start_and_drop_stream(env, conv_id):
    """Start `conv_id`'s wake turn and close its stream mid-turn."""
    wake_queue.issue_ticket(conv_id, wake_queue.drain(conv_id))
    endpoint = _route(env.router, "/chat-stream").endpoint
    req = ChatRequest(conversation_id=conv_id, message="x", user_id=1, origin="wake")

    async def run():
        resp = await endpoint(request=req, x_session_token="")
        it = resp.body_iterator
        for _ in range(3):
            await asyncio.wait_for(it.__anext__(), 3.0)
        await it.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("cancelled,forwarded", [(False, True), (True, False)])
def test_a_detached_claude_turn_forwards_through_the_db_saver(env, auto, runner, monkeypatch,
                                                            cancelled, forwarded):
    from providers import claude_sdk_session
    env.db.save_ai_config(1, "subscription", "claude-opus-5", "")
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)
    runner.scripts[b] = _hanging
    _start_and_drop_stream(env, b)
    assert _mail(env, "from_conv = ?", (b,)) == []

    monkeypatch.setitem(claude_sdk_session._SESSIONS, b, _FakeClaudeSession("Turkuaz", cancelled))
    claude_sdk_session._DB_SAVE_CB(b, "çalışıyorumTurkuaz", "claude-opus-5")
    assert [m["content"] for m in env.db.get_conversation_messages(b)
            if m["role"] == "assistant"] == ["çalışıyorumTurkuaz"]
    expected = [(b, a, "Turkuaz", "queued", 2, 0, 1)] if forwarded else []
    assert _mail(env, "from_conv = ?", (b,)) == expected


def test_a_detached_non_claude_turn_owes_nothing_later(env, auto, runner, monkeypatch):
    from providers import claude_sdk_session
    a, b = _chat(env.db, "A"), _chat(env.db, "B")  # gpt-5.4 (Codex) in the fixture
    _asked(env, a, b)
    runner.scripts[b] = _hanging
    _start_and_drop_stream(env, b)
    monkeypatch.setitem(claude_sdk_session._SESSIONS, b, _FakeClaudeSession("Turkuaz"))
    claude_sdk_session._DB_SAVE_CB(b, "Turkuaz", "claude-opus-5")
    assert _mail(env, "from_conv = ?", (b,)) == []


def test_a_new_turn_drops_a_reply_an_earlier_turn_still_owed(env, auto, runner, monkeypatch):
    from providers import claude_sdk_session
    env.db.save_ai_config(1, "subscription", "claude-opus-5", "")
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)
    runner.scripts[b] = _hanging
    _start_and_drop_stream(env, b)
    runner.scripts[b] = _answers("başka konu")
    r = env.client.post("/chat-stream", headers=H, json={
        "conversation_id": b, "message": "kullanıcı yazdı", "user_id": 1})
    assert r.status_code == 200
    monkeypatch.setitem(claude_sdk_session._SESSIONS, b, _FakeClaudeSession("Turkuaz"))
    claude_sdk_session._DB_SAVE_CB(b, "Turkuaz", "claude-opus-5")
    assert _mail(env, "from_conv = ?", (b,)) == []


# ── the consecutive-wake counter ─────────────────────────────────────────────

def test_notes_from_user_started_turns_do_not_count_as_consecutive_wakes(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    for i in range(wake_queue.MAX_CHAIN + 1):
        _asked(env, a, b, body=f"hatırlatma {i}")
        r = _wake(env, b)
        assert "wake_chain_exhausted" not in r.text
        assert wake_queue.chain(b) == 0
    assert sum(1 for cid, _t, _k in runner.turns if cid == b) == wake_queue.MAX_CHAIN + 1


def test_reply_notes_still_count(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    assert _send(env.client, a, b, depth=1).status_code == 200  # depth 2
    _wake(env, b)
    assert wake_queue.chain(b) == 1


def test_a_depth_1_note_riding_with_a_task_notice_still_counts(env, auto, runner):
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)
    wake_queue.enqueue(b, "tasks_done|build")
    _wake(env, b)
    assert wake_queue.chain(b) == 1


def test_a_failed_claim_still_counts(env, auto, runner):
    import sqlite3
    a, b = _chat(env.db, "A"), _chat(env.db, "B")
    _asked(env, a, b)

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    env.db.claim_queued_mail = locked
    try:
        r = _wake(env, b)
    finally:
        del env.db.claim_queued_mail
    assert "mail_claim_failed" in r.text
    assert wake_queue.chain(b) == 1


# ── the measured case, end to end ────────────────────────────────────────────

def test_113_asks_111_for_a_colour_and_gets_the_answer(env, auto, runner):
    """#113 (the user is talking to it) asks #111; #111 answers only in its
    own chat. The answer reaches #113 and wakes it; #113 does not need to
    re-send, and #111's counter is untouched."""
    c111, c113 = _chat(env.db, "Tasarım"), _chat(env.db, "Renk seçimi")
    service = mailbox.get_service()

    async def asks(kw, message):
        with ambient_turn(".", "auto", c113, 0):
            res = await service.send_and_wait(
                c113, c111, "Bir renk seç ve yalnızca seçtiğin rengin adını yaz.")
        assert res["status"] == mailbox.STATUS_QUEUED
        for event in _answers("#111'e sordum."):
            yield event

    runner.scripts[c113] = asks
    runner.scripts[c111] = _answers("Turkuaz")
    r = env.client.post("/chat-stream", headers=H, json={
        "conversation_id": c113, "message": "@111'e bir renk sor", "user_id": 1})
    assert r.status_code == 200

    _wake(env, c111)
    assert wake_queue.chain(c111) == 0
    runner.scripts[c113] = _answers("#111 turkuaz seçti.")
    _wake(env, c113)

    cid, turn, _kw = runner.turns[-1]
    assert cid == c113 and "Turkuaz" in turn and "[OTOMATİK İLETİLDİ]" in turn
    assert _mail(env) == [
        (c113, c111, "Bir renk seç ve yalnızca seçtiğin rengin adını yaz.", "delivered", 1, 1, 0),
        (c111, c113, "Turkuaz", "delivered", 2, 0, 1),
    ]
    # #113's answer to a reply is not forwarded back: no third row, no loop.
    assert wake_queue.pending(c111) == 0
    assert approval_mode.current_mode() == "auto"
