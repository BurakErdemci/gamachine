"""`POST /transcribe` and `/transcribe/settings` — the one-shot dictation route.

WHAT IS PINNED HERE
    The validation order and detail strings the renderer switches on (token,
    lang, base64 size before decoding, RIFF shape, sample format, truncation,
    frame count); the language-selection contract that turns the engine's GPU
    state and the auto_language_cpu setting into "auto" vs. a fixed UI
    language; and the settings GET/POST round trip.

WHY THERE IS A FAKE SERVER
    whisper-server.exe and its ~874 MB model are packaging output (fetched by
    Backend/vendor/build_whisper.ps1), not part of a developer checkout.
    `stt_whisper.set_server` swaps the process-wide manager for an in-process
    fake that records what it was asked to transcribe. A REAL whisper-server
    child is exercised separately, in test_stt_whisper_server.py.
"""

import base64
import io
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from providers import stt_whisper
from routes import transcribe_routes
from routes.transcribe_routes import MAX_B64_CHARS, create_transcribe_router


# ── Fake engine ─────────────────────────────────────────────────────────────

class FakeServer:
    """Stands in for `stt_whisper.WhisperServer`. Records every `transcribe`
    call so a test can assert the language it was asked to use."""

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


class FakeDB:
    """Dict-backed stand-in for the real sqlite `get_setting`/`set_setting`."""

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
    return TestClient(app)


HEADERS = {"X-Session-Token": "dev"}


def _wav(frames=16000, channels=1, width=2, rate=16000, pcm=None):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm if pcm is not None else b"\x01\x02" * (frames * channels * width // 2))
    return buf.getvalue()


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _post(client, **body):
    return client.post("/transcribe", json=body, headers=HEADERS)


# ── Happy path ──────────────────────────────────────────────────────────────

def test_a_valid_wav_goes_through_transcribe_final(client, fake_server):
    response = _post(client, lang="tr", wav_base64=_b64(_wav(frames=16000)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == fake_server.text
    assert body["lang"] == "tr"
    # 16000 frames at 16 kHz is exactly one second; the number is computed
    # from the audio, not from how long the fake "recognition" took.
    assert body["duration_ms"] == 1000
    assert body["language"] == "tr"
    assert body["language_mode"] == "fixed"
    assert len(fake_server.calls) == 1
    assert fake_server.calls[0]["pcm"] == b"\x01\x02" * 16000


def test_an_empty_recognition_is_a_200_not_an_error(client, fake_server):
    """Silence is a normal dictation outcome; the renderer has a named state
    for it, so it must not read as a malformed request."""
    fake_server.text = ""
    response = _post(client, lang="tr", wav_base64=_b64(_wav()))
    assert response.status_code == 200
    assert response.json()["text"] == ""


# ── Language choice ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "gpu, auto_cpu_setting, ui_lang, server_reports, expected_call_lang, expected_mode, expected_lang_code",
    [
        (True, None, "tr", "turkish", "auto", "auto", "tr"),
        (True, None, "en", "english", "auto", "auto", "en"),
        (False, None, "tr", "turkish", "tr", "fixed", "tr"),
        (False, None, "en", "english", "en", "fixed", "en"),
        (False, "1", "tr", "turkish", "auto", "auto", "tr"),
        (None, None, "tr", "turkish", "tr", "fixed", "tr"),
    ],
    ids=["gpu-auto-tr", "gpu-auto-en", "cpu-fixed-tr", "cpu-fixed-en", "cpu-setting-auto", "gpu-unknown-is-cpu"],
)
def test_language_choice_end_to_end(
    client, fake_server, db, gpu, auto_cpu_setting, ui_lang, server_reports,
    expected_call_lang, expected_mode, expected_lang_code,
):
    fake_server.gpu = gpu
    fake_server.response_language = server_reports
    if auto_cpu_setting is not None:
        db.set_setting(transcribe_routes.AUTO_LANGUAGE_CPU_KEY, auto_cpu_setting)
    response = _post(client, lang=ui_lang, wav_base64=_b64(_wav()))
    assert response.status_code == 200, response.text
    body = response.json()
    assert fake_server.calls[0]["language"] == expected_call_lang
    assert body["language_mode"] == expected_mode
    assert body["language"] == expected_lang_code
    # The one-shot route's response contract has no "gpu" key (unlike session
    # finish); the fields above already cover what the engine's GPU state
    # decided.


# ── Rejections ────────────────────────────────────────────────────────────────

def test_an_unsupported_language_is_rejected(client, fake_server):
    response = _post(client, lang="de", wav_base64=_b64(_wav()))
    assert response.status_code == 400
    assert response.json()["detail"] == "stt_bad_lang"


def test_a_body_that_is_not_base64_is_rejected(client, fake_server):
    response = _post(client, lang="tr", wav_base64="not base64 !!!")
    assert response.status_code == 400
    assert response.json()["detail"] == "stt_bad_base64"


def test_bytes_that_are_not_a_riff_file_are_rejected(client, fake_server):
    response = _post(client, lang="tr", wav_base64=_b64(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64))
    assert response.status_code == 400
    assert response.json()["detail"] == "stt_not_wav"


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("stereo", {"channels": 2}),
        ("8 kHz", {"rate": 8000}),
        ("8-bit", {"width": 1}),
    ],
)
def test_a_wav_that_is_not_16k_mono_16bit_is_rejected(client, fake_server, name, kwargs):
    response = _post(client, lang="tr", wav_base64=_b64(_wav(frames=1600, **kwargs)))
    assert response.status_code == 400, name
    assert response.json()["detail"] == "stt_wrong_format", name


