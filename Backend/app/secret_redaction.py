"""Log'a giden metinden sırları silen TEK kaynak.

Neden gerekli: `subprocess.run(..., check=True)` başarısız olduğunda fırlattığı
`CalledProcessError`'ın `str()`'i KOMUT LİSTESİNİN TAMAMINI taşıyor. Yani tek
bir başarısız MCP kayıt denemesi sırrı düz metin olarak log dosyasına
yazıyordu — 2026-07-27 denetiminde probe ile üretildi.

⚠️ SIR YER DEĞİŞTİRDİ, koruma yerinde kalmıştı (bulgu S3, 30 Tem 2026).
Bu modül yazıldığında paylaşımlı sır transport URL'inin YOL SEGMENTİNDE
duruyordu ve `_MCP_PATH_SECRET` tam olarak orayı maskeliyordu. Sır o zamandan
beri `X-API-Key` BAŞLIĞINA taşındı; kayıt komutu artık
``--header "X-API-Key: <sır>"`` biçiminde. Eski iki desenin ikisi de bunu
kaçırıyordu:

    _MCP_PATH_SECRET → sırrın ARTIK OLMADIĞI yeri koruyordu
    _ENV_ASSIGNMENT  → `=` ayracı ve `[A-Z_]` bekliyordu; `X-API-Key: <sır>`
                       iki nokta kullanıyor ve adında tire var

Ders, bu depoda tekrarlayan bir sınıf: bir korumanın hedefi taşındığında koruma
sessizce boşa düşüyor ve testler yeşil kaldığı için kimse fark etmiyor.

Neden hata metni tamamen atılmıyor: kaydın NEDEN başarısız olduğu teşhis için
gerekli bilgi. Metin korunuyor, yalnız sır maskeleniyor.

Neden ayrı modül: ilk düzeltmede aynı fonksiyon üç sağlayıcı dosyasına
kopyalanmıştı. Aynı oturumda kapatılan bulguların en sık tekrarlayan sınıfı
"güvenlik kararının kopyalanması" idi — kopya, düzeltmenin kopyalardan yalnız
birine uygulanması demek.
"""

import logging
import re

# token_urlsafe(32) → [A-Za-z0-9_-] alfabesinde ~43 karakter. Alt sınır 20 idi;
# 12'ye çekildi çünkü daha kısa sırlar da üretilebiliyor ve maskelenmiyordu.
# 12'nin altına inilmedi: gerçek rota adları ("hub", "sse", "messages",
# "stream") hepsi bunun altında ve onları maskelemek log'u okunmaz yapardı.
_MCP_PATH_SECRET = re.compile(r"(/mcp/)[A-Za-z0-9_\-]{12,}")

# LOCAL_APP_TOKEN=..., ANTHROPIC_API_KEY=..., X_API_SECRET=... gibi atamalar
_ENV_ASSIGNMENT = re.compile(r"((?:TOKEN|KEY|SECRET)[A-Z_]*=)\S+")

# SDK error text and logs can contain bare provider keys without a header.
# A leading '-' (e.g. "--key-sk-...") still counts as a boundary: when in
# doubt, mask.
_BARE_PROVIDER_KEY = re.compile(
    r"(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|"
    r"gsk_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9_-]{20,})"
)

