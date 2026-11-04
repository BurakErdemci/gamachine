"""A session close that kills background tasks tells the model on the next turn.

A model, effort or workspace change, a compact, or any other close of a Claude
session takes the CLI's background tasks with it. The row clearing on its own
told nobody why (audit of the bg-wait lane, 4 Nov 2026): the names are kept and
one system line goes in front of the next message to the model, like the Stop
notice. The user's model change is never blocked or delayed by it.
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import providers.claude_sdk_session as cs
from agentic import agent_runner as ar
from agentic import mailbox, turn_events, wake_queue
from schemas import ChatRequest
from test_bg_tasks_frames import MINE, _db, _live, _open, _post, _route, _started, runner  # noqa: F401  (runner is a fixture)

CONV = 9301
NOTICE = ("[Sistem] Oturum yenilendi (model/ayar değişikliği); şu arka plan işleri kesildi: "
          "dev server, audit. Gerekirse yeniden başlat.")


@pytest.fixture(autouse=True)
def _fresh():
    turn_events.RING.reset()
    wake_queue.reset_all()
    cs._RESET_KILLED.clear()
    yield
    for cid in (CONV, 1, MINE):
        cs._SESSIONS.pop(cid, None)
    cs._RESET_KILLED.clear()
    cs._BG_TASK_LISTENERS.clear()
    turn_events.RING.reset()
    wake_queue.reset_all()


async def _with_tasks(conv_id):
    sess = _live(conv_id)
    sess.model = "claude-opus-5"
    await sess._on_message(_started("b", "dev server", "local_bash"))
    await sess._on_message(_started("a", "audit"))
    return sess


def test_a_model_change_records_the_killed_tasks_and_clears_the_row():
    async def run():
        sess = await _with_tasks(CONV)
        seen = []
        cs.add_bg_tasks_listener(lambda cid, tasks: seen.append((cid, [t["desc"] for t in tasks])))
        new = cs.get_session(CONV, model="claude-sonnet-5")
        # Recorded at the moment of the switch, before the close finishes in the background.
        pending = list(cs._RESET_KILLED.get(CONV, []))
        await asyncio.sleep(0.1)
        return new is sess, pending, seen

    same, pending, seen = asyncio.run(run())
    assert same is False
    assert pending == ["dev server", "audit"]
    assert seen[-1] == (CONV, [])
    assert cs.take_reset_notice(CONV) == NOTICE
    assert cs.take_reset_notice(CONV) == ""


def test_any_close_with_tasks_running_leaves_the_notice_once():
    async def run():
        sess = await _with_tasks(CONV)
        await cs.close_session(CONV)       # compact, chat reset, broken session ...
        return sess.take_stop_notice(), sess.take_stop_notice()

    first, second = asyncio.run(run())
    assert first == NOTICE
    assert second == ""


def test_a_close_with_no_task_running_leaves_nothing():
    async def run():
        _live(CONV)
        await cs.close_session(CONV)

    asyncio.run(run())
    assert cs.take_reset_notice(CONV) == ""


def test_the_next_turn_carries_the_notice_and_a_system_row(runner):
    asyncio.run(_with_tasks(1))
    asyncio.run(cs.close_session(1))
    db = _db()
    _post(db, ChatRequest(conversation_id=1, message="hi", user_id=1))
    assert runner.messages == [f"{NOTICE}\n\nhi"]
    rows = [c.args for c in db.add_message.call_args_list]
    assert (1, "system", NOTICE) in rows
    assert (1, "user", "hi") in rows
    _post(db, ChatRequest(conversation_id=1, message="next", user_id=1))
    assert runner.messages[-1] == "next"


def test_the_runners_model_change_tells_the_new_model_in_the_same_turn(monkeypatch):
    from computer_use import settings
    from providers.cli_base import BaseCLIProvider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    values = {settings.ENABLED_KEY: "0"}
    monkeypatch.setattr(settings, "_store", SimpleNamespace(get_setting=values.get))
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(ar, "_remove_project_mcp_json", lambda *args: None)
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", lambda args: args)
    monkeypatch.setattr(ar.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(mailbox, "claude_server_entry", lambda cid: None)
    sent = []

    async def stream(message):
        sent.append(message)
        yield {"type": "done", "stop_reason": "complete"}

    monkeypatch.setattr(cs, "get_session",
                        lambda cid, **kw: SimpleNamespace(session_id="s1", stream=stream, _broken=False))

    async def run():
        old = await _with_tasks(CONV)
        old.effort = None
        old.cwd = "."
        current = ar.AgentRunner(provider_type="subscription", api_key="", model_name="claude-sonnet-5",
                                 workspace_path=".", language="en", conversation_id=CONV,
                                 generation_mode="step", thinking_level="off", effort_level="off")
        events = [ev async for ev in current._run_claude_session("go on")]
        return current, events

    current, events = asyncio.run(run())
    assert CONV not in cs._SESSIONS
    assert sent == [f"{NOTICE}\n\ngo on"]
    assert current.session_reset_notice == NOTICE
    assert cs.take_reset_notice(CONV) == ""
    assert events[-1].type == "done"


def test_the_route_stores_the_runners_reset_notice_as_a_system_row(runner):
    class _Resetting(runner):
        async def run(self, message):
            # The runner replaced the session mid-turn and told the new model.
            self.session_reset_notice = NOTICE
            async for ev in runner.run(self, message):
                yield ev

    db = _db()
    # Not monkeypatch: an autouse fixture sets it up before `runner`, so its
    # undo would run last and leave the fake runner in place.
    with patch("routes.conversation_routes.AgentRunner", _Resetting):
        _post(db, ChatRequest(conversation_id=1, message="hi", user_id=1))
    rows = [c.args for c in db.add_message.call_args_list]
    assert rows.index((1, "system", NOTICE)) < rows.index((1, "assistant", "ok"))


# ── A wake frame nobody acted on (audit B4, 4 Nov 2026) ──────────────────
# The renderer declines a wake frame while the chat is loading or has a queued
# message, and an SSE can drop: the drained notices lived only in the ticket,
# and its expiry deleted them, so the chat never continued.
def test_an_expired_ticket_puts_its_task_notices_back_for_the_next_wake():
    async def run():
        wake_queue.enqueue(MINE, "tasks_done|audit the scene")
        with patch.object(wake_queue, "WAKE_TICKET_TTL", 0.5):
            frames, close = await _open(_route())
            try:
                for _ in range(80):
                    if [f for f in frames if f.get("type") == "wake"]:
                        break
                    await asyncio.sleep(0.05)
                assert wake_queue.pending(MINE) == 0      # drained into the ticket
                await asyncio.sleep(0.6)                  # declined: nobody claims it
                assert not wake_queue.ticket_outstanding(MINE)
                assert wake_queue.pending(MINE) == 1
                for _ in range(100):
                    if len([f for f in frames if f.get("type") == "wake"]) >= 2:
                        break
                    await asyncio.sleep(0.05)
            finally:
                await close()
        return [f["notices"] for f in frames if f.get("type") == "wake"]

    wakes = asyncio.run(run())
    assert wakes[:2] == [["tasks_done|audit the scene"], ["tasks_done|audit the scene"]]


def test_a_consumed_ticket_does_not_come_back():
    wake_queue.enqueue(CONV, "tasks_done|audit")
    wake_queue.issue_ticket(CONV, wake_queue.drain(CONV))
    assert wake_queue.consume_ticket(CONV)
    assert wake_queue.take_ticket_notices(CONV) == ["tasks_done|audit"]
    with patch.object(wake_queue, "WAKE_TICKET_TTL", 0.0):
        assert not wake_queue.ticket_outstanding(CONV)
        wake_queue.issue_ticket(CONV + 1, [])
    assert wake_queue.pending(CONV) == 0


def test_expiry_found_by_the_sweep_of_another_chats_ticket_returns_the_notices():
    wake_queue.enqueue(CONV, "tasks_done|audit")
    wake_queue.issue_ticket(CONV, wake_queue.drain(CONV))
    with patch.object(wake_queue, "WAKE_TICKET_TTL", 0.0):
        wake_queue.issue_ticket(CONV + 1, ["tasks_done|other"])
    assert wake_queue.drain(CONV) == ["tasks_done|audit"]
