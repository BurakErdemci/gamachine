"""tools/list_changed reaches already-connected clients on both MCP SDKs.

Replaces a monkeypatch of fastmcp.server.low_level.MiddlewareServerSession,
removed in FastMCP 4 (the P1 spike crashed at startup on that import). The
middleware now records, per SDK:
  * SDK 1: the ServerSession, which lives as long as the connection;
  * SDK 2: the session's Connection, only when it has a standalone channel
    (a 2026-07-28 connection without one would drop the notification).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from transport import plugin_hub
from transport.plugin_hub import McpSessionTrackingMiddleware, PluginHub


@pytest.fixture(autouse=True)
def _empty_tracking():
    plugin_hub._active_mcp_sessions.clear()
    yield
    plugin_hub._active_mcp_sessions.clear()


class _Sdk1Session:
    def __init__(self):
        self.send_tool_list_changed = AsyncMock()


class _Sdk2Connection:
    def __init__(self, channel: bool):
        self.has_standalone_channel = channel
        self.send_tool_list_changed = AsyncMock()


class _Sdk2Session:
    """Built per request in SDK 2; the connection is what persists."""

    def __init__(self, connection):
        self._connection = connection


def _message(session):
    return SimpleNamespace(fastmcp_context=SimpleNamespace(session=session))


def _track(session):
    call_next = AsyncMock(return_value="next")
    result = asyncio.run(McpSessionTrackingMiddleware().on_message(_message(session), call_next))
    assert result == "next"
    call_next.assert_awaited_once()


def test_sdk1_session_is_tracked_and_notified():
    session = _Sdk1Session()
    _track(session)
    asyncio.run(PluginHub._notify_mcp_tool_list_changed())
    session.send_tool_list_changed.assert_awaited_once()


def test_sdk2_connection_with_a_channel_is_tracked_once_across_requests():
    connection = _Sdk2Connection(channel=True)
    _track(_Sdk2Session(connection))
    _track(_Sdk2Session(connection))
    assert list(plugin_hub._active_mcp_sessions) == [connection]
    asyncio.run(PluginHub._notify_mcp_tool_list_changed())
    connection.send_tool_list_changed.assert_awaited_once()


def test_sdk2_connection_without_a_channel_is_not_tracked():
    _track(_Sdk2Session(_Sdk2Connection(channel=False)))
    assert len(plugin_hub._active_mcp_sessions) == 0


def test_tracking_never_blocks_the_request():
    class Broken:
        @property
        def session(self):
            raise RuntimeError("no session outside a request")

    call_next = AsyncMock(return_value="next")
    context = SimpleNamespace(fastmcp_context=Broken())
    assert asyncio.run(McpSessionTrackingMiddleware().on_message(context, call_next)) == "next"


def test_one_failing_client_does_not_stop_the_others():
    bad, good = _Sdk1Session(), _Sdk1Session()
    bad.send_tool_list_changed.side_effect = RuntimeError("stream closed")
    _track(bad)
    _track(good)
    asyncio.run(PluginHub._notify_mcp_tool_list_changed())
    good.send_tool_list_changed.assert_awaited_once()


def test_no_monkeypatch_of_fastmcp_internals_remains():
    source = open(plugin_hub.__file__, encoding="utf-8").read()
    assert "MiddlewareServerSession" not in source.replace(
        "fastmcp.server.low_level.MiddlewareServerSession", "")
    assert "fastmcp.server.low_level import" not in source
