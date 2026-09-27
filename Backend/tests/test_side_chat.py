"""Side chat: a read-only side question over a main chat ("Yan soru").

Contract: a side row (`side_of = main`) never shows in the list, dies with its
main chat, never becomes a normal chat, writes nothing about the main chat, and
can write nothing at all - refused on the server before auto mode can approve,
and again inside every provider path the backend owns.
"""
import asyncio
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import approval_mode
from agentic.approval_policy import ambient_turn
from database import DatabaseManager
from rag.memory_manager import memory_manager

H = {"X-Session-Token": ""}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_manager, "base_dir", mem_dir)
    db = DatabaseManager(str(tmp_path / "side.db"))
    db.save_ai_config(1, "subscription", "claude-opus-5", "")
    closed = []

    async def _record_close(cid):
        closed.append(cid)

    from providers import claude_sdk_session
    monkeypatch.setattr(claude_sdk_session, "close_session", _record_close)
    app = FastAPI()
    router = cr.create_conversation_router(db, {})
    app.include_router(router)
    with TestClient(app) as client:
        yield db, client, router, mem_dir, closed


def _seed(db, n=4, title="Ana sohbet"):
    cid = db.create_conversation(1, title)
    for i in range(n):
        db.add_message(cid, "user" if i % 2 == 0 else "assistant", f"m{i}")
    return cid


def _rows(db, sql, args=()):
    with sqlite3.connect(db.db_path) as conn:
        return conn.execute(sql, args).fetchall()


def _open_side(client, main_id):
    r = client.post(f"/conversations/{main_id}/side", headers=H)
    assert r.status_code == 200, r.text
    return r.json()["side_id"]


# ── DB and list ─────────────────────────────────────────────────────────────

def test_side_row_is_excluded_from_the_list_and_reopening_returns_it(env):
    db, client, _, _, _ = env
    main = _seed(db)
    side = _open_side(client, main)
    assert side != main
    assert _open_side(client, main) == side
    assert db.get_side_of(side) == main
    assert db.get_side_of(main) is None
    ids = [c["id"] for c in client.get("/conversations/1", headers=H).json()]
    assert main in ids and side not in ids
    # No message copied, no CLI session, parent_id NULL (so `_touch` leaves main alone).
    assert db.get_conversation_messages(side) == []
    assert _rows(db, "SELECT * FROM cli_sessions WHERE conversation_id = ?", (side,)) == []
    assert _rows(db, "SELECT parent_id FROM conversations WHERE id = ?", (side,))[0][0] is None


def test_open_side_refuses_a_side_of_a_side_and_an_unknown_chat(env):
    db, client, _, _, _ = env
    side = _open_side(client, _seed(db))
    assert client.post(f"/conversations/{side}/side", headers=H).status_code == 400
    assert client.post("/conversations/9999/side", headers=H).status_code == 404


def test_side_activity_does_not_bump_the_main_chat(env):
    db, client, _, _, _ = env
    main = _seed(db)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE conversations SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (main,))
    side = _open_side(client, main)
    db.add_message(side, "user", "yan")
    assert _rows(db, "SELECT updated_at FROM conversations WHERE id = ?", (main,))[0][0] == "2000-01-01 00:00:00"


def test_deleting_the_main_chat_deletes_its_side_rows_and_closes_their_sessions(env):
    db, client, _, _, closed = env
    main = _seed(db)
    branch = client.post(f"/conversations/{main}/branch", headers=H).json()["id"]
    side_main = _open_side(client, main)
    side_branch = _open_side(client, branch)
    db.add_message(side_main, "user", "q")
    db.save_cli_session(side_main, "claude", "sess-side", "")
    other = _seed(db, title="başka")
    side_other = _open_side(client, other)

    r = client.delete(f"/conversations/{main}", headers=H)
    assert r.status_code == 200
    assert r.json()["deleted_ids"] == [main, branch]
    for gone in (side_main, side_branch):
        assert _rows(db, "SELECT * FROM conversations WHERE id = ?", (gone,)) == []
        assert _rows(db, "SELECT * FROM messages WHERE conversation_id = ?", (gone,)) == []
        assert _rows(db, "SELECT * FROM cli_sessions WHERE conversation_id = ?", (gone,)) == []
        assert gone in closed
    assert db.get_side_of(side_other) == other


# ── ordinary routes refuse a side id ────────────────────────────────────────

