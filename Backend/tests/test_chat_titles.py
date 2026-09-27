"""AI chat titles (Burak, 27 Sep 2026): who writes them, when, and what is
never touched. Every runner here is a fake; no CLI or vendor API starts."""
import asyncio
import json
import os
import sqlite3
import sys
import unicodedata
from collections import defaultdict

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import chat_titles as ct
from database import DatabaseManager
from rag.memory_manager import memory_manager

H = {"X-Session-Token": ""}


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(ct, "_EVENTS", ct.deque(maxlen=256))
    return DatabaseManager(str(tmp_path / "titles.db"))


def _chat(db, replies=0, title="Yeni Sohbet"):
    cid = db.create_conversation(1, title)
    for i in range(replies):
        db.add_message(cid, "user", f"soru {i}")
        db.add_message(cid, "assistant", f"cevap {i}")
    return cid


def _reply(db, cid, n):
    db.add_message(cid, "user", f"soru {n}")
    db.add_message(cid, "assistant", f"cevap {n}")


class _Recorder:
    def __init__(self, answer="Sahne Işıklandırma Ayarı"):
        self.answer = answer
        self.calls = []

    async def __call__(self, choice, prompt, ctx):
        self.calls.append((choice, prompt, ctx))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def _use(monkeypatch, runner, key="claude"):
    monkeypatch.setitem(ct.RUNNERS, key, runner)


def _run(coro):
    return asyncio.run(coro)


async def _reply_and_wait(db, cid, provider="subscription", model="claude-opus-5"):
    task = ct.after_reply(db, cid, provider, model, "")
    if task is not None:
        await task
    return task


def _title(db, cid):
    return db.get_title_state(cid)["title"]


# ── Model mapping ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"])
def test_claude_code_titles_with_haiku(model):
    choice = ct.title_model_for("subscription", model)
    assert (choice.runner, choice.model) == ("claude", "claude-haiku-4-5")
    # effort_caps records --effort as an error on Haiku 4.5.
    assert choice.effort is None


@pytest.mark.parametrize("model,luna", [("gpt-6-sol", "gpt-6-luna"), ("gpt-6-astra", "gpt-6-luna"),
                                        ("gpt-5.6-sol", "gpt-5.6-luna"), ("gpt-5.5", "gpt-5.6-luna")])
def test_codex_titles_with_luna_at_low_effort(model, luna):
    choice = ct.title_model_for("subscription", model)
    assert (choice.runner, choice.model, choice.effort) == ("codex", luna, "low")


def test_codex_luna_ids_are_in_the_model_list():
    import inspect
    import routes.config_routes as cfg
    src = inspect.getsource(cfg)
    assert f'"{ct.CODEX_TITLE_MODEL}"' in src and f'"{ct.CODEX_TITLE_MODEL_GPT6}"' in src
    assert f'"{ct.CLAUDE_TITLE_MODEL}"' in src


@pytest.mark.parametrize("provider,model", [("openai", "gpt-5.5"), ("anthropic", "claude-opus-5"),
                                            ("google", "gemini-3.8-flash"), ("ollama", "qwen2.5-coder:7b")])
def test_api_providers_title_with_their_own_model(provider, model):
    choice = ct.title_model_for(provider, model)
    assert (choice.runner, choice.model) == ("api", model)


def test_unknown_subscription_id_uses_its_own_model_at_low_effort():
    choice = ct.title_model_for("subscription", "mystery-model-9")
    assert (choice.runner, choice.model, choice.effort) == ("claude", "mystery-model-9", "low")


@pytest.mark.parametrize("model", ["copilot-gpt-5.5", "cursor-auto", "cursor-gpt-5.2", "kimi-k3",
                                   "kimi-k2.7-code", "opencode:"])
def test_clis_without_a_measured_tool_less_call_generate_nothing(model):
    choice = ct.title_model_for("subscription", model)
    assert choice.runner == ""
    assert choice.note


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-3.1-pro-preview", "agy-gpt-oss-120b",
                                   "agy-claude-opus-4-6"])
def test_agy_titles_with_flash_low_at_low_effort(model):
    choice = ct.title_model_for("subscription", model)
    assert (choice.runner, choice.model, choice.effort) == ("agy", "gemini-3.8-flash-low", "low")


@pytest.mark.parametrize("model,cli_model", [("opencode:opencode/space-bunny-free", "opencode/space-bunny-free"),
                                             ("opencode:google/gemini-3.1-flash-lite", "google/gemini-3.1-flash-lite")])
def test_opencode_titles_with_the_chats_own_model_at_low_variant(model, cli_model):
    choice = ct.title_model_for("subscription", model)
    assert (choice.runner, choice.model, choice.effort) == ("opencode", cli_model, "low")


def test_copilot_candidate_is_luna_but_it_is_not_run():
    choice = ct.title_model_for("subscription", "copilot-claude-sonnet-5")
    assert choice.runner == "" and choice.model == "copilot-gpt-5.6-luna"
    import inspect
    import routes.config_routes as cfg
    assert f'"{ct.COPILOT_TITLE_MODEL}"' in inspect.getsource(cfg)


def test_agy_title_model_is_one_gamachine_knows():
    from providers.cli_base import BaseCLIProvider
    assert ct.AGY_TITLE_MODEL in BaseCLIProvider._AGY_MODEL_MAP


# ── Trigger ─────────────────────────────────────────────────────────────────

