"""Test takımı için ortak fixture'lar.

Neden burası var: `auth_utils._check_token` 2026-07-27 denetiminden sonra
fail-CLOSED oldu — `LOCAL_APP_TOKEN` yoksa istek 503 ile reddediliyor. Testler
o güne kadar fail-OPEN davranışa **örtük olarak** bağlıydı; hiçbiri token
kurmuyordu ve yine de korumalı uçlara vurabiliyordu.

Bağımlılığı silmek yerine görünür kılıyoruz: test ortamı token'sız çalışmayı
burada AÇIKÇA seçiyor. Kapının kendisini sınayan bir test yazılırsa bu değişkeni
monkeypatch ile kaldırıp 503'ü doğrulayabilir.
"""

import errno
import os
import stat
import subprocess
import tempfile

import sys

import pytest

# `app/` sys.path'e burada ekleniyor. Mevcut test dosyalarının her biri bunu
# kendisi yapıyordu; sonuç, satırı unutan bir dosyanın YALNIZCA başka bir test
# ondan önce toplandığında çalışmasıydı — yani toplama sırasına bağlı, tek
# başına koşturulunca ModuleNotFoundError veren testler. Burada bir kez yapmak
# o bağımlılığı kaldırıyor; dosyalardaki mevcut satırlar zararsız (idempotent).
_APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP not in sys.path:
    sys.path.insert(0, _APP)


@pytest.fixture(autouse=True)
def _allow_tokenless_local_api(monkeypatch):
    monkeypatch.setenv("UNITYAI_ALLOW_NO_TOKEN", "1")
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)
    yield


@pytest.fixture(autouse=True)
def _approval_mode_starts_in_step():
    # Module-level state: a test that switches to auto must not leak it.
    from agentic import approval_mode
    approval_mode._reset_for_tests()
    yield
    approval_mode._reset_for_tests()


# ── Platform yeteneği kapıları ───────────────────────────────────────────────
#
# Sorun (ölçüldü 30 Tem 2026, Windows 11 / Python 3.13): aynı ağaç macOS'ta 824
# testle yeşilken bu makinede 30 fail + 40 error veriyordu. Sebebin BÜYÜK kısmı
# ürün değil ORTAM: iki POSIX ilkeli Windows'ta yok ve testler onları örtük
# olarak varsayıyordu —
#
#   • sembolik bağ kurmak `SeCreateSymbolicLinkPrivilege` istiyor
#     (yönetici ya da Geliştirici Modu); yoksa `os.symlink` → WinError 1314
#   • POSIX izin bitleri yok: `os.chmod(p, 0o600)` sonrası `st_mode` hâlâ `0o666`
#
# Atlamak korumayı kaybetmek DEĞİL — CI Linux'ta koşuyor ve bu iddialar orada
# gerçek. Kırmızı bırakmak ise baseline'ı okunmaz yapıyor: okunmayan bir
# baseline'da hiç kimse kendi işinin kapısını ölçemez, ve "42 kırmızının hangisi
# benim" sorusu cevapsız kalır.
#
# Koşullar platform ADINA değil YETENEĞE bakıyor: Geliştirici Modu açık bir
# Windows'ta bağ kurulabiliyor ve o makinede testin koşması GEREKİR. `os.name`
# sabitlemek ölçülebilir bir şeyi varsayıma çevirirdi.


# ⚠️ ÜÇ DURUMLU, iki değil. Ölçülmüş arıza (30 Tem 2026 denetimi, bulgu E-c):
# eski sürüm `except OSError: return False` diyordu, yani **ilgisiz** bir I/O
# hatası da "yetenek yok" cevabına dönüşüyordu. Diski dolu bir makinede
# (`ENOSPC`) ya da geçici bir ACL reddinde (`EACCES`) güvenlik testleri sessizce
# atlanıyor ve koşu YEŞİL raporlanıyordu — sahte yeşilin en pahalı biçimi.
#
# Ayrım şu: "bu platform bunu yapamaz" ile "ölçemedim" aynı şey değil.
#   True  → yetenek var
#   False → yetenek YOK, sebebi ölçüldü      → test atlanır (meşru)
#   None  → ÖLÇÜLEMEDİ                        → test ATLANMAZ, FAIL eder
#
# `False` yalnız beyaz listedeki sebeplerle dönüyor; beklenmedik her hata
# `None`. Ters tasarım (beklenmedik hatayı "yetenek yok" saymak) tam olarak
# E-c bulgusunun kendisiydi.


