"""Regressions from the Codex audit of branching (bc6ee0a) and of the one-shot
CLI cleanup on delete (cardaudit), 26 Sep 2026. Each test is a probe that
reproduced on the tree before the fix."""
import sqlite3
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import routes.conversation_routes as cr
from agentic import approval_mode
from agentic.approval_policy import ambient_turn
from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS, register_gate, release_gate
from database import DatabaseManager
from rag.memory_manager import memory_manager

H = {"X-Session-Token": ""}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_manager, "base_dir", mem_dir)
    db = DatabaseManager(str(tmp_path / "audit.db"))
    router = cr.create_conversation_router(db, {})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield db, client, router, mem_dir, tmp_path


def _endpoint(router, path, method):
    return next(r.endpoint for r in router.routes
                if getattr(r, "path", "") == path and method in getattr(r, "methods", set()))


def _conversation_ids(db):
    with sqlite3.connect(db.db_path) as conn:
        return sorted(r[0] for r in conn.execute("SELECT id FROM conversations"))


def _message_conv_ids(db):
    with sqlite3.connect(db.db_path) as conn:
        return sorted({r[0] for r in conn.execute("SELECT conversation_id FROM messages")})


def test_deleting_a_family_denies_its_pending_cards_so_auto_cannot_approve_them(env):
    db, client, _, _, tmp_path = env
    approval_mode.set_ui_secret("test-secret")
    root = db.create_conversation(1, "root")
    branch = db.create_branch(root)["id"]
    other = db.create_conversation(1, "other")
    try:
        with ambient_turn(str(tmp_path), "step", branch):
            branch_gate = register_gate("branch-local", branch)
            other_gate = register_gate("other-local", other)
            r = client.post("/mcp-approval-request", json={
                "gate_id": "branch-card", "conversation_id": branch,
                "tool": "safe_test_tool", "params": {}, "workspace_path": str(tmp_path),
            }, headers=H)
            assert r.status_code == 200, r.text
            pending = client.get("/mcp-pending", headers=H).json()["pending"]
            assert pending["branch-card"]["conversation_id"] == branch

            assert client.delete(f"/conversations/{root}", headers=H).status_code == 200

            assert "branch-card" not in client.get("/mcp-pending", headers=H).json()["pending"]
            assert "branch-local" not in APPROVAL_GATES
            # Released as a denial: the waiter was woken and reads no approval.
            assert branch_gate.is_set() and not APPROVAL_RESULTS.get("branch-local", False)
            # Another chat's gate is not the deleted family's to deny.
            assert "other-local" in APPROVAL_GATES and not other_gate.is_set()

            switched = client.post("/approval-mode", json={"mode": "auto"}, headers={
                **H, "X-Gamachine-UI-Secret": "test-secret"})
            assert switched.status_code == 200, switched.text
            decision = client.get("/mcp-approval-result/branch-card", headers=H).json()
            assert decision.get("approved") is False, decision
    finally:
        release_gate("branch-local")
        release_gate("other-local")


def test_branch_requested_mid_delete_leaves_no_orphan_under_the_deleted_root(env, monkeypatch):
    db, _, router, _, _ = env
    root = db.create_conversation(1, "root")
    db.add_message(root, "user", "hi")
    branch = db.create_branch(root)["id"]
    branch_ep = _endpoint(router, "/conversations/{conv_id}/branch", "POST")
    delete_ep = _endpoint(router, "/conversations/{conv_id}", "DELETE")
    outcome = {}

    import providers.claude_sdk_session as claude

    async def interleaving_close(conv_id):
        # The first await of the delete is where another request can run.
        if conv_id == root and "status" not in outcome:
            try:
                made = await branch_ep(branch, x_session_token="")
                outcome["status"] = 200
                outcome["id"] = made["id"]
            except HTTPException as exc:
                outcome["status"] = exc.status_code

    monkeypatch.setattr(claude, "close_session", interleaving_close)
    import asyncio
    result = asyncio.run(delete_ep(root, x_session_token=""))

    assert result["deleted_ids"] == [root, branch]
    assert outcome.get("status") == 404, outcome
    assert _conversation_ids(db) == []
    assert _message_conv_ids(db) == []


def test_create_branch_refuses_when_the_root_row_is_gone(env):
    db, _, _, _, _ = env
    root = db.create_conversation(1, "root")
    branch = db.create_branch(root)["id"]
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (root,))
    assert db.create_branch(branch) is None
    assert _conversation_ids(db) == [branch]


def test_unreadable_source_memory_fails_before_any_branch_is_created(env):
    db, client, _, mem_dir, _ = env
    src = db.create_conversation(1, "source")
    db.add_message(src, "user", "saved text")
    (mem_dir / f"memory_{src}.md").write_bytes(b"\xff")

    r = client.post(f"/conversations/{src}/branch", headers=H)

    assert r.status_code == 500
    assert "hafıza" in r.json()["detail"]
    assert db.get_branch_ids(src) == []
    assert _conversation_ids(db) == [src]
    assert _message_conv_ids(db) == [src]


def test_failed_memory_copy_rolls_the_branch_back(env, monkeypatch):
    db, client, _, mem_dir, _ = env
    src = db.create_conversation(1, "source")
    db.add_message(src, "user", "saved text")
    memory_manager.save_memory(str(src), "saved memory")
    real_path = memory_manager._get_path

    def path_for(chat_id):
        if chat_id == str(src):
            return real_path(chat_id)
        # A directory where the branch's file should go: the write fails.
        blocked = mem_dir / f"blocked_{chat_id}"
        blocked.mkdir(exist_ok=True)
        return blocked

    monkeypatch.setattr(memory_manager, "_get_path", path_for)

    r = client.post(f"/conversations/{src}/branch", headers=H)

    assert r.status_code == 500
    assert "hafıza" in r.json()["detail"]
    assert db.get_branch_ids(src) == []
    assert _conversation_ids(db) == [src]
    assert _message_conv_ids(db) == [src]
    assert (mem_dir / f"memory_{src}.md").read_text(encoding="utf-8") == "saved memory"


def test_deleting_a_chat_closes_its_one_shot_cli_sessions(env):
    db, client, _, _, _ = env
    from providers import oneshot_cli
    cid = db.create_conversation(1, "copilot chat")
    other = db.create_conversation(1, "other")
    mine = oneshot_cli.get_session("copilot", cid)
    theirs = oneshot_cli.get_session("copilot", other)
    try:
        assert client.delete(f"/conversations/{cid}", headers=H).status_code == 200
        assert oneshot_cli._SESSIONS.get(("copilot", cid)) is not mine
        assert ("copilot", cid) not in oneshot_cli._SESSIONS
        assert oneshot_cli._SESSIONS.get(("copilot", other)) is theirs
    finally:
        oneshot_cli._SESSIONS.pop(("copilot", cid), None)
        oneshot_cli._SESSIONS.pop(("copilot", other), None)
