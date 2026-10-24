"""Yetkilendirme matrisi — backend'in HTTP yüzeyinin TAMAMI × kimlik ekseni.

NEYİ KORUYOR
    `Backend/app/main.py` içindeki FastAPI uygulamasının her ucu, üç kimlik
    durumunda: kimliksiz · geçersiz kimlik · geçerli kimlik.

HANGİ ARIZADAN DOĞDU
    2026-07-27'de aynı gün iki ciddi arıza yaşandı ve ikisini de 1462 yeşil test
    kaçırdı; ikisi de canlıda elle curl atılarak bulundu:

      1. Gömülü MCP sunucusuna eklenen ASGI middleware, FastMCP tarafından
         yalnız transport'a değil TÜM uygulamaya uygulandı. `/health` 401
         dönmeye başladı; masaüstü uygulamasının canlılık kontrolü ona bağlı
         olduğu için toggle sonsuza kadar sarıda kaldı ve iptal edilemedi.
      2. Eski `/mcp/<sır>` yolunun kaldırıldığı SANILIYORDU; ölçüldüğünde hâlâ
         200 dönüyordu, yani günün tüm güvenlik işi sessizce devre dışıydı.

    Ortak sebep: bütün testler kapının çok DAR olmadığını sınıyordu; hiçbiri çok
    GENİŞ olmadığını sınamıyordu. Fazla-geniş yön, kimsenin test yazmadığı yön —
    ve ürünü kıran yön orası. Bu dosyanın işi tam olarak o boşluk.

MATRİS NEREDEN ÜRETİLİYOR
    Uçlar ELLE YAZILMIŞ bir listeden değil, uygulamanın kendi route tablosundan
    (`app.routes`) geziliyor. Gerekçe: elle liste yarın eklenen bir ucu görmez ve
    test yeşil kalır — yani koruduğunu sandığın şeyi korumaz.

SINIFLANDIRMA KURALI (kural + gerekçe)
    Route tablosundaki her uç iki sınıftan birine düşer:
      • BİLERek AÇIK  → aşağıdaki `DELIBERATELY_OPEN` tablosunda, GEREKÇESİYLE.
      • KORUNMALI     → tablodaki her şey; yani varsayılan sınıf budur.
    Varsayılanın "korunmalı" olması bilinçli: yeni bir uç ekleyen kişi ya onu
    kapının arkasına koyar (test sessizce geçer) ya da açık bırakma KARARINI
    `DELIBERATELY_OPEN`a bir gerekçeyle yazmak zorunda kalır (aksi hâlde test
    kırılır). Ters varsayım — "sınıflandırılmamış uç serbest" — tam olarak
    yukarıdaki 2 numaralı arızanın şeklidir.
    Ayrıca beyaz listede ARTIK VAR OLMAYAN bir uç kalırsa da kırılır: bayat bir
    muafiyet, hiç olmayan bir muafiyetten tehlikelidir.

KAPI GERÇEKTEN ÇAĞRILIYOR MU (bu dosyanın asıl numarası)
    Bu backend'de kimlik doğrulama uç başına ELLE yapılıyor: handler
    `x_session_token: str = Header(alias="X-Session-Token")` parametresini alıp
    gövdesinde `auth_utils._check_token(...)` çağırıyor. Bu desende iki sessiz
    arıza yolu var:
      (a) başlık parametresi bildirilmiş ama `_check_token` HİÇ çağrılmamış
          (2026-07-27'de `/effort-capabilities` tam olarak böyleydi),
      (b) `Header(..., default="")` konmuş, böylece başlığın yokluğu 422 yerine
          boş dize olup gövdeye giriyor.
    (a)'yı yakalamak için `_check_token` testte bir SENTINEL ile sarılıyor:
    gerçek kontrolü çalıştırır, geçerse HTTP 599 fırlatır. Böylece "geçerli
    kimlik" sütununda 599 GÖRMEK, kapının o handler'ın içinde gerçekten
    çağrıldığının kanıtı olur — ve handler'ın gövdesi hiç koşmadığı için test
    yan etkisiz kalır (yoksa /cli-install gerçek bir terminal açardı, /chat
    gerçek bir AI turu başlatırdı).
    (b) için beklenen kod ucun kendi imzasından TÜRETİLİYOR: başlık zorunluysa
    kimliksiz istek 422 (FastAPI doğrulaması, gövde hiç koşmaz), `default=""`
    varsa 401 (gövde koşar, `_check_token("")` reddeder). İkisi de reddir; test
    hangisinin geçerli olduğunu ucun imzasına bakarak bilir, elle listeye değil.

NE SINANMIYOR
    • Kapının handler içindeki SIRASI. 599 sentinel'i "kapı çağrıldı" der,
      "kapıdan önce iş yapılmadı" demez. Kapıdan önce yan etkili iş yapan bir
      handler burada yeşil görünür.
    • Yetki (authorization) — bu yerel uygulamada tek kullanıcı var
      (`auth_utils` her zaman user_id=1 döner), dolayısıyla matris kimlik
      doğrulamayı sınar, sahiplik ayrımını değil.
    • CORS, rate limit, gövde doğrulama sınırları — başka dosyaların işi.
"""

