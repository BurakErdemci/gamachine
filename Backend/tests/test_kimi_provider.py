"""Kimi CLI, Moonshot ve yeni Gemini model entegrasyonu birim testleri."""
import os
import sys
import asyncio
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


class TestManagerRouting(unittest.TestCase):
    def test_kimi_subscription_routing(self):
        from providers.kimi_provider import KimiProvider
        from providers.manager import AIProviderManager

        for model_name in ("kimi-k3", "kimi-k2.7-code"):
            provider = AIProviderManager.get_provider({
                "provider_type": "subscription",
                "model_name": model_name,
            })
            self.assertIsInstance(provider, KimiProvider)

    def test_moonshot_endpoint(self):
        from providers.api_providers import OpenAICompatibleProvider
        from providers.manager import AIProviderManager

        provider = AIProviderManager.get_provider({
            "provider_type": "moonshot",
            "api_key": "x",
            "model_name": "kimi-k3",
        })
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.base_url, "https://api.moonshot.ai/v1")


class TestApiProviders(unittest.TestCase):
    def test_kimi_detection(self):
        from providers.api_providers import OpenAICompatibleProvider

        k3 = OpenAICompatibleProvider(
            api_key="x",
            base_url="https://api.moonshot.ai/v1",
            model_name="kimi-k3",
        )
        self.assertTrue(k3._is_kimi())
        self.assertTrue(k3._is_kimi_k3())

        k2 = OpenAICompatibleProvider(
            api_key="x",
            base_url="https://api.moonshot.ai/v1",
            model_name="kimi-k2.6",
        )
        self.assertTrue(k2._is_kimi())
        self.assertFalse(k2._is_kimi_k3())

    def test_gemini_model_normalization(self):
        from providers.api_providers import GeminiProvider

        self.assertEqual(
            GeminiProvider(api_key="x", model_name="gemini-3.6-flash").model_id,
            "gemini-3.6-flash",
        )
        self.assertEqual(
            GeminiProvider(api_key="x", model_name="gemini-3.5-flash-lite").model_id,
            "gemini-3.5-flash-lite",
        )


