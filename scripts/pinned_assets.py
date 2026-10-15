"""Build sırasında indirilen üçüncü parti ikililerin sabitlenmiş sürüm + özet kütüğü.

Neden var: bu depo 13 ayrı yerden yürütülebilir içerik indiriyordu ve hiçbirinde
checksum, imza ya da boyut kontrolü yoktu (ölçüldü 2026-07-28, grep ile: `sha256|
sha512|checksum|gpg|--verify|signature|hashlib` tüm fetch betiklerinde ve
workflow'larda SIFIR sonuç). İndirilenlerin hepsi installer'ın içine giriyor ve
son kullanıcıda çalıştırılıyor, yani upstream'de ya da aradaki bir hostta bozulan
tek bir asset her kullanıcıya ulaşıyordu.

Neden TEK dosya ve neden URL'ler de burada: özet bir yerde, URL başka yerde
dursaydı "birbiriyle uyuşması gereken iki yer" olurdu — bu depoda 2026-07-27/28'de
bulunan arızaların HEPSİ tam olarak o şekildeydi (`_intact` ↔ `_resolve_binary`,
üretilip tüketilmeyen `csproj_count`, `didOpen` ama `didChange` yok). Bir asset'in
adresi ve beklenen özeti aynı satırda duruyor; birini değiştirip diğerini unutmak
mümkün değil.

Neden özet indirme anında yayıncıdan ÇEKİLMİYOR: aynı hosttan özet çekmek yalnız
BOZULMAYA karşı korur. Yeniden yayınlanmış ya da ele geçirilmiş bir asset'te
saldırgan ikisini birden değiştirir. Yayıncının yayınladığı özet bize yalnız ilk
sabitlemeyi elle hesaplamaktan kurtarır; doğrulama mekanizması kaynağa gömülü
değerdir.

Bu modül hem kütüphane hem CLI: `fetch_omnisharp.py` doğrudan import ediyor,
kabuk betikleri (bash + PowerShell) `python3 scripts/pinned_assets.py …` ile
çağırıyor. Tek uygulama olması bilinçli — lookup mantığını üç dilde ayrı ayrı
yazmak, kapatmaya çalıştığımız hata sınıfının ta kendisini üretirdi.

ÇIKIŞ KODU SÖZLEŞMESİ (kabuk çağıranları yalnız bu sayıyı görüyor):
  0 = tamam
  1 = BÜTÜNLÜK arızası: indirilen baytlar pinle uyuşmuyor, ya da tazeleme
      eksik kaldı. ASLA tekrar denenmemeli.
  2 = kullanım / ORTAM hatası (yanlış argüman, desteklenmeyen Python sürümü).
  3 = İŞLETİM arızası: kütükte anahtar yok, dosya okunamadı, kütük ayrıştırılamadı.
      Tekrar denenebilir ve düzeltilebilir; bütünlük arızasıyla ilgisi yok.
1 ile 3'ün ayrı olması şart: denetimde ölçüldü (2026-07-28), ikisi de 1 dönerken
okunamayan bir dosya operatöre "baytlar pinle uyuşmuyor" diye raporlanıyordu —
projenin "uyuşmazlık asla tekrar denenmez" doktrini yüzünden en pahalı yanlış
yönlendirme buydu.
"""
# ─────────────────────────────────────────────────────────────────────────────
# Python sürüm kapısı — HER ŞEYDEN ÖNCE.
#
# Hangi arızadan doğdu: bu modül PEP 604 gösterimi (`dict | None`) kullanıyor ve o
# gösterim def anında değerlendiriliyor, yani Python < 3.10'da modül IMPORT anında
# TypeError atıyor. Kabuk betikleri (`fetch_video_bins.sh/.ps1`) yalnız bir python3
# VAR MI diye bakıp sıfır olmayan her çıkışı "bu anahtar kütükte yok" sayıyordu; stok
# macOS Python 3.9.6'da build sessizce ffmpeg/yt-dlp'siz paketleniyor ve sebebi
# YANLIŞ raporlanıyordu (ölçüldü, varsayım değil).
#
# Bu blok 3.9'da da koşabilmek zorunda: PEP 604 yok, annotation yok, düz kod.
# ─────────────────────────────────────────────────────────────────────────────
import sys

_MIN_PYTHON = (3, 10)


def _python_surum_kapisi(bulunan, stream):
    """Sürüm yetersizse mesajı yazıp SystemExit(2) atar; yeterliyse sessizce döner.

    Fonksiyona ayrılmasının sebebi sınanabilirlik: burada 3.9 koşturulamıyor, ama
    kapının KARARI ve mesajı doğrudan çağrılarak sınanabiliyor.
    """
    if tuple(bulunan) >= _MIN_PYTHON:
        return
    gereken = ".".join(str(x) for x in _MIN_PYTHON)
    var_olan = ".".join(str(x) for x in bulunan)
    stream.write(
        "[pinned] ORTAM HATASI: scripts/pinned_assets.py en az Python " + gereken
        + " gerektiriyor, bulunan: Python " + var_olan + ".\n"
        "         Bu bir bütünlük arızası DEĞİL (çıkış kodu 2, 1 değil): hiçbir\n"
        "         asset doğrulanmadı, hiçbir özet uyuşmazlığı bulunmadı.\n"
        "         Çözüm: Python " + gereken + "+ kurup betiği onunla çağırın\n"
        "         (ör. python3.13 scripts/pinned_assets.py …) ya da PATH'teki\n"
        "         python3'ü güncelleyin. Stok macOS python3 (3.9.6) YETMİYOR.\n"
    )
    raise SystemExit(2)


_python_surum_kapisi(sys.version_info[:3], sys.stderr)

import calendar  # noqa: E402  (sürüm kapısı bilerek bütün importlardan önce)
import datetime  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import tempfile  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pinned_assets.json")

