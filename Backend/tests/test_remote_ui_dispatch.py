"""The web remote's UI dispatcher (remote/ui_dispatch.py): the allow-lists, the
in-process execution with the desktop's identity, streams, uploads and IPC run
through a fake desktop renderer."""
import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Header, Request
from fastapi.responses import Response, StreamingResponse

from agentic import approval_mode
from remote import desktop_channel, rpc, ui_dispatch as d
from remote.desktop_channel import CHANNEL
from routes.remote_routes import create_remote_router

TOKEN = "app-token-for-ui-dispatch"
SECRET = "ui-secret-for-ui-dispatch"


class FakeSession:
    def __init__(self, name="pixel"):
        self.device_label = f"phone:{name}"
        self.closed = False
        self.sent = []

    async def send(self, obj):
        if self.closed:
            return False
        self.sent.append(obj)
        return True

    async def reply(self, obj):
        await self.send(obj)

    def replies(self, rid):
        return [m for m in self.sent if m.get("id") == rid]


def backend_app(seen):
    app = FastAPI()

    @app.get("/conversations/{conv}/messages")
    async def messages(conv: int, request: Request, x_session_token: str = Header(alias="X-Session-Token", default="")):
        seen.append(dict(request.headers))
        return {"conv": conv, "token_ok": x_session_token == TOKEN, "q": dict(request.query_params)}

    @app.post("/mcp-approval-respond/{gate}")
    async def respond(gate: str, request: Request):
        seen.append(dict(request.headers))
        return {"gate": gate, "body": await request.json()}

    @app.post("/chat-stop/{conv}")
    async def stop(conv: int, request: Request):
        seen.append(dict(request.headers))
        return {"stopped": conv}

    @app.post("/transcribe")
    async def transcribe(request: Request):
        raw = await request.body()
        return {"len": len(raw), "ctype": request.headers.get("content-type"), "head": raw[:4].hex()}

    @app.get("/api-keys/{user}")
    async def keys(user: int):
        return {"keys": "secret"}

    @app.get("/session-report/{conv}/{kind}")
    async def report(conv: int, kind: str):
        return Response(b"\x89PNG" + bytes(range(256)), media_type="image/png")

    @app.get("/conversations/{conv}/model")
    async def big(conv: int):
        return {"blob": "x" * (d.REPLY_ROOM + 10)}

    @app.post("/chat-stream")
    async def chat_stream(request: Request):
        n = (await request.json())["n"]

        async def gen():
            for i in range(n):
                yield f"data: {i}\n\n"
            yield "z" * (d.CHUNK_MAX * 2 + 5)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/conversations/{conv}/wake-stream")
    async def endless(conv: int):
        async def gen():
            yield "data: start\n\n"
            try:
                await asyncio.Event().wait()
            finally:
                seen.append("cancelled")
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/wake-stream-all")
    async def wake_all():
        async def gen():
            yield "data: " + json.dumps({"type": "remote_ipc", "request_id": "ab" * 8, "channel": "read-file",
                                         "payload": ["x"]}) + "\n\n"
            yield "data: " + json.dumps({"type": "title", "conversation_id": 1})
            yield "\n\n: keepalive\n\n"
            yield "data: " + json.dumps({"type": "remote_message", "text": "hi"}) + "\n\n"
            yield "data: " + json.dumps({"type": "card_closed", "card_id": "c"}) + "\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", TOKEN)
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    approval_mode.set_ui_secret(SECRET)
    seen = []
    bridge = SimpleNamespace()
    disp = rpc.Dispatcher(bridge)
    bridge.rpc = disp
    disp.ui.app = backend_app(seen)
    return SimpleNamespace(disp=disp, seen=seen, session=FakeSession(), bridge=bridge)


async def call(ui, session=None, **req):
    session = session or ui.session
    rid = req.pop("rid", 1)
    await ui.disp.handle(session, {"id": rid, **req})
    return session.replies(rid)[-1]


