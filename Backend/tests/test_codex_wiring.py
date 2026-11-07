"""A user action on a Codex chat (compaction, mid-turn message, model/effort
change, settings change, chat delete) never kills the app-server's background
commands or subagents; only the main turn is affected (Burak, 8 Nov 2026).
The Claude path is the reference.

Only the app-server boundary is faked: its JSON-RPC replies (a fake transport,
as in test_codex_session_caps) and the process kill (`CodexSession.close`).
No codex binary, no subprocess, no network.
"""
import asyncio
import sys
from collections import defaultdict
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.conversation_routes as cr
from agentic import agent_runner as ar
from agentic import shell_jobs
from database import DatabaseManager
from error_i18n import translate_detail
from providers import claude_sdk_session, codex_session
from providers.codex_provider import CodexProvider
from providers.codex_session import CodexSession

H = {"X-Session-Token": ""}
CID = 7311


class FakeAppServer:
    """Stands in for the app-server's JSON-RPC replies; records each request."""

    def __init__(self, session, replies=None):
        self.session = session
        self.calls = []
        self.replies = replies or {}

    async def request(self, method, params, timeout=60):
        self.calls.append((method, params))
        reply = self.replies.get(method)
        if method == "thread/compact/start" and reply is None:
            # The real server answers, then notifies thread/compacted.
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(self.session._dispatch(
                    {"method": "thread/compacted", "params": {"threadId": "thr-1"}})))
        if method == "turn/start" and reply is None:
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(self.session._emit_terminal({"type": "done"})))
            return {"id": 1, "result": {"turn": {"id": f"turn-{len(self.calls)}"}}}
        if isinstance(reply, Exception):
            raise reply
        return reply if reply is not None else {"id": 1, "result": {}}

    def methods(self):
        return [m for m, _ in self.calls]


def _live(cid=CID, replies=None, **kwargs):
    session = CodexSession(cid, **kwargs)
    server = FakeAppServer(session, replies)
    session._request = server.request
    session._started = True
    session.thread_id = "thr-1"
    return session, server


def _started(item_type, item_id):
    return {"method": "item/started", "params": {"item": {"type": item_type, "id": item_id}}}


def _completed(item_type, item_id):
    return {"method": "item/completed", "params": {"item": {"type": item_type, "id": item_id}}}


@pytest.fixture
def sessions(monkeypatch):
    table = {}
    monkeypatch.setattr(codex_session, "_SESSIONS", table)
    kill = AsyncMock()
    monkeypatch.setattr(CodexSession, "close", kill)
    return SimpleNamespace(table=table, kill=kill)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    db = DatabaseManager(str(tmp_path / "wiring.db"))
    app = FastAPI()
    app.include_router(cr.create_conversation_router(db, {}))
    with TestClient(app) as client:
        yield db, client


# ── 1. running-work tracker ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_started_commands_and_subagents_count_as_running_until_completed():
    session, _ = _live()
    for msg in (_started("commandExecution", "cmd-1"), _started("collabAgentToolCall", "sub-1"),
                _started("agentMessage", "msg-1"), _started("fileChange", "fc-1"),
                _started("reasoning", "r-1")):
        await session._dispatch(msg)
    assert sorted(session.running_work()) == ["cmd-1", "sub-1"]

    await session._dispatch(_completed("commandExecution", "cmd-1"))
    assert list(session.running_work()) == ["sub-1"]
    await session._dispatch(_completed("collabAgentToolCall", "sub-1"))
    assert not session.running_work()


@pytest.mark.asyncio
async def test_closing_the_session_forgets_running_work():
    session, _ = _live()
    await session._dispatch(_started("commandExecution", "cmd-1"))
    await session.close()
    assert not session.running_work()


