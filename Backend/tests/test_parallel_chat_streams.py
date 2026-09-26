"""Parallel chats: several `/chat-stream` turns of the same user at once.

The frontend is about to run two or more turns concurrently, each in its own
conversation. These tests run 2 and 4 real turns through the route on one event
loop against a real SQLite file and check that the turns neither block each
other nor leak into each other: every event reaches its own stream, every stream
gets its terminal, every answer lands in its own conversation, and nothing
raises (`database is locked` included).

A barrier inside the fake runner holds each turn until ALL of them are
streaming. Without it the test could pass on a backend that quietly serialised
turns, because gather() would still collect every response in the end.
"""
import asyncio
import json
import logging
from collections import defaultdict

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from database import DatabaseManager

CHUNKS = 6


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # An explicit key keeps DatabaseManager away from the OS keyring.
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    # Fresh limiter: the real 15/min rule still applies to these turns, but
    # entries from earlier tests cannot trip it and ours cannot leak onward.
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    db = DatabaseManager(str(tmp_path / "parallel.db"))
    db.save_ai_config(1, "claude", "claude-opus-5", "")
    return db


def _runner_factory(n_turns: int, order_log: list):
    barrier = asyncio.Barrier(n_turns)

    class _ParallelRunner:
        def __init__(self, **kw):
            self.conv = kw["conversation_id"]

        async def run(self, message):
            order_log.append((self.conv, "start"))
            try:
                await asyncio.wait_for(barrier.wait(), timeout=10)
            except asyncio.TimeoutError:
                raise AssertionError("turns did not overlap: the backend serialised them")
            for i in range(CHUNKS):
                await asyncio.sleep(0.005 * (1 + (self.conv + i) % 3))
                order_log.append((self.conv, i))
                yield ar.AgentEvent("text", {"content": f"c{self.conv}-chunk{i}"})
            yield ar.AgentEvent("response", {"content": f"answer-for-{self.conv}"})
            yield ar.AgentEvent("turn_usage", {"input_tokens": self.conv, "output_tokens": 1})
            yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})

    return _ParallelRunner


def _events(body: str) -> list:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


async def _run_parallel(db, conv_ids, runner_cls):
    app = FastAPI()
    app.include_router(cr.create_conversation_router(db, {}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async def one(cid):
            return await client.post(
                "/chat-stream",
                json={"conversation_id": cid, "user_id": 1, "message": f"question-for-{cid}"},
                headers={"X-Session-Token": ""},
                timeout=30,
            )
        return await asyncio.gather(*(one(c) for c in conv_ids))


@pytest.mark.parametrize("n_turns", [2, 4])
def test_parallel_turns_stay_in_their_own_conversation(isolated, monkeypatch, caplog, n_turns):
    db = isolated
    conv_ids = [db.create_conversation(1, f"chat {i}") for i in range(n_turns)]
    order_log: list = []
    monkeypatch.setattr(cr, "AgentRunner", _runner_factory(n_turns, order_log))

    with caplog.at_level(logging.WARNING):
        responses = asyncio.run(_run_parallel(db, conv_ids, cr.AgentRunner))

    # Real overlap, not just eventual completion: every turn had streamed a
    # chunk before any turn streamed its last one.
    first = {c: order_log.index((c, 0)) for c in conv_ids}
    last = {c: order_log.index((c, CHUNKS - 1)) for c in conv_ids}
    assert max(first.values()) < min(last.values()), order_log

    for cid, resp in zip(conv_ids, responses):
        assert resp.status_code == 200, (cid, resp.status_code, resp.text)
        events = _events(resp.text)
        types = [e["type"] for e in events]
        assert "error" not in types and "warning" not in types, (cid, events)
        assert types.count("done") == 1, (cid, types)

        texts = [e["content"] for e in events if e["type"] == "text"]
        assert texts == [f"c{cid}-chunk{i}" for i in range(CHUNKS)], (cid, texts)
        assert [e["content"] for e in events if e["type"] == "response"] == [f"answer-for-{cid}"]

        usage = events[-1]
        assert usage["type"] == "context_usage", events[-1]
        # The gauge counted THIS conversation: its user message + its answer.
        assert usage["message_count"] == 2, usage
        assert usage["last_turn"]["input_tokens"] == cid, usage

        # Every event names the conversation its stream belongs to.
        assert all(e.get("conversation_id") == cid for e in events), (cid, events)

        stored = [(m["role"], m["content"]) for m in db.get_conversation_messages(cid)]
        assert stored == [("user", f"question-for-{cid}"),
                          ("assistant", f"answer-for-{cid}")], (cid, stored)

    assert "database is locked" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], caplog.text


def test_every_event_of_a_turn_carries_its_conversation_id(isolated, monkeypatch):
    """Covers the frames written outside `AgentEvent.to_sse`: the save-failure
    `warning`, the route's own `error`, and the trailing `context_usage`."""
    db = isolated
    cid = db.create_conversation(1, "one")
    real_add = db.add_message

    def failing_assistant_write(conv_id, role, content, *a, **kw):
        if role == "assistant":
            raise RuntimeError("disk full")
        return real_add(conv_id, role, content, *a, **kw)

    monkeypatch.setattr(db, "add_message", failing_assistant_write)

    class _BrokenRunner:
        def __init__(self, **kw):
            pass

        async def run(self, message):
            yield ar.AgentEvent("text", {"content": "partial"})
            yield ar.AgentEvent("response", {"content": "answer"})
            yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})

    monkeypatch.setattr(cr, "AgentRunner", _BrokenRunner)
    (resp,) = asyncio.run(_run_parallel(db, [cid], _BrokenRunner))
    events = _events(resp.text)
    assert {"text", "response", "done", "warning", "context_usage"} <= {e["type"] for e in events}
    assert all(e.get("conversation_id") == cid for e in events), events

    class _RaisingRunner:
        def __init__(self, **kw):
            pass

        async def run(self, message):
            yield ar.AgentEvent("text", {"content": "partial"})
            raise RuntimeError("provider died")

    monkeypatch.setattr(cr, "AgentRunner", _RaisingRunner)
    (resp,) = asyncio.run(_run_parallel(db, [cid], _RaisingRunner))
    events = _events(resp.text)
    assert [e["type"] for e in events] == ["text", "error", "context_usage"], events
    assert all(e.get("conversation_id") == cid for e in events), events


def test_the_wake_chain_exhausted_frame_carries_its_conversation_id(isolated, monkeypatch):
    from agentic import wake_queue

    db = isolated
    cid = db.create_conversation(1, "woken")
    monkeypatch.setattr(wake_queue, "consume_ticket", lambda conv_id: True)
    monkeypatch.setattr(wake_queue, "chain_exhausted", lambda conv_id: True)

    async def go():
        app = FastAPI()
        app.include_router(cr.create_conversation_router(db, {}))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/chat-stream",
                json={"conversation_id": cid, "user_id": 1, "message": "wake", "origin": "wake"},
                headers={"X-Session-Token": ""})

    events = _events(asyncio.run(go()).text)
    assert events == [{"type": "done", "stop_reason": "wake_chain_exhausted",
                       "conversation_id": cid}], events


def test_an_existing_conversation_id_field_is_not_overwritten():
    frame = 'data: {"type": "x", "conversation_id": 99}\n\n'
    assert cr._tag_sse_frame(frame, 1) == frame
    assert cr._tag_sse_frame(": keep-alive\n\n", 1) == ": keep-alive\n\n"
    assert json.loads(cr._tag_sse_frame('data: {}\n\n', 7)[6:]) == {"conversation_id": 7}
