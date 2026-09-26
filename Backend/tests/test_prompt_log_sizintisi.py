"""K5 — prompt kalıcı loglara ve argv'ye sızmıyor mu?

İKİ AYRI MARUZİYET, iki ayrı yarım:

(a) KALICI LOG. Zincir 2026-08-01'de uçtan uca ölçüldü:
    cli_base `[CMD]` satırı → backend stdout → Electron `console.log`
    → `fileLog` → `%TEMP%/gamachine.log` (`fs.appendFileSync`).
    Yani `[CMD]` uçucu bir konsol satırı değildi; kullanıcının sohbete
    yapıştırdığı her şey diske birikiyordu. Çözüm: `maskeli_cmd`.

(b) ARGV. Prompt son pozisyonel argüman olduğu için makinedeki HER sürece
    görünüyordu (`ps` / `Get-CimInstance Win32_Process`). Çözüm: stdin kabulü
    CANLI ölçülen CLI'larda prompt stdin'e taşındı (`prompt_via_stdin`).

Testler ürünün GERÇEK argv'lerini üretiyor — her sağlayıcının kendi
`_build_cmd`'i çağrılıyor. Uydurma bir girdiyle sınanan muhafız, ürünün o
girdiyi hiç üretmediği durumu göremiyor (K3'ün dersi).
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

# Ürünün üreteceği metinde ARANACAK iğne. Gerçek bir sır değil; bir kullanıcının
# sohbete yapıştırabileceği türden ayırt edici bir dize.
CANARY = "GIZLI-PROMPT-ICERIGI-4f3a9c"

# Prompt'u stdin'den okuduğu CANLI ÖLÇÜLEN sağlayıcılar (2026-08-01).
STDIN_SAGLAYICILAR = {"claude", "codex", "copilot", "opencode"}

# ⛔ ADLANDIRILMIŞ İSTİSNALAR — prompt HÂLÂ argv'de.
#   cursor : CLI bu makinede kurulu değil → stdin kabulü ÖLÇÜLEMEDİ.
#   kimi   : abonelik yok ve CLI kurulu değil → ölçülemez.
#   agy    : ÖLÇÜLDÜ ve OLMUYOR — `agy --print` argümanı ZORUNLU kılıyor
#            ("flag needs an argument: -print"), yani yol yapısal olarak kapalı.
# Bu küme bilerek TESTE yazıldı: sessizce büyürse test kırılır.
ARGV_ISTISNALARI = {"cursor", "kimi", "agy"}


def _mock_unity_mcp(running=False):
    m = MagicMock()
    m.unity_mcp_manager.is_running.return_value = running
    m.unity_mcp_manager.mcp_port = 8080
    # The real manager returns None while Unity is down; a bare MagicMock
    # would be truthy and end up in claude's --mcp-config JSON.
    m.unity_mcp_manager.mcp_url.return_value = None
    return patch.dict(sys.modules, {"unity_ai_mcp": MagicMock(unity_mcp_manager=m.unity_mcp_manager),
                                    "unity_ai_mcp.unity_mcp_manager": m})


def _saglayicilar(prompt: str):
    """Sağlayıcıların GERÇEK argv'lerini üretir → [(ad, provider, cmd), ...]

    agy burada yok: prompt'u `_build_cmd`'den geçmiyor, spawn anında ayrı
    argüman olarak veriliyor (cli_base `agy_prompt`). Ayrı testte ele alınıyor.
    """
    from providers.claude_provider import ClaudeCodeProvider
    from providers.codex_provider import CodexProvider
    from providers.cursor_provider import CursorProvider
    from providers.copilot_provider import CopilotProvider
    from providers.opencode_provider import OpenCodeProvider

    kurulanlar = [
        ("claude", ClaudeCodeProvider(binary_name="claude-opus-5")),
        ("codex", CodexProvider(binary_name="gpt-5.5")),
        ("cursor", CursorProvider(binary_name="cursor-composer-2.5")),
        ("copilot", CopilotProvider(binary_name="copilot-claude-sonnet-5")),
        ("opencode", OpenCodeProvider(binary_name="opencode:opencode/big-pickle")),
    ]
    with _mock_unity_mcp(), \
            patch("providers.cursor_provider.resolve_cursor_cmd", return_value=["agent"]), \
            patch("providers.copilot_provider.resolve_copilot_cmd", return_value=["copilot"]), \
            patch("providers.opencode_provider.resolve_opencode_cmd", return_value=["opencode"]):
        return [(ad, p, p._build_cmd(prompt)) for ad, p in kurulanlar]


class TestPromptArgvdeGorunmuyor(unittest.TestCase):
    """K5(b) — `ps` ile okunabilen komut satırında prompt var mı?"""

    def test_olculen_saglayicilarda_prompt_argvde_HIC_yok(self):
        for ad, p, cmd in _saglayicilar(CANARY):
            if ad not in STDIN_SAGLAYICILAR:
                continue
            with self.subTest(saglayici=ad):
                self.assertNotIn(CANARY, " ".join(str(x) for x in cmd),
                                 f"{ad}: prompt hâlâ argv'de — `ps` ile okunabilir")
                # POZİTİF KONTROL: prompt kaybolmadı, stdin yüküne geçti. Bu
                # olmadan "argv'de yok" testi, prompt'u tamamen düşüren bozuk
                # bir sağlayıcıda da yeşil kalırdı.
                self.assertIn(CANARY, p._stdin_payload or "",
                              f"{ad}: prompt argv'den çıkmış ama stdin yüküne de girmemiş")

    def test_istisnalar_ADLANDIRILMIS_ve_buyumemis(self):
        """Argv'de kalan sağlayıcı kümesi sessizce büyüyemez.

        Bir istisna gerekçesiz eklenirse bu test kırılır ve gerekçe yazılmaya
        zorlanır — K3'te aynı desen tek istisnayı görünür tutmuştu.
        """
        from providers.cli_base import BaseCLIProvider
        self.assertFalse(BaseCLIProvider.prompt_via_stdin,
                         "varsayılan argv OLMALI: stdin kabulü ölçülmeden varsayılamaz")
        for ad, p, _cmd in _saglayicilar("x"):
            with self.subTest(saglayici=ad):
                if ad in STDIN_SAGLAYICILAR:
                    self.assertTrue(p.prompt_via_stdin, f"{ad}: stdin'e taşınmış olmalıydı")
                else:
                    self.assertIn(ad, ARGV_ISTISNALARI,
                                  f"{ad}: argv'de kalıyor ama adlandırılmış istisna değil")


class TestHamCikti_LoglaKaydedilmiyor(unittest.TestCase):
    """Denetim bulgusu `prompt-derived-log-disclosure` (1 Ağu 2026).

    `[CMD]`'yi maskelemek yetmedi: Codex dalı her ham stdout satırının ilk 300
    karakterini INFO seviyesinde basıyordu ve Codex'in ham stdout'u MODELİN
    CEVABINI taşıyor. Cevap prompt'u tekrarlayabiliyor ya da okuduğu dosya
    içeriğini taşıyabiliyor → aynı kalıcı log dosyasına bu yoldan geri giriyordu.

    Sır maskesi bunu kurtarmıyor: o ADLANDIRILMIŞ kimlik bilgilerini maskeliyor,
    serbest metni değil.
    """

    def test_RAW_log_satiri_cocugun_ciktisini_TASIMIYOR(self):
        """Kaynak metnini değil DAVRANIŞI ölçer: gerçek `analyze_code` koşuyor,
        sahte bir Codex çocuğu stdin'de aldığı canary'yi kendi cevabı gibi geri
        yazıyor, ve log işleyicisi o canary'yi görüyor mu diye bakılıyor."""
        import asyncio
        import logging
        from providers import cli_base

        canary = "GIZLI-CEVAP-ICERIGI-9b21c7"

        class SahteCodex(cli_base.BaseCLIProvider):
            prompt_via_stdin = True

            def _write_mcp_config(self, workspace):
                return ""

            def _get_file_tree(self, workspace):
                return ""

            def _build_cmd(self, prompt, thinking_level="medium", workspace=None):
                self._stdin_payload = prompt
                cocuk = ("import json,sys; p=sys.stdin.read(); "
                         "print(json.dumps({'type':'item.completed',"
                         "'item':{'type':'agent_message','text':p}}))")
                return [sys.executable, "-c", cocuk]

        yakalanan = []

        class Yakala(logging.Handler):
            def emit(self, record):
                yakalanan.append(record.getMessage())

        h = Yakala()
        eski_seviye, eski_yayilim = cli_base.logger.level, cli_base.logger.propagate
        cli_base.logger.setLevel(logging.INFO)
        cli_base.logger.propagate = False
        cli_base.logger.addHandler(h)
        try:
            async def kostur():
                async for _ev in SahteCodex(binary_name="gpt-test").analyze_code(
                        canary, cwd=os.path.dirname(__file__)):
                    pass
            asyncio.run(kostur())
        finally:
            cli_base.logger.removeHandler(h)
            cli_base.logger.setLevel(eski_seviye)
            cli_base.logger.propagate = eski_yayilim

        raw_satirlari = [m for m in yakalanan if "[RAW#" in m]
        # POZİTİF KONTROL: RAW yolu gerçekten koştu mu? Koşmadıysa test boştur.
        self.assertTrue(raw_satirlari, "[RAW#] satırı hiç üretilmedi — test bir şey ölçmüyor")
        for m in raw_satirlari:
            self.assertNotIn(canary, m, "çocuğun çıktısı kalıcı log satırına giriyor")


