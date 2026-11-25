"""4.0.3: the backend knows two approval modes, auto and balanced (Safe Auto).

Owner decision (Burak, 24 Nov 2026): the "step" mode (every write asked) is
removed. Every fallback that was step is balanced; a saved "step" is read as
balanced and rewritten once; a "step" an old phone or desktop sends is applied
as balanced; a process without the UI secret is pinned to balanced.
"""
import logging
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode

UI_SECRET = "two-modes-secret"
DELETE = {"kind": "file_delete", "paths": ["Assets/X.cs"]}
MAIL = {"kind": "mail"}


class _Store:
    def __init__(self, value=None, *, failing_write=False):
        self.rows = {} if value is None else {"approval_mode": value}
        self.writes = []
        self.failing_write = failing_write

    def get_setting(self, key):
        return self.rows.get(key)

    def set_setting(self, key, value):
        if self.failing_write:
            raise OSError("database is locked")
        self.writes.append((key, value))
        self.rows[key] = value


def _client(db=None):
    from routes.conversation_routes import create_conversation_router

    app = FastAPI()
    app.include_router(create_conversation_router(db or MagicMock(), {}))
    return TestClient(app)


# ── 1. two modes, every fallback balanced ───────────────────────────────────

def test_the_backend_knows_exactly_two_modes():
    assert approval_mode.MODES == ("auto", "balanced")
    assert approval_mode.FALLBACK_MODE == approval_mode.FRESH_INSTALL_MODE == "balanced"


def test_before_the_store_is_read_the_mode_is_balanced():
    approval_mode._reset_for_tests()
    assert approval_mode.current_mode() == "balanced"
    assert approval_mode.is_stored() is False


@pytest.mark.parametrize("secret", ["", UI_SECRET])
def test_an_unreadable_store_reads_as_balanced(secret):
    store = MagicMock()
    store.get_setting.side_effect = OSError("database is locked")
    approval_mode.set_ui_secret(secret)
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "balanced"
    store.set_setting.assert_not_called()


@pytest.mark.parametrize("value", ["plan", "Step", "STEP", " step", "auto ", 3, ["auto"]])
def test_a_tampered_row_reads_as_balanced_and_is_left_alone(value):
    store = _Store(value)
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "balanced"
    assert store.writes == []


@pytest.mark.parametrize("unknown", ["step", "plan", "", None, 7])
def test_needs_card_with_an_unknown_mode_asks_the_classifier(unknown):
    # Never "no card" for a mode it does not know, never a card the
    # classifier calls routine either: exactly like balanced.
    assert approval_mode.needs_card(MAIL, mode=unknown) == approval_mode.needs_card(MAIL, mode="balanced")
    assert approval_mode.needs_card(MAIL, mode=unknown).card is False
    decision = approval_mode.needs_card(DELETE, mode=unknown)
    assert decision.card is True and decision.mode == "balanced"
    assert decision.reason == "file_delete"
    assert approval_mode.needs_card({"kind": "no_such_kind"}, mode=unknown).card is True
    assert approval_mode.needs_card("not an action", mode=unknown).card is True


@pytest.mark.parametrize("unknown", ["step", "plan", None])
def test_needs_card_many_with_an_unknown_mode_follows(unknown):
    routine = [{"kind": "file_write", "paths": ["a.txt"], "workspace": "C:\\ws"}]
    assert approval_mode.needs_card_many(routine, mode=unknown) == \
        approval_mode.needs_card_many(routine, mode="balanced")
    assert approval_mode.needs_card_many([DELETE], mode=unknown).card is True
    assert approval_mode.needs_card_many([], mode=unknown).card is True
    assert approval_mode.needs_card_many("garbage", mode=unknown).card is True
    assert approval_mode.needs_card_many([DELETE], mode="auto").card is False


def test_set_mode_refuses_what_it_does_not_know():
    for bad in ("plan", "", None, "Balanced"):
        with pytest.raises(ValueError):
            approval_mode.set_mode(bad)
    assert approval_mode.current_mode() == "balanced"


def test_normalize_mode():
    assert [approval_mode.normalize_mode(v) for v in ("auto", "balanced", "step")] == \
        ["auto", "balanced", "balanced"]
    for bad in ("plan", "Step", "", None, 1, ["auto"]):
        assert approval_mode.normalize_mode(bad) is None


# ── 2. migration of the removed mode ────────────────────────────────────────