# ── allow-list ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,path", [
    ("POST", "/chat-stream"), ("POST", "/chat-stop/12"), ("POST", "/mcp-approval-respond/abc"),
    ("POST", "/computer-use/halt/12"), ("GET", "/wake-stream-all"), ("GET", "/conversations/3/messages"),
    ("DELETE", "/conversations/3"), ("GET", "/scene-editor/tree"), ("POST", "/scene-editor/set-field"),
    ("GET", "/mcp/unity/console"), ("GET", "/cli-models/claude"), ("GET", "/mcp-pending"),
    ("POST", "/question-answer/g1"), ("GET", "/approval-mode"), ("GET", "/computer-use/settings"),
    ("GET", "/browser/settings"), ("get", "/available-models"), ("GET", "/conversations/a%20b/messages"),
])
def test_allowed(method, path):
    assert d.http_allowed(method, path) is True


@pytest.mark.parametrize("method,path", [
    ("POST", "/api-keys/save"), ("GET", "/api-keys/1"), ("DELETE", "/api-keys/1/openai"),
    ("POST", "/cli-install/claude"), ("POST", "/cli-login/codex"), ("POST", "/profile/reset"),
    ("DELETE", "/computer-use/grants/com.apple.finder"), ("POST", "/computer-use/runtime"),
    ("POST", "/computer-use/settings"), ("POST", "/browser/runtime"), ("PUT", "/browser/settings"),
    ("POST", "/remote/pair/start"), ("GET", "/remote/status"), ("POST", "/remote/ui-ipc-result/" + "a" * 16),
    ("POST", "/approval-mode"), ("POST", "/save-ai-config"), ("POST", "/mailbox/send"),
    ("POST", "/mcp-approval-request"),
    # traversal and shapes that are not a plain path
    ("GET", "/../api-keys/1"), ("GET", "/chat-stream/../api-keys/1"), ("GET", "//evil"),
    ("GET", "http://x/chat-stream"), ("GET", "/%2e%2e/api-keys/1"), ("GET", "/conversations/%2e%2e/messages"),
    ("GET", "/api-keys%2f1"), ("GET", "/conversations/1%252e/messages"), ("GET", "\\api-keys\\1"),
    ("GET", "/conversations/1/messages?x=1"), ("GET", "/conversations/1/./messages"),
    ("GET", "/conversations//messages"), ("GET", "/conversations/1/messages\x00"), ("GET", "chat-stream"),
    ("GET", "/conversations/%ff/messages"), ("GET", ""), (None, "/chat-stream"), ("TRACE", "/chat-stream"),
    ("POST", None), ("GET", "/" + "a" * 3000),
])
def test_denied(method, path):
    assert d.http_allowed(method, path) is False


def test_deny_wins_over_allow(monkeypatch):
    monkeypatch.setattr(d, "_ALLOW_RE", d._ALLOW_RE + [(("GET", "POST"), __import__("re").compile(r"/.*"))])
    assert d.http_allowed("GET", "/anything") is True
    assert d.http_allowed("POST", "/api-keys/save") is False
    assert d.http_allowed("POST", "/remote/enable") is False


def test_secret_elevated_only_for_the_two_routes():
    assert d.secret_elevated("POST", "/mcp-approval-respond/abc") is True
    assert d.secret_elevated("POST", "/computer-use/halt/12") is True
    for m, p in (("POST", "/chat-stream"), ("POST", "/api-keys/save"), ("GET", "/mcp-approval-respond/abc"),
                 ("POST", "/mcp-approval-respond/a/b"), ("POST", "/computer-use/halt/../../approval-mode"),
                 ("POST", "/approval-mode"), ("POST", "/chat-stop/1")):
        assert d.secret_elevated(m, p) is False, (m, p)