class TestDogrulamaTuruBulgulari(unittest.TestCase):
    """Doğrulama turunun (1 Ağu 2026) taşıma/yaşam döngüsünde bulduğu üç kusur."""

    def test_STDERR_satiri_da_icerik_TASIMIYOR(self):
        """`stderr_prompt_log_disclosure`: stdout'u susturmak yetmiyordu —
        `_drain_stderr` her satırı kelimesi kelimesine kalıcı log'a yazıyordu ve
        bir CLI/model prompt'u oraya da yankılayabiliyor."""
        yol = os.path.join(os.path.dirname(__file__), "..", "app", "providers", "cli_base.py")
        with open(yol, encoding="utf-8") as f:
            satirlar = [s for s in f.read().splitlines()
                        if "[STDERR]" in s and "logger." in s]
        self.assertTrue(satirlar, "[STDERR] log satırı bulunamadı — test bayatlamış")
        for s in satirlar:
            self.assertNotIn("{decoded}", s, f"ham stderr içeriği loglanıyor: {s.strip()}")

    def test_prompt_TESLIM_EDILEMEZSE_tur_basarili_sayilmiyor(self):
        """`broken_pipe_prompt_not_delivered`: ilk düzeltme kırık boruyu yalnız
        loglayıp devam ediyordu → prompt teslim EDİLMEMİŞken çocuğun geçerli
        stdout'u ve `exit 0`'ı gerçek cevap sanılıyordu. Sessiz yanlış cevap,
        görünür hatadan pahalı."""
        import asyncio
        from providers.cli_base import BaseCLIProvider

        class StdinKapatan(BaseCLIProvider):
            prompt_via_stdin = True

            def _write_mcp_config(self, workspace):
                return ""

            def _get_file_tree(self, workspace):
                return ""

            def _build_cmd(self, prompt, thinking_level="medium", workspace=None):
                self._stdin_payload = prompt
                # stdin'i okumadan KAPATIR, sonra geçerli bir cevap basıp 0 döner.
                cocuk = ("import sys,os,json; os.close(0); "
                         "print(json.dumps({'type':'item.completed','item':"
                         "{'type':'agent_message','text':'ISTENMEYEN_BASARI'}}))")
                return [sys.executable, "-c", cocuk]

        olaylar = []

        async def kostur():
            async for ev in StdinKapatan(binary_name="gpt-test").analyze_code(
                    "Z" * 500_000, cwd=os.path.dirname(__file__)):
                olaylar.append(ev)

        asyncio.run(kostur())
        hatalar = [e for e in olaylar if e.get("type") == "error"]
        metinler = "".join(str(e.get("content", "")) for e in olaylar)
        self.assertTrue(hatalar, "teslim edilemeyen prompt sessizce başarılı sayıldı")
        self.assertNotIn("ISTENMEYEN_BASARI", metinler,
                         "sorulmamış bir soruya gelen cevap kullanıcıya sunuluyor")

    def test_DURDUR_ust_uste_binen_TUM_turlari_kapsiyor(self):
        """`concurrent_turn_stop_orphan` için GUARD: `active_provider` tek
        yuvaydı, ikinci tur birincinin referansını eziyordu ve Durdur yalnız
        sonuncuya ulaşıyordu. Eşzamanlılığı engelleyen bir kod YOK — sadece
        arayüz alışkanlığı vardı; alışkanlık enforcement değildir."""
        from providers.oneshot_cli import OneShotSession

        class SahteSaglayici:
            def __init__(self):
                self._active_process = None
                self.iptal_edildi = False

            async def cancel_active_process(self):
                self.iptal_edildi = True
                return True

        s = OneShotSession("copilot", 1)
        birinci, ikinci = SahteSaglayici(), SahteSaglayici()
        s.active_provider = birinci
        s.active_provider = ikinci   # ikinci tur birinciyi EZİYORDU
        self.assertIn(birinci, s.iptal_edilecekler(),
                      "ilk turun süreci Durdur'un kapsamı dışında kalıyor")
        self.assertIn(ikinci, s.iptal_edilecekler())


