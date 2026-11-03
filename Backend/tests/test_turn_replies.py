"""agentic.turn_replies: the in-memory record of which assistant row answered a user row."""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from agentic import turn_replies  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    turn_replies.clear()
    yield
    turn_replies.clear()


def test_begin_then_end_then_get():
    assert turn_replies.get(1, 10) is None
    turn_replies.begin(1, 10)
    assert turn_replies.get(1, 10) == {"state": "running"}
    turn_replies.end(1, 10, 11, "done")
    assert turn_replies.get(1, 10) == {"state": "ended", "assistant_row_id": 11, "status": "done"}
    assert turn_replies.get(2, 10) is None and turn_replies.get(1, 11) is None


def test_get_returns_a_copy():
    turn_replies.begin(1, 10)
    turn_replies.get(1, 10)["state"] = "ended"
    assert turn_replies.get(1, 10) == {"state": "running"}


def test_a_second_end_keeps_the_first():
    turn_replies.begin(1, 10)
    turn_replies.end(1, 10, 11, "done")
    turn_replies.end(1, 10, None, "error")
    turn_replies.end(1, 10, 12, "stopped")
    assert turn_replies.get(1, 10) == {"state": "ended", "assistant_row_id": 11, "status": "done"}
    turn_replies.begin(1, 20)
    turn_replies.end(1, 20, None, "error")
    turn_replies.end(1, 20, None, "stopped")
    assert turn_replies.get(1, 20) == {"state": "ended", "assistant_row_id": None, "status": "error"}


def test_an_owed_reply_upgrades_an_end_without_a_row():
    turn_replies.begin(1, 10)
    turn_replies.end(1, 10, None, "stopped")
    turn_replies.end(1, 10, 15, "done")
    assert turn_replies.get(1, 10) == {"state": "ended", "assistant_row_id": 15, "status": "done"}


def test_the_registry_keeps_the_newest_entries(monkeypatch):
    assert turn_replies.KEEP == 2000
    for i in range(turn_replies.KEEP + 5):
        turn_replies.begin(1, i)
    assert turn_replies.get(1, 0) is None and turn_replies.get(1, 4) is None
    assert turn_replies.get(1, 5) == {"state": "running"}
    assert turn_replies.get(1, turn_replies.KEEP + 4) == {"state": "running"}
    # Ending an old entry makes it the newest, so it survives the next evictions.
    turn_replies.end(1, 5, 9, "done")
    turn_replies.begin(1, 99_999)
    assert turn_replies.get(1, 6) is None and turn_replies.get(1, 5)["assistant_row_id"] == 9


def test_concurrent_begin_and_end_stay_consistent():
    def work(conv):
        for i in range(300):
            turn_replies.begin(conv, i)
            turn_replies.end(conv, i, i + 1, "done")

    threads = [threading.Thread(target=work, args=(c,)) for c in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(turn_replies.get(c, i)["assistant_row_id"] == i + 1 for c in range(4) for i in range(300))
