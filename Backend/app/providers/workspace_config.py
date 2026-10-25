"""Ürünün KULLANICININ projesine yazdığı yerel araç dosyalarını `.gitignore`'a alır.

Neden var (2026-07-29 ölçümü, K3): ürün dört ayrı noktada kullanıcının Unity
projesine config dosyası yazıyor (`.mcp.json`, `opencode.json`,
`.cursor/mcp.json`, `.cursor/cli.json`) ve bunlar unityMCP'nin `X-API-Key`'ini
**düz metin** taşıyor. Kullanıcının gerçek projesinde (GitHub remote'u olan
`MatchDayOfficial`) ölçüldü: iki dosya gitignore'daydı ama satırları kullanıcı
kendi eliyle yazmıştı (`git blame` → 24 Tem commit'i), `.cursor/mcp.json` ise
İZLENİYORDU. Ürün hiçbir yere gitignore girdisi eklemiyordu, yani yeni bir
kullanıcıda koruma sıfır.

Bu modül `providers` altında: `agentic` → `providers` yönü zaten kurulu
(`agent_runner` düzinelerce yerde `providers.*` import ediyor). Ters yön
(`providers` → `agentic`) bu depoda bir kez döngüye sebep oldu — burada YENİ bir
ters import açılmıyor, modülün hiçbir proje-içi bağımlılığı yok.

Sözleşme: **bu modülün hiçbir fonksiyonu çağıranın yoluna istisna fırlatmaz.**
gitignore yazamamak bir kolaylık kaybıdır; sohbetin ya da oturum açılışının
kırılması değil. Bu yüzden her giriş noktası geniş bir `except` ile bitiyor.
"""

import json
import logging
import os
import stat
import subprocess
import tempfile
from typing import Iterable, List, Optional, Set

from path_containment import is_within

logger = logging.getLogger(__name__)

# Etiketli blok: kullanıcı `git diff`'inde ne olduğunu görsün ve tek parça
# silebilsin. Sessizce araya satır sıkıştırmak, kullanıcının kendi dosyasını
# ürünün habersizce düzenlemesi demek olurdu.
BLOCK_BEGIN = "# >>> Gamachine — yerel araç dosyaları (ürün tarafından eklendi) >>>"
BLOCK_END = "# <<< Gamachine <<<"

# Marka değişiminden (8 Ağu 2026) ÖNCEKİ etiketler. Bunlar KULLANICININ kendi
# `.gitignore`'ına yazılmış durumda, dolayısıyla yeni etiketlere geçmek tek başına
# yeterli değil: eşleşme birebir dize karşılaştırması olduğu için eski blok YETİM
# kalır ve dosyaya İKİNCİ bir blok eklenir — yani "tek parça silebilirsin" vaadi
# tam da marka temizliği yüzünden bozulurdu. Eski blok bulunursa kaldırılıyor.
#
# ⚠️ Bu iki dize BİLEREK eski markayı taşıyor ve otomatik bir yeniden adlandırma
# betiğine ASLA dahil edilmemeli. 8 Ağu 2026'da tam bu oldu: marka betiği bunları
# da "Gamachine" yaptı, `_LEGACY_*` ile `BLOCK_*` eşitlendi ve geçiş kodu kendini
# sessizce iptal etti — koruma yerinde görünüyor, hiçbir şey korumuyordu.
_LEGACY_BEGIN = "# >>> Unity Architect AI — yerel araç dosyaları (ürün tarafından eklendi) >>>"
_LEGACY_END = "# <<< Unity Architect AI <<<"
_BLOCK_NOTE = (
    "# Bu satırları Gamachine ekledi — istemiyorsanız bu bloğu\n"
    "# (iki etiket dahil) silebilirsiniz, uygulama çalışmaya devam eder.\n"
    "# Sebebi: aşağıdaki dosyalar yerel MCP anahtarını DÜZ METİN taşır ve\n"
    "# depoya girerlerse anahtar da girer.\n"
)

# git alt sürecinin bekleme sınırı. Ağ yok, yerel disk işi: 5 sn cömert. Sınır
# olmadan, bozuk bir depoda (kilitli index, ağ dosya sistemi) oturum açılışı
# git'i beklerken asılı kalırdı.
_GIT_TIMEOUT_S = 5


# ───────────────────────────── yol güvenliği ──────────────────────────────


