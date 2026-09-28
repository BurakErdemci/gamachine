"""OmniSharp sidecar yaşam döngüsü. unity_mcp_manager deseninde: workspace
açılınca spawn, workspace değişince restart, kapanışta kill. Tüm satır/kolon
çevirileri BURADA yapılır: LSP 0 tabanlı ↔ bizim format 1 tabanlı."""
import asyncio
import collections
import fnmatch
import json
import logging
import os
import platform
import shutil
import socket
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .lsp_client import LspClient, LspError

logger = logging.getLogger("OmniSharp")

_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}

# initialize beklemesi. Eskiden 120 sn'ydi ve kullanıcının gördüğü şey iki dakika
# donan bir editördü. Sağlıklı bir kurulumda ölçüm 1.5-2.5 sn (macOS arm64, Unity
# projesi, 4 koşu) — 60 sn yavaş makinede bile geniş bir pay, ama donmayı
# "bozuk" olarak hissedilebilir bir süreye indiriyor.
_INIT_TIMEOUT = 60
# Başarısız başlatmadan sonra yeniden denemeden önceki bekleme.
_RETRY_COOLDOWN = 30.0

# OmniSharp reports a failed `initialize` ONLY through window/logMessage: the
# error response is held by its output filter until initialization completes,
# which never happens, so the request would sit out the whole _INIT_TIMEOUT.
# Measured 28 Sep 2026, Windows: both lines arrive ~0.7 s after spawn, stderr
# stays empty. "OmniSharp requires the .NET 6 SDK" is logged 30 ms earlier but
# is not a marker: it comes from one discovery provider, and another may still
# register an MSBuild instance.
_FATAL_INIT_MARKERS = ("Failed to handle request initialize", "Could not locate MSBuild")
_LOG_KEEP = 5


class _StartupLog:
    """Server log lines seen during one start attempt, and a future that
    resolves with the first line that means `initialize` has already failed."""

    def __init__(self):
        self.errors: collections.deque[str] = collections.deque(maxlen=_LOG_KEEP)
        self.recent: collections.deque[str] = collections.deque(maxlen=_LOG_KEEP)
        self.fatal: asyncio.Future = asyncio.get_running_loop().create_future()

    def feed(self, params: dict) -> None:
        msg = str(params.get("message", "")).strip()
        if not msg:
            return
        # The failure line carries a full .NET stack trace after the first line.
        line = msg.splitlines()[0].rstrip(" |")[:300]
        self.recent.append(line)
        try:
            is_error = int(params.get("type", 4)) == 1
        except (TypeError, ValueError):
            is_error = False
        if is_error:
            self.errors.append(line)
            if not self.fatal.done() and any(m in msg for m in _FATAL_INIT_MARKERS):
                self.fatal.set_result(line)

    def tail(self, limit: int = 400) -> str:
        """Newest lines that fit in `limit`, cut at line boundaries; the fatal
        line is left out because it already is the failure reason."""
        fatal = self.fatal.result() if self.fatal.done() else None
        kept: list[str] = []
        size = 0
        for line in reversed([x for x in (self.errors or self.recent) if x != fatal]):
            if kept and size + len(line) > limit:
                break
            kept.insert(0, line)
            size += len(line) + 3
        return " / ".join(kept)


def _norm_key(path: str) -> str:
    """Diagnostics sözlüğü anahtarı: URI'den gelen yol (C:/eğik/çizgi) ile
    os.path.abspath çıktısı (C:\\ters\\çizgi) Windows'ta eşleşmez — ikisini de
    normalize et (normpath + normcase), yoksa diagnostics hep boş görünür."""
    return os.path.normcase(os.path.normpath(path))


def _path_to_uri(path: str) -> str:
    return "file:///" + urllib.parse.quote(path.replace("\\", "/").lstrip("/"))


def _uri_to_path(uri: str) -> str:
    p = urllib.parse.unquote(uri)
    p = p[len("file:///"):] if p.startswith("file:///") else p[len("file://"):]
    return p


def _lsp_diag_to_problem(rel_file: str, d: dict) -> dict:
    r = d.get("range") or {}
    start, end = r.get("start") or {}, r.get("end") or {}
    return {
        "file": rel_file,
        "line": int(start.get("line", 0)) + 1,
        "column": int(start.get("character", 0)) + 1,
        "endColumn": int(end.get("character", 0)) + 1,
        "message": d.get("message", ""),
        "severity": _SEVERITY.get(d.get("severity", 1), "error"),
    }


def _omnisharp_roots() -> list[str]:
    """omnisharp kök klasörü adayları: frozen'da resources/omnisharp, dev'de repo third_party/omnisharp."""
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", "omnisharp")))
    here = os.path.dirname(os.path.abspath(__file__))          # Backend/app/omnisharp
    repo = os.path.abspath(os.path.join(here, "..", "..", ".."))  # repo kökü
    roots.append(os.path.join(repo, "third_party", "omnisharp"))
    return roots


def _is_apple_silicon() -> bool:
    """Donanım Apple Silicon mı? platform.machine() YETMİYOR: Rosetta altında
    koşan bir x86_64 Python 'x86_64' döndürüyor, oysa makine arm64 ve arm64
    binary'si sorunsuz spawn edilir (subprocess, in-process yüklenmiyor).
    Kernel sürüm dizesi Rosetta'da bile çevrilmiyor — ölçüldü 2026-07-27:
    `arch -x86_64 python3` → machine='x86_64', uname().version '…RELEASE_ARM64_T8132'."""
    if platform.machine().lower() in ("arm64", "aarch64"):
        return True
    try:
        return "ARM64" in os.uname().version.upper()
    except AttributeError:      # os.uname yok (Windows) — buraya düşmemeli
        return False


