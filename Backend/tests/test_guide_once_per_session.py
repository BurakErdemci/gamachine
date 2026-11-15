"""The agent guide reaches a session's model once, and again after a compaction;
the approval mode and the live Unity state are stated on the first turn and
then only on the turn they change (owner decision, 14 Nov 2026). Every
session, process and CLI here is a fake; no model runs."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

import agent_guide as guide
from agentic import agent_runner as ar
from providers import codex_session
from providers.codex_provider import CodexProvider
from tests.test_stable_guide import CID, _prepare, _runner, _unity

MARKER = "[GAMACHINE AGENT GUIDE]"


def test_briefing_sends_the_guide_once_and_a_note_only_when_it_changed():
    briefing = guide.Briefing()
    notes = [("unity", "U1"), ("mode", "M1")]
    assert briefing.turn("G", notes) == (True, ["U1", "M1"])
    assert briefing.turn("G", notes) == (False, [])
    assert briefing.turn("G", [("unity", "U2"), ("mode", "M1")]) == (False, ["U2"])
    assert briefing.turn("G2", [("unity", "U2"), ("mode", "M1")]) == (True, [])
    briefing.forget()
    assert briefing.turn("G2", [("unity", "U2"), ("mode", "M1")]) == (True, ["U2", "M1"])


@pytest.mark.parametrize("language", ["en", "tr"])
def test_mode_is_a_note_not_part_of_the_guide(language):
    composed = {guide.compose(language, project_open=True, unity_state="connected") for _ in range(2)}
    assert len(composed) == 1
    notes = {mode: guide.mode_note(language, mode, True) for mode in ("auto", "balanced", "step")}
    assert len(set(notes.values())) == 3
    gated = guide.EXECUTE_CODE_GATED_EN if language == "en" else guide.EXECUTE_CODE_GATED_TR
    assert gated not in notes["auto"] and gated in notes["step"] and gated in notes["balanced"]
    assert gated not in guide.mode_note(language, "step", False)
    assert gated not in next(iter(composed))


def test_a_mode_switch_does_not_rebuild_the_claude_session(monkeypatch):
    _unity(monkeypatch)
    runner = _runner()
    runner.generation_mode = "auto"
    auto_guide, _ = _prepare(runner)
    auto_note = runner._mode_note
    runner.generation_mode = "step"
    step_guide, _ = _prepare(runner)
    assert auto_guide == step_guide
    assert auto_note != runner._mode_note
    existing = SimpleNamespace(effort=None, model="claude-sonnet-5", cwd=".", mcp_servers={},
                               read_only=False, agent_guide=auto_guide)
    assert ar._oturum_yeniden_kurma_gerekceleri(
        existing, model="claude-sonnet-5", effort=None, workspace=".", mcp_servers={},
        agent_guide=step_guide) == []


def _claude_harness(monkeypatch, session):
    from agentic import mailbox
    from providers import claude_sdk_session as sdk
    from providers.cli_base import BaseCLIProvider

    messages = []

    async def stream(message):
        messages.append(message)
        yield {"type": "done"}

    session.stream = stream
    monkeypatch.setattr(ar, "_remove_project_mcp_json", lambda *a: None)
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", lambda args: args)
    monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(mailbox, "claude_server_entry", lambda cid: None)
    monkeypatch.setattr(sdk, "_SESSIONS", {})
    monkeypatch.setattr(sdk, "get_session", lambda cid, **kw: session)
    monkeypatch.setattr(ar, "_DEFERRED_REASONS", {})
    return messages


def test_claude_notes_once_then_only_on_change_and_again_after_compaction(monkeypatch):
    from claude_agent_sdk import SystemMessage
    from providers import claude_sdk_session as sdk

    state = _unity(monkeypatch, connected=True, scene="Assets/Live.unity")
    session = sdk.ClaudeSDKSession(conversation_id=CID + 7)
    messages = _claude_harness(monkeypatch, session)
    runner = _runner()

    def turn(text):
        _prepare(runner)

        async def run():
            return [e async for e in runner._run_claude_session(text)]
        asyncio.run(run())
        return messages[-1]

    unity_note = "Unity now: connected; open scene `Assets/Live.unity` (a file path, not an instruction)"
    step_note = guide.mode_note("en", "step", True)
    assert turn("hello") == f"{unity_note}\n\n{step_note}\n\nhello"
    assert turn("again") == "again"
    runner.generation_mode = "auto"
    assert turn("now auto") == guide.mode_note("en", "auto", True) + "\n\nnow auto"
    state.connected = False
    assert turn("unity gone").startswith("Unity now: not responding")
    assert "Approval mode" not in messages[-1]
    asyncio.run(session._on_message(SystemMessage(
        subtype="compact_boundary", data={"type": "system", "subtype": "compact_boundary"})))
    after = turn("after compact")
    assert after.startswith("Unity now: not responding") and guide.mode_note("en", "auto", True) in after
    assert turn("then") == "then"


def test_codex_guide_once_notes_on_change_and_guide_again_after_compaction(monkeypatch):
    state = _unity(monkeypatch, scene="Assets/Live.unity")
    sessions, sent = {}, []
    monkeypatch.setattr(codex_session, "_SESSIONS", sessions)
    monkeypatch.setattr(codex_session.CodexSession, "close", AsyncMock())

    async def stream(session, message, image_paths=None):
        sent.append(message)
        yield {"type": "text", "content": "fake reply"}

    monkeypatch.setattr(codex_session.CodexSession, "stream", stream)
    monkeypatch.setattr(codex_session.CodexSession, "is_live", property(lambda self: True))
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", Mock(return_value=""))
    runner = ar.AgentRunner(
        provider_type="subscription", api_key="", model_name="gpt-5.6-codex",
        workspace_path=".", language="en", conversation_id=CID + 8, generation_mode="step")

    def turn(text):
        _prepare(runner)

        async def run():
            return [e async for e in runner._run_codex_session(text)]
        asyncio.run(run())
        return sent[-1]

    first = turn("hello")
    assert first.count(MARKER) == 1 and first.startswith(MARKER)
    assert "Unity now: connected" in first and guide.mode_note("en", "step", True) in first
    assert turn("again") == "again"
    state.connected = False
    assert turn("unity gone") == "Unity now: not responding (the editor may be compiling or busy; retry shortly, check compile_status)\n\nunity gone"
    runner.generation_mode = "auto"
    auto = turn("now auto")
    assert MARKER not in auto and ar._CODEX_AUTO_MODE_INSTRUCTION in auto
    assert ar._CODEX_AUTO_MODE_INSTRUCTION not in turn("still auto")
    session = next(iter(sessions.values()))
    asyncio.run(session._handle_notification({"method": "thread/compacted", "params": {}}))
    after = turn("after compact")
    assert after.count(MARKER) == 1 and ar._CODEX_AUTO_MODE_INSTRUCTION in after
    assert "Unity now: not responding" in after
    assert turn("then") == "then"
    assert len(sessions) == 1


def test_agy_guide_and_notes_once_per_process_and_again_on_a_new_one():
    from . import test_agy_stream_session as fixture

    composed = guide.compose("en", project_open=True, unity_state="connected")

    async def scenario():
        harness = fixture.TestAgyStreamSession()
        await harness.asyncSetUp()
        try:
            # The fixture's fake process answers two turns.
            session = fixture.agy_session.get_session(11)
            session.agent_guide = composed
            contents = []

            async def turn(message, notes, **kwargs):
                session.turn_notes = notes
                try:
                    await asyncio.wait_for(harness.collect(session=session, message=message, **kwargs), 15)
                except TimeoutError:
                    raise AssertionError(f"turn {message!r} hung; processes={len(harness.processes)} "
                                         f"lines={[len(p.stdin.lines) for p in harness.processes]}")
                process = harness.processes[-1]
                contents.append(json.loads(process.stdin.lines[-1])["message"]["content"])
                return contents[-1]

            notes = (("unity", "U1"), ("mode", "M1"))
            assert await turn("first", notes, model="gemini-4-argon") == composed + "\n\nU1\n\nM1\n\nfirst"
            changed = (("unity", "U2"), ("mode", "M1"))
            assert await turn("second", changed, model="gemini-4-argon") == "U2\n\nsecond"
            assert len(harness.processes) == 1
            respawned = await turn("third", changed, model="gemini-3.8-flash")
            assert len(harness.processes) == 2
            assert respawned == composed + "\n\nU2\n\nM1\n\nthird"
            assert await turn("fourth", changed, model="gemini-3.8-flash") == "fourth"
        finally:
            await harness.asyncTearDown()
    asyncio.run(asyncio.wait_for(scenario(), 60))


class _FakeCLI:
    """Records what a one-shot CLI would get: the shared provider_guide every
    _build_cmd puts in front, plus the prompt."""

    fail_next = False
    raise_next = None
    hang_next = False

    def __init__(self, sent, session_id):
        self.sent, self.session_id = sent, session_id
        self.resume_session_id = None

    async def analyze_code(self, prompt, **kwargs):
        self.sent.append((self.resume_session_id,
                          guide.provider_guide(self, project_open=True, unity_running=False) + prompt))
        if _FakeCLI.raise_next is not None:
            raise _FakeCLI.raise_next
        if _FakeCLI.hang_next:
            yield {"type": "delta", "text": "partial"}
            await asyncio.sleep(3600)
        if _FakeCLI.fail_next:
            yield {"type": "error", "content": "cli failed"}
            return
        if self.session_id:
            yield {"type": "session_meta", "session_id": self.session_id}
        yield {"type": "final", "text": "ok"}


def _oneshot(monkeypatch, cli_key, session_id):
    from providers import oneshot_cli
    from providers.oneshot_cli import _SESSIONS
    # A named-model turn that succeeds records the plan capability on disk.
    monkeypatch.setattr(oneshot_cli, "set_named_models_cap", lambda *a, **k: None)
    state = _unity(monkeypatch)
    _SESSIONS.clear()
    sent = []
    runner = _runner(cid=CID + 9)

    def turn(text):
        _prepare(runner)

        async def run():
            return [e async for e in runner._run_oneshot_cli_session(text, cli_key)]
        with patch("ai_providers.AIProviderManager.get_provider",
                   side_effect=lambda cfg: _FakeCLI(sent, session_id)):
            asyncio.run(run())
        return sent[-1]
    turn.runner = runner
    return turn, state, _SESSIONS


def test_resumed_oneshot_cli_gets_the_guide_once_and_again_after_a_reset(monkeypatch):
    turn, state, sessions = _oneshot(monkeypatch, "cursor", "chat-1")
    try:
        resume, first = turn("hello")
        assert resume is None and first.count(MARKER) == 1 and "Unity now: connected" in first
        resume, second = turn("again")
        assert resume == "chat-1" and second == "again"
        state.connected = False
        _, third = turn("unity gone")
        assert MARKER not in third and third.startswith("Unity now: not responding")
        sessions[("cursor", CID + 9)].session_id = None   # what a reset_session error does
        _, fresh = turn("fresh")
        assert fresh.count(MARKER) == 1 and "Unity now: not responding" in fresh
        assert guide.mode_note("en", "step", True) in fresh
    finally:
        sessions.clear()


def test_oneshot_cli_without_resume_gets_the_guide_and_notes_every_turn(monkeypatch):
    turn, _, sessions = _oneshot(monkeypatch, "kimi", "kimi-session")
    try:
        for text in ("one", "two", "three"):
            _, payload = turn(text)
            assert payload.count(MARKER) == 1
            assert "Unity now: connected" in payload and guide.mode_note("en", "step", True) in payload
            assert payload.endswith(text)
    finally:
        sessions.clear()


def test_a_slash_command_carries_no_note_and_does_not_use_them_up(monkeypatch):
    turn, state, sessions = _oneshot(monkeypatch, "opencode", "ses-1")
    try:
        turn("hello")
        state.connected = False
        _, slash = turn("/context")
        assert slash == "/context"
        _, plain = turn("plain")
        assert plain.startswith("Unity now: not responding") and plain.endswith("plain")
    finally:
        sessions.clear()


# ── A turn that did not end normally may not have reached the model ──────────

def _codex_runner(monkeypatch, cid, streams, mode="step"):
    """A runner on a fake Codex session whose stream() plays `streams` in order;
    a stream is a list of events or an exception to raise. Returns (turn, sent)."""
    _unity(monkeypatch)
    sessions, sent = {}, []
    monkeypatch.setattr(codex_session, "_SESSIONS", sessions)
    monkeypatch.setattr(codex_session.CodexSession, "close", AsyncMock())
    plan = list(streams)

    async def stream(session, message, image_paths=None):
        sent.append(message)
        step = plan.pop(0) if plan else [{"type": "text", "content": "ok"}]
        if isinstance(step, BaseException):
            raise step
        for event in step:
            yield dict(event)

    monkeypatch.setattr(codex_session.CodexSession, "stream", stream)
    monkeypatch.setattr(codex_session.CodexSession, "is_live", property(lambda self: True))
    monkeypatch.setattr(CodexProvider, "_write_mcp_config", Mock(return_value=""))
    runner = ar.AgentRunner(
        provider_type="subscription", api_key="", model_name="gpt-5.6-codex",
        workspace_path=".", language="en", conversation_id=cid, generation_mode=mode)

    def turn(text):
        _prepare(runner)

        async def run():
            return [e async for e in runner._run_codex_session(text)]
        return asyncio.run(run())
    turn.runner = runner
    return turn, sent


def test_codex_error_event_turn_is_not_remembered(monkeypatch):
    turn, sent = _codex_runner(monkeypatch, CID + 20, [[{"type": "error", "message": "boom"}]])
    turn("first")
    turn("second")
    assert sent[1].count(MARKER) == 1
    assert guide.mode_note("en", "step", True) in sent[1]
    assert guide.EXECUTE_CODE_GATED_EN in sent[1]


def test_codex_exception_turn_is_not_remembered(monkeypatch):
    turn, sent = _codex_runner(monkeypatch, CID + 21, [RuntimeError("stream broke")])
    events = turn("first")
    assert any(e.type == "error" for e in events)
    turn("second")
    assert sent[1].count(MARKER) == 1 and guide.EXECUTE_CODE_GATED_EN in sent[1]


def test_codex_turn_cancelled_before_delivery_does_not_use_up_the_mode_note(monkeypatch):
    # A real Codex session: the turn waits for the session lock, is cancelled
    # there, and the next delivered turn must still carry the gated rule.
    from browser_use import settings as browser_settings
    from computer_use import settings as computer_settings
    from connectors import registration

    _unity(monkeypatch, running=False)
    monkeypatch.setattr(computer_settings, "enabled_now", lambda *a, **k: False)
    monkeypatch.setattr(browser_settings, "enabled_now", lambda *a, **k: False)
    monkeypatch.setattr(registration, "served", lambda *a, **k: ((), ""))
    cid, delivered = CID + 22, []
    session = codex_session.CodexSession(cid, model="gpt-5.6-codex", cwd=".", effort=None)
    session._started = True
    session.thread_id = "fake-thread"

    async def request(method, params, **kwargs):
        assert method == "turn/start"
        delivered.append(params["input"][0]["text"])
        await session._out_q.put({"type": "done"})
        await session._out_q.put(None)
        return {"result": {"turn": {"id": "fake-turn"}}}

    session._request = request
    monkeypatch.setattr(codex_session, "_SESSIONS", {cid: session})
    runner = ar.AgentRunner(
        provider_type="subscription", api_key="", model_name="gpt-5.6-codex",
        workspace_path=".", language="en", conversation_id=cid, generation_mode="auto")

    async def turn(text):
        await runner._prepare_agent_guide()
        return [e async for e in runner._run_codex_session(text)]

    async def scenario():
        await turn("initial auto turn")
        runner.generation_mode = "step"
        await session._turn_lock.acquire()
        pending = asyncio.create_task(turn("cancelled while waiting"))
        try:
            for _ in range(100):
                if guide.briefing_of(session).notes.get("mode") == runner._mode_note:
                    break
                await asyncio.sleep(0.01)
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        finally:
            session._turn_lock.release()
        assert len(delivered) == 1
        await turn("ordinary step turn")

    asyncio.run(asyncio.wait_for(scenario(), 30))
    assert len(delivered) == 2
    assert guide.EXECUTE_CODE_GATED_EN in delivered[-1]


def test_claude_stream_that_raises_with_no_events_is_not_remembered(monkeypatch):
    from providers import claude_sdk_session as sdk

    _unity(monkeypatch)
    session = sdk.ClaudeSDKSession(conversation_id=CID + 23)
    messages = _claude_harness(monkeypatch, session)
    plan = [RuntimeError("stream broke"), RuntimeError("stream broke again"), None]

    async def stream(message):
        messages.append(message)
        step = plan.pop(0)
        if step is not None:
            raise step
        yield {"type": "done"}

    session.stream = stream
    runner = _runner(cid=CID + 23)

    def turn(text):
        _prepare(runner)

        async def run():
            return [e async for e in runner._run_claude_session(text)]
        asyncio.run(run())
        return messages[-1]

    turn("first")
    assert guide.mode_note("en", "step", True) in turn("second")


def test_claude_cancelled_turn_is_not_remembered(monkeypatch):
    from providers import claude_sdk_session as sdk

    _unity(monkeypatch)
    session = sdk.ClaudeSDKSession(conversation_id=CID + 24)
    messages = _claude_harness(monkeypatch, session)
    plan = [asyncio.CancelledError(), None]

    async def stream(message):
        messages.append(message)
        step = plan.pop(0)
        if step is not None:
            raise step
        yield {"type": "done"}

    session.stream = stream
    runner = _runner(cid=CID + 24)
    _prepare(runner)

    async def cancelled():
        return [e async for e in runner._run_claude_session("first")]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled())
    _prepare(runner)

    async def again():
        return [e async for e in runner._run_claude_session("second")]
    asyncio.run(again())
    assert guide.mode_note("en", "step", True) in messages[-1]


def test_resumed_oneshot_cli_error_turn_is_not_remembered(monkeypatch):
    turn, _, sessions = _oneshot(monkeypatch, "cursor", "chat-2")
    try:
        turn("hello")
        _, second = turn("again")
        assert second == "again"
        _FakeCLI.fail_next = True
        try:
            turn("this one errors")
        finally:
            _FakeCLI.fail_next = False
        resume, after = turn("after the error")
        assert resume == "chat-2"
        assert after.count(MARKER) == 1 and guide.mode_note("en", "step", True) in after
    finally:
        sessions.clear()


def test_api_and_simple_paths_carry_the_mode_note_on_every_request(monkeypatch):
    _unity(monkeypatch)
    runner = _runner(cid=CID + 25)
    _prepare(runner)
    note = guide.mode_note("en", "step", True)
    for _ in range(2):
        assert note in runner._api_system_instruction()

    seen = []

    class _Plain:
        def analyze_code(self, prompt, **kwargs):
            seen.append(prompt)
            return "ok"

    runner.provider_type = "openai"
    with patch("ai_providers.AIProviderManager.get_provider", return_value=_Plain()):
        for text in ("one", "two"):
            async def run():
                return [e async for e in runner._run_simple(text)]
            asyncio.run(run())
    assert len(seen) == 2 and all(note in prompt for prompt in seen)


# ── Round 2: every abnormal end, and the history transcript ──────────────────

HIST = "EARLIER-CHAT-HISTORY-MARKER"


def _claude_runner(monkeypatch, cid, plan):
    """A runner on a fake Claude session whose stream() plays `plan` in order:
    a list of events, an exception raised before any event, or a tuple
    (events, exception) raised after them. Returns (runner, session, messages)."""
    from providers import claude_sdk_session as sdk

    _unity(monkeypatch)
    session = sdk.ClaudeSDKSession(conversation_id=cid)
    messages = _claude_harness(monkeypatch, session)
    steps = list(plan)

    async def stream(message):
        messages.append(message)
        step = steps.pop(0) if steps else [{"type": "done"}]
        if isinstance(step, BaseException):
            events, error = [], step
        elif isinstance(step, tuple):
            events, error = step
        else:
            events, error = step, None
        for event in events:
            yield dict(event)
        if error is not None:
            raise error

    session.stream = stream
    runner = _runner(cid=cid)
    return runner, session, messages


def _claude_turn(runner, text):
    _prepare(runner)

    async def run():
        return [e async for e in runner._run_claude_session(text)]
    return asyncio.run(run())


def test_claude_error_event_turn_is_not_remembered(monkeypatch):
    runner, _, messages = _claude_runner(monkeypatch, CID + 30, [[{"type": "error", "message": "boom"}]])
    _claude_turn(runner, "first")
    _claude_turn(runner, "second")
    assert guide.mode_note("en", "step", True) in messages[-1]
    _claude_turn(runner, "third")
    assert messages[-1] == "third"


def test_claude_generator_close_is_not_remembered(monkeypatch):
    runner, _, messages = _claude_runner(
        monkeypatch, CID + 31, [[{"type": "text", "content": "partial"}, {"type": "done"}]])
    _prepare(runner)

    async def close_midway():
        agen = runner._run_claude_session("first")
        await agen.__anext__()
        await agen.aclose()
    asyncio.run(close_midway())
    _claude_turn(runner, "second")
    assert guide.mode_note("en", "step", True) in messages[-1]


def test_claude_exception_after_events_is_not_remembered(monkeypatch):
    runner, _, messages = _claude_runner(
        monkeypatch, CID + 32, [([{"type": "text", "content": "partial"}], RuntimeError("stream broke"))])
    events = _claude_turn(runner, "first")
    assert any(e.type == "error" for e in events)
    _claude_turn(runner, "second")
    assert guide.mode_note("en", "step", True) in messages[-1]


def test_oneshot_raise_cancel_and_close_are_not_remembered(monkeypatch):
    turn, _, sessions = _oneshot(monkeypatch, "cursor", "chat-4")
    key = ("cursor", CID + 9)
    turn.runner.context = HIST
    try:
        turn("hello")
        assert guide.briefing_of(sessions[key]).notes

        _FakeCLI.raise_next = RuntimeError("cli died")
        try:
            with pytest.raises(RuntimeError):
                turn("raises")
        finally:
            _FakeCLI.raise_next = None
        assert guide.briefing_of(sessions[key]).notes == {}
        resume, after = turn("after the raise")
        assert resume == "chat-4" and after.count(MARKER) == 1
        assert guide.mode_note("en", "step", True) in after

        _FakeCLI.raise_next = asyncio.CancelledError()
        try:
            with pytest.raises(asyncio.CancelledError):
                turn("cancelled")
        finally:
            _FakeCLI.raise_next = None
        assert guide.briefing_of(sessions[key]).notes == {}
        _, fresh = turn("after the cancel")
        assert fresh.count(MARKER) == 1 and guide.mode_note("en", "step", True) in fresh
        assert HIST in fresh

        runner = turn.runner
        _FakeCLI.hang_next = True
        _prepare(runner)

        async def close_midway():
            with patch("ai_providers.AIProviderManager.get_provider",
                       side_effect=lambda cfg: _FakeCLI([], "chat-4")):
                agen = runner._run_oneshot_cli_session("closed", "cursor")
                await agen.__anext__()
                await agen.aclose()
        try:
            asyncio.run(close_midway())
        finally:
            _FakeCLI.hang_next = False
        assert guide.briefing_of(sessions[key]).notes == {}
        _, again = turn("after the close")
        assert again.count(MARKER) == 1
    finally:
        sessions.clear()


def test_codex_native_stop_is_not_remembered(monkeypatch):
    turn, sent = _codex_runner(
        monkeypatch, CID + 33, [[{"type": "done", "stop_reason": "cancelled"}]])
    turn("first, stopped")
    turn("second")
    assert sent[1].count(MARKER) == 1
    assert guide.mode_note("en", "step", True) in sent[1]
    turn("third")
    assert MARKER not in sent[2]


def test_codex_history_is_resent_after_a_failed_first_turn(monkeypatch):
    turn, sent = _codex_runner(monkeypatch, CID + 34, [[{"type": "error", "message": "quota"}]])
    turn.runner.context = HIST
    turn("first, errors")
    turn("retry")
    turn("third")
    assert HIST in sent[0] and HIST in sent[1]
    assert HIST not in sent[2]


def test_oneshot_history_is_resent_after_a_failed_first_turn(monkeypatch):
    turn, _, sessions = _oneshot(monkeypatch, "cursor", "chat-5")
    turn.runner.context = HIST
    try:
        _FakeCLI.fail_next = True
        try:
            turn("first, errors")
        finally:
            _FakeCLI.fail_next = False
        _, retry = turn("retry")
        assert HIST in retry
        _, later = turn("later")
        assert HIST not in later
        _FakeCLI.raise_next = RuntimeError("cli died")
        sessions.clear()
        try:
            with pytest.raises(RuntimeError):
                turn("raises on a fresh session")
        finally:
            _FakeCLI.raise_next = None
        _, again = turn("again")
        assert HIST in again
    finally:
        sessions.clear()


def test_claude_handoff_history_is_resent_after_failed_turns(monkeypatch):
    from providers import claude_sdk_session as sdk

    runner, session, messages = _claude_runner(
        monkeypatch, CID + 35, [RuntimeError("a"), RuntimeError("b"), [{"type": "done"}], [{"type": "done"}]])
    session.session_id = "live-session"
    session._handoff_pending = True
    runner.context = HIST

    async def fake_close(cid):
        return None
    monkeypatch.setattr(sdk, "close_session", fake_close)
    _claude_turn(runner, "first, fails twice")
    assert session._handoff_pending is True
    _claude_turn(runner, "retry")
    assert HIST in messages[-1]
    assert session._handoff_pending is False
    _claude_turn(runner, "later")
    assert HIST not in messages[-1]


# ── Round 3: busy hand-off, restarted Codex thread, cancel guard, flags ──────

def test_claude_busy_with_background_delivery_consumes_the_handoff(monkeypatch):
    from providers import claude_sdk_session as sdk

    runner, session, messages = _claude_runner(monkeypatch, CID + 36, [])
    handed, calls = [], {"n": 0}

    async def stream(message, lock_timeout=None):
        calls["n"] += 1
        messages.append(message)
        if calls["n"] == 1:
            raise sdk.SessionBusyError("busy")
        yield {"type": "done"}

    async def hand(message):
        handed.append(message)
        return True

    session.stream = stream
    session.hand_to_running_turn = hand
    session._active_tasks = {"bg": object()}
    session.session_id = "live-session"
    session._handoff_pending = True
    runner.context = HIST
    _claude_turn(runner, "first, busy, handed to the running turn")
    assert handed and HIST in handed[0]
    assert session._handoff_pending is False
    _claude_turn(runner, "second")
    assert HIST not in messages[-1]


def test_codex_history_is_resent_when_a_restarted_thread_fails_its_first_turn(monkeypatch):
    turn, sent = _codex_runner(monkeypatch, CID + 37, [
        [{"type": "text", "content": "ok"}],
        [{"type": "error", "message": "boom"}],
        [{"type": "text", "content": "ok"}],
        [{"type": "text", "content": "ok"}]])
    turn.runner.context = HIST
    live = {"v": True}
    plain_stream = codex_session.CodexSession.stream

    async def stream(session, message, image_paths=None):
        live["v"] = True  # stream() (re)starts the app-server
        async for event in plain_stream(session, message, image_paths):
            yield event

    monkeypatch.setattr(codex_session.CodexSession, "stream", stream)
    monkeypatch.setattr(codex_session.CodexSession, "is_live", property(lambda self: live["v"]))
    turn("first, normal")
    live["v"] = False  # the app-server died between turns
    turn("second, restarts the thread, then fails")
    turn("third")
    assert HIST in sent[1] and HIST in sent[2]
    turn("fourth")
    assert HIST not in sent[3]


def test_oneshot_cancel_after_a_normal_turn_sends_history_to_the_fresh_session(monkeypatch):
    turn, _, sessions = _oneshot(monkeypatch, "cursor", "chat-6")
    turn.runner.context = HIST
    try:
        _, first = turn("hello")
        assert HIST in first
        _, second = turn("again")
        assert HIST not in second
        _FakeCLI.raise_next = asyncio.CancelledError()
        try:
            with pytest.raises(asyncio.CancelledError):
                turn("cancelled")
        finally:
            _FakeCLI.raise_next = None
        _, fresh = turn("after the cancel")
        assert HIST in fresh
    finally:
        sessions.clear()


def test_codex_failed_turn_puts_the_pending_handoff_back(monkeypatch):
    turn, sent = _codex_runner(monkeypatch, CID + 38, [
        [{"type": "text", "content": "ok"}], [{"type": "error", "message": "boom"}]])
    turn.runner.context = HIST
    turn("first, normal")
    session = next(iter(codex_session._SESSIONS.values()))
    session._handoff_pending = True
    turn("second, fails")
    assert HIST in sent[1] and session._handoff_pending is True
    turn("retry")
    assert HIST in sent[2] and session._handoff_pending is False
    turn("later")
    assert HIST not in sent[3]


def test_claude_error_event_puts_the_pending_handoff_back(monkeypatch):
    runner, session, messages = _claude_runner(
        monkeypatch, CID + 39, [[{"type": "error", "message": "boom"}], [{"type": "done"}]])
    session.session_id = "live-session"
    session._handoff_pending = True
    runner.context = HIST
    _claude_turn(runner, "first, errors")
    assert session._handoff_pending is True
    _claude_turn(runner, "retry")
    assert HIST in messages[-1] and session._handoff_pending is False
