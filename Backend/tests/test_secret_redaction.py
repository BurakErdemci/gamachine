"""Sır maskesinin kendisini sınayan testler (bulgu S3, 30 Tem 2026).

Bulgunun özü sıra dışı: koruma yanlış yazılmamıştı, HEDEFİ TAŞINMIŞTI. Modül
yazıldığında paylaşımlı sır transport URL'inin yol segmentindeydi ve
`_MCP_PATH_SECRET` tam orayı maskeliyordu. Sır sonradan `X-API-Key` başlığına
taşındı; iki mevcut desen de onu kaçırıyordu:

    _MCP_PATH_SECRET → sırrın ARTIK OLMADIĞI yeri koruyordu
    _ENV_ASSIGNMENT  → `=` ayracı ve `[A-Z_]` bekliyordu, `X-API-Key:` ikisine de uymaz

Mevcut testler yeşil kaldığı için kimse fark etmedi. Bu dosya o boşluğu ve
ters yönünü (yanlış pozitif) birlikte sabitliyor.
"""

import io
import logging

import pytest

from secret_redaction import install_log_redaction, redact_secrets

SIR = "aB3dE5fG7hIjKlMnOpQrStUvWxYz0123456789"


BARE_PROVIDER_KEYS = [
    "sk-ant-api03-" + SIR,
    "sk-proj-" + SIR,
    "sk-or-v1-" + SIR,
    "sk-" + "A1_b-" * 4,
    "AIza" + "A1_b-" * 6,
    "gsk_" + "A1b2" * 5,
    "nvapi-" + "A1_b-" * 4,
]


@pytest.mark.parametrize("key", BARE_PROVIDER_KEYS)
@pytest.mark.parametrize("template", [
    "Error: invalid credential {} rejected.",
    '{{"credential": "{}", "status": "invalid"}}',
    "key={}",
])
def test_bare_provider_keys_are_redacted(key, template):
    assert redact_secrets(template.format(key)) == template.format("<REDACTED>")


@pytest.mark.parametrize("text", [
    "task-1234567890123456789012",
    "risk-free",
    "sk-short",
    "AIza",
    "aaa8a1b232643b5355d1c7d7b83fbbd8b2cd6d37",
    "550e8400-e29b-41d4-a716-446655440000",
    "Please ask-me before changing this setting.",
    "sk-" + "A" * 19,
    "AIza" + "A" * 29,
    "gsk_" + "A" * 19,
    "nvapi-" + "A" * 19,
])
def test_bare_provider_key_near_misses_stay_intact(text):
    assert redact_secrets(text) == text


@pytest.mark.parametrize("key", BARE_PROVIDER_KEYS)
@pytest.mark.parametrize("prefix", ["a", "9", "_"])
def test_bare_provider_keys_require_a_left_boundary(key, prefix):
    text = prefix + key
    assert redact_secrets(text) == text


@pytest.mark.parametrize("key", BARE_PROVIDER_KEYS)
def test_bare_provider_key_after_a_dash_is_masked(key):
    assert key not in redact_secrets("--api-key-" + key)


@pytest.mark.parametrize("key", BARE_PROVIDER_KEYS)
def test_log_filter_redacts_bare_provider_keys(key):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        install_log_redaction()
        logging.getLogger("providers.redaction_test").warning(
            "Invalid credential %s rejected.", key
        )
        handler.flush()
        output = stream.getvalue()
    finally:
        root.removeHandler(handler)

    assert output == "Invalid credential <REDACTED> rejected.\n"


@pytest.mark.parametrize("metin", [
    f"X-API-Key: {SIR}",                                  # düz başlık satırı
    f"x-api-key: {SIR}",                                  # küçük harf
    f"Authorization: Bearer {SIR}",                       # şema kelimeli değer
    f"LOCAL_APP_TOKEN={SIR}",                             # ortam ataması
    f"http://127.0.0.1:8080/mcp/{SIR}",                   # URL yol segmenti
    f'Command \'["claude","mcp","add","--header","X-API-Key: {SIR}"]\' failed',
    f"API-SECRET : {SIR}",                                # ayraç çevresi boşluklu
])
def test_sir_maskeleniyor(metin):
    assert SIR not in redact_secrets(metin)


def test_authorization_degerinin_TAMAMI_maskeleniyor():
    """`\\S+` yetmiyordu: "Bearer" yutuluyor, asıl sır AÇIKTA kalıyordu.

    Yarım maskeleme hiç maskelememekten tehlikeli, çünkü log'a bakan kişi
    korunduğunu sanıyor.
    """
    c = redact_secrets(f"Authorization: Bearer {SIR}")
    assert SIR not in c
    assert "Bearer" not in c