def test_fires_after_reply_1_and_3_only(db, monkeypatch):
    rec = _Recorder()
    _use(monkeypatch, rec)
    cid = _chat(db, replies=1)
    _run(_reply_and_wait(db, cid))
    assert len(rec.calls) == 1 and _title(db, cid) == "Sahne Işıklandırma Ayarı"
    for n in (2, 3, 4, 5):
        rec.answer = f"Başlık {n}"
        _reply(db, cid, n)
        _run(_reply_and_wait(db, cid))
    assert len(rec.calls) == 2
    assert _title(db, cid) == "Başlık 3"


def test_never_more_than_two_generations(db, monkeypatch):
    rec = _Recorder()
    _use(monkeypatch, rec)
    cid = _chat(db, replies=1)
    _run(_reply_and_wait(db, cid))
    _reply(db, cid, 2)
    _reply(db, cid, 3)
    _run(_reply_and_wait(db, cid))
    # Compaction or deleted rows can bring the count back to 1 or 3.
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ? AND id > "
                     "(SELECT MIN(id) + 1 FROM messages WHERE conversation_id = ?)", (cid, cid))
    assert db.count_own_assistant_replies(cid) == 1
    _run(_reply_and_wait(db, cid))
    assert len(rec.calls) == 2
    assert db.get_title_state(cid)["auto_title_runs"] == 2


def test_chat_first_seen_at_reply_3_still_gets_one_title(db, monkeypatch):
    rec = _Recorder()
    _use(monkeypatch, rec)
    cid = _chat(db, replies=3)
    _run(_reply_and_wait(db, cid))
    assert len(rec.calls) == 1


def test_branch_counts_only_its_own_replies(db, monkeypatch):
    rec = _Recorder("Dal Konusu")
    _use(monkeypatch, rec)
    root = _chat(db, replies=2, title="Kök")
    branch = db.create_branch(root)["id"]
    assert db.count_own_assistant_replies(branch) == 0
    _reply(db, branch, 9)
    _run(_reply_and_wait(db, branch))
    assert _title(db, branch) == "Dal Konusu"
    # Only the branch's own messages are named.
    prompt = rec.calls[0][1]
    assert "soru 9" in prompt and "soru 0" not in prompt


def test_user_renamed_chat_is_never_touched(db, monkeypatch):
    rec = _Recorder()
    _use(monkeypatch, rec)
    cid = _chat(db)
    db.rename_conversation(cid, "Benim adım")
    _reply(db, cid, 1)
    _run(_reply_and_wait(db, cid))
    _reply(db, cid, 2)
    _reply(db, cid, 3)
    _run(_reply_and_wait(db, cid))
    assert rec.calls == []
    assert _title(db, cid) == "Benim adım"


def test_rename_during_a_running_job_wins(db, monkeypatch):
    started, release = asyncio.Event, asyncio.Event
    box = {}

    async def slow(choice, prompt, ctx):
        box["started"].set()
        await box["release"].wait()
        return "Yapay Zeka Başlığı"

    _use(monkeypatch, slow)
    cid = _chat(db, replies=1)

    async def scenario():
        box["started"], box["release"] = started(), release()
        task = ct.after_reply(db, cid, "subscription", "claude-opus-5")
        await box["started"].wait()
        db.rename_conversation(cid, "Kullanıcının adı")
        box["release"].set()
        await task

    _run(scenario())
    assert _title(db, cid) == "Kullanıcının adı"
    assert ct.updates_after(0, 1)[1] == []


def test_one_job_per_chat_at_a_time(db, monkeypatch):
    box = {}

    async def slow(choice, prompt, ctx):
        await box["release"].wait()
        return "Tek İş"

    _use(monkeypatch, slow)
    cid = _chat(db, replies=1)

    async def scenario():
        box["release"] = asyncio.Event()
        first = ct.after_reply(db, cid, "subscription", "claude-opus-5")
        second = ct.after_reply(db, cid, "subscription", "claude-opus-5")
        assert first is not None and second is None
        box["release"].set()
        await first

    _run(scenario())
    assert db.get_title_state(cid)["auto_title_runs"] == 1


def test_setting_off_generates_nothing(db, monkeypatch):
    rec = _Recorder()
    _use(monkeypatch, rec)
    ct.set_enabled(db, False)
    cid = _chat(db, replies=1)
    assert _run(_reply_and_wait(db, cid)) is None
    assert rec.calls == [] and db.get_title_state(cid)["auto_title_runs"] == 0


def test_setting_defaults_to_on(db):
    assert db.get_setting(ct.SETTING_KEY) is None
    assert ct.enabled(db) is True


