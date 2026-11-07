"""API-key loop session basics: streaming, history, context, compaction, cost.

Fake SDK clients only: nothing here reaches a provider. The fakes copy the
installed SDKs' stream shapes (anthropic 1.11 raw events, openai 3.3 chunks and
Responses events, google-genai 2.27 chunks); that the live APIs still send
these shapes is a separate, live check.
"""
import asyncio
import json
import time
from types import SimpleNamespace as NS
from unittest import mock

import pytest
from google.genai import types as gtypes

import agentic.agent_runner as ar
from agentic import api_compaction
from providers import model_catalog


READ_FILE = {"name": "read_file", "description": "Read a file",
             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}


class Stream:
    """Async iterator over prepared events; `gate` pauses after the first one."""

    def __init__(self, events, gate=None, hang=False):
        self.events, self.gate, self.hang = list(events), gate, hang
        self.closed = self.cancelled = False

    def __aiter__(self):
        return self._run()

    async def _run(self):
        try:
            for index, event in enumerate(self.events):
                yield event
                if index == 0 and self.gate is not None:
                    await asyncio.wait_for(self.gate.wait(), 5)
            if self.hang:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def close(self):
        self.closed = True


class Client:
    """One object standing in for all three SDK clients."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = NS(create=self.create)
        self.chat = NS(completions=NS(create=self.create))
        self.responses = NS(create=self.create)
        self.models = NS(generate_content=self.blocking)
        self.aio = NS(models=NS(generate_content_stream=self.create))

    def next(self, kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        return reply() if callable(reply) else reply

    async def create(self, **kwargs):
        return self.next(kwargs)

    def blocking(self, **kwargs):
        return self.next(kwargs)


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(ar, "get_gemini_tool_declarations",
                        lambda: [{"function_declarations": [READ_FILE]}])
    monkeypatch.setattr(ar, "get_openai_tool_declarations",
                        lambda: [{"type": "function", "function": READ_FILE}])
    monkeypatch.setattr(ar, "_all_tool_definitions", lambda: [READ_FILE])
    monkeypatch.setattr(ar.AgentRunner, "_get_architect_wisdom", lambda self: "")

    def go(provider, replies, model="m", on_event=None, results=None, **runner_kw):
        client = Client(replies)
        monkeypatch.setattr(ar.genai, "Client", lambda **kw: client)
        monkeypatch.setattr(ar.anthropic, "AsyncAnthropic", lambda **kw: client)
        monkeypatch.setattr(ar.openai, "AsyncOpenAI", lambda **kw: client)
        runner = ar.AgentRunner(provider, "k", model, None, **runner_kw)
        executed = []

        async def execute(name, args):
            executed.append((name, args))
            return (results or {}).get(name, {"success": True, "summary": "ok"}), []
        monkeypatch.setattr(runner, "_execute_tool_with_approval", execute)

        async def collect():
            events = []
            async for event in runner._run_inner("hello"):
                events.append(event)
                if on_event:
                    on_event(event)
            return events
        with mock.patch.object(ar.asyncio, "sleep", mock.AsyncMock()):
            events = asyncio.run(collect())
        return events, client, executed, runner
    return go


# ── fake stream builders, one per SDK ────────────────────────────────────────

def anthropic_stream(texts=(), tool=None, gate=None, usage=(10, 7, 5, 2), stop=None):
    events = [NS(type="message_start", message=NS(usage=NS(
        input_tokens=usage[0], output_tokens=1, cache_read_input_tokens=usage[2],
        cache_creation_input_tokens=usage[3])))]
    if texts:
        events.append(NS(type="content_block_start", index=0, content_block=NS(type="text", text="")))
        events += [NS(type="content_block_delta", index=0, delta=NS(type="text_delta", text=t))
                   for t in texts]
        events.append(NS(type="content_block_stop", index=0))
    if tool:
        events.append(NS(type="content_block_start", index=1, content_block=NS(
            type="tool_use", id="t1", name=tool[0], input={})))
        args = json.dumps(tool[1])
        events += [NS(type="content_block_delta", index=1,
                      delta=NS(type="input_json_delta", partial_json=args[:5])),
                   NS(type="content_block_delta", index=1,
                      delta=NS(type="input_json_delta", partial_json=args[5:])),
                   NS(type="content_block_stop", index=1)]
    events.append(NS(type="message_delta", delta=NS(stop_reason=stop or ("tool_use" if tool else "end_turn")),
                     usage=NS(output_tokens=usage[1])))
    if gate is not None:
        # events[2] is the first text delta.
        return _gated(events, 2, gate)
    return lambda: Stream(events)


def _gated(events, after, gate):
    class Gated(Stream):
        async def _run(self):
            for index, event in enumerate(self.events):
                yield event
                if index == after:
                    await asyncio.wait_for(gate.wait(), 5)
    return lambda: Gated(events)


def openai_stream(texts=(), tool=None, gate=None, usage=(10, 3, 4)):
    events = [NS(choices=[NS(delta=NS(content=t, tool_calls=None), finish_reason=None)], usage=None)
              for t in texts]
    if tool:
        args = json.dumps(tool[1])
        events += [
            NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(index=0, id="c1", function=NS(
                name=tool[0], arguments=args[:4]))]), finish_reason=None)], usage=None),
            NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(index=0, id=None, function=NS(
                name=None, arguments=args[4:]))]), finish_reason=None)], usage=None),
        ]
    events.append(NS(choices=[NS(delta=NS(content=None, tool_calls=None),
                                 finish_reason="tool_calls" if tool else "stop")], usage=None))
    events.append(NS(choices=[], usage=NS(prompt_tokens=usage[0], completion_tokens=usage[1],
                                          prompt_tokens_details=NS(cached_tokens=usage[2]))))
    if gate is not None:
        return _gated(events, 0, gate)
    return lambda: Stream(events)


def gemini_stream(texts=(), tool=None, gate=None, hang=False):
    def chunk(part, usage=None, reason=None):
        return NS(candidates=[NS(content=NS(parts=[part]), finish_reason=reason)], usage_metadata=usage)
    events = [chunk(gtypes.Part(text=t)) for t in texts]
    if tool:
        events.append(chunk(gtypes.Part(function_call=gtypes.FunctionCall(
            id="g1", name=tool[0], args=tool[1]))))
    events.append(NS(candidates=[], usage_metadata=NS(
        prompt_token_count=12, candidates_token_count=4, cached_content_token_count=2)))
    if gate is not None:
        return _gated(events, 0, gate)
    if hang:
        stream = Stream(events[:1], hang=True)
        return stream
    return lambda: Stream(events)


STREAMS = {"anthropic": anthropic_stream, "openai": openai_stream, "google": gemini_stream}


# ── 1. streaming ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
def test_text_streams_as_deltas_in_order_before_the_response(run, provider):
    """The second delta is held back until the first `text` event has reached the
    consumer: the reply cannot pass by being sent whole after the request."""
    gate = asyncio.Event()

    def on_event(event):
        if event.type == "text":
            gate.set()
    events, _, _, _ = run(provider, [STREAMS[provider](["Hello ", "world"], gate=gate)],
                          on_event=on_event)
    kinds = [e.type for e in events]
    assert [e.data["content"] for e in events if e.type == "text"] == ["Hello ", "world"]
    assert [e.data["content"] for e in events if e.type == "response"] == ["Hello world"]
    assert kinds.index("text") < kinds.index("turn_usage") < kinds.index("response")
    assert kinds[-1] == "done"


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
def test_a_tool_call_is_assembled_from_streamed_deltas(run, provider):
    events, client, executed, _ = run(provider, [
        STREAMS[provider](["Looking."], tool=("read_file", {"path": "Assets/A.cs"})),
        STREAMS[provider](["Done."])])
    assert executed == [("read_file", {"path": "Assets/A.cs"})]
    assert [e.data["content"] for e in events if e.type == "text"] == ["Looking.", "Done."]
    assert [e.data["content"] for e in events if e.type == "response"] == ["Done."]
    assert len(client.requests) == 2
    if provider == "anthropic":
        sent = client.requests[1]["messages"]
        assert sent[-2]["content"][-1] == {"type": "tool_use", "id": "t1", "name": "read_file",
                                           "input": {"path": "Assets/A.cs"}}
        assert sent[-1]["content"][0]["tool_use_id"] == "t1"
        assert client.requests[0]["stream"] is True
    elif provider == "openai":
        sent = client.requests[1]["messages"]
        assert sent[-2]["tool_calls"][0]["function"] == {
            "name": "read_file", "arguments": json.dumps({"path": "Assets/A.cs"})}
        assert sent[-1]["tool_call_id"] == "c1"
        assert client.requests[0]["stream_options"] == {"include_usage": True}
    else:
        model_turn = client.requests[1]["contents"][-2]
        assert model_turn.role == "model"
        assert [p.text for p in model_turn.parts if p.text] == ["Looking."]
        assert client.requests[1]["contents"][-1].parts[0].function_response.id == "g1"


def test_stopping_a_gemini_turn_cancels_the_stream_promptly(monkeypatch):
    monkeypatch.setattr(ar, "get_gemini_tool_declarations", lambda: [{"function_declarations": []}])
    monkeypatch.setattr(ar, "_all_tool_definitions", lambda: [])
    monkeypatch.setattr(ar.AgentRunner, "_get_architect_wisdom", lambda self: "")
    stream = gemini_stream(["partial "], hang=True)
    client = Client([stream])
    monkeypatch.setattr(ar.genai, "Client", lambda **kw: client)
    runner = ar.AgentRunner("google", "k", "gemini-3.8-flash", None)

    async def scenario():
        gen = runner._run_inner("hello")
        seen = []
        while True:
            event = await asyncio.wait_for(gen.__anext__(), 5)
            seen.append(event)
            if event.type == "text":
                break
        started = time.monotonic()
        await asyncio.wait_for(gen.aclose(), 5)
        return seen, time.monotonic() - started
    seen, took = asyncio.run(scenario())
    assert seen[-1].data["content"] == "partial "
    assert took < 2
    assert stream.cancelled and stream.closed


# ── 2. history as messages, caching ──────────────────────────────────────────

HISTORY = [{"role": "user", "text": "first question", "id": 11},
           {"role": "assistant", "text": "first answer", "id": 12}]


def anthropic_reply(text="ok", usage=(5, 2)):
    return NS(content=[NS(type="text", text=text)], stop_reason="end_turn",
              usage=NS(input_tokens=usage[0], output_tokens=usage[1]))


def openai_reply(text="ok"):
    return NS(choices=[NS(message=NS(content=text, tool_calls=None), finish_reason="stop")],
              usage=NS(prompt_tokens=5, completion_tokens=2))


REPLIES = {"anthropic": anthropic_reply, "openai": openai_reply}


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
def test_history_goes_as_provider_messages_not_a_text_block(run, monkeypatch, provider):
    reply = gemini_stream(["ok"]) if provider == "google" else REPLIES[provider]()
    _, client, _, _ = run(provider, [reply], context="BIG-CONTEXT " * 3000, api_history=HISTORY)
    request = client.requests[0]
    if provider == "anthropic":
        system = "".join(b["text"] for b in request["system"])
        roles = [(m["role"], m["content"] if isinstance(m["content"], str) else m["content"][0]["text"])
                 for m in request["messages"]]
    elif provider == "openai":
        system = request["messages"][0]["content"]
        roles = [(m["role"], m["content"] if isinstance(m["content"], str) else m["content"][0]["text"])
                 for m in request["messages"][1:]]
    else:
        system = request["config"].system_instruction
        roles = [({"model": "assistant"}.get(c.role, c.role), c.parts[0].text)
                 for c in request["contents"]]
    assert roles == [("user", "first question"), ("assistant", "first answer"), ("user", "hello")]
    assert "BIG-CONTEXT" not in system


def test_anthropic_marks_system_tools_and_the_last_message_for_caching(run):
    _, client, _, _ = run("anthropic", [anthropic_reply()], api_history=HISTORY)
    request = client.requests[0]
    assert request["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert request["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    messages = request["messages"]
    assert messages[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all(isinstance(m["content"], str) or "cache_control" not in m["content"][-1]
               for m in messages[:-1])


def test_stored_rows_become_alternating_turns_without_the_current_input():
    # The compaction summary row is left out: the same text is the stored memory.
    rows = [{"id": 0, "role": "assistant", "content": api_compaction.SUMMARY_ROW_PREFIX + "\n\nold"},
            {"id": 1, "role": "user", "content": "a"},
            {"id": 2, "role": "system", "content": "wake notice"},
            {"id": 3, "role": "user", "content": "b"},
            {"id": 4, "role": "assistant", "content": "c"},
            {"id": 5, "role": "user", "content": "current"}]
    assert api_compaction.history_turns(rows) == [
        {"role": "user", "text": "a\n\nb", "id": 3}, {"role": "assistant", "text": "c", "id": 4}]


# ── 3. context reading and the endpoints ─────────────────────────────────────

def test_the_context_endpoints_report_measured_numbers_for_an_api_chat(run):
    from routes.conversation_routes import create_conversation_router
    run("anthropic", [anthropic_stream(["hi"], usage=(1000, 500, 3000, 200))],
        model="claude-opus-5-5", conversation_id=4242)
    db = mock.MagicMock()
    db.get_ai_config.return_value = ("anthropic", "claude-opus-5-5", "", False)
    db.get_conversation_messages.return_value = [{"id": 1, "role": "user", "content": "hi"}]
    router = create_conversation_router(db, mock.MagicMock())

    def endpoint(path):
        return next(r for r in router.routes if getattr(r, "path", "") == path).endpoint
    with mock.patch("routes.conversation_routes.require_conversation_owner", return_value=(1, None)):
        gauge = asyncio.run(endpoint("/conversations/{conv_id}/context-usage")(
            conv_id=4242, x_session_token="t"))
        report = asyncio.run(endpoint("/session-report/{conv_id}/{kind}")(
            conv_id=4242, kind="context", x_session_token="t"))
    assert gauge["estimated"] is False
    assert gauge["real"] == {"used": "4.7k", "total": "1M", "model": "claude-opus-5-5"}
    assert report["status"] == "ok" and "4.7k / 1M" in report["text"]


# ── 4. auto-compaction ───────────────────────────────────────────────────────

def test_auto_compaction_above_85_percent_keeps_the_last_turns(run, monkeypatch):
    prompts, stored = [], []

    async def summarize(provider, key, model, prompt):
        prompts.append(prompt)
        return "SUMMARY OF OLD TURNS"
    monkeypatch.setattr(api_compaction, "summarize", summarize)
    history = []
    for n in range(6):
        history += [{"role": "user", "text": f"question {n}", "id": 2 * n + 1},
                    {"role": "assistant", "text": f"answer {n}", "id": 2 * n + 2}]
    window = model_catalog.context_window("anthropic", "claude-haiku-4-5")
    near_full = int(window * 0.9)
    replies = [anthropic_stream(["x"], tool=("read_file", {"path": "a"}), usage=(near_full, 10, 0, 0)),
               anthropic_stream(["done"], usage=(1000, 10, 0, 0))]

    def go():
        events, client, _, runner = run("anthropic", replies, model="claude-haiku-4-5",
                                        api_history=history, conversation_id=77)
        return events, client
    original = ar.AgentRunner.__init__

    def init(self, *a, **kw):
        original(self, *a, **kw)
        self.compaction_store = lambda summary, through: stored.append((summary, through))
    monkeypatch.setattr(ar.AgentRunner, "__init__", init)
    events, client = go()
    assert len(client.requests) == 2
    first, second = client.requests[0]["messages"], client.requests[1]["messages"]
    assert len(first) == 13 and len(prompts) == 1
    assert "question 0" in prompts[0] and "answer 3" in prompts[0]
    assert second[0]["content"] == api_compaction.summary_message("SUMMARY OF OLD TURNS")
    kept = [m["content"] if isinstance(m["content"], str) else m["content"][0].get("text")
            for m in second[1:]]
    assert kept[:3] == ["answer 4", "question 5", "answer 5"]
    assert stored == [("SUMMARY OF OLD TURNS", 9)]
    assert events[-1].type == "done"


def test_no_compaction_below_the_threshold(run, monkeypatch):
    called = []

    async def summarize(*a):
        called.append(a)
        return "S"
    monkeypatch.setattr(api_compaction, "summarize", summarize)
    window = model_catalog.context_window("anthropic", "claude-haiku-4-5")
    run("anthropic", [anthropic_stream(["x"], tool=("read_file", {}), usage=(int(window * 0.5), 10, 0, 0)),
                      anthropic_stream(["done"])],
        model="claude-haiku-4-5", api_history=HISTORY * 4)
    assert called == []


# ── 5. usage and cost ────────────────────────────────────────────────────────

def test_cost_comes_from_the_price_table_and_is_none_when_unknown(run):
    events, _, _, _ = run("anthropic", [anthropic_stream(["hi"], usage=(1_000_000, 1_000_000, 0, 0))],
                          model="claude-opus-5-5")
    usage = next(e.data for e in events if e.type == "turn_usage")
    assert usage["cost_usd"] == pytest.approx(24.0)  # $4 in + $20 out per MTok
    assert usage["input_tokens"] == 1_000_000 and usage["output_tokens"] == 1_000_000
    assert model_catalog.turn_cost_usd("anthropic", "claude-opus-5-5", 0, 0, 1_000_000, 1_000_000) \
        == pytest.approx(0.20 + 5.0)
    events, _, _, _ = run("openai", [openai_stream(["hi"])], model="some-unpriced-model")
    usage = next(e.data for e in events if e.type == "turn_usage")
    assert usage["cost_usd"] is None
    assert usage["input_tokens"] == 10 and usage["cache_read_input_tokens"] == 4


# ── 6. tool output cap ───────────────────────────────────────────────────────

def test_tool_output_is_cut_at_30000_chars_with_an_english_note(run):
    big = {"success": True, "content": "y" * 50_000}
    _, client, _, _ = run("anthropic", [anthropic_reply_tool(), anthropic_reply()],
                          results={"read_file": big})
    sent = client.requests[1]["messages"][-1]["content"][0]["content"]
    full = json.dumps(big)
    assert sent == full[:30_000] + f"… [truncated {len(full) - 30_000} chars]"
    assert "kısaltıldı" not in sent


def anthropic_reply_tool():
    return NS(content=[NS(type="tool_use", id="t9", name="read_file", input={})],
              stop_reason="tool_use", usage=NS(input_tokens=1, output_tokens=1))


# ── 7. system instruction ────────────────────────────────────────────────────

@pytest.mark.parametrize("language,fallback", [("tr", "Turkish"), ("en", "English")])
def test_the_system_instruction_is_english_and_follows_the_users_language(run, language, fallback):
    _, client, _, _ = run("anthropic", [anthropic_reply()], language=language)
    system = client.requests[0]["system"][0]["text"]
    assert system.startswith(ar.SYSTEM_PROMPT)
    assert ("Reply in the language of the user's latest message; when it cannot be told, "
            f"reply in {fallback}.") in system
    for turkish in ("Sen Unity", "HAYATİ KURALLAR", "[DİL]", "[BAĞLAM]", "MODEL KİMLİĞİN"):
        assert turkish not in system


# ── 8. web search ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,kind", [("claude-opus-5-5", "web_search_20260209"),
                                        ("claude-haiku-4-5", "web_search_20250305")])
def test_anthropic_offers_its_server_web_search_tool(run, model, kind):
    _, client, _, _ = run("anthropic", [anthropic_reply()], model=model)
    tools = client.requests[0]["tools"]
    assert {"type": kind, "name": "web_search", "max_uses": 5}.items() <= tools[0].items()
    assert [t["name"] for t in tools[1:]] == ["read_file"]


def test_gemini_3_offers_google_search_next_to_function_tools(run):
    _, client, _, _ = run("google", [gemini_stream(["hi"])], model="gemini-3.8-flash")
    config = client.requests[0]["config"]
    assert config.tools[0].function_declarations[0].name == "read_file"
    assert config.tools[1].google_search is not None
    assert config.tool_config.include_server_side_tool_invocations is True


def test_a_read_only_runner_is_offered_no_provider_web_search(run):
    # Read-only side chats get no network tool (web_fetch is held back too).
    _, client, _, _ = run("anthropic", [anthropic_reply()], read_only=True)
    assert all(t.get("name") != "web_search" for t in client.requests[0]["tools"])
    _, client, _, _ = run("google", [gemini_stream(["hi"])], model="gemini-3.8-flash",
                          read_only=True)
    config = client.requests[0]["config"]
    assert all(getattr(t, "google_search", None) is None for t in config.tools)
    assert config.tool_config is None


# ── 9. GPT-6 through the Responses API ───────────────────────────────────────

def responses_stream(texts=(), call=None):
    output = []
    if texts:
        output.append(NS(type="message", role="assistant", id="m1", status="completed",
                         content=[NS(type="output_text", text="".join(texts), annotations=[])]))
    if call:
        output.append(NS(type="function_call", id="fc1", call_id="call_1", name=call[0],
                         arguments=json.dumps(call[1]), status="completed"))
    events = [NS(type="response.output_text.delta", delta=t) for t in texts]
    events.append(NS(type="response.completed", response=NS(
        status="completed", output=output, incomplete_details=None,
        usage=NS(input_tokens=20, output_tokens=5, input_tokens_details=NS(cached_tokens=8)))))
    return lambda: Stream(events)


def test_a_gpt_6_model_runs_on_the_responses_api_with_tools(run):
    events, client, executed, _ = run("openai", [
        responses_stream(["Checking."], call=("read_file", {"path": "x"})),
        responses_stream(["All good."])], model="gpt-6-sol")
    assert not any(e.type == "error" for e in events)
    assert executed == [("read_file", {"path": "x"})]
    first, second = client.requests
    assert first["stream"] is True and first["store"] is False
    assert first["tools"] == [{"type": "function", "name": "read_file", "description": "Read a file",
                               "parameters": READ_FILE["parameters"], "strict": False}]
    assert "instructions" in first and "messages" not in first
    assert second["input"][-1] == {"type": "function_call_output", "call_id": "call_1",
                                   "output": json.dumps({"success": True, "summary": "ok"})}
    assert [e.data["content"] for e in events if e.type == "text"] == ["Checking.", "All good."]
    assert [e.data["content"] for e in events if e.type == "response"] == ["All good."]