def test_ipc_lists():
    for ch in ("read-file", "write-file", "terminal-spawn", "terminal-write", "read-directory", "move-entry"):
        assert ch in d.IPC_ALLOW
    for ch in ("app-token-get", "profile-reset", "authorize-workspace", "open-folder-dialog", "open-file-dialog",
               "remote-control", "approval-mode-set", "register-dropped-folder", "computer-use"):
        assert ch not in d.IPC_ALLOW
    assert d.ipc_allowed("browser-live", [{"op": "halt"}])
    assert not d.ipc_allowed("browser-live", [{"op": "enable"}])
    assert not d.ipc_allowed("browser-live", [])
    assert d.ipc_allowed("computer-use-live", [{"op": "status"}])
    assert not d.ipc_allowed("computer-use-live", [{"op": "open-panel"}])
    assert d.ipc_event_allowed("terminal-data-t1") and d.ipc_event_allowed("open-conversation")
    assert not d.ipc_event_allowed("terminal-data-") and not d.ipc_event_allowed("menu-open-terminal")


# ── in-process HTTP ─────────────────────────────────────────────────────
async def test_http_runs_with_the_desktop_token_and_never_the_secret(ui):
    r = await call(ui, type="ui_http", method="GET", path="/conversations/7/messages", query={"since": 3})
    assert r["ok"] and r["result"]["status"] == 200
    assert r["result"]["content_type"].startswith("application/json")
    assert json.loads(r["result"]["body"]) == {"conv": 7, "token_ok": True, "q": {"since": "3"}}
    assert "x-gamachine-ui-secret" not in ui.seen[-1]
    assert TOKEN not in json.dumps(ui.session.sent)

    r = await call(ui, rid=2, type="ui_http", method="POST", path="/chat-stop/7")
    assert r["result"]["status"] == 200 and "x-gamachine-ui-secret" not in ui.seen[-1]


async def test_http_adds_the_secret_for_card_answers(ui):
    r = await call(ui, type="ui_http", method="POST", path="/mcp-approval-respond/g1", body={"approved": True})
    assert json.loads(r["result"]["body"]) == {"gate": "g1", "body": {"approved": True}}
    assert ui.seen[-1]["x-gamachine-ui-secret"] == SECRET
    assert ui.seen[-1]["x-session-token"] == TOKEN
    assert SECRET not in json.dumps(ui.session.sent)


async def test_http_refuses_off_list_paths_without_running_them(ui):
    for i, path in enumerate(("/api-keys/1", "/../api-keys/1", "/remote/status")):
        r = await call(ui, rid=10 + i, type="ui_http", method="GET", path=path)
        assert r == {"id": 10 + i, "ok": False, "error": "remote_forbidden", "status": 403}
    assert ui.seen == []


async def test_http_binary_body_and_too_large(ui):
    r = await call(ui, type="ui_http", method="GET", path="/session-report/1/png")
    assert base64.b64decode(r["result"]["body_b64"]).startswith(b"\x89PNG")
    r = await call(ui, rid=2, type="ui_http", method="GET", path="/conversations/1/model")
    assert r["ok"] is False and r["error"] == "too_large"


async def test_http_rejects_bad_params(ui):
    bad = [dict(query={"a": {"x": 1}}), dict(query="a=1#x"), dict(body={}, body_b64="AA=="),
           dict(body_b64="!!"), dict(content_type="a\r\nx-session-token: y", body_b64="AA=="),
           dict(upload_id="nope")]
    for i, extra in enumerate(bad):
        r = await call(ui, rid=20 + i, type="ui_http", method="POST", path="/transcribe", **extra)
        assert r["ok"] is False, extra


async def test_no_app_means_unavailable(ui):
    ui.disp.ui.app = None
    r = await call(ui, type="ui_http", method="GET", path="/conversations/1/messages")
    assert r["error"] == "unavailable"


