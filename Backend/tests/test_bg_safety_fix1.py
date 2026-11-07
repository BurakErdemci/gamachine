"""Sending, Send now and Stop never kill background work, and no sent message
or its reply is lost (owner's red line, 7 Nov 2026; blind audits of 31775392 /
2c6a7c0d). Every client is a fake; no CLI starts."""
import asyncio
import json
from collections import defaultdict
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import providers.claude_sdk_session as cs
import routes.conversation_routes as cr
from agentic import mailbox, turn_events, wake_queue
from database import DatabaseManager
from providers.cli_base import BaseCLIProvider
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

CID = 8123
H = {"X-Session-Token": ""}


@pytest.fixture(autouse=True)
def _clean_state():
    wake_queue.reset_all()
    turn_events.RING.reset()
    yield
    wake_queue.reset_all()
    turn_events.RING.reset()


class _FakeClient:
    def __init__(self, usage_delay=0.0):
        self.queries, self.interrupts, self.stopped = [], 0, []
        self.usage_delay = usage_delay
        self.closed = False

    async def query(self, text, *a, **k):
        self.queries.append(text)

    async def interrupt(self):
        self.interrupts += 1

    async def stop_task(self, task_id):
        self.stopped.append(task_id)

    async def get_context_usage(self):
        await asyncio.sleep(self.usage_delay)
        raise RuntimeError("no context reading in tests")

    async def __aexit__(self, *a):
        self.closed = True
        return None


def _session(cid=CID, usage_delay=0.0):
    sess = cs.ClaudeSDKSession(conversation_id=cid)
    sess._started = True
    sess._client = _FakeClient(usage_delay)
    return sess


def _started(task_id, task_type, **raw):
    from claude_agent_sdk import TaskStartedMessage
    data = {"task_id": task_id, "description": task_id, **raw}
    return TaskStartedMessage(subtype="task_started", data=data, task_id=task_id, description=task_id,
                              uuid="u-" + task_id, session_id="s", task_type=task_type)


def _assistant(text):
    from claude_agent_sdk import AssistantMessage, TextBlock
    return AssistantMessage(content=[TextBlock(text)], model="claude-opus-5")


def _echo(text):
    from claude_agent_sdk import UserMessage
    return UserMessage(content=text, parent_tool_use_id=None)


def _result(text=""):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=1, session_id="s", result=text)


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


# ── RL-1: a send after Stop while the stopped turn holds the lock ──────────

@pytest.fixture
def runner_env(monkeypatch):
    """The real `_run_claude_session` over a cached session; nothing spawns a CLI."""
    async def no_start(self):
        raise RuntimeError("test: CLI start blocked")

    real_stream = cs.ClaudeSDKSession.stream

    def quick_stream(self, message, lock_timeout=10.0):
        # The first 10 s lock wait shortened; the runner's own longer wait kept.
        return real_stream(self, message, lock_timeout=0.3 if lock_timeout == 10.0 else lock_timeout)

    monkeypatch.setattr(cs.ClaudeSDKSession, "start", no_start)
    monkeypatch.setattr(cs.ClaudeSDKSession, "stream", quick_stream)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(ar, "_remove_project_mcp_json", lambda *a: None)
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", staticmethod(lambda args: args))
    monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(mailbox, "claude_server_entry", lambda cid: None)
    monkeypatch.setattr(ar, "_oturum_yeniden_kurma_gerekceleri", lambda *a, **k: [])
    monkeypatch.setattr(cs, "_identity_mismatch", lambda s, k: None)
    monkeypatch.setattr(cs, "_SESSIONS", {})
    monkeypatch.setattr(ar, "_BUSY_FOLLOW_S", 5.0, raising=False)

    def runner():
        r = ar.AgentRunner(provider_type="subscription", api_key="", model_name="claude-sonnet-5",
                           workspace_path=".", language="en", conversation_id=CID,
                           generation_mode="step", thinking_level="off", effort_level="off")
        r._agent_guide = "g"
        return r
    return runner


async def _busy_session_with_subagent(turn_running=True):
    sess = _session()
    cs._SESSIONS[CID] = sess
    if turn_running:
        sess._begin_turn()
        sess._out_q = asyncio.Queue()
        sess._owed_results = 1
    await sess._turn_lock.acquire()  # the stopped turn's stream still holds it
    await sess._on_message(_started("bgagent", "local_agent", subagent_type="x"))
    return sess


def _details(events):
    return [str((e.data or {}).get("detail", "")) for e in events if e.type == "status"]


