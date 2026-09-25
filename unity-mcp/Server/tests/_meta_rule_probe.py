"""Posts .meta-touching commands to the real /api/command route, emitting JSON.

Runs as a child interpreter, launched by test_protection_rules.py, for the
reason given in _authz_matrix_probe.py: tests/integration/conftest.py stubs
fastmcp for the whole session, so the real server cannot be built in-process.
The rule answers before any Unity session is looked up, so no Editor is needed.
"""

import json
import sys

SECRET = "meta-rule-probe-secret"

BODIES = [
    {"type": "manage_asset", "params": {"action": "delete", "path": "Assets/x.meta"}},
    {"type": "manage_asset", "params": {"action": "move", "path": "Assets/a.png",
                                        "destination": "Assets/b.png.meta"}},
    {"type": "batch_execute", "params": {"commands": [
        {"tool": "manage_asset", "params": {"action": "rename", "Path": "Assets/x.meta"}}]}},
]


def main() -> int:
    sys.path.insert(0, sys.argv[1])
    from starlette.testclient import TestClient

    from core.config import config
    from core.constants import API_KEY_HEADER
    import main as server_main

    config.local_api_token = SECRET
    config.http_remote_hosted = False
    mcp = server_main.create_mcp_server(project_scoped_tools=False)
    app = mcp.http_app(
        path=server_main.resolve_http_transport_path(),
        middleware=server_main.build_transport_middleware(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    out = []
    for body in BODIES:
        response = client.post("/api/command", json=body, headers={API_KEY_HEADER: SECRET})
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text[:300]}
        out.append({"status": response.status_code, "body": payload})
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
