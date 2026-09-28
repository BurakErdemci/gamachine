"""Live dictation over HTTP chunk sessions — the registry and its three routes.

WHAT IS PINNED HERE
    The bookkeeping bounds a caller cannot police from outside: how many
    sessions may be alive at once (MAX_SESSIONS), how long an idle one lingers
    (SESSION_TTL_S), and how much audio one chunk or one session may hold —
    plus the rejection details the renderer switches on. And the live-text
    contract: it only fires on a GPU engine once LIVE_MIN_AUDIO_BYTES has
    accumulated, is throttled by LIVE_INTERVAL_S, pins the language its first
    partial detected, and treats a failed partial decode as a dropped preview
    rather than a dropped session.

WHY THERE IS A FAKE SERVER
    Same reason as test_transcribe_route.py: whisper-server.exe and its model
    are packaging output, not part of a developer checkout. `stt_whisper.
    set_server` swaps the process-wide manager for an in-process fake that
    records every `transcribe` call; the real child process is covered
    separately, in test_stt_whisper_server.py.
"""

import base64
import contextlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from providers import stt_whisper
from routes.transcribe_routes import (
    MAX_CHUNK_B64_CHARS,
    MAX_CHUNK_BYTES,
    MAX_SESSION_BYTES,
    create_transcribe_router,
)


# ── Fake engine ─────────────────────────────────────────────────────────────

class FakeServer:
    """See test_transcribe_route.py — the identical stand-in, kept local so
    this file stays self-sufficient when run on its own."""

    def __init__(self, gpu=False, ready=True, running=True):
        self.gpu = gpu
        self._ready = ready
        self._running = running
        self.text = "merhaba dunya"
        self.response_language = "turkish"
        self.calls = []
        self.ensure_started_error = None
        self.wait_ready_error = None
        self.transcribe_error = None   # exception, or callable(call_no) -> exception | None
        self.stopped = False

    def ensure_started(self):
        if self.ensure_started_error is not None:
            raise self.ensure_started_error

    def wait_ready(self, timeout=None):
        if self.wait_ready_error is not None:
            raise self.wait_ready_error

    def is_ready(self):
        return self._ready

    def is_running(self):
        return self._running

    def died(self, grace_s=0.5):
        return not self._running

    def transcribe(self, pcm, language, timeout=None):
        self.calls.append({"pcm": pcm, "language": language, "timeout": timeout})
        error = self.transcribe_error
        if callable(error):
            error = error(len(self.calls))
        if error is not None:
            raise error
        return {"text": self.text, "language": self.response_language}

    def stop(self):
        self.stopped = True

    def lease(self):
        return contextlib.nullcontext(self)

    def touch(self):
        pass


class FakeDB:
    def __init__(self):
        self._data = {}

    def get_setting(self, key):
        return self._data.get(key)

    def set_setting(self, key, value):
        self._data[key] = value


@pytest.fixture(autouse=True)
def _clean_engine_state():
    stt_whisper.reset_sessions()
    yield
    stt_whisper.set_server(None)
    stt_whisper.reset_sessions()


@pytest.fixture
def fake_server():
    server = FakeServer()
    stt_whisper.set_server(server)
    return server


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(create_transcribe_router(db))
    # `raise_server_exceptions=False` matches what a real HTTP client sees (a
    # 500 response) rather than the test client's default of re-raising.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def ready(client, fake_server):
    """A client wired to a working fake engine — the state every test starts from."""
    return client


HEADERS = {"X-Session-Token": "dev"}


def _open(client, lang="tr"):
    return client.post("/transcribe/session", json={"lang": lang}, headers=HEADERS)


def _chunk(client, session_id, pcm=None, pcm_base64=None):
    if pcm_base64 is None:
        pcm_base64 = base64.b64encode(pcm or b"").decode("ascii")
    return client.post(f"/transcribe/session/{session_id}", json={"pcm_base64": pcm_base64}, headers=HEADERS)