class TestDogrulamaTuru2Bulgulari(unittest.TestCase):
    """İkinci doğrulama turunun taşıma/oturum tarafında bulduğu üç kusur."""

    def test_BASARISIZ_turda_da_stderr_icerigi_loga_girmiyor(self):
        """`stderr_failure_log_leak`: satır logunu ölçüye çevirmek yetmiyordu —
        `[FAILED]`/`[NO_OUTPUT]` dalları aynı tamponu bütün hâlinde log'a
        basıyordu. İçerik kullanıcıya gitmeye DEVAM etmeli, diske değil."""
        import asyncio
        import logging
        from providers import cli_base

        canary = "CANARY-STDERR-7c1"

        class StderrYazan(cli_base.BaseCLIProvider):
            prompt_via_stdin = True

            def _write_mcp_config(self, workspace):
                return ""

            def _get_file_tree(self, workspace):
                return ""

            def _build_cmd(self, prompt, thinking_level="medium", workspace=None):
                # canary YALNIZ stdin'den gidiyor — argv'ye hiç girmiyor ki
                # ölçüm `[CMD]` satırıyla karışmasın.
                self._stdin_payload = prompt
                return [sys.executable, "-c",
                        "import sys; p=sys.stdin.read(); sys.stderr.write(p); "
                        "sys.stderr.flush(); sys.exit(7)"]

        kayitlar, olaylar = [], []

        class Yakala(logging.Handler):
            def emit(self, record):
                kayitlar.append(record.getMessage())

        h = Yakala()
        eski_s, eski_y = cli_base.logger.level, cli_base.logger.propagate
        cli_base.logger.setLevel(logging.INFO)
        cli_base.logger.propagate = False
        cli_base.logger.addHandler(h)
        try:
            async def kostur():
                async for ev in StderrYazan(binary_name="gpt-test").analyze_code(
                        canary, cwd=os.path.dirname(__file__)):
                    olaylar.append(ev)
            asyncio.run(kostur())
        finally:
            cli_base.logger.removeHandler(h)
            cli_base.logger.setLevel(eski_s)
            cli_base.logger.propagate = eski_y

        self.assertFalse([m for m in kayitlar if canary in m],
                         "çocuğun stderr içeriği kalıcı log'a giriyor")
        # POZİTİF KONTROL: teşhis kaybolmadı — içerik kullanıcıya ulaşıyor.
        self.assertTrue(any(canary in str(e.get("content", "")) for e in olaylar),
                        "hata mesajı da içeriği kaybetmiş — teşhis yok oldu")

    def test_agy_oturumu_da_UST_USTE_binen_turlari_kapsiyor(self):
        """`agy_concurrent_stop_orphan`: aynı tek-yuva deseni `AgySession`'da da
        vardı; ilk düzeltme yalnız `OneShotSession`'ı kapatmıştı. Bir sınıfı
        kapatmak, kaç kopyası olduğunu SAYMAYI gerektiriyor."""
        from providers.agy_session import AgySession
        s = AgySession(1)
        a, b = object(), object()
        s.active_provider = a
        s.active_provider = b
        self.assertIn(a, s.iptal_edilecekler(), "ilk turun süreci Durdur dışında")
        self.assertIn(b, s.iptal_edilecekler())

    def test_KAPANMIS_oturuma_gec_gelen_saglayici_DAMGALANIYOR(self):
        """`oneshot_provider_after_close`: kapanıştan SONRA atanan bir sağlayıcı
        hiçbir Durdur yoluna görünmüyordu.

        ⚠️ İlk çözüm zamanlamaya dayanıyordu (süreci 5 sn bekleyip bir kez iptal)
        ve üçüncü doğrulama turu tam oradan girdi: süreç tavandan sonra doğarsa
        gözlemci kalmıyordu. Sözleşme değişti — sağlayıcı DAMGALANIYOR ve spawn
        hiç yapılmıyor (bkz. TestDogrulamaTuru3Bulgulari).
        """
        import asyncio
        from providers import oneshot_cli

        class Sahte:
            def __init__(self):
                self._active_process = None

        async def senaryo():
            s = oneshot_cli.get_session("copilot", 4242)
            await oneshot_cli.close_session("copilot", 4242)
            gec = Sahte()
            s.active_provider = gec          # kapanmış oturuma geç atama
            self.assertTrue(getattr(gec, "_oturum_kapandi", False),
                            "kapanıştan sonra atanan sağlayıcı damgalanmadı")

        asyncio.run(senaryo())