def _platform_key() -> str | None:
    """Bu makine için OmniSharp asset klasör adı; desteklenmiyorsa None.

    Adlar UYDURULMUYOR: tek kaynak scripts/fetch_omnisharp.py ASSETS sözlüğü —
    orada yalnız win-x64, osx-arm64, linux-x64 var. Özellikle osx-x64 (Intel Mac)
    ve linux-arm64 release'i indirilmiyor; None dönüp çağıranın anlaşılır hata
    vermesini sağlıyoruz, yoksa var olmayan bir yol denenip "binary bulunamadı"
    gibi yanıltıcı bir mesaj çıkıyor."""
    if os.name == "nt" or sys.platform.startswith("win"):
        # win-arm64 release'i yok; ARM Windows x64'ü emüle ettiği için win-x64 doğru.
        return "win-x64"
    if sys.platform == "darwin":
        return "osx-arm64" if _is_apple_silicon() else None
    if sys.platform.startswith("linux"):
        return "linux-x64" if platform.machine().lower() in ("x86_64", "amd64") else None
    return None


def _unsupported_reason() -> str:
    """Desteklenmeyen platform için kullanıcıya gösterilecek somut sebep."""
    return (f"OmniSharp bu platform için dağıtılmıyor: {sys.platform}/{platform.machine()}. "
            f"Desteklenen: Windows x64, macOS Apple Silicon, Linux x64. "
            f"C# analizi (hata denetimi, IntelliSense) devre dışı; diğer özellikler çalışır.")


def _contained(root: str, path: str) -> bool:
    """`path`, bağlar ÇÖZÜLDÜKTEN sonra hâlâ `root`'un altında mı.

    `islink` tek bir bileşene bakar; kapı yaprakta kurulunca **yolun kendisi**
    bağ olabiliyor. `third_party/omnisharp/<plat>` bir bağsa içindeki `OmniSharp`
    gerçek dosyadır, `islink(cand)` False döner, kapı hiç ateşlenmez ve ürün
    dışarıdaki binary'yi spawn eder. Ölçüldü 2026-07-28 denetiminde, üç ayrı
    biçimde (bkz. `scripts/fetch_omnisharp.py::_contained` — indirme tarafındaki
    ikizi; ürün `scripts/` içinden import edemediği için kod iki yerde duruyor,
    ikisinin de aynı vakayı reddettiği testle bağlı).

    `realpath` iki tarafta: kurulum meşru olarak bir bağın altında olabilir
    (macOS `/tmp` → `/private/tmp`). Reddedilen, kökün altından DIŞARI çıkan yol.
    """
    try:
        r = os.path.realpath(root)
        p = os.path.realpath(path)
    except OSError:
        return False
    return p == r or p.startswith(r + os.sep)


def _resolve_binary() -> str | None:
    plat = _platform_key()
    if plat is None:
        return None
    exe = "OmniSharp.exe" if plat.startswith("win") else "OmniSharp"
    for root in _omnisharp_roots():
        cand = os.path.join(root, plat, exe)
        if not _contained(root, cand):
            logger.error("OmniSharp yolu kökün dışına çıkıyor, çalıştırılmadı: %s", cand)
            continue
        # ⚠️ `islink` kapısı ÇALIŞTIRMADAN önce. `os.path.exists` bağ takip ediyor,
        # yani `third_party/omnisharp/<plat>/OmniSharp` yerine konmuş bir bağ,
        # gösterdiği herhangi bir binary'nin bu ürün tarafından spawn edilmesini
        # sağlıyordu. İndirme tarafındaki `_intact()` de aynı bağı "sağlam kurulum"
        # sayıp indirmeyi atlıyordu, yani kalıcı hale geliyordu (dış denetim,
        # 2026-07-28). Bizim kurulumumuz gerçek dosya üretir; burada bağ görmek
        # beklenmedik bir durumdur ve çalıştırmamak doğru cevaptır.
        if os.path.islink(cand):
            logger.error("OmniSharp yolu bir sembolik bağ, çalıştırılmadı: %s", cand)
            continue
        if os.path.exists(cand):
            return cand
    return None


def _embedded_dotnet_root() -> str | None:
    """Gömülü .NET SDK kökü; yoksa None.

    `sdk/` klasörünün varlığı ŞART koşuluyor, yalnız `dotnet` host'unun varlığı
    YETMİYOR: .NET *runtime* paketi de host'u ve `shared/` klasörünü getiriyor,
    yani host'un orada olması SDK olduğunu kanıtlamıyor. 27 Tem 2026'ya kadar
    burada yalnız host aranıyordu ve gömülü yük runtime'dı — OmniSharp MSBuild'i
    çözemeyip `initialize` isteğine hiç yanıt vermiyor, C# hover 120 sn asılıyordu."""
    plat = _platform_key()
    if plat is None:
        return None
    exe = "dotnet.exe" if plat.startswith("win") else "dotnet"
    # dotnet-<plat> klasör adı _platform_key ile aynı anahtarı kullanıyor
    # (fetch_omnisharp.fetch_dotnet da öyle yazıyor) — sabit string yazmak, Intel
    # Mac'te var olmayan bir dotnet-linux-x64 yolunu aramaya yol açıyordu.
    for root in _omnisharp_roots():
        cand = os.path.join(root, f"dotnet-{plat}")
        sdk = os.path.join(cand, "sdk")
        # ⚠️ Burada 2026-07-28'e kadar HİÇ bağ kontrolü yoktu — ne yaprakta ne
        # yolda. Döndürülen dizin `DOTNET_ROOT` oluyor ve `PATH`'in BAŞINA
        # ekleniyor (_spawn_env), yani sürecin yorumlayıcısını belirliyor.
        # Denetimde kanıtlandı: `dotnet-<plat>` bir bağ olduğunda dışarıdaki ağaç
        # DOTNET_ROOT olarak dönüyordu. Kardeş kapılar `_resolve_binary`'de.
        if not _contained(root, cand) or not _contained(root, sdk):
            logger.error("Gömülü .NET kökü kökün dışına çıkıyor, kullanılmadı: %s", cand)
            continue
        if os.path.exists(os.path.join(cand, exe)) and os.path.isdir(sdk) and os.listdir(sdk):
            return cand
    return None


