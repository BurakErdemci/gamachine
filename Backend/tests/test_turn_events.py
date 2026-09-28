"""Turn-event ring (docs/remote-control.md, backend piece 1).

Ring bounds, gap, text coalescing, live subscription and delete, then the one
hook in `/chat-stream` driven through the real AgentRunner dispatch with every
provider method replaced by a fake: no CLI, SDK or vendor API starts.
"""
import asyncio
import json
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import cards, turn_events, wake_queue
from agentic.turn_events import TurnEventRing, TurnTap
from database import DatabaseManager
from rag.memory_manager import memory_manager

H = {"X-Session-Token": ""}


@pytest.fixture(autouse=True)
def _fresh_ring():
    turn_events.RING.reset()
    cards.reset()
    yield
    turn_events.RING.reset()
    cards.reset()


# ── Ring ────────────────────────────────────────────────────────────────────

def test_ring_keeps_the_last_500_and_reports_the_gap():
    ring = TurnEventRing()
    for i in range(510):
        ring.append(1, "tool_call", name=f"t{i}", summary="")
    full = ring.since(1, 0)
    assert len(full["events"]) == 500
    assert full["events"][0]["seq"] == 11 and full["last_seq"] == 510
    assert full["gap"] is True
    tail = ring.since(1, 10)
    assert tail["gap"] is False and len(tail["events"]) == 500
    assert ring.since(1, 505)["events"][0]["seq"] == 506
    assert ring.since(1, 510) == {**ring.since(1, 510), "events": [], "gap": False}


def test_seq_is_per_conversation_and_monotonic():
    ring = TurnEventRing()
    ring.append(1, "turn_start", provider="claude", model="m", origin="user")
    ring.append(2, "turn_start", provider="codex", model="m", origin="user")
    ring.append(1, "turn_end", status="done")
    assert [e["seq"] for e in ring.since(1)["events"]] == [1, 2]
    assert [e["seq"] for e in ring.since(2)["events"]] == [1]


def test_a_seq_past_the_end_is_a_gap_and_returns_what_is_held():
    """After a restart the reader's seq belongs to the previous process."""
    ring = TurnEventRing()
    ring.append(1, "turn_start", provider="claude", model="m", origin="user")
    res = ring.since(1, 900)
    assert res["gap"] is True and [e["seq"] for e in res["events"]] == [1]
    assert ring.since(42, 3)["gap"] is True
    assert ring.since(42, 0) == {**ring.since(42, 0), "events": [], "gap": False}
    assert res["epoch"] == turn_events.EPOCH


def test_text_deltas_coalesce_and_flush_before_the_next_event():
    ring = TurnEventRing()
    for part in ("Mer", "ha", "ba"):
        ring.text(1, part)
    ring.append(1, "tool_call", name="Read", summary="a.cs")
    events = ring.since(1)["events"]
    assert [(e["kind"], e.get("content")) for e in events] == [
        ("text", "Merhaba"), ("tool_call", None)]


def test_text_flushes_at_2kb():
    ring = TurnEventRing()
    chunk = "x" * 1000
    for _ in range(5):
        ring.text(1, chunk)
    events = ring.since(1)["events"]
    # 3000 bytes crossed 2 KiB -> one event; the rest waits for the timer.
    assert [len(e["content"]) for e in events] == [3000]
    ring.flush(1)
    assert [len(e["content"]) for e in ring.since(1)["events"]] == [3000, 2000]


def test_text_flushes_on_time_without_another_event(monkeypatch):
    monkeypatch.setattr(turn_events, "TEXT_FLUSH_S", 0.02)
    ring = TurnEventRing()

    async def _drive():
        ring.text(1, "a")
        ring.text(1, "b")
        assert ring.since(1)["events"] == []
        await asyncio.sleep(0.1)
        return ring.since(1)["events"]

    events = asyncio.run(_drive())
    assert [(e["kind"], e["content"]) for e in events] == [("text", "ab")]


def test_live_subscription_gets_new_events_and_join_has_no_hole():
    ring = TurnEventRing()
    ring.append(1, "turn_start", provider="claude", model="m", origin="user")

    async def _drive():
        backlog, sub = ring.join(1, 0)
        ring.append(1, "tool_call", name="Bash", summary="ls")
        ring.text(1, "done")
        ring.append(1, "turn_end", status="done")
        got = [await asyncio.wait_for(sub.get(), 1) for _ in range(3)]
        sub.close()
        return backlog, got

    backlog, got = asyncio.run(_drive())
    assert [e["seq"] for e in backlog["events"]] == [1]
    assert [(e["seq"], e["kind"]) for e in got] == [(2, "tool_call"), (3, "text"), (4, "turn_end")]


