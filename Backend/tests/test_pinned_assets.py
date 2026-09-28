"""Sabitlenmiş indirme kütüğünün (scripts/pinned_assets.json) kendi sözleşmesi.

Hangi arızadan doğdu: 2026-07-28 denetiminde bu depoda 13 ayrı yerden yürütülebilir
içerik indirildiği ve HİÇBİRİNDE checksum/imza/boyut kontrolü olmadığı ölçüldü
(grep: `sha256|sha512|checksum|gpg|--verify|signature|hashlib` → sıfır sonuç).
İndirilenlerin hepsi installer'a giriyor ve son kullanıcıda çalıştırılıyor.

Bu dosya kütüğün KENDİSİNİ koruyor, indirme mekaniğini değil (o
`test_fetch_omnisharp_cache.py`'de). Buradaki testlerin ortak derdi tek bir şey:
**bir doğrulama kapısının sessizce etkisiz hale gelmesi.** Bu depoda kapılar üç
kez tam da böyle kayboldu — damga doğruyken ağaç eksikti, `csproj_count` üretilip
tüketilmedi, `didOpen` gönderilip `didChange` gönderilmedi. Ortak şekil:
**birbiriyle uyuşması gereken iki yer, ve uyuşmuyorlar.**

En kritik test aşağıdaki `TestCSharpTablosuKutukleAyrisamaz`: Roslyn özetleri hem
JSON'da hem `RoslynInstaller.cs`'de yazılı (C# kodu UPM paketiyle dağıtılıyor ve
JSON'u göremiyor). İkisinin ayrışmasını hatırlamaya bağlamak, yukarıdaki arıza
sınıfını bilerek yeniden üretmek olurdu.
"""
import importlib.util
import io
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MANIFEST = os.path.join(_ROOT, "scripts", "pinned_assets.json")
_ROSLYN_CS = os.path.join(
    _ROOT, "unity-mcp", "MCPForUnity", "Editor", "Setup", "RoslynInstaller.cs"
)


