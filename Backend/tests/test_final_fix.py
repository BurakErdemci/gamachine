"""Final-audit fixes: a CLI switch or a stuck-session retry never closes a
session with running background work, auto-compaction carries the stored
summary, a Gemini search-quota 429 falls back to no search, parallel OpenAI
tool calls stay separate, and an API context reading answers only for its
own provider and model.

Fakes only: no CLI process, no provider request, no network.
"""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest import mock

import pytest
from google.genai import types as gtypes

import agentic.agent_runner as ar
from agentic import api_compaction
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

READ_FILE = {"name": "read_file", "description": "Read a file",
             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}
TRANSCRIPT = "USER: earlier request on the other CLI"


# ── B1. a CLI switch keeps a session with running work ──────────────────────

def _claude_harness(monkeypatch, tasks, cid):
    from agentic import mailbox
    from providers import claude_sdk_session as sdk
    from providers.cli_base import BaseCLIProvider

    messages, closed = [], []

    async def stream(message, **kwargs):
        messages.append(message)
        yield {"type": "done"}

    existing = NS(is_live=True, _active_tasks=dict(tasks), session_id="live-1",
                  model="claude-opus-5-5", effort=None, cwd=".", mcp_servers={},
                  read_only=False, agent_guide="g", _broken=False, stream=stream)

    async def close_session(conversation_id):
        closed.append(conversation_id)

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(ar, "_remove_project_mcp_json", lambda *a: None)
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", lambda args: args)
    monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: NS(returncode=0))
    monkeypatch.setattr(mailbox, "claude_server_entry", lambda c: None)
    monkeypatch.setattr(sdk, "_SESSIONS", {cid: existing})
    monkeypatch.setattr(sdk, "get_session", lambda c, **kw: existing)
    monkeypatch.setattr(sdk, "close_session", close_session)
    monkeypatch.setattr(ar, "_oturum_yeniden_kurma_gerekceleri", lambda *a, **k: [])
    monkeypatch.setattr(ar, "_DEFERRED_REASONS", {})
    # The chat's last turn ran Codex; this one is back on Claude.
    monkeypatch.setattr(ar, "_LAST_SUB_PROVIDER", {cid: "codex"})
    return messages, closed


def _sub_turn(model, cid, text):
    runner = ar.AgentRunner(provider_type="subscription", api_key="", model_name=model,
                            workspace_path=".", conversation_id=cid, context=TRANSCRIPT,
                            generation_mode="step")
    runner._agent_guide = "guide"

    async def go():
        return [e async for e in runner._run_inner(text)]

    return runner, asyncio.run(go())


def test_claude_switch_keeps_busy_session_and_hands_it_the_transcript_once(monkeypatch):
    messages, closed = _claude_harness(monkeypatch, {"t1": "background subagent"}, 991001)
    runner, _ = _sub_turn("claude-opus-5-5", 991001, "hello")
    assert closed == []
    assert TRANSCRIPT in messages[0] and messages[0].startswith("hello")
    assert "kapatılmadı" in runner.session_defer_notice
    _sub_turn("claude-opus-5-5", 991001, "again")
    assert closed == [] and TRANSCRIPT not in messages[1]

    # No running work: the switch closes the session as before.
    messages, closed = _claude_harness(monkeypatch, {}, 991002)
    runner, _ = _sub_turn("claude-opus-5-5", 991002, "hello")
    assert closed == [991002] and runner.session_defer_notice == ""


def test_codex_switch_keeps_busy_session_and_hands_it_the_transcript_once(monkeypatch):
    from computer_use import settings as computer_use_settings
    from browser_use import settings as browser_use_settings
    from connectors import registration as connectors_registration
    from providers import codex_session
    from providers.codex_provider import CodexProvider

    cid, sent, closed = 991003, [], []

    class _Sess:
        thread_id = session_id = "thread-1"
        _ctx_injected = True
        auto_approve = False
        is_live = True
        turn_live = False
        model = "gpt-5.6-codex"
        effort = None
        cwd = "."
        read_only = False
        unity_mcp = computer_use = browser_use = False
        connectors = ((), "")

        def running_work(self):
            return ["cmd-1"]

        def set_turn_overrides(self, **kw):
            pass

        async def stream(self, message, image_paths=None, **kw):
            sent.append(message)
            yield {"type": "done", "session_id": "thread-1"}

    sess = _Sess()

    async def close_session(conversation_id):
        closed.append(conversation_id)

    monkeypatch.setattr(codex_session, "_SESSIONS", {cid: sess})
    monkeypatch.setattr(codex_session, "get_session", lambda *a, **k: sess)
    monkeypatch.setattr(codex_session, "close_session", close_session)
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", lambda self, *a, **k: "")
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(computer_use_settings, "enabled_now", lambda: False)
    monkeypatch.setattr(browser_use_settings, "enabled_now", lambda: False)
    monkeypatch.setattr(connectors_registration, "served", lambda: ((), ""))
    monkeypatch.setattr(ar, "_DEFERRED_REASONS", {})
    monkeypatch.setattr(ar, "_LAST_SUB_PROVIDER", {cid: "claude"})

    runner, _ = _sub_turn("gpt-5.6-codex", cid, "hello")
    assert closed == []
    assert TRANSCRIPT in sent[0]
    assert "kapatılmadı" in runner.session_defer_notice
    _sub_turn("gpt-5.6-codex", cid, "again")
    assert closed == [] and TRANSCRIPT not in sent[1]


