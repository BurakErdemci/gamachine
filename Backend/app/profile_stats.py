"""Local maker profile rules (maker profile plan, 2 Oct 2026)."""
import calendar
import json
import logging
import sqlite3
from bisect import bisect_left
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta
from statistics import quantiles
from threading import Lock

from agentic.chat_model import agent_label


logger = logging.getLogger(__name__)
_warning_lock = Lock()
_invalid_at_logged = False
_invalid_seen_logged = False
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SUMMARY_PREFIXES = ("📝 **Sohbet özetlendi.**", "🧠 **Analiz Raporu**")
ACHIEVEMENTS = (
    ("first_task", 1), ("tasks_100", 100), ("tasks_1000", 1000),
    ("night_owl", 50), ("streak_7", 7), ("pocket", 50),
    ("careful", 100), ("polyglot", 3),
)


def backfill_once(db) -> int:
    """Commit the imported events and marker together, including concurrent starts."""
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT value FROM app_settings WHERE key = ?",
                        ("profile_backfill_done",)).fetchone():
            return 0
        # A retry must not import turns already recorded live (profile audit, 2 Oct 2026).
        earliest = conn.execute(
            "SELECT MIN(at) FROM activity_events WHERE kind = 'turn_done'").fetchone()[0]
        rows = conn.execute(
            "SELECT m.timestamp, m.conversation_id, m.provider, m.model, "
            "c.provider_type, c.model_name FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.role = 'assistant' AND c.side_of IS NULL "
            "AND (c.copied_until IS NULL OR m.id > c.copied_until) "
            "AND m.timestamp GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] "
            "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]' "
            "AND (? IS NULL OR m.timestamp < ?) "
            "AND substr(m.content, 1, ?) != ? AND substr(m.content, 1, ?) != ? "
            "ORDER BY m.timestamp, m.id",
            (earliest, earliest, len(SUMMARY_PREFIXES[0]), SUMMARY_PREFIXES[0],
             len(SUMMARY_PREFIXES[1]), SUMMARY_PREFIXES[1])).fetchall()
        for at, conversation_id, provider, model, conv_provider, conv_model in rows:
            family = provider or (agent_label(conv_provider, conv_model)
                                  if conv_provider is not None or conv_model is not None
                                  else None)
            conn.execute(
                "INSERT INTO activity_events (at, kind, conversation_id, provider, model) "
                "VALUES (?, 'turn_done', ?, ?, ?)",
                (at, conversation_id, family, model or conv_model))
        conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                     ("profile_backfill_done", "1", datetime.now().strftime(TIME_FORMAT)))
        return len(rows)


def _months_ago(now, months):
    month_index = now.year * 12 + now.month - 1 - months
    year, month = divmod(month_index, 12)
    month += 1
    return now.replace(year=year, month=month,
                       day=min(now.day, calendar.monthrange(year, month)[1]))


def _streak(days, today):
    longest, run, longest_end, previous = 0, 0, None, None
    for day in sorted(days):
        run = run + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        if run >= longest:
            longest, longest_end = run, day.isoformat()
        previous = day
    end = today if today in days else today - timedelta(days=1)
    current = 0
    while end in days:
        current += 1
        end -= timedelta(days=1)
    return {"current": current, "longest": longest, "longest_end": longest_end}


def _ledger_counts(db, since):
    # One flushed snapshot supplies both range counts and all-time XP.
    with closing(db._ledger_read_connection()) as conn:
        rows = conn.execute(
            "SELECT at, outcome, device FROM approval_ledger "
            "WHERE outcome IN ('approved', 'rejected') ORDER BY at, id").fetchall()
    def count(selected):
        return {
            "approved_cards": sum(outcome == "approved" for _, outcome, _ in selected),
            "rejected_cards": sum(outcome == "rejected" for _, outcome, _ in selected),
            "phone_approvals": sum(outcome == "approved" and
                                   (device or "").lower().startswith("phone:")
                                   for _, outcome, device in selected),
        }
    approved = [at for at, outcome, _ in rows if outcome == "approved"]
    phone = [at for at, outcome, device in rows if outcome == "approved"
             and (device or "").lower().startswith("phone:")]
    earned = {"careful": _threshold_at(approved, 100), "pocket": _threshold_at(phone, 50)}
    return count([row for row in rows if since is None or row[0] >= since]), count(rows), earned


