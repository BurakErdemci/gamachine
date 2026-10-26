"""Computer-use registration and runtime discovery without external services."""
import asyncio
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from mcp.types import CallToolResult, TextContent

import agentic.agent_runner as ar
from computer_use import registration, server, settings
from computer_use.platform import macos as driver_client
from computer_use.driver_client import DriverError, DriverUnavailable
from spawn_env import CONVERSATION_ENV


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    monkeypatch.setattr(settings, "_store", None)


def bind_flag(on):
    values = {settings.ENABLED_KEY: "1" if on else "0"}
    db = types.SimpleNamespace(get_setting=values.get, set_setting=values.__setitem__)
    settings.bind_store(db)
    return db


def test_enabled_now_fails_closed_and_tracks_the_stored_flag():
    assert settings.enabled_now() is False
    broken = MagicMock()
    broken.get_setting.side_effect = RuntimeError("database unavailable")
    settings.bind_store(broken)
    assert settings.enabled_now() is False
    db = bind_flag(False)
    assert settings.enabled_now() is False
    settings.set_enabled(db, True)
    assert settings.enabled_now() is True
    settings.set_enabled(db, False)
    assert settings.enabled_now() is False
    settings.bind_store(None)
    assert settings.enabled_now() is False


@pytest.mark.parametrize("frozen", [False, True])
def test_server_argv_for_dev_and_frozen_builds(monkeypatch, frozen):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    expected = [sys.executable]
    if not frozen:
        expected.append(str(Path(registration.__file__).resolve().parents[1] / "main.py"))
    expected.append("computer-mcp-server")
    assert registration.server_argv() == expected