# ── streams ─────────────────────────────────────────────────────────────
async def test_stream_chunks_in_order_then_ends(ui):
    await ui.disp.handle(ui.session, {"id": 5, "type": "ui_stream_open", "method": "POST",
                                      "path": "/chat-stream", "body": {"n": 3}})
    first = ui.session.sent[0]
    assert first["id"] == 5 and first["ok"] and first["result"]["status"] == 200
    sid = first["result"]["stream_id"]
    chunks = [m for m in ui.session.sent if m.get("type") == "ui_stream_chunk"]
    assert [c["seq"] for c in chunks] == list(range(len(chunks)))
    assert all(len(base64.b64decode(c["data_b64"])) <= d.CHUNK_MAX for c in chunks)
    data = b"".join(base64.b64decode(c["data_b64"]) for c in chunks)
    assert data == b"data: 0\n\ndata: 1\n\ndata: 2\n\n" + b"z" * (d.CHUNK_MAX * 2 + 5)
    assert ui.session.sent[-1] == {"type": "ui_stream_end", "stream_id": sid}
    assert not ui.disp.ui._session_state(ui.session).streams


async def test_stream_close_cancels_the_request(ui):
    task = asyncio.create_task(ui.disp.handle(ui.session, {"id": 1, "type": "ui_stream_open", "method": "GET",
                                                           "path": "/conversations/1/wake-stream"}))
    for _ in range(100):
        if any(m.get("type") == "ui_stream_chunk" for m in ui.session.sent):
            break
        await asyncio.sleep(0.01)
    sid = ui.session.sent[0]["result"]["stream_id"]
    other = FakeSession("other")
    assert (await call(ui, session=other, rid=2, type="ui_stream_close", stream_id=sid))["result"] == {}
    assert not task.done()  # another session cannot close it
    r = await call(ui, rid=3, type="ui_stream_close", stream_id=sid)
    assert r["ok"]
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
    for _ in range(100):
        if "cancelled" in ui.seen:
            break
        await asyncio.sleep(0.01)
    assert "cancelled" in ui.seen


async def test_stream_ends_with_the_session(ui):
    task = asyncio.create_task(ui.disp.handle(ui.session, {"id": 1, "type": "ui_stream_open", "method": "GET",
                                                           "path": "/conversations/1/wake-stream"}))
    await asyncio.sleep(0.05)
    task.cancel()  # PhoneSession.close() cancels its tasks
    await asyncio.gather(task, return_exceptions=True)
    for _ in range(100):
        if "cancelled" in ui.seen:
            break
        await asyncio.sleep(0.01)
    assert "cancelled" in ui.seen
    assert not ui.disp.ui._session_state(ui.session).streams


async def test_stream_limit_per_session(ui, monkeypatch):
    monkeypatch.setattr(d, "MAX_STREAMS", 1)
    task = asyncio.create_task(ui.disp.handle(ui.session, {"id": 1, "type": "ui_stream_open", "method": "GET",
                                                           "path": "/conversations/1/wake-stream"}))
    await asyncio.sleep(0.05)
    r = await call(ui, rid=2, type="ui_stream_open", method="GET", path="/conversations/1/wake-stream")
    assert r["error"] == "too_many_streams"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_wake_stream_copy_drops_desktop_commands(ui):
    await ui.disp.handle(ui.session, {"id": 1, "type": "ui_stream_open", "method": "GET", "path": "/wake-stream-all"})
    data = b"".join(base64.b64decode(m["data_b64"]) for m in ui.session.sent if m.get("type") == "ui_stream_chunk")
    assert b"remote_ipc" not in data and b"remote_message" not in data
    assert b'"title"' in data and b"card_closed" in data and b": keepalive" in data


async def test_stream_refused_path(ui):
    r = await call(ui, type="ui_stream_open", method="GET", path="/api-keys/1")
    assert r["error"] == "remote_forbidden"


# ── uploads ─────────────────────────────────────────────────────────────
async def test_upload_parts_then_body(ui):
    parts = [b"\x00\x01" * 100, b"\x02" * 50]
    for i, p in enumerate(parts):
        r = await call(ui, rid=i + 1, type="ui_upload_part", upload_id="u1", seq=i,
                       data_b64=base64.b64encode(p).decode(), last=i == len(parts) - 1)
        assert r["ok"], r
    assert r["result"] == {"received": 250}
    r = await call(ui, rid=9, type="ui_http", method="POST", path="/transcribe", upload_id="u1",
                   content_type="audio/webm")
    assert json.loads(r["result"]["body"]) == {"len": 250, "ctype": "audio/webm", "head": "00010001"}
    # used once
    r = await call(ui, rid=10, type="ui_http", method="POST", path="/transcribe", upload_id="u1")
    assert r["error"] == "unknown_upload"


