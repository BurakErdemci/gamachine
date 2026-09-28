"""Approval ledger (docs/remote-control.md, backend piece 3).

One row per closed card - approve, reject, timeout, Stop, delete, a switch to
auto - with the params only as a sha256. The stats helper is how the project
metric (share of cards that time out while the owner is away) is counted.
"""
import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import approval_mode, cards, turn_events
from agentic.approval_policy import ambient_turn
from agentic.command_gates import (
    APPROVAL_GATES, APPROVAL_RESULTS, QUESTION_GATES, cancel_gate, register_gate, release_gate,
)
import database
from database import DatabaseManager
from rag.memory_manager import memory_manager

H = {"X-Session-Token": ""}
SECRET = "hunter2-SECRET-IN-PARAMS"


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
    db = DatabaseManager(str(tmp_path / "ledger.db"))
    router = cr.create_conversation_router(db, {})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield db, client, tmp_path
    for gid in list(APPROVAL_GATES) + list(QUESTION_GATES):
        release_gate(gid)
    cards.set_ledger(None)
    cards.reset()
    turn_events.RING.reset()


def _rows(db):
    return db.get_approval_ledger()


def _canonical_hash(params):
    return hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _mcp_card(client, gate_id, conv_id, params):
    with ambient_turn(".", "step", conv_id):
        res = client.post("/mcp-approval-request", headers=H, json={
            "gate_id": gate_id, "tool": "bash", "params": params,
            "workspace_path": ".", "conversation_id": conv_id}).json()
    assert res["status"] == "ok", res


# ── Rows per outcome ────────────────────────────────────────────────────────