def _threshold_at(times, goal):
    if len(times) < goal:
        return None
    try:
        return datetime.strptime(times[goal - 1], TIME_FORMAT).strftime(TIME_FORMAT)
    except (ValueError, TypeError):
        return None


def _activity_earn_dates(events, dates):
    # Earn dates follow historical rule satisfaction, not the first profile visit
    # (profile screen audit, 2 Oct 2026).
    times = [event["at"] for event in events]
    earned = {id_: _threshold_at(times, goal)
              for id_, goal in ACHIEVEMENTS if id_ in ("first_task", "tasks_100", "tasks_1000")}
    earned["night_owl"] = _threshold_at(
        [event["at"] for event in events if int(event["at"][11:13]) < 5], 50)
    providers = set()
    for event in events:
        if event["provider"] is not None:
            providers.add(event["provider"])
        if len(providers) == 3:
            earned["polyglot"] = event["at"]
            break
    previous, run = None, 0
    for day in sorted(dates):
        run = run + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        if run == 7:
            earned["streak_7"] = datetime.combine(day, datetime.min.time()).strftime(TIME_FORMAT)
            break
        previous = day
    return earned


def _achievements(db, values, now, earned):
    global _invalid_seen_logged
    # Only the caller that stores an unlock may announce it as new.
    with closing(sqlite3.connect(db.db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                           ("profile_achievements_seen",)).fetchone()
        result, changed = [], False
        # Repair corrupt state even without an unlock (profile audit, 2 Oct 2026).
        try:
            seen = json.loads(row[0]) if row else {}
            if not isinstance(seen, dict):
                raise ValueError("Achievement seen record must be an object")
        except (ValueError, TypeError):
            seen, changed = {}, True
            with _warning_lock:
                if not _invalid_seen_logged:
                    _invalid_seen_logged = True
                    logger.warning("[profile] invalid achievement seen record; resetting")
        for id_, goal in ACHIEVEMENTS:
            if values[id_] is None:
                # A failed ledger read must never create or clear its seen entries
                # (profile screen audit, 2 Oct 2026).
                result.append({"id": id_, "goal": goal, "progress": None,
                               "unlocked": None, "unlocked_at": seen.get(id_), "new": False})
                continue
            unlocked = values[id_] >= goal
            new = unlocked and id_ not in seen
            if new:
                seen[id_] = now.strftime(TIME_FORMAT)
                changed = True
            result.append({"id": id_, "goal": goal, "progress": min(values[id_], goal),
                           "unlocked": unlocked,
                           "unlocked_at": (earned.get(id_) or seen.get(id_)) if unlocked else None,
                           "new": new})
        if changed:
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                ("profile_achievements_seen", json.dumps(seen), now.strftime(TIME_FORMAT)))
        return result