def test_a_slow_subscriber_is_marked_lagged_not_blocking(monkeypatch):
    monkeypatch.setattr(turn_events, "SUBSCRIBER_QUEUE", 2)
    ring = TurnEventRing()

    async def _drive():
        sub = ring.subscribe(1)
        for i in range(5):
            ring.append(1, "tool_call", name=str(i), summary="")
        return sub

    sub = asyncio.run(_drive())
    assert sub.lagged is True
    assert ring.since(1)["last_seq"] == 5


def test_delete_drops_the_ring_closes_readers_and_ignores_late_writes():
    ring = TurnEventRing()

    async def _drive():
        sub = ring.subscribe(7)
        ring.append(7, "turn_start", provider="claude", model="m", origin="user")
        ring.drop(7)
        first = await asyncio.wait_for(sub.get(), 1)
        end = await asyncio.wait_for(sub.get(), 1)
        return first, end

    first, end = asyncio.run(_drive())
    assert first["kind"] == "turn_start" and end is None
    ring.append(7, "card_closed", card_id="g", decision="reject", by="system")
    ring.text(7, "late")
    assert 7 not in ring.conversations()
    assert ring.since(7, 0)["events"] == []


def test_a_huge_delta_is_split_on_character_boundaries_and_replays_exactly():
    ring = TurnEventRing()
    # 1-, 2-, 3- and 4-byte characters, so byte cuts land mid-character.
    text = ("aç€😀" * 40000)[:130001]
    ring.text(1, text)
    ring.flush(1)
    events = ring.since(1)["events"]
    assert len(events) > 1 and all(e["kind"] == "text" for e in events)
    assert all(len(e["content"].encode("utf-8")) <= turn_events.TEXT_EVENT_MAX_BYTES
               for e in events)
    assert "".join(e["content"] for e in events) == text
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


def test_split_text_keeps_a_lone_surrogate():
    text = "x" * 8191 + chr(0xD800) + "y" * 10
    pieces = turn_events.split_text(text)
    assert "".join(pieces) == text and len(pieces) == 2


def test_ring_bytes_are_bounded_and_eviction_reports_a_gap():
    ring = TurnEventRing()
    ring.append(1, "turn_start", provider="p", model="m", origin="user")
    ring.text(1, "x" * (4 * 1024 * 1024))
    ring.append(1, "turn_end", status="done")
    full = ring.since(1, 0)
    held = sum(len(e.get("content", "")) for e in full["events"])
    assert held <= turn_events.RING_MAX_BYTES
    assert full["gap"] is True and full["events"][-1]["kind"] == "turn_end"
    assert full["events"][0]["seq"] > 2
    # A reader that kept up still gets everything after its seq, no gap.
    last = full["last_seq"]
    assert ring.since(1, last - 1)["gap"] is False


def test_deleted_chat_ids_are_bounded_by_count_and_age(monkeypatch):
    ring = TurnEventRing()
    for cid in range(1, turn_events.DROPPED_KEEP + 501):
        ring.append(cid, "turn_start", provider="p", model="m", origin="user")
        ring.drop(cid)
    assert ring.conversations() == []
    assert len(ring._dropped) == turn_events.DROPPED_KEEP
    newest = turn_events.DROPPED_KEEP + 500
    ring.append(newest, "card_closed", card_id="g", decision="reject", by="system")
    assert newest not in ring.conversations()

    now = turn_events.time.monotonic()
    monkeypatch.setattr(turn_events.time, "monotonic",
                        lambda: now + turn_events.DROPPED_TTL_S + 1)
    ring.drop(10 ** 6)
    assert list(ring._dropped) == [10 ** 6]


def test_ring_failures_never_reach_the_caller_and_log_once(monkeypatch, caplog):
    ring = TurnEventRing()
    monkeypatch.setattr(turn_events, "_failure_logged", False)

    def _boom(*a, **k):
        raise RuntimeError("ring broke")

    monkeypatch.setattr(ring, "_push", _boom)
    with caplog.at_level("ERROR"):
        assert ring.append(1, "turn_start", provider="p", model="m", origin="user") is None
        ring.append(1, "turn_end", status="done")
        TurnTap(1, "p", "m", ring=ring).start()
    assert sum("turn-events" in r.getMessage() for r in caplog.records) == 1


