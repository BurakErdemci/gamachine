"""Ürünün config yazımı, workspace DIŞINDAKİ bir dosyayı ezememeli (K4).

Ölçülmüş açık (bu makinede 2026-08-01'de yeniden üretildi, İKİ vektör de
AYRICALIKSIZ): ürünün config yazım noktaları hedefi düz `open(path, "w")` ile
açıyordu ve kullanıcının projesindeki yol yönlendirilmiş olabiliyordu.

    dosyanın KENDİSİ kurbana sabit bağla bağlı   → kurban EZİLDİ
    ANA DİZİN kurbanın dizinine junction'lı      → kurban EZİLDİ

⚠️ Neden `os.path.islink` yetmiyor (ölçüldü): junction'ı GÖRMÜYOR (`False`
döndürüyor) ve sabit bağ zaten bir bağ değil, ikinci bir addır — `O_NOFOLLOW`
da onu reddetmiyor. Ayrıca "önce kontrol et sonra aç" sırası TOCTOU yarışını
kaybediyor. Doğru soru "bu yol bir bağ mı" DEĞİL, **"açtığım şey beklediğim
dosya mı"** — `safe_paths.dogrula_kimlik` ona açıştan SONRA cevap veriyor.

Bu dosya iki katmanı da sınıyor: yardımcının kendisi VE ürünün gerçek yazım
yolları. İkincisi olmadan test bir şey ölçmez — bu depoda tekrarlayan arıza
şekli "yardımcı doğru ama çağrı yeri onu kullanmıyor".
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from providers.workspace_config import guvenli_config_yaz  # noqa: E402

KURBAN = "KURBANIN-ONEMLI-VERISI"


def _junction(bag: str, hedef: str) -> bool:
    """Ayrıcalıksız dizin yönlendirmesi: Windows'ta junction, POSIX'te symlink."""
    try:
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", bag, hedef],
                               capture_output=True, text=True, timeout=30)
            return r.returncode == 0
        os.symlink(hedef, bag)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _dosya_bagi(bag: str, hedef: str) -> bool:
    """Ayrıcalıksız dosya yönlendirmesi: sabit bağ."""
    try:
        os.link(hedef, bag)
        return True
    except OSError:
        return False


def _kurulum(tmp_path, kurban_adi: str):
    ws = tmp_path / "workspace"
    ws.mkdir()
    disari = tmp_path / "disarisi"
    disari.mkdir()
    kurban = disari / kurban_adi
    kurban.write_text(KURBAN, encoding="utf-8")
    return str(ws), str(disari), kurban


# ── Yardımcının kendisi ───────────────────────────────────────────────────


def test_dosya_kurbana_bagliyken_yazilmiyor(tmp_path):
    ws, _, kurban = _kurulum(tmp_path, ".mcp.json")
    if not _dosya_bagi(os.path.join(ws, ".mcp.json"), str(kurban)):
        pytest.skip("bu makinede sabit bağ kurulamadı")

    yazildi = guvenli_config_yaz(ws, ".mcp.json", '{"urun":"yazdi"}')

    assert yazildi is False, "yönlendirilmiş hedefe yazıldı"
    assert kurban.read_text(encoding="utf-8") == KURBAN, "workspace dışındaki dosya EZİLDİ"


def test_ana_dizin_junction_iken_yazilmiyor(tmp_path):
    ws, disari, kurban = _kurulum(tmp_path, "cli.json")
    if not _junction(os.path.join(ws, ".cursor"), disari):
        pytest.skip("bu makinede junction/symlink kurulamadı")

    yazildi = guvenli_config_yaz(ws, ".cursor/cli.json", '{"urun":"yazdi"}')

    assert yazildi is False
    assert kurban.read_text(encoding="utf-8") == KURBAN, "workspace dışındaki dosya EZİLDİ"


def test_normal_yazim_ve_uzerine_yazim_CALISIYOR(tmp_path):
    """Muhafız, ürünü çalışmaz hâle getirmemeli — yoksa koruma değil arızadır."""
    ws = str(tmp_path)

    assert guvenli_config_yaz(ws, ".mcp.json", '{"a":1}') is True
    assert guvenli_config_yaz(ws, ".cursor/cli.json", '{"b":2}') is True, \
        "ara dizin yaratılamadı"
    assert guvenli_config_yaz(ws, ".mcp.json", '{"a":2}') is True

    # Üzerine yazım ESKİ İÇERİĞİ BIRAKMAMALI. Yazım geçici dosya + `os.replace`
    # ile yapılıyor, yani kalıntı ancak takas yanlış kurulursa olur.
    assert json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8")) == {"a": 2}
    # Geçici dosya artığı da kalmamalı — kullanıcının `git status`'una çöp düşmesin.
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".config-")] == []


def test_kardes_dizin_ON_EK_olarak_eslesmemeli(tmp_path):
    """`/a/bc` düz dize olarak `/a/b` ile başlar ama onun ALTINDA değildir.

    Kapsama kontrolü `startswith` ile yazılsaydı kardeş dizin workspace içi
    sayılırdı. Mutasyon turunda bu vaka yoktu ve `startswith`'e düşürmek testi
    KIRMIYORDU — yani kontrolün doğruluğu ölçülmemişti.
    """
    from providers.workspace_config import _icinde_mi

    kok = str(tmp_path / "b")
    assert _icinde_mi(str(tmp_path / "b" / "icerde.json"), kok)
    assert not _icinde_mi(str(tmp_path / "bc" / "disarda.json"), kok), \
        "kardeş dizin workspace içi sayıldı (ön ek karşılaştırması)"


def test_O_NOFOLLOW_acilista_veriliyor():
    """POSIX'te son bileşen sembolik bağsa açılış reddedilmeli.

    ⚠️ Bu iddia DAVRANIŞLA ölçülemiyor: Windows'ta `os.O_NOFOLLOW` yok ve
    `safe_paths` onu `0`'a düşürüyor, yani bayrağı kaldırmak bu makinede
    hiçbir şeyi değiştirmiyor (mutasyon turunda ölçüldü — test yeşil kaldı).
    Asıl koruma zaten açıştan sonraki kimlik doğrulaması; bu bayrak POSIX'te
    ikinci hat ve kaynak düzeyinde kilitleniyor ki sessizce düşmesin.
    """
    import inspect

    from providers import workspace_config

    kaynak = inspect.getsource(workspace_config.guvenli_config_yaz)

    # ⚠️ Düz "O_NOFOLLOW kaynakta geçiyor mu" YETMEZ: `from safe_paths import
    # O_NOFOLLOW` satırı fonksiyonun İÇİNDE, yani bayrak `os.open` çağrısından
    # düşse bile kaynakta görünmeye devam ediyordu ve test KÖR kalıyordu
    # (mutasyon turunda ölçüldü). Bayrağın AÇILIŞ ÇAĞRISINDA olması aranıyor.
    acilis = [s for s in kaynak.splitlines() if "os.open(" in s]
    assert acilis, "açılış çağrısı bulunamadı"
    assert any("O_NOFOLLOW" in s for s in acilis), \
        "os.open çağrısından O_NOFOLLOW düşmüş"
    assert "dogrula_kimlik" in kaynak, "asıl koruma (kimlik doğrulaması) düşmüş"


def test_workspace_disina_bakan_goreli_yol_reddediliyor(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    disari = tmp_path / "disarisi"
    disari.mkdir()
    kurban = disari / "kurban.json"
    kurban.write_text(KURBAN, encoding="utf-8")

    assert guvenli_config_yaz(str(ws), "../disarisi/kurban.json", "{}") is False
    assert kurban.read_text(encoding="utf-8") == KURBAN


# ── Ürünün GERÇEK yazım yolları ───────────────────────────────────────────
# Yardımcının doğru olması yetmez; çağrı yerleri onu kullanmalı.


def test_cli_base_mcp_json_yolu_korunuyor(tmp_path, monkeypatch):
    from providers.cli_base import BaseCLIProvider

    ws, _, kurban = _kurulum(tmp_path, ".mcp.json")
    if not _dosya_bagi(os.path.join(ws, ".mcp.json"), str(kurban)):
        pytest.skip("bu makinede sabit bağ kurulamadı")

    p = BaseCLIProvider("claude")
    monkeypatch.setattr(p, "_launcher_path", lambda name: "/bin/true")
    monkeypatch.setattr(BaseCLIProvider, "_ensure_exec", staticmethod(lambda path: None))

    p._write_mcp_config(ws)

    assert kurban.read_text(encoding="utf-8") == KURBAN, \
        "cli_base yazım noktası hâlâ yönlendirmeyi takip ediyor"


def _cursor_saglayici():
    from providers.cursor_provider import CursorProvider

    p = CursorProvider.__new__(CursorProvider)
    p._launcher_path = lambda name: "/bin/true"
    p._ensure_exec = lambda path: None
    return p


def test_cursor_mcp_json_yolu_korunuyor(tmp_path):
    """Ana dizin junction'lıyken `.cursor/mcp.json` yazımı kurbanı ezmemeli."""
    ws, disari, kurban = _kurulum(tmp_path, "mcp.json")
    if not _junction(os.path.join(ws, ".cursor"), disari):
        pytest.skip("bu makinede junction/symlink kurulamadı")

    _cursor_saglayici()._register_mcp("/bin/true", ws, "http://localhost:8000")

    assert kurban.read_text(encoding="utf-8") == KURBAN, \
        "cursor mcp.json yazım noktası hâlâ yönlendirmeyi takip ediyor"


