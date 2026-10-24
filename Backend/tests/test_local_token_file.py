"""Backend bearer token'ının dosya üzerinden aktarımı — 2026-07-27 C grubu.

Bulgu: token `workspace/.mcp.json` içine düz metin yazılıyordu (modelin kendi
okuyabildiği yer) ve `codex mcp add --env LOCAL_APP_TOKEN=...` argv'sinde
geçiyordu (`ps` ile aynı-kullanıcı her sürece görünür). Aktarım artık 0600
izinli tek bir dosyadan geçiyor.

Testler hem aktarımın ÇALIŞTIĞINI hem de sırrın SIZMADIĞINI sabitliyor; ikincisi
olmadan sızıntıyı geri getiren bir değişiklik testlerden geçerdi.
"""

import json
import os
import stat

import pytest

import local_token_file
import safe_paths  # noqa: E402  (yol kimliği artık burada)


@pytest.fixture
def isolated_token(tmp_path, monkeypatch):
    """Gerçek ~/.unity_architect_ai kaydına dokunmadan sınamak için."""
    target = tmp_path / "local-app-token"
    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(target))
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)
    return target


@pytest.mark.izin_bitleri_gerekli


def test_token_file_is_owner_only(isolated_token):
    """0600 olmazsa aynı makinedeki başka kullanıcılar sırrı okur."""
    assert local_token_file.write_local_app_token("s3cret")
    mode = stat.S_IMODE(os.stat(isolated_token).st_mode)
    assert mode == 0o600, f"beklenen 0600, bulunan {oct(mode)}"


def test_written_token_is_readable_back(isolated_token):
    """Bu yön olmadan düzeltme sırrı gizler ama MCP sunucusunu kilitler."""
    local_token_file.write_local_app_token("s3cret")
    assert local_token_file.read_local_app_token() == "s3cret"


def test_environment_wins_over_file(isolated_token, monkeypatch):
    """Backend kendi sürecinde ortamdaki değeri kullanmalı — dosya bayat olabilir."""
    local_token_file.write_local_app_token("eski")
    monkeypatch.setenv("LOCAL_APP_TOKEN", "yeni")
    assert local_token_file.read_local_app_token() == "yeni"


def test_empty_token_removes_the_file(isolated_token):
    """Token'sız (dev) başlatmada önceki oturumun GEÇERLİ sırrı diskte kalmamalı."""
    local_token_file.write_local_app_token("s3cret")
    assert isolated_token.exists()
    local_token_file.write_local_app_token("")
    assert not isolated_token.exists()
    assert local_token_file.read_local_app_token() == ""


def test_missing_file_reads_as_empty_not_error(isolated_token):
    assert local_token_file.read_local_app_token() == ""


def test_mcp_config_written_to_workspace_carries_no_token(tmp_path, monkeypatch):
    """Asıl bulgu: `.mcp.json` modelin okuyabildiği yerde ve token oradaydı.

    Sağlayıcıyı import etmeden, üretim fonksiyonunun yazdığı dosyayı sınıyoruz;
    dosyanın HERHANGİ bir yerinde token görünürse test kırılır.
    """
    monkeypatch.setenv("LOCAL_APP_TOKEN", "KANARYA-TOKEN")
    from providers.cli_base import BaseCLIProvider

    workspace = tmp_path / "ws"
    workspace.mkdir()

    provider = BaseCLIProvider.__new__(BaseCLIProvider)
    monkeypatch.setattr(provider, "_launcher_path", lambda name: "/bin/true", raising=False)
    monkeypatch.setattr(provider, "_ensure_exec", lambda path: None, raising=False)
    monkeypatch.setattr(provider, "_register_mcp", lambda *a, **k: None, raising=False)

    config_path = provider._write_mcp_config(str(workspace))
    raw = open(config_path, encoding="utf-8").read()

    assert "KANARYA-TOKEN" not in raw, ".mcp.json bearer token içeriyor"
    # Yapı bozulmasın: dosya hâlâ geçerli ve unityai sunucusunu tanımlıyor olmalı.
    assert json.loads(raw)["mcpServers"]["unityai"]


