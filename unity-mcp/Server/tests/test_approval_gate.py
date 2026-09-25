"""Onay kapısının kendisi — K1'in boğazı.

⚠️ Bu suite CI'DA KOŞMUYOR (`.github/workflows/test.yml` üç ayak taşıyor:
backend, frontend, PowerShell). Ölçülmüş bir risk, bu dosyaya özgü değil: aynı
şey kütüğü koruyan 6 tripwire için de geçerli. Elle ateşlenmesi gerekiyor:

    PYTHONPATH=unity-mcp/Server/src Backend/venv/Scripts/python.exe -m pytest \
        unity-mcp/Server/tests/test_approval_gate.py

Testlerin hepsi ağ ÇAĞIRMADAN koşuyor: `_onay_iste` taklit ediliyor, çünkü
ölçülmek istenen şey HTTP değil KARARIN kendisi — hangi çağrı kapıya uğruyor,
uğrayan reddedilince ne oluyor.
"""

import asyncio
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from transport import approval_gate  # noqa: E402
from transport.approval_gate import ApprovalDenied, kapiyi_gec  # noqa: E402


def _kos(coro):
    return asyncio.run(coro)


@pytest.fixture
def sorulanlar(monkeypatch):
    """Kapının backend'e sorduğu her çağrıyı kaydeder; hep ONAY döner."""
    kayit = []

    async def sahte(tool_name, params):
        kayit.append((tool_name, dict(params)))
        return {"approved": True}

    monkeypatch.setattr(approval_gate, "_onay_iste", sahte)
    return kayit


@pytest.fixture
def reddet(monkeypatch):
    async def sahte(tool_name, params):
        return {"approved": False, "error": "sınama reddi"}

    monkeypatch.setattr(approval_gate, "_onay_iste", sahte)


# ── kapıya kimin uğradığı ────────────────────────────────────────────────────


@pytest.mark.parametrize("tool,params", [
    ("read_console", {"action": "get"}),
    ("manage_packages", {"action": "list_packages"}),
    ("manage_material", {"action": "ping"}),
])
def test_OKUMA_kapiya_hic_ugramiyor(tool, params, sorulanlar):
    """Keşif araçları her turda çağrılıyor; onlara ağ maliyeti bindirmek kapıyı
    kullanıcının kapatmak isteyeceği bir şeye çevirirdi."""
    _kos(kapiyi_gec(tool, params))
    assert sorulanlar == [], f"{tool} okuma olduğu halde onay istendi"


def test_preflight_REFRESH_yapan_okumalar_da_onay_istiyor(sorulanlar):
    """`manage_scene get_hierarchy` okuma GİBİ görünüyor ama kart çıkarıyor.

    Bu bir kusur değil, kullanıcının 29 Tem kararı: 8 araç koşulsuz
    `preflight(refresh_if_dirty=True)` çağırıyor, yani proje dışarıdan kirliyse
    asset import + script derlemesi + muhtemel domain reload aracın KENDİ
    işinden ÖNCE koşuyor — okuma action'larında bile.

    > *"Read çağrısının doğru çalışması ama bunun kartla gelmesi gerek adım
    >  adım modda böyle bir çözüm lazım"*

    Kabul edilmiş bedel: en sık çağrılan keşif aracı artık her turda kart
    çıkarıyor. Test bunu SABİTLİYOR — biri "okuma neden kart çıkarıyor" deyip
    kütüğü gevşetirse bu kırmızı olur ve kararı hatırlatır.
    """
    _kos(kapiyi_gec("manage_scene", {"action": "get_hierarchy"}))
    assert [t for t, _ in sorulanlar] == ["manage_scene"]


@pytest.mark.parametrize("tool,params", [
    ("manage_gameobject", {"action": "create"}),
    ("execute_code", {"code": "x"}),
    ("read_console", {"action": "clear"}),   # aynı araç, action'a göre YAZMA
])
def test_YAZMA_onaydan_geciyor(tool, params, sorulanlar):
    _kos(kapiyi_gec(tool, params))
    assert [t for t, _ in sorulanlar] == [tool]