# `X-API-Key: <sır>` gibi HTTP başlıkları. `_ENV_ASSIGNMENT`'tan üç farkı var ve
# üçü de S3'ün kaçmasının sebebiydi: ayraç `=` değil `:`, ad tire içeriyor, ve
# harf kipi karışık.
#
# İki ayrıntı ölçülerek eklendi, ikisi de ilk denemede yanlıştı:
#
#  • `(?<![A-Za-z0-9])` ŞART. Onsuz desen dizenin ORTASINDAN eşleşiyor ve
#    "monkey: banana" → "monkey: <REDACTED>" oluyordu (monKEY). Yanlış pozitif
#    log'u okunmaz yapar, okunmayan log da teşhis değeri taşımaz.
#  • Değer `\S+` OLAMAZ. "Authorization: Bearer xyz123" içinde `\S+` yalnız
#    "Bearer"ı yutuyor ve asıl sır AÇIKTA kalıyordu — yarım maskeleme, hiç
#    maskelememekten tehlikeli çünkü bakan kişi korunduğunu sanıyor.
#    Değer artık satırın kalanı; tırnak/virgül/süslü parantezde duruyor ki
#    argv ya da JSON içine gömülü başlıklarda komşu alanları yutmasın.
#  • Ad ile `:` ARASINA TIRNAK girebilir ve girdiğinde desen kaçıyordu
#    (denetim bulgusu, ölçüldü 2026-08-01). Serileştirilmiş biçim tam olarak
#    böyle görünüyor ve bir istisna metnindeki sözlük de öyle:
#
#        X-API-Key: sir            → maskeleniyordu
#        {'X-API-Key': 'sir'}      → SIZIYORDU
#        {"X-API-Key": "sir"}      → SIZIYORDU
#
#    Yani koruma tam da sırrın tarayıcıya ulaşabildiği biçimde yoktu. Tırnaklar
#    1. gruba alınıyor ki çıktı geçerli JSON/repr olarak okunabilir kalsın;
#    değerin kendisi zaten tırnakta durduğu için kapanış tırnağı sağ kalıyor.
_HEADER_SECRET = re.compile(
    r"((?<![A-Za-z0-9])(?:[A-Za-z0-9]+[-_])*"
    r"(?:TOKEN|KEY|SECRET|AUTH|APIKEY)[A-Za-z0-9_-]*"
#  • Tırnağın ÖNÜNDE ters bölü olabilir: bir kez daha serileştirilmiş
#    (JSON içine gömülü JSON) tanılama nesnesi `\"X-API-Key\": \"<sır>\"`
#    biçiminde görünüyor ve tırnak isteğe bağlı olsa bile ters bölü deseni
#    kırıyordu. Ters bölüler de 1. gruba alınıyor.
#    `\\*` (bir değil, KAÇ TANE olursa) — art arda serileştirme her turda ters
#    bölü sayısını katlıyor (1 → 3 → 7). Tek bir `\\?` yalnız ilk katmanı
#    kapatıyordu (4. denetim turu).
    r"\\*[\"']?\s*:\s*\\*[\"']?)"
    r"[^\"',}\n\r\\]+",
    re.IGNORECASE,
)


# `AUTH`/`KEY`/`SECRET` ÖNEK olarak eşleştiği için masum kelimeler de yakalanıyor.
# Öneki kaldırmak seçenek değil: `Authorization` gerçek bir sır başlığı ve o da
# `AUTH` önekiyle geliyor. Bu yüzden istisna listesi DAR ve kelimenin TAMAMINA
# bakıyor — `authorization` buraya girmediği için maskelenmeye devam ediyor.
#
# Neden dert edildi: bu ürün git komutlarının çıktısını logluyor ve `author:`
# orada her commit'te geçiyor. Modülün kendi gerekçesi zaten "yanlış pozitif
# log'u okunmaz yapar, okunmayan log da teşhis değeri taşımaz" diyor.
# ⚠️ Şüphede kalınırsa MASKELE: fazla maskeleme okunabilirlik kaybı, eksik
# maskeleme sır sızıntısı. Bu listeye ekleme yaparken ölçüt bu.
_MASUM_ADLAR = frozenset({"author", "authors", "authority", "keyboard", "secretary", "keywords"})


def _header_maskele(m: "re.Match") -> str:
    ad = m.group(1)
    # ⚠️ Yalnız SARMALAYICI karakterler (tırnak, iki nokta, boşluk) atılıyor;
    # adın İÇİNDEKİ tire/alt çizgi KORUNUYOR. İlk yazımda bütün alfanümerik
    # olmayanlar siliniyordu ve bu bir KAÇIŞ YOLU açıyordu (4. denetim turu,
    # ölçüldü): `auth-or:` → `author` → masum listesine takla atıp
    # maskelenmiyordu. `key-board`, `secret-ary` de aynı yoldan geçiyordu.
    # Yorum "kelimenin tamamına bakıyor" diyordu ama kod bakmıyordu — bu
    # depoda adı konmuş sınıf: belge ile davranışın ayrışması.
    # Ters bölü de SARMALAYICI sayılıyor: serileştirilmiş `{\"author\": ...}`
    # biçiminde ad `\"author\"` olarak geliyordu ve masum listesine uymayıp
    # gereksiz maskeleniyordu. İç ayırıcılar yine korunuyor, yani `auth-or`
    # kaçış yolu KAPALI kalıyor (ikisi de testte).
    cekirdek = ad.strip(" \t\"':\\").lower()
    if cekirdek in _MASUM_ADLAR:
        return m.group(0)  # dokunma
    return ad + "<REDACTED>"


