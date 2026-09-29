"""The CLI's POST /api/command reaches the Unity package without this server's run_tests gate.

That route forwards the command as it is (main.py cli_command_route) and never runs the Python
compile gate or the finished-job correction, so the gate lives in the plugin (CompileGate.cs). These
tests pin the server's half: the route adds nothing of its own and hands the plugin's answer back byte
for byte, with no get_compile_status of its own in between.

The replies are the plugin's real output (see test_run_tests_unity_gate.py).
"""

import json
import pathlib
import subprocess
import sys

from .test_run_tests_unity_gate import PLUGIN_REFUSAL, PLUGIN_REJECTED_JOB

SERVER_DIR = pathlib.Path(__file__).resolve().parents[2]
_SRC = SERVER_DIR / "src"
PROBE = SERVER_DIR / "tests" / "_run_tests_cli_probe.py"


def _through_the_route(tmp_path, order, commands, params=None):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"order": order, "commands": commands, "params": params or {}}), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(_SRC), str(spec)],
        cwd=str(SERVER_DIR), capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return json.loads(completed.stdout)


def test_cli_start_gets_the_plugins_refusal_untouched(tmp_path):
    out = _through_the_route(tmp_path, ["run_tests"], {"run_tests": PLUGIN_REFUSAL}, {"run_tests": {"mode": "EditMode"}})

    [response] = out["responses"]
    assert response["status"] == 200
    assert response["body"] == PLUGIN_REFUSAL
    # No get_compile_status from the route: the plugin is the only gate on this path.
    assert out["received"] == ["run_tests"]


def test_cli_poll_gets_the_plugins_rejected_job_untouched(tmp_path):
    out = _through_the_route(
        tmp_path, ["get_test_job"], {"get_test_job": PLUGIN_REJECTED_JOB}, {"get_test_job": {"job_id": "j"}})

    [response] = out["responses"]
    assert response["status"] == 200
    assert response["body"] == PLUGIN_REJECTED_JOB
    assert response["body"]["data"]["status"] == "failed"
    assert response["body"]["data"]["result"]["summary"]["passed"] == 1
    assert out["received"] == ["get_test_job"]