class TestDogrulamaTuru3Bulgulari(unittest.TestCase):
    """Üçüncü doğrulama turunun yaşam döngüsünde bulduğu iki kusur.

    İkisi de aynı dersi verdi: bir yarışı SONRADAN iptal ederek kazanmaya
    çalışmak yakınsamıyor — tavan ne olursa olsun ötesi var. Ölçüt değişti,
    yarış kaldırıldı: kapanmış oturumda çocuk süreç HİÇ doğmuyor.
    """

    def test_KAPANMIS_oturumda_surec_HIC_DOGMUYOR(self):
        import asyncio
        from providers import oneshot_cli, agy_session
        from providers.cli_base import BaseCLIProvider

        class Uyuyan(BaseCLIProvider):
            prompt_via_stdin = True

            def _write_mcp_config(self, workspace):
                return ""

            def _get_file_tree(self, workspace):
                return ""

            def _build_cmd(self, prompt, thinking_level="medium", workspace=None):
                self._stdin_payload = prompt
                return [sys.executable, "-c", "import time; time.sleep(20)"]

        async def senaryo():
            for ad, oturum_al, kapat in (
                ("oneshot", lambda: oneshot_cli.get_session("copilot", 7001),
                            lambda: oneshot_cli.close_session("copilot", 7001)),
                ("agy", lambda: agy_session.get_session(7002),
                        lambda: agy_session.close_session(7002)),
            ):
                s = oturum_al()
                await kapat()
                p = Uyuyan(binary_name="probe-gate")
                s.active_provider = p          # kapanıştan SONRA atama
                olaylar = [ev async for ev in p.analyze_code("merhaba", cwd=".")]
                with self.subTest(oturum=ad):
                    self.assertIsNone(p._active_process,
                                      f"{ad}: kapanmış oturumda çocuk süreç doğdu")
                    self.assertTrue(any(e.get("type") == "error" for e in olaylar),
                                    f"{ad}: engelleme kullanıcıya bildirilmedi")

        asyncio.run(senaryo())

    def test_stdin_KAPANIS_hatasi_da_cevabi_engelliyor(self):
        """`stdin_close_failure_answer`: kapanış asenkron — `drain()` çocuk okuma
        ucunu kapatmadan dönebiliyor ve `close()` hatayı senkron yüzeye
        çıkarmıyor. Hata `wait_closed()`'da fırlıyor; o nokta beklenmediği için
        teslim edilmemiş prompt'un ardından gelen cevap kabul ediliyordu."""
        yol = os.path.join(os.path.dirname(__file__), "..", "app", "providers", "cli_base.py")
        with open(yol, encoding="utf-8") as f:
            kaynak = f.read()
        # Kaynak nöbetçisi: davranışı sürmek gerçek bir asyncio transport seam'i
        # gerektiriyor; burada kollanan şey çağrının VARLIĞI — düşerse sınıf geri gelir.
        self.assertIn("wait_closed()", kaynak,
                      "stdin kapanışı beklenmiyor — ertelenmiş hata görünmez olur")