def test_ordinary_routes_refuse_a_side_id(env, monkeypatch):
    db, client, _, _, _ = env
    side = _open_side(client, _seed(db))

    class _MustNotRun:
        def __init__(self, **kw):
            raise AssertionError("a side id reached AgentRunner through /chat-stream")

    monkeypatch.setattr(cr, "AgentRunner", _MustNotRun)
    body = {"conversation_id": side, "message": "x", "user_id": 1}
    assert client.post("/chat-stream", json=body, headers=H).status_code == 400
    assert client.post("/chat", json=body, headers=H).status_code == 400
    assert client.post(f"/conversations/{side}/branch", headers=H).status_code == 400
    assert client.put(f"/conversations/{side}/hidden", json={"hidden": False}, headers=H).status_code == 400
    assert client.put(f"/conversations/{side}", json={"title": "x"}, headers=H).status_code == 400
    assert client.post(f"/conversations/{side}/compact", headers=H).status_code == 400
    assert client.post(f"/conversations/{side}/import-memory", json={"content": "x"}, headers=H).status_code == 400
    assert client.delete(f"/conversations/{side}", headers=H).status_code == 400
    assert db.get_conversation_messages(side) == []
    assert db.get_side_of(side) is not None


# ── MCP approval: refused in step AND auto, before auto approval ────────────

def _request_card(client, gate_id, conversation_id=None):
    body = {"gate_id": gate_id, "tool": "manage_gameobject", "params": {"action": "create"}}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return client.post("/mcp-approval-request", json=body, headers=H).json()


@pytest.mark.parametrize("mode", ["step", "auto", "balanced"])
def test_mcp_approval_request_refuses_a_side_chat_in_both_modes(env, mode):
    db, client, _, _, _ = env
    main = _seed(db)
    side = _open_side(client, main)
    approval_mode.set_mode(mode, source="test")
    with ambient_turn(".", mode, side):
        res = _request_card(client, f"side-{mode}", conversation_id=side)
    assert res["status"] == "resolved" and res["approved"] is False
    assert "salt okunur" in res["error"]
    assert f"side-{mode}" not in client.get("/mcp-pending", headers=H).json()["pending"]


def test_mcp_approval_request_for_the_main_chat_is_unaffected(env):
    db, client, _, _, _ = env
    main = _seed(db)
    _open_side(client, main)
    approval_mode.set_mode("auto", source="test")
    assert _request_card(client, "main-auto", conversation_id=main)["approved"] is True
    approval_mode.set_mode("step", source="test")
    with ambient_turn(".", "step", main):
        assert _request_card(client, "main-step", conversation_id=main) == {"status": "ok", "gate_id": "main-step"}
    pending = client.get("/mcp-pending", headers=H).json()["pending"]
    assert pending["main-step"]["conversation_id"] == main


def test_mcp_approval_request_refuses_when_the_side_lookup_fails(env, monkeypatch):
    db, client, _, _, _ = env
    main = _seed(db)
    approval_mode.set_mode("auto", source="test")

    def _boom(_cid):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(db, "get_side_of", _boom)
    res = _request_card(client, "lookup-fail", conversation_id=main)
    assert res["approved"] is False


# ── chat-stop / close on a side id leave foreign cards alone ────────────────

def test_chat_stop_on_a_side_chat_does_not_deny_an_unowned_card(env):
    db, client, _, _, _ = env
    main = _seed(db)
    side = _open_side(client, main)
    assert _request_card(client, "unowned")["status"] == "ok"

    assert client.post(f"/chat-stop/{side}", headers=H).status_code == 200
    assert "unowned" in client.get("/mcp-pending", headers=H).json()["pending"]
    assert client.delete(f"/conversations/{side}/side", headers=H).status_code == 200
    assert "unowned" in client.get("/mcp-pending", headers=H).json()["pending"]

    # Codex sideaudit: after the sweep the side id has no row; its Stop
    # must still leave other chats' unowned cards alone.
    assert client.post(f"/chat-stop/{side}", headers=H).status_code == 200
    assert "unowned" in client.get("/mcp-pending", headers=H).json()["pending"]

    # The main chat's Stop keeps its old meaning: unowned cards are denied.
    client.post(f"/chat-stop/{main}", headers=H)
    assert "unowned" not in client.get("/mcp-pending", headers=H).json()["pending"]


