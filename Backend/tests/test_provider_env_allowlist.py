"""Sağlayıcı alt süreçlerine geçen ortamın İZİN LİSTESİ olduğunu koruyan testler.

Hangi ölçümden doğdu (2026-07-28): provider child process'leri
`{**os.environ, ...}` ile kuruluyordu. Altı canary ortama konup çocuğun gördüğü
ortam yakalandı; **altısı da** geçti:

    LOCAL_APP_TOKEN, API_KEY_ENCRYPTION_KEY, OPENAI_API_KEY,
    ANTHROPIC_API_KEY, GEMINI_API_KEY, AWS_SECRET_ACCESS_KEY

Yani kullanıcı Cursor'u seçtiğinde `cursor-agent` süreci Anthropic anahtarını,
veritabanı şifreleme anahtarını ve backend'in tek yetki kanıtı olan
LOCAL_APP_TOKEN'ı okuyabiliyordu.

Kapı İKİ yönden de sınanıyor ve bu dosyada ikinci yön birincisinden ÖNEMLİ:
bu düzeltmenin başarısızlık yönü "sır sızmaya devam eder" değil, **"sağlayıcı
artık hiç çalışmaz"**. Listeden düşen bir ad (PATH, HOME, proxy, CA sertifikası)
CLI'ı sessizce başlatmaz ya da her HTTPS çağrısını düşürür. O yüzden her sızıntı
testinin karşısında "gereken değişken hâlâ geçiyor" testi duruyor.

Testler saf fonksiyonu DEĞİL, gerçek spawn noktalarını sürüyor: `build_spawn_env`
doğru olsa bile bir spawn noktası onu çağırmayı unutursa sızıntı sürer — bu
depoda tekrarlayan arıza şekli tam olarak "birbiriyle uyuşması gereken iki yer
uyuşmuyor".
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from providers import cli_base
from providers import codex_session as cs
from providers.cli_base import BaseCLIProvider, build_spawn_env, env_family
from unity_ai_mcp import unity_mcp_manager as um


# Ölçümde kullanılan altı canary. Değerler de kontrol ediliyor: bir sır ADI
# elenip DEĞERİ başka bir adın içinde (örn. birleştirilmiş PATH) taşınmış olabilir.
CANARIES = {
    "LOCAL_APP_TOKEN": "canary-local-app-token",
    "API_KEY_ENCRYPTION_KEY": "canary-db-encryption-key",
    "OPENAI_API_KEY": "canary-openai-key",
    "ANTHROPIC_API_KEY": "canary-anthropic-key",
    "GEMINI_API_KEY": "canary-gemini-key",
    "AWS_SECRET_ACCESS_KEY": "canary-aws-secret",
}


@pytest.fixture
def canaries(monkeypatch):
    """Altı canary'yi ebeveyn ortamına koyar. conftest'teki autouse fixture
    LOCAL_APP_TOKEN'ı SİLİYOR; bu fixture ondan sonra koştuğu için geri koyuyor."""
    for name, value in CANARIES.items():
        monkeypatch.setenv(name, value)
    return CANARIES


def assert_no_canaries(env, *, allowed=()):
    """`allowed` dışındaki hiçbir canary ne ad ne DEĞER olarak çocukta olmamalı."""
    leaked = [n for n in CANARIES if n not in allowed and n in env]
    assert not leaked, f"sır alt sürece geçti: {leaked}"
    blob = "\n".join(str(v) for v in env.values())
    leaked_values = [n for n, v in CANARIES.items() if n not in allowed and v in blob]
    assert not leaked_values, f"sırrın DEĞERİ başka bir ad altında geçti: {leaked_values}"


class _SpawnCaptured(Exception):
    """Spawn'ı env yakalandıktan hemen sonra kesmek için sentinel.

    Sahte bir süreç nesnesi taklit etmekten daha dürüst: gerçek okuma
    döngüsünün taklidi kolayca yalancı yeşil üretir, oysa burada ölçmek
    istediğimiz tek şey `env` argümanının kendisi."""


def _capture_async_spawn(monkeypatch, module):
    """`asyncio.create_subprocess_exec` çağrısındaki env'i yakalar."""
    box = {}

    async def fake(*args, **kwargs):
        box["env"] = kwargs.get("env")
        raise _SpawnCaptured()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake)
    return box


