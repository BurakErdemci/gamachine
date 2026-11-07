"""A message sent while a Claude turn runs reaches the agent inside that turn,
as in terminal Claude Code (Burak, 7 Nov 2026): no interrupt, no stopped
background work. Measured with the real CLI on 7 Nov 2026 (M1/M4): the CLI
echoes each user message it takes in (replay-user-messages), one Result
answers a message delivered mid-turn, and after a Stop the CLI runs a sent
message as its own turn. Every client here is a fake; no CLI starts."""
import asyncio
import json
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import providers.claude_sdk_session as cs
import routes.conversation_routes as cr
from agentic import turn_events, wake_queue
from database import DatabaseManager

H = {"X-Session-Token": ""}


@pytest.fixture(autouse=True)
def _clean_state():
    wake_queue.reset_all()
    turn_events.RING.reset()
    yield
    wake_queue.reset_all()
    turn_events.RING.reset()


class _FakeClient:
    def __init__(self):
        self.queries = []
        self.interrupts = 0

    async def query(self, text, *a, **k):
        self.queries.append(text)

    async def interrupt(self):
        self.interrupts += 1

    async def get_context_usage(self):
        raise RuntimeError("no context reading in tests")

    async def __aexit__(self, *a):
        return None


def _session(cid=5151):
    sess = cs.ClaudeSDKSession(conversation_id=cid)
    sess._started = True
    sess._client = _FakeClient()
    return sess


def _assistant(text):
    from claude_agent_sdk import AssistantMessage, TextBlock
    return AssistantMessage(content=[TextBlock(text)], model="claude-opus-5")


def _echo(text):
    from claude_agent_sdk import UserMessage
    return UserMessage(content=text, parent_tool_use_id=None)


def _result(text=""):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id="s1", result=text)


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def _open_stream(sess, message="first"):
    events = []

    async def consume():
        async for ev in sess.stream(message):
            if not ev.get("heartbeat"):
                events.append(ev)

    task = asyncio.create_task(consume())
    await _settle()
    return task, events


def test_inject_during_a_listened_turn_sends_without_interrupt_or_the_lock():
    async def main():
        sess = _session()
        task, _ = await _open_stream(sess)
        assert sess._turn_lock.locked()  # the stream holds it the whole turn
        ok = await asyncio.wait_for(sess.inject("also do X", "c1"), 2)
        assert ok is True
        assert sess._client.queries == ["first", "also do X"]
        assert sess._client.interrupts == 0
        assert sess._owed_results == 2
        await sess._on_message(_result())
        await sess._on_message(_result())
        await asyncio.wait_for(task, 2)
    asyncio.run(main())


def test_inject_refused_without_a_listened_turn():
    async def main():
        sess = _session()
        assert await sess.inject("hi", "c1") is False
        assert sess._client.queries == []
        assert sess._owed_results == 0
    asyncio.run(main())


def test_echo_inside_the_same_turn_emits_user_injected_and_one_result_ends_the_stream():
    async def main():
        sess = _session()
        task, events = await _open_stream(sess)
        await sess._on_message(_echo("first"))  # the opening query's own echo
        await sess._on_message(_assistant("part one"))
        await sess.inject("also do X", "c1")
        await sess._on_message(_echo("also do X"))
        await sess._on_message(_assistant("the rest"))
        await sess._on_message(_result("the rest"))
        await asyncio.wait_for(task, 2)
        injected = [e for e in events if e["type"] == "user_injected"]
        assert injected == [{"type": "user_injected", "client_id": "c1", "text": "also do X"}]
        types = [e["type"] for e in events]
        assert types.index("user_injected") < types.index("response")
        response = next(e for e in events if e["type"] == "response")
        assert response["content"] == "the rest"
        assert sess._owed_results == 0
        assert sess._pending_injects == []
    asyncio.run(main())


def test_result_before_echo_keeps_the_stream_open_until_the_second_result():
    async def main():
        sess = _session()
        task, events = await _open_stream(sess)
        await sess._on_message(_assistant("answer one"))
        await sess.inject("and then Y", "c2")
        await sess._on_message(_result("answer one"))
        await _settle()
        assert not task.done()
        await sess._on_message(_echo("and then Y"))
        await sess._on_message(_assistant("answer two"))
        await _settle()
        assert not task.done()
        await sess._on_message(_result("answer two"))
        await asyncio.wait_for(task, 2)
        assert any(e["type"] == "user_injected" and e["client_id"] == "c2" for e in events)
    asyncio.run(main())


def test_stop_with_a_pending_injection_then_the_clis_own_turn_emits_user_injected(monkeypatch):
    frames, saved, splits = [], [], []
    monkeypatch.setattr(cs, "_DB_SAVE_CB", lambda cid, text, model=None: saved.append(text))
    monkeypatch.setattr(cs, "_INJECT_SAVE_CB",
                        lambda cid, before, text, model=None: splits.append((before, text)))
    listener = lambda cid, frame: frames.append((cid, frame))  # noqa: E731
    cs.add_inject_listener(listener)
    try:
        async def main():
            sess = _session(cid=5252)
            task, _ = await _open_stream(sess)
            await sess._on_message(_assistant("working"))
            await sess.inject("change of plan", "c3")
            # What cancel_turn does before the CLI answers the interrupt.
            sess._cancel_requested = True
            await sess._on_message(_result())  # error_during_execution in the CLI
            await asyncio.wait_for(task, 2)
            assert not sess._turn_active
            # ~1.8 s later the CLI echoes the message and runs it by itself (M4).
            await sess._on_message(_echo("change of plan"))
            assert sess._turn_active and sess.unprompted_turn_active
            await sess._on_message(_assistant("new plan done"))
            await sess._on_message(_result("new plan done"))
            assert not sess._turn_active
            assert sess._owed_results == 0
        asyncio.run(main())
    finally:
        cs.remove_inject_listener(listener)
    assert frames == [(5252, {"type": "user_injected", "client_id": "c3", "text": "change of plan"})]
    assert splits == [("", "change of plan")]
    assert saved == ["new plan done"]
    kinds = [e["kind"] for e in turn_events.since(5252, 0)["events"]]
    assert "turn_start" in kinds


