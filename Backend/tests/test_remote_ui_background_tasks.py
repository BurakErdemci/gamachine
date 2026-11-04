"""A request run for the web remote finishes its FastAPI background tasks
(remote/ui_dispatch.py), but a request that times out is still cancelled."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, FastAPI

from agentic import approval_mode
from remote import rpc, ui_dispatch as d
from tests.test_remote_ui_dispatch import SECRET, TOKEN, FakeSession


@pytest.fixture
def disp(monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    approval_mode.set_ui_secret(SECRET)
    bridge = SimpleNamespace()
    dispatcher = rpc.Dispatcher(bridge)
    bridge.rpc = dispatcher
    return dispatcher


def _call(path, rid=1):
    return {"id": rid, "type": "ui_http", "method": "POST", "path": path, "body": "{}",
            "headers": {"content-type": "application/json"}}


async def test_background_task_runs_after_the_reply(disp):
    ran = []
    app = FastAPI()

    async def later():
        await asyncio.sleep(0.2)
        ran.append("background")

    @app.post("/mcp/unity/toggle")
    async def toggle(background_tasks: BackgroundTasks):
        background_tasks.add_task(later)
        return {"status": "started"}

    disp.ui.app = app
    session = FakeSession()
    await disp.handle(session, _call("/mcp/unity/toggle"))
    assert session.replies(1)[-1].get("ok") is True
    assert ran == []
    await asyncio.sleep(0.8)
    assert ran == ["background"]
    assert not d._BACKGROUND


async def test_timeout_still_cancels_the_request(disp, monkeypatch):
    monkeypatch.setattr(d, "HTTP_TIMEOUT_S", 0.2)
    state = []
    app = FastAPI()

    @app.post("/chat-stop/{conv}")
    async def hang(conv: int):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state.append("cancelled")
            raise

    disp.ui.app = app
    session = FakeSession()
    await disp.handle(session, _call("/chat-stop/1"))
    assert session.replies(1)[-1].get("error") == "timeout"
    await asyncio.sleep(0.2)
    assert state == ["cancelled"]


async def test_background_part_is_cancelled_after_the_grace(disp, monkeypatch):
    monkeypatch.setattr(d, "BACKGROUND_GRACE_S", 0.3)
    state = []
    app = FastAPI()

    async def forever():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state.append("cancelled")
            raise

    @app.post("/mcp/unity/toggle")
    async def bg(background_tasks: BackgroundTasks):
        background_tasks.add_task(forever)
        return {}

    disp.ui.app = app
    session = FakeSession()
    await disp.handle(session, _call("/mcp/unity/toggle"))
    assert session.replies(1)[-1].get("ok") is True
    assert state == []
    await asyncio.sleep(0.7)
    assert state == ["cancelled"]
    assert not d._BACKGROUND