import os
import sys
import tempfile

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

# ── main'i içe aktarmadan ÖNCE yan etkilerini tmp'ye yönlendir ──────────────
# `main` import edilir edilmez iki şey yapıyor: DB dosyasını açıyor ve
# `write_local_app_token(LOCAL_APP_TOKEN)` çağırıyor. İkincisi kritik: test
# ortamında LOCAL_APP_TOKEN boş olduğu için o çağrı kullanıcının GERÇEK
# `~/.unity_architect_ai/local-app-token` dosyasını SİLERDİ — yani testi
# koşturmak, o an açık olan uygulamanın Unity MCP köprüsünü bozardı. Sır
# dosyasının yolunu import'tan önce tmp'ye çekiyoruz; kod yolu aynen koşuyor,
# hedefi değişiyor. (Bu dosya suite'te `main`i import eden ilk dosya.)
_TMP = tempfile.mkdtemp(prefix="authz-matrix-")
os.environ["DB_PATH"] = os.path.join(_TMP, "matrix.db")

# Yaratılan her şeyin silicisi aynı yerde yazılı. Denetimde ölçüldü
# (2026-07-27): temizlik olmadan her koşu geriye 44-88 KiB'lık bir
# `authz-matrix-*` dizini bırakıyordu (canlı sayım 31 → 32) ve içinde DB'ler,
# üretilmiş Fernet anahtarı ve yönlendirilmiş token dosyası duruyordu.
# atexit tercih edildi: bu dizin modül import'unda kuruluyor, yani bir
# fixture'ın ömründen daha uzun yaşıyor.
import atexit  # noqa: E402
import shutil  # noqa: E402

