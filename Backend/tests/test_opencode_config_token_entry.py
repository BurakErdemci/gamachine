"""The unityai entry in opencode.json carries no approval token.

An older build wrote a per-turn approval token and an auto-approve flag into
the entry's `environment`. The file is readable by the model, so rewriting the
entry must drop both keys and leave only the URL and workspace.
"""
import json

import pytest

MODEL = "opencode:opencode/ling-3.0-flash-fin-free"
STALE_KEYS = ("UNITYAI_APPROVAL_TURN_TOKEN", "UNITYAI_AUTO_APPROVE")


@pytest.fixture(autouse=True)
def _isolated_opencode(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    from unity_ai_mcp import unity_mcp_manager as um
    monkeypatch.setattr(um.unity_mcp_manager, "mcp_url", lambda: None)
    monkeypatch.setattr(um.unity_mcp_manager, "is_running", lambda: False)
    from providers import opencode_provider as op
    monkeypatch.setattr(op, "bash_shadow_dir", lambda: str(tmp_path / "shadow"))
    monkeypatch.setattr(op, "resolve_opencode_cmd", lambda: ["opencode"])
    monkeypatch.setattr(op, "_opencode_version", lambda base: (1, 18, 25))


def test_rewriting_the_unityai_entry_drops_stale_approval_keys(tmp_path):
    from providers.opencode_provider import OpenCodeProvider
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "opencode.json").write_text(json.dumps({
        "mcp": {"unityai": {
            "type": "local",
            "command": ["old-launcher"],
            "environment": {
                "UNITYAI_URL": "http://old",
                "WORKSPACE": "old",
                "UNITYAI_APPROVAL_TURN_TOKEN": "stale-token",
                "UNITYAI_AUTO_APPROVE": "1",
            },
            "enabled": True,
        }},
    }), encoding="utf-8")

    OpenCodeProvider(MODEL)._register_mcp("unityai-launcher", str(ws), "http://localhost:8000")

    raw = (ws / "opencode.json").read_text(encoding="utf-8")
    env = json.loads(raw)["mcp"]["unityai"]["environment"]
    assert set(env) == {"UNITYAI_URL", "WORKSPACE"}
    for key in STALE_KEYS:
        assert key not in raw
    assert "stale-token" not in raw
