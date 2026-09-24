"""Canlı model listesi — ve BİLİNMEZLİĞİN ayrı bir hâl olarak korunması.

Bu akışın merkez tasarım kararı şu: bir sağlayıcıdan liste alınamadığında
sonuç boş küme DEĞİL `None`. Boş küme "hesabında hiç model yok" demek olurdu
ve ağı olmayan bir makinede katalogdaki her satırı "erişemiyorsun" diye
gösterirdi — yanlış kırmızının en pahalı biçimi, çünkü kullanıcı çalışan bir
modeli denemekten vazgeçer.

Bir de ölçülmüş bir tuzak sabitleniyor (30 Ağu 2026): testlerdeki sahte
veritabanı anahtar yerine mock döndürüyor ve mock truthy. `isinstance` kapısı
konmadan önce `test_anthropic_models.py` sessizce GERÇEK ağ çağrıları yapıyordu
— ölçüldü, dosyanın süresi 6,14 sn'den 3,34 sn'ye düştü.
"""
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from providers import model_catalog


@pytest.fixture(autouse=True)
def temiz_onbellek():
    model_catalog.clear_cache()
    yield
    model_catalog.clear_cache()


class _Cevap:
    """`urlopen` bağlam yöneticisinin ihtiyaç duyulan kadarı."""

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ─── Ağa hiç çıkmaması gereken durumlar ────────────────────────────────────


def test_unknown_provider_never_reaches_the_network():
    with patch("urllib.request.urlopen") as urlopen:
        assert model_catalog.list_models("bilinmeyen", "sk-test") is None
    urlopen.assert_not_called()


def test_a_mock_shaped_key_is_not_a_key():
    # Kapının var olma sebebi: mock truthy, ve onsuz birim testleri dışarı
    # istek atıyordu.
    with patch("urllib.request.urlopen") as urlopen:
        assert model_catalog.list_models("openai", MagicMock()) is None
    urlopen.assert_not_called()


def test_empty_key_never_reaches_the_network():
    with patch("urllib.request.urlopen") as urlopen:
        assert model_catalog.list_models("openai", "") is None
    urlopen.assert_not_called()


# ─── Cevap şekilleri ────────────────────────────────────────────────────────


def test_openai_shape_is_read_as_id_to_name():
    payload = {"data": [{"id": "gpt-5.5"}, {"id": "gpt-5.4-mini"}]}
    with patch("urllib.request.urlopen", return_value=_Cevap(payload)):
        assert model_catalog.list_models("openai", "sk-x") == {
            "gpt-5.5": "gpt-5.5", "gpt-5.4-mini": "gpt-5.4-mini",
        }


def test_anthropic_display_name_is_preferred_when_present():
    payload = {"data": [{"id": "claude-opus-5", "display_name": "Claude Opus 5"}]}
    with patch("urllib.request.urlopen", return_value=_Cevap(payload)):
        assert model_catalog.list_models("anthropic", "sk-x") == {"claude-opus-5": "Claude Opus 5"}


def test_google_ids_lose_their_models_prefix():
    payload = {"models": [{"name": "models/gemini-3.6-flash", "displayName": "Gemini 3.6 Flash"}]}
    with patch("urllib.request.urlopen", return_value=_Cevap(payload)):
        assert model_catalog.list_models("google", "k") == {"gemini-3.6-flash": "Gemini 3.6 Flash"}


# ─── Bilinmezlik ────────────────────────────────────────────────────────────


def test_an_unreadable_body_is_unknown_rather_than_empty():
    # Şema değişirse ayrıştırma boş çıkar. Onu "model yok" diye taşımak
    # katalogdaki her satırı erişilemez gösterirdi.
    with patch("urllib.request.urlopen", return_value=_Cevap({"beklenmedik": 1})):
        assert model_catalog.list_models("openai", "sk-x") is None


