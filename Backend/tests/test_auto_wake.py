"""AUTO-WAKE: the wake queue + the `origin` contract of `/chat-stream`.

This feature has two parts and both can break silently:

  1. `wake_queue` — an in-process, bounded notice queue. If the limit or the
     chain counter breaks, the failure is INVISIBLE: the chat starts looping
     back on itself with no user present, and nobody measures it.
  2. The `origin` branch of `/chat-stream` — a wake turn's message MUST be
     stored with the `system` role. Writing `user` would make a sentence the
     user never typed appear as theirs on screen, and also enter the CLI
     handoff transcript as "USER: ...". The tests therefore check the ROLE
     that gets written, not that the call was made.

The route tests follow the pattern of `test_session_report.py`: the endpoint
function is run DIRECTLY against a fake db — a fake client never actually
runs the server's own lines.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from agentic import wake_queue
from routes.conversation_routes import create_conversation_router
from schemas import ChatRequest


@pytest.fixture(autouse=True)
def _temiz_kuyruk():
    """The queue is a MODULE-LEVEL GLOBAL and outlives a single test.

    Without cleanup, the chain counter left by the previous test wrongly opens
    the next test's "limit reached" branch — what gets measured is the leak,
    not the endpoint.
    """
    wake_queue.reset_all()
    yield
    wake_queue.reset_all()


# ── wake_queue ───────────────────────────────────────────────────────────────

def test_enqueue_then_drain_returns_notice_and_clears_queue():
    wake_queue.enqueue(7, "Task finished: build")
    assert wake_queue.drain(7) == ["Task finished: build"]
    assert wake_queue.drain(7) == []


def test_empty_text_and_invalid_id_are_not_enqueued():
    wake_queue.enqueue(7, "   ")
    wake_queue.enqueue(0, "something")
    assert wake_queue.pending(7) == 0
    assert wake_queue.pending(0) == 0


def test_n_completions_coalesce_into_one_drain():
    """Coalescing contract: N completions -> 1 wake.

    The endpoint merges the list returned by drain into a SINGLE `wake` frame;
    if the queue turned every completion into its own wake, three tasks would
    start three turns.
    """
    for ad in ("a", "b", "c"):
        wake_queue.enqueue(7, ad)
    assert wake_queue.drain(7) == ["a", "b", "c"]


def test_queue_is_bounded_and_drops_the_OLDEST():
    for i in range(wake_queue.MAX_NOTICES + 5):
        wake_queue.enqueue(7, f"n{i}")
    kalan = wake_queue.drain(7)
    assert len(kalan) == wake_queue.MAX_NOTICES
    # The newest completion is what the model needs; the oldest ones are what drops.
    assert kalan[-1] == f"n{wake_queue.MAX_NOTICES + 4}"
    assert kalan[0] == "n5"


def test_chain_counter_exhausts_at_the_limit_and_resets_on_user_message():
    assert wake_queue.chain_exhausted(7) is False
    for _ in range(wake_queue.MAX_CHAIN):
        wake_queue.bump_chain(7)
    assert wake_queue.chain_exhausted(7) is True
    wake_queue.reset_chain(7)
    assert wake_queue.chain_exhausted(7) is False


def test_wait_returns_immediately_when_a_notice_is_pending():
    async def run_it():
        wake_queue.enqueue(7, "ready")
        assert wake_queue.pending(7) == 1
        await asyncio.wait_for(wake_queue.wait(7), timeout=1.0)
        assert wake_queue.drain(7) == ["ready"]

    asyncio.run(run_it())


def test_wait_wakes_up_on_enqueue():
    async def run_it():
        bekle = asyncio.create_task(wake_queue.wait(7))
        await asyncio.sleep(0)
        assert not bekle.done()
        wake_queue.enqueue(7, "late arrival")
        await asyncio.wait_for(bekle, timeout=1.0)
        assert bekle.done()
        assert wake_queue.drain(7) == ["late arrival"]

    asyncio.run(run_it())


def test_reset_wakes_a_waiter_before_removing_its_queue_entry():
    async def run_it():
        waiter = asyncio.create_task(wake_queue.wait(801))
        await asyncio.sleep(0)
        wake_queue.reset(801)
        wake_queue.enqueue(801, "after reset")
        await asyncio.wait_for(waiter, timeout=1.0)

    asyncio.run(run_it())


# ── /chat-stream: origin contract ──────────────────────────────────────────

def _db():
    db = MagicMock()
    db.get_conversation_owner.return_value = 1
    db.get_ai_config.return_value = ("subscription", "claude-opus-5", "", False)
    db.get_api_key.return_value = ""
    db.get_last_workspace.return_value = ""
    db.get_memory.return_value = ""
    db.get_conversation_messages.return_value = []
    db.get_cli_session.return_value = None
    return db


def _chat_stream(db, **alanlar):
    router = create_conversation_router(db, MagicMock())
    route = next(r for r in router.routes if getattr(r, "path", "") == "/chat-stream")
    istek = ChatRequest(conversation_id=1, message="m", user_id=1, **alanlar)
    with patch("routes.conversation_routes.AgentRunner") as runner:
        runner.return_value = MagicMock()
        return asyncio.run(route.endpoint(request=istek, x_session_token="t"))


def test_wake_turn_message_is_stored_with_system_role():
    db = _db()
    wake_queue.issue_ticket(1)
    _chat_stream(db, origin="wake")
    roller = [c.args[1] for c in db.add_message.call_args_list]
    assert roller == ["system"], roller


def test_unticketed_wake_claim_is_stored_as_user_turn():
    db = _db()
    with patch("routes.conversation_routes._check_chat_rate_limit"):
        _chat_stream(db, origin="wake")
    assert db.add_message.call_args_list[0].args[1] == "user"
    assert wake_queue.chain(1) == 0


def test_wake_ticket_cannot_be_consumed_twice():
    wake_queue.issue_ticket(1)
    assert wake_queue.consume_ticket(1) is True
    assert wake_queue.consume_ticket(1) is False


def test_user_turn_is_stored_with_user_role_and_cancels_pending_wake():
    db = _db()
    wake_queue.enqueue(1, "pending notice")
    wake_queue.bump_chain(1)
    _chat_stream(db, origin="user")
    assert db.add_message.call_args_list[0].args[1] == "user"
    # The human is back in the loop: both the pending notice and the counter drop.
    assert wake_queue.pending(1) == 0
    assert wake_queue.chain(1) == 0


def test_origin_defaults_to_user():
    db = _db()
    _chat_stream(db)
    assert db.add_message.call_args_list[0].args[1] == "user"


async def _govde(yanit) -> str:
    """Collapses a StreamingResponse body into one string (str/bytes chunks arrive mixed)."""
    parcalar = []
    async for p in yanit.body_iterator:
        parcalar.append(p if isinstance(p, str) else p.decode("utf-8"))
    return "".join(parcalar)


def test_when_chain_is_exhausted_turn_does_NOT_start_and_notice_frame_is_sent():
    db = _db()
    for _ in range(wake_queue.MAX_CHAIN):
        wake_queue.bump_chain(1)

    wake_queue.issue_ticket(1)
    yanit = _chat_stream(db, origin="wake")

    govde = asyncio.run(_govde(yanit))
    assert "wake_chain_exhausted" in govde
    # Since the turn never starts, the message is ALSO never written: if it
    # were, an unexplained system row would remain in the chat.
    assert db.add_message.call_count == 0


def test_chain_increments_on_every_wake():
    db = _db()
    wake_queue.issue_ticket(1)
    _chat_stream(db, origin="wake")
    assert wake_queue.chain(1) == 1
    wake_queue.issue_ticket(1)
    _chat_stream(db, origin="wake")
    assert wake_queue.chain(1) == 2


# ── CLI handoff: system rows must not enter the transcript ───────────────────

def test_handoff_transcript_SKIPS_system_rows():
    """A wake text must not be carried into a new CLI as "USER: ...".

    This was the reason the "send a ready-made continuation message" idea,
    proposed as a stopgap, was rejected: an instruction that isn't the user's
    enters the history and gets copied forward on later handoffs.
    """
    from routes.conversation_routes import _build_handoff_context
    mesajlar = [
        {"role": "user", "content": "real request"},
        {"role": "system", "content": "Arka plan görevleri tamamlandı: build"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "last message (excluded)"},
    ]
    metin = _build_handoff_context("", mesajlar)
    assert "real request" in metin
    assert "Arka plan görevleri tamamlandı" not in metin


# ── /wake-stream ─────────────────────────────────────────────────────────────

def _wake_stream_route(db):
    router = create_conversation_router(db, MagicMock())
    return next(r for r in router.routes
                if getattr(r, "path", "") == "/conversations/{conv_id}/wake-stream")


def test_wake_stream_coalesces_pending_notices_into_ONE_frame():
    import json

    db = _db()
    route = _wake_stream_route(db)
    wake_queue.enqueue(1, "task A")
    wake_queue.enqueue(1, "task B")

    async def run_it():
        yanit = await route.endpoint(conv_id=1, x_session_token="t")
        return await asyncio.wait_for(_govde(yanit), timeout=5.0)

    with patch("providers.claude_sdk_session.session_busy", return_value=False),          patch("providers.claude_sdk_session.peek_session", return_value=None):
        govde = asyncio.run(run_it())

    satirlar = [l for l in govde.splitlines() if l.startswith("data: ")]
    assert len(satirlar) == 1, govde
    cerceve = json.loads(satirlar[0][6:])
    assert cerceve["type"] == "wake"
    assert cerceve["count"] == 2
    assert cerceve["notices"] == ["task A", "task B"]
    assert "task A" in cerceve["text"] and "task B" in cerceve["text"]
    assert wake_queue.pending(1) == 0


def test_wake_stream_does_NOT_fire_while_approval_pending_and_does_NOT_drop_notice():
    """Waking up while a decision card is on screen would orphan the card.

    The notice STAYING in the queue is the second half of the contract: the
    blocker is transient, so the wake is postponed — not cancelled.
    """
    from agentic.command_gates import APPROVAL_GATES

    db = _db()
    route = _wake_stream_route(db)
    wake_queue.enqueue(1, "task A")
    APPROVAL_GATES["test-gate"] = asyncio.Event()
    try:
        async def run_it():
            yanit = await route.endpoint(conv_id=1, x_session_token="t")
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_govde(yanit), timeout=1.0)

        asyncio.run(run_it())
    finally:
        APPROVAL_GATES.pop("test-gate", None)
    assert wake_queue.pending(1) == 1


def test_a_gate_owned_by_ANOTHER_conversation_does_not_block_this_wake():
    """The blocker must be this conversation's card, not anyone's card.

    The gate id is random in production (`uuid4().hex`), so it carries no
    conversation: the owner comes from GATE_OWNERS. A probe whose id spelled
    out its own conversation would pass without the registry ever being read,
    which is why this test registers the owner separately from the id.
    An UNREGISTERED gate still blocks - the test above covers that direction.
    """
    from agentic.command_gates import APPROVAL_GATES, GATE_OWNERS

    db = _db()
    route = _wake_stream_route(db)
    wake_queue.enqueue(2, "task B")
    foreign = "9f2c41ab7d6e4c0fa1b35e8d90c72461"
    APPROVAL_GATES[foreign] = asyncio.Event()
    GATE_OWNERS[foreign] = 1
    try:
        async def run_it():
            yanit = await route.endpoint(conv_id=2, x_session_token="t")
            return await asyncio.wait_for(_govde(yanit), timeout=2.0)

        govde = asyncio.run(run_it())
    finally:
        APPROVAL_GATES.pop(foreign, None)
        GATE_OWNERS.pop(foreign, None)
    assert '"type": "wake"' in govde
    assert wake_queue.pending(2) == 0


# ── Gate ownership teardown ──────────────────────────────────────────────────
# Both tests below guard the same class as the tests above: a gate that outlives
# the thing that created it keeps blocking AUTO-WAKE, and an unscoped teardown
# resolves cards that belong to somebody else.

def _routes(db):
    router = create_conversation_router(db, MagicMock())
    return {getattr(r, "path", ""): r for r in router.routes}


def test_closing_a_claude_session_releases_its_pending_gate():
    """`close()` used to cancel the reader and leave the card behind.

    The gate and its owner survived the session: the waiter sat out the full
    approval timeout, and the stale owner entry kept blocking wake for the
    conversation it named.
    """
    from agentic.command_gates import APPROVAL_GATES, GATE_OWNERS
    from providers.claude_sdk_session import ClaudeSDKSession

    kapi = {}

    async def run_it():
        session = ClaudeSDKSession(
            conversation_id=4242, cwd=os.getcwd(),
            auto_approve=False, approval_timeout=30.0,
        )
        session._out_q = asyncio.Queue()
        bekleyen = asyncio.create_task(
            session._can_use_tool("Bash", {"command": "echo probe"}, None)
        )
        olay = await asyncio.wait_for(session._out_q.get(), timeout=1.0)
        kapi["id"] = olay["gate_id"]
        assert GATE_OWNERS[kapi["id"]] == 4242
        await session.close()
        # Must return NOW rather than at approval_timeout: close wakes the waiter.
        karar = await asyncio.wait_for(bekleyen, timeout=1.0)
        return (kapi["id"] in APPROVAL_GATES, kapi["id"] in GATE_OWNERS,
                type(karar).__name__)

    try:
        gate_kaldi, sahip_kaldi, karar_tipi = asyncio.run(run_it())
    finally:
        APPROVAL_GATES.pop(kapi.get("id"), None)
        GATE_OWNERS.pop(kapi.get("id"), None)

    assert not gate_kaldi
    assert not sahip_kaldi
    assert karar_tipi == "PermissionResultDeny"


def test_stop_denies_only_the_stopping_conversations_mcp_gates():
    """Stop in one conversation must not resolve another conversation's card.

    Both chats have a turn in flight: a claimed owner is only accepted then
    (test_mcp_approval_owner.py), otherwise the card is unowned.
    """
    from agentic.approval_policy import ambient_turn
    from agentic.command_gates import GATE_OWNERS

    kapilar = ("stop-own", "stop-foreign", "stop-unowned")
    routes = _routes(_db())
    istek = routes["/mcp-approval-request"]
    durdur = routes["/chat-stop/{conversation_id}"]
    sonuc_route = routes["/mcp-approval-result/{gate_id}"]

    async def run_it():
        for gate_id, conv in zip(kapilar, (1, 2, None)):
            govde = {"gate_id": gate_id, "tool": "bash",
                     "params": {"command": "echo x"}, "workspace_path": os.getcwd()}
            if conv is not None:
                govde["conversation_id"] = conv
            assert (await istek.endpoint(body=govde, x_session_token="t"))["status"] == "ok"
        assert (await durdur.endpoint(conversation_id=1, x_session_token="t"))["status"] == "ok"
        return {gid: await sonuc_route.endpoint(gate_id=gid, x_session_token="t")
                for gid in kapilar}

    try:
        with ambient_turn(".", "step", 1), ambient_turn(".", "step", 2):
            sonuc = asyncio.run(run_it())
    finally:
        for gid in kapilar:
            GATE_OWNERS.pop(gid, None)

    assert sonuc["stop-own"] == {"status": "resolved", "approved": False}
    assert sonuc["stop-foreign"] == {"status": "pending"}
    # Unknown owner is denied WITH the stopping conversation, on purpose: several
    # carriers still send no `conversation_id` and an unverifiable claim is
    # stored unowned, so sparing unowned cards would leave Stop unable to
    # release the ones it exists to release.
    assert sonuc["stop-unowned"] == {"status": "resolved", "approved": False}
