"""Tabbed branching: a tab is a full copy of a chat "from now", then independent.

Contract the frontend codes against: POST /conversations/{id}/branch,
PUT /conversations/{id}/hidden, `parent_id`/`hidden` on the list, and a root
DELETE that takes its branches with it (`deleted_ids`).
"""
import sqlite3
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
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
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_manager, "base_dir", mem_dir)
    db = DatabaseManager(str(tmp_path / "branch.db"))
    db.save_ai_config(1, "subscription", "claude-opus-5", "")
    progress = {}
    app = FastAPI()
    app.include_router(cr.create_conversation_router(db, progress))
    with TestClient(app) as client:
        yield db, client, progress, mem_dir


def _seed(db, n=4, title="Asıl sohbet"):
    cid = db.create_conversation(1, title)
    for i in range(n):
        db.add_message(cid, "user" if i % 2 == 0 else "assistant", f"m{i}",
                       smells=[{"s": i}] if i == 1 else None)
    return cid


def _rows(db, sql, args=()):
    with sqlite3.connect(db.db_path) as conn:
        return conn.execute(sql, args).fetchall()


def test_branch_copies_messages_memory_summary_and_memory_file(env):
    db, client, _, mem_dir = env
    src = _seed(db)
    db.save_memory(src, "özet: kararlar")
    memory_manager.save_memory(str(src), "teknik hafıza")
    db.save_cli_session(src, "claude", "sess-src", "")

    r = client.post(f"/conversations/{src}/branch", headers=H)
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == {"id", "title", "parent_id", "hidden", "created_at", "updated_at"}
    assert b["parent_id"] == src and b["hidden"] is False
    assert b["title"] == "Asıl sohbet · dal"

    src_msgs = db.get_conversation_messages(src)
    new_msgs = db.get_conversation_messages(b["id"])
    strip = lambda ms: [(m["role"], m["content"], m["smells"], m["timestamp"]) for m in ms]
    assert strip(new_msgs) == strip(src_msgs)
    assert [m["id"] for m in new_msgs] == sorted(m["id"] for m in new_msgs)

    assert db.get_memory(b["id"]) == "özet: kararlar"
    assert (mem_dir / f"memory_{b['id']}.md").read_text(encoding="utf-8") == "teknik hafıza"
    # No CLI session: the branch's first turn must take the handoff path.
    assert _rows(db, "SELECT * FROM cli_sessions WHERE conversation_id = ?", (b["id"],)) == []
    fork_at = _rows(db, "SELECT fork_at FROM conversations WHERE id = ?", (b["id"],))[0][0]
    assert fork_at == src_msgs[-1]["id"]

    # Independent afterwards: writing to the branch leaves the source alone.
    db.add_message(b["id"], "user", "yalnız dalda")
    assert len(db.get_conversation_messages(src)) == len(src_msgs)


def test_branch_of_empty_chat_has_null_fork_and_no_memory_file(env):
    db, client, _, mem_dir = env
    src = db.create_conversation(1, "boş")
    b = client.post(f"/conversations/{src}/branch", headers=H).json()
    assert db.get_conversation_messages(b["id"]) == []
    assert _rows(db, "SELECT fork_at FROM conversations WHERE id = ?", (b["id"],))[0][0] is None
    assert not (mem_dir / f"memory_{b['id']}.md").exists()


def test_branch_of_branch_flattens_to_root_and_keeps_title(env):
    db, client, _, _ = env
    root = _seed(db)
    b1 = client.post(f"/conversations/{root}/branch", headers=H).json()
    db.add_message(b1["id"], "user", "b1'e özel")
    b2 = client.post(f"/conversations/{b1['id']}/branch", headers=H).json()
    assert b2["parent_id"] == root
    assert b2["title"] == "Asıl sohbet · dal"
    assert db.get_conversation_messages(b2["id"])[-1]["content"] == "b1'e özel"


def test_branch_refused_while_source_turn_in_flight(env):
    db, client, _, _ = env
    src = _seed(db)
    with ambient_turn(".", "step", src):
        r = client.post(f"/conversations/{src}/branch", headers=H)
    assert r.status_code == 409
    assert "sürüyor" in r.json()["detail"]
    assert db.get_branch_ids(src) == []
    # Another chat's turn does not block this one.
    with ambient_turn(".", "step", src + 999):
        assert client.post(f"/conversations/{src}/branch", headers=H).status_code == 200


def test_hidden_toggle_and_root_refused(env):
    db, client, _, _ = env
    root = _seed(db)
    b = client.post(f"/conversations/{root}/branch", headers=H).json()

    r = client.put(f"/conversations/{b['id']}/hidden", json={"hidden": True}, headers=H)
    assert r.status_code == 200 and r.json() == {"id": b["id"], "hidden": True}
    listed = {c["id"]: c for c in client.get("/conversations/1", headers=H).json()}
    assert listed[b["id"]]["hidden"] is True
    # Hidden, not deleted: messages survive and it can be reopened.
    assert db.get_conversation_messages(b["id"])
    r = client.put(f"/conversations/{b['id']}/hidden", json={"hidden": False}, headers=H)
    assert r.json() == {"id": b["id"], "hidden": False}

    r = client.put(f"/conversations/{root}/hidden", json={"hidden": True}, headers=H)
    assert r.status_code == 400
    assert "Ana sohbet" in r.json()["detail"]


