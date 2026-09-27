"""Side question turn text order, on every provider path.

Live test 27 Sep 2026: a side question ("selam nasılsın") asked while the main
chat ran "detaylıca anlatır mısın bunların ne olduğunu" was answered with the
MAIN chat's request. The question went first, the main history after it under
"continue where you left off", and the running request was its last line.
"""
import asyncio
import os
from unittest.mock import patch

import pytest

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


# Codex mentionaudit, 27 Sep 2026: only the history was budgeted, so the
# running request, live answer and side Q/A pushed a capped one-shot message
# to 37,593 characters (Windows command lines stop near 32K).
def _big_turn(question="What does the earlier answer mean?"):
    return sp.SideTurn(
        question=question,
        main_history="H" * 20000,
        in_flight_request="R" * 4000,
        live_answer="[ANA SOHBETİN ŞU AN YAZILMAKTA OLAN (YARIM) CEVABI]\n" + "L" * 7990 + "LIVE_END",
        side_history="[BU YAN SOHBETTEKİ ÖNCEKİ SORU-CEVAPLAR]\n" + "S" * 7990 + "SIDE_END",
    )


def test_side_turn_cap_covers_the_whole_message():
    st = _big_turn()
    text = st.text(full=True, context_cap=24000)
    assert len(text) <= 24000
    _assert_side_order(text, question=st.question)
    # Higher-priority parts are whole; the main history absorbed the cut,
    # keeping its newest part.
    assert "R" * 4000 in text and "LIVE_END" in text and "SIDE_END" in text
    assert "ana sohbetin eski kısmı kırpıldı" in text
    assert "H" * 100 in text and text.count("H") < 20000


def test_side_turn_cap_trims_in_priority_order():
    st = _big_turn()
    fixed = len(sp.SIDE_INSTRUCTION) + 2 + len(
        f"{sp.QUESTION_HEADER}\n{st.question}\n\n{sp.FINAL_REMINDER}")
    # Room for the request and part of the live answer only: the live answer
    # keeps its tail, side Q/A and history are dropped whole.
    text = st.text(full=True, context_cap=fixed + 4000 + 300 + 3000)
    assert len(text) <= fixed + 7300
    _assert_side_order(text, full=False, question=st.question)
    assert sp.IN_FLIGHT_HEADER in text and "LIVE_END" in text and "başı kısaltıldı" in text
    assert "SIDE_END" not in text and "H" * 100 not in text
    # Barely more than the fixed parts: the request is cut at its tail.
    text = st.text(full=True, context_cap=fixed + 1000)
    assert len(text) <= fixed + 1000
    assert sp.IN_FLIGHT_HEADER in text and "kısaltıldı]" in text and "LIVE_END" not in text
    _assert_side_order(text, full=False, question=st.question)


def test_side_turn_without_a_cap_is_unchanged():
    text = _big_turn().text(full=True)
    assert "H" * 20000 in text and "R" * 4000 in text and "SIDE_END" in text


def test_side_question_too_long_for_the_cap_is_refused():
    st = _big_turn(question="Q" * 24000)
    assert not st.fits(24000)
    with pytest.raises(sp.SideTurnTooLong):
        st.text(full=True, context_cap=24000)


# Codex mentionverify, 27 Sep 2026: Windows limits a command line in UTF-16
# units, where an emoji counts 2; a 20,000-emoji question passed a 24,000
# code-point check and built a 41,564-unit Cursor command (limit 32,767).
_EMOJI = "\U0001f600"


def _units(s):
    return len(s.encode("utf-16-le")) // 2


def _no_lone_surrogate(s):
    return not any("\ud800" <= c <= "\udfff" for c in s)


def test_side_cap_counts_utf16_units_for_the_fixed_parts():
    fixed = _units(sp.SIDE_INSTRUCTION) + 2 + _units(
        f"{sp.QUESTION_HEADER}\n\n\n{sp.FINAL_REMINDER}")
    # Exactly at the cap fits; one more astral character (2 units) does not,
    # though it adds a single code point.
    n = (24000 - fixed) // 2
    assert sp.SideTurn(question=_EMOJI * n + "x" * ((24000 - fixed) % 2)).fits(24000)
    assert not sp.SideTurn(question=_EMOJI * (n + 1)).fits(24000)
    st = sp.SideTurn(question=_EMOJI * 20000)
    assert len(st.question) < 24000 and not st.fits(24000)
    with pytest.raises(sp.SideTurnTooLong):
        st.text(full=True, context_cap=24000)


def test_side_cap_trims_astral_text_in_utf16_units_without_splitting_a_pair():
    st = sp.SideTurn(
        question=f"{_EMOJI} ne demek?",
        main_history="H" + _EMOJI * 30000,
        in_flight_request=_EMOJI * 5000 + "REQ_END",
        live_answer="[ANA SOHBETİN ŞU AN YAZILMAKTA OLAN (YARIM) CEVABI]\n" + _EMOJI * 8000 + "LIVE_END",
        side_history="[BU YAN SOHBETTEKİ ÖNCEKİ SORU-CEVAPLAR]\n" + _EMOJI * 8000 + "SIDE_END",
    )
    history_only = sp.SideTurn(question=st.question, main_history=st.main_history)
    # Odd caps leave one unit of slack next to a 2-unit character.
    for turn, full in ((st, False), (history_only, True)):
        for cap in (24000, 24001, 9001, 6000):
            text = turn.text(full=True, context_cap=cap)
            assert _units(text) <= cap, (cap, _units(text))
            assert _no_lone_surrogate(text)
            _assert_side_order(text, question=st.question, full=full)
    assert "LIVE_END" in st.text(full=True, context_cap=6001)
    # The running request is pre-cut to IN_FLIGHT_CAP units, not code points.
    whole = st.text(full=True)
    head = whole.index(sp.IN_FLIGHT_HEADER) + len(sp.IN_FLIGHT_HEADER) + 1
    request = whole[head:whole.index("[ANA SOHBETİN ŞU AN YAZILMAKTA")].rstrip("\n")
    assert _units(request) <= sp.IN_FLIGHT_CAP + _units(sp._TRIM_TAIL)
    assert _units(request) >= sp.IN_FLIGHT_CAP - 1 and "REQ_END" not in request


