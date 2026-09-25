"""Bulut model listesi ARTIK ELLE YAZILMIYOR — sözleşme (30 Ağu 2026).

Eski hâli 40 satırlık bir sözlüktü ve listelediğinden ayrışıyordu: Groq'un tek
modeli 16 Ağu 2026'da kapatılmıştı ve katalog onu hâlâ tek seçenek diye
sunuyordu. Karar (Burak): elle yazılan gitsin, her şey canlı çekilsin.

Yerine geçen tasarımda her kaynak TEK bir soruyu cevaplıyor:

  * sağlayıcının kendi `/v1/models`i (kullanıcının anahtarıyla)
      → "bu hesap neyi çağırabiliyor" — listenin kaynağı
  * OpenRouter'ın AÇIK kataloğu (anahtar gerekmiyor)
      → "bu modelin düzgün adı, bağlam penceresi, fiyatı ne" — yalnız künye

Testlerin koruduğu asıl şey: anahtar yokken sağlayıcının GÖRÜNMEZ OLMAMASI.
Boş bir grup "bu sağlayıcı yok" diye okunur; doğru cümle "hesabında
doğrulanmadı" ve ikisinin kullanıcıya söylediği iş farklı.
"""
import asyncio
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from providers import model_catalog
from routes import config_routes
from routes.config_routes import create_config_router

OR_KATALOG = {
    "anthropic/claude-opus-5": {"name": "Anthropic: Claude Opus 5", "context_length": 1000000,
                                "pricing": {"prompt": "0.000015"}},
    "anthropic/claude-haiku-9": {"name": "Anthropic: Claude Haiku 9", "context_length": 200000,
                                 "pricing": {"prompt": "0"}},
    "openai/gpt-9": {"name": "OpenAI: GPT-9", "context_length": 400000,
                     "pricing": {"prompt": "0.00001"}},
}


def _db(anahtarlar: dict):
    db = MagicMock()
    db.get_api_key.side_effect = lambda _uid, saglayici: anahtarlar.get(saglayici, "")
    return db


def _katalog(anahtarlar: dict, canli: dict, or_katalog=OR_KATALOG, refresh: bool = False):
    """`canli`: saglayici -> ({id: ad} | None)."""
    router = create_config_router(_db(anahtarlar))
    route = next(r for r in router.routes if r.path == "/available-models")

    with patch("providers.model_catalog.list_models",
               side_effect=lambda s, a, force=False: canli.get(s)), \
         patch("providers.model_catalog.openrouter_catalog",
               side_effect=lambda force=False: or_katalog), \
         patch("urllib.request.urlopen", side_effect=OSError("kapalı")):
        return asyncio.run(route.endpoint(refresh=refresh))


def _bul(cloud, mid, saglayici):
    return next((m for m in cloud if m["id"] == mid and m["provider"] == saglayici), None)


def test_a_live_list_becomes_the_cloud_list():
    sonuc = _katalog({"anthropic": "sk-x"}, {"anthropic": {"claude-opus-5": "claude-opus-5"}})
    m = _bul(sonuc["cloud"], "claude-opus-5", "anthropic")
    assert m and m["available"] is True and m["verified"] is True
    assert m["source"] == "live"
    assert sonuc["cloud_sources"]["anthropic"] == "live"


def test_openrouter_supplies_the_display_name_and_context_window():
    # Sağlayıcı yalnız kimlik döndürüyor; düzgün ad ve pencere künyeden gelir.
    sonuc = _katalog({"anthropic": "sk-x"}, {"anthropic": {"claude-opus-5": "claude-opus-5"}})
    m = _bul(sonuc["cloud"], "claude-opus-5", "anthropic")
    assert m["name"] == "Anthropic: Claude Opus 5"
    assert m["context_length"] == 1000000
    assert m["openrouter_id"] == "anthropic/claude-opus-5"
    assert m["paid"] is True


def test_a_model_with_no_openrouter_match_still_appears_just_without_a_badge():
    # Eşleşmeyi zorlamak YANLIŞ bir künyeyi doğru gibi gösterirdi.
    sonuc = _katalog({"anthropic": "sk-x"}, {"anthropic": {"claude-gizli-1": "claude-gizli-1"}})
    m = _bul(sonuc["cloud"], "claude-gizli-1", "anthropic")
    assert m is not None
    assert m["name"] == "claude-gizli-1"
    assert "context_length" not in m


