"""`POST /transcribe*` — offline dictation. Audio in, text out; nothing is sent.

The renderer records 16 kHz mono 16-bit PCM and posts it here; the recognised
text is inserted into the chat box and the user presses Enter themselves.

The handlers are plain `def` on purpose: recognition blocks (a second on a GPU,
several on a CPU), so FastAPI runs them in the threadpool instead of stalling
the event loop the way an `async def` body would.
"""
import base64
import binascii
import io
import logging
import re
import wave

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, StrictBool

from auth_utils import _check_token
from providers import stt_whisper

logger = logging.getLogger(__name__)

MAX_WAV_BYTES = 2_097_152                 # 2 MiB decoded ≈ 65 s of 16 kHz mono PCM
# ceil(2 MiB / 3) * 4 — the longest base64 string that can still decode within
# the cap. Checked BEFORE decoding so a hostile 50 MB string is refused without
# ever being materialised in memory.
MAX_B64_CHARS = 2_796_204

# Live dictation chunks. ~2 s of 16 kHz mono PCM per chunk; the base64 bound is
# ceil(65536 / 3) * 4, checked before decoding for the same reason as above.
MAX_CHUNK_BYTES = 65_536
MAX_CHUNK_B64_CHARS = 87_384
MAX_SESSION_BYTES = 2_097_152             # same 2 MiB budget as the one-shot route

# A session id only ever comes from `secrets.token_urlsafe`. Anything outside
# that alphabet cannot name a live session, so it is answered like any other
# unknown id instead of reaching the registry.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# app_settings key. "1" = detect the language on a CPU-only machine too.
AUTO_LANGUAGE_CPU_KEY = "stt_auto_language_cpu"


class TranscribeRequest(BaseModel):
    lang: str
    wav_base64: str


class SessionCreateRequest(BaseModel):
    lang: str


class SessionChunkRequest(BaseModel):
    pcm_base64: str


class SessionFinishRequest(BaseModel):
    # StrictBool, not bool: Pydantic's plain bool coerces 1 and "yes" to True,
    # so a stray non-boolean value silently discarded a dictation instead of
    # being refused (audit finding, 3 Sep 2026).
    discard: StrictBool = False


class DictationSettingsRequest(BaseModel):
    auto_language_cpu: StrictBool


def _engine_error(exc: Exception) -> HTTPException:
    if isinstance(exc, stt_whisper.SttEngineMissing):
        return HTTPException(503, detail="stt_model_missing")
    logger.warning(f"[transcribe] dictation engine failed: {exc}")
    return HTTPException(503, detail="stt_engine_failed")


