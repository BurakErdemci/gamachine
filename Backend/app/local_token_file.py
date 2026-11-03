"""Backend bearer token'ının (LOCAL_APP_TOKEN) süreçler arası TEK aktarım yolu.

Sorun (2026-07-27 denetimi, C grubu): token yedi sağlayıcı yolunda ayrı ayrı
çoğaltılıyordu ve iki yerde okunabilir hâlde duruyordu —

  1. ``workspace/.mcp.json`` içine düz metin yazılıyordu. Bu dosya MODELİN kendi
     projesinin içinde: sıradan bir okuma aracı, bir commit ya da bir arşiv onu
     ifşa ediyordu. Token backend'de sohbet, API anahtarı değiştirme, workspace
     işlemleri ve Unity MCP kontrolü için tek yetki kanıtı.
  2. ``codex mcp add --env LOCAL_APP_TOKEN=<sır>`` ve ``claude mcp add -e ...``
     komut satırlarında geçiyordu. ``--env`` çocuğun ortamını kurar ama EBEVEYNİN
     argv'sini gizlemez; kayıt sürerken ``ps`` çalıştıran her aynı-kullanıcı
     süreci token'ı okuyabiliyordu.

Çözüm, Unity MCP tarafında zaten işe yarayan kalıbın aynısı: sır 0600 izinli bir
dosyada durur, onu okuması gereken çocuk süreç dosyadan okur. Config dosyalarına
ve komut satırlarına hiç girmez.

Neden ortam değişkeni tek başına yetmiyor: MCP sunucusunu BİZ başlatmıyoruz —
claude/codex/cursor gibi CLI'lar başlatıyor, dolayısıyla bizim ortamımızı miras
almıyorlar. Aktarım için ya config dosyası ya argv ya da bu dosya kalıyor; ilk
ikisi okunabilir olduğu için üçüncüsü seçildi.
"""

import logging
import os
import stat

logger = logging.getLogger(__name__)

_TOKEN_DIR = os.path.join(os.path.expanduser("~"), ".unity_architect_ai")
_TOKEN_PATH = os.path.join(_TOKEN_DIR, "local-app-token")

# ── Sır dosyası ilkelleri — iki sır da (backend bearer'ı ve Unity MCP paylaşımlı
#    sırrı) buradan geçer. 2026-07-27 denetiminin üç bulgusu tek kökten çıktı ve
#    üçü de burada kapanıyor:
#
#      1. Yazıcılar sembolik bağı TAKİP EDİYORDU. `~/.unity-mcp/local-api-token`
#         yerine önceden bir bağ kurabilen saldırgan, sırrı seçtiği dosyaya
#         yazdırabiliyordu. Kanıtlandı, probe ile üretildi.
#      2. Dosya ZATEN 0644 ile varsa hiç sıkılaştırılmıyordu: okuma yolu erken
#         dönüyor, yazma yolunda O_CREAT modu var olan dosyaya uygulanmıyor.
#      3. Var olan gevşek dosyaya bytlar önce yazılıp SONRA chmod ediliyordu —
#         aradaki pencerede sır 0644'te okunabilir duruyordu.
#
# ── Windows (ölçüldü 2026-07-30, Python 3.13/win32) — iki POSIX varsayımı çöktü:
#
#      1. `os.O_NOFOLLOW` YOK. Koşulsuz kullanmak `AttributeError` üretiyordu ve
#         `AttributeError` bir `OSError` DEĞİL, yani aşağıdaki `except OSError`
#         onu yakalamıyor, hata çağırana sızıyordu. Ölçülen bedeli ürünün
#         tamamı: `main.py` bu modülü import anında çağırdığı için **backend
#         Windows'ta hiç ayağa kalkmıyordu**; `unity_mcp_manager.start_server()`
#         False dönüyordu (Unity MCP hiç başlamıyor) ve `approval_bridge`
#         token'ı okuyamadığı için **her onay isteğini reddediyordu.**
#      2. POSIX izin bitleri YOK. `os.chmod(path, 0o600)` sonrası `st_mode`
#         hâlâ `0o666` — ama `os.fchmod` VAR ve hata VERMİYOR. Yani eski kod
#         "izni 0600'e çekildi" diye uyarı basıp hiçbir şey yapmıyordu, ve
#         maske (`0o666 & 0o077 = 0o66`) her seferinde tuttuğu için bu yalan
#         HER OKUMADA tekrarlanıyordu. Bu depoda kapatılmış bir sınıf:
#         olmamış işleme "oldu" demek.
#
# Windows'ta sırrın korunması bu yüzden dosyanın KONUMUNA dayanıyor
# (`%USERPROFILE%\.unity_architect_ai`, NTFS profil ACL'i). Bu bir taviz ve
# aynı gerekçeye dayanıyor: tek kullanıcılı makinede aynı kullanıcı sırrı
# zaten okuyabiliyor.