def _drive_cli_provider(monkeypatch, tmp_path, binary_name):
    """`BaseCLIProvider.analyze_code`'u gerçekten koşturur, spawn env'ini döndürür."""
    box = _capture_async_spawn(monkeypatch, cli_base)
    monkeypatch.setattr(BaseCLIProvider, "_write_mcp_config", lambda self, ws: "")
    monkeypatch.setattr(BaseCLIProvider, "_cli_installed", staticmethod(lambda name: True))
    monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", staticmethod(lambda cmd: list(cmd)))

    provider = BaseCLIProvider(binary_name=binary_name)

    async def run():
        async for _ in provider.analyze_code("merhaba", cwd=str(tmp_path)):
            pass

    asyncio.run(run())
    assert box.get("env") is not None, "spawn hiç yapılmadı — test kendi kendini ölçüyor"
    return box["env"]


# ── 1. cli_base: tüm CLI sağlayıcılarının ortak spawn noktası ────────────────

class TestCliProviderSpawnOrtami:
    def test_a_cursor_child_sees_none_of_the_six_canaries(
        self, canaries, monkeypatch, tmp_path
    ):
        """Ölçümün birebir kendisi: kullanıcı Cursor seçtiğinde Cursor süreci
        Anthropic/OpenAI/Gemini anahtarlarını ve backend bearer'ını görüyordu."""
        env = _drive_cli_provider(monkeypatch, tmp_path, "cursor-gpt-5.2")
        assert_no_canaries(env)

    def test_the_claude_child_gets_its_own_identity_variable_only(
        self, canaries, monkeypatch, tmp_path
    ):
        """İki katmanlı tasarımın bütün değeri burada: Claude CLI kendi kimlik
        değişkenini ALIR, başkasınınkini ALMAZ. Tek düz liste yapılsaydı ya
        sızıntı sürerdi ya da env ile giriş yapan kullanıcının CLI'ı kırılırdı."""
        env = _drive_cli_provider(monkeypatch, tmp_path, "claude-opus-4-6")
        assert env.get("ANTHROPIC_API_KEY") == CANARIES["ANTHROPIC_API_KEY"]
        assert_no_canaries(env, allowed=("ANTHROPIC_API_KEY",))

    def test_the_codex_child_gets_openai_but_not_anthropic(
        self, canaries, monkeypatch, tmp_path
    ):
        env = _drive_cli_provider(monkeypatch, tmp_path, "gpt-5.6-codex")
        assert env.get("OPENAI_API_KEY") == CANARIES["OPENAI_API_KEY"]
        assert_no_canaries(env, allowed=("OPENAI_API_KEY",))

    def test_the_agy_branch_is_gated_too_and_gets_only_the_google_key(
        self, canaries, monkeypatch, tmp_path
    ):
        """agy AYRI bir `create_subprocess_exec` çağrısından geçiyor. İki çağrı
        noktasından yalnız birini düzeltmek bu depoda tekrarlayan arıza şekli."""
        env = _drive_cli_provider(monkeypatch, tmp_path, "gemini-3.6-flash")
        assert env.get("GEMINI_API_KEY") == CANARIES["GEMINI_API_KEY"]
        assert_no_canaries(env, allowed=("GEMINI_API_KEY",))

    def test_the_operational_variables_a_cli_cannot_start_without_still_pass(
        self, canaries, monkeypatch, tmp_path
    ):
        """KARŞIT YÖN. PATH olmadan CLI hiç bulunmaz, HOME olmadan CLI kendi
        oturum/config dizinini (~/.claude, ~/.codex) bulamaz, CA/proxy
        değişkenleri olmadan kurumsal ağda her HTTPS çağrısı düşer. Hepsi
        SESSİZ arızalar."""
        beklenen = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/test",
            "TMPDIR": "/tmp/x",
            "LANG": "tr_TR.UTF-8",
            "SHELL": "/bin/zsh",
            "HTTPS_PROXY": "http://proxy.local:3128",
            "NO_PROXY": "localhost,127.0.0.1",
            "NODE_EXTRA_CA_CERTS": "/etc/ssl/kurum.pem",
            "SSL_CERT_FILE": "/etc/ssl/cert.pem",
            "NVM_DIR": "/Users/test/.nvm",
            "XDG_CONFIG_HOME": "/Users/test/.config",
            # Modeller onay kapısından geçip `git push` çalıştırıyor; bu düşerse
            # SSH remote'lu her push parola sorup takılır.
            "SSH_AUTH_SOCK": "/private/tmp/ssh-agent.sock",
        }
        for name, value in beklenen.items():
            monkeypatch.setenv(name, value)
        env = _drive_cli_provider(monkeypatch, tmp_path, "cursor-gpt-5.2")
        for name, value in beklenen.items():
            assert env.get(name) == value, f"{name} alt sürece geçmiyor"

    def test_the_terminal_overrides_the_provider_sets_itself_survive(
        self, canaries, monkeypatch, tmp_path
    ):
        """NO_COLOR/TERM/COLUMNS/LINES bizim DAYATTIĞIMIZ değerler; izin listesi
        onları ezmemeli, yoksa CLI çıktısı ANSI ile dolar ve JSONL parse'ı bozulur."""
        env = _drive_cli_provider(monkeypatch, tmp_path, "cursor-gpt-5.2")
        assert env["NO_COLOR"] == "1"
        assert env["TERM"] == "xterm-256color"
        assert env["COLUMNS"] == "220"
        assert env["LINES"] == "50"


