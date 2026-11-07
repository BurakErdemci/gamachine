"""Dictation without a length limit, and without Whisper's silence credits.

A session used to end at 2 MiB / 300 s and its /finish decoded the whole
recording. Now a session takes 60 minutes of audio, long audio is committed in
windows while the user speaks, and /finish decodes only the tail. Whisper's
subtitle credits on silence ("Altyazı M.K.") are filtered out of every decode.

The engine is a fake that records its calls; whether the real whisper-server
accepts the `suppress_nst` field is not provable here.
"""
import base64
import contextlib
import json
import struct
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from providers import stt_whisper
from routes import transcribe_routes
from routes.transcribe_routes import MAX_CHUNK_BYTES, create_transcribe_router

HEADERS = {"X-Session-Token": "dev"}
SECOND = stt_whisper.SAMPLE_RATE * 2          # bytes of PCM per second


class FakeServer:
    def __init__(self):
        self.gpu = False
        self.calls = []
        self.error_on_call = None             # call number that raises SttEngineFailed

    def ensure_started(self):
        pass

    def wait_ready(self, timeout=None):
        pass

    def is_ready(self):
        return True

    def is_running(self):
        return True

    def died(self, grace_s=0.5):
        return False

    def transcribe(self, pcm, language, timeout=None):
        self.calls.append({"pcm": pcm, "language": language})
        if self.error_on_call == len(self.calls):
            raise stt_whisper.SttEngineFailed("boom")
        return {"text": "merhaba dunya", "language": "turkish"}

    def stop(self):
        pass

    def lease(self):
        return contextlib.nullcontext(self)

    def touch(self):
        pass


class Clock:
    def __init__(self, start=1000.0):
        self.value = start

    def __call__(self):
        return self.value


@pytest.fixture(autouse=True)
def _clean_engine_state():
    stt_whisper.reset_sessions()
    yield
    stt_whisper.set_server(None)
    stt_whisper.reset_sessions()


@pytest.fixture
def fake():
    server = FakeServer()
    stt_whisper.set_server(server)
    return server


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(create_transcribe_router(None))
    return TestClient(app, raise_server_exceptions=False)


