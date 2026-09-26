"""Cursor / Copilot / OpenCode entegrasyonu birim testleri.

Kapsam:
  1. Model ID eşlemesi (split_model_id)
  2. Manager routing (subscription prefix'leri doğru provider'a gider)
  3. Komut satırı inşası (_build_cmd) — resume/session bayrakları
  4. cli_base JSON event parser — canlı yakalanan GERÇEK event örnekleriyle
     (2026-07-13 canlı problardan alındı)
"""
import os
import sys
import json
import asyncio
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


class TestSplitModelId(unittest.TestCase):
    def test_cursor(self):
        from providers.oneshot_cli import split_model_id
        self.assertEqual(split_model_id("cursor-composer-2.5"), ("cursor", "composer-2.5"))
        # Cursor'ın kendi id'si zaten cursor- ile başlayanlar (cursor-grok-4.5-high)
        self.assertEqual(split_model_id("cursor-cursor-grok-4.5-high"), ("cursor", "cursor-grok-4.5-high"))

    def test_copilot(self):
        from providers.oneshot_cli import split_model_id
        self.assertEqual(split_model_id("copilot-claude-sonnet-5"), ("copilot", "claude-sonnet-5"))
        self.assertEqual(split_model_id("copilot-auto"), ("copilot", "auto"))

    def test_opencode(self):
        from providers.oneshot_cli import split_model_id
        self.assertEqual(split_model_id("opencode:opencode/big-pickle"), ("opencode", "opencode/big-pickle"))
        self.assertEqual(split_model_id("opencode:google/gemini-3.5-flash"), ("opencode", "google/gemini-3.5-flash"))

    def test_unknown(self):
        from providers.oneshot_cli import split_model_id
        self.assertEqual(split_model_id("claude-sonnet-5"), (None, "claude-sonnet-5"))


class TestManagerRouting(unittest.TestCase):
    def _get(self, model):
        from providers.manager import AIProviderManager
        return AIProviderManager.get_provider({"provider_type": "subscription", "model_name": model})

    def test_routing(self):
        from providers.cursor_provider import CursorProvider
        from providers.copilot_provider import CopilotProvider
        from providers.opencode_provider import OpenCodeProvider
        from providers.claude_provider import ClaudeCodeProvider
        from providers.codex_provider import CodexProvider
        self.assertIsInstance(self._get("cursor-composer-2.5"), CursorProvider)
        self.assertIsInstance(self._get("copilot-gpt-5.5"), CopilotProvider)
        self.assertIsInstance(self._get("opencode:opencode/big-pickle"), OpenCodeProvider)
        # Mevcut routing bozulmadı:
        self.assertIsInstance(self._get("gpt-5.5"), CodexProvider)
        self.assertIsInstance(self._get("claude-sonnet-5"), ClaudeCodeProvider)
        opus_5 = self._get("claude-opus-5")
        self.assertIsInstance(opus_5, ClaudeCodeProvider)
        self.assertEqual(opus_5.binary_name, "claude-opus-5")
        # copilot-gpt-* Codex'e DÜŞMEMELİ (prefix önceliği):
        self.assertIsInstance(self._get("copilot-gpt-5.6-sol"), CopilotProvider)
        # Yeni modeller (2026-09-05) aynı önek kuralıyla yerine gidiyor ve
        # kimlik CLI'a OLDUĞU GİBİ geçiyor — Codex `-m <id>` ile çağırıyor.
        astra = self._get("gpt-6-astra")
        self.assertIsInstance(astra, CodexProvider)
        self.assertEqual(astra.binary_name, "gpt-6-astra")
        for mid in ("gpt-6-sol", "gpt-6-luna"):
            codex = self._get(mid)
            self.assertIsInstance(codex, CodexProvider)
            self.assertEqual(codex.binary_name, mid)
        opus_55 = self._get("claude-opus-5-5")
        self.assertIsInstance(opus_55, ClaudeCodeProvider)
        self.assertEqual(opus_55.binary_name, "claude-opus-5-5")
        from providers.agy_provider import AgyProvider
        for mid, gorunen in (("gemini-3.8-flash", "Gemini 3.8 Flash (High)"),
                             ("gemini-3.7-flash", "Gemini 3.7 Flash (High)")):
            agy = self._get(mid)
            self.assertIsInstance(agy, AgyProvider)
            # agy modeli --model flag'iyle DEĞİL settings.json'daki görünen adla
            # seçiliyor; eşleme yoksa sessizce 3.6'ya düşerdi.
            self.assertEqual(AgyProvider._AGY_MODEL_MAP.get(mid), gorunen)

    def test_nvidia_routing(self):
        """NVIDIA NIM → OpenAI-uyumlu provider, doğru base_url ile."""
        from providers.manager import AIProviderManager
        from providers.api_providers import OpenAICompatibleProvider
        p = AIProviderManager.get_provider({
            "provider_type": "nvidia", "api_key": "nvapi-test",
            "model_name": "nvidia/nemotron-3-super-120b-a12b",
        })
        self.assertIsInstance(p, OpenAICompatibleProvider)
        self.assertIn("integrate.api.nvidia.com", getattr(p, "base_url", ""))


def _mock_unity_mcp(running=False):
    """unity_ai_mcp.unity_mcp_manager import'unu mock'lar."""
    m = MagicMock()
    m.unity_mcp_manager.is_running.return_value = running
    m.unity_mcp_manager.mcp_port = 8080
    return patch.dict(sys.modules, {"unity_ai_mcp": MagicMock(unity_mcp_manager=m.unity_mcp_manager),
                                    "unity_ai_mcp.unity_mcp_manager": m})