def _finish(client, session_id, **body):
    return client.post(f"/transcribe/session/{session_id}/finish", json=body, headers=HEADERS)


def _new_session(client, lang="tr"):
    response = _open(client, lang)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, start=1000.0):
        self.value = start

    def __call__(self):
        return self.value


# ── Session creation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["tr", "en"])
def test_a_session_can_be_opened_for_each_supported_language(ready, lang):
    response = _open(ready, lang)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lang"] == lang
    assert body["session_id"]


def test_an_unsupported_language_cannot_open_a_session(ready):
    response = _open(ready, "de")
    assert response.status_code == 400
    assert response.json()["detail"] == "stt_bad_lang"


def test_a_missing_engine_refuses_the_session_with_503(client, fake_server):
    fake_server.ensure_started_error = stt_whisper.SttEngineMissing(["whisper-server.exe"])
    response = _open(client)
    assert response.status_code == 503
    assert response.json()["detail"] == "stt_model_missing"


def test_a_fifth_session_is_refused_and_a_finish_frees_the_slot(ready):
    """MAX_SESSIONS is a memory bound: each live session buffers its own audio."""
    ids = [_new_session(ready) for _ in range(stt_whisper.MAX_SESSIONS)]

    refused = _open(ready)
    assert refused.status_code == 503
    assert refused.json()["detail"] == "stt_busy"

    assert _finish(ready, ids[0]).status_code == 200
    assert _open(ready).status_code == 200, "finishing a session did not free its slot"


# ── Chunks ────────────────────────────────────────────────────────────────────

def test_the_reported_byte_count_is_the_session_total_not_the_chunk(ready):
    session_id = _new_session(ready)
    assert _chunk(ready, session_id, b"\x00" * 800).json()["bytes"] == 800
    assert _chunk(ready, session_id, b"\x00" * 400).json()["bytes"] == 1200


def test_an_oversized_base64_string_is_refused_before_it_is_decoded(ready):
    """The string below is not valid base64 at all, so a 400 here would prove
    the decoder ran before the length check — the whole point of the precheck."""
    session_id = _new_session(ready)
    response = _chunk(ready, session_id, pcm_base64="!" * (MAX_CHUNK_B64_CHARS + 4))
    assert response.status_code == 413
    assert response.json()["detail"] == "stt_too_large"


def test_a_chunk_that_is_not_base64_is_rejected(ready):
    session_id = _new_session(ready)
    response = _chunk(ready, session_id, pcm_base64="not base64 !!!")
    assert response.status_code == 400
    assert response.json()["detail"] == "stt_bad_base64"


def test_a_chunk_over_the_per_chunk_cap_is_refused(ready):
    session_id = _new_session(ready)
    payload = base64.b64encode(b"\x00" * (MAX_CHUNK_BYTES + 2)).decode("ascii")
    assert len(payload) <= MAX_CHUNK_B64_CHARS, "this body must pass the string precheck"
    response = _chunk(ready, session_id, pcm_base64=payload)
    assert response.status_code == 413
    assert response.json()["detail"] == "stt_too_large"


