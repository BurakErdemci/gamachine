"""All pending cards and first-answer-wins (docs/remote-control.md, backend piece 2).

The desktop routes and a future phone share one gate (`agentic.cards`): the
first answer resolves a card, a later one gets `already_answered {by, at}`, and
the desktop's own replies stay what the renderer has always read.
"""
import asyncio
import threading
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.conversation_routes as cr
from agentic import approval_mode, cards, turn_events
from agentic.approval_policy import ambient_turn
from agentic.command_gates import (
    APPROVAL_GATES, APPROVAL_RESULTS, QUESTION_GATES, QUESTION_RESULTS,
    cancel_gate, register_gate, release_gate,
)
from database import DatabaseManager
from rag.memory_manager import memory_manager

H = {"X-Session-Token": ""}
UI_SECRET = "ui-secret-for-card-tests"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_manager, "base_dir", mem_dir)
    cards.reset()
    turn_events.RING.reset()
    db = DatabaseManager(str(tmp_path / "cards.db"))
    router = cr.create_conversation_router(db, {})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield db, client, router
    for gid in list(APPROVAL_GATES) + list(QUESTION_GATES):
        release_gate(gid)
    cards.set_ledger(None)
    cards.reset()
    turn_events.RING.reset()


def _endpoint(router, path, method):
    return next(r.endpoint for r in router.routes
                if getattr(r, "path", "") == path and method in getattr(r, "methods", set()))


def _mcp_card(client, gate_id, conv_id, command="rm -rf Library"):
    with ambient_turn(".", "step", conv_id):
        res = client.post("/mcp-approval-request", headers=H, json={
            "gate_id": gate_id, "tool": "bash", "params": {"command": command},
            "workspace_path": ".", "conversation_id": conv_id}).json()
    assert res["status"] == "ok", res
    return gate_id


# ── One list for every kind ─────────────────────────────────────────────────