def test_a_saved_step_is_read_as_balanced_and_rewritten_once():
    store = _Store("step")
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "balanced"
    assert approval_mode.is_stored() is True
    assert store.writes == [("approval_mode", "balanced")]
    # The next launch finds balanced and writes nothing.
    approval_mode._reset_for_tests()
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "balanced"
    assert store.writes == [("approval_mode", "balanced")]


def test_a_failed_migration_write_is_logged_and_still_reads_as_balanced(caplog):
    store = _Store("step", failing_write=True)
    approval_mode.set_ui_secret(UI_SECRET)
    with caplog.at_level(logging.ERROR, logger="agentic.approval_mode"):
        approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "balanced"
    assert store.rows == {"approval_mode": "step"}
    assert any("could not be rewritten as balanced" in r.getMessage() for r in caplog.records)


def test_a_saved_step_migrates_on_a_real_database(tmp_path):
    from database import DatabaseManager

    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.set_setting("approval_mode", "step")
    approval_mode.bind_store(db)
    assert approval_mode.current_mode() == "balanced"
    assert db.get_setting("approval_mode") == "balanced"


def test_the_route_applies_a_legacy_step_as_balanced():
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.set_mode("auto", source="test")
    with _client() as client:
        resp = client.post("/approval-mode", json={"mode": "step", "source": "settings"},
                           headers={"X-Gamachine-UI-Secret": UI_SECRET})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"mode": "balanced", "previous": "auto", "approved_pending": 0}
        assert client.get("/approval-mode").json()["mode"] == "balanced"
    assert approval_mode.current_mode() == "balanced"


def test_apply_approval_mode_reports_balanced_for_a_legacy_step():
    from routes.conversation_routes import create_conversation_router

    router = create_conversation_router(MagicMock(), {})
    approval_mode.set_mode("auto", source="test")
    assert router.apply_approval_mode("step", "phone") == {
        "mode": "balanced", "previous": "auto", "approved_pending": 0}
    with pytest.raises(ValueError):
        router.apply_approval_mode("plan", "phone")


def test_set_mode_saves_balanced_for_a_legacy_step():
    store = _Store("auto")
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(store)
    assert approval_mode.set_mode("step", source="test") == "auto"
    assert store.rows == {"approval_mode": "balanced"}
    assert approval_mode.current_mode() == "balanced"


def test_ledger_rows_that_recorded_step_stay_readable(tmp_path):
    from database import DatabaseManager

    db = DatabaseManager(db_path=str(tmp_path / "ledger.db"))
    row = {"at": "2026-11-01 10:00:00", "card_id": "old-1", "conversation_id": 3, "kind": "mcp",
           "tool": "bash", "params_hash": "x", "approval_mode": "step", "decision": "approve",
           "device": "desktop", "outcome": "approved", "reason": None}
    assert db.record_card_resolution(row)
    assert db.flush_ledger()
    approval_mode.bind_store(db)
    [read] = db.get_approval_ledger()
    assert read["card_id"] == "old-1" and read["approval_mode"] == "step"


# ── 3. no UI secret: pinned to balanced ─────────────────────────────────────

@pytest.mark.parametrize("saved", [None, "auto", "balanced", "step", "garbage"])
def test_without_a_ui_secret_every_saved_mode_runs_as_balanced(saved):
    store = _Store(saved)
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "balanced"
    assert approval_mode.is_auto() is False
    # Only the migration of a saved step writes; a saved auto stays for the
    # real app, which has the secret.
    assert store.rows.get("approval_mode") == ("balanced" if saved == "step" else saved)


def test_without_a_ui_secret_the_saved_auto_comes_back_with_the_secret():
    store = _Store("auto")
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "balanced"
    approval_mode.set_ui_secret(UI_SECRET)
    assert approval_mode.current_mode() == "auto"


def test_without_a_ui_secret_mode_writes_are_still_refused():
    approval_mode.bind_store(_Store("auto"))
    with _client() as client:
        for mode in ("auto", "balanced", "step"):
            resp = client.post("/approval-mode", json={"mode": mode},
                               headers={"X-Gamachine-UI-Secret": ""})
            assert resp.status_code == 403
    assert approval_mode.current_mode() == "balanced"


def test_the_logs_name_balanced_for_a_process_without_a_secret(caplog):
    with caplog.at_level(logging.INFO, logger="agentic.approval_mode"):
        approval_mode.bind_store(_Store("auto"))
        approval_mode.current_mode()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "running as balanced" in text
    assert "step" not in text


def test_the_fresh_install_log_names_balanced(caplog):
    with caplog.at_level(logging.INFO, logger="agentic.approval_mode"):
        approval_mode.bind_store(_Store())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "fresh install, balanced" in text and "step" not in text
