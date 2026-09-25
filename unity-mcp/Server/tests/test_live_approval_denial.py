"""An approval refusal reaches the model as a tool error that says why.

WHICH FAILURE IT CAME FROM
    On the 2026-07-28 protocol (what Claude Code negotiates) a refused call
    came back as a bare JSON-RPC "Internal server error": ApprovalDenied is a
    RuntimeError raised from middleware, and the mcp 2 runner masks any
    non-MCP exception. The call was still blocked, but in step mode the model
    saw no reason and could simply retry.

WHAT IT PINS
    Over the real server and a fake approval backend
    (_approval_denial_probe.py), on both protocol eras, for every way the
    gate refuses (refused on request, refused after polling, token rejected,
    backend unreachable): the result is isError with the backend's reason in
    it. The 180 s poll timeout raises the same ApprovalDenied through the same
    path (test_approval_gate.py measures that); it is not waited out here.
    Also: a read call and a tool outside the connection's profile never ask
    the backend.
"""

import json
import pathlib
import subprocess
import sys

import pytest

PROBE = pathlib.Path(__file__).resolve().parent / "_approval_denial_probe.py"
SERVER_DIR = PROBE.parent.parent
SCENARIOS = ("deny_now", "deny_later", "auth", "down")

# What the gate itself says for refusals whose reason it writes (approval_gate.py).
GATE_REASON = {
    "auth": "HTTP 401",
    "down": "Onay servisine",
}


@pytest.fixture(scope="module")
def report():
    completed = subprocess.run(
        [sys.executable, str(PROBE)],
        cwd=str(SERVER_DIR), capture_output=True, text=True, timeout=240,
    )
    assert completed.returncode == 0, (
        f"approval probe failed (exit {completed.returncode}):\n{completed.stderr[-4000:]}"
    )
    data = json.loads(completed.stdout)
    assert data.get("healthy"), (
        f"server never answered /health.\nexit code: {data.get('exit_code')}\n"
        f"{data.get('server_output_tail')}"
    )
    return data


def _expected_reason(scenario: str, era: str) -> str:
    return GATE_REASON.get(scenario, f"fake-backend-refused-{scenario}-{era}")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_old_era_refusal_is_a_tool_error_with_its_reason(report, scenario):
    call = report["old_era"][scenario]
    assert call["status"] == 200 and call["error"] is None, call
    assert call["is_error"] is True, call
    assert "manage_gameobject" in call["text"], call
    assert _expected_reason(scenario, "old") in call["text"], call


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_new_era_refusal_is_a_tool_error_with_its_reason(report, scenario):
    new = report["new_era"]
    if "skipped" in new:
        pytest.skip(new["skipped"])
    call = new[scenario]
    assert "exception" not in call, call
    assert call["protocol"] == "2026-07-28", call
    assert call["is_error"] is True, call
    assert "manage_gameobject" in call["text"], call
    assert _expected_reason(scenario, "new") in call["text"], call


def test_every_refused_call_asked_the_backend_with_the_app_token(report):
    asked = [r for r in report["backend_requests"] if r["path"] == "/mcp-approval-request"]
    names = sorted(r["params"]["name"] for r in asked)
    # "down" is retried until the 10 s budget runs out, so it asks several times.
    for era in ("old", "new"):
        for scenario in SCENARIOS:
            assert f"{scenario}:{era}" in names, names
    assert {r["token"] for r in report["backend_requests"]} == {report["app_token"]}


def test_a_read_and_an_out_of_profile_call_never_ask_the_backend(report):
    assert report["backend_requests_after_gated_calls"] == []
    refused = report["outside_profile"]
    assert refused["is_error"] or refused["error"], refused
    assert "/mcp/gamachine" in (refused["text"] + str(refused["error"])), refused


def test_server_survived_and_probe_cleaned_up(report):
    assert report["still_running"], report.get("server_output_tail")
    assert report["workdir_removed"] is True