atexit.register(shutil.rmtree, _TMP, True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import local_token_file  # noqa: E402

local_token_file._TOKEN_DIR = _TMP
local_token_file._TOKEN_PATH = os.path.join(_TMP, "local-app-token")

import auth_utils  # noqa: E402
import main as backend_main  # noqa: E402

APP = backend_main.app
TOKEN_HEADER = "X-Session-Token"
VALID_TOKEN = "authz-matrix-token-0123456789"
INVALID_TOKEN = "authz-matrix-WRONG"
# HTTP başlık değerleri tel üzerinde bayttır ve Starlette onları latin-1 ile
# çözer; yani bir soket 0x80-0xFF aralığını başlığa koyabilir. httpx'in kolaylık
# API'si böyle bir değeri göndermeyi reddettiği için testte HAM BAYT veriyoruz —
# aksi hâlde aşağıdaki bulgu hiç üretilemezdi.
NON_ASCII_TOKEN = b"\xfc"

# Kapı geçildiğinde fırlatılan işaret. 599 seçildi çünkü ne FastAPI ne de bu
# uygulamanın herhangi bir handler'ı onu üretiyor — yani "kapı geçti" ile
# "handler bir şey döndürdü" asla karışamaz.
GATE_PASSED = 599


# ── BİLEREK AÇIK BEYAZ LİSTESİ ─────────────────────────────────────────────
# Kısa tutulacak. Her satır bir KARAR ve yanında gerekçesi var; gerekçesiz
# bırakılan gizli bir karar bu projede bulgu sayılıyor.
DELIBERATELY_OPEN: dict = {
    ("GET", "/health"):
        "Masaüstü uygulaması canlılık kontrolünü buna yapıyor. 2026-07-27'de "
        "gömülü MCP sunucusunda tam olarak bu uç yanlışlıkla 401'e döndü ve "
        "ürünün toggle'ı sonsuza kadar sarıda kaldı — kapanırsa ürün kırılır.",

    ("POST", "/login"):
        "Geriye dönük uyumluluk stub'ı: sabit bir gövde döner, DB'ye dokunmaz. "
        "Döndürdüğü \"session_token\": \"local\" gerçek sır DEĞİL — gerçek sır "
        "LOCAL_APP_TOKEN'dır ve bu uçtan elde edilemez.",

    ("POST", "/logout"):
        "Aynı stub ailesi: {\"ok\": true} döner, hiçbir durum değiştirmez.",

    ("GET", "/auth/providers"):
        "Sabit {\"google\": false, \"github\": false}. Yerel modda kimlik "
        "sağlayıcı yok; makineye özel hiçbir bilgi sızdırmıyor.",

    # FastAPI'nin gömülü dokümantasyon yüzeyi. Dördü de yalnız şema servis
    # ediyor; veri okumuyor, yan etki üretmiyor.
    #
    # Burada AÇIK görünmelerinin sebebi bu suite'in token'sız koşuyor olması.
    # 2026-07-27'de karar verildi: dağıtılan üründe (LOCAL_APP_TOKEN varken)
    # üçü de KAPALI, çünkü uç adlarının ve gövde şemalarının tamamını kimliksiz
    # açığa vermek makinedeki her sürece hazır bir saldırı yüzeyi haritası
    # veriyordu. Geliştirirken faydası gerçek olduğu için dev modunda duruyorlar.
    # O kararın kanıtı bu tabloda DEĞİL — ayrı süreçte ölçülüyor, bkz.
    # test_the_api_docs_are_closed_when_a_token_is_configured.
    ("GET", "/openapi.json"):
        "FastAPI şema ucu; yalnız token'sız dev modunda kayıtlı.",
    ("GET", "/docs"): "Swagger UI; /openapi.json ile aynı karar.",
    ("GET", "/docs/oauth2-redirect"): "Swagger UI'ın statik yardımcı sayfası.",
    ("GET", "/redoc"): "ReDoc UI; /openapi.json ile aynı karar.",
}


# ── Route tablosundan matrisin üretilmesi ──────────────────────────────────

def _iter_routes():
    """(method, path, body_model, path_params, token_header_required) üretir.

    Kaynak `app.routes` — elle liste değil. FastAPI'nin kendi Route'ları
    (/docs, /openapi.json ...) APIRoute değil; onlar da tabloya girer, çünkü
    onlar da bu sunucunun cevapladığı uçlar."""
    for route in APP.routes:
        if not isinstance(route, APIRoute):
            yield ("GET", getattr(route, "path", "?"), None, {}, None)
            continue
        header_required = None
        for field in route.dependant.header_params:
            if getattr(field, "alias", None) == TOKEN_HEADER:
                header_required = bool(field.required)
        body_model = route.body_field.type_ if route.body_field is not None else None
        path_params = {f.name: f.type_ for f in route.dependant.path_params}
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield (method, route.path, body_model, path_params, header_required)


ALL_ROUTES = list(_iter_routes())
PROTECTED = [r for r in ALL_ROUTES if (r[0], r[1]) not in DELIBERATELY_OPEN]
OPEN = [r for r in ALL_ROUTES if (r[0], r[1]) in DELIBERATELY_OPEN]


def _minimal_body(model):
    """Gövde modelinin zorunlu alanlarını tipe göre en küçük geçerli değerle doldurur.

    Neden üretiliyor: gövde geçersizse FastAPI handler'ı hiç çağırmadan 422
    döner ve "geçerli kimlik" sütunu kapıyı sınayamaz. Gövde de ucun kendi
    modelinden türetiliyor ki yeni bir uç elle bakım gerektirmesin."""
    if model is None:
        return None
    if not hasattr(model, "model_fields"):
        return {}          # düz `dict` / `Dict[str, str]` gövdeler
    return {
        name: {int: 1, float: 1.0, bool: False, str: "x"}.get(field.annotation, "x")
        for name, field in model.model_fields.items()
        if field.is_required()
    }


def _concrete_path(path: str, path_params: dict) -> str:
    for name, type_ in path_params.items():
        path = path.replace("{%s}" % name, "1" if type_ is int else "x")
    return path


def _ident(route) -> str:
    return f"{route[0]} {route[1]}"


# ── Fixture'lar ────────────────────────────────────────────────────────────

@pytest.fixture
def gate(monkeypatch):
    """Gerçek token kapısını kurar ve SENTINEL ile sarar.

    conftest.py tüm suite için `UNITYAI_ALLOW_NO_TOKEN=1` verip
    `LOCAL_APP_TOKEN`ı siliyor (yani kapıyı kapatıyor). Bu dosya kapının
    KENDİSİNİ sınadığı için tam tersini yapıyor: gerçek bir token kuruluyor ve
    dev muafiyeti kaldırılıyor.

    Sentinel `auth_utils` üzerinde ve `_check_token`ı kendi ad alanına import
    etmiş HER route modülünde ayrı ayrı kurulmalı: `from auth_utils import
    _check_token` ismi import anında bağlıyor, dolayısıyla yalnız auth_utils'i
    yamamak o modülleri ıskalardı. Modüller `sys.modules`ten geziliyor —
    yarın eklenen bir route modülü kendiliğinden kapsanır."""
    monkeypatch.setenv("LOCAL_APP_TOKEN", VALID_TOKEN)
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)

    real_check = auth_utils._check_token

    def sentinel(token):
        real_check(token)                       # geçersizse buradan 401/503 çıkar
        raise HTTPException(GATE_PASSED, "AUTH_GATE_PASSED")

    monkeypatch.setattr(auth_utils, "_check_token", sentinel)
    # Her modül, yalnız `routes.*` DEĞİL. Filtre 31 Ağu 2026'ya kadar
    # `name.startswith("routes.")` idi; `main.py` içine konan korumalı bir uç
    # sentinel'i hiç almıyordu.
    #
    # Sonucu YANLIŞ ALARM, kaçırma değil — ölçüldü: `/health/auth` main.py'deyken
    # geçerli token'la 200 döndü ve test patladı. Yani kapı doğru kurulmuş bir uç
    # "kapısı çağrılmıyor" diye raporlanıyordu. Kaçırma olsaydı çok daha kötüydü,
    # ama yanlış alarm da ucuz değil: bu testin verdiği alarma güvenilmezse
    # gerçek bulgusu da görmezden gelinir.
    #
    # Kimlik karşılaştırması (`is real_check`) isim desenine tercih edildi: bir
    # modülün `_check_token`'ı gerçekten AYNI fonksiyon nesnesi mi, ona bakıyor —
    # aynı adı taşıyan ilgisiz bir sembolü yamamak yeni bir arıza sınıfı olurdu.
    for name, module in list(sys.modules.items()):
        if module is auth_utils or not hasattr(module, "_check_token"):
            continue
        if getattr(module, "_check_token", None) is real_check:
            monkeypatch.setattr(module, "_check_token", sentinel)
    yield


