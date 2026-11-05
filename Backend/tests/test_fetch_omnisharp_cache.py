"""İndirilen OmniSharp / .NET SDK ağacının "güncel" sayılma kuralını koruyan testler.

Hangi arızadan doğdu: bu repoda "damga dosyası varsa atla" deseni İKİ ayrı
denetimde arıza üretti — bir kere derleme cache'i eski build'i güncel damgalayıp
günün tüm güvenlik işini sessizce devre dışı bıraktı, bir kere de Monaco kopyası
eksik kaldığı halde güncel sayıldı. Buradaki üçüncü örnek en pahalısıydı:
`.version` damgası doğruyken gömülü .NET ağacı SDK içermiyordu ve C# hover
120 saniye asılıyordu.

Korunan kural: **damga tek başına kanıt değil; hedefin içine bakılmalı.**
Kapı iki yönden de sınanıyor — bozuk ağaç reddedilmeli AMA sağlam ağaç
gereksiz yere yeniden indirilmemeli (indirme 220-290 MB).
"""
import glob
import importlib.util
import io
import os
import re
import sys
import tarfile
import time
import zipfile

import pytest

_SPEC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "fetch_omnisharp.py",
)


@pytest.fixture(scope="module")
def fetch_mod():
    """scripts/ bir paket değil — dosyayı yolundan yükle. Testin bu dosyayı
    bulamaması da bir bulgudur, o yüzden atlanmıyor, patlıyor."""
    assert os.path.isfile(_SPEC_PATH), f"beklenen script yok: {_SPEC_PATH}"
    spec = importlib.util.spec_from_file_location("fetch_omnisharp", _SPEC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sabitle(fetch_mod, monkeypatch):
    """Kütüğü test verisine göre yeniden sabitler ve `_sabitle(anahtar, baytlar)`
    döndürür.

    Neden doğrulamayı monkeypatch'lemek YERİNE kütüğü değiştiriyoruz: gerçek
    asset'ler 48-290 MB ve testler ağa çıkmıyor, ama `verify_bytes`'ı kapatmak
    testi "doğrulama koşuyor mu" sorusuna kör bırakırdı — tam da bu depoda
    ölçülen sınıf. Sentetik arşivin GERÇEK özeti kütüğe yazılınca üretimdeki
    kod yolu birebir koşuyor, yalnız baytlar küçük.

    Gerçek kütüğün üstüne yazılıyor (kopyası alınarak): bir testin sabitlemediği
    anahtarlar hâlâ gerçek değerleriyle görünür, yani yanlış anahtar kullanan bir
    test sessizce geçmez."""
    pinned = fetch_mod.pinned_assets
    entries = dict(pinned.load_manifest()["assets"])
    monkeypatch.setattr(pinned, "load_manifest", lambda: {"assets": entries})

    def _sabitle(key: str, data: bytes) -> None:
        entries[key] = {
            "url": entries.get(key, {}).get("url", "https://ornek.invalid/x.zip"),
            "digest": "sha256:" + pinned.digest_of(data, "sha256"),
            "size": len(data),
        }

    return _sabitle


def _write_stamp(dest: str, value: str) -> None:
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, ".version"), "w", encoding="utf-8") as f:
        f.write(value)


