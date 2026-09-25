"""Authorization matrix -- every route this server serves x the identity axis.

WHAT THIS PROTECTS
    The whole local HTTP surface of the server built by `create_mcp_server()`:
    the streamable-http MCP transport, the REST control plane under /api, the
    /register-tools injection route and the plugin WebSocket hub. Each is probed
    with no credential, a wrong credential and a valid one.

WHICH FAILURE IT CAME FROM
    Two serious faults shipped on 2026-07-27, both missed by a green suite of
    1462 tests, both found by hand with curl against a live server:

      1. LocalTokenHeaderMiddleware is handed to FastMCP as `middleware=`, and
         FastMCP applies that to the entire Starlette app rather than to the
         transport mount. /health -- deliberately unauthenticated, because the
         desktop app polls it to decide whether the server is up -- began
         answering 401. The liveness probe read that as "not running" and the
         product's toggle spun forever with no way to cancel it.
      2. The `/mcp/<secret>` path form was believed to be gone. Measured, `/mcp`
         answered 404 and `/mcp/<secret>` answered 200 -- the entire day's
         security work was silently inert.

    One root cause: every existing test asserted the gate was not too NARROW.
    None asserted it was not too WIDE. The too-wide direction is the one nobody
    writes a test for, and it is the direction that broke the product.

RELATIONSHIP TO test_local_transport_auth_scope.py
    That file drives LocalTokenHeaderMiddleware directly with synthetic ASGI
    scopes: it pins the middleware's SIZE. This file never touches the
    middleware. It builds the real app, walks its real routing table and sends
    real requests, so it also sees what the middleware cannot: a route that is
    registered but ungated, a route that disappears, a route added tomorrow.
    Neither subsumes the other -- fault 1 was a middleware-size bug, fault 2 was
    a routing bug.

WHERE THE MATRIX COMES FROM
    Endpoints are read from the built application's own routing table, not from
    a hand-written list, and every route found there is probed with every
    credential. A hand-written list cannot see a route added tomorrow, so the
    suite would stay green while protecting less than it claims.

    The build and the probing happen in a child interpreter
    (`_authz_matrix_probe.py`) because the integration suite's stubs make the
    real server unbuildable in-process; the reasons, and the three alternatives
    that were measured and rejected, are documented in that file. No port is
    bound -- the child uses Starlette's in-process test client.

CLASSIFICATION RULE (rule plus rationale)
    Every path in the routing table must appear in exactly one of the tables
    below -- PROTECTED_ROUTES, WEBSOCKET_ROUTES or DELIBERATELY_OPEN -- and an
    unclassified path fails the suite. That forces whoever adds a route to
    either gate it or write down, with a reason, that it is open on purpose. A
    stale entry pointing at a route that no longer exists fails too: a leftover
    exemption silently re-opens the door if that path ever comes back.

    Classification is by PATH rather than by (method, path), because here every
    path has exactly one handler.

WHAT IS NOT COVERED
    * Remote-hosted mode (`config.http_remote_hosted`), which authenticates per
      request through ApiKeyService on a different code path. This file pins the
      LOCAL surface -- the one every process on the user's machine can reach.
    * Whether a handler does work BEFORE its gate. The matrix proves the gate
      answers, not that it answers first.
    * Which identity a valid credential maps to. Local mode has one shared
      secret and no per-user identity, so there is nothing to separate.
"""

import json
import pathlib
import subprocess
import sys

import pytest

SECRET = "authz-matrix-shared-secret"
LOGIN_URL = "https://example.invalid/keys?tenant=acme"

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = SERVER_DIR / "src"
PROBE = pathlib.Path(__file__).resolve().parent / "_authz_matrix_probe.py"

# Application-level close code sent by plugin_hub when the handshake carries no
# usable key. A real HTTP client sees the rejected upgrade as 403; in-process
# the test client surfaces the close code itself, so that is what is asserted.
WS_UNAUTHORIZED_CLOSE_CODE = 4401


# ── PROTECTED: every one of these must answer 401 without the shared secret ──
# The value is the reason the route needs the gate, not a description of it.
PROTECTED_ROUTES: dict = {
    "/mcp":
        "The streamable-http transport, and by far the widest surface here: "
        "every tool is reachable through it, including execute_code, which runs "
        "arbitrary C# inside a connected Unity Editor.",
    "/api/command":
        "Forwards arbitrary command types to Unity, execute_code among them.",
    "/api/instances":
        "Discloses which Unity projects are open on this machine.",
    "/api/custom-tools":
        "Discloses the tool list registered for the active project.",
    "/register-tools":
        "Tool INJECTION: whatever is posted here becomes a tool the user's AI "
        "clients can see and call.",
    "/api/auth/login-url":
        "Under /api, which core.local_auth's module docstring states is guarded "
        "by the shared secret. It was the one route the gate missed (found "
        "2026-07-27, gated the same day); upstream leaves it open, but upstream "
        "has no local-secret gate at all, so there was nothing to be consistent "
        "with. Nothing bootstraps through it: the secret comes from a 0600 file.",
}