# ── 2. codex_session: kalıcı app-server'ın İKİ ayrı spawn noktası ────────────

class TestCodexSessionSpawnOrtami:
    def test_the_persistent_app_server_child_is_gated(self, canaries, monkeypatch):
        box = _capture_async_spawn(monkeypatch, cs)
        session = cs.CodexSession(0)
        with pytest.raises(_SpawnCaptured):
            asyncio.run(session.start())
        env = box["env"]
        assert env is not None
        assert_no_canaries(env, allowed=("OPENAI_API_KEY",))
        assert env["NO_COLOR"] == "1"
        assert env.get("PATH")

    def test_the_throwaway_skills_probe_child_is_gated_too(self, canaries, monkeypatch):
        """İkinci spawn noktası. `fetch_codex_skills` sohbetten bağımsız çalışıyor
        ve gözden kaçmaya en açık yer burası."""
        box = _capture_async_spawn(monkeypatch, cs)
        monkeypatch.setattr(cs, "_CODEX_SKILLS_CACHE", [])
        asyncio.run(cs.fetch_codex_skills(force=True))
        env = box["env"]
        assert env is not None
        assert_no_canaries(env, allowed=("OPENAI_API_KEY",))


# ── 3. unity_mcp_manager: uvx torunu ─────────────────────────────────────────