class TestKimiProvider(unittest.TestCase):
    def test_build_cmd(self):
        """The command uses only options kimi 2.5.3 accepts in prompt mode
        (inputs/help.txt; `--print`, `--verbose`, `-w` and `--mcp-config-file`
        were rejected as unknown options) and ends with the prompt."""
        from providers.kimi_provider import KimiProvider

        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.dict(os.environ, {"KIMI_CODE_HOME": tmpdir}), \
             patch(
                 "unity_ai_mcp.unity_mcp_manager.unity_mcp_manager.is_running",
                 return_value=False,
             ), \
             patch.object(KimiProvider, "_ensure_exec"):
            cmd = KimiProvider(binary_name="kimi-code/k3")._build_cmd(
                "HELLO_PROMPT",
                workspace=tmpdir,
            )

        self.assertEqual(os.path.basename(cmd[0]).lower().split(".")[0], "kimi")
        self.assertEqual(cmd[1:5], ["--output-format", "stream-json", "-m", "kimi-code/k3"])
        self.assertEqual(cmd[5], "-p")
        self.assertEqual(len(cmd), 7)
        prompt = cmd[6]
        self.assertTrue(prompt.endswith("HELLO_PROMPT"))
        self.assertFalse(prompt.startswith("/"))
        self.assertIn("mcp__unityai__save_file", prompt)

    def test_write_kimi_permissions_is_idempotent(self):
        from providers.kimi_provider import KimiProvider

        existing = '[providers.kimi]\napi_key="x"\n'
        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.dict(os.environ, {"KIMI_CODE_HOME": tmpdir}):
            config_path = os.path.join(tmpdir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as config_file:
                config_file.write(existing)

            provider = KimiProvider(binary_name="kimi-k3")
            provider._write_kimi_permissions()
            provider._write_kimi_permissions()

            with open(config_path, "r", encoding="utf-8") as config_file:
                content = config_file.read()

        self.assertIn(existing, content)
        self.assertEqual(content.count(provider._KIMI_MANAGED_MARKER), 1)
        self.assertIn('decision = "deny"', content)
        self.assertIn('pattern = "Write"', content)
        self.assertIn('pattern = "mcp__unityai__*"', content)

    def test_agent_runner_routes_kimi_subscription_to_ephemeral_cli(self):
        from agentic.agent_runner import AgentEvent, AgentRunner

        runner = AgentRunner(
            provider_type="subscription",
            api_key="",
            model_name="kimi-k3",
            workspace_path=os.getcwd(),
            conversation_id=996,
        )
        routed = []

        async def fake_oneshot(message, cli_key):
            routed.append((message, cli_key))
            yield AgentEvent("done", {"iterations": 1})

        runner._run_oneshot_cli_session = fake_oneshot

        async def collect():
            return [event async for event in runner.run("merhaba")]

        events = asyncio.run(collect())

        self.assertEqual(routed, [("merhaba", "kimi")])
        self.assertEqual(events[-1].type, "done")

    def test_kimi_cli_gets_handoff_context_on_every_stateless_turn(self):
        from agentic.agent_runner import AgentRunner
        from providers.oneshot_cli import _SESSIONS

        class FakeKimiProvider:
            resume_session_id = None
            prompts = []

            async def analyze_code(self, prompt, **kwargs):
                self.prompts.append(prompt)
                yield {"type": "final", "text": "tamam"}

        _SESSIONS.clear()
        provider = FakeKimiProvider()
        runner = AgentRunner(
            provider_type="subscription",
            api_key="",
            model_name="kimi-k3",
            workspace_path=os.getcwd(),
            context="CONTEXT_MARKER",
            conversation_id=997,
        )

        async def run_turn(message):
            return [
                event
                async for event in runner._run_oneshot_cli_session(message, "kimi")
            ]

        with patch(
            "ai_providers.AIProviderManager.get_provider",
            return_value=provider,
        ):
            asyncio.run(run_turn("ilk"))
            asyncio.run(run_turn("ikinci"))

        self.assertEqual(len(provider.prompts), 2)
        self.assertTrue(all("CONTEXT_MARKER" in p for p in provider.prompts))
        _SESSIONS.clear()


# ── Kimi Code 2.5.3 behaviour through the real analyze_code ──────────────────
# The CLI is the boundary: a stand-in process prints the lines kimi writes and
# exits with its code; everything on our side (spawn, env, parsing, error
# handling) runs for real.

import json  # noqa: E402

import pytest  # noqa: E402

# Lines in the shapes kimi 2.5.3's PromptJsonWriter writes (prompt-render.ts in
# the kimi.exe bundle; doc reference/kimi-command "stream-json"). The first line
# is verbatim from the installed CLI (measured 15 Nov 2026).
_TURN_LINES = [
    '{"role":"meta","type":"system.version","version":"2.5.3"}',
    json.dumps({"role": "assistant", "content": "Sahneye bakıyorum.",
                "tool_calls": [{"type": "function", "id": "call_1", "function": {
                    "name": "mcp__unityai__read_file",
                    "arguments": json.dumps({"path": "Assets/Player.cs"})}}]}),
    json.dumps({"role": "tool", "tool_call_id": "call_1", "content": "public class Player {}"}),
    json.dumps({"role": "meta", "type": "turn.step.retrying", "failed_attempt": 1,
                "next_attempt": 2, "max_attempts": 3, "delay_ms": 500,
                "error_name": "APIError", "error_message": "overloaded"}),
    json.dumps({"role": "assistant", "content": "Player sınıfı boş."}),
    # Not written by 2.5.3 for a plain prompt, or not JSON objects: ignored.
    json.dumps({"type": "goal.summary", "goalId": None, "status": None}),
    json.dumps({"role": "meta", "type": "some.future.meta"}),
    "[1, 2]",
    '"bare string"',
    json.dumps({"role": "meta", "type": "session.resume_hint", "session_id": "sess-42",
                "command": "kimi -r sess-42",
                "content": "To resume this session: kimi -r sess-42"}),
]

# Measured 15 Nov 2026: kimi 2.5.3 with an empty KIMI_CODE_HOME (no login) and
# `-m kimi-code/k3` printed the version line on stdout, this on stderr, rc 1.
_NOT_CONFIGURED_STDERR = [
    'error: failed to run prompt: Model "kimi-code/k3" is not configured in config.toml.',
    "See log: C:\\kimi-home\\logs\\kimi-code.log",
]


@pytest.fixture
def run_kimi(tmp_path, monkeypatch):
    """Runs one KimiProvider turn against a stand-in CLI; returns its events."""
    from providers.kimi_provider import KimiProvider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "kimi-home"))
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda: None)
    ws = tmp_path / "ws"
    ws.mkdir()
    real_build = KimiProvider._build_cmd

    def run(stdout_lines, stderr_lines=(), rc=0, model="kimi-code/k3"):
        script = tmp_path / "fake_kimi.py"
        script.write_text(
            "import os, sys\n"
            f"sys.stdout.write({json.dumps(chr(10).join(stdout_lines) + chr(10) if stdout_lines else '')})\n"
            f"sys.stderr.write({json.dumps(chr(10).join(stderr_lines) + chr(10) if stderr_lines else '')})\n"
            "sys.stdout.flush(); sys.stderr.flush()\n"
            f"os._exit({rc})\n",
            encoding="utf-8")

        def build(self, *args, **kwargs):
            real_build(self, *args, **kwargs)
            return [sys.executable, str(script)]

        monkeypatch.setattr(KimiProvider, "_build_cmd", build)
        provider = KimiProvider(binary_name=model)
        monkeypatch.setattr(provider, "_launcher_path", lambda name: "test-launcher")
        monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)

        async def collect():
            return [ev async for ev in provider.analyze_code("hi", cwd=str(ws))]

        return asyncio.run(collect())

    return run


