"""A turn the agent runs by itself is fed to the ring with what a follower needs
to show it like a normal turn: provider and model at start, tool results, and
throttled status heartbeats."""
import pytest

from agentic import turn_events
from agentic.agent_runner import AgentEvent
from agentic.turn_events import STATUS_MIN_INTERVAL_S, TurnEventRing, TurnTap


def _tap(ring):
    tap = TurnTap(1, "claude", "claude-opus-5-5", "agent", ring=ring)
    tap.start()
    return tap


def test_turn_start_carries_provider_and_model():
    ring = TurnEventRing()
    _tap(ring)
    start = ring.since(1, 0)["events"][0]
    assert (start["kind"], start["provider"], start["model"], start["origin"]) == (
        "turn_start", "claude", "claude-opus-5-5", "agent")


def test_tool_result_is_recorded_with_name_success_and_short_summary():
    ring = TurnEventRing()
    tap = _tap(ring)
    tap.feed(AgentEvent("tool_result", {"tool": "Read", "success": True, "summary": "x" * 500}))
    tap.feed(AgentEvent("tool_result", {"tool": "Bash", "success": False, "summary": "boom"}))
    results = [e for e in ring.since(1, 0)["events"] if e["kind"] == "tool_result"]
    assert [(e["name"], e["success"]) for e in results] == [("Read", True), ("Bash", False)]
    assert len(results[0]["summary"]) <= turn_events.SUMMARY_CHARS
    assert results[1]["summary"] == "boom"


def test_status_is_throttled_and_carries_tokens_and_detail(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(turn_events.time, "monotonic", lambda: clock[0])
    ring = TurnEventRing()
    tap = _tap(ring)
    tap.feed(AgentEvent("status", {"tokens": 10, "detail": "working"}))
    clock[0] += STATUS_MIN_INTERVAL_S / 4
    tap.feed(AgentEvent("status", {"tokens": 20, "detail": "working"}))
    clock[0] += STATUS_MIN_INTERVAL_S
    tap.feed(AgentEvent("status", {"tokens": 30, "detail": "reading"}))
    got = [e for e in ring.since(1, 0)["events"] if e["kind"] == "status"]
    assert [(e["tokens"], e["detail"]) for e in got] == [(10, "working"), (30, "reading")]


def test_a_flood_of_status_does_not_push_the_turn_out_of_the_ring():
    ring = TurnEventRing()
    tap = _tap(ring)
    for i in range(2000):
        tap.feed(AgentEvent("status", {"tokens": i, "detail": "w"}))
    events = ring.since(1, 0)["events"]
    assert events[0]["kind"] == "turn_start"
    assert sum(e["kind"] == "status" for e in events) == 1