# ── Bağ yönlendirmesi sınıfı (30 Tem 2026 denetimi) ──────────────────────────
#
# Sınıfın kökü: `os.path.islink()` ve `O_NOFOLLOW` *"bu yol sandığım dosya mı"*
# sorusunu CEVAPLAMIYOR. Ölçüldü (ayrıcalıksız, IsUserAnAdmin=0):
#
#     os.symlink → RED (1314) · os.link → OK · mklink /J → OK
#     os.path.islink(junction) → False
#
# Yani sırrı yönlendirmenin ayrıcalıksız iki yolu vardı ve mevcut kontrol
# ikisini de görmüyordu. Bu testler `junction_gerekli` / `sabit_bag_gerekli`
# kapılarına bağlı ve bu makinede GERÇEKTEN koşuyorlar — sembolik bağ kapısına
# bağlansalardı sessizce atlanırlardı ki koruma ölü demek olurdu.


def _junction_kur(bag, hedef):
    import subprocess

    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(bag), str(hedef)],
        capture_output=True, text=True, timeout=20,
    )
    assert r.returncode == 0, r.stderr or r.stdout


@pytest.mark.junction_gerekli
def test_junction_ana_dizin_sahte_tokeni_KABUL_ETMIYOR(tmp_path, monkeypatch):
    """Saldırganın yerleştirdiği token ürüne kabul ettiriliyordu."""
    saldirgan = tmp_path / "saldirgan"
    saldirgan.mkdir()
    (saldirgan / "local-app-token").write_text("SALDIRGAN-TOKENI", encoding="utf-8")
    token_dir = tmp_path / "token-home"
    _junction_kur(token_dir, saldirgan)

    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(token_dir))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(token_dir / "local-app-token"))
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    assert local_token_file.read_local_app_token() == ""


@pytest.mark.junction_gerekli
def test_junction_ana_dizin_sirri_DISARI_YAZMIYOR(tmp_path, monkeypatch):
    """Yazma yolu saldırganın seçtiği dosyaya sırrı taşıyordu."""
    saldirgan = tmp_path / "saldirgan"
    saldirgan.mkdir()
    hedef = saldirgan / "local-app-token"
    hedef.write_text("ONCEKI-ICERIK", encoding="utf-8")
    token_dir = tmp_path / "token-home"
    _junction_kur(token_dir, saldirgan)

    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(token_dir))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(token_dir / "local-app-token"))

    assert local_token_file.write_local_app_token("URUN-SIRRI") is False
    # O_TRUNC açılıştan çıkarıldığı için hedef dosya KESİLMİYOR da.
    assert hedef.read_text(encoding="utf-8") == "ONCEKI-ICERIK"


@pytest.mark.sabit_bag_gerekli
def test_sabit_bag_ile_ikinci_ad_REDDEDILIYOR(tmp_path, monkeypatch):
    """Sabit bağ bir bağ değil, ikinci bir ADdır — `islink` onu görmüyor.

    Sır dosyasının ikinci bir adı varsa, sır o addan da okunabilir. `O_NOFOLLOW`
    bunu reddetmiyor çünkü takip edilecek bir bağ yok.
    """
    token = tmp_path / "local-app-token"
    token.write_text("SIR", encoding="utf-8")
    ikinci_ad = tmp_path / "saldirganin-adi"
    os.link(token, ikinci_ad)
    assert not os.path.islink(ikinci_ad), "ön koşul: islink sabit bağı görmemeli"

    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(token))
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    assert local_token_file.read_local_app_token() == ""
    assert local_token_file.write_local_app_token("YENI-SIR") is False


@pytest.mark.junction_gerekli
def test_yonlendirme_YOKKEN_normal_calisiyor(tmp_path, monkeypatch):
    """TERS YÖN — bu olmadan yukarıdakiler "her şeyi reddet" ile de geçerdi.

    Kimlik doğrulaması yanlış kurulursa (ör. `\\\\?\\` öneki normalize edilmezse)
    ürün token'ı hiç yazamaz ve backend açılmaz. O arıza bu testle görünür.
    """
    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(tmp_path / "local-app-token"))
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    assert local_token_file.write_local_app_token("GERCEK-SIR") is True
    assert local_token_file.read_local_app_token() == "GERCEK-SIR"