def test_a_turn_streams_text_tools_and_session_and_ignores_unknown_lines(run_kimi):
    events = run_kimi(_TURN_LINES)

    assert not [e for e in events if e["type"] == "error"], events
    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    assert deltas == "Sahneye bakıyorum.\n\nPlayer sınıfı boş."
    assert events[-1] == {"type": "final", "text": "Sahneye bakıyorum.\n\nPlayer sınıfı boş."}
    thinking = [e["text"] for e in events if e["type"] == "thinking"]
    assert thinking[0] == "🔧 `mcp__unityai__read_file` → `Assets/Player.cs`"
    assert thinking[1] == "↩ public class Player {}"
    assert thinking[2].startswith("↻") and "2/3" in thinking[2] and "overloaded" in thinking[2]
    assert len(thinking) == 3
    assert [e for e in events if e["type"] == "session_meta"] == [
        {"type": "session_meta", "session_id": "sess-42"}]
    # The meta version line, the goal summary and the non-object lines left no text.
    assert "system.version" not in deltas and "[1, 2]" not in deltas


def test_an_unconfigured_model_gives_a_clear_error_naming_the_alias(run_kimi):
    events = run_kimi(['{"role":"meta","type":"system.version","version":"2.5.3"}'],
                      _NOT_CONFIGURED_STDERR, rc=1, model="kimi-k3")

    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1, events
    assert errors[0]["code"] == "kimi_model_unavailable"
    assert "kimi-code/k3" in errors[0]["content"] and "kimi login" in errors[0]["content"]
    assert errors[0]["retryable"] is False
    assert events[-1]["type"] == "final"


def test_a_missing_login_says_how_to_log_in(run_kimi):
    events = run_kimi(['{"role":"meta","type":"system.version","version":"2.5.3"}'], [
        "error: failed to run prompt: No model configured. Run `kimi` and use /login to "
        "sign in, then retry; or set default_model in config.toml.",
    ], rc=1)

    errors = [e for e in events if e["type"] == "error"]
    assert [e["code"] for e in errors] == ["kimi_not_logged_in"], events
    assert "kimi login" in errors[0]["content"]


@pytest.mark.skipif(sys.platform != "win32", reason="0xC0000409 is a Windows exit status")
def test_a_windows_crash_exit_is_a_clear_error_not_a_raw_code(run_kimi):
    # -1073740791 as a C int is the 0xC0000409 status kimi 2.5.3 exited with.
    events = run_kimi(['{"role":"meta","type":"system.version","version":"2.5.3"}'],
                      ["fatal"], rc=-1073740791)

    errors = [e for e in events if e["type"] == "error"]
    assert [e["code"] for e in errors] == ["kimi_crashed"], events
    assert "0xC0000409" in errors[0]["content"] and errors[0]["reset_session"] is True


def test_a_session_occupied_line_is_reported_once(run_kimi):
    events = run_kimi([
        '{"role":"meta","type":"system.version","version":"2.5.3"}',
        json.dumps({"type": "error", "code": "SESSION_OCCUPIED",
                    "message": "Session is in use by another process.", "details": None}),
    ], rc=4)

    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1, events
    assert "Session is in use by another process." in errors[0]["content"]


@pytest.mark.parametrize("saved, alias", [
    ("kimi-k3", "kimi-code/k3"),
    ("kimi-k2.7-code", "kimi-code/kimi-for-coding"),
    ("kimi-code/k3.1-pro", "kimi-code/k3.1-pro"),
])
def test_saved_model_ids_start_the_cli_with_a_kimi_code_alias(saved, alias, tmp_path, monkeypatch):
    from providers.kimi_provider import KimiProvider
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    monkeypatch.setattr(unity_mcp_manager, "is_running", lambda: False)
    provider = KimiProvider(binary_name=saved)
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None)
    cmd = provider._build_cmd("HELLO", workspace=str(tmp_path))
    assert cmd[cmd.index("-m") + 1] == alias