def test_chat_stop_keeps_denying_when_the_chat_lookup_fails(env, monkeypatch):
    """Codex sideverify: skipping the denial on a lookup error left an
    ordinary chat's pending card open after its Stop."""
    db, client, _, _, _ = env
    main = _seed(db)
    assert _request_card(client, "unowned")["status"] == "ok"

    def boom(_cid):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "get_conversation_owner", boom)
    assert client.post(f"/chat-stop/{main}", headers=H).status_code == 200
    assert "unowned" not in client.get("/mcp-pending", headers=H).json()["pending"]


def test_close_side_deletes_the_row_and_closes_its_sessions_only(env):
    db, client, _, _, closed = env
    main = _seed(db)
    side = _open_side(client, main)
    db.add_message(side, "user", "q")
    r = client.delete(f"/conversations/{side}/side", headers=H)
    assert r.status_code == 200
    assert _rows(db, "SELECT * FROM conversations WHERE id = ?", (side,)) == []
    assert _rows(db, "SELECT * FROM messages WHERE conversation_id = ?", (side,)) == []
    assert closed == [side]
    assert len(db.get_conversation_messages(main)) == 4
    # A main chat id is not a side chat: nothing deleted.
    assert client.delete(f"/conversations/{main}/side", headers=H).status_code == 404
    assert db.get_conversation_owner(main) == 1


# ── side-stream ──────────────────────────────────────────────────────────────

class _FakeRunner:
    last = None

    def __init__(self, **kw):
        self.kw = kw
        _FakeRunner.last = self

    async def run(self, message):
        self.message = message
        yield ar.AgentEvent("text", {"content": "yan "})
        yield ar.AgentEvent("response", {"content": "yan cevap"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete",
                                     "session_id": "side-sess"})


def _stream(client, side, **body):
    payload = {"message": "Bu neden böyle?", **body}
    r = client.post(f"/conversations/{side}/side-stream", json=payload, headers=H)
    return r


def test_side_stream_writes_nothing_about_the_main_chat(env, monkeypatch):
    db, client, _, mem_dir, _ = env
    main = _seed(db)
    db.save_memory(main, "ana özet")
    before_msgs = db.get_conversation_messages(main)
    before_row = _rows(db, "SELECT title, updated_at, memory_summary FROM conversations WHERE id = ?", (main,))
    side = _open_side(client, main)
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)

    r = _stream(client, side)
    assert r.status_code == 200
    assert '"conversation_id": %d' % side in r.text
    assert "yan cevap" in r.text

    assert db.get_conversation_messages(main) == before_msgs
    assert _rows(db, "SELECT title, updated_at, memory_summary FROM conversations WHERE id = ?", (main,)) == before_row
    assert _rows(db, "SELECT * FROM cli_sessions WHERE conversation_id = ?", (main,)) == []
    assert not (mem_dir / f"memory_{main}.md").exists()
    # The side row keeps its own question, answer and CLI session.
    assert [(m["role"], m["content"]) for m in db.get_conversation_messages(side)] == [
        ("user", "Bu neden böyle?"), ("assistant", "yan cevap")]
    assert db.get_cli_session(side, "claude", "") == "side-sess"

    kw = _FakeRunner.last.kw
    assert kw["conversation_id"] == side
    assert kw["read_only"] is True
    assert kw["resume_id"] is None
    # Every main-chat message reaches the side turn's history (the handoff
    # helper drops the last element; the route pads for it), plus the main
    # chat's memory. The runner gets the raw question and builds the text.
    side_turn = kw["side_turn"]
    for i in range(4):
        assert f"m{i}" in side_turn.main_history
    assert "ana özet" in side_turn.main_history
    assert kw["context"] == ""
    assert "SALT OKUNUR" in side_turn.text(full=False)
    assert _FakeRunner.last.message == "Bu neden böyle?"


def test_side_follow_up_resumes_the_side_session_and_sees_earlier_answers(env, monkeypatch):
    db, client, _, _, _ = env
    side = _open_side(client, _seed(db))
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    _stream(client, side)
    _stream(client, side, message="peki ya bu?")
    kw = _FakeRunner.last.kw
    assert kw["resume_id"] == "side-sess"
    assert "yan cevap" in kw["side_turn"].side_history


def test_side_stream_caps_the_live_context_and_labels_a_running_turn(env, monkeypatch):
    db, client, _, _, _ = env
    main = _seed(db)
    side = _open_side(client, main)
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    live = "A" * 20000 + "SON"
    with ambient_turn(".", "step", main):
        _stream(client, side, live_context=live)
    msg = _FakeRunner.last.kw["side_turn"].text(full=False)
    assert "YARIM" in msg
    assert msg.count("A") <= cr.SIDE_LIVE_CONTEXT_CAP + 50
    assert "SON" in msg


