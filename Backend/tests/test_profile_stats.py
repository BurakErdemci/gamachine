"""Maker profile plan, 2 Oct 2026: persistent counts and local calendar rules."""
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
from agentic import approval_mode, cards, turn_events, wake_queue
from database import DatabaseManager, LedgerNotCaughtUp
import profile_stats as ps
from rag.memory_manager import memory_manager
import routes.conversation_routes as cr
from routes.profile_routes import create_profile_router
from tests.test_terminal_contract import _NormalRunner


NOW = datetime(2026, 10, 2, 12)
H = {"X-Session-Token": ""}


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    memories = tmp_path / "memories"
    memories.mkdir()
    monkeypatch.setattr(memory_manager, "base_dir", memories)
    cards.reset()
    wake_queue.reset_all()
    turn_events.RING.reset()
    manager = DatabaseManager(str(tmp_path / "profile.db"))
    yield manager
    manager.flush_ledger()
    cards.set_ledger(None)
    cards.reset()
    wake_queue.reset_all()
    turn_events.RING.reset()


def _turns(db, at, count=1, provider="claude", model="claude-sonnet-5"):
    if isinstance(at, datetime):
        at = at.strftime(ps.TIME_FORMAT)
    for _ in range(count):
        assert db.record_activity("turn_done", provider=provider, model=model, at=at)


def _ledger(db, at, outcome="approved", device="desktop", count=1):
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.executemany(
            "INSERT INTO approval_ledger (at, card_id, outcome, device) VALUES (?, ?, ?, ?)",
            [(at, f"card-{i}", outcome, device) for i in range(count)])


def _client(db, chat=False):
    app = FastAPI()
    app.include_router(create_profile_router(db))
    if chat:
        app.include_router(cr.create_conversation_router(db, {}))
    return TestClient(app)


def test_activity_schema_filters_order_and_clear(db):
    assert db.record_activity("other", at="2026-09-01 00:00:00", tool="Bash", detail="metric")
    _turns(db, "2026-10-02 09:00:00", 2)
    _turns(db, "2026-10-01 09:00:00")
    rows = db.list_activity(["turn_done"], since="2026-10-02")
    assert len(rows) == 2 and rows[0]["id"] < rows[1]["id"]
    assert set(rows[0]) == {"id", "at", "kind", "conversation_id", "provider", "model", "tool", "detail"}
    assert db.list_activity([]) == []
    assert db.list_activity()[0]["kind"] == "other"
    assert db.record_activity("other")
    datetime.strptime(db.list_activity()[-1]["at"], ps.TIME_FORMAT)
    with closing(sqlite3.connect(db.db_path)) as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(activity_events)")}
    assert "idx_activity_kind_at" in indexes
    assert db.clear_activity() == 5
    assert db.clear_activity() == 0


def test_record_activity_failure_returns_false_and_logs_only_first(db, caplog):
    assert db.record_activity(None) is False
    assert db.record_activity(None) is False
    assert sum("activity row not written" in record.message for record in caplog.records) == 1
    assert db.list_activity() == []


def test_backfill_filters_and_model_fallback_once(db):
    root = db.create_conversation(1, "root")
    db.set_conversation_model(root, "subscription", "claude-sonnet-5")
    db.add_message(root, "user", "request")
    first = db.add_message(root, "assistant", "old answer")
    db.add_message(root, "assistant", "new answer", provider="codex", model="gpt-5.5")
    for prefix in ps.SUMMARY_PREFIXES:
        db.add_message(root, "assistant", prefix + "\nsummary")
    branch = db.create_branch(root)["id"]
    db.add_message(branch, "assistant", "own answer", provider="agy", model="gemini-3.8-flash")
    side = db.create_conversation(1, "side")
    unknown = db.create_conversation(1, "unknown")
    db.add_message(side, "assistant", "side answer")
    db.add_message(unknown, "assistant", "unlabelled")
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("UPDATE conversations SET side_of = ? WHERE id = ?", (root, side))
        conn.execute("UPDATE messages SET timestamp = ? WHERE id = ?",
                     ("2026-05-30 13:45:00", first))
    assert ps.backfill_once(db) == 4
    rows = db.list_activity()
    assert rows[0]["at"] == "2026-05-30 13:45:00"
    assert (rows[0]["conversation_id"], rows[0]["provider"], rows[0]["model"]) == (
        root, "claude", "claude-sonnet-5")
    assert {(row["provider"], row["model"]) for row in rows} == {
        ("claude", "claude-sonnet-5"), ("codex", "gpt-5.5"),
        ("agy", "gemini-3.8-flash"), (None, None)}
    assert all(row["detail"] is None and row["tool"] is None for row in rows)
    db.add_message(root, "assistant", "later")
    assert ps.backfill_once(db) == 0
    assert len(db.list_activity()) == 4