def test_BILINMEYEN_arac_yazma_sayiliyor(sorulanlar):
    """Kütük fail-closed: yeni bir araç eklenip kütüğe yazılmazsa kapı ISIRIR.

    Ters kurulmuş olsaydı (bilinmeyen → okuma) kütüğe eklenmeyi unutulan her
    araç sessizce kapısız kalırdı — kapının en olası sessiz açılma yolu bu.
    """
    _kos(kapiyi_gec("uydurma_arac_xyz", {"action": "her ne"}))
    assert [t for t, _ in sorulanlar] == ["uydurma_arac_xyz"]


def test_batch_execute_IC_cagriyi_gorup_onay_istiyor(sorulanlar):
    """Dış ada bakan bir kapı, tüm mutasyonların tek pakette geçmesine izin verir."""
    _kos(kapiyi_gec("batch_execute", {
        "commands": [{"tool": "manage_gameobject", "params": {"action": "create"}}]
    }))
    assert [t for t, _ in sorulanlar] == ["batch_execute"]


# ── reddin sonucu ────────────────────────────────────────────────────────────


def test_RED_cagriyi_durduruyor(reddet):
    """Kapının tek işi bu: reddedilen çağrı Unity'ye ULAŞMAMALI.

    `kapiyi_gec` `call_next`'ten ÖNCE çağrıldığı için fırlatan bir istisna
    çağrıyı gerçekten iptal ediyor; sessizce `False` dönmek onu geçirirdi.
    """
    with pytest.raises(ApprovalDenied) as e:
        _kos(kapiyi_gec("manage_gameobject", {"action": "delete"}))
    assert "manage_gameobject" in str(e.value)
    assert "sınama reddi" in str(e.value)


def test_backend_ULASILAMAZSA_reddediliyor(monkeypatch):
    """Fail-CLOSED: ürün kapalıyken mutasyon geçmez, okuma çalışmaya devam eder.

    Kullanıcı kararı (29 Tem): reddedilen alternatif *"ürün yoksa kapıyı hiç
    kurma"* idi — o, güvenlik sınırını "uygulama açık mı" sorusuna bağlar ve
    kapı "ürünü kapat" denerek atlatılır.
    """
    def hep_patla(*a, **k):
        # `async def` DEĞİL: `async with httpx.AsyncClient(...)` çağrının
        # DÖNÜŞÜNÜ bağlam yöneticisi olarak kullanıyor, coroutine'i değil.
        # İlk yazımı async'ti ve testi yanlış sebeple geçirdi.
        raise OSError("bağlantı yok")

    monkeypatch.setattr(approval_gate.httpx, "AsyncClient", hep_patla)
    monkeypatch.setattr(approval_gate.asyncio, "sleep", _hemen)
    # Bütçe duvar saatine bağlı olduğu için uykuyu taklit etmek testi
    # hızlandırmıyor, SICAK döngüye çeviriyor — bütçenin kendisi kısaltılıyor.
    monkeypatch.setattr(approval_gate, "_POST_BUTCESI", 0.2)

    with pytest.raises(ApprovalDenied):
        _kos(kapiyi_gec("manage_asset", {"action": "create"}))

    # Aynı koşullarda OKUMA hâlâ çalışıyor — kapı ürünü kilitlemiyor.
    _kos(kapiyi_gec("read_console", {"action": "get"}))


async def _hemen(_saniye):
    return None


# ── karta HANGİ projenin değişeceği yazılıyor ───────────────────────────────


def test_karta_HEDEF_proje_yaziliyor(monkeypatch):
    """Toplu denetim bulgusu (med): kart hangi Unity projesinin değişeceğini
    göstermiyordu.

    `_inject_unity_instance` yönlendirme argümanını mesajdan `pop` ediyor ve
    kapı ondan SONRA koşuyor; yani kapının gördüğü parametrelerde hedef YOK.
    Birden fazla Editor bağlıyken kullanıcı "A projesinde" sanıp onaylıyor,
    komut B'de koşuyordu.

    ⚠️ Bu, kapı kodunda YAZILI bir iddiayı da yalanlamıştı: "kapı enjeksiyondan
    sonra koşuyor, böylece gösterilen parametreler Unity'ye gidecek olanlarla
    aynı". `pop` edilen argüman tam olarak eksik olandı.
    """
    gorulen = []

    async def yakala(tool, params):
        gorulen.append(dict(params))
        return {"approved": True}

    monkeypatch.setattr(approval_gate, "_onay_iste", yakala)
    _kos(kapiyi_gec("manage_gameobject", {"action": "delete"}, hedef="ProjeB"))
    assert gorulen[0]["unity_instance"] == "ProjeB"
    assert gorulen[0]["action"] == "delete"