def test_side_stream_refuses_agy_while_an_agy_turn_runs(env, monkeypatch):
    from providers import agy_session
    db, client, _, _, _ = env
    side = _open_side(client, _seed(db))
    db.save_ai_config(1, "subscription", "gemini-3-pro", "")
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    monkeypatch.setattr(agy_session, "agy_turn_busy", lambda: True)
    _FakeRunner.last = None
    r = _stream(client, side)
    assert r.status_code == 409
    assert r.json()["detail"] == cr._SIDE_AGY_REFUSED
    assert "agy aynı anda tek bir tur" in r.json()["detail"]
    assert _FakeRunner.last is None
    assert db.get_conversation_messages(side) == []


def test_side_stream_runs_agy_when_no_agy_turn_runs(env, monkeypatch):
    """Burak, 27 Sep 2026: the side panel refused agy even when agy was idle."""
    from providers import agy_session
    db, client, _, _, _ = env
    side = _open_side(client, _seed(db))
    db.save_ai_config(1, "subscription", "gemini-3-pro", "")
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    monkeypatch.setattr(agy_session, "agy_turn_busy", lambda: False)
    r = _stream(client, side)
    assert r.status_code == 200
    assert "yan cevap" in r.text
    kw = _FakeRunner.last.kw
    assert kw["conversation_id"] == side and kw["read_only"] is True
    assert kw["side_turn"] is not None


def test_side_stream_refuses_a_main_chat_id(env, monkeypatch):
    db, client, _, _, _ = env
    main = _seed(db)
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    assert _stream(client, main).status_code == 404


# ── sweeps ───────────────────────────────────────────────────────────────────

def _age(db, cid, seconds):
    old = (datetime.now() - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (old, cid))


def test_idle_sweep_deletes_old_idle_side_chats_only(env):
    db, client, router, _, closed = env
    a, b, c = _seed(db), _seed(db), _seed(db)
    old_idle, fresh, old_busy = _open_side(client, a), _open_side(client, b), _open_side(client, c)
    _age(db, old_idle, 3600)
    _age(db, old_busy, 3600)
    _age(db, a, 3600)
    with ambient_turn(".", "step", old_busy):
        swept = asyncio.run(router.sweep_idle_side_chats())
    assert swept == [old_idle]
    assert closed == [old_idle]
    assert db.get_side_of(fresh) == b and db.get_side_of(old_busy) == c
    # The main chats are never swept, however old.
    assert db.get_conversation_owner(a) == 1


def test_startup_sweep_deletes_every_side_row(env):
    db, client, _, _, _ = env
    main = _seed(db)
    side = _open_side(client, main)
    db.add_message(side, "user", "q")
    assert db.sweep_side_chats(0) == [side]
    assert _rows(db, "SELECT * FROM conversations WHERE side_of IS NOT NULL") == []
    assert _rows(db, "SELECT * FROM messages WHERE conversation_id = ?", (side,)) == []
    assert db.get_conversation_owner(main) == 1


def test_main_lifespan_runs_the_startup_sweep():
    # main.py builds its DB at import time from the real home, so this reads
    # the source rather than importing it.
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "main.py"), encoding="utf-8").read()
    lifespan = src[src.index("async def lifespan"):src.index("    yield\n")]
    assert "db.sweep_side_chats(0)" in lifespan
    assert "_side_sweep_loop()" in lifespan


# ── provider paths: read-only in auto mode too ──────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_claude_read_only_session_denies_writes_in_auto_and_allows_reads(tmp_path):
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    from providers.claude_sdk_session import ClaudeSDKSession

    ws = str(tmp_path)
    approval_mode.set_mode("auto", source="test")
    sess = ClaudeSDKSession(77, cwd=ws, auto_approve=True, read_only=True)
    for tool, inp in (("Write", {"file_path": os.path.join(ws, "a.cs"), "content": "x"}),
                      ("Edit", {"file_path": os.path.join(ws, "a.cs")}),
                      ("Bash", {"command": "echo hi"}),
                      ("AskUserQuestion", {"questions": []}),
                      ("mcp__unityMCP__manage_gameobject", {"action": "create"})):
        assert isinstance(_run(sess._can_use_tool(tool, inp, None)), PermissionResultDeny), tool
    assert isinstance(_run(sess._can_use_tool("Read", {"file_path": os.path.join(ws, "a.cs")}, None)),
                      PermissionResultAllow)
    # The same call in a normal auto session is allowed: the deny is read-only's.
    normal = ClaudeSDKSession(78, cwd=ws, auto_approve=True)
    assert isinstance(_run(normal._can_use_tool("Bash", {"command": "echo hi"}, None)), PermissionResultAllow)


