"""A client that gives up on a call waiting for approval gets nothing run.

WHICH FAILURE IT CAME FROM
    Audit 25 Sep 2026: agy cancels every MCP call at exactly 180 s (not
    configurable) and tells the model the call timed out. The approval card
    wait was 180 s after a POST budget of up to 10 s, so an approval near the
    end ran in Unity after agy had given up, and the model's retry wrote twice.

WHAT IT PINS, over a real socket in both protocol eras (_approval_cancel_probe.py)
    * The cancel reaches the gate: mcp 2.2's dispatcher cancels the handler on
      notifications/cancelled (session era, what agy speaks) and on a closed
      response stream (2026-07-28). If an SDK upgrade switched to "signal"
      mode, the gate would keep waiting and dispatch later: this fails first.
    * Card polling stops and nothing is dispatched to Unity.
    * The card is withdrawn (resolved as not approved), so the user cannot
      approve a call nobody is waiting for.
"""

import json
import pathlib
import subprocess
import sys

import pytest

PROBE = pathlib.Path(__file__).resolve().parent / "_approval_cancel_probe.py"
SERVER_DIR = PROBE.parent.parent

# A poll already in flight when the cancel lands may still be logged.
POLL_SLACK_S = 0.6


@pytest.fixture(scope="module")
def report():
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(SERVER_DIR / "src")],
        cwd=str(SERVER_DIR), capture_output=True, text=True, timeout=180,
    )
    assert completed.returncode == 0, (
        f"cancel probe failed (exit {completed.returncode}):\n{completed.stderr[-4000:]}"
    )
    return json.loads(completed.stdout)


def _era(report, name):
    events = report[name]
    if isinstance(events, str):
        pytest.skip(events)
    return events


def _first(events, kind):
    return next((e for e in events if e[1] == kind), None)


@pytest.mark.parametrize("era", ["session_era", "modern_era"])
def test_the_card_was_waiting_when_the_client_cancelled(report, era):
    events = _era(report, era)
    assert _first(events, "card_opened"), events
    assert _first(events, "card_polled"), events
    assert _first(events, "client_cancelled"), events


@pytest.mark.parametrize("era", ["session_era", "modern_era"])
def test_the_cancel_reaches_the_gate_and_nothing_runs(report, era):
    events = _era(report, era)
    assert _first(events, "gate_cancelled"), events
    assert not _first(events, "gate_returned"), events
    assert not _first(events, "dispatched_to_unity"), events


@pytest.mark.parametrize("era", ["session_era", "modern_era"])
def test_polling_stops_and_the_card_is_withdrawn(report, era):
    events = _era(report, era)
    cancelled_at = _first(events, "client_cancelled")[0]
    late_polls = [e for e in events if e[1] == "card_polled" and e[0] > cancelled_at + POLL_SLACK_S]
    assert not late_polls, events
    opened = _first(events, "card_opened")[2]
    responded = [json.loads(e[2]) for e in events if e[1] == "card_responded"]
    assert responded == [{"gate_id": opened, "approved": False}], events


def test_session_era_cancel_is_what_agy_sends(report):
    """agy POSTs notifications/cancelled; the server must accept it (202)."""
    events = _era(report, "session_era")
    assert _first(events, "client_cancelled")[2] == "202", events