def test_a_provider_without_a_key_is_still_visible_from_the_open_catalogue():
    # Bu testin tamamı bunun için: boş grup "sağlayıcı yok" diye okunur.
    sonuc = _katalog({}, {})
    m = _bul(sonuc["cloud"], "claude-opus-5", "anthropic")
    assert m is not None
    assert m["verified"] is False
    assert m["source"] == "openrouter"
    assert "available" not in m       # bilinmiyor — "erişemiyorsun" DEĞİL
    assert sonuc["cloud_sources"]["anthropic"] == "no_key"


def test_a_provider_whose_live_list_failed_is_unknown_not_keyless():
    sonuc = _katalog({"anthropic": "sk-x"}, {"anthropic": None})
    assert sonuc["cloud_sources"]["anthropic"] == "unknown"
    assert _bul(sonuc["cloud"], "claude-opus-5", "anthropic")["verified"] is False


# Rows as OpenRouter's public catalogue lists them (measured 25 Sep 2026).
OR_ANTHROPIC_OPUS = {
    or_id: {"name": name, "context_length": 1000000, "pricing": {"prompt": "0.000005"}}
    for or_id, name in [
        ("anthropic/claude-opus-5.5", "Anthropic: Claude Opus 5.5"),
        ("anthropic/claude-opus-5.5:batch", "Anthropic: Claude Opus 5.5 (batch)"),
        ("anthropic/claude-opus-5", "Anthropic: Claude Opus 5"),
        ("anthropic/claude-opus-5:batch", "Anthropic: Claude Opus 5 (batch)"),
        ("anthropic/claude-opus-4.8", "Anthropic: Claude Opus 4.8"),
        ("anthropic/claude-opus-4.8:batch", "Anthropic: Claude Opus 4.8 (batch)"),
    ]
}


def test_the_keyless_anthropic_list_offers_every_opus_by_its_api_id():
    sonuc = _katalog({}, {}, or_katalog=OR_ANTHROPIC_OPUS)
    anthropic_ids = sorted(m["id"] for m in sonuc["cloud"] if m["provider"] == "anthropic")

    assert anthropic_ids == ["claude-opus-4-8", "claude-opus-5", "claude-opus-5-5"]
    m = _bul(sonuc["cloud"], "claude-opus-5-5", "anthropic")
    assert m["name"] == "Anthropic: Claude Opus 5.5"
    # The OpenRouter toggle still sends OpenRouter's own id.
    assert m["openrouter_id"] == "anthropic/claude-opus-5.5"


def test_live_anthropic_rows_find_their_openrouter_entry():
    canli = {"claude-opus-5-5": "Claude Opus 5.5", "claude-opus-5": "Claude Opus 5",
             "claude-opus-4-8": "Claude Opus 4.8"}
    sonuc = _katalog({"anthropic": "sk-x"}, {"anthropic": canli}, or_katalog=OR_ANTHROPIC_OPUS)

    for mid, or_id in [("claude-opus-5-5", "anthropic/claude-opus-5.5"),
                       ("claude-opus-5", "anthropic/claude-opus-5"),
                       ("claude-opus-4-8", "anthropic/claude-opus-4.8")]:
        m = _bul(sonuc["cloud"], mid, "anthropic")
        assert m["verified"] is True
        assert m["openrouter_id"] == or_id
        assert m["context_length"] == 1000000


def test_non_chat_models_are_kept_out_of_a_chat_picker():
    sonuc = _katalog({"openai": "sk-x"},
                     {"openai": {"gpt-9": "gpt-9", "text-embedding-4": "text-embedding-4",
                                 "whisper-2": "whisper-2"}})
    assert _bul(sonuc["cloud"], "gpt-9", "openai") is not None
    assert _bul(sonuc["cloud"], "text-embedding-4", "openai") is None
    assert _bul(sonuc["cloud"], "whisper-2", "openai") is None


def test_one_provider_being_down_does_not_hide_another_provider_state():
    sonuc = _katalog({"openai": "sk-x", "anthropic": "sk-y"},
                     {"openai": None, "anthropic": {"claude-opus-5": "claude-opus-5"}})
    assert sonuc["cloud_sources"]["openai"] == "unknown"
    assert sonuc["cloud_sources"]["anthropic"] == "live"
    assert _bul(sonuc["cloud"], "claude-opus-5", "anthropic")["verified"] is True


def test_without_the_open_catalogue_a_keyless_provider_simply_has_no_rows():
    # Ağ yoksa künye de yedek liste de yok. Uydurulmuş bir liste göstermektense
    # boş bırakmak doğru; `cloud_sources` yine neden boş olduğunu söylüyor.
    sonuc = _katalog({}, {}, or_katalog={})
    assert sonuc["cloud"] == []
    assert sonuc["cloud_sources"]["anthropic"] == "no_key"