def test_cursor_cli_json_yolu_korunuyor(tmp_path):
    """`.cursor/cli.json` K4'ün EN SİNSİ vakası.

    Cursor'un `Write`/`Shell` deny-list'ini taşıyor: yönlendirilirse görünen
    sonuç bir dosyanın ezilmesi değil, POLİTİKANIN SESSİZCE KAYBI olur.

    ⚠️ Yönlendirme DOSYANIN KENDİSİNE kuruluyor, ana dizine değil. İlk yazımda
    junction `.cursor` dizinine kuruluyordu ve kod `mcp.json` başarısız olunca
    erken dönüyordu — yani test cli.json yoluna HİÇ ULAŞMIYORDU ve adının
    iddia ettiği şeyi ölçmüyordu (mutasyon turunda yakalandı: cli.json yazımını
    düz `open`'a çevirmek testi kırmıyordu).
    """
    ws, _, kurban = _kurulum(tmp_path, "cli.json")
    os.makedirs(os.path.join(ws, ".cursor"), exist_ok=True)
    if not _dosya_bagi(os.path.join(ws, ".cursor", "cli.json"), str(kurban)):
        pytest.skip("bu makinede sabit bağ kurulamadı")

    _cursor_saglayici()._register_mcp("/bin/true", ws, "http://localhost:8000")

    assert kurban.read_text(encoding="utf-8") == KURBAN, \
        "cursor cli.json yazım noktası hâlâ yönlendirmeyi takip ediyor"


