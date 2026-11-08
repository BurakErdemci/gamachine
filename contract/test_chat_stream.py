"""Promises about a chat turn, with the model provider faked at its HTTP boundary."""
import time

import pytest

import sse

REPLY = "Hello from the contract fake."


def _text(events):
    return "".join(e.get("content", "") for e in events if e.get("type") == "text")


def test_a_chat_turn_streams_the_reply_and_ends_with_one_done(api, golden):
    """POST /chat-stream streams the model's text and ends with exactly one `done`; the stream matches its golden file."""
    conv_id = api.new_conversation(model="contract-text")
    events = api.chat(conv_id, "Say hello.")
    assert sse.terminal_problems(events) == []
    assert _text(events) == REPLY
    done = next(e for e in events if e["type"] == "done")
    assert done["stop_reason"] == "complete"
    assert all(e.get("conversation_id") == conv_id for e in events)
    golden("chat_text_turn", events)


def test_a_chat_turn_stores_the_user_message_and_the_reply(api):
    """After a finished turn, GET /conversations/{id}/messages holds the user's message and the reply, in order."""
    conv_id = api.new_conversation(model="contract-text")
    api.chat(conv_id, "Remember this message.")
    messages = [(m["role"], m["content"]) for m in api.ok("GET", f"/conversations/{conv_id}/messages")]
    assert messages[-2:] == [("user", "Remember this message."), ("assistant", REPLY)]


def test_the_model_receives_the_user_message_and_the_saved_key(api, fake_openai):
    """The provider request carries the user's message and the chat's saved API key."""
    conv_id = api.new_conversation(model="contract-text")
    api.chat(conv_id, "A message for the model.")
    request = fake_openai.requests[-1]
    assert request["authorization"] == "Bearer sk-contract-fake-key"
    users = [m.get("content") for m in request["body"]["messages"] if m.get("role") == "user"]
    assert any("A message for the model." in str(content) for content in users)


def test_a_provider_error_ends_the_stream_with_one_error_and_no_done(api, golden):
    """A model API that refuses the key ends the turn with exactly one `error` event, no `done`, and no traceback."""
    conv_id = api.new_conversation(model="contract-unauthorized")
    events = api.chat(conv_id, "This will fail.")
    assert sse.terminal_problems(events) == []
    types = [e["type"] for e in events]
    assert "done" not in types and types.count("error") == 1
    error = next(e for e in events if e["type"] == "error")
    assert error["message"] and "Traceback" not in error["message"]
    golden("chat_provider_error", events)


def test_stopping_a_running_turn_ends_it_once_as_stopped(api, fake_openai):
    """Stop (POST /chat-stop, then the client hangs up) ends the running turn with one `turn_end` "stopped" and cancels the model request."""
    conv_id = api.new_conversation(model="contract-stall")

    def stop_on_first_text(event, response):
        if event.get("type") == "text":
            api.ok("POST", f"/chat-stop/{conv_id}")
            return True
        return False

    events = api.chat(conv_id, "Write a long answer.", on_event=stop_on_first_text)
    assert [e["type"] for e in events if e["type"] in sse.TERMINAL] == []  # the client left first
    assert fake_openai.hung_up.wait(10), "the model request kept running after Stop"
    deadline = time.monotonic() + 10
    while True:
        turn = api.ok("GET", f"/conversations/{conv_id}/turn-events")["events"]
        ends = [e for e in turn if e["kind"] == "turn_end"]
        if ends or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert [e["status"] for e in ends] == ["stopped"], turn
    assert turn[-1]["kind"] == "turn_end", "events after the end of the turn"


@pytest.mark.xfail(strict=True, reason=(
    "Known product gap (8 Nov 2026): stop_chat cancels CLI sessions only; for an API-provider turn "
    "/chat-stop answers no_session and the turn runs on until the streaming client hangs up, so a "
    "Stop from another client (phone, a followed turn) does not stop it. Remove this mark with the fix."))
def test_stop_ends_an_api_turn_while_the_client_keeps_listening(api, fake_openai):
    """POST /chat-stop alone ends a running turn: the open stream gets one terminal event and the model request is cancelled."""
    conv_id = api.new_conversation(model="contract-stall")
    sent = {}

    def stop_once(event, response):
        if event.get("type") == "text" and not sent:
            sent["answer"] = api.ok("POST", f"/chat-stop/{conv_id}")
        return event.get("type") in sse.TERMINAL

    events = api.chat(conv_id, "Write a long answer.", on_event=stop_once, max_seconds=8)
    assert sent, "the turn never streamed text"
    assert [e["type"] for e in events if e["type"] in sse.TERMINAL], "Stop did not end the open stream"
    assert fake_openai.hung_up.wait(5), "the model request kept running after Stop"