WEBSOCKET_ROUTES: dict = {
    "/hub/plugin":
        "The Unity Editor's bridge. An unauthenticated socket here is a "
        "connected Editor under someone else's control.",
    "/mcp/hub/plugin":
        "Same endpoint under the transport prefix, kept for Editor packages "
        "that build the URL from the transport base path.",
}

# ── DELIBERATELY OPEN: short, and every line carries its reason ──────────────
DELIBERATELY_OPEN: dict = {
    "/health":
        "The desktop app's liveness probe. This exact route was accidentally "
        "gated on 2026-07-27; the toggle read 401 as 'not running' and hung "
        "forever with no way to cancel. Closing it breaks the product. It "
        "returns a fixed status blob and touches no Unity state.",
}

# /api/auth/login-url used to be the one open question here: it was classified
# PROTECTED (that is what core.local_auth claims about /api) while the code left
# it open, so its two rejection probes carried a strict xfail. Gating the route
# turned them XPASS and failed the file, which is exactly what a strict marker is
# for -- a finding must not rot into an accepted behaviour. The marker is gone
# because the finding is; the route now rides the normal parametrisation below.
REJECTION_PARAMS = [pytest.param(path, id=path) for path in PROTECTED_ROUTES]
PROTECTED_IDS = list(PROTECTED_ROUTES)
WEBSOCKET_IDS = list(WEBSOCKET_ROUTES)
OPEN_IDS = list(DELIBERATELY_OPEN)