@pytest.fixture
def client():
    # raise_server_exceptions=False: handler'daki bir istisna 500'e dönsün,
    # testi patlatmasın — matris durum KODLARINI sınıyor.
    return TestClient(APP, raise_server_exceptions=False)


def _request(client, route, headers):
    method, path, body_model, path_params, _ = route
    kwargs = {"headers": headers}
    body = _minimal_body(body_model)
    if body is not None:
        kwargs["json"] = body
    return client.request(method, _concrete_path(path, path_params), **kwargs)


# ── 1. Tablo bütünlüğü: sınıflandırılmamış ya da bayat satır kalmasın ──────

def test_every_route_in_the_application_falls_into_exactly_one_class():
    """Route tablosundaki her uç ya korunmalı ya da gerekçeli olarak bilerek açık."""
    assert ALL_ROUTES, "app.routes boş — matris hiçbir şeyi korumuyor demektir"
    for route in ALL_ROUTES:
        key = (route[0], route[1])
        in_open = key in DELIBERATELY_OPEN
        assert (key in DELIBERATELY_OPEN) or (route in PROTECTED)
        if in_open:
            assert DELIBERATELY_OPEN[key].strip(), (
                f"{_ident(route)} beyaz listede ama gerekçesi boş"
            )
    assert len(PROTECTED) + len(OPEN) == len(ALL_ROUTES)