class TestUnityMcpSpawnOrtami:
    @pytest.fixture
    def hazir_manager(self, monkeypatch, tmp_path):
        """Gerçek ev dizinine ve gerçek porta dokunmadan `start_server`'ı sürer."""
        from unity_ai_mcp import mcp_port_guard

        monkeypatch.setattr(
            os.path, "expanduser", lambda p: p.replace("~", str(tmp_path), 1)
        )
        # start_server() asks both what answers on 8080 and which process owns it.
        # Left real, a live listener there (e.g. a Unity MCP server that is slow
        # or answers 401) turns either answer foreign and the spawn is refused.
        monkeypatch.setattr(mcp_port_guard, "foreign_port_owner_infos", lambda port: [])
        monkeypatch.setattr(
            um,
            "probe_port_identity",
            lambda port: um.Occupant(um.ServerIdentity.NONE, "faked in test"),
        )
        monkeypatch.setattr(um.UnityMCPManager, "is_running", lambda self: False)
        monkeypatch.setattr(
            um.UnityMCPManager, "_ensure_writable_resources", lambda self: None
        )
        monkeypatch.setattr(um.UnityMCPManager, "_get_uvx", lambda self: "/bin/true")
        monkeypatch.setattr(
            um.UnityMCPManager, "_server_source_changed", lambda self: False
        )

        box = {}

        class _FakePopen:
            def __init__(self, cmd, **kwargs):
                box["env"] = kwargs.get("env")
                self.pid = 4242

        monkeypatch.setattr(um.subprocess, "Popen", _FakePopen)
        return um.UnityMCPManager(), box

    def test_the_uvx_grandchild_sees_no_application_secrets(
        self, canaries, hazir_manager
    ):
        manager, box = hazir_manager
        assert manager.start_server() is True
        # LOCAL_APP_TOKEN burada BİLEREK dışarıda tutulmuyor: mevcut kod onu
        # açıkça set ediyor (aşağıdaki test onu ayrıca sabitliyor).
        assert_no_canaries(box["env"], allowed=("LOCAL_APP_TOKEN",))

    def test_the_deliberately_env_passed_unity_secret_survives_the_allowlist(
        self, canaries, hazir_manager
    ):
        """⚠️ KARŞIT YÖN — bu testin kırılması Unity MCP kimlik doğrulamasının
        TAMAMEN çökmesi demektir. Sır bilerek argv yerine ortamla geçiyor
        (argv `ps` ile makinedeki her sürece görünür); izin listesi onu elerse
        sunucu /api uçlarını hiç açmaz ve toggle sonsuza kadar sarıda kalır."""
        manager, box = hazir_manager
        assert manager.start_server() is True
        assert box["env"]["UNITY_MCP_LOCAL_API_TOKEN"] == manager.local_api_token
        assert manager.local_api_token
        # Mevcut davranış: backend bearer'ı da açıkça geçiriliyor. Kaldırmak ayrı
        # bir karar; burada SABİTLENİYOR ki sessizce değişmesin.
        assert box["env"]["LOCAL_APP_TOKEN"] == CANARIES["LOCAL_APP_TOKEN"]

    def test_uvx_still_gets_what_it_needs_to_reach_pypi(self, canaries, hazir_manager):
        """KARŞIT YÖN: `uvx` her açılışta PyPI'dan indiriyor. PATH/HOME olmadan
        önbelleğini bulamaz, proxy/CA olmadan kurumsal ağda indiremez — ve arıza
        "toggle sonsuza kadar sarıda" biçiminde görünür."""
        manager, box = hazir_manager
        assert manager.start_server() is True
        env = box["env"]
        assert env.get("PATH")
        assert env.get("HOME")

    def test_devralinan_telemetri_KAPALI_olarak_spawn_ediliyor(
        self, canaries, hazir_manager
    ):
        """Fork'tan devralınan telemetri varsayılan AÇIK geliyor ve açılışta
        `api-prod.coplay.dev`'e ping atıyor (`Server/src/core/config.py`,
        `telemetry_enabled: bool = True`). Kullanıcıya bunu bildiren hiçbir yer
        yok, dolayısıyla rızası da yok — bu yüzden spawn ortamında kapatılıyor.

        Üç ad da sınanıyor: upstream `_is_disabled()` üçünü de kabul ediyor, ve
        tek bir düz metin ada bağlı kalmak bu depoda daha önce sessizce açılan
        bir yol üretti.

        ⚠️ Sabitler ve kabul edilen değerler BİLEREK elle yazıldı, üretim
        kodundan import EDİLMEDİ: korunan değeri koruduğu yerden okuyan bir test,
        değer değişince ölçütünü de değiştirir ve hiçbir şeyi korumaz.
        """
        manager, box = hazir_manager
        assert manager.start_server() is True
        env = box["env"]
        for ad in ("DISABLE_TELEMETRY",
                   "UNITY_MCP_DISABLE_TELEMETRY",
                   "MCP_DISABLE_TELEMETRY"):
            # Kabul edilen değerler upstream `_is_disabled()` ile aynı küme.
            assert env.get(ad, "").lower() in ("true", "1", "yes", "on"), (
                f"{ad} spawn ortamında kapatma değeri taşımıyor: {env.get(ad)!r}"
            )


# ── 4. İzin listesi fonksiyonunun kendisi ────────────────────────────────────