# ── B2. the stuck-session retry spares background tasks ─────────────────────

def test_stream_error_with_background_tasks_ends_the_turn_without_a_reset(monkeypatch):
    messages, closed = _claude_harness(monkeypatch, {"t1": "background bash"}, 991004)
    from providers import claude_sdk_session as sdk
    session = sdk._SESSIONS[991004]

    async def broken_stream(message, **kwargs):
        messages.append(message)
        raise RuntimeError("CLI pipe closed")
        yield  # pragma: no cover

    session.stream = broken_stream
    runner = ar.AgentRunner(provider_type="subscription", api_key="",
                            model_name="claude-opus-5-5", workspace_path=".",
                            conversation_id=991004, generation_mode="step")

    async def go():
        return [e async for e in runner._run_claude_session("hi")]

    events = asyncio.run(go())
    assert closed == [] and len(messages) == 1
    assert events[-1].type == "error"
    assert "CLI pipe closed" in events[-1].data["message"]
    assert "yeniden gönder" in events[-1].data["message"]


# ── API loop fakes (the pattern of test_api_loop_core) ──────────────────────

class Stream:
    def __init__(self, events):
        self.events = list(events)

    def __aiter__(self):
        return self._run()

    async def _run(self):
        for event in self.events:
            yield event

    async def close(self):
        pass


class Client:
    def __init__(self, replies, on_request=None):
        self.replies, self.requests, self.on_request = list(replies), [], on_request
        self.messages = NS(create=self.create)
        self.chat = NS(completions=NS(create=self.create))
        self.responses = NS(create=self.create)
        self.models = NS(generate_content=self.create)
        self.aio = NS(models=NS(generate_content_stream=self.create))

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.on_request:
            self.on_request(kwargs)
        reply = self.replies.pop(0)
        return reply() if callable(reply) else reply


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(ar, "get_gemini_tool_declarations",
                        lambda: [{"function_declarations": [READ_FILE]}])
    monkeypatch.setattr(ar, "get_openai_tool_declarations",
                        lambda: [{"type": "function", "function": READ_FILE}])
    monkeypatch.setattr(ar, "_all_tool_definitions", lambda: [READ_FILE])
    monkeypatch.setattr(ar.AgentRunner, "_get_architect_wisdom", lambda self: "")

    def go(provider, replies, model="m", on_request=None, **runner_kw):
        client = Client(replies, on_request)
        monkeypatch.setattr(ar.genai, "Client", lambda **kw: client)
        monkeypatch.setattr(ar.anthropic, "AsyncAnthropic", lambda **kw: client)
        monkeypatch.setattr(ar.openai, "AsyncOpenAI", lambda **kw: client)
        runner = ar.AgentRunner(provider, "k", model, None, **runner_kw)
        executed = []

        async def execute(name, args):
            executed.append((name, args))
            return {"success": True, "summary": "ok"}, []
        monkeypatch.setattr(runner, "_execute_tool_with_approval", execute)

        async def collect():
            return [e async for e in runner._run_inner("hello")]
        sleep = mock.AsyncMock()
        with mock.patch.object(ar.asyncio, "sleep", sleep):
            events = asyncio.run(collect())
        return events, executed, sleep
    return go


# ── B3. auto-compaction carries the stored summary ──────────────────────────

