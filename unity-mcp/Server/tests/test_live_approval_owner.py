"""Over the real server: which conversation each carrier puts on the card.

test_approval_conversation_owner.py pins the reader with a fake
fastmcp.server.dependencies; this pins that the real FastMCP hands the
middleware the header, the ?conv= query and the call's _meta on both protocol
eras (_approval_owner_probe.py), and that tool arguments never become an owner.
"""

import json
import pathlib
import subprocess
import sys

import pytest

PROBE = pathlib.Path(__file__).resolve().parent / "_approval_owner_probe.py"
SERVER_DIR = PROBE.parent.parent


@pytest.fixture(scope="module")
def report():
    completed = subprocess.run(
        [sys.executable, str(PROBE)],
        cwd=str(SERVER_DIR), capture_output=True, text=True, timeout=240,
    )
    assert completed.returncode == 0, (
        f"owner probe failed (exit {completed.returncode}):\n{completed.stderr[-4000:]}"
    )
    data = json.loads(completed.stdout)
    assert data.get("healthy"), (
        f"server never answered /health.\nexit code: {data.get('exit_code')}\n"
        f"{data.get('server_output_tail')}"
    )
    return data


def _asked(report, case):
    hits = [r for r in report["backend_requests"]
            if r["path"] == "/mcp-approval-request"
            and r["params"].get("name") == f"deny_now:{case}"]
    assert len(hits) == 1, (case, report["backend_requests"], report["server_output_tail"])
    return hits[0]


@pytest.mark.parametrize("case,expected", [
    ("old_header", 41), ("old_query", 42), ("old_meta", 43),
])
def test_old_era_carriers_reach_the_body(report, case, expected):
    assert report["old_era"][case]["is_error"] is True, report["old_era"][case]
    asked = _asked(report, case)
    assert asked["has_conversation"] is True and asked["conversation_id"] == expected


@pytest.mark.parametrize("case", ["old_args", "old_junk", "old_conflict", "old_none"])
def test_old_era_no_owner(report, case):
    assert report["old_era"][case]["is_error"] is True, report["old_era"][case]
    assert _asked(report, case)["has_conversation"] is False


@pytest.mark.parametrize("case,expected", [
    ("new_header", 51), ("new_query", 52), ("new_meta", 53), ("new_none", None),
])
def test_new_era_carriers(report, case, expected):
    new = report["new_era"]
    if "skipped" in new:
        pytest.skip(new["skipped"])
    assert "exception" not in new[case], new[case]
    assert new[case]["protocol"] == "2026-07-28", new[case]
    asked = _asked(report, case)
    if expected is None:
        assert asked["has_conversation"] is False
    else:
        assert asked["has_conversation"] is True and asked["conversation_id"] == expected


def test_server_survived_and_probe_cleaned_up(report):
    assert report["still_running"], report.get("server_output_tail")
    assert report["workdir_removed"] is True