def test_open_whitelist_has_no_entry_for_a_route_that_no_longer_exists():
    """Bayat bir muafiyet, hiç olmayan bir muafiyetten tehlikelidir: kaldırılan
    bir ucun beyaz liste satırı, yarın aynı yolu geri getiren birine sessiz bir
    açık kapı hediye eder."""
    live = {(m, p) for m, p, *_ in ALL_ROUTES}
    stale = sorted(key for key in DELIBERATELY_OPEN if key not in live)
    assert not stale, f"DELIBERATELY_OPEN'da artık var olmayan uçlar: {stale}"


def test_the_open_whitelist_stays_short():
    """Beyaz liste büyüyorsa kapı değil, kapının etrafından dolaşma yolu büyüyor.

    Sınır bir hedef değil, bir alarm: her artış gerekçesiyle birlikte bilinçli
    olarak buraya yazılmalı."""
    assert len(DELIBERATELY_OPEN) <= 12, (
        "Bilerek açık uç sayısı arttı. Yeni satırın gerekçesi gerçekten "
        "'kapatılırsa ürün kırılır' seviyesinde mi?"
    )


# ── 2. KORUNMALI uçlar: kapı çok GENİŞ olmasın ────────────────────────────

@pytest.mark.parametrize("route", PROTECTED, ids=_ident)
def test_protected_endpoint_rejects_a_request_that_carries_no_credential(gate, client, route):
    """Kimliksiz bir istek hiçbir korumalı ucun gövdesine ulaşamaz.

    Beklenen kod ucun KENDİ imzasından türetiliyor:
      • başlık zorunlu  → 422 (FastAPI doğrulaması; gövde hiç koşmaz)
      • `default=""`    → 401 (gövde koşar, `_check_token("")` reddeder)
    İkisi de reddir. Kritik olan negatif: yanıt asla 599 (kapı geçti) ya da
    2xx olmamalı."""
    _, _, _, _, header_required = route
    expected = 422 if header_required else 401
    response = _request(client, route, headers={})
    assert response.status_code == expected, (
        f"{_ident(route)} kimliksiz istekte {response.status_code} döndü "
        f"(beklenen {expected}). Kapı yoksa ekle; uç bilerek açıksa "
        f"DELIBERATELY_OPEN'a GEREKÇESİYLE yaz."
    )


@pytest.mark.parametrize("route", PROTECTED, ids=_ident)
def test_protected_endpoint_rejects_a_request_that_carries_a_wrong_credential(gate, client, route):
    """Yanlış token taşıyan istek her korumalı uçta tam olarak 401 alır.

    Bu sütun kimliksiz sütunundan ayrı duruyor çünkü farklı bir şeyi sınıyor:
    orada başlığın YOKLUĞU reddediliyordu, burada başlık VAR ve içeriği
    karşılaştırılıyor. Karşılaştırmayı hiç yapmayan bir uç yalnız burada
    yakalanır."""
    response = _request(client, route, headers={TOKEN_HEADER: INVALID_TOKEN})
    assert response.status_code == 401, (
        f"{_ident(route)} geçersiz token'la {response.status_code} döndü, 401 bekleniyordu"
    )