def test_tap_marks_a_stopped_turn_even_when_the_provider_says_complete():
    ring = TurnEventRing()
    tap = TurnTap(3, "claude", "claude-x", ring=ring)
    tap.start()
    ring.note_stop(3)
    tap.feed(ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"}))
    end = ring.since(3)["events"][-1]
    assert (end["kind"], end["status"]) == ("turn_end", "stopped")


def test_tap_uses_response_only_when_no_text_streamed():
    ring = TurnEventRing()
    tap = TurnTap(4, "kimi", "kimi-k3", ring=ring)
    tap.start()
    tap.feed(ar.AgentEvent("response", {"content": "tek parca"}))
    tap.feed(ar.AgentEvent("done", {"stop_reason": "complete"}))
    tap2 = TurnTap(5, "claude", "c", ring=ring)
    tap2.start()
    tap2.feed(ar.AgentEvent("text", {"content": "akış"}))
    tap2.feed(ar.AgentEvent("response", {"content": "akış"}))
    tap2.feed(ar.AgentEvent("done", {"stop_reason": "complete"}))
    assert [e["content"] for e in ring.since(4)["events"] if e["kind"] == "text"] == ["tek parca"]
    assert [e["content"] for e in ring.since(5)["events"] if e["kind"] == "text"] == ["akış"]


# ── /chat-stream: every provider path ───────────────────────────────────────

PROVIDER_METHODS = ("_run_gemini", "_run_anthropic", "_run_openai", "_run_codex_session",
                    "_run_agy_session", "_run_oneshot_cli_session", "_run_claude_session",
                    "_run_simple")


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
    ran = []

    def _fake(name):
        async def _run(self, message, *extra):
            ran.append(name)
            yield ar.AgentEvent("text", {"content": "Mer"})
            yield ar.AgentEvent("text", {"content": "haba"})
            yield ar.AgentEvent("tool_call", {"tool": "Read", "arguments": {"file_path": "a.cs"}})
            yield ar.AgentEvent("response", {"content": "Merhaba"})
            yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})
        return _run

    for name in PROVIDER_METHODS:
        monkeypatch.setattr(ar.AgentRunner, name, _fake(name))
    db = DatabaseManager(str(tmp_path / "ring.db"))
    app = FastAPI()
    app.include_router(cr.create_conversation_router(db, {}))
    with TestClient(app) as client:
        yield db, client, ran


@pytest.mark.parametrize("provider,model,method,agent", [
    ("subscription", "claude-sonnet-5", "_run_claude_session", "claude"),
    ("subscription", "gpt-6-luna", "_run_codex_session", "codex"),
    ("subscription", "gemini-3.8-flash", "_run_agy_session", "agy"),
    ("subscription", "opencode:opencode/big-pickle", "_run_oneshot_cli_session", "opencode"),
    ("subscription", "copilot-gpt-5.5", "_run_oneshot_cli_session", "copilot"),
    ("subscription", "cursor-auto", "_run_oneshot_cli_session", "cursor"),
    ("subscription", "kimi-k3", "_run_oneshot_cli_session", "kimi"),
    ("anthropic", "claude-api", "_run_anthropic", "api-anthropic"),
    ("google", "gemini-api", "_run_gemini", "api-google"),
    ("openai", "gpt-api", "_run_openai", "api-openai"),
    ("ollama", "llama", "_run_simple", "api-ollama"),
])
def test_every_provider_path_writes_turn_start_and_turn_end(env, provider, model, method, agent):
    db, client, ran = env
    db.save_ai_config(1, provider, model, "")
    cid = db.create_conversation(1, "t")
    resp = client.post("/chat-stream", headers=H,
                       json={"conversation_id": cid, "message": "selam", "user_id": 1})
    assert resp.status_code == 200
    assert ran == [method]
    events = client.get(f"/conversations/{cid}/turn-events?since=0", headers=H).json()["events"]
    kinds = [e["kind"] for e in events]
    assert kinds == ["turn_start", "text", "tool_call", "turn_end"]
    assert (events[0]["provider"], events[0]["model"], events[0]["origin"]) == (agent, model, "user")
    assert events[1]["content"] == "Merhaba"
    assert events[2]["name"] == "Read" and "a.cs" in events[2]["summary"]
    assert events[3]["status"] == "done"