class TestIzinListesiFonksiyonu:
    def test_the_family_of_a_binary_name_matches_the_manager_dispatch_table(self):
        """`env_family` ile `manager.get_provider` AYNI önekleri kullanmak
        zorunda; ayrışırlarsa bir sağlayıcı sessizce kimlik değişkenini
        kaybeder ve auth hatası verir."""
        assert env_family("claude-opus-4-6") == "claude"
        assert env_family("gpt-5.6-codex") == "codex"
        assert env_family("gemini-3.6-flash") == "agy"
        assert env_family("agy-claude-sonnet-4-6") == "agy"
        assert env_family("kimi-k3") == "kimi"
        assert env_family("cursor-gpt-5.2") == "cursor"
        assert env_family("copilot-claude-sonnet-4.6") == "copilot"
        assert env_family("opencode:anthropic/claude") == "opencode"

    def test_an_unknown_binary_name_falls_back_exactly_like_the_manager_does(self):
        """`manager.get_provider` bilinmeyen adı ClaudeCodeProvider'a düşürüyor
        ("claude-* ve diğerleri"). İzin listesi de aynı yere düşmeli; ayrışsalardı
        çalışan bir sağlayıcı kimlik değişkenini SESSİZCE kaybederdi."""
        assert env_family("bilinmeyen-cli") == "claude"

    def test_no_family_at_all_means_the_base_layer_only(self, canaries):
        """Aile verilmeyen çağrı (ör. uvx öncesi hâli) fail-closed: taban dışında
        hiçbir kimlik değişkeni eklenmez."""
        env = build_spawn_env()
        assert_no_canaries(env)
        assert env.get("PATH")

    def test_variables_that_do_not_exist_are_not_invented_as_empty_strings(
        self, monkeypatch
    ):
        """Boş dizeyle TANIMLI bir değişken tanımsız olmakla aynı şey değil:
        `HTTPS_PROXY=""` gören bir HTTP istemcisi boş proxy'yi kullanmayı
        deneyip bağlantıyı düşürebiliyor."""
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("SystemRoot", raising=False)
        env = build_spawn_env(family="claude")
        assert "HTTPS_PROXY" not in env
        assert "SystemRoot" not in env
        assert "" not in env.values()

    def test_windows_only_names_are_copied_when_the_host_defines_them(self, monkeypatch):
        """İzin listesi bilerek TEK parça (os.name'e göre dallanmıyor), böylece
        Windows'a özgü adların kopyalandığı macOS'ta da doğrulanabiliyor —
        Windows ürünün ana kitlesi ama bu makineden sınanamıyor."""
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        monkeypatch.setenv("APPDATA", r"C:\Users\t\AppData\Roaming")
        monkeypatch.setenv("PATHEXT", ".COM;.EXE;.CMD")
        env = build_spawn_env(family="claude")
        assert env["SystemRoot"] == r"C:\Windows"
        assert env["APPDATA"] == r"C:\Users\t\AppData\Roaming"
        assert env["PATHEXT"] == ".COM;.EXE;.CMD"

    def test_overrides_win_over_the_inherited_value(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "0")
        env = build_spawn_env(family="claude", overrides={"NO_COLOR": "1"})
        assert env["NO_COLOR"] == "1"

    def test_the_result_is_never_none(self):
        """`None`, subprocess'e "ebeveyn ortamını AYNEN devral" demek — yani
        sızıntıyı kapatmayan dal. OmniSharp tarafında tam olarak bu dal
        gözden kaçmıştı (dış denetim, 2026-07-27)."""
        assert isinstance(build_spawn_env(), dict)
        assert isinstance(build_spawn_env(family="claude"), dict)


# ── 5. oneshot_cli: yetenek probe'u — aynı sınıfın altıncı spawn noktası ─────

