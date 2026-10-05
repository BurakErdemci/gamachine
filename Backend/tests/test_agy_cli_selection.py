"""Offline checks for the flag behavior measured by the architect on agy 1.2.17."""
import asyncio
import copy
import os
from unittest.mock import Mock

import pytest

from providers import agy_provider, agy_session
from providers.agy_provider import AgyProvider
from providers.cli_base import BaseCLIProvider


# Literal slugs from .delegate-runs/agy-a/agy-models.txt (5 Oct 2026).
AGY_SLUGS = {
    "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
    "gemini-3.7-flash-high", "gemini-3.7-flash-medium", "gemini-3.7-flash-low",
    "gemini-3.6-flash-high", "gemini-3.6-flash-medium", "gemini-3.6-flash-low",
    "gemini-3.1-pro-high", "gemini-3.1-pro-low",
    "claude-opus-5-5-low", "claude-opus-5-5-medium", "claude-opus-5-5-high",
    "claude-sonnet-5-5-low", "claude-sonnet-5-5-medium", "claude-sonnet-5-5-high",
    "gpt-oss-120b-medium",
}
APP_SLUGS = {
    "gemini-3.8-flash": "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low": "gemini-3.8-flash-low",
    "gemini-3.7-flash": "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium": "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low": "gemini-3.7-flash-low",
    "gemini-3.6-flash": "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium": "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low": "gemini-3.6-flash-low",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-high",
    "gemini-3.1-pro-low": "gemini-3.1-pro-low",
    "agy-claude-opus-5-5": "claude-opus-5-5-high",
    "agy-claude-sonnet-5-5": "claude-sonnet-5-5-high",
    "agy-gpt-oss-120b": "gpt-oss-120b-medium",
}
EFFORTS = ("auto", "low", "medium", "high", None, "unknown")


@pytest.mark.parametrize("effort", EFFORTS)
def test_every_picker_id_and_effort_selects_a_listed_slug(monkeypatch, effort):
    from routes import config_routes

    monkeypatch.setattr(config_routes.urllib.request, "urlopen", Mock(side_effect=OSError("offline")))
    router = config_routes.create_config_router(Mock())
    models = asyncio.run(router.list_models(None))["subscription"]
    picker_ids = {m["id"] for m in models if m["id"].startswith(("gemini-", "agy-"))}
    # Low Flash IDs remain supported saved IDs but are absent from the picker.
    saved_only = {f"gemini-{version}-flash-low" for version in ("3.8", "3.7", "3.6")}
    assert picker_ids == set(APP_SLUGS) - saved_only
    for model in APP_SLUGS:
        expected = APP_SLUGS[model]
        if model.startswith("agy-claude-"):
            tier = effort if effort in ("low", "medium", "high") else "high"
            expected = model.removeprefix("agy-") + "-" + tier
        slug = BaseCLIProvider._resolve_agy_model(model, effort)
        assert slug == expected
        assert slug in AGY_SLUGS


@pytest.mark.parametrize("model", ["unknown-model", "gemini-3.5-flash", "agy-claude-opus-4-6", ""])
def test_unknown_app_id_errors_instead_of_falling_back(model):
    with pytest.raises(ValueError, match="Unknown agy model ID"):
        AgyProvider(binary_name=model)._build_cmd("private prompt")


@pytest.mark.parametrize("model,slug", APP_SLUGS.items())
def test_chat_argv_selects_model_and_permissions_without_prompt_or_effort(monkeypatch, model, slug):
    monkeypatch.setattr(AgyProvider, "_agy_binary", staticmethod(lambda: "fake-agy"))
    provider = AgyProvider(binary_name=model)
    command = provider._build_cmd("private prompt\nsecond line", thinking_level="auto", workspace=".")
    assert command.count("--model") == 1
    assert command[command.index("--model") + 1] == slug
    assert command.count("--dangerously-skip-permissions") == 1
    assert "--effort" not in command
    assert "private prompt" not in " ".join(command)
    assert command[command.index("--input-format") + 1] == "stream-json"
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert "-p=" in command


@pytest.mark.parametrize("existing", [False, True])
def test_config_writers_leave_model_and_permission_keys_owned_by_user(monkeypatch, existing):
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    files = {}
    writes = []
    original = {"own": {"keep": True}, "trustedWorkspaces": ["existing-workspace"]}
    if existing:
        original.update(model="user-selected-model", toolPermission="request-review")

    def read(path, default=None):
        return copy.deepcopy(files.get(path, original))

    def write(path, data):
        files[path] = copy.deepcopy(data)
        writes.append((path, copy.deepcopy(data)))
        return True

    monkeypatch.setattr(agy_provider, "_read_json_config", read)
    monkeypatch.setattr(agy_provider, "_write_json_config", write)
    monkeypatch.setattr(AgyProvider, "_write_cli_env", Mock())
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", Mock(return_value=None))
    provider = AgyProvider(binary_name="agy-claude-opus-5-5")
    provider._register_mcp("launcher", "new-workspace", "backend-url")
    provider._register_agy_workspace("new-workspace")
    settings_writes = [data for path, data in writes if os.path.basename(path) == "settings.json"]
    assert len(settings_writes) == 4
    for data in settings_writes:
        assert data["own"] == original["own"]
        assert data["disabledTools"] == BaseCLIProvider._AGY_DISABLED_TOOLS
        for key in ("model", "toolPermission"):
            if existing:
                assert data[key] == original[key]
            else:
                assert key not in data
    for data in settings_writes[-2:]:
        assert data["trustedWorkspaces"] == ["existing-workspace", "new-workspace"]