def test_a_failing_call_is_unknown_and_does_not_raise():
    with patch("urllib.request.urlopen", side_effect=OSError("ağ yok")):
        assert model_catalog.list_models("openai", "sk-x") is None


def test_the_api_key_is_never_written_to_the_log(caplog):
    with patch("urllib.request.urlopen", side_effect=OSError("bağlanılamadı")):
        model_catalog.list_models("openai", "sk-COK-GIZLI-ANAHTAR")
    assert "sk-COK-GIZLI-ANAHTAR" not in caplog.text


# ─── Anahtarın doğru uca gitmesi ────────────────────────────────────────────


def test_each_key_only_ever_reaches_its_own_provider_endpoint():
    """Bir sağlayıcının anahtarı başka bir sağlayıcının ucuna gidemez.

    Yapısal olarak imkânsız (aynı ad hem anahtarı hem ucu seçiyor) ama bu
    depoda sır sızıntısı ölçülmüş bir arıza sınıfı, o yüzden yapıya değil
    DAVRANIŞA bakan bir kapı bırakılıyor.
    """
    for saglayici in model_catalog.supported_providers():
        model_catalog.clear_cache()
        with patch("urllib.request.urlopen", return_value=_Cevap({"data": [{"id": "m"}]})) as urlopen:
            model_catalog.list_models(saglayici, f"anahtar-{saglayici}")
        istek = urlopen.call_args[0][0]
        gonderilen = json.dumps(dict(istek.headers))
        assert f"anahtar-{saglayici}" in gonderilen
        for digeri in model_catalog.supported_providers():
            if digeri != saglayici:
                assert f"anahtar-{digeri}" not in gonderilen


# ─── Önbellek ───────────────────────────────────────────────────────────────


def test_a_second_call_inside_the_ttl_does_not_hit_the_network_again():
    with patch("urllib.request.urlopen", return_value=_Cevap({"data": [{"id": "m"}]})) as urlopen:
        model_catalog.list_models("openai", "sk-x")
        model_catalog.list_models("openai", "sk-x")
    assert urlopen.call_count == 1


def test_force_bypasses_the_cache_or_refresh_means_nothing():
    with patch("urllib.request.urlopen", return_value=_Cevap({"data": [{"id": "m"}]})) as urlopen:
        model_catalog.list_models("openai", "sk-x")
        model_catalog.list_models("openai", "sk-x", force=True)
    assert urlopen.call_count == 2


def test_a_failed_lookup_is_cached_too_so_a_dead_endpoint_is_not_retried_per_request():
    with patch("urllib.request.urlopen", side_effect=OSError("yok")) as urlopen:
        assert model_catalog.list_models("openai", "sk-x") is None
        assert model_catalog.list_models("openai", "sk-x") is None
    assert urlopen.call_count == 1


# ─── Önbellek, kimliğe bağlı ────────────────────────────────────────────────
#
# Audit finding "credential-insensitive-cache" (30 Aug 2026): the entry was
# keyed by provider NAME only, so replacing a credential kept serving the old
# credential's answer for the rest of the 600 s TTL.


def test_a_changed_credential_does_not_receive_the_previous_keys_cached_list():
    responses = [_Cevap({"data": [{"id": "account-a-private-model"}]}),
                 _Cevap({"data": [{"id": "account-b-model"}]})]
    with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
        first = model_catalog.list_models("openai", "credential-a")
        second = model_catalog.list_models("openai", "credential-b")
    assert urlopen.call_count == 2
    assert "account-a-private-model" in first
    assert "account-a-private-model" not in second
    assert "account-b-model" in second


def test_a_corrected_credential_is_not_left_unknown_by_the_bad_keys_cached_failure():
    # The same mechanism retained FAILURES: after fixing a bad key the provider
    # stayed `unknown` until the TTL expired.
    with patch("urllib.request.urlopen",
               side_effect=[OSError("401"), _Cevap({"data": [{"id": "m"}]})]):
        assert model_catalog.list_models("openai", "bad-key") is None
        assert model_catalog.list_models("openai", "good-key") == {"m": "m"}


