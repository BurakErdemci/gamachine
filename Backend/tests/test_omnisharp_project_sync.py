"""Unity proje dosyalarının (.sln/.csproj) tazelenme kararını koruyan testler.

Hangi arızadan doğdu (2026-07-27): C# hover/completion/definition sessizce BOŞ
dönüyordu. OmniSharp sorunsuz açılıyor ve `state: ready` diyordu — ama Unity'nin
ürettiği csproj'lar 4 gün önce, projede yalnız 2 şablon dosyası varken üretilmişti
ve o günden beri yenilenmemişti. Unity legacy MSBuild formatı ürettiği için (glob
YOK) dosya listesi tam olarak yazılı olan kadar: 33 kaynak dosyanın 28'i OmniSharp'ın
evrenine hiç girmemişti.

Eski tetikleme koşulu "workspace'te hiç .sln yok" idi — kapı çok DARDI, çünkü var
ama bayat bir .sln arızayı kalıcı hale getiriyordu.

Kapı burada iki yönden de sınanıyor. Çok dar olmamalı (bayatlık yakalanmalı) AMA
çok geniş de olmamalı: Unity'nin `Library/` klasörü sürekli değişir, ona bakan bir
kontrol her açılışta boşuna Unity'ye istek atardı.
"""
import json
import os

import pytest

from app.omnisharp import omnisharp_manager as om

_OLD, _NEW = 1_000_000, 2_000_000       # sabit mtime'lar — duvar saatine bağlı değil


def _touch(path: str, mtime: int, body: str = "") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def unity_ws(tmp_path):
    """Minimal ama gerçekçi bir Unity workspace'i: kökte .sln/.csproj, Assets altında
    kaynak. Zaman damgaları testte açıkça kurulur."""
    ws = str(tmp_path / "Proje")
    os.makedirs(os.path.join(ws, "Assets"), exist_ok=True)
    return ws


class TestBayatlikYakalanmali:
    def test_a_workspace_with_no_project_files_at_all_needs_a_sync(self, unity_ws):
        """Eski davranışın koruduğu tek durum — korunmaya devam ediyor."""
        _touch(os.path.join(unity_ws, "Assets", "A.cs"), _NEW)
        assert om._csproj_sync_reason(unity_ws) == "proje dosyaları hiç üretilmemiş"

    def test_a_source_file_newer_than_the_project_files_needs_a_sync(self, unity_ws):
        """Sahadaki arızanın birebir kendisi: .sln VAR ama kaynaklar ondan yeni.
        Bu test kırılırsa hover yine sessizce boş dönmeye başlar."""
        _touch(os.path.join(unity_ws, "P.sln"), _OLD)
        _touch(os.path.join(unity_ws, "Assembly-CSharp.csproj"), _OLD)
        _touch(os.path.join(unity_ws, "Assets", "Yeni.cs"), _NEW)
        assert om._csproj_sync_reason(unity_ws) == "kaynak dosyalar proje dosyalarından yeni"

    def test_an_asmdef_with_no_matching_project_needs_a_sync_even_when_nothing_is_newer(
        self, unity_ws
    ):
        """asmdef başına ayrı bir csproj üretilir. asmdef csproj'lardan ESKİ olsa
        bile karşılığı yoksa o assembly'nin tüm dosyaları evrenin dışında kalır —
        sahada iki asmdef tam olarak böyle kayboldu. Yalnız mtime'a bakan bir
        kontrol bunu kaçırırdı."""
        _touch(os.path.join(unity_ws, "P.sln"), _NEW)
        _touch(os.path.join(unity_ws, "Assembly-CSharp.csproj"), _NEW)
        _touch(os.path.join(unity_ws, "Assets", "Scripts", "Oyun.asmdef"), _OLD,
               json.dumps({"name": "MatchOfficial.Runtime"}))
        assert om._csproj_sync_reason(unity_ws) == "MatchOfficial.Runtime için proje dosyası yok"


