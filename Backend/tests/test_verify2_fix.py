"""Blind-audit blockers of the dictation, background-job and web_fetch work.

- a failed dictation finish never loses the committed text or the tail audio,
- the hallucination filter keeps real speech and catches a credit line inside one segment,
- background jobs leave no orphans (shell gone, stop, shutdown),
- web_fetch ends within a total deadline however slowly the server sends.
"""
import contextlib
import os
import sys
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import process_tree
from agentic import shell_jobs, web_fetch as wf
from providers import stt_whisper as sw
from routes.transcribe_routes import create_transcribe_router

HEADERS = {"X-Session-Token": "dev"}


# ── DL-1: a failed finish keeps the session ──────────────────────────────────

class FlakyServer:
    """The first decode (a committed window) works, the second one (the tail) fails once."""
    gpu = False

    def __init__(self, fail_calls=(2,)):
        self.fail_calls = set(fail_calls)
        self.calls = []

    def ensure_started(self):
        pass

    def wait_ready(self, timeout=None):
        pass

    def is_ready(self):
        return True

    def died(self, grace_s=0.5):
        return False

    def touch(self):
        pass

    def stop(self):
        pass

    def lease(self):
        return contextlib.nullcontext(self)

    def transcribe(self, pcm, language, timeout=None):
        self.calls.append(len(pcm))
        if len(self.calls) in self.fail_calls:
            raise sw.SttEngineFailed("engine hiccup")
        return {"text": "first window text" if len(self.calls) == 1 else "the tail", "language": "turkish"}


@pytest.fixture
def flaky():
    sw.reset_sessions()
    server = FlakyServer()
    sw.set_server(server)
    yield server
    sw.set_server(None)
    sw.reset_sessions()


def _dictate_past_one_window():
    session_id = sw.open_session("tr")
    pcm = b"\x01\x00" * (sw.SAMPLE_RATE * 40)
    step = sw.SAMPLE_RATE * 2
    for offset in range(0, len(pcm), step):
        sw.feed(session_id, pcm[offset:offset + step])
        thread = sw._sessions[session_id].commit_thread
        if thread:
            thread.join(10)
    return session_id


def test_a_retry_after_a_failed_finish_returns_committed_text_and_tail(flaky):
    session_id = _dictate_past_one_window()
    with pytest.raises(sw.SttEngineFailed) as failure:
        sw.finish(session_id)
    assert failure.value.committed_text == "first window text"
    result = sw.finish(session_id)
    assert result["text"] == "first window text the tail"
    with pytest.raises(sw.SttNoSession):
        sw.finish(session_id)                       # a finished session is gone


def test_the_finish_route_answers_503_with_the_committed_text_and_keeps_the_session(flaky):
    app = FastAPI()
    app.include_router(create_transcribe_router(None))
    client = TestClient(app, raise_server_exceptions=False)
    session_id = _dictate_past_one_window()
    url = f"/transcribe/session/{session_id}/finish"
    failed = client.post(url, json={}, headers=HEADERS)
    assert failed.status_code == 503
    assert failed.json() == {"detail": "stt_engine_failed", "committed_text": "first window text"}
    retried = client.post(url, json={}, headers=HEADERS)
    assert retried.status_code == 200
    assert retried.json()["text"] == "first window text the tail"


def test_a_failed_finish_gives_the_abandoned_session_a_fresh_idle_ttl(flaky):
    session_id = _dictate_past_one_window()
    session = sw._sessions[session_id]
    session.last_seen -= sw.SESSION_TTL_S - 1
    with pytest.raises(sw.SttEngineFailed):
        sw.finish(session_id)
    assert sw.purge_expired(sw._now() + sw.SESSION_TTL_S - 5) == []
    assert sw.purge_expired(sw._now() + sw.SESSION_TTL_S + 5) == [session_id]


# ── DL-2 / DL-3: the credit filter ───────────────────────────────────────────

def _seg(text, no_speech=0.1, logprob=-0.3):
    return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob}


def _clean(*segments):
    return sw.clean_text({"text": "".join(s["text"] for s in segments), "segments": list(segments)})


@pytest.mark.parametrize("text", [
    "Abone ol", "Abone olun", "Abone ol.", "Untertitel", "Altyazı", "Altyazı Ekle", "Subtitles by default are off",
    "Captions by default", "İzlediğiniz için teşekkürler", "Thank you for watching",
])
def test_ordinary_speech_is_not_a_credit_line(text):
    assert _clean(_seg(" " + text)) == text


@pytest.mark.parametrize("text", [
    "Altyazı M.K.", "Altyazı: A.B.C.D.", "Subtitles by the Amara.org community", "Subtitles by John Smith",
    "Untertitel im Auftrag des ZDF, 2020", "Untertitel von Amara.org",
])
def test_a_real_credit_line_is_dropped(text):
    assert _clean(_seg(" Merhaba."), _seg(" " + text)) == "Merhaba."


