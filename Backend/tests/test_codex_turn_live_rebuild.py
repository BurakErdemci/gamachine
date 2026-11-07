"""Codex settings-rebuild path closes a session whose turn is still streaming (running_work empty)."""
import sys, pathlib, asyncio
R = pathlib.Path(__file__).resolve().parents[2] / "Backend"
for p in (R / "tests", R / "app", R):
    sys.path.insert(0, str(p))
import test_final_fix as t
import agentic.agent_runner as ar
from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager


def test_workspace_change_does_not_close_a_streaming_codex_turn(monkeypatch):
    from computer_use import settings as cu
    from browser_use import settings as bu
    from connectors import registration as cr
    from providers import codex_session
    from providers.codex_provider import CodexProvider
    cid, closed = 992002, []

    class S:
        thread_id = session_id = "th"
        _ctx_injected = True; auto_approve = False; is_live = True
        turn_live = True            # a turn is streaming ...
        model = "gpt-5.6-codex"; effort = None   # workspace differs
        cwd = "C:\other-project"; read_only = False
        unity_mcp = computer_use = browser_use = False
        connectors = ((), "")
        def running_work(self): return []     # ... but no command/subagent is running
        def set_turn_overrides(self, **kw): pass
        async def stream(self, message, image_paths=None, **kw):
            yield {"type": "done", "session_id": "th"}

    s = S()
    async def close_session(c): closed.append(c)
    monkeypatch.setattr(codex_session, "_SESSIONS", {cid: s})
    monkeypatch.setattr(codex_session, "get_session", lambda *a, **k: s)
    monkeypatch.setattr(codex_session, "close_session", close_session)
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", lambda self, *a, **k: "")
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(cu, "enabled_now", lambda: False)
    monkeypatch.setattr(bu, "enabled_now", lambda: False)
    monkeypatch.setattr(cr, "served", lambda: ((), ""))
    monkeypatch.setattr(ar, "_DEFERRED_REASONS", {})
    monkeypatch.setattr(ar, "_LAST_SUB_PROVIDER", {cid: "codex"})
    t._sub_turn("gpt-5.6-codex", cid, "next")
    assert closed == [], f"close_session called on a session with turn_live=True: {closed}"