def test_opencode_json_yolu_korunuyor(tmp_path):
    from providers.opencode_provider import OpenCodeProvider

    ws, _, kurban = _kurulum(tmp_path, "opencode.json")
    if not _dosya_bagi(os.path.join(ws, "opencode.json"), str(kurban)):
        pytest.skip("bu makinede sabit bağ kurulamadı")

    p = OpenCodeProvider.__new__(OpenCodeProvider)
    p._launcher_path = lambda name: "/bin/true"
    p._ensure_exec = lambda path: None
    p._approval_turn_token = ""
    p.binary_name = "opencode:opencode/grok-code"

    p._register_mcp("/bin/true", ws, "http://localhost:8000")

    assert kurban.read_text(encoding="utf-8") == KURBAN, \
        "opencode yazım noktası hâlâ yönlendirmeyi takip ediyor"


def test_hicbir_yazim_noktasi_duz_open_kullanmiyor():
    """Sınıf muhafızı: yeni bir yazım noktası eski desene dönmesin.

    Bu depoda tekrarlayan arıza şekli "yardımcı doğru ama bir çağrı yeri onu
    kullanmayı unuttu" — K3'te `.cursor/mcp.json` tam olarak öyle kapsam dışı
    kalmıştı. Tek tek davranış testleri o sınıfı kapatmaz, bu kapatır.
    """
    import inspect

    from providers import cli_base, cursor_provider, opencode_provider

    for modul, dosyalar in (
        (cli_base, [".mcp.json"]),
        (cursor_provider, [".cursor/mcp.json", ".cursor/cli.json"]),
        (opencode_provider, ["opencode.json"]),
    ):
        kaynak = inspect.getsource(modul)
        assert "guvenli_config_yaz" in kaynak, (
            f"{modul.__name__} config yazımı güvenli yola bağlı değil ({dosyalar})"
        )