def test_a_wake_turn_is_written_with_origin_wake(env):
    db, client, ran = env
    db.save_ai_config(1, "subscription", "claude-sonnet-5", "")
    cid = db.create_conversation(1, "t")
    wake_queue.reset(cid)
    wake_queue.issue_ticket(cid, ["Arka plan işi bitti"])
    try:
        resp = client.post("/chat-stream", headers=H, json={
            "conversation_id": cid, "message": "Arka plan işi bitti", "user_id": 1,
            "origin": "wake"})
    finally:
        wake_queue.reset(cid)
    assert resp.status_code == 200 and ran == ["_run_claude_session"]
    events = turn_events.since(cid, 0)["events"]
    assert events[0]["kind"] == "turn_start" and events[0]["origin"] == "wake"
    assert events[-1]["kind"] == "turn_end"


def test_a_failing_turn_ends_with_error(env, monkeypatch):
    db, client, _ = env

    async def _boom(self, message):
        yield ar.AgentEvent("text", {"content": "yarım"})
        raise RuntimeError("provider died")

    monkeypatch.setattr(ar.AgentRunner, "_run_claude_session", _boom)
    db.save_ai_config(1, "subscription", "claude-sonnet-5", "")
    cid = db.create_conversation(1, "t")
    client.post("/chat-stream", headers=H,
                json={"conversation_id": cid, "message": "selam", "user_id": 1})
    events = turn_events.since(cid, 0)["events"]
    assert [e["kind"] for e in events][-1] == "turn_end"
    assert events[-1]["status"] == "error"


def test_ring_failure_does_not_break_the_stream(env, monkeypatch):
    db, client, _ = env
    monkeypatch.setattr(turn_events.RING, "_push",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    db.save_ai_config(1, "subscription", "claude-sonnet-5", "")
    cid = db.create_conversation(1, "t")
    resp = client.post("/chat-stream", headers=H,
                       json={"conversation_id": cid, "message": "selam", "user_id": 1})
    frames = [json.loads(l[6:]) for l in resp.text.splitlines() if l.startswith("data: ")]
    assert [f["type"] for f in frames if f["type"] in ("done", "error")] == ["done"]
    assert db.get_conversation_messages(cid)[-1]["content"] == "Merhaba"


def test_deleting_a_chat_drops_its_ring(env):
    db, client, _ = env
    db.save_ai_config(1, "subscription", "claude-sonnet-5", "")
    cid = db.create_conversation(1, "t")
    client.post("/chat-stream", headers=H,
                json={"conversation_id": cid, "message": "selam", "user_id": 1})
    assert turn_events.since(cid, 0)["events"]
    assert client.delete(f"/conversations/{cid}", headers=H).status_code == 200
    assert cid not in turn_events.RING.conversations()


# ── Route auth ──────────────────────────────────────────────────────────────

def test_turn_events_route_auth(env, monkeypatch):
    db, client, _ = env
    mine = db.create_conversation(1, "mine")
    other = db.create_conversation(2, "someone else's")
    turn_events.append(mine, "turn_start", provider="claude", model="m", origin="user")
    turn_events.append(other, "turn_start", provider="claude", model="m", origin="user")
    monkeypatch.setenv("LOCAL_APP_TOKEN", "tok")
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    ok = client.get(f"/conversations/{mine}/turn-events?since=0",
                    headers={"X-Session-Token": "tok"})
    assert ok.status_code == 200 and len(ok.json()["events"]) == 1
    assert client.get(f"/conversations/{other}/turn-events",
                      headers={"X-Session-Token": "tok"}).status_code == 403
    assert client.get("/conversations/99999/turn-events",
                      headers={"X-Session-Token": "tok"}).status_code == 404
    assert client.get(f"/conversations/{mine}/turn-events",
                      headers={"X-Session-Token": "wrong"}).status_code == 401
    missing = client.get(f"/conversations/{mine}/turn-events")
    assert missing.status_code in (401, 422)
    assert "events" not in missing.text


@pytest.mark.parametrize("since", ["-1", "abc", "1.5"])
def test_turn_events_route_rejects_a_bad_cursor(env, since):
    db, client, _ = env
    cid = db.create_conversation(1, "t")
    turn_events.append(cid, "turn_start", provider="claude", model="m", origin="user")
    res = client.get(f"/conversations/{cid}/turn-events?since={since}", headers=H)
    assert res.status_code == 422 and "events" not in res.json()