@pytest.fixture
def fake_spawns(monkeypatch):
    from tests.test_agy_stream_session import FakeProcess, TURNS

    spawns = []

    async def spawn(*argv, **kwargs):
        # At most four turns per fake child; no real processes are started.
        turns = [copy.deepcopy(TURNS[0]) for _ in range(4)]
        for index, turn in enumerate(turns, 1):
            turn[-1]["result"]["num_turns"] = index
        process = FakeProcess(turns=turns)
        spawns.append((argv, process))
        return process

    monkeypatch.setattr(AgyProvider, "_agy_binary", staticmethod(lambda: "fake-agy"))
    monkeypatch.setattr(AgyProvider, "_resolve_exec", staticmethod(lambda command: command))
    monkeypatch.setattr(AgyProvider, "_write_mcp_config", Mock())
    monkeypatch.setattr(AgyProvider, "_register_agy_workspace", Mock())
    monkeypatch.setattr(AgyProvider, "_write_step_gate", Mock(return_value=True))
    monkeypatch.setattr(AgyProvider, "_stream_instructions", Mock(return_value=""))
    monkeypatch.setattr(agy_provider, "write_gate_state", Mock())
    monkeypatch.setattr(agy_session, "_sync_gate_state", Mock(return_value=True))
    monkeypatch.setattr(agy_session.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(agy_session, "_SESSIONS", {})
    monkeypatch.setattr(agy_session, "_RESUME_IDS", {})
    return spawns


@pytest.mark.parametrize("mode", ["auto", "balanced", "step", "side"])
def test_slug_change_respawns_once_and_keeps_uuid_in_every_mode(monkeypatch, fake_spawns, mode):
    from tests.test_agy_stream_session import SESSION_ID

    monkeypatch.setattr(agy_session, "_effective_gate_mode", lambda: mode)

    async def run():
        monkeypatch.setattr(BaseCLIProvider, "_AGY_LOCK", asyncio.Lock())
        session = agy_session.AgyStreamSession(71, cwd=".")
        try:
            for effort in ("low", "low", "medium", "medium"):
                events = [event async for event in session.stream(
                    "private prompt", model="agy-claude-opus-5-5", thinking_level=effort)]
                assert events[-1]["type"] == "done"
            assert len(fake_spawns) == 2
            first_argv, first = fake_spawns[0]
            second_argv, second = fake_spawns[1]
            assert first.returncode is not None
            assert second.returncode is None
            assert first_argv[first_argv.index("--model") + 1] == "claude-opus-5-5-low"
            assert second_argv[second_argv.index("--model") + 1] == "claude-opus-5-5-medium"
            assert second_argv[second_argv.index("--conversation") + 1] == SESSION_ID
            assert session.session_id == SESSION_ID
            for argv, process in fake_spawns:
                assert "--dangerously-skip-permissions" in argv
                assert "--effort" not in argv
                assert "private prompt" not in " ".join(argv)
                assert all("private prompt" in line.decode() for line in process.stdin.lines)
            assert AgyProvider._write_step_gate.call_count >= 2
        finally:
            await session.close()

    asyncio.run(run())
    assert all(process.returncode is not None for _, process in fake_spawns)


def test_one_shot_spawn_passes_both_flags_and_closes(monkeypatch, fake_spawns):
    monkeypatch.setattr(agy_session, "_effective_gate_mode", lambda: "step")

    async def run():
        monkeypatch.setattr(BaseCLIProvider, "_AGY_LOCK", asyncio.Lock())
        provider = AgyProvider(binary_name="agy-claude-sonnet-5-5")
        return [event async for event in provider.analyze_code(
            "private prompt", thinking_level="low", cwd=".")]

    events = asyncio.run(run())
    assert events[-1]["type"] == "final"
    assert len(fake_spawns) == 1
    argv, process = fake_spawns[0]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5-low"
    assert "--dangerously-skip-permissions" in argv
    assert "--effort" not in argv
    assert "private prompt" not in " ".join(argv)
    assert "private prompt" in process.stdin.lines[0].decode()
    assert process.returncode is not None
    assert not agy_session._SESSIONS