class TestOneShotProbeSpawnOrtami:
    """`probe_named_models` `env=` HİÇ vermiyordu, yani ebeveynin tamamını
    devralıyordu. Diğer beş nokta kapatılırken burası açık kalmıştı: sınıfı
    değil tek tek yolları kapatmanın bu depoda ölçülmüş üçüncü vakası."""

    def _probe_env(self, monkeypatch, cli):
        import asyncio as _aio
        from providers import oneshot_cli as oc

        box = {}

        async def fake(*args, **kwargs):
            box["env"] = kwargs.get("env")
            raise _SpawnCaptured()

        monkeypatch.setattr(_aio, "create_subprocess_exec", fake)
        monkeypatch.setattr(oc, "resolve_cli_cmd", lambda name: [name])
        try:
            asyncio.run(oc.probe_named_models(cli))
        except _SpawnCaptured:
            pass
        assert box.get("env") is not None, "spawn hiç yapılmadı — test kendini ölçüyor"
        return box["env"]

    def test_the_cursor_capability_probe_child_is_gated(self, canaries, monkeypatch):
        assert_no_canaries(self._probe_env(monkeypatch, "cursor"))

    def test_the_copilot_capability_probe_child_is_gated(self, canaries, monkeypatch):
        assert_no_canaries(self._probe_env(monkeypatch, "copilot"))

    def test_the_probe_still_gets_what_it_needs_to_run(self, canaries, monkeypatch):
        """Karşı yön: probe PATH'siz ikiliyi bulamaz, HOME'suz oturumu okuyamaz.
        Tek yönlü sınanan bir kapı bu depoda üç ayrı arıza üretti."""
        env = self._probe_env(monkeypatch, "cursor")
        for name in ("PATH", "HOME"):
            if name in os.environ:
                assert env.get(name) == os.environ[name], f"{name} düştü"


class TestEnvFamilyCiplakAdlar:
    """`env_family` iki AYRI ad biçimi görüyor: model adı ("cursor-gpt-5.2",
    BaseCLIProvider'dan) ve çıplak CLI adı ("cursor", probe'dan). Tireli desen
    ikincisini kaçırıp "claude"a düşürüyordu — yani sızıntı bir çağrı yerinde
    açık kalıyordu. İki biçim de sınanır, yoksa tablo sessizce ayrışır."""

    @pytest.mark.parametrize("name,family", [
        ("cursor", "cursor"), ("cursor-gpt-5.2", "cursor"),
        ("copilot", "copilot"), ("copilot-claude-haiku-4.5", "copilot"),
        ("gpt-5.6-codex", "codex"), ("kimi-k3", "kimi"),
        ("gemini-3.6-flash", "agy"), ("claude-opus-4-6", "claude"),
    ])
    def test_both_name_shapes_land_in_the_same_family(self, name, family):
        assert env_family(name) == family


class TestBosDegerlerKasitliGeciyor:
    """Ebeveynde VAR olan boş değer çocuğa geçer — bu karar, kaza değil.

    Dış denetim (2026-07-28) bunu bulgu olarak yazdı: `build_spawn_env`
    docstring'i `HTTPS_PROXY=""`'nin bağlantı düşürebileceğini anlatıyor ama kod
    boşları elemiyor. Docstring'in söylediği başka bir şeydi (var olmayan adı
    uydurma), ama yanlış okunabildiğine göre niyet yoruma bırakılmamalı.

    Elemek ebeveyn davranışını DEĞİŞTİRİR: filtreden önce `{**os.environ}` da
    boşları geçiriyordu, yani eleme bir düzeltme değil yeni bir davranış olurdu
    ve "tanımlı ama boş" ile "hiç tanımlı değil" arasında ayrım yapan bir CLI'ı
    sessizce kırardı. Bu filtre yalnız ADLARA karar verir, DEĞERLERE değil."""

    def test_an_empty_but_defined_value_still_reaches_the_child(self, monkeypatch):
        monkeypatch.setenv("COLORTERM", "")
        monkeypatch.setenv("HTTPS_PROXY", "")
        env = build_spawn_env(family="claude")
        assert env.get("COLORTERM") == "", "boş değer elendi — ebeveyn davranışı değişti"
        assert env.get("HTTPS_PROXY") == ""

    def test_an_absent_name_is_never_invented_as_empty(self, monkeypatch):
        """Karşı yön: tanımsız ad çocukta HİÇ olmamalı, boş dizeyle bile."""
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        env = build_spawn_env(family="claude")
        assert "HTTPS_PROXY" not in env


# ── 6. MCP KAYIT yolu: her turda koşan, 2026-07-29'a kadar açık kalan nokta ──

