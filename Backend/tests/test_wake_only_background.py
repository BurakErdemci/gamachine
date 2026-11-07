"""Only background work the main agent started wakes the chat when it ends: a
foreground tool call and a subagent's own internal task do not (owner report,
8 Nov 2026). Every client is a fake; no CLI starts."""
import asyncio

import pytest

import providers.claude_sdk_session as cs
from agentic import turn_events, wake_queue

CID = 8124


@pytest.fixture(autouse=True)
def _clean_state():
    wake_queue.reset_all()
    turn_events.RING.reset()
    yield
    wake_queue.reset_all()
    turn_events.RING.reset()


def _session():
    sess = cs.ClaudeSDKSession(conversation_id=CID)
    sess._started = True
    return sess


def _started(task_id, task_type, **raw):
    from claude_agent_sdk import TaskStartedMessage
    data = {"task_id": task_id, "description": task_id, **raw}
    return TaskStartedMessage(subtype="task_started", data=data, task_id=task_id, description=task_id,
                              uuid="u-" + task_id, session_id="s", task_type=task_type)


def _done(task_id):
    from claude_agent_sdk import TaskNotificationMessage
    data = {"task_id": task_id, "status": "completed", "summary": "ok"}
    return TaskNotificationMessage(subtype="task_notification", data=data, task_id=task_id,
                                   status="completed", output_file="", summary="ok",
                                   uuid="n-" + task_id, session_id="s")


async def _run(*tasks):
    """Start and end each (id, type, raw) task inside one running turn."""
    sess = _session()
    sess._turn_active = True
    for tid, ttype, raw in tasks:
        await sess._on_message(_started(tid, ttype, **raw))
        await sess._on_message(_done(tid))
    return sess


def _wake_names(sess):
    """What the turn-end wake would name: the queued wake, drained."""
    sess._queue_wake("tasks_done")
    return wake_queue.drain(CID)


def test_foreground_bash_queues_no_wake():
    sess = asyncio.run(_run(("fg", "local_bash", {"is_backgrounded": False})))
    assert sess._done_tasks == []
    assert not sess._active_tasks and sess._ended_tasks["fg"] == "fg"
    assert wake_queue.pending(CID) == 0


def test_subagent_owned_task_queues_no_wake():
    sess = asyncio.run(_run(("inner", "local_bash", {"is_backgrounded": True, "owned_by_subagent": True})))
    assert sess._done_tasks == []
    assert not sess._active_tasks and sess._ended_tasks["inner"] == "inner"


def test_background_bash_and_background_subagent_still_wake_with_their_names():
    sess = asyncio.run(_run(("bash1", "local_bash", {"is_backgrounded": True}),
                            ("agent1", "local_agent", {})))
    assert sess._done_tasks == ["bash1", "agent1"]
    assert _wake_names(sess) == ["tasks_done|bash1, agent1"]


def test_a_mix_names_only_the_background_tasks():
    sess = asyncio.run(_run(("fg", "local_bash", {"is_backgrounded": False}),
                            ("bg", "local_bash", {"is_backgrounded": True}),
                            ("inner", "local_bash", {"is_backgrounded": True, "owned_by_subagent": True}),
                            ("agent1", "local_agent", {})))
    assert sess._done_tasks == ["bg", "agent1"]
    assert sess._drain_state == "after_turn"
    assert _wake_names(sess) == ["tasks_done|bg, agent1"]


def test_only_foreground_and_owned_tasks_schedule_no_drain():
    sess = asyncio.run(_run(("fg", "local_bash", {"is_backgrounded": False}),
                            ("inner", "local_bash", {"owned_by_subagent": True})))
    assert sess._drain_state is None