# ⚠️ Yol kimliği doğrulaması ARTIK BURADA DEĞİL — `safe_paths`'te.
# Taşınma sebebi: aynı korumaya ikinci bir çağıran geldi (`workspace_config`)
# ve bu depoda güvenlik kararını KOPYALAMAK adı konmuş bir arıza sınıfı.
# Aşağıdaki adlar geriye dönük takma adlar: bu modülün gövdesi ve testleri
# onları kullanmaya devam ediyor.
from safe_paths import (  # noqa: E402
    _O_NOFOLLOW,
    _dogrula_kimlik,
    _refuse_symlink,
)

# ⚠️ Bu bayrak BURADA kalıyor, `safe_paths`'e taşınmadı: yalnız bu modülün izin
# sıkılaştırma yolunu ilgilendiriyor ve testleri onu bu modülde eziyor.
# `os.name` üzerinden, `hasattr(os, "fchmod")` üzerinden DEĞİL: Windows'ta
# `fchmod` var ama etkisiz, yani varlığı yanlış soruya doğru cevap veriyor.
_POSIX_MODE_BITS = os.name == "posix"


def _open_secret_for_write(path: str) -> int:
    """Sır yazmak için fd döndürür; dosya açıldığı anda 0600 ve bağ değil.

    Sıra önemli: O_TRUNC ile içerik açılışta boşalıyor, fchmod ondan SONRA ama
    ilk bayttan ÖNCE koşuyor. Böylece sır hiçbir an gevşek izinle diskte
    bulunmuyor — (3) numaralı bulgu buydu.

    ⚠️ `O_TRUNC` açılıştan ÇIKARILDI. Eskiden açılışta içerik boşalıyordu; ana
    dizin junction ise bu, saldırganın seçtiği dosyayı kimlik doğrulanmadan
    KESMEK demekti. Şimdi sıra: aç → kimliği doğrula → izni sıkılaştır →
    kısalt → yaz. Sır hâlâ hiçbir an gevşek izinle diskte bulunmuyor, ama
    yönlendirme durumunda hedef dosyaya hiç dokunulmuyor.
    """
    _refuse_symlink(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | _O_NOFOLLOW, 0o600)
    try:
        _dogrula_kimlik(fd, path)
        if _POSIX_MODE_BITS:
            os.fchmod(fd, 0o600)
        else:
            _windows_acl_sertlestir(path)
        os.ftruncate(fd, 0)
    except OSError:
        os.close(fd)
        raise
    return fd


# Okuma yolundaki ACL onarımı süreç başına bir kez koşsun diye (gerekçe orada).
_ACL_ONARILDI: set = set()


def _windows_acl_sertlestir(path: str) -> None:
    """Windows'ta izin sıkılaştırması — `0o600` burada HİÇBİR ŞEY yapmıyor.

    ⚠️ Bu, ÖLÇÜLMÜŞ bir açığın kapatılması (2026-08-01, bu makinede):
    `_POSIX_MODE_BITS` Windows'ta `False` olduğu için yazma yolu izinlere hiç
    dokunmuyordu ve dosya ana dizinin ACL'ini MİRAS ALIYORDU. Ölçüm:

        ~/.unity_architect_ai            → CodexSandboxUsers:(OI)(CI)(M)
        ~/.unity_architect_ai/local-app-token → CodexSandboxUsers:(I)(M)

    Yani bu dizinde açık bir miras ACE'si vardı ve içinde doğan HER dosya, Codex
    sandbox kullanıcı grubuna DEĞİŞTİRME yetkisiyle geliyordu. Etkilenen dosya
    da bu modülün korumak için var olduğu şeydi: backend'in tek yetki kanıtı.

    Mekanizma yeniden yazılmıyor; `harden_config_file` bu depoda zaten var,
    mirası kırıyor ve SONUCU DOĞRULUYOR (ölçüldü: sertleştirilen dosyada yalnız
    `BURAK\\burcu:(F)` kalıyor). Kopyalamak yerine çağırmak kasıtlı — güvenlik
    kararını kopyalamak burada adı konmuş bir arıza sınıfı.

    İçe aktarma FONKSİYON İÇİNDE: `providers` bu modülü import ediyor, ters yön
    modül düzeyinde yazılırsa döngü riski doğar.

    Başarısızlık sessiz DEĞİL ama fırlatmıyor: sıkılaştıramamak, token'ı hiç
    yazamamaktan iyidir (uygulama açılmazdı). Uyarı loglanıyor.
    """
    try:
        from providers.workspace_config import harden_config_file
        if not harden_config_file(path):
            logger.warning(
                "[local-token] %s ACL'i kısıtlanamadı; dosya ana dizinin "
                "izinlerini miras alıyor olabilir.", path,
            )
    except Exception as e:  # import ya da icacls tarafı — token yazımını kırma
        logger.warning("[local-token] %s ACL sıkılaştırması atlandı: %s", path, e)