def test_a_send_after_stop_joins_the_running_turn_and_keeps_the_subagent(runner_env):
    async def main():
        sess = await _busy_session_with_subagent()
        await sess.cancel_turn()  # soft Stop: the turn goes on until it reads the note
        assert sess._turn_active and sess._client.interrupts == 0

        async def turn_ends():
            await asyncio.sleep(0.8)
            await sess._on_message(_result())
        ender = asyncio.create_task(turn_ends())
        events = [e async for e in runner_env()._run_claude_session("next message")]
        await ender
        return sess, events
    sess, events = asyncio.run(main())
    assert cs._SESSIONS.get(CID) is sess
    assert "bgagent" in sess._active_tasks
    assert sess._client.queries[-1] == "next message"
    assert sess._client.interrupts == 0 and not sess._client.closed
    assert not any("yeniden başlatılıyor" in d for d in _details(events))
    assert events[-1].type == "done"


def test_a_send_that_cannot_join_waits_for_the_lock_then_runs(runner_env):
    async def main():
        sess = await _busy_session_with_subagent(turn_running=False)

        async def lock_frees_then_answer():
            await asyncio.sleep(0.6)
            sess._turn_lock.release()
            await asyncio.sleep(0.2)
            await sess._on_message(_assistant("done it"))
            await sess._on_message(_result("done it"))
        helper = asyncio.create_task(lock_frees_then_answer())
        events = [e async for e in runner_env()._run_claude_session("next message")]
        await helper
        return sess, events
    sess, events = asyncio.run(main())
    assert ar._BUSY_WAITING_DETAIL in _details(events)
    assert cs._SESSIONS.get(CID) is sess and "bgagent" in sess._active_tasks
    assert sess._client.queries == ["next message"]
    assert any(e.type == "done" for e in events)


def test_a_send_still_busy_gets_an_error_and_no_reset(runner_env, monkeypatch):
    monkeypatch.setattr(ar, "_BUSY_LOCK_WAIT_S", 0.3, raising=False)

    async def main():
        sess = await _busy_session_with_subagent(turn_running=False)
        return sess, [e async for e in runner_env()._run_claude_session("next message")]
    sess, events = asyncio.run(main())
    assert events[-1].type == "error" and events[-1].data["message"] == ar._BUSY_GAVE_UP
    assert cs._SESSIONS.get(CID) is sess and "bgagent" in sess._active_tasks
    assert not sess._client.closed


def test_a_busy_session_without_background_work_is_still_reset(runner_env):
    async def main():
        sess = _session()
        cs._SESSIONS[CID] = sess
        await sess._turn_lock.acquire()
        return sess, sess._client, [e async for e in runner_env()._run_claude_session("next message")]
    sess, fake, events = asyncio.run(main())
    assert any("yeniden başlatılıyor" in d for d in _details(events))
    assert cs._SESSIONS.get(CID) is not sess and fake.closed


# ── CC-1: output arriving while the stream finishes ────────────────────────

def test_an_echo_during_the_streams_context_reading_is_shown_and_stored(monkeypatch):
    saved, splits, frames = [], [], []
    monkeypatch.setattr(cs, "_DB_SAVE_CB", lambda cid, text, model=None: saved.append(text))
    monkeypatch.setattr(cs, "_INJECT_SAVE_CB", lambda cid, before, text, model=None: splits.append((before, text)))
    listener = lambda cid, f: frames.append(f)  # noqa: E731
    cs.add_inject_listener(listener)

    async def main():
        sess = _session(7001, usage_delay=1.0)
        task, _ = await _open_stream(sess)
        await sess._on_message(_assistant("working"))
        await sess.inject("change of plan", "c3")
        sess._cancel_requested = True       # what cancel_turn does
        await sess._on_message(_result())   # the stopped turn's Result
        await _settle()
        # The stream now waits on its context reading; the CLI runs the message.
        await sess._on_message(_echo("change of plan"))
        await sess._on_message(_assistant("new plan done"))
        await sess._on_message(_result("new plan done"))
        await asyncio.wait_for(task, 3)
    try:
        asyncio.run(main())
    finally:
        cs.remove_inject_listener(listener)
    assert frames == [{"type": "user_injected", "client_id": "c3", "text": "change of plan"}]
    assert splits == [("", "change of plan")]
    assert saved == ["new plan done"]


# ── CC-2: a sent message that is never echoed ──────────────────────────────

def test_a_never_echoed_message_is_dropped_when_its_turn_ends():
    async def main():
        sess = _session(7002)
        task, first = await _open_stream(sess)
        await sess.inject("A", "cA")
        await sess._on_message(_result("x"))
        await sess._on_message(_result("y"))   # the turn ends owing nothing, A unread
        await asyncio.wait_for(task, 2)
        assert {"type": "user_injected_dropped", "client_ids": ["cA"]} in first
        assert sess._pending_injects == []

        task, second = await _open_stream(sess, "second")
        await sess.inject("B", "cB")
        await sess._on_message(_echo("second"))
        await sess._on_message(_echo("B"))
        await sess._on_message(_assistant("ok"))
        await sess._on_message(_result("ok"))
        await asyncio.wait_for(task, 2)
        assert [e for e in second if e["type"] == "user_injected"] == [
            {"type": "user_injected", "client_id": "cB", "text": "B"}]
        assert sess._owed_results == 0
    asyncio.run(main())


