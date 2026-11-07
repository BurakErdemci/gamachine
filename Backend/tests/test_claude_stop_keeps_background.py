"""Send, Send now and Stop never kill background subagents or processes
(Burak, 7 Nov 2026). Measured that day with the CLI: interrupt() kills every
background subagent (background Bash survives); stop_task() on the
foreground tool's task ends only that tool and the model carries on. So with
a background subagent running, Stop stops the foreground tool and tells the
model to stop; without one it is Claude Code's Esc (interrupt). A Stop with no
turn running touches nothing. Every client here is a fake; no CLI starts."""
import asyncio

import pytest

import providers.claude_sdk_session as cs
from agentic import turn_events, wake_queue


@pytest.fixture(autouse=True)
def _clean_state():
    wake_queue.reset_all()
    turn_events.RING.reset()
    yield
    wake_queue.reset_all()
    turn_events.RING.reset()


class _FakeClient:
    def __init__(self, sess=None):
        self.sess = sess
        self.queries, self.stopped, self.interrupts = [], [], 0

    async def query(self, text, *a, **k):
        self.queries.append(text)

    async def stop_task(self, task_id):
        self.stopped.append(task_id)

    async def interrupt(self):
        self.interrupts += 1
        if self.sess is not None:
            self.sess._turn_active = False  # the CLI answers with a Result

    async def __aexit__(self, *a):
        return None


def _listened_turn(cid=6161):
    sess = cs.ClaudeSDKSession(conversation_id=cid)
    sess._started = True
    sess._client = _FakeClient(sess)
    sess._begin_turn()
    sess._out_q = asyncio.Queue()
    sess._owed_results = 1
    return sess


def _started(task_id, task_type, **raw):
    from claude_agent_sdk import TaskStartedMessage
    data = {"task_id": task_id, "description": task_id, **raw}
    return TaskStartedMessage(subtype="task_started", data=data, task_id=task_id,
                              description=task_id, uuid="u-" + task_id, session_id="s1",
                              task_type=task_type)


def _updated(task_id, status):
    from claude_agent_sdk import TaskUpdatedMessage
    return TaskUpdatedMessage(subtype="task_updated", data={}, task_id=task_id,
                              patch={"status": status}, status=status)


async def _tasks(sess, bg_agent=True):
    if bg_agent:
        await sess._on_message(_started("agent1", "local_agent", subagent_type="general-purpose"))
    await sess._on_message(_started("fg_bash", "local_bash", is_backgrounded=False))
    await sess._on_message(_started("bg_bash", "local_bash", is_backgrounded=True))
    await sess._on_message(_started("sub_bash", "local_bash", is_backgrounded=False,
                                    owned_by_subagent=True))


def test_task_started_marks_only_the_main_turns_own_tool_as_foreground():
    async def main():
        sess = _listened_turn()
        await _tasks(sess)
        return {tid: m["foreground"] for tid, m in sess._task_meta.items()}
    assert asyncio.run(main()) == {"agent1": False, "fg_bash": True, "bg_bash": False, "sub_bash": False}


def test_stop_with_a_background_subagent_stops_only_the_foreground_tool_and_tells_the_model():
    async def main():
        sess = _listened_turn()
        await _tasks(sess)
        await asyncio.wait_for(sess.cancel_turn(), 2)
        c = sess._client
        assert c.interrupts == 0
        assert c.stopped == ["fg_bash"]
        assert c.queries == [cs._STOP_NOTE]
        assert sess._owed_results == 2
        # No forced reset: the session and its background work stay.
        assert sess._client is not None and sess._started
        assert set(sess._active_tasks) == {"agent1", "fg_bash", "bg_bash", "sub_bash"}
        # Only the stopped tool counts as the Stop's casualty.
        await sess._on_message(_updated("fg_bash", "killed"))
        await sess._on_message(_updated("agent1", "completed"))
        assert sess._stop_killed == ["fg_bash"]
        assert sess._done_tasks == ["agent1"]
    asyncio.run(main())


def test_the_stop_notes_echo_is_neither_shown_nor_stored(monkeypatch):
    splits = []
    monkeypatch.setattr(cs, "_INJECT_SAVE_CB", lambda *a, **k: splits.append(a))

    async def main():
        sess = _listened_turn()
        await _tasks(sess)
        await sess.cancel_turn()
        from claude_agent_sdk import UserMessage
        await sess._on_message(UserMessage(content=cs._STOP_NOTE, parent_tool_use_id=None))
        frames = []
        while not sess._out_q.empty():
            frames.append(sess._out_q.get_nowait())
        assert not any(f and f.get("type") == "user_injected" for f in frames)
        assert sess._pending_injects == []
        assert sess._owed_results == 1  # same turn: one Result answers both
    asyncio.run(main())
    assert splits == []


def test_stop_without_a_background_subagent_interrupts():
    async def main():
        sess = _listened_turn()
        await _tasks(sess, bg_agent=False)
        await asyncio.wait_for(sess.cancel_turn(), 5)
        assert sess._client.interrupts == 1
        assert sess._client.stopped == []
        assert sess._client.queries == []
    asyncio.run(main())


def test_stop_after_the_turn_ended_calls_neither_interrupt_nor_stop_task():
    async def main():
        sess = _listened_turn()
        await _tasks(sess)
        sess._turn_active = False
        await sess.cancel_turn()
        assert (sess._client.interrupts, sess._client.stopped, sess._client.queries) == (0, [], [])
    asyncio.run(main())


def test_a_turn_that_ends_while_stop_releases_its_cards_is_not_interrupted(monkeypatch):
    async def main():
        sess = _listened_turn()
        await _tasks(sess, bg_agent=False)
        sess._active_gate_ids.add("g1")

        def _turn_ends(gid):
            sess._turn_active = False  # the turn's Result landed meanwhile
        monkeypatch.setattr(cs, "cancel_gate", _turn_ends)
        await sess.cancel_turn()
        assert sess._client.interrupts == 0
    asyncio.run(main())


def test_a_mid_turn_send_never_interrupts_or_stops_a_task():
    async def main():
        sess = _listened_turn()
        await _tasks(sess)
        assert await sess.inject("and also this", "c1") is True
        assert sess._client.interrupts == 0
        assert sess._client.stopped == []
        assert sess._client.queries == ["and also this"]
    asyncio.run(main())