# Alt sürece geçirilecek ortam değişkenlerinin İZİN LİSTESİ. Liste bilerek tek
# parça: Windows'a özgü adlar da burada duruyor ve yalnız gerçekten VAR olanlar
# kopyalanıyor, yani macOS'ta hiçbiri eklenmiyor. Alternatif (os.name'e göre
# dallanmak) Windows dalını macOS'tan sınanamaz kılardı — bu dosyada dallanmayı
# azaltmak bilinçli bir doğrulanabilirlik kararı (bkz. _spawn_env gerekçesi).
#
# Neden allow-list, neden `{**os.environ}` değil (dış denetim, 2026-07-27): canlı
# probe ile ölçüldü — OmniSharp çocuğu LOCAL_APP_TOKEN ve API_KEY_ENCRYPTION_KEY
# değişkenlerini görüyordu. İkincisi veritabanı şifreleme anahtarı ve üçüncü parti
# bir binary'nin ona ihtiyacı yok.
#
# ⚠️ Liste DARALTILIRKEN dikkat: buradan çıkarılan her ad, OmniSharp'ın hiç
# başlamamasına yol açabilir ve arıza sessiz olur (initialize'a yanıt gelmez,
# yalnız timeout görünür). PATH `dotnet` host'unu bulmak için, HOME/USERPROFILE
# NuGet ve MSBuild önbelleği için, TMPDIR ailesi MSBuild'in ara dosyaları için
# zorunlu.
_ENV_ALLOWLIST = (
    # POSIX + ortak
    "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL",
    "USER", "LOGNAME", "SHELL",
    # Windows
    "SystemRoot", "SystemDrive", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "ProgramData", "ProgramFiles", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS",
)


def _spawn_env() -> dict:
    """OmniSharp spawn ortamı: İZİN LİSTESİYLE kurulmuş minimal ortam, üstüne
    (varsa) GÖMÜLÜ .NET SDK yönlendirmesi (0-kurulum — kullanıcının makinesinde
    .NET olmasa da çalışır). OmniSharp net6.0 hedefli → DOTNET_ROLL_FORWARD=LatestMajor
    ile gömülü .NET 10 LTS'te koşar.

    HER İKİ dal da minimal ortam döndürüyor. Eskiden gömülü SDK yokken `None`
    dönülüyordu ve `None` "ebeveyn ortamını aynen devral" demek — yani sızıntıyı
    kapatmayan bir daldı. Gömülü SDK yokluğunda yapılması gereken tek şey
    DOTNET_ROOT'u DAYATMAMAK (sistemdeki kurulum bozulmasın), ortamı olduğu gibi
    aktarmak değil.

    Windows da buradan geçiyor. Eskiden net472 varyantı kullanılıp "runtime
    gerekmez" diye atlanıyordu; bu .NET Framework için doğru ama MSBuild için
    yanlıştı — hiçbir OmniSharp v1.39.15 asset'i MSBuild paketlemiyor (ölçüldü
    2026-07-27, win-x64 ve mono zip'leri açıldı). İki platform artık AYNI kod
    yolundan geçiyor; Windows'u macOS'tan sınayamadığımız için dallanmayı azaltmak
    doğrulanabilirliğin kendisi."""
    env = {name: os.environ[name] for name in _ENV_ALLOWLIST if name in os.environ}
    root = _embedded_dotnet_root()
    if root is not None:
        env["DOTNET_ROOT"] = root
        # LatestMajor, not Major: Major picks the LOWEST installed major >= 6, which
        # on a machine with a system .NET 8 runtime is 8 — and Microsoft.Build.Locator
        # under .NET 8 rejects the bundled SDK 10, so `initialize` never completes.
        # Measured 28 Sep 2026, Windows: Major -> "Could not locate MSBuild";
        # LatestMajor -> initialize answered in 1.4 s with SDK 10.0.100. It also
        # matches OmniSharp's own runtimeconfig (`rollForward: LatestMajor`).
        env["DOTNET_ROLL_FORWARD"] = "LatestMajor"
        # PATH'e de ekleniyor: OmniSharp MSBuild'i Microsoft.Build.Locator ile
        # çözerken `dotnet` komutunu çalıştırıyor ve DOTNET_ROOT tek başına onu
        # PATH'e koymuyor.
        env["PATH"] = root + os.pathsep + os.environ.get("PATH", "")
    return env


def _dotnet_missing_reason() -> str | None:
    """C# zekası için MSBuild şart ve o yalnız .NET SDK'da var. SDK hiç yoksa
    ANINDA anlaşılır hata döndürülüyor; yoksa OmniSharp initialize'a yanıt
    vermeden sessizce bekletiyor ve kullanıcı yalnız uzun bir donma görüyor."""
    if _embedded_dotnet_root() is not None:
        return None
    if shutil.which("dotnet"):
        return None      # sistemde dotnet var — dene, sonucu stderr söyler
    return ("C# zekası için .NET SDK gerekli ama gömülü SDK bulunamadı "
            "(scripts/fetch_omnisharp.py koşuldu mu?) ve sistemde de .NET yok. "
            "Diğer özellikler çalışır.")


# Kaynak taramasında atlanacak klasörler: Library/Temp Unity'nin üretim çöplüğü ve
# on binlerce dosya içerebiliyor, obj/bin derleme çıktısı.
_SKIP_DIRS = frozenset({"Library", "Temp", "Logs", "obj", "bin", "Build", "Builds",
                        ".git", ".vs", "node_modules"})
_SOURCE_EXTS = (".cs", ".asmdef")


