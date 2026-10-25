"""Shared API-turn decisions, exercised with fake SDK clients only."""
import asyncio
import copy
import json
from types import SimpleNamespace as NS
from unittest import mock

import pytest

import agentic.agent_runner as ar


PROVIDERS = ("google", "anthropic", "openai")


def response(provider, text="finished", tool=False, truncated=False):
    if provider == "google":
        parts = [NS(text=text, thought=False, function_call=None)] if text else []
        if tool:
            parts.append(NS(text=None, thought=False,
                            function_call=NS(id="call1", name="read_file", args={})))
        return NS(candidates=[NS(content=NS(role="model", parts=parts),
                                 finish_reason=NS(name="MAX_TOKENS") if truncated else None)],
                  usage_metadata=NS(prompt_token_count=2, candidates_token_count=3))
    if provider == "anthropic":
        blocks = [NS(type="text", text=text)] if text else []
        if tool:
            blocks.append(NS(type="tool_use", id="call1", name="read_file", input={}))
        return NS(content=blocks, stop_reason="max_tokens" if truncated else "end_turn",
                  usage=NS(input_tokens=2, output_tokens=3))
    calls = [NS(id="call1", function=NS(name="read_file", arguments="{}"))] if tool else None
    return NS(choices=[NS(message=NS(content=text, tool_calls=calls),
                          finish_reason="length" if truncated else "stop")],
              usage=NS(prompt_tokens=2, completion_tokens=3))