def redact_secrets(text: str) -> str:
    if not text:
        return text
    text = _MCP_PATH_SECRET.sub(r"\1<REDACTED>", text)
    text = _ENV_ASSIGNMENT.sub(r"\1<REDACTED>", text)
    text = _BARE_PROVIDER_KEY.sub("<REDACTED>", text)
    return _HEADER_SECRET.sub(_header_maskele, text)


class _RedactingFilter(logging.Filter):
    """Log'a giden HER kaydı maskeden geçirir.

    Neden çağrı yeri yeri değil de filtre: denetimde `copilot_provider:133` ve
    `opencode_provider:147` hata yollarında `redact_secrets` çağrısının HİÇ
    olmadığı bulundu. Tek tek eklemek aynı hatayı tekrarlamak olurdu — bu
    modülün kendi gerekçesi zaten "güvenlik kararının kopyalanması" sınıfını
    adlandırıyor: kopya, düzeltmenin kopyalardan yalnız birine uygulanması
    demek.

    Sistematik tarama sırra dokunan başka noktalar da gösterdi (`agy_provider`
    mcp_config yazımı, `kimi_provider` config.toml, `approval_bridge` POST).
    Filtre bunların hepsini ve HENÜZ YAZILMAMIŞ olanları da kapsıyor; çağrı
    yerine bağlı bir çözüm yalnız bugün bilinenleri kapsardı.

    ⚠️ Maskeleme FORMATLANMIŞ mesaja uygulanıyor, ham `msg`'e değil. İlk sürüm
    ham `msg`'i maskeliyordu ve kendi testi onu yakaladı:

        logger.warning("MCP config yazılamadı: X-API-Key: %s", sır)

    Burada `msg` içindeki `X-API-Key: %s` desene UYUYOR, yani `%s` yer tutucusu
    da `<REDACTED>` ile değişiyordu. Sonra `msg % args` "not all arguments
    converted" ile patlıyor ve logging kaydı **tamamen düşürüyor** — sırrı
    korurken log'u yok eden bir düzeltme. Formatlanmış metni maskelemek hem
    yer tutucu sorununu hem `args` içindeki sırları tek adımda çözüyor.
    """

    def filter(self, record: "logging.LogRecord") -> bool:
        try:
            tam = record.getMessage()
        except Exception:
            # Bozuk format dizgesi bizim sorunumuz değil; kaydı olduğu gibi
            # geçiriyoruz ki asıl hata görünür kalsın.
            return True
        temiz = redact_secrets(tam)
        if temiz != tam:
            record.msg = temiz
            record.args = ()

        # ⚠️ İSTİSNA METNİ AYRI BİR YÜZEY (doğrulama turu bulgusu, 30 Tem 2026).
        # `getMessage()` yalnız log çağrısının kendi metnini verir; `exc_info`
        # ile gelen traceback ondan SONRA, biçimlendirici tarafından eklenir.
        # Ölçüldü: `raise RuntimeError(f"... X-API-Key: {sır}")` + `logger.
        # exception(...)` → sır çıktıda GÖRÜNÜYORDU. Bu, modülün var oluş
        # sebebinin ta kendisi: `CalledProcessError`'ın metni argv'nin tamamını
        # taşıyor ve o metin tam olarak buradan geçiyor.
        #
        # Çözüm biçimlendiriciyi beklemek yerine `exc_text`'i ŞİMDİ üretip
        # maskelemek: `Formatter.format` `exc_text` doluysa onu yeniden
        # üretmiyor, yani maskelenmiş hâli kullanılıyor.
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact_secrets(record.exc_text)
        elif record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)

        # `stack_info` de aynı yoldan geliyor (logger.<level>(..., stack_info=True)).
        if getattr(record, "stack_info", None):
            record.stack_info = redact_secrets(record.stack_info)
        return True


def install_log_redaction() -> None:
    """Maskeyi kök logger'ın tüm işleyicilerine takar. Yeniden çağrılabilir.

    Kök logger'a filtre eklemek YETMEZ: `logging.Filter` yalnız o logger'a
    doğrudan verilen kayıtlara uygulanır, alt logger'lardan yayılanlara
    uygulanmaz. İşleyici düzeyinde takmak yayılan kayıtları da kapsıyor.
    """
    kok = logging.root
    for isleyici in kok.handlers:
        if not any(isinstance(f, _RedactingFilter) for f in isleyici.filters):
            isleyici.addFilter(_RedactingFilter())
