"""The /agent-bridge routes a terminal agent's MCP server calls (docs/terminal-agents.md).

Real database, real phone Dispatcher; the desktop channel is a recording fake so
the frame the open window would receive can be read. Nothing touches the real
~/.unity_architect_ai: the backend-url file test patches the token paths.
"""
import os
import sqlite3
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

import local_token_file  # noqa: E402
from agentic import turn_events  # noqa: E402
from database import DatabaseManager  # noqa: E402
from remote import agent_bridge, rpc, ui_dispatch  # noqa: E402
from remote.rpc import Dispatcher  # noqa: E402

H = {"X-Session-Token": "t"}


class FakeChannel:
    def __init__(self):
        self.frames = []

    def publish(self, frame):
        self.frames.append(frame)
        return True


class Bridge:
    list_models = None
    plan_locked = None
    stop_chat = None
    list_slash_commands = None
    app = None

    def __init__(self, db):
        self.db = db
        self.rpc = Dispatcher(self)


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Every test's database numbers its chats from 1, and the ring is per process.
    turn_events.RING.reset()
    monkeypatch.setenv("LOCAL_APP_TOKEN", "t")
    db = DatabaseManager(db_path=str(tmp_path / "bridge.db"))
    channel = FakeChannel()
    monkeypatch.setattr(rpc, "CHANNEL", channel)
    monkeypatch.setattr(agent_bridge, "CHANNEL", channel)
    monkeypatch.setattr(ui_dispatch, "desktop_renderer_ready", lambda: True)
    bridge = Bridge(db)

    async def models(user_id):
        return {"subscription": [{"id": "m1", "provider": "subscription"}]}

    bridge.list_models = models
    app = FastAPI()
    app.include_router(agent_bridge.create_agent_bridge_router(db, bridge))
    client = TestClient(app)
    client.db, client.channel, client.bridge = db, channel, bridge
    yield client
    turn_events.RING.reset()


def enable(client):
    assert client.put("/agent-bridge/settings", headers=H, json={"enabled": True}).json() == {"enabled": True}


GATED = [
    ("get", "/agent-bridge/chats", None),
    ("get", "/agent-bridge/models", None),
    ("post", "/agent-bridge/chats", {"title": "x"}),
    ("put", "/agent-bridge/chats/1/model", {"provider_type": "a", "model_name": "b"}),
    ("post", "/agent-bridge/chats/1/messages", {"text": "hi"}),
    ("get", "/agent-bridge/chats/1/events?since=0", None),
    ("get", "/agent-bridge/chats/1/messages", None),
]


@pytest.mark.parametrize("method, path, body", GATED)
def test_every_route_but_settings_refuses_while_disabled(env, method, path, body):
    r = getattr(env, method)(path, headers=H, **({"json": body} if body is not None else {}))
    assert r.status_code == 403
    assert r.json()["detail"] == "agent_bridge_disabled"
    assert env.channel.frames == []


@pytest.mark.parametrize("method, path, body", GATED)
def test_a_wrong_token_is_refused_before_anything(env, method, path, body):
    enable(env)
    r = getattr(env, method)(path, headers={"X-Session-Token": "nope"},
                             **({"json": body} if body is not None else {}))
    assert r.status_code in (401, 403) and r.json().get("detail") != "agent_bridge_disabled"


def test_settings_round_trip_and_server_command(env):
    first = env.get("/agent-bridge/settings", headers=H).json()
    assert first["enabled"] is False
    assert first["server_command"] == agent_bridge.server_argv()
    assert first["server_command"][-1] == "agent-mcp-server"
    enable(env)
    assert env.get("/agent-bridge/settings", headers=H).json()["enabled"] is True
    assert env.put("/agent-bridge/settings", headers=H, json={"enabled": "yes"}).status_code == 400
    assert env.put("/agent-bridge/settings", headers=H, json={"enabled": False}).json() == {"enabled": False}
    assert env.get("/agent-bridge/settings", headers=H).json()["enabled"] is False


def test_unreadable_setting_means_off(env):
    class Broken:
        def get_setting(self, key):
            raise RuntimeError("db gone")

    assert agent_bridge.enabled(Broken()) is False


