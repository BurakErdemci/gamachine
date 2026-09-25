"""Routing state (unity_instance, unity_session_id, user_id) is request-scoped.

Runs _request_state_probe.py in a child interpreter against the real FastMCP;
its docstring has the measured leak this guards against.
"""

import json
import pathlib
import subprocess
import sys

import pytest

PROBE = pathlib.Path(__file__).resolve().parent / "_request_state_probe.py"
SRC_DIR = PROBE.parent.parent / "src"


@pytest.fixture(scope="module")
def report():
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(SRC_DIR)],
        cwd=str(PROBE.parent.parent), capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    return json.loads(completed.stdout)


def test_tools_still_read_the_routed_instance(report):
    assert report["seen_expected"], report


def test_no_routing_state_reaches_the_session_store(report):
    assert report["store_puts"] == 0, report


def test_routing_does_not_carry_over_into_the_next_request(report):
    assert report["value_after_unrouted_call"] is None, report