@pytest.mark.parametrize("route", PROTECTED, ids=_ident)
def test_protected_endpoint_actually_calls_its_token_gate(gate, client, route):
    """Geçerli token'la her korumalı uç SENTINEL'e çarpar — yani kapı gerçekten
    handler'ın içinde çağrılıyor.

    Bu, kapı çok DAR olmadığının kanıtı (her şeyi reddeden bir kapı da testleri
    geçerdi) ve aynı anda "başlık bildirilmiş ama `_check_token` hiç
    çağrılmamış" sessiz arızasının tek dedektörü. 2026-07-27'de
    `/effort-capabilities` tam olarak o durumdaydı: imzada token vardı,
    gövdesinde kontrol yoktu."""
    response = _request(client, route, headers={TOKEN_HEADER: VALID_TOKEN})
    assert response.status_code == GATE_PASSED, (
        f"{_ident(route)} geçerli token'la {response.status_code} döndü. "
        f"{GATE_PASSED} beklenirdi: kapı ya hiç çağrılmıyor ya da istek "
        f"kapıya varmadan (gövde doğrulaması gibi) reddediliyor."
    )


@pytest.mark.parametrize("route", PROTECTED, ids=_ident)
def test_protected_endpoint_fails_closed_when_no_app_token_is_configured(monkeypatch, client, route):
    """Token HİÇ kurulmamışken korumalı uçlar 503 ile kapanır, açılmaz.

    Eskiden tam tersiydi: token kurulmamışsa kontrol tamamen atlanıyordu, yani
    backend tek başına çalıştırıldığında 127.0.0.1:8000'deki her uç
    kimliksizdi. "Yapılandırılmamış = korumasız" kalıbı; sessiz açık kapı da
    açık kapıdır."""
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)
    monkeypatch.delenv("UNITYAI_ALLOW_NO_TOKEN", raising=False)
    response = _request(client, route, headers={TOKEN_HEADER: VALID_TOKEN})
    assert response.status_code == 503, (
        f"{_ident(route)} token yapılandırılmamışken {response.status_code} döndü, "
        f"503 (fail-closed) bekleniyordu"
    )


# Bu test 2026-07-27'de BULGU olarak doğdu ve xfail(strict) ile işaretlenmişti:
# _check_token, compare_digest'in ASCII dışı girdide TypeError fırlatan str
# aşırı yüklemesini kullanıyordu. Düzeltme aynı gün yapıldı (auth_utils.py,
# bayt karşılaştırması) ve marker kaldırıldı — artık düz bir regresyon testi.
def test_a_non_ascii_credential_is_rejected_rather_than_crashing(gate, client):
    """ASCII dışı bir token 401 almalı; 500 değil.

    Neden önemli: bu bir kimlik atlatma DEĞİL — istek yine reddediliyor. Ama iki
    somut zararı var. (1) Kimliksiz bir çağıran her korumalı uçta yakalanmamış
    istisna tetikleyebiliyor; hata yolu, üzerinde hiç düşünülmemiş bir yol.
    (2) Yapılandırılan LOCAL_APP_TOKEN'ın kendisi ASCII dışı bir karakter
    içerirse karşılaştırmanın İKİ tarafı da patlar ve backend'in tamamı, doğru
    token'la bile, kalıcı 500'e düşer."""
    response = client.get("/me", headers={TOKEN_HEADER: NON_ASCII_TOKEN})
    assert response.status_code == 401, (
        f"ASCII dışı token {response.status_code} üretti (500 = yakalanmamış "
        f"TypeError). hmac.compare_digest'e str değil bytes verilmeli."
    )