def _symlink_olc() -> "tuple[bool | None, str]":
    with tempfile.TemporaryDirectory() as d:
        try:
            os.symlink(os.path.join(d, "hedef"), os.path.join(d, "bag"))
            return True, ""
        except (NotImplementedError, AttributeError) as e:
            return False, f"bu platformda sembolik bağ yok ({type(e).__name__})"
        except OSError as e:
            # ERROR_PRIVILEGE_NOT_HELD (1314): Windows'ta ayrıcalıksız kullanıcı.
            # EPERM: POSIX'te aynı anlam. İkisi de gerçek yetenek yokluğu.
            if getattr(e, "winerror", None) == 1314 or e.errno == errno.EPERM:
                sebep = f"winerror={getattr(e, 'winerror', None)}" if getattr(
                    e, "winerror", None
                ) else f"errno={errno.errorcode.get(e.errno, e.errno)}"
                return False, f"sembolik bağ kurma ayrıcalığı yok — ölçüldü: {sebep}"
            kod = errno.errorcode.get(e.errno, e.errno)
            return None, (
                f"symlink yeteneği ÖLÇÜLEMEDİ: beklenmedik {type(e).__name__} "
                f"errno={kod} ({e.strerror}). Bu bir yetenek yokluğu değil, "
                f"ölçümün kendisinin başarısızlığı — test atlanmıyor."
            )


def _izin_bitleri_olc() -> "tuple[bool | None, str]":
    fd, p = tempfile.mkstemp()
    os.close(fd)
    try:
        os.chmod(p, 0o600)
    except OSError as e:
        # Kendi yarattığımız geçici dosyada chmod'un patlaması "POSIX izin
        # bitleri yok" demek DEĞİL; anormal bir durumdur ve ölçüm sayılmaz.
        kod = errno.errorcode.get(e.errno, e.errno)
        return None, (
            f"izin biti yeteneği ÖLÇÜLEMEDİ: chmod {type(e).__name__} "
            f"errno={kod} ({e.strerror}) — test atlanmıyor."
        )
    else:
        # chmod başarılı ama bitler tutmuyorsa: Windows'ın gerçek davranışı.
        etkin = stat.S_IMODE(os.stat(p).st_mode)
        if etkin == 0o600:
            return True, ""
        return False, (
            f"POSIX izin bitleri etkisiz — ölçüldü: chmod(0o600) sonrası "
            f"st_mode=0o{etkin:o}"
        )
    finally:
        os.remove(p)


# ⚠️ Sabit bağ ve junction AYRI kapılar, symlink'in altına toplanamaz.
# Ölçüldü (30 Tem 2026, bu makine, `IsUserAnAdmin()==0`):
#
#     os.symlink   → RED, winerror 1314 (ayrıcalık gerekiyor)
#     os.link      → OK,  st_nlink=2
#     mklink /J    → OK
#     os.path.islink(junction) → False   ← tespitin kaçtığı yer
#
# Yani "bağ testleri Windows'ta koşamaz" YANLIŞ bir genelleme: koşamayan yalnız
# sembolik bağ. Üçünü tek işarete bağlamak, ayrıcalıksız kurulabilen iki bağ
# türünün testlerini de sessizce atlatırdı — ve tam o iki tür `islink()`
# kontrolünü delen türler.


def _hardlink_olc() -> "tuple[bool | None, str]":
    with tempfile.TemporaryDirectory() as d:
        kaynak = os.path.join(d, "kaynak")
        with open(kaynak, "w", encoding="utf-8") as f:
            f.write("x")
        try:
            os.link(kaynak, os.path.join(d, "bag"))
            return True, ""
        except (NotImplementedError, AttributeError) as e:
            return False, f"bu platformda sabit bağ yok ({type(e).__name__})"
        except OSError as e:
            kod = errno.errorcode.get(e.errno, e.errno)
            # EPERM/EACCES: izin yok. EXDEV: farklı birim. ENOSYS/EMLINK: dosya
            # sistemi desteklemiyor. Hepsi gerçek yetenek yokluğu.
            if e.errno in (errno.EPERM, errno.EACCES, errno.EXDEV, errno.ENOSYS):
                return False, f"sabit bağ kurulamıyor — ölçüldü: errno={kod}"
            return None, (
                f"sabit bağ yeteneği ÖLÇÜLEMEDİ: beklenmedik {type(e).__name__} "
                f"errno={kod} ({e.strerror}) — test atlanmıyor."
            )


