"""The approval card names the Gamachine conversation the call works for.

Parallel chats run at once, and a card from this gate reached the UI with no
owner: Stop in one chat could not tell its own cards from another chat's. The
gate now forwards the caller's claim as `conversation_id` in the request body.

Where the claim may come from: the connection (X-Gamachine-Conversation header
or ?conv= on the MCP URL) or the call's _meta (`gamachine_conversation`), for a
client that shares one session across chats. Never from tool arguments: the
model writes those. A wrong id is worse than none, so junk or disagreeing
values drop the claim instead of guessing.

The middleware reads fastmcp.server.dependencies at call time; these tests put
a fake of that module in sys.modules, because in a full run
tests/integration/conftest.py may have stubbed fastmcp.server without it. The
real wiring over HTTP, both protocol eras, is test_live_approval_owner.py.
"""

import asyncio
import contextvars
import sys
import types

import pytest

from transport import approval_gate
from transport.approval_gate import parse_conversation_id


@pytest.mark.parametrize("value,expected", [
    (7, 7), ("7", 7), ("123456", 123456),
])
def test_positive_ints_parse(value, expected):
    assert parse_conversation_id(value) == expected


@pytest.mark.parametrize("value", [
    None, "", "0", 0, -3, "-3", "+7", " 7", "7 ", "7.0", 7.0, "abc", "7abc",
    True, False, "٧", "²", [7], {"id": 7}, 2**63, str(2**63),
])
def test_junk_is_refused(value):
    assert parse_conversation_id(value) is None


# ── the request body ─────────────────────────────────────────────────────────


class _RecordingClient:
    """httpx.AsyncClient stand-in: records each POST body, resolves at once."""

    bodies: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _RecordingClient.bodies.append(json)
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "resolved", "approved": True},
        )


@pytest.fixture
def bodies(monkeypatch):
    _RecordingClient.bodies = []
    monkeypatch.setattr(approval_gate.httpx, "AsyncClient", _RecordingClient)
    return _RecordingClient.bodies


def test_gate_puts_a_valid_id_in_the_body(bodies):
    asyncio.run(approval_gate.kapiyi_gec(
        "manage_gameobject", {"action": "create"}, conversation_id=41))
    assert bodies[0]["conversation_id"] == 41
    assert bodies[0]["tool"] == "manage_gameobject"


@pytest.mark.parametrize("value", [None, 0, -1, True, "junk", "4.0"])
def test_gate_omits_anything_but_a_positive_int(bodies, value):
    asyncio.run(approval_gate.kapiyi_gec(
        "manage_gameobject", {"action": "create"}, conversation_id=value))
    assert "conversation_id" not in bodies[0]
    assert bodies[0]["tool"] == "manage_gameobject"


def test_an_id_in_tool_arguments_is_NOT_an_owner(bodies):
    asyncio.run(approval_gate.kapiyi_gec(
        "manage_gameobject",
        {"action": "create", "conversation_id": 9, "gamachine_conversation": 9}))
    assert "conversation_id" not in bodies[0]
    # The argument still shows on the card as what the model sent.
    assert bodies[0]["params"]["conversation_id"] == 9


def test_a_read_call_asks_nothing(bodies):
    asyncio.run(approval_gate.kapiyi_gec(
        "read_console", {"action": "get"}, conversation_id=41))
    assert bodies == []


# ── the middleware: which sources count ──────────────────────────────────────


class _Request:
    """Starlette's own containers, so repeated keys behave as on a real request."""

    def __init__(self, headers=None, query=None, raw_headers=None):
        from starlette.datastructures import Headers, QueryParams

        if raw_headers is not None:
            self.headers = Headers(raw=[(k.lower().encode(), v.encode()) for k, v in raw_headers])
        else:
            self.headers = Headers(headers=headers or {})
        self.query_params = QueryParams(query or {})


def _install_dependencies(monkeypatch, request=None, meta=None):
    """A fastmcp.server.dependencies with the two names the middleware reads."""
    module = types.ModuleType("fastmcp.server.dependencies")

    def get_http_request():
        if request is None:
            raise RuntimeError("no HTTP request (stdio)")
        return request

    ctx_var = contextvars.ContextVar("fastmcp_request_ctx", default=None)
    if meta is not None:
        ctx_var.set(types.SimpleNamespace(meta=meta))
    module.get_http_request = get_http_request
    module.fastmcp_request_ctx = ctx_var
    monkeypatch.setitem(sys.modules, "fastmcp.server.dependencies", module)


def _read(monkeypatch, **sources):
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    _install_dependencies(monkeypatch, **sources)
    return UnityInstanceMiddleware._request_conversation_id()


