"""Regression coverage for computer-use approval waits and grant cleanup."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from computer_use import platform, registration
from computer_use.platform.macos import INSTRUCTIONS
from computer_use.platform.windows import WINDOWS_INSTRUCTIONS, WindowsPlatform
from tests.test_computer_use_gate import SECRET, answer, env, pending, request


APP_TASK_SENTENCE = (
    "When the user asks you to do something in an app, do it in that app with these tools; "
    "do not answer from your own reasoning or by reading files instead, unless the user allows it."
)
UI = {"X-Gamachine-UI-Secret": SECRET}


def test_codex_timeout_outlasts_approval_card_wait(monkeypatch):
    monkeypatch.setattr(registration.settings, "enabled_now", lambda: True)
    entry = registration.codex_thread_entry(())
    assert entry["tool_timeout_sec"] == 180.0
    # approval_bridge._BEKLEME_BUTCESI is local to request_approval, not importable.
    assert entry["tool_timeout_sec"] > 150.0


@pytest.mark.parametrize("persistent", [False, True])
def test_delete_app_grant_revokes_all_matching_sessions_only(env, monkeypatch, persistent):
    windows = WindowsPlatform(SimpleNamespace(set_dpi_awareness=lambda: None, parent_pids=lambda: {}))
    monkeypatch.setattr(platform, "current", lambda: windows)
    paint = "win:c:" + chr(92) + "apps" + chr(92) + "paint.exe"
    other = "win:c:" + chr(92) + "apps" + chr(92) + "notes.exe"
    for cid in (1, 2):
        gate_id = f"paint-{cid}"
        assert request(env, gate_id, conversation_id=cid, bundle_id=paint)["status"] == "ok"
        assert answer(env, True, gate_id, remember=False)["status"] == "ok"
        assert request(env, f"before-{cid}", conversation_id=cid, bundle_id=paint)["approved"] is True
    assert request(env, "other", bundle_id=other)["status"] == "ok"
    assert answer(env, True, "other", remember=False)["status"] == "ok"
    if persistent:
        registration.settings.add_grant(env.db, paint, "Paint")

    response = env.client.delete("/computer-use/grants/" + paint.upper(), headers=UI)
    assert response.status_code == 200
    assert response.json() == {"removed": persistent}
    assert not registration.settings.is_granted(env.db, paint)
    for cid in (1, 2):
        gate_id = f"after-{cid}"
        assert request(env, gate_id, conversation_id=cid, bundle_id=paint)["status"] == "ok"
        assert gate_id in pending(env)
    assert request(env, "other-after", bundle_id=other)["approved"] is True


def test_delete_halted_chat_unblocks_unowned_batches(env, monkeypatch):
    from providers import agy_session, claude_sdk_session, codex_session, oneshot_cli

    for provider, method in (
        (agy_session, "close_session"), (claude_sdk_session, "close_session"),
        (codex_session, "close_session"), (oneshot_cli, "close_conversation_sessions"),
    ):
        monkeypatch.setattr(provider, method, AsyncMock())
    env.db.delete_conversation_family_and_sides.return_value = ([1], [])
    assert env.client.post("/computer-use/halt/1", headers=UI).status_code == 200
    assert env.client.get("/computer-use/settings").json()["halted"] == [1]
    assert env.client.post("/computer-use/batch-step", json={}).json() == {"stop": "halted"}

    deleted = env.client.delete("/conversations/1", headers={"X-Session-Token": ""})
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "success", "deleted_ids": [1]}
    env.db.delete_conversation_family_and_sides.assert_called_once_with(1)
    assert env.client.get("/computer-use/settings").json()["halted"] == []
    assert env.client.post("/computer-use/batch-step", json={}).json() == {"stop": None}


@pytest.mark.parametrize("instructions", [WINDOWS_INSTRUCTIONS, INSTRUCTIONS], ids=["windows", "macos"])
def test_instructions_end_with_app_task_sentence(instructions):
    assert instructions.endswith(" " + APP_TASK_SENTENCE)