# ─── Uçtan uca: uzak veri ve yenileme maliyeti ──────────────────────────────
#
# The two tests below drive the REAL catalogue (no `list_models` patch), only
# the socket is faked, because both audit findings live in the path between the
# provider response and the endpoint result.


class _Cevap:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()
        self._sent = False

    def read(self, size=None):
        if self._sent:
            return b""
        self._sent = True
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _canli_uc(anahtarlar: dict, cevap_ver, refresh: bool = False):
    """Gerçek katalogla `/available-models`; yalnız soket sahte.

    Önbelleği KENDİ temizlemiyor: kısma testleri iki çağrı arasında sıcak
    önbelleğe ihtiyaç duyuyor, çünkü ölçülen şey tam olarak "kısılan istek
    önbellekten mi yanıtlandı".
    """
    router = create_config_router(_db(anahtarlar))
    route = next(r for r in router.routes if r.path == "/available-models")
    with patch("routes.config_routes._check_token"), \
         patch("routes.config_routes.get_current_user", return_value=(1, None)), \
         patch("urllib.request.urlopen", side_effect=cevap_ver) as urlopen:
        sonuc = asyncio.run(route.endpoint(refresh=refresh, x_session_token="valid"))
    return sonuc, urlopen.call_count


def test_an_object_valued_remote_name_never_reaches_the_endpoint():
    # OpenRouter controls this response; the picker renders `m.name` as a React
    # child, so an object there is a render-time TypeError, not a bad label.
    def cevap_ver(request, timeout=None):
        if request.full_url.startswith("http://127.0.0.1:11434"):
            return _Cevap({"models": []})
        return _Cevap({"data": [{"id": "openai/remote-object-name",
                                 "name": {"not": "renderable text"},
                                 "context_length": 1, "pricing": {"prompt": "0"}}]})

    model_catalog.clear_cache()
    try:
        sonuc, _ = _canli_uc({}, cevap_ver)
    finally:
        model_catalog.clear_cache()
    satir = _bul(sonuc["cloud"], "remote-object-name", "openai")
    assert satir is not None, "model yine listelenmeli — sadece adı düzeltilmiş olmalı"
    assert isinstance(satir["name"], str)


def _dokuz_saglayici_cevabi(request, timeout=None):
    url = request.full_url
    if url.startswith("http://127.0.0.1:11434"):
        return _Cevap({"models": []})
    if "generativelanguage.googleapis.com" in url:
        return _Cevap({"models": [{"name": "models/chat-model"}]})
    return _Cevap({"data": [{"id": "chat-model"}]})


def test_an_immediate_second_forced_refresh_does_not_repeat_all_eleven_calls():
    # One forced refresh = 11 outbound calls (Ollama + open catalogue + nine
    # keyed providers). Nothing stopped an immediate repeat from costing 11 more.
    anahtarlar = {s: f"key-{s}" for s in model_catalog.supported_providers()}
    model_catalog.clear_cache()
    config_routes._last_forced_refresh = 0.0
    try:
        _, ilk = _canli_uc(anahtarlar, _dokuz_saglayici_cevabi, refresh=True)
        _, ikinci = _canli_uc(anahtarlar, _dokuz_saglayici_cevabi, refresh=True)
    finally:
        model_catalog.clear_cache()
    assert ilk == 11
    assert ikinci < ilk


def test_the_refresh_button_still_works_once_the_interval_has_passed():
    # The guard must not turn "refresh now" into "refresh maybe": a click after
    # the minimum interval has to reach every provider again.
    anahtarlar = {s: f"key-{s}" for s in model_catalog.supported_providers()}
    model_catalog.clear_cache()
    config_routes._last_forced_refresh = 0.0
    try:
        _, ilk = _canli_uc(anahtarlar, _dokuz_saglayici_cevabi, refresh=True)
        config_routes._last_forced_refresh -= config_routes._FORCED_REFRESH_MIN_INTERVAL_SECONDS
        _, ikinci = _canli_uc(anahtarlar, _dokuz_saglayici_cevabi, refresh=True)
    finally:
        model_catalog.clear_cache()
    assert ilk == ikinci == 11


def test_the_subscription_list_is_untouched_by_cloud_liveness():
    sonuc = _katalog({"anthropic": "sk-x"}, {"anthropic": {"claude-opus-5": "claude-opus-5"}})
    assert sonuc["subscription"], "abonelik listesi bu değişiklikten etkilenmemeli"
    assert all("verified" not in m for m in sonuc["subscription"])