def test_a_trailing_credit_inside_one_segment_is_cut_off():
    assert _clean(_seg(" Merhaba. Altyazı M.K.")) == "Merhaba."
    assert sw.clean_text({"text": " Merhaba. Altyazı M.K."}) == "Merhaba."
    assert _clean(_seg(" Altyazı dosyasını indirdim.")) == "Altyazı dosyasını indirdim."


@pytest.mark.parametrize("text", ["Thank you for watching.", "İzlediğiniz için teşekkürler", "Abone olmayı unutmayın!"])
def test_a_closing_line_is_dropped_only_from_the_final_decode(text):
    assert sw.strip_closing_credit(text) == ""
    assert sw.strip_closing_credit("Merhaba. " + text) == "Merhaba. " + text


def test_a_noisy_long_sentence_is_kept_and_a_noisy_short_one_is_dropped():
    sentence = _seg(" yarın sabah toplantıya birlikte gidelim mi", no_speech=0.7, logprob=-1.2)
    assert _clean(sentence) == "yarın sabah toplantıya birlikte gidelim mi"
    assert _clean(_seg(" Evet.", no_speech=0.9, logprob=-1.4)) == ""


# ── RL-2 / RL-3 / RL-4: background jobs ──────────────────────────────────────

def _alive(pid):
    return process_tree.creation_time(pid) is not None


def _spawner(tmp_path, name, parent_stays_s):
    """A script that starts a long-lived grandchild which writes its pid, then lives parent_stays_s."""
    pid_file = tmp_path / f"{name}.pid"
    script = tmp_path / f"{name}.py"
    child_code = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
    script.write_text("\n".join([
        "import subprocess, sys, time",
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])",
        f"time.sleep({parent_stays_s})",
    ]), encoding="utf-8")
    return f'"{sys.executable}" "{script}"', pid_file


def _grandchild_pid(pid_file):
    deadline = time.time() + 15
    while time.time() < deadline:
        if pid_file.exists() and pid_file.read_text().strip():
            return int(pid_file.read_text())
        time.sleep(0.1)
    raise AssertionError("the grandchild never started")


@pytest.fixture
def jobs(tmp_path):
    spawned = []
    yield spawned
    shell_jobs.stop_all()
    process_tree._kill_pids_sync([pid for pid in spawned if _alive(pid)])