def test_hedef_YOKSA_kart_yine_de_cikiyor(monkeypatch):
    """Hedef okunamazsa kart hedefsiz çıkar ama ÇIKAR — kartı hiç çıkarmamak
    kullanıcıyı 180 sn'lik sessiz bir redde kilitlerdi."""
    gorulen = []

    async def yakala(tool, params):
        gorulen.append(dict(params))
        return {"approved": True}

    monkeypatch.setattr(approval_gate, "_onay_iste", yakala)
    _kos(kapiyi_gec("manage_gameobject", {"action": "delete"}, hedef=None))
    assert "unity_instance" not in gorulen[0]


def test_onay_ancak_GERCEK_true_ile_veriliyor(monkeypatch):
    """Bozuk bir onay yanıtı kabul sayılmamalı.

    Ölçüldü: `bool("false")`, `bool("no")`, `bool([0])` hepsi `True`. Kapının
    sözleşmesi "bozuk yanıt = RED" diyor; doğruluk (truthiness) o sözleşmeyi
    sessizce tersine çeviriyordu.
    """
    for bozuk in ["false", "no", [0], 1, "true"]:
        async def sahte(tool, params, _b=bozuk):
            return {"approved": _b}

        monkeypatch.setattr(approval_gate, "_onay_iste", sahte)
        with pytest.raises(ApprovalDenied):
            _kos(kapiyi_gec("manage_asset", {"action": "delete"}))


# ── ADIM 4: /api/command'ın köken işareti ───────────────────────────────────


class _SahteIstek:
    def __init__(self, basliklar):
        self.headers = basliklar


def test_bakim_isareti_ancak_TAM_eslesince_muaf(monkeypatch):
    """`/api/command` kapıya uğruyor; yalnız ürünün kendi bakımı muaf.

    Rotanın kendi paylaşılan sırrı bu ayrımı YAPAMIYOR — o sır `unity-mcp`
    CLI'ında da var. `LOCAL_APP_TOKEN` ürünün backend oturum sırrı, yani
    "bu çağrı üründen geldi" sorusuna cevap veren tek işaret.
    """
    monkeypatch.setenv("LOCAL_APP_TOKEN", "gizli-deger")
    assert approval_gate.urun_bakim_cagrisi_mi(
        _SahteIstek({"X-UnityAI-Maintenance": "gizli-deger"})) is True
    assert approval_gate.urun_bakim_cagrisi_mi(
        _SahteIstek({"X-UnityAI-Maintenance": "yanlis"})) is False
    assert approval_gate.urun_bakim_cagrisi_mi(_SahteIstek({})) is False


def test_bakim_isareti_BOS_token_ile_kimseyi_muaf_yapmiyor(monkeypatch):
    """Fail-CLOSED. Boş dize eşleşmesi, başlığı boş gönderen HERKESİ muaf yapardı
    — yani kapı token'ı olmayan bir kurulumda tamamen açılırdı."""
    monkeypatch.setenv("LOCAL_APP_TOKEN", "")
    assert approval_gate.urun_bakim_cagrisi_mi(
        _SahteIstek({"X-UnityAI-Maintenance": ""})) is False
    assert approval_gate.urun_bakim_cagrisi_mi(
        _SahteIstek({"X-UnityAI-Maintenance": "her ne"})) is False


def test_bakim_isareti_baslik_okunamazsa_muaf_DEGIL(monkeypatch):
    """Ölçemediğimiz durum kabule değil kapıya dönüşmeli."""
    monkeypatch.setenv("LOCAL_APP_TOKEN", "gizli-deger")

    class _Patlayan:
        @property
        def headers(self):
            raise RuntimeError("başlık okunamıyor")

    assert approval_gate.urun_bakim_cagrisi_mi(_Patlayan()) is False


# ── bütçe: sayıya değil DUVAR SAATİNE bağlı ─────────────────────────────────