def test_an_old_unechoed_head_does_not_block_a_later_echo():
    async def main():
        sess = _session(7003)
        task, events = await _open_stream(sess)
        await sess.inject("A", "cA")
        await sess.inject("B", "cB")
        sess._pending_injects[0]["at"] -= cs._PENDING_INJECT_MAX_AGE_S + 1
        await sess._on_message(_echo("B"))
        await sess._on_message(_result("ok"))   # one Result: A owes nothing any more
        await asyncio.wait_for(task, 2)
        return events
    events = asyncio.run(main())
    kinds = [(e["type"], e.get("client_ids") or e.get("client_id")) for e in events
             if e["type"].startswith("user_injected")]
    assert kinds == [("user_injected_dropped", ["cA"]), ("user_injected", "cB")]


# ── Stop: never a reset over background work, never twice ──────────────────

def test_stop_whose_interrupt_does_not_end_the_turn_keeps_a_background_bash(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(cs.asyncio, "sleep", lambda *a, **k: real_sleep(0))

    async def main():
        sess = _session(770991)
        sess._begin_turn()
        sess._out_q = asyncio.Queue()
        sess._owed_results = 1
        await sess._on_message(_started("bash1", "local_bash", is_backgrounded=True))
        await asyncio.wait_for(sess.cancel_turn(), 10)
        frames = []
        while not sess._out_q.empty():
            frames.append(sess._out_q.get_nowait())
        return sess, frames
    sess, frames = asyncio.run(main())
    assert sess._client.interrupts == 1 and not sess._client.closed
    assert "bash1" in sess._active_tasks and cs.take_reset_notice(770991) == ""
    assert not sess._turn_active and frames[-1] is None  # the stream was ended


def test_a_second_stop_while_one_is_in_flight_sends_no_second_note():
    async def main():
        sess = _session(771177)
        sess._begin_turn()
        sess._out_q = asyncio.Queue()
        sess._owed_results = 1
        await sess._on_message(_started("agent1", "local_agent"))
        await sess.cancel_turn()
        await sess.cancel_turn()  # double click, held Esc, a second client
        return sess
    sess = asyncio.run(main())
    assert sess._client.queries.count(cs._STOP_NOTE) == 1
    assert sess._owed_results == 2


# ── Route: deferral line, /chat-inject promises ────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return DatabaseManager(str(tmp_path / "fix1.db"))


class _DeferRunner:
    def __init__(self, **kw):
        self.session_defer_notice = "Ayar değişikliği (çalışma klasörü) arka plan işleri bitince uygulanacak."

    async def run(self, message):
        yield ar.AgentEvent("response", {"content": "on it"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    monkeypatch.setattr(cr, "AgentRunner", _DeferRunner)

    async def _no_close(cid):
        return None

    monkeypatch.setattr(cs, "close_session", _no_close)
    fast = FastAPI()
    fast.include_router(cr.create_conversation_router(db, {}))
    with TestClient(fast) as c:
        yield c


class _Live:
    def __init__(self):
        self.calls = []

    async def inject(self, text, client_id):
        self.calls.append((text, client_id))
        return True


@pytest.fixture
def live(monkeypatch):
    sess = _Live()
    monkeypatch.setattr(cs, "peek_session", lambda c: sess)
    return sess


def test_a_deferred_rebuild_is_a_system_row_in_the_chat(db, client):
    cid = db.create_conversation(1, "Yeni Sohbet")
    db.save_ai_config(1, "subscription", "claude-opus-5", "")
    client.post("/chat-stream", json={"conversation_id": cid, "message": "go on", "user_id": 1}, headers=H)
    rows = [(m["role"], m["content"]) for m in db.get_conversation_messages(cid)]
    assert ("system", "Ayar değişikliği (çalışma klasörü) arka plan işleri bitince uygulanacak.") in rows


def test_chat_inject_shares_the_chat_rate_limit(db, client, live):
    import time
    cid = db.create_conversation(1, "Yeni Sohbet")
    cr.CHAT_RATE_LIMIT[1] = [time.time()] * cr.CHAT_RATE_LIMIT_MAX
    r = client.post(f"/chat-inject/{cid}", json={"message": "hi", "client_id": "c1"}, headers=H)
    assert r.status_code == 429 and live.calls == []


def test_chat_inject_caps_the_message_like_the_chat_route(db, client, live):
    cid = db.create_conversation(1, "Yeni Sohbet")
    r = client.post(f"/chat-inject/{cid}", json={"message": "x" * 200_001, "client_id": "c1"}, headers=H)
    assert r.status_code == 422 and live.calls == []
    r = client.post(f"/chat-inject/{cid}", json={"message": "x" * 200_000, "client_id": "c1"}, headers=H)
    assert r.json() == {"status": "injected"}