class TestGereksizSyncTetiklenmemeli:
    def test_up_to_date_project_files_do_not_trigger_a_sync(self, unity_ws):
        """Karşıt yön: her açılışta Unity'ye istek atmak hem yavaş hem gereksiz."""
        _touch(os.path.join(unity_ws, "Assets", "A.cs"), _OLD)
        _touch(os.path.join(unity_ws, "P.sln"), _NEW)
        _touch(os.path.join(unity_ws, "Assembly-CSharp.csproj"), _NEW)
        assert om._csproj_sync_reason(unity_ws) is None

    def test_an_asmdef_that_already_has_its_project_does_not_trigger_a_sync(self, unity_ws):
        _touch(os.path.join(unity_ws, "P.sln"), _NEW)
        _touch(os.path.join(unity_ws, "MatchOfficial.Runtime.csproj"), _NEW)
        _touch(os.path.join(unity_ws, "Assets", "Scripts", "Oyun.asmdef"), _OLD,
               json.dumps({"name": "MatchOfficial.Runtime"}))
        assert om._csproj_sync_reason(unity_ws) is None

    def test_churn_inside_unitys_library_folder_is_ignored(self, unity_ws):
        """Unity `Library/` içine sürekli yazar. Orayı sayan bir kontrol her
        açılışta sync tetikler — kapının çok GENİŞ olduğu yön bu, ve bu projede
        test edilmeyen yön hep bu yön oldu."""
        _touch(os.path.join(unity_ws, "Assets", "A.cs"), _OLD)
        _touch(os.path.join(unity_ws, "P.sln"), _NEW)
        _touch(os.path.join(unity_ws, "Assets", "Library", "Onbellek.cs"), _NEW)
        _touch(os.path.join(unity_ws, "Assets", "obj", "Uretilmis.cs"), _NEW)
        assert om._csproj_sync_reason(unity_ws) is None

    def test_a_workspace_that_cannot_be_listed_does_not_claim_a_sync_is_needed(self, tmp_path):
        """Var olmayan yol için "tazele" demek, Unity'ye anlamsız istek atmak olur."""
        assert om._csproj_sync_reason(str(tmp_path / "yok")) is None


class TestKullaniciyaGosterilenIpucu:
    def test_when_the_projects_cannot_be_refreshed_the_user_learns_what_still_works(
        self, unity_ws, monkeypatch
    ):
        """Tazeleme başarısızsa en azından SEBEP görünmeli: eskiden durum `ready`
        görünüp hover sessizce boş dönüyordu.

        Mesaj neyin ÇALIŞTIĞINI söylüyor, çünkü kısmi zeka gerçekten var: dosya
        içi semboller misc-files yolundan çözülüyor, yalnız Unity tipleri düşüyor."""
        _touch(os.path.join(unity_ws, "P.sln"), _OLD)
        _touch(os.path.join(unity_ws, "Assets", "Yeni.cs"), _NEW)

        def _boom(*_a, **_k):
            raise OSError("Unity'ye ulaşılamadı")

        monkeypatch.setattr(om.urllib.request, "urlopen", _boom)
        mgr = om.OmniSharpManager()
        hint = mgr._maybe_sync_csproj(unity_ws)
        assert hint is not None
        assert "Dosya içi tamamlama" in hint

    def test_the_hint_does_not_promise_that_opening_unity_fixes_it(
        self, unity_ws, monkeypatch
    ):
        """⚠️ Bu test bir DOĞRULUK kapısı, üslup değil. Ölçüldü 2026-07-27: Unity
        AÇIKKEN de proje dosyaları tazelenmedi, çünkü .csproj üretimini Unity'nin
        harici IDE entegrasyonu yapıyor ve makinede kayıtlı bir IDE yoktu — bu,
        ürünün hedef kullanıcısının normal hali. Kullanıcıya doğrulanmamış bir çare
        söylemek arızayı onun üstüne yıkmak olur."""
        _touch(os.path.join(unity_ws, "P.sln"), _OLD)
        _touch(os.path.join(unity_ws, "Assets", "Yeni.cs"), _NEW)

        def _boom(*_a, **_k):
            raise OSError("Unity'ye ulaşılamadı")

        monkeypatch.setattr(om.urllib.request, "urlopen", _boom)
        hint = om.OmniSharpManager()._maybe_sync_csproj(unity_ws)
        assert "Unity'yi bir kez aç" not in hint

    def test_a_fresh_workspace_produces_no_hint(self, unity_ws):
        _touch(os.path.join(unity_ws, "Assets", "A.cs"), _OLD)
        _touch(os.path.join(unity_ws, "P.sln"), _NEW)
        mgr = om.OmniSharpManager()
        assert mgr._maybe_sync_csproj(unity_ws) is None