@pytest.mark.parametrize("source", ["UNITYAI_URL", "ANTIGRAVITY_URL", None])
def test_entries_contain_only_the_stable_backend_url_and_owner(monkeypatch, source):
    bind_flag(True)
    monkeypatch.delenv("UNITYAI_URL", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_URL", raising=False)
    url = "http://localhost:8000"
    if source:
        url = "http://127.0.0.1:8123"
        monkeypatch.setenv(source, url)
    if source == "UNITYAI_URL":
        monkeypatch.setenv("ANTIGRAVITY_URL", "http://localhost:8124")
    argv = registration.server_argv()
    assert registration.claude_server_entry(7) == {
        "type": "stdio", "command": argv[0], "args": argv[1:],
        "env": {CONVERSATION_ENV: "7", "UNITYAI_URL": url},
    }
    entry = {"command": argv[0], "args": argv[1:], "env": {"UNITYAI_URL": url},
             "default_tools_approval_mode": "approve"}
    assert registration.codex_thread_entry(()) == entry
    assert registration.codex_thread_entry((CONVERSATION_ENV,)) == {
        **entry, "env_vars": [CONVERSATION_ENV],
    }


@pytest.mark.parametrize("conversation_id", [None, 0, -1, "7", True])
def test_claude_entry_requires_a_positive_plain_integer(conversation_id):
    bind_flag(True)
    assert registration.claude_server_entry(conversation_id) is None


def test_entries_are_absent_when_disabled_or_unbound():
    for db in (None, bind_flag(False)):
        settings.bind_store(db)
        assert registration.claude_server_entry(7) is None
        assert registration.codex_thread_entry((CONVERSATION_ENV,)) is None


def test_claude_session_entry_is_identical_across_turns_and_approval_modes(monkeypatch, tmp_path):
    from tests.test_mcp_approval_owner import _claude_session_kwargs
    from agentic import approval_mode

    db = bind_flag(True)
    monkeypatch.setenv("UNITYAI_URL", "http://localhost:8000")
    first = _claude_session_kwargs(monkeypatch, tmp_path, 7)["mcp_servers"]
    assert first[registration.SERVER_NAME] == registration.claude_server_entry(7)
    for mode in ("step", "balanced", "auto"):
        monkeypatch.setattr(approval_mode, "current_mode", lambda mode=mode: mode)
        second = _claude_session_kwargs(monkeypatch, tmp_path, 7)["mcp_servers"]
        assert first == second
        cached = types.SimpleNamespace(effort=None, model=None, cwd=str(tmp_path),
                                       mcp_servers=first, read_only=False)
        assert ar._oturum_yeniden_kurma_gerekceleri(
            cached, model=None, effort=None, workspace=str(tmp_path), mcp_servers=second) == []
    settings.set_enabled(db, False)
    disabled = _claude_session_kwargs(monkeypatch, tmp_path, 7)["mcp_servers"]
    assert registration.SERVER_NAME not in disabled
    assert ar._oturum_yeniden_kurma_gerekceleri(
        cached, model=None, effort=None, workspace=str(tmp_path), mcp_servers=disabled)


def test_read_only_claude_session_has_no_computer_server(monkeypatch, tmp_path):
    import subprocess
    from providers import claude_sdk_session
    from tests.test_mcp_approval_owner import _FakeClaudeSession
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    bind_flag(True)
    captured = []
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda host="localhost": None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(claude_sdk_session, "get_session",
                        lambda cid, **kw: _FakeClaudeSession(captured, cid, kw))
    runner = ar.AgentRunner(provider_type="subscription", api_key="", model_name="claude-x",
                            workspace_path=str(tmp_path), conversation_id=9,
                            generation_mode="auto", read_only=True)

    async def run():
        async for _ in runner._run_claude_session("hi"):
            pass

    asyncio.run(run())
    assert captured
    assert registration.SERVER_NAME not in captured[0][1]["mcp_servers"]


@pytest.mark.parametrize("read_only", [False, True])
def test_claude_computer_tool_permission_defers_to_backend_except_read_only(read_only):
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    from providers.claude_sdk_session import ClaudeSDKSession

    async def run():
        session = ClaudeSDKSession(conversation_id=4343, cwd=".", auto_approve=False,
                                   approval_timeout=1.0, read_only=read_only)
        session._out_q = asyncio.Queue()
        args = {"pid": 4242}
        result = await session._can_use_tool("mcp__gamachineComputer__computer_click", args, None)
        assert session._out_q.empty()
        assert isinstance(result, PermissionResultDeny if read_only else PermissionResultAllow)
        if not read_only:
            assert result.updated_input == args

    asyncio.run(run())


@pytest.mark.parametrize("on", [False, True])
@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("forward_env", [(), (CONVERSATION_ENV, "GAMACHINE_WORKSPACE")])
def test_codex_config_contains_the_full_entry_only_for_enabled_main_threads(
        monkeypatch, on, read_only, forward_env):
    from providers import codex_session

    bind_flag(on)
    monkeypatch.setattr(codex_session, "_configured_codex_mcp_names", lambda: set())
    config = codex_session._trusted_mcp_config(forward_env=forward_env, read_only=read_only)
    expected = ({registration.SERVER_NAME: registration.codex_thread_entry(forward_env)}
                if on and not read_only else {})
    assert config == {"mcp_servers": expected}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    binary = tmp_path / "cua-driver"
    binary.write_text("test driver")
    state = {"running": True, "driver_path": str(binary),
             "socket_path": str(tmp_path / "driver.sock")}
    observed = []
    client_type = httpx.AsyncClient
    monkeypatch.setattr(driver_client.approval_bridge, "BACKEND_URL", "http://localhost:8123")
    monkeypatch.setattr(driver_client.approval_bridge, "_get_headers",
                        lambda: {"X-Session-Token": "test-token"})

    def response(request):
        observed.append(request)
        if state.get("http_error"):
            return httpx.Response(503)
        if state.get("connection_error"):
            raise httpx.ConnectError("offline", request=request)
        if state.get("invalid_json"):
            return httpx.Response(200, content=b"invalid json")
        return httpx.Response(200, json={"runtime": state.get("override", state)})

    def client(**kwargs):
        assert kwargs == {"timeout": 5.0}
        return client_type(**kwargs, transport=httpx.MockTransport(response))

    monkeypatch.setattr(driver_client.httpx, "AsyncClient", client)
    return state, observed


def test_fetch_runtime_uses_authenticated_backend_values(runtime):
    state, observed = runtime
    assert asyncio.run(driver_client.fetch_runtime()) == (state["driver_path"], state["socket_path"])
    assert len(observed) == 1
    assert observed[0].method == "GET"
    assert str(observed[0].url) == "http://localhost:8123/computer-use/settings"
    assert observed[0].headers["X-Session-Token"] == "test-token"


@pytest.mark.parametrize("bad", [
    "stopped", "non_boolean", "relative_binary", "relative_socket", "missing_binary",
    "wrong_basename", "non_string_binary", "non_string_socket", "missing_socket",
    "directory", "no_runtime", "http_error", "connection_error", "invalid_json",
])
def test_invalid_runtime_raises_and_has_a_clear_model_visible_error(runtime, monkeypatch, tmp_path, bad):
    state, _ = runtime
    if bad == "stopped":
        state["running"] = False
    elif bad == "non_boolean":
        state["running"] = 1
    elif bad == "relative_binary":
        state["driver_path"] = "cua-driver"
    elif bad == "relative_socket":
        state["socket_path"] = "driver.sock"
    elif bad == "missing_binary":
        state["driver_path"] = str(tmp_path / "missing" / "cua-driver")
    elif bad == "wrong_basename":
        binary = tmp_path / "other-driver"
        binary.write_text("test driver")
        state["driver_path"] = str(binary)
    elif bad == "non_string_binary":
        state["driver_path"] = 7
    elif bad == "non_string_socket":
        state["socket_path"] = []
    elif bad == "missing_socket":
        state.pop("socket_path")
    elif bad == "directory":
        binary = tmp_path / "dir" / "cua-driver"
        binary.mkdir(parents=True)
        state["driver_path"] = str(binary)
    elif bad == "no_runtime":
        state["override"] = None
    else:
        state[bad] = True
    with pytest.raises(DriverUnavailable):
        asyncio.run(driver_client.fetch_runtime())
    constructor = MagicMock()
    monkeypatch.setattr(server, "DriverClient", constructor)
    result = asyncio.run(server.ComputerServer().call("computer_click", {"pid": 4242}))
    assert result.is_error
    assert result.content[0].text == "Bilgisayar kullanımı sürücüsü kullanılamıyor."
    constructor.assert_not_called()


@pytest.mark.parametrize("changed", ["socket_path", "driver_path"])
def test_server_refreshes_runtime_once_per_call_and_reuses_only_matching_clients(
        runtime, monkeypatch, tmp_path, changed):
    state, observed = runtime
    clients = []

    def build(binary, socket):
        client = MagicMock(binary=binary, socket=socket)
        client.list_running_apps = AsyncMock(return_value={4242: ("com.apple.TextEdit", "TextEdit")})
        client.call = AsyncMock(return_value=CallToolResult(content=[TextContent(type="text", text="ok")]))
        client.close = AsyncMock()
        clients.append(client)
        return client

    constructor = MagicMock(side_effect=build)
    monkeypatch.setattr(server, "DriverClient", constructor)
    monkeypatch.setattr(server.approval_bridge, "request_approval", AsyncMock(return_value={"approved": True}))

    async def run():
        handler = server.ComputerServer()
        try:
            for _ in range(2):
                assert not (await handler.call("computer_click", {"pid": 4242})).is_error
            constructor.assert_called_once_with(state["driver_path"], state["socket_path"])
            assert clients[0].call.await_count == 2
            if changed == "socket_path":
                state[changed] = str(tmp_path / "restarted.sock")
            else:
                binary = tmp_path / "updated" / "cua-driver"
                binary.parent.mkdir()
                binary.write_text("new driver")
                state[changed] = str(binary)
            assert not (await handler.call("computer_click", {"pid": 4242})).is_error
            assert constructor.call_count == 2
            constructor.assert_called_with(state["driver_path"], state["socket_path"])
            clients[0].close.assert_awaited_once()
            clients[1].call.assert_awaited_once_with("click", {"pid": 4242})
            state["running"] = False
            assert (await handler.call("computer_click", {"pid": 4242})).is_error
            assert clients[1].call.await_count == 1
        finally:
            await handler.close()

    asyncio.run(run())
    assert len(observed) == 4
    clients[1].close.assert_awaited_once()


def test_driver_failure_never_retries_a_mutating_call(runtime, monkeypatch):
    state, observed = runtime
    client = MagicMock(binary=state["driver_path"], socket=state["socket_path"])
    client.list_running_apps = AsyncMock(return_value={4242: ("com.apple.TextEdit", "TextEdit")})
    client.call = AsyncMock(side_effect=DriverError("disconnected"))
    client.close = AsyncMock()
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(server, "DriverClient", constructor)
    monkeypatch.setattr(server.approval_bridge, "request_approval", AsyncMock(return_value={"approved": True}))

    async def run():
        handler = server.ComputerServer()
        try:
            result = await handler.call("computer_type_text", {"pid": 4242, "text": "hello"})
            assert result.is_error and "yinelenmedi" in result.content[0].text
        finally:
            await handler.close()

    asyncio.run(run())
    constructor.assert_called_once_with(state["driver_path"], state["socket_path"])
    client.call.assert_awaited_once_with("type_text", {"pid": 4242, "text": "hello"})
    assert len(observed) == 1


def test_concurrent_runtime_change_constructs_one_owned_replacement(runtime, monkeypatch, tmp_path):
    state, _ = runtime
    clients = []
    live = set()

    class Client:
        def __init__(self, binary, socket):
            self.binary, self.socket = binary, socket
            clients.append(self)
            live.add(self)

        async def call(self, tool, args):
            return CallToolResult(content=[TextContent(type="text", text="ok")])

        async def close(self):
            await asyncio.sleep(0)
            live.discard(self)

    monkeypatch.setattr(server, "DriverClient", Client)
    monkeypatch.setattr(server.approval_bridge, "request_approval", AsyncMock(return_value={"approved": True}))

    async def run():
        handler = server.ComputerServer()
        try:
            assert not (await handler.call("computer_list_apps", {})).is_error
            state["socket_path"] = str(tmp_path / "restarted.sock")
            # Exactly two concurrent requests; close yields to expose double replacement.
            results = await asyncio.gather(
                handler.call("computer_list_apps", {}), handler.call("computer_list_apps", {}))
            assert all(not result.is_error for result in results)
            assert len(clients) == 2
            assert live == {handler.driver}
        finally:
            await handler.close()
        assert live == set()

    asyncio.run(run())


def test_old_client_held_across_replacement_cannot_spawn_or_retry(runtime, monkeypatch, tmp_path):
    from computer_use import driver_client
    state, _ = runtime
    old = driver_client.DriverClient(state["driver_path"], state["socket_path"])
    old.list_running_apps = AsyncMock(return_value={4242: ("com.apple.TextEdit", "TextEdit")})
    transport = MagicMock(side_effect=AssertionError("closed client started a transport"))
    monkeypatch.setattr(driver_client, "_transport", transport)
    replacement = MagicMock(binary=state["driver_path"], socket=str(tmp_path / "restarted.sock"))
    replacement.call = AsyncMock(return_value=CallToolResult(content=[]))
    replacement.close = AsyncMock()
    monkeypatch.setattr(server, "DriverClient", MagicMock(return_value=replacement))

    async def run():
        awaiting_approval, release = asyncio.Event(), asyncio.Event()

        async def approve(*args, **kwargs):
            awaiting_approval.set()
            await release.wait()
            return {"approved": True}

        monkeypatch.setattr(server.approval_bridge, "request_approval", approve)
        handler = server.ComputerServer(old)
        # One held mutation and one runtime replacement; no child may start.
        task = asyncio.create_task(handler.call("computer_type_text", {"pid": 4242, "text": "hello"}))
        try:
            await asyncio.wait_for(awaiting_approval.wait(), 2)
            state["socket_path"] = replacement.socket
            assert await handler._runtime_driver() is replacement
            release.set()
            result = await asyncio.wait_for(task, 2)
            assert result.is_error
            assert result.content[0].text == "Bilgisayar kullanımı sürücüsü kullanılamıyor."
            transport.assert_not_called()
            replacement.call.assert_not_called()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await handler.close()

    asyncio.run(run())


@pytest.mark.parametrize("initial,following", [(True, False), (False, True), (True, True), (False, False)])
@pytest.mark.parametrize("read_only", [False, True])
def test_codex_chat_rebuilds_only_when_effective_computer_flag_changes(
        monkeypatch, tmp_path, caplog, initial, following, read_only):
    from providers import codex_session
    from providers.codex_provider import CodexProvider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    db = bind_flag(initial)
    monkeypatch.setattr(codex_session, "_SESSIONS", {})
    monkeypatch.setattr(codex_session, "_configured_codex_mcp_names", lambda: set())
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    write_config = MagicMock()
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", write_config)
    monkeypatch.setattr(codex_session.CodexSession, "close", AsyncMock())
    close_session = AsyncMock(side_effect=codex_session.close_session)
    monkeypatch.setattr(codex_session, "close_session", close_session)
    started = []

    async def stream(session, message, **kwargs):
        if not session._started:
            session._started = True
            started.append((session, codex_session._trusted_mcp_config(read_only=session.read_only)))
        yield {"type": "text", "content": "ok"}

    monkeypatch.setattr(codex_session.CodexSession, "stream", stream)
    runner = ar.AgentRunner(provider_type="subscription", api_key="", model_name="gpt-test",
                            workspace_path=str(tmp_path), conversation_id=17,
                            generation_mode="auto", read_only=read_only)

    async def run():
        sessions = []
        for on in (initial, following, following):
            settings.set_enabled(db, on)
            async for _ in runner._run_codex_session("hi"):
                pass
            current = codex_session._SESSIONS[17]
            assert current.computer_use is (on and not read_only)
            sessions.append(current)
        changed = initial != following and not read_only
        assert (sessions[0] is not sessions[1]) is changed
        assert sessions[1] is sessions[2]
        assert close_session.await_count == int(changed)
        assert write_config.call_count == 1 + int(changed)
        assert len(started) == 1 + int(changed)
        for session, config in started:
            assert (registration.SERVER_NAME in config["mcp_servers"]) is session.computer_use
        if changed:
            assert ("bilgisayar kullanımı açıldı" if following else "bilgisayar kullanımı kapandı") in caplog.text

    with caplog.at_level("INFO"):
        asyncio.run(run())
