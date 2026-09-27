"""Side question turn text order, on every provider path.

Live test 27 Sep 2026: a side question ("selam nasılsın") asked while the main
chat ran "detaylıca anlatır mısın bunların ne olduğunu" was answered with the
MAIN chat's request. The question went first, the main history after it under
"continue where you left off", and the running request was its last line.
"""
import asyncio
import os
from unittest.mock import patch

import agentic.agent_runner as ar
import routes.conversation_routes as cr
from agentic import side_prompt as sp
from agentic.approval_policy import ambient_turn

from tests.test_side_chat import H, _FakeRunner, _open_side, _stream, env  # noqa: F401  (fixture)

# Snapshot of the generic texts as they stood before the side fix: a normal
# turn must keep them byte for byte, a side turn must never carry them.
_GENERIC_HANDOFF_HEADER = (
    "[ÖNCEKİ KONUŞMA BAĞLAMI — bu geçmiş sana YETERLİ bağlamı veriyor. Kullanıcı "
    "'kaldığımız yeri/ne yaptığımızı biliyor musun' tarzı bir şey soruyorsa, dosya "
    "okuma/tarama/web araması YAPMADAN doğrudan bu geçmişten özetleyerek yanıtla.]"
)
_GENERIC_HISTORY_HEADER = (
    "[SOHBET GEÇMİŞİ — bu konuşma başka bir AI CLI ile sürdürülmüş olabilir; "
    "aşağıdaki geçmişi dikkate alıp kaldığın yerden devam et]"
)
_MAIN_REQUEST = "detaylıca anlatır mısın bunların ne olduğunu"
_SIDE_Q = "selam nasılsın"


def _assert_side_order(text, question=_SIDE_Q, full=True, request=None):
    assert text.startswith(sp.SIDE_INSTRUCTION)
    assert _GENERIC_HANDOFF_HEADER not in text
    assert _GENERIC_HISTORY_HEADER not in text
    assert "kaldığın yerden devam et" not in text
    assert text.endswith(f"{sp.QUESTION_HEADER}\n{question}\n\n{sp.FINAL_REMINDER}")
    assert text.count(question) == 1
    if full:
        assert sp.MAIN_HISTORY_HEADER in text
        assert text.index(sp.SIDE_INSTRUCTION) < text.index(sp.MAIN_HISTORY_HEADER)
    else:
        assert sp.MAIN_HISTORY_HEADER not in text
    if request is not None:
        head = text.index(sp.IN_FLIGHT_HEADER)
        # The running request appears once, under its label, before the question.
        assert text.count(request) == 1
        assert head < text.index(request) < text.index(sp.QUESTION_HEADER)
        if full:
            assert text.index(sp.MAIN_HISTORY_HEADER) < head


def _seed_running_main(db):
    cid = db.create_conversation(1, "Ana sohbet")
    for role, content in (("user", "assets klasörünü listele"),
                          ("assistant", "Animations/, Animators/"),
                          ("user", _MAIN_REQUEST)):
        db.add_message(cid, role, content)
    return cid


# ── route: which parts, from which rows ──────────────────────────────────────

def test_side_turn_labels_the_running_request_and_puts_the_question_last(env, monkeypatch):
    db, client, _, _, _ = env
    main = _seed_running_main(db)
    db.save_memory(main, "ana özet")
    side = _open_side(client, main)
    db.add_message(side, "user", "önceki yan soru")
    db.add_message(side, "assistant", "önceki yan cevap")
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    with ambient_turn(".", "step", main):
        _stream(client, side, message=_SIDE_Q, live_context="yarım cevap metni")
    st = _FakeRunner.last.kw["side_turn"]
    assert st.in_flight_request == _MAIN_REQUEST
    assert _MAIN_REQUEST not in st.main_history
    full = st.text(full=True)
    _assert_side_order(full, request=_MAIN_REQUEST)
    order = [sp.SIDE_INSTRUCTION, sp.MAIN_HISTORY_HEADER, "ana özet", "assets klasörünü listele",
             sp.IN_FLIGHT_HEADER, "YARIM", "yarım cevap metni", "önceki yan cevap", sp.QUESTION_HEADER]
    positions = [full.index(p) for p in order]
    assert positions == sorted(positions), list(zip(order, positions))
    # A resumed session gets only what changes per turn, in the same shape.
    follow = st.text(full=False)
    _assert_side_order(follow, full=False, request=_MAIN_REQUEST)
    assert "assets klasörünü listele" not in follow and "önceki yan cevap" not in follow
    assert "yarım cevap metni" in follow