def compute(db, range_, now=None) -> dict:
    global _invalid_at_logged
    if range_ not in ("month", "6m", "all"):
        raise ValueError("Invalid profile range")
    now = now or datetime.now()
    today = now.date()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month = _months_ago(month_start, 1)
    next_month = _months_ago(month_start, -1)
    # Include the entire boundary day (profile audit, 2 Oct 2026).
    six_month_start = _months_ago(now, 6).replace(hour=0, minute=0, second=0, microsecond=0)
    since = {"month": month_start, "6m": six_month_start, "all": None}[range_]
    since = since.strftime(TIME_FORMAT) if since else None
    # Validate before any counters or slices; normalize parseable times for slices
    # and lexical windows (profile audit, 2 Oct 2026).
    events = []
    for event in db.list_activity(kinds=["turn_done"]):
        try:
            at = datetime.strptime(event["at"], TIME_FORMAT)
        except (ValueError, TypeError):
            with _warning_lock:
                if not _invalid_at_logged:
                    _invalid_at_logged = True
                    logger.warning("[profile] invalid activity timestamp; skipping event")
            continue
        events.append({**event, "at": at.strftime(TIME_FORMAT)})
    events.sort(key=lambda event: (event["at"], event["id"]))
    selected = [event for event in events if since is None or event["at"] >= since]
    dates = Counter(datetime.strptime(event["at"], TIME_FORMAT).date() for event in events)
    selected_dates = Counter(datetime.strptime(event["at"], TIME_FORMAT).date()
                             for event in selected)
    streak = _streak(dates, today)
    try:
        ledger, all_ledger, ledger_earned = _ledger_counts(db, since)
        ledger_ok = True
    except sqlite3.Error:
        ledger = dict.fromkeys(("approved_cards", "rejected_cards", "phone_approvals"))
        all_ledger = {key: 0 for key in ledger}
        ledger_earned = {}
        ledger_ok = False

    hours = Counter(int(event["at"][11:13]) for event in selected)
    weekdays = Counter(day.weekday() for day in selected_dates.elements())
    monday = today - timedelta(days=today.weekday())
    start = monday - timedelta(weeks=25)
    # Fixed window and inclusive value quartiles are product decisions.
    # Equal counts get equal levels (maker profile plan, 2 Oct 2026).
    heat_days = [dates[start + timedelta(days=i)] if start + timedelta(days=i) <= today else 0
                 for i in range(182)]
    nonzero = sorted(count for count in heat_days if count)
    cuts = quantiles(nonzero, n=4, method="inclusive") if len(nonzero) > 1 else nonzero * 3
    levels = [bisect_left(cuts, count) + 1 if count else 0 for count in heat_days]

    families = Counter(event["provider"] if event["provider"] is not None else "unknown"
                       for event in selected)
    mix = [{"family": family, "turns": turns, "share": round(turns / len(selected), 3)}
           for family, turns in sorted(families.items(), key=lambda item: (-item[1], item[0]))]
    recent = defaultdict(list)
    recent_start = (now - timedelta(days=30)).strftime(TIME_FORMAT)
    for event in events:
        if event["model"] is not None and event["at"] >= recent_start:
            recent[event["model"]].append(event)
    favourite = None
    if recent:
        model, model_events = max(recent.items(),
                                  key=lambda item: (len(item[1]), item[1][-1]["at"],
                                                    item[1][-1]["id"]))
        if len(model_events) >= 5:
            favourite = {"model": model, "family": model_events[-1]["provider"],
                         "turns": len(model_events)}

    xp = (10 * len(events) + 20 * len(dates) + 5 * all_ledger["approved_cards"]
          + 5 * all_ledger["phone_approvals"])
    level, level_xp = 1, xp
    while level_xp >= 100 * level:
        level_xp -= 100 * level
        level += 1
    rank = ("rookie" if level < 5 else "prototyper" if level < 10 else
            "scene_master" if level < 15 else "prefab_wizard" if level < 20 else
            "engine_whisperer")
    values = {"first_task": len(events), "tasks_100": len(events), "tasks_1000": len(events),
              "night_owl": sum(int(event["at"][11:13]) < 5 for event in events),
              "streak_7": streak["longest"], "pocket": all_ledger["phone_approvals"] if ledger_ok else None,
              "careful": all_ledger["approved_cards"] if ledger_ok else None,
              "polyglot": len({event["provider"] for event in events
                               if event["provider"] is not None})}
    return {
        "range": range_, "since": events[0]["at"][:10] if events else None,
        "ledger_ok": ledger_ok, "xp_partial": not ledger_ok,
        "xp": xp, "level": level, "level_xp": level_xp,
        "level_need": 100 * level, "rank": rank,
        "counts": {"tasks": len(selected),
                   "tasks_this_month": sum(month_start.strftime(TIME_FORMAT) <= event["at"]
                                           < next_month.strftime(TIME_FORMAT) for event in events),
                   "tasks_last_month": sum(last_month.strftime(TIME_FORMAT) <= event["at"]
                                           < month_start.strftime(TIME_FORMAT) for event in events),
                   **ledger, "active_days": len(selected_dates)},
        "streak": streak,
        "best_hour": min(hours, key=lambda hour: (-hours[hour], hour))
                     if len(selected) >= 10 else None,
        "busiest_weekday": min(weekdays, key=lambda day: (-weekdays[day], day))
                           if weekdays else None,
        "heatmap": {"start": start.isoformat(), "today": today.isoformat(),
                    "days": heat_days, "levels": levels},
        "models": {"mix": mix, "favourite": favourite},
        "achievements": _achievements(db, values, now, {**_activity_earn_dates(events, dates), **ledger_earned}),
    }