async def test_upload_limits(ui, monkeypatch):
    big = base64.b64encode(b"x" * (d.UPLOAD_PART_MAX + 1)).decode()
    r = await call(ui, type="ui_upload_part", upload_id="u", seq=0, data_b64=big, last=False)
    assert r["error"] == "part_too_large"
    r = await call(ui, rid=2, type="ui_upload_part", upload_id="u", seq=1, data_b64="AA==", last=False)
    assert r["error"] == "unknown_upload"
    await call(ui, rid=3, type="ui_upload_part", upload_id="u", seq=0, data_b64="AA==", last=False)
    r = await call(ui, rid=4, type="ui_upload_part", upload_id="u", seq=2, data_b64="AA==", last=False)
    assert r["error"] == "bad_seq" and r["expected"] == 1
    r = await call(ui, rid=5, type="ui_http", method="POST", path="/transcribe", upload_id="u")
    assert r["error"] == "unknown_upload"  # not finished

    monkeypatch.setattr(d, "UPLOAD_TOTAL_MAX", 3)
    r = await call(ui, rid=6, type="ui_upload_part", upload_id="v", seq=0,
                   data_b64=base64.b64encode(b"abc").decode(), last=False)
    assert r["error"] == "upload_too_large"


async def test_upload_expires(ui):
    now = [1000.0]
    ui.disp.ui.clock = lambda: now[0]
    await call(ui, type="ui_upload_part", upload_id="u", seq=0, data_b64="AA==", last=True)
    now[0] += d.UPLOAD_TTL_S + 1
    r = await call(ui, rid=2, type="ui_http", method="POST", path="/transcribe", upload_id="u")
    assert r["error"] == "unknown_upload"


async def test_uploads_are_per_session(ui):
    await call(ui, type="ui_upload_part", upload_id="u", seq=0, data_b64="AA==", last=True)
    r = await call(ui, session=FakeSession("b"), rid=2, type="ui_http", method="POST", path="/transcribe",
                   upload_id="u")
    assert r["error"] == "unknown_upload"


# ── IPC through a fake desktop renderer ────────────────────────────────
@pytest.fixture
def renderer(ui):
    """Listens on the desktop channel like /wake-stream-all and answers over
    the real /remote routes."""
    q = CHANNEL.listen()
    app = FastAPI()
    app.include_router(create_remote_router(ui.bridge))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    yield SimpleNamespace(q=q, client=client)
    CHANNEL.unlisten(q)


async def test_ipc_round_trip(ui, renderer):
    async def desktop():
        frame = await renderer.q.get()
        assert frame["type"] == desktop_channel.IPC_TYPE
        assert frame["channel"] == "read-file" and frame["payload"] == ["Assets/a.cs", "/ws"]
        bad = await renderer.client.post(f"/remote/ui-ipc-result/{frame['request_id']}", json={"ok": True})
        assert bad.status_code == 401
        r = await renderer.client.post(f"/remote/ui-ipc-result/{frame['request_id']}",
                                       json={"ok": True, "result": {"content": "class A {}"}},
                                       headers={"X-Session-Token": TOKEN})
        assert r.json() == {"accepted": True}
        again = await renderer.client.post(f"/remote/ui-ipc-result/{frame['request_id']}",
                                           json={"ok": True, "result": 1}, headers={"X-Session-Token": TOKEN})
        assert again.json() == {"accepted": False}

    t = asyncio.create_task(desktop())
    r = await call(ui, type="ui_ipc", channel="read-file", payload="Assets/a.cs", args=["Assets/a.cs", "/ws"])
    await t
    assert r == {"id": 1, "ok": True, "result": {"content": "class A {}"}}


