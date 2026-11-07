"""After /compact on an API chat the gauge no longer shows the reading measured on the uncompacted history."""
import sys, pathlib
R = pathlib.Path(__file__).resolve().parents[2] / "Backend"
for p in (R / "tests", R / "app", R):
    sys.path.insert(0, str(p))
from collections import defaultdict
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import routes.conversation_routes as cr
import agentic.agent_runner as ar
from agentic import api_compaction
from test_compact_spares_bg import db  # fixture  # noqa: F401

H = {"X-Session-Token": ""}


def test_gauge_drops_after_compact(db, monkeypatch):
    monkeypatch.setenv("UNITYAI_ALLOW_NO_TOKEN", "1")
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))

    async def fake_summary(*a, **k):
        return "short summary"
    monkeypatch.setattr(api_compaction, "summarize", fake_summary)
    fast = FastAPI()
    fast.include_router(cr.create_conversation_router(db, {}))
    cid = db.create_conversation(1, "t")
    for i in range(10):
        db.add_message(cid, "user" if i % 2 == 0 else "assistant", f"m{i}")
    db.set_conversation_model(cid, "openai", "gpt-5")
    monkeypatch.setattr(ar, "_API_CONTEXT_READINGS", {cid: {
        "used": 180000, "window": 200000, "percent": 90.0, "model": "gpt-5", "provider": "openai"}})
    with TestClient(fast) as c:
        before = c.get(f"/conversations/{cid}/context-usage", headers=H).json()
        print("BEFORE", before); assert before["percent"] == 90, before
        r = c.post(f"/conversations/{cid}/compact", headers=H)
        assert r.status_code == 200, r.text
        after = c.get(f"/conversations/{cid}/context-usage", headers=H).json()
    assert after["percent"] < 90 and after["estimated"], f"stale pre-compaction reading still served: {after}"
