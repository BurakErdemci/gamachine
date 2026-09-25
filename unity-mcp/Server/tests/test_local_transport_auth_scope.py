"""The header gate is deny-by-default, and /health is the one door left open.

Two failures shaped this file, one in each direction:

* Too WIDE (2026-07-27, live-only): the first gate also guarded /health, which
  the desktop app polls to decide whether the server is up. /health answered
  401, the liveness probe read that as "not running", and the product's toggle
  spun forever. None of the suites asserted that the open route stayed open.
* Too NARROW (2026-09-25 audit): the gate listed the paths to PROTECT
  (`== "/mcp"` or `startswith("/mcp/")`). POST /mcp%0A decodes to "/mcp\n",
  which that list skipped while Starlette's ^/mcp$ route regex still matched
  it, so initialize and tools/list answered with no key. The gate now lists
  what is OPEN and compares by exact equality.

The same corpus runs over a real uvicorn socket in test_transport_path_corpus.py.
"""

import asyncio

import pytest

from core.config import config
from core.local_auth import LocalTokenHeaderMiddleware

SECRET = "test-shared-secret-value"


class _Sentinel:
    """Stands in for the wrapped app; records that the request got through."""

    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def _run(path, headers=(), scope_type="http", method="POST"):
    inner = _Sentinel()
    mw = LocalTokenHeaderMiddleware(inner)
    status = {}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": scope_type, "method": method, "path": path,
             "headers": list(headers)}
    asyncio.run(mw(scope, receive, send))
    return inner.called, status.get("code")


@pytest.fixture(autouse=True)
def _local_mode(monkeypatch):
    monkeypatch.setattr(config, "local_api_token", SECRET, raising=False)
    monkeypatch.setattr(config, "http_remote_hosted", False, raising=False)


# ── The gate must not be too WIDE (the bug that shipped) ────────────────────

@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_health_stays_open(method):
    """The toggle's liveness probe depends on it."""
    passed_through, _ = _run("/health", method=method)
    assert passed_through, f"{method} /health was blocked by the gate"


# ── Deny by default: everything else needs the secret ──────────────────────

# Every spelling here was either a real bypass (/mcp%0A, measured 25 Sep 2026)
# or a neighbour of one. The gate sees the DECODED path, which is what these are.
UNLISTED_PATHS = [
    "/", "/api/instances", "/api/command", "/api/custom-tools",
    "/api/auth/login-url", "/register-tools", "/hub/plugin",
    "/mcp\n", "/mcp\r", "/mcp\r\n", "/mcp\x00", "/mcp/", "//mcp", "/MCP",
    "/Mcp", "/mcp/full\n", "/mcp/../mcp", "/mcp/not-a-profile",
    "/health\n", "/health/", "/Health", "//health", "/health\x00",
]


@pytest.mark.parametrize("path", UNLISTED_PATHS)
def test_unlisted_path_without_header_is_rejected(path):
    passed_through, code = _run(path)
    assert not passed_through, f"{path!r} got through without the secret"
    assert code == 401


def test_health_by_another_method_is_not_open():
    """Open means GET/HEAD /health, not the path under any method."""
    passed_through, code = _run("/health", method="POST")
    assert not passed_through
    assert code == 401


@pytest.mark.parametrize("path", ["/api/instances", "/register-tools", "/mcp/full"])
def test_unlisted_path_with_header_goes_through(path):
    passed_through, _ = _run(path, [(b"x-api-key", SECRET.encode())])
    assert passed_through


def test_websocket_scope_is_not_touched():
    """/mcp/hub/plugin lives under the transport prefix but is a WebSocket and
    authenticates on connect in plugin_hub. Blocking it here would break the
    Unity Editor's bridge."""
    passed_through, _ = _run("/mcp/hub/plugin", scope_type="websocket")
    assert passed_through


# ── The gate must not be too NARROW either ─────────────────────────────────

def test_transport_without_header_is_rejected():
    passed_through, code = _run("/mcp")
    assert not passed_through
    assert code == 401


def test_transport_with_wrong_header_is_rejected():
    passed_through, code = _run("/mcp", [(b"x-api-key", b"wrong")])
    assert not passed_through
    assert code == 401


def test_transport_with_correct_header_is_allowed():
    """Without this direction a middleware that rejects everything would pass."""
    passed_through, _ = _run("/mcp", [(b"x-api-key", SECRET.encode())])
    assert passed_through


@pytest.mark.parametrize("name", [b"X-API-Key", b"x-api-key", b"X-Api-Key"])
def test_header_name_is_case_insensitive(name):
    """Measured 2026-07-27: claude and codex send `x-api-key`, kimi sends
    `X-API-Key`. A case-sensitive lookup would have locked out two clients of
    three, and only in the field."""
    passed_through, _ = _run("/mcp", [(name, SECRET.encode())])
    assert passed_through, f"{name!r} was rejected"


def test_old_secret_in_path_no_longer_authenticates():
    """The path form was removed on purpose; keeping it would preserve the leak
    this gate exists to remove. An old config must fail closed, not work."""
    passed_through, code = _run(f"/mcp/{SECRET}")
    assert not passed_through
    assert code == 401