def test_from_header(monkeypatch):
    assert _read(monkeypatch, request=_Request(
        headers={"X-Gamachine-Conversation": "12"})) == 12


def test_from_query(monkeypatch):
    assert _read(monkeypatch, request=_Request(query={"conv": "13"})) == 13


def test_from_meta(monkeypatch):
    assert _read(monkeypatch, meta={"gamachine_conversation": 14}) == 14


def test_from_meta_on_stdio_without_any_http_request(monkeypatch):
    assert _read(monkeypatch, request=None, meta={"gamachine_conversation": "15"}) == 15


def test_agreeing_sources_are_accepted(monkeypatch):
    assert _read(monkeypatch,
                 request=_Request(headers={"X-Gamachine-Conversation": "16"},
                                  query={"conv": "16"}),
                 meta={"gamachine_conversation": 16}) == 16


@pytest.mark.parametrize("request_,meta", [
    (_Request(headers={"X-Gamachine-Conversation": "16"}), {"gamachine_conversation": 17}),
    (_Request(headers={"X-Gamachine-Conversation": "16"}, query={"conv": "17"}), None),
])
def test_disagreeing_sources_drop_the_claim(monkeypatch, request_, meta):
    assert _read(monkeypatch, request=request_, meta=meta) is None


@pytest.mark.parametrize("header", ["abc", "0", "-4", "4.0", ""])
def test_junk_header_drops_the_claim_even_beside_a_valid_meta(monkeypatch, header):
    assert _read(monkeypatch,
                 request=_Request(headers={"X-Gamachine-Conversation": header}),
                 meta={"gamachine_conversation": 4}) is None


def test_a_unicode_digit_in_the_query_drops_the_claim(monkeypatch):
    # HTTP header values are latin-1, so a non-ASCII digit can only arrive in
    # the URL, where it is percent-encoded UTF-8.
    from starlette.datastructures import QueryParams

    request = _Request()
    request.query_params = QueryParams("conv=%D9%A4")
    assert request.query_params.getlist("conv") == ["٤"]
    assert _read(monkeypatch, request=request, meta={"gamachine_conversation": 4}) is None


def test_an_explicit_null_meta_owner_drops_the_claim(monkeypatch):
    header = _Request(headers={"X-Gamachine-Conversation": "41"})
    assert _read(monkeypatch, request=header, meta={"gamachine_conversation": None}) is None
    assert _read(monkeypatch, request=header, meta={"progressToken": 3}) == 41


@pytest.mark.parametrize("values,expected", [
    (["41", "junk"], None),
    (["41", "42"], None),
    (["41", "41"], 41),
])
def test_every_repeated_header_line_is_a_source(monkeypatch, values, expected):
    request = _Request(raw_headers=[("X-Gamachine-Conversation", v) for v in values])
    assert _read(monkeypatch, request=request) is expected


def test_every_repeated_query_value_is_a_source(monkeypatch):
    from starlette.datastructures import QueryParams

    request = _Request()
    request.query_params = QueryParams("conv=41&conv=junk")
    assert _read(monkeypatch, request=request) is None


def test_nothing_claimed(monkeypatch):
    assert _read(monkeypatch, request=_Request(), meta={"progressToken": 3}) is None


def test_no_dependencies_module_means_no_claim(monkeypatch):
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    monkeypatch.setitem(sys.modules, "fastmcp.server.dependencies", None)
    assert UnityInstanceMiddleware._request_conversation_id() is None


def test_middleware_hands_the_claim_to_the_gate_and_not_the_arguments(monkeypatch):
    """Through on_call_tool: the header's id reaches the body, the argument's does not."""
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    mw = UnityInstanceMiddleware()

    async def no_injection(_ctx):
        return None

    monkeypatch.setattr(mw, "_inject_unity_instance", no_injection)
    _install_dependencies(monkeypatch, request=_Request(
        headers={"X-Gamachine-Conversation": "21"}))
    seen = []

    async def ask(tool_name, params, conversation_id=None):
        seen.append((tool_name, conversation_id, dict(params)))
        return {"approved": True}

    monkeypatch.setattr(approval_gate, "_onay_iste", ask)

    context = types.SimpleNamespace(message=types.SimpleNamespace(
        name="manage_gameobject",
        arguments={"action": "create", "conversation_id": 99}))

    async def call_next(_ctx):
        return "REACHED UNITY"

    assert asyncio.run(mw.on_call_tool(context, call_next)) == "REACHED UNITY"
    assert seen[0][1] == 21
    assert seen[0][2]["conversation_id"] == 99