def test_yazim_ATOMIK_yarim_dosya_birakmiyor(tmp_path, monkeypatch):
    """Yazma ortasındaki bir hata, config'i BOŞ ya da yarım bırakmamalı.

    Denetim bulgusu: eski biçim hedefi `ftruncate` ile boşaltıp üzerine
    yazıyordu, yani disk hatası `.cursor/cli.json`'ı boş bırakabiliyordu — ve
    onun boşalması Cursor'un Write/Shell deny-list'inin SESSİZCE KAYBI demek,
    yani K4'ün kapatmaya çalıştığı sınıfın aynısı.
    """
    ws = str(tmp_path)
    assert guvenli_config_yaz(ws, "cfg.json", '{"eski":true}') is True
    hedef = tmp_path / "cfg.json"

    gercek_replace = os.replace

    def patlayan_replace(src, dst):
        raise OSError("disk doldu")

    monkeypatch.setattr(os, "replace", patlayan_replace)
    sonuc = guvenli_config_yaz(ws, "cfg.json", '{"yeni":true}')
    monkeypatch.setattr(os, "replace", gercek_replace)

    assert sonuc is False
    assert json.loads(hedef.read_text(encoding="utf-8")) == {"eski": True}, \
        "yazma yarıda kalınca ESKİ içerik korunmalıydı"
    # Geçici dosya artık kalmamalı — kullanıcının `git status`'unda çöp bırakma.
    artik = [p.name for p in tmp_path.iterdir() if p.name.startswith(".config-")]
    assert artik == [], f"geçici dosya artığı kaldı: {artik}"


def test_sertlestirme_basarisizsa_SIR_YAZILMIYOR(tmp_path, monkeypatch):
    """`sir_tasiyor=True` sözü: ACL kısıtlanamazsa sır diske hiç inmemeli."""
    from providers import workspace_config

    monkeypatch.setattr(workspace_config, "harden_config_file", lambda p: False)

    ws = str(tmp_path)
    sonuc = guvenli_config_yaz(ws, "sirli.json", '{"X-API-Key":"KANARYA"}',
                               sir_tasiyor=True)

    assert sonuc is False
    hedef = tmp_path / "sirli.json"
    assert not hedef.exists() or "KANARYA" not in hedef.read_text(encoding="utf-8"), \
        "sıkılaştırma başarısızken sır diske yazıldı"
    artik = [p.name for p in tmp_path.iterdir() if p.name.startswith(".config-")]
    assert artik == [], f"sır taşıyan geçici dosya artığı kaldı: {artik}"


