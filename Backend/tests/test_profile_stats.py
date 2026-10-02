"""Maker profile plan, 2 Oct 2026: persistent counts and local calendar rules."""
import inspect
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
        assert set(result) == {"range", "since", "ledger_ok", "xp_partial", "xp", "level", "level_xp",
                               "level_need", "rank", "counts", "streak", "best_hour",
                               "busiest_weekday", "heatmap", "models", "achievements"}
        assert result["range"] == "all" and result["since"] is None and result["ledger_ok"]
        assert result["xp_partial"] is False
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
    assert result["xp_partial"] is False
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
    assert result["xp_partial"] is True
    for achievement in result["achievements"]:
        if achievement["id"] in ("careful", "pocket"):
            assert achievement["progress"] is None and achievement["unlocked"] is None
            assert achievement["unlocked_at"] is None and achievement["new"] is False
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
    monkeypatch.setattr(ps, "_ledger_counts", lambda *_: (counts, counts, {}))
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
        earned = {"first_task": "2026-09-26 02:00:00", "night_owl": "2026-10-02 02:00:00",
                  "streak_7": "2026-10-02 00:00:00", "pocket": "2026-10-01 09:00:00",
                  "careful": "2026-10-01 09:00:00", "polyglot": "2026-09-28 02:00:00"}
        assert achievement["unlocked_at"] == (earned[achievement["id"]] if unlocked else None)
    seen = json.loads(db.get_setting("profile_achievements_seen"))
    assert set(seen) == {"first_task", "night_owl", "streak_7", "pocket", "careful", "polyglot"}
    repeated = ps.compute(db, "all", NOW + timedelta(hours=1))
    assert not any(a["new"] for a in repeated["achievements"])
    assert json.loads(db.get_setting("profile_achievements_seen")) == seen
    _turns(db, NOW, 944)
    final = {a["id"]: a for a in ps.compute(db, "all", NOW)["achievements"]}
    assert final["tasks_100"]["progress"] == 100 and final["tasks_100"]["new"]
    assert final["tasks_1000"]["progress"] == 1000 and final["tasks_1000"]["new"]
    assert final["tasks_100"]["unlocked_at"] == final["tasks_1000"]["unlocked_at"] == NOW.strftime(ps.TIME_FORMAT)


def test_ledger_down_keeps_seen_dates_and_does_not_store_unknown_unlocks(db, monkeypatch):
    _ledger(db, "2026-05-01 09:00:00", device="phone:Burak", count=100)
    ps.compute(db, "all", NOW)
    seen_before = db.get_setting("profile_achievements_seen")
    def fail():
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(db, "_ledger_read_connection", fail)
    result = ps.compute(db, "all", NOW + timedelta(days=1))
    assert result["xp_partial"] and result["xp"] == 0
    for achievement in result["achievements"]:
        if achievement["id"] in ("careful", "pocket"):
            assert achievement["progress"] is None and achievement["unlocked"] is None
            assert achievement["unlocked_at"] == json.loads(seen_before)[achievement["id"]]
            assert achievement["new"] is False
    assert db.get_setting("profile_achievements_seen") == seen_before
    _turns(db, NOW)
    result = ps.compute(db, "all", NOW)
    assert next(a for a in result["achievements"] if a["id"] == "first_task")["new"]
    seen = json.loads(db.get_setting("profile_achievements_seen"))
    assert seen["careful"] == seen["pocket"] == json.loads(seen_before)["careful"]


def test_earn_dates_order_by_time_then_id_and_ignore_first_seen(db, monkeypatch):
    _turns(db, "2026-07-01 12:00:00", 900, provider="agy")
    _turns(db, "2026-05-01 12:00:00", 99, provider="claude")
    _turns(db, "2026-05-01 12:00:00", provider="codex")
    for offset in range(8):
        _turns(db, datetime(2026, 4, 1 + offset, 2), 7, provider=None)
    for offset in range(7):
        _turns(db, datetime(2026, 6, 1 + offset, 2), provider=None)
    _ledger(db, "2026-08-01 09:00:00", count=50)
    _ledger(db, "2026-04-01 09:00:00", device="PHONE:Burak", count=49)
    _ledger(db, "2026-04-02 09:00:00", device="phone:Burak")
    _ledger(db, "2026-03-01 09:00:00", outcome="rejected", device="phone:Burak", count=100)
    db.set_setting("profile_achievements_seen", json.dumps(dict.fromkeys(
        (id_ for id_, _ in ps.ACHIEVEMENTS), NOW.strftime(ps.TIME_FORMAT))))
    list_activity = db.list_activity
    monkeypatch.setattr(db, "list_activity", lambda **kwargs: list(reversed(list_activity(**kwargs))))
    achievements = {a["id"]: a for a in ps.compute(db, "month", NOW)["achievements"]}
    expected = {"first_task": "2026-04-01 02:00:00", "tasks_100": "2026-05-01 12:00:00",
                "tasks_1000": "2026-07-01 12:00:00", "night_owl": "2026-04-08 02:00:00",
                "streak_7": "2026-04-07 00:00:00", "polyglot": "2026-07-01 12:00:00",
                "careful": "2026-08-01 09:00:00", "pocket": "2026-04-02 09:00:00"}
    assert {id_: a["unlocked_at"] for id_, a in achievements.items()} == expected
    assert not any(a["new"] for a in achievements.values())


def test_uncomputable_ledger_earn_date_falls_back_to_first_seen(db):
    _ledger(db, "unknown timestamp", device="phone:Burak", count=100)
    first_seen = "2026-09-01 12:00:00"
    db.set_setting("profile_achievements_seen", json.dumps({"careful": first_seen, "pocket": first_seen}))
    seen_before = db.get_setting("profile_achievements_seen")
    result = ps.compute(db, "all", NOW)
    assert not result["xp_partial"]
    for achievement in result["achievements"]:
        if achievement["id"] in ("careful", "pocket"):
            assert achievement["unlocked"] and achievement["unlocked_at"] == first_seen
            assert not achievement["new"]
    assert db.get_setting("profile_achievements_seen") == seen_before


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