async def test_ipc_error_and_refusals(ui, renderer):
    async def desktop():
        frame = await renderer.q.get()
        await renderer.client.post(f"/remote/ui-ipc-result/{frame['request_id']}",
                                   json={"ok": False, "error": "outside workspace"},
                                   headers={"X-Session-Token": TOKEN})

    t = asyncio.create_task(desktop())
    r = await call(ui, type="ui_ipc", channel="write-file", payload="/etc/x", args=["/etc/x", "y"])
    await t
    assert r["error"] == "ipc_failed" and r["message"] == "outside workspace"

    for i, (ch, args) in enumerate((("app-token-get", []), ("profile-reset", []),
                                       ("browser-live", [{"op": "enable"}]), ("open-folder-dialog", []))):
        r = await call(ui, rid=10 + i, type="ui_ipc", channel=ch, args=args)
        assert r["error"] == "remote_unavailable"
    assert renderer.q.empty()
    r = await call(ui, rid=20, type="ui_ipc", channel="read-file", args={"not": "a list"})
    assert r["error"] == "bad_request"
    bad = await renderer.client.post("/remote/ui-ipc-result/not-hex", json={}, headers={"X-Session-Token": TOKEN})
    assert bad.status_code == 400
    unknown = await renderer.client.post("/remote/ui-ipc-result/" + "0" * 16, json={"ok": True},
                                         headers={"X-Session-Token": TOKEN})
    assert unknown.json() == {"accepted": False}


async def test_ipc_without_a_renderer(ui):
    r = await call(ui, type="ui_ipc", channel="read-file", args=["a"])
    assert r["error"] == "desktop_not_ready"


async def test_ipc_timeout(ui, renderer, monkeypatch):
    monkeypatch.setattr(d, "IPC_TIMEOUT_S", 0.05)
    r = await call(ui, type="ui_ipc", channel="read-directory", payload="Assets")
    assert r["error"] == "desktop_not_ready"
    assert ui.disp.ui._pending == {}


async def test_subscription_forwards_events_until_the_session_ends(ui, renderer):
    r = await call(ui, type="ui_ipc_subscribe", channel="terminal-data-t1")
    assert r["result"] == {}
    assert await renderer.q.get() == {"type": "remote_ipc_subscribe", "channel": "terminal-data-t1"}
    assert (await call(ui, rid=2, type="ui_ipc_subscribe", channel="app-token-get"))["error"] == "remote_unavailable"

    h = {"X-Session-Token": TOKEN}
    r = await renderer.client.post("/remote/ui-ipc-event", json={"channel": "terminal-data-t1", "args": ["ls\r\n"]},
                                   headers=h)
    assert r.json() == {"subscribed": True}
    await asyncio.sleep(0.01)
    assert ui.session.sent[-1] == {"type": "ui_ipc_event", "channel": "terminal-data-t1", "args": ["ls\r\n"]}
    bad = await renderer.client.post("/remote/ui-ipc-event", json={"channel": "app-token-get", "args": []},
                                     headers=h)
    assert bad.status_code == 400

    ui.session.closed = True
    r = await renderer.client.post("/remote/ui-ipc-event", json={"channel": "terminal-data-t1", "args": ["x"]},
                                   headers=h)
    assert r.json() == {"subscribed": False}


async def answer_next(renderer, result):
    frame = await renderer.q.get()
    await renderer.client.post(f"/remote/ui-ipc-result/{frame['request_id']}", json={"ok": True, "result": result},
                               headers={"X-Session-Token": TOKEN})
    return frame


async def test_ipc_single_payload_without_args(ui, renderer):
    t = asyncio.create_task(answer_next(renderer, ["a.cs"]))
    r = await call(ui, type="ui_ipc", channel="read-directory", payload="Assets")
    assert (await t)["payload"] == ["Assets"] and r["result"] == ["a.cs"]
    t = asyncio.create_task(answer_next(renderer, {"ok": True}))
    r = await call(ui, rid=2, type="ui_ipc", channel="browser-live", payload={"op": "halt"})
    assert (await t)["payload"] == [{"op": "halt"}] and r["ok"]
    t = asyncio.create_task(answer_next(renderer, None))
    await call(ui, rid=3, type="ui_ipc", channel="git-status")
    assert (await t)["payload"] == []