# Yalnız bu ikisi kabul ediliyor. MD5 ve SHA-1 çakışmaya karşı kırık; johnvansickle
# yalnız MD5 yayınlıyor (2026-07-28), o yüzden oradaki özet elle SHA-256 hesaplandı.
_ALGOS = {"sha256": hashlib.sha256, "sha512": hashlib.sha512}


class IntegrityError(RuntimeError):
    """İndirilen baytlar sabitlenmiş özetle uyuşmuyor.

    Ayrı bir tip: çağıranların bunu ağ hatasından ayırt etmesi gerekiyor. Ağ hatası
    tekrar denenebilir; özet uyuşmazlığı ASLA tekrar denenmemeli, çünkü "birkaç kez
    dene" bir saldırgana yalnızca birkaç şans daha verir.
    """


def load_manifest() -> dict:
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def asset(key: str) -> dict:
    """Tek bir asset kaydı. Bilinmeyen anahtar sessizce boş dönmüyor: bir kabuk
    betiği anahtarı yanlış yazarsa doğrulama atlanmış olurdu ve build yeşil
    kalırdı — bu, düzeltmeye çalıştığımız 'sessiz no-op' sınıfının kendisi."""
    entries = load_manifest()["assets"]
    if key not in entries:
        raise KeyError(
            f"pinned_assets.json içinde '{key}' yok. Tanımlı anahtarlar: "
            + ", ".join(sorted(entries))
        )
    return entries[key]


def digest_of(data: bytes, algo: str) -> str:
    if algo not in _ALGOS:
        raise ValueError(f"desteklenmeyen özet algoritması: {algo}")
    return _ALGOS[algo](data).hexdigest()


def verify_bytes(data: bytes, key: str) -> None:
    """Sabitlenmiş boyut ve özetle karşılaştırır; uyuşmazlıkta IntegrityError.

    Boyut önce bakılıyor çünkü yarım inen bir dosyada asıl bilgi orada: "48 MB
    bekleniyordu, 12 KB geldi" mesajı, iki hex dizisini yan yana koymaktan çok daha
    hızlı teşhis ettiriyor. Özet kontrolünün yerini TUTMUYOR, ondan önce geliyor.
    """
    entry = asset(key)
    algo, _, expected = entry["digest"].partition(":")
    # Beklenen değerin BİÇİMİ de doğrulanıyor. Sebebi: bu fonksiyon yalnız
    # "gelen == beklenen" karşılaştırması yapsaydı, beklenen tarafın bozulması
    # (yer tutucu, boş dize, kırpılmış hex, yanlış algoritma adı) sessizce
    # kabul edilebilir bir kapıya dönüşürdü. Denetimde ölçüldü (2026-07-28):
    # kütükteki özeti yer tutucuya çeviren bir mutasyon 72 testin hepsini sağ
    # geçti — kapının kendisi değil yalnız kütüğün metni sınanıyordu.
    beklenen_uzunluk = {"sha256": 64, "sha512": 128}.get(algo)
    if beklenen_uzunluk is None or len(expected) != beklenen_uzunluk or not all(
        c in "0123456789abcdef" for c in expected
    ):
        raise IntegrityError(
            f"{key}: kütükteki özet kullanılabilir değil ({entry['digest']!r}). "
            "Beklenen biçim 'sha256:<64 hex>' ya da 'sha512:<128 hex>'. "
            "Bozuk ya da doldurulmamış bir pin, doğrulamayı sessizce etkisiz "
            "kılacağı için burada durduruluyor."
        )
    expected_size = entry.get("size")
    if expected_size is not None and len(data) != expected_size:
        raise IntegrityError(
            f"{key}: boyut uyuşmadı — beklenen {expected_size} bayt, gelen {len(data)}. "
            f"Kaynak: {entry['url']}"
        )
    actual = digest_of(data, algo)
    if actual != expected:
        raise IntegrityError(
            f"{key}: {algo} özeti uyuşmadı.\n"
            f"  beklenen: {expected}\n"
            f"  gelen   : {actual}\n"
            f"  kaynak  : {entry['url']}\n"
            "İndirme bozulmuş ya da yayıncıdaki asset değişmiş olabilir. Sürümü "
            "kasten yükselttiysen `python3 scripts/pinned_assets.py refresh` ile "
            "kütüğü tazele ve diff'i İNCELE — özeti körlemesine güncellemek bu "
            "kontrolü tamamen anlamsız kılar."
        )


def verify_file(path: str, key: str) -> None:
    with open(path, "rb") as f:
        verify_bytes(f.read(), key)


# ─────────────────────────────────────────────────────────────────────────────
# refresh / check
#
# Neden buradalar: bu modülün docstring'i, JSON'un `$aciklama` alanı ve iki kabuk
# betiği (fetch_uv.sh/.ps1) uzun süre `pinned_assets.py refresh` komutunu VAAT
# ETTİ ama komut yoktu. Vaat edilip var olmayan çare, bu depodaki arızaların
# ortak şekli ("uyuşması gereken iki yer") ile aynı sınıfa giriyor: okuyan kişi
# bir kapının var olduğunu sanıp elle, körlemesine düzenleme yapıyor.
#
# refresh KURU KOŞU varsayılanıyla çalışıyor. Gerekçe: bu kütük güvenlik
# kapısının kendisi; kütüğü sessizce üzerine yazan bir araç, kapıyı kapatmak
# için yazılmış olmasına rağmen açardı. Yazmak açık niyet ister (`--write`).
# ─────────────────────────────────────────────────────────────────────────────

_UA = "gamachine-pinned-assets (+scripts/pinned_assets.py)"
_HTTP_TIMEOUT = 60


class RefreshError(RuntimeError):
    """Tazeleme sırasında bir asset çözümlenemedi.

    IntegrityError'dan ayrı tip: orada doğrulanmış bir uyuşmazlık var ve asla
    tekrar denenmemeli. Burada ise henüz doğrulanan bir şey yok — yalnızca
    yayıncıdan bilgi alınamadı, yani tekrar denenebilir bir durum.
    """