def test_the_tokenless_dev_mode_needs_an_explicit_opt_in(monkeypatch, client):
    """Token'sız çalışma yalnız UNITYAI_ALLOW_NO_TOKEN AÇIKÇA verilince açılır.

    Muafiyetin kendisi kayıtlı bir taviz; sınanan şey onun KAZAYLA açılamaz
    olması — rastgele/boş bir değer kapıyı açmamalı."""
    protected = ("GET", "/me", None, {}, False)
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    for value in ("", "0", "false", "maybe"):
        monkeypatch.setenv("UNITYAI_ALLOW_NO_TOKEN", value)
        assert _request(client, protected, headers={}).status_code == 503, (
            f"UNITYAI_ALLOW_NO_TOKEN={value!r} kapıyı açmamalıydı"
        )

    monkeypatch.setenv("UNITYAI_ALLOW_NO_TOKEN", "1")
    assert _request(client, protected, headers={}).status_code == 200


# ── 3. BİLEREK AÇIK uçlar: kapı çok DAR olmasın ───────────────────────────

@pytest.mark.parametrize("route", OPEN, ids=_ident)
def test_deliberately_open_endpoint_still_answers_without_any_credential(gate, client, route):
    """Bilerek açık bırakılan uç kimliksiz istekte hâlâ cevap verir.

    Bu testler bir bulgu değil bir KARARI sabitliyor. Yönü de tersine çeviriyor:
    yukarıdakiler "kapatılması gereken açık mı" diye sorar, bunlar "açık
    kalması gereken kapandı mı" diye. Ürünü kıran 2026-07-27 arızası ikinci
    sorunun cevabıydı ve o soruyu kimse sormamıştı."""
    response = _request(client, route, headers={})
    assert response.status_code not in (401, 403, 599), (
        f"{_ident(route)} kimliksiz istekte {response.status_code} döndü. "
        f"Bu uç bilerek açık: {DELIBERATELY_OPEN[(route[0], route[1])]}"
    )