def _new_session(client):
    response = client.post("/transcribe/session", json={"lang": "tr"}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def _chunk(client, session_id, pcm):
    return client.post(f"/transcribe/session/{session_id}",
                       json={"pcm_base64": base64.b64encode(pcm).decode("ascii")}, headers=HEADERS)


def _finish(client, session_id):
    return client.post(f"/transcribe/session/{session_id}/finish", json={}, headers=HEADERS)


def _settle(session_id):
    """Waits for the background window decode, if one is running."""
    thread = stt_whisper._get(session_id).commit_thread
    if thread is not None:
        thread.join(10)


def _tone(seconds, quiet=None):
    """Loud constant PCM; `quiet` = (start_s, end_s) is silent."""
    loud = struct.pack("<h", 4000)
    samples = bytearray(loud * int(seconds * stt_whisper.SAMPLE_RATE))
    if quiet:
        a, b = (int(x * stt_whisper.SAMPLE_RATE) * 2 for x in quiet)
        samples[a:b] = b"\x00" * (b - a)
    return bytes(samples)


# ── The session ceiling ──────────────────────────────────────────────────────

def test_the_ceiling_is_sixty_minutes_of_audio():
    assert transcribe_routes.MAX_SESSION_BYTES == 60 * 60 * SECOND
    assert stt_whisper.SESSION_MAX_LIFETIME_S >= 3700


def test_a_session_takes_more_than_2_mib(client, fake):
    session_id = _new_session(client)
    block = b"\x00" * MAX_CHUNK_BYTES
    for _ in range(48):                           # 3 MiB
        assert _chunk(client, session_id, block).status_code == 200
    assert _chunk(client, session_id, b"\x00\x00").json()["bytes"] == 48 * MAX_CHUNK_BYTES + 2


def test_the_cap_counts_audio_that_was_already_committed(client, fake, monkeypatch):
    monkeypatch.setattr(transcribe_routes, "MAX_SESSION_BYTES", 40 * SECOND)
    session_id = _new_session(client)
    for _ in range(80):                           # 40 s in 0.5 s chunks; a window is committed on the way
        assert _chunk(client, session_id, _tone(0.5)).status_code == 200
    _settle(session_id)
    assert stt_whisper.session_bytes(session_id) == 40 * SECOND
    over = _chunk(client, session_id, b"\x00\x00")
    assert over.status_code == 413
    assert over.json()["detail"] == "stt_too_large"


def test_a_session_lives_past_300_s_while_chunks_arrive_and_still_ends_when_idle(client, fake, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(stt_whisper, "_now", clock)
    session_id = _new_session(client)
    for _ in range(8):                            # 8 x 60 s = 480 s
        clock.value += 60
        assert _chunk(client, session_id, b"\x00\x00").status_code == 200

    clock.value += stt_whisper.SESSION_TTL_S + 1
    gone = _chunk(client, session_id, b"\x00\x00")
    assert gone.status_code == 404
    assert gone.json()["detail"] == "stt_no_session"


# ── Windowed commit ──────────────────────────────────────────────────────────

def test_a_70_s_session_is_decoded_in_windows_and_finish_only_decodes_the_tail(client, fake):
    session_id = _new_session(client)
    audio = _tone(70, quiet=(26.0, 26.1))         # the quietest frame of the first 24-28 s search
    step = SECOND // 2
    for offset in range(0, len(audio), step):
        assert _chunk(client, session_id, audio[offset:offset + step]).status_code == 200
        _settle(session_id)

    response = _finish(client, session_id)
    assert response.status_code == 200
    windows, tail = fake.calls[:-1], fake.calls[-1]
    assert len(windows) >= 2
    assert len(tail["pcm"]) < stt_whisper.COMMIT_AFTER_BYTES
    assert all(len(w["pcm"]) <= stt_whisper.COMMIT_AFTER_BYTES for w in windows)
    # Every second of audio is decoded exactly once, in speaking order.
    assert b"".join(c["pcm"] for c in fake.calls) == audio
    assert response.json()["text"] == " ".join(["merhaba dunya"] * len(fake.calls))
    assert response.json()["duration_ms"] == 70_000


def test_a_window_is_cut_in_the_quietest_frame_of_the_last_seconds(client, fake):
    session_id = _new_session(client)
    audio = _tone(28, quiet=(26.0, 26.1))
    step = SECOND // 2
    for offset in range(0, len(audio), step):
        _chunk(client, session_id, audio[offset:offset + step])
    _settle(session_id)
    assert len(fake.calls[0]["pcm"]) == int(26.05 * SECOND)


def test_committed_text_stays_in_the_live_text(client, fake):
    session_id = _new_session(client)
    audio = _tone(30)
    step = SECOND // 2
    for offset in range(0, len(audio), step):
        _chunk(client, session_id, audio[offset:offset + step])
    _settle(session_id)
    assert _chunk(client, session_id, _tone(0.5)).json()["partial"] == "merhaba dunya"


def test_a_failed_window_decode_keeps_its_audio_for_finish(client, fake):
    fake.error_on_call = 1
    session_id = _new_session(client)
    audio = _tone(30)
    step = SECOND // 2
    for offset in range(0, len(audio), step):
        assert _chunk(client, session_id, audio[offset:offset + step]).status_code == 200
    _settle(session_id)

    response = _finish(client, session_id)
    assert response.status_code == 200
    assert fake.calls[-1]["pcm"] == audio         # nothing was lost
    assert response.json()["text"] == "merhaba dunya"


# ── Hallucination filter ─────────────────────────────────────────────────────

def _segment(text, no_speech=0.01, logprob=-0.2):
    return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob}


def test_inference_fields_suppress_non_speech_tokens():
    assert stt_whisper.inference_fields("tr")["suppress_nst"] == "true"


def test_a_subtitle_credit_segment_is_dropped():
    data = {"text": " Merhaba nasılsın. Altyazı M.K.", "segments": [
        _segment(" Merhaba nasılsın."), _segment(" Altyazı M.K.")]}
    assert stt_whisper.clean_text(data) == "Merhaba nasılsın."


def test_a_segment_the_model_doubts_is_dropped():
    data = {"text": " Evet. Bugün hava güzel.", "segments": [
        _segment(" Evet.", no_speech=0.9, logprob=-1.4), _segment(" Bugün hava güzel.")]}
    assert stt_whisper.clean_text(data) == "Bugün hava güzel."


def test_a_segment_is_kept_when_only_one_doubt_applies():
    data = {"text": " Tamam.", "segments": [_segment(" Tamam.", no_speech=0.9, logprob=-0.3)]}
    assert stt_whisper.clean_text(data) == "Tamam."


def test_a_sentence_that_merely_contains_altyazi_is_kept():
    for text in ("Bu filmin altyazı dosyasını indirdim.", "Altyazı dosyasını aç.", "Altyazı ekle ve kaydet."):
        data = {"text": " " + text, "segments": [_segment(" " + text)]}
        assert stt_whisper.clean_text(data) == text


def test_credit_phrases_in_other_languages_are_dropped_as_whole_segments():
    for text in ("Abone ol!", "Thank you for watching.", "Subtitles by the Amara.org community",
                 "Untertitel der Amara.org-Community"):
        assert stt_whisper.clean_text({"text": " " + text, "segments": [_segment(" " + text)]}) == ""


def test_without_segments_the_whole_text_gets_the_phrase_filter():
    assert stt_whisper.clean_text({"text": " Altyazı M.K."}) == ""
    assert stt_whisper.clean_text({"text": " Merhaba dünya. "}) == "Merhaba dünya."


def test_an_untouched_answer_keeps_whispers_own_text():
    data = {"text": " Merhaba dünya.", "segments": [_segment(" Merhaba"), _segment(" dünya.")]}
    assert stt_whisper.clean_text(data) == "Merhaba dünya."


def test_the_server_sends_the_field_and_filters_the_answer():
    server = stt_whisper.WhisperServer(command=lambda: ["unused"])
    server._proc = types.SimpleNamespace(poll=lambda: None)
    server._ready.set()
    server._port, server._prefix = 1, "/p"
    sent = {}
    answer = json.dumps({"text": " Merhaba. Altyazı M.K.", "language": "turkish", "segments": [
        _segment(" Merhaba."), _segment(" Altyazı M.K.")]}).encode()

    class _Res:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return answer

    def open_(req, timeout=None):
        sent["body"] = req.data
        return _Res()

    server._opener = types.SimpleNamespace(open=open_)
    result = server.transcribe(b"\x00\x00" * 100, "tr")
    assert b'name="suppress_nst"\r\n\r\ntrue' in sent["body"]
    assert result["text"] == "Merhaba."
