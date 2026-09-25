"""Shared-secret gate for the local (non-remote-hosted) control plane.

Everything a local server exposes -- the REST routes under /api, the
streamable-http MCP transport, the plugin WebSocket hub and /register-tools --
is reachable by every process running as this user, and each of them can reach
into a connected Unity Editor or into the tool list the user's AI clients read.
They therefore all check the same secret, and they all check it the same way,
which is what this module is for.

Remote-hosted deployments do not use any of this: they authenticate per request
through ApiKeyService instead.
"""

from __future__ import annotations

import hmac

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.config import config
from core.constants import API_KEY_HEADER, LOCAL_API_TOKEN_ENV

# Well-known path of the file holding the shared secret. Referenced here only to
# make the rejection log actionable; the file is written by the process that
# launches this server, and read back by the Unity Editor package
# (WebSocketTransportClient.ReadLocalApiToken). ~/.unity-mcp is the directory the
# Python and C# sides already share on every platform (port registry, status
# files), which is why the secret lives there too.
LOCAL_API_TOKEN_FILE_HINT = "~/.unity-mcp/local-api-token"


def local_token_matches(provided: str | None) -> bool:
    """True when `provided` is exactly the configured local shared secret.

    Fails closed when no secret is configured: a server without a secret must
    never read "sent nothing" as "sent the right thing".
    """
    expected = config.local_api_token
    if not expected:
        return False
    # compare_digest on bytes, not str: the str overload raises TypeError on
    # non-ASCII input and the value being compared is attacker-controlled.
    return hmac.compare_digest(
        (provided or "").encode("utf-8"), expected.encode("utf-8"))


def require_local_token(request: Request) -> JSONResponse | None:
    """Reject a local HTTP request unless it carries the shared secret.

    Returns the error response to send, or None when the request may proceed.
    """
    if not config.local_api_token:
        return JSONResponse(
            {"success": False,
             "error": f"Local API disabled: {LOCAL_API_TOKEN_ENV} is not set"},
            status_code=503,
        )
    if not local_token_matches(request.headers.get(API_KEY_HEADER)):
        return JSONResponse(
            {"success": False,
             "error": f"Missing or invalid {API_KEY_HEADER} header"},
            status_code=401,
        )
    return None


# The ONLY HTTP requests the local server answers without the shared secret.
#
# Deny by default: before 25 Sep 2026 the gate listed what to PROTECT
# (`path == "/mcp" or path.startswith("/mcp/")`) and let everything else through
# to handlers that were supposed to check the secret themselves. POST /mcp%0A
# decodes to "/mcp\n": that list skipped it, while Starlette's route regex
# ^/mcp$ still matched it (Python's `$` also matches before a trailing newline),
# so initialize and tools/list answered with no key. Listing what is OPEN, and
# comparing by exact equality, closes that class for every path spelling at once.
#
# /health: the desktop app's liveness probe (Frontend TerminalPanel polls it with
# no header). Gating it on 2026-07-27 made the toggle read 401 as "not running"
# and hang with no way to cancel. It returns a fixed blob and touches no Unity
# state. GET and HEAD only: Starlette serves HEAD for every GET route.
#
# WebSocket scopes are not HTTP requests and are left alone: the plugin hub
# (/hub/plugin, /mcp/hub/plugin) authenticates on connect, before accept(), and
# closes with 4401 without the secret (tests/test_local_authz_matrix.py).
OPEN_HTTP_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/health"),
    ("HEAD", "/health"),
})


def is_open_http_route(method: str, path: str) -> bool:
    """Exact (method, path) membership. Never a prefix, a regex or `$`."""
    return (method, path) in OPEN_HTTP_ROUTES


class LocalTokenHeaderMiddleware:
    """ASGI gate: in local mode every HTTP request needs the shared secret in
    the X-API-Key header, except the few listed in OPEN_HTTP_ROUTES.

    It exists so the MCP transport can authenticate by HEADER rather than by a
    secret in its URL. The secret used to ride in the path (`/mcp/<secret>`)
    because header support "had not been verified" across the MCP clients this
    project drives; measured on 2026-07-27, claude, kimi and codex all deliver a
    configured header on every MCP request (codex only under the key
    `http_headers`; the obvious-looking `headers` is accepted and then silently
    dropped). A URL is not a credential container: it lands in workspace
    `.mcp.json` files the model can read, in `ps` output and in log lines. The
    path form is NOT kept as a fallback; an old config fails closed with 401.

    FastMCP applies `middleware=` to the whole Starlette app, so this guards
    every route, not only the transport. /api/* and /register-tools still call
    require_local_token themselves: two checks of the same secret, so a route
    that forgets its own check is still covered, and a handler moved outside
    this app keeps its gate.

    Header lookup is CASE-INSENSITIVE on purpose: claude and codex emit
    `x-api-key`, kimi emits `X-API-Key` (measured 2026-07-27).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or config.http_remote_hosted:
            await self.app(scope, receive, send)
            return

        if is_open_http_route(scope.get("method", ""), scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        # Headers arrive as raw bytes; nothing has lowercased them yet.
        wanted = API_KEY_HEADER.lower().encode("latin-1")
        provided: str | None = None
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() == wanted:
                provided = raw_value.decode("latin-1")
                break

        if not local_token_matches(provided):
            response = JSONResponse(
                {"success": False,
                 "error": f"Missing or invalid {API_KEY_HEADER}. This local "
                          f"server requires the shared secret from "
                          f"{LOCAL_API_TOKEN_FILE_HINT}."},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