class Client:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
        self.models = NS(generate_content=self.generate)
        self.messages = NS(create=self.create)
        self.chat = NS(completions=NS(create=self.create))

    def generate(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return value

    async def create(self, **kwargs):
        return self.generate(**kwargs)


@pytest.fixture
def run_case(monkeypatch):
    monkeypatch.setattr(ar, "get_gemini_tool_declarations",
                        lambda: [{"function_declarations": []}])
    monkeypatch.setattr(ar, "get_openai_tool_declarations", lambda: [])
    monkeypatch.setattr(ar, "_all_tool_definitions", lambda: [])
    monkeypatch.setattr(ar.AgentRunner, "_get_architect_wisdom", lambda self: "architect wisdom")

    def run(provider, replies, denied=False, images=None, on_execute=None, read_only=False):
        client = Client(replies)
        monkeypatch.setattr(ar.genai, "Client", lambda **kw: client)
        monkeypatch.setattr(ar.anthropic, "AsyncAnthropic", lambda **kw: client)
        monkeypatch.setattr(ar.openai, "AsyncOpenAI", lambda **kw: client)
        runner = ar.AgentRunner(provider, "k", "m", None, images=images)
        runner.read_only = read_only
        timeline = []

        async def execute(name, args):
            timeline.append("executed")
            if on_execute:
                on_execute()
            return {"success": True, "summary": "ok"}, []

        monkeypatch.setattr(runner, "_execute_tool_with_approval", execute)
        if denied:
            monkeypatch.setattr(runner, "_approval_prompt", lambda *a: "approval")
            monkeypatch.setattr(runner, "_await_approval", mock.AsyncMock(
                return_value=NS(approved=False, summary="declined")))
        sleep = mock.AsyncMock()

        async def collect():
            events = []
            async for event in runner._run_inner("hello"):
                timeline.append(event.type)
                events.append(event)
            return events

        with mock.patch.object(ar.asyncio, "sleep", sleep):
            events = asyncio.run(collect())
        return events, client.requests, timeline, sleep

    return run


@pytest.mark.parametrize("provider", PROVIDERS)
def test_retry_exhaustion(run_case, provider):
    events, requests, _, sleep = run_case(provider, [RuntimeError("429 quota")] * 3)
    assert len(requests) == 3
    assert [e.data["code"] for e in events if e.type == "error"] == ["provider_quota"]
    assert len([e for e in events if e.type == "status"]) == 3
    assert sleep.call_args_list == [mock.call(10), mock.call(20), mock.call(30)]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_intermediate_text_precedes_execution_and_final_text_precedes_response(run_case, provider):
    events, _, timeline, sleep = run_case(provider, [response(provider, "working", tool=True),
                                                    response(provider)])
    assert timeline.index("text") < timeline.index("executed")
    assert [e.data["content"] for e in events if e.type == "text"] == ["working", "finished"]
    assert [e.data["content"] for e in events if e.type == "response"] == ["finished"]
    final_index = timeline.index("response")
    assert timeline[final_index - 1] == "text"
    sleep.assert_not_called()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_denial_is_reported_and_encoded_in_history(run_case, provider):
    events, requests, timeline, _ = run_case(provider, [response(provider, tool=True),
                                                      response(provider)], denied=True)
    results = [e.data for e in events if e.type == "tool_result"]
    assert results == [{"tool": "read_file", "success": False, "summary": "declined"}]
    assert "executed" not in timeline
    history = requests[1].get("contents", requests[1].get("messages"))
    if provider == "google":
        payload = history[-1].parts[0].function_response.response["result"]
    elif provider == "anthropic":
        payload = history[-1]["content"][0]["content"]
    else:
        payload = history[-1]["content"]
    assert json.loads(payload) == {"success": False, "summary": "declined"}


def test_empty_openai_final(run_case):
    events, _, _, _ = run_case("openai", [response("openai", None)])
    assert [e.data["content"] for e in events if e.type == "response"] == [""]
    assert not any(e.type == "text" for e in events)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_mailbox_reply_receives_final_answer_after_tools(run_case, provider):
    from routes.conversation_routes import _TurnReply

    events, _, _, _ = run_case(provider, [response(provider, "working", tool=True),
                                         response(provider, "final answer")])
    reply = _TurnReply()
    for event in events:
        reply.add(event)
    assert reply.value() == "final answer"


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("read_only", [False, True])
def test_tools_added_during_execution_reach_next_request(run_case, monkeypatch,
                                                        provider, read_only):
    registry = [{"name": "read_file", "description": "Read a file",
                 "parameters": {"type": "object", "properties": {}}}]
    monkeypatch.setattr(ar, "get_gemini_tool_declarations",
                        lambda: [{"function_declarations": registry}])
    monkeypatch.setattr(ar, "get_openai_tool_declarations", lambda: [
        {"type": "function", "function": t} for t in registry])
    monkeypatch.setattr(ar, "_all_tool_definitions", lambda: registry)

    def add_tools():
        registry.extend([
            {"name": "newly_enabled_tool", "description": "New MCP tool",
             "parameters": {"type": "object", "properties": {}}},
            {"name": "write_file", "description": "Write a file",
             "parameters": {"type": "object", "properties": {}}},
        ])

    _, requests, _, _ = run_case(provider, [response(provider, tool=True), response(provider)],
                                 on_execute=add_tools, read_only=read_only)

    def declarations(request):
        if provider == "google":
            return request["config"].tools[0].function_declarations
        return request["tools"]

    def names(request):
        tools = declarations(request)
        if provider == "google":
            return [t.name for t in tools]
        if provider == "openai":
            return [t["function"]["name"] for t in tools]
        return [t["name"] for t in tools]

    assert names(requests[0]) == ["read_file"]
    expected = ["read_file", "newly_enabled_tool"]
    if not read_only:
        expected.append("write_file")
    assert names(requests[1]) == expected
    if provider == "anthropic":
        for request in requests:
            tools = declarations(request)
            assert tools[-1]["cache_control"] == {"type": "ephemeral"}
            assert all("cache_control" not in t for t in tools[:-1])
    assert all("cache_control" not in t for t in registry)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_truncation_never_executes_partial_tools(run_case, provider):
    events, _, timeline, _ = run_case(provider, [response(provider, "partial", tool=True,
                                                        truncated=True)])
    assert "executed" not in timeline
    assert "tool_call" not in timeline
    assert "text" not in timeline
    assert [e.data["stop_reason"] for e in events if e.type == "done"] == ["max_tokens"]
    assert [e.data["content"] for e in events if e.type == "response"] == ["partial"]


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("image", ["invalid-image", "data:;base64,YQ==",
                                    "data:image/png,YQ==", "data:image/png;base64,"])
def test_shared_system_and_invalid_image(run_case, provider, image, caplog):
    _, requests, _, _ = run_case(provider, [response(provider)], images=[image])
    request = requests[0]
    if provider == "google":
        system = request["config"].system_instruction
        parts = request["contents"][0].parts
    elif provider == "anthropic":
        system = request["system"][0]["text"]
        parts = request["messages"][0]["content"]
        assert request["timeout"] == ar._SAGLAYICI_ZAMAN_ASIMI
    else:
        system = request["messages"][0]["content"]
        parts = request["messages"][1]["content"]
    assert "[DİL]\nKullanıcıyla Türkçe konuş." in system
    assert "C# kodu yazmak için KULLANMA" not in system
    assert "architect wisdom" in system
    assert len(parts) == 1
    assert any(record.levelname == "WARNING" for record in caplog.records)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_provider_errors_redact_secrets_and_log_traceback(run_case, provider, caplog):
    secret = "sk-ant-api03-" + "a" * 40
    events, _, _, _ = run_case(provider, [RuntimeError("rejected " + secret)])
    message = next(e.data["message"] for e in events if e.type == "error")
    assert secret not in message
    assert "rejected" in message
    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert secret not in errors[0].getMessage()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_retry_classifies_raw_error_before_redaction(run_case, provider):
    secret = "AIza" + "a" * 40
    events, requests, _, sleep = run_case(
        provider, [RuntimeError(f"x-api-key: {secret} status 429")] * 3)
    assert len(requests) == 3
    assert len([e for e in events if e.type == "status"]) == 3
    assert [e.data["code"] for e in events if e.type == "error"] == ["provider_quota"]
    assert all(secret not in json.dumps(e.data) for e in events)
    assert sleep.call_args_list == [mock.call(10), mock.call(20), mock.call(30)]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_no_tools_classifies_raw_error_before_redaction(run_case, provider):
    secret = "AIza" + "a" * 40
    events, requests, _, sleep = run_case(
        provider, [RuntimeError(f"x-api-key: {secret} function calling is not enabled")])
    assert len(requests) == 1
    assert [e.data["code"] for e in events if e.type == "error"] == ["model_no_tools"]
    assert all(secret not in json.dumps(e.data) for e in events)
    sleep.assert_not_called()