def test_desktop_approve_writes_one_row(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    params = {"command": "npm publish"}
    register_gate("cmd-1", a, tool="Bash", summary="npm publish", params=params)
    client.post("/command-approval/cmd-1", headers=H, json={"approved": True})
    [row] = _rows(db)
    assert {k: row[k] for k in ("card_id", "conversation_id", "kind", "tool", "approval_mode",
                                "decision", "device", "outcome")} == {
        "card_id": "cmd-1", "conversation_id": a, "kind": "command", "tool": "Bash",
        "approval_mode": "step", "decision": "approve", "device": "desktop",
        "outcome": "approved"}
    assert row["params_hash"] == _canonical_hash(params) and row["at"]


def test_reject_from_a_phone_names_the_phone(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    _mcp_card(client, "mcp-1", a, {"command": "rm -rf Library"})
    cards.answer_card("mcp-1", "reject", device="Burak's iPhone")
    [row] = _rows(db)
    assert (row["kind"], row["decision"], row["device"], row["outcome"]) == (
        "mcp", "reject", "Burak's iPhone", "rejected")


def test_a_lost_race_writes_no_second_row(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    register_gate("cmd-2", a)
    cards.answer_card("cmd-2", "approve", device="iPhone")
    client.post("/command-approval/cmd-2", headers=H, json={"approved": False})
    assert [r["outcome"] for r in _rows(db)] == ["approved"]


def test_question_answer_is_ledgered_as_answered(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    register_gate("q-1", a, kind="question", tool="AskUserQuestion")
    client.post("/question-answer/q-1", headers=H, json={"answers": {"S": "A"}})
    [row] = _rows(db)
    assert (row["kind"], row["decision"], row["outcome"]) == ("question", "answer", "answered")


# ── Timeouts ────────────────────────────────────────────────────────────────

def test_api_loop_card_timeout_is_ledgered_as_timed_out(env, monkeypatch):
    db, _, _ = env
    a = db.create_conversation(1, "A")
    monkeypatch.setattr(ar, "APPROVAL_TIMEOUT_S", 0.05)
    runner = ar.AgentRunner(provider_type="anthropic", api_key="k", model_name="m",
                            workspace_path=".", conversation_id=a)

    async def _drive():
        text = runner._approval_prompt("delete_file", {"file_path": "Assets/a.cs"})
        assert text
        with runner._approval_gate(text) as (_ev, gid):
            decision = await runner._await_approval(gid)
        return decision

    decision = asyncio.run(_drive())
    assert decision.approved is False
    [row] = _rows(db)
    assert (row["tool"], row["device"], row["outcome"]) == ("delete_file", "system", "timed_out")
    assert row["params_hash"] == _canonical_hash({"file_path": "Assets/a.cs"})


def test_claude_card_timeout_is_ledgered_as_timed_out(env, tmp_path):
    from providers.claude_sdk_session import ClaudeSDKSession
    db, _, _ = env
    a = db.create_conversation(1, "A")
    ws = tmp_path / "ws"
    ws.mkdir()
    sess = ClaudeSDKSession(conversation_id=a, cwd=str(ws), approval_timeout=0.05)

    async def _drive():
        sess._out_q = asyncio.Queue()
        return await sess._can_use_tool("Bash", {"command": "git push --force"}, None)

    res = asyncio.run(_drive())
    assert type(res).__name__ == "PermissionResultDeny"
    [row] = _rows(db)
    assert (row["kind"], row["tool"], row["outcome"]) == ("command", "Bash", "timed_out")


def test_codex_card_timeout_is_ledgered_as_timed_out(env, tmp_path):
    from providers.codex_session import CodexSession
    db, _, _ = env
    a = db.create_conversation(1, "A")
    ws = tmp_path / "ws"
    ws.mkdir()
    sess = CodexSession(a, cwd=str(ws), approval_timeout=0.05)

    async def _drive():
        sess._out_q = asyncio.Queue()
        return await sess._resolve_approval("item/commandExecution/requestApproval",
                                            {"command": "git push --force", "itemId": "i1"})

    assert asyncio.run(_drive()) == "decline"
    [row] = _rows(db)
    assert (row["tool"], row["outcome"]) == ("item/commandExecution/requestApproval", "timed_out")


def test_mcp_card_swept_by_ttl_is_ledgered_as_timed_out(env, monkeypatch):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    _mcp_card(client, "mcp-ttl", a, {"command": "rm -rf Library"})
    real_time = cr.time
    monkeypatch.setattr(cr, "time", lambda: real_time() + 200)
    assert "mcp-ttl" not in client.get("/mcp-pending", headers=H).json()["pending"]
    [row] = _rows(db)
    assert (row["kind"], row["outcome"], row["device"]) == ("mcp", "timed_out", "system")


def test_note_card_whose_sender_gave_up_is_ledgered_as_timed_out(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    b = db.create_conversation(1, "B")
    with ambient_turn(".", "step", a, 0):
        sent = client.post("/mailbox/send", headers=H,
                           json={"conversation_id": a, "to": b, "body": "selam"}).json()
    client.post(f"/mailbox/cancel/{sent['mail_id']}", headers=H, json={"conversation_id": a})
    [row] = _rows(db)
    assert (row["kind"], row["outcome"]) == ("mail", "timed_out")


# ── Stop, delete, mode switch ───────────────────────────────────────────────

def test_stop_is_ledgered_as_cancelled(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    _mcp_card(client, "mcp-stop", a, {"command": "rm -rf Library"})
    register_gate("cmd-stop", a)
    client.post(f"/chat-stop/{a}", headers=H)
    cancel_gate("cmd-stop")  # what Claude's / Codex's cancel_turn does per gate
    assert sorted((r["card_id"], r["outcome"]) for r in _rows(db)) == [
        ("cmd-stop", "cancelled"), ("mcp-stop", "cancelled")]


def test_deleting_a_chat_cancels_its_cards_and_keeps_every_ledger_row(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    register_gate("cmd-a", a)
    client.post("/command-approval/cmd-a", headers=H, json={"approved": True})
    release_gate("cmd-a")
    register_gate("cmd-b", a)
    before = _rows(db)
    assert client.delete(f"/conversations/{a}", headers=H).status_code == 200
    after = _rows(db)
    assert after[:len(before)] == before
    assert [(r["card_id"], r["conversation_id"], r["outcome"]) for r in after] == [
        ("cmd-a", a, "approved"), ("cmd-b", a, "cancelled")]


def test_switch_to_auto_is_ledgered_as_a_mode_switch_approval(env):
    db, client, _ = env
    approval_mode.set_ui_secret("ledger-secret")
    a = db.create_conversation(1, "A")
    _mcp_card(client, "mcp-auto", a, {"command": "rm -rf Library"})
    client.post("/approval-mode", json={"mode": "auto", "source": "settings"},
                headers={**H, "X-Gamachine-UI-Secret": "ledger-secret"})
    [row] = _rows(db)
    assert (row["decision"], row["outcome"], row["approval_mode"]) == (
        "mode_switch", "approved", "step")


# ── Params are never stored raw ─────────────────────────────────────────────

def test_params_are_hashed_never_stored(env):
    db, client, tmp_path = env
    a = db.create_conversation(1, "A")
    params = {"command": f"curl -H 'Authorization: {SECRET}' example.invalid"}
    _mcp_card(client, "mcp-secret", a, params)
    register_gate("cmd-secret", a, tool="Bash", summary="x", params={"command": SECRET})
    cards.answer_card("mcp-secret", "approve", device="desktop")
    cancel_gate("cmd-secret")
    rows = _rows(db)
    assert len(rows) == 2
    assert SECRET not in json.dumps(rows)
    assert rows[0]["params_hash"] == _canonical_hash(params)
    with sqlite3.connect(db.db_path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(approval_ledger)")]
    assert "params" not in cols and "summary" not in cols
    assert SECRET.encode() not in (tmp_path / "ledger.db").read_bytes()


# ── Stats helper ────────────────────────────────────────────────────────────

def test_stats_count_per_outcome_and_device_in_a_window(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db = DatabaseManager(str(tmp_path / "stats.db"))
    rows = [("2026-09-01 10:00:00", "desktop", "approved"),
            ("2026-09-01 11:00:00", "system", "timed_out"),
            ("2026-09-02 09:00:00", "system", "timed_out"),
            ("2026-09-02 10:00:00", "iPhone", "approved"),
            ("2026-09-02 11:00:00", "iPhone", "rejected"),
            ("2026-09-03 12:00:00", "system", "cancelled")]
    for i, (at, device, outcome) in enumerate(rows):
        db.record_card_resolution({"at": at, "card_id": f"c{i}", "conversation_id": 1,
                                   "tool": "bash", "device": device, "outcome": outcome})
    everything = db.approval_ledger_stats()
    assert everything["total"] == 6
    assert everything["by_outcome"] == {"approved": 2, "timed_out": 2, "rejected": 1,
                                        "cancelled": 1}
    assert everything["by_device"] == {"desktop": 1, "system": 3, "iPhone": 2}
    assert everything["by_device_outcome"]["iPhone"] == {"approved": 1, "rejected": 1}
    assert everything["timed_out_share"] == pytest.approx(2 / 6)
    day2 = db.approval_ledger_stats("2026-09-02 00:00:00", "2026-09-03 00:00:00")
    assert day2["total"] == 3 and day2["by_outcome"] == {"timed_out": 1, "approved": 1,
                                                         "rejected": 1}
    assert db.approval_ledger_stats("2027-01-01 00:00:00")["timed_out_share"] is None


def test_ledger_table_is_added_to_an_existing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "user_id INTEGER NOT NULL, title TEXT, created_at TEXT NOT NULL, "
                     "updated_at TEXT NOT NULL)")
    DatabaseManager(path)
    db = DatabaseManager(path)  # a second start must not fail on the existing table
    db.record_card_resolution({"card_id": "x", "outcome": "approved"})
    assert db.approval_ledger_stats()["total"] == 1


def test_a_failing_ledger_never_breaks_an_answer(env, monkeypatch):
    db, client, _ = env
    a = db.create_conversation(1, "A")

    def _boom(row):
        raise sqlite3.OperationalError("disk full")

    cards.set_ledger(_boom)
    register_gate("cmd-x", a)
    assert client.post("/command-approval/cmd-x", headers=H,
                       json={"approved": True}).json() == {"status": "ok", "approved": True}


# ── The ledger is off the answer path ───────────────────────────────────────

def test_a_locked_database_does_not_hold_the_answer(env):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    register_gate("cmd-lock", a, tool="Bash", params={"command": "ls"})
    lock = sqlite3.connect(db.db_path)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        res = client.post("/command-approval/cmd-lock", headers=H, json={"approved": True})
        elapsed = time.monotonic() - started
    finally:
        lock.rollback()
        lock.close()
    assert res.json() == {"status": "ok", "approved": True}
    assert APPROVAL_RESULTS["cmd-lock"] is True
    assert elapsed < 1.0
    # The row lands once the lock is gone; exactly one, as before.
    assert db.flush_ledger(10)
    assert [(r["card_id"], r["outcome"]) for r in _rows(db)] == [("cmd-lock", "approved")]


def test_a_full_ledger_queue_drops_the_row_and_keeps_the_answer(env, monkeypatch, caplog):
    db, client, _ = env
    a = db.create_conversation(1, "A")
    writer = database._LedgerWriter()
    monkeypatch.setattr(writer, "_queue", database.queue.Queue(maxsize=1))
    monkeypatch.setattr(database, "LEDGER_WRITER", writer)
    busy, gate = threading.Event(), threading.Event()
    writer.submit(lambda: (busy.set(), gate.wait(5)))
    assert busy.wait(5)  # the thread is inside this row
    assert writer.submit(lambda: None)  # and this one fills the queue
    register_gate("cmd-full", a)
    with caplog.at_level("WARNING", logger="database"):
        res = client.post("/command-approval/cmd-full", headers=H, json={"approved": True})
    assert res.json() == {"status": "ok", "approved": True}
    assert "queue full" in caplog.text
    gate.set()
    assert writer.flush(5)
    assert _rows(db) == []


def test_shutdown_writes_the_queued_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db = DatabaseManager(str(tmp_path / "shutdown.db"))
    for i in range(20):
        assert db.record_card_resolution({"card_id": f"s{i}", "outcome": "approved"})
    assert database.shutdown_ledger_writer(5)
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approval_ledger").fetchone() == (20,)
    # A write after shutdown starts the writer again.
    db.record_card_resolution({"card_id": "after", "outcome": "approved"})
    assert [r["card_id"] for r in db.get_approval_ledger()][-1] == "after"


def test_a_row_accepted_during_a_slow_shutdown_is_still_written():
    # shutdown() timed out behind a slow insert and queued its stop marker; a row
    # accepted after that used to sit behind the marker and never be written.
    writer = database._LedgerWriter()
    busy, release, written = threading.Event(), threading.Event(), threading.Event()
    try:
        assert writer.submit(lambda: (busy.set(), release.wait(5)))
        assert busy.wait(5)
        started = time.monotonic()
        assert writer.shutdown(0.05) is False
        assert time.monotonic() - started < 1.0  # one deadline for flush and join
        assert writer.submit(written.set)
        release.set()
        assert written.wait(5)
        assert writer.flush(5)
    finally:
        release.set()
        writer.shutdown(5)


def test_a_writer_that_stopped_is_restarted_by_the_next_row():
    writer = database._LedgerWriter()
    first, second = threading.Event(), threading.Event()
    assert writer.submit(first.set)
    assert writer.shutdown(5)
    assert first.is_set()
    assert writer._thread is None
    assert writer.submit(second.set)
    assert second.wait(5)
    assert writer.shutdown(5)


@pytest.mark.parametrize("read", ["get_approval_ledger", "approval_ledger_stats"])
def test_a_ledger_read_under_a_held_lock_has_one_deadline(tmp_path, monkeypatch, read):
    # The queue wait and SQLite's lock wait used to stack (~9.4 s measured).
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db = DatabaseManager(str(tmp_path / "deadline.db"))
    writer = database._LedgerWriter()
    monkeypatch.setattr(database, "LEDGER_WRITER", writer)
    monkeypatch.setattr(database, "LEDGER_READ_WAIT_S", 0.5)
    lock = sqlite3.connect(db.db_path)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        assert db.record_card_resolution({"card_id": "queued", "outcome": "approved"})
        started = time.monotonic()
        # A held lock surfaces as an error, not as an empty ledger.
        with pytest.raises(sqlite3.OperationalError, match="locked|not caught up"):
            getattr(db, read)()
        assert time.monotonic() - started < 0.5 + 1.0
    finally:
        lock.rollback()
        lock.close()
    assert writer.flush(10)
    assert [r["card_id"] for r in db.get_approval_ledger()] == ["queued"]
    writer.shutdown(5)


def test_a_ledger_read_the_writer_has_not_caught_up_with_raises(tmp_path, monkeypatch):
    # A RESERVED lock blocks the queued insert but not SELECT; the read used to
    # return 0 rows and total 0, as if no card had closed (Codex, 28 Sep 2026).
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db = DatabaseManager(str(tmp_path / "reserved.db"))
    writer = database._LedgerWriter()
    monkeypatch.setattr(database, "LEDGER_WRITER", writer)
    monkeypatch.setattr(database, "LEDGER_READ_WAIT_S", 0.3)
    lock = sqlite3.connect(db.db_path)
    try:
        lock.execute("BEGIN IMMEDIATE")
        assert db.record_card_resolution({"card_id": "queued", "outcome": "approved"})
        for read in (db.get_approval_ledger, db.approval_ledger_stats):
            with pytest.raises(sqlite3.OperationalError, match="not caught up"):
                read()
    finally:
        lock.rollback()
        lock.close()
    assert writer.flush(10)
    assert [r["card_id"] for r in db.get_approval_ledger()] == ["queued"]
    assert db.approval_ledger_stats()["total"] == 1
    writer.shutdown(5)