def test_pending_cards_lists_every_kind_across_chats(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    b = db.create_conversation(1, "B")
    _mcp_card(client, "mcp-1", a, "git push --force")
    with ambient_turn(".", "step", a, 0):
        mail = client.post("/mailbox/send", headers=H,
                           json={"conversation_id": a, "to": b, "body": "selam B"}).json()
    assert mail["status"] == "pending"
    register_gate("cmd-1", b, tool="Bash", summary="npm publish", params={"command": "npm publish"},
                  risk="shell_publish")
    register_gate("q-1", b, kind="question", tool="AskUserQuestion", summary="Hangisi?")

    pending = cards.list_pending()
    by_id = {c["card_id"]: c for c in pending}
    assert set(by_id) == {"mcp-1", mail["gate_id"], "cmd-1", "q-1"}
    for card in pending:
        assert set(card) == {"card_id", "conversation_id", "kind", "tool", "summary", "risk",
                             "created_at"}
    assert (by_id["mcp-1"]["kind"], by_id["mcp-1"]["conversation_id"],
            by_id["mcp-1"]["summary"]) == ("mcp", a, "git push --force")
    assert (by_id[mail["gate_id"]]["kind"], by_id[mail["gate_id"]]["conversation_id"]) == ("mail", a)
    assert (by_id["cmd-1"]["kind"], by_id["cmd-1"]["risk"], by_id["cmd-1"]["tool"]) == (
        "command", "shell_publish", "Bash")
    assert (by_id["q-1"]["kind"], by_id["q-1"]["conversation_id"]) == ("question", b)
    assert [c["card_id"] for c in cards.list_pending(b)] == ["cmd-1", "q-1"]
    opened = [e["card_id"] for e in turn_events.since(a)["events"] if e["kind"] == "card_opened"]
    assert opened == ["mcp-1", mail["gate_id"]]


def test_answer_closes_the_card_and_writes_card_closed(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    _mcp_card(client, "mcp-2", a)
    assert cards.answer_card("mcp-2", "approve", device="iPhone")["status"] == "ok"
    assert cards.list_pending() == []
    closed = [e for e in turn_events.since(a)["events"] if e["kind"] == "card_closed"]
    assert [(e["card_id"], e["decision"], e["by"]) for e in closed] == [("mcp-2", "approve", "iPhone")]
    assert client.get("/mcp-approval-result/mcp-2", headers=H).json() == {
        "status": "resolved", "approved": True}


# ── First answer wins ───────────────────────────────────────────────────────

def test_two_desktop_answers_race_and_the_first_wins(env):
    """Before the shared gate, the second answer overwrote the first while the
    waiter had not yet woken: approve then reject ran as a rejection."""
    db, _, router = env
    a = db.create_conversation(1, "A")
    answer = _endpoint(router, "/command-approval/{gate_id}", "POST")

    async def _drive():
        ev = register_gate("race-1", a)
        waiter = asyncio.create_task(ev.wait())
        replies = await asyncio.gather(
            answer("race-1", {"approved": True}, ""),
            answer("race-1", {"approved": False}, ""))
        await waiter
        result = APPROVAL_RESULTS.get("race-1")
        release_gate("race-1")
        return replies, result

    replies, result = asyncio.run(_drive())
    assert replies[0] == {"status": "ok", "approved": True}
    assert replies[1]["status"] == "already_answered"
    assert result is True


def test_threads_racing_on_one_card_resolve_it_once():
    cards.reset()
    calls = []
    cards.open_card("t-race", conversation_id=None, kind="mcp", tool="bash", summary="x",
                    resolver=calls.append)
    barrier = threading.Barrier(8)
    replies = []

    def _answer(i):
        barrier.wait()
        replies.append(cards.answer_card("t-race", "approve" if i % 2 else "reject",
                                         device=f"dev{i}"))

    threads = [threading.Thread(target=_answer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in replies if r["status"] == "ok"]
    assert len(winners) == 1 and len(calls) == 1
    losers = [r for r in replies if r["status"] == "already_answered"]
    assert len(losers) == 7 and {r["by"] for r in losers} == {winners[0]["by"]}
    cards.reset()


def test_phone_first_then_desktop_gets_already_answered_for_every_kind(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    _mcp_card(client, "mcp-3", a)
    register_gate("cmd-3", a)
    register_gate("q-3", a, kind="question")

    assert cards.answer_card("mcp-3", "reject", device="iPhone")["status"] == "ok"
    assert cards.answer_card("cmd-3", "reject", device="iPhone")["status"] == "ok"
    assert cards.answer_card("q-3", "answer", {"Hangisi?": "A"}, device="iPhone")["status"] == "ok"

    mcp = client.post("/mcp-approval-respond/mcp-3", headers=H, json={"approved": True}).json()
    cmd = client.post("/command-approval/cmd-3", headers=H, json={"approved": True}).json()
    q = client.post("/question-answer/q-3", headers=H, json={"answers": {"Hangisi?": "B"}}).json()
    for reply in (mcp, cmd, q):
        assert reply["status"] == "already_answered" and reply["by"] == "iPhone" and reply["at"]
    assert client.get("/mcp-approval-result/mcp-3", headers=H).json()["approved"] is False
    assert APPROVAL_RESULTS["cmd-3"] is False
    assert QUESTION_RESULTS["q-3"] == {"Hangisi?": "A"}


def test_a_late_desktop_answer_after_release_still_learns_the_phone_won(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    register_gate("cmd-late", a)
    register_gate("q-late", a, kind="question")
    _mcp_card(client, "mcp-late", a)
    cards.answer_card("cmd-late", "reject", device="iPhone")
    cards.answer_card("q-late", "answer", {"S": "A"}, device="iPhone")
    cards.answer_card("mcp-late", "reject", device="iPhone")
    # The waiters woke and cleaned up; the MCP bridge collected its result.
    release_gate("cmd-late")
    release_gate("q-late")
    assert client.get("/mcp-approval-result/mcp-late", headers=H).json()["approved"] is False

    cmd = client.post("/command-approval/cmd-late", headers=H, json={"approved": True}).json()
    q = client.post("/question-answer/q-late", headers=H,
                    json={"answers": {"S": "B"}}).json()
    mcp = client.post("/mcp-approval-respond/mcp-late", headers=H,
                      json={"approved": True}).json()
    assert (cmd["status"], cmd["by"], cmd["decision"]) == ("already_answered", "iPhone", "reject")
    assert (q["status"], q["by"], q["decision"]) == ("already_answered", "iPhone", "answer")
    assert (mcp["status"], mcp["by"], mcp["decision"]) == ("already_answered", "iPhone", "reject")
    assert cmd["at"] and q["at"] and mcp["at"]


def test_a_released_gate_closed_by_the_system_or_desktop_keeps_gate_not_found(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    register_gate("cmd-sys", a)
    cancel_gate("cmd-sys")
    release_gate("cmd-sys")
    register_gate("cmd-own", a)
    client.post("/command-approval/cmd-own", headers=H, json={"approved": True})
    release_gate("cmd-own")
    for gid in ("cmd-sys", "cmd-own"):
        assert client.post(f"/command-approval/{gid}", headers=H,
                           json={"approved": True}).json() == {"status": "gate_not_found"}


def test_a_late_answer_learns_that_stop_closed_the_card(env):
    db, _, _ = env
    a = db.create_conversation(1, "A")
    register_gate("cmd-4", a)
    cancel_gate("cmd-4")
    late = cards.answer_card("cmd-4", "approve", device="iPhone")
    assert (late["status"], late["by"], late["outcome"]) == ("already_answered", "system", "cancelled")
    assert APPROVAL_RESULTS["cmd-4"] is False


def test_stop_and_a_mode_switch_do_not_overwrite_an_answer(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    register_gate("cmd-5", a)
    assert cards.answer_card("cmd-5", "reject", device="iPhone")["status"] == "ok"
    cancel_gate("cmd-5")
    assert APPROVAL_RESULTS["cmd-5"] is False
    register_gate("cmd-6", a)
    assert cards.answer_card("cmd-6", "approve", device="iPhone")["status"] == "ok"
    cancel_gate("cmd-6")
    assert APPROVAL_RESULTS["cmd-6"] is True


def test_invalid_decisions_are_refused_without_closing(env):
    db, _, _ = env
    a = db.create_conversation(1, "A")
    register_gate("cmd-7", a)
    register_gate("q-7", a, kind="question")
    assert cards.answer_card("cmd-7", "maybe")["status"] == "invalid"
    assert cards.answer_card("q-7", "answer", "not a dict")["status"] == "invalid"
    assert {c["card_id"] for c in cards.list_pending()} == {"cmd-7", "q-7"}
    assert cards.answer_card("nope", "approve")["status"] == "not_found"


@pytest.mark.parametrize("card_id", [[], {}, None, 7, "", "x" * (cards.CARD_ID_MAX + 1)],
                         ids=["list", "dict", "none", "int", "empty", "too_long"])
def test_a_malformed_card_id_is_refused_without_raising(env, card_id):
    db, _, _ = env
    register_gate("cmd-m", db.create_conversation(1, "A"))
    assert cards.answer_card(card_id, "approve")["status"] == "invalid"
    assert cards.close_card(card_id, "cancelled") is False
    assert cards.get(card_id) is None and cards.is_open(card_id) is False
    assert [c["card_id"] for c in cards.list_pending()] == ["cmd-m"]


def test_malformed_decision_and_device_are_refused_without_closing(env):
    db, _, _ = env
    register_gate("cmd-d", db.create_conversation(1, "A"))
    assert cards.answer_card("cmd-d", ["approve"])["status"] == "invalid"
    assert cards.answer_card("cmd-d", "approve", device=["phone"])["status"] == "invalid"
    assert cards.is_open("cmd-d")


# ── The desktop sees what it always saw ─────────────────────────────────────

def test_desktop_command_answer_is_unchanged(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    register_gate("cmd-8", a)
    first = client.post("/command-approval/cmd-8", headers=H, json={"approved": True}).json()
    # The renderer resends after an uncertain delivery: same reply, no overwrite.
    again = client.post("/command-approval/cmd-8", headers=H, json={"approved": True}).json()
    assert first == again == {"status": "ok", "approved": True}
    assert APPROVAL_GATES["cmd-8"].is_set() and APPROVAL_RESULTS["cmd-8"] is True
    assert client.post("/command-approval/missing", headers=H,
                       json={"approved": True}).json() == {"status": "gate_not_found"}


def test_desktop_question_answer_is_unchanged(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    register_gate("q-8", a, kind="question")
    assert client.post("/question-answer/q-8", headers=H,
                       json={"answers": {"S": "A"}}).json() == {"status": "ok"}
    assert QUESTION_GATES["q-8"].is_set() and QUESTION_RESULTS["q-8"] == {"S": "A"}
    assert client.post("/question-answer/q-8", headers=H,
                       json={"answers": "x"}).json()["status"] == "invalid"


def test_desktop_mcp_answer_is_unchanged(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    _mcp_card(client, "mcp-8", a)
    assert client.post("/mcp-approval-respond/mcp-8", headers=H,
                       json={"approved": False}).json() == {"status": "ok"}
    # A second desktop answer before the bridge read it: the old reply.
    assert client.post("/mcp-approval-respond/mcp-8", headers=H,
                       json={"approved": True}).json()["status"] == "gate_expired"
    assert client.get("/mcp-approval-result/mcp-8", headers=H).json() == {
        "status": "resolved", "approved": False}
    assert "mcp-8" not in client.get("/mcp-pending", headers=H).json()["pending"]


def test_a_gate_without_a_card_still_answers_the_old_way(env):
    ev = asyncio.Event()
    APPROVAL_GATES["legacy-1"] = ev
    APPROVAL_RESULTS["legacy-1"] = False
    _, client, _ = env
    assert client.post("/command-approval/legacy-1", headers=H,
                       json={"approved": True}).json() == {"status": "ok", "approved": True}
    assert ev.is_set() and APPROVAL_RESULTS["legacy-1"] is True
    release_gate("legacy-1")


def test_switch_to_auto_approves_open_cards_but_not_answered_ones(env):
    db, client, _ = env
    approval_mode.set_ui_secret(UI_SECRET)
    a = db.create_conversation(1, "A")
    register_gate("cmd-9", a)
    register_gate("cmd-10", a)
    assert cards.answer_card("cmd-10", "reject", device="iPhone")["status"] == "ok"
    res = client.post("/approval-mode", json={"mode": "auto", "source": "settings"},
                      headers={**H, "X-Gamachine-UI-Secret": UI_SECRET}).json()
    assert res["approved_pending"] == 1
    assert APPROVAL_RESULTS["cmd-9"] is True and APPROVAL_RESULTS["cmd-10"] is False
    assert cards.get("cmd-9").decision == "mode_switch"
