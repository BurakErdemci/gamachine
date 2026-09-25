"""Builds the real local server and walks its routing table, emitting JSON.

This runs as a CHILD INTERPRETER, launched by test_local_authz_matrix.py. It is
not a test module (the name does not match `python_files = test_*.py`) and it
must not be imported into the test session.

WHY A SUBPROCESS, measured 2026-07-27
    `tests/integration/conftest.py` installs stub `fastmcp` AND `mcp` modules
    into sys.modules at COLLECTION time, so they are in place for the whole
    session rather than for the integration tests alone. Three ways of building
    the real server inside that session were tried and all failed:
      * plain import          -> TypeError: _DummyFastMCP() takes no arguments
      * rebinding main.FastMCP -> ImportError: cannot import name 'LoggingLevel'
                                  from 'mcp' (the stub shadows the SDK too)
      * purging and restoring sys.modules around the build -> the restore left
        the module graph inconsistent and took 545 unrelated tests down with it
    Forcing the real fastmcp for the whole session was also measured: it makes
    tests/integration/test_editor_state_v2_contract.py fail, so the stub is
    load-bearing for at least one test and cannot simply be removed.
    A child interpreter has none of that state. It binds no port -- every probe
    goes through Starlette's in-process test client.

WHAT IT EMITS
    A single JSON object on stdout. Endpoints are enumerated from the built
    application's own `app.routes`, never from a list written here, so a route
    added tomorrow shows up in the report and the parent's classification tables
    have to account for it.
"""

import json
import sys


def _probe_method(route):
    """The single method a route is probed with, taken from the route itself.

    The MCP transport mount reports `methods = None`; it speaks JSON-RPC over
    POST, which is what every client uses and what the header gate sees.
    """
    methods = getattr(route, "methods", None) or set()
    for candidate in ("GET", "POST", "PUT", "DELETE"):
        if candidate in methods:
            return candidate
    return "POST"


def build_report(secret: str, login_url: str) -> dict:
    from starlette.routing import WebSocketRoute
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from core.config import config
    from core.constants import API_KEY_HEADER
    import main as server_main
    from transport.tool_profiles import PROFILES

    # Set before building: /api/command, /api/instances and /api/custom-tools
    # are registered CONDITIONALLY on a secret being present, so configuration
    # decides the routing table itself.
    config.local_api_token = secret
    config.http_remote_hosted = False
    # Configured on purpose: with it unset the login-url route answers 404 and
    # hides what it would disclose. The parent asserts on the disclosed value.
    config.api_key_login_url = login_url

    mcp = server_main.create_mcp_server(project_scoped_tools=False)
    app = mcp.http_app(
        path=server_main.resolve_http_transport_path(),
        middleware=server_main.build_transport_middleware(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    credentials = {
        "anonymous": {},
        "invalid": {API_KEY_HEADER: "authz-matrix-WRONG"},
        "valid": {API_KEY_HEADER: secret},
    }

    http, websocket, routes = {}, {}, []
    for route in app.routes:
        path = route.path
        if isinstance(route, WebSocketRoute):
            routes.append({"path": path, "kind": "websocket"})
            websocket[path] = {}
            for label, headers in credentials.items():
                try:
                    with client.websocket_connect(path, headers=headers):
                        websocket[path][label] = "connected"
                except WebSocketDisconnect as exc:
                    websocket[path][label] = exc.code
                except Exception as exc:            # handshake refused some other way
                    websocket[path][label] = f"{type(exc).__name__}"
            continue

        method = _probe_method(route)
        routes.append({"path": path, "kind": "http", "method": method})
        # Keyed by METHOD + path, not path alone. Measured 2026-07-27: with a
        # path-only key, a second route on an already-classified path silently
        # overwrote the first one's observations -- an ungated PUT /api/instances
        # registered before the gated GET disappeared behind the GET's 401 and
        # the whole matrix stayed green.
        key = f"{method} {path}"
        http[key] = {"method": method, "path": path}
        for label, headers in credentials.items():
            response = client.request(method, path, headers=headers, json={})
            http[key][label] = response.status_code

    # Tool profiles are served from the one /mcp route by an ASGI rewrite, so
    # they never appear in app.routes; they are enumerated from the profile
    # table instead, plus one name that is not a profile.
    profile_paths = {}
    for path in [f"/mcp/{name}" for name in PROFILES] + ["/mcp/not-a-profile"]:
        profile_paths[path] = {
            label: client.post(path, headers=headers, json={}).status_code
            for label, headers in credentials.items()
        }

    health = client.get("/health")
    login = client.get("/api/auth/login-url")

    report = {
        "routes": routes,
        "http": http,
        "websocket": websocket,
        "profile_paths": profile_paths,
        # The `/mcp/<secret>` path form was removed; an old config must fail
        # closed rather than keep working while leaking the secret into files.
        "legacy_secret_path_status": client.post(f"/mcp/{secret}", json={}).status_code,
        "health_body_status": health.json().get("status"),
        "login_url_anonymous": {"status": login.status_code, "body": login.text},
        "header_case": {
            name: {
                "/api/instances": client.get("/api/instances", headers={name: secret}).status_code,
                "/mcp": client.post("/mcp", headers={name: secret}, json={}).status_code,
            }
            for name in ("X-API-Key", "x-api-key", "X-Api-Key")
        },
    }

    # Second application: no secret configured at all.
    config.local_api_token = ""
    config.api_key_login_url = ""
    closed = server_main.create_mcp_server(project_scoped_tools=False)
    closed_app = closed.http_app(path=None)
    closed_client = TestClient(closed_app, raise_server_exceptions=False)
    report["without_secret"] = {
        "routes": sorted(route.path for route in closed_app.routes),
        "register_tools_status": closed_client.post("/register-tools", json={}).status_code,
    }

    # Third application: REMOTE-HOSTED mode. This is the mode the local secret
    # gate must not touch. Callers there authenticate through ApiKeyService and
    # `local_api_token` is deliberately unset, so gating /api/auth/login-url on
    # it answered 503 to everyone -- closing the one route whose job is to tell a
    # caller who has NO key where to obtain one. Measured 2026-07-27; the Unity
    # package's "Get API Key" button calls it with no header by design.
    config.http_remote_hosted = True
    config.local_api_token = ""
    config.api_key_login_url = login_url
    remote = server_main.create_mcp_server(project_scoped_tools=False)
    remote_app = remote.http_app(path=None)
    remote_client = TestClient(remote_app, raise_server_exceptions=False)
    remote_login = remote_client.get("/api/auth/login-url")
    report["remote_hosted_login"] = {
        "status": remote_login.status_code,
        "body": remote_login.text,
    }
    config.http_remote_hosted = False
    return report


def main() -> int:
    secret, login_url, src_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, src_dir)
    json.dump(build_report(secret, login_url), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