@pytest.mark.parametrize(
    "keep_bytes",
    [46, 44 + 8000],
    ids=["two bytes of a declared second", "a quarter of a declared second"],
)
def test_a_wav_whose_data_chunk_is_shorter_than_declared_is_rejected(client, fake_server, keep_bytes):
    """`wave` reports the declared frame count; `readframes` silently returns
    fewer bytes than declared. The audit probe posted a file declaring 32,000
    PCM bytes with two present and got a 200 whose recognition ran on two
    bytes; this pins the fix."""
    truncated = _wav(frames=16000)[:keep_bytes]
    response = _post(client, lang="tr", wav_base64=_b64(truncated))
    assert response.status_code == 400
    assert response.json()["detail"] == "stt_not_wav"
    assert fake_server.calls == []


def test_a_wav_with_zero_frames_is_rejected(client, fake_server):
    response = _post(client, lang="tr", wav_base64=_b64(_wav(pcm=b"")))
    assert response.status_code == 400
    assert response.json()["detail"] == "stt_empty_audio"


def test_an_oversize_body_is_refused_before_it_is_decoded(client, fake_server, monkeypatch):
    """The cap is on the base64 STRING, and this is the point of it: a
    hostile 50 MB string must cost the backend a `len()`, not a 37 MB
    allocation. Patching `b64decode` to explode proves the ordering."""

    def _explode(*args, **kwargs):
        raise AssertionError("base64 was decoded despite the string exceeding the cap")

    monkeypatch.setattr(base64, "b64decode", _explode)
    response = _post(client, lang="tr", wav_base64="A" * (MAX_B64_CHARS + 1))
    assert response.status_code == 413
    assert response.json()["detail"] == "stt_too_large"


# ── Engine errors ─────────────────────────────────────────────────────────────

def test_a_missing_engine_is_a_503(client, fake_server):
    fake_server.ensure_started_error = stt_whisper.SttEngineMissing(["whisper-server.exe"])
    response = _post(client, lang="tr", wav_base64=_b64(_wav()))
    assert response.status_code == 503
    assert response.json()["detail"] == "stt_model_missing"


def test_an_engine_failure_is_a_503(client, fake_server):
    fake_server.wait_ready_error = stt_whisper.SttEngineFailed("did not become ready")
    response = _post(client, lang="tr", wav_base64=_b64(_wav()))
    assert response.status_code == 503
    assert response.json()["detail"] == "stt_engine_failed"


# ── The token gate ────────────────────────────────────────────────────────────

class TestTheTokenGate:
    """The suite-wide conftest runs token-less on purpose; this class opts
    back IN to a configured token, otherwise `_check_token` returns early and
    the rejection tests would pass with no gate present at all."""

    TOKEN = "transcribe-token-4f21"

    @pytest.fixture(autouse=True)
    def _real_token(self, monkeypatch):
        monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
        monkeypatch.setenv("LOCAL_APP_TOKEN", self.TOKEN)

    def _body(self):
        return {"lang": "tr", "wav_base64": _b64(_wav())}

    def test_a_request_without_the_header_never_reaches_the_handler(self, client, fake_server):
        # The header is declared required, so FastAPI validation rejects with
        # 422 before the body runs — measured, not assumed.
        response = client.post("/transcribe", json=self._body())
        assert response.status_code == 422
        assert fake_server.calls == []

    def test_a_wrong_token_is_rejected(self, client, fake_server):
        response = client.post("/transcribe", json=self._body(), headers={"X-Session-Token": "WRONG"})
        assert response.status_code == 401
        assert fake_server.calls == []

    def test_the_configured_token_is_accepted(self, client, fake_server):
        response = client.post("/transcribe", json=self._body(), headers={"X-Session-Token": self.TOKEN})
        assert response.status_code == 200


# ── Settings ──────────────────────────────────────────────────────────────────

def test_settings_round_trip(client, db):
    initial = client.get("/transcribe/settings", headers=HEADERS)
    assert initial.status_code == 200
    assert initial.json() == {"auto_language_cpu": False}

    updated = client.post("/transcribe/settings", json={"auto_language_cpu": True}, headers=HEADERS)
    assert updated.status_code == 200
    assert updated.json() == {"auto_language_cpu": True}

    confirmed = client.get("/transcribe/settings", headers=HEADERS)
    assert confirmed.status_code == 200
    assert confirmed.json() == {"auto_language_cpu": True}
    assert db.get_setting(transcribe_routes.AUTO_LANGUAGE_CPU_KEY) == "1"


@pytest.mark.parametrize("value", [1, "yes"])
def test_settings_post_rejects_a_non_bool_value(client, value):
    """StrictBool: `1`/`"yes"` must not silently coerce to True."""
    response = client.post("/transcribe/settings", json={"auto_language_cpu": value}, headers=HEADERS)
    assert response.status_code == 422