def _windows_mu() -> bool:
    """Bu makine Windows mu?

    Neden `os.name` yerinde okunmuyor: aşağıdaki sınıflandırma mantığı (hangi
    `mklink` çıkışı "yetenek yok", hangisi "ölçülemedi") platformdan bağımsız
    saf mantık, ama önündeki erken çıkış onu POSIX'te ulaşılamaz yapıyor. CI
    `ubuntu-latest`'te koştuğu için o mantık orada hiç ölçülmüyordu — ve tam o
    mantık doğrulama turunun üç durumlu kural ihlalini bulduğu yer. Dikiş,
    testin `mklink`'i taklit ederek sınıflandırmayı her makinede sürmesini
    sağlıyor; taklit edilen şey saf mantık olduğu için ölçüm gerçek.
    """
    return os.name == "nt"


# Ayrıcalık ya da dosya sistemi desteği yokluğunun SAYISAL karşılıkları.
# Metin yerine bunlara bakmanın sebebi doğrulama turu bulgusu (30 Tem 2026):
# sınıflandırma `mklink`'in İngilizce/Türkçe çıktısını eşliyordu, yani Almanca
# ya da Fransızca bir Windows'ta o metinler hiç gelmeyecek ve meşru bir yetenek
# yokluğu "ÖLÇÜLEMEDİ"ye düşüp testleri FAIL ettirecekti. Sayı çevrilmiyor.
_JUNCTION_YETENEK_YOK = frozenset({
    1314,  # ERROR_PRIVILEGE_NOT_HELD
    50,    # ERROR_NOT_SUPPORTED — birim reparse point taşımıyor (exFAT/FAT32)
    1,     # ERROR_INVALID_FUNCTION — aynı sınıf, bazı birimler bunu döndürüyor
})


def _junction_olusturucu():
    """Dilden bağımsız junction kurucusu; yoksa ``None``.

    Ölçüldü (31 Tem 2026, bu makine, AYRICALIKSIZ): `_winapi.CreateJunction`
    var, junction'ı kurdu (`os.path.islink` → `False`, `realpath` farklı) ve
    hatalarını sayısal `winerror` ile bildirdi (hatalı yol → `winerror=3`).
    Yani `mklink` metnini ayrıştırmaya gerek yok.

    Ayrı bir fonksiyon olmasının sebebi test edilebilirlik: hem yokluğu
    (son çare dalı) hem de hata sınıflandırması taklit edilebilsin.
    """
    try:
        import _winapi
    except ImportError:
        return None
    return getattr(_winapi, "CreateJunction", None)


def _junction_olc() -> "tuple[bool | None, str]":
    if not _windows_mu():
        return False, "junction yalnız Windows/NTFS kavramı"
    olustur = _junction_olusturucu()
    if olustur is None:
        return _junction_olc_mklink()
    with tempfile.TemporaryDirectory() as d:
        hedef = os.path.join(d, "hedef")
        os.mkdir(hedef)
        try:
            olustur(hedef, os.path.join(d, "bag"))
            return True, ""
        except OSError as e:
            we = getattr(e, "winerror", None)
            if we in _JUNCTION_YETENEK_YOK:
                return False, f"junction kurulamıyor — ölçüldü: winerror={we}"
            return None, (
                f"junction yeteneği ÖLÇÜLEMEDİ: CreateJunction winerror={we} "
                f"errno={e.errno} ({e}) — bu bir yetenek yokluğu değil, "
                "test atlanmıyor."
            )


def _junction_olc_mklink() -> "tuple[bool | None, str]":
    """Son çare: `_winapi.CreateJunction` yoksa `mklink` metnine bakılır.

    ⚠️ Bu dal ADLANDIRILMIŞ bir varsayıma dayanıyor: *"makinenin Windows dili
    İngilizce ya da Türkçe"*. Başka bir dilde meşru yetenek yokluğu `None`'a
    düşer ve test FAIL eder — fail-CLOSED, ama yanlış sebeple kırmızı.
    Varsayımı kaldıramıyoruz çünkü `mklink` hata kodunu sayı olarak vermiyor;
    onun yerine dalın kendisi son çareye indirildi. CPython'da `CreateJunction`
    3.5'ten beri var, yani bu dala düşen bir kurulum beklenmiyor.
    """
    with tempfile.TemporaryDirectory() as d:
        hedef = os.path.join(d, "hedef")
        os.mkdir(hedef)
        try:
            r = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.path.join(d, "bag"), hedef],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return None, (
                f"junction yeteneği ÖLÇÜLEMEDİ: mklink çalıştırılamadı "
                f"({type(e).__name__}: {e}) — test atlanmıyor."
            )
        if r.returncode == 0:
            return True, ""
        # ⚠️ Her sıfır olmayan rc "yetenek yok" DEĞİL (doğrulama turu bulgusu,
        # 30 Tem 2026). Bu fonksiyon üç durumlu kuralı kuran commit'te yazıldı
        # ve tam o kuralı ihlal ediyordu: disk dolu, geçici bir komut hatası ya
        # da erişim reddi de `False`'a düşüyordu — yani E-c'nin kapattığı sahte
        # yeşil, yeni kapının içinde yeniden açılmıştı.
        #
        # `mklink` ayrıcalık/destek yokluğunu bu iki metinle bildiriyor; başka
        # her şey ölçüm başarısızlığı sayılıyor ve test ATLANMIYOR.
        ciktı = ((r.stderr or "") + (r.stdout or "")).strip()
        yetenek_yok = any(
            im in ciktı.lower()
            for im in ("privilege", "ayrıcalık", "not supported", "desteklenmiyor")
        )
        if yetenek_yok:
            return False, f"junction kurulamıyor — ölçüldü: {ciktı[:120]}"
        return None, (
            f"junction yeteneği ÖLÇÜLEMEDİ: mklink /J rc={r.returncode} "
            f"{ciktı[:120]} — bu bir yetenek yokluğu değil, test atlanmıyor."
        )