@pytest.mark.junction_gerekli
def test_ARA_dizin_junctioni_da_yakalaniyor(tmp_path, monkeypatch):
    """İki-varyant: yönlendirme token dizininin KENDİSİNDE değil, ÜSTÜNDE.

    Bulgu S1a token dizininin kendisini junction yapıyordu. Aynı sınıfın başka
    bir yazımı, yolun herhangi bir üst bileşenini yönlendirmek. Tam yol
    karşılaştırması bunu da kapsıyor — ama kapsadığı ÖLÇÜLMEDEN iddia edilemez.
    """
    saldirgan = tmp_path / "saldirgan"
    (saldirgan / "uai").mkdir(parents=True)
    (saldirgan / "uai" / "local-app-token").write_text("SALDIRGAN", encoding="utf-8")
    ara = tmp_path / "ara"
    _junction_kur(ara, saldirgan)

    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(ara / "uai"))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(ara / "uai" / "local-app-token"))
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    assert local_token_file.read_local_app_token() == ""
    assert local_token_file.write_local_app_token("SIR") is False


@pytest.mark.junction_gerekli
@pytest.mark.sabit_bag_gerekli
def test_junction_ve_sabit_bag_BIRLIKTE_kullanilinca(tmp_path, monkeypatch):
    """İki-varyant: iki mekanizmanın birleşimi.

    Tek tek kapatılan iki yazım birlikte kullanıldığında kontrollerden birinin
    diğerini gölgelemediğini ölçüyor — sınıf kapatmalarında sık görülen boşluk.
    """
    gercek = tmp_path / "gercek"
    gercek.mkdir()
    token = gercek / "local-app-token"
    token.write_text("SIR", encoding="utf-8")
    os.link(token, gercek / "ikinci-ad")
    gorunen = tmp_path / "gorunen"
    _junction_kur(gorunen, gercek)

    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(gorunen))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(gorunen / "local-app-token"))
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    assert local_token_file.read_local_app_token() == ""


@pytest.mark.baglar_gerekli
def test_POSIX_ara_dizin_sembolik_bagi_reddediliyor(tmp_path, monkeypatch):
    """POSIX dalının karşılığı — bu makinede ATLANIYOR, bilerek burada.

    Windows'ta tanıtıcının gerçek yolu `GetFinalPathNameByHandleW` ile
    sorulabiliyor; POSIX'te taşınabilir bir karşılığı yok ve kod `realpath`
    karşılaştırmasına düşüyor. O dal bu makinede ÖLÇÜLEMİYOR (sembolik bağ
    kurmak winerror 1314 veriyor), ama dalı yakalayacak test yazılı: CI Linux'ta
    koştuğunda gerçek bir iddia taşıyor.
    """
    saldirgan = tmp_path / "saldirgan"
    saldirgan.mkdir()
    (saldirgan / "local-app-token").write_text("SALDIRGAN", encoding="utf-8")
    token_dir = tmp_path / "token-home"
    os.symlink(str(saldirgan), str(token_dir), target_is_directory=True)

    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(token_dir))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(token_dir / "local-app-token"))
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    assert local_token_file.read_local_app_token() == ""


# ── guard (a): harf-duyarsız karşılaştırmanın varsayımı çöktüğünde ───────────
#
# 2. doğrulama turu bulgusu (30 Tem 2026): `_yol_esitle` her Windows yolunu
# `lower()` yapıyor, yani `Token` ile `token` EŞİT sayılıyor. Varsayım
# *"kullanıcı token dizinini case-sensitive yapmadı"* — operatör davranışı, kod
# zorlamıyor, o yüzden `demoted` değil `guard`.
#
# Varsayımın çökebildiği ÖLÇÜLDÜ (31 Tem 2026, bu makine, AYRICALIKSIZ):
# `fsutil file setCaseSensitiveInfo <dizin> enable` → rc=0, ardından `token` ile
# `Token` yan yana durdu ve `st_ino`'ları farklı çıktı.
#
# Aşağıdaki testler `fsutil`e BAĞLI DEĞİL: karşılaştırılan iki dosya gerçekten
# ayrı iki dosya (adları farklı olabilir — `gercek` dizesi zaten taklit) ve
# kimlik ölçümü gerçek `fstat`/`stat` ile yapılıyor. Taklit edilen tek şey iki
# adlandırılmış dikiş: hangi platform kipinde olduğumuz ve tanıtıcının çekirdeğe
# göre yolu. Böylece guard POSIX CI'da da ölçülüyor.


