"""Posts run_tests / get_test_job to the real /api/command route with a fake Unity behind it, emitting JSON.

Runs as a child interpreter, launched by test_run_tests_cli_gate.py, for the reason given in
_authz_matrix_probe.py: tests/integration/conftest.py stubs fastmcp for the whole session, so the real
server cannot be built in-process. The route hands the command to PluginHub.send_command, which is
replaced here; the approval gate is replaced by a pass-through. HOME points at a scratch directory so
nothing of the real app's state is read.

argv: <src dir> <path of a JSON file: {"commands": {type: reply}}>. The fake Unity answers each command
with the reply given for its type and records the order of the commands it received.
"""

import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

SECRET = "run-tests-cli-probe-secret"


def main() -> int:
    src, spec_path = sys.argv[1], sys.argv[2]
    with open(spec_path, encoding="utf-8") as handle:
        spec = json.load(handle)

    scratch = tempfile.mkdtemp(prefix="run-tests-cli-probe-")
    os.environ["HOME"] = scratch
    os.environ["USERPROFILE"] = scratch
    sys.dont_write_bytecode = True
    sys.path.insert(0, src)
    try:
        from starlette.testclient import TestClient

        from core.config import config
        from core.constants import API_KEY_HEADER
        from transport import approval_gate
        import main as server_main

        config.transport_mode = "http"
        config.http_remote_hosted = False
        config.local_api_token = SECRET
        received: list[str] = []

        async def pass_through(*args, **kwargs):
            return None

        async def fake_sessions():
            return SimpleNamespace(sessions={"fake-session": SimpleNamespace(hash="fake-hash", project="Fixture")})

        async def fake_send(session_id, command_type, params):
            received.append(command_type)
            return spec["commands"][command_type]

        approval_gate.kapiyi_gec = pass_through
        server_main.PluginHub.get_sessions = fake_sessions
        server_main.PluginHub.send_command = fake_send
        mcp = server_main.create_mcp_server(project_scoped_tools=False)
        app = mcp.http_app(
            path=server_main.resolve_http_transport_path(),
            middleware=server_main.build_transport_middleware(),
        )
        client = TestClient(app, raise_server_exceptions=False)
        out = []
        for command_type in spec["order"]:
            response = client.post(
                "/api/command",
                json={"type": command_type, "params": spec.get("params", {}).get(command_type, {})},
                headers={API_KEY_HEADER: SECRET},
            )
            try:
                body = response.json()
            except ValueError:
                body = {"raw": response.text[:300]}
            out.append({"type": command_type, "status": response.status_code, "body": body})
        json.dump({"responses": out, "received": received}, sys.stdout)
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
