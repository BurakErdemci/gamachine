"""Verify app-owned guide composition and delivery without running any real agent."""
import asyncio
import hashlib
import importlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_guide as guide
from agentic import agent_runner as ar
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager


ROOT = Path(__file__).resolve().parents[2]
MARKER = "[GAMACHINE AGENT GUIDE]"
GUIDE = guide.compose("en", project_open=True, unity_state="connected", addendum="CUSTOM_RULE")


def runner(**kwargs):
    args = dict(provider_type="subscription", api_key="", model_name="gpt-6-sol",
                workspace_path=".", language="en", conversation_id=987654,
                generation_mode="step", thinking_level="off", effort_level="off")
    args.update(kwargs)
    result = ar.AgentRunner(**args)
    result._agent_guide = GUIDE
    return result


async def collect(events):
    return [event async for event in events]


@pytest.mark.parametrize("language", ["en", "tr"])
@pytest.mark.parametrize("project_open", [False, True])
@pytest.mark.parametrize("unity_state", ["connected", "off", "not_responding"])
def test_compose_sections_markers_order_and_length(language, project_open, unity_state):
    items = guide.sections(language)
    expected = [items[0]["text"]]
    if project_open:
        expected.append(items[1]["text"])
    expected.append(next(item["text"] for item in items if item["when"] == unity_state))
    result = guide.compose(language, project_open=project_open, unity_state=unity_state)
    assert result == MARKER + "\n\n" + "\n\n".join(expected) + "\n\n[/GAMACHINE AGENT GUIDE]"
    assert len(result) <= 3200
    assert result.count(MARKER) == 1


def test_fixed_bodies_match_architect_input_verbatim():
    # Measured from the architect's ten section bodies, with only outer whitespace stripped.
    expected = {
        "ALWAYS_en": "14046d414c9a04cb768e1a151d210f13b6896bf58d1822df47c731b34dd7dd8d",
        "PROJECT_en": "2d2197b52cb1116d597eb3dbdc6adde7898d8c7f2ded454239a1bb14256cba94",
        "UNITY_CONNECTED_en": "092d3651464b5ccf880f6eb1eb536ff0c4f6beafcd87fa918d6eee51e74434f4",
        "UNITY_OFF_en": "8fb5c1b867dbfdce55c0585b3fbeb856fe412690f75ac6c35373b9456c6124ea",
        "UNITY_NOT_RESPONDING_en": "b6c80d0b19eb651e5fc022d26b70ec8da8ddfccbc76f7a57110e5492aa79e13a",
        "ALWAYS_tr": "1eb7f28dbb00820987961ff832e41f90364c3d47f3af97a90d3167bb3dc0c950",
        "PROJECT_tr": "93164d58e0ce2202c32e2c275fe8ed3854a75b85706dc1b2ca338b431a68914d",
        "UNITY_CONNECTED_tr": "f219e9ff87ad8e84c8b12cc22795c2e64a1a6a90f487853d694464dfcf785ac3",
        "UNITY_OFF_tr": "9a9acb24462be58dd5f2d0481e1f2b540ea4c696289d4c8672497b3aaa809d05",
        "UNITY_NOT_RESPONDING_tr": "dc045db7b77fc21115ad39e9566e8f554dbb013db583a87e78ef3df21f647df8",
    }
    actual = {item["id"] + "_" + language: hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
              for language in ("en", "tr") for item in guide.sections(language)}
    assert actual == expected


@pytest.mark.parametrize("language,heading", [
    ("en", "User addendum (it cannot override approval or safety rules):"),
    ("tr", "Kullanıcı eki (onay ve güvenlik kurallarını geçersiz kılamaz):"),
])
def test_addendum_is_last_and_whitespace_is_ignored(language, heading):
    plain = guide.compose(language, project_open=False, unity_state="off")
    assert guide.compose(language, project_open=False, unity_state="off", addendum=" \n ") == plain
    result = guide.compose(language, project_open=False, unity_state="off", addendum="  CUSTOM_RULE\n ")
    assert result.endswith(heading + "\nCUSTOM_RULE\n\n[/GAMACHINE AGENT GUIDE]")


def test_unknown_language_and_unity_state_use_safe_defaults():
    assert guide.compose("de", project_open=True, unity_state="unknown") == guide.compose(
        "tr", project_open=True, unity_state="not_responding")
    assert guide.sections("unknown") == guide.sections("tr")