class TestMcpKayitSpawnOrtami:
    """`_register_mcp` `subprocess.run` ile ÇIPLAK çağırıyordu (env= yok).

    Bu yol sohbetin sıcak yolunda: `cli_base:_write_mcp_config` → `_register_mcp`,
    yani HER TURDA. Canlı ölçüldü (2026-07-29, gerçek alt süreç ortamını diske
    döktü): çocuk altı canary'nin ALTISINI de görüyordu. 2026-07-28'de aynı
    sınıfın sohbet spawn'ı kapatılmış, bu nokta atlanmıştı.
    """

    def _kayit_envleri(self, monkeypatch, provider, tmp_path):
        """`_register_mcp`'in yaptığı HER sp.run çağrısının env'ini toplar.

        Tek tek değil HEPSİ toplanıyor: dört çağrıdan üçünü kapatıp birini
        açık bırakmak bu depoda ölçülmüş arıza şekli.
        """
        import subprocess as sp
        kutu = []

        def fake_run(cmd, **kwargs):
            kutu.append(kwargs.get("env"))
            class _R:
                returncode = 0
                stdout = b""
                stderr = b""
            return _R()

        monkeypatch.setattr(sp, "run", fake_run)
        monkeypatch.setattr(BaseCLIProvider, "_cli_installed", staticmethod(lambda name: True))
        monkeypatch.setattr(BaseCLIProvider, "_resolve_exec", staticmethod(lambda cmd: list(cmd)))
        # unityMCP dalı da koşsun: kayıt çağrılarının YARISI o dalın içinde.
        monkeypatch.setattr(um.unity_mcp_manager, "mcp_url", lambda *a, **k: "http://127.0.0.1:8080/mcp")
        monkeypatch.setattr(um.unity_mcp_manager, "api_headers", lambda: {"X-API-Key": "sir"})

        provider._register_mcp("/tmp/launcher", str(tmp_path), "http://localhost:8000")
        assert kutu, "hiç kayıt çağrısı yapılmadı — test kendi kendini ölçüyor"
        return kutu

    def test_every_claude_mcp_registration_call_is_gated(self, canaries, monkeypatch, tmp_path):
        from providers.claude_provider import ClaudeCodeProvider
        envler = self._kayit_envleri(
            monkeypatch, ClaudeCodeProvider("claude-opus-4-6"), tmp_path)
        assert len(envler) == 4, f"beklenen 4 kayıt çağrısı, bulunan {len(envler)}"
        for env in envler:
            assert env is not None, "bir kayıt çağrısı hâlâ env= almıyor"
            assert_no_canaries(env, allowed=("ANTHROPIC_API_KEY",))

    def test_the_claude_registration_still_gets_what_the_cli_needs(
        self, canaries, monkeypatch, tmp_path
    ):
        """KARŞIT YÖN: `claude mcp add` HOME'suz ~/.claude'u bulamaz, PATH'siz
        hiç başlamaz. Bu testin kırılması "MCP kaydı sessizce hiç yapılmıyor"
        demektir ve kullanıcı yalnız 'Unity araçları görünmüyor' olarak görür."""
        from providers.claude_provider import ClaudeCodeProvider
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/Users/test/.claude")
        envler = self._kayit_envleri(
            monkeypatch, ClaudeCodeProvider("claude-opus-4-6"), tmp_path)
        for env in envler:
            assert env.get("PATH")
            assert env.get("HOME")
            assert env.get("CLAUDE_CONFIG_DIR") == "/Users/test/.claude"

    def test_every_codex_mcp_registration_call_is_gated(self, canaries, monkeypatch, tmp_path):
        from providers.codex_provider import CodexProvider
        envler = self._kayit_envleri(
            monkeypatch, CodexProvider("gpt-5.6-codex"), tmp_path)
        # 5, 4 değil: kaynakta DÖRT `sp.run` var ama ilki bir döngünün içinde
        # ("unityai" ve "antigravity" eski kayıtları) → çalışma zamanında beşe
        # çıkıyor. Statik kapı testi (test_spawn_env_gate) 4 sayar; ikisinin
        # farklı sayması normal ve ikisi de gerekli — biri kaynağı, diğeri
        # gerçekten koşan çağrıları ölçüyor.
        assert len(envler) == 5, f"beklenen 5 kayıt çağrısı, bulunan {len(envler)}"
        for env in envler:
            assert env is not None
            assert_no_canaries(env, allowed=("OPENAI_API_KEY",))

    def test_the_codex_registration_keeps_codex_home(self, canaries, monkeypatch, tmp_path):
        """CODEX_HOME düşerse config.toml'un yeri değişir ve yazılan MCP kaydı
        codex'in okuduğu yere gitmez — arıza "araçlar görünmüyor" biçiminde
        SESSİZ olur (codex_session._configured_codex_mcp_names aynı değişkeni
        okuyor, iki yer uyuşmak zorunda)."""
        from providers.codex_provider import CodexProvider
        monkeypatch.setenv("CODEX_HOME", "/Users/test/.codex")
        envler = self._kayit_envleri(
            monkeypatch, CodexProvider("gpt-5.6-codex"), tmp_path)
        for env in envler:
            assert env.get("CODEX_HOME") == "/Users/test/.codex"
            assert env.get("PATH")


