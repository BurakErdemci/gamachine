"""Live dictation partials follow the owner's language rule (8 Nov 2026).

WHAT IS PINNED HERE
    A Turkish session asks the server for "tr" from its very first live
    partial (no detection pass); an English session keeps detecting ("auto");
    the user's auto opt-in forces "auto" for a Turkish session too.
    Runs against the stand-in `fake_whisper_server.py` child process, never a
    real whisper-server.
"""

import sys
from pathlib import Path

import pytest

from providers import stt_whisper

FAKE = str(Path(__file__).with_name("fake_whisper_server.py"))
ENOUGH_AUDIO = b"\x00\x00" * (stt_whisper.LIVE_MIN_AUDIO_BYTES // 2 + 10)


@pytest.fixture
def spied_languages(tmp_path, monkeypatch):
    model = tmp_path / "model.bin"
    model.write_bytes(b"fake-model-bytes")
    server = stt_whisper.WhisperServer(
        command=lambda: [sys.executable, FAKE, "--fake-gpu", "on", "--fake-startup-delay", "0.1"],
        model=lambda: str(model),
        threads=2,
        startup_timeout_s=10,
    )
    stt_whisper.reset_sessions()
    stt_whisper.set_server(server)
    languages = []
    real_transcribe = server.transcribe

    def spy(pcm, language, timeout=stt_whisper.FINAL_TIMEOUT_S):
        languages.append(language)
        return real_transcribe(pcm, language, timeout=timeout)

    monkeypatch.setattr(server, "transcribe", spy)
    yield server, languages
    server.stop()
    stt_whisper.set_server(None)
    stt_whisper.reset_sessions()


def _first_partial_language(server, languages, ui_lang, auto_on_cpu=False):
    session_id = stt_whisper.open_session(ui_lang)
    server.wait_ready()
    stt_whisper.feed(session_id, ENOUGH_AUDIO, auto_on_cpu=auto_on_cpu)
    return languages[0] if languages else None


def test_a_turkish_session_requests_tr_from_the_first_partial(spied_languages):
    server, languages = spied_languages
    assert _first_partial_language(server, languages, "tr") == "tr"


def test_an_english_session_keeps_detecting_in_live_partials(spied_languages):
    server, languages = spied_languages
    assert _first_partial_language(server, languages, "en") == "auto"


def test_the_auto_opt_in_makes_a_turkish_session_detect(spied_languages):
    server, languages = spied_languages
    assert _first_partial_language(server, languages, "tr", auto_on_cpu=True) == "auto"
