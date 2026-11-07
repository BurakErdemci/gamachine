"""A live Claude session is never closed while its background tasks run, and a
model-only change switches the model in place instead of rebuilding."""
import asyncio
from types import SimpleNamespace

import pytest

from agentic import agent_runner as ar
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

CID = 987655


def _existing(tasks, set_model=None):
    async def _ok(model):
        return None
    return SimpleNamespace(
        is_live=True, _client=SimpleNamespace(set_model=set_model or _ok),
        _active_tasks=dict(tasks), model="claude-opus-5", effort=None, cwd=".",
        mcp_servers={}, read_only=False, agent_guide="g", session_id=None)


class Harness:
    def __init__(self, monkeypatch, existing, reasons):
        from agentic import mailbox
        from providers import claude_sdk_session as sdk
        from providers.cli_base import BaseCLIProvider

        self.existing, self.closed, self.kwargs, self.messages = existing, [], [], []
        self.reasons = reasons
        harness = self

        async def stream(message):
            harness.messages.append(message)
            yield {"type": "done"}

        def get_session(cid, **kwargs):
            harness.kwargs.append(kwargs)
            return SimpleNamespace(session_id=None, stream=stream)

        async def close_session(cid):
            harness.closed.append(cid)

        monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
        monkeypatch.setattr(ar, "_remove_project_mcp_json", lambda *a: None)
        monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", lambda args: args)
        monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
        monkeypatch.setattr(mailbox, "claude_server_entry", lambda cid: None)
        monkeypatch.setattr(sdk, "_SESSIONS", {CID: existing})
        monkeypatch.setattr(sdk, "get_session", get_session)
        monkeypatch.setattr(sdk, "close_session", close_session)
        monkeypatch.setattr(ar, "_oturum_yeniden_kurma_gerekceleri", lambda *a, **k: list(harness.reasons))
        monkeypatch.setattr(ar, "_DEFERRED_REASONS", {})

    def turn(self, text="hello"):
        runner = ar.AgentRunner(
            provider_type="subscription", api_key="", model_name="claude-sonnet-5",
            workspace_path=".", language="en", conversation_id=CID,
            generation_mode="step", thinking_level="off", effort_level="off")
        runner._agent_guide = "guide"

        async def run():
            return [e async for e in runner._run_claude_session(text)]

        events = asyncio.run(run())
        assert any(e.type == "done" for e in events)


def test_reasons_with_running_background_tasks_defer_the_rebuild(monkeypatch):
    h = Harness(monkeypatch, _existing({"t1": "codex exec"}), ["agent guide changed"])
    h.turn()
    assert h.closed == []
    # get_session must see the live session's own identity, or it would discard it.
    assert h.kwargs[0]["agent_guide"] == "g" and h.kwargs[0]["model"] == "claude-opus-5"
    note = "[Sistem] Ayar değişikliği (agent guide changed) arka plan işleri bitince uygulanacak."
    assert h.messages[0].startswith(note + "\n\nhello")
    h.turn("again")
    assert h.closed == [] and note not in h.messages[1]
    h.reasons = ["effort None→high"]
    h.turn("changed")
    assert "effort None→high" in h.messages[2]


def test_reasons_without_background_tasks_close_the_session(monkeypatch):
    h = Harness(monkeypatch, _existing({}), ["agent guide changed"])
    h.turn()
    assert h.closed == [CID]
    assert "arka plan işleri bitince" not in h.messages[0]


def test_model_only_change_switches_in_place(monkeypatch):
    calls = []

    async def set_model(model):
        calls.append(model)

    existing = _existing({"t1": "codex exec"}, set_model)
    h = Harness(monkeypatch, existing, ["model claude-opus-5→claude-sonnet-5"])
    h.turn()
    assert calls == ["claude-sonnet-5"] and h.closed == []
    assert existing.model == "claude-sonnet-5"
    assert "arka plan işleri bitince" not in h.messages[0]


@pytest.mark.parametrize("tasks,closes", [({}, [CID]), ({"t1": "codex exec"}, [])])
def test_failed_model_switch_falls_back(monkeypatch, tasks, closes):
    async def set_model(model):
        raise RuntimeError("boom")

    h = Harness(monkeypatch, _existing(tasks, set_model), ["model claude-opus-5→claude-sonnet-5"])
    h.turn()
    assert h.closed == closes


def test_decision_helper_is_pure():
    busy, idle = _existing({"t": "x"}), _existing({})
    assert ar._oturum_karari([], busy) == "keep"
    assert ar._oturum_karari(["model a→b"], busy) == "switch_model"
    assert ar._oturum_karari(["model a→b", "effort a→b"], busy) == "defer"
    assert ar._oturum_karari(["model a→b", "effort a→b"], idle) == "rebuild"
