"""Odd request paths over a real uvicorn socket: no key, no transport.

WHICH FAILURE IT CAME FROM
    Audit 25 Sep 2026: POST /mcp%0A decodes to "/mcp\\n". The header gate
    protected `== "/mcp"` or `startswith("/mcp/")`, so it skipped that path; the
    profile rewrite skipped it too; and Starlette's route regex ^/mcp$ still
    matched it, because Python's `$` also matches before a trailing newline.
    initialize and tools/list (65 tools) answered with no X-API-Key.

WHAT IT PINS
    * Without the key, every spelling answers 401 (deny by default) except
      GET/HEAD /health, the one open route.
    * With the key, only the exact transport paths initialize a session; every
      other /mcp spelling is 404 and never reaches the transport.

The unit-level versions of the same corpus are in
test_local_transport_auth_scope.py and test_tool_profiles.py. This file sends
the request line byte for byte (_path_corpus_probe.py, a child interpreter), so
it also covers how uvicorn decodes the path.
"""

import json
import pathlib
import subprocess
import sys

import pytest

PROBE = pathlib.Path(__file__).resolve().parent / "_path_corpus_probe.py"
SERVER_DIR = PROBE.parent.parent
SRC_DIR = SERVER_DIR / "src"

TRANSPORT_PATHS = ["/mcp", "/mcp/gamachine", "/mcp/full", "/mcp/full/"]

# Written on the wire exactly as listed (percent-encoding included).
ODD_PATHS = [
    "/mcp%0A", "/mcp%0D", "/mcp%0D%0A", "/mcp%00", "/mcp%09", "/mcp%20",
    "//mcp", "/MCP", "/Mcp", "/mcp/full%0A", "/mcp/../mcp",
    "/mcp/not-a-profile", "/mcp//full", "/mcp/full//", "/mcpx", "/hub/plugin",
]

# "/mcp/" stays FastMCP's: Starlette redirects it to /mcp (307) and the client
# follows with its key. Not a transport answer in itself.
TRAILING_SLASH_BASE = "/mcp/"


@pytest.fixture(scope="module")
def report():
    paths = TRANSPORT_PATHS + ODD_PATHS + [TRAILING_SLASH_BASE]
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(SRC_DIR), *paths],
        cwd=str(SERVER_DIR), capture_output=True, text=True, timeout=180,
    )
    assert completed.returncode == 0, (
        f"path corpus probe failed (exit {completed.returncode}):\n"
        f"{completed.stderr[-4000:]}"
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("path", TRANSPORT_PATHS + ODD_PATHS + [TRAILING_SLASH_BASE])
def test_no_path_answers_without_the_key(report, path):
    observed = report["anonymous"][path]
    assert observed["status"] == 401, f"{path}: {observed}"
    assert not observed["initialized"], f"{path} initialized a session with no key"


@pytest.mark.parametrize("path", TRANSPORT_PATHS)
def test_exact_transport_paths_initialize_with_the_key(report, path):
    """Without this direction a server that refuses everything would pass."""
    observed = report["keyed"][path]
    assert observed["status"] == 200 and observed["initialized"], f"{path}: {observed}"


@pytest.mark.parametrize("path", ODD_PATHS)
def test_odd_spellings_never_reach_the_transport_even_with_the_key(report, path):
    observed = report["keyed"][path]
    assert observed["status"] == 404, f"{path}: {observed}"
    assert not observed["initialized"], f"{path} was served as the transport"


def test_bare_trailing_slash_redirects_rather_than_serving(report):
    observed = report["keyed"][TRAILING_SLASH_BASE]
    assert observed["status"] == 307, observed
    assert not observed["initialized"]


@pytest.mark.parametrize("request_line", ["GET /health", "HEAD /health"])
def test_health_stays_open(report, request_line):
    assert report["health"][request_line]["status"] == 200


@pytest.mark.parametrize("request_line", ["GET /health%0A", "GET /Health"])
def test_only_the_exact_health_path_is_open(report, request_line):
    assert report["health"][request_line]["status"] == 401