class TestStdinDevriTeslimindeIptalCalisiyor(unittest.TestCase):
    """Denetim bulgusu `uncancellable-pre-reader-stall` (1 Ağu 2026).

    `_active_process` yalnız write+drain+close bittikten SONRA atanıyordu. Çocuk
    stdin'i tüketmeden oyalanırsa `drain()` dolu boruda bloke oluyor ve o
    pencerede süreç ÇALIŞIYOR ama `cancel_active_process` onu göremiyor:
    Durdur'a basan kullanıcı `False` alıyor, tur ve çocuk süreç ayakta kalıyor.

    Pencere prompt argv'deyken YOKTU — stdin'e taşıma onu açtı. Yani bu, K5(b)
    düzeltmesinin kendi getirdiği kusurdu.
    """

    def test_stdin_bloke_olsa_bile_DURDUR_sureci_yakaliyor(self):
        import asyncio
        from providers.cli_base import BaseCLIProvider

        class OkumayanCocuk(BaseCLIProvider):
            prompt_via_stdin = True

            def _write_mcp_config(self, workspace):
                return ""

            def _get_file_tree(self, workspace):
                return ""

            def _build_cmd(self, prompt, thinking_level="medium", workspace=None):
                self._stdin_payload = prompt
                # stdin'i HİÇ okumadan bekler → drain() dolu boruda bloke olur.
                return [sys.executable, "-c", "import time; time.sleep(30)"]

        p = OkumayanCocuk(binary_name="probe-stall")

        async def senaryo():
            async def tur():
                async for _ev in p.analyze_code("Y" * 200_000,
                                                cwd=os.path.dirname(__file__)):
                    pass
            gorev = asyncio.create_task(tur())
            # Süreç doğduğu ANDAN itibaren iptal edilebilmeli.
            for _ in range(100):
                if p._active_process is not None:
                    break
                await asyncio.sleep(0.1)
            self.assertIsNotNone(p._active_process,
                                 "süreç doğdu ama kayıt edilmedi — Durdur onu göremez")
            iptal = await p.cancel_active_process()
            self.assertTrue(iptal, "Durdur çalışan çocuğu sonlandıramadı")
            try:
                await asyncio.wait_for(gorev, timeout=20)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                gorev.cancel()

        asyncio.run(senaryo())