def read_secret_file(path: str) -> str:
    """Sır dosyasını bağ takip etmeden okur ve gevşek izin bulursa SIKILAŞTIRIR.

    Sıkılaştırma burada, çünkü tek okunan yer burası: yalnız yazma yolunu
    düzeltmek yetmiyordu — mevcut kurulumlarda dosya çoktan 0644'le yaratılmıştı
    ve okuma yolu erken dönüp onu olduğu gibi bırakıyordu ((2) numaralı bulgu).
    """
    try:
        _refuse_symlink(path)
        fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW)
    except OSError:
        return ""
    try:
        # Okuma yolu da doğrulanıyor: junction'lı bir ana dizin, saldırganın
        # yerleştirdiği token'ı ürüne KABUL ETTİRİYORDU (probe ile üretildi).
        # Yönlendirme varsa boş dönüyoruz — sahte bir sırla devam etmek,
        # sırrı sızdırmak kadar tehlikeli.
        try:
            _dogrula_kimlik(fd, path)
        except OSError as e:
            logger.error(f"[local-token] {path} kimliği doğrulanamadı: {e}")
            return ""
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
        # İzin ölçümü ve düzeltmesi yalnız POSIX'te anlamlı. Windows'ta `st_mode`
        # ACL'den türetilmiyor (`0o666` sabit gelir) ve `fchmod` etkisiz; koşulu
        # kaldırmak "sıkılaştırdım" diyen ama hiçbir şey yapmayan bir uyarıyı
        # her okumada bastırırdı.
        if _POSIX_MODE_BITS and mode & 0o077:
            try:
                os.fchmod(fd, 0o600)
                logger.warning(
                    f"[local-token] {path} izni {oct(mode)} idi, 0600'e çekildi."
                )
            except OSError as e:
                logger.error(f"[local-token] {path} sıkılaştırılamadı: {e}")
        elif not _POSIX_MODE_BITS:
            # Windows onarım ucu, POSIX dalıyla AYNI gerekçe: yalnız yazma
            # yolunu düzeltmek mevcut kurulumları kapsamıyor — dosya çoktan
            # ana dizinin ACL'iyle yaratılmış durumda (ölçüldü: bu makinede
            # backend token'ı `CodexSandboxUsers:(I)(M)` taşıyordu).
            #
            # ⚠️ SÜREÇ BAŞINA BİR KEZ. Windows'ta ACL'i okumanın ucuz bir yolu
            # yok; `icacls` iki alt süreç demek ve bu fonksiyon her onay
            # isteğinde çağrılıyor. Her okumada koşturmak, düzeltmeyi bir
            # performans sorununa çevirirdi.
            if path not in _ACL_ONARILDI:
                _ACL_ONARILDI.add(path)
                _windows_acl_sertlestir(path)
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as f:
            return f.read().strip()
    finally:
        os.close(fd)


def token_path() -> str:
    return _TOKEN_PATH


def write_local_app_token(token: str) -> bool:
    """Token'ı 0600 izinle diske yazar. Boş token dosyayı SİLER.

    Boşta silme kasıtlı: uygulama token'sız (dev) başlatıldığında önceki
    oturumdan kalan geçerli bir sırrın diskte kalması, kapalı sanılan bir kapıyı
    açık bırakırdı.
    """
    try:
        if not token:
            if os.path.exists(_TOKEN_PATH):
                os.remove(_TOKEN_PATH)
            return True
        os.makedirs(_TOKEN_DIR, exist_ok=True)
        with os.fdopen(_open_secret_for_write(_TOKEN_PATH), "w", encoding="utf-8") as f:
            f.write(token)
        return True
    except OSError as e:
        logger.error(f"[local-token] yazılamadı: {e}")
        return False


def read_local_app_token() -> str:
    """Önce ortam, sonra dosya.

    Sıra önemli: backend'in KENDİ sürecinde token ortamda var ve dosyadan daha
    günceldir. MCP sunucusu gibi ayrı başlatılan çocuklarda ortam boştur ve
    dosya devreye girer.
    """
    token = os.environ.get("LOCAL_APP_TOKEN", "")
    if token:
        return token
    return read_secret_file(_TOKEN_PATH)


def backend_url_path() -> str:
    return os.path.join(os.path.dirname(_TOKEN_PATH), "backend-url")


def write_backend_url(url: str) -> bool:
    """Writes the running backend's URL next to the token so a terminal agent's
    stdio MCP server can find it. An empty url deletes the file."""
    path = backend_url_path()
    try:
        if not url:
            if os.path.exists(path):
                os.remove(path)
            return True
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with os.fdopen(_open_secret_for_write(path), "w", encoding="utf-8") as f:
            f.write(url)
        return True
    except OSError as e:
        logger.error(f"[local-token] backend URL could not be written: {e}")
        return False


def read_backend_url() -> str:
    """The URL written by the running backend; "" when missing or unreadable."""
    try:
        return read_secret_file(backend_url_path())
    except Exception:
        return ""