class TestBuildCmd(unittest.TestCase):
    def test_cursor_cmd_resume(self):
        from providers.cursor_provider import CursorProvider
        p = CursorProvider(binary_name="cursor-composer-2.5")
        p.resume_session_id = "chat-123"
        with _mock_unity_mcp(), \
             patch("providers.cursor_provider.resolve_cursor_cmd", return_value=["C:/node.exe", "C:/index.js"]):
            cmd = p._build_cmd("merhaba", workspace="C:/ws")
        self.assertEqual(cmd[:2], ["C:/node.exe", "C:/index.js"])
        self.assertIn("-p", cmd)
        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[cmd.index("--resume") + 1], "chat-123")
        self.assertEqual(cmd[cmd.index("--model") + 1], "composer-2.5")
        self.assertIn("stream-json", cmd)
        self.assertTrue(cmd[-1].endswith("merhaba"))  # prompt son pozisyonel arg

    def test_cursor_auto_model_explicit(self):
        """'auto' da AÇIKÇA --model auto olarak geçirilir: bayraksız çağrı CLI'ın
        kayıtlı adlı modelini dener ve Free planda patlar (canlı doğrulandı)."""
        from providers.cursor_provider import CursorProvider
        p = CursorProvider(binary_name="cursor-auto")
        with _mock_unity_mcp(), \
             patch("providers.cursor_provider.resolve_cursor_cmd", return_value=["agent"]):
            cmd = p._build_cmd("hi")
        self.assertEqual(cmd[cmd.index("--model") + 1], "auto")

    def test_copilot_first_turn_vs_resume(self):
        from providers.copilot_provider import CopilotProvider
        p = CopilotProvider(binary_name="copilot-claude-sonnet-5")
        p.fresh_session_id = "uuid-1"
        with _mock_unity_mcp(), \
             patch("providers.copilot_provider.resolve_copilot_cmd", return_value=["node", "loader.js"]):
            cmd1 = p._build_cmd("ilk tur")
            p.fresh_session_id = None
            p.resume_session_id = "uuid-1"
            cmd2 = p._build_cmd("ikinci tur")
        self.assertIn("--session-id=uuid-1", cmd1)
        self.assertNotIn("--session-id=uuid-1", cmd2)
        self.assertIn("--resume=uuid-1", cmd2)
        # Yazma/shell reddedilir, unityai'ye izin verilir:
        self.assertIn("write", cmd1[cmd1.index("--deny-tool") + 1])
        self.assertIn("unityai", cmd1)
        # K5(b): prompt ARTIK argv'de değil — `-p` bayrağıyla birlikte düştü ve
        # metin stdin'e taşındı (`-p -` stdin anlamına gelmiyor; copilot onu düz
        # metin sanıyor, 2026-08-01 canlı ölçüldü). Prompt'un kaybolmadığını
        # stdin yükünden doğrula.
        self.assertNotIn("-p", cmd1)
        self.assertTrue(p._stdin_payload.endswith("ikinci tur"))

    def test_opencode_cmd(self):
        from providers.opencode_provider import OpenCodeProvider
        p = OpenCodeProvider(binary_name="opencode:opencode/big-pickle")
        p.resume_session_id = "ses_abc"
        with _mock_unity_mcp(), \
             patch("providers.opencode_provider.resolve_opencode_cmd", return_value=["opencode.exe"]):
            cmd = p._build_cmd("soru")
        self.assertEqual(cmd[0], "opencode.exe")
        self.assertEqual(cmd[1], "run")
        self.assertEqual(cmd[cmd.index("-m") + 1], "opencode/big-pickle")
        self.assertEqual(cmd[cmd.index("-s") + 1], "ses_abc")
        self.assertIn("--format", cmd)

    def test_opencode_turn_token_rides_the_process_env_not_the_shared_file(self):
        """opencode.json is shared by every chat of a workspace; two concurrent
        turns overwrote each other's token there. The token goes to this turn's
        process env, and a stale one left in the file by an older build is gone."""
        from providers.opencode_provider import OpenCodeProvider

        p = OpenCodeProvider(binary_name="opencode:opencode-go/kimi-k3")
        p._approval_turn_token = "one-turn-secret"
        with tempfile.TemporaryDirectory() as workspace, _mock_unity_mcp():
            with open(os.path.join(workspace, "opencode.json"), "w", encoding="utf-8") as f:
                json.dump({"mcp": {"unityai": {"type": "local", "command": ["old"],
                           "environment": {"UNITYAI_APPROVAL_TURN_TOKEN": "stale"}}}}, f)
            p._register_mcp("unityai-launcher", workspace, "http://localhost:8000")
            with open(os.path.join(workspace, "opencode.json"), encoding="utf-8") as f:
                cfg = json.load(f)

        env = cfg["mcp"]["unityai"]["environment"]
        self.assertNotIn("UNITYAI_APPROVAL_TURN_TOKEN", env)
        self.assertNotIn("UNITYAI_AUTO_APPROVE", env)
        self.assertEqual(p._turn_spawn_env(), {"UNITYAI_APPROVAL_TURN_TOKEN": "one-turn-secret"})
        p._approval_turn_token = ""
        self.assertEqual(p._turn_spawn_env(), {})

    def test_opencode_spawn_env_carries_the_turn_token(self):
        from providers import cli_base
        from providers.opencode_provider import OpenCodeProvider

        p = OpenCodeProvider(binary_name="opencode:opencode-go/kimi-k3")
        p._approval_turn_token = "one-turn-secret"
        seen = {}

        def fake_build(family=None, overrides=None):
            seen.update(overrides or {})
            raise RuntimeError("stop after env")

        async def run():
            with patch.object(cli_base, "build_spawn_env", side_effect=fake_build), \
                 patch.object(OpenCodeProvider, "_write_mcp_config", lambda self, ws: ""), \
                 patch.object(OpenCodeProvider, "_build_cmd", lambda self, *a, **k: ["opencode"]):
                try:
                    async for _ in p.analyze_code("x", cwd="."):
                        pass
                except RuntimeError:
                    pass

        asyncio.run(run())
        self.assertEqual(seen.get("UNITYAI_APPROVAL_TURN_TOKEN"), "one-turn-secret")