def test_close_drops_pending_injections_as_unsent():
    async def main():
        sess = _session()
        task, events = await _open_stream(sess)
        await sess.inject("one", "a")
        await sess.inject("two", "b")
        await sess.close()
        await _settle()
        assert sess._pending_injects == []
        dropped = [e for e in events if e["type"] == "user_injected_dropped"]
        if not dropped:  # the stream may still be waiting: read its queue
            q = sess._out_q
            while q is not None and not q.empty():
                ev = q.get_nowait()
                if ev and ev.get("type") == "user_injected_dropped":
                    dropped.append(ev)
        assert dropped == [{"type": "user_injected_dropped", "client_ids": ["a", "b"]}]
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    asyncio.run(main())


def test_close_without_a_stream_tells_the_listeners():
    frames = []
    listener = lambda cid, frame: frames.append(frame)  # noqa: E731
    cs.add_inject_listener(listener)
    try:
        async def main():
            sess = _session()
            sess._pending_injects = [{"client_id": "z", "text": "t", "answered": True}]
            await sess.close()
        asyncio.run(main())
    finally:
        cs.remove_inject_listener(listener)
    assert frames == [{"type": "user_injected_dropped", "client_ids": ["z"]}]


def test_the_opening_querys_echo_is_not_an_injection():
    async def main():
        sess = _session()
        task, events = await _open_stream(sess, "same words")
        # Sent before the CLI echoed the opening query, with the same text.
        await sess.inject("same words", "c4")
        await sess._on_message(_echo("same words"))  # the opening query
        assert not any(e["type"] == "user_injected" for e in events)
        assert len(sess._pending_injects) == 1
        await sess._on_message(_assistant("ok"))
        await sess._on_message(_echo("same words"))  # the injected one
        await sess._on_message(_result("ok"))
        await asyncio.wait_for(task, 2)
        assert [e["client_id"] for e in events if e["type"] == "user_injected"] == ["c4"]
    asyncio.run(main())


def test_session_options_turn_on_replay_user_messages(monkeypatch):
    captured = {}

    class _Opts:
        def __init__(self, **kw):
            captured.update(kw)

    class _Client:
        def __init__(self, options):
            pass

        async def __aenter__(self):
            return self

        async def receive_messages(self):
            if False:
                yield None

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions", _Opts)
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", _Client)
    monkeypatch.setattr(cs, "claude_ikilisini_coz", lambda: None)

    async def main():
        sess = cs.ClaudeSDKSession(conversation_id=5353)
        await sess.start()
        await asyncio.sleep(0)
    asyncio.run(main())
    assert captured["extra_args"] == {"replay-user-messages": None}


# ── Route ───────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return DatabaseManager(str(tmp_path / "inject.db"))


class _SplitRunner:
    def __init__(self, **kw):
        self.kw = kw

    async def run(self, message):
        yield ar.AgentEvent("text", {"content": "looking at the scene"})
        yield ar.AgentEvent("user_injected", {"client_id": "c9", "text": "use the red cube"})
        yield ar.AgentEvent("text", {"content": "red cube placed"})
        yield ar.AgentEvent("response", {"content": "red cube placed"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    monkeypatch.setattr(cr, "AgentRunner", _SplitRunner)

    async def _no_close(cid):
        return None

    monkeypatch.setattr(cs, "close_session", _no_close)
    fast = FastAPI()
    fast.include_router(cr.create_conversation_router(db, {}))
    with TestClient(fast) as c:
        yield c


def test_chat_inject_answers_not_running_without_a_live_turn(db, client):
    cid = db.create_conversation(1, "Yeni Sohbet")
    r = client.post(f"/chat-inject/{cid}", json={"message": "hi", "client_id": "c1"}, headers=H)
    assert r.status_code == 200
    assert r.json() == {"status": "not_running"}


def test_chat_inject_reaches_the_live_session(db, client):
    cid = db.create_conversation(1, "Yeni Sohbet")
    calls = []

    class _Live:
        async def inject(self, text, client_id):
            calls.append((text, client_id))
            return True

    orig = cs.peek_session
    cs.peek_session = lambda c: _Live() if c == cid else None
    try:
        r = client.post(f"/chat-inject/{cid}", json={"message": "hi", "client_id": "c1"}, headers=H)
    finally:
        cs.peek_session = orig
    assert r.json() == {"status": "injected"}
    assert calls == [("hi", "c1")]


def test_stream_stores_assistant_part_then_injected_message_then_the_rest(db, client):
    cid = db.create_conversation(1, "Yeni Sohbet")
    db.save_ai_config(1, "subscription", "claude-opus-5", "")
    r = client.post("/chat-stream", json={"conversation_id": cid, "message": "build a scene",
                                          "user_id": 1}, headers=H)
    frames = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert any(f.get("type") == "user_injected" and f.get("client_id") == "c9" for f in frames)
    rows = [(m["role"], m["content"]) for m in db.get_conversation_messages(cid)]
    assert rows == [
        ("user", "build a scene"),
        ("assistant", "looking at the scene"),
        ("user", "use the red cube"),
        ("assistant", "red cube placed"),
    ]
