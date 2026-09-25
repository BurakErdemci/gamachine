"""Bulut sağlayıcılardan CANLI model listesi.

Neden var: `config_routes.get_available_models`'daki bulut kataloğu elle
yazılmış. Elle tutulan liste listelediğinden ayrışıyor ve kaymış bir liste hiç
liste olmamasından kötü, çünkü güncel sanılıp okunuyor — bu depoda aynı kalıp
CLI önek tablolarında iki kez ölçüldü.

Neden elle yazılı katalog SİLİNMİYOR: canlı uç yalnız kimlik döndürüyor.
Görünen ad, OpenRouter karşılığı ve "ücretli" işareti küratörlü bilgi ve hiçbir
`/v1/models` cevabında yok. Bu yüzden tasarım "değiştir" değil BİRLEŞTİR:
katalog künye, canlı liste ise "bu hesap gerçekten neye erişiyor" sorusunun
cevabı.

Belirsizlik sessizce doldurulmuyor: çağrı başarısızsa `None` döner ve çağıran
"bilmiyoruz" durumunu ayrı bir hâl olarak taşır. Boş küme döndürmek "hesabın
hiçbir modeli yok" demek olurdu ve katalogdaki her satırı yanlışlıkla
erişilemez göstermeye yeterdi.

Bağımlılık eklenmedi: `urllib.request`, `config_routes`'un Ollama çağrısıyla
aynı desen.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
import urllib.request
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Sağlayıcı → (uç, kimlik biçimi, cevap şekli).
#
# Anahtar HER ZAMAN bu sözlükteki sağlayıcı adıyla çekiliyor ve aynı adın
# ucuna gidiyor, yani bir sağlayıcının anahtarı başka bir sağlayıcının ucuna
# yapısal olarak gidemiyor. `test_model_catalog` bunu sabitliyor.
_ENDPOINTS: Dict[str, tuple[str, str, str]] = {
    "openai":     ("https://api.openai.com/v1/models",              "bearer",     "openai"),
    "deepseek":   ("https://api.deepseek.com/v1/models",            "bearer",     "openai"),
    "groq":       ("https://api.groq.com/openai/v1/models",         "bearer",     "openai"),
    "openrouter": ("https://openrouter.ai/api/v1/models",           "bearer",     "openai"),
    "moonshot":   ("https://api.moonshot.ai/v1/models",             "bearer",     "openai"),
    "z-ai":       ("https://api.z.ai/api/paas/v4/models",           "bearer",     "openai"),
    "nvidia":     ("https://integrate.api.nvidia.com/v1/models",    "bearer",     "openai"),
    "anthropic":  ("https://api.anthropic.com/v1/models?limit=100", "x-api-key",  "anthropic"),
    "google":     ("https://generativelanguage.googleapis.com/v1beta/models", "google", "google"),
}

_TTL_SECONDS = 600.0
_TIMEOUT_SECONDS = 6.0

# Read caps. The socket timeout only bounds INACTIVITY: a peer that drips a few
# bytes every second keeps it alive forever, so a byte cap and an overall
# deadline are both needed and neither replaces the other.
#
# 8 MiB: the largest observed catalogue (OpenRouter, 396 models, 30 Aug 2026) is
# ~0.5 MB, so this is ~16x headroom. Cost of the number: a provider that grows
# past 8 MiB is reported as "unknown" instead of listed, which is the same
# failure mode as an unreachable endpoint and is visible in `cloud_sources`.
_MAX_BODY_BYTES = 8 * 1024 * 1024
# 64 KiB per read: one syscall per chunk, so the deadline is re-checked at least
# every chunk. Smaller would check more often but costs more syscalls per MB.
_READ_CHUNK_BYTES = 64 * 1024
# Overall deadline = 3x the socket timeout. A healthy body arrives well inside
# one timeout; 3x leaves room for a slow but honest peer (connect + TLS + body)
# while capping the worst case at 18 s for a keyed lookup instead of unbounded.
_DEADLINE_FACTOR = 3.0

# Per-process random salt for the cache key. It is generated at import, never
# persisted and never logged, so the stored digest is not an offline oracle for
# the key: without the salt a guessed key cannot be confirmed against it, and
# the salt dies with the process. The digest is only ever compared to another
# digest computed in the same process.
_CACHE_SALT = secrets.token_bytes(16)


def _credential_fingerprint(api_key: str) -> str:
    """Stable-within-process handle for a credential; not reversible to it."""
    return hashlib.blake2b(api_key.encode("utf-8", "replace"),
                           key=_CACHE_SALT, digest_size=16).hexdigest()


# (provider, kimlik parmak izi) → (zaman damgası, sonuç).
# Sonuç `None` ise "o turda ulaşılamadı".
#
# Parmak izi anahtarın PARÇASI: yalnız sağlayıcı adıyla anahtarlanırken,
# kullanıcı anahtarını değiştirdikten sonra 600 sn boyunca ESKİ anahtarın
# listesi (ya da eski anahtarın başarısızlığı) dönüyordu — düzeltilen anahtar
# `unknown` kalıyordu.
_cache: Dict[tuple[str, str], tuple[float, Optional[Dict[str, str]]]] = {}


def _read_bounded(response, deadline: float) -> Optional[bytes]:
    """Read the body under a byte cap and an absolute deadline, else `None`.

    Crossing either bound is a FAILED lookup, not a partial parse: half a
    catalogue parsed as a whole one would silently mark real models missing.
    """
    chunks: list[bytes] = []
    total = 0
    # Some response objects expose `read()` without a size argument (older
    # wrappers, test doubles). They cannot be chunked, so they get a single
    # read that is still measured against the same cap below.
    sized = True
    while True:
        if time.monotonic() > deadline:
            return None
        if sized:
            try:
                chunk = response.read(_READ_CHUNK_BYTES)
            except TypeError:
                sized = False
                continue
        else:
            chunk = response.read()
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
        if not sized:
            break
    if time.monotonic() > deadline:
        return None
    return b"".join(chunks)


def _coerce_name(value, fallback: str) -> str:
    """Remote display names are only accepted as strings.

    A provider can put any JSON value in `name`; an object reaching the picker
    makes React throw at render time instead of showing a model. Rejecting the
    non-string here means the model still appears, under its own id.
    """
    return value if isinstance(value, str) and value else fallback


# Bidi overrides/isolates and the C0/C1 control ranges. A model ID is a wire
# identifier — it is sent to the provider, stored in the config and shown as a
# label — and none of these characters has a legitimate place in one.
_ID_CONTROL_RE = re.compile("[\u0000-\u001f\u007f-\u009f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def usable_model_id(mid: object) -> bool:
    """Whether a remote model ID may be accepted at all.

    Names are sanitised where they are DISPLAYED, because the name is only ever
    a label. An ID cannot be handled that way: it is both displayed and acted
    on, so stripping it for display would show one string while sending
    another, and stripping it everywhere would send the provider an ID it never
    published. The only honest option left is to refuse the row.

    Found by the verification round after the display-side fix: the picker's
    labels were sanitised, but the SAVED id reached an editable settings field
    and the chat's model label untouched — the same class, one step further
    along. Refusing it here closes every consumer at once instead of chasing
    render sites.
    """
    return isinstance(mid, str) and bool(mid) and not _ID_CONTROL_RE.search(mid)


def supported_providers() -> tuple[str, ...]:
    """Canlı listeleme ucu BİLİNEN sağlayıcılar.

    Burada olmayan bir sağlayıcı için "listeleme yolu yok" demek doğru; bir uç
    denenip başarısız olmasıyla karıştırılmamalı.
    """
    return tuple(_ENDPOINTS)


def _headers(kind: str, api_key: str) -> Dict[str, str]:
    if kind == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    if kind == "x-api-key":
        # Anthropic sürüm başlığı olmadan 400 döndürüyor.
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    if kind == "google":
        return {"x-goog-api-key": api_key}
    raise ValueError(f"bilinmeyen kimlik biçimi: {kind}")


def _parse(shape: str, payload: dict) -> Dict[str, str]:
    """Cevabı `{model_id: gorunen_ad}` sözlüğüne indir."""
    out: Dict[str, str] = {}
    if shape in ("openai", "anthropic"):
        for item in payload.get("data") or []:
            mid = (item or {}).get("id")
            if usable_model_id(mid):
                out[mid] = _coerce_name(item.get("display_name"),
                                        _coerce_name(item.get("name"), mid))
    elif shape == "google":
        for item in payload.get("models") or []:
            raw = (item or {}).get("name")
            if not isinstance(raw, str):
                continue
            # "models/gemini-3.6-flash" → "gemini-3.6-flash"
            mid = raw.split("/", 1)[1] if "/" in raw else raw
            if usable_model_id(mid):
                out[mid] = _coerce_name(item.get("displayName"), mid)
    return out


def _fetch(provider: str, api_key: str) -> Optional[Dict[str, str]]:
    url, auth_kind, shape = _ENDPOINTS[provider]
    # Deadline starts BEFORE the connection: it bounds the whole operation,
    # which is exactly what the socket timeout does not do.
    deadline = time.monotonic() + _TIMEOUT_SECONDS * _DEADLINE_FACTOR
    try:
        request = urllib.request.Request(url, headers=_headers(auth_kind, api_key))
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            body = _read_bounded(response, deadline)
        if body is None:
            logger.warning("Model listesi sınırı aştı (%s): gövde çok büyük ya da çok yavaş", provider)
            return None
        payload = json.loads(body)
        parsed = _parse(shape, payload)
        # Şema değişmiş ya da beklenmedik bir gövde gelmişse boş sözlük çıkar.
        # Onu "hesapta model yok" diye taşımak katalogdaki her satırı
        # erişilemez gösterirdi; bilinmezlik olarak bildiriliyor.
        return parsed or None
    except Exception as exc:
        # Anahtar ASLA loglanmıyor: istisna metni URL taşıyabilir, başlık taşımaz.
        logger.warning("Model listesi alınamadı (%s): %s", provider, exc)
        return None


def list_models(provider: str, api_key: str, force: bool = False) -> Optional[Dict[str, str]]:
    """Sağlayıcının hesaba açık modelleri, ya da bilinemiyorsa `None`.

    `force` önbelleği atlar — kullanıcı "yenile" dediğinde bayat cevap vermek
    tam olarak yenilemenin var olma sebebini boşa çıkarır.
    """
    # `isinstance` şart, "truthy" yetmiyor: testlerde sahte bir veritabanı
    # anahtar yerine bir mock döndürüyor ve mock her zaman truthy. Onsuz test
    # takımı sessizce GERÇEK ağ çağrısı yapardı — hem yavaş hem kırılgan, hem
    # de dışarıya istek atan bir birim testi.
    if provider not in _ENDPOINTS or not isinstance(api_key, str) or not api_key:
        return None
    now = time.monotonic()
    # Önbellek girdisi onu ÜRETEN kimliğe bağlı; anahtar değişince eski cevap
    # (ya da eski başarısızlık) yeniden kullanılamıyor.
    cache_key = (provider, _credential_fingerprint(api_key))
    if not force:
        cached = _cache.get(cache_key)
        if cached and (now - cached[0]) < _TTL_SECONDS:
            return cached[1]
    result = _fetch(provider, api_key)
    _cache[cache_key] = (now, result)
    return result


# ── OpenRouter açık kataloğu ────────────────────────────────────────────────
#
# Neden burada: bir modelin KÜNYESİ (düzgün ad, bağlam penceresi, fiyat,
# OpenRouter karşılığı) sağlayıcıların kendi `/v1/models` cevaplarında YOK —
# OpenAI yalnız kimlik döndürüyor. Bu bilgi 30 Ağu 2026'ya kadar elle
# yazılıyordu ve elle tutulan liste listelediğinden ayrışıyordu.
#
# ÖLÇÜLDÜ 30 Ağu 2026: `https://openrouter.ai/api/v1/models` **anahtarsız**
# çalışıyor (HTTP 200, 396 model) ve her kayıt `name`, `context_length`,
# `pricing`, `expiration_date` taşıyor. Yani künye için ücretsiz, canlı ve
# sağlayıcı-üstü bir kaynak var.
#
# Sınırı da ölçüldü: 14 kimliğin 12'si `<ad-alanı>/<kimlik>` biçiminde birebir
# tutuyor, 2'si tutmuyor (`anthropic/claude-haiku-4-5` OR'da yok; Groq'un
# modeli OR'da `meta-llama/` altında). Bu yüzden katalog LİSTENİN KAYNAĞI
# DEĞİL, yalnız künye zenginleştiricisi: eşleşme bulunamazsa model yine
# görünür, sadece künyesiz.
_OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
_OR_TTL_SECONDS = 3600.0
_or_cache: tuple[float, Optional[Dict[str, dict]]] | None = None


def openrouter_catalog(force: bool = False) -> Optional[Dict[str, dict]]:
    """`{openrouter_id: {name, context_length, pricing}}`, ya da ulaşılamazsa None."""
    global _or_cache
    now = time.monotonic()
    if not force and _or_cache and (now - _or_cache[0]) < _OR_TTL_SECONDS:
        return _or_cache[1]
    sonuc: Optional[Dict[str, dict]] = None
    socket_timeout = _TIMEOUT_SECONDS * 2
    deadline = time.monotonic() + socket_timeout * _DEADLINE_FACTOR
    try:
        request = urllib.request.Request(_OPENROUTER_URL)
        with urllib.request.urlopen(request, timeout=socket_timeout) as response:
            body = _read_bounded(response, deadline)
        if body is None:
            logger.warning("OpenRouter kataloğu sınırı aştı: gövde çok büyük ya da çok yavaş")
            _or_cache = (now, None)
            return None
        payload = json.loads(body)
        kayitlar = {}
        for m in payload.get("data") or []:
            mid = (m or {}).get("id")
            if not usable_model_id(mid):
                continue
            # Every field below is remote-controlled and crosses `/available-models`
            # into the React picker, so each is typed at this boundary: a wrong
            # type is dropped, never forwarded.
            context_length = m.get("context_length")
            pricing = m.get("pricing")
            expiration = m.get("expiration_date")
            kayitlar[mid] = {
                "name": _coerce_name(m.get("name"), mid),
                # `bool` is an `int` in Python; `True` is not a context window.
                "context_length": (context_length
                                   if isinstance(context_length, int) and not isinstance(context_length, bool)
                                   else None),
                "pricing": pricing if isinstance(pricing, dict) else None,
                "expiration_date": expiration if isinstance(expiration, str) else None,
            }
        sonuc = kayitlar or None
    except Exception as exc:
        logger.warning("OpenRouter kataloğu alınamadı: %s", exc)
    _or_cache = (now, sonuc)
    return sonuc


# Sağlayıcı → OpenRouter ad alanı. Bu tablo BİLEREK elle yazılı ve elle yazılı
# model listesinden farklı: 9 satır, ve sağlayıcı adları model adları gibi her
# ay değişmiyor. `None` = ad alanı eşlemesi yok; o sağlayıcının kimlikleri
# zaten `vendor/model` biçiminde geliyor (NVIDIA NIM, Groq'un gpt-oss'ları).
_OR_NAMESPACE: Dict[str, Optional[str]] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "deepseek": "deepseek",
    "z-ai": "z-ai",
    "moonshot": "moonshotai",
    "groq": None,
    "nvidia": None,
    "openrouter": "",
}

# Sohbet DIŞI modelleri eleyen desenler. ⚠️ Bu bir SEZGİ, ölçüm değil:
# sağlayıcıların ham `/v1/models` cevabı gömme, ses, görsel ve moderasyon
# modellerini de döndürüyor ve bunların sohbet seçicisinde işi yok. Yanlış
# elemek yanlış göstermekten daha az zararlı değil, o yüzden desenler dar
# tutuldu — şüphede kalan model LİSTEDE KALIR.
_SOHBET_DISI = (
    "embedding", "embed-", "tts", "whisper", "transcribe", "moderation",
    "dall-e", "image-", "-image", "audio", "realtime", "rerank", "guard",
)


def is_chat_model(model_id: str) -> bool:
    m = model_id.lower()
    return not any(p in m for p in _SOHBET_DISI)


# OpenRouter writes an Anthropic minor version with a dot where the Messages
# API id has a dash (anthropic/claude-opus-5.5 vs claude-opus-5-5, measured
# 25 Sep 2026). The trailing (?!\d) keeps a date suffix from being read as a
# minor: claude-opus-4-20250514 has none.
_ANTHROPIC_FAMILY_MAJOR = r"^(claude-(?:opus|sonnet|haiku|fable|mythos)-\d+)"
_ANTHROPIC_DASH_MINOR_RE = re.compile(_ANTHROPIC_FAMILY_MAJOR + r"-(\d)(?!\d)")
_ANTHROPIC_DOT_MINOR_RE = re.compile(_ANTHROPIC_FAMILY_MAJOR + r"\.(\d)(?!\d)")


def native_id_from_openrouter(provider: str, local_id: str) -> Optional[str]:
    """The provider's own id for an OpenRouter row, or None when it has none.

    Rows from OpenRouter's catalogue are offered when the provider's live list
    is unavailable, and the id picked there is sent to the provider's API as
    is. For Anthropic that id must be the dashed one, and a routing variant
    (`:batch`) exists only on OpenRouter.
    """
    if provider != "anthropic":
        return local_id
    if ":" in local_id:
        return None
    return _ANTHROPIC_DOT_MINOR_RE.sub(r"\1-\2", local_id)


def openrouter_id_for(provider: str, model_id: str) -> Optional[str]:
    """Yerel kimlikten OpenRouter kimliğini türet.

    Ölçüldü 30 Ağu 2026: 14 kimliğin 12'si bu kuralla tutuyor. Tutmayanlar
    künyesiz kalıyor — model yine listede görünüyor, sadece bağlam penceresi
    ve düzgün adı olmuyor. Eşleşmeyi zorlamak, YANLIŞ bir künyeyi doğru gibi
    göstermek olurdu.
    """
    if "/" in model_id:
        return model_id
    ns = _OR_NAMESPACE.get(provider)
    if ns is None:
        return None
    if provider == "anthropic":
        model_id = _ANTHROPIC_DASH_MINOR_RE.sub(r"\1.\2", model_id)
    return f"{ns}/{model_id}" if ns else model_id


def clear_cache() -> None:
    """Testler ve `force` yolu için; süreç ömrü boyunca başka çağıranı yok."""
    global _or_cache
    _cache.clear()
    _or_cache = None