def test_side_turn_with_no_running_turn_keeps_the_last_request_as_history(env, monkeypatch):
    db, client, _, _, _ = env
    main = _seed_running_main(db)
    side = _open_side(client, main)
    monkeypatch.setattr(cr, "AgentRunner", _FakeRunner)
    _stream(client, side, message=_SIDE_Q)
    st = _FakeRunner.last.kw["side_turn"]
    assert st.in_flight_request == ""
    assert _MAIN_REQUEST in st.main_history
    full = st.text(full=True)
    assert sp.IN_FLIGHT_HEADER not in full
    _assert_side_order(full)
    assert full.index(_MAIN_REQUEST) < full.index(sp.QUESTION_HEADER)


def test_side_turn_caps_history_and_keeps_the_question():
    st = sp.SideTurn(question=_SIDE_Q, main_history="H" * 50000, side_history="S" * 1000)
    text = st.text(full=True, context_cap=24000)
    assert text.count("H") <= 23000 + 10
    _assert_side_order(text)


def test_handoff_history_header_default_is_unchanged():
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
            {"role": "user", "content": "şimdiki"}]
    assert cr._build_handoff_context("hafıza", msgs) == (
        f"[ÖNCEKİ SOHBET HAFIZASI]\nhafıza\n\n{_GENERIC_HISTORY_HEADER}\nUSER: a\nASSISTANT: b")


# ── runner: every provider path ──────────────────────────────────────────────

def _side_turn():
    return sp.SideTurn(question=_SIDE_Q, main_history="USER: eski istek",
                       in_flight_request=_MAIN_REQUEST)


def _runner(model_name, cid, side_turn=None, context="", **kw):
    extra = {"side_turn": side_turn} if side_turn is not None else {}
    return ar.AgentRunner(provider_type=kw.pop("provider_type", "subscription"), api_key="",
                          model_name=model_name, conversation_id=cid, context=context,
                          read_only=side_turn is not None, **extra, **kw)


def _oneshot_prompts(model_name, cli_key, turns, side_turn=None, context=""):
    from providers.oneshot_cli import _SESSIONS

    prompts = []

    class _Provider:
        resume_session_id = None

        async def analyze_code(self, prompt, **kwargs):
            prompts.append(prompt)
            yield {"type": "session_meta", "session_id": "ses_side"}
            yield {"type": "final", "text": "tamam"}

    _SESSIONS.clear()
    runner = _runner(model_name, 7301, side_turn, context, workspace_path=os.getcwd())

    async def turn(msg):
        return [e async for e in runner._run_oneshot_cli_session(msg, cli_key)]

    try:
        with patch("ai_providers.AIProviderManager.get_provider", return_value=_Provider()):
            for msg in turns:
                asyncio.run(turn(msg))
    finally:
        _SESSIONS.clear()
    return prompts


def test_oneshot_side_turn_order_first_and_resumed():
    prompts = _oneshot_prompts("opencode:opencode/ling-free", "opencode", [_SIDE_Q, _SIDE_Q],
                               side_turn=_side_turn())
    _assert_side_order(prompts[0], request=_MAIN_REQUEST)
    assert "USER: eski istek" in prompts[0]
    _assert_side_order(prompts[1], full=False, request=_MAIN_REQUEST)


def test_kimi_side_turn_is_full_on_every_turn():
    prompts = _oneshot_prompts("kimi-k3", "kimi", [_SIDE_Q, _SIDE_Q], side_turn=_side_turn())
    assert len(prompts) == 2
    for p in prompts:
        _assert_side_order(p, request=_MAIN_REQUEST)


def test_oneshot_normal_turn_text_is_unchanged():
    prompts = _oneshot_prompts("opencode:opencode/ling-free", "opencode", ["merhaba", "devam"],
                               context="USER: önceki")
    assert prompts == [f"merhaba\n\n{_GENERIC_HANDOFF_HEADER}\nUSER: önceki", "devam"]