@pytest.mark.parametrize("metin", [
    "monkey: banana",          # 'monKEY' — kelime İÇİNDE, ad bileşeni değil
    "monitor: on",
    "GET /mcp/hub HTTP/1.1",   # gerçek rota adı, sır değil
    "turkey: roasted",
])
def test_yanlis_pozitif_YOK(metin):
    """Ters yön. Bu olmadan "her şeyi maskele" de testi geçerdi.

    Maskelenen log okunmaz, okunmayan log teşhis değeri taşımaz — yani aşırı
    maskeleme de bir arıza, sadece sessiz olanı.
    """
    assert redact_secrets(metin) == metin


def test_kisa_mcp_yol_sirri_da_maskeleniyor():
    """Alt sınır 20'den 12'ye çekildi; 18 karakterlik sır kaçıyordu."""
    kisa = "abc123def456ghi789"
    assert kisa not in redact_secrets(f"http://h/mcp/{kisa}")


# ── Log filtresi: çağrı yerine değil, tek noktaya bağlı ─────────────────────


def test_log_filtresi_ALT_loggerdan_yayilani_da_maskeliyor():
    """Denetimde iki sağlayıcının hata yolunda `redact_secrets` çağrısı HİÇ yoktu.

    Çağrı yerine tek tek eklemek aynı hatayı tekrarlamak olurdu: unutulan çağrı,
    olmayan korumadır. Filtre işleyici düzeyinde takılı olduğu için alt
    logger'lardan YAYILAN kayıtları da kapsıyor — kök logger'a filtre eklemek
    bunu yapmazdı ve sessizce yetersiz kalırdı.
    """
    akis = io.StringIO()
    isleyici = logging.StreamHandler(akis)
    kok = logging.getLogger()
    kok.addHandler(isleyici)
    try:
        install_log_redaction()
        log = logging.getLogger("providers.copilot")
        log.warning("MCP config yazılamadı: X-API-Key: %s", SIR)
        log.warning(f"Authorization: Bearer {SIR}")
        isleyici.flush()
        cikti = akis.getvalue()
    finally:
        kok.removeHandler(isleyici)

    assert SIR not in cikti, "sır log çıktısına sızdı"
    assert "<REDACTED>" in cikti


def test_log_filtresi_iki_kez_takilmiyor():
    """`install_log_redaction` yeniden çağrılabilir olmalı — import sırası
    değişirse iki kez çalışabilir ve filtreler birikirse her kayıt iki kez
    işlenirdi."""
    akis = io.StringIO()
    isleyici = logging.StreamHandler(akis)
    kok = logging.getLogger()
    kok.addHandler(isleyici)
    try:
        install_log_redaction()
        install_log_redaction()
        install_log_redaction()
        sayi = sum(
            1 for f in isleyici.filters if type(f).__name__ == "_RedactingFilter"
        )
    finally:
        kok.removeHandler(isleyici)
    assert sayi == 1


# ── Doğrulama turu bulgusu: istisna metni ayrı bir yüzey ────────────────────


def test_exc_info_traceback_da_maskeleniyor():
    """`getMessage()` yalnız çağrının kendi metnini verir; traceback SONRA eklenir.

    Ölçüldü (30 Tem 2026, doğrulama turu): `logger.exception(...)` ile sır log
    çıktısında GÖRÜNÜYORDU. Bu modülün var oluş sebebinin ta kendisi —
    `CalledProcessError`'ın metni argv'nin tamamını taşıyor ve tam buradan geçiyor.
    """
    akis = io.StringIO()
    isleyici = logging.StreamHandler(akis)
    kok = logging.getLogger()
    kok.addHandler(isleyici)
    try:
        install_log_redaction()
        log = logging.getLogger("sinama.exc")
        try:
            raise RuntimeError(f"bağlantı hatası: X-API-Key: {SIR}")
        except RuntimeError:
            log.exception("MCP kaydı başarısız")
        isleyici.flush()
        cikti = akis.getvalue()
    finally:
        kok.removeHandler(isleyici)

    assert SIR not in cikti, "sır traceback üzerinden sızdı"
    assert "RuntimeError" in cikti, "traceback tamamen kaybolmamalı — teşhis lazım"


def test_stack_info_da_maskeleniyor():
    akis = io.StringIO()
    isleyici = logging.StreamHandler(akis)
    kok = logging.getLogger()
    kok.addHandler(isleyici)
    try:
        install_log_redaction()
        logging.getLogger("sinama.stack").warning(
            f"X-API-Key: {SIR}", stack_info=True
        )
        isleyici.flush()
        cikti = akis.getvalue()
    finally:
        kok.removeHandler(isleyici)
    assert SIR not in cikti