def test_claude_read_only_is_connect_time_identity_and_main_sessions_are_unaffected():
    from providers.claude_sdk_session import ClaudeSDKSession, _identity_mismatch

    main_sess = ClaudeSDKSession(1, cwd=".")
    assert _identity_mismatch(main_sess, {"read_only": False}) is None
    assert _identity_mismatch(main_sess, {"read_only": True}) is not None
    assert ar._oturum_yeniden_kurma_gerekceleri(
        main_sess, model=None, effort=None, workspace=".", mcp_servers={}) == []
    assert ar._oturum_yeniden_kurma_gerekceleri(
        main_sess, model=None, effort=None, workspace=".", mcp_servers={}, read_only=True)


def test_codex_read_only_session_declines_in_auto_mode():
    from providers.codex_session import CodexSession

    approval_mode.set_mode("auto", source="test")
    sess = CodexSession(79, auto_approve=True, read_only=True)
    sess._out_q = asyncio.Queue()
    for method, params in (("item/commandExecution/requestApproval", {"command": "touch x"}),
                           ("item/fileChange/requestApproval", {"changes": []}),
                           ("item/permissions/requestApproval", {"permissions": {}})):
        assert _run(sess._resolve_approval(method, params)) == "decline", method
    assert sess._out_q.empty()
    normal = CodexSession(80, auto_approve=True)
    assert _run(normal._resolve_approval("item/commandExecution/requestApproval",
                                         {"command": "touch x"})) == "accept"


def _api_runner(ws, read_only=True):
    return ar.AgentRunner(provider_type="anthropic", api_key="", model_name="claude-x",
                          workspace_path=ws, conversation_id=81, read_only=read_only)


def test_api_loop_read_only_refuses_writes_and_memory(tmp_path, monkeypatch):
    mem_dir = tmp_path / "mem"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_manager, "base_dir", mem_dir)
    approval_mode.set_mode("auto", source="test")
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    runner = _api_runner(ws)

    res, _ = _run(runner._execute_tool_with_approval("write_file", {"file_path": "a.txt", "content": "x"}))
    assert res["success"] is False and not os.path.exists(os.path.join(ws, "a.txt"))
    res, _ = _run(runner._execute_tool_with_approval("save_to_memory", {"content": "gizli"}))
    assert res["success"] is False and list(mem_dir.iterdir()) == []
    for tool, args in (("delete_file", {"file_path": "a.txt"}), ("run_command", {"command": "echo x"}),
                       ("manage_gameobject", {"action": "create"})):
        assert _run(runner._execute_tool_with_approval(tool, args))[0]["success"] is False, tool

    with open(os.path.join(ws, "b.txt"), "w", encoding="utf-8") as f:
        f.write("okunur")
    res, _ = _run(runner._execute_tool_with_approval("read_file", {"file_path": "b.txt"}))
    assert res["success"] is True


def test_api_loop_read_only_declares_no_write_tool_and_shows_no_card(tmp_path):
    runner = _api_runner(str(tmp_path))
    names = {t["name"] for t in runner._tool_definitions()}
    assert names.isdisjoint({"write_file", "delete_file", "run_command", "save_to_memory"})
    assert "read_file" in names
    assert runner._approval_prompt("run_command", {"command": "rm -rf /"}) is None
    normal = _api_runner(str(tmp_path), read_only=False)
    assert {"write_file", "save_to_memory"} <= {t["name"] for t in normal._tool_definitions()}
    assert normal._approval_prompt("delete_file", {"file_path": "x"}) is not None


def test_read_only_unity_calls_follow_the_ledger():
    import unity_tool_policy
    assert ar._read_only_tool_allowed("manage_gameobject", {"action": "create"}) is False
    if unity_tool_policy.ledger_available():
        assert ar._read_only_tool_allowed("read_console", {"action": "get"}) is True
        assert ar._read_only_tool_allowed("read_console", {"action": "clear"}) is False