def test_the_session_total_cap_refuses_the_chunk_but_keeps_the_session(ready):
    session_id = _new_session(ready)
    block = b"\x00" * MAX_CHUNK_BYTES
    for _ in range(MAX_SESSION_BYTES // MAX_CHUNK_BYTES):
        assert _chunk(ready, session_id, block).status_code == 200

    response = _chunk(ready, session_id, block)
    assert response.status_code == 413
    assert response.json()["detail"] == "stt_too_large"
    # Alive on purpose: the caller still has to collect what was already said.
    assert _finish(ready, session_id).status_code == 200


def test_an_odd_byte_count_is_rejected(ready):
    """Half a 16-bit sample; the engine would reinterpret every following byte."""
    session_id = _new_session(ready)
    response = _chunk(ready, session_id, b"\x01\x02\x03")
    assert response.status_code == 400
    assert response.json()["detail"] == "stt_wrong_format"


def test_an_empty_chunk_refreshes_the_idle_timer(ready, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(stt_whisper, "_now", clock)
    session_id = _new_session(ready)
    clock.value += stt_whisper.SESSION_TTL_S - 1
    assert _chunk(ready, session_id, b"").status_code == 200
    clock.value += stt_whisper.SESSION_TTL_S - 1
    assert _chunk(ready, session_id, b"").status_code == 200


def test_a_chunk_for_an_unknown_session_is_a_404(ready):
    response = _chunk(ready, "Zm9vYmFyMTIzNDU2", b"\x00\x00")
    assert response.status_code == 404
    assert response.json()["detail"] == "stt_no_session"


def test_a_session_id_outside_the_token_alphabet_is_a_404_not_a_lookup(ready):
    response = _chunk(ready, "a.b/../etc", b"\x00\x00")
    assert response.status_code == 404


# ── Live text ─────────────────────────────────────────────────────────────────

def test_below_the_live_minimum_no_decode_happens(ready, fake_server):
    fake_server.gpu = True
    session_id = _new_session(ready)
    below = b"\x00\x00" * (stt_whisper.LIVE_MIN_AUDIO_BYTES // 2 - 100)
    response = _chunk(ready, session_id, below)
    assert response.status_code == 200
    assert response.json()["partial"] == ""
    assert fake_server.calls == []


def test_live_text_pins_the_language_only_after_a_supported_detection_on_2_5s_of_audio(ready, fake_server, monkeypatch):
    """LIVE_PIN_MIN_BYTES (2.5 s), not LIVE_MIN_AUDIO_BYTES (1.5 s): auto
    detection is unreliable before 2.5 s (measured 28 Sep 2026: only 6/12 of
    the owner's clips were right at 1.5 s, 12/12 at 2.5 s). Every decode below
    that reuses "auto"; only one made on >= 2.5 s of audio may pin."""
    fake_server.gpu = True
    fake_server.response_language = "turkish"
    clock = _Clock()
    monkeypatch.setattr(stt_whisper, "_now", clock)
    session_id = _new_session(ready, lang="tr")

    # Crosses LIVE_MIN_AUDIO_BYTES (1.5 s = 48000 bytes) but stays under
    # LIVE_PIN_MIN_BYTES (2.5 s = 80000 bytes): decodes with "auto", updates
    # the partial, does not pin.
    first = b"\x00\x00" * 24010          # 48020 bytes
    response = _chunk(ready, session_id, first)
    assert response.status_code == 200
    assert len(fake_server.calls) == 1
    assert fake_server.calls[0]["language"] == "auto"
    assert response.json()["partial"] == fake_server.text

    # Still under 2.5 s in total (68020 bytes): a second "auto" decode, still
    # not pinned.
    clock.value += stt_whisper.LIVE_INTERVAL_S + 0.1
    second = b"\x00\x00" * 10000         # +20000 bytes -> 68020 total
    assert _chunk(ready, session_id, second).status_code == 200
    assert len(fake_server.calls) == 2
    assert fake_server.calls[1]["language"] == "auto"

    # Crosses LIVE_PIN_MIN_BYTES (80020 bytes total). This decode itself still
    # runs as "auto" (the pin decision is made from ITS result, after it
    # returns); the session pins for the NEXT one.
    clock.value += stt_whisper.LIVE_INTERVAL_S + 0.1
    third = b"\x00\x00" * 6000           # +12000 bytes -> 80020 total
    assert _chunk(ready, session_id, third).status_code == 200
    assert len(fake_server.calls) == 3
    assert fake_server.calls[2]["language"] == "auto"

    # Now pinned: the next decode reuses the raw language name the engine
    # reported ("turkish"), not "auto".
    clock.value += stt_whisper.LIVE_INTERVAL_S + 0.1
    assert _chunk(ready, session_id, b"\x00\x00" * 50).status_code == 200
    assert len(fake_server.calls) == 4
    assert fake_server.calls[3]["language"] == "turkish"


def test_an_early_misdetection_is_discarded_and_does_not_pin(ready, fake_server, monkeypatch):
    """An early guess in the wrong script ("コミュタッパー" for Turkish at
    1.5 s, measured 28 Sep 2026) must not be shown, and must not lock the
    session into decoding a language nobody is speaking."""
    fake_server.gpu = True
    fake_server.response_language = "japanese"   # not in SUPPORTED_LANGS ("tr", "en")
    clock = _Clock()
    monkeypatch.setattr(stt_whisper, "_now", clock)
    session_id = _new_session(ready, lang="tr")

    crossing = b"\x00\x00" * (stt_whisper.LIVE_MIN_AUDIO_BYTES // 2 + 10)
    response = _chunk(ready, session_id, crossing)
    assert response.status_code == 200
    assert len(fake_server.calls) == 1
    # The misdetected text is never shown; the partial stays at its previous
    # (empty) value.
    assert response.json()["partial"] == ""

    fake_server.response_language = "turkish"
    clock.value += stt_whisper.LIVE_INTERVAL_S + 0.1
    response = _chunk(ready, session_id, b"\x00\x00" * 50)
    assert response.status_code == 200
    assert len(fake_server.calls) == 2
    assert fake_server.calls[1]["language"] == "auto"    # the misdetection did not pin anything
    assert response.json()["partial"] == fake_server.text


def test_without_gpu_no_partial_transcribe_call_is_ever_made(ready, fake_server):
    fake_server.gpu = False
    session_id = _new_session(ready)
    plenty = b"\x00\x00" * (stt_whisper.LIVE_MIN_AUDIO_BYTES // 2 + 1000)
    response = _chunk(ready, session_id, plenty)
    assert response.status_code == 200
    assert response.json()["partial"] == ""
    assert fake_server.calls == []


def test_a_partial_decode_failure_does_not_end_the_session(ready, fake_server):
    fake_server.gpu = True
    fake_server.transcribe_error = lambda n: stt_whisper.SttEngineFailed("boom") if n == 1 else None
    session_id = _new_session(ready, lang="tr")
    crossing = b"\x00\x00" * (stt_whisper.LIVE_MIN_AUDIO_BYTES // 2 + 10)

    response = _chunk(ready, session_id, crossing)
    assert response.status_code == 200
    assert response.json()["partial"] == ""     # the failed decode left no partial text
    assert len(fake_server.calls) == 1

    fake_server.transcribe_error = None
    finished = _finish(ready, session_id)
    assert finished.status_code == 200
    assert finished.json()["text"] == fake_server.text


# ── Finish ────────────────────────────────────────────────────────────────────

def test_finish_response_has_the_full_contract(ready, fake_server):
    fake_server.gpu = True
    fake_server.response_language = "english"
    session_id = _new_session(ready, lang="en")
    _chunk(ready, session_id, b"\x01\x02" * 16000)     # 32000 bytes = 1 s at 16 kHz
    response = _finish(ready, session_id)
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == fake_server.text
    assert body["duration_ms"] == 1000
    assert body["language"] == "en"
    assert body["language_mode"] == "auto"
    assert body["gpu"] is True


def test_finish_on_cpu_uses_the_sessions_own_language(ready, fake_server):
    fake_server.gpu = False
    fake_server.response_language = "turkish"
    session_id = _new_session(ready, lang="tr")
    response = _finish(ready, session_id)
    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "tr"
    assert body["language_mode"] == "fixed"
    assert body["gpu"] is False


def test_the_session_is_gone_after_finish(ready):
    session_id = _new_session(ready)
    assert _finish(ready, session_id).status_code == 200
    assert _chunk(ready, session_id, b"\x00\x00").status_code == 404
    second = _finish(ready, session_id)
    assert second.status_code == 404
    assert second.json()["detail"] == "stt_no_session"


def test_a_discarded_session_is_dropped_without_a_recognition(ready, fake_server):
    """A cancelled dictation must not pay for a result nobody will read."""
    fake_server.gpu = False   # keep the live decoder out of the call count
    session_id = _new_session(ready)
    _chunk(ready, session_id, b"\x01\x02" * 4000)
    response = _finish(ready, session_id, discard=True)
    assert response.status_code == 200
    assert response.json() == {"text": "", "duration_ms": 0}
    assert fake_server.calls == []
    assert _chunk(ready, session_id, b"\x00\x00").status_code == 404


@pytest.mark.parametrize("value", [1, "yes"])
def test_a_non_boolean_discard_value_is_rejected_not_coerced(ready, value):
    """Audit finding: plain `bool` coerces `1` and `"yes"` to True, so a stray
    non-boolean value silently discarded a dictation instead of being
    refused. `StrictBool` turns both into a 422 instead."""
    session_id = _new_session(ready)
    response = _finish(ready, session_id, discard=value)
    assert response.status_code == 422
    # The session is untouched by the refused request.
    assert _chunk(ready, session_id, b"\x00\x00").status_code == 200


def test_finish_on_an_unknown_session_is_a_404(ready):
    response = _finish(ready, "Zm9vYmFyMTIzNDU2")
    assert response.status_code == 404
    assert response.json()["detail"] == "stt_no_session"


def test_a_wait_ready_failure_on_finish_is_a_503(ready, fake_server):
    fake_server.wait_ready_error = stt_whisper.SttEngineFailed("did not become ready")
    session_id = _new_session(ready)
    response = _finish(ready, session_id)
    assert response.status_code == 503
    assert response.json()["detail"] == "stt_engine_failed"


def test_a_transcribe_failure_on_finish_is_also_a_503(ready, fake_server):
    session_id = _new_session(ready)
    # transcribe_final() skips the transcribe() call entirely for an empty
    # recording (nothing to send), so this needs real audio to reach it.
    _chunk(ready, session_id, b"\x01\x02" * 100)
    fake_server.transcribe_error = stt_whisper.SttEngineFailed("engine crashed mid-request")
    response = _finish(ready, session_id)
    assert response.status_code == 503
    assert response.json()["detail"] == "stt_engine_failed"


# ── Idle TTL ──────────────────────────────────────────────────────────────────

def test_an_abandoned_session_expires_after_the_idle_ttl(ready, monkeypatch):
    """A renderer that crashes mid-dictation never sends finish; without the
    TTL its slot would be held until the backend restarts."""
    clock = _Clock()
    monkeypatch.setattr(stt_whisper, "_now", clock)
    session_id = _new_session(ready)

    clock.value += stt_whisper.SESSION_TTL_S - 1
    assert _chunk(ready, session_id, b"\x00\x00").status_code == 200

    clock.value += stt_whisper.SESSION_TTL_S + 1
    expired = _chunk(ready, session_id, b"\x00\x00")
    assert expired.status_code == 404
    assert expired.json()["detail"] == "stt_no_session"


def test_expiry_frees_the_slot_of_a_session_nobody_finished(ready, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(stt_whisper, "_now", clock)
    for _ in range(stt_whisper.MAX_SESSIONS):
        _new_session(ready)
    assert _open(ready).status_code == 503

    clock.value += stt_whisper.SESSION_TTL_S + 1
    assert _open(ready).status_code == 200


# ── The gate ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/transcribe/session",
    "/transcribe/session/abc",
    "/transcribe/session/abc/finish",
])
def test_every_session_route_requires_the_session_token_header(ready, path):
    """The header is declared without a default, so its absence is a 422 from
    FastAPI's own validation — the handler body never runs."""
    assert ready.post(path, json={"lang": "tr", "pcm_base64": ""}).status_code == 422