def _claude_messages(monkeypatch, tmp_path, side_turn=None, resume_id=None, context=""):
    import subprocess
    import types as _types
    from providers import claude_sdk_session
    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    sent = []

    class _Sess:
        session_id = None
        auto_approve = False

        async def stream(self, message):
            sent.append(message)
            return
            yield  # pragma: no cover

    monkeypatch.setattr(unity_mcp_manager, "mcp_url", lambda host="localhost": None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(claude_sdk_session, "get_session", lambda cid, **kw: _Sess())
    runner = _runner("claude-x", 7302, side_turn, context, workspace_path=str(tmp_path),
                     generation_mode="step", resume_id=resume_id)

    async def run():
        async for _ in runner._run_claude_session(_SIDE_Q if side_turn else "merhaba"):
            pass

    asyncio.run(run())
    return sent


def test_claude_side_turn_order_first_and_resumed(monkeypatch, tmp_path):
    first = _claude_messages(monkeypatch, tmp_path, side_turn=_side_turn())
    _assert_side_order(first[0], request=_MAIN_REQUEST)
    resumed = _claude_messages(monkeypatch, tmp_path, side_turn=_side_turn(), resume_id="side-sess")
    _assert_side_order(resumed[0], full=False, request=_MAIN_REQUEST)


def test_claude_normal_turn_text_is_unchanged(monkeypatch, tmp_path):
    assert _claude_messages(monkeypatch, tmp_path, context="USER: önceki") == [
        f"merhaba\n\n{_GENERIC_HANDOFF_HEADER}\nUSER: önceki"]


def _codex_messages(monkeypatch, turns, side_turn=None, context=""):
    from providers import codex_session
    from providers.codex_provider import CodexProvider
    sent = []

    class _Sess:
        thread_id = "t-side"
        session_id = "t-side"
        _ctx_injected = False
        auto_approve = False

        async def stream(self, message, image_paths=None, **kw):
            sent.append(message)
            yield {"type": "done", "session_id": "t-side"}

    sess = _Sess()
    monkeypatch.setattr(codex_session, "get_session", lambda *a, **k: sess)
    monkeypatch.setattr(codex_session, "close_session", lambda *a, **k: None, raising=False)
    # Keeps the run away from the owner's real ~/.codex/config.toml.
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", lambda self, *a, **k: "")
    runner = _runner("gpt-5.6-codex", 7303, side_turn, context, workspace_path="",
                     generation_mode="step")

    async def run(msg):
        async for _ in runner._run_codex_session(msg):
            pass

    for msg in turns:
        asyncio.run(run(msg))
    return sent


def test_codex_side_turn_order_first_and_resumed(monkeypatch):
    sent = _codex_messages(monkeypatch, [_SIDE_Q, _SIDE_Q], side_turn=_side_turn())
    _assert_side_order(sent[0], request=_MAIN_REQUEST)
    _assert_side_order(sent[1], full=False, request=_MAIN_REQUEST)


def test_codex_normal_turn_text_is_unchanged(monkeypatch):
    sent = _codex_messages(monkeypatch, ["merhaba", "devam"], context="USER: önceki")
    assert sent == [f"merhaba\n\n{_GENERIC_HANDOFF_HEADER}\nUSER: önceki", "devam"]


def test_api_loop_side_turn_sends_every_part_in_order(tmp_path):
    runner = _runner("claude-x", 7304, _side_turn(), provider_type="anthropic",
                     workspace_path=str(tmp_path))
    seen = {}

    async def fake_anthropic(user_message):
        seen["message"], seen["context"] = user_message, runner.context
        yield ar.AgentEvent("done", {})

    runner._run_anthropic = fake_anthropic

    async def run():
        async for _ in runner.run(_SIDE_Q):
            pass

    asyncio.run(run())
    _assert_side_order(seen["message"], request=_MAIN_REQUEST)
    # The system prompt's context slot carries no history to continue.
    assert seen["context"] == sp.API_SYSTEM_CONTEXT


# ── closing a side chat's session ────────────────────────────────────────────

def test_normal_close_does_not_warn_but_a_late_assignment_does(caplog):
    import logging
    from providers.oneshot_cli import OneShotSession
    from providers.saglayici_sahipligi import oturumu_kapat

    class _Prov:
        async def cancel_active_process(self):
            return False

    sess = OneShotSession("opencode", 7305)
    early = _Prov()
    sess.active_provider = early
    with caplog.at_level(logging.WARNING, logger="providers.saglayici_sahipligi"):
        asyncio.run(oturumu_kapat(sess))
    assert early._oturum_kapandi is True
    assert "spawn engellendi" not in caplog.text
    late = _Prov()
    with caplog.at_level(logging.WARNING, logger="providers.saglayici_sahipligi"):
        sess.active_provider = late
    assert late._oturum_kapandi is True
    assert "spawn engellendi" in caplog.text
