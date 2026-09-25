"""Global approval mode (closed-loop.md §5, P1 part A).

Auto mode: no approval card anywhere, external MCP clients included. Step mode:
cards exactly as before. Only the app UI (Electron main holding the UI secret)
may flip the mode; LOCAL_APP_TOKEN alone, which the Unity MCP server and model
children can read, must not be enough.
"""

import asyncio
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode
from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS, GATE_OWNERS, register_gate, release_gate

UI_SECRET = "ui-secret-for-tests"


def _client(db=None):
    from routes.conversation_routes import create_conversation_router

    app = FastAPI()
    app.include_router(create_conversation_router(db or MagicMock(), {}))
    return TestClient(app)


def _request_card(client, gate_id, **extra):
    body = {"gate_id": gate_id, "tool": "manage_gameobject",
            "params": {"action": "create", "name": "Cube"}, "workspace_path": ""}
    body.update(extra)
    return client.post("/mcp-approval-request", json=body).json()


def _flip(client, mode, secret=UI_SECRET, **headers):
    hdrs = {"X-Gamachine-UI-Secret": secret} if secret is not None else {}
    hdrs.update(headers)
    return client.post("/approval-mode", json={"mode": mode, "source": "settings"}, headers=hdrs)


# ── /mcp-approval-request ────────────────────────────────────────────────────

def test_auto_mode_approves_request_without_conversation_and_leaves_no_pending_entry():
    approval_mode.set_mode("auto", source="test")
    with _client() as client:
        result = _request_card(client, "ext-auto")
        pending = client.get("/mcp-pending").json()["pending"]
        # The server's gate only accepts `approved is True` in the POST reply.
        assert result["status"] == "resolved"
        assert result["approved"] is True
        assert "ext-auto" not in pending
        assert client.get("/mcp-approval-result/ext-auto").json() == {"status": "pending"}
    assert "ext-auto" not in GATE_OWNERS


def test_auto_mode_approves_request_with_conversation_too():
    approval_mode.set_mode("auto", source="test")
    with _client() as client:
        result = _request_card(client, "own-auto", conversation_id=3)
        assert result["approved"] is True
        assert client.get("/mcp-pending").json()["pending"] == {}


def test_step_mode_creates_a_card_as_before():
    assert approval_mode.current_mode() == "step"
    with _client() as client:
        result = _request_card(client, "ext-step")
        assert result == {"status": "ok", "gate_id": "ext-step"}
        assert "ext-step" in client.get("/mcp-pending").json()["pending"]
        assert client.get("/mcp-approval-result/ext-step").json() == {"status": "pending"}
        client.post("/mcp-approval-respond/ext-step", json={"approved": False})


def test_running_auto_turn_no_longer_approves_in_step_mode():
    """A turn that started in auto must not keep approving after a flip to step."""
    from agentic.approval_policy import ambient_turn

    with ambient_turn("/ws", "auto"), _client() as client:
        result = _request_card(client, "stale-auto")
        assert result["status"] == "ok"
        client.post("/mcp-approval-respond/stale-auto", json={"approved": False})


# ── switching to auto drains every pending card ─────────────────────────────

def test_switching_to_auto_resolves_pending_cards_as_approved():
    approval_mode.set_ui_secret(UI_SECRET)
    with _client() as client:
        assert _request_card(client, "drain-1")["status"] == "ok"
        assert _request_card(client, "drain-2", conversation_id=7)["status"] == "ok"

        async def _in_process_gate():
            return register_gate("inproc-1", 7)

        event = asyncio.run(_in_process_gate())
        try:
            resp = _flip(client, "auto")
            assert resp.status_code == 200, resp.text
            assert resp.json()["approved_pending"] == 3
            assert client.get("/mcp-pending").json()["pending"] == {}
            for gid in ("drain-1", "drain-2"):
                got = client.get(f"/mcp-approval-result/{gid}").json()
                assert got["status"] == "resolved" and got["approved"] is True
            assert event.is_set()
            assert APPROVAL_RESULTS["inproc-1"] is True
        finally:
            release_gate("inproc-1")
    assert approval_mode.current_mode() == "auto"


def test_switching_to_step_leaves_pending_cards_alone():
    approval_mode.set_ui_secret(UI_SECRET)
    with _client() as client:
        assert _request_card(client, "keep-1")["status"] == "ok"
        resp = _flip(client, "step")
        assert resp.status_code == 200
        assert resp.json()["approved_pending"] == 0
        assert "keep-1" in client.get("/mcp-pending").json()["pending"]
        client.post("/mcp-approval-respond/keep-1", json={"approved": False})


# ── who may write ────────────────────────────────────────────────────────────

def test_write_with_plain_local_app_token_is_refused(monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "app-token")
    approval_mode.set_ui_secret(UI_SECRET)
    with _client() as client:
        resp = client.post("/approval-mode", json={"mode": "auto"},
                           headers={"X-Session-Token": "app-token"})
        assert resp.status_code == 403
        # Reads stay on the normal token.
        read = client.get("/approval-mode", headers={"X-Session-Token": "app-token"})
        assert read.status_code == 200 and read.json()["mode"] == "step"
    assert approval_mode.current_mode() == "step"