def test_every_picker_kimi_model_is_a_cli_alias_run_by_the_kimi_provider():
    """The picker offers only ids the CLI's -m takes as is (kimi-code/<model>),
    and each one still routes to the Kimi Code CLI."""
    from unittest.mock import MagicMock
    from providers.families import kimi_cli_model, subscription_family
    from routes.config_routes import create_config_router

    with patch("providers.model_catalog.list_models", return_value=None), \
         patch("providers.model_catalog.openrouter_catalog", return_value={}), \
         patch("urllib.request.urlopen", side_effect=OSError("closed")):
        catalog = asyncio.run(create_config_router(MagicMock()).list_models(None))
    ids = [m["id"] for m in catalog["subscription"] if subscription_family(m["id"]) == "kimi"]
    assert "kimi-code/k3" in ids, ids
    assert all(i.startswith("kimi-code/") and kimi_cli_model(i) == i for i in ids), ids


def _spawn_env(provider):
    from spawn_env import build_spawn_env, env_family

    return build_spawn_env(family=env_family(provider.binary_name),
                           overrides=provider._turn_spawn_env())


def test_spawn_env_turns_off_auto_update_and_telemetry():
    from providers.kimi_provider import KimiProvider

    env = _spawn_env(KimiProvider(binary_name="kimi-code/kimi-for-coding"))
    assert env["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    assert env["KIMI_DISABLE_TELEMETRY"] == "1"
    assert "KIMI_MODEL_THINKING_EFFORT" not in env


@pytest.mark.parametrize("model, level, expected", [
    ("kimi-code/k3", "max", "max"),
    ("kimi-k3", "low", "low"),
    ("kimi-code/k3", "medium", None),          # not in K3's declared list
    ("kimi-code/k3", "auto", None),
    ("kimi-code/kimi-for-coding", "high", None),  # declares no efforts
])
def test_effort_reaches_the_cli_only_for_levels_the_model_declares(model, level, expected):
    from providers.kimi_provider import KimiProvider

    provider = KimiProvider(binary_name=model)
    provider._effort_level = level
    assert _spawn_env(provider).get("KIMI_MODEL_THINKING_EFFORT") == expected


def test_a_failure_after_answer_text_is_an_error_and_keeps_the_text(run_kimi):
    # kimi's only failure signal is stderr + rc 1; a failed later step must not look finished.
    events = run_kimi([
        '{"role":"meta","type":"system.version","version":"2.5.3"}',
        '{"role":"assistant","content":"Let me look at the file first."}',
    ], ["error: failed to run prompt: APIConnectionError: fetch failed"], rc=1)

    errors = [e for e in events if e["type"] == "error"]
    assert [e["code"] for e in errors] == ["kimi_failed"], events
    assert "fetch failed" in errors[0]["content"]
    assert any(e["type"] == "delta" and "look at the file" in e["text"] for e in events)


def test_a_clean_exit_with_text_is_not_an_error(run_kimi):
    events = run_kimi([
        '{"role":"meta","type":"system.version","version":"2.5.3"}',
        '{"role":"assistant","content":"Done."}',
    ], [], rc=0)
    assert not [e for e in events if e["type"] == "error"], events


def test_an_unrelated_login_url_in_an_error_is_not_a_missing_login(run_kimi):
    events = run_kimi(['{"role":"meta","type":"system.version","version":"2.5.3"}'], [
        "error: failed to run prompt: request to https://api.example.invalid/v1/login/refresh "
        "failed: 502 Bad Gateway",
    ], rc=1)

    errors = [e for e in events if e["type"] == "error"]
    assert [e["code"] for e in errors] == ["kimi_failed"], events


@pytest.mark.parametrize("message", [
    "error: OAuth login expired. Send /login to login.",
    'error: LLM not set, send "/login" to login',
    "error: Authorization failed. Please check your API key (try /login).",
    "error: Login required: Run /login to authenticate with the OAuth provider.",
    "error: Run /login to refresh",
])
def test_kimis_own_login_messages_say_how_to_log_in(run_kimi, message):
    events = run_kimi(['{"role":"meta","type":"system.version","version":"2.5.3"}'], [message], rc=1)
    assert [e["code"] for e in events if e["type"] == "error"] == ["kimi_not_logged_in"], events


@pytest.mark.parametrize("rc, resets", [(1, False), (2, True), (4, True), (137, True)])
def test_a_failure_after_text_resets_the_session_like_the_generic_path(run_kimi, rc, resets):
    # A killed or timed-out CLI can leave a half-written session; rc 1 is kimi's plain failure.
    events = run_kimi([
        '{"role":"meta","type":"system.version","version":"2.5.3"}',
        '{"role":"assistant","content":"partial answer"}',
    ], ["error: boom"], rc=rc)
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1, events
    assert bool(errors[0].get("reset_session")) is resets
    assert any(e["type"] == "delta" and "partial answer" in e["text"] for e in events)



if __name__ == "__main__":
    unittest.main()