def test_auto_compaction_prompt_carries_the_stored_summary(run, monkeypatch):
    prompts = []

    async def summarize(provider, key, model, prompt):
        prompts.append(prompt)
        return "NEW SUMMARY"
    monkeypatch.setattr(api_compaction, "summarize", summarize)
    history = []
    for n in range(6):
        history += [{"role": "user", "text": f"question {n}", "id": 2 * n + 1},
                    {"role": "assistant", "text": f"answer {n}", "id": 2 * n + 2}]

    def anthropic_stream(text, tool, used):
        events = [NS(type="message_start", message=NS(usage=NS(
            input_tokens=used, output_tokens=1, cache_read_input_tokens=0,
            cache_creation_input_tokens=0)))]
        events += [NS(type="content_block_start", index=0, content_block=NS(type="text", text="")),
                   NS(type="content_block_delta", index=0, delta=NS(type="text_delta", text=text)),
                   NS(type="content_block_stop", index=0)]
        if tool:
            events += [NS(type="content_block_start", index=1, content_block=NS(
                type="tool_use", id="t1", name="read_file", input={})),
                NS(type="content_block_delta", index=1, delta=NS(
                    type="input_json_delta", partial_json=json.dumps({"path": "a"}))),
                NS(type="content_block_stop", index=1)]
        events.append(NS(type="message_delta", delta=NS(stop_reason="tool_use" if tool else "end_turn"),
                         usage=NS(output_tokens=10)))
        return lambda: Stream(events)

    run("anthropic", [anthropic_stream("x", True, 180_000), anthropic_stream("done", False, 1000)],
        model="claude-haiku-4-5", api_history=history, api_memory="DECISION-KEEP-ME",
        conversation_id=991005)
    assert prompts and "DECISION-KEEP-ME" in prompts[0]


# ── B4. a Gemini search-quota 429 falls back to no search ───────────────────

def _gemini_reply(text):
    events = [NS(candidates=[NS(content=NS(parts=[gtypes.Part(text=text)]), finish_reason=None)],
                 usage_metadata=None),
              NS(candidates=[], usage_metadata=NS(prompt_token_count=12, candidates_token_count=4,
                                                  cached_content_token_count=0))]
    return lambda: Stream(events)


def _quota_error():
    raise RuntimeError("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
                       "'message': 'You exceeded your current quota'}}")


def test_gemini_search_quota_429_retries_at_once_without_search(run, monkeypatch):
    monkeypatch.setattr(ar, "_GEMINI_SEARCH_OFF", set())
    searched = []

    def on_request(kwargs):
        searched.append(any(getattr(t, "google_search", None) is not None
                            for t in kwargs["config"].tools))

    events, _, sleep = run("google", [_quota_error, _gemini_reply("hi")],
                           model="gemini-3.8-flash", on_request=on_request)
    assert searched == [True, False]
    sleep.assert_not_awaited()
    assert [e.data["content"] for e in events if e.type == "response"] == ["hi"]
    assert "gemini-3.8-flash" in ar._GEMINI_SEARCH_OFF

    # A 429 on a request without search keeps the quota retry.
    searched.clear()
    events, _, sleep = run("google", [_quota_error, _gemini_reply("ok")],
                           model="gemini-3.8-flash", on_request=on_request)
    assert searched == [False, False]
    sleep.assert_awaited_once_with(10)
    assert [e.data["content"] for e in events if e.type == "response"] == ["ok"]


# ── M1. parallel OpenAI tool calls on one index stay separate ───────────────

def test_parallel_openai_tool_calls_on_index_zero_are_separate(run):
    def delta(call_id, name, arguments):
        return NS(choices=[NS(delta=NS(content=None, tool_calls=[NS(index=0, id=call_id, function=NS(
            name=name, arguments=arguments))]), finish_reason=None)], usage=None)
    usage = NS(choices=[], usage=NS(prompt_tokens=10, completion_tokens=3,
                                    prompt_tokens_details=NS(cached_tokens=0)))
    first = [delta("c1", "read_file", '{"path"'), delta(None, None, ': "a"}'),
             delta("c2", "read_file", '{"path": '), delta(None, None, '"b"}'),
             NS(choices=[NS(delta=NS(content=None, tool_calls=None), finish_reason="tool_calls")],
                usage=None), usage]
    second = [NS(choices=[NS(delta=NS(content="done", tool_calls=None), finish_reason="stop")],
                 usage=None), usage]
    _, executed, _ = run("openai", [lambda: Stream(first), lambda: Stream(second)])
    assert executed == [("read_file", {"path": "a"}), ("read_file", {"path": "b"})]


# ── M2. an API context reading answers only for its provider and model ──────

def test_api_context_reading_answers_only_for_its_own_provider_and_model(monkeypatch):
    from routes.conversation_routes import _live_context_reading
    reading = {"used": 10, "window": 100, "percent": 10.0,
               "model": "claude-opus-5-5", "provider": "anthropic"}
    monkeypatch.setattr(ar, "_API_CONTEXT_READINGS", {991006: reading})
    assert _live_context_reading(991006, None, "anthropic", "claude-opus-5-5") == reading
    assert _live_context_reading(991006, None, "anthropic", "claude-haiku-4-5") is None
    assert _live_context_reading(991006, None, "ollama", "claude-opus-5-5") is None