# ── 2. compaction ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_codex_compaction_runs_in_place_and_never_kills_the_app_server(env, sessions):
    db, _ = env
    cid = db.create_conversation(1, "codex chat")
    session, server = _live(cid)
    sessions.table[cid] = session

    assert await cr._cli_baglamini_kucult(db, cid) is None

    assert server.methods() == ["thread/compact/start"]
    sessions.kill.assert_not_awaited()
    assert sessions.table.get(cid) is session


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["command", "subagent", "turn"])
async def test_failed_codex_compaction_keeps_a_session_with_running_work(env, sessions, busy):
    db, _ = env
    cid = db.create_conversation(1, "codex chat")
    session, server = _live(cid, replies={"thread/compact/start": {"id": 1, "error": {"code": -32601}}})
    sessions.table[cid] = session
    if busy == "command":
        await session._dispatch(_started("commandExecution", "cmd-1"))
    elif busy == "subagent":
        await session._dispatch(_started("collabAgentToolCall", "sub-1"))
    else:
        session._out_q = asyncio.Queue()
        session._terminal_sent = False

    kept = await cr._cli_baglamini_kucult(db, cid)

    assert kept == cr._COMPACT_FAILED_KEPT_BG
    assert server.methods() == ["thread/compact/start"]
    sessions.kill.assert_not_awaited()
    assert sessions.table.get(cid) is session


@pytest.mark.asyncio
async def test_failed_codex_compaction_with_nothing_running_resets_as_before(env, sessions):
    db, _ = env
    cid = db.create_conversation(1, "codex chat")
    session, server = _live(cid, replies={"thread/compact/start": RuntimeError("pipe closed")})
    sessions.table[cid] = session

    assert await cr._cli_baglamini_kucult(db, cid) is None

    assert server.methods() == ["thread/compact/start"]
    sessions.kill.assert_awaited_once()
    assert cid not in sessions.table


def test_codex_kept_line_has_an_english_translation():
    english = translate_detail(cr._COMPACT_FAILED_KEPT_BG, "en")
    assert english != cr._COMPACT_FAILED_KEPT_BG
    assert "background work" in english


# ── 3. mid-turn message ─────────────────────────────────────────────────────

def test_chat_inject_steers_a_running_codex_turn(env, sessions, monkeypatch):
    db, client = env
    cid = db.create_conversation(1, "codex chat")
    session, server = _live(cid)
    session._current_turn_id = "turn-9"
    session._out_q = asyncio.Queue()
    sessions.table[cid] = session
    monkeypatch.setattr(claude_sdk_session, "peek_session", lambda c: None)

    r = client.post(f"/chat-inject/{cid}", json={"message": "also the lights", "client_id": "c1"}, headers=H)

    assert r.status_code == 200
    assert r.json() == {"status": "injected"}
    assert server.methods() == ["turn/steer"]
    params = server.calls[0][1]
    assert (params["expectedTurnId"], params["clientUserMessageId"]) == ("turn-9", "c1")
    assert params["input"] == [{"type": "text", "text": "also the lights"}]
    assert "turn/interrupt" not in server.methods()
    sessions.kill.assert_not_awaited()

    # Once the turn has ended the client is sent back to its queue.
    session._out_q = None
    r = client.post(f"/chat-inject/{cid}", json={"message": "later", "client_id": "c2"}, headers=H)
    assert r.json() == {"status": "not_running"}
    assert server.methods() == ["turn/steer"]


# ── 4. chat delete ends the chat's shell jobs ───────────────────────────────

def test_deleting_a_chat_stops_its_background_shell_jobs(env, monkeypatch):
    db, client = env
    stopped = []

    def stop(cid):
        stopped.append(cid)
        raise RuntimeError("kill failed")  # best-effort: neither the delete nor the next id fails

    monkeypatch.setattr(shell_jobs, "stop_conversation", stop)

    async def boom(_cid):
        raise RuntimeError("claude close failed")

    # A failing provider close must not skip the jobs.
    monkeypatch.setattr(claude_sdk_session, "close_session", boom)
    root = db.create_conversation(1, "root")
    branch = db.create_branch(root)["id"]

    r = client.delete(f"/conversations/{root}", headers=H)

    assert r.status_code == 200
    assert sorted(r.json()["deleted_ids"]) == sorted([root, branch])
    assert sorted(stopped) == sorted([root, branch])