def test_list_carries_parent_id_and_hidden(env):
    db, client, _, _ = env
    root = _seed(db)
    b = client.post(f"/conversations/{root}/branch", headers=H).json()
    listed = {c["id"]: c for c in client.get("/conversations/1", headers=H).json()}
    assert listed[root]["parent_id"] is None and listed[root]["hidden"] is False
    assert listed[b["id"]]["parent_id"] == root and listed[b["id"]]["hidden"] is False
    assert {"id", "title", "created_at", "updated_at"} <= set(listed[root])


def test_branch_activity_bumps_root_updated_at(env):
    db, client, _, _ = env
    root = _seed(db, title="aile")
    other = _seed(db, title="başka")
    b = client.post(f"/conversations/{root}/branch", headers=H).json()
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE conversations SET updated_at = '2000-01-01 00:00:00' WHERE id IN (?, ?)",
                     (root, b["id"]))
        conn.execute("UPDATE conversations SET updated_at = '2001-01-01 00:00:00' WHERE id = ?", (other,))

    db.add_message(b["id"], "user", "dalda yeni mesaj")

    listed = client.get("/conversations/1", headers=H).json()
    roots = [c["id"] for c in listed if c["parent_id"] is None]
    assert roots[0] == root, listed
    by_id = {c["id"]: c for c in listed}
    assert by_id[root]["updated_at"] > "2001-01-01 00:00:00"
    # The bump goes to the root only, never sideways.
    assert by_id[other]["updated_at"] == "2001-01-01 00:00:00"


def test_root_delete_removes_branches_with_full_cleanup(env, monkeypatch):
    db, client, progress, mem_dir = env
    root = _seed(db)
    b1 = client.post(f"/conversations/{root}/branch", headers=H).json()["id"]
    b2 = client.post(f"/conversations/{root}/branch", headers=H).json()["id"]
    unrelated = _seed(db, title="dokunma")
    for cid in (root, b1, b2, unrelated):
        db.save_cli_session(cid, "claude", f"s{cid}", "")
        memory_manager.save_memory(str(cid), f"mem {cid}")
        progress[cid] = ["x"]
        cr.scope_plan_store[cid] = {}
        cr.continuation_store[cid] = {}

    closed = defaultdict(list)
    import providers.agy_session as agy
    import providers.claude_sdk_session as cl
    import providers.codex_session as cx
    from agentic import wake_queue
    for name, mod in (("claude", cl), ("codex", cx), ("agy", agy)):
        async def _close(cid, _n=name):
            closed[_n].append(cid)
        monkeypatch.setattr(mod, "close_session", _close)
    resets = []
    monkeypatch.setattr(wake_queue, "reset", lambda cid: resets.append(cid))

    r = client.delete(f"/conversations/{root}", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["deleted_ids"] == [root, b1, b2]

    gone = (root, b1, b2)
    for cid in gone:
        assert db.get_conversation_owner(cid) is None
        assert db.get_conversation_messages(cid) == []
        assert _rows(db, "SELECT * FROM cli_sessions WHERE conversation_id = ?", (cid,)) == []
        assert not (mem_dir / f"memory_{cid}.md").exists()
        assert cid not in progress
        assert cid not in cr.scope_plan_store and cid not in cr.continuation_store
    for fam in ("claude", "codex", "agy"):
        assert sorted(closed[fam]) == sorted(gone), (fam, closed)
    assert sorted(resets) == sorted(gone)

    assert db.get_conversation_owner(unrelated) == 1
    assert (mem_dir / f"memory_{unrelated}.md").exists()
    cr.scope_plan_store.pop(unrelated, None)
    cr.continuation_store.pop(unrelated, None)


def test_branch_delete_removes_only_the_branch(env):
    db, client, _, _ = env
    root = _seed(db)
    b1 = client.post(f"/conversations/{root}/branch", headers=H).json()["id"]
    b2 = client.post(f"/conversations/{root}/branch", headers=H).json()["id"]
    r = client.delete(f"/conversations/{b1}", headers=H)
    assert r.json()["deleted_ids"] == [b1]
    assert db.get_conversation_owner(root) == 1
    assert db.get_branch_ids(root) == [b2]


def test_first_chat_stream_turn_of_branch_gets_handoff_context(env, monkeypatch):
    db, client, _, _ = env
    src = _seed(db)
    db.save_cli_session(src, "claude", "sess-src", "")
    b = client.post(f"/conversations/{src}/branch", headers=H).json()

    captured = {}

    class _Runner:
        def __init__(self, **kw):
            captured[kw["conversation_id"]] = kw

        async def run(self, message):
            yield ar.AgentEvent("response", {"content": "cevap"})
            yield ar.AgentEvent("done", {"stop_reason": "complete"})

    monkeypatch.setattr(cr, "AgentRunner", _Runner)
    for cid in (src, b["id"]):
        r = client.post("/chat-stream", headers=H, json={
            "conversation_id": cid, "user_id": 1, "message": "yeni soru"})
        assert r.status_code == 200, r.text

    kw = captured[b["id"]]
    assert kw["resume_id"] is None
    ctx = kw["context"]
    assert "[SOHBET GEÇMİŞİ" in ctx
    for i in range(4):
        assert f": m{i}" in ctx, ctx
    # Control: the source itself would resume its CLI session instead.
    assert captured[src]["resume_id"] == "sess-src"