class _Yanit:
    def __init__(self, kod, govde):
        self.status_code = kod
        self._govde = govde

    def json(self):
        return self._govde


class _CevapsizClient:
    """POST'u kabul eden ama sonucu asla döndürmeyen bir backend taklidi."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return _Yanit(200, {"status": "ok"})

    async def get(self, *a, **k):
        raise OSError("backend yanıt vermiyor")


def test_yoklama_butcesi_SAYIYA_degil_saate_bagli(monkeypatch):
    """Denetim turu bulgusu: "180 sn" yazan döngü 33 dakika sürebiliyordu.

    Sebep çarpımsaldı — 360 tur × (0,5 sn uyku + 5 sn HTTP zaman aşımı). Bütçe
    deneme SAYISIYLA ifade edilince, adım başına gecikme değiştiğinde sessizce
    büyüyor. O sürede çağrıyı bekleyen asistan turu da kilitli kalıyor.

    Test bütçeyi küçültüp gerçek geçen süreyi ölçüyor: sayı tabanlı eski
    döngüde bu koşu en az 180 saniye sürerdi.
    """
    import time as _t

    monkeypatch.setattr(approval_gate, "_POST_BUTCESI", 5.0)
    monkeypatch.setattr(approval_gate, "_BEKLEME_BUTCESI", 0.2)
    monkeypatch.setattr(approval_gate.httpx, "AsyncClient", _CevapsizClient)

    basla = _t.monotonic()
    with pytest.raises(ApprovalDenied):
        _kos(kapiyi_gec("manage_asset", {"action": "create"}))
    gecen = _t.monotonic() - basla

    assert gecen < 5.0, f"bütçe aşıldı: {gecen:.1f} sn"


# ── Card wait vs agy's fixed 180 s MCP timeout (audit 25 Sep 2026) ───────────

AGY_MCP_TIMEOUT_S = 180.0  # "timed out after 3m0s"; agy offers no setting for it


def test_the_gate_answers_before_agy_gives_up():
    """An approval near the end of the card wait must still be dispatched,
    run and answered inside agy's deadline, or agy reports a timeout for a
    call Unity ran and the model's retry writes twice. 15 s is kept for Unity."""
    worst_case = approval_gate._POST_BUTCESI + approval_gate._BEKLEME_BUTCESI
    assert worst_case + 15.0 <= AGY_MCP_TIMEOUT_S, worst_case