def test_closing_a_side_chat_stops_its_background_shell_jobs(env, monkeypatch):
    db, client = env
    stopped = []
    monkeypatch.setattr(shell_jobs, "stop_conversation", lambda c: stopped.append(c) or [])
    main = db.create_conversation(1, "main")
    side = db.create_side_chat(main, 1)

    assert client.delete(f"/conversations/{side}/side", headers=H).status_code == 200

    assert stopped == [side]


# ── 5. settings changes at the next turn start ──────────────────────────────

@pytest.fixture
def runner_env(monkeypatch, sessions):
    manager = SimpleNamespace(is_running=Mock(return_value=False))
    package = ModuleType("unity_ai_mcp")
    module = ModuleType("unity_ai_mcp.unity_mcp_manager")
    module.unity_mcp_manager = manager
    package.unity_mcp_manager = module
    monkeypatch.setitem(sys.modules, "unity_ai_mcp", package)
    monkeypatch.setitem(sys.modules, "unity_ai_mcp.unity_mcp_manager", module)
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", Mock(return_value=""))
    servers = {}

    async def start(self):
        servers[id(self)] = FakeAppServer(self)
        self._request = servers[id(self)].request
        self._started = True
        self.thread_id = "thr-1"

    monkeypatch.setattr(CodexSession, "start", start)
    monkeypatch.setattr(ar, "_DEFERRED_REASONS", {})
    runner = ar.AgentRunner(provider_type="subscription", api_key="", model_name="gpt-5.6-codex",
                            workspace_path=".", conversation_id=CID, generation_mode="step")
    runner.effort_level = "low"
    return SimpleNamespace(runner=runner, servers=servers, kill=sessions.kill, table=sessions.table)


async def _turn(env, text="hi"):
    async for _ in env.runner._run_codex_session(text):
        pass
    session = env.table[CID]
    return session, env.servers[id(session)]


def _turn_starts(server):
    return [p for m, p in server.calls if m == "turn/start"]


@pytest.mark.asyncio
async def test_model_and_effort_changes_switch_per_turn_without_closing(runner_env):
    first, _ = await _turn(runner_env)
    await first._dispatch(_started("collabAgentToolCall", "sub-1"))

    runner_env.runner.effort_level = "high"
    runner_env.runner.model_name = "gpt-6-luna"
    second, server = await _turn(runner_env)

    assert second is first
    runner_env.kill.assert_not_awaited()
    last = _turn_starts(server)[-1]
    assert (last["model"], last["effort"]) == ("gpt-6-luna", "high")
    # The next unchanged turn sees no change, so nothing is switched or closed again.
    third, server = await _turn(runner_env)
    assert third is first
    runner_env.kill.assert_not_awaited()
    assert (_turn_starts(server)[-1]["model"], _turn_starts(server)[-1]["effort"]) == ("gpt-6-luna", "high")


@pytest.mark.asyncio
async def test_a_rebuild_waits_for_running_work_then_runs(runner_env, tmp_path):
    first, _ = await _turn(runner_env)
    await first._dispatch(_started("commandExecution", "cmd-1"))

    runner_env.runner.workspace_path = str(tmp_path)
    second, server = await _turn(runner_env, "next")

    assert second is first
    runner_env.kill.assert_not_awaited()
    assert "arka plan işleri bitince uygulanacak" in runner_env.runner.session_defer_notice
    sent = _turn_starts(server)[-1]["input"][0]["text"]
    assert sent.startswith("[Sistem] Ayar değişikliği (workspace değişti) arka plan işleri bitince uygulanacak.")

    await first._dispatch(_completed("commandExecution", "cmd-1"))
    third, _ = await _turn(runner_env, "later")

    assert third is not first
    runner_env.kill.assert_awaited_once()