def test_backfill_rolls_back_events_and_flag_on_failure(db):
    conv = db.create_conversation(1, "root")
    db.add_message(conv, "assistant", "one", model="one")
    db.add_message(conv, "assistant", "two", model="two")
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("CREATE TRIGGER refuse_second BEFORE INSERT ON activity_events "
                     "WHEN NEW.model = 'two' BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    with pytest.raises(sqlite3.Error):
        ps.backfill_once(db)
    assert db.list_activity() == []
    assert db.get_setting("profile_backfill_done") is None


def test_empty_shape_and_routes(db):
    with _client(db) as client:
        response = client.get("/profile/stats", headers=H)
        assert response.status_code == 200
        result = response.json()
        assert set(result) == {"range", "since", "ledger_ok", "xp", "level", "level_xp",
                               "level_need", "rank", "counts", "streak", "best_hour",
                               "busiest_weekday", "heatmap", "models", "achievements"}
        assert result["range"] == "all" and result["since"] is None and result["ledger_ok"]
        assert (result["xp"], result["level"], result["level_xp"], result["level_need"], result["rank"]) == (
            0, 1, 0, 100, "rookie")
        assert set(result["counts"]) == {"tasks", "tasks_this_month", "tasks_last_month",
                                         "approved_cards", "rejected_cards", "phone_approvals", "active_days"}
        assert all(value == 0 for value in result["counts"].values())
        assert result["streak"] == {"current": 0, "longest": 0, "longest_end": None}
        assert result["models"] == {"mix": [], "favourite": None}
        assert result["best_hour"] is None and result["busiest_weekday"] is None
        assert result["heatmap"]["days"] == result["heatmap"]["levels"] == [0] * 182
        assert [a["id"] for a in result["achievements"]] == [id_ for id_, _ in ps.ACHIEVEMENTS]
        assert all(a["progress"] == 0 and not a["new"] and not a["unlocked"]
                   and a["unlocked_at"] is None for a in result["achievements"])
        assert client.get("/profile/stats?range=invalid", headers=H).status_code == 400


def test_streak_gap_and_yesterday(db):
    for day in ("2026-09-20", "2026-09-21", "2026-09-22", "2026-09-30", "2026-10-01"):
        _turns(db, day + " 10:00:00", 2)
    result = ps.compute(db, "all", NOW)
    assert result["streak"] == {"current": 2, "longest": 3, "longest_end": "2026-09-22"}
    _turns(db, NOW)
    assert ps.compute(db, "all", NOW)["streak"] == {
        "current": 3, "longest": 3, "longest_end": "2026-10-02"}
    assert ps.compute(db, "all", NOW + timedelta(days=2))["streak"]["current"] == 0


def test_best_hour_threshold_tie_weekday_and_mix(db):
    _turns(db, "2026-10-01 09:00:00", 5, provider="codex")
    _turns(db, "2026-10-02 08:00:00", 4, provider=None)
    assert ps.compute(db, "all", NOW)["best_hour"] is None
    _turns(db, "2026-10-02 08:00:00", provider=None)
    result = ps.compute(db, "all", NOW)
    assert result["best_hour"] == 8
    assert result["busiest_weekday"] == 3
    assert result["counts"]["active_days"] == 2
    assert result["models"]["mix"] == [{"family": "codex", "turns": 5, "share": .5},
                                         {"family": "unknown", "turns": 5, "share": .5}]


def test_heatmap_quartiles_start_and_future(db):
    _turns(db, "2026-04-05 12:00:00", 9)
    for i, count in enumerate((1, 2, 3, 4)):
        _turns(db, datetime(2026, 4, 6 + i, 12), count)
    _turns(db, "2026-10-03 12:00:00", 20)
    result = ps.compute(db, "month", NOW)["heatmap"]
    assert result["start"] == "2026-04-06" and result["today"] == "2026-10-02"
    assert len(result["days"]) == len(result["levels"]) == 182
    assert result["days"][:4] == [1, 2, 3, 4]
    assert result["levels"][:4] == [1, 2, 3, 4]
    assert result["days"][-2:] == result["levels"][-2:] == [0, 0]


def test_favourite_recent_window_threshold_and_tie(db):
    _turns(db, NOW - timedelta(days=31), 20, model="old")
    _turns(db, NOW - timedelta(days=1), 4, model="a")
    assert ps.compute(db, "all", NOW)["models"]["favourite"] is None
    _turns(db, NOW - timedelta(days=1), model="a")
    _turns(db, NOW, 5, provider="codex", model="b")
    assert ps.compute(db, "month", NOW)["models"]["favourite"] == {
        "model": "b", "family": "codex", "turns": 5}
    _turns(db, NOW, model="a", provider="agy")
    assert ps.compute(db, "all", NOW)["models"]["favourite"] == {
        "model": "a", "family": "agy", "turns": 6}


def test_range_month_six_months_calendar_counts(db):
    for at in ("2026-04-01 23:59:59", "2026-04-02 12:00:00",
               "2026-09-30 23:59:59", "2026-10-01 00:00:00"):
        _turns(db, at)
    for range_, count in (("all", 4), ("6m", 3), ("month", 1)):
        result = ps.compute(db, range_, NOW)
        assert result["range"] == range_ and result["counts"]["tasks"] == count
        assert result["counts"]["tasks_this_month"] == result["counts"]["tasks_last_month"] == 1
        assert result["since"] == "2026-04-01" and result["xp"] == 120
    assert ps._months_ago(datetime(2026, 8, 31), 6) == datetime(2026, 2, 28)


def test_ledger_counts_range_and_xp(db):
    _turns(db, NOW, 2)
    _ledger(db, "2026-09-01 09:00:00", count=3)
    _ledger(db, "2026-10-01 09:00:00", count=2)
    _ledger(db, "2026-10-01 09:00:00", device="phone:Burak", count=4)
    _ledger(db, "2026-10-01 09:00:00", outcome="rejected", device="phone:Burak")
    _ledger(db, "2026-10-01 09:00:00", outcome="answered")
    result = ps.compute(db, "month", NOW)
    assert result["ledger_ok"]
    assert result["counts"]["approved_cards"] == 6
    assert result["counts"]["rejected_cards"] == 1
    assert result["counts"]["phone_approvals"] == 4
    assert result["xp"] == 20 + 20 + 9 * 5 + 4 * 5


@pytest.mark.parametrize("error", [LedgerNotCaughtUp("busy"), sqlite3.OperationalError("locked")])
def test_ledger_failure_is_null_and_not_500(db, monkeypatch, error):
    _turns(db, NOW)
    def fail():
        raise error
    monkeypatch.setattr(db, "_ledger_read_connection", fail)
    result = ps.compute(db, "all", NOW)
    assert not result["ledger_ok"] and result["xp"] == 30
    assert all(result["counts"][key] is None
               for key in ("approved_cards", "rejected_cards", "phone_approvals"))
    with _client(db) as client:
        assert client.get("/profile/stats", headers=H).status_code == 200


@pytest.mark.parametrize("xp,level,remainder,need,rank", [
    (95, 1, 95, 100, "rookie"), (100, 2, 0, 200, "rookie"),
    (300, 3, 0, 300, "rookie"), (1000, 5, 0, 500, "prototyper"),
    (4500, 10, 0, 1000, "scene_master"), (10500, 15, 0, 1500, "prefab_wizard"),
    (19000, 20, 0, 2000, "engine_whisperer"),
])
def test_levels_and_ranks(db, xp, level, remainder, need, rank):
    _ledger(db, "2026-10-01 09:00:00", count=xp // 5)
    result = ps.compute(db, "all", NOW)
    assert (result["xp"], result["level"], result["level_xp"], result["level_need"], result["rank"]) == (
        xp, level, remainder, need, rank)


def test_99_xp_boundary_with_read_seam(db, monkeypatch):
    # Persisted XP is a multiple of five; inject 99 to cover the requested boundary.
    counts = {"approved_cards": 19.8, "phone_approvals": 0, "rejected_cards": 0}
    monkeypatch.setattr(ps, "_ledger_counts", lambda *_: (counts, counts))
    result = ps.compute(db, "all", NOW)
    assert (result["xp"], result["level"], result["level_xp"], result["level_need"]) == (99, 1, 99, 100)


def test_all_achievement_progress_and_unlock_seen_once(db):
    for i in range(7):
        _turns(db, (NOW - timedelta(days=i)).replace(hour=2), 8,
               provider=("claude", "codex", "agy")[i % 3])
    _ledger(db, "2026-10-01 09:00:00", count=50)
    _ledger(db, "2026-10-01 09:00:00", device="phone:Burak", count=50)
    result = ps.compute(db, "month", NOW)
    values = {"first_task": 1, "tasks_100": 56, "tasks_1000": 56, "night_owl": 50,
              "streak_7": 7, "pocket": 50, "careful": 100, "polyglot": 3}
    for achievement in result["achievements"]:
        assert set(achievement) == {"id", "goal", "progress", "unlocked", "unlocked_at", "new"}
        assert achievement["progress"] == values[achievement["id"]]
        unlocked = achievement["progress"] == achievement["goal"]
        assert achievement["unlocked"] == achievement["new"] == unlocked
        assert achievement["unlocked_at"] == (NOW.strftime(ps.TIME_FORMAT) if unlocked else None)
    seen = json.loads(db.get_setting("profile_achievements_seen"))
    assert set(seen) == {"first_task", "night_owl", "streak_7", "pocket", "careful", "polyglot"}
    repeated = ps.compute(db, "all", NOW + timedelta(hours=1))
    assert not any(a["new"] for a in repeated["achievements"])
    assert json.loads(db.get_setting("profile_achievements_seen")) == seen
    _turns(db, NOW, 944)
    final = {a["id"]: a for a in ps.compute(db, "all", NOW)["achievements"]}
    assert final["tasks_100"]["progress"] == 100 and final["tasks_100"]["new"]
    assert final["tasks_1000"]["progress"] == 1000 and final["tasks_1000"]["new"]


def test_reset_secret_token_maintenance_and_no_rebackfill(db, monkeypatch):
    conv = db.create_conversation(1, "root")
    db.add_message(conv, "assistant", "answer")
    with _client(db) as client:
        approval_mode.set_ui_secret("profile-secret")
        assert client.get("/profile/stats", headers=H).json()["counts"]["tasks"] == 1
        assert db.get_setting("profile_achievements_seen")
        assert client.post("/profile/reset", headers=H).status_code == 403
        headers = {**H, "X-Gamachine-UI-Secret": "profile-secret"}
        assert client.post("/profile/reset", headers={**headers, "X-UnityAI-Maintenance": "1"}).status_code == 403
        monkeypatch.setenv("LOCAL_APP_TOKEN", "profile-token")
        assert client.get("/profile/stats", headers=H).status_code == 401
        assert client.post("/profile/reset", headers=headers).status_code == 401
        headers["X-Session-Token"] = "profile-token"
        response = client.post("/profile/reset", headers=headers)
        assert response.status_code == 200 and response.json() == {"cleared": 1}
        assert db.list_activity() == [] and db.get_setting("profile_achievements_seen") == "{}"
        assert db.get_setting("profile_backfill_done") == "1"
        assert ps.backfill_once(db) == 0


def test_backfill_error_does_not_crash_router_creation(db, monkeypatch, caplog):
    def fail(_):
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(ps, "backfill_once", fail)
    assert create_profile_router(db).routes
    assert "backfill failed" in caplog.text


@pytest.mark.parametrize("mode", ["normal", "error", "stopped", "wake", "metric_failure", "save_failure", "chat"])
def test_turn_record_real_routes_with_fake_runner(db, monkeypatch, mode):
    class Runner(_NormalRunner):
        async def run(self, message):
            if mode in ("error", "stopped"):
                yield ar.AgentEvent("response", {"content": "partial"})
                yield ar.AgentEvent("error" if mode == "error" else "done",
                                    {"stop_reason": "cancelled"})
            else:
                async for event in super().run(message):
                    yield event
    monkeypatch.setattr(cr, "AgentRunner", Runner)
    conv = db.create_conversation(1, "root")
    db.save_ai_config(1, "subscription", "claude-sonnet-5", "")
    if mode == "wake":
        wake_queue.issue_ticket(conv)
    if mode == "metric_failure":
        with closing(sqlite3.connect(db.db_path)) as conn, conn:
            conn.execute("CREATE TRIGGER refuse_metric BEFORE INSERT ON activity_events "
                         "BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    if mode == "save_failure":
        add_message = db.add_message
        def fail_assistant(conversation_id, role, content, **kwargs):
            if role == "assistant":
                raise sqlite3.OperationalError("locked")
            return add_message(conversation_id, role, content, **kwargs)
        monkeypatch.setattr(db, "add_message", fail_assistant)
    with _client(db, chat=True) as client:
        response = client.post("/chat" if mode == "chat" else "/chat-stream", headers=H,
                               json={"conversation_id": conv, "user_id": 1, "message": "hello",
                                     "origin": "wake" if mode == "wake" else "user"})
    assert response.status_code == 200
    rows = db.list_activity(["turn_done"])
    if mode in ("normal", "chat"):
        assert len(rows) == 1
        assert (rows[0]["conversation_id"], rows[0]["provider"], rows[0]["model"]) == (
            conv, "claude", "claude-sonnet-5")
    else:
        assert rows == []
    if mode == "metric_failure":
        assert "turn_not_saved" not in response.text
        assert any(row["role"] == "assistant" for row in db.get_conversation_messages(conv))
    if mode == "save_failure":
        assert "turn_not_saved" in response.text