def _sdk_zip(host_body: str) -> bytes:
    """Gerçek .NET SDK zip'inin minik taklidi: host + dolu bir `sdk/` klasörü, yani
    `_intact` gözünde sağlam bir ağaç. `host_body` hangi koşunun kurduğunu ayırt
    etmek için — testler ağın 290 MB'ını değil, KURULUM MEKANİĞİNİ sınıyor."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dotnet.exe", host_body)
        zf.writestr("sdk/10.0.100/Sdk.props", "")
    return buf.getvalue()


def _read(*parts: str) -> str:
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


class TestDamgaTekBasinaYetmez:
    def test_a_correct_stamp_is_rejected_when_the_expected_files_are_missing(
        self, fetch_mod, tmp_path
    ):
        """27 Tem 2026 arızasının indirme tarafındaki yüzü: damga "10.0.100" diyor
        ama ağaçta `sdk/` yok. Eski kod bunu "zaten güncel — atlandı" diye geçiyordu,
        yani bozuk kurulum kendini onarma şansını da kaybediyordu."""
        dest = str(tmp_path / "dotnet-osx-arm64")
        _write_stamp(dest, "10.0.100")
        with open(os.path.join(dest, "dotnet"), "w") as f:
            f.write("")
        assert fetch_mod._intact(dest, "10.0.100", ["dotnet", "sdk"]) is False

    def test_a_present_but_empty_marker_directory_is_rejected(self, fetch_mod, tmp_path):
        """Yarıda kesilen bir çıkarma boş klasör bırakabiliyor — klasörün VARLIĞI
        değil İÇERİĞİ kanıt."""
        dest = str(tmp_path / "dotnet-osx-arm64")
        _write_stamp(dest, "10.0.100")
        with open(os.path.join(dest, "dotnet"), "w") as f:
            f.write("")
        os.makedirs(os.path.join(dest, "sdk"), exist_ok=True)
        assert fetch_mod._intact(dest, "10.0.100", ["dotnet", "sdk"]) is False

    def test_a_stale_version_stamp_is_rejected_even_when_the_tree_looks_complete(
        self, fetch_mod, tmp_path
    ):
        """Sürüm yükseltmesi kaçırılmasın: ağaç sağlam ama damga eski."""
        dest = str(tmp_path / "dotnet-osx-arm64")
        _write_stamp(dest, "10.0.10")
        with open(os.path.join(dest, "dotnet"), "w") as f:
            f.write("")
        os.makedirs(os.path.join(dest, "sdk", "10.0.100"), exist_ok=True)
        assert fetch_mod._intact(dest, "10.0.100", ["dotnet", "sdk"]) is False

    def test_a_missing_stamp_is_rejected(self, fetch_mod, tmp_path):
        dest = str(tmp_path / "dotnet-osx-arm64")
        os.makedirs(os.path.join(dest, "sdk", "10.0.100"), exist_ok=True)
        with open(os.path.join(dest, "dotnet"), "w") as f:
            f.write("")
        assert fetch_mod._intact(dest, "10.0.100", ["dotnet", "sdk"]) is False


class TestSaglamAgacYenidenIndirilmez:
    def test_a_matching_stamp_with_a_populated_tree_is_accepted(self, fetch_mod, tmp_path):
        """Karşıt yön: kapı çok GENİŞ olmamalı ama çok DAR da olmamalı. Her build'de
        220-290 MB yeniden indirmek, kaçırılan bir bozuk kurulum kadar gerçek bir
        arızadır — sadece daha sessizdir."""
        dest = str(tmp_path / "dotnet-osx-arm64")
        _write_stamp(dest, "10.0.100")
        with open(os.path.join(dest, "dotnet"), "w") as f:
            f.write("")
        os.makedirs(os.path.join(dest, "sdk", "10.0.100"), exist_ok=True)
        assert fetch_mod._intact(dest, "10.0.100", ["dotnet", "sdk"]) is True


class TestPlatformaGoreIsaretler:
    def test_windows_looks_for_the_windows_host_executable(self, fetch_mod):
        assert fetch_mod.dotnet_markers("win-x64")[0] == "dotnet.exe"
        assert fetch_mod.omnisharp_marker("win-x64") == "OmniSharp.exe"

    def test_unix_platforms_look_for_the_extensionless_host(self, fetch_mod):
        assert fetch_mod.dotnet_markers("osx-arm64")[0] == "dotnet"
        assert fetch_mod.omnisharp_marker("osx-arm64") == "OmniSharp"

    def test_every_platform_requires_the_sdk_directory_not_just_the_host(self, fetch_mod):
        """Bu satır, arızanın tekrarını engelleyen tek şey: runtime paketi de host'u
        getiriyor, `sdk` işaretini istemek runtime'ı SDK'dan ayıran fark."""
        for plat in ("win-x64", "osx-arm64", "linux-x64"):
            assert "sdk" in fetch_mod.dotnet_markers(plat)

    def test_every_shipped_platform_has_an_embedded_sdk_asset(self, fetch_mod):
        """Windows eskiden bilerek dışarıda bırakılmıştı ("net472 → runtime
        gerekmez"). MSBuild açısından yanlıştı ve ürünün ANA kitlesi orada."""
        assert set(fetch_mod.DOTNET_ASSETS) == set(fetch_mod.ASSETS)

    def test_the_downloaded_dotnet_asset_is_an_sdk_and_not_a_runtime(self, fetch_mod):
        """URL'nin kendisi bir iddia: /Sdk/ yolundan indirilmeli. Biri bunu tekrar
        /Runtime/'a çevirirse C# zekası sessizce ölür ve semptom yine 120 sn asılma
        olur — o yüzden URL'nin kendisi teste bağlandı."""
        for plat, url in fetch_mod.DOTNET_ASSETS.items():
            assert "/Sdk/" in url, f"{plat}: SDK değil runtime indiriliyor → {url}"
            assert "dotnet-sdk-" in url, f"{plat}: {url}"


class TestEszamanliKoşularBirbiriniBozmaz:
    """Aynı checkout'ta iki build aynı anda koşabiliyor (CI matrisi, elle başlatılan
    ikinci bir `npm run build`). Sabit `dest + ".staging"` bunu tek bir paylaşılan
    dizine yığıyordu."""

    def test_each_install_run_gets_a_staging_directory_of_its_own(
        self, fetch_mod, tmp_path, monkeypatch
    ):
        """Garantinin kendisi: iki kurulum aynı staging yolunu ASLA paylaşmaz.
        Paylaşırlarsa biri diğerinin ağacını `rmtree` ile siler; bu test o yolu
        adıyla yakalıyor, çünkü ayıklanması en kolay hali bu."""
        dest = str(tmp_path / "dotnet-win-x64")
        seen: list[str] = []
        real_extract = fetch_mod._extract

        def spy(data, url, staging):
            seen.append(staging)
            real_extract(data, url, staging)

        monkeypatch.setattr(fetch_mod, "_extract", spy)
        fetch_mod._install(_sdk_zip("A"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)
        fetch_mod._install(_sdk_zip("B"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)
        assert seen[0] != seen[1], f"iki koşu aynı staging'i paylaştı: {seen[0]}"
        assert dest + ".staging" not in seen, "sabit staging adı geri gelmiş"

    def test_a_second_install_started_mid_extraction_does_not_destroy_the_first(
        self, fetch_mod, tmp_path, monkeypatch
    ):
        """Çakışmanın canlandırılmış hali: dıştaki koşu tam çıkarmayı bitirmişken
        içerde ikinci bir kurulum baştan sona koşuyor. Eski kodda içteki koşu
        dıştakinin staging'ini siliyordu ve dıştaki `os.replace` FileNotFoundError
        ile patlıyordu; hedefte de içtekinin ağacı kalıyordu."""
        dest = str(tmp_path / "dotnet-win-x64")
        real_extract = fetch_mod._extract
        nested_ran: list[str] = []

        def extract_then_reenter(data, url, staging):
            real_extract(data, url, staging)
            if nested_ran:
                return
            nested_ran.append(staging)
            fetch_mod._install(_sdk_zip("B"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)
            assert os.path.isfile(os.path.join(staging, "dotnet.exe")), (
                "ikinci koşu birincinin staging'ini götürdü"
            )

        monkeypatch.setattr(fetch_mod, "_extract", extract_then_reenter)
        fetch_mod._install(_sdk_zip("A"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)

        assert nested_ran, "iç içe koşu hiç çalışmadı — test kendini sınamıyor"
        # Son yazan kazanır; kabul edilen budur. Kabul EDİLMEYEN, iki ağacın
        # birbirine karışması ya da kurulumun patlaması.
        assert fetch_mod._intact(dest, "10.0.100", ["dotnet.exe", "sdk"]) is True
        assert _read(dest, "dotnet.exe") == "A"


class TestBasarisizKurulumIzBirakmaz:
    def test_a_failed_install_cleans_up_its_own_staging_directory(
        self, fetch_mod, tmp_path
    ):
        """Ölmüş/patlamış koşunun kalıntısı diskte kalmamalı: dotnet ağacı ~290 MB
        ve eski kod her başarısız çıkarmada bir tane bırakıyordu."""
        dest = str(tmp_path / "dotnet-win-x64")
        with pytest.raises(Exception):
            fetch_mod._install(b"bu bir zip degil", "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)
        assert glob.glob(dest + ".staging*") == []
        assert glob.glob(dest + ".old*") == []

    def test_a_failed_install_leaves_the_previous_working_tree_in_place(
        self, fetch_mod, tmp_path
    ):
        """Bozuk bir indirme çalışan kurulumu götürmemeli. Eski kod hedefi
        `rmtree` ile silip sonra takas ediyordu; bu testin koruduğu şey, silmenin
        artık BAŞARILI takastan sonraya alınmış olması."""
        dest = str(tmp_path / "dotnet-win-x64")
        fetch_mod._install(_sdk_zip("iyi"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)
        with pytest.raises(Exception):
            fetch_mod._install(b"bu bir zip degil", "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)
        assert fetch_mod._intact(dest, "10.0.100", ["dotnet.exe", "sdk"]) is True
        assert _read(dest, "dotnet.exe") == "iyi"

    def test_a_swap_that_fails_halfway_puts_the_previous_installation_back(
        self, fetch_mod, tmp_path, monkeypatch
    ):
        """Eski sırada (`rmtree(dest)` → `os.replace`) arada ne eski ne yeni ağacın
        olduğu bir pencere vardı; orada ölen süreç `.version` damgasıyla birlikte
        ÇALIŞAN kurulumu da götürüyordu. Takas artık silme değil rename olduğu için
        geri alınabiliyor. Bu testin `os.replace`'i patlatması, o pencereyi süreç
        öldürmeden gözlemlemenin tek ucuz yolu."""
        dest = str(tmp_path / "dotnet-win-x64")
        fetch_mod._install(_sdk_zip("iyi"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)

        def boom(src, dst):
            raise OSError("takas tutmadı")

        monkeypatch.setattr(fetch_mod.os, "replace", boom)
        with pytest.raises(OSError):
            fetch_mod._install(_sdk_zip("yeni"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)
        monkeypatch.undo()
        assert fetch_mod._intact(dest, "10.0.100", ["dotnet.exe", "sdk"]) is True
        assert _read(dest, "dotnet.exe") == "iyi"

    def test_a_stale_leftover_is_pruned_but_a_fresh_neighbour_is_left_alone(
        self, fetch_mod, tmp_path
    ):
        """Kalıntı süpürme iki yönlü: eski çöp gitmeli AMA taze bir dizin ŞU AN
        koşan komşu build'in staging'i olabilir — ona dokunmak, düzeltilen arızanın
        ta kendisi olurdu."""
        dest = str(tmp_path / "dotnet-win-x64")
        stale = dest + ".staging-1234-deadbeef"
        fresh = dest + ".staging-5678-cafebabe"
        for d in (stale, fresh):
            os.makedirs(d)
        old = time.time() - fetch_mod._STALE_LEFTOVER_SECONDS - 60
        os.utime(stale, (old, old))

        fetch_mod._install(_sdk_zip("A"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)

        assert not os.path.exists(stale), "bayat kalıntı süpürülmedi"
        assert os.path.isdir(fresh), "canlı komşunun staging'i silinmiş"


class TestKurulumSonrasiAgacGuncelSayilir:
    """Düzeltmenin bozmaması gereken yön: `_install` biter bitmez `_intact` "güncel"
    demeli. Demezse her build 220-290 MB'ı yeniden indirir — kaçırılan bozuk kurulum
    kadar gerçek, sadece daha sessiz bir arıza."""

    def test_a_freshly_installed_tree_is_immediately_considered_current(
        self, fetch_mod, tmp_path
    ):
        dest = str(tmp_path / "dotnet-win-x64")
        fetch_mod._install(_sdk_zip("A"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)
        assert fetch_mod._intact(dest, "10.0.100", ["dotnet.exe", "sdk"]) is True

    def test_the_version_stamp_ends_up_inside_the_target_and_not_beside_it(
        self, fetch_mod, tmp_path
    ):
        """Damganın yeri dünkü arızanın düzeltmesiydi: hedefin İÇİNDE olmalı, çünkü
        `_intact` oraya bakıyor ve hedefle birlikte atomik takas edilmesi gereken de o."""
        dest = str(tmp_path / "dotnet-win-x64")
        fetch_mod._install(_sdk_zip("A"), "x.zip", dest, "10.0.100", ["dotnet.exe"], asset_key=None)
        assert _read(dest, ".version") == "10.0.100"
        assert not os.path.exists(dest + ".version")

    def test_a_sound_installation_is_not_downloaded_a_second_time(
        self, fetch_mod, tmp_path, monkeypatch, sabitle
    ):
        """Uçtan uca kapı testi: `fetch_dotnet` iki kez koşuyor, indirme BİR kez
        olmalı. Buradaki asıl konu maliyet — win-x64 SDK'sı 290 MB.

        Sentetik arşiv kütüğe sabitleniyor (bkz. `sabitle`): bütünlük kontrolü
        eklendiğinde bu test, 239 baytlık taklidi 290 MB'lık gerçek özetle
        karşılaştırıp haklı olarak patladı. Doğrulamayı kapatmak yerine taklit
        kütüğe tanıtıldı — kontrol koşuyor VE atlama davranışı hâlâ sınanıyor.
        `blob` bir kez hesaplanıp saklanıyor: `_sdk_zip` zip üyelerine o anki saati
        yazıyor, iki çağrı saniye sınırını geçerse farklı bayt üretirdi."""
        monkeypatch.setattr(fetch_mod, "DEST", str(tmp_path))
        blob = _sdk_zip("A")
        sabitle("dotnet-sdk/win-x64", blob)
        calls: list[str] = []

        def fake_download(url):
            calls.append(url)
            return blob

        monkeypatch.setattr(fetch_mod, "_download", fake_download)
        fetch_mod.fetch_dotnet("win-x64")       # .zip asset'i → ağ yok, bellekten
        fetch_mod.fetch_dotnet("win-x64")
        assert len(calls) == 1, f"ikinci koşu yeniden indirdi: {calls}"
        assert fetch_mod._intact(
            str(tmp_path / "dotnet-win-x64"),
            fetch_mod._stamp_value(fetch_mod.DOTNET_VERSION, "dotnet-sdk/win-x64"),
            fetch_mod.dotnet_markers("win-x64"),
        ) is True

class TestArsivHedefinDisinaYazamaz:
    """Arşiv çıkarmanın içerme garantisi.

    Hangi arızadan doğdu (dış denetim, 2026-07-28): `_extract` içinde
    `except TypeError: tf.extractall(staging)` diye bir yedek dal vardı, `filter`
    parametresini tanımayan eski Python'lar için. O dal HİÇBİR içerme kontrolü
    yapmıyordu — gerçek 3.9.6 ile ölçüldü: `../ESCAPED.txt` hedefin dışına yazıldı,
    sembolik bağ + üstüne yazma iki aşamada dışarı çıktı, dizin ve FIFO de öyle.
    Teorik bir yol da değildi: prebuild `python … || python3 …` diyor ve Homebrew
    Python'ı olmayan makinede `python3` stok macOS'un 3.9.6'sı oluyor.

    Kapı iki yönden de sınanıyor: kaçan üye reddedilmeli AMA normal arşiv sorunsuz
    açılmalı — çıkarmayı bozmak 220-290 MB'lık yükü tümden kullanılamaz kılardı.
    """

    @staticmethod
    def _tar_with(members) -> bytes:
        """members: (TarInfo, veri|None) çiftleri."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for info, data in members:
                tf.addfile(info, io.BytesIO(data) if data is not None else None)
        return buf.getvalue()

    @staticmethod
    def _regular(name: str, data: bytes = b"x"):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        return info, data

    @staticmethod
    def _symlink(name: str, target: str):
        info = tarfile.TarInfo(name)
        info.type = tarfile.SYMTYPE
        info.linkname = target
        return info, None

    def test_a_member_whose_name_climbs_out_of_the_target_is_refused(
        self, fetch_mod, tmp_path
    ):
        """Arızanın birebir kendisi. Bu test kırılırsa build makinesinde hedef
        klasörün dışına yazılabiliyor demektir."""
        staging = tmp_path / "staging"
        staging.mkdir()
        data = self._tar_with([self._regular("../KACAN.txt")])
        with pytest.raises(Exception):
            fetch_mod._extract(data, "x.tar.gz", str(staging))
        assert not (tmp_path / "KACAN.txt").exists()

    def test_a_symlink_pointing_outside_the_target_is_refused(self, fetch_mod, tmp_path):
        """Tek başına zararsız görünen bağ, ikinci aşamada üstüne yazmak için
        kullanılıyordu — ölçümde iki aşamalı yazma başarılı olmuştu."""
        staging = tmp_path / "staging"
        staging.mkdir()
        data = self._tar_with([self._symlink("kotu", "../DISARIDA")])
        with pytest.raises(Exception):
            fetch_mod._extract(data, "x.tar.gz", str(staging))

    def test_an_ordinary_archive_still_extracts(self, fetch_mod, tmp_path):
        """Karşıt yön: kapı çok DAR olmamalı, yoksa gömülü SDK hiç kurulamaz."""
        staging = tmp_path / "staging"
        staging.mkdir()
        data = self._tar_with([self._regular("alt/dosya.txt", b"merhaba")])
        fetch_mod._extract(data, "x.tar.gz", str(staging))
        assert (staging / "alt" / "dosya.txt").read_bytes() == b"merhaba"

    def test_extraction_refuses_to_run_at_all_without_filter_support(
        self, fetch_mod, tmp_path, monkeypatch
    ):
        """Eski Python'da SESSİZCE korumasız çıkarmak yerine gürültülü hata.
        Bu bir build script'i; kırılmak, korumasız yazmaktan iyidir."""
        staging = tmp_path / "staging"
        staging.mkdir()
        monkeypatch.setattr(fetch_mod, "_TAR_FILTER_SUPPORTED", False)
        data = self._tar_with([self._regular("zararsiz.txt")])
        with pytest.raises(RuntimeError, match="filter"):
            fetch_mod._extract(data, "x.tar.gz", str(staging))

    @pytest.mark.baglar_gerekli

    def test_a_symlink_named_like_the_executable_is_refused_before_chmod(
        self, fetch_mod, tmp_path, monkeypatch
    ):
        """`os.chmod` bağ takip ediyor: arşivde `OmniSharp` ADINDA bir bağ varsa
        hedefin DIŞINDAKİ dosyanın modu değişiyordu (ölçüldü: 0600 → 0755)."""
        dest = tmp_path / "osx-arm64"
        kurban = tmp_path / "kurban.txt"
        kurban.write_text("gizli")
        # Windows' chmod only toggles the read-only attribute (0o600 reads back as 0o666
        # and 0o755 would leave it unchanged), so the victim starts read-only there: a
        # followed chmod then flips it writable and the comparison below sees it.
        os.chmod(kurban, 0o400 if sys.platform == "win32" else 0o600)
        once = os.stat(kurban).st_mode

        def _sahte_extract(_data, _url, staging):
            os.symlink(str(kurban), os.path.join(staging, "OmniSharp"))

        monkeypatch.setattr(fetch_mod, "_extract", _sahte_extract)
        with pytest.raises(RuntimeError, match="sembolik bağ"):
            fetch_mod._install(b"", "x.tar.gz", str(dest), "v1", ["OmniSharp"], asset_key=None)
        sonra = os.stat(kurban).st_mode
        os.chmod(kurban, 0o600)     # let the temp-dir cleanup delete a read-only file
        assert sonra == once
        if sys.platform != "win32":
            assert oct(sonra)[-3:] == "600"

class TestSembolikBagKuruluSayilmaz:
    """Sembolik bağ üzerinden kalıcı kod çalıştırma zincirini kapatan testler.

    Hangi arızadan doğdu (dış denetim, 2026-07-28, ikinci model ailesi): tek
    seferlik bir yazma, kalıcı kod çalıştırmaya dönüşebiliyordu. Zincir:
    `third_party/omnisharp/<plat>/OmniSharp` yerine dışarıyı gösteren bir bağ
    konur → `_intact()` bunu "sağlam kurulum" sayıp indirmeyi ATLAR → ürün
    `_resolve_binary()` ile o bağı bulur ve ÇALIŞTIRIR. Ölçüldü: `_intact()`
    dışarıdaki bir binary için True döndü.

    Sebep her üç yerde de aynı: `os.path.exists` ve `os.chmod` bağ TAKİP EDİYOR.
    """

    @pytest.mark.baglar_gerekli

    def test_a_symlinked_marker_is_not_a_healthy_installation(self, fetch_mod, tmp_path):
        """Zincirin ilk halkası. Bu test kırılırsa indirme atlanmaya ve dışarıdaki
        bir binary 'kurulu OmniSharp' sayılmaya geri döner."""
        disarida = tmp_path / "DISARIDAKI"
        disarida.write_text("#!/bin/sh\n")
        dest = tmp_path / "osx-arm64"
        dest.mkdir()
        os.symlink(str(disarida), str(dest / "OmniSharp"))
        (dest / ".version").write_text("v1")
        assert fetch_mod._intact(str(dest), "v1", ["OmniSharp"]) is False

    def test_a_real_marker_file_is_still_accepted(self, fetch_mod, tmp_path):
        """Karşıt yön: gerçek dosya reddedilirse her build yeniden indirir."""
        dest = tmp_path / "osx-arm64"
        dest.mkdir()
        (dest / "OmniSharp").write_text("")
        (dest / ".version").write_text("v1")
        assert fetch_mod._intact(str(dest), "v1", ["OmniSharp"]) is True

    @pytest.mark.baglar_gerekli

    def test_a_symlink_named_like_the_stamp_is_refused(self, fetch_mod, tmp_path, monkeypatch):
        """Damga yazımı da bağ takip ediyordu — yazma hedefin dışına gidiyordu."""
        dest = tmp_path / "osx-arm64"
        kurban = tmp_path / "kurban.txt"
        kurban.write_text("dokunulmamis")

        def _sahte_extract(_data, _url, staging):
            os.symlink(str(kurban), os.path.join(staging, ".version"))

        monkeypatch.setattr(fetch_mod, "_extract", _sahte_extract)
        with pytest.raises(RuntimeError, match="sembolik bağ"):
            fetch_mod._install(b"", "x.tar.gz", str(dest), "v1", [], asset_key=None)
        assert kurban.read_text() == "dokunulmamis"


class TestAdreslerTekKaynaktan:
    """İndirilen her şeyin adresi `pinned_assets.json`'da, özetiyle AYNI satırda.

    Hangi arızadan doğdu: adres burada bir f-string'ken, özet başka bir dosyada
    duracaktı. O an "birbiriyle uyuşması gereken iki yer" doğuyor ve bu depoda
    2026-07-27/28'de bulunan arızaların hepsi tam olarak o şekildeydi. Bir sürümü
    yükseltip özeti güncellemeyi unutmak, doğrulamayı ilk indirmede patlatır
    (gürültülü, iyi); URL'yi güncelleyip özeti unutmak ise sessizce YANLIŞ asset'i
    doğrulanmış gibi kurardı — bu testler o ikinci yolun var olmadığını sınıyor.
    """

    def test_every_omnisharp_url_comes_from_the_pinned_manifest(self, fetch_mod):
        entries = fetch_mod.pinned_assets.load_manifest()["assets"]
        assert fetch_mod.ASSETS, "ASSETS boş — türetme sessizce hiçbir şey üretmemiş"
        for plat, url in fetch_mod.ASSETS.items():
            assert url == entries[f"omnisharp/{plat}"]["url"]

    def test_every_dotnet_url_comes_from_the_pinned_manifest(self, fetch_mod):
        entries = fetch_mod.pinned_assets.load_manifest()["assets"]
        assert fetch_mod.DOTNET_ASSETS
        for plat, url in fetch_mod.DOTNET_ASSETS.items():
            assert url == entries[f"dotnet-sdk/{plat}"]["url"]

    def test_the_declared_versions_still_agree_with_the_pinned_urls(self, fetch_mod):
        """`VERSION` artık URL kurmuyor, yalnız damgaya giriyor — yani kütükteki
        adres yükseltilip bu sabit unutulursa kimse fark etmez ve damga yanlış
        sürümü söyler. Ucuz kapı: sabit, adresin içinde geçmeli."""
        for plat, url in fetch_mod.ASSETS.items():
            assert fetch_mod.VERSION in url, f"{plat}: {url}"
        for plat, url in fetch_mod.DOTNET_ASSETS.items():
            assert fetch_mod.DOTNET_VERSION in url, f"{plat}: {url}"

    def test_a_platform_missing_from_the_manifest_is_a_loud_failure(self, fetch_mod):
        """Bilinmeyen anahtar sessizce boş dönmemeli: dönseydi o platform için
        doğrulama atlanmış olurdu ve build yeşil kalırdı."""
        with pytest.raises(KeyError):
            fetch_mod.pinned_assets.asset("omnisharp/olmayan-platform")


class TestBozukBaytlarKurulmaz:
    """Bütünlük kapısı, İKİ YÖNDEN de.

    Neden var: bu depo 13 yerden yürütülebilir içerik indiriyordu ve hiçbirinde
    checksum yoktu; indirilenlerin hepsi installer'a girip son kullanıcıda
    çalışıyor. Kapının çok GENİŞ olmaması kadar çok DAR olmaması da sınanıyor —
    doğrulamayı yanlış kurup her arşivi reddetmek, ürünün hiç kurulamaması demek.
    """

    KEY = "dotnet-sdk/win-x64"

    def test_bytes_that_do_not_match_the_pinned_digest_are_refused(
        self, fetch_mod, tmp_path, sabitle
    ):
        """Kapının asıl işi. `_sdk_zip("A")` ile `_sdk_zip("B")` aynı UZUNLUKTA,
        yani burada reddi sağlayan şey boyut değil özetin kendisi."""
        dest = str(tmp_path / "dotnet-win-x64")
        sabitle(self.KEY, _sdk_zip("A"))
        with pytest.raises(fetch_mod.pinned_assets.IntegrityError):
            fetch_mod._install(
                _sdk_zip("B"), "x.zip", dest, "10.0.100+abc", ["dotnet.exe"], asset_key=self.KEY
            )

    def test_a_refused_download_writes_nothing_at_all_to_the_target(
        self, fetch_mod, tmp_path, sabitle
    ):
        """Doğrulama çıkarmadan ÖNCE olmalı, sonra değil: arşiv çıkarmanın kendisi
        bir saldırı yüzeyi (bu dosyada zaten iki ayrı kaçış arızası düzeltildi) ve
        bozuk baytlar o yüzeye hiç varmamalı. Staging'in de hiç doğmaması ölçülüyor,
        çünkü "açtım sonra sildim" ile "hiç açmadım" aynı şey değil."""
        dest = str(tmp_path / "dotnet-win-x64")
        sabitle(self.KEY, _sdk_zip("A"))
        with pytest.raises(fetch_mod.pinned_assets.IntegrityError):
            fetch_mod._install(
                _sdk_zip("B"), "x.zip", dest, "10.0.100+abc", ["dotnet.exe"], asset_key=self.KEY
            )
        assert not os.path.exists(dest), "reddedilen arşiv hedefe yazmış"
        assert glob.glob(dest + ".staging*") == [], "reddedilen arşiv açılmış"

    def test_a_truncated_download_is_refused_on_size_before_the_digest(
        self, fetch_mod, tmp_path, sabitle
    ):
        """Yarım inen dosyanın teşhisi boyutta: "301667702 bayt bekleniyordu, 239
        geldi" mesajı iki hex dizisini karşılaştırmaktan hızlı okunuyor."""
        dest = str(tmp_path / "dotnet-win-x64")
        sabitle(self.KEY, _sdk_zip("uzunca-bir-govde"))
        with pytest.raises(fetch_mod.pinned_assets.IntegrityError, match="boyut"):
            fetch_mod._install(
                _sdk_zip("A"), "x.zip", dest, "10.0.100+abc", ["dotnet.exe"], asset_key=self.KEY
            )
        assert not os.path.exists(dest)

    def test_bytes_that_match_the_pinned_digest_are_installed_normally(
        self, fetch_mod, tmp_path, sabitle
    ):
        """Karşıt yön: kapı çok DAR olmamalı. Bu test kırılırsa gömülü SDK hiçbir
        makinede kurulamaz, yani C# zekası tümden ölür."""
        dest = str(tmp_path / "dotnet-win-x64")
        blob = _sdk_zip("A")
        sabitle(self.KEY, blob)
        stamp = fetch_mod._stamp_value("10.0.100", self.KEY)
        fetch_mod._install(blob, "x.zip", dest, stamp, ["dotnet.exe"], asset_key=self.KEY)
        assert _read(dest, "dotnet.exe") == "A"
        assert fetch_mod._intact(dest, stamp, ["dotnet.exe", "sdk"]) is True

    def test_the_dotnet_download_path_verifies_what_it_downloaded(
        self, fetch_mod, tmp_path, monkeypatch, sabitle
    ):
        """`_install`'ın `asset_key` parametresi varsayılanı `None`, yani doğrulamayı
        kapatmanın kolay yolu anahtarı çağrı yerinde geçirmemek olurdu — sessizce.
        Bu test üretimdeki çağıranı uçtan uca koşturup anahtarın gerçekten
        geçirildiğini ölçüyor; parametreyi düşüren bir düzenleme burada patlar."""
        monkeypatch.setattr(fetch_mod, "DEST", str(tmp_path))
        sabitle(self.KEY, _sdk_zip("beklenen"))
        monkeypatch.setattr(fetch_mod, "_download", lambda url: _sdk_zip("degistirilmis"))
        with pytest.raises(fetch_mod.pinned_assets.IntegrityError):
            fetch_mod.fetch_dotnet("win-x64")
        assert not os.path.exists(tmp_path / "dotnet-win-x64")

    def test_the_omnisharp_download_path_verifies_what_it_downloaded(
        self, fetch_mod, tmp_path, monkeypatch, sabitle
    ):
        """Aynı ölçüm ikinci çağıran için: iki yolu birden kapatmayan bir düzeltme,
        kapatılmayan yol üzerinden hâlâ keyfi ikili kurdurur."""
        monkeypatch.setattr(fetch_mod, "DEST", str(tmp_path))
        sabitle("omnisharp/osx-arm64", _sdk_zip("beklenen"))
        monkeypatch.setattr(fetch_mod, "_download", lambda url: _sdk_zip("degistirilmis"))
        with pytest.raises(fetch_mod.pinned_assets.IntegrityError):
            fetch_mod.fetch("osx-arm64")
        assert not os.path.exists(tmp_path / "osx-arm64")


class TestDamgaOzetiDeKapsar:
    """Damga sürüm numarasına ek olarak sabitlenmiş özetin önekini taşır.

    Hangi boşluktan doğdu: sürüm numarası kimliğin tamamı değil. Yayıncı aynı
    etiketi yeniden yayınlarsa (GitHub release asset'leri değiştirilebiliyor)
    "10.0.100" damgalı eski kurulum hâlâ güncel görünür, indirme atlanır ve
    doğrulama HİÇ KOŞMAZ — yani en çok ihtiyaç duyulduğu anda kontrol devre dışı.
    """

    KEY = "dotnet-sdk/win-x64"

    def test_the_stamp_still_starts_with_the_human_readable_version(
        self, fetch_mod, sabitle
    ):
        """Damga hâlâ elle okunabilmeli: sorun ayıklarken `.version` dosyasına bakan
        insan önce sürümü görüyor."""
        sabitle(self.KEY, _sdk_zip("A"))
        assert fetch_mod._stamp_value("10.0.100", self.KEY).startswith("10.0.100+")

    def test_the_stamp_changes_when_the_pinned_digest_changes(self, fetch_mod, sabitle):
        sabitle(self.KEY, _sdk_zip("A"))
        onceki = fetch_mod._stamp_value("10.0.100", self.KEY)
        sabitle(self.KEY, _sdk_zip("B"))
        assert fetch_mod._stamp_value("10.0.100", self.KEY) != onceki

    def test_an_asset_republished_under_the_same_version_is_downloaded_again(
        self, fetch_mod, tmp_path, monkeypatch, sabitle
    ):
        """Uçtan uca: sürüm numarası DEĞİŞMEDEN kütükteki özet değişiyor. Eski
        damgada bu kurulum "zaten 10.0.100 — atlandı" diye geçilirdi ve makinede
        eski baytlar kalırdı."""
        monkeypatch.setattr(fetch_mod, "DEST", str(tmp_path))
        dest = str(tmp_path / "dotnet-win-x64")
        indirilen = {"blob": _sdk_zip("A")}
        calls: list[str] = []

        def fake_download(url):
            calls.append(url)
            return indirilen["blob"]

        monkeypatch.setattr(fetch_mod, "_download", fake_download)
        sabitle(self.KEY, indirilen["blob"])
        fetch_mod.fetch_dotnet("win-x64")
        assert _read(dest, "dotnet.exe") == "A"

        # Yayıncı aynı sürüm numarasıyla yeni baytlar yayınladı, kütük tazelendi.
        indirilen["blob"] = _sdk_zip("B")
        sabitle(self.KEY, indirilen["blob"])
        fetch_mod.fetch_dotnet("win-x64")

        assert len(calls) == 2, "özet değiştiği halde bayat kurulum güncel sayıldı"
        assert _read(dest, "dotnet.exe") == "B"



class TestUretimYolundaDogrulamaAtlanamaz:
    """`_install`'ın `asset_key` parametresi varsayılan olarak `None`, yani doğrulama
    teknik olarak ATLANABİLİR. Bu bilinçli: testler kütükte karşılığı olmayan sentetik
    arşivler enjekte ediyor ve onların sabitlenmiş bir özeti yok.

    Ama bir güvenlik kapısının "unutulabilir" olması, bu deponun tam olarak ödediği
    bedel. Davranış testleri yalnız BUGÜNKÜ iki çağıranı bağlıyor; yarın eklenen
    üçüncü bir çağıran anahtarı geçirmezse hiçbir test kırılmaz ve indirilen baytlar
    sessizce doğrulanmadan kurulur.

    Bu sınıf o boşluğu kaynak seviyesinde kapatıyor: üretim dosyasındaki HER
    `_install` çağrısı bir anahtar geçirmek ZORUNDA. `asset_key=None` yazarak kapıyı
    kapatma yolu da böylece kapanıyor — davranış testinin yakalayamadığı şey buydu.
    """

    @staticmethod
    def _uretim_cagrilari() -> list[str]:
        with open(_SPEC_PATH, encoding="utf-8") as f:
            src = f.read()
        # `def _install(` tanımını atla, yalnız çağrıları al.
        return [
            m.group(0)
            for m in re.finditer(r"(?<!def )_install\((?:[^()]|\([^()]*\))*\)", src)
        ]

    def test_the_production_calls_were_actually_found(self):
        """Kendini sınayan test: regex tutmazsa aşağıdaki test BOŞ küme üzerinde koşar
        ve sessizce geçer — yani kapı, kırılmadan kaybolur. Bugün iki çağıran var
        (`fetch` ve `fetch_dotnet`); sayı değişirse bu testin de gözden geçirilmesi
        gerekiyor, o yüzden sayıya bağlandı."""
        assert len(self._uretim_cagrilari()) == 2, (
            "fetch_omnisharp.py'deki _install çağrıları ayrıştırılamadı ya da sayısı "
            "değişti; bu testin deseni güncellenmeli — sessizce boş geçmesin"
        )

    def test_every_production_install_passes_a_pinned_asset_key(self):
        """Asıl garanti: üretim yolunda doğrulamasız kurulum yok."""
        for cagri in self._uretim_cagrilari():
            # ⚠️ Eskiden burada `\bkey\b` aranıyordu ve bu ATLATILABİLİRDİ:
            # `_install(..., _stamp_value(VERSION, key), [marker])` yazınca `key`
            # kelimesi geçiyor, kapı "geçti" diyor, ama `asset_key` DÜŞMÜŞ oluyor.
            # Dış denetimde (2026-07-28) tam bu refactor gösterildi ve iki testi de
            # sağ geçti. Artık anahtar-kelime biçiminin KENDİSİ aranıyor; `_install`
            # imzası da `*` ile keyword-only olduğu için konumsal geçmek mümkün değil.
            assert "asset_key=" in cagri, (
                "bir _install çağrısı `asset_key=` geçirmiyor, yani indirilen "
                f"baytlar doğrulanmadan kurulur:\n  {cagri}"
            )
            assert "asset_key=None" not in cagri, (
                f"doğrulama açıkça kapatılmış:\n  {cagri}"
            )


class TestUstDizinBagiKuruluSayilmaz:
    """`_intact` kapısının YAPRAKTA değil YOL üzerinde olduğunu koruyan testler.

    Hangi arızadan doğdu (dış denetim, 2026-07-28): 27 Tem'de kapatılan sembolik
    bağ zincirinin kapısı `dest/OmniSharp` yaprağına konmuştu. Ama `dest` DİZİNİNİN
    kendisi bağ olduğunda içindeki dosya gerçek dosyadır — `islink(yaprak)` False
    döner, kapı ateşlenmez, `_intact` True der ve indirme KALICI olarak atlanır.
    Canlı ölçüldü ve ürün tarafındaki `_resolve_binary` de aynı ağacı döndürüyordu.

    Kapatma dersi: bir sınıfı kapatmak, o sınıfın başka biçimlerini adlandırıp
    sınamayı gerektiriyor. Tek yolu kapatmak sınıfı kapatmıyor.
    """

    @pytest.mark.baglar_gerekli

    def test_a_symlinked_destination_directory_is_not_a_healthy_installation(
        self, fetch_mod, tmp_path
    ):
        disarida = tmp_path / "DISARIDAKI_AGAC"
        disarida.mkdir()
        (disarida / "OmniSharp").write_text("#!/bin/sh\n")
        (disarida / ".version").write_text("v1")
        kurulum = tmp_path / "third_party" / "omnisharp"
        kurulum.mkdir(parents=True)
        dest = kurulum / "osx-arm64"
        os.symlink(str(disarida), str(dest))
        assert fetch_mod._intact(str(dest), "v1", ["OmniSharp"]) is False

    def test_a_real_destination_directory_is_still_accepted(self, fetch_mod, tmp_path):
        """Karşıt yön: kapı çok GENİŞ olursa her build 220-290 MB yeniden iner."""
        dest = tmp_path / "osx-arm64"
        dest.mkdir()
        (dest / "OmniSharp").write_text("")
        (dest / ".version").write_text("v1")
        assert fetch_mod._intact(str(dest), "v1", ["OmniSharp"]) is True

    @pytest.mark.baglar_gerekli

    def test_a_marker_reachable_only_through_an_escaping_path_is_refused(
        self, fetch_mod, tmp_path
    ):
        """Ara bileşen bağ: işaretin kendisi gerçek dosya olsa bile yol dışarı
        çıkıyorsa kurulum sağlam sayılmamalı."""
        disarida = tmp_path / "DISARIDA"
        (disarida / "ic").mkdir(parents=True)
        (disarida / "ic" / "OmniSharp").write_text("")
        dest = tmp_path / "osx-arm64"
        dest.mkdir()
        (dest / ".version").write_text("v1")
        os.symlink(str(disarida / "ic"), str(dest / "alt"))
        assert fetch_mod._intact(str(dest), "v1", [os.path.join("alt", "OmniSharp")]) is False