def test_sertlestirme_SIRSIZ_yazimda_da_deneniyor(tmp_path, monkeypatch):
    """`os.replace` KAYNAK dosyanın ACL'ini taşır — sertleştirme koşullu olamaz.

    Ölçülmüş gerileme (2026-08-01, Windows): sertleştirme yalnız
    `sir_tasiyor=True` iken uygulanınca, sırsız yedek yazım önceki turda
    sertleştirilmiş bir config'in üzerine dizin ACL'ini miras alan bir geçici
    dosya takıyordu. Ölçüm `BURAK\burcu:(F)` → `SYSTEM:(I)(F)` + iki miras ACE
    daha. Bayrak yalnız başarısızlığın ölümcül olup olmadığını söylemeli.
    """
    from providers import workspace_config

    cagrilanlar = []

    def _kaydet(p):
        cagrilanlar.append(p)
        return True

    monkeypatch.setattr(workspace_config, "harden_config_file", _kaydet)
    assert workspace_config.guvenli_config_yaz(
        str(tmp_path), "sirsiz.json", '{"a":1}', sir_tasiyor=False)

    assert cagrilanlar, "sırsız yazımda sertleştirme HİÇ denenmedi"
    assert all(os.path.basename(p).startswith(".config-") for p in cagrilanlar), \
        f"sertleştirme hedefe uygulandı, geçici dosyaya değil: {cagrilanlar}"


def test_sertlestirme_basarisiz_ama_SIRSIZ_ise_yazim_SURUYOR(tmp_path, monkeypatch):
    """Sertleştirememek sırsız içerikte ölümcül DEĞİL — yoksa config hiç yazılmaz.

    Bu, yukarıdaki testin karşı ağırlığı: "her zaman sertleştir" kuralı
    yanlışlıkla "sertleştiremezsen hiç yazma"ya dönüşürse `.cursor/cli.json`
    gibi sır taşımayan ama GEREKLİ dosyalar sessizce kaybolur — K4'ün
    kapatmaya çalıştığı sınıfın aynısı.
    """
    from providers import workspace_config

    monkeypatch.setattr(workspace_config, "harden_config_file", lambda p: False)
    assert workspace_config.guvenli_config_yaz(
        str(tmp_path), "sirsiz2.json", '{"b":2}', sir_tasiyor=False), \
        "sertleştirme başarısız diye SIRSIZ yazım da iptal edildi"
    assert (tmp_path / "sirsiz2.json").read_text(encoding="utf-8") == '{"b":2}'


# ── K3: paylaşımlı sır hiçbir CLI config dosyasına girmemeli ───────────────