# Bir kez ölçülüp saklanıyor: her test için yeniden dosya yaratmak toplama
# süresini gereksiz uzatır ve yetenek koşu ortasında değişmiyor.
SYMLINK_VAR, SYMLINK_GEREKCE = _symlink_olc()
IZIN_BITLERI_VAR, IZIN_BITLERI_GEREKCE = _izin_bitleri_olc()
HARDLINK_VAR, HARDLINK_GEREKCE = _hardlink_olc()
JUNCTION_VAR, JUNCTION_GEREKCE = _junction_olc()


# Bu iki ad denetim probe'larının giriş noktası (`probe_e1_capability_gates_
# false_negative.py` varlıklarını şart koşuyor, yoksa "probe invalid" diyor).
# Ölçümü yeniden koşturuyorlar; saklanan değeri değil.
def _symlink_kurulabiliyor_mu():
    return _symlink_olc()[0]


def _izin_bitleri_anlamli_mi():
    return _izin_bitleri_olc()[0]


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "baglar_gerekli: sembolik bağ KURULABİLEN bir platform gerektirir",
    )
    config.addinivalue_line(
        "markers",
        "izin_bitleri_gerekli: POSIX izin bitleri (chmod'un etkili olması) gerektirir",
    )
    config.addinivalue_line(
        "markers",
        "sabit_bag_gerekli: sabit bağ (hardlink) KURULABİLEN bir platform gerektirir",
    )
    config.addinivalue_line(
        "markers",
        "junction_gerekli: NTFS junction (mklink /J) KURULABİLEN bir platform gerektirir",
    )


def _kapilar():
    """(işaret adı, yetenek, gerekçe) — modül değişkenleri ÇAĞRI ANINDA okunuyor.

    Sabit bir sözlük değil, çünkü denetim probe'ları `conftest.SYMLINK_VAR`'ı
    dışarıdan yeniden atayıp kapıyı o değerle sınıyor; modül yüklenirken
    dondurulmuş bir kopya o enjeksiyonu görmezdi.
    """
    return (
        ("baglar_gerekli", SYMLINK_VAR, SYMLINK_GEREKCE),
        ("izin_bitleri_gerekli", IZIN_BITLERI_VAR, IZIN_BITLERI_GEREKCE),
        ("sabit_bag_gerekli", HARDLINK_VAR, HARDLINK_GEREKCE),
        ("junction_gerekli", JUNCTION_VAR, JUNCTION_GEREKCE),
    )


def pytest_collection_modifyitems(config, items):
    for item in items:
        for isaret, yetenek, gerekce in _kapilar():
            if isaret not in item.keywords:
                continue
            # `is False` bilerek: `None` (ölçülemedi) buraya DÜŞMEMELİ.
            # Eski kod `not SYMLINK_VAR` diyordu ve None'ı da atlamaya çevirirdi.
            if yetenek is False:
                item.add_marker(pytest.mark.skip(reason=gerekce))


def pytest_runtest_setup(item):
    """Yeteneği ölçülemeyen test ATLANMAZ, FAIL eder.

    Gerekçe (bulgu E-c): atlama sessizdir ve koşuyu yeşil bırakır. Ölçüm
    başarısızlığı bir platform gerçeği değil, bir arızadır — görünmesi gerekir.
    Atlamak, "diski dolu makinede güvenlik testleri kendiliğinden kapanır"
    demekle aynı şey.
    """
    for isaret, yetenek, gerekce in _kapilar():
        if yetenek is None and isaret in item.keywords:
            pytest.fail(gerekce, pytrace=False)
