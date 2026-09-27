"""C# zekasının çalışma zamanı ortamını koruyan testler.

Hangi arızadan doğdu (2026-07-27): C# hover isteği ~120 saniye asılıp tarayıcıda
"Failed to fetch" ile düşüyordu. Kök neden, gömülü yükün .NET **SDK** değil
.NET **runtime** olmasıydı: OmniSharp proje yüklemek için MSBuild'i SDK'dan
çözüyor, çözemeyince `initialize` isteğine NE result NE error frame'i gönderiyor
ve süreci de kapatmıyor — yani tek geri bildirim uzun bir sessizlik oluyordu.

Bu dosya iki yüzeyi birlikte koruyor:
  1. Gömülü .NET ağacının SDK olduğunun doğrulanması (host'un varlığı yetmez),
  2. O ortam kurulamadığında ürünün ASILMADAN, sebebini söyleyerek düşmesi.

Kapı iki yönden de sınanıyor: eksik SDK reddedilmeli AMA sağlam kurulum
kabul edilmeli, ve sistemde .NET varken ürün "yok" diye erken pes etmemeli.
Bu projede kapıların yalnız "çok dar değil" yönü sınandığı için üç ayrı arıza
üretildi; buradaki her testin karşıt yönü de yazılı.
"""
import asyncio
import os
import pathlib
import sys
import tempfile
import time

import pytest

from app.omnisharp import omnisharp_manager as om


def _make_dotnet_tree(root: str, plat: str, *, with_sdk: bool, sdk_empty: bool = False) -> str:
    """Gömülü .NET ağacının taklidi. `with_sdk=False` tam olarak 27 Tem 2026'daki
    bozuk kurulumu üretir: `dotnet` host'u var, `sdk/` yok."""
    dest = os.path.join(root, f"dotnet-{plat}")
    os.makedirs(dest, exist_ok=True)
    exe = "dotnet.exe" if plat.startswith("win") else "dotnet"
    with open(os.path.join(dest, exe), "w") as f:
        f.write("")
    if with_sdk:
        sdk = os.path.join(dest, "sdk")
        os.makedirs(sdk, exist_ok=True)
        if not sdk_empty:
            os.makedirs(os.path.join(sdk, "10.0.100"), exist_ok=True)
    return dest


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """`_omnisharp_roots` yerine tek geçici kök koy — gerçek third_party ağacı
    testin sonucunu belirlemesin (makinede SDK varsa testler yalancı yeşil olur)."""
    root = str(tmp_path)
    monkeypatch.setattr(om, "_omnisharp_roots", lambda: [root])
    return root