def test_K3_hicbir_config_yazicisi_sirri_DISKE_yazmiyor(tmp_path, monkeypatch):
    """Sınıfı kapatmak giriş noktalarını SAYMAYI gerektirir — hepsini say.

    Ölçülmüş açık: unityMCP `X-API-Key`'i beş ayrı CLI config dosyasına düz
    metin yazılıyordu. Bu dosyalar MODELİN okuyabildiği yerde duruyor ve
    29 Tem'de gerçek bir projede git tarafından İZLENİYOR bulundu; ACL
    sertleştirmek çözmüyor çünkü model aynı kullanıcı olarak koşuyor.

    Çözüm stdio köprüsü: sır config'e değil, köprünün kendi okuduğu token
    dosyasına dayanıyor. Bu test tek tek yazıcıları değil, YAZICI KÜMESİNİ
    savunuyor — yeni bir sağlayıcı eklenip listeye girmezse burada değil,
    aşağıdaki sayım testinde yakalanır.
    """
    import json as _json
    from providers.cli_base import BaseCLIProvider
    from providers.cursor_provider import CursorProvider
    from providers.opencode_provider import OpenCodeProvider

    KANARYA = "KANARYA-K3-PAYLASIMLI-SIR"

    class _SahteYonetici:
        @staticmethod
        def mcp_url(host="localhost"):
            return "http://127.0.0.1:8080/mcp"

        @staticmethod
        def api_headers():
            return {"X-API-Key": KANARYA}

        @staticmethod
        def is_running():
            return True

    import unity_ai_mcp.unity_mcp_manager as umm
    monkeypatch.setattr(umm, "unity_mcp_manager", _SahteYonetici())
    monkeypatch.setenv("LOCAL_APP_TOKEN", "x")

    def _ws(ad):
        d = tmp_path / ad
        d.mkdir()
        return str(d)

    yazilanlar = []

    # 1) cli_base → workspace/.mcp.json
    ws1 = _ws("clibase")
    p1 = BaseCLIProvider.__new__(BaseCLIProvider)
    monkeypatch.setattr(p1, "_launcher_path", lambda n: "/bin/true", raising=False)
    monkeypatch.setattr(p1, "_ensure_exec", lambda p: None, raising=False)
    monkeypatch.setattr(p1, "_register_mcp", lambda *a, **k: None, raising=False)
    p1._write_mcp_config(ws1)
    yazilanlar.append(os.path.join(ws1, ".mcp.json"))

    # 2) cursor → .cursor/mcp.json
    ws2 = _ws("cursor")
    p2 = CursorProvider.__new__(CursorProvider)
    p2._register_mcp("/bin/true", ws2, "http://127.0.0.1:8000")
    yazilanlar.append(os.path.join(ws2, ".cursor", "mcp.json"))

    # 3) opencode → opencode.json
    ws3 = _ws("opencode")
    p3 = OpenCodeProvider.__new__(OpenCodeProvider)
    p3.binary_name = "opencode:opencode/grok-code"
    p3._effort_level = "auto"
    p3._approval_turn_token = ""
    p3._register_mcp("/bin/true", ws3, "http://127.0.0.1:8000")
    yazilanlar.append(os.path.join(ws3, "opencode.json"))

    bulunanlar = []
    for yol in yazilanlar:
        assert os.path.exists(yol), f"yazıcı dosyayı hiç üretmedi: {yol}"
        ham = open(yol, encoding="utf-8").read()
        if KANARYA in ham or "X-API-Key" in ham:
            bulunanlar.append(yol)
        # unityMCP kaydı DÜŞMEMELİ: sır gitti, özellik kalmalı
        d = _json.loads(ham)
        sunucular = d.get("mcpServers") or d.get("mcp") or {}
        assert "unityMCP" in sunucular, f"unityMCP kaydı düştü: {yol}"

    assert not bulunanlar, f"paylaşımlı sır diske yazıldı: {bulunanlar}"


def test_K3_unityMCP_yazan_her_saglayici_KOPRUYU_kullaniyor():
    """Sayım testi: `api_headers()`'ı config'e gömen bir sağlayıcı kalmamalı.

    Kaynak taraması BİLEREK — davranış testi yeni eklenen bir sağlayıcıyı
    göremez, çünkü onu çağırmayı bilmiyor. Burada savunulan şey bir davranış
    değil, bir DEĞİŞMEZ: `providers/` altında hiçbir yer unityMCP kaydına
    `headers` gömmüyor.

    ⛔ TEK İSTİSNA `claude_provider`: unityMCP'yi HTTP olarak başlıkla
    veriyor ve köprüye GEÇEMİYOR — ölçüldü, Claude CLI'da stdio kaydı
    `pending`de kalıyor, HTTP `connected` oluyor. 26 Eyl 2026'dan beri
    başlık bir config dosyasına değil satır içi `--mcp-config` JSON'una
    (argv) giriyor; sır hiçbir dosyaya yazılmıyor. İstisna burada
    ADLANDIRILMIŞ olsun ki sessizce büyümesin.
    """
    kok = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "providers")
    ihlaller = []
    for ad in os.listdir(kok):
        if not ad.endswith(".py") or ad == "claude_provider.py":
            continue
        metin = open(os.path.join(kok, ad), encoding="utf-8").read()
        # yorum satırlarını ele — gerekçeler `api_headers`'tan söz ediyor
        kod = "\n".join(s for s in metin.splitlines()
                        if not s.lstrip().startswith("#"))
        if "api_headers()" in kod:
            ihlaller.append(ad)

    assert not ihlaller, (
        f"bu sağlayıcılar unityMCP kaydına hâlâ başlık gömüyor: {ihlaller}. "
        "Köprüye geçir ya da istisnayı gerekçesiyle bu testte adlandır."
    )