def test_write_with_maintenance_header_is_refused_even_with_the_ui_secret(monkeypatch):
    monkeypatch.setenv("LOCAL_APP_TOKEN", "app-token")
    approval_mode.set_ui_secret(UI_SECRET)
    with _client() as client:
        resp = _flip(client, "auto", **{"X-Session-Token": "app-token",
                                        "X-UnityAI-Maintenance": "app-token"})
        assert resp.status_code == 403
    assert approval_mode.current_mode() == "step"


def test_write_is_refused_when_no_ui_secret_was_configured():
    with _client() as client:
        assert _flip(client, "auto", secret="").status_code == 403
        assert _flip(client, "auto", secret="anything").status_code == 403
    assert approval_mode.current_mode() == "step"


def test_write_through_the_ipc_path_is_accepted(monkeypatch):
    """Electron main sends both the app token and the UI secret."""
    monkeypatch.setenv("LOCAL_APP_TOKEN", "app-token")
    approval_mode.set_ui_secret(UI_SECRET)
    with _client() as client:
        wrong_token = _flip(client, "auto", **{"X-Session-Token": "nope"})
        assert wrong_token.status_code == 401
        ok = _flip(client, "auto", **{"X-Session-Token": "app-token"})
        assert ok.status_code == 200
        assert ok.json()["mode"] == "auto" and ok.json()["previous"] == "step"
        bad_mode = _flip(client, "plan", **{"X-Session-Token": "app-token"})
        assert bad_mode.status_code == 400
    assert approval_mode.current_mode() == "auto"


# ── persistence ──────────────────────────────────────────────────────────────

def test_fresh_install_starts_in_auto_without_storing_it(tmp_path):
    from database import DatabaseManager

    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(db)
    assert approval_mode.current_mode() == "auto"
    # Not written: the renderer's one-time legacy migration keys off this.
    assert approval_mode.is_stored() is False
    assert db.get_setting("approval_mode") is None


def test_mode_is_persisted_across_restarts(tmp_path):
    from database import DatabaseManager

    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    approval_mode.bind_store(db)

    approval_mode.set_mode("auto", source="test")
    approval_mode._reset_for_tests()
    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(DatabaseManager(db_path=str(tmp_path / "t.db")))
    assert approval_mode.current_mode() == "auto"
    assert approval_mode.is_stored() is True


def test_explicit_step_survives_a_restart_despite_the_auto_default(tmp_path):
    from database import DatabaseManager

    approval_mode.set_ui_secret(UI_SECRET)
    approval_mode.bind_store(DatabaseManager(db_path=str(tmp_path / "t.db")))
    assert approval_mode.current_mode() == "auto"
    approval_mode.set_mode("step", source="test")
    approval_mode._reset_for_tests()
    approval_mode.bind_store(DatabaseManager(db_path=str(tmp_path / "t.db")))
    assert approval_mode.current_mode() == "step"
    assert approval_mode.is_stored() is True


class _FreshStore:
    def __init__(self):
        self.rows = {}

    def get_setting(self, key):
        return self.rows.get(key)

    def set_setting(self, key, value):
        self.rows[key] = value


def test_fresh_install_without_a_ui_secret_starts_in_step():
    """Docker backend / uvicorn reload worker: no secret, so auto could never be left."""
    store = _FreshStore()
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "step"
    assert approval_mode.is_stored() is False
    assert store.rows == {}
    with _client() as client:
        assert client.get("/approval-mode").json() == {"mode": "step", "stored": False}


def test_fresh_install_default_follows_a_secret_read_after_bind():
    """Electron path: main binds the store at import, reads the stdin secret later."""
    approval_mode.bind_store(_FreshStore())
    assert approval_mode.current_mode() == "step"
    approval_mode.set_ui_secret(UI_SECRET)
    assert approval_mode.current_mode() == "auto"
    with _client() as client:
        resp = _flip(client, "step")
        assert resp.status_code == 200
        assert resp.json()["previous"] == "auto"
    assert approval_mode.current_mode() == "step"


def test_stored_auto_reads_as_step_without_a_ui_secret():
    """Promoted from an external audit probe (2026-09-25): a no-secret process
    (Docker, uvicorn reload worker) whose database holds "auto" stayed in auto
    with no way to switch it off. The row is left for the app with a secret."""
    store = _FreshStore()
    store.rows["approval_mode"] = "auto"
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "step"
    assert approval_mode.is_stored() is True
    assert store.rows == {"approval_mode": "auto"}
    with _client() as client:
        assert client.get("/approval-mode").json() == {"mode": "step", "stored": True}
        assert _flip(client, "auto", secret="").status_code == 403
    assert approval_mode.current_mode() == "step"


def test_stored_auto_wins_once_a_ui_secret_is_configured():
    store = _FreshStore()
    store.rows["approval_mode"] = "auto"
    approval_mode.bind_store(store)
    approval_mode.set_ui_secret(UI_SECRET)
    assert approval_mode.current_mode() == "auto"


