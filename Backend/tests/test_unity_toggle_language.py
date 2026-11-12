"""POST /mcp/unity/toggle: the language for the manager's own messages comes from the body, else from
the X-UI-Lang header (12 Nov 2026: the body said 'en' before the app had read the stored language)."""
import pytest
from fastapi import BackgroundTasks
from unittest.mock import AsyncMock

import routes.mcp_routes as mr


@pytest.fixture
def start(monkeypatch):
    start = AsyncMock(return_value={"status": "started"})
    monkeypatch.setattr(mr, "start_mcp_for", start)
    monkeypatch.setattr(mr, "_check_token", lambda token: None)
    return start


async def toggle(body_language, header_language):
    req = mr.MCPToggleRequest(enabled=True, workspace_path=None, language=body_language)
    await mr.toggle_unity_mcp(req, BackgroundTasks(), "tok", header_language)


@pytest.mark.asyncio
async def test_header_language_is_used_when_the_body_has_none(start):
    await toggle(None, "tr")
    assert start.await_args.args[2] == "tr"


@pytest.mark.asyncio
async def test_explicit_body_language_wins_over_the_header(start):
    await toggle("en", "tr")
    assert start.await_args.args[2] == "en"