class _PendingBackend:
    """Accepts the card, keeps it pending, records every request."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **k):
        _PendingBackend.calls.append(("POST", url, json))
        return _Yanit(200, {"status": "ok"})

    async def get(self, url, **k):
        _PendingBackend.calls.append(("GET", url, None))
        return _Yanit(200, {"status": "pending"})


def _withdrawals():
    return [(url, body) for method, url, body in _PendingBackend.calls
            if method == "POST" and "/mcp-approval-respond/" in url]


@pytest.fixture
def pending_backend(monkeypatch):
    _PendingBackend.calls = []
    monkeypatch.setattr(approval_gate.httpx, "AsyncClient", _PendingBackend)
    monkeypatch.setattr(approval_gate, "_BEKLEME_ADIMI", 0.02)
    return _PendingBackend


def test_the_gates_own_timeout_withdraws_the_card(pending_backend, monkeypatch):
    """Before: the card stayed on screen until the backend's sweep, and an
    approval clicked there reported success while nothing ran."""
    monkeypatch.setattr(approval_gate, "_BEKLEME_BUTCESI", 0.1)
    with pytest.raises(ApprovalDenied):
        _kos(kapiyi_gec("manage_asset", {"action": "create"}))
    opened = [body["gate_id"] for method, url, body in pending_backend.calls
              if url.endswith("/mcp-approval-request")]
    assert _withdrawals() == [
        (f"{approval_gate._BACKEND_URL}/mcp-approval-respond/{opened[0]}", {"approved": False})]


def test_a_client_cancel_stops_the_wait_and_withdraws_the_card(pending_backend):
    """What the MCP layer does when agy sends notifications/cancelled (or a
    2026-07-28 client closes its stream): it cancels the handler. The wait must
    end there, nothing may run, and the card must leave the screen."""

    async def scenario():
        task = asyncio.ensure_future(kapiyi_gec("manage_asset", {"action": "create"}))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        polls_at_cancel = sum(1 for c in pending_backend.calls if c[0] == "GET")
        await asyncio.gather(*approval_gate._pending_withdrawals)
        await asyncio.sleep(0.1)
        return polls_at_cancel

    polls_at_cancel = _kos(scenario())
    assert polls_at_cancel > 0, "the wait never started; the test proves nothing"
    assert sum(1 for c in pending_backend.calls if c[0] == "GET") == polls_at_cancel
    assert len(_withdrawals()) == 1 and _withdrawals()[0][1] == {"approved": False}


# ── kablolama: kapı VAR olmak yetmez, BAĞLI olmalı ──────────────────────────


class _SahteMesaj:
    def __init__(self, ad, args):
        self.name = ad
        self.arguments = args


class _SahteBaglam:
    def __init__(self, ad, args):
        self.message = _SahteMesaj(ad, args)


def _middleware(monkeypatch):
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    mw = UnityInstanceMiddleware()

    async def enjeksiyon_yok(_ctx):
        return None

    monkeypatch.setattr(mw, "_inject_unity_instance", enjeksiyon_yok)
    return mw


def test_middleware_kapiyi_GERCEKTEN_cagiriyor(monkeypatch):
    """`on_call_tool`'dan kapı satırı silinirse bu test kırmızı olur.

    Neden ayrı yazıldı: yukarıdaki testlerin hepsi `kapiyi_gec`'i DOĞRUDAN
    çağırıyor, yani kapının mantığını ölçüyor, bağlı olup olmadığını değil.
    Bu depoda tam o ayrım daha önce ölçüldü — kütük doğruydu ama kapı
    kablolamasına uygulanan mutasyon 7 testi birden kırmıştı; kablolamasız
    bir kütük hiçbir şey korumaz.
    """
    mw = _middleware(monkeypatch)

    async def reddet(_tool, _params):
        return {"approved": False, "error": "kablolama sınaması"}

    monkeypatch.setattr(approval_gate, "_onay_iste", reddet)

    ulasildi = []

    async def call_next(_ctx):
        ulasildi.append(True)
        return "UNITY'YE ULAŞTI"

    # The module's own ToolError: in a full run tests/integration/conftest.py
    # has stubbed fastmcp for the session, so the real class is not the one
    # raised. It must be a ToolError so the model gets a tool result with the
    # reason (test_live_approval_denial.py measures that over the wire).
    from transport import unity_instance_middleware

    with pytest.raises(unity_instance_middleware.ToolError) as raised:
        _kos(mw.on_call_tool(
            _SahteBaglam("manage_gameobject", {"action": "create"}), call_next))
    assert isinstance(raised.value.__cause__, ApprovalDenied)
    assert "kablolama sınaması" in str(raised.value)
    assert ulasildi == [], "kapı reddetti ama çağrı yine de Unity'ye gitti"


def test_middleware_ONAYLANINCA_cagriyi_gecirıyor(monkeypatch):
    """Ters yön — yoksa 'her şeyi reddet' diyen bir kapı da yukarıdakini geçerdi."""
    mw = _middleware(monkeypatch)

    async def onayla(_tool, _params):
        return {"approved": True}

    monkeypatch.setattr(approval_gate, "_onay_iste", onayla)

    async def call_next(_ctx):
        return "UNITY'YE ULAŞTI"

    sonuc = _kos(mw.on_call_tool(
        _SahteBaglam("manage_gameobject", {"action": "create"}), call_next))
    assert sonuc == "UNITY'YE ULAŞTI"


def test_token_YOKSA_da_kapi_calisiyor_ve_reddi_secer(monkeypatch):
    """Sır ortamda yoksa çağrı kimliksiz gider ve backend'de reddedilir.

    Sırrın ikinci bir okuyucusu BİLEREK yazılmadı: `local_token_file`'daki
    bağ/junction/TOCTOU korumalarının sertleştirilmemiş bir kopyası olurdu.
    Eksik token'ın kabule değil REDDE dönüşmesi o kararın bedeli ve burada
    sabitleniyor.
    """
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)
    assert approval_gate._headers() == {}