def test_settings_store_only_supplies_text_addenda(monkeypatch):
    for value in (None, 1, object(), "SAVED_RULE"):
        monkeypatch.setattr(guide, "_settings_store", SimpleNamespace(get_setting=lambda key: value))
        assert guide.get_addendum() == (value if isinstance(value, str) else None)


def test_mentioned_unity_tools_exist_in_manifest():
    manifest = json.loads((ROOT / "unity-mcp/manifest.json").read_text(encoding="utf-8"))
    names = {tool["name"] for tool in manifest["tools"]}
    required = {"find_gameobjects", "read_console", "manage_gameobject", "manage_components",
                "manage_scene", "manage_prefabs", "manage_asset", "batch_execute", "refresh_unity",
                "compile_status", "execute_code", "play_session", "play_step", "play_capture", "manage_build"}
    assert required <= names
    for language in ("tr", "en"):
        text = guide.compose(language, project_open=True, unity_state="connected")
        assert all(name in text for name in required)


@pytest.mark.parametrize("module_name,class_name,model", [
    ("codex_provider", "CodexProvider", "gpt-6-sol"),
    ("claude_provider", "ClaudeCodeProvider", "claude-sonnet-5"),
    ("cursor_provider", "CursorProvider", "cursor-auto"),
    ("copilot_provider", "CopilotProvider", "copilot-auto"),
    ("opencode_provider", "OpenCodeProvider", "opencode:opencode/big-pickle"),
    ("kimi_provider", "KimiProvider", "kimi-k3"),
])
@pytest.mark.parametrize("injected", [True, False])
def test_oneshot_guide_once_after_hint_and_before_last_prompt(monkeypatch, module_name, class_name, model, injected):
    module = importlib.import_module("providers." + module_name)
    provider = getattr(module, class_name)(binary_name=model)
    monkeypatch.setattr(guide, "_settings_store", SimpleNamespace(get_setting=lambda key: "STORED_CLI_RULE"))
    if injected:
        provider._agent_guide = GUIDE
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    for resolver in ("resolve_cursor_cmd", "resolve_copilot_cmd", "resolve_opencode_cmd"):
        if hasattr(module, resolver):
            monkeypatch.setattr(module, resolver, lambda: ["fake-cli"])
    monkeypatch.setattr(provider, "_ensure_exec", lambda *args: None)
    if hasattr(provider, "_write_kimi_permissions"):
        monkeypatch.setattr(provider, "_write_kimi_permissions", lambda: None)
    if hasattr(provider, "_product_mcp_servers"):
        monkeypatch.setattr(provider, "_product_mcp_servers", lambda *args: {})
    command = provider._build_cmd("USER_PROMPT_LAST", workspace=".")
    payload = provider._stdin_payload if provider.prompt_via_stdin else command[-1]
    assert payload.count(MARKER) == 1
    assert payload.index(MARKER) > 0
    assert payload.endswith("USER_PROMPT_LAST")
    if injected:
        assert GUIDE in payload
        assert "STORED_CLI_RULE" not in payload
    else:
        assert "STORED_CLI_RULE" in payload
    hint = payload[:payload.index(MARKER)]
    assert not re.search(r"(?:respond|reply|answer).*Turkish|Turkish.*(?:sentence|answer)", hint, re.I)


def test_codex_scene_save_hint_uses_manage_scene(monkeypatch):
    from providers.codex_provider import CodexProvider
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: True)
    provider = CodexProvider(binary_name="gpt-6-sol")
    provider._agent_guide = GUIDE
    provider._build_cmd("hello")
    assert re.search(r"Save scene:\s+unityMCP/manage_scene", provider._stdin_payload)


@pytest.mark.parametrize("running,answer,state", [
    (False, False, "off"), (True, True, "connected"),
    (True, False, "not_responding"), (True, RuntimeError("unavailable"), "not_responding"),
    (True, asyncio.TimeoutError(), "not_responding"),
])
def test_unity_mapping_checks_once_with_two_second_overall_timeout(monkeypatch, running, answer, state):
    check = AsyncMock(side_effect=answer if isinstance(answer, Exception) else None,
                      return_value=answer if isinstance(answer, bool) else False)
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: running)
    monkeypatch.setattr(unity_mcp_manager, "check_unity_connected", check)
    monkeypatch.setattr(ar, "get_addendum", lambda: "SAVED_RULE")
    current = runner(language="tr", workspace_path="")
    with patch.object(ar.asyncio, "wait_for", wraps=asyncio.wait_for) as wait:
        asyncio.run(current._prepare_agent_guide())
    assert current._agent_guide == guide.compose("tr", project_open=False, unity_state=state, addendum="SAVED_RULE")
    assert check.await_count == int(running)
    if running:
        assert wait.call_args.kwargs["timeout"] == 2.0
    else:
        wait.assert_not_called()