class _FakeHttpResponse:
    """urllib yanıtının taklidi — context manager + read()."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _stale_ws(ws: str) -> None:
    """Tazelenmeye MUHTAÇ bir workspace: .sln var ama kaynak ondan yeni."""
    _touch(os.path.join(ws, "P.sln"), _OLD)
    _touch(os.path.join(ws, "Assets", "Yeni.cs"), _NEW)


def _urlopen_returning(payload: bytes, *, refreshes: str | None = None):
    """Sahte urlopen. `refreshes` verilirse Unity'nin İŞİ GERÇEKTEN YAPMASINI
    taklit eder: proje dosyalarını kaynaklardan yeni hale getirir. Bu ayrım testin
    kendisi — yanıtın "başarılı" demesi ile dosyaların yazılmış olması iki ayrı
    olgu ve kod artık ikincisine bakıyor."""
    def _fake(*_a, **_k):
        if refreshes is not None:
            _touch(os.path.join(refreshes, "P.sln"), _NEW + 1)
        return _FakeHttpResponse(payload)
    return _fake


class TestUnityYanitiOkunuyor:
    """Eskiden yalnız "istek gitti mi" bakılıyordu. Unity tarafı hiçbir dosya
    yazmadan `success` dönebiliyordu ve bu fark edilmiyordu — arızanın günlerce
    görünmez kalmasının doğrudan sebebi buydu (ölçüldü 2026-07-27: kayıtlı harici
    IDE yokken Unity'nin sync yolu gövdesi boş bir metoda iniyor)."""

    def test_a_failure_reported_by_unity_is_surfaced_to_the_user(
        self, unity_ws, monkeypatch
    ):
        _stale_ws(unity_ws)
        monkeypatch.setattr(
            om.urllib.request, "urlopen",
            _urlopen_returning(b'{"success": false, "error": "paket kurulu degil"}'))
        hint = om.OmniSharpManager()._maybe_sync_csproj(unity_ws)
        assert hint is not None

    def test_a_successful_regeneration_produces_no_hint(self, unity_ws, monkeypatch):
        """Karşıt yön: gerçekten tazelendiyse kullanıcıyı uyarma.

        ⚠️ Gövde SARMALANMIŞ biçimde — Unity MCP `/api/command` gerçekte böyle
        döndürüyor (canlı ölçüldü 2026-07-27). Sarmalamayı açmayan bir kod başarılı
        yanıtı başarısız sayıyordu ve bu yalnız Unity bağlıyken ortaya çıkıyordu,
        yani hiçbir birim testi yakalayamazdı."""
        _stale_ws(unity_ws)
        monkeypatch.setattr(
            om.urllib.request, "urlopen",
            _urlopen_returning(
                b'{"status": "success", "result": {"success": true, '
                b'"message": "ok", "data": {"csproj_count": 4}}}',
                refreshes=unity_ws))
        assert om.OmniSharpManager()._maybe_sync_csproj(unity_ws) is None

    def test_a_failure_wrapped_in_the_transport_envelope_is_still_seen(
        self, unity_ws, monkeypatch
    ):
        """Sarmalamanın içindeki başarısızlık da görülmeli — dış seviyede
        `status: success` yazması taşımanın çalıştığını söyler, işin yapıldığını
        değil."""
        _stale_ws(unity_ws)
        monkeypatch.setattr(
            om.urllib.request, "urlopen",
            _urlopen_returning(
                b'{"status": "success", "result": {"success": false, '
                b'"error": "paket kurulu degil"}}'))
        assert om.OmniSharpManager()._maybe_sync_csproj(unity_ws) is not None


class TestSessizNoOpBasariSayilmiyor:
    """Bir işlemin başarısı kendi RAPORUNDAN değil, dışarıda bıraktığı İZDEN
    doğrulanır. `success: true` Unity'nin isteği aldığını söyler; proje
    dosyalarının yazıldığını söylemez.

    Dış denetim bulgusu (2026-07-27): `csproj_count` tam olarak bu hali yakalamak
    için eklenmiş, ama yalnızca LOGLANIYORDU — yani kod, sessiz no-op'u başarı
    sayıyordu. Bu sınıf (sessiz no-op) bu projede zaten bir arızanın günlerce
    fark edilmemesine sebep oldu."""

    def test_a_success_that_wrote_zero_project_files_is_treated_as_a_failure(
        self, unity_ws, monkeypatch
    ):
        """`csproj_count: 0` = Unity yolu gövdesi boş bir metoda indi. Yanıt
        "başarılı" olsa da kullanıcı uyarılmalı."""
        _stale_ws(unity_ws)
        monkeypatch.setattr(
            om.urllib.request, "urlopen",
            _urlopen_returning(
                b'{"status": "success", "result": {"success": true, '
                b'"message": "ok", "data": {"csproj_count": 0}}}'))
        hint = om.OmniSharpManager()._maybe_sync_csproj(unity_ws)
        assert hint is not None
        assert "Dosya içi tamamlama" in hint

    def test_a_success_that_leaves_the_files_stale_is_treated_as_a_failure(
        self, unity_ws, monkeypatch
    ):
        """Asıl kapı bu: sayaç İNANDIRICI bir sayı dönse bile (4 dosya) diskte
        hiçbir şey değişmediyse iş yapılmamıştır. Sonuçtan doğrulama, yanıt
        şemasının her yalanına karşı çalışan tek kontrol."""
        _stale_ws(unity_ws)
        monkeypatch.setattr(
            om.urllib.request, "urlopen",
            _urlopen_returning(
                b'{"status": "success", "result": {"success": true, '
                b'"message": "ok", "data": {"csproj_count": 4}}}'))
        assert om.OmniSharpManager()._maybe_sync_csproj(unity_ws) is not None

    def test_zero_written_files_is_a_failure_even_when_the_timestamps_look_fresh(
        self, unity_ws, monkeypatch
    ):
        """`csproj_count` kapısının KENDİ başına gerektiği yer — sonuç doğrulaması
        buraya kör.

        Sonuç doğrulaması mtime'a bakıyor; Unity ise .sln'i tek başına yeniden
        yazıp hiç .csproj üretmeyebiliyor (sync yolu gövdesi boş bir metoda
        indiğinde görülen hal). O anda damgalar TAZE görünür ama OmniSharp'ın
        evreni yine boştur. İki kapı bu yüzden ayrı ayrı duruyor: biri diskteki
        ize, diğeri Unity'nin saydığı dosyaya bakıyor.

        ⚠️ Mutasyonla doğrulandı (2026-07-28): yalnız bu kapı kaldırıldığında
        diğer testlerin hepsi YEŞİL kalıyordu — yani kapıyı ısıran tek test bu."""
        _stale_ws(unity_ws)
        monkeypatch.setattr(
            om.urllib.request, "urlopen",
            _urlopen_returning(
                b'{"status": "success", "result": {"success": true, '
                b'"message": "ok", "data": {"csproj_count": 0}}}',
                refreshes=unity_ws))
        assert om.OmniSharpManager()._maybe_sync_csproj(unity_ws) is not None

    def test_a_response_without_the_counter_is_judged_purely_by_the_result(
        self, unity_ws, monkeypatch
    ):
        """Karşıt yön — kapı çok DAR olmamalı. `csproj_count` alanını göndermeyen
        bir Unity paketi (eski sürüm) yalnızca alan eksik diye başarısız
        sayılmamalı: dosyalar gerçekten tazelendiyse uyarı çıkmaz."""
        _stale_ws(unity_ws)
        monkeypatch.setattr(
            om.urllib.request, "urlopen",
            _urlopen_returning(
                b'{"status": "success", "result": {"success": true, "message": "ok"}}',
                refreshes=unity_ws))
        assert om.OmniSharpManager()._maybe_sync_csproj(unity_ws) is None

    def test_an_unparsable_counter_does_not_by_itself_condemn_a_real_refresh(
        self, unity_ws, monkeypatch
    ):
        """Karşıt yönün ikinci yarısı: bilinmeyeni 0 saymak, gerçekten yapılmış
        bir işi başarısız ilan ederdi. Beklenmedik biçimdeki sayaç yok sayılır,
        karar diskteki ize bırakılır."""
        _stale_ws(unity_ws)
        monkeypatch.setattr(
            om.urllib.request, "urlopen",
            _urlopen_returning(
                b'{"status": "success", "result": {"success": true, '
                b'"data": {"csproj_count": "bilinmiyor"}}}',
                refreshes=unity_ws))
        assert om.OmniSharpManager()._maybe_sync_csproj(unity_ws) is None


class TestUnitySirriImportu:
    """The app loads this module as top-level `omnisharp.omnisharp_manager`
    (main.py, lsp_routes.py). A relative `..unity_ai_mcp` import raised
    "attempted relative import beyond top-level package" there, the broad except
    turned it into the stale-projects hint, and the refresh never ran (since
    e988258, measured 28 Sep 2026, Windows). The other tests in this file load
    the module as `app.omnisharp...`, where the relative form happened to work."""

    def test_the_refresh_request_is_sent_under_the_module_name_the_app_uses(
        self, unity_ws, monkeypatch
    ):
        import importlib
        live = importlib.import_module("omnisharp.omnisharp_manager")
        mcp = importlib.import_module("unity_ai_mcp.unity_mcp_manager")
        # The secret must come from the SAME singleton the app starts the server
        # with, not from a second copy of the module.
        monkeypatch.setattr(mcp.unity_mcp_manager, "local_api_token", "sir-123")
        _stale_ws(unity_ws)
        sent = []

        def _fake(req, *_a, **_k):
            sent.append(req)
            _touch(os.path.join(unity_ws, "P.sln"), _NEW + 1)
            return _FakeHttpResponse(
                b'{"status": "success", "result": {"success": true, '
                b'"data": {"csproj_count": 2}}}')

        monkeypatch.setattr(live.urllib.request, "urlopen", _fake)
        assert live.OmniSharpManager()._maybe_sync_csproj(unity_ws) is None
        assert len(sent) == 1, "the import failed before the request was built"
        assert sent[0].get_header("X-api-key") == "sir-123"