def _http_bytes(url: str, headers: dict | None = None) -> bytes:
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=_HTTP_TIMEOUT) as r:
        return r.read()


def _http_json(url: str, headers: dict | None = None):
    return json.loads(_http_bytes(url, headers).decode("utf-8"))


def _http_status(url: str) -> int:
    """`check` için: gövdeyi indirmeden adresin ayakta olup olmadığına bak.

    HEAD kullanılıyor çünkü buradaki asset'lerin toplamı 1.5 GB'ın üzerinde;
    erken uyarının ucuz olması, düzenli koşturulmasının ön koşulu.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return getattr(r, "status", None) or r.getcode() or 200
    except urllib.error.HTTPError as e:
        return e.code


def _basename(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def _version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _hex_for_name(sums: bytes, name: str) -> str:
    """`<hex>  <ad>` satırlarından ada karşılık geleni çıkarır.

    Ad eşleşmesi ŞART: bir checksum dosyasında onlarca satır olabiliyor ve
    yanlış satırı almak, doğrulama kapısını açık bırakmanın sessiz yolu olurdu.
    """
    for line in sums.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            if re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                return parts[0].lower()
    raise RefreshError(f"checksum listesinde '{name}' satırı yok")


def _first_hex64(data: bytes) -> str:
    m = re.search(r"\b([0-9a-fA-F]{64})\b", data.decode("utf-8", "replace"))
    if not m:
        raise RefreshError("sidecar dosyasında 64 haneli sha256 bulunamadı")
    return m.group(1).lower()


def _bugun() -> str:
    return datetime.date.today().isoformat()


# ---- GitHub release'leri: omnisharp / uv / yt-dlp / ffmpeg-win için tek çözümleyici ----

def _github_headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    # Kimliksiz GitHub API saatte 60 istek veriyor. Bu kütükte GitHub'a bakan 7
    # asset var ve release listesi repo başına bir kez çekiliyor (aşağıdaki ctx
    # önbelleği), ama art arda koşularda sınır yine de dolabiliyor.
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _github_releases(repo: str, ctx: dict) -> list:
    """Repo başına TEK istek. per_page=100 bilinçli: BtbN'de aranan ay-sonu
    tag'i, günlük build'lerin arasında ilk 30 kaydın dışına düşüyor."""
    cache = ctx.setdefault("gh", {})
    if repo not in cache:
        cache[repo] = _http_json(
            f"https://api.github.com/repos/{repo}/releases?per_page=100", _github_headers()
        )
    return cache[repo]


def _github_stable_release(repo: str, ctx: dict) -> dict:
    for rel in _github_releases(repo, ctx):
        if rel.get("draft") or rel.get("prerelease"):
            continue
        return rel
    raise RefreshError(f"{repo}: kararlı (prerelease/draft olmayan) release yok")


def _release_asset(rel: dict, name: str) -> dict:
    for a in rel.get("assets", []):
        if a.get("name") == name:
            return a
    raise RefreshError(f"{rel.get('tag_name')} release'inde '{name}' asset'i yok")


def _github_digest(rel: dict, gh_asset: dict, source: str) -> str:
    """Özeti yayıncının kendi yayınladığı yerden alır. Kaynak asset başına
    farklı çünkü üç yayıncı üç ayrı gelenek kullanıyor — ve hiçbiri
    ötekinin dosyasını yayınlamıyor."""
    if source == "api":
        # omnisharp checksum dosyası YAYINLAMIYOR; tek yayınlanmış özet GitHub'ın
        # kendi sunucu tarafında hesapladığı `digest` alanı. Güven kökü burada
        # yayıncı değil GitHub — kütükte `kaynak: github-api` diye yazılmasının sebebi.
        d = gh_asset.get("digest") or ""
        if not d.startswith("sha256:") or not re.fullmatch(r"[0-9a-f]{64}", d[7:]):
            raise RefreshError(
                f"{gh_asset.get('name')}: GitHub 'digest' alanı boş ya da beklenmedik "
                f"biçimde ({d!r}). Bu asset için özet ELLE hesaplanmalı."
            )
        return d
    if source == "sidecar":
        return "sha256:" + _first_hex64(_http_bytes(gh_asset["browser_download_url"] + ".sha256"))
    if source.startswith("sums:"):
        sums = _release_asset(rel, source.split(":", 1)[1])
        return "sha256:" + _hex_for_name(
            _http_bytes(sums["browser_download_url"]), gh_asset["name"]
        )
    raise RefreshError(f"bilinmeyen özet kaynağı: {source}")


def _github_pin(repo: str, entry: dict, ctx: dict, source: str, kaynak: str) -> dict:
    """Dosya adı sürümler arasında sabit olan GitHub asset'leri (omnisharp, uv,
    yt-dlp) için ortak yol: en yeni kararlı release'te AYNI ADLI asset'i bul."""
    rel = _github_stable_release(repo, ctx)
    gh_asset = _release_asset(rel, _basename(entry["url"]))
    return {
        "kind": "pin",
        "url": gh_asset["browser_download_url"],
        "surum": rel.get("tag_name", "?"),
        "finalize": lambda: {
            "url": gh_asset["browser_download_url"],
            "digest": _github_digest(rel, gh_asset, source),
            "size": int(gh_asset["size"]),
            "kaynak": kaynak,
        },
    }


# ---- BtbN/FFmpeg-Builds: budanan tag'ler yüzünden özel ----

_BTBN_TAG = re.compile(r"^autobuild-(\d{4})-(\d{2})-(\d{2})-\d{2}-\d{2}$")
_BTBN_ASSET = re.compile(r"^ffmpeg-n(\d+(?:\.\d+)+).*win64-lgpl.*\.zip$")


def _btbn_ay_sonu_mu(tag: str) -> bool:
    """BtbN `util/prunetags.sh` ile son 14 build + son 24 AY-SONU tag'ini tutup
    gerisini release'iyle birlikte siliyor. Yani günlük bir tag ~2 haftada 404
    olur, ay-sonu tag'i ~24 ay yaşar. Bu fonksiyon o seçimi zorunlu kılıyor;
    kaldırılırsa pin iki hafta sonra sessizce ölür."""
    m = _BTBN_TAG.match(tag)
    if not m:
        return False
    y, ay, gun = (int(x) for x in m.groups())
    if not 1 <= ay <= 12:
        return False
    return gun == calendar.monthrange(y, ay)[1]


def _resolve_ffmpeg_win(entry: dict, ctx: dict) -> dict:
    repo = "BtbN/FFmpeg-Builds"
    uygun = [r for r in _github_releases(repo, ctx) if _btbn_ay_sonu_mu(r.get("tag_name", ""))]
    if not uygun:
        raise RefreshError(
            f"{repo}: ay-sonu autobuild tag'i bulunamadı. `latest` tag'i her build'de "
            "silinip yeniden yaratıldığı için KULLANILAMAZ."
        )
    rel = max(uygun, key=lambda r: r["tag_name"])

    # `shared` DIŞARIDA: paylaşımlı build ayrı DLL'ler istiyor, biz tek exe
    # kopyalıyoruz. `-n<sürüm>` ŞART: sürümsüz adlar (master-latest) hareketli hedef.
    adaylar = [
        a for a in rel.get("assets", [])
        if _BTBN_ASSET.match(a.get("name", "")) and "shared" not in a["name"]
    ]
    if not adaylar:
        raise RefreshError(f"{rel['tag_name']}: win64-lgpl (shared olmayan) zip yok")
    gh_asset = max(adaylar, key=lambda a: (_version_key(_BTBN_ASSET.match(a["name"]).group(1)),
                                           a["name"]))
    return {
        "kind": "pin",
        "url": gh_asset["browser_download_url"],
        "surum": rel["tag_name"],
        "finalize": lambda: {
            "url": gh_asset["browser_download_url"],
            "digest": _github_digest(rel, gh_asset, "sums:checksums.sha256"),
            "size": int(gh_asset["size"]),
            "kaynak": "yayinci",
        },
    }


# ---- .NET SDK ----

# Kanal genelindeki releases.json 935 KB; tek bir sürümün release.json'ı ~30 KB.
# ⚠️ SINIRI AÇIK OLSUN: bu adres 10.0.0 sürümünün DURAĞAN belgesi. Yani buradan
# yalnız 10.0.0'ın SDK'sı (10.0.100) çıkar; 10.0.1 gibi bir yama çıktığında bu
# çözümleyici onu GÖRMEZ, sürekli "güncel" der. Yama takibi istenirse bu sabitin
# elle güncellenmesi ya da kanal listesine geçilmesi gerekir — bilinçli taviz:
# .NET major/patch atlaması OmniSharp tarafında sınanmadan yapılamıyor, o yüzden
# otomatik takip buradaki en düşük değerli otomasyon.
_DOTNET_RELEASE_JSON = (
    "https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/10.0.0/release.json"
)


def _resolve_dotnet(key: str, entry: dict, ctx: dict) -> dict:
    data = ctx.setdefault("dotnet", {})
    if "release" not in data:
        # Dosyalar `release.sdk.files` altında; en üst düzeyde `sdk` YOK
        # (üst düzey anahtarlar: channel-version, release, signature). 2026-07-28'de
        # ölçüldü — yanlış yol sessiz boş liste değil, aşağıdaki sayım kapısına düşer.
        data["release"] = _http_json(_DOTNET_RELEASE_JSON).get("release", {})
    rid = key.split("/", 1)[1]
    url = entry["url"]
    # `name` alanı sürüm TAŞIMIYOR (`dotnet-sdk-win-x64.zip`), o yüzden eşleşme
    # rid + uzantı üzerinden: aynı rid'de .pkg/.exe/.tar.gz/.zip yan yana duruyor
    # ve yanlışını seçmek kullanıcıya çalışmayan bir SDK indirtir.
    ext = ".tar.gz" if url.endswith(".tar.gz") else os.path.splitext(url)[1]
    files = [
        f for f in data["release"].get("sdk", {}).get("files", [])
        if f.get("rid") == rid and f.get("name", "").endswith(ext)
    ]
    if len(files) != 1:
        raise RefreshError(
            f"{key}: release.json'da rid={rid} + '{ext}' için {len(files)} dosya var, 1 bekleniyordu"
        )
    f = files[0]
    if not re.fullmatch(r"[0-9a-fA-F]{128}", f.get("hash", "")):
        raise RefreshError(f"{key}: 'hash' çıplak sha512 hex değil")

    def finalize():
        # release.json boyut vermiyor; boyut kütüğün sözleşmesinde zorunlu (yarım
        # inen dosyayı hex karşılaştırmasından ÖNCE teşhis ettiriyor), o yüzden
        # Content-Length'ten alınıyor.
        req = urllib.request.Request(f["url"], headers={"User-Agent": _UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            boyut = r.headers.get("Content-Length")
        if not boyut:
            raise RefreshError(f"{key}: Content-Length yok, boyut sabitlenemiyor")
        return {
            "url": f["url"],
            "digest": "sha512:" + f["hash"].lower(),
            "size": int(boyut),
            "kaynak": "yayinci",
        }

    surum = data["release"].get("release-version") or f["name"]
    return {"kind": "pin", "url": f["url"], "surum": surum, "finalize": finalize}


# ---- Yayıncının özet yayınlamadığı iki ffmpeg: baytları indirip KENDİMİZ hesaplıyoruz ----

def _yerel_ozet(url: str) -> dict:
    data = _http_bytes(url)
    return {
        "url": url,
        "digest": "sha256:" + digest_of(data, "sha256"),
        "size": len(data),
        # Güven kökü burada yayıncı değil, bu satırı koşturan kişi. `kaynak`
        # alanının tarihi taşıması bunu görünür tutuyor (bkz. testler).
        "kaynak": f"yerel:{_bugun()}",
    }


def _resolve_ffmpeg_macos(entry: dict, ctx: dict) -> dict:
    surum = _http_json("https://evermeet.cx/ffmpeg/info/ffmpeg/release").get("version")
    if not surum:
        raise RefreshError("evermeet.cx 'version' alanı boş döndü")
    url = f"https://evermeet.cx/ffmpeg/ffmpeg-{surum}.zip"
    return {"kind": "pin", "url": url, "surum": surum, "finalize": lambda: _yerel_ozet(url)}


def _resolve_ffmpeg_linux(entry: dict, ctx: dict) -> dict:
    metin = _http_bytes("https://johnvansickle.com/ffmpeg/release-readme.txt").decode(
        "utf-8", "replace"
    )
    m = re.search(r"(?im)^\s*version:\s*([0-9][0-9A-Za-z.\-]*)", metin) or re.search(
        r"ffmpeg-([0-9][0-9A-Za-z.]*)-amd64-static", metin
    )
    if not m:
        raise RefreshError("johnvansickle release-readme.txt içinde sürüm bulunamadı")
    surum = m.group(1).rstrip(".")
    url = f"https://johnvansickle.com/ffmpeg/releases/ffmpeg-{surum}-amd64-static.tar.xz"
    return {"kind": "pin", "url": url, "surum": surum, "finalize": lambda: _yerel_ozet(url)}


# ---- NuGet / Roslyn: BİLDİR, yükseltme ----

def _resolve_roslyn(key: str, entry: dict, ctx: dict) -> dict:
    """roslyn/* Unity Editor'ün İÇİNE yükleniyor (RoslynInstaller.cs) ve sürüm
    yükseltmesi Unity uyumluluğunu sessizce kırabiliyor. Bu yüzden burada kütük
    ASLA otomatik değiştirilmiyor; yalnız 'daha yenisi var' diye bildiriliyor.
    Yükseltme, Unity'de sınandıktan sonra elle yapılacak bir karardır."""
    pkg = key.split("/", 1)[1]
    pinli = entry["url"].rstrip("/").split("/")[-2]
    surumler = _http_json(
        f"https://api.nuget.org/v3-flatcontainer/{pkg}/index.json"
    ).get("versions", [])
    kararli = [v for v in surumler if "-" not in v]
    if not kararli:
        raise RefreshError(f"{pkg}: nuget'te kararlı sürüm listelenmedi")
    en_yeni = max(kararli, key=_version_key)
    if _version_key(en_yeni) > _version_key(pinli):
        return {
            "kind": "notice",
            "mesaj": f"daha yeni sürüm var: {pinli} → {en_yeni} "
                     "(Unity uyumluluğu sınanmadan OTOMATİK yükseltilmiyor)",
        }
    return {"kind": "guncel", "surum": pinli}


def _resolve_cua_driver(key: str, entry: dict, ctx: dict) -> dict:
    """Verify the pinned wheel against PyPI's version-specific metadata."""
    filename = _basename(entry["url"])
    match = re.fullmatch(r"cua_driver-([0-9.]+)-.+\.whl", filename)
    if not match:
        raise RefreshError("invalid cua-driver wheel filename")
    version = match.group(1)
    metadata = _http_json(f"https://pypi.org/pypi/cua-driver/{version}/json")
    wheel = next((item for item in metadata["urls"] if item["filename"] == filename), None)
    if not wheel:
        raise RefreshError(f"cua-driver {version}: pinned wheel missing on PyPI")
    digest = wheel["digests"]["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RefreshError("invalid PyPI sha256")
    return {"kind": "pin", "url": wheel["url"], "surum": version, "finalize": lambda: {
        "url": wheel["url"], "digest": f"sha256:{digest}", "size": int(wheel["size"]), "kaynak": "pypi",
    }}


def _resolver_for(key: str):
    """Anahtar → çözümleyici. Sözlük değil zincir: anahtarlar önek taşıyor
    (`omnisharp/win-x64`) ve yeni bir platform eklendiğinde burada değişiklik
    gerekmemesi isteniyor."""
    if key == "cua-driver/macos-universal2":
        return _resolve_cua_driver
    if key.startswith("omnisharp/"):
        return lambda k, e, c: _github_pin("OmniSharp/omnisharp-roslyn", e, c, "api", "github-api")
    if key.startswith("uv/"):
        return lambda k, e, c: _github_pin("astral-sh/uv", e, c, "sidecar", "yayinci")
    if key.startswith("yt-dlp/"):
        return lambda k, e, c: _github_pin("yt-dlp/yt-dlp", e, c, "sums:SHA2-256SUMS", "yayinci")
    if key == "ffmpeg/win":
        return lambda k, e, c: _resolve_ffmpeg_win(e, c)
    if key == "ffmpeg/macos":
        return lambda k, e, c: _resolve_ffmpeg_macos(e, c)
    if key == "ffmpeg/linux":
        return lambda k, e, c: _resolve_ffmpeg_linux(e, c)
    if key.startswith("dotnet-sdk/"):
        return _resolve_dotnet
    if key.startswith("roslyn/"):
        return _resolve_roslyn
    return None


# ---- Kütüğü yerinde güncelleme ----

def _block_span(text: str, key: str) -> tuple[int, int]:
    """Bir asset bloğunun ham metindeki sınırları.

    Neden json.dump ile yeniden yazılmıyor: bu dosyada gruplar arası boş
    satırlar ve `$aciklama`/`$not`/`$roslyn_notu` blokları var; yeniden
    serileştirmek biçimi düzleştirir ve diff'i okunamaz hale getirir. Diff'in
    okunabilir kalması burada bir güvenlik özelliği — `$aciklama` "diff'i
    İNCELE" diyor, incelenemeyen diff o talimatı boşa çıkarır."""
    needle = f'"{key}": {{'
    i = text.find(needle)
    if i < 0:
        raise RefreshError(f"kütükte '{key}' bloğu bulunamadı")
    start = i + len(needle) - 1
    depth, in_str, esc = 0, False, False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return start, j + 1
    raise RefreshError(f"'{key}' bloğu kapanmamış — kütük bozuk")


def _replace_field(block: str, name: str, value, key: str) -> str:
    if isinstance(value, int):
        pattern, yeni = rf'("{name}"\s*:\s*)\d+', str(value)
    else:
        pattern, yeni = rf'("{name}"\s*:\s*)"[^"]*"', json.dumps(value, ensure_ascii=False)
    block, n = re.subn(pattern, lambda m: m.group(1) + yeni, block, count=1)
    if n != 1:
        raise RefreshError(f"{key}: '{name}' alanı bloğun içinde bulunamadı")
    return block


def _atomik_yaz(path: str, text: str) -> None:
    """Aynı dizinde geçici dosyaya yaz → flush + fsync → `os.replace` ile yerine koy.

    Hangi arızadan doğdu: düz `open(path, "w")` dosyayı ÖNCE kesiyor. Kesme ile
    yazmanın bitmesi arasındaki çökme/disk-dolması/kill, güven kökü olan kütüğü
    boş ya da yarım bırakıyordu — ve aşağıdaki `json.loads` kapısı yalnız bellekteki
    metni doğruluyor, yazmanın hayatta kaldığına dair hiçbir şey söylemiyor.

    Geçici dosya HEDEFİN KENDİ dizininde açılıyor: `os.replace` yalnız aynı dosya
    sistemi içinde atomik, dosya sistemleri arası rename değil.

    `newline="\\n"`: metin modu Windows'ta her satır sonunu CRLF'e çevirip kütüğü
    baştan sona yeniden yazardı. Bu dosyanın okunabilir diff'i bu kodun kendi
    yorumlarında güvenlik özelliği sayılıyor (`$aciklama` "diff'i İNCELE" diyor);
    her satırı değişmiş gösteren bir diff o talimatı boşa çıkarır.
    """
    dizin = os.path.dirname(os.path.abspath(path))
    fd, gecici = tempfile.mkstemp(prefix=".pinned_assets-", suffix=".tmp", dir=dizin)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # mkstemp 0600 veriyor; hedefin izinleri korunmazsa kütük replace sonrası
        # okunamaz hale gelebilir (betikleri başka bir kullanıcı koşturuyorsa).
        try:
            os.chmod(gecici, os.stat(path).st_mode & 0o7777)
        except OSError:
            pass
        os.replace(gecici, path)
    except BaseException:
        # Yerine konamayan geçici dosya ortada bırakılmıyor: dizinde duran yarım bir
        # kütük kopyası, tam da kapatmaya çalıştığımız "hangisi gerçek" arızası.
        try:
            os.unlink(gecici)
        except OSError:
            pass
        raise


def _write_updates(updates: dict) -> None:
    # newline="": satır sonları OLDUĞU GİBİ okunuyor. `newline=None` (varsayılan)
    # CRLF'i LF'e çevirir ve yazma yolu dosyayı sessizce baştan sona değiştirirdi.
    with open(MANIFEST_PATH, encoding="utf-8", newline="") as f:
        text = f.read()
    for key, fields in updates.items():
        s, e = _block_span(text, key)
        block = text[s:e]
        for name, value in fields.items():
            block = _replace_field(block, name, value, key)
        text = text[:s] + block + text[e:]

    # Kapı: yazmadan ÖNCE yeniden ayrıştır ve değerlerin gerçekten oturduğunu gör.
    # Metin cerrahisi biçimi koruyor ama sessizce bozabilir; bozuk bir kütük
    # doğrulamanın tamamını devre dışı bırakır, yani en pahalı arıza budur.
    data = json.loads(text)
    for key, fields in updates.items():
        for name, value in fields.items():
            if data["assets"][key][name] != value:
                raise RefreshError(f"{key}: '{name}' yazıldıktan sonra doğrulanamadı")
    _atomik_yaz(MANIFEST_PATH, text)


def refresh(keys: list[str] | None = None, *, write: bool = False, out=None) -> list[dict]:
    """Yayıncılardan en yeni sürümü çözer, kütükle karşılaştırır, farkı basar.

    Varsayılan KURU KOŞU. `--write` verilmedikçe JSON'a dokunulmuyor.
    Dönen liste test edilebilirlik için: her kayıt {key, durum, ...}.
    """
    out = out or sys.stdout
    entries = load_manifest()["assets"]
    secilen = list(keys) if keys else sorted(entries)
    for k in secilen:
        if k not in entries:
            raise KeyError(
                f"pinned_assets.json içinde '{k}' yok. Tanımlı anahtarlar: "
                + ", ".join(sorted(entries))
            )

    ctx: dict = {}
    sonuclar: list[dict] = []
    for key in secilen:
        entry = entries[key]
        try:
            resolver = _resolver_for(key)
            if resolver is None:
                raise RefreshError("bu anahtar için çözümleyici tanımlı değil")
            r = resolver(key, entry, ctx)
            if r["kind"] == "notice":
                sonuclar.append({"key": key, "durum": "bildirim", "mesaj": r["mesaj"]})
                continue
            if r["kind"] == "guncel" or r["url"] == entry["url"]:
                # Adres aynıysa özet ÇEKİLMİYOR. İki sebep: (1) 1.5 GB'lık kütüğü
                # boşuna indirmemek, (2) aynı adreste DEĞİŞMİŞ bir özeti buraya
                # sessizce yazmamak — o durum yeniden yayınlanmış/ele geçirilmiş
                # asset demektir ve `verify_bytes` tarafından yüksek sesle
                # yakalanması gereken bir arızadır, refresh tarafından kapatılması
                # gereken bir fark değil.
                sonuclar.append({"key": key, "durum": "guncel", "surum": r.get("surum")})
                continue
            yeni = r["finalize"]()
            degisen = {n: v for n, v in yeni.items() if entry.get(n) != v}
            sonuclar.append({
                "key": key, "durum": "degisti", "surum": r.get("surum"),
                "eski": {n: entry.get(n) for n in degisen}, "yeni": degisen,
            })
        except Exception as e:  # ağ, ayrıştırma, biçim — hepsi tek asset'i düşürür
            # Tek asset'in çökmesi koşuyu öldürmüyor: 19 asset'in 8 ayrı yayıncısı
            # var, birinin bakımda olması diğer 18'in tazelenmesini engellememeli.
            sonuclar.append({"key": key, "durum": "hata", "mesaj": f"{type(e).__name__}: {e}"})

    degisenler = {s["key"]: s["yeni"] for s in sonuclar if s["durum"] == "degisti"}
    if write and degisenler:
        _write_updates(degisenler)

    _refresh_raporu(sonuclar, write=write, out=out)
    return sonuclar


def _refresh_raporu(sonuclar: list[dict], *, write: bool, out) -> None:
    mod = "YAZMA" if write else "KURU KOŞU"
    print(f"[pinned] refresh — {len(sonuclar)} asset, {mod}", file=out)
    if not write:
        print("         JSON'a DOKUNULMADI; uygulamak için: --write", file=out)
    print("", file=out)
    for s in sonuclar:
        if s["durum"] == "guncel":
            print(f"  güncel   {s['key']}  ({s.get('surum') or '-'})", file=out)
        elif s["durum"] == "bildirim":
            print(f"  BİLDİRİM {s['key']}  {s['mesaj']}", file=out)
        elif s["durum"] == "hata":
            print(f"  HATA     {s['key']}  {s['mesaj']}", file=out)
        else:
            print(f"  DEĞİŞTİ  {s['key']}  ({s.get('surum') or '-'})", file=out)
            for alan, deger in s["yeni"].items():
                print(f"             {alan:7s}: {s['eski'].get(alan)}", file=out)
                print(f"             {'':7s}→ {deger}", file=out)
    sayim = {d: sum(1 for s in sonuclar if s["durum"] == d)
             for d in ("degisti", "guncel", "bildirim", "hata")}
    print("", file=out)
    print(
        f"Özet: {sayim['degisti']} değişiklik, {sayim['guncel']} güncel, "
        f"{sayim['bildirim']} bildirim, {sayim['hata']} hata"
        + ("" if write else "  (kuru koşu — hiçbir şey yazılmadı)"),
        file=out,
    )
    if sayim["degisti"] and write:
        print("Diff'i İNCELE: özeti körlemesine güncellemek bu kontrolü anlamsız kılar.", file=out)


def check(keys: list[str] | None = None, *, out=None) -> list[dict]:
    """Sabitlenmiş adresler hâlâ ayakta mı?

    Neden gerekli: iki pin'in süresi DOLACAK ve bu sessizce olacak —
    `ffmpeg/win` BtbN'in tag budamasıyla, `ffmpeg/linux` releases/ → old-releases/
    taşınmasıyla. İkisi de build gününe kadar fark edilmez; bu ucuz yoklama
    farkı haftalar öne çeker.
    """
    out = out or sys.stdout
    entries = load_manifest()["assets"]
    secilen = list(keys) if keys else sorted(entries)
    sonuclar = []
    for key in secilen:
        if key not in entries:
            raise KeyError(f"pinned_assets.json içinde '{key}' yok")
        url = entries[key]["url"]
        try:
            kod = _http_status(url)
            durum = "ok" if 200 <= kod < 400 else ("olu" if kod in (404, 410) else "bilinmiyor")
            sonuclar.append({"key": key, "durum": durum, "kod": kod, "url": url})
        except Exception as e:
            sonuclar.append({"key": key, "durum": "bilinmiyor", "kod": None,
                             "url": url, "mesaj": f"{type(e).__name__}: {e}"})

    for s in sonuclar:
        etiket = {"ok": "ayakta ", "olu": "ÖLÜ    ", "bilinmiyor": "?      "}[s["durum"]]
        print(f"  {etiket} {s['key']}  HTTP {s['kod']}"
              + (f"  {s.get('mesaj')}" if s.get("mesaj") else ""), file=out)

    olu = [s["key"] for s in sonuclar if s["durum"] == "olu"]
    bilinmeyen = [s["key"] for s in sonuclar if s["durum"] == "bilinmiyor"]
    ayakta = sum(1 for s in sonuclar if s["durum"] == "ok")
    print("", file=out)
    if olu:
        print(f"ÖLÜ PİN: {', '.join(olu)} — `refresh` ile tazele ve diff'i incele.", file=out)
    if bilinmeyen:
        # Ölçülemeyenler AYRI raporlanıyor. Eski hâli 404/410 yoksa "hepsi ayakta"
        # yazıyordu — ağ tamamen kopukken bile. Bir kontrolün ölçemediği şeyi
        # geçmiş sayması, bu deponun yazılı kuralının ihlali ("`rc=2` 'geçti'
        # DEĞİLDİR") ve bu yoklamanın tek işini (ölen pini build gününden önce
        # yakalamak) sessizce iptal ediyordu.
        print(f"ÖLÇÜLEMEDİ: {', '.join(bilinmeyen)} — ağ hatası ya da 5xx. Bu adresler"
              " DOĞRULANMADI; ne ayakta ne ölü sayılıyorlar, tekrar yoklayın.", file=out)
    if not olu and not bilinmeyen:
        # "hepsi ayakta" cümlesi ancak GERÇEKTEN hepsi ölçülebildiyse kurulabilir.
        print(f"Özet: {len(sonuclar)} adresin hepsi ayakta.", file=out)
    else:
        print(f"Özet: {ayakta} ayakta, {len(olu)} ölü, {len(bilinmeyen)} ölçülemedi"
              f"  ({len(sonuclar)} adres).", file=out)
    return sonuclar


def _main(argv: list[str]) -> int:
    """Kabuk betikleri için ince CLI. Çıkış kodu sözleşmesi (modül docstring'iyle aynı):

      0 = tamam
      1 = BÜTÜNLÜK arızası (özet/boyut uyuşmazlığı) ya da eksik kalan tazeleme.
          ASLA tekrar denenmemeli.
      2 = kullanım hatası ya da ortam hatası (desteklenmeyen Python sürümü).
      3 = İŞLETİM arızası: anahtar kütükte yok, dosya okunamadı, kütük bozuk.

    1 ile 3 bilerek AYRI. Çağıran kabuk betikleri yalnız bu sayıyı okuyor; ikisi de
    1 iken okunamayan bir dosya operatöre "baytlar pinle uyuşmuyor" diye görünüyordu
    ve o teşhis, doktrini gereği tekrar denenmemesi gereken bir arızayı işaret ediyor.
    """
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print(
            "kullanım: pinned_assets.py {url|digest|size|keys} <anahtar>\n"
            "          pinned_assets.py verify <anahtar> <dosya>\n"
            "          pinned_assets.py refresh [--write] [anahtar ...]\n"
            "          pinned_assets.py check [anahtar ...]",
            file=sys.stderr,
        )
        return 2
    cmd = argv[1]
    try:
        if cmd == "refresh":
            kalan = argv[2:]
            write = "--write" in kalan
            keys = [a for a in kalan if not a.startswith("--")]
            bilinmeyen = [a for a in kalan if a.startswith("--") and a != "--write"]
            if bilinmeyen:
                print(f"bilinmeyen seçenek: {' '.join(bilinmeyen)}", file=sys.stderr)
                return 2
            sonuclar = refresh(keys or None, write=write)
            # Ağ/ayrıştırma hatası 1 döndürüyor: bir asset tazelenemediyse koşu
            # eksik kalmıştır ve CI'ın bunu yeşil sayması, `refresh`i yazdıran
            # arıza sınıfını (sessizce etkisiz kapı) geri getirirdi.
            return 1 if any(s["durum"] == "hata" for s in sonuclar) else 0
        if cmd == "check":
            sonuclar = check([a for a in argv[2:]] or None)
            # Sıra önemli: ölü pin DOĞRULANMIŞ bir arıza, ölçülememe ise bilgi
            # yokluğu. İkisi birden varsa operatörün önce ölüyü görmesi gerekir.
            if any(s["durum"] == "olu" for s in sonuclar):
                return 1
            # Ölçülemeyen adres sıfır DÖNDÜRMEZ: otomasyonun gördüğü tek sinyal bu
            # sayı ve hiçbir adres doğrulanamamışken "geçti" demek, yoklamayı
            # sessizce iptal etmekti (dış denetim bulgusu, 2026-07-28).
            #
            # Neden 3, 1 değil: sözleşme gereği 1 = BÜTÜNLÜK ve "ASLA tekrar
            # denenmemeli". Ağ hatası ya da 5xx tanım gereği tekrar denenebilir;
            # 1 dönseydi operatöre "bu asla düzelmez, pini tazele" denmiş olurdu
            # ve sağlam bir pin gereksiz yere değiştirilirdi. 3 = İŞLETİM arızası
            # tam olarak bu sınıf: hiçbir bayt doğrulanmadı, hiçbir uyuşmazlık
            # bulunmadı, ortam düzelince tekrar koşulabilir.
            if any(s["durum"] == "bilinmiyor" for s in sonuclar):
                return 3
            return 0
        if cmd == "keys":
            for k in sorted(load_manifest()["assets"]):
                print(k)
            return 0
        if cmd == "verify":
            if len(argv) != 4:
                print("kullanım: pinned_assets.py verify <anahtar> <dosya>", file=sys.stderr)
                return 2
            verify_file(argv[3], argv[2])
            print(f"[pinned] tamam: {argv[2]}")
            return 0
        if len(argv) != 3:
            print(f"kullanım: pinned_assets.py {cmd} <anahtar>", file=sys.stderr)
            return 2
        entry = asset(argv[2])
        if cmd == "url":
            print(entry["url"])
            return 0
        if cmd == "digest":
            print(entry["digest"])
            return 0
        if cmd == "size":
            print(entry["size"])
            return 0
        print(f"bilinmeyen komut: {cmd}", file=sys.stderr)
        return 2
    except IntegrityError as e:
        print(f"[pinned] BÜTÜNLÜK HATASI\n{e}", file=sys.stderr)
        return 1
    except RefreshError as e:
        # Kütüğe yazarken çıkan hata: dosya YAZILMADI (yazma kapısı json.loads ile
        # doğrulandıktan sonra açılıyor), o yüzden kütük bozulmadan kalıyor.
        print(f"[pinned] tazeleme hatası: {e}", file=sys.stderr)
        return 1
    except (KeyError, OSError, json.JSONDecodeError) as e:
        # 3, 1 DEĞİL: bunlar işletim arızası — anahtar yok, dosya okunamadı, kütük
        # bozuk. 1 dönerlerken kabuk çağıranı bunları "indirilen baytlar sabitlenmiş
        # özetle uyuşmuyor" diye raporluyordu; oysa burada hiçbir bayt doğrulanmadı.
        print(f"[pinned] {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