class TestEventParsing(unittest.TestCase):
    """cli_base.analyze_code'un JSON satırlarını doğru event'lere çevirdiğini,
    sahte bir subprocess'le (echo JSONL) uçtan uca doğrular."""

    def _run_provider(self, provider, jsonl_lines):
        """Provider'ın analyze_code'unu sahte komutla çalıştırıp event listesi döner.
        Sahte komut: bir python betiği stdout'a JSONL basar.

        Betik argv'ye DEĞİL geçici bir DOSYAYA yazılıyor. `python -c <betik>`
        biçimi, 64 KB'lık Unity ekran görüntüsü satırını taşıyan testte Linux'ta
        `OSError: [Errno 7] Argument list too long` veriyordu — argüman başına
        sınır (`MAX_ARG_STRLEN`, 128 KB) macOS'ta yok, o yüzden yerelde hiç
        görünmedi ve yalnız runner'da patladı (ölçüldü, CI 2026-07-28).
        Dosyadan okumak aynı baytları aynı borudan geçiriyor, yani test
        zayıflamıyor; tersine büyük-satır yolu Linux'ta ARTIK gerçekten
        sınanabiliyor — eskiden orada kurulum aşamasında ölüyordu.
        """
        script = "import sys\n" + "".join(
            f"sys.stdout.write({json.dumps(line + chr(10))})\n" for line in jsonl_lines
        )
        fd, script_path = tempfile.mkstemp(prefix="fake_cli_", suffix=".py")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(script)
            with _mock_unity_mcp(), \
                 patch.object(type(provider), "_build_cmd", lambda self, *a, **k: [sys.executable, script_path]), \
                 patch.object(type(provider), "_write_mcp_config", lambda self, ws: ""):
                async def collect():
                    evs = []
                    async for ev in provider.analyze_code("test", cwd=os.getcwd()):
                        evs.append(ev)
                    return evs
                return asyncio.run(collect())
        finally:
            # Yaratan adımın silen adımı: aksi hâlde her koşu $TMPDIR'a bir
            # betik bırakır ve büyük satır testinde bunlar 64 KB'lık.
            try:
                os.unlink(script_path)
            except OSError:
                pass

    def test_cursor_stream(self):
        """Gerçek cursor stream-json örneği: parçalı delta + tam metin tekrarı + result."""
        from providers.cursor_provider import CursorProvider
        p = CursorProvider(binary_name="cursor-composer-2.5")
        sid = "e6301622-314f-4980-8ced-248772b378f1"
        lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": sid, "model": "Auto"}),
            json.dumps({"type": "thinking", "subtype": "delta", "text": "düşünüyor...", "session_id": sid}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "TAM"}]}, "session_id": sid}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "AM-1"}]}, "session_id": sid}),
            # Cursor'ın tam-metin tekrar event'i (dedup edilmeli):
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "TAMAM-1"}]}, "session_id": sid}),
            json.dumps({"type": "result", "subtype": "success", "result": "TAMAM-1", "session_id": sid}),
        ]
        evs = self._run_provider(p, lines)
        metas = [e for e in evs if e["type"] == "session_meta"]
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0]["session_id"], sid)
        deltas = "".join(e["text"] for e in evs if e["type"] == "delta")
        self.assertEqual(deltas, "TAMAM-1")  # tam-metin tekrarı MÜKERRER basılmadı
        self.assertTrue(any(e["type"] == "thinking" for e in evs))
        final = [e for e in evs if e["type"] == "final"][0]
        self.assertEqual(final["text"], "TAMAM-1")

    def test_copilot_stream(self):
        """Gerçek copilot JSONL örneği: reasoning delta + message delta + tam mesaj + result."""
        from providers.copilot_provider import CopilotProvider
        p = CopilotProvider(binary_name="copilot-claude-haiku-4.5")
        lines = [
            json.dumps({"type": "session.mcp_servers_loaded", "data": {"servers": []}}),
            json.dumps({"type": "assistant.reasoning_delta", "data": {"reasoningId": "r1", "deltaContent": "kısa düşünce"}}),
            json.dumps({"type": "assistant.message_delta", "data": {"messageId": "m1", "deltaContent": "MERHABA-"}}),
            json.dumps({"type": "assistant.message_delta", "data": {"messageId": "m1", "deltaContent": "42"}}),
            # Tam mesaj (delta'ları akıtıldı → tekrar basılmamalı):
            json.dumps({"type": "assistant.message", "data": {"messageId": "m1", "content": "MERHABA-42", "toolRequests": []}}),
            json.dumps({"type": "result", "sessionId": "0065763b-761e-4c7e-ae1d-b5cc2f455471", "exitCode": 0}),
        ]
        evs = self._run_provider(p, lines)
        deltas = "".join(e["text"] for e in evs if e["type"] == "delta")
        self.assertEqual(deltas, "MERHABA-42")
        metas = [e for e in evs if e["type"] == "session_meta"]
        self.assertEqual(metas[0]["session_id"], "0065763b-761e-4c7e-ae1d-b5cc2f455471")
        self.assertTrue(any("kısa düşünce" in e.get("text", "") for e in evs if e["type"] == "thinking"))
        final = [e for e in evs if e["type"] == "final"][0]
        self.assertEqual(final["text"], "MERHABA-42")

    def test_copilot_message_without_deltas(self):
        """Delta akmadıysa assistant.message'ın tam içeriği basılır (stream=off senaryosu)."""
        from providers.copilot_provider import CopilotProvider
        p = CopilotProvider(binary_name="copilot-auto")
        lines = [
            json.dumps({"type": "assistant.message", "data": {"messageId": "m1", "content": "SELAM"}}),
            json.dumps({"type": "result", "sessionId": "abc", "exitCode": 0}),
        ]
        evs = self._run_provider(p, lines)
        deltas = "".join(e["text"] for e in evs if e["type"] == "delta")
        self.assertEqual(deltas, "SELAM")

    def test_opencode_stream(self):
        """Gerçek opencode --format json örneği: tool_use + text + step_finish."""
        from providers.opencode_provider import OpenCodeProvider
        p = OpenCodeProvider(binary_name="opencode:opencode/big-pickle")
        sid = "ses_0a49b7dfbffe84bVZY1idV7JmY"
        lines = [
            json.dumps({"type": "step_start", "sessionID": sid, "part": {"type": "step-start"}}),
            json.dumps({"type": "tool_use", "sessionID": sid, "part": {"type": "tool", "tool": "bash",
                        "state": {"status": "completed", "title": "Write-Output \"hi\"", "output": "hi\r\n"}}}),
            json.dumps({"type": "text", "sessionID": sid, "part": {"type": "text", "text": "hi"}}),
            json.dumps({"type": "step_finish", "sessionID": sid, "part": {"reason": "stop",
                        "tokens": {"total": 8234, "input": 71, "output": 4}}}),
        ]
        evs = self._run_provider(p, lines)
        metas = [e for e in evs if e["type"] == "session_meta"]
        self.assertEqual(metas[0]["session_id"], sid)
        deltas = "".join(e["text"] for e in evs if e["type"] == "delta")
        self.assertEqual(deltas, "hi")
        hints = [e["text"] for e in evs if e["type"] == "thinking"]
        self.assertTrue(any("bash" in h for h in hints))

    def test_opencode_large_unity_screenshot_event_exceeds_default_asyncio_limit(self):
        """Unity screenshot gibi 64 KiB üstü tek JSONL event bridge'i düşürmemeli."""
        from providers.opencode_provider import OpenCodeProvider

        p = OpenCodeProvider(binary_name="opencode:opencode-go/kimi-k3")
        sid = "ses_large_unity_screenshot"
        large_screenshot = "data:image/png;base64," + ("A" * 220_000)
        lines = [
            json.dumps({
                "type": "tool_use",
                "sessionID": sid,
                "part": {
                    "type": "tool",
                    "tool": "unityMCP_manage_camera",
                    "state": {
                        "status": "completed",
                        "title": "screenshot",
                        "output": large_screenshot,
                    },
                },
            }),
            json.dumps({
                "type": "text",
                "sessionID": sid,
                "part": {"type": "text", "text": "Kart görünürlüğü doğrulandı."},
            }),
        ]

        self.assertGreater(len(lines[0].encode("utf-8")), 64 * 1024)
        evs = self._run_provider(p, lines)

        self.assertFalse(any(
            "CLI Bridge Hatası" in e.get("content", "")
            for e in evs
            if e["type"] == "error"
        ))
        self.assertTrue(any(
            "unityMCP_manage_camera" in e.get("text", "")
            for e in evs
            if e["type"] == "thinking"
        ))
        deltas = "".join(e["text"] for e in evs if e["type"] == "delta")
        self.assertEqual(deltas, "Kart görünürlüğü doğrulandı.")

    def test_opencode_structured_error_is_reported_without_bridge_crash(self):
        """OpenCode error.error=dict döndürdüğünde str+dict TypeError oluşmamalı."""
        from providers.opencode_provider import OpenCodeProvider
        p = OpenCodeProvider(binary_name="opencode:opencode-go/kimi-k3")
        lines = [
            json.dumps({
                "type": "error",
                "sessionID": "ses_broken",
                "error": {
                    "name": "ProviderError",
                    "data": {"message": "Interrupted session cannot be resumed"},
                },
            }),
        ]

        evs = self._run_provider(p, lines)
        errors = [e for e in evs if e["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("ProviderError", errors[0]["content"])
        self.assertIn("Interrupted session cannot be resumed", errors[0]["content"])
        self.assertTrue(errors[0]["reset_session"])
        self.assertFalse(any("CLI Bridge Hatası" in e.get("content", "") for e in errors))

    def test_opencode_upstream_error_keeps_resume_session(self):
        """Provider yoğunluğu/rate-limit session bozulması sayılmamalı."""
        from providers.opencode_provider import OpenCodeProvider

        p = OpenCodeProvider(binary_name="opencode:opencode-go/kimi-k3")
        lines = [
            json.dumps({
                "type": "error",
                "sessionID": "ses_rate_limited",
                "error": {
                    "name": "APIError",
                    "data": {
                        "message": (
                            "Error from provider (Console Go): "
                            "Upstream request failed"
                        ),
                        "statusCode": 400,
                    },
                },
            }),
        ]

        evs = self._run_provider(p, lines)
        error = [e for e in evs if e["type"] == "error"][0]
        self.assertEqual(error["reason"], "provider_upstream")
        self.assertTrue(error["retryable"])
        self.assertNotIn("reset_session", error)

    def test_opencode_access_refusals_are_not_retryable_and_keep_session(self):
        """Go-subscription and free-tier 403s (captured live 26 Sep 2026) are
        plan refusals, not rate limits; the Go one is wrapped in
        "Upstream request failed" and must not fall into that branch."""
        from providers.opencode_provider import OpenCodeProvider

        for model, message in (
            ("opencode-go/space-bunny-free",
             "Upstream request failed: An active OpenCode Go subscription is "
             "required to use Go models."),
            ("opencode/ling-3.0-flash-fin-free",
             "Error from provider (Console): OpenCode's free tier can only be "
             "used from within OpenCode"),
        ):
            with self.subTest(model=model):
                p = OpenCodeProvider(binary_name=f"opencode:{model}")
                lines = [json.dumps({
                    "type": "error",
                    "sessionID": "ses_access",
                    "error": {"name": "APIError",
                              "data": {"message": message, "statusCode": 403,
                                       "isRetryable": False}},
                })]
                error = [e for e in self._run_provider(p, lines) if e["type"] == "error"][0]
                self.assertEqual(error["reason"], "provider_access")
                self.assertFalse(error["retryable"])
                self.assertNotIn("reset_session", error)

    def test_opencode_rate_limit_error_keeps_resume_session(self):
        """Codex tabverify: a structured 429 without "Upstream request failed"
        fell to structured_error and reset the session."""
        from providers.opencode_provider import OpenCodeProvider

        p = OpenCodeProvider(binary_name="opencode:opencode/big-pickle")
        lines = [json.dumps({
            "type": "error",
            "sessionID": "ses_429",
            "error": {"name": "APIError",
                      "data": {"message": "429 rate limit exceeded", "statusCode": 429,
                               "isRetryable": True}},
        })]
        error = [e for e in self._run_provider(p, lines) if e["type"] == "error"][0]
        self.assertEqual(error["reason"], "provider_quota")
        self.assertNotIn("reset_session", error)

    def test_opencode_context_limit_error_still_resets_session(self):
        """Codex tabverify2: "limit reached" is also a context overflow, and a
        session that cannot resume must be reset, not retried forever."""
        from providers.opencode_provider import OpenCodeProvider

        for message in (
            "ProviderError: Interrupted session cannot be resumed: context limit reached",
            "maximum context limit reached",
        ):
            with self.subTest(message=message):
                p = OpenCodeProvider(binary_name="opencode:opencode/big-pickle")
                lines = [json.dumps({
                    "type": "error",
                    "sessionID": "ses_overflow",
                    "error": {"name": "APIError", "data": {"message": message}},
                })]
                error = [e for e in self._run_provider(p, lines) if e["type"] == "error"][0]
                self.assertTrue(error.get("reset_session"))

    def test_rate_limit_text_is_never_read_as_an_access_refusal(self):
        """Codex tabaudit: a 429 carrying the Go phrase was shown as "retrying
        will not help". Any quota/limit wording keeps the quota mapping."""
        from providers.oneshot_cli import opencode_access_message

        self.assertIsNone(opencode_access_message(
            "429 Too many requests: An active OpenCode Go subscription is "
            "required to use Go models."))
        self.assertIsNone(opencode_access_message(
            "rate limit reached; OpenCode's free tier can only be used from "
            "within OpenCode"))

    def test_cli_timeout_uses_idle_time_not_five_minute_total_runtime(self):
        """Aktif CLI toplam 5 dakikayı geçti diye öldürülmemeli."""
        from providers.cli_base import BaseCLIProvider

        self.assertIsNone(
            BaseCLIProvider._cli_timeout_reason(elapsed=301, idle=1)
        )
        self.assertEqual(
            BaseCLIProvider._cli_timeout_reason(
                elapsed=901,
                idle=BaseCLIProvider._CLI_IDLE_TIMEOUT_SECONDS + 1,
            ),
            "idle_timeout",
        )
        self.assertEqual(
            BaseCLIProvider._cli_timeout_reason(
                elapsed=BaseCLIProvider._CLI_MAX_RUNTIME_SECONDS + 1,
                idle=0,
            ),
            "max_runtime",
        )


class TestOneShotSessionRecovery(unittest.TestCase):
    def test_fatal_opencode_error_invalidates_resume_session(self):
        """Öldürülmüş/bozuk OpenCode session'ı sonraki turda resume edilmemeli."""
        from agentic.agent_runner import AgentRunner
        from providers.oneshot_cli import _SESSIONS, get_session

        class FakeOpenCodeProvider:
            resume_session_id = None

            async def analyze_code(self, *args, **kwargs):
                yield {"type": "session_meta", "session_id": "ses_broken"}
                yield {
                    "type": "error",
                    "content": "CLI hareketsizlik nedeniyle durduruldu.",
                    "reset_session": True,
                    "reason": "idle_timeout",
                }

        _SESSIONS.clear()
        session = get_session("opencode", 991)
        session.session_id = "ses_old"
        session.ctx_injected = True
        runner = AgentRunner(
            provider_type="subscription",
            api_key="",
            model_name="opencode:opencode-go/kimi-k3",
            workspace_path=os.getcwd(),
            context="USER: önceki görev",
            conversation_id=991,
        )

        async def collect():
            events = []
            async for event in runner._run_oneshot_cli_session("devam et", "opencode"):
                events.append(event)
            return events

        with patch(
            "ai_providers.AIProviderManager.get_provider",
            return_value=FakeOpenCodeProvider(),
        ):
            events = asyncio.run(collect())

        self.assertTrue(any(event.type == "error" for event in events))
        self.assertIsNone(session.session_id)
        self.assertFalse(session.ctx_injected)
        _SESSIONS.clear()

    def test_opencode_error_text_separates_plan_refusal_from_rate_limit(self):
        """The Go-subscription 403 used to show the "temporarily refused /
        rate limit, retry later" text (owner report, 26 Sep 2026)."""
        from agentic.agent_runner import AgentRunner
        from providers.oneshot_cli import _SESSIONS

        def run(raw_error):
            class FakeOpenCodeProvider:
                resume_session_id = None

                async def analyze_code(self, *args, **kwargs):
                    yield {"type": "session_meta", "session_id": "ses_keep"}
                    yield {"type": "error", "content": f"❌ CLI hatası: APIError: {raw_error}"}

            _SESSIONS.clear()
            runner = AgentRunner(
                provider_type="subscription", api_key="",
                model_name="opencode:opencode-go/space-bunny-free",
                workspace_path=os.getcwd(), conversation_id=995,
            )

            async def collect():
                return [e async for e in runner._run_oneshot_cli_session("selam", "opencode")]

            with patch("ai_providers.AIProviderManager.get_provider",
                       return_value=FakeOpenCodeProvider()):
                events = asyncio.run(collect())
            _SESSIONS.clear()
            return [e.data["message"] for e in events if e.type == "error"][0]

        go = run("Upstream request failed: An active OpenCode Go subscription is "
                 "required to use Go models.")
        self.assertIn("OpenCode Go aboneliği gerektiriyor", go)
        self.assertNotIn("geçici olarak reddetti", go)

        free = run("Error from provider (Console): OpenCode's free tier can only be "
                   "used from within OpenCode")
        self.assertIn("yalnız kendi uygulaması içinden", free)
        self.assertNotIn("geçici olarak reddetti", free)

        upstream = run("Error from provider (Console Go): Upstream request failed")
        self.assertIn("geçici olarak reddetti", upstream)

        quota = run("Rate limit reached for requests")
        self.assertIn("kullanım hakkın dolmuş", quota)

    def test_cancelled_agy_stream_closes_persistent_session(self):
        from agentic.agent_runner import AgentRunner
        from providers.agy_session import _SESSIONS, get_session

        started = asyncio.Event()

        async def fake_stream(*args, **kwargs):
            yield {"type": "thinking", "text": "başladı"}
            started.set()
            await asyncio.Event().wait()

        _SESSIONS.clear()
        session = get_session(1000)
        session.session_id = "agy-partial"
        runner = AgentRunner(
            provider_type="subscription",
            api_key="",
            model_name="gemini-3.6-flash",
            workspace_path=os.getcwd(),
            conversation_id=1000,
        )

        async def cancel_mid_stream():
            async def consume():
                async for _ in runner._run_agy_session("başla"):
                    pass

            task = asyncio.create_task(consume())
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        with patch.object(session, "stream", fake_stream):
            asyncio.run(cancel_mid_stream())

        self.assertFalse(session.is_live)
        self.assertNotIn(1000, _SESSIONS)
        self.assertIsNone(session.active_provider)
        _SESSIONS.clear()

    def test_closing_oneshot_session_cancels_active_provider(self):
        from providers.oneshot_cli import _SESSIONS, close_session, get_session

        class FakeProvider:
            cancelled = False

            async def cancel_active_process(self):
                self.cancelled = True
                return True

        _SESSIONS.clear()
        provider = FakeProvider()
        session = get_session("opencode", 993)
        session.session_id = "ses_active"
        session.active_provider = provider

        asyncio.run(close_session("opencode", 993))

        self.assertTrue(provider.cancelled)
        self.assertNotIn(("opencode", 993), _SESSIONS)

    def test_cancelled_stream_invalidates_oneshot_resume_session(self):
        from agentic.agent_runner import AgentRunner
        from providers.oneshot_cli import _SESSIONS, get_session

        started = asyncio.Event()

        class FakeOpenCodeProvider:
            resume_session_id = None

            async def analyze_code(self, *args, **kwargs):
                yield {"type": "session_meta", "session_id": "ses_partial"}
                started.set()
                await asyncio.Event().wait()

        _SESSIONS.clear()
        session = get_session("opencode", 994)
        runner = AgentRunner(
            provider_type="subscription",
            api_key="",
            model_name="opencode:opencode-go/kimi-k3",
            workspace_path=os.getcwd(),
            conversation_id=994,
        )

        async def cancel_mid_stream():
            async def consume():
                async for _ in runner._run_oneshot_cli_session("başla", "opencode"):
                    pass

            task = asyncio.create_task(consume())
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        with patch(
            "ai_providers.AIProviderManager.get_provider",
            return_value=FakeOpenCodeProvider(),
        ):
            asyncio.run(cancel_mid_stream())

        self.assertIsNone(session.session_id)
        self.assertFalse(session.ctx_injected)
        _SESSIONS.clear()


class TestOpenCodeApprovalPolicy(unittest.TestCase):
    def test_auto_turn_requires_active_token_and_exact_workspace(self):
        from agentic.approval_policy import (
            begin_opencode_turn,
            end_opencode_turn,
            should_auto_approve,
        )

        with tempfile.TemporaryDirectory() as workspace, \
             tempfile.TemporaryDirectory() as other_workspace:
            token = begin_opencode_turn(workspace, "auto")
            self.assertTrue(should_auto_approve(token, workspace))
            self.assertFalse(should_auto_approve(token, other_workspace))
            self.assertFalse(should_auto_approve("wrong-token", workspace))
            end_opencode_turn(token)
            self.assertFalse(should_auto_approve(token, workspace))

    def test_step_turn_never_auto_approves(self):
        from agentic.approval_policy import (
            begin_opencode_turn,
            end_opencode_turn,
            should_auto_approve,
        )

        with tempfile.TemporaryDirectory() as workspace:
            token = begin_opencode_turn(workspace, "step")
            try:
                self.assertFalse(should_auto_approve(token, workspace))
            finally:
                end_opencode_turn(token)

    def test_agent_runner_limits_auto_policy_to_running_opencode_turn(self):
        from agentic.agent_runner import AgentRunner
        from agentic.approval_policy import should_auto_approve
        from providers.oneshot_cli import _SESSIONS

        class FakeOpenCodeProvider:
            resume_session_id = None
            auto_was_active = False
            captured_token = None

            async def analyze_code(self, *args, **kwargs):
                self.captured_token = self._approval_turn_token
                self.auto_was_active = should_auto_approve(
                    self.captured_token, kwargs["cwd"]
                )
                yield {"type": "final", "text": "tamam"}

        _SESSIONS.clear()
        provider = FakeOpenCodeProvider()
        with tempfile.TemporaryDirectory() as workspace:
            runner = AgentRunner(
                provider_type="subscription",
                api_key="",
                model_name="opencode:opencode-go/kimi-k3",
                workspace_path=workspace,
                conversation_id=992,
                generation_mode="auto",
            )

            async def collect():
                return [
                    event
                    async for event in runner._run_oneshot_cli_session(
                        "devam et", "opencode"
                    )
                ]

            with patch(
                "ai_providers.AIProviderManager.get_provider",
                return_value=provider,
            ):
                asyncio.run(collect())

            self.assertTrue(provider.auto_was_active)
            self.assertFalse(
                should_auto_approve(provider.captured_token, workspace)
            )
        _SESSIONS.clear()

    def test_auto_request_skips_pending_card_but_step_request_does_not(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agentic.approval_policy import begin_opencode_turn, end_opencode_turn
        from routes.conversation_routes import create_conversation_router

        app = FastAPI()
        app.include_router(create_conversation_router(MagicMock(), {}))

        from agentic import approval_mode

        with tempfile.TemporaryDirectory() as workspace, TestClient(app) as client:
            # The decision now follows the global approval mode (25 Sep 2026);
            # the turn token no longer grants anything on its own.
            approval_mode.set_mode("auto", source="test")
            auto_token = begin_opencode_turn(workspace, "auto")
            try:
                auto = client.post("/mcp-approval-request", json={
                    "gate_id": "auto-gate",
                    "tool": "run_terminal_command",
                    "params": {"command": "echo ok"},
                    "workspace_path": workspace,
                    "approval_turn_token": auto_token,
                })
                self.assertEqual(auto.status_code, 200)
                self.assertEqual(auto.json()["status"], "resolved")
                self.assertTrue(auto.json()["approved"])
                self.assertNotIn(
                    "auto-gate",
                    client.get("/mcp-pending").json()["pending"],
                )
            finally:
                end_opencode_turn(auto_token)

            approval_mode.set_mode("step", source="test")
            step_token = begin_opencode_turn(workspace, "step")
            try:
                step = client.post("/mcp-approval-request", json={
                    "gate_id": "step-gate",
                    "tool": "run_terminal_command",
                    "params": {"command": "echo ok"},
                    "workspace_path": workspace,
                    "approval_turn_token": step_token,
                })
                self.assertEqual(step.status_code, 200)
                self.assertEqual(step.json()["status"], "ok")
                self.assertIn(
                    "step-gate",
                    client.get("/mcp-pending").json()["pending"],
                )
            finally:
                end_opencode_turn(step_token)

    def test_approval_bridge_returns_immediate_auto_result_without_polling(self):
        from unity_ai_mcp import approval_bridge

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "status": "resolved",
                    "approved": True,
                    "automatic": True,
                }

        class FakeClient:
            get_called = False

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                return FakeResponse()

            async def get(self, *args, **kwargs):
                FakeClient.get_called = True
                raise AssertionError("Auto sonucu için polling yapılmamalı")

        with patch.object(approval_bridge.httpx, "AsyncClient", FakeClient), \
             patch.dict(os.environ, {
                 "UNITYAI_APPROVAL_TURN_TOKEN": "one-turn-secret",
             }):
            result = asyncio.run(approval_bridge.request_approval(
                "run_terminal_command",
                {"command": "echo ok"},
                os.getcwd(),
            ))

        self.assertTrue(result["approved"])
        self.assertFalse(FakeClient.get_called)

    def test_chat_stop_closes_running_opencode_session(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from providers.oneshot_cli import _SESSIONS, get_session
        from routes.conversation_routes import create_conversation_router

        class FakeProvider:
            cancelled = False

            async def cancel_active_process(self):
                self.cancelled = True
                return True

        _SESSIONS.clear()
        provider = FakeProvider()
        session = get_session("opencode", 995)
        session.session_id = "ses_interrupted"
        session.active_provider = provider

        app = FastAPI()
        app.include_router(create_conversation_router(MagicMock(), {}))
        with TestClient(app) as client:
            response = client.post("/chat-stop/995")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertTrue(provider.cancelled)
        self.assertNotIn(("opencode", 995), _SESSIONS)

    def test_chat_stop_closes_every_ephemeral_cli_family(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from providers.oneshot_cli import _SESSIONS, get_session
        from providers.agy_session import (
            _SESSIONS as _AGY_SESSIONS,
            get_session as get_agy_session,
        )
        from routes.conversation_routes import create_conversation_router

        class FakeProvider:
            def __init__(self):
                self.cancelled = False

            async def cancel_active_process(self):
                self.cancelled = True
                return True

        _SESSIONS.clear()
        _AGY_SESSIONS.clear()
        providers = {}
        for cli in ("cursor", "copilot", "opencode", "kimi"):
            provider = FakeProvider()
            providers[cli] = provider
            session = get_session(cli, 998)
            session.active_provider = provider

        agy_provider = FakeProvider()
        agy_session = get_agy_session(998)
        agy_session.session_id = "agy-partial"
        agy_session.active_provider = agy_provider

        app = FastAPI()
        app.include_router(create_conversation_router(MagicMock(), {}))
        with TestClient(app) as client:
            response = client.post("/chat-stop/998")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertTrue(all(provider.cancelled for provider in providers.values()))
        self.assertTrue(agy_provider.cancelled)
        self.assertFalse(any(conv_id == 998 for _, conv_id in _SESSIONS))
        self.assertNotIn(998, _AGY_SESSIONS)

    def test_chat_stop_preserves_native_claude_and_codex_cancellation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from providers.claude_sdk_session import _SESSIONS as _CLAUDE_SESSIONS
        from providers.codex_session import _SESSIONS as _CODEX_SESSIONS
        from routes.conversation_routes import create_conversation_router

        class FakeNativeSession:
            def __init__(self):
                self.cancelled = False

            async def cancel_turn(self):
                self.cancelled = True

        for provider_name, sessions in (
            ("claude", _CLAUDE_SESSIONS),
            ("codex", _CODEX_SESSIONS),
        ):
            with self.subTest(provider=provider_name):
                _CLAUDE_SESSIONS.clear()
                _CODEX_SESSIONS.clear()
                session = FakeNativeSession()
                sessions[999] = session

                app = FastAPI()
                app.include_router(create_conversation_router(MagicMock(), {}))
                with TestClient(app) as client:
                    response = client.post("/chat-stop/999")

                self.assertEqual(response.json()["status"], "ok")
                self.assertTrue(session.cancelled)

        _CLAUDE_SESSIONS.clear()
        _CODEX_SESSIONS.clear()

    def test_chat_stop_rejects_stale_mcp_approval_gates(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.conversation_routes import create_conversation_router

        app = FastAPI()
        app.include_router(create_conversation_router(MagicMock(), {}))
        with TestClient(app) as client:
            pending = client.post("/mcp-approval-request", json={
                "gate_id": "stop-gate",
                "tool": "bash",
                "params": {"command": "echo waiting"},
                "workspace_path": os.getcwd(),
            })
            self.assertEqual(pending.json()["status"], "ok")

            stopped = client.post("/chat-stop/1001")
            result = client.get("/mcp-approval-result/stop-gate")
            remaining = client.get("/mcp-pending")

        self.assertEqual(stopped.json()["status"], "ok")
        self.assertEqual(
            result.json(),
            {"status": "resolved", "approved": False},
        )
        self.assertNotIn("stop-gate", remaining.json()["pending"])


class TestModelListParsers(unittest.TestCase):
    def test_cursor_models_parse(self):
        raw = (
            "Available models\n\n"
            "auto - Auto (current, default)\n"
            "gpt-5.3-codex - Codex 5.3\n"
            "gpt-5.3-codex-fast - Codex 5.3 Fast\n"
            "composer-2.5 - Composer 2.5\n"
            "claude-fable-5-thinking-high - Fable 5 1M Thinking (NO ZDR)\n"
        )
        # config_routes içindeki parser'lar closure — mantığı burada bire bir doğrula
        models = []
        for line in raw.splitlines():
            line = line.strip()
            if " - " not in line or line.lower().startswith("available"):
                continue
            mid, _, name = line.partition(" - ")
            mid, name = mid.strip(), name.strip()
            if not mid or mid.endswith("-fast"):
                continue
            name = name.split(" (current")[0].split(" (default")[0].strip()
            models.append((f"cursor-{mid}", name))
        ids = [m[0] for m in models]
        self.assertIn("cursor-auto", ids)
        self.assertIn("cursor-composer-2.5", ids)
        self.assertNotIn("cursor-gpt-5.3-codex-fast", ids)  # -fast elendi
        self.assertEqual(dict(models)["cursor-auto"], "Auto")

    def test_opencode_models_parse(self):
        from routes.config_routes import _parse_opencode_models

        raw = (
            "opencode/big-pickle\n"
            "opencode/deepseek-v4-flash-free\n"
            "opencode-go/kimi-k3\n"
            "google/gemini-3.5-flash\n"
            "openai/gpt-5.4\n"
        )
        models = _parse_opencode_models(raw)
        ids = [m["id"] for m in models]
        self.assertEqual(len(models), 3)
        self.assertIn("opencode:opencode-go/kimi-k3", ids)
        self.assertNotIn("opencode:google/gemini-3.5-flash", ids)

    def test_opencode_go_free_suffix_is_labelled_go_not_free(self):
        """opencode-go/*-free still needs the Go subscription (403, measured
        26 Sep 2026); only opencode/*-free is the keyless free tier."""
        from routes.config_routes import _parse_opencode_models

        names = {m["id"]: m["name"] for m in _parse_opencode_models(
            "opencode/space-bunny-free\nopencode-go/space-bunny-free\nopencode-go/kimi-k3\n")}
        self.assertEqual(names["opencode:opencode/space-bunny-free"], "Space Bunny (Ücretsiz)")
        self.assertEqual(names["opencode:opencode-go/space-bunny-free"], "Space Bunny (Go)")
        self.assertEqual(names["opencode:opencode-go/kimi-k3"], "Kimi K3 (Go)")



class TestOneShotTurnNamesItsChat(unittest.TestCase):
    """The runner tells every provider it makes (the Auto fallback included)
    which chat it works for; cli_base turns that into GAMACHINE_CONVERSATION_ID."""

    def test_runner_hands_the_conversation_id_to_the_provider(self):
        from agentic.agent_runner import AgentRunner
        from providers.oneshot_cli import _SESSIONS

        class FakeProvider:
            resume_session_id = None
            seen = None

            async def analyze_code(self, *args, **kwargs):
                self.seen = self._conversation_id
                yield {"type": "final", "text": "tamam"}

        for conversation_id in (994, -994):
            _SESSIONS.clear()
            provider = FakeProvider()
            runner = AgentRunner(provider_type="subscription", api_key="",
                                 model_name="cursor-gpt-5.2", workspace_path=os.getcwd(),
                                 conversation_id=conversation_id)

            async def collect():
                return [e async for e in runner._run_oneshot_cli_session("selam", "cursor")]

            with patch("ai_providers.AIProviderManager.get_provider", return_value=provider):
                asyncio.run(collect())
            # The raw id; the > 0 guard is conversation_env's (tested with cli_base).
            self.assertEqual(provider.seen, conversation_id)
        _SESSIONS.clear()


class TestCopilotTurnMcpConfig(unittest.TestCase):
    """copilot's --additional-mcp-config file is written per turn, so it can
    carry the chat id, and it is deleted when the turn ends."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.patches = [
            patch.dict(os.environ, {"HOME": self.tmp, "USERPROFILE": self.tmp}),
            patch.object(tempfile, "tempdir", self.tmp),
            patch("unity_ai_mcp.unity_mcp_manager.unity_mcp_manager.mcp_url",
                  return_value="http://127.0.0.1:8080/mcp"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self._tmp.cleanup()

    def _run_turn(self, conversation_id):
        from providers.cli_base import BaseCLIProvider
        from providers.copilot_provider import CopilotProvider

        seen = {}

        async def fake_base_turn(provider, *args, **kwargs):
            provider._register_mcp("launcher", self.tmp, "http://127.0.0.1:8000")
            seen["path"] = provider._mcp_cfg_path
            with open(seen["path"], encoding="utf-8") as f:
                seen["servers"] = json.load(f)["mcpServers"]
            yield {"type": "final", "text": "ok"}

        provider = CopilotProvider(binary_name="copilot-auto")
        if conversation_id is not None:
            provider._conversation_id = conversation_id

        async def collect():
            return [e async for e in provider.analyze_code("hi", cwd=self.tmp)]

        with patch.object(BaseCLIProvider, "analyze_code", fake_base_turn):
            asyncio.run(collect())
        return provider, seen

    def test_the_file_names_the_chat_and_is_deleted_after_the_turn(self):
        provider, seen = self._run_turn(7)
        self.assertEqual(set(seen["servers"]), {"unityai", "unityMCP"})
        for server in seen["servers"].values():
            self.assertEqual(server["env"]["GAMACHINE_CONVERSATION_ID"], "7")
        self.assertFalse(os.path.exists(seen["path"]))
        self.assertIsNone(provider._mcp_cfg_path)

    def test_no_real_chat_writes_no_id_and_still_deletes(self):
        for conversation_id in (None, 0, -2):
            _, seen = self._run_turn(conversation_id)
            for server in seen["servers"].values():
                self.assertNotIn("GAMACHINE_CONVERSATION_ID", server["env"])
            self.assertFalse(os.path.exists(seen["path"]))

    def test_a_failed_turn_deletes_the_file_too(self):
        from providers.cli_base import BaseCLIProvider
        from providers.copilot_provider import CopilotProvider

        seen = {}

        async def failing_turn(provider, *args, **kwargs):
            provider._register_mcp("launcher", self.tmp, "http://127.0.0.1:8000")
            seen["path"] = provider._mcp_cfg_path
            raise RuntimeError("copilot died")
            yield  # pragma: no cover

        provider = CopilotProvider(binary_name="copilot-auto")

        async def collect():
            return [e async for e in provider.analyze_code("hi", cwd=self.tmp)]

        with patch.object(BaseCLIProvider, "analyze_code", failing_turn):
            with self.assertRaises(RuntimeError):
                asyncio.run(collect())
        self.assertTrue(seen["path"])
        self.assertFalse(os.path.exists(seen["path"]))


if __name__ == "__main__":
    unittest.main()