async def test_ipc_args_win_over_payload_and_are_checked(ui, renderer):
    # The op check reads args[0]; a harmless payload cannot cover a refused op.
    r = await call(ui, type="ui_ipc", channel="browser-live", payload={"op": "status"}, args=[{"op": "enable"}])
    assert r["error"] == "remote_unavailable"
    assert renderer.q.empty()
    r = await call(ui, rid=2, type="ui_ipc", channel="read-file", args=list(range(d.IPC_ARGS_MAX + 1)))
    assert r["error"] == "bad_request"


async def test_ipc_content_type_reaches_the_request(ui):
    r = await call(ui, type="ui_http", method="POST", path="/transcribe", body_b64="AAE=",
                   content_type="audio/webm;codecs=opus")
    assert json.loads(r["result"]["body"])["ctype"] == "audio/webm;codecs=opus"


async def test_ipc_argument_from_an_upload(ui, renderer):
    text = "class A {}\n// \u00e7\u011f" * 3000
    raw = text.encode("utf-8")
    parts = [raw[i:i + 32 * 1024] for i in range(0, len(raw), 32 * 1024)]
    for i, p in enumerate(parts):
        r = await call(ui, rid=100 + i, type="ui_upload_part", upload_id="w1", seq=i,
                       data_b64=base64.b64encode(p).decode(), last=i == len(parts) - 1)
        assert r["ok"], r
    t = asyncio.create_task(answer_next(renderer, {"success": True}))
    r = await call(ui, type="ui_ipc", channel="write-file", payload="Assets/a.cs",
                   args=["Assets/a.cs", "", "/ws"], upload_id="w1", upload_arg_index=1)
    frame = await t
    assert frame["payload"] == ["Assets/a.cs", text, "/ws"] and r["result"] == {"success": True}
    # used once
    r = await call(ui, rid=2, type="ui_ipc", channel="write-file", args=["a", "", "/ws"], upload_id="w1",
                   upload_arg_index=1)
    assert r["error"] == "unknown_upload"


async def test_ipc_upload_refusals(ui, renderer):
    await call(ui, type="ui_upload_part", upload_id="bin", seq=0, data_b64=base64.b64encode(b"\xff\xfe").decode(),
               last=True)
    for i, extra in enumerate((dict(upload_arg_index=3), dict(upload_arg_index=True), dict(upload_arg_index=-1),
                               dict())):
        r = await call(ui, rid=10 + i, type="ui_ipc", channel="write-file", args=["a", "", "/ws"],
                       upload_id="bin", **extra)
        assert r["error"] == "bad_upload_arg_index", extra
    r = await call(ui, rid=20, type="ui_ipc", channel="write-file", args=["a", "", "/ws"], upload_id="bin",
                   upload_arg_index=1)
    assert r["error"] == "bad_upload"
    # a refused channel never consumes an upload
    await call(ui, rid=21, type="ui_upload_part", upload_id="k", seq=0, data_b64="AA==", last=True)
    r = await call(ui, rid=22, type="ui_ipc", channel="app-token-get", args=["x"], upload_id="k",
                   upload_arg_index=0)
    assert r["error"] == "remote_unavailable"
    assert "k" in ui.disp.ui._session_state(ui.session).uploads
    assert renderer.q.empty()


async def test_ipc_upload_expires(ui, renderer):
    now = [0.0]
    ui.disp.ui.clock = lambda: now[0]
    await call(ui, type="ui_upload_part", upload_id="e", seq=0, data_b64="QQ==", last=True)
    now[0] += d.UPLOAD_TTL_S + 1
    r = await call(ui, rid=2, type="ui_ipc", channel="write-file", args=["a", "", "/ws"], upload_id="e",
                   upload_arg_index=1)
    assert r["error"] == "unknown_upload"