@pytest.fixture
def harf_duyarsiz_kip(monkeypatch):
    """Windows'un harf-duyarsız karşılaştırma kipini her makinede kurar."""
    monkeypatch.setattr(safe_paths, "_harf_duyarsiz_kip", lambda: True)


@pytest.fixture
def case_sensitive_dizin(tmp_path):
    """A directory that can hold two spellings of one name side by side.

    Linux CI volumes are case-sensitive, so these tests run there. On Windows
    `fsutil setCaseSensitiveInfo ... enable` turns it on (measured 31 Jul 2026:
    rc=0 without admin rights). macOS APFS is case-insensitive by default, so
    the volume is probed and the test is skipped rather than counted as passed.
    """
    if os.name == "nt":
        import subprocess

        r = subprocess.run(
            ["fsutil", "file", "setCaseSensitiveInfo", str(tmp_path), "enable"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.skip(
                "case-sensitive dizin kurulamadı — ölçüldü: "
                f"fsutil rc={r.returncode} {(r.stdout + r.stderr).strip()[:100]}"
            )
    (tmp_path / "case-sensitivity-probe").write_text("probe", encoding="utf-8")
    if (tmp_path / "CASE-SENSITIVITY-PROBE").exists():
        pytest.skip("Case-insensitive volume: distinct entries differing only in case are required")
    return tmp_path


def test_harf_farki_iki_AYRI_giris_ise_gercek_sayiliyor(case_sensitive_dizin):
    """Guard'ın çekirdeği, GERÇEK dosya sistemi durumuyla.

    ⚠️ Bu testin eski hâli KURGUYDU ve 3. doğrulama turu onu yakaladı: tanıtıcı
    bir dosyaya bağlıyken `_fd_gercek_yol` taklidi ilgisiz BAŞKA bir dosyanın
    yolunu döndürüyordu, yani `os.open` / `GetFinalPathNameByHandleW` / `os.stat`
    arasındaki gerçek ilişki hiç ölçülmüyordu. Şimdi iki yazım da diskte
    gerçekten duruyor ve karar onların varlığından çıkıyor.
    """
    (case_sensitive_dizin / "token").write_text("mesru", encoding="utf-8")
    (case_sensitive_dizin / "TOKEN").write_text("saldirgan", encoding="utf-8")
    assert {"token", "TOKEN"} <= set(os.listdir(case_sensitive_dizin))

    assert safe_paths._harf_farki_GERCEK_mi(
        str(case_sensitive_dizin / "TOKEN"), str(case_sensitive_dizin / "token")
    ) is True


def test_harf_farki_tek_giris_ise_ayni_adin_yazimi_sayiliyor(tmp_path):
    """Ters yön — yoksa guard ürünü Windows'ta açılmaz hale getirirdi.

    Harf-duyarsız bir dizinde çekirdek dosyanın diskteki yazımını döndürüyor,
    çağıran başka türlü yazmış olabiliyor; ikisi AYNI dosya. Guard bunu
    reddederse token hiç okunamaz — kapattığı açıktan pahalıya mal olur.
    """
    (tmp_path / "token").write_text("mesru", encoding="utf-8")
    assert "TOKEN" not in os.listdir(tmp_path)

    assert safe_paths._harf_farki_GERCEK_mi(
        str(tmp_path / "TOKEN"), str(tmp_path / "token")
    ) is False


def test_bilesen_ayristirmasi_kok_bicimlerini_dogru_cozuyor():
    """Ayrıştırma elle `split(os.sep)` DEĞİL — o, kökü yanlış yere koyuyordu.

    Ölçüldü (31 Tem 2026): elle bölünce sürücüye yakın bir bileşenin ebeveyni
    `"C:"` çıkıyor ve `os.listdir("C:")` kökü değil o sürücünün çalışma dizinini
    listeliyor. Yani kontrol YANLIŞ dizine bakıp "iki giriş yok" diyebilirdi.
    """
    if os.name == "nt":
        ust, ad = safe_paths._bilesenler(r"C:\a\b")[0]
        assert ust == "C:\\" and ad == "a"
        assert safe_paths._bilesenler(r"\\server\share\d\t")[0][0] == (
            "\\\\server\\share\\"
        )
    else:
        assert safe_paths._bilesenler("/a/b")[0] == ("/", "a")


def test_iki_bilesen_ayrisiyorsa_GERCEK_olani_yakalaniyor(case_sensitive_dizin):
    """Sınıfın ikinci yazımı: fark tek bileşende değil, birden fazlasında.

    Döngü ilk masum bileşende durursa (yalnız bir yazım var) sonrakini hiç
    ölçmez ve gerçek olanı kaçırırdı. İki-varyant kuralı ölçümle uygulanıyor.
    """
    (case_sensitive_dizin / "ara").mkdir()
    (case_sensitive_dizin / "ARA").mkdir()
    (case_sensitive_dizin / "ara" / "token").write_text("mesru", encoding="utf-8")
    (case_sensitive_dizin / "ARA" / "token").write_text("saldirgan", encoding="utf-8")

    assert safe_paths._harf_farki_GERCEK_mi(
        str(case_sensitive_dizin / "ARA" / "token"),
        str(case_sensitive_dizin / "ara" / "token"),
    ) is True


def test_harf_farki_OLCULEMEZSE_gercek_sayiliyor():
    """Listeleyemediğimiz bir dizin hakkında iddia üretmiyoruz — fail-CLOSED."""
    yok = os.path.join(os.sep, "olmayan-dizin-xyz", "alt", "token")
    assert safe_paths._harf_farki_GERCEK_mi(
        yok.replace("token", "TOKEN"), yok
    ) is True


@pytest.mark.junction_gerekli
def test_case_only_junction_sirri_SIZDIRMIYOR(case_sensitive_dizin, monkeypatch):
    """3. doğrulama turunun blocker'ı, uçtan uca — ürün ucundan ölçülüyor.

    Ölçüldü (31 Tem 2026): düzeltmeden ÖNCE `read_secret_file` saldırganın
    token'ını DÖNDÜRÜYORDU. Kimlik karşılaştırması bu senaryoyu göremiyordu,
    çünkü `os.stat(beklenen)` de junction'ı takip edip aynı dosyaya çıkıyordu.

    Junction bir Windows kavramı olduğu için bu uçtan uca ölçüm yalnız orada
    koşuyor; sınıfın MANTIĞI ise yukarıdaki üç testte her makinede ölçülüyor.
    """
    import _winapi

    buyuk = case_sensitive_dizin / "TOKEN-HOME"
    buyuk.mkdir()
    (buyuk / "local-app-token").write_text("SALDIRGANIN-TOKENI", encoding="utf-8")
    kucuk = case_sensitive_dizin / "token-home"
    _winapi.CreateJunction(str(buyuk), str(kucuk))

    hedef = kucuk / "local-app-token"
    monkeypatch.setattr(local_token_file, "_TOKEN_DIR", str(kucuk))
    monkeypatch.setattr(local_token_file, "_TOKEN_PATH", str(hedef))
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    assert local_token_file.read_secret_file(str(hedef)) == ""
    assert local_token_file.read_local_app_token() == ""


def test_harf_DUYARLI_kipte_yol_karsilastirmasi_zaten_yakaliyor(
    tmp_path, monkeypatch
):
    """POSIX kipinde guard'a hiç gerek yok — reddi ilk kontrol veriyor.

    Bu iddia yazılı, çünkü guard'ın POSIX'te sessiz kalması bir eksiklik değil:
    orada `_yol_esitle` harfi korumakla zaten farklı iki yol görüyor.
    """
    monkeypatch.setattr(safe_paths, "_harf_duyarsiz_kip", lambda: False)
    mesru = tmp_path / "mesru.txt"
    mesru.write_text("MESRU", encoding="utf-8")

    monkeypatch.setattr(
        safe_paths, "_fd_gercek_yol", lambda fd: str(mesru).upper()
    )
    fd = os.open(str(mesru), os.O_RDONLY)
    try:
        with pytest.raises(OSError, match="yönlendirilmiş"):
            local_token_file._dogrula_kimlik(fd, str(mesru))
    finally:
        os.close(fd)