@pytest.mark.parametrize("model", ["copilot-gpt-5.5", "cursor-auto", "kimi-k3"])
def test_skipped_cli_spends_no_generation_and_starts_no_process(db, monkeypatch, model):
    async def no_spawn(*argv, **kw):
        raise AssertionError(f"a disabled CLI started a process: {argv}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_spawn)
    cid = _chat(db, replies=1, title="kesik başlık...")
    assert _run(_reply_and_wait(db, cid, model=model)) is None
    assert db.get_title_state(cid)["auto_title_runs"] == 0
    assert _title(db, cid) == "kesik başlık..."


def test_side_chat_is_never_titled(db, monkeypatch):
    rec = _Recorder()
    _use(monkeypatch, rec)
    main = _chat(db, replies=1)
    side = db.create_side_chat(main, 1)
    db.add_message(side, "user", "yan")
    db.add_message(side, "assistant", "cevap")
    assert _run(_reply_and_wait(db, side)) is None
    assert rec.calls == []


# ── Failures keep the title ─────────────────────────────────────────────────

def test_runner_error_keeps_the_title(db, monkeypatch):
    _use(monkeypatch, _Recorder(RuntimeError("boom")))
    cid = _chat(db, replies=1, title="eski başlık")
    _run(_reply_and_wait(db, cid))
    assert _title(db, cid) == "eski başlık"
    assert cid not in ct._RUNNING


def test_timeout_keeps_the_title(db, monkeypatch):
    async def never(choice, prompt, ctx):
        await asyncio.sleep(30)

    _use(monkeypatch, never)
    monkeypatch.setattr(ct, "TIMEOUT_S", 0.05)
    cid = _chat(db, replies=1, title="eski başlık")
    _run(_reply_and_wait(db, cid))
    assert _title(db, cid) == "eski başlık"
    assert cid not in ct._RUNNING


@pytest.mark.parametrize("answer", ["", "   ", "❌ Anthropic API Hatası: model hatası", "\"\"", "🎮🎮",
                                    "‮​﻿\x07"])
def test_unusable_answer_keeps_the_title(db, monkeypatch, answer):
    _use(monkeypatch, _Recorder(answer))
    cid = _chat(db, replies=1, title="eski başlık")
    _run(_reply_and_wait(db, cid))
    assert _title(db, cid) == "eski başlık"


# ── Input and sanitizer ─────────────────────────────────────────────────────

def test_input_skips_system_rows_and_stays_under_the_cap():
    msgs = [{"role": "system", "content": "[Posta] başka sohbetten not"},
            {"role": "user", "content": "x" * 5000},
            {"role": "assistant", "content": "y" * 5000},
            {"role": "user", "content": "z" * 5000},
            {"role": "assistant", "content": "w" * 5000}]
    text = ct.build_title_input(msgs)
    assert "Posta" not in text
    assert len(text) <= ct.INPUT_CAP
    assert text.startswith("User: x") and "Assistant: y" in text


def test_prompt_asks_for_a_short_title_in_the_chat_language():
    p = ct.build_title_prompt("User: merhaba")
    assert "3 to 6" in p and "same language" in p and "User: merhaba" in p


@pytest.mark.parametrize("raw,expected", [
    ('"Oyuncu Hareket Sistemi"', "Oyuncu Hareket Sistemi"),
    ("“Işık ve Gölge Ayarı”", "Işık ve Gölge Ayarı"),
    ("**Title:** Enemy Spawner Fix", "Enemy Spawner Fix"),
    ("Başlık: Çağrı Şeması Güncellemesi.", "Çağrı Şeması Güncellemesi"),
    ("# Sahne Yükleme Hatası", "Sahne Yükleme Hatası"),
    ("Envanter Arayüzü\nBu başlık envanteri anlatıyor.", "Envanter Arayüzü"),
    ("```\nKamera Takibi\n```", "Kamera Takibi"),
    ("🎮 Oyun Döngüsü Tasarımı 🚀", "Oyun Döngüsü Tasarımı"),
    ("  çok   boşluklu    başlık  ", "çok boşluklu başlık"),
    ("İğneli Şişe Ölçümü!", "İğneli Şişe Ölçümü"),
])
def test_sanitizer(raw, expected):
    assert ct.sanitize_title(raw) == expected


# Codex eveaudit, 27 Sep 2026: control and bidi characters reached the saved title.
@pytest.mark.parametrize("raw,expected", [
    ("Alpha‮Beta\x07 Gamma", "AlphaBeta Gamma"),
    ("⁦Sahne­ Yükleme⁩", "Sahne Yükleme"),
    ("﻿​Kamera‌ Takibi‍", "Kamera Takibi"),
    ("‭‪Çağrı‬ Şeması‎‏", "Çağrı Şeması"),
    ("Envanter\U000f0000 Arayüzü", "Envanter Arayüzü"),
    ("Oyun\tDöngüsü", "Oyun Döngüsü"),
    ("Envanter Arayüzü ikinci satır", "Envanter Arayüzü"),
    ("‮​\nIşık Ayarı", "Işık Ayarı"),
    ("Düşman: Yapay Zekâ (v2), İlk Adım", "Düşman: Yapay Zekâ (v2), İlk Adım"),
])
def test_sanitizer_drops_invisible_characters(raw, expected):
    out = ct.sanitize_title(raw)
    assert out == expected
    assert not any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp") for ch in out)


def test_sanitizer_caps_long_titles_on_a_word_boundary():
    raw = "Bu çok uzun bir başlık ve altmış karakteri kesinlikle aşan bir cümle olarak yazıldı"
    out = ct.sanitize_title(raw)
    assert len(out) <= ct.TITLE_MAX
    assert raw.startswith(out) and not out.endswith(" ")


@pytest.mark.parametrize("raw", [None, "", "\n\n", '""', "...", "🎮", "❌ hata", "SİSTEM MESAJI: model yok",
                                 "‮​﻿\x07", "⁦­⁩"])
def test_sanitizer_rejects_garbage(raw):
    assert ct.sanitize_title(raw) is None


# ── Runners: no tools, no MCP, no session ───────────────────────────────────

def test_claude_options_carry_no_tools_mcp_or_settings(tmp_path):
    opts = ct.claude_title_options("claude-haiku-4-5", str(tmp_path))
    assert opts.tools == []
    assert opts.mcp_servers == {}
    assert opts.strict_mcp_config is True
    assert opts.setting_sources == []
    assert opts.max_turns == 1
    assert opts.resume is None and opts.can_use_tool is None
    assert opts.model == "claude-haiku-4-5" and opts.cwd == str(tmp_path)


def test_claude_command_line_disables_tools_mcp_and_settings(tmp_path):
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
    t = SubprocessCLITransport(prompt="x", options=ct.claude_title_options("claude-haiku-4-5", str(tmp_path)))
    t._cli_path = "claude"
    cmd = t._build_command()
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in cmd and "--mcp-config" not in cmd
    assert "--setting-sources=" in cmd
    assert "--resume" not in cmd and "--continue" not in cmd


def test_claude_runner_uses_a_scratch_cwd_and_closes_the_stream(monkeypatch):
    import claude_agent_sdk as sdk
    seen = {}

    class _Stream:
        def __init__(self, options):
            self.options = options
            self.closed = False
            self._msgs = [sdk.AssistantMessage(content=[sdk.TextBlock(text="Haiku Başlığı")], model="m")]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._msgs:
                raise StopAsyncIteration
            return self._msgs.pop(0)

        async def aclose(self):
            self.closed = True

    def fake_query(*, prompt, options):
        seen["prompt"], seen["stream"] = prompt, _Stream(options)
        seen["cwd_listing"] = __import__("os").listdir(options.cwd)
        return seen["stream"]

    monkeypatch.setattr(sdk, "query", fake_query)
    choice = ct.title_model_for("subscription", "claude-opus-5")
    out = _run(ct._run_claude(choice, "PROMPT", ct.RunContext("subscription", "")))
    assert out == "Haiku Başlığı"
    assert seen["stream"].closed and seen["cwd_listing"] == []
    assert seen["stream"].options.tools == [] and seen["stream"].options.mcp_servers == {}
    assert not __import__("os").path.exists(seen["stream"].options.cwd)


def test_codex_args_disable_shell_and_every_configured_mcp():
    args = ct.codex_title_args("gpt-6-luna", "low", {"unityai", "unityMCP", "my-server"})
    assert args[:3] == ["exec", "-m", "gpt-6-luna"]
    joined = " ".join(args)
    assert "--disable shell_tool" in joined and "--disable unified_exec" in joined
    assert "-s read-only" in joined and "--skip-git-repo-check" in joined
    assert "model_reasoning_effort=low" in args
    for name in ("unityai", "unityMCP", "my-server"):
        assert f"mcp_servers.{name}.enabled=false" in args
    assert "approve" not in joined and "--mcp-config" not in joined
    assert "resume" not in args


def test_codex_refuses_an_mcp_name_it_cannot_disable():
    with pytest.raises(ct.TitleRunError):
        ct.codex_title_args("gpt-6-luna", "low", {"weird.name"})


def test_codex_runner_sends_the_prompt_on_stdin_from_a_scratch_dir(monkeypatch):
    import providers.cli_base as cb
    import providers.codex_session as cs
    seen = {}

    class _Proc:
        returncode = None
        pid = 1

        async def communicate(self, data):
            seen["stdin"] = data.decode("utf-8")
            self.returncode = 0
            lines = [{"type": "thread.started"},
                     {"type": "item.completed", "item": {"type": "agent_message", "text": "Luna Başlığı"}}]
            return ("\n".join(json.dumps(x) for x in lines).encode(), b"")

    async def fake_exec(*argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return _Proc()

    monkeypatch.setattr(cb.BaseCLIProvider, "_cli_installed", staticmethod(lambda n: True))
    monkeypatch.setattr(cs, "_configured_codex_mcp_names", lambda: {"unityai"})
    monkeypatch.setattr(cs, "_resolve_codex_appserver_cmd", lambda: ["node", "codex.js", "app-server"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    choice = ct.title_model_for("subscription", "gpt-6-sol")
    out = _run(ct._run_codex(choice, "PROMPT", ct.RunContext("subscription", "")))
    assert out == "Luna Başlığı"
    assert seen["argv"][:4] == ("node", "codex.js", "exec", "-m")
    assert "app-server" not in seen["argv"] and "PROMPT" not in seen["argv"]
    assert seen["stdin"] == "PROMPT"
    assert "mcp_servers.unityai.enabled=false" in seen["argv"]
    assert "LOCAL_APP_TOKEN" not in seen["kw"]["env"]
    assert "gamachine-title-" in seen["kw"]["cwd"]


def test_api_runner_is_a_plain_completion(monkeypatch):
    import providers.manager as pm
    seen = {}

    class _Provider:
        def analyze_code(self, prompt, max_tokens=4096, images=None):
            seen["args"] = (prompt, max_tokens, images)
            return "API Başlığı"

    monkeypatch.setattr(pm.AIProviderManager, "get_provider",
                        staticmethod(lambda cfg: seen.setdefault("cfg", cfg) and _Provider()))
    choice = ct.title_model_for("openai", "gpt-5.5")
    out = _run(ct._run_api(choice, "PROMPT", ct.RunContext("openai", "sk-test")))
    assert out == "API Başlığı"
    assert seen["cfg"] == {"provider_type": "openai", "model_name": "gpt-5.5", "api_key": "sk-test"}
    assert seen["args"] == ("PROMPT", ct.API_MAX_TOKENS, None)


def test_api_runner_refuses_a_cli_provider(monkeypatch):
    import providers.manager as pm
    from providers.claude_provider import ClaudeCodeProvider
    monkeypatch.setattr(pm.AIProviderManager, "get_provider",
                        staticmethod(lambda cfg: ClaudeCodeProvider(binary_name="claude-x")))
    with pytest.raises(ct.TitleRunError):
        _run(ct._run_api(ct.TitleModel("api", "x", None, ""), "P", ct.RunContext("subscription", "")))


# ── agy and OpenCode runners (fake processes only) ──────────────────────────

class _FakeProc:
    def __init__(self, out=b"", rc=0, hang=False):
        self.pid = 4242
        self.returncode = None
        self._out, self._rc, self._hang = out, rc, hang
        self.stdin_data = None
        self.killed = False

    async def communicate(self, data=None):
        self.stdin_data = data
        if self._hang:
            await asyncio.sleep(60)
        self.returncode = self._rc
        return self._out, b""

    async def wait(self):
        while self.returncode is None and self._hang:
            await asyncio.sleep(0.01)
        if self.returncode is None:
            self.returncode = self._rc
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class _Spawner:
    """Stands in for asyncio.create_subprocess_exec; `results` are handed out in order."""

    def __init__(self, *results, on_spawn=None):
        self.results = list(results)
        self.calls = []
        self.on_spawn = on_spawn

    async def __call__(self, *argv, **kw):
        self.calls.append((argv, kw))
        if self.on_spawn:
            self.on_spawn(argv, kw)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _made_dirs(monkeypatch):
    """Records every temp dir the runner creates."""
    made = []
    real = ct.tempfile.mkdtemp

    def mkdtemp(*a, **kw):
        path = real(*a, **kw)
        made.append(path)
        return path

    monkeypatch.setattr(ct.tempfile, "mkdtemp", mkdtemp)
    return made


@pytest.fixture
def fake_agy(monkeypatch):
    from providers.agy_provider import AgyProvider
    from providers.cli_base import BaseCLIProvider
    monkeypatch.setattr(AgyProvider, "_agy_binary", staticmethod(lambda: "agy.exe"))
    monkeypatch.setattr(BaseCLIProvider, "_cli_installed", staticmethod(lambda n: True))
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", staticmethod(lambda c: list(c)))
    monkeypatch.setenv("GEMINI_CONFIG_DIR", "C:/real/gemini")
    return _made_dirs(monkeypatch)


def _agy_answer(text="Zıplama Mekaniği Ayarı"):
    return json.dumps({"response": text, "status": "success", "usage": {}}).encode()


PROMPT = ct.build_title_prompt('User: "/help" && del *\nAssistant: tamam')


def test_agy_runner_argv_env_agent_file_and_cleanup(fake_agy, monkeypatch):
    seen = {}

    def on_spawn(argv, kw):
        cwd, home = kw["cwd"], kw["env"]["HOME"]
        seen["agent"] = open(os.path.join(cwd, ".agents", "agents", "titler", "agent.md"),
                             encoding="utf-8").read()
        seen["cwd_listing"] = sorted(os.listdir(cwd))
        seen["home_listing"] = os.listdir(home)

    spawner = _Spawner(_FakeProc(_agy_answer()), on_spawn=on_spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    choice = ct.title_model_for("subscription", "gemini-3.8-flash")
    out = _run(ct._run_agy(choice, PROMPT, ct.RunContext("subscription", "")))
    assert out == "Zıplama Mekaniği Ayarı"

    argv, kw = spawner.calls[0]
    assert list(argv) == ["agy.exe", "--agent", "titler", "--model", "gemini-3.8-flash-low",
                          "--effort", "low", "--sandbox", "--disable-slash-commands",
                          "--output-format", "json", f"--print={PROMPT}"]
    # The whole prompt is one element; no shell sits in front of agy.
    assert sum(PROMPT in a for a in argv) == 1 and argv[0] != "cmd"
    env = kw["env"]
    assert env["HOME"] == env["USERPROFILE"] == kw["env"]["HOME"]
    assert env["HOME"] != kw["cwd"] and seen["home_listing"] == []
    assert "GEMINI_CONFIG_DIR" not in env
    assert "LOCAL_APP_TOKEN" not in env and "API_KEY_ENCRYPTION_KEY" not in env
    if sys.platform == "win32":
        assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert kw["stdin"] == asyncio.subprocess.DEVNULL
    assert seen["cwd_listing"] == [".agents"]
    assert seen["agent"] == ("---\nname: titler\ndescription: Writes a short chat title. Uses no tools.\n"
                             "tools: []\n---\nYou write short chat titles. Reply with the title only.\n")
    assert len(fake_agy) == 2 and not any(os.path.exists(d) for d in fake_agy)


def test_agy_chat_gets_its_title_after_the_first_reply(db, fake_agy, monkeypatch):
    spawner = _Spawner(_FakeProc(_agy_answer("Kamera Takip Sistemi")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    _use(monkeypatch, ct._run_agy, "agy")
    cid = _chat(db, replies=1)
    _run(_reply_and_wait(db, cid, model="gemini-3.8-flash"))
    assert _title(db, cid) == "Kamera Takip Sistemi"
    assert db.get_title_state(cid)["auto_title_runs"] == 1 and len(spawner.calls) == 1


def test_agy_runner_does_not_wait_for_the_agy_turn_lock(fake_agy, monkeypatch):
    from providers.cli_base import BaseCLIProvider
    monkeypatch.setattr(BaseCLIProvider, "_AGY_LOCK", asyncio.Lock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _Spawner(_FakeProc(_agy_answer())))

    async def scenario():
        await BaseCLIProvider._AGY_LOCK.acquire()  # a chat turn is running
        try:
            choice = ct.title_model_for("subscription", "gemini-3.8-flash")
            return await asyncio.wait_for(
                ct._run_agy(choice, "P", ct.RunContext("subscription", "")), timeout=2)
        finally:
            BaseCLIProvider._AGY_LOCK.release()

    assert _run(scenario()) == "Zıplama Mekaniği Ayarı"


def test_agy_timeout_kills_the_tree_and_removes_both_dirs(fake_agy, monkeypatch):
    proc = _FakeProc(hang=True)
    killed = []

    async def fake_kill(p):
        killed.append(p)
        p.kill()

    monkeypatch.setattr(ct, "_kill_tree", fake_kill)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _Spawner(proc))
    choice = ct.title_model_for("subscription", "gemini-3.8-flash")

    async def scenario():
        await asyncio.wait_for(ct._run_agy(choice, "P", ct.RunContext("subscription", "")), timeout=0.2)

    with pytest.raises(asyncio.TimeoutError):
        _run(scenario())
    assert killed == [proc]
    assert len(fake_agy) == 2 and not any(os.path.exists(d) for d in fake_agy)


def test_agy_spawn_failure_still_removes_both_dirs(fake_agy, monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _Spawner(OSError("no agy")))
    choice = ct.title_model_for("subscription", "gemini-3.8-flash")
    with pytest.raises(OSError):
        _run(ct._run_agy(choice, "P", ct.RunContext("subscription", "")))
    assert len(fake_agy) == 2 and not any(os.path.exists(d) for d in fake_agy)


def test_agy_without_an_answer_keeps_the_title(fake_agy, monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _Spawner(_FakeProc(b"not json", rc=1)))
    choice = ct.title_model_for("subscription", "gemini-3.8-flash")
    with pytest.raises(ct.TitleRunError):
        _run(ct._run_agy(choice, "P", ct.RunContext("subscription", "")))


def test_agy_behind_a_shell_shim_is_refused(fake_agy, monkeypatch):
    from providers.cli_base import BaseCLIProvider
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec",
                        staticmethod(lambda c: ["cmd", "/c", "C:/x/agy.cmd", *c[1:]]))
    spawner = _Spawner()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    choice = ct.title_model_for("subscription", "gemini-3.8-flash")
    with pytest.raises(ct.TitleRunError):
        _run(ct._run_agy(choice, "P", ct.RunContext("subscription", "")))
    assert spawner.calls == [] and fake_agy == []


def test_agy_json_parser():
    assert ct.parse_agy_json('{"response": "Başlık", "status": "success"}') == "Başlık"
    assert ct.parse_agy_json('log line\n{"response": "Son", "usage": {}}\n') == "Son"
    assert ct.parse_agy_json('{"status": "error"}') == ""
    assert ct.parse_agy_json("") == ""


def test_scratch_removal_touches_only_its_own_dirs(tmp_path, monkeypatch):
    other = tmp_path / "user-data"
    other.mkdir()
    (other / "keep.txt").write_text("x")
    _run(ct._remove_scratch(str(other), None))
    assert (other / "keep.txt").exists()
    # A file held open by a dying child: logged, never raised.
    mine = tmp_path / f"{ct.SCRATCH_PREFIX}locked"
    mine.mkdir()
    monkeypatch.setattr(ct.shutil, "rmtree", lambda *a, **kw: None)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    _run(ct._remove_scratch(str(mine)))
    assert mine.exists()


async def _no_sleep(*_a, **_kw):
    return None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows kills the tree with taskkill")
def test_kill_tree_uses_taskkill_on_the_whole_tree(monkeypatch):
    killer = _FakeProc()
    spawner = _Spawner(killer)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    proc = _FakeProc(hang=True)
    _run(ct._kill_tree(proc))
    argv, kw = spawner.calls[0]
    assert list(argv) == ["taskkill", "/PID", "4242", "/T", "/F"]
    assert "LOCAL_APP_TOKEN" not in kw["env"]
    assert proc.killed


@pytest.fixture
def fake_opencode(monkeypatch):
    import providers.oneshot_cli as oc
    monkeypatch.setattr(oc, "resolve_opencode_cmd", lambda: ["opencode.exe"])
    monkeypatch.setattr(ct, "_CLEANUPS", set())
    return _made_dirs(monkeypatch)


def _oc_events(*events):
    return "\n".join(json.dumps(e) for e in events).encode()


SID = "ses_f1c227733ffetbzrITjUNjt70a"
OC_OK = _oc_events({"type": "step_start", "sessionID": SID},
                   {"type": "text", "sessionID": SID, "part": {"text": "Unity CharacterController jump"}},
                   {"type": "step_finish", "sessionID": SID})
OC_403 = _oc_events({"type": "error", "sessionID": SID,
                     "error": {"name": "FreeTierError", "data": {"message": "403 free tier"}}})


async def _run_opencode_and_cleanups(choice, prompt="PROMPT"):
    try:
        return await ct._run_opencode(choice, prompt, ct.RunContext("subscription", ""))
    finally:
        await asyncio.gather(*list(ct._CLEANUPS), return_exceptions=True)


def test_opencode_runner_argv_env_stdin_and_session_delete(fake_opencode, monkeypatch):
    seen = {}
    spawner = _Spawner(_FakeProc(OC_OK), _FakeProc(),
                       on_spawn=lambda argv, kw: seen.setdefault("listings", []).append(os.listdir(kw["cwd"])))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    choice = ct.title_model_for("subscription", "opencode:opencode/space-bunny-free")
    out = _run(_run_opencode_and_cleanups(choice))
    assert out == "Unity CharacterController jump"

    (argv, kw), (del_argv, del_kw) = spawner.calls
    assert list(argv) == ["opencode.exe", "run", "--pure", "--format", "json",
                          "-m", "opencode/space-bunny-free", "--variant", "low",
                          "--title", "gamachine-title"]
    assert "PROMPT" not in argv
    env = kw["env"]
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"] == {
        "*": "deny", "read": "allow", "external_directory": "deny"}
    for name in ("OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_DISABLE_CLAUDE_CODE",
                 "OPENCODE_DISABLE_EXTERNAL_SKILLS", "OPENCODE_DISABLE_DEFAULT_PLUGINS",
                 "OPENCODE_DISABLE_AUTOUPDATE", "OPENCODE_DISABLE_LSP_DOWNLOAD"):
        assert env[name] == "1"
    assert "OPENCODE_CONFIG_DIR" not in env and "LOCAL_APP_TOKEN" not in env
    assert kw["stdin"] == asyncio.subprocess.PIPE
    assert spawner.results == [] and seen["listings"][0] == []
    assert list(del_argv) == ["opencode.exe", "session", "delete", SID]
    assert del_kw["stdin"] == asyncio.subprocess.DEVNULL
    assert fake_opencode and not any(os.path.exists(d) for d in fake_opencode)


def test_opencode_stdin_carries_the_prompt(fake_opencode, monkeypatch):
    proc = _FakeProc(OC_OK)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _Spawner(proc, _FakeProc()))
    choice = ct.title_model_for("subscription", "opencode:opencode/space-bunny-free")
    _run(_run_opencode_and_cleanups(choice, PROMPT))
    assert proc.stdin_data == PROMPT.encode("utf-8")


def test_opencode_403_keeps_the_title_counts_the_run_and_deletes_the_session(db, fake_opencode, monkeypatch):
    spawner = _Spawner(_FakeProc(OC_403, rc=1), _FakeProc())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    # conftest refuses every real runner; this one only meets fake processes.
    _use(monkeypatch, ct._run_opencode, "opencode")
    cid = _chat(db, replies=1, title="ilk mesaj kesiti")

    async def scenario():
        await _reply_and_wait(db, cid, model="opencode:opencode/ling-3.0-flash-fin-free")
        await asyncio.gather(*list(ct._CLEANUPS), return_exceptions=True)

    _run(scenario())
    assert _title(db, cid) == "ilk mesaj kesiti"
    assert db.get_title_state(cid)["auto_title_runs"] == 1
    # One run, no retry; the refused call's session is still deleted.
    assert [c[0][1] for c in spawner.calls] == ["run", "session"]


def test_opencode_without_a_session_id_deletes_nothing(fake_opencode, monkeypatch):
    spawner = _Spawner(_FakeProc(_oc_events({"type": "text", "part": {"text": "Başlık"}})))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    choice = ct.title_model_for("subscription", "opencode:opencode/space-bunny-free")
    assert _run(_run_opencode_and_cleanups(choice)) == "Başlık"
    assert len(spawner.calls) == 1


def test_opencode_session_id_is_validated_before_it_reaches_argv(fake_opencode, monkeypatch):
    spawner = _Spawner(_FakeProc(_oc_events({"type": "text", "sessionID": "--all", "part": {"text": "B"}},
                                            {"type": "text", "sessionID": "a b", "part": {"text": "C"}})))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    choice = ct.title_model_for("subscription", "opencode:opencode/space-bunny-free")
    assert _run(_run_opencode_and_cleanups(choice)) == "BC"
    assert len(spawner.calls) == 1


@pytest.mark.parametrize("failure", [OSError("gone"), "hang", "rc"])
def test_a_failed_session_delete_never_costs_the_title(db, fake_opencode, monkeypatch, failure):
    if failure == "hang":
        second = _FakeProc(hang=True)
        monkeypatch.setattr(ct, "OPENCODE_DELETE_TIMEOUT_S", 0.05)

        async def fake_kill(p):
            p.kill()

        monkeypatch.setattr(ct, "_kill_tree", fake_kill)
    elif failure == "rc":
        second = _FakeProc(rc=1)
    else:
        second = failure
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _Spawner(_FakeProc(OC_OK), second))
    _use(monkeypatch, ct._run_opencode, "opencode")
    cid = _chat(db, replies=1)

    async def scenario():
        await _reply_and_wait(db, cid, model="opencode:opencode/space-bunny-free")
        await asyncio.gather(*list(ct._CLEANUPS), return_exceptions=True)

    _run(scenario())
    assert _title(db, cid) == "Unity CharacterController jump"
    assert not any(os.path.exists(d) for d in fake_opencode)


def test_opencode_not_installed_keeps_the_title(fake_opencode, monkeypatch):
    import providers.oneshot_cli as oc
    monkeypatch.setattr(oc, "resolve_opencode_cmd", lambda: None)
    spawner = _Spawner()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)
    choice = ct.title_model_for("subscription", "opencode:opencode/space-bunny-free")
    with pytest.raises(ct.TitleRunError):
        _run(_run_opencode_and_cleanups(choice))
    assert spawner.calls == []


def test_opencode_timeout_kills_the_tree_and_removes_the_dir(fake_opencode, monkeypatch):
    proc = _FakeProc(hang=True)
    killed = []

    async def fake_kill(p):
        killed.append(p)
        p.kill()

    monkeypatch.setattr(ct, "_kill_tree", fake_kill)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _Spawner(proc))
    choice = ct.title_model_for("subscription", "opencode:opencode/space-bunny-free")

    async def scenario():
        await asyncio.wait_for(ct._run_opencode(choice, "P", ct.RunContext("subscription", "")), 0.2)

    with pytest.raises(asyncio.TimeoutError):
        _run(scenario())
    assert killed == [proc]
    assert fake_opencode and not any(os.path.exists(d) for d in fake_opencode)