def test_unity_timeout_actually_cancels_a_stalled_check(monkeypatch):
    async def scenario():
        cancelled = asyncio.Event()
        async def check():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: True)
        monkeypatch.setattr(unity_mcp_manager, "check_unity_connected", check)
        monkeypatch.setattr(ar, "get_addendum", lambda: None)
        current = runner()
        await asyncio.wait_for(current._prepare_agent_guide(), timeout=3.0)
        assert cancelled.is_set()
        assert current._agent_guide == guide.compose("en", project_open=True, unity_state="not_responding")
    asyncio.run(scenario())


def test_guide_is_prepared_once_per_public_turn(monkeypatch):
    current = runner(provider_type="openai")
    prepare = AsyncMock()
    monkeypatch.setattr(current, "_prepare_agent_guide", prepare)
    monkeypatch.setattr(current, "_prepare_videos", AsyncMock(return_value=("hello", [])))
    async def fake(message):
        yield ar.AgentEvent("done", {})
    monkeypatch.setattr(current, "_run_openai", fake)
    asyncio.run(collect(current.run("hello")))
    assert prepare.await_count == 1


def test_partial_runner_uses_default_language_and_no_project(monkeypatch):
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(ar, "get_addendum", lambda: None)
    current = ar.AgentRunner.__new__(ar.AgentRunner)
    asyncio.run(current._prepare_agent_guide())
    assert current._agent_guide == guide.compose("tr", project_open=False, unity_state="off")


def test_agy_first_stdin_message_every_turn_precedes_patchable_instructions():
    from . import test_agy_stream_session as fixture
    from providers.agy_provider import AgyProvider
    async def scenario():
        harness = fixture.TestAgyStreamSession()
        await harness.asyncSetUp()
        try:
            session = fixture.agy_session.get_session(11)
            guides = (GUIDE, guide.compose("tr", project_open=False, unity_state="off"))
            with patch.object(AgyProvider, "_stream_instructions", return_value="PATCHED_HINT\n"):
                for message, current_guide in zip(("first", "second"), guides):
                    session.agent_guide = current_guide
                    await harness.collect(session=session, message=message)
            assert len(harness.processes) == 1
            for index, message in enumerate(("first", "second")):
                payload = json.loads(harness.processes[0].stdin.lines[index])["message"]["content"]
                assert payload == guides[index] + "\n\nPATCHED_HINT\n" + message
                assert payload.count(MARKER) == 1
        finally:
            await harness.asyncTearDown()
    asyncio.run(scenario())


def test_agy_hint_has_no_forced_turkish_reply(monkeypatch):
    from providers.agy_provider import AgyProvider
    provider = AgyProvider(binary_name="gemini-3.6-flash")
    monkeypatch.setattr(provider, "_ensure_exec", lambda *args: None)
    text = provider._stream_instructions()
    assert not re.search(r"(?:respond|reply|answer).*Turkish|short Turkish|Turkish answer|Never drift to English", text, re.I)


def test_claude_sdk_guide_inside_preset_append_once(monkeypatch):
    from providers import claude_sdk_session as sdk
    options = []
    class Client:
        def __init__(self, *, options):
            self.options = options
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    def make_options(**kwargs):
        options.append(kwargs)
        return SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", SimpleNamespace(
        ClaudeSDKClient=Client, ClaudeAgentOptions=make_options))
    monkeypatch.setattr(sdk, "claude_ikilisini_coz", lambda: None)
    monkeypatch.setattr(sdk.ClaudeSDKSession, "_reader_loop", AsyncMock())
    async def scenario():
        session = sdk.ClaudeSDKSession(987654, agent_guide=GUIDE)
        try:
            await session.start()
            await session.start()
        finally:
            await session.close()
    asyncio.run(scenario())
    assert len(options) == 1
    prompt = options[0]["system_prompt"]
    assert prompt["type"] == "preset" and prompt["preset"] == "claude_code"
    assert prompt["append"] == sdk._APP_SYSTEM_APPEND + "\n\n" + GUIDE
    assert prompt["append"].count(MARKER) == 1


