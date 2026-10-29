"""Web remote fixes: workspace roots, the per-chat wake stream, card answers
from a paired browser and upstream failures (remote/ui_dispatch.py)."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

import routes.conversation_routes as cr
import routes.workspace_routes as wr
from agentic import cards, command_gates
from database import DatabaseManager
from remote import rpc, ui_dispatch as d
from remote.desktop_channel import CARD_CLOSED_TYPE, CHANNEL

TOKEN = "desktop-token"


class FakeSession:
    device_label = "phone:web-fixes"
    closed = False

    def __init__(self):
        self.sent = []

    async def send(self, frame):
        self.sent.append(frame)
        return True

    async def reply(self, frame):
        await self.send(frame)

    def replies(self, rid):
        return [m for m in self.sent if m.get("id") == rid]


def dispatcher(app):
    return rpc.Dispatcher(SimpleNamespace(), app=app)


async def call(disp, session, rid=1, **req):
    await disp.handle(session, {"id": rid, **req})
    return session.replies(rid)[-1]


@pytest.fixture(autouse=True)
def desktop_token(monkeypatch):
    monkeypatch.setattr(d, "read_local_app_token", lambda: TOKEN)


# ── F1: a paired device picks only an already saved workspace ───────────
@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "ws.db"))
    monkeypatch.setattr(wr, "require_user", lambda db_, token, user_id: (user_id, None))
    app = FastAPI()
    app.include_router(wr.create_workspace_router(db))
    known = str(tmp_path / "known")
    db.save_workspace(1, known)
    return SimpleNamespace(db=db, app=app, known=known)


async def test_remote_save_workspace_takes_a_saved_path(workspaces):
    db, other = workspaces.db, "/elsewhere/project"
    db.save_workspace(1, other)  # now the last one; picking `known` again must move it up
    r = await call(dispatcher(workspaces.app), FakeSession(), type="ui_http", method="POST",
                   path="/save-workspace", body={"user_id": 1, "path": workspaces.known})
    assert r["ok"] and r["result"]["status"] == 200
    assert json.loads(r["result"]["body"])["status"] == "success"
    assert db.get_last_workspace(1) == workspaces.known


async def test_remote_save_workspace_refuses_a_new_root(workspaces):
    db = workspaces.db
    before = db.get_recent_workspaces(1, 50)
    for path in ("/etc", workspaces.known + "/", workspaces.known.upper()):
        r = await call(dispatcher(workspaces.app), FakeSession(), type="ui_http", method="POST",
                       path="/save-workspace", body={"user_id": 1, "path": path})
        assert r["ok"] and r["result"]["status"] == 403, path
        assert json.loads(r["result"]["body"]) == {"detail": "remote_forbidden"}
    assert db.get_recent_workspaces(1, 50) == before


async def test_desktop_save_workspace_still_authorises_a_new_root(workspaces):
    transport = httpx.ASGITransport(app=workspaces.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        res = await client.post("/save-workspace", json={"user_id": 1, "path": "/new/root"},
                                headers={"X-Session-Token": TOKEN})
    assert res.status_code == 200
    assert workspaces.db.get_last_workspace(1) == "/new/root"


async def test_remote_remove_workspace_is_unchanged(workspaces):
    r = await call(dispatcher(workspaces.app), FakeSession(), type="ui_http", method="POST",
                   path="/remove-workspace", body={"user_id": 1, "path": workspaces.known})
    assert r["ok"] and json.loads(r["result"]["body"]) == {"removed": True}


# ── F2: the per-chat wake stream is the desktop's ───────────────────────
def test_per_chat_wake_stream_is_not_remote():
    assert not d.http_allowed("GET", "/conversations/3/wake-stream")
    assert d.http_allowed("GET", "/wake-stream-all")


async def test_remote_per_chat_wake_stream_is_refused_without_running():
    ran = []
    app = FastAPI()

    @app.get("/conversations/{conv}/wake-stream")
    async def wake(conv: int):
        ran.append(conv)
        return {}

    session = FakeSession()
    r = await call(dispatcher(app), session, type="ui_stream_open", method="GET", path="/conversations/3/wake-stream")
    assert r["ok"] is False and r["error"] == "remote_forbidden"
    assert ran == []


# ── F4: command and question answers are recorded as the device's ───────
@pytest.fixture
def card_app(monkeypatch):
    cards.reset()
    ledger = []
    monkeypatch.setattr(cr, "_check_token", lambda token: None)
    app = FastAPI()
    app.include_router(cr.create_conversation_router(SimpleNamespace(
        record_card_resolution=ledger.append, max_mail_id=lambda: 0,
        sweep_undelivered_mail=lambda *a, **k: 0, reject_pending_mail=lambda **k: 0)))
    q = CHANNEL.listen()
    gates = []

    def gate(gate_id, kind):
        command_gates.register_gate(gate_id, 1, kind=kind, summary="Synthetic card",
                                    questions=[{"question": "Pick", "options": ["A", "B"], "multi": False}]
                                    if kind == "question" else None)
        gates.append(gate_id)
        return gate_id

    yield SimpleNamespace(app=app, ledger=ledger, q=q, gate=gate)
    for g in gates:
        command_gates.release_gate(g)
    CHANNEL.unlisten(q)
    cards.set_ledger(None)
    cards.reset()


def closed_frames(q):
    frames = []
    while not q.empty():
        frame = q.get_nowait()
        if isinstance(frame, dict) and frame.get("type") == CARD_CLOSED_TYPE:
            frames.append(frame)
    return frames


async def test_remote_command_approval_is_the_devices(card_app):
    g = card_app.gate("web-cmd", "approval")
    session = FakeSession()
    r = await call(dispatcher(card_app.app), session, type="ui_http", method="POST",
                   path=f"/command-approval/{g}", body={"approved": True})
    assert r["ok"] and r["result"]["status"] == 200
    assert json.loads(r["result"]["body"]) == {"status": "ok", "approved": True}
    card = cards.get(g)
    assert not card.open and card.by == session.device_label
    assert command_gates.APPROVAL_RESULTS[g] is True
    assert [row.get("device") for row in card_app.ledger] == [session.device_label]
    assert [f["card_id"] for f in closed_frames(card_app.q)] == [g]
    # The same device sending its answer again gets the reply it got the first time.
    again = await call(dispatcher(card_app.app), session, rid=2, type="ui_http", method="POST",
                       path=f"/command-approval/{g}", body={"approved": True})
    assert json.loads(again["result"]["body"]) == {"status": "ok", "approved": True}


async def test_remote_question_answer_is_the_devices_with_its_choice(card_app):
    g = card_app.gate("web-q", "question")
    session = FakeSession()
    r = await call(dispatcher(card_app.app), session, type="ui_http", method="POST",
                   path=f"/question-answer/{g}", body={"answers": {"Pick": "B"}})
    assert r["ok"] and r["result"]["status"] == 200
    assert json.loads(r["result"]["body"]) == {"status": "ok"}
    card = cards.get(g)
    assert not card.open and card.by == session.device_label
    assert command_gates.QUESTION_RESULTS[g] == {"Pick": "B"}
    assert [row.get("device") for row in card_app.ledger] == [session.device_label]


async def test_remote_answer_after_the_desktop_reports_who_won(card_app):
    g = card_app.gate("web-late", "approval")
    transport = httpx.ASGITransport(app=card_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        res = await client.post(f"/command-approval/{g}", json={"approved": False}, headers={"X-Session-Token": TOKEN})
    assert res.json() == {"status": "ok", "approved": False}
    r = await call(dispatcher(card_app.app), FakeSession(), type="ui_http", method="POST",
                   path=f"/command-approval/{g}", body={"approved": True})
    body = json.loads(r["result"]["body"])
    assert body["status"] == "already_answered" and body["by"] == "desktop"
    assert command_gates.APPROVAL_RESULTS[g] is False


async def test_desktop_answers_stay_desktop(card_app):
    g, q = card_app.gate("desk-cmd", "approval"), card_app.gate("desk-q", "question")
    transport = httpx.ASGITransport(app=card_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        a = await client.post(f"/command-approval/{g}", json={"approved": True}, headers={"X-Session-Token": TOKEN})
        b = await client.post(f"/question-answer/{q}", json={"answers": {"Pick": "A"}},
                              headers={"X-Session-Token": TOKEN})
    assert a.json() == {"status": "ok", "approved": True} and b.json() == {"status": "ok"}
    assert cards.get(g).by == "desktop" and cards.get(q).by == "desktop"
    assert [row.get("device") for row in card_app.ledger] == ["desktop", "desktop"]
    assert closed_frames(card_app.q) == []


# ── F7: an upstream failure is no successful reply ──────────────────────
def failing_app():
    app = FastAPI()

    async def interrupted():
        yield b"incomplete-prefix"
        raise RuntimeError("synthetic upstream failure")

    @app.get("/session-report/{conv}/{kind}")
    async def report(conv: int, kind: str):
        return StreamingResponse(interrupted(), media_type="application/octet-stream")

    @app.get("/conversations/{conv}/turn-events")
    async def events(conv: int):
        return StreamingResponse(interrupted(), media_type="text/event-stream")

    @app.get("/conversations/{conv}/messages")
    async def before_headers(conv: int):
        raise RuntimeError("synthetic failure before the response started")

    return app


async def test_http_reports_a_body_cut_short_as_failed():
    r = await call(dispatcher(failing_app()), FakeSession(), type="ui_http", method="GET", path="/session-report/1/x")
    assert r == {"id": 1, "ok": False, "error": "upstream_failed"}


async def test_stream_cut_short_ends_with_an_error():
    session = FakeSession()
    await dispatcher(failing_app()).handle(session, {"id": 1, "type": "ui_stream_open", "method": "GET",
                                                     "path": "/conversations/1/turn-events"})
    opened = session.replies(1)[-1]
    assert opened["ok"] and opened["result"]["status"] == 200
    end = session.sent[-1]
    assert end == {"type": "ui_stream_end", "stream_id": opened["result"]["stream_id"], "error": "upstream_failed"}


async def test_failure_before_the_response_stays_a_500_reply():
    r = await call(dispatcher(failing_app()), FakeSession(), type="ui_http", method="GET", path="/conversations/1/messages")
    assert r["ok"] and r["result"]["status"] == 500