class TestGomuluSdkDogrulamasi:
    def test_a_runtime_only_tree_is_rejected_even_though_the_dotnet_host_exists(
        self, fake_root, monkeypatch
    ):
        """27 Tem 2026 arızasının birebir kendisi: .NET runtime paketi de `dotnet`
        host'unu getiriyor, yani host'un varlığı SDK olduğunu KANITLAMIYOR. Bu test
        kırılırsa 120 saniyelik asılma geri gelmiş demektir."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        _make_dotnet_tree(fake_root, "osx-arm64", with_sdk=False)
        assert om._embedded_dotnet_root() is None

    def test_a_tree_with_a_populated_sdk_directory_is_accepted(self, fake_root, monkeypatch):
        """Karşıt yön: kapı çok GENİŞ olmamalı ama çok DAR da olmamalı — sağlam
        kurulum reddedilirse C# zekası hiç çalışmaz."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        expected = _make_dotnet_tree(fake_root, "osx-arm64", with_sdk=True)
        assert om._embedded_dotnet_root() == expected

    def test_an_sdk_directory_that_exists_but_is_empty_is_rejected(self, fake_root, monkeypatch):
        """Yarıda kesilmiş bir çıkarma boş `sdk/` bırakabiliyor; klasörün VARLIĞI
        değil İÇERİĞİ kanıt."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        _make_dotnet_tree(fake_root, "osx-arm64", with_sdk=True, sdk_empty=True)
        assert om._embedded_dotnet_root() is None

    def test_windows_resolves_the_embedded_sdk_through_the_same_code_path_as_macos(
        self, fake_root, monkeypatch
    ):
        """Windows eskiden `_spawn_env` içinde erkenden `None` dönüp bu mekanizmayı
        tamamen atlıyordu ("net472 → runtime gerekmez"); bu .NET Framework için
        doğru ama MSBuild için yanlıştı. Windows ürünün ANA kitlesi ve macOS'tan
        sınanamıyor — tek yol iki platformu aynı kod yolunda tutmak."""
        monkeypatch.setattr(om, "_platform_key", lambda: "win-x64")
        expected = _make_dotnet_tree(fake_root, "win-x64", with_sdk=True)
        assert om._embedded_dotnet_root() == expected


class TestSpawnOrtami:
    def test_the_spawn_environment_pins_dotnet_root_and_puts_it_on_path(
        self, fake_root, monkeypatch
    ):
        """DOTNET_ROOT tek başına yetmiyor: OmniSharp MSBuild'i Microsoft.Build.Locator
        ile çözerken `dotnet` komutunu ÇALIŞTIRIYOR, o da PATH'ten aranıyor."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        root = _make_dotnet_tree(fake_root, "osx-arm64", with_sdk=True)
        env = om._spawn_env()
        assert env is not None
        assert env["DOTNET_ROOT"] == root
        # Not "Major": that picks the lowest major >= 6, i.e. a system .NET 8 when
        # one exists, and MSBuild then rejects the bundled SDK 10 (measured
        # 28 Sep 2026, Windows).
        assert env["DOTNET_ROLL_FORWARD"] == "LatestMajor"
        assert env["PATH"].split(os.pathsep)[0] == root

    def test_without_an_embedded_sdk_no_dotnet_root_is_imposed_on_the_child(
        self, fake_root, monkeypatch
    ):
        """Gömülü SDK yoksa sistemdeki .NET'i BOZMA: kendi (eksik) kökümüzü
        dayatmak, makinede çalışan bir kurulumu da çalışmaz hale getirirdi.

        Ama bu dal artık `None` DÖNMÜYOR — `None`, subprocess'e "ebeveyn ortamını
        aynen devral" demek, yani sızıntıyı kapatmayan dal tam olarak buydu
        (dış denetim, 2026-07-27). Doğru davranış: minimal ortam kur, sadece
        DOTNET_ROOT'u ekleme."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        env = om._spawn_env()
        assert isinstance(env, dict)
        assert "DOTNET_ROOT" not in env
        assert "DOTNET_ROLL_FORWARD" not in env


class TestAltSurecOrtamiSizdirmiyor:
    """Alt sürece geçen ortam bir İZİN LİSTESİ. Kanıt (canlı probe, 2026-07-27):
    `{**os.environ}` ile spawn edilen OmniSharp çocuğu LOCAL_APP_TOKEN ve
    API_KEY_ENCRYPTION_KEY değişkenlerini görüyordu — ikincisi veritabanı şifreleme
    anahtarı ve üçüncü parti bir binary'nin ona ihtiyacı yok.

    Kapı İKİ yönden de sınanıyor. Çok GENİŞ olmamalı (sır sızmamalı) ama çok DAR
    da olmamalı: listeden düşen bir ad OmniSharp'ı hiç başlatmaz ve arıza SESSİZ
    olur (initialize'a yanıt gelmez, kullanıcı yalnız donma görür) — bugün
    düzeltilen ürün hatasının aynısı geri gelir."""

    _SIRLAR = {"LOCAL_APP_TOKEN": "tok-123",
               "API_KEY_ENCRYPTION_KEY": "sifreleme-anahtari",
               "OPENAI_API_KEY": "sk-gizli"}

    def _sirlari_koy(self, monkeypatch):
        for name, value in self._SIRLAR.items():
            monkeypatch.setenv(name, value)

    def test_application_secrets_never_reach_the_child_when_an_embedded_sdk_exists(
        self, fake_root, monkeypatch
    ):
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        _make_dotnet_tree(fake_root, "osx-arm64", with_sdk=True)
        self._sirlari_koy(monkeypatch)
        env = om._spawn_env()
        assert not (self._SIRLAR.keys() & env.keys())
        # Değerin başka bir ad altında taşınmadığı da sınanıyor: PATH gibi
        # birleştirilen alanlara sızmış olabilirdi.
        assert not any(v in "".join(env.values()) for v in self._SIRLAR.values())

    def test_application_secrets_never_reach_the_child_without_an_embedded_sdk_either(
        self, fake_root, monkeypatch
    ):
        """⚠️ Bu dal ayrıca sınanıyor çünkü İKİ dal da sızdırıyordu: SDK yokken
        fonksiyon `None` dönüyordu ve `None` = ebeveyn ortamının tamamı."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        self._sirlari_koy(monkeypatch)
        env = om._spawn_env()
        assert not (self._SIRLAR.keys() & env.keys())

    def test_the_variables_omnisharp_actually_needs_are_still_handed_down(
        self, fake_root, monkeypatch
    ):
        """Karşıt yön — kapı çok DAR olmamalı. PATH `dotnet` host'unu bulmak,
        HOME NuGet/MSBuild önbelleği, TMPDIR ailesi MSBuild'in ara dosyaları için
        gerekli. Biri düşerse OmniSharp sessizce hiç başlamaz."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        beklenen = {"PATH": "/usr/bin", "HOME": "/Users/test", "TMPDIR": "/tmp/x",
                    "LANG": "tr_TR.UTF-8", "USER": "test", "LOGNAME": "test",
                    "SHELL": "/bin/zsh"}
        for name, value in beklenen.items():
            monkeypatch.setenv(name, value)
        env = om._spawn_env()
        for name, value in beklenen.items():
            assert env.get(name) == value, f"{name} alt sürece geçmiyor"

    def test_the_embedded_sdk_still_wins_on_path_while_the_rest_stays_minimal(
        self, fake_root, monkeypatch
    ):
        """PATH hem izin listesinden gelir hem gömülü kök onun BAŞINA eklenir;
        ikisi çakışmamalı — sıralama bozulursa sistemdeki başka bir `dotnet`
        gömülü SDK'nın önüne geçer."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        root = _make_dotnet_tree(fake_root, "osx-arm64", with_sdk=True)
        monkeypatch.setenv("PATH", "/usr/bin")
        env = om._spawn_env()
        assert env["PATH"] == root + os.pathsep + "/usr/bin"

    def test_windows_only_variables_are_handed_down_when_the_host_defines_them(
        self, fake_root, monkeypatch
    ):
        """Windows ürünün ANA kitlesi ama macOS'tan sınanamıyor. İzin listesi
        bilerek TEK parça (os.name'e göre dallanmıyor), böylece Windows'a özgü
        adların kopyalandığı burada — macOS'ta — doğrulanabiliyor."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\t\AppData\Local")
        monkeypatch.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")
        env = om._spawn_env()
        assert env["SystemRoot"] == r"C:\Windows"
        assert env["LOCALAPPDATA"] == r"C:\Users\t\AppData\Local"
        assert env["COMSPEC"] == r"C:\Windows\system32\cmd.exe"

    def test_variables_that_do_not_exist_are_not_invented_as_empty_strings(
        self, fake_root, monkeypatch
    ):
        """Boş dizeyle TANIMLI bir değişken, tanımsız olmakla aynı şey değil:
        MSBuild bazı yolları "var ama boş" görünce onları kullanmayı deniyor.
        macOS'ta SystemRoot yok — o yüzden ortamda da olmamalı."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        monkeypatch.delenv("SystemRoot", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        env = om._spawn_env()
        assert "SystemRoot" not in env
        assert "LC_ALL" not in env
        assert "" not in env.values()


class TestSdkYokkenDavranis:
    def test_a_machine_with_no_dotnet_at_all_gets_a_reason_instead_of_a_long_hang(
        self, fake_root, monkeypatch
    ):
        """Ön kontrolün varlık sebebi: SDK yokken OmniSharp `initialize`'a hiç yanıt
        vermiyor, yani tek geri bildirim timeout oluyor. Anında ve sebebiyle düşmek
        şart; mesaj kullanıcıya ne yapacağını söylemeli."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        monkeypatch.setattr(om.shutil, "which", lambda _name: None)
        reason = om._dotnet_missing_reason()
        assert reason is not None
        assert ".NET SDK" in reason

    def test_a_system_dotnet_is_enough_to_stop_us_from_refusing_early(
        self, fake_root, monkeypatch
    ):
        """Karşıt yön: gömülü SDK yok ama makinede .NET varsa denemeden pes etme —
        aksi halde geliştirici makinelerinde çalışan kurulum reddedilirdi."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        monkeypatch.setattr(om.shutil, "which", lambda _name: "/usr/local/bin/dotnet")
        assert om._dotnet_missing_reason() is None

    def test_an_embedded_sdk_makes_the_preflight_pass_without_consulting_the_system(
        self, fake_root, monkeypatch
    ):
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        _make_dotnet_tree(fake_root, "osx-arm64", with_sdk=True)
        monkeypatch.setattr(om.shutil, "which", lambda _name: None)
        assert om._dotnet_missing_reason() is None


class TestSunucuYokkenUcNoktalar:
    """OmniSharp hiç başlamadığında `_client` None kalıyor. Bu üç uç eskiden
    doğrudan `self._client.request(...)` çağırıyordu → `AttributeError` handler'dan
    dışarı çıkıyor, CORSMiddleware'in ALTINDAKİ katmanda 500'e dönüşüyor ve o
    yanıtta Access-Control-Allow-Origin olmadığı için tarayıcı "Failed to fetch"
    gösteriyordu. Yani kullanıcının gördüğü mesaj gerçek sebebi tamamen gizliyordu."""

    def test_hover_returns_nothing_instead_of_raising_when_the_server_never_started(self):
        mgr = om.OmniSharpManager()
        assert asyncio.run(mgr.hover("/x/A.cs", "class A {}", 1, 1)) is None

    def test_completion_returns_an_empty_list_instead_of_raising(self):
        mgr = om.OmniSharpManager()
        assert asyncio.run(mgr.completion("/x/A.cs", "class A {}", 1, 1)) == []

    def test_definition_returns_nothing_instead_of_raising(self):
        mgr = om.OmniSharpManager()
        assert asyncio.run(mgr.definition("/x/A.cs", "class A {}", 1, 1)) is None


class TestBasarisizBaslatmaninTekrari:
    def test_a_failed_start_is_not_retried_on_every_single_request(self, monkeypatch):
        """Monaco hover/completion her imleç hareketinde istek üretiyor ve hepsi
        `ensure_started`'ın kilidinden geçiyor. Başarısız başlatma her istekte
        tekrarlanırsa kuyruk büyüyor ve editör tümden donuyor — soğuma penceresi
        tam olarak bunu engelliyor."""
        mgr = om.OmniSharpManager()
        calls = []
        monkeypatch.setattr(om, "_resolve_binary", lambda: (calls.append(1), "/bin/false")[1])
        monkeypatch.setattr(om, "_dotnet_missing_reason", lambda: "SDK yok")

        async def run_twice():
            await mgr.ensure_started("/ws")
            await mgr.ensure_started("/ws")

        asyncio.run(run_twice())
        assert len(calls) == 1, "başarısız başlatma her istekte yeniden denendi"
        assert mgr.status["state"] == "error"
        assert mgr.status["detail"] == "SDK yok"

    def test_a_different_workspace_still_gets_a_fresh_attempt_during_the_cooldown(
        self, monkeypatch
    ):
        """Karşıt yön: soğuma penceresi çok GENİŞ olmamalı. Kullanıcı başka bir
        projeye geçtiğinde eski arızanın onu 30 saniye rehin alması yanlış olurdu."""
        mgr = om.OmniSharpManager()
        calls = []
        monkeypatch.setattr(om, "_resolve_binary", lambda: (calls.append(1), "/bin/false")[1])
        monkeypatch.setattr(om, "_dotnet_missing_reason", lambda: "SDK yok")

        async def run_two_workspaces():
            await mgr.ensure_started("/ws-a")
            await mgr.ensure_started("/ws-b")

        asyncio.run(run_two_workspaces())
        assert len(calls) == 2

class TestBinaryCozumlemeBagCalistirmaz:
    """`_resolve_binary()` zincirin ÇALIŞTIRMA halkası. `os.path.exists` bağ takip
    ettiği için, binary yerine konmuş bir bağ spawn ediliyordu (dış denetim,
    2026-07-28). İndirme tarafındaki `_intact()` kapısı ayrı ve ikisi de gerekli:
    biri kurulumu tazeler, diğeri çalıştırmayı engeller."""

    @pytest.mark.baglar_gerekli

    def test_a_symlinked_binary_is_not_executed(self, fake_root, monkeypatch):
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        hedef = os.path.join(fake_root, "DISARIDAKI")
        with open(hedef, "w") as f:
            f.write("")
        plat_dir = os.path.join(fake_root, "osx-arm64")
        os.makedirs(plat_dir, exist_ok=True)
        os.symlink(hedef, os.path.join(plat_dir, "OmniSharp"))
        assert om._resolve_binary() is None

    def test_a_real_binary_is_still_resolved(self, fake_root, monkeypatch):
        """Karşıt yön: gerçek binary reddedilirse C# zekası hiç çalışmaz."""
        monkeypatch.setattr(om, "_platform_key", lambda: "osx-arm64")
        plat_dir = os.path.join(fake_root, "osx-arm64")
        os.makedirs(plat_dir, exist_ok=True)
        gercek = os.path.join(plat_dir, "OmniSharp")
        with open(gercek, "w") as f:
            f.write("")
        assert om._resolve_binary() == gercek



class TestBagKapisiYaprakDegilYolBoyunca:
    """Sembolik bağ kapısının YOL üzerinde kurulduğunu koruyan testler.

    Hangi arızadan doğdu (dış denetim, 2026-07-28): 27 Tem'de aynı sınıf bir
    bulgu "kapatıldı" ama kapı YAPRAĞA kondu — `islink(dest/OmniSharp)`. `islink`
    tek bir bileşene bakar, oysa **yolun kendisi** bağ olabiliyor: `dest` dizini
    dışarıyı gösterirse içindeki dosya gerçek dosyadır, `islink` False döner ve
    kapı hiç ateşlenmez.

    Üç biçim de canlı ölçüldü: `_intact` üst dizin bağıyla True döndü,
    `_resolve_binary` dışarıdaki binary'yi döndürdü, ve `_embedded_dotnet_root`
    HİÇ kontrol içermiyordu — döndürdüğü dizin `DOTNET_ROOT` olup `PATH`'in
    başına geçtiği için en ağırı oydu.

    Ders, kapatma protokolünün kendisi: bir bulgu kapatılırken sınıfın başka
    biçimleri adlandırılıp sınanmazsa, kapatma tek YOLU kapatır, SINIFI değil.
    """

    @staticmethod
    def _kok_disi(ad: str) -> pathlib.Path:
        """Tuzak ağaç KÖKÜN DIŞINDA olmalı. İlk yazımda `tmp_path` altına konmuştu
        ve testler kırmızı verdi — haklı olarak: kökün İÇİNE gösteren bir bağ kaçış
        değildir ve kapsama kontrolü ona izin verir. Kırmızı, kodun değil testin
        hatasıydı; bu yorum o hatayı tekrar etmemek için burada."""
        d = pathlib.Path(tempfile.mkdtemp()) / ad
        d.mkdir(parents=True)
        return d

    @pytest.mark.baglar_gerekli

    def test_a_symlinked_platform_directory_is_not_executed(self, fake_root):
        """`third_party/omnisharp/<plat>` bir bağ olduğunda ürün onu spawn etmemeli."""
        plat = om._platform_key()
        exe = "OmniSharp.exe" if plat.startswith("win") else "OmniSharp"
        disarida = self._kok_disi("DISARIDA")
        (disarida / exe).write_text("#!/bin/sh\n")
        os.symlink(str(disarida), os.path.join(fake_root, plat))
        assert om._resolve_binary() is None

    def test_a_real_platform_directory_still_resolves(self, fake_root):
        """Karşıt yön: kapı çok GENİŞ olursa C# zekası hiç ayağa kalkmaz."""
        plat = om._platform_key()
        exe = "OmniSharp.exe" if plat.startswith("win") else "OmniSharp"
        gercek = os.path.join(fake_root, plat)
        os.makedirs(gercek)
        yol = os.path.join(gercek, exe)
        with open(yol, "w") as f:
            f.write("")
        assert om._resolve_binary() == yol

    @pytest.mark.baglar_gerekli

    def test_a_symlinked_dotnet_directory_is_not_used_as_dotnet_root(
        self, fake_root
    ):
        """En ağır biçim: bu yol `DOTNET_ROOT` olur ve `PATH`'in başına eklenir,
        yani sürecin yorumlayıcısını saldırgan belirlerdi."""
        plat = om._platform_key()
        exe = "dotnet.exe" if plat.startswith("win") else "dotnet"
        disarida = self._kok_disi("SAHTE_SDK")
        (disarida / "sdk" / "10.0.100").mkdir(parents=True)
        (disarida / exe).write_text("#!/bin/sh\n")
        os.symlink(str(disarida), os.path.join(fake_root, f"dotnet-{plat}"))
        assert om._embedded_dotnet_root() is None

    def test_a_real_dotnet_tree_is_still_accepted(self, fake_root):
        """Karşıt yön: gerçek gömülü SDK reddedilirse hover yine 120 sn asılır."""
        plat = om._platform_key()
        dest = _make_dotnet_tree(fake_root, plat, with_sdk=True)
        assert om._embedded_dotnet_root() == dest


# A stand-in for OmniSharp that never answers `initialize`. In "msbuild" mode it
# first logs what the real server logged on 28 Sep 2026 (Windows) when it ran on
# .NET 8 and rejected the bundled SDK; in "silent" mode it logs one info line only.
_FAKE_OMNISHARP = r'''
import json, sys
def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.decode()
        if line in ("\r\n", "\n"):
            break
        k, v = line.split(":", 1); headers[k.strip().lower()] = v.strip()
    return json.loads(sys.stdin.buffer.read(int(headers["content-length"])))
def write_msg(obj):
    body = json.dumps(obj).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()
def log(kind, text):
    write_msg({"jsonrpc": "2.0", "method": "window/logMessage",
               "params": {"type": kind, "message": text}})
mode = sys.argv[1]
while True:
    msg = read_msg()
    if msg is None or msg.get("method") == "exit":
        break
    if msg.get("method") != "initialize":
        continue
    log(3, "OmniSharp.Services.DotNetCliService: DotNetPath set to dotnet | ")
    if mode == "msbuild":
        log(1, "OmniSharp.MSBuild.Discovery.Providers.SdkInstanceProvider: OmniSharp "
               "requires the .NET 6 SDK or higher be installed. | ")
        log(1, "OmniSharp.Extensions.JsonRpc.DefaultRequestInvoker: Failed to handle "
               "request initialize 1 - OmniSharp.MSBuild.Discovery.MSBuildNotFoundException: "
               "Could not locate MSBuild instance to register with OmniSharp.\r\n"
               "   at OmniSharp.MSBuild.Discovery.Extensions.RegisterDefaultInstance()")
        log(2, "OmniSharp.Extensions.LanguageServer.Server.LspServerOutputFilter: Tried "
               "to send request or notification before initialization was completed")
'''


@pytest.fixture
def fake_omnisharp(tmp_path, monkeypatch):
    """Routes ensure_started to the fake server; returns (set_mode, started clients).

    `_maybe_sync_csproj` is stubbed: an empty workspace counts as stale and the
    real method would POST to the live Unity MCP port."""
    server = tmp_path / "fake_omnisharp.py"
    server.write_text(_FAKE_OMNISHARP, encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    state = {"mode": "msbuild"}
    started = []

    class _FakeServerClient(om.LspClient):
        async def start(self, cmd, cwd, env=None):
            started.append(self)
            await super().start([sys.executable, str(server), state["mode"]], cwd=cwd)

    monkeypatch.setattr(om, "LspClient", _FakeServerClient)
    monkeypatch.setattr(om, "_resolve_binary", lambda: "fake-omnisharp")
    monkeypatch.setattr(om, "_dotnet_missing_reason", lambda: None)
    monkeypatch.setattr(om.OmniSharpManager, "_maybe_sync_csproj", lambda self, _ws: None)
    return str(ws), state, started


class TestInitializeReddiBeklemeden:
    def test_a_logged_msbuild_failure_ends_the_start_without_waiting_for_the_timeout(
        self, fake_omnisharp
    ):
        """Before: the server logged the failure within 0.7 s and the manager
        still waited the full 60 s. The timeout stays 60 s here on purpose."""
        ws, _state, started = fake_omnisharp
        mgr = om.OmniSharpManager()
        t0 = time.monotonic()
        asyncio.run(mgr.ensure_started(ws))
        elapsed = time.monotonic() - t0
        assert om._INIT_TIMEOUT == 60
        assert elapsed < 15, f"waited {elapsed:.1f} s for a start that had already failed"
        assert mgr.status["state"] == "error"
        assert mgr.status["detail"].count("Could not locate MSBuild") == 1
        # The SDK line precedes the failure and must reach the user too.
        assert "requires the .NET 6 SDK" in mgr.status["detail"]
        # Only the first line of the .NET stack trace is kept.
        assert "RegisterDefaultInstance" not in mgr.status["detail"]
        assert len(started) == 1 and not started[0].alive
        assert mgr._client is None

    def test_the_failure_keeps_the_cooldown_and_a_later_retry_still_starts(
        self, fake_omnisharp
    ):
        ws, _state, started = fake_omnisharp
        mgr = om.OmniSharpManager()

        async def scenario():
            await mgr.ensure_started(ws)
            await mgr.ensure_started(ws)        # inside the 30 s cooldown
            assert len(started) == 1
            assert not mgr._lock.locked()
            assert mgr._retry_after - time.monotonic() > om._RETRY_COOLDOWN - 5
            mgr._retry_after = time.monotonic() - 1     # cooldown elapsed
            await mgr.ensure_started(ws)

        asyncio.run(scenario())
        assert len(started) == 2
        assert mgr.status["state"] == "error"

    def test_a_timeout_still_produces_a_non_empty_reason(self, fake_omnisharp, monkeypatch):
        """TimeoutError stringifies to "" — the 28 Sep 2026 log line was literally
        `ERROR:OmniSharp:OmniSharp:` with nothing after it."""
        ws, state, _started = fake_omnisharp
        state["mode"] = "silent"
        monkeypatch.setattr(om, "_INIT_TIMEOUT", 1.5)
        mgr = om.OmniSharpManager()
        asyncio.run(mgr.ensure_started(ws))
        detail = mgr.status["detail"]
        assert mgr.status["state"] == "error"
        assert "yanıtlanmadı" in detail
        # No error line was logged, so the latest info lines are attached instead.
        assert "DotNetPath set to dotnet" in detail
