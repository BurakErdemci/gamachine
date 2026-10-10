"""A background-task wake turn runs on a plain sentence, not the raw
`tasks_done|...` notice, and the stored system row says the same."""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from agentic import agent_runner as ar
from agentic import mailbox, wake_queue
from routes.conversation_routes import create_conversation_router
from schemas import ChatRequest

_HEAD = "[Sistem] Sen yokken başlattığın arka plan işleri bitti: "
_TAIL = (". Bu mesajı kullanıcı yazmadı. Sonuçlarına bakıp kaldığın yerden devam et "
         "ve işi sonuçlandır.")


@pytest.fixture(autouse=True)
def _clean_queue():
    wake_queue.reset_all()
    yield
    wake_queue.reset_all()


def test_one_name():
    assert wake_queue.notice_turn_text(["tasks_done|build"]) == _HEAD + "build" + _TAIL


def test_two_names_in_order():
    text = wake_queue.notice_turn_text(["tasks_done|a, b", "tasks_done|c"])
    assert text == _HEAD + "a, b; c" + _TAIL


def test_duplicate_names_collapse():
    text = wake_queue.notice_turn_text(["tasks_done|build", "tasks_done|build"])
    assert text == _HEAD + "build" + _TAIL


def test_saved_suffix():
    text = wake_queue.notice_turn_text(["tasks_done_saved|build"])
    assert text == _HEAD + "build" + _TAIL + " Son yanıtın sohbete kaydedildi."


def test_notice_without_separator_is_its_own_name():
    assert wake_queue.notice_turn_text(["plain text"]) == _HEAD + "plain text" + _TAIL


def test_mail_notices_are_ignored():
    mail = mailbox.notice(3)
    assert wake_queue.notice_turn_text([mail]) == ""
    assert wake_queue.notice_turn_text([mail, "tasks_done|x"]) == _HEAD + "x" + _TAIL


def test_empty_gives_empty_string():
    assert wake_queue.notice_turn_text([]) == ""


class _FakeRunner:
    messages = []

    def __init__(self, **kw):
        pass

    async def run(self, message):
        _FakeRunner.messages.append(message)
        yield ar.AgentEvent("response", {"content": "ok"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})


def _db():
    db = MagicMock()
    db.get_conversation_owner.return_value = 1
    db.get_ai_config.return_value = ("subscription", "claude-opus-5", "", False)
    db.get_api_key.return_value = ""
    db.get_last_workspace.return_value = ""
    db.get_memory.return_value = ""
    db.get_conversation_messages.return_value = []
    db.get_cli_session.return_value = None
    return db


def test_ticketed_wake_runs_and_stores_the_sentence():
    _FakeRunner.messages = []
    db = _db()
    wake_queue.issue_ticket(1, ["tasks_done|bot run"])
    router = create_conversation_router(db, MagicMock())
    route = next(r for r in router.routes if getattr(r, "path", "") == "/chat-stream")
    req = ChatRequest(conversation_id=1, message="tasks_done|bot run", user_id=1, origin="wake")

    async def go():
        resp = await route.endpoint(request=req, x_session_token="t")
        async for _ in resp.body_iterator:
            pass

    with patch("routes.conversation_routes.AgentRunner", _FakeRunner):
        asyncio.run(go())
    expected = wake_queue.notice_turn_text(["tasks_done|bot run"])
    assert "bot run" in expected
    assert _FakeRunner.messages == [expected]
    assert "tasks_done|" not in _FakeRunner.messages[0]
    system_rows = [c.args for c in db.add_message.call_args_list if c.args[1] == "system"]
    assert system_rows and system_rows[0][2] == expected