def test_the_health_probe_the_product_depends_on_answers_200_without_a_token(gate, client):
    """/health kimliksiz olarak 200 ve `status: ok` döner.

    Ayrı ve adıyla duruyor çünkü genel "açık kaldı mı" testinden fazlasını
    sabitliyor: masaüstü toggle'ı yalnız durum koduna değil, gövdedeki alana da
    bakıyor. 401 dönmesi ürünü kırdı; 200 dönüp gövdeyi değiştirmesi de kırar."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_the_api_docs_are_closed_in_a_packaged_build_and_open_from_source():
    """Paketlenmiş (donmuş) ikilide /docs, /redoc, /openapi.json HİÇ kayıtlı
    değil; kaynaktan koşarken üçü de duruyor.

    Neden ayrı bir SÜREÇTE ölçülüyor: bu karar `main` import edilirken, FastAPI
    nesnesi kurulurken veriliyor. Bu dosya `main`i bir kez import ediyor; aynı
    süreçte yeniden yükleyip ortamı değiştirmek DB ve sır dosyası yan etkilerini
    tekrar tetikler ve suite'in geri kalanını kirletir. Alt süreç ikisinden de
    kaçınır ve gerçek PyInstaller işaretini (`sys.frozen`) taklit eder.

    Karar 2026-07-27'de verildi: üç uç da yalnız şema servis ediyor, veri
    vermiyor, yan etkisi yok — ama uç adlarının ve gövde şemalarının tamamını
    kimliksiz açığa vermek dağıtılan üründe gereksiz.

    ⚠️ Bu testin İLK hâli göstergeyi LOCAL_APP_TOKEN varlığına bağlamıştı ve
    denetimde ölçüldü ki gösterge İKİ YÖNDE DE yanlıştı: background.ts token'ı
    dev'de de veriyor (geliştiricinin dökümanı kapanıyordu), donmuş ikili
    sarmalayıcısız çalıştırılınca token olmuyor (paketlenmiş süreç dökümanı
    açıyordu). Token bir istek kimliği, dağıtım biçimi göstergesi değil.

    İKİ YÖNÜ DE sınıyor. Sadece "kapandı mı" diye sormak, üçünü her koşulda
    kapatan bir düzeltmeyi de yeşil geçirirdi; o düzeltme geliştirme akışını
    sessizce bozardı. Bu projede ürünü kıran hata tam olarak fazla-geniş
    kapatmaktı (bkz. modül docstring'i, /health arızası)."""
    import json
    import subprocess

    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    # main import edilince DB açıyor ve sır dosyası yazıyor — ikisini de tmp'ye
    # yönlendir ki koşan uygulamanın gerçek token dosyası SİLİNMESİN.
    # sys.frozen import'tan ÖNCE kuruluyor: main onu modül düzeyinde okuyor.
    program = (
        "import os, sys, json;"
        "sys.path.insert(0, os.environ['APP_DIR']);"
        # pywin32 `sys.frozen`'a BAKIYOR ve set edilmişse `pywintypes` DLL'ini
        # frozen sys.path'te arıyor; simülasyonda orada olmadığı için
        # `ImportError: Module 'pywintypes' isn't in frozen sys.path` ile
        # düşüyordu (ölçüldü 30 Tem 2026, Windows). Bayrağı kurmadan ÖNCE
        # yüklemek yeterli: modül bir kez içeri girdikten sonra `sys.frozen`
        # onu etkilemiyor. Gerçek paketlemede pywin32 zaten pakette olduğu için
        # bu yalnız simülasyonun eksiğiydi, ürünün değil.
        "\ntry:\n import pywintypes\nexcept Exception:\n pass\n"
        "frozen = os.environ.get('FAKE_FROZEN') == '1';"
        "sys.frozen = True if frozen else getattr(sys, 'frozen', False);"
        "import local_token_file;"
        "local_token_file._TOKEN_DIR = os.environ['TMP_DIR'];"
        "local_token_file._TOKEN_PATH = os.path.join(os.environ['TMP_DIR'], 'tok');"
        "import main;"
        "print(json.dumps([getattr(r, 'path', '') for r in main.app.routes]))"
    )

    def routes(frozen: bool, token: str) -> set:
        env = dict(
            os.environ, APP_DIR=app_dir, TMP_DIR=_TMP,
            DB_PATH=os.path.join(_TMP, "docs.db"),
            FAKE_FROZEN="1" if frozen else "0",
        )
        if token:
            env["LOCAL_APP_TOKEN"] = token
        else:
            env.pop("LOCAL_APP_TOKEN", None)
        completed = subprocess.run(
            [sys.executable, "-c", program], env=env,
            capture_output=True, text=True, timeout=120,
        )
        assert completed.returncode == 0, f"alt süreç patladı:\n{completed.stderr}"
        return set(json.loads(completed.stdout.strip().splitlines()[-1]))

    docs_paths = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}

    # DAR YÖN: paketlenmiş ikilide döküman kapalı — token OLSA DA OLMASA DA,
    # çünkü donmuş ikili sarmalayıcısız da çalıştırılabiliyor.
    for token in ("paketlenmis-uygulama-tokeni", ""):
        leaked = docs_paths & routes(frozen=True, token=token)
        assert not leaked, (
            f"Paketlenmiş ikilide (token={'var' if token else 'yok'}) şu döküman "
            f"uçları hâlâ kayıtlı: {sorted(leaked)}. Dağıtılan üründe uç adları "
            f"ve gövde şemaları kimliksiz açığa çıkıyor."
        )

    # GENİŞ YÖN: kaynaktan koşarken döküman açık — Electron dev'de de token
    # verdiği için token varlığı burada hiçbir şeyi değiştirmemeli.
    for token in ("dev-electron-tokeni", ""):
        missing = docs_paths - routes(frozen=False, token=token)
        assert not missing, (
            f"Kaynaktan koşarken (token={'var' if token else 'yok'}) şu döküman "
            f"uçları kapatılmış: {sorted(missing)}. Kapı FAZLA GENİŞ — "
            f"geliştirme akışı sessizce bozulur."
        )

    # /health her dört durumda da duruyor: kapatma yalnız dökümanı hedefliyor.
    assert "/health" in routes(frozen=True, token="x")
    assert "/health" in routes(frozen=False, token="")