def _csproj_sync_reason(workspace: str) -> str | None:
    """Unity'nin ürettiği .sln/.csproj tazelenmeli mi? Gerekiyorsa SEBEBİ döndürür.

    Neden gerekli (ölçüldü 2026-07-27): Unity'nin ürettiği csproj **legacy** MSBuild
    formatında (`ToolsVersion 4.0`, `Microsoft.CSharp.targets`) — yani glob YOK,
    dosya listesi tam olarak yazılı olan kadar. Sahadaki projede csproj'lar 23 Tem'de,
    ortada Unity şablonunun yalnız 2 dosyası varken üretilmişti; sonraki 31 dosya ve
    2 asmdef hiç girmedi. OmniSharp sorunsuz açılıp `ready` diyordu ama evreninde 2
    dosya vardı, bu yüzden hover/completion/definition sessizce BOŞ dönüyordu.

    Eski koşul yalnızca "workspace'te hiç .sln yok" idi — yani kapı çok DARDI:
    var ama bayat olan bir .sln arızayı kalıcı hale getiriyordu."""
    try:
        entries = os.listdir(workspace)
    except OSError:
        return None
    project_files = [f for f in entries if f.endswith((".sln", ".csproj"))]
    if not project_files:
        return "proje dosyaları hiç üretilmemiş"

    try:
        newest_project = max(os.path.getmtime(os.path.join(workspace, f))
                             for f in project_files)
    except OSError:
        return None

    assets = os.path.join(workspace, "Assets")
    scan_root = assets if os.path.isdir(assets) else workspace
    existing = {f.lower() for f in project_files}
    for dirpath, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(_SOURCE_EXTS):
                continue
            full = os.path.join(dirpath, name)
            # asmdef → kendi adıyla ayrı bir csproj üretilmeli. asmdef csproj'dan
            # sonra oluşturulmuşsa o proje HİÇ yaratılmamış oluyor; mtime kontrolü
            # bunu zaten yakalar ama asmdef sonradan dokunulmamışsa yakalamaz.
            if name.endswith(".asmdef"):
                asm_name = _asmdef_name(full)
                if asm_name and f"{asm_name.lower()}.csproj" not in existing:
                    return f"{asm_name} için proje dosyası yok"
            try:
                if os.path.getmtime(full) > newest_project:
                    return "kaynak dosyalar proje dosyalarından yeni"
            except OSError:
                continue
    return None


_PROJECT_EXTS = (".csproj", ".sln")
# A file outside every csproj gets syntax errors only; the retry asks Unity to
# regenerate the projects, and a regeneration is seconds of editor work, so it
# is not repeated on every keystroke.
_SYNC_RETRY_INTERVAL = 30.0
# LSP FileChangeType
_FILE_CREATED, _FILE_CHANGED, _FILE_DELETED = 1, 2, 3


def _project_files(workspace: str) -> dict[str, float]:
    """The workspace's root .csproj/.sln files with their mtimes. Root only:
    that is where Unity writes them and where `_csproj_sync_reason` looks."""
    out: dict[str, float] = {}
    try:
        names = os.listdir(workspace)
    except OSError:
        return out
    for name in names:
        if name.lower().endswith(_PROJECT_EXTS):
            full = os.path.join(workspace, name)
            try:
                out[full] = os.path.getmtime(full)
            except OSError:
                continue
    return out


def _source_key(path: str) -> str:
    # Lower-cased on every platform: Unity projects live on case-insensitive
    # file systems (NTFS, default APFS), and a false "not in the project" would
    # show the hint and fire a Unity sync for nothing.
    return os.path.normcase(os.path.normpath(path)).lower()


def _csproj_sources(csproj: str) -> tuple[set[str], list[str], str | None]:
    """(explicit Compile items, wildcard Compile items, glob root of an SDK-style
    project). Unity writes legacy projects with one explicit `<Compile Include>`
    per file; the other two shapes are here so a hand-made project does not
    read as "file missing"."""
    base = os.path.dirname(csproj)
    try:
        root = ET.parse(csproj).getroot()
    except (OSError, ET.ParseError):
        return set(), [], None
    keys: set[str] = set()
    patterns: list[str] = []
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag.rsplit("}", 1)[-1] != "Compile":
            continue
        for part in (el.get("Include") or "").split(";"):
            # MSBuild escapes special characters as %XX.
            part = urllib.parse.unquote(part.strip())
            if not part:
                continue
            key = _source_key(os.path.join(base, part))
            if "*" in part or "?" in part:
                patterns.append(key)
            else:
                keys.add(key)
    sdk_root = _source_key(base) if root.get("Sdk") else None
    return keys, patterns, sdk_root


def _unity_api_up() -> bool:
    """Is anything listening where `_maybe_sync_csproj` posts? The unity-mcp
    server is started only by the user's MCP toggle; after an app restart
    nobody had toggled it and every sync attempt ended in WinError 10061
    (measured 28 Sep 2026). A refused connect is cheap; a sync that cannot
    connect is still a thread and a log line per attempt."""
    try:
        with socket.create_connection(("localhost", 8080), timeout=0.5):
            return True
    except OSError:
        return False


def _unwrap_unity_result(body: dict) -> dict:
    """Unity MCP `/api/command` yanıtının gerçek gövdesini çıkarır.

    İki farklı biçim dönebiliyor ve ikisi de sahada görüldü (ölçüldü 2026-07-27):

        {"status": "success", "result": {"success": true, "message": …, "data": …}}
        {"success": false, "error": "…"}          ← rotanın kendi ürettiği hatalar

    Yalnız dış seviyedeki `success` alanına bakmak, BAŞARILI bir yanıtı başarısız
    saymaya yol açıyordu — canlı ölçüm olmasa fark edilmezdi, çünkü hatalı dal
    yalnız Unity bağlıyken çalışıyor."""
    inner = body.get("result")
    return inner if isinstance(inner, dict) else body