def test_stored_step_stays_step_either_way():
    store = _FreshStore()
    store.rows["approval_mode"] = "step"
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "step"
    approval_mode.set_ui_secret(UI_SECRET)
    assert approval_mode.current_mode() == "step"


def test_real_main_picks_the_fresh_default_after_the_stdin_secret(tmp_path):
    """Imports the real main.py: import alone is what the reload worker runs,
    the stdin read is what Electron's spawn adds afterwards."""
    import subprocess
    import sys

    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    program = (
        "import os, sys; sys.path.insert(0, os.environ['APP_DIR']);"
        "import local_token_file;"
        "local_token_file._TOKEN_DIR = os.environ['TMP_DIR'];"
        "local_token_file._TOKEN_PATH = os.path.join(os.environ['TMP_DIR'], 'tok');"
        "import main;"
        "from agentic import approval_mode;"
        "print('after-import', approval_mode.current_mode());"
        "main._read_ui_secret_from_stdin();"
        "print('after-stdin', approval_mode.current_mode(), approval_mode.is_stored())"
    )
    env = dict(os.environ, APP_DIR=app_dir, TMP_DIR=str(tmp_path),
               DB_PATH=str(tmp_path / "fresh.db"), GAMACHINE_UI_SECRET_STDIN="1")
    env.pop("LOCAL_APP_TOKEN", None)
    done = subprocess.run([sys.executable, "-c", program], env=env, input="electron-secret\n",
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.strip().splitlines()
    assert "after-import step" in lines
    assert "after-stdin auto False" in lines


def test_tampered_stored_value_falls_to_step(tmp_path):
    from database import DatabaseManager

    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    db.set_setting("approval_mode", "AUTO ")
    approval_mode.bind_store(db)
    assert approval_mode.current_mode() == "step"


def test_unreadable_store_falls_to_step():
    store = MagicMock()
    store.get_setting.side_effect = OSError("database is locked")
    approval_mode.bind_store(store)
    assert approval_mode.current_mode() == "step"
    assert approval_mode.is_stored() is False


def test_ui_secret_check_is_fail_closed():
    assert approval_mode.check_ui_secret("") is False
    approval_mode.set_ui_secret(UI_SECRET)
    assert approval_mode.check_ui_secret(UI_SECRET) is True
    assert approval_mode.check_ui_secret("ğ" * 5) is False


# ── agent paths read the backend value ───────────────────────────────────────

def _chat_db():
    db = MagicMock()
    db.get_ai_config.return_value = ("claude", "claude-opus-5", None, None)
    db.get_api_key.return_value = ""
    db.get_last_workspace.return_value = ""
    db.get_memory.return_value = ""
    db.get_conversation_messages.return_value = []
    db.get_cli_session.return_value = None
    return db


@pytest.mark.parametrize("global_mode,requested", [("step", "auto"), ("auto", "step")])
def test_chat_stream_uses_the_global_mode_not_the_request_field(global_mode, requested):
    approval_mode.set_mode(global_mode, source="test")
    seen = {}

    class _Runner:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def run(self, message):
            yield MagicMock(type="done", data={}, to_sse=lambda: "data: {}\n\n")

    # The per-user chat rate limit is process-global; spending it here would
    # push unrelated later tests into 429.
    with patch("routes.conversation_routes.AgentRunner", _Runner),             patch("routes.conversation_routes._check_chat_rate_limit"), _client(_chat_db()) as client:
        resp = client.post("/chat-stream", json={"conversation_id": 1, "message": "hi", "user_id": 1,
                                          "generation_mode": requested},
                           headers={"X-Session-Token": ""})
    assert resp.status_code == 200, resp.text
    assert seen["generation_mode"] == global_mode


def test_cloud_api_cards_follow_the_global_mode(tmp_path):
    from agentic.agent_runner import AgentRunner

    runner = AgentRunner.__new__(AgentRunner)
    runner.workspace_path = str(tmp_path)
    assert runner._approval_prompt("delete_file", {"file_path": "Assets/X.cs"})
    approval_mode.set_mode("auto", source="test")
    assert runner._approval_prompt("delete_file", {"file_path": "Assets/X.cs"}) is None
    assert runner._approval_prompt("run_command", {"command": "rm Assets/X.cs"}) is None


def test_flip_updates_live_cli_sessions():
    from providers import claude_sdk_session, codex_session

    claude = MagicMock(auto_approve=True)
    codex = MagicMock(auto_approve=True)
    claude_sdk_session._SESSIONS[91] = claude
    codex_session._SESSIONS[92] = codex
    try:
        approval_mode.set_mode("step", source="test")
        assert claude.auto_approve is False and codex.auto_approve is False
        approval_mode.set_mode("auto", source="test")
        assert claude.auto_approve is True and codex.auto_approve is True
    finally:
        claude_sdk_session._SESSIONS.pop(91, None)
        codex_session._SESSIONS.pop(92, None)
