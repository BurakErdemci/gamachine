"""/api/command routes a command that names a project by the editors' real registered paths."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import fastmcp
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import config as config_module  # noqa: E402
from transport import approval_gate  # noqa: E402
from transport.plugin_hub import PluginHub  # noqa: E402
from transport.plugin_registry import PluginRegistry  # noqa: E402

SECRET = "route-local-secret"
PRODUCT_TOKEN = "route-product-token"
HEADERS = {"X-API-Key": SECRET, "X-UnityAI-Maintenance": PRODUCT_TOKEN}
BODY = {"type": "gm_editor_rename", "params": {"id": 1, "name": "x"}}


# tests/integration/conftest.py swaps fastmcp for stubs process-wide; the route
# needs the real library, so in such a session it runs in a clean interpreter.
_STUBBED = "_Dummy" in type(fastmcp.FastMCP).__name__ or fastmcp.FastMCP.__name__.startswith("_Dummy")


def test_route_in_a_clean_interpreter_when_fastmcp_is_stubbed():
    if not _STUBBED:
        pytest.skip("runs in this interpreter")
    env = {k: v for k, v in os.environ.items() if k != "LOCAL_APP_TOKEN"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", __file__],
        env=env, capture_output=True, text=True, timeout=120,
        cwd=str(Path(__file__).resolve().parents[1]))
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-500:]


@pytest.fixture
def route(monkeypatch):
    if _STUBBED:
        pytest.skip("fastmcp is stubbed in this session")
    original = fastmcp.FastMCP.__init__

    def patched(self, *args, **kwargs):
        # The installed FastMCP rejects these (known environment issue).
        kwargs.pop("cache_ttl", None)
        kwargs.pop("cache_scope", None)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(fastmcp.FastMCP, "__init__", patched)
    import main

    monkeypatch.setenv("LOCAL_APP_TOKEN", PRODUCT_TOKEN)
    monkeypatch.setattr(config_module.config, "http_remote_hosted", False)
    monkeypatch.setattr(config_module.config, "local_api_token", SECRET)
    registry = PluginRegistry()
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    sent = []

    async def send_command(session_id, command_type, params):
        sent.append(session_id)
        return {"success": True}

    async def gate(tool_name, params, hedef=None, conversation_id=None):
        return None

    monkeypatch.setattr(PluginHub, "send_command", send_command)
    monkeypatch.setattr(approval_gate, "kapiyi_gec", gate)
    client = TestClient(main.create_mcp_server(False).http_app())
    return client, registry, sent


def _register(registry, *editors):
    for sid, name, path in editors:
        asyncio.run(registry.register(sid, name, f"hash{name}", "6000.0", path))


def test_one_editor_with_the_path_gets_the_command(route):
    client, registry, sent = route
    _register(registry, ("sA", "GameA", "/projects/GameA"))
    response = client.post("/api/command", headers=HEADERS, json={**BODY, "project_path": "/projects/GameA"})
    assert response.status_code == 200
    assert sent == ["sA"]


def test_the_matching_editor_is_chosen_not_the_first(route):
    client, registry, sent = route
    _register(registry, ("sB", "GameB", "/projects/GameB"), ("sA", "GameA", "/projects/GameA"))
    response = client.post("/api/command", headers=HEADERS, json={**BODY, "project_path": "/projects/GameA"})
    assert response.status_code == 200
    assert sent == ["sA"]


def test_a_project_open_nowhere_is_refused_and_nothing_is_sent(route):
    client, registry, sent = route
    _register(registry, ("sB", "GameB", "/projects/GameB"))
    response = client.post("/api/command", headers=HEADERS, json={**BODY, "project_path": "/projects/GameA"})
    assert response.status_code == 409
    assert response.json()["error"] == "Unity'de bu proje açık değil: GameA."
    assert sent == []