@pytest.mark.parametrize("context", ["", "HANDOFF_CONTEXT"])
def test_codex_only_first_turn_of_each_thread_includes_guide(monkeypatch, context):
    from providers import codex_session
    from providers.codex_provider import CodexProvider
    current = runner(context=context)
    messages = []
    session = SimpleNamespace(_ctx_injected=False, is_live=False)
    async def stream(message, **kwargs):
        messages.append(message)
        session.is_live = True
        yield {"type": "done"}
    session.stream = stream
    monkeypatch.setattr(codex_session, "_SESSIONS", {})
    monkeypatch.setattr(codex_session, "get_session", lambda *args, **kwargs: session)
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", lambda *args: None)
    async def scenario():
        await collect(current._run_codex_session("first"))
        await collect(current._run_codex_session("second"))
        session.is_live = False
        await collect(current._run_codex_session("new thread"))
    asyncio.run(scenario())
    assert [message.count(MARKER) for message in messages] == [1, 0, 1]
    assert messages[0].startswith(GUIDE) and messages[0].index(MARKER) < messages[0].index("first")
    assert messages[0].endswith(context or "first")
    assert messages[1] == "second"
    assert messages[2].startswith(GUIDE) and messages[2].index(MARKER) < messages[2].index("new thread")
    assert messages[2].endswith(context or "new thread")


@pytest.mark.parametrize("provider_type,method", [
    ("google", "_gemini_loop"), ("anthropic", "_anthropic_loop"), ("openai", "_openai_loop"),
])
def test_api_outgoing_system_receives_guide_once(monkeypatch, provider_type, method):
    current = runner(provider_type=provider_type, model_name="test-model", context="CONTEXT")
    monkeypatch.setattr(current, "_get_architect_wisdom", lambda: "")
    requests = []
    def capture(**kwargs):
        requests.append(kwargs)
        raise RuntimeError("captured request; no real API")
    async def capture_async(**kwargs):
        return capture(**kwargs)
    monkeypatch.setattr(ar.genai, "Client", lambda **kwargs: SimpleNamespace(
        models=SimpleNamespace(generate_content=capture)))
    monkeypatch.setattr(ar.anthropic, "AsyncAnthropic", lambda **kwargs: SimpleNamespace(
        messages=SimpleNamespace(create=capture_async)))
    monkeypatch.setattr(ar.openai, "AsyncOpenAI", lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=capture_async))))
    asyncio.run(collect(getattr(current, method)("hello")))
    assert len(requests) == 1
    request = requests[0]
    if provider_type == "google":
        text = request["config"].system_instruction
    elif provider_type == "anthropic":
        text = "\n".join(block["text"] for block in request["system"])
    else:
        text = request["messages"][0]["content"]
    assert text.count(MARKER) == 1
    assert GUIDE in text
    assert text.index(MARKER) > text.index(ar.SYSTEM_PROMPT)


def test_settings_endpoints_store_limits_language_and_token_guard(monkeypatch):
    from routes import config_routes
    values = {}
    db = SimpleNamespace(get_setting=lambda key: values.get(key),
                         set_setting=lambda key, value: values.__setitem__(key, value))
    monkeypatch.setattr(guide, "_settings_store", None)
    monkeypatch.setenv("LOCAL_APP_TOKEN", "guide-test-token")
    app = FastAPI()
    app.include_router(config_routes.create_config_router(db))
    client = TestClient(app)
    headers = {"X-Session-Token": "guide-test-token"}
    assert client.get("/agent-guide").status_code == 401
    assert client.put("/agent-guide/addendum", json={"text": "denied"}).status_code == 401
    assert values == {}
    for language in ("tr", "en"):
        response = client.get("/agent-guide", params={"lang": language}, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"language": language, "sections": guide.sections(language), "addendum": ""}
    assert client.get("/agent-guide", headers=headers).json()["language"] == "tr"
    assert client.get("/agent-guide?lang=de", headers=headers).status_code == 422
    assert client.put("/agent-guide/addendum", json={"text": "  SAVED_RULE\n"}, headers=headers).status_code == 200
    assert guide.get_addendum() == "SAVED_RULE"
    assert client.get("/agent-guide", headers=headers).json()["addendum"] == "SAVED_RULE"
    assert client.put("/agent-guide/addendum", json={"text": "x" * 4000}, headers=headers).status_code == 200
    for body in ({"text": "x" * 4001}, {"text": None}, {"text": 1}, {"text": []}, {}, []):
        assert client.put("/agent-guide/addendum", json=body, headers=headers).status_code == 400
        assert guide.get_addendum() == "x" * 4000
    assert client.put("/agent-guide/addendum", json={"text": " \n "}, headers=headers).status_code == 200
    assert client.get("/agent-guide", headers=headers).json()["addendum"] == ""