@pytest.fixture(scope="module")
def pinned():
    """scripts/ bir paket değil — dosyayı yolundan yükle. Bulunamaması da bir
    bulgudur, o yüzden atlanmıyor, patlıyor."""
    path = os.path.join(_ROOT, "scripts", "pinned_assets.py")
    assert os.path.isfile(path), f"beklenen modül yok: {path}"
    spec = importlib.util.spec_from_file_location("pinned_assets", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def entries():
    with open(_MANIFEST, encoding="utf-8") as f:
        return json.load(f)["assets"]


class TestKutukKendiBicimineUyuyor:
    """Biçim kapısı. Bozuk bir girdi çalışma anında değil, burada yakalanmalı:
    build sırasında keşfedilen bir yazım hatası, CI'da tag atıldıktan sonra
    görünür ve o noktada geri dönmek pahalıdır."""

    def test_every_asset_carries_a_url_a_digest_and_a_size(self, entries):
        for key, e in entries.items():
            assert e.get("url", "").startswith("https://"), f"{key}: https URL yok"
            assert isinstance(e.get("size"), int) and e["size"] > 0, f"{key}: boyut yok"
            assert ":" in e.get("digest", ""), f"{key}: 'algo:hex' biçiminde özet yok"

    def test_every_digest_uses_a_collision_resistant_algorithm(self, entries):
        """MD5 ve SHA-1 çakışmaya karşı kırık. johnvansickle YALNIZ MD5 yayınlıyor;
        oradaki özet bilerek elle SHA-256 hesaplandı. Bu test, birinin kolaylık
        olsun diye yayınlanmış MD5'i kütüğe koymasını engelliyor."""
        for key, e in entries.items():
            algo, _, hexpart = e["digest"].partition(":")
            assert algo in ("sha256", "sha512"), f"{key}: kabul edilmeyen algoritma {algo}"
            expected_len = 64 if algo == "sha256" else 128
            assert len(hexpart) == expected_len, f"{key}: {algo} uzunluğu yanlış"
            assert re.fullmatch(r"[0-9a-f]+", hexpart), f"{key}: hex değil ya da büyük harf"

    def test_no_asset_is_pinned_to_a_moving_target(self, entries):
        """Sabitlemenin ta kendisi. `latest` içeren bir URL'in özeti sabitlenemez —
        yayıncı her yayında baytları değiştirir ve doğrulama ilk gün kırılır.
        Bu test o hatayı kütüğe girer girmez yakalıyor.

        ⚠️ `ffmpeg/win` bir istisna DEĞİL: BtbN'in `latest` tag'i her build'de
        silinip yeniden yaratılıyor, o yüzden bilerek ay-sonu autobuild tag'i
        seçildi. Bu test o seçimi bağlıyor."""
        for key, e in entries.items():
            assert "/latest/" not in e["url"], f"{key}: sabitlenmemiş 'latest' adresi"
            assert not e["url"].endswith("/latest"), f"{key}: sabitlenmemiş 'latest' adresi"

    def test_no_digest_is_left_as_a_placeholder(self, entries):
        """Yer tutucu bir özet, doğrulama kapısını açık bırakmanın en sessiz yolu."""
        for key, e in entries.items():
            assert "todo" not in e["digest"].lower(), f"{key}: doldurulmamış özet"

    def test_a_hand_computed_digest_says_so_and_carries_its_date(self, entries):
        """İki ffmpeg'de yayıncı özet YAYINLAMIYOR, yani özeti hesaplayan kişi güven
        kökü oluyor. Bunun kütükte görünür olması şart: gelecekte okuyan biri
        değerin vendor'dan geldiğini SANARSA, aslında olmayan bir garantiye
        güvenmiş olur."""
        for key, e in entries.items():
            kaynak = e.get("kaynak", "")
            assert kaynak, f"{key}: özetin kaynağı yazılmamış"
            if kaynak.startswith("yerel"):
                assert re.fullmatch(r"yerel:\d{4}-\d{2}-\d{2}", kaynak), (
                    f"{key}: elle hesaplanan özet tarihsiz — 'yerel:YYYY-AA-GG' olmalı"
                )


class TestIndirmeKaynagiAllowlistDisinaCikamaz:
    """Denetim bulgusu (missing-download-origin-policy): kütük bu deponun bütün
    build-zamanı indirmelerinin güven kökü, ama HERHANGİ bir https adresi kabul
    ediliyordu. Bir URL'i ve özetini BİRLİKTE değiştiren bir düzenleme doğrulamayı
    sorunsuz geçer — çünkü doğrulama "baytlar kütükle uyuşuyor mu" diye sorar,
    "kütük doğru mu" diye değil. Tek engel diff'i okuyan insandı; yani bir kapı
    değil, bir süreç. Bu depoda süreçle tutulan hiçbir kural iki haftadan uzun
    yaşamadı (ölçüldü: 6 lane'in 1'inde uygulandı).

    Host allowlist'i bunu kapıya çeviriyor: özet değiştirmek hâlâ diff'te
    görünmeyebilir, ama indirmenin NEREDEN geldiğini değiştirmek artık bu dosyaya
    dokunmadan mümkün değil. Yeni bir host eklemek BİLİNÇLİ bir karardır ve
    burada, görünür biçimde verilmelidir — bir veri dosyasında sessizce değil.
    Liste bugün fiilen kullanılan hostlardan türetildi (2026-07-28, 19 girdi).

    Kapının kendisi teste konuldu, `pinned_assets.py`'ye değil: modül hem indirme
    hem doğrulama yolunda koşuyor, oraya konan bir liste build makinesinde
    kırılırdı; buradaki ise kütük düzenlenirken, CI'da, insan diff'inden ÖNCE
    kırılır."""

    # Her satır bir yayıncı. Silmeden/eklemeden önce: bu host'un yayınladığı
    # baytlar son kullanıcıda çalıştırılıyor.
    _IZINLI_HOSTLAR = frozenset({
        "github.com",                    # omnisharp, uv, yt-dlp, ffmpeg/win (BtbN)
        "api.nuget.org",                 # roslyn/* paketleri
        "builds.dotnet.microsoft.com",   # dotnet-sdk/*
        "evermeet.cx",                   # ffmpeg/macos (statik, checksum yayınlamıyor)
        "johnvansickle.com",             # ffmpeg/linux (statik)
        "huggingface.co",                # whisper-model/* (dictation, LFS oid published)
        "sdk.lunarg.com",                # vulkan-sdk/* (build-time only, sha published)
    })

    @staticmethod
    def _host(url: str) -> str:
        # `netloc` DEĞİL `hostname`: netloc userinfo ve port taşıyabiliyor
        # ('https://github.com@baska.example/x' → netloc 'github.com@baska.example',
        # hostname 'baska.example'). hostname ayrıca küçük harfe indiriyor, yani
        # 'GitHub.com' ile kaçamak da kapanıyor.
        return urllib.parse.urlsplit(url).hostname or ""

    def test_every_asset_url_points_at_an_allowlisted_host(self, entries):
        for key, e in entries.items():
            host = self._host(e["url"])
            assert host in self._IZINLI_HOSTLAR, (
                f"{key}: '{host}' izinli indirme kaynakları arasında değil.\n"
                f"  Bu bilerek yapılan bir değişiklikse host'u bu testteki "
                f"_IZINLI_HOSTLAR listesine ELLE ekle — kararın kütükte değil "
                f"burada, görünür biçimde verilmesi isteniyor."
            )

    def test_the_allowlist_names_no_host_that_is_no_longer_used(self, entries):
        """Kapının çok GENİŞ olmadığı yönü — bu deponun en pahalı dersi tam buydu:
        bütün testler kapının dar olmadığını sınıyordu, hiçbiri geniş olmadığını
        sınamıyordu. Artık kullanılmayan bir host listede kalırsa, allowlist
        zamanla 'fiilen kullanılanlar'dan 'bir zamanlar kullanılmışlar'a döner ve
        izin verdiği yüzey sessizce büyür."""
        kullanilan = {self._host(e["url"]) for e in entries.values()}
        assert kullanilan, "kütükte hiç asset yok — bu testler boşa koşuyor"
        artakalan = self._IZINLI_HOSTLAR - kullanilan
        assert not artakalan, (
            f"allowlist'te artık hiçbir asset'in kullanmadığı host(lar) var: "
            f"{sorted(artakalan)} — bir asset kaldırıldıysa host'u da kaldır"
        )


class TestDogrulamaIkiYondenDeCalisiyor:
    """Kapı iki yönden de sınanmalı. Bu deponun en pahalı dersi: bütün testler
    kapının çok DAR olmadığını sınıyordu, hiçbiri çok GENİŞ olmadığını
    sınamıyordu — ve üç ayrı arıza tam oradan çıktı."""

    def test_bytes_matching_the_pin_are_accepted(self, pinned, entries, monkeypatch):
        """Çok DAR olmama yönü: doğru baytlar reddedilirse ürün hiç kurulamaz."""
        data = b"tam olarak bu baytlar"
        key = "omnisharp/osx-arm64"
        sahte = dict(entries[key])
        sahte["digest"] = "sha256:" + pinned.digest_of(data, "sha256")
        sahte["size"] = len(data)
        monkeypatch.setattr(pinned, "asset", lambda k: sahte)
        pinned.verify_bytes(data, key)      # istisna atmamalı

    def test_a_single_flipped_byte_is_refused(self, pinned, entries, monkeypatch):
        """Çok GENİŞ olmama yönü, en dar hali: tek bayt değişimi bile geçmemeli."""
        data = b"tam olarak bu baytlar"
        key = "omnisharp/osx-arm64"
        sahte = dict(entries[key])
        sahte["digest"] = "sha256:" + pinned.digest_of(data, "sha256")
        sahte["size"] = len(data)
        monkeypatch.setattr(pinned, "asset", lambda k: sahte)
        with pytest.raises(pinned.IntegrityError):
            pinned.verify_bytes(data.replace(b"bu", b"su"), key)

    def test_an_unknown_key_raises_instead_of_silently_passing(self, pinned):
        """Bir kabuk betiği anahtarı yanlış yazarsa doğrulama ATLANMIŞ olurdu ve
        build yeşil kalırdı — 'sessiz no-op' sınıfının tam kendisi."""
        with pytest.raises(KeyError):
            pinned.asset("boyle/bir/anahtar/yok")

    def test_the_error_names_the_asset_and_its_source(self, pinned, entries, monkeypatch):
        """Uyuşmazlık anında teşhis edilebilirlik: mesaj hangi asset'in, hangi
        adresten geldiğini söylemezse, 19 girdilik bir kütükte arıza aramak
        gereksizce pahalı olur."""
        key = "omnisharp/osx-arm64"
        monkeypatch.setattr(pinned, "asset", lambda k: entries[key])
        with pytest.raises(pinned.IntegrityError) as hata:
            pinned.verify_bytes(b"yanlis", key)
        assert entries[key]["url"] in str(hata.value)

    def test_a_truncated_download_is_named_as_a_size_problem(
        self, pinned, entries, monkeypatch
    ):
        """Yarım inen dosyada asıl bilgi boyutta: 'iki hex uyuşmadı' demek yerine
        'X bayt bekleniyordu, Y geldi' demek teşhisi anında bitiriyor."""
        key = "omnisharp/osx-arm64"
        monkeypatch.setattr(pinned, "asset", lambda k: entries[key])
        with pytest.raises(pinned.IntegrityError, match="boyut"):
            pinned.verify_bytes(b"yarim", key)


class TestCSharpTablosuKutukleAyrisamaz:
    """Roslyn özetleri İKİ yerde yazılı ve bu kaçınılmaz: `RoslynInstaller.cs` UPM
    paketiyle son kullanıcıya gidiyor, `pinned_assets.json`'u göremiyor.

    Kaçınılmaz olan şey, denetimsiz olmak zorunda değil. Bu sınıf ayrışmayı
    hatırlamaya değil bir kapıya bağlıyor — çünkü bu depoda hatırlamaya bağlı
    hiçbir kural iki haftadan uzun yaşamadı (ölçüldü: 6 lane'in 1'inde uygulandı).
    """

    @staticmethod
    def _csharp_entries() -> dict:
        with open(_ROSLYN_CS, encoding="utf-8") as f:
            src = f.read()
        satirlar = re.findall(
            r'\(\s*"([\w.]+)"\s*,\s*"([\d.]+)"\s*,\s*"[^"]*"\s*,\s*"[^"]*"\s*,\s*"([^"]*)"\s*\)',
            src,
        )
        return {pid: (ver, digest) for pid, ver, digest in satirlar}

    def test_the_csharp_table_was_actually_parsed(self):
        """Kendini sınayan test: regex tutmazsa aşağıdaki testler BOŞ küme üzerinde
        koşar ve sessizce geçer — yani kapı, kırılmadan kaybolur."""
        assert len(self._csharp_entries()) == 4, (
            "RoslynInstaller.cs'deki tablo ayrıştırılamadı; tablo biçimi değiştiyse "
            "bu testin regex'i de güncellenmeli — sessizce boş geçmesin"
        )

    def test_every_csharp_digest_matches_the_manifest(self, entries):
        """Ayrışmayı yakalayan asıl satır."""
        for pid, (ver, digest) in self._csharp_entries().items():
            key = f"roslyn/{pid}"
            assert key in entries, f"C# tablosunda olan '{pid}' kütükte yok"
            beklenen = entries[key]["digest"].partition(":")[2]
            assert digest.lower() == beklenen, (
                f"{pid}: C# tablosu ile kütük ayrışmış\n"
                f"  C#    : {digest}\n"
                f"  kütük : {beklenen}"
            )

    def test_every_csharp_version_matches_the_manifest_url(self, entries):
        """Özet tutup sürüm ayrışırsa, indirilen paket beklenenden başka olur ve
        doğrulama ilk koşuda kırılır — build makinesinde değil, KULLANICININ
        Unity'sinde. O yüzden sürüm de bağlanıyor."""
        for pid, (ver, _digest) in self._csharp_entries().items():
            url = entries[f"roslyn/{pid}"]["url"]
            assert f"/{ver}/" in url, f"{pid}: C# sürümü {ver}, kütük URL'i {url}"

    @staticmethod
    def _manifest_roslyn_keys(entries) -> set:
        return {k for k in entries if k.startswith("roslyn/")}

    def test_the_manifest_actually_contains_roslyn_rows(self, entries):
        """Kendini sınayan test, ters yön için. Anahtar öneki değişirse (örn.
        'roslyn/' → 'roslyn-') aşağıdaki test BOŞ küme üzerinde koşar ve sessizce
        geçer — yani kapı, kırılmadan kaybolur. Bu satır o sessizliği engelliyor."""
        assert len(self._manifest_roslyn_keys(entries)) == 4, (
            "kütükte beklenen sayıda roslyn/* girdisi yok; paket sayısı gerçekten "
            "değiştiyse bu sayı ve RoslynInstaller.cs tablosu birlikte güncellenmeli"
        )

    def test_every_manifest_roslyn_row_exists_in_the_csharp_table(self, entries):
        """Denetim bulgusu (one-way-drift-gate): yukarıdaki testler C# tablosunu
        gezip her satırın kütükte olduğunu sınıyordu, yani kapı TEK YÖNLÜYDÜ.
        Kütüğe bir `roslyn/*` eklenip installer'a satır eklenmediğinde döngü o
        girdiyi hiç ziyaret etmiyor ve boşluk sessiz kalıyordu — üstelik sessizliği
        kullanıcı ödüyor: paket kütükte var, Unity tarafında hiç indirilmiyor.

        Aynı arıza sınıfı bu depoda üç kez yaşandı (damga doğru/ağaç eksik,
        `csproj_count` üretilip tüketilmedi, `didOpen` var `didChange` yok):
        birbiriyle uyuşması gereken iki yer, ve yalnız bir yönü denetleniyor."""
        cs = self._csharp_entries()
        for key in sorted(self._manifest_roslyn_keys(entries)):
            pid = key.partition("/")[2]
            assert pid in cs, (
                f"kütükteki '{key}' RoslynInstaller.cs tablosunda YOK — kütüğe paket "
                f"eklenmiş ama installer'a satır eklenmemiş; bu paket hiç indirilmez"
            )

    def test_no_csharp_digest_is_left_as_a_placeholder(self):
        """`TODO-DIGEST` bilerek fail-loud bırakılmıştı; dolmadan sürüm çıkmasın."""
        for pid, (_ver, digest) in self._csharp_entries().items():
            assert re.fullmatch(r"[0-9a-fA-F]{64}", digest), (
                f"{pid}: özet doldurulmamış ya da 64 hanelik hex değil → {digest!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# `refresh` / `check` — tazeleme aracının kendi sözleşmesi
#
# Bu testler AĞA ÇIKMIYOR: `urllib.request.urlopen` monkeypatch'leniyor. Gerekçe
# ölçülmüş değil, yapısal — ağa çıkan bir test, yayıncı bir sürüm yayınladığı gün
# kırmızıya döner ve kırmızılığı ürünle ilgisiz olur; o noktada test susturulur
# ve kapı kaybolur. Bu depoda kapılar tam olarak böyle kayboldu.
# ─────────────────────────────────────────────────────────────────────────────

_HEX_A = "a" * 64
_HEX_B = "b" * 64

_UV_API = "https://api.github.com/repos/astral-sh/uv/releases?per_page=100"
_UV_TAR = "https://github.com/astral-sh/uv/releases/download/0.99.0/uv-aarch64-apple-darwin.tar.gz"

_BTBN_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=100"
_BTBN_DL = "https://github.com/BtbN/FFmpeg-Builds/releases/download"
_BTBN_AY_SONU = "autobuild-2026-07-31-13-34"      # doğru seçim (~24 ay yaşar)
_BTBN_GUNLUK = "autobuild-2026-08-05-09-00"       # daha YENİ ama ~2 haftada silinir
_BTBN_ZIP = "ffmpeg-n8.2.0-10-gaaaaaaaaaa-win64-lgpl-8.2.zip"

_NUGET_INDEX = "https://api.nuget.org/v3-flatcontainer/microsoft.codeanalysis.common/index.json"


class _SahteYanit:
    """urlopen'ın döndürdüğü nesnenin bu koddaki kullanılan yüzeyi: bağlam
    yöneticisi + `read()` + `headers`. Fazlası taklit edilmiyor."""

    def __init__(self, govde: bytes, headers: dict | None = None):
        self._govde = govde
        self.status = 200
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._govde

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _sahte_ag(monkeypatch, rotalar: dict, patlayanlar: tuple = ()):
    """URL → gövde eşlemesi. Tanımlanmamış bir adres SESSİZCE boş dönmüyor,
    patlıyor: eksik rota bir test hatasıdır ve testin yanlışlıkla geçmesine
    yol açmamalı."""

    def urlopen(req, timeout=None):
        url = getattr(req, "full_url", req)
        for parca in patlayanlar:
            if parca in url:
                raise urllib.error.URLError(f"sahte ağ arızası: {parca}")
        if url not in rotalar:
            raise AssertionError(f"testte tanımsız ağ adresi: {url}")
        govde = rotalar[url]
        return _SahteYanit(govde if isinstance(govde, bytes) else json.dumps(govde).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def _uv_rotalari() -> dict:
    return {
        _UV_API: [
            # En üstteki KARARLI DEĞİL; seçilirse sürüm sabitlemesi anlamsızlaşır.
            {"tag_name": "0.99.1rc1", "draft": False, "prerelease": True, "assets": []},
            {
                "tag_name": "0.99.0", "draft": False, "prerelease": False,
                "assets": [{
                    "name": "uv-aarch64-apple-darwin.tar.gz",
                    "size": 12345,
                    "browser_download_url": _UV_TAR,
                }],
            },
        ],
        _UV_TAR + ".sha256": f"{_HEX_A}  uv-aarch64-apple-darwin.tar.gz\n".encode(),
    }


@pytest.fixture
def kutuk(pinned, tmp_path, monkeypatch):
    """Kütüğün koşuya özel kopyası. Testlerin gerçek `scripts/pinned_assets.json`
    üzerine yazması, tam da kapatmaya çalıştığımız arızayı test paketinin
    kendisiyle üretmek olurdu."""
    kopya = tmp_path / "pinned_assets.json"
    shutil.copyfile(_MANIFEST, kopya)
    monkeypatch.setattr(pinned, "MANIFEST_PATH", str(kopya))
    return kopya


class TestTazelemeKutugeIzinsizDokunmuyor:
    """`refresh`in en kritik özelliği hangi sürümü bulduğu değil, İZİNSİZ
    YAZMAMASI. Bu kütük güvenlik kapısının kendisi; sessizce üzerine yazan bir
    araç, kapıyı kapatmak için yazılmış olmasına rağmen açardı."""

    def test_a_dry_run_leaves_the_manifest_byte_identical(self, pinned, kutuk, monkeypatch):
        """Varsayılan davranış. Bir bayt bile değişirse bu test kırılmalı —
        'sadece boşluk' diye başlayan bir fark, elle incelenen diff'i boğar."""
        once = kutuk.read_bytes()
        _sahte_ag(monkeypatch, _uv_rotalari())
        sonuc = pinned.refresh(["uv/darwin-arm64"], out=io.StringIO())
        assert kutuk.read_bytes() == once, "kuru koşu kütüğe DOKUNDU"
        assert sonuc[0]["durum"] == "degisti", "değişiklik bulunmalıydı ama bulunmadı"
        assert sonuc[0]["yeni"]["url"] == _UV_TAR

    def test_a_dry_run_says_out_loud_that_nothing_was_written(self, pinned, kutuk, monkeypatch):
        """Kuru koşu sessiz kalırsa okuyan kişi yazıldığını sanar ve diff'i
        incelemez; `$aciklama`'nın verdiği talimat boşa çıkar."""
        _sahte_ag(monkeypatch, _uv_rotalari())
        cikti = io.StringIO()
        pinned.refresh(["uv/darwin-arm64"], out=cikti)
        assert "KURU KOŞU" in cikti.getvalue()
        assert "--write" in cikti.getvalue()

    def test_write_applies_the_pin_without_losing_the_prose_fields(
        self, pinned, kutuk, monkeypatch
    ):
        """`$aciklama`/`$not`/`$roslyn_notu` ölçülmüş gerekçe taşıyor. Kütüğü
        json.dump ile yeniden serileştiren bir uygulama biçimi düzleştirir ve
        diff'i okunamaz kılar — bu test yazma yolunun metin cerrahisi kalmasını
        bağlıyor."""
        _sahte_ag(monkeypatch, _uv_rotalari())
        pinned.refresh(["uv/darwin-arm64"], write=True, out=io.StringIO())

        veri = json.loads(kutuk.read_text(encoding="utf-8"))
        girdi = veri["assets"]["uv/darwin-arm64"]
        assert girdi["url"] == _UV_TAR
        assert girdi["digest"] == "sha256:" + _HEX_A
        assert girdi["size"] == 12345
        assert girdi["kaynak"] == "yayinci"

        with open(_MANIFEST, encoding="utf-8") as f:
            asil = json.load(f)
        assert veri["$aciklama"] == asil["$aciklama"], "$aciklama kayboldu/değişti"
        assert veri["$roslyn_notu"] == asil["$roslyn_notu"], "$roslyn_notu kayboldu/değişti"
        assert veri["assets"]["ffmpeg/win"]["$not"] == asil["assets"]["ffmpeg/win"]["$not"]
        assert veri["assets"]["ffmpeg/macos"]["$not"] == asil["assets"]["ffmpeg/macos"]["$not"]
        assert list(veri["assets"]) == list(asil["assets"]), "anahtar sırası bozuldu"
        assert list(girdi) == list(asil["assets"]["uv/darwin-arm64"]), "alan sırası bozuldu"

    def test_only_the_touched_entry_changes(self, pinned, kutuk, monkeypatch):
        """Yazma yolu blok sınırlarına saygılı mı: bir asset'i tazelerken
        komşusunu bozmak, 19 girdilik bir dosyada gözden kaçar."""
        with open(_MANIFEST, encoding="utf-8") as f:
            asil = json.load(f)["assets"]
        _sahte_ag(monkeypatch, _uv_rotalari())
        pinned.refresh(["uv/darwin-arm64"], write=True, out=io.StringIO())
        yeni = json.loads(kutuk.read_text(encoding="utf-8"))["assets"]
        for key in asil:
            if key != "uv/darwin-arm64":
                assert yeni[key] == asil[key], f"{key} dokunulmadan değişti"


class TestBtbNSadeceAySonuTaginiSeciyor:
    """BtbN `util/prunetags.sh` ile son 14 build + son 24 ay-sonu tag'ini tutup
    gerisini release'iyle birlikte SİLİYOR. Bu kural unutulursa `refresh` en yeni
    (günlük) tag'i seçer ve pin ~2 haftada 404 olur — üstelik sessizce, çünkü
    kırılma build gününe kadar görünmez."""

    def test_the_month_end_rule_is_computed_not_hardcoded(self, pinned):
        assert pinned._btbn_ay_sonu_mu("autobuild-2026-07-31-13-34")
        assert pinned._btbn_ay_sonu_mu("autobuild-2026-06-30-13-34")
        assert pinned._btbn_ay_sonu_mu("autobuild-2024-02-29-00-00")   # artık yıl
        assert not pinned._btbn_ay_sonu_mu("autobuild-2026-08-05-09-00")
        assert not pinned._btbn_ay_sonu_mu("autobuild-2025-02-29-00-00")  # yok olan gün
        assert not pinned._btbn_ay_sonu_mu("latest")

    def _rotalar(self) -> dict:
        def varlik(tag, ad, boyut=999):
            return {"name": ad, "size": boyut, "browser_download_url": f"{_BTBN_DL}/{tag}/{ad}"}

        adlar = [
            _BTBN_ZIP,
            "ffmpeg-n8.2.0-10-gaaaaaaaaaa-win64-lgpl-shared-8.2.zip",
            "ffmpeg-n8.2.0-10-gaaaaaaaaaa-win64-gpl-8.2.zip",
            "ffmpeg-master-latest-win64-lgpl.zip",
            "checksums.sha256",
        ]
        toplamlar = "".join(f"{_HEX_B}  {ad}\n" for ad in adlar).encode()
        return {
            _BTBN_API: [
                # Listenin başında daha YENİ ama günlük olan tag duruyor: seçim
                # "en yeni"ye göre yapılırsa test kırmızıya döner.
                {"tag_name": _BTBN_GUNLUK, "draft": False, "prerelease": False,
                 "assets": [varlik(_BTBN_GUNLUK, ad) for ad in adlar]},
                {"tag_name": _BTBN_AY_SONU, "draft": False, "prerelease": False,
                 "assets": [varlik(_BTBN_AY_SONU, ad, 144332999) for ad in adlar]},
                {"tag_name": "autobuild-2026-06-30-13-34", "draft": False, "prerelease": False,
                 "assets": [varlik("autobuild-2026-06-30-13-34", ad) for ad in adlar]},
            ],
            f"{_BTBN_DL}/{_BTBN_AY_SONU}/checksums.sha256": toplamlar,
        }

    def test_a_newer_daily_tag_is_refused_in_favour_of_the_month_end_one(
        self, pinned, kutuk, monkeypatch
    ):
        _sahte_ag(monkeypatch, self._rotalar())
        sonuc = pinned.refresh(["ffmpeg/win"], out=io.StringIO())[0]
        assert sonuc["durum"] == "degisti", sonuc
        assert _BTBN_AY_SONU in sonuc["yeni"]["url"]
        assert _BTBN_GUNLUK not in sonuc["yeni"]["url"], (
            "günlük autobuild tag'i seçildi — BtbN bunu ~2 haftada siler, pin ölür"
        )

    def test_the_shared_and_unversioned_variants_are_not_pinned(
        self, pinned, kutuk, monkeypatch
    ):
        """`shared` ayrı DLL'ler ister; `master-latest` sürümsüz, yani hareketli
        hedef — ikisi de sabitlenemez."""
        _sahte_ag(monkeypatch, self._rotalar())
        url = pinned.refresh(["ffmpeg/win"], out=io.StringIO())[0]["yeni"]["url"]
        assert url.endswith(_BTBN_ZIP)
        assert "shared" not in url and "master-latest" not in url

    def test_writing_ffmpeg_win_keeps_its_expiry_warning(self, pinned, kutuk, monkeypatch):
        """Bu girdinin `$not`'u pinin süresinin DOLACAĞINI anlatıyor; kaybolursa
        gelecekteki okuyucu 404'ün sebebini yeniden keşfetmek zorunda kalır."""
        _sahte_ag(monkeypatch, self._rotalar())
        pinned.refresh(["ffmpeg/win"], write=True, out=io.StringIO())
        girdi = json.loads(kutuk.read_text(encoding="utf-8"))["assets"]["ffmpeg/win"]
        assert _BTBN_AY_SONU in girdi["url"]
        assert girdi["digest"] == "sha256:" + _HEX_B
        assert girdi["size"] == 144332999
        assert "prunetags" in girdi["$not"], "süre-dolumu uyarısı yazma sırasında silindi"


class TestRoslynOtomatikYukseltilmiyor:
    """roslyn/* Unity Editor'ün İÇİNE yükleniyor. Sürüm yükseltmesi Unity
    uyumluluğunu kırabilir ve kırılma bizim makinemizde değil KULLANICININ
    Unity'sinde görünür — o yüzden karar otomatikleştirilemez, yalnız bildirilir.
    Ayrıca özetler `RoslynInstaller.cs` tablosunda da yazılı; kütüğü tek başına
    yükseltmek yukarıdaki `TestCSharpTablosuKutukleAyrisamaz`ı da kırardı."""

    def test_a_newer_nuget_version_is_reported_but_never_written(
        self, pinned, kutuk, monkeypatch
    ):
        once = kutuk.read_bytes()
        _sahte_ag(monkeypatch, {
            _NUGET_INDEX: {"versions": ["4.12.0", "4.14.0", "5.0.0-preview.1.25"]},
        })
        # write=True bilerek: yazma İZNİ verilse bile bu girdi değişmemeli.
        sonuc = pinned.refresh(
            ["roslyn/microsoft.codeanalysis.common"], write=True, out=io.StringIO()
        )[0]
        assert sonuc["durum"] == "bildirim", sonuc
        assert "4.14.0" in sonuc["mesaj"]
        assert kutuk.read_bytes() == once, "roslyn girdisi otomatik yükseltildi"

    def test_a_prerelease_is_not_reported_as_the_newest(self, pinned, kutuk, monkeypatch):
        """5.0.0-preview mevcut sürümden büyük görünür ama kararlı değil; onu
        bildirmek, sınanmamış bir yükseltmeyi meşru gösterirdi."""
        _sahte_ag(monkeypatch, {
            _NUGET_INDEX: {"versions": ["4.12.0", "5.0.0-preview.1.25"]},
        })
        sonuc = pinned.refresh(["roslyn/microsoft.codeanalysis.common"], out=io.StringIO())[0]
        assert sonuc["durum"] == "guncel", sonuc


class TestTekBirArizaKosuyuOldurmuyor:
    """19 asset'in 8 ayrı yayıncısı var. Birinin bakımda olması diğer 18'in
    tazelenmesini engellerse araç kullanılmaz hale gelir — ve kullanılmayan bir
    araç, var olmayan araçla aynı şeydir (bu görevi doğuran durum tam olarak
    buydu: vaat edilmiş ama olmayan `refresh`)."""

    def test_a_failing_publisher_does_not_stop_the_others(self, pinned, kutuk, monkeypatch):
        _sahte_ag(monkeypatch, _uv_rotalari(), patlayanlar=("evermeet.cx",))
        sonuclar = pinned.refresh(
            ["ffmpeg/macos", "uv/darwin-arm64"], out=io.StringIO()
        )
        durumlar = {s["key"]: s["durum"] for s in sonuclar}
        assert durumlar["ffmpeg/macos"] == "hata"
        assert durumlar["uv/darwin-arm64"] == "degisti", (
            "bir asset'in ağ hatası sonrakini de düşürdü"
        )

    def test_a_failed_asset_is_named_with_its_reason(self, pinned, kutuk, monkeypatch):
        """'bir şeyler ters gitti' teşhis ettirmiyor; hangi asset, hangi hata."""
        _sahte_ag(monkeypatch, _uv_rotalari(), patlayanlar=("evermeet.cx",))
        cikti = io.StringIO()
        pinned.refresh(["ffmpeg/macos"], out=cikti)
        metin = cikti.getvalue()
        assert "ffmpeg/macos" in metin and "HATA" in metin
        assert "sahte ağ arızası" in metin

    def test_the_cli_exit_code_reports_the_failure(self, pinned, kutuk, monkeypatch):
        """Çıkış kodu sözleşmesi (0=tamam, 1=başarısız): eksik kalan bir koşu
        CI'da yeşil sayılırsa, kapı yine sessizce etkisiz hale gelir."""
        _sahte_ag(monkeypatch, _uv_rotalari(), patlayanlar=("evermeet.cx",))
        assert pinned._main(["pinned_assets.py", "refresh", "ffmpeg/macos"]) == 1
        _sahte_ag(monkeypatch, _uv_rotalari())
        assert pinned._main(["pinned_assets.py", "refresh", "uv/darwin-arm64"]) == 0
        assert kutuk.read_bytes() == open(_MANIFEST, "rb").read(), (
            "CLI kuru koşusu kütüğe yazdı"
        )

    def test_an_unknown_key_is_refused_instead_of_silently_skipped(self, pinned, kutuk):
        with pytest.raises(KeyError):
            pinned.refresh(["boyle/bir/anahtar/yok"], out=io.StringIO())


class TestCheckOlenPinleriGoruyor:
    """İki pin'in süresi DOLACAK ve sessizce olacak: `ffmpeg/win` BtbN'in tag
    budamasıyla, `ffmpeg/linux` releases/ → old-releases/ taşınmasıyla. HEAD
    yoklaması bunu build gününden haftalar öne çekiyor."""

    def test_a_404_pin_is_reported_as_dead(self, pinned, kutuk, monkeypatch):
        olu = json.loads(kutuk.read_text(encoding="utf-8"))["assets"]["ffmpeg/win"]["url"]

        def urlopen(req, timeout=None):
            url = getattr(req, "full_url", req)
            assert req.get_method() == "HEAD", "check gövdeyi indiriyor — 1.5 GB"
            if url == olu:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return _SahteYanit(b"")

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        sonuclar = {s["key"]: s["durum"] for s in pinned.check(out=io.StringIO())}
        assert sonuclar["ffmpeg/win"] == "olu"
        assert sonuclar["uv/win-x64"] == "ok"
        assert pinned._main(["pinned_assets.py", "check"]) == 1


class TestCheckOlcemedigineGectiDemiyor:
    """Dış denetim bulgusu (2026-07-28): `check` ağ hatalarını ve 5xx'i
    "bilinmiyor" olarak İŞARETLİYOR ama özette hiç saymıyordu — 404/410 yoksa
    "adreslerin hepsi ayakta" yazıp SIFIR dönüyordu. Yani ağ tamamen kopukken,
    tek bir adres bile doğrulanamamışken kontrol GEÇMİŞ görünüyordu.

    Bu, deponun kendi yazılı kuralının ihlali: ölçemeyen bir kontrol geçmiş
    sayılmaz. Ve maliyeti somut: bu yoklamanın tek işi `ffmpeg/win` ile
    `ffmpeg/linux` pinlerinin sessizce ölmesini build gününden önce yakalamak —
    sessizce yeşil dönen bir yoklama, hiç koşmayan bir yoklamayla aynı şey.
    """

    def _ag(self, monkeypatch, *, patlayan=None, kod_5xx=None, olu=None):
        """URL'ye göre davranış: istisna / HTTP kodu / 200."""
        def urlopen(req, timeout=None):
            url = getattr(req, "full_url", req)
            if patlayan and patlayan in url:
                raise urllib.error.URLError("ağ yok")
            if kod_5xx and kod_5xx in url:
                raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
            if olu and olu in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return _SahteYanit(b"")

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    def _url(self, kutuk, key: str) -> str:
        return json.loads(kutuk.read_text(encoding="utf-8"))["assets"][key]["url"]

    def test_a_network_error_is_not_counted_as_alive(self, pinned, kutuk, monkeypatch):
        hedef = self._url(kutuk, "uv/win-x64")
        self._ag(monkeypatch, patlayan=hedef)
        cikti = io.StringIO()
        sonuclar = {s["key"]: s["durum"] for s in pinned.check(out=cikti)}
        assert sonuclar["uv/win-x64"] == "bilinmiyor"
        metin = cikti.getvalue()
        assert "hepsi ayakta" not in metin, (
            "bir adres hiç ölçülemediği halde özet 'hepsi ayakta' diyor:\n" + metin
        )

    def test_a_5xx_is_not_counted_as_alive_either(self, pinned, kutuk, monkeypatch):
        """5xx yayıncının geçici arızası: adresin ölü mü diri mi olduğunu
        SÖYLEMİYOR. 'ayakta' saymak da 'ölü' saymak da uydurma olurdu."""
        self._ag(monkeypatch, kod_5xx=self._url(kutuk, "uv/win-x64"))
        cikti = io.StringIO()
        sonuclar = {s["key"]: s["durum"] for s in pinned.check(out=cikti)}
        assert sonuclar["uv/win-x64"] == "bilinmiyor"
        assert "hepsi ayakta" not in cikti.getvalue()

    def test_the_unknown_ones_are_named_so_the_operator_can_recheck(
        self, pinned, kutuk, monkeypatch
    ):
        """Sayı yetmez: hangi adresin ölçülemediği yazılmalı, yoksa operatör
        elle tekrar yoklayamaz."""
        self._ag(monkeypatch, patlayan=self._url(kutuk, "uv/win-x64"))
        cikti = io.StringIO()
        pinned.check(out=cikti)
        assert "uv/win-x64" in cikti.getvalue()

    def test_the_cli_does_not_exit_zero_when_nothing_could_be_measured(
        self, pinned, kutuk, monkeypatch, capsys
    ):
        """Otomasyonun gördüğü TEK sinyal bu sayı. Sıfır dönerse CI kontrolü
        geçmiş sayar."""
        def urlopen(req, timeout=None):
            raise urllib.error.URLError("ağ tamamen kopuk")

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        kod = pinned._main(["pinned_assets.py", "check"])
        capsys.readouterr()
        assert kod != 0, "hiçbir adres doğrulanamadı ama CLI 'geçti' dedi"

    def test_an_unmeasurable_address_is_an_operational_failure_not_an_integrity_one(
        self, pinned, kutuk, monkeypatch, capsys
    ):
        """Kodun HANGİ sınıf olduğu önemli. Modül docstring'indeki sözleşme:
        1 = bütünlük (ASLA tekrar denenmez), 3 = işletim (tekrar denenebilir).
        Ölçülemeyen bir adres tanım gereği tekrar denenebilir — 1 dönseydi
        operatöre 'bu asla düzelmez' denmiş olurdu ve pin gereksiz yere
        tazelenirdi.

        Beklenti sabiti elle yazılı, koddan okunmuyor."""
        self._ag(monkeypatch, patlayan=self._url(kutuk, "uv/win-x64"))
        kod = pinned._main(["pinned_assets.py", "check"])
        capsys.readouterr()
        assert kod == 3, f"ölçülemeyen adres {kod} döndürdü, işletim kodu 3 bekleniyordu"

    def test_a_dead_pin_still_wins_over_an_unknown_one(
        self, pinned, kutuk, monkeypatch, capsys
    ):
        """İkisi birden varsa ölü pin baskın: 404 doğrulanmış bir arıza,
        ölçülememe ise bilgi yokluğu. Operatörün önce ölüyü görmesi gerekir."""
        self._ag(
            monkeypatch,
            patlayan=self._url(kutuk, "uv/win-x64"),
            olu=self._url(kutuk, "ffmpeg/win"),
        )
        kod = pinned._main(["pinned_assets.py", "check"])
        capsys.readouterr()
        assert kod == 1, f"ölü pin varken {kod} döndü, bütünlük kodu 1 bekleniyordu"

    def test_a_fully_measurable_healthy_run_still_exits_zero(
        self, pinned, kutuk, monkeypatch, capsys
    ):
        """KARŞI YÖN — kapının ateşlenMEmesi gereken hali. Bu olmadan yukarıdaki
        testler 'her zaman sıfırdan farklı dön' ile de geçerdi."""
        self._ag(monkeypatch)
        cikti = io.StringIO()
        sonuclar = pinned.check(out=cikti)
        assert all(s["durum"] == "ok" for s in sonuclar)
        assert "hepsi ayakta" in cikti.getvalue(), (
            "her adres ölçüldü ve ayakta, ama özet bunu söylemiyor"
        )
        kod = pinned._main(["pinned_assets.py", "check"])
        capsys.readouterr()
        assert kod == 0


class TestDotnetSurumDosyasiDogruYerdenOkunuyor:
    """Canlı koşuda ölçülmüş arıza (2026-07-28): dosyalar `release.sdk.files`
    altında, en üst düzeyde `sdk` YOK. Yanlış yol 'aday bulunamadı' hatası
    veriyordu. Ayrıca aynı rid altında .pkg/.exe/.tar.gz/.zip yan yana duruyor ve
    `name` alanı sürüm taşımıyor — yanlışını seçmek kullanıcıya çalışmayan bir
    SDK indirtirdi."""

    _URL = "https://builds.dotnet.microsoft.com/dotnet/Sdk/10.0.101"

    def _rotalar(self, pinned) -> dict:
        def dosya(rid, ad):
            return {"name": ad, "rid": rid, "url": f"{self._URL}/dotnet-sdk-10.0.101-{ad[11:]}",
                    "hash": "c" * 128}

        # Adres modülden okunuyor, kopyalanmıyor: sabiti burada tekrarlamak
        # 'uyuşması gereken iki yer' üretirdi.
        return {
            pinned._DOTNET_RELEASE_JSON: {
                "channel-version": "10.0",
                "release": {
                    "release-version": "10.0.1",
                    "sdk": {"files": [
                        dosya("osx-arm64", "dotnet-sdk-osx-arm64.pkg"),
                        dosya("osx-arm64", "dotnet-sdk-osx-arm64.tar.gz"),
                        dosya("win-x64", "dotnet-sdk-win-x64.exe"),
                        dosya("win-x64", "dotnet-sdk-win-x64.zip"),
                    ]},
                },
            },
            f"{self._URL}/dotnet-sdk-10.0.101-osx-arm64.tar.gz": b"",
        }

    def test_the_archive_is_chosen_over_the_installer_for_the_same_rid(
        self, pinned, kutuk, monkeypatch
    ):
        rotalar = self._rotalar(pinned)

        def urlopen(req, timeout=None):
            url = getattr(req, "full_url", req)
            if req.get_method() == "HEAD":
                return _SahteYanit(b"", {"Content-Length": "229999999"})
            if url not in rotalar:
                raise AssertionError(f"testte tanımsız ağ adresi: {url}")
            govde = rotalar[url]
            return _SahteYanit(govde if isinstance(govde, bytes) else json.dumps(govde).encode())

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        sonuc = pinned.refresh(["dotnet-sdk/osx-arm64"], out=io.StringIO())[0]
        assert sonuc["durum"] == "degisti", sonuc
        assert sonuc["yeni"]["url"].endswith("osx-arm64.tar.gz"), ".pkg seçildi"
        assert sonuc["yeni"]["digest"] == "sha512:" + "c" * 128, "çıplak hex sha512 sayılmadı"
        # Boyut release.json'da YOK; kütüğün sözleşmesi zorunlu tuttuğu için
        # Content-Length'ten geliyor.
        assert sonuc["yeni"]["size"] == 229999999


class TestOzetGercektenOAlgoritma:
    """Bilinen-cevap vektörleri. Denetimde ölçüldü (2026-07-28): `_ALGOS`'un sha512
    girdisini `hashlib.sha256`'ya çeviren bir mutasyon 72 testin HEPSİNİ sağ geçti,
    ve özet karşılaştırmasını ilk 4 haneye indiren bir mutasyon da öyle.

    Sebep: bütün testler beklenen değeri `digest_of`'un KENDİSİYLE üretiyordu, yani
    yalnız "deterministik mi" sorusunu sınıyorlardı. Dışarıdan gelen sabit bir
    vektör olmadan bir özet fonksiyonunun DOĞRU algoritma olduğu kanıtlanamaz.

    Değerler `printf 'abc' | shasum -a 256|512` ile üretildi, ezberden yazılmadı.
    """

    ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    ABC_SHA512 = (
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f"
    )

    def test_sha256_matches_the_published_vector(self, pinned):
        assert pinned.digest_of(b"abc", "sha256") == self.ABC_SHA256

    def test_sha512_matches_the_published_vector(self, pinned):
        """sha512 yalnız `dotnet-sdk/*` için kullanılıyor — yani 220-300 MB'lık üç
        asset'in doğrulaması, gerçek hash'leme ile HİÇ koşturulmamıştı."""
        assert pinned.digest_of(b"abc", "sha512") == self.ABC_SHA512

    def test_an_unknown_algorithm_is_refused(self, pinned):
        with pytest.raises(ValueError):
            pinned.digest_of(b"abc", "md5")

    def test_a_sha512_pinned_asset_is_verified_with_real_sha512(
        self, pinned, entries, monkeypatch
    ):
        """Uçtan uca: sha512 pinli bir girdide doğru baytlar geçmeli, bir bayt
        değişince geçmemeli. Mutasyon A'nın hedefi tam buydu."""
        data = b"abc"
        sahte = dict(entries["dotnet-sdk/win-x64"])
        sahte["digest"] = "sha512:" + self.ABC_SHA512
        sahte["size"] = len(data)
        monkeypatch.setattr(pinned, "asset", lambda k: sahte)
        pinned.verify_bytes(data, "dotnet-sdk/win-x64")
        with pytest.raises(pinned.IntegrityError):
            pinned.verify_bytes(b"abd", "dotnet-sdk/win-x64")


class TestBozukPinKapiyiAcamaz:
    """Beklenen tarafın bozulması. Mutasyon C ölçüldü: yer tutucu bir pin'i kabul
    eden bir dal eklendiğinde 72 test yeşil kaldı — çünkü yer tutucu yalnız JSON
    METNİNDE aranıyordu, doğrulayıcıya hiç sorulmuyordu."""

    @pytest.mark.parametrize("bozuk", [
        "sha256:TODO-DIGEST",
        "sha256:",
        "sha256:abc",                                    # kısa
        "sha256:" + "z" * 64,                            # hex değil
        "sha256:" + "A" * 64,                            # büyük harf
        "md5:" + "a" * 32,                               # kabul edilmeyen algoritma
        "abc123",                                        # algo ayracı yok
    ])
    def test_an_unusable_pin_raises_instead_of_being_compared(
        self, pinned, entries, monkeypatch, bozuk
    ):
        sahte = dict(entries["omnisharp/osx-arm64"])
        sahte["digest"] = bozuk
        sahte.pop("size", None)
        monkeypatch.setattr(pinned, "asset", lambda k: sahte)
        with pytest.raises(pinned.IntegrityError):
            pinned.verify_bytes(b"herhangi", "omnisharp/osx-arm64")

    def test_a_well_formed_pin_still_works(self, pinned, entries, monkeypatch):
        """Karşıt yön: biçim kapısı çok DAR olursa hiçbir asset kurulamaz."""
        data = b"abc"
        sahte = dict(entries["omnisharp/osx-arm64"])
        sahte["digest"] = "sha256:" + TestOzetGercektenOAlgoritma.ABC_SHA256
        sahte["size"] = len(data)
        monkeypatch.setattr(pinned, "asset", lambda k: sahte)
        pinned.verify_bytes(data, "omnisharp/osx-arm64")


def _gecici_kalintilar(dizin) -> list:
    """Yerine konamamış geçici kütük kopyaları. Dizinde kalan yarım bir kopya,
    'hangisi gerçek kütük' sorusunu doğurur — düzeltmenin kapatmaya çalıştığı
    arızanın aynısı."""
    return [p.name for p in dizin.iterdir() if p.name.startswith(".pinned_assets-")]


class TestKutugeYazmaYarimKalamaz:
    """Kütük güven kökü; yazılması ya TAMAMEN olur ya HİÇ olmaz.

    Hangi arızadan doğdu (dış denetim, 2026-07-28): `refresh --write` kütüğü düz
    `open(path, "w")` ile yeniden yazıyordu; bu dosyayı ÖNCE kesiyor. Kesme ile
    yazmanın bitmesi arasındaki bir çökme/disk-dolması güven kökünü boş ya da yarım
    bırakırdı. Mevcut `json.loads` kapısı bunu görmüyor: o yalnız BELLEKTEKİ metni
    doğruluyor, yazmanın hayatta kaldığına dair hiçbir şey söylemiyor.
    """

    def test_a_failed_rename_leaves_the_original_ledger_intact(
        self, pinned, kutuk, monkeypatch
    ):
        """Son adım (`os.replace`) patlarsa kütük DOKUNULMAMIŞ kalmalı."""
        once = kutuk.read_bytes()
        gercek_replace = os.replace

        def patlayan_replace(src, dst, *a, **kw):
            # Yalnız kütüğe yapılan replace patlıyor; testin geri kalanı (pytest'in
            # kendi dosya işlemleri dahil) gerçek os.replace'i kullanmaya devam etsin.
            if str(dst) == str(kutuk):
                raise OSError(28, "sahte: aygıtta yer kalmadı")
            return gercek_replace(src, dst, *a, **kw)

        monkeypatch.setattr(os, "replace", patlayan_replace)
        _sahte_ag(monkeypatch, _uv_rotalari())
        with pytest.raises(OSError):
            pinned.refresh(["uv/darwin-arm64"], write=True, out=io.StringIO())

        assert kutuk.read_bytes() == once, "yarım kalan yazma kütüğü değiştirdi"
        json.loads(kutuk.read_text(encoding="utf-8"))   # ayrıştırılabilir mi
        assert _gecici_kalintilar(kutuk.parent) == [], "geçici dosya ortada bırakıldı"

    def test_a_failed_flush_leaves_the_original_ledger_intact(
        self, pinned, kutuk, monkeypatch
    ):
        """Diğer yön: arıza yazma/fsync sırasında çıkarsa da kütük bozulmamalı.

        Ayrı test, çünkü iki arıza noktası ayrı: `os.replace` patladığında hedefe
        hiç dokunulmamıştır; fsync patladığında ise geçici dosya YARIM kalmıştır ve
        asıl soru onun hedefin üstüne konmaması (ve ortada bırakılmaması).
        """
        once = kutuk.read_bytes()

        def patlayan_fsync(fd):
            raise OSError(5, "sahte: G/Ç hatası")

        monkeypatch.setattr(os, "fsync", patlayan_fsync)
        _sahte_ag(monkeypatch, _uv_rotalari())
        with pytest.raises(OSError):
            pinned.refresh(["uv/darwin-arm64"], write=True, out=io.StringIO())

        assert kutuk.read_bytes() == once, "yarım yazılan dosya kütüğün üstüne kondu"
        json.loads(kutuk.read_text(encoding="utf-8"))
        assert _gecici_kalintilar(kutuk.parent) == [], "geçici dosya ortada bırakıldı"

    def test_a_successful_write_still_lands_with_the_prose_intact(
        self, pinned, kutuk, monkeypatch
    ):
        """Karşıt yön. Atomik yazma 'hiç yazma' ile geçilebilecek bir kapı değil:
        başarılı koşuda değerler oturmalı ve `$aciklama`/`$not`/`$roslyn_notu`
        blokları yerinde kalmalı (bu bloklar ölçülmüş gerekçe taşıyor)."""
        _sahte_ag(monkeypatch, _uv_rotalari())
        pinned.refresh(["uv/darwin-arm64"], write=True, out=io.StringIO())

        veri = json.loads(kutuk.read_text(encoding="utf-8"))
        with open(_MANIFEST, encoding="utf-8") as f:
            asil = json.load(f)
        assert veri["assets"]["uv/darwin-arm64"]["url"] == _UV_TAR
        assert veri["assets"]["uv/darwin-arm64"]["digest"] == "sha256:" + _HEX_A
        assert veri["$aciklama"] == asil["$aciklama"]
        assert veri["$roslyn_notu"] == asil["$roslyn_notu"]
        # `$not` asset bloklarının içinde (üst düzeyde yok): ffmpeg/win'inki pinin
        # süresinin DOLACAĞINI anlatıyor, kaybolursa 404'ün sebebi yeniden keşfedilir.
        assert veri["assets"]["ffmpeg/win"]["$not"] == asil["assets"]["ffmpeg/win"]["$not"]
        assert veri["assets"]["ffmpeg/macos"]["$not"] == asil["assets"]["ffmpeg/macos"]["$not"]
        assert _gecici_kalintilar(kutuk.parent) == [], "başarılı koşuda geçici dosya kaldı"

    def test_the_write_does_not_rewrite_every_line_ending(
        self, pinned, kutuk, monkeypatch
    ):
        """Metin modu Windows'ta her `\\n`'i CRLF yapıyordu; o da elle incelenmesi
        istenen diff'i 'her satır değişti'ye çeviriyordu. Kütüğün okunabilir diff'i
        bu kodun kendi yorumlarında bir güvenlik özelliği sayılıyor."""
        once = kutuk.read_bytes()
        assert b"\r" not in once, "başlangıç kütüğü zaten LF değil — test varsayımı çöktü"
        _sahte_ag(monkeypatch, _uv_rotalari())
        pinned.refresh(["uv/darwin-arm64"], write=True, out=io.StringIO())

        sonra = kutuk.read_bytes()
        assert b"\r" not in sonra, "yazma satır sonlarını CRLF'e çevirdi"
        assert once.count(b"\n") == sonra.count(b"\n"), "satır sayısı değişti"
        farkli = [a for a, b in zip(once.split(b"\n"), sonra.split(b"\n")) if a != b]
        assert len(farkli) <= 4, (
            f"tek bir asset tazelenirken {len(farkli)} satır değişti — diff okunamaz hale gelir"
        )

    def test_a_crlf_ledger_round_trips_as_crlf(self, pinned, tmp_path, monkeypatch):
        """Öteki platform yönü: kütük CRLF ile check-out edilmişse (Windows,
        `core.autocrlf=true`) yazma onu LF'e çevirmemeli. İki yön birlikte
        'dosya hangi platformda yazılırsa yazılsın bayt bayt kendisi kalır' demek."""
        crlf = tmp_path / "pinned_assets.json"
        ham = open(_MANIFEST, "rb").read()
        crlf.write_bytes(ham.replace(b"\n", b"\r\n"))
        monkeypatch.setattr(pinned, "MANIFEST_PATH", str(crlf))
        satir_sonu = crlf.read_bytes().count(b"\r\n")

        _sahte_ag(monkeypatch, _uv_rotalari())
        pinned.refresh(["uv/darwin-arm64"], write=True, out=io.StringIO())

        sonra = crlf.read_bytes()
        assert sonra.count(b"\r\n") == satir_sonu, "CRLF kütük LF'e çevrildi"
        assert sonra.count(b"\n") == sonra.count(b"\r\n"), "satır sonları karıştı"
        assert json.loads(sonra.decode("utf-8"))["assets"]["uv/darwin-arm64"]["url"] == _UV_TAR


class TestCikisKodlariArizaSiniflariniAyiriyor:
    """Kabuk çağıranları yalnız çıkış kodunu okuyor; kod, arıza SINIFINI taşımak
    zorunda.

    Hangi arızadan doğdu (dış denetim, 2026-07-28): `IntegrityError` 1 dönüyordu,
    ama `KeyError`/`OSError` de 1 dönüyordu. Yani okunamayan bir dosya ya da kütükte
    olmayan bir anahtar operatöre "indirilen baytlar sabitlenmiş özetle uyuşmuyor"
    diye raporlanıyordu. Bu, mümkün olan en pahalı yanlış yönlendirme: projenin
    yazılı doktrini bir özet uyuşmazlığının ASLA tekrar denenmemesi.
    """

    _KEY = "omnisharp/osx-arm64"

    def _butunluk_kodu(self, pinned, tmp_path) -> int:
        dosya = tmp_path / "indirilmis.bin"
        dosya.write_bytes(b"bunlar beklenen baytlar degil")
        return pinned._main(["pinned_assets.py", "verify", self._KEY, str(dosya)])

    def test_an_integrity_failure_still_exits_1(self, pinned, kutuk, tmp_path, capsys):
        """1'in anlamı korunuyor: kabuk betikleri ona göre yazılmış."""
        assert self._butunluk_kodu(pinned, tmp_path) == 1
        assert "BÜTÜNLÜK" in capsys.readouterr().err

    def test_an_unreadable_file_exits_3_not_1(self, pinned, kutuk, tmp_path):
        """Dosya hiç yok: bu bir G/Ç arızası, doğrulanmış bir uyuşmazlık değil."""
        yok = tmp_path / "hic-inmemis.bin"
        assert pinned._main(["pinned_assets.py", "verify", self._KEY, str(yok)]) == 3

    def test_a_missing_key_exits_3_not_1(self, pinned, kutuk):
        assert pinned._main(["pinned_assets.py", "url", "boyle/bir/anahtar/yok"]) == 3

    def test_a_corrupt_ledger_exits_3_not_1(self, pinned, kutuk):
        """Ayrıştırılamayan kütük de işletim arızası: hiçbir bayt doğrulanmadı."""
        kutuk.write_text("{ bu json degil", encoding="utf-8")
        assert pinned._main(["pinned_assets.py", "keys"]) == 3

    def test_the_two_classes_are_actually_distinguishable(
        self, pinned, kutuk, tmp_path
    ):
        """Testin asıl noktası. Yukarıdaki iki testin ikisi de geçip kodlar EŞİT
        olsaydı, çağıran hâlâ ayırt edemezdi — o yüzden fark burada açıkça
        sınanıyor, sabitlere değil ilişkiye bakılarak."""
        butunluk = self._butunluk_kodu(pinned, tmp_path)
        islem = pinned._main(["pinned_assets.py", "url", "boyle/bir/anahtar/yok"])
        assert butunluk != 0 and islem != 0, "iki arıza da başarısızlık olarak dönmeli"
        assert butunluk != islem, (
            f"bütünlük arızası ve işletim arızası aynı kodu ({butunluk}) döndürüyor — "
            "çağıran 'tekrar deneme' ile 'ortamı düzelt'i ayırt edemez"
        )

    def test_a_usage_error_still_exits_2(self, pinned, kutuk):
        """2'nin anlamı DEĞİŞMEDİ; 3 ondan da ayrı olmalı."""
        assert pinned._main(["pinned_assets.py"]) == 2
        assert pinned._main(["pinned_assets.py", "verify", self._KEY]) == 2


class TestPythonSurumKapisi:
    """Desteklenmeyen yorumlayıcı, 'anahtar kütükte yok' diye teşhis edilmemeli.

    Hangi arızadan doğdu (dış denetim, 2026-07-28, ölçülmüş): modül PEP 604 gösterimi
    (`dict | None`) kullanıyor, o gösterim def anında değerlendiriliyor, yani Python
    3.9'da modül IMPORT anında TypeError atıyor. `fetch_video_bins.sh/.ps1` yalnız bir
    python3 VAR MI diye bakıp sıfır olmayan her çıkışı "bu anahtar kütükte yok" sayıyor
    ve binary'yi `exit 0` ile atlıyordu. Stok macOS Python 3.9.6'da build sessizce
    ffmpeg/yt-dlp'siz çıkıyor, üstelik SEBEBİ yanlış raporlanıyordu.

    ⚠️ Burada 3.9 KOŞTURULAMIYOR (bu ortam 3.13). O yüzden kapının kendisi
    çalıştırılmış gibi yapılmıyor; kapının KARARI, MESAJI, ÇIKIŞ KODU ve modüldeki
    YERİ doğrudan sınanıyor. Sınanmayan tek şey, 3.9'un bu blok import edilirken
    gerçekten hata vermemesi — o yalnız blokta PEP 604/walrus kullanılmamasıyla
    sağlanıyor ve aşağıdaki kaynak testi onu bağlıyor.
    """

    def test_an_unsupported_interpreter_is_refused_by_name(self, pinned):
        akis = io.StringIO()
        with pytest.raises(SystemExit) as cikis:
            pinned._python_surum_kapisi((3, 9, 6), akis)
        mesaj = akis.getvalue()
        assert "3.10" in mesaj, "mesaj GEREKEN en düşük sürümü söylemiyor"
        assert "3.9.6" in mesaj, "mesaj BULUNAN sürümü söylemiyor — teşhis eksik kalır"
        assert cikis.value.code == 2

    def test_the_guard_does_not_borrow_the_integrity_exit_code(self, pinned):
        """1 dönerse operatör 'baytlar pinle uyuşmuyor' okur; oysa hiçbir bayt
        indirilmedi. Ortam hatası 2, bütünlük hatası 1 — karışmamalı."""
        with pytest.raises(SystemExit) as cikis:
            pinned._python_surum_kapisi((3, 9, 6), io.StringIO())
        assert cikis.value.code != 1
        assert cikis.value.code == 2

    def test_a_supported_interpreter_passes_silently(self, pinned):
        """Karşıt yön: kapı çok GENİŞ kapanırsa desteklenen sürümde de build ölür."""
        for surum in [(3, 10, 0), (3, 12, 7), (3, 13, 13), (4, 0, 0)]:
            akis = io.StringIO()
            assert pinned._python_surum_kapisi(surum, akis) is None, surum
            assert akis.getvalue() == "", f"{surum} için gereksiz uyarı basıldı"

    def test_the_minimum_matches_the_syntax_the_module_actually_uses(self, pinned):
        """Eşiğin ezbere yazılmadığının kanıtı: modül gerçekten PEP 604 kullanıyor
        ve PEP 604 tam olarak 3.10'da geldi. Biri gösterimden vazgeçerse ya da
        eşiği düşürürse ikisi ayrışır ve bu satır kırılır."""
        assert pinned._MIN_PYTHON == (3, 10)
        with open(pinned.__file__, encoding="utf-8") as f:
            kaynak = f.read()
        assert re.search(r"def \w+\([^)]*: *\w+ \| None", kaynak), (
            "modülde PEP 604 imzası kalmamış — o zaman 3.10 eşiği gerekçesini "
            "kaybetti, kapı ya kaldırılmalı ya gerekçesi yenilenmeli"
        )

    def test_the_guard_runs_before_anything_that_could_break_on_3_9(self, pinned):
        """Kapının YERİ işlevinin yarısı: PEP 604 taşıyan ilk def'ten sonra
        konursa 3.9'da modül kapı çalışmadan TypeError ile ölür ve arıza aynen
        geri gelir. Yapısal olarak sınanıyor, çünkü hatırlamaya bağlanamaz."""
        with open(pinned.__file__, encoding="utf-8") as f:
            kaynak = f.read()
        kapi = kaynak.index("_python_surum_kapisi(sys.version_info")
        pep604 = re.search(r"def \w+\([^)]*: *\w+ \| None", kaynak).start()
        assert kapi < pep604, "sürüm kapısı PEP 604 imzasından SONRA koşuyor"
        # Kapının kendisi 3.9'da ayrıştırılabilir olmalı: bloğun içinde ne PEP 604
        # ne de `match` var. (3.9'da koşturamadığımız için sınırı burada bağlıyoruz.)
        blok = kaynak[:kapi]
        assert "|" not in blok.split("def _python_surum_kapisi", 1)[1], (
            "sürüm kapısının gövdesinde `|` var — 3.9'da ayrıştırılamayabilir"
        )
