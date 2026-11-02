"""The approval ledger records why balanced mode raised a card: the reason code
only, never the command, params or file contents (owner decision, 2 Nov 2026)."""
import sqlite3

import pytest
from cryptography.fernet import Fernet

from agentic import cards
from database import DatabaseManager

OLD_LEDGER = '''CREATE TABLE approval_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    card_id TEXT NOT NULL,
    conversation_id INTEGER,
    kind TEXT,
    tool TEXT,
    params_hash TEXT,
    approval_mode TEXT,
    decision TEXT,
    device TEXT,
    outcome TEXT NOT NULL)'''


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture
def ledger(tmp_path, key):
    cards.reset()
    db = DatabaseManager(str(tmp_path / "ledger.db"))
    cards.set_ledger(db.record_card_resolution)
    yield db
    cards.set_ledger(None)
    cards.reset()


def _columns(path):
    with sqlite3.connect(path) as conn:
        return [r[1] for r in conn.execute("PRAGMA table_info(approval_ledger)")]


def test_an_old_ledger_gains_the_reason_column_and_keeps_its_rows(tmp_path, key):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute(OLD_LEDGER)
        conn.execute("INSERT INTO approval_ledger (at, card_id, outcome) "
                     "VALUES ('2026-10-01 10:00:00', 'old-1', 'approved')")
    assert "reason" not in _columns(path)
    DatabaseManager(path)
    db = DatabaseManager(path)  # a second start must not fail on the existing column
    assert "reason" in _columns(path)
    db.record_card_resolution({"card_id": "new-1", "outcome": "rejected",
                               "reason": "shell_delete_move"})
    rows = {r["card_id"]: r for r in db.get_approval_ledger()}
    assert rows["old-1"]["reason"] is None and rows["old-1"]["outcome"] == "approved"
    assert rows["new-1"]["reason"] == "shell_delete_move"


def test_a_new_database_has_the_reason_column(tmp_path, key):
    path = str(tmp_path / "new.db")
    DatabaseManager(path)
    assert "reason" in _columns(path)


def test_a_balanced_card_records_its_reason_code(ledger):
    secret = "SECRET-COMMAND-TEXT"
    cards.open_card("card-1", conversation_id=1, kind="command", tool="Bash",
                    summary="rm " + secret, risk="shell_delete_move",
                    params={"command": "rm " + secret})
    assert cards.answer_card("card-1", "approve")["status"] == "ok"
    [row] = ledger.get_approval_ledger()
    assert row["reason"] == "shell_delete_move"
    assert secret not in repr(row)


def test_a_step_card_has_no_reason(ledger):
    cards.open_card("card-2", conversation_id=1, kind="command", tool="Bash", summary="npm test")
    assert cards.answer_card("card-2", "reject")["status"] == "ok"
    [row] = ledger.get_approval_ledger()
    assert row["reason"] is None


def test_a_blender_reason_keeps_the_code_without_the_name_from_the_code(ledger):
    cards.open_card("card-3", conversation_id=1, kind="command", tool="blender",
                    risk="blender_code: system module (os.secret_name)")
    assert cards.close_card("card-3", "timed_out")
    [row] = ledger.get_approval_ledger()
    assert row["reason"] == "blender_code: system module"