# ── 7. CLI model/durum listesi: `cursor models`, `opencode models`, `cursor status` ──

class TestCliModelListesiSpawnOrtami:
    """`config_routes._run_cli_capture` `env=` HİÇ vermiyordu."""

    def _capture(self, monkeypatch, family):
        from routes import config_routes as cr
        box = {}

        async def fake(*args, **kwargs):
            box["env"] = kwargs.get("env")
            raise _SpawnCaptured()

        monkeypatch.setattr(cr.asyncio, "create_subprocess_exec", fake)
        try:
            asyncio.run(cr._run_cli_capture(["x", "models"], family))
        except _SpawnCaptured:
            pass
        assert box.get("env") is not None, "spawn hiç yapılmadı — test kendini ölçüyor"
        return box["env"]

    def test_the_cursor_model_listing_child_is_gated(self, canaries, monkeypatch):
        assert_no_canaries(self._capture(monkeypatch, "cursor"))

    def test_the_opencode_model_listing_child_gets_no_vendor_key(self, canaries, monkeypatch):
        """EN SİVRİ NOKTA. `_PROVIDER_ENV_ALLOWLIST["opencode"]` yorumu vendor
        anahtarlarının OpenCode'a BİLEREK verilmediğini yazıyor; `opencode
        models` çağrı yeri tam da onları geçiriyordu. Yani kontrolün BEYAN
        EDİLMİŞ amacı tek bir çağrı yerinde bozuluyordu."""
        env = self._capture(monkeypatch, env_family("opencode"))
        assert_no_canaries(env)
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env

    def test_the_listing_still_gets_what_the_cli_needs_to_run(self, canaries, monkeypatch):
        env = self._capture(monkeypatch, "cursor")
        for name in ("PATH", "HOME"):
            if name in os.environ:
                assert env.get(name) == os.environ[name], f"{name} düştü"


class TestCiplakCliAdlariDogruAileyeDusuyor:
    """`_run_cli_capture` elinde MODEL adı değil ÇIPLAK CLI adı var ("opencode").

    "opencode:" öneki çıplak adı kaçırıyor ve bilinmeyen ad "claude"a düştüğü
    için OpenCode'un süreci ANTHROPIC_API_KEY alıyordu — 2026-07-28'de
    "cursor"da ölçülen tuzağın BİREBİR tekrarı, farklı sağlayıcıda. Bu yüzden
    her önek artık çıplak ada da uyuyor ve iki biçim de sınanıyor.
    """

    @pytest.mark.parametrize("name,family", [
        ("opencode", "opencode"), ("opencode:anthropic/claude", "opencode"),
        ("codex", "codex"), ("gpt-5.6-codex", "codex"),
        ("kimi", "kimi"), ("kimi-k3", "kimi"),
        ("agy", "agy"), ("agy-claude-sonnet-4-6", "agy"), ("gemini-3.6-flash", "agy"),
        ("cursor", "cursor"), ("copilot", "copilot"),
    ])
    def test_the_bare_cli_name_lands_in_its_own_family(self, name, family):
        assert env_family(name) == family

    def test_a_bare_name_never_silently_hands_over_another_vendors_key(self, canaries):
        """Yukarıdaki eşlemenin ASIL sonucu: yanlış aile = başka bir vendor'ın
        anahtarını teslim etmek. Eşleme testi tek başına bunu göstermiyor."""
        for cli in ("opencode", "cursor", "copilot", "codex", "kimi", "agy"):
            env = build_spawn_env(env_family(cli))
            assert "ANTHROPIC_API_KEY" not in env, f"{cli} Anthropic anahtarını alıyor"