def create_transcribe_router(db=None):
    router = APIRouter()

    def auto_language_cpu() -> bool:
        if db is None:
            return False
        try:
            return db.get_setting(AUTO_LANGUAGE_CPU_KEY) == "1"
        except Exception as exc:                     # noqa: BLE001
            # A settings read must not cost the user their dictation; the
            # default (UI language) is the fast one.
            logger.warning(f"[transcribe] could not read {AUTO_LANGUAGE_CPU_KEY}: {exc}")
            return False

    @router.post("/transcribe")
    def transcribe(request: TranscribeRequest, x_session_token: str = Header(alias="X-Session-Token")):
        _check_token(x_session_token)

        if request.lang not in stt_whisper.SUPPORTED_LANGS:
            raise HTTPException(400, detail="stt_bad_lang")

        if len(request.wav_base64) > MAX_B64_CHARS:
            raise HTTPException(413, detail="stt_too_large")

        try:
            wav_bytes = base64.b64decode(request.wav_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, detail="stt_bad_base64")

        if len(wav_bytes) > MAX_WAV_BYTES:
            raise HTTPException(413, detail="stt_too_large")

        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
                channels = wav.getnchannels()
                width = wav.getsampwidth()
                rate = wav.getframerate()
                frames = wav.getnframes()
                pcm = wav.readframes(frames)
        except Exception:                            # noqa: BLE001
            # `wave` raises wave.Error, EOFError or struct.error depending on
            # where the bytes stop being a RIFF file; all three mean the same
            # thing to the caller.
            raise HTTPException(400, detail="stt_not_wav")

        if channels != 1 or width != 2 or rate != stt_whisper.SAMPLE_RATE:
            raise HTTPException(400, detail="stt_wrong_format")

        if frames <= 0:
            raise HTTPException(400, detail="stt_empty_audio")

        # `wave` trusts the declared data length; `readframes` returns what is
        # actually there. A truncated file otherwise reaches recognition as
        # complete audio (audit probe, 3 Sep 2026: 32,000 declared, 2 present, 200).
        if len(pcm) != frames * channels * width:
            raise HTTPException(400, detail="stt_not_wav")

        try:
            result = stt_whisper.transcribe_final(pcm, request.lang, auto_language_cpu())
        except (stt_whisper.SttEngineMissing, stt_whisper.SttEngineFailed) as exc:
            raise _engine_error(exc)

        return {
            "text": result["text"],
            "lang": request.lang,
            "duration_ms": len(pcm) // 2 * 1000 // stt_whisper.SAMPLE_RATE,
            "language": result["language"],
            "language_mode": result["language_mode"],
        }

    @router.post("/transcribe/session")
    def open_session(request: SessionCreateRequest, x_session_token: str = Header(alias="X-Session-Token")):
        _check_token(x_session_token)
        stt_whisper.purge_expired()

        if request.lang not in stt_whisper.SUPPORTED_LANGS:
            raise HTTPException(400, detail="stt_bad_lang")

        try:
            session_id = stt_whisper.open_session(request.lang)
        except stt_whisper.SttBusy:
            raise HTTPException(503, detail="stt_busy")
        except (stt_whisper.SttEngineMissing, stt_whisper.SttEngineFailed) as exc:
            raise _engine_error(exc)

        return {"session_id": session_id, "lang": request.lang}

    @router.post("/transcribe/session/{session_id}")
    def feed_session(
        session_id: str,
        request: SessionChunkRequest,
        x_session_token: str = Header(alias="X-Session-Token"),
    ):
        # Deliberately silent: this runs twice a second while the user speaks,
        # and a log line per chunk would flood the console the desktop app tails.
        _check_token(x_session_token)
        stt_whisper.purge_expired()

        if not _SESSION_ID_RE.fullmatch(session_id):
            raise HTTPException(404, detail="stt_no_session")

        if len(request.pcm_base64) > MAX_CHUNK_B64_CHARS:
            raise HTTPException(413, detail="stt_too_large")

        try:
            pcm = base64.b64decode(request.pcm_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, detail="stt_bad_base64")

        if len(pcm) > MAX_CHUNK_BYTES:
            raise HTTPException(413, detail="stt_too_large")

        if len(pcm) % 2:
            # Half a sample: the caller sliced its Int16 buffer wrong, and every
            # following byte would be read shifted by one.
            raise HTTPException(400, detail="stt_wrong_format")

        try:
            partial = stt_whisper.feed(session_id, pcm, cap=MAX_SESSION_BYTES)
            total = stt_whisper.session_bytes(session_id)
        except stt_whisper.SttTooLarge:
            raise HTTPException(413, detail="stt_too_large")
        except stt_whisper.SttNoSession:
            raise HTTPException(404, detail="stt_no_session")

        return {"partial": partial, "bytes": total}

    @router.post("/transcribe/session/{session_id}/finish")
    def finish_session(
        session_id: str,
        request: SessionFinishRequest,
        x_session_token: str = Header(alias="X-Session-Token"),
    ):
        _check_token(x_session_token)
        stt_whisper.purge_expired()

        if not _SESSION_ID_RE.fullmatch(session_id):
            raise HTTPException(404, detail="stt_no_session")

        try:
            result = stt_whisper.finish(session_id, auto_on_cpu=auto_language_cpu(), discard=request.discard)
        except stt_whisper.SttNoSession:
            raise HTTPException(404, detail="stt_no_session")
        except (stt_whisper.SttEngineMissing, stt_whisper.SttEngineFailed) as exc:
            raise _engine_error(exc)

        return result

    @router.get("/transcribe/settings")
    def get_settings(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        return {"auto_language_cpu": auto_language_cpu()}

    @router.post("/transcribe/settings")
    def set_settings(request: DictationSettingsRequest,
                     x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        if db is None:
            raise HTTPException(503, detail="settings_unavailable")
        db.set_setting(AUTO_LANGUAGE_CPU_KEY, "1" if request.auto_language_cpu else "0")
        return {"auto_language_cpu": request.auto_language_cpu}

    return router