def test_six_month_boundary_includes_morning_and_midnight(db):
    for at in ("2026-04-01 23:59:59", "2026-04-02 00:00:00", "2026-04-02 08:00:00"):
        _turns(db, at)
        _ledger(db, at)
    result = ps.compute(db, "6m", NOW)
    assert result["counts"]["tasks"] == 2
    assert result["counts"]["active_days"] == 1
    assert result["counts"]["approved_cards"] == 2


@pytest.mark.parametrize("with_older_message", [False, True])
def test_backfill_after_live_turn_imports_only_strictly_older_messages(db, with_older_message):
    conv = db.create_conversation(1, "root")
    live = db.add_message(conv, "assistant", "live answer")
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("UPDATE messages SET timestamp = ? WHERE id = ?",
                     ("2026-10-02 10:00:00", live))
    assert db.record_activity("turn_done", conversation_id=conv, at="2026-10-02 10:00:00")
    if with_older_message:
        older = db.add_message(conv, "assistant", "older answer")
        with closing(sqlite3.connect(db.db_path)) as conn, conn:
            conn.execute("UPDATE messages SET timestamp = ? WHERE id = ?",
                         ("2026-10-01 10:00:00", older))
        _turns(db, "2026-10-02 11:00:00")
    assert db.get_setting("profile_backfill_done") is None
    assert ps.backfill_once(db) == int(with_older_message)
    rows = db.list_activity(["turn_done"])
    assert len(rows) == (3 if with_older_message else 1)
    assert sum(row["at"] == "2026-10-02 10:00:00" for row in rows) == 1
    if with_older_message:
        assert rows[0]["at"] == "2026-10-01 10:00:00"
    assert ps.backfill_once(db) == 0


def test_malformed_event_and_message_times_are_ignored(db, monkeypatch, caplog):
    monkeypatch.setattr(ps, "_invalid_at_logged", False, raising=False)
    conv = db.create_conversation(1, "root")
    message = db.add_message(conv, "assistant", "malformed answer")
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("UPDATE messages SET timestamp = ? WHERE id = ?",
                     ("2026-09-01T10:00:00", message))
    assert ps.backfill_once(db) == 0
    _turns(db, "2026-09-01T10:00:00")
    _turns(db, "2026-10-02T02:00:00", 5, provider="bad", model="bad")
    _turns(db, "2026-10-02 25:00:00")
    _turns(db, "2026-02-30 10:00:00")
    empty = ps.compute(db, "all", NOW)
    assert empty["counts"]["tasks"] == empty["xp"] == 0
    assert empty["since"] is None and empty["models"]["favourite"] is None
    _turns(db, NOW, 10)
    for range_ in ("all", "month", "6m"):
        result = ps.compute(db, range_, NOW)
        assert result["counts"]["tasks"] == result["counts"]["tasks_this_month"] == 10
        assert result["counts"]["tasks_last_month"] == 0
        assert result["counts"]["active_days"] == 1 and result["xp"] == 120
        assert result["best_hour"] == 12 and result["busiest_weekday"] == 4
        assert sum(result["heatmap"]["days"]) == 10
        assert result["streak"] == {"current": 1, "longest": 1, "longest_end": "2026-10-02"}
        assert result["models"]["mix"] == [{"family": "claude", "turns": 10, "share": 1.0}]
        assert result["models"]["favourite"]["model"] == "claude-sonnet-5"
        assert next(a for a in result["achievements"] if a["id"] == "night_owl")["progress"] == 0
    assert sum("invalid activity timestamp" in record.message for record in caplog.records) == 1


@pytest.mark.parametrize("raw", ["not json", "[1]", "null", "42", '"text"'])
@pytest.mark.parametrize("with_turn", [False, True])
def test_corrupt_achievement_seen_is_repaired(db, monkeypatch, caplog, raw, with_turn):
    monkeypatch.setattr(ps, "_invalid_seen_logged", False, raising=False)
    if with_turn:
        _turns(db, NOW)
    db.set_setting("profile_achievements_seen", raw)
    result = ps.compute(db, "all", NOW)
    assert result["counts"]["tasks"] == int(with_turn)
    seen = json.loads(db.get_setting("profile_achievements_seen"))
    assert isinstance(seen, dict)
    assert set(seen) == ({"first_task"} if with_turn else set())
    db.set_setting("profile_achievements_seen", raw)
    with _client(db) as client:
        response = client.get("/profile/stats", headers=H)
        assert response.status_code == 200 and set(response.json()) == set(result)
    assert isinstance(json.loads(db.get_setting("profile_achievements_seen")), dict)
    assert sum("invalid achievement seen record" in record.message for record in caplog.records) == 1


def test_profile_handlers_are_sync_and_keep_get_post_behavior(db):
    router = create_profile_router(db)
    assert {route.path for route in router.routes} == {"/profile/stats", "/profile/reset"}
    assert all(not inspect.iscoroutinefunction(route.endpoint) for route in router.routes)
    _turns(db, NOW)
    approval_mode.set_ui_secret("profile-sync-secret")
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/profile/stats", headers=H)
        assert response.status_code == 200 and response.json()["counts"]["tasks"] == 1
        response = client.post("/profile/reset", headers={
            **H, "X-Gamachine-UI-Secret": "profile-sync-secret"})
        assert response.status_code == 200 and response.json() == {"cleared": 1}
        assert client.get("/profile/stats", headers=H).json()["counts"]["tasks"] == 0