def test_oneshot_side_turn_refuses_an_astral_question_over_the_unit_cap():
    events = []
    prompts = _oneshot_prompts("cursor-auto", "cursor", ["x"],
                               side_turn=sp.SideTurn(question=_EMOJI * 20000), events=events)
    assert prompts == []
    assert [e.type for e in events[0]] == ["error"]
    prompts = _oneshot_prompts("cursor-auto", "cursor", [_SIDE_Q], side_turn=sp.SideTurn(
        question=_SIDE_Q, main_history=_EMOJI * 30000, in_flight_request=_MAIN_REQUEST))
    assert _units(prompts[0]) <= 24000 and _no_lone_surrogate(prompts[0])
    _assert_side_order(prompts[0], request=_MAIN_REQUEST)


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


def _oneshot_prompts(model_name, cli_key, turns, side_turn=None, context="",
                     session_id="ses_side", events=None):
    from providers.oneshot_cli import _SESSIONS

    prompts = []

    class _Provider:
        resume_session_id = None

        async def analyze_code(self, prompt, **kwargs):
            prompts.append(prompt)
            if session_id:
                yield {"type": "session_meta", "session_id": session_id}
            yield {"type": "final", "text": "tamam"}

    _SESSIONS.clear()
    runner = _runner(model_name, 7301, side_turn, context, workspace_path=os.getcwd())

    async def turn(msg):
        return [e async for e in runner._run_oneshot_cli_session(msg, cli_key)]

    try:
        with patch("ai_providers.AIProviderManager.get_provider", return_value=_Provider()):
            for msg in turns:
                got = asyncio.run(turn(msg))
                if events is not None:
                    events.append(got)
    finally:
        _SESSIONS.clear()
    return prompts


def test_oneshot_side_turn_order_first_and_resumed():
    prompts = _oneshot_prompts("opencode:opencode/ling-free", "opencode", [_SIDE_Q, _SIDE_Q],
                               side_turn=_side_turn())
    _assert_side_order(prompts[0], request=_MAIN_REQUEST)
    assert "USER: eski istek" in prompts[0]
    _assert_side_order(prompts[1], full=False, request=_MAIN_REQUEST)


def test_oneshot_side_turn_without_a_resume_key_stays_full():
    # Codex mentionaudit, 27 Sep 2026: the first turn marked the history as
    # injected though the CLI returned no session id to resume, so the second
    # side question reached a fresh CLI session without the history.
    prompts = _oneshot_prompts("cursor-auto", "cursor", [_SIDE_Q, _SIDE_Q],
                               side_turn=_side_turn(), session_id=None)
    assert len(prompts) == 2
    for p in prompts:
        _assert_side_order(p, request=_MAIN_REQUEST)
        assert "USER: eski istek" in p


def test_oneshot_side_turn_is_capped_and_a_too_long_question_never_spawns():
    prompts = _oneshot_prompts("opencode:opencode/ling-free", "opencode", [_SIDE_Q],
                               side_turn=_big_turn(question=_SIDE_Q))
    assert len(prompts[0]) <= 24000
    _assert_side_order(prompts[0], request="R" * 4000)
    events = []
    prompts = _oneshot_prompts("opencode:opencode/ling-free", "opencode", ["x"],
                               side_turn=_big_turn(question="Q" * 24000), events=events)
    assert prompts == []
    assert [e.type for e in events[0]] == ["error"]
    assert "Yan soru gönderilemedi" in events[0][0].data["message"]


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


def _codex_messages(monkeypatch, turns, side_turn=None, context="", die_after_turn=False):
    from providers import codex_session
    from providers.codex_provider import CodexProvider
    sent = []

    class _Sess:
        thread_id = "t-side"
        session_id = "t-side"
        _ctx_injected = False
        auto_approve = False
        is_live = True

        async def stream(self, message, image_paths=None, **kw):
            sent.append(message)
            yield {"type": "done", "session_id": "t-side"}
            if die_after_turn:
                # The app-server died: the next stream starts a new thread.
                self.is_live = False

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


def test_codex_side_turn_on_a_restarted_app_server_is_full_again(monkeypatch):
    # Codex mentionaudit, 27 Sep 2026: a dead app-server restarts with a new
    # thread (start() only does thread/start), which holds none of the history.
    sent = _codex_messages(monkeypatch, [_SIDE_Q, _SIDE_Q], side_turn=_side_turn(),
                           die_after_turn=True)
    for message in sent:
        _assert_side_order(message, request=_MAIN_REQUEST)
        assert "USER: eski istek" in message


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