# ── Migration ───────────────────────────────────────────────────────────────

def test_migration_on_an_old_db_marks_existing_rows_auto(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "user_id INTEGER NOT NULL, title TEXT DEFAULT 'Yeni Sohbet', "
                     "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO conversations (user_id, title, created_at, updated_at) "
                     "VALUES (1, 'eski sohbet', 'x', 'x')")
    db = DatabaseManager(path)
    state = db.get_title_state(1)
    assert state["title_source"] == "auto" and state["auto_title_runs"] == 0
    assert db.set_auto_title(1, "yeni") is True
    db.rename_conversation(1, "elle")
    assert db.get_title_state(1)["title_source"] == "user"
    assert db.set_auto_title(1, "yapay") is False
    DatabaseManager(path)  # a second start must not fail on existing columns
    assert db.get_title_state(1)["title"] == "elle"


# ── Routes ──────────────────────────────────────────────────────────────────

class _FakeRunner:
    def __init__(self, **kw):
        self.kw = kw

    async def run(self, message):
        yield ar.AgentEvent("response", {"content": "tamam"})
        yield ar.AgentEvent("done", {"iterations": 1, "stop_reason": "complete"})


@pytest.fixture
def app(db, tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "CHAT_RATE_LIMIT", defaultdict(list))
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_manager, "base_dir", mem_dir)
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    db.save_ai_config(1, "subscription", "claude-opus-5", "")
    fast = FastAPI()
    router = cr.create_conversation_router(db, {})
    fast.include_router(router)
    with TestClient(fast) as client:
        yield client, router