def test_the_same_credential_still_hits_the_cache():
    # The binding must not defeat the cache it is protecting.
    with patch("urllib.request.urlopen", return_value=_Cevap({"data": [{"id": "m"}]})) as urlopen:
        model_catalog.list_models("openai", "sk-x")
        model_catalog.list_models("openai", "sk-x")
    assert urlopen.call_count == 1


def test_the_cache_never_stores_the_key_itself():
    # The binding is a salted digest; nothing in the cache may echo the key.
    with patch("urllib.request.urlopen", return_value=_Cevap({"data": [{"id": "m"}]})):
        model_catalog.list_models("openai", "sk-COK-GIZLI-ANAHTAR")
    assert "sk-COK-GIZLI-ANAHTAR" not in repr(model_catalog._cache)


# ─── Gövde sınırları ────────────────────────────────────────────────────────
#
# Audit finding "unbounded-response-consumption": both paths called
# `response.read()` with no cap. The socket timeout bounds INACTIVITY only, so
# a slow-drip peer could hold a worker for as long as it liked.


class _OlcenCevap:
    """`read` çağrılarının boyutunu kaydeden cevap."""

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()
        self._sent = False
        self.read_sizes = []

    def read(self, size=None):
        self.read_sizes.append(size)
        if self._sent:
            return b""
        self._sent = True
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _SonsuzCevap:
    """Bitmeyen gövde: her okuma yeni bayt döndürüyor, EOF hiç gelmiyor.

    Parça BİLEREK geçerli JSON: sınır kaldırılırsa tek bir `read()` başarıyla
    ayrıştırılır ve test yeşile döner. Yani testin yeşili sınırın varlığını
    ölçüyor, ayrıştırma hatasını değil.
    """

    GECERLI = b'{"data": [{"id": "m"}]}'

    def __init__(self, chunk: bytes = GECERLI, gecikme: float = 0.0):
        self._chunk = chunk
        self._gecikme = gecikme
        self.reads = 0

    def read(self, size=None):
        self.reads += 1
        if self._gecikme:
            time.sleep(self._gecikme)
        return self._chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_keyed_body_is_read_in_sized_chunks_not_to_eof():
    cevap = _OlcenCevap({"data": [{"id": "m"}]})
    with patch("urllib.request.urlopen", return_value=cevap):
        model_catalog.list_models("openai", "sk-x")
    assert cevap.read_sizes and None not in cevap.read_sizes


def test_the_anonymous_catalogue_body_is_read_in_sized_chunks_too():
    cevap = _OlcenCevap({"data": [{"id": "openai/gpt-9", "name": "GPT-9"}]})
    with patch("urllib.request.urlopen", return_value=cevap):
        model_catalog.openrouter_catalog(force=True)
    assert cevap.read_sizes and None not in cevap.read_sizes


def test_a_body_past_the_byte_cap_is_a_failed_lookup_not_a_partial_parse():
    cevap = _SonsuzCevap()
    # The real cap is 8 MiB; shrinking it keeps the test fast without changing
    # the behaviour under test.
    with patch.object(model_catalog, "_MAX_BODY_BYTES", 4096), \
         patch("urllib.request.urlopen", return_value=cevap):
        assert model_catalog.list_models("openai", "sk-x") is None
    assert cevap.reads < 4096, "okuma sınırda durmalı, gövdeyi sonuna kadar çekmemeli"


def test_a_slow_drip_body_is_abandoned_at_the_deadline():
    # Bytes keep arriving, so no socket inactivity timeout ever fires; only an
    # overall deadline can end this.
    cevap = _SonsuzCevap(gecikme=0.02)
    basladi = time.monotonic()
    with patch.object(model_catalog, "_TIMEOUT_SECONDS", 0.01), \
         patch("urllib.request.urlopen", return_value=cevap):
        assert model_catalog.list_models("openai", "sk-x", force=True) is None
    assert time.monotonic() - basladi < 5.0, "süresiz beklememeli"