def _resolve_gitignore(workspace: str) -> Optional[tuple]:
    """(ws_real, gitignore_path) döndürür; güvenli değilse None.

    Symlink'le config ezme bu depoda ÖLÇÜLMÜŞ bir sınıf (K4: altı yazım
    noktasının altısı da workspace dışındaki kurbanı ezdi). Burada aynı sınıfı
    yeni bir noktada açmamak için hem workspace hem de `.gitignore` `realpath`
    ile çözülüyor ve hedefin gerçek konumu workspace'in İÇİNDE olmak zorunda.
    `realpath` iki tarafa da uygulanıyor: yalnız hedefe uygulamak, workspace'in
    kendisi bir bağ olduğunda (macOS'ta `/tmp` → `/private/tmp`) meşru
    kurulumları yanlışlıkla reddederdi.
    """
    if not workspace:
        return None
    ws_real = os.path.realpath(workspace)
    if not os.path.isdir(ws_real):
        return None
    path = os.path.join(ws_real, ".gitignore")
    target_real = os.path.realpath(path)
    if os.path.dirname(target_real) != ws_real:
        logger.warning(
            "[gitignore] %s workspace dışına (%s) işaret ediyor — yazılmadı.",
            path, target_real,
        )
        return None
    return ws_real, path


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _atomic_write(path: str, content: str, ws_real: str) -> None:
    """Geçici dosya + `os.replace`. Yarı yazılmış bir `.gitignore` bırakmaz.

    Geçici dosya BİLEREK workspace'in içinde: `os.replace` ancak aynı dosya
    sisteminde atomik. Hata durumunda temizleniyor, yoksa kullanıcının
    `git status`'unda artık bir `.gitignore.*.tmp` kalırdı.
    """
    fd, tmp = tempfile.mkstemp(dir=ws_real, prefix=".gitignore.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, 0o644)   # mkstemp 0600 verir; `.gitignore` sır değil
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ──────────────────────────── kapsama tespiti ─────────────────────────────


def _git_covered(ws_real: str, entries: List[str]) -> Optional[Set[str]]:
    """git'e göre zaten yok sayılan girdiler; ölçülemezse None.

    `--no-index` ŞART ve ölçümle seçildi (2026-07-29, git 2.50.1): onsuz
    `check-ignore`, TAKİP EDİLEN bir dosya için desen eşleşse bile rc=1 ("yok
    sayılmıyor") döner. O davranışla, zaten gitignore'da yazan ama takip edilen
    bir dosya her oturumda "eksik" görünür ve bloğa tekrar tekrar eklenirdi.
    `--no-index` saf desen sorusunu sorar; takip durumu ayrıca `_tracked()` ile
    ölçülüp kullanıcıya uyarı olarak veriliyor.

    Tek alt süreçte tüm girdiler sorulur (`--stdin`): rc=0 "en az biri", rc=1
    "hiçbiri" demek — ikisi de geçerli cevap. Başka her şey (repo değil → 128,
    git yok, zaman aşımı) ölçülemedi sayılır ve çağıran literal karşılaştırmaya
    düşer.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", ws_real, "check-ignore", "--no-index", "--stdin"],
            # `input` BYTES ve `text` YOK — ölçüldü (30 Tem 2026, Windows):
            # `text=True` ile stdin `io.TextIOWrapper(newline=None)` üzerinden
            # yazılıyor ve her `\n` platform ayracına çevriliyor (`\r\n`). git
            # `\r`'yi YOL ADININ PARÇASI sayıyor → hiçbir girdi eşleşmiyor →
            # fonksiyon her zaman boş küme dönüyordu. Yani git katmanı Windows'ta
            # sessizce ÖLÜ: ürün literal karşılaştırmaya düşüyor, `*.json` gibi
            # geniş desenler tanınmıyor ve kullanıcının `.gitignore`'una zaten
            # kapsanan girdiler için gereksiz blok ekleniyordu.
            # İki varyantlı ölçüm: tek fark `input=b"..."` iken rc 1 → 0 ve
            # stdout `.mcp.json` döndü.
            input=("\n".join(entries) + "\n").encode("utf-8"),
            capture_output=True, timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode not in (0, 1):
        return None
    cikti = proc.stdout.decode("utf-8", "replace")
    return {line.strip() for line in cikti.splitlines() if line.strip()}


def harden_config_file(path: str) -> bool:
    """Sır taşıyan bir workspace config dosyasını sahibine kilitler.

    Neden gerekli (bulgu S4b): bu dosyalar `X-API-Key`'i DÜZ METİN taşıyor ve
    workspace'in ACL'ini MİRAS alıyor. Paylaşımlı ya da CI workspace'inde ana
    dizinde `Everyone (RX)` varsa sır makinedeki her hesap tarafından
    okunabiliyordu — dosyanın kendisi hiç incelenmeden.

    Neden ortak modülde, çağrı yerinde değil: aynı sırrı ÜÇ yazıcı üretiyor
    (`cli_base._write_mcp_config`, `copilot_provider`, `opencode_provider`).
    Korumayı birine gömmek, bu depoda tekrar tekrar ölçülmüş "güvenlik
    kararının kopyalanması" sınıfını yeniden üretirdi.

    Windows'ta miras kırılıp yalnız mevcut hesaba tam erişim veriliyor; POSIX'te
    0600. Dönüş değeri sıkılaştırmanın ÖLÇÜLDÜĞÜNÜ söylüyor — çağıran bunu
    yutmuyor, log'a yazıyor: sessizce başarısız olan bir sıkılaştırma, hiç
    olmayandan kötüdür çünkü korunduğu sanılır.
    """
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
            return True
        except OSError as e:
            logger.warning("[config-acl] %s izni kısıtlanamadı: %s", path, e)
            return False

    # ⚠️ `/inheritance:r` MİRASI kırıyor ama dosyaya DOĞRUDAN verilmiş açık
    # yetkileri kaldırmıyor (doğrulama turu bulgusu, 30 Tem 2026). Var olan bir
    # dosyanın üzerine yazarken o yetkiler sağ kalır ve sır okunabilir kalırdı.
    # `/remove` önce koşuyor; hedef yoksa `icacls` zaten hata vermiyor.

    kullanici = os.environ.get("USERNAME", "")
    alan = os.environ.get("USERDOMAIN", "")
    if not kullanici:
        logger.warning("[config-acl] %s: USERNAME okunamadı, ACL kısıtlanmadı", path)
        return False
    hesap = f"{alan}\\{kullanici}" if alan else kullanici
    try:
        proc = subprocess.run(
            [
                "icacls", path,
                "/inheritance:r",
                "/remove", "Everyone", "Users", "Authenticated Users",
                "/grant:r", f"{hesap}:F",
            ],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("[config-acl] %s için icacls çalıştırılamadı: %s", path, e)
        return False
    if proc.returncode != 0:
        logger.warning(
            "[config-acl] %s ACL'i kısıtlanamadı: %s",
            path, (proc.stderr or proc.stdout).strip()[:200],
        )
        return False

    # ⚠️ SONUCU DOĞRULA, komutun rc'sine güvenme (2. doğrulama turu bulgusu).
    # `/inheritance:r` yalnız MİRAS ACE'leri kaldırıyor, `/remove` yalnız
    # adlandırdığımız üç principal'ı, `/grant:r` yalnız sahibin girdisini
    # değiştiriyor. Dosyaya DOĞRUDAN verilmiş başka bir yetki (ör. bir
    # `AuditReaders` grubu) bunların üçünden de sağ çıkıyordu ve fonksiyon yine
    # `True` dönüyordu — yani "sıkılaştırıldı" diyen ama sırrı okunabilir
    # bırakan bir yalan. Bu depoda kapatılmış bir sınıf: olmamış işleme
    # "oldu" demek.
    try:
        kontrol = subprocess.run(
            ["icacls", path], capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("[config-acl] %s ACL'i doğrulanamadı: %s", path, e)
        return False
    if kontrol.returncode != 0:
        logger.warning("[config-acl] %s ACL'i okunamadı", path)
        return False

    # `icacls` çıktısı: "<yol> HESAP:(izinler)" satırları. Yolun kendisi ilk
    # satırın başında; onu çıkarıp kalan her satırdaki hesabı topluyoruz.
    yabanci: list[str] = []
    for i, satir in enumerate((kontrol.stdout or "").splitlines()):
        parca = satir[len(path):] if i == 0 and satir.startswith(path) else satir
        parca = parca.strip()
        if not parca or ":" not in parca:
            continue
        hesap_adi = parca.rsplit(":", 1)[0].strip()
        if not hesap_adi:
            continue
        if hesap_adi.lower() not in (hesap.lower(), kullanici.lower()):
            yabanci.append(hesap_adi)
    if yabanci:
        logger.warning(
            "[config-acl] %s üzerinde beklenmeyen erişim kaldı: %s",
            path, ", ".join(sorted(set(yabanci))[:5]),
        )
        return False
    return True


def _tracked(ws_real: str, entries: List[str]) -> Optional[List[str]]:
    """git'in HÂLÂ takip ettiği girdiler. ÖLÇÜLEMEZSE ``None`` — boş liste DEĞİL.

    ⚠️ Eskiden her hata `[]`'e eşleniyordu ve çağıran onu "takip edilen yok"
    diye okuyordu: fail-OPEN. Aynı dosyadaki kardeşi `_git_covered` ise
    ölçemediğinde `None` dönüp muhafazakâr davranıyordu — tek dosyada iki zıt
    hata felsefesi (bulgu E-b). Ayrım artık çağıranın elinde: "takip edilmiyor"
    ile "bakamadım" farklı şeyler ve ikincisi sessizce geçilemez.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", ws_real, "ls-files", "--"] + entries,
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _literal_present(text: str, entry: str) -> bool:
    """Satır bazlı birebir karşılaştırma — git yolunun yedeği.

    Geniş desenleri (`*.json`) GÖREMEZ; onu yalnız git yakalar. Buradaki iş
    ikili: (a) git'siz dizinlerde tek koruma, (b) git yolunda ikinci emniyet —
    bloğumuz zaten yazılıysa ikinci kez yazılmasını engeller.
    """
    want = entry.strip().lstrip("/").rstrip("/")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lstrip("/").rstrip("/") == want:
            return True
    return False


def _compose(existing: str, missing: List[str]) -> str:
    """Eksik girdileri etiketli bloğa yerleştirilmiş hâlde döndürür.

    Blok dosyanın **başına** konuyor (kullanıcı kararı, 29 Tem 2026): sona
    eklenirse uzun bir `.gitignore`'da görünmez ve kullanıcı `git diff`'te
    açıklayamadığı bir değişiklik görür. Başta durunca ilk bakışta "bunu ürün
    ekledi, istemezsem silerim" diyebiliyor.

    Blok zaten varsa İÇİNE eklenir; ikinci bir blok açmak kullanıcıya aynı
    etiketten birden fazla gösterirdi ve "tek parça sil" vaadini bozardı.
    """
    added = "".join(f"{e}\n" for e in missing)
    # Eski markanın bloğu varsa ÖNCE kaldır (gerekçe: _LEGACY_BEGIN). Yoksa aynı
    # dosyada iki blok birikir ve içindekiler de tekrarlanır.
    if _LEGACY_BEGIN in existing and _LEGACY_END in existing:
        _bas = existing.index(_LEGACY_BEGIN)
        _son = existing.index(_LEGACY_END) + len(_LEGACY_END)
        existing = (existing[:_bas] + existing[_son:]).lstrip("\n")
    if BLOCK_BEGIN in existing and BLOCK_END in existing:
        cut = existing.index(BLOCK_END)
        return existing[:cut] + added + existing[cut:]
    block = BLOCK_BEGIN + "\n" + _BLOCK_NOTE + added + BLOCK_END + "\n"
    if existing and not existing.startswith("\n"):
        block += "\n"   # bloğumuzla kullanıcının ilk satırı arasına nefes payı
    return block + existing


# ───────────────────────────── genel arayüz ───────────────────────────────


_BACKUP_SUFFIX = ".gamachine-backup"
_MAX_CONFIG_BYTES = 1024 * 1024
_RETRY = object()


def _move_aside(parts: List[str], rel_path: str, dfd: int, path: str,
                ident: tuple) -> object:
    """Rename the verified config to the first free backup name.

    Returns the backup's relative path, ``_RETRY`` if the name no longer refers to
    the file that was read, or ``None`` when no rename is possible.
    """
    name = parts[-1]
    kwargs = {"dir_fd": dfd} if dfd != -1 else {}
    current = os.stat(path if dfd == -1 else name, follow_symlinks=False, **kwargs)
    if (current.st_dev, current.st_ino) != ident:
        return _RETRY
    for n in range(0, 21):
        suffix = _BACKUP_SUFFIX if n == 0 else f"{_BACKUP_SUFFIX}-{n}"
        try:
            if dfd != -1:
                os.stat(name + suffix, follow_symlinks=False, dir_fd=dfd)
            else:
                os.lstat(path + suffix)
        except FileNotFoundError:
            pass
        else:
            continue  # never overwrite anything, a regular file included
        try:
            if dfd != -1:
                os.rename(name, name + suffix, src_dir_fd=dfd, dst_dir_fd=dfd)
            else:
                os.replace(path, path + suffix)
        except (IsADirectoryError, NotADirectoryError, FileExistsError):
            continue
        return rel_path + suffix
    return None


def _read_config_once(workspace: str, ws_real: str, rel_path: str, parts: List[str]) -> object:
    from safe_paths import O_NOFOLLOW, dogrula_kimlik

    path = os.path.join(ws_real, rel_path)
    dfd = -1
    fd = -1
    try:
        try:
            if os.open in os.supports_dir_fd:
                dfd = os.open(ws_real, os.O_RDONLY | os.O_DIRECTORY)
                for part in parts[:-1]:
                    next_dfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | O_NOFOLLOW, dir_fd=dfd)
                    os.close(dfd)
                    dfd = next_dfd
                # Resolve every component against pinned directories, never a mutable path.
                fd = os.open(parts[-1], os.O_RDONLY | O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            else:
                if not _icinde_mi(os.path.realpath(os.path.dirname(path)), ws_real):
                    raise OSError("Config parent redirects outside the workspace")
                fd = os.open(path, os.O_RDONLY | O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            return {}
        try:
            # Rejects hardlinks (st_nlink) and an fd that no longer sits at `path`.
            dogrula_kimlik(fd, path)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("Config must be a regular file")
            ident = (info.st_dev, info.st_ino)
            original_bytes = b""
            if info.st_size <= _MAX_CONFIG_BYTES:
                with os.fdopen(fd, "rb") as f:
                    fd = -1
                    original_bytes = f.read(_MAX_CONFIG_BYTES + 1)
        finally:
            if fd != -1:
                os.close(fd)
                fd = -1

        if info.st_size <= _MAX_CONFIG_BYTES and len(original_bytes) <= _MAX_CONFIG_BYTES:
            try:
                original_text = original_bytes.decode("utf-8-sig")
                if not original_text.strip():
                    return {}
                existing = json.loads(original_text)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
                pass
            else:
                if existing is None:
                    return {}
                if isinstance(existing, dict):
                    return existing

        backup_path = _move_aside(parts, rel_path, dfd, path, ident)
        if backup_path is _RETRY or backup_path is None:
            return backup_path
        ensure_gitignored(workspace, [backup_path])
        logger.warning("[config-read] Unusable JSON object in %s; original moved to %s.",
                       path, os.path.join(workspace, backup_path))
        return {}
    except OSError:
        logger.error("[config-read] Cannot read or rename %s; config left unchanged.", path)
        return None
    finally:
        if fd != -1:
            os.close(fd)
        if dfd != -1:
            os.close(dfd)


def read_user_json_config(workspace: str, rel_path: str) -> Optional[dict]:
    """Read a user config, renaming unusable regular files before replacement."""
    ws_real = os.path.realpath(workspace)
    normalized_path = rel_path.replace(os.altsep, os.sep) if os.altsep else rel_path
    parts = normalized_path.split(os.sep)
    if (os.path.isabs(rel_path) or os.path.splitdrive(rel_path)[0]
            or any(part in ("", ".", "..") for part in parts)):
        logger.error("[config-read] Invalid relative config path: %s", rel_path)
        return None
    # A changed name between read and rename means another process swapped the
    # file; read once more, then give up rather than move the wrong file.
    for _ in range(2):
        result = _read_config_once(workspace, ws_real, rel_path, parts)
        if result is not _RETRY:
            return result
    logger.error("[config-read] %s changed while being read; config left unchanged.", rel_path)
    return None


def guvenli_config_yaz(workspace: str, goreli_yol: str, icerik: str,
                       *, sir_tasiyor: bool = False) -> bool:
    """Config dosyasını workspace İÇİNE, yönlendirilmeye kapalı şekilde yazar.

    Ölçülmüş açık (K4, bu makinede 2026-08-01'de yeniden üretildi, İKİ vektör de
    ayrıcalıksız): ürünün altı config yazım noktasının altısı da hedefi düz
    `open(path, "w")` ile açıyordu. Sonuç, workspace DIŞINDAKİ bir kurbanın
    ezilmesi:

        dosyanın KENDİSİ kurbana sabit bağla bağlı   → kurban EZİLDİ
        ANA DİZİN kurbanın dizinine junction'lı      → kurban EZİLDİ

    En sinsisi `.cursor/cli.json` idi: Cursor'un `Write`/`Shell` deny-list'ini
    taşıyor, dışarı yönlendirilirse dosya ezilmesi değil **politikanın sessizce
    kaybı** oluyordu — hedef workspace'e hiç uygulanmıyordu.

    Üç katmanlı savunma, üçü de gerekli:

      1. **Kapsama**: hedefin ve her ara dizinin `realpath`'i workspace'in
         İÇİNDE olmalı. Bu, ana dizin yönlendirmesini yakalar.
      2. **`O_NOFOLLOW`** (varsa): son bileşenin sembolik bağ olmasını reddeder.
      3. **Açıştan SONRA kimlik doğrulaması** (`safe_paths.dogrula_kimlik`):
         asıl koruma bu. `os.path.islink` junction'ı GÖRMÜYOR ve `O_NOFOLLOW`
         sabit bağı reddetmiyor; ayrıca kontrol-sonra-aç sırası TOCTOU yarışını
         kaybediyor. Tanıtıcının kimliğini açtıktan sonra sormak üçünü de kapatır.

    `.gitignore` yazımı bu yoldan GEÇMİYOR: onun kendi `_resolve_gitignore`
    kapsama kontrolü var ve dosya sır taşımıyor.

    Dönüş: yazıldıysa `True`. `False` dönerse sır DİSKE YAZILMAMIŞTIR — çağıran
    bunu yutmamalı, çünkü sessizce başarısız olan bir koruma hiç olmayandan
    kötüdür (bu depoda ölçülmüş bir sınıf: `harden_config_file`'ın dönüşü bir
    dönem yutuluyordu).
    """
    from safe_paths import O_NOFOLLOW, dogrula_kimlik

    try:
        ws_real = os.path.realpath(workspace)
        if not os.path.isdir(ws_real):
            logger.warning("[config-yaz] workspace yok: %s", workspace)
            return False

        hedef = os.path.join(ws_real, goreli_yol)
        ust = os.path.dirname(hedef)

        # (1) Ara dizinleri yaratırken de kapsama kontrolü: `os.makedirs`
        # `exist_ok=True` ile bir junction'ın üzerinden GEÇİYOR ve hata vermiyor
        # (ölçüldü) — yani dizin yaratmak tek başına güvenli değil.
        os.makedirs(ust, exist_ok=True)
        if not _icinde_mi(os.path.realpath(ust), ws_real):
            logger.error("[config-yaz] %s workspace dışına yönlendirilmiş; yazılmadı", ust)
            return False

        # (2) + (3) Kimlik doğrulaması — hedef VARSA, ve SALT OKUNUR açılarak.
        # Yazmaya açmıyoruz: amaç "bu ad yönlendirilmiş mi" sorusunu cevaplamak,
        # ve yönlendirilmişse dosyaya hiç dokunmadan reddetmek. Salt okunur
        # açılış, yanlışlıkla kısaltma ihtimalini de sıfırlıyor.
        if os.path.exists(hedef):
            fd = os.open(hedef, os.O_RDONLY | O_NOFOLLOW)
            try:
                dogrula_kimlik(fd, hedef)
            finally:
                os.close(fd)

        # ⚠️ İÇERİK GEÇİCİ DOSYAYA YAZILIP `os.replace` İLE TAKILIYOR.
        # Eskiden hedef kısaltılıp üzerine yazılıyordu ve denetim bunu haklı
        # olarak bulgu yazdı: `ftruncate`'ten sonraki bir disk hatası config'i
        # BOŞ bırakıyordu. `.cursor/cli.json` için bunun anlamı, K4'ün kapatmaya
        # çalıştığı şeyin aynısıydı — Write/Shell deny-list'inin sessizce
        # kaybolması. Geçici dosya + takas ile dosya ya eski hâlinde ya yeni
        # hâlinde; arada bir hâl yok.
        #
        # Geçici dosya BİLEREK aynı dizinde: `os.replace` ancak aynı dosya
        # sisteminde atomik.
        gecici_fd, gecici = tempfile.mkstemp(dir=ust, prefix=".config-", suffix=".tmp")
        try:
            # ⚠️ SIKILAŞTIRMA İÇERİKTEN ÖNCE ve GEÇİCİ DOSYAYA. Çağrı yerlerinde
            # bu sıra elle kuruluyordu ("boş yarat → icacls → yaz") ve o ilk
            # yaratma da yönlendirmeye açıktı. Ayrıca izni yola değil, birazdan
            # hedef olacak NESNENİN kendisine uyguluyoruz.
            # ⚠️ SERTLEŞTİRME `sir_tasiyor`'DAN BAĞIMSIZ OLARAK HER ZAMAN
            # DENENİR. Bayrak yalnız BAŞARISIZLIĞIN ölümcül olup olmadığını
            # söyler; "sertleştirilsin mi" sorusunu değil.
            #
            # Sebep ölçüldü (2026-08-01, bu makinede): `os.replace` KAYNAK
            # dosyanın ACL'ini taşır, hedefinkini değil. Bayrak sertleştirmeyi
            # de kapattığı sürece, önceki turda sertleştirilmiş bir config'in
            # üzerine sertleştirilmemiş bir geçici dosya takılıyor ve izin
            # DÜŞÜYORDU:
            #
            #     sir_tasiyor=True  → BURAK\\burcu:(F)
            #     sir_tasiyor=False → SYSTEM:(I)(F), Administrators:(I)(F), ...
            #
            # `(I)` = miras geri gelmiş. Ölçülmüş gerçek dizinde geri gelen ACE
            # `CodexSandboxUsers:(OI)(CI)(M)` idi (bkz. `local_token_file`).
            # Üstelik sırsız sanılan yedek yazım, kullanıcının KENDİ üçüncü-parti
            # MCP kayıtlarını (kendi `authorization` başlıklarıyla) taşıyor —
            # yani "sır yok" varsayımı çağrı yerinde de doğru değildi.
            sertlesti = harden_config_file(gecici)
            if sir_tasiyor and not sertlesti:
                logger.error(
                    "[config-yaz] %s izinleri kısıtlanamadı; sır YAZILMADI", hedef)
                return False
            if not sertlesti:
                # Ölümcül değil ama sessiz de değil: dosya dizin ACL'ini miras
                # alacak ve bu, önceki bir sertleştirmeyi geri alabilir.
                logger.warning(
                    "[config-yaz] %s sertleştirilemedi; dizin izinlerini miras "
                    "alıyor.", hedef)
            with os.fdopen(gecici_fd, "w", encoding="utf-8") as f:
                gecici_fd = -1  # fdopen sahipliği aldı; finally iki kez kapatmasın
                f.write(icerik)
            os.replace(gecici, hedef)
            gecici = ""  # takas başarılı, silinecek bir şey kalmadı
        finally:
            if gecici_fd != -1:
                os.close(gecici_fd)
            if gecici and os.path.exists(gecici):
                try:
                    os.unlink(gecici)
                except OSError:
                    pass
        return True
    except OSError as e:
        logger.error("[config-yaz] %s yazılamadı: %s", goreli_yol, e)
        return False


def _icinde_mi(yol: str, kok: str) -> bool:
    """`yol` gerçekten `kok`un altında mı? Ön ek karşılaştırması YETMEZ.

    `/a/bc` düz dize olarak `/a/b` ile başlıyor ama onun altında değil;
    `os.path.commonpath` bunu bileşen bazında doğru cevaplıyor.
    """
    return is_within(yol, kok)


def ensure_gitignored(workspace: str, entries: Iterable[str]) -> None:
    """`entries`'i workspace'in `.gitignore`'ına idempotent şekilde ekler.

    Zaten yok sayılan (geniş desen dahil) hiçbir girdi için dosyaya DOKUNULMAZ.
    Kural: dosyayı yazan nokta, girdisini de yazsın — tek bir kuruluma bağlamak
    diğer yazım noktalarını kapsam dışı bırakır, ki bugünkü boşluk
    (`.cursor/mcp.json` izleniyordu) tam olarak öyle oluştu.
    """
    try:
        # Satır sonu içeren bir girdi `--stdin` protokolünü ve dosya biçimini
        # birden bozardı; ürün sabitleri için imkânsız ama sessiz bozulma yerine
        # sessiz atlama tercih ediliyor.
        wanted = [e.strip() for e in entries
                  if e and e.strip() and "\n" not in e and "\0" not in e]
        if not wanted:
            return
        resolved = _resolve_gitignore(workspace)
        if resolved is None:
            return
        ws_real, path = resolved

        existing = _read_text(path)
        covered = _git_covered(ws_real, wanted)
        missing = [
            e for e in wanted
            if not (covered is not None and e in covered)
            and not _literal_present(existing, e)
        ]
        if missing:
            _atomic_write(path, _compose(existing, missing), ws_real)
            logger.info("[gitignore] %s: %s eklendi.", path, ", ".join(missing))

        # gitignore ZATEN takip edilen bir dosyayı geri almaz. Ürün bunu
        # kendiliğinden düzeltmiyor: kullanıcının deposunda sessizce index
        # değiştirmek (`git rm --cached`) kabul edilemez — o karar onun.
        #
        # ⚠️ İKİ REGRESYON burada kapandı (bulgu E-a, `b1204f1` ile erişilebilir
        # olmuştu):
        #
        #   1. Sorgu `missing` ile yapılıyordu. Ama `covered` girdiler —
        #      yani git'in ZATEN yok saydıkları — `missing`'e hiç girmiyor,
        #      dolayısıyla takip kontrolünden de kaçıyorlardı. Oysa asıl
        #      tehlikeli durum tam olarak bu: bir dosya hem yok sayılıyor
        #      HEM takip ediliyor olabilir ve o zaman sır depoda demektir.
        #      Sorgu artık `wanted`'ın tamamıyla yapılıyor.
        #   2. `if not missing: return` erken dönüşü, eklenecek yeni girdi
        #      olmadığında kontrole hiç ulaşılmamasına yol açıyordu — yani
        #      kurulu bir workspace'te uyarı bir daha ASLA ateşlenmiyordu.
        still_tracked = _tracked(ws_real, wanted)
        if still_tracked is None:
            # Uyarı yalnız GERÇEKTEN bir depo varken anlamlı. `.git` yoksa
            # "takip" diye bir şey de yok ve uyarı her oturumda gürültü olurdu —
            # uyarı körlüğü, bu dosyanın kaçındığı sınıfın aynısı. Depo VARKEN
            # ölçememek ise gerçek bir bilinmezlik ve sessiz geçilemez.
            if os.path.exists(os.path.join(ws_real, ".git")):
                logger.warning(
                    "[gitignore] %s: takip durumu ÖLÇÜLEMEDİ (git çalıştırılamadı). "
                    "Girdiler depoda takipli olabilir; kontrol edin: git ls-files",
                    path,
                )
        elif still_tracked:
            logger.warning(
                "[gitignore] %s git tarafından ZATEN takip ediliyor; gitignore "
                "bunu geri almaz. Sırrın depoya girmemesi için: "
                "git rm --cached %s",
                ", ".join(still_tracked), " ".join(still_tracked),
            )
    except Exception as e:
        # Bilerek geniş: gitignore yazamamak oturum açılışını kırmamalı.
        # Sohbet, gitignore'suz da doğru çalışır; tersi doğru değil.
        logger.warning("[gitignore] %s için girdi yazılamadı: %s", workspace, e)


def remove_gitignore_block(workspace: str) -> None:
    """Ürünün eklediği etiketli bloğu kaldırır; kullanıcının satırlarına dokunmaz.

    Temizlik ucu zorunlu: bir şey yaratan her adımın onu silen bir adımı olmalı,
    yoksa kullanıcı ürünü kaldırdığında deposunda sahibi belirsiz satırlar kalır.
    """
    try:
        resolved = _resolve_gitignore(workspace)
        if resolved is None:
            return
        ws_real, path = resolved
        existing = _read_text(path)
        if BLOCK_BEGIN not in existing:
            return

        out, inside = [], False
        for line in existing.splitlines(keepends=True):
            stripped = line.strip()
            if stripped == BLOCK_BEGIN:
                inside = True
                continue
            if inside:
                if stripped == BLOCK_END:
                    inside = False
                continue
            out.append(line)
        # Blok yanındaki nefes payıyla birlikte gitsin; aksi hâlde her
        # ekle/kaldır turunda dosyada bir boş satır birikirdi. Blok BAŞTA
        # olduğu için asıl birikme noktası baştaki boşluk — sondaki de
        # temizleniyor, çünkü blok eski bir dosyada sonda kalmış olabilir.
        while out and not out[0].strip():
            out.pop(0)
        while out and not out[-1].strip():
            out.pop()
        cleaned = "".join(out)
        if cleaned and not cleaned.endswith("\n"):
            cleaned += "\n"
        _atomic_write(path, cleaned, ws_real)
        logger.info("[gitignore] %s: ürün bloğu kaldırıldı.", path)
    except Exception as e:
        logger.warning("[gitignore] %s bloğu kaldırılamadı: %s", workspace, e)