def _wait_status(job_id, conversation_id, wanted, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if shell_jobs.status(job_id, conversation_id)["status"] == wanted:
            return
        time.sleep(0.1)
    raise AssertionError(f"job never became {wanted}")


def test_stop_on_an_exited_job_ends_the_grandchild_its_shell_left(tmp_path, jobs):
    command, pid_file = _spawner(tmp_path, "gc", parent_stays_s=1.5)
    job_id = shell_jobs.start(command, str(tmp_path), 7)
    grandchild = _grandchild_pid(pid_file)
    jobs.append(grandchild)
    _wait_status(job_id, 7, "exited")
    assert _alive(grandchild)
    shell_jobs.stop(job_id, 7)
    deadline = time.time() + 10
    while _alive(grandchild) and time.time() < deadline:
        time.sleep(0.1)
    assert not _alive(grandchild)


def test_stop_returns_the_status_after_the_kill(tmp_path, jobs):
    command, pid_file = _spawner(tmp_path, "run", parent_stays_s=60)
    job_id = shell_jobs.start(command, str(tmp_path), 7)
    jobs.append(_grandchild_pid(pid_file))
    assert shell_jobs.stop(job_id, 7)["status"] == "stopped"
    assert shell_jobs.status(job_id, 7)["status"] == "stopped"


def test_stopping_a_conversation_ends_only_its_jobs(tmp_path, jobs):
    mine, mine_pid = _spawner(tmp_path, "mine", parent_stays_s=60)
    other, other_pid = _spawner(tmp_path, "other", parent_stays_s=60)
    mine_id = shell_jobs.start(mine, str(tmp_path), 31)
    other_id = shell_jobs.start(other, str(tmp_path), 32)
    jobs += [_grandchild_pid(mine_pid), _grandchild_pid(other_pid)]
    shell_jobs.stop_conversation(31)
    assert shell_jobs.status(mine_id, 31)["status"] == "stopped"
    assert shell_jobs.status(other_id, 32)["status"] == "running"
    assert not _alive(jobs[0]) and _alive(jobs[1])


def test_shutdown_stops_every_running_job(tmp_path, jobs):
    command, pid_file = _spawner(tmp_path, "down", parent_stays_s=60)
    job_id = shell_jobs.start(command, str(tmp_path), 41)
    grandchild = _grandchild_pid(pid_file)
    jobs.append(grandchild)
    shell_jobs.stop_all()
    assert shell_jobs.status(job_id, 41)["status"] == "stopped"
    deadline = time.time() + 10
    while _alive(grandchild) and time.time() < deadline:
        time.sleep(0.1)
    assert not _alive(grandchild)


def test_the_backend_shutdown_calls_stop_all():
    main = os.path.join(os.path.dirname(__file__), "..", "app", "main.py")
    with open(main, encoding="utf-8") as handle:
        assert "shell_jobs.stop_all" in handle.read()


# ── SEC-1: web_fetch total deadline ──────────────────────────────────────────

class _Drip(httpx.SyncByteStream):
    def __iter__(self):
        for _ in range(40):
            time.sleep(0.25)
            yield b"x"


def test_a_server_that_drips_one_byte_at_a_time_is_cut_at_the_total_deadline(monkeypatch):
    monkeypatch.setattr(wf, "_transport", lambda: httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, stream=_Drip())))
    monkeypatch.setattr(wf, "_resolve", lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr(wf, "TOTAL_TIMEOUT_S", 0.6)
    monkeypatch.setattr(wf, "TIMEOUT_S", 20.0)
    started = time.monotonic()
    result = wf.web_fetch("http://example.com/")
    assert time.monotonic() - started < 2.5
    assert result["success"] is False and "longer than" in result["error"]


# ── background run_command takes the same approval path as the foreground one ─

DANGEROUS = "rm -rf build"


def _runner(monkeypatch, tmp_path, replies):
    import agentic.agent_runner as ar
    from types import SimpleNamespace as NS
    from unittest import mock

    class Client:
        def __init__(self):
            self.replies = iter(replies)
            self.chat = NS(completions=NS(create=self.create))

        async def create(self, **kwargs):
            return next(self.replies)

    monkeypatch.setattr(ar, "get_openai_tool_declarations", lambda: [])
    monkeypatch.setattr(ar, "_all_tool_definitions", lambda: [])
    monkeypatch.setattr(ar.AgentRunner, "_get_architect_wisdom", lambda self: "architect wisdom")
    monkeypatch.setattr(ar.openai, "AsyncOpenAI", lambda **kw: Client())
    return ar, ar.AgentRunner("openai", "k", "m", str(tmp_path)), mock


def _tool_reply(arguments):
    from types import SimpleNamespace as NS
    import json
    calls = [NS(id="c1", function=NS(name="run_command", arguments=json.dumps(arguments)))]
    return NS(choices=[NS(message=NS(content="", tool_calls=calls), finish_reason="tool_calls")],
              usage=NS(prompt_tokens=1, completion_tokens=1))


def _final_reply():
    from types import SimpleNamespace as NS
    return NS(choices=[NS(message=NS(content="done", tool_calls=None), finish_reason="stop")],
              usage=NS(prompt_tokens=1, completion_tokens=1))


def _drive(monkeypatch, tmp_path, background, mode="step", bypass_background=False):
    """Runs one API turn whose model calls run_command and whose user refuses the card.
    Returns (approval prompts shown, shell_jobs.start calls)."""
    from types import SimpleNamespace as NS
    from agentic import approval_mode

    ar, runner, mock = _runner(monkeypatch, tmp_path, [_tool_reply({"command": DANGEROUS, "background": background}),
                                                      _final_reply()])
    monkeypatch.setattr(approval_mode, "current_mode", lambda: mode)
    started, prompts = [], []
    monkeypatch.setattr(shell_jobs, "start", lambda *a, **k: started.append(a) or "job-1")
    real_prompt = runner._approval_prompt

    def prompt(name, args):
        if bypass_background and args.get("background"):
            return None
        text = real_prompt(name, args)
        prompts.append(text)
        return text

    monkeypatch.setattr(runner, "_approval_prompt", prompt)
    monkeypatch.setattr(runner, "_await_approval",
                        mock.AsyncMock(return_value=NS(approved=False, summary="declined")))

    async def collect():
        return [event async for event in runner._run_inner("hello")]

    async def no_sleep(_):
        return None

    with mock.patch.object(ar.asyncio, "sleep", no_sleep):
        import asyncio
        asyncio.run(collect())
    return prompts, started


@pytest.mark.parametrize("mode", ["step", "balanced"])
def test_a_background_command_gets_the_same_approval_card_and_a_refusal_starts_nothing(monkeypatch, tmp_path, mode):
    foreground = _drive(monkeypatch, tmp_path, False, mode)
    background = _drive(monkeypatch, tmp_path, True, mode)
    assert foreground == ([DANGEROUS], [])
    assert background == foreground


def test_the_approval_test_goes_red_when_background_skips_the_card(monkeypatch, tmp_path):
    prompts, started = _drive(monkeypatch, tmp_path, True, "step", bypass_background=True)
    assert prompts == [] and len(started) == 1       # what the test above would catch