def test_chats_list_has_the_agent_fields(env):
    enable(env)
    conv = env.db.create_conversation(1, "Arena")
    env.db.set_conversation_model(conv, "subscription", "claude-opus-5")
    chats = env.get("/agent-bridge/chats", headers=H).json()["chats"]
    row = next(c for c in chats if c["chat_id"] == conv)
    assert row["title"] == "Arena"
    assert row["provider_type"] == "subscription" and row["model_name"] == "claude-opus-5"
    assert row["status"] == "idle" and row["busy"] is False
    assert {"provider", "last_activity"} <= set(row)


def test_models_come_from_the_phones_list_models(env):
    enable(env)
    assert env.get("/agent-bridge/models", headers=H).json() == {
        "subscription": [{"id": "m1", "provider": "subscription"}]}


def test_new_chat_binds_the_last_workspace_and_publishes_conversation_created(env, tmp_path):
    enable(env)
    env.db.save_workspace(1, str(tmp_path))
    r = env.post("/agent-bridge/chats", headers=H, json={"title": "Agent chat", "client": "Codex"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["chat_id"], int) and body["title"] == "Agent chat"
    assert {"provider_type", "model_name"} <= set(body) and "model_error" not in body
    with sqlite3.connect(env.db.db_path) as conn:
        stored = conn.execute("SELECT workspace FROM conversations WHERE id = ?", (body["chat_id"],)).fetchone()
    assert stored[0] == str(tmp_path)
    created = [f for f in env.channel.frames if f["type"] == "conversation_created"]
    assert len(created) == 1 and created[0]["conversation_id"] == body["chat_id"]
    assert isinstance(created[0]["at"], int)


def test_new_chat_keeps_the_chat_when_the_model_is_refused(env):
    enable(env)
    r = env.post("/agent-bridge/chats", headers=H, json={"provider_type": "nope", "model_name": "x"})
    assert r.status_code == 200 and r.json()["model_error"]
    assert env.post("/agent-bridge/chats", headers=H, json={"title": 5}).status_code == 400


@pytest.mark.parametrize("given,expected", [
    ("\u202eEvil\x07\nTitle  x", "Evil Title x"),
    ("   ", "Yeni Sohbet"),
    ("", "Yeni Sohbet"),
    ("\x07\u202e", "Yeni Sohbet"),
    ("  a \t b  ", "a b"),
    ("t" * 201, "t" * 200),
])
def test_new_chat_title_is_cleaned(env, given, expected):
    enable(env)
    r = env.post("/agent-bridge/chats", headers=H, json={"title": given})
    assert r.status_code == 200 and r.json()["title"] == expected


def test_set_model_error_is_a_400_with_the_code(env):
    enable(env)
    conv = env.db.create_conversation(1, "c")
    r = env.put(f"/agent-bridge/chats/{conv}/model", headers=H,
                json={"provider_type": "nope", "model_name": "x"})
    assert r.status_code == 400 and r.json()["error"]
    assert env.put("/agent-bridge/chats/99999/model", headers=H,
                   json={"provider_type": "a", "model_name": "b"}).status_code == 404


def test_message_returns_the_snapshot_and_the_frame_is_marked_agent(env):
    enable(env)
    conv = env.db.create_conversation(1, "c")
    turn_events.append(conv, "turn_start", provider="claude", model="m", origin="user")
    before = turn_events.since(conv, 0)
    r = env.post(f"/agent-bridge/chats/{conv}/messages", headers=H,
                 json={"text": "hello", "client": "Cl‮aude\x00 Code"})
    assert r.status_code == 200
    assert r.json() == {"status": "accepted", "since_seq": before["last_seq"], "epoch": before["epoch"],
                        "busy_at_send": True}
    frame = env.channel.frames[-1]
    assert frame["type"] == rpc.FRAME_TYPE and frame["source"] == "agent"
    assert frame["device_name"] == "Claude Code" and frame["device_id"] == "agent:Claude Code"
    assert frame["conversation_id"] == conv and frame["text"] == "hello"


def test_message_without_a_window_is_desktop_not_ready_and_bad_input_is_400_404(env, monkeypatch):
    enable(env)
    conv = env.db.create_conversation(1, "c")
    monkeypatch.setattr(ui_dispatch, "desktop_renderer_ready", lambda: False)
    r = env.post(f"/agent-bridge/chats/{conv}/messages", headers=H, json={"text": "hi"})
    assert r.json()["status"] == "desktop_not_ready" and r.json()["busy_at_send"] is False
    assert env.post(f"/agent-bridge/chats/{conv}/messages", headers=H, json={"text": " "}).status_code == 400
    assert env.post("/agent-bridge/chats/99999/messages", headers=H, json={"text": "hi"}).status_code == 404


def test_events_and_messages_routes(env):
    enable(env)
    conv = env.db.create_conversation(1, "c")
    env.db.add_message(conv, "user", "q")
    env.db.add_message(conv, "assistant", "a", provider="claude", model="m")
    turn_events.append(conv, "turn_start", provider="claude", model="m", origin="user")
    got = env.get(f"/agent-bridge/chats/{conv}/events?since=0", headers=H).json()
    assert got["last_seq"] == 1 and got["events"][0]["kind"] == "turn_start"
    assert env.get(f"/agent-bridge/chats/{conv}/events?since=-1", headers=H).status_code == 422
    msgs = env.get(f"/agent-bridge/chats/{conv}/messages?limit=1", headers=H).json()["messages"]
    assert [m["role"] for m in msgs] == ["assistant"]
    assert env.get(f"/agent-bridge/chats/{conv}/messages?limit=51", headers=H).status_code == 422
    for path in ("events?since=0", "messages"):
        r = env.get(f"/agent-bridge/chats/99999/{path}", headers=H)
        assert r.status_code == 404 and r.json() == {"detail": "unknown_chat"}


def test_clean_client_strips_bidi_and_control_characters_and_caps_the_length():
    assert agent_bridge.clean_client("Co‮dex⁦\x00\x1b[31m") == "Codex[31m"
    assert agent_bridge.clean_client("a \n\t  b") == "a b"
    assert len(agent_bridge.clean_client("x" * 500)) == 48
    for empty in (None, 5, "", "  ‮\x07 "):
        assert agent_bridge.clean_client(empty) == "Terminal"


def test_the_module_never_names_the_approval_paths():
    source = open(agent_bridge.__file__, encoding="utf-8").read()
    server = open(os.path.join(os.path.dirname(agent_bridge.__file__), "..", "unity_ai_mcp", "agent_server.py"),
                  encoding="utf-8").read()
    for text in (source, server):
        for banned in ("answer_card", "set_approval_mode", "halt_computer", "apply_approval_mode",
                       "command-approval", "question-answer", "mcp-approval-respond"):
            assert banned not in text


async def test_a_phone_sessions_frame_keeps_source_phone(env):
    from types import SimpleNamespace
    conv = env.db.create_conversation(1, "c")
    phone = SimpleNamespace(device=SimpleNamespace(device_id="d1", name="Pixel"), device_label="phone:Pixel")
    out = await env.bridge.rpc.send_message(phone, {"chat_id": conv, "text": "hi"}, None)
    assert out == {"status": "accepted"}
    frame = env.channel.frames[-1]
    assert (frame["source"], frame["device_id"], frame["device_name"]) == ("phone", "d1", "Pixel")


def test_backend_url_file_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(tmp_path / "home"))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(tmp_path / "home" / "local-app-token"))
    assert local_token_file.backend_url_path() == str(tmp_path / "home" / "backend-url")
    assert local_token_file.read_backend_url() == ""
    assert local_token_file.write_backend_url("http://127.0.0.1:5123") is True
    assert local_token_file.read_backend_url() == "http://127.0.0.1:5123"
    assert local_token_file.write_backend_url("http://127.0.0.1:6000") is True
    assert local_token_file.read_backend_url() == "http://127.0.0.1:6000"
    assert local_token_file.write_backend_url("") is True
    assert not os.path.exists(local_token_file.backend_url_path())
    assert local_token_file.write_backend_url("") is True