def test_the_anonymous_catalogue_is_abandoned_at_its_deadline_too():
    cevap = _SonsuzCevap(b'{"data": [{"id": "openai/gpt-9"}]}', gecikme=0.02)
    with patch.object(model_catalog, "_TIMEOUT_SECONDS", 0.005), \
         patch("urllib.request.urlopen", return_value=cevap):
        assert model_catalog.openrouter_catalog(force=True) is None


# ─── Uzak alanların TİPİ ────────────────────────────────────────────────────
#
# Audit finding "unvalidated-remote-data": `name` was copied without a type
# check, so an object reached `/available-models` and the React picker threw
# while rendering it.


def test_an_object_valued_name_is_rejected_at_the_anonymous_parse_boundary():
    payload = {"data": [{"id": "openai/gpt-9", "name": {"not": "renderable text"}}]}
    with patch("urllib.request.urlopen", return_value=_Cevap(payload)):
        katalog = model_catalog.openrouter_catalog(force=True)
    assert katalog["openai/gpt-9"]["name"] == "openai/gpt-9"


def test_wrongly_typed_metadata_is_dropped_rather_than_forwarded():
    payload = {"data": [{"id": "openai/gpt-9", "name": "GPT-9",
                         "context_length": "400000", "pricing": "free",
                         "expiration_date": {"soon": True}}]}
    with patch("urllib.request.urlopen", return_value=_Cevap(payload)):
        kayit = model_catalog.openrouter_catalog(force=True)["openai/gpt-9"]
    assert kayit["context_length"] is None
    assert kayit["pricing"] is None
    assert kayit["expiration_date"] is None


def test_a_provider_display_name_that_is_not_a_string_falls_back_to_the_id():
    payload = {"data": [{"id": "gpt-9", "display_name": ["nope"]}]}
    with patch("urllib.request.urlopen", return_value=_Cevap(payload)):
        assert model_catalog.list_models("openai", "sk-x") == {"gpt-9": "gpt-9"}


def test_a_google_model_whose_name_is_not_a_string_is_skipped():
    payload = {"models": [{"name": {"models": "gemini"}}, {"name": "models/gemini-3.6-flash"}]}
    with patch("urllib.request.urlopen", return_value=_Cevap(payload)):
        assert model_catalog.list_models("google", "k") == {"gemini-3.6-flash": "gemini-3.6-flash"}


def test_new_model_ids_still_derive_an_openrouter_identity():
    """Yeni kimlikler künye birleştirmesi için ayrı bir takma-ad tablosu istemiyor.

    Bulut listesi tamamen canlı; katalogda elle yazılı satır yok. Tek koşul
    `openrouter_id_for`in kimliği türetebilmesi — türetemezse model listede
    kalır ama bağlam penceresi/fiyatı olmaz.
    """
    assert model_catalog.openrouter_id_for("google", "gemini-3.8-flash") == "google/gemini-3.8-flash"
    assert model_catalog.openrouter_id_for("google", "gemini-3.7-flash") == "google/gemini-3.7-flash"
    assert model_catalog.openrouter_id_for("openai", "gpt-6-astra") == "openai/gpt-6-astra"
    assert model_catalog.openrouter_id_for("openai", "gpt-6-sol") == "openai/gpt-6-sol"
    assert model_catalog.openrouter_id_for("openai", "gpt-6-luna") == "openai/gpt-6-luna"
    assert model_catalog.openrouter_id_for("anthropic", "claude-opus-5-5") == "anthropic/claude-opus-5-5"
    # Ve hiçbiri sohbet-dışı filtresine takılmıyor
    for mid in ("gemini-3.8-flash", "gemini-3.7-flash", "gpt-6-astra", "gpt-6-sol",
                "gpt-6-luna", "claude-opus-5-5"):
        assert model_catalog.is_chat_model(mid), mid
