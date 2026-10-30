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


# ── G1: only the dispatcher decides a request is remote ─────────────────
async def test_desktop_header_does_not_select_remote_workspace_policy(workspaces):
    transport = httpx.ASGITransport(app=workspaces.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        res = await client.post("/save-workspace", json={"user_id": 1, "path": "/new/marked"},
                                headers={"X-Session-Token": TOKEN, "X-Gamachine-Remote": "1"})
    assert res.status_code == 200
    assert workspaces.db.get_last_workspace(1) == "/new/marked"


# ── G2: the Unity toggle takes only a saved workspace from a paired device ──
@pytest.fixture
def toggle_app(workspaces, monkeypatch):
    import routes.mcp_routes as mr
    monkeypatch.setattr(mr, "_check_token", lambda token: None)
    monkeypatch.setattr(mr, "stop_mcp", lambda path=None: {"stopped": path})
    app = FastAPI()
    app.include_router(mr.router)
    return SimpleNamespace(app=app, known=workspaces.known)


async def test_remote_unity_toggle_refuses_an_unsaved_workspace(toggle_app):
    r = await call(dispatcher(toggle_app.app), FakeSession(), type="ui_http", method="POST",
                   path="/mcp/unity/toggle", body={"enabled": False, "workspace_path": "/never/saved"})
    assert r["ok"] and r["result"]["status"] == 403
    assert "remote_forbidden" in r["result"]["body"]


async def test_remote_unity_toggle_takes_a_saved_workspace(toggle_app):
    r = await call(dispatcher(toggle_app.app), FakeSession(), type="ui_http", method="POST",
                   path="/mcp/unity/toggle", body={"enabled": False, "workspace_path": toggle_app.known})
    assert r["result"]["status"] == 200
    assert json.loads(r["result"]["body"]) == {"stopped": toggle_app.known}


async def test_desktop_unity_toggle_takes_any_workspace(toggle_app):
    transport = httpx.ASGITransport(app=toggle_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        res = await client.post("/mcp/unity/toggle", json={"enabled": False, "workspace_path": "/never/saved"})
    assert res.status_code == 200


# ── G4: the end of a reply is never lost ────────────────────────────────
async def burst_then_fail(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/octet-stream")]})
    for _ in range(63):
        await send({"type": "http.response.body", "body": b"partial", "more_body": True})
    raise RuntimeError("synthetic failure with a full queue")


async def raise_at_once(scope, receive, send):
    raise RuntimeError("synthetic failure before the response started")


async def test_full_queue_does_not_lose_the_failure():
    r = await call(rpc.Dispatcher(SimpleNamespace(), app=burst_then_fail), FakeSession(),
                   type="ui_http", method="GET", path="/session-report/1/x")
    assert r == {"id": 1, "ok": False, "error": "upstream_failed"}
    session = FakeSession()
    await rpc.Dispatcher(SimpleNamespace(), app=burst_then_fail).handle(
        session, {"id": 1, "type": "ui_stream_open", "method": "GET", "path": "/session-report/1/x"})
    assert session.sent[-1]["type"] == "ui_stream_end" and session.sent[-1]["error"] == "upstream_failed"


async def test_failure_before_response_start_fails_promptly(monkeypatch):
    monkeypatch.setattr(d, "HTTP_TIMEOUT_S", 0.5)
    r = await call(rpc.Dispatcher(SimpleNamespace(), app=raise_at_once), FakeSession(),
                   type="ui_http", method="GET", path="/session-report/1/x")
    assert r == {"id": 1, "ok": False, "error": "upstream_failed"}
    session = FakeSession()
    await rpc.Dispatcher(SimpleNamespace(), app=raise_at_once).handle(
        session, {"id": 1, "type": "ui_stream_open", "method": "GET", "path": "/session-report/1/x"})
    assert session.replies(1)[-1] == {"id": 1, "ok": False, "error": "upstream_failed"}
    assert not [m for m in session.sent if m.get("type") == "ui_stream_end" and "error" not in m]


# ── G5: a browser card answered over the browser-live IPC op is the device's ──
async def test_browser_live_answer_card_is_the_devices(card_app):
    g = card_app.gate("web-bl", "approval")
    session = FakeSession()
    r = await call(dispatcher(card_app.app), session, type="ui_ipc", channel="browser-live",
                   args=[{"op": "answer-card", "gateId": g, "approved": True}])
    assert r == {"id": 1, "ok": True, "result": {"status": "ok"}}
    card = cards.get(g)
    assert not card.open and card.by == session.device_label
    assert [row.get("device") for row in card_app.ledger] == [session.device_label]
    assert [f["card_id"] for f in closed_frames(card_app.q)] == [g]
    again = await call(dispatcher(card_app.app), session, rid=2, type="ui_ipc", channel="browser-live",
                       args=[{"op": "answer-card", "gateId": g, "approved": False}])
    assert again["result"]["status"] == "already_answered"


async def test_browser_live_answer_card_validates_like_the_desktop(card_app):
    session = FakeSession()
    for bad in ({"gateId": "../x", "approved": True}, {"gateId": "ok", "approved": "yes"}, {"gateId": 5, "approved": True}):
        r = await call(dispatcher(card_app.app), session, type="ui_ipc", channel="browser-live",
                       args=[{"op": "answer-card", **bad}])
        assert "error" in r["result"]
    r = await call(dispatcher(card_app.app), session, type="ui_ipc", channel="browser-live",
                   args=[{"op": "answer-card", "gateId": "nope", "approved": True}])
    assert r["result"] == {"status": "gate_not_found"}


# ── H2: a device answer expires stale cards like the desktop route ──────
async def test_remote_card_answer_sweeps_expired_cards(card_app):
    transport = httpx.ASGITransport(app=card_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        for gate in ("web-exp-1", "web-exp-2"):
            res = await client.post("/mcp-approval-request", json={
                "gate_id": gate, "tool": "bash", "params": {"command": "ls"}, "workspace_path": ".",
                "conversation_id": None}, headers={"X-Session-Token": TOKEN})
            assert res.json()["status"] == "ok", res.text
        later = cr.time() + 180
        from unittest.mock import patch
        with patch.object(cr, "time", return_value=later):
            desktop = (await client.post("/mcp-approval-respond/web-exp-1", json={"approved": True},
                                         headers={"X-Session-Token": TOKEN})).json()
            r = await call(dispatcher(card_app.app), FakeSession(), type="ui_http", method="POST",
                           path="/mcp-approval-respond/web-exp-2", body={"approved": True})
            via_ipc = await call(dispatcher(card_app.app), FakeSession(), rid=2, type="ui_ipc",
                                 channel="browser-live",
                                 args=[{"op": "answer-card", "gateId": "web-exp-2", "approved": True}])
    remote = json.loads(r["result"]["body"])
    assert desktop["status"] == "gate_expired"
    assert remote["status"] == "gate_expired"
    assert remote["error"] == desktop["error"]
    assert via_ipc["result"]["status"] == "gate_expired"
    assert cards.get("web-exp-2").outcome == "timed_out"


async def test_remote_answer_to_a_desktop_closed_card_says_already_answered(card_app):
    # The sweep must not hide who decided: the desktop route reports the other
    # device's answer before it calls a resolved gate expired, and so must the remote one.
    transport = httpx.ASGITransport(app=card_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        res = await client.post("/mcp-approval-request", json={
            "gate_id": "web-won-1", "tool": "bash", "params": {"command": "ls"}, "workspace_path": ".",
            "conversation_id": None}, headers={"X-Session-Token": TOKEN})
        assert res.json()["status"] == "ok", res.text
        await client.post("/mcp-approval-respond/web-won-1", json={"approved": True},
                          headers={"X-Session-Token": TOKEN})
        r = await call(dispatcher(card_app.app), FakeSession(), type="ui_http", method="POST",
                       path="/mcp-approval-respond/web-won-1", body={"approved": False})
        via_ipc = await call(dispatcher(card_app.app), FakeSession(), rid=2, type="ui_ipc",
                             channel="browser-live",
                             args=[{"op": "answer-card", "gateId": "web-won-1", "approved": False}])
    assert json.loads(r["result"]["body"])["status"] == "already_answered"
    assert via_ipc["result"]["status"] == "already_answered"


# ── H1: a device-approved browser card earns the one-time retry grant ───
async def test_device_browser_approval_grants_the_one_time_retry(tmp_path, monkeypatch):
    from unittest.mock import patch
    from agentic import approval_mode, approval_policy
    from browser_use import gateway, settings
    cards.reset()
    host_ops = []

    async def host(runtime, op, conversation_id, args):
        host_ops.append(op)
        return {"ok": True, "url": "https://shop.example/cart",
                "element": {"role": "button", "name": "Buy now", "tag": "button"}}

    db = DatabaseManager(str(tmp_path / "h1.db"))
    approval_mode.set_ui_secret("h1-secret")
    settings.set_enabled(db, True)
    monkeypatch.setattr(cr, "_check_token", lambda token: None)
    headers = {"X-Session-Token": TOKEN, "X-Gamachine-UI-Secret": "h1-secret"}
    body_for = lambda conv: {"tool": "browser_click", "conversation_id": conv,
                             "args": {"snapshot_id": "s1", "ref": "9"}}
    # The router builds its gateway on the host transport it finds at creation.
    with patch.object(gateway, "post_cmd", host), patch.object(gateway, "APPROVAL_WAIT_S", 0.02):
        app = FastAPI()
        app.include_router(cr.create_conversation_router(db))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1", headers=headers) as client:
            await client.post("/browser/runtime", json={"running": True, "port": 4711, "token": "ab" * 32})
            conv = db.create_conversation(1, "h1")
            with approval_policy.ambient_turn("", "step", conversation_id=conv):
                first = (await client.post("/browser/command", json=body_for(conv))).json()
                assert first.get("needs_approval") is True, first
                gate = next(c["card_id"] for c in cards.list_pending(conv))
                answer = await call(dispatcher(app), FakeSession(), type="ui_ipc", channel="browser-live",
                                    args=[{"op": "answer-card", "gateId": gate, "approved": True}])
                assert answer["result"]["status"] == "ok"
                assert cards.get(gate).by == FakeSession.device_label
                before = len(host_ops)
                retry = (await client.post("/browser/command", json=body_for(conv))).json()
    cards.set_ledger(None)
    cards.reset()
    assert retry.get("ok") is True and "click" in host_ops[before:], retry


# ── Turn 4: resend, expiry after result retention, legacy RPC sweep ─────
async def _request_gate(client, gate):
    res = await client.post("/mcp-approval-request", json={
        "gate_id": gate, "tool": "bash", "params": {"command": "ls"}, "workspace_path": ".",
        "conversation_id": None}, headers={"X-Session-Token": TOKEN})
    assert res.json()["status"] == "ok", res.text


async def test_same_device_resend_says_already_answered(card_app):
    transport = httpx.ASGITransport(app=card_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        await _request_gate(client, "web-resend-1")
    session = FakeSession()
    first = await call(dispatcher(card_app.app), session, type="ui_http", method="POST",
                       path="/mcp-approval-respond/web-resend-1", body={"approved": True})
    again = await call(dispatcher(card_app.app), session, rid=2, type="ui_http", method="POST",
                       path="/mcp-approval-respond/web-resend-1", body={"approved": True})
    assert json.loads(first["result"]["body"])["status"] == "ok"
    assert json.loads(again["result"]["body"])["status"] == "already_answered"


async def test_expiry_survives_result_retention(card_app):
    from unittest.mock import patch
    transport = httpx.ASGITransport(app=card_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        await _request_gate(client, "web-late-1")
    # Far past both lifetimes: the sweep times the card out and drops its result record.
    with patch.object(cr, "time", return_value=cr.time() + 100000):
        r = await call(dispatcher(card_app.app), FakeSession(), type="ui_http", method="POST",
                       path="/mcp-approval-respond/web-late-1", body={"approved": True})
    assert json.loads(r["result"]["body"])["status"] == "gate_expired"
    assert cards.get("web-late-1").outcome == "timed_out"


async def test_legacy_answer_card_rpc_sweeps_expired_cards(card_app):
    from unittest.mock import patch
    transport = httpx.ASGITransport(app=card_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        await _request_gate(client, "web-rpc-1")
    with patch.object(cr, "time", return_value=cr.time() + 180):
        r = await call(dispatcher(card_app.app), FakeSession(), type="answer_card",
                       card_id="web-rpc-1", decision="approve")
    assert r["ok"] is False and r["error"] == "gate_expired"
    assert cards.get("web-rpc-1").outcome == "timed_out"