@pytest.fixture(scope="session")
def matrix():
    """The probe's report, produced once per session."""
    completed = subprocess.run(
        [sys.executable, str(PROBE), SECRET, LOGIN_URL, str(SRC_DIR)],
        cwd=str(SERVER_DIR), capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, (
        f"the authorization probe failed to build the server "
        f"(exit {completed.returncode}):\n{completed.stderr[-4000:]}"
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:      # pragma: no cover - diagnostic path
        raise AssertionError(
            f"probe emitted no usable JSON: {exc}\n"
            f"stdout tail: {completed.stdout[-2000:]}\n"
            f"stderr tail: {completed.stderr[-2000:]}"
        ) from exc


# ── 1. Table integrity ──────────────────────────────────────────────────────

def test_every_route_in_the_application_is_classified(matrix):
    """No path in the routing table is missing from the tables above."""
    live = {route["path"] for route in matrix["routes"]}
    assert live, "the app exposes no routes at all -- the matrix guards nothing"
    classified = set(PROTECTED_ROUTES) | set(WEBSOCKET_ROUTES) | set(DELIBERATELY_OPEN)
    unclassified = sorted(live - classified)
    assert not unclassified, (
        f"unclassified route(s): {unclassified}. Add each to PROTECTED_ROUTES, "
        f"or to DELIBERATELY_OPEN with the reason it may stay open."
    )


def test_no_table_entry_points_at_a_route_that_no_longer_exists(matrix):
    """A stale exemption is worse than no exemption: it quietly re-opens the
    door for whoever brings that path back."""
    live = {route["path"] for route in matrix["routes"]}
    classified = set(PROTECTED_ROUTES) | set(WEBSOCKET_ROUTES) | set(DELIBERATELY_OPEN)
    stale = sorted(classified - live)
    assert not stale, f"table entries for routes that are gone: {stale}"


def test_every_classified_route_carries_a_reason():
    for table in (PROTECTED_ROUTES, WEBSOCKET_ROUTES, DELIBERATELY_OPEN):
        for path, reason in table.items():
            assert reason.strip(), f"{path} is classified with an empty reason"


def test_the_open_list_stays_short():
    """The open list is the list of ways around the gate. Growth is an alarm,
    not a budget: each addition has to be argued for in place."""
    assert len(DELIBERATELY_OPEN) <= 3


# ── 2. The gate must not be too WIDE ────────────────────────────────────────

def _entries_for(matrix, path):
    """EVERY method registered on this path, never just one of them.

    The probe keys observations by "METHOD path". Asserting on a single entry
    per path was a measured hole (2026-07-27): a second route on an
    already-classified path -- an ungated PUT next to a gated GET -- was
    invisible, because the later registration overwrote the earlier one's
    result and every test stayed green. Classification is per path, so every
    method on that path has to satisfy it.
    """
    entries = [(key, value) for key, value in matrix["http"].items()
               if value["path"] == path]
    assert entries, f"{path} is classified but the probe observed no route for it"
    return entries


@pytest.mark.parametrize("path", REJECTION_PARAMS)
def test_protected_route_rejects_a_request_with_no_credential(matrix, path):
    """An anonymous request reaches no protected route, by ANY method."""
    for key, entry in _entries_for(matrix, path):
        assert entry["anonymous"] == 401, (
            f"{key} answered {entry['anonymous']} to an anonymous request. "
            f"It is classified protected because: {PROTECTED_ROUTES[path]}"
        )


@pytest.mark.parametrize("path", REJECTION_PARAMS)
def test_protected_route_rejects_a_request_with_an_invalid_credential(matrix, path):
    """A wrong key is rejected too -- a route that reads the header without
    comparing it would pass the anonymous check and fail only here."""
    for key, entry in _entries_for(matrix, path):
        assert entry["invalid"] == 401, (
            f"{key} answered {entry['invalid']} to a wrong key, expected 401"
        )


@pytest.mark.parametrize("path", WEBSOCKET_IDS, ids=WEBSOCKET_IDS)
@pytest.mark.parametrize("credential", ["anonymous", "invalid"])
def test_plugin_websocket_refuses_a_handshake_without_the_shared_secret(
        matrix, path, credential):
    """The Editor bridge authenticates on connect, before accept().

    It is asserted here as well as in plugin_hub's own tests because the socket
    is part of the same local surface: a reader of this matrix must be able to
    see that /hub/plugin is covered without going to find another file.
    """
    assert matrix["websocket"][path][credential] == WS_UNAUTHORIZED_CLOSE_CODE


def test_the_removed_secret_in_path_transport_no_longer_authenticates(matrix):
    """`/mcp/<secret>` is gone, checked through the real router.

    The middleware-level test already asserts this shape. This is not a
    duplicate: it goes through the assembled application, so it also fails if
    somebody re-registers a route under that prefix -- which is exactly how
    fault 2 survived. Keeping the path form would preserve the very leak the
    header gate exists to remove, so an old config must fail closed.
    """
    assert matrix["legacy_secret_path_status"] == 401


def test_the_local_rest_routes_are_not_even_registered_without_a_secret(matrix):
    """With no shared secret configured, /api/command, /api/instances and
    /api/custom-tools do not exist at all.

    Fail-closed by non-registration rather than by a runtime check: a route that
    is never mounted cannot be reached by a later refactor that moves an auth
    check around. /register-tools is mounted unconditionally and answers 503 --
    also closed, by the other mechanism.
    """
    closed = matrix["without_secret"]
    assert "/api/command" not in closed["routes"]
    assert "/api/instances" not in closed["routes"]
    assert "/api/custom-tools" not in closed["routes"]
    assert closed["register_tools_status"] == 503


# ── 3. The gate must not be too NARROW ──────────────────────────────────────

# What a VALID credential may legitimately be answered with, per route.
#
# This used to be a blanket "anything except 401 and 403". Measured 2026-07-27:
# that oracle is permissive enough to certify a broken gate -- make
# require_local_token answer 500 to a MATCHING token and every REST route returns
# 500 while all 34 tests stay green. An allow-list of expected statuses is the
# narrowest form that still tolerates the environment-dependent answers below.
_VALID_DEFAULT = frozenset({200, 400, 503})
VALID_CREDENTIAL_EXPECTED: dict = {
    # 200 when a Unity Editor is connected, 400/503 when none is -- the tests do
    # not run an Editor, and both answers prove the request got past the gate.
    "/api/command": _VALID_DEFAULT,
    "/api/instances": _VALID_DEFAULT,
    "/api/custom-tools": _VALID_DEFAULT,
    "/register-tools": _VALID_DEFAULT,
    "/api/auth/login-url": _VALID_DEFAULT,
    # The ONE tolerated 5xx, and it is an artifact of the harness rather than of
    # the code: the streamable-http session manager needs an app lifespan the
    # in-process test client never runs. Written down here so the tolerance is
    # bounded to this route instead of being granted to the whole table.
    "/mcp": frozenset({500}),
}


@pytest.mark.parametrize("path", PROTECTED_IDS, ids=PROTECTED_IDS)
def test_protected_route_lets_a_valid_credential_through(matrix, path):
    """Without this direction a gate that rejects everything would pass."""
    expected = VALID_CREDENTIAL_EXPECTED.get(path)
    assert expected is not None, (
        f"{path} is protected but has no expected-status entry. Add one with a "
        f"reason rather than widening the check."
    )
    for key, entry in _entries_for(matrix, path):
        assert entry["valid"] in expected, (
            f"{key} answered {entry['valid']} to a VALID credential; "
            f"expected one of {sorted(expected)}."
        )


def test_every_tool_profile_path_is_behind_the_same_gate(matrix):
    """/mcp/<profile> serves the same tools as /mcp, execute_code included, so
    it needs the same secret. Unknown names must answer 401 too, before the
    404: an anonymous caller must not learn which profile names exist."""
    profile_paths = matrix["profile_paths"]
    assert "/mcp/gamachine" in profile_paths and "/mcp/full" in profile_paths
    for path, statuses in profile_paths.items():
        assert statuses["anonymous"] == 401, f"{path}: {statuses}"
        assert statuses["invalid"] == 401, f"{path}: {statuses}"
        if path == "/mcp/not-a-profile":
            assert statuses["valid"] == 404, f"{path}: {statuses}"
        else:
            # Same harness artifact as /mcp in VALID_CREDENTIAL_EXPECTED.
            assert statuses["valid"] in VALID_CREDENTIAL_EXPECTED["/mcp"], f"{path}: {statuses}"


@pytest.mark.parametrize("header_name", ["X-API-Key", "x-api-key", "X-Api-Key"])
def test_the_api_key_header_is_matched_case_insensitively_end_to_end(matrix, header_name):
    """Measured 2026-07-27: claude and codex send `x-api-key`, kimi sends
    `X-API-Key`. A case-sensitive lookup would have locked out two clients of
    three, and only in the field. Checked through the assembled app so it covers
    both gates: the middleware on /mcp, and require_local_token on /api/*."""
    observed = matrix["header_case"][header_name]
    assert observed["/api/instances"] != 401, f"{header_name} rejected by require_local_token"
    assert observed["/mcp"] != 401, f"{header_name} rejected by the transport middleware"


# ── 4. Deliberately open routes must STAY open ──────────────────────────────

@pytest.mark.parametrize("path", OPEN_IDS, ids=OPEN_IDS)
def test_deliberately_open_route_still_answers_without_a_credential(matrix, path):
    """This is not a finding, it is a decision being pinned -- and it asks the
    question the whole 2026-07-27 outage came from: did something that must stay
    open get closed?"""
    for key, entry in _entries_for(matrix, path):
        assert entry["anonymous"] not in (401, 403), (
            f"{key} answered {entry['anonymous']} without a credential. "
            f"It is open on purpose: {DELIBERATELY_OPEN[path]}"
        )


def test_the_health_probe_the_product_depends_on_answers_200_and_says_healthy(matrix):
    """/health returns 200 and `status: healthy` with no credential.

    Named separately from the generic check because the desktop toggle reads the
    body, not just the status line: answering 200 with a changed payload breaks
    the product the same way answering 401 did.
    """
    for key, entry in _entries_for(matrix, "/health"):
        assert entry["anonymous"] == 200, f"{key} answered {entry['anonymous']}"
    assert matrix["health_body_status"] == "healthy"


# ── 5. The closed finding, pinned by what it must never disclose again ──────

def test_the_login_url_route_discloses_nothing_to_an_anonymous_caller(matrix):
    """The configured login URL must not appear in an unauthenticated response.

    This is the regression for the 2026-07-27 finding. Its earlier form recorded
    the opposite -- that an anonymous GET returned 200 and the URL -- so that the
    finding could not be argued about from memory. Gating the route flipped it,
    which is why the assertion is written against the BODY and not just the
    status: a future refactor that answers 200 with an empty payload would still
    be wrong, and a status-only check would call it fixed.
    """
    anonymous = matrix["login_url_anonymous"]
    assert anonymous["status"] == 401, (
        f"/api/auth/login-url answered {anonymous['status']} without a "
        f"credential; every /api route is gated (see core.local_auth)."
    )
    assert LOGIN_URL not in anonymous["body"]


def test_the_login_url_route_stays_open_in_remote_hosted_mode(matrix):
    """The other direction of the same gate, and the one that broke the product.

    Gating /api/auth/login-url was correct for local mode and WRONG everywhere
    else: remote-hosted deployments authenticate through ApiKeyService and leave
    `local_api_token` unset on purpose, so require_local_token answered 503 to
    every caller -- including the Unity package's "Get API Key" button, which
    sends no header by design because the whole point of the route is to tell
    someone who has NO key where to obtain one. Found by an outside audit on
    2026-07-27, after the local-mode test above had been green for hours.

    This is the third time in this project that a correct-looking gate was made
    too WIDE, so the pair of tests is deliberate: the one above proves the gate
    exists, this one proves it stops at the edge of local mode."""
    remote = matrix["remote_hosted_login"]
    assert remote["status"] == 200, (
        f"remote-hosted /api/auth/login-url answered {remote['status']}; a "
        f"keyless caller must be able to learn where to get a key. Body: "
        f"{remote['body'][:200]}"
    )
    assert LOGIN_URL in remote["body"], (
        "remote-hosted login bootstrap answered 200 but without the configured "
        "URL -- status alone would have called this fixed."
    )
