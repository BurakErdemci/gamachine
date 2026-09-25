"""max_tokens of the Anthropic agent loop, and what a `max_tokens` stop does.

Per the claude-api skill, Fable/Mythos, Opus 5, Opus 5.5 and Sonnet 5 think by
default and thinking tokens count toward max_tokens, so the loop's old flat 4096
could be spent thinking. A `max_tokens` stop also used to pass as a normal
finish. No provider is contacted: the client is a fake.
"""
import asyncio
import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import agentic.agent_runner as ar


class _FakeAnthropic:
    def __init__(self, responses):
        self.requests = []
        self._responses = list(responses)
        self.messages = types.SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


def _text(text, stop_reason="end_turn"):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
        stop_reason=stop_reason,
    )


def _tool_call(stop_reason):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="tool_use", id="t0", name="read_file",
                                       input={"file_path": "A.c"})],
        usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
        stop_reason=stop_reason,
    )


def _run(model_name, client, tool_fn=None):
    runner = ar.AgentRunner(provider_type="anthropic", api_key="k",
                            model_name=model_name, workspace_path=".")
    tool_fn = tool_fn or (lambda name, args, workspace, conversation_id:
                          {"success": True, "summary": "ok"})
    patches = [
        mock.patch.object(ar, "execute_tool", tool_fn),
        mock.patch.object(ar, "_all_tool_definitions",
                          lambda: [{"name": "read_file", "description": "d",
                                    "parameters": {"type": "object", "properties": {}}}]),
        mock.patch.object(ar.anthropic, "AsyncAnthropic", lambda **kw: client),
    ]

    async def _go():
        return [e async for e in runner._run_inner("merhaba")]

    for p in patches:
        p.start()
    try:
        return asyncio.run(_go())
    finally:
        for p in reversed(patches):
            p.stop()


@pytest.mark.parametrize("model, expected", [
    # Thinking on by default (skill "Thinking & Effort" table): room for thinking.
    ("claude-fable-5-1", 16000),
    ("claude-fable-5", 16000),
    ("claude-mythos-5-1", 16000),
    ("claude-opus-5-5", 16000),
    ("claude-opus-5", 16000),
    ("claude-sonnet-5", 16000),
    ("claude-some-future-model", 16000),
    # No thinking unless requested, and this loop does not request it.
    ("claude-opus-4-8", 4096),
    ("claude-opus-4-7", 4096),
    ("claude-opus-4-6", 4096),
    ("claude-sonnet-4-6", 4096),
    ("claude-haiku-4-5-20251001", 4096),
])
def test_request_max_tokens_per_family(model, expected):
    client = _FakeAnthropic([_text("bitti")])

    events = _run(model, client)

    assert len(client.requests) == 1
    kwargs = client.requests[0]
    assert kwargs["model"] == model
    assert kwargs["max_tokens"] == expected
    # The loop must not start sending thinking/sampling params as a side effect.
    for absent in ("thinking", "temperature", "top_p", "top_k"):
        assert absent not in kwargs
    done = [e for e in events if e.type == "done"]
    assert [d.data["stop_reason"] for d in done] == ["complete"]


def test_thinking_cap_stays_under_the_sdk_non_streaming_guard():
    # anthropic SDK 1.x raises for a non-streaming request whose expected time
    # (3600 s * max_tokens / 128000) exceeds 600 s.
    assert 3600 * ar.ANTHROPIC_LOOP_MAX_TOKENS_THINKING / 128_000 <= 600


def test_text_cut_at_max_tokens_is_reported_not_passed_as_finished():
    client = _FakeAnthropic([_text("yarım kalan cev", stop_reason="max_tokens")])

    events = _run("claude-opus-5", client)

    done = [e for e in events if e.type == "done"]
    assert len(done) == 1
    assert done[0].data["stop_reason"] == "max_tokens"
    assert done[0].data["stop_message"] == ar._STOP_TEXTS["max_tokens"]
    assert not [e for e in events if e.type == "error"]
    # The partial answer is still delivered, so the stored turn is not empty.
    assert [e.data["content"] for e in events if e.type == "response"] == ["yarım kalan cev"]


def test_tool_call_cut_at_max_tokens_is_not_executed():
    client = _FakeAnthropic([_tool_call(stop_reason="max_tokens")])
    tool_fn = mock.Mock(return_value={"success": True, "summary": "ok"})

    events = _run("claude-opus-5", client, tool_fn=tool_fn)

    tool_fn.assert_not_called()
    assert len(client.requests) == 1
    assert [e.data["stop_reason"] for e in events if e.type == "done"] == ["max_tokens"]


def test_tool_use_stop_still_runs_the_tool():
    client = _FakeAnthropic([_tool_call(stop_reason="tool_use"), _text("bitti")])
    tool_fn = mock.Mock(return_value={"success": True, "summary": "ok"})

    events = _run("claude-opus-5", client, tool_fn=tool_fn)

    assert tool_fn.call_count == 1
    assert len(client.requests) == 2
    assert [e.data["stop_reason"] for e in events if e.type == "done"] == ["complete"]