class TestPromptLogaGirmiyor(unittest.TestCase):
    """K5(a) — `[CMD]` satırı kalıcı log dosyasına ne yazıyor?"""

    def test_hicbir_saglayicinin_CMD_satiri_prompt_ICERMIYOR(self):
        from providers.cli_base import maskeli_cmd
        for ad, _p, cmd in _saglayicilar(CANARY):
            with self.subTest(saglayici=ad):
                self.assertNotIn(CANARY, maskeli_cmd(cmd, CANARY),
                                 f"{ad}: prompt [CMD] satırında görünüyor")

    def test_argvde_KALAN_saglayicida_maske_gercekten_calisiyor(self):
        """Maske testi boşa dönmesin: hâlâ argv kullanan bir sağlayıcıda önce
        sızıntının VAR olduğu, sonra maskenin kapattığı ölçülüyor."""
        from providers.cli_base import maskeli_cmd
        argvli = [(ad, cmd) for ad, _p, cmd in _saglayicilar(CANARY)
                  if ad not in STDIN_SAGLAYICILAR]
        self.assertTrue(argvli, "argv kullanan sağlayıcı kalmamış — test bayatladı")
        for ad, cmd in argvli:
            with self.subTest(saglayici=ad):
                ham = " ".join(str(x) for x in cmd)
                self.assertIn(CANARY, ham, f"{ad}: pozitif kontrol düştü")
                maskeli = maskeli_cmd(cmd, CANARY)
                self.assertNotIn(CANARY, maskeli)
                self.assertIn("<prompt:", maskeli)
                self.assertIn(str(cmd[0]), maskeli, f"{ad}: ikili adı bile kaybolmuş")

    def test_prompt_bir_HINT_metnine_yapisiksa_da_maskeleniyor(self):
        """Maskelenecek öğe prompt'a EŞİT değil, onu İÇERİYOR — eşitlik ölçütü
        kullanan bir maske burada sessizce açılırdı."""
        from providers.cli_base import maskeli_cmd
        cmd = ["opencode", "run", "IMPORTANT — follow exactly:\n" + CANARY]
        self.assertNotIn(CANARY, maskeli_cmd(cmd, CANARY))

    def test_bos_prompt_her_seyi_maskelemiyor(self):
        """Boş dize her metnin alt dizesidir → naif bir maske komutu komple yerdi."""
        from providers.cli_base import maskeli_cmd
        self.assertEqual(maskeli_cmd(["claude", "--model", "opus"], ""),
                         "claude --model opus")

    def test_CMD_log_satiri_maskeden_GECIYOR(self):
        """Kaynak düzeyinde nöbetçi: `[CMD]` satırı ham `' '.join(cmd)`'ye dönmesin.

        Diğer testler maskeyi ölçüyor ama maskeyi ÇAĞIRAN satırı değil; çağrı
        silinse hepsi yeşil kalırdı ve kusur tam orada yaşıyordu.
        """
        yol = os.path.join(os.path.dirname(__file__), "..", "app", "providers", "cli_base.py")
        with open(yol, encoding="utf-8") as f:
            kaynak = f.read()
        # Ölçüt "[CMD] geçen satır" DEĞİL "[CMD] BASAN satır": ilki yorumları ve
        # docstring'leri de yakalıyordu (bu dosyanın kendi açıklaması dahil).
        cmd_satirlari = [s for s in kaynak.splitlines()
                         if "[CMD]" in s and "logger." in s]
        self.assertTrue(cmd_satirlari, "[CMD] log satırı bulunamadı — test bayatlamış")
        for satir in cmd_satirlari:
            self.assertIn("maskeli_cmd", satir,
                          f"[CMD] satırı maskeden geçmiyor: {satir.strip()}")


if __name__ == "__main__":
    unittest.main()
