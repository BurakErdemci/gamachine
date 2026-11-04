"""`bg_tasks` frames on /wake-stream-all and the routes around the wake-once rule.

A chat's running background tasks are data, not a turn (owner decision,
4 Nov 2026): every start/end reaches the owner's streams (and a paired
browser's copy), a stream that connects while tasks run learns them at once,
another user's stream gets nothing. A real user message keeps task notices; a
wake never resets a busy session; the CLI's unprompted turn blocks a wake.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import providers.claude_sdk_session as cs
from agentic import agent_runner as ar
from agentic import mailbox, turn_events, wake_queue
from remote import desktop_channel, ui_dispatch
from routes.conversation_routes import create_conversation_router
from schemas import ChatRequest

MINE, THEIRS, BROKEN = 9201, 9202, 9203


@pytest.fixture(autouse=True)
def _fresh():
    turn_events.RING.reset()
    wake_queue.reset_all()
    yield
    for cid in (MINE, THEIRS, BROKEN, 1):
        cs._SESSIONS.pop(cid, None)
    turn_events.RING.reset()
    wake_queue.reset_all()


def _owner(conv_id):
    if conv_id == BROKEN:
        raise RuntimeError("db down")
    return 1 if conv_id == MINE else 2


def _route():
    db = MagicMock()
    db.get_conversation_owner.side_effect = _owner
    db.get_conversation_title.return_value = None
    router = create_conversation_router(db, MagicMock())
    return next(r for r in router.routes if getattr(r, "path", "") == "/wake-stream-all")


async def _open(route, remote="", user_id=1):
    with patch("routes.conversation_routes.get_current_user", return_value=(user_id, None)):
        resp = await route.endpoint(x_session_token="t", x_ui_lang=None, x_gamachine_remote=remote)
    it = resp.body_iterator
    frames = []

    async def read():
        async for chunk in it:
            text = chunk if isinstance(chunk, str) else chunk.decode()
            for line in text.splitlines():
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:]))

    reader = asyncio.create_task(read())
    await asyncio.sleep(0.2)

    async def close():
        reader.cancel()
        try:
            await reader
        except BaseException:
            pass
        await it.aclose()
        await asyncio.sleep(0.05)

    return frames, close


async def _until(frames, pred, timeout=4.0):
    for _ in range(int(timeout / 0.05)):
        if any(pred(f) for f in frames):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"no matching frame in {frames}")


def _bg(frames):
    return [f for f in frames if f.get("type") == "bg_tasks"]


def _live(conv_id):
    sess = cs.ClaudeSDKSession(conversation_id=conv_id)
    sess._started = True
    sess._client = MagicMock()
    sess._client.query = AsyncMock()
    cs._SESSIONS[conv_id] = sess
    return sess


def _started(task_id, desc, task_type="local_agent"):
    from claude_agent_sdk import TaskStartedMessage
    return TaskStartedMessage("task_started", {}, task_id, desc, "u", "s1", task_type=task_type)


def _notified(task_id):
    from claude_agent_sdk import TaskNotificationMessage
    return TaskNotificationMessage("task_notification", {}, task_id, "completed", "out", "done", "n", "s1")


def test_every_change_reaches_the_owner_and_an_empty_list_clears():
    async def run():
        sess = _live(MINE)
        frames, close = await _open(_route())
        try:
            with patch.object(cs, "_TASKS_DONE_GRACE_S", 60.0):
                await sess._on_message(_started("a", "audit the scene"))
                await _until(frames, lambda f: f.get("type") == "bg_tasks" and f["tasks"])
                frame = _bg(frames)[0]
                assert set(frame) == {"type", "conversation_id", "tasks"}
                assert frame["conversation_id"] == MINE
                (task,) = frame["tasks"]
                assert task["task_id"] == "a" and task["desc"] == "audit the scene"
                assert task["kind"] == "agent" and isinstance(task["started_at"], float)
                await sess._on_message(_notified("a"))
                await _until(frames, lambda f: f.get("type") == "bg_tasks" and f["tasks"] == [])
                sess._cancel_grace()
        finally:
            await close()
        assert len(_bg(frames)) == 2
        assert not cs._BG_TASK_LISTENERS

    asyncio.run(run())


def test_a_stream_connecting_while_tasks_run_gets_them_once():
    async def run():
        sess = _live(MINE)
        sess._active_tasks = {"b": "npm run dev"}
        sess._task_meta = {"b": {"kind": "bash", "started_at": 1.0}}
        idle = _live(THEIRS)  # no tasks: no frame
        assert not idle._active_tasks
        frames, close = await _open(_route())
        try:
            await _until(frames, lambda f: f.get("type") == "bg_tasks")
            await asyncio.sleep(1.2)
        finally:
            await close()
        assert _bg(frames) == [{"type": "bg_tasks", "conversation_id": MINE,
                                "tasks": [{"task_id": "b", "desc": "npm run dev",
                                           "kind": "bash", "started_at": 1.0}]}]

    asyncio.run(run())


def test_another_users_chat_and_an_unknown_owner_get_no_frame():
    async def run():
        theirs, broken = _live(THEIRS), _live(BROKEN)
        frames, close = await _open(_route(), user_id=1)
        try:
            await theirs._on_message(_started("a", "their task"))
            await broken._on_message(_started("b", "unknown owner"))
            await asyncio.sleep(0.5)
        finally:
            await close()
        assert _bg(frames) == []

    asyncio.run(run())


def test_the_browser_copy_carries_the_frame():
    assert desktop_channel.BG_TASKS_TYPE == "bg_tasks"
    assert "bg_tasks" in ui_dispatch.BROWSER_FRAME_TYPES
    assert "bg_tasks" not in desktop_channel.DESKTOP_ONLY_TYPES
    frame = {"type": "bg_tasks", "conversation_id": MINE, "tasks": []}
    out = ui_dispatch._SseFilter().feed(b"data: " + json.dumps(frame).encode() + b"\n\n")
    assert json.loads(out.decode()[6:]) == frame

    async def run():
        sess = _live(MINE)
        frames, close = await _open(_route(), remote="1")
        try:
            await sess._on_message(_started("a", "audit"))
            await _until(frames, lambda f: f.get("type") == "bg_tasks")
            sess._cancel_grace()
        finally:
            await close()

    asyncio.run(run())


def test_no_wake_on_top_of_an_unprompted_cli_turn():
    async def run():
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

        sess = _live(MINE)
        await sess._on_message(AssistantMessage(content=[TextBlock("On it.")], model="m"))
        assert sess.unprompted_turn_active
        wake_queue.enqueue(MINE, "tasks_done|audit")
        frames, close = await _open(_route())
        try:
            await asyncio.sleep(1.5)
            assert not [f for f in frames if f.get("type") == "wake"], frames
            with patch.object(cs, "_DB_SAVE_CB", MagicMock()):
                await sess._on_message(ResultMessage("success", 1, 1, False, 1, "s1", result="On it."))
            await _until(frames, lambda f: f.get("type") == "wake")
        finally:
            await close()

    asyncio.run(run())


# ── POST /chat-stream ────────────────────────────────────────────────────
class _Runner:
    messages = []
    kwargs = []
    events = None

    def __init__(self, **kw):
        _Runner.kwargs.append(kw)

    async def run(self, message):
        _Runner.messages.append(message)
        for ev in (_Runner.events or [ar.AgentEvent("response", {"content": "ok"}),
                                      ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})]):
            yield ev


@pytest.fixture
def runner():
    _Runner.messages, _Runner.kwargs, _Runner.events = [], [], None
    with patch("routes.conversation_routes.AgentRunner", _Runner):
        yield _Runner


def _db():
    db = MagicMock()
    db.get_conversation_owner.return_value = 1
    db.get_ai_config.return_value = ("subscription", "claude-opus-5", "", False)
    db.get_api_key.return_value = ""
    db.get_last_workspace.return_value = ""
    db.get_memory.return_value = ""
    db.get_conversation_messages.return_value = []
    db.get_cli_session.return_value = None
    db.get_side_of.return_value = None
    return db


def _post(db, req):
    router = create_conversation_router(db, MagicMock())
    route = next(r for r in router.routes if getattr(r, "path", "") == "/chat-stream")

    async def go():
        resp = await route.endpoint(request=req, x_session_token="t")
        return [chunk async for chunk in resp.body_iterator]

    return asyncio.run(go())


def test_a_user_message_keeps_task_notices_and_drops_mail_notices(runner):
    wake_queue.enqueue(1, "tasks_done|audit")
    wake_queue.enqueue(1, mailbox.notice(7))
    wake_queue.bump_chain(1)
    _post(_db(), ChatRequest(conversation_id=1, message="hello", user_id=1))
    assert wake_queue.drain(1) == ["tasks_done|audit"]
    assert wake_queue.chain(1) == 0
    assert runner.messages == ["hello"]
    assert runner.kwargs[0]["task_wake"] is False


def test_the_turn_after_a_stop_that_killed_tasks_carries_the_notice(runner):
    sess = _live(1)
    sess._stop_killed = ["audit", "dev server"]
    db = _db()
    _post(db, ChatRequest(conversation_id=1, message="again", user_id=1))
    notice = "[Sistem] Durdurma şu arka plan işlerini kesti: audit, dev server. Gerekirse yeniden başlat."
    assert runner.messages == [f"{notice}\n\nagain"]
    rows = [c.args for c in db.add_message.call_args_list]
    assert (1, "system", notice) in rows
    assert (1, "user", "again") in rows  # the user's own row is unchanged
    _post(db, ChatRequest(conversation_id=1, message="next", user_id=1))
    assert runner.messages[-1] == "next"


def test_a_deferred_task_wake_is_queued_again_once(runner):
    runner.events = [ar.AgentEvent("done", {"stop_reason": "wake_deferred"})]
    db = _db()
    for expected in (["tasks_done|audit"], []):
        wake_queue.issue_ticket(1, ["tasks_done|audit"])
        _post(db, ChatRequest(conversation_id=1, message="x", user_id=1, origin="wake"))
        assert runner.kwargs[-1]["task_wake"] is True
        assert wake_queue.drain(1) == expected


def test_a_busy_session_defers_a_task_wake_without_a_reset(monkeypatch):
    from computer_use import settings
    from providers.cli_base import BaseCLIProvider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    values = {settings.ENABLED_KEY: "0"}
    monkeypatch.setattr(settings, "_store", SimpleNamespace(get_setting=values.get))

    async def busy(message):
        raise cs.SessionBusyError("Önceki tur kilidi bırakmadı.")
        yield  # pragma: no cover

    closed = AsyncMock()
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(ar, "_remove_project_mcp_json", lambda *args: None)
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", lambda args: args)
    monkeypatch.setattr(ar.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(mailbox, "claude_server_entry", lambda cid: None)
    monkeypatch.setattr(cs, "_SESSIONS", {})
    monkeypatch.setattr(cs, "get_session",
                        lambda cid, **kw: SimpleNamespace(session_id=None, stream=busy, _broken=False))
    monkeypatch.setattr(cs, "close_session", closed)

    def run(task_wake):
        current = ar.AgentRunner(provider_type="subscription", api_key="", model_name="claude-sonnet-5",
                                 workspace_path=".", language="en", conversation_id=987655,
                                 generation_mode="step", thinking_level="off", effort_level="off",
                                 task_wake=task_wake)

        async def collect():
            return [ev async for ev in current._run_claude_session("wake")]

        return asyncio.run(collect())

    events = run(True)
    assert [(e.type, e.data.get("stop_reason")) for e in events] == [("done", "wake_deferred")]
    closed.assert_not_awaited()
    # A user turn keeps the old recovery: reset and retry once.
    run(False)
    closed.assert_awaited()