def _wait_for(pred, timeout=5.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_chat_stream_titles_the_chat_in_the_background(db, app, monkeypatch):
    client, _ = app
    rec = _Recorder("Arka Plan Başlığı")
    _use(monkeypatch, rec)
    cid = db.create_conversation(1, "Yeni Sohbet")
    r = client.post("/chat-stream", json={"conversation_id": cid, "message": "zıplama mekaniği nasıl olmalı",
                                          "user_id": 1}, headers=H)
    assert r.status_code == 200 and '"type": "done"' in r.text
    assert _wait_for(lambda: _title(db, cid) == "Arka Plan Başlığı")
    choice, prompt, _ctx = rec.calls[0]
    assert choice.model == "claude-haiku-4-5" and "zıplama mekaniği" in prompt
    # The title call wrote no message and left no turn running.
    assert [m["role"] for m in db.get_conversation_messages(cid)] == ["user", "assistant"]
    from agentic.approval_policy import conversation_turn_in_flight
    assert not conversation_turn_in_flight(cid)


def test_first_message_cut_respects_a_name_given_before_the_first_reply(db, app):
    client, _ = app
    cid = db.create_conversation(1, "Yeni Sohbet")
    assert client.put(f"/conversations/{cid}", json={"title": "Önceden adlandı"}, headers=H).status_code == 200
    client.post("/chat-stream", json={"conversation_id": cid, "message": "ilk mesaj", "user_id": 1}, headers=H)
    assert _title(db, cid) == "Önceden adlandı"


def test_rename_route_marks_the_title_as_the_users(db, app):
    client, _ = app
    cid = db.create_conversation(1, "Yeni Sohbet")
    client.put(f"/conversations/{cid}", json={"title": "Elle"}, headers=H)
    assert db.get_title_state(cid)["title_source"] == "user"


def test_setting_routes_round_trip(db, app):
    client, _ = app
    assert client.get("/chat-title-setting", headers=H).json() == {"enabled": True}
    assert client.post("/chat-title-setting", json={"enabled": False}, headers=H).json() == {"enabled": False}
    assert client.get("/chat-title-setting", headers=H).json() == {"enabled": False}
    assert client.post("/chat-title-setting", json={"enabled": "no"}, headers=H).status_code == 400


def test_wake_stream_all_carries_the_title_frame(db, app):
    _, router = app
    cid = db.create_conversation(1, "Yeni Sohbet")
    stale = db.create_conversation(1, "Elle verilen")
    db.set_auto_title(cid, "Canlı Başlık")
    ct._publish(1, stale, "Eskimiş Başlık")  # renamed over since: must not be sent
    ct._publish(1, cid, "Canlı Başlık")
    ct._publish(2, cid, "Başka kullanıcı")
    endpoint = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/wake-stream-all")

    async def first_frame():
        resp = await endpoint(x_session_token="")
        it = resp.body_iterator
        try:
            return await asyncio.wait_for(it.__anext__(), timeout=5)
        finally:
            await it.aclose()

    frame = _run(first_frame())
    data = json.loads(frame[len("data: "):])
    assert data == {"type": "title", "conversation_id": cid, "title": "Canlı Başlık"}


def test_stream_start_replays_only_the_last_minute(monkeypatch):
    monkeypatch.setattr(ct, "_EVENTS", ct.deque(maxlen=256))
    ct._publish(1, 5, "Taze")
    seq, items = ct.updates_after(ct.stream_start_seq(), 1)
    assert [i["title"] for i in items] == ["Taze"]
    monkeypatch.setattr(ct, "REPLAY_S", -1.0)
    seq, items = ct.updates_after(ct.stream_start_seq(), 1)
    assert items == []