def _as_int(value) -> int | None:
    """Sayı alanını güvenle çevirir; çevrilemiyorsa None ("bilinmiyor").

    Bilinmeyeni 0 saymak yanlış olurdu: Unity gerçekten dosya yazmışken sırf alanın
    biçimi beklenmedik diye kullanıcıya "tazelenemedi" uyarısı gösterilirdi."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _asmdef_name(path: str) -> str | None:
    """asmdef'in `name` alanı üretilecek csproj'un adını belirler."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("name") or None
    except (OSError, ValueError):
        return None


class OmniSharpManager:
    def __init__(self):
        self._client: LspClient | None = None
        self._workspace: str | None = None
        self._opened: set[str] = set()
        self._doc_versions: dict[str, int] = {}   # abs path → son gönderilen LSP sürümü
        self._diags: dict[str, list[dict]] = {}   # abs path → problems (eski format)
        self._diag_ping: dict[str, float] = {}    # abs path → son yayın zamanı
        self.status = {"state": "off", "detail": ""}
        self._lock = asyncio.Lock()
        self._retry_after: float = 0.0            # başarısız başlatma sonrası bekleme
        self._proj_mtimes: dict[str, float] | None = None  # what OmniSharp last loaded
        self._sources_stamp: dict[str, float] | None = None
        self._sources: tuple[set[str], list[str], list[str]] = (set(), [], [])
        self._sync_retry_after: float = 0.0
        self._sync_task: asyncio.Future | None = None

    # ── yaşam döngüsü ────────────────────────────────────────────────
    async def ensure_started(self, workspace: str) -> None:
        async with self._lock:
            if self._workspace == workspace and self._client and self._client.alive:
                return
            # Başarısız bir başlatmayı HER istekte tekrarlama. Monaco hover/completion
            # her imleç hareketinde istek üretiyor; başlatma pahalıysa (ya da asılıp
            # timeout'a düşüyorsa) bu kilit üzerinde kuyruk oluşuyor ve editör
            # tamamen donuyor. Soğuma penceresi boyunca hızlıca son hatayı döndür.
            if self._workspace == workspace and time.monotonic() < self._retry_after:
                return
            await self._stop_locked()
            binary = _resolve_binary()
            if not binary:
                # İki ayrı arıza — kullanıcıya farklı şey söylemeli: platform hiç
                # desteklenmiyor (yapılacak bir şey yok) vs. binary indirilmemiş
                # (fetch script'i koşturulunca düzelir).
                if _platform_key() is None:
                    detail = _unsupported_reason()
                else:
                    detail = "OmniSharp binary bulunamadı (scripts/fetch_omnisharp.py koşuldu mu?)"
                self._fail(workspace, detail)
                return
            missing = _dotnet_missing_reason()
            if missing:
                # Ön kontrol: SDK yoksa süreci hiç başlatma. OmniSharp bu durumda
                # `initialize`'a NE result NE error frame'i gönderiyor ve süreci de
                # kapatmıyor, yani tek geri bildirim timeout oluyor. Burada anında
                # ve NEDENİYLE birlikte düşmek, dakikalarca donmaktan iyidir.
                self._fail(workspace, missing)
                return
            self.status = {"state": "starting", "detail": "C# analizi hazırlanıyor…"}
            self._workspace = workspace
            # to_thread: içerideki urlopen SENKRON. `async def` içinden doğrudan
            # çağrılınca tüm event loop'u 15 sn'ye kadar donduruyordu — yani yalnız
            # C# değil, backend'e giden HER istek bekliyordu.
            sync_hint = await asyncio.to_thread(self._maybe_sync_csproj, workspace)
            client = LspClient()
            startup = _StartupLog()
            # Registered before start so no line from the first read can be missed.
            client.on_notification("window/logMessage", startup.feed)
            client.on_notification("window/showMessage", startup.feed)
            # Bayrak adları Task 1 Step 3'te doğrulandı (--help çıktısına göre güncel)
            cmd = [binary, "-z", "-s", workspace, "--languageserver", "--encoding", "utf-8"]
            try:
                await client.start(cmd, cwd=workspace, env=_spawn_env())
                client.on_notification("textDocument/publishDiagnostics", self._on_diags)
                client.on_notification("window/logMessage", self._on_log_message)
                init = asyncio.ensure_future(client.request("initialize", {
                    "processId": os.getpid(),
                    "rootUri": _path_to_uri(workspace),
                    "capabilities": {"textDocument": {
                        "synchronization": {"didSave": False},
                        "publishDiagnostics": {},
                        "completion": {"completionItem": {"snippetSupport": False}},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                    }},
                }, timeout=_INIT_TIMEOUT))
                # _INIT_TIMEOUT stays the outer bound (inside the request); a
                # fatal log line only ends the wait early.
                try:
                    await asyncio.wait({init, startup.fatal},
                                       return_when=asyncio.FIRST_COMPLETED)
                except asyncio.CancelledError:
                    init.cancel()
                    raise
                if not init.done():
                    init.cancel()
                    await asyncio.gather(init, return_exceptions=True)
                    raise LspError(f"OmniSharp initialize'ı reddetti: {startup.fatal.result()}")
                await init
                client.notify("initialized", {})
                self._client = client
                self._proj_mtimes = _project_files(workspace)
                # `ready` ama detail dolu olabilir: sunucu ayakta VE proje dosyaları
                # bayat. Bu tam olarak sahada görülen hal — durum "hazır" görünüyor,
                # hover sessizce boş dönüyordu. Sebep artık yüzeye çıkıyor.
                self.status = {"state": "ready", "detail": sync_hint or ""}
                self._retry_after = 0.0
                logger.info("OmniSharp hazır: %s", workspace)
            except Exception as e:
                # stderr kuyruğu iliştiriliyor: OmniSharp asıl sebebi (örn.
                # "No .NET SDKs were found.") LSP kanalına değil stderr'e yazıyor,
                # o yüzden çıplak timeout mesajı tek başına hiçbir şey anlatmıyor.
                # A TimeoutError stringifies to "" — that empty text is exactly what
                # made the 28 Sep 2026 failure log read "OmniSharp: " with nothing
                # after it. And OmniSharp wrote the real cause to logMessage, not
                # stderr, so the log tail is attached too.
                reason = str(e).strip()
                if not reason:
                    reason = (f"initialize {_INIT_TIMEOUT} sn içinde yanıtlanmadı"
                              if isinstance(e, TimeoutError) else type(e).__name__)
                logs = startup.tail()
                tail = client.stderr_tail
                detail = (reason[:300]
                          + (f" | OmniSharp log: {logs}" if logs else "")
                          + (f" | OmniSharp: {tail[-300:]}" if tail else ""))
                logger.exception("OmniSharp başlatılamadı")
                await client.stop()
                self._fail(workspace, detail)

    def _fail(self, workspace: str, detail: str) -> None:
        """Arızayı kaydet ve soğuma penceresi aç (bkz. ensure_started'daki gerekçe)."""
        self._workspace = workspace
        self.status = {"state": "error", "detail": detail}
        self._retry_after = time.monotonic() + _RETRY_COOLDOWN
        logger.error("OmniSharp: %s", detail)

    def _on_log_message(self, params: dict) -> None:
        """Sunucunun window/logMessage bildirimleri. MSBuild çözümlenemediğinde
        OmniSharp arızayı YALNIZCA buradan duyuruyor ve isteği yanıtsız bırakıyor —
        bu kanal dinlenmezse geriye hiçbir iz kalmıyor."""
        # Yalnız type=1 (error) yükseltiliyor. type=2 (warning) sağlıklı çalışmada
        # da onlarca kez geliyor ("Tried to send request … will be sent later"),
        # WARNING'e basmak logu kullanılmaz kılıyor.
        if int(params.get("type", 4)) == 1:
            logger.warning("OmniSharp: %s", str(params.get("message", ""))[:500])

    def _maybe_sync_csproj(self, workspace: str, reason: str | None = None) -> str | None:
        """Proje dosyaları bayatsa Unity'den tazelemeyi dene (MCP REST, best-effort).
        Tazelenemezse kullanıcıya gösterilecek ipucunu döndürür. A given `reason`
        forces the attempt: an open file missing from every csproj is stale
        projects even when no mtime says so."""
        reason = reason or _csproj_sync_reason(workspace)
        if reason is None:
            return None
        try:
            # /api/command paylaşımlı sır ister (sırsız çağrı 401). Sır sunucuyu
            # başlatan manager'da tutuluyor; import döngüsüne girmemek için yerel import.
            # Absolute, like every other app module: the app loads this file as
            # top-level `omnisharp.omnisharp_manager` (main.py, lsp_routes.py), where
            # `..unity_ai_mcp` raised "attempted relative import beyond top-level
            # package" and the refresh never ran (since e988258). It is also the
            # same module object whose singleton holds the secret.
            from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
            req = urllib.request.Request(
                "http://localhost:8080/api/command", method="POST",
                data=b'{"type": "manage_editor", "params": {"action": "sync_csproj"}}',
                headers={"Content-Type": "application/json",
                         # Köken işareti: bu çağrı MODELDEN değil ürünün kendi
                         # bakımından geliyor, yani onay kartı çıkarmamalı
                         # (K1 ADIM 4). Rotanın paylaşılan sırrı bu ayrımı
                         # yapamıyor — o sır `unity-mcp` CLI'ında da var.
                         # ⚠️ Güvenlik sınırı değil köken işareti; gerekçesi
                         # `approval_gate.urun_bakim_cagrisi_mi`'de.
                         "X-UnityAI-Maintenance": os.environ.get("LOCAL_APP_TOKEN", ""),
                         **unity_mcp_manager.api_headers()})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace"))
            inner = _unwrap_unity_result(body)
            # Yanıt gövdesi OKUNUYOR. Eskiden yalnız "istek gitti mi" bakılıyordu ve
            # Unity tarafı hiçbir şey yazmadan "başarılı" dönebiliyordu — arızanın
            # günlerce fark edilmemesinin sebebi tam olarak buydu.
            if not inner.get("success"):
                raise RuntimeError(str(inner.get("error") or inner.get("message"))[:200])
            data = inner.get("data")
            count = _as_int(data.get("csproj_count")) if isinstance(data, dict) else None
            # `csproj_count` tam olarak "başarılı dedi ama hiçbir dosya yazmadı"
            # halini yakalamak için Unity tarafına eklenmişti; eskiden yalnızca
            # LOGLANIYORDU. 0 dosya = sessiz no-op, ve sessiz no-op'u başarı saymak
            # bu projede zaten bir arızanın günlerce görülmemesine sebep oldu.
            # Alan yoksa ya da sayıya çevrilemiyorsa (eski/farklı Unity paketi)
            # sayı üzerinden karar VERİLMİYOR — aşağıdaki sonuç doğrulaması
            # zaten tek başına yeterli kanıt, uydurma bir varsayım eklemiyoruz.
            if count is not None and count <= 0:
                raise RuntimeError("Unity 'başarılı' dedi ama hiç proje dosyası yazılmadı")
            # ASIL kanıt: raporu değil, dışarıda bıraktığı İZİ ölç. Sync'ten sonra
            # bayatlık kararı yeniden değerlendiriliyor; hâlâ bir sebep dönüyorsa
            # dosyalar gerçekten tazelenmemiştir — yanıt ne derse desin.
            remaining = _csproj_sync_reason(workspace)
            if remaining is not None:
                raise RuntimeError(f"tazeleme sonrası hâlâ bayat: {remaining}")
            logger.info("sync_csproj tamam (%s) → %s", reason, data)
            return None
        except Exception as e:
            logger.info("proje dosyaları tazelenemedi (%s): %s", reason, e)
            # ⚠️ Mesaj bilerek "Unity'yi açın, düzelir" DEMİYOR. Ölçüldü 2026-07-27:
            # Unity açıkken de tazelenmiyor, çünkü .csproj üretimini Unity'nin harici
            # IDE entegrasyonu yapıyor ve makinede kayıtlı bir IDE yoksa (bu ürünün
            # hedef kullanıcısının normal hali) hiç üretilmiyor. Kullanıcıya
            # doğrulanmamış bir çare söylemek, arızayı onun üstüne yıkmak olurdu.
            return ("C# zekası sınırlı: Unity proje dosyaları güncel değil "
                    f"({reason}). Dosya içi tamamlama ve hata denetimi çalışır; "
                    "Unity API'leri (Debug, GameObject gibi) tanınmayabilir.")

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if self._client:
            await self._client.stop()
        self._client = None
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
        self._sync_task = None
        self._sync_retry_after = 0.0
        self._proj_mtimes = None
        self._opened.clear()
        # Sürüm sayaçları `_opened` ile BİRLİKTE sıfırlanmalı: yeni sunucuya
        # yeniden didOpen (sürüm 1) gidecek, sayaç eski değerde kalırsa didChange
        # sürümleri didOpen'la tutarsız olur.
        self._doc_versions.clear()
        self._diags.clear()
        self.status = {"state": "off", "detail": ""}

    # ── diagnostics ──────────────────────────────────────────────────
    def _on_diags(self, params: dict) -> None:
        path = _uri_to_path(params.get("uri", ""))
        rel = os.path.relpath(path, self._workspace).replace("\\", "/") if self._workspace else path
        key = _norm_key(path)
        self._diags[key] = [_lsp_diag_to_problem(rel, d) for d in params.get("diagnostics") or []]
        self._diag_ping[key] = time.monotonic()

    def diagnostics_for(self, path: str) -> list[dict]:
        return self._diags.get(_norm_key(os.path.abspath(path)), [])

    def latest_diagnostics(self, path: str) -> list[dict]:
        """The last set OmniSharp published for `path`, without sending the text.
        `sync_document` waits ~1.2 s; a cold start measured 28 Sep 2026 answered
        empty at 1.34 s and the two errors came 60 ms later, so the editor asks
        again through this."""
        self._check_project_files()
        return self.diagnostics_for(path)

    # ── project files ────────────────────────────────────────────────
    def _check_project_files(self) -> None:
        """Tell OmniSharp about .csproj/.sln files changed since it last loaded
        them. It does not watch them itself: a csproj rewritten on disk while it
        ran had no effect in 30 s, the same rewrite followed by
        `workspace/didChangeWatchedFiles` gave the semantic errors in 2.5 s
        (measured 28 Sep 2026). Unity regenerates the csproj minutes after a
        new script is written (00:44:55 file, 00:56:31 csproj)."""
        if not (self._workspace and self._client and self._client.alive):
            return
        current = _project_files(self._workspace)
        previous, self._proj_mtimes = self._proj_mtimes, current
        if previous is None:
            return
        changes = [{"uri": _path_to_uri(p),
                    "type": _FILE_CREATED if p not in previous else _FILE_CHANGED}
                   for p, mtime in current.items() if previous.get(p) != mtime]
        changes += [{"uri": _path_to_uri(p), "type": _FILE_DELETED}
                    for p in previous if p not in current]
        if changes:
            logger.info("proje dosyaları değişti, OmniSharp'a bildiriliyor: %d", len(changes))
            self._client.notify("workspace/didChangeWatchedFiles", {"changes": changes})

    def in_project(self, path: str) -> bool | None:
        """Is this .cs file compiled by a csproj in the workspace? None when the
        question does not apply. A file outside every csproj gets syntax errors
        only (CS1002); CS0029/CS0103 never come (measured 28 Sep 2026)."""
        if not self._workspace or not path.lower().endswith(".cs"):
            return None
        stamp = _project_files(self._workspace)
        if stamp != self._sources_stamp:
            keys: set[str] = set()
            patterns: list[str] = []
            roots: list[str] = []
            for proj in stamp:
                if not proj.lower().endswith(".csproj"):
                    continue
                k, p, r = _csproj_sources(proj)
                keys |= k
                patterns += p
                if r:
                    roots.append(r)
            self._sources, self._sources_stamp = (keys, patterns, roots), stamp
        keys, patterns, roots = self._sources
        key = _source_key(os.path.abspath(path))
        return (key in keys
                or any(fnmatch.fnmatchcase(key, p) for p in patterns)
                or any(key.startswith(r + os.sep) for r in roots))

    def _maybe_retry_project_sync(self) -> None:
        """Ask Unity for fresh projects again, at most once per
        _SYNC_RETRY_INTERVAL. At start this ran once; a script written later
        stayed outside the csproj until Unity happened to regenerate it."""
        now = time.monotonic()
        if not self._workspace or now < self._sync_retry_after:
            return
        self._sync_retry_after = now + _SYNC_RETRY_INTERVAL
        self._sync_task = asyncio.ensure_future(self._retry_project_sync(self._workspace))

    async def _retry_project_sync(self, workspace: str) -> None:
        # The unity-mcp server is NOT started here: starting it is the owner's
        # MCP toggle (decision pending, 28 Sep 2026).
        if not await asyncio.to_thread(_unity_api_up):
            return
        hint = await asyncio.to_thread(self._maybe_sync_csproj, workspace,
                                       "açık dosya proje dosyalarında yok")
        if self._workspace != workspace or not self._ready():
            return
        if self.status.get("state") == "ready":
            self.status = {"state": "ready", "detail": hint or ""}
        self._check_project_files()

    async def sync_document(self, path: str, text: str) -> list[dict]:
        if not (self._client and self._client.alive):
            return []
        apath = os.path.abspath(path)
        uri = _path_to_uri(apath)
        self._check_project_files()
        if apath not in self._opened:
            self._opened.add(apath)
            self._doc_versions[apath] = 1
            self._client.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": "csharp", "version": 1, "text": text}})
        # didOpen'dan SONRA da her zaman bir tam metinli didChange gönderiliyor.
        #
        # Sebebi ölçüldü (2026-07-27): OmniSharp'ın "miscellaneous files" çalışma
        # alanı — yüklü bir projeye ait olmayan dosyaları taşıyan alan — her zaman
        # açık, ama ona giden TEK tetikleyici `BufferManager.UpdateBufferAsync` ve
        # LSP'de onu çağıran tek bildirim `didChange`. `didOpen` yalnızca
        # `FileOpenService`'e gidiyor, o da workspace'te ZATEN var olan dokümanı
        # açıyor. Dolayısıyla csproj'da listelenmeyen bir dosyada ilk hover
        # garantili boş dönüyordu.
        #
        # Ölçülen kazanç (gerçek Unity dosyası, bayat csproj ile): hover 'PitchBuilder'
        # boş → `class MatchOfficial.EditorTools.PitchBuilder`, completion 0 → 191 öğe.
        # SINIRI da ölçüldü: misc proje yalnız temel .NET referanslarını alıyor, yani
        # `UnityEngine.Debug` gibi Unity tipleri yine çözülmüyor — onun için csproj
        # tazelenmeli. İkisi çakışmıyor: csproj sonradan yüklendiğinde OmniSharp
        # misc dokümanları gerçek projeye kendisi taşıyor.
        # Sürüm numarası doküman başına MONOTON bir sayaç. Eskiden `int(time.time())`
        # idi ve çözünürlüğü 1 saniye: aynı saniyedeki iki senkron AYNI sürümü
        # üretiyor, LSP sunucusu da "bu sürümü zaten gördüm" diyip ikincisini yok
        # sayabiliyordu. Artık her senkronda didChange gönderdiğimiz için (yukarıdaki
        # misc-files gerekçesi) bu çarpışma sık ulaşılabilir hale geldi — hızlı yazan
        # bir kullanıcıda hover/completion bayat metne bakardı.
        version = self._doc_versions[apath] = self._doc_versions.get(apath, 1) + 1
        self._client.notify("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": text}]})
        if self.in_project(apath) is False:
            self._maybe_retry_project_sync()
        # publishDiagnostics async gelir → kısa pencere bekle (yeni yayın ya da timeout)
        sent = time.monotonic()
        key = _norm_key(apath)
        for _ in range(24):  # ~1.2 sn
            await asyncio.sleep(0.05)
            if self._diag_ping.get(key, 0) >= sent:
                break
        return self.diagnostics_for(apath)

    # ── IntelliSense ─────────────────────────────────────────────────
    def _doc_pos(self, path: str, line: int, column: int) -> dict:
        return {"textDocument": {"uri": _path_to_uri(os.path.abspath(path))},
                "position": {"line": line - 1, "character": column - 1}}

    def _ready(self) -> bool:
        """İstek göndermeden önce ZORUNLU kontrol. `ensure_started` başarısız
        olduğunda `_client` None kalıyor; buraya bakılmazsa `None.request`
        AttributeError'ı handler'dan dışarı çıkıyor ve CORSMiddleware'in ALTINDAKİ
        katmanda 500'e dönüşüyor — o yanıtta Access-Control-Allow-Origin olmadığı
        için tarayıcı bunu ağ hatası sayıp "Failed to fetch" gösteriyor. Yani
        kullanıcının gördüğü hata mesajı, gerçek sebebi tamamen gizliyordu."""
        return bool(self._client and self._client.alive)

    async def completion(self, path: str, text: str, line: int, column: int) -> list[dict]:
        if not self._ready():
            return []
        await self.sync_document(path, text)
        try:
            res = await self._client.request("textDocument/completion",
                                             self._doc_pos(path, line, column), timeout=10)
        except LspError:
            return []
        items = res.get("items", res) if isinstance(res, dict) else (res or [])
        out = []
        for it in items[:200]:
            out.append({"label": it.get("label", ""), "kind": it.get("kind", 1),
                        "insertText": it.get("insertText") or it.get("label", ""),
                        "detail": it.get("detail") or ""})
        return out

    async def hover(self, path: str, text: str, line: int, column: int) -> str | None:
        if not self._ready():
            return None
        await self.sync_document(path, text)
        try:
            res = await self._client.request("textDocument/hover",
                                             self._doc_pos(path, line, column), timeout=10)
        except LspError:
            return None
        if not res:
            return None
        c = res.get("contents")
        if isinstance(c, dict):
            return c.get("value")
        if isinstance(c, list):
            return "\n\n".join(x.get("value", x) if isinstance(x, dict) else str(x) for x in c)
        return str(c) if c else None

    async def definition(self, path: str, text: str, line: int, column: int) -> dict | None:
        if not self._ready():
            return None
        await self.sync_document(path, text)
        try:
            res = await self._client.request("textDocument/definition",
                                             self._doc_pos(path, line, column), timeout=10)
        except LspError:
            return None
        loc = (res[0] if isinstance(res, list) and res else res) or None
        if not loc or "uri" not in loc:
            return None
        start = (loc.get("range") or {}).get("start") or {}
        return {"file": _uri_to_path(loc["uri"]),
                "line": int(start.get("line", 0)) + 1,
                "column": int(start.get("character", 0)) + 1}


_manager: OmniSharpManager | None = None


def get_omnisharp_manager() -> OmniSharpManager:
    global _manager
    if _manager is None:
        _manager = OmniSharpManager()
    return _manager
