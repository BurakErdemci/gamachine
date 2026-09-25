import os
import subprocess
import asyncio
import httpx
import logging
import json
import secrets
from typing import Optional

from .mcp_identity import Occupant, ServerIdentity, probe_port_identity

logger = logging.getLogger(__name__)

# Unity bağlantı durumu
class UnityMCPStatus:
    OFF = "off"            # Sunucu kapalı
    STARTING = "starting"  # Sunucu başlatılıyor
    RUNNING = "running"    # Sunucu açık, Unity henüz bağlanmadı
    CONNECTED = "connected" # Sunucu açık + Unity bağlı
    # Portu BAŞKASI tutuyor. "off" ile aynı şey değil ve ayrı olması şart:
    # kullanıcı "kapalı" görünce toggle'a basıyor, başlatma reddediliyor ve
    # sebep hiçbir yerde görünmüyordu. Bu durum kullanıcı eylemi gerektiriyor.
    BLOCKED = "blocked"


class UnityMCPManager:
    """
    Unity MCP Python sunucusunu (unity-mcp/Server) subprocess olarak yönetir.
    Toggle ON → HTTP sunucu başlar (localhost:8080)
    Unity Editor (plugin yüklüyse) bu sunucuya WebSocket ile bağlanır.
    """

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.mcp_port = 8080
        self._starting = False  # Çift başlatmayı önler
        # port_occupant() kimlik probe'u cache'i (perf). Değer artık bool DEĞİL:
        # "biri dinliyor mu" ile "o biri BİZ miyiz" ayrı sorular ve ikincisi
        # cevaplanmadan toggle yeşile dönüyordu.
        self._identity_cache_ts = 0.0
        self._identity_cache_val = Occupant(ServerIdentity.NONE, "henüz ölçülmedi")
        # MCP sunucusunun yerel REST uçlarının (/api/*) paylaşımlı sırrı. Her
        # start_server'da yenilenir; sunucu bizim değilse None kalır.
        self.local_api_token: Optional[str] = None
        # Bu açılışta cache baypas edilerek kurulan kaynağın özeti; yalnız
        # sunucu sağlıklı görülünce diske yazılır (bkz. _record_server_source_hash).
        self._pending_source_hash: Optional[str] = None
        # start_server'ın başarısızlık SEBEBİ. Port çakışmasında kullanıcıya
        # gösterilecek metin buradan geliyor; sadece "başlatılamadı" demek
        # kullanıcıyı çözümsüz bırakıyordu.
        self.last_error: Optional[str] = None
        import sys
        if getattr(sys, "frozen", False):
            # Frozen build: backend.exe = .../resources/Backend/backend.exe →
            # paket kökü (resources) = backend.exe'nin iki üst dizini. unity-mcp ve uv
            # buraya extraResources ile kopyalanır.
            self.project_root = os.path.dirname(os.path.dirname(sys.executable))
        else:
            self.project_root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "..")
            )
        self.server_dir = os.path.join(self.project_root, "unity-mcp", "Server")
        self.local_mcp_source = os.path.join(self.project_root, "unity-mcp", "MCPForUnity")
        self.unity_mcp_repo = f"file:{self.local_mcp_source}"

    def _source_hash_path(self) -> str:
        return os.path.join(os.path.expanduser("~"), ".unity_architect_ai", "mcp_server_src.hash")

    def _compute_server_source_hash(self) -> str:
        """Server/ kaynağının içerik özeti. Okunamazsa boş döner (→ cache baypas edilir)."""
        import hashlib
        h = hashlib.sha256()
        try:
            # uv.lock is hashed because the launch is pinned to it: a cached
            # tool env built from the previous lock would keep old versions.
            for root in (os.path.join(self.server_dir, "src"),
                         os.path.join(self.server_dir, "pyproject.toml"),
                         os.path.join(self.server_dir, "uv.lock")):
                if os.path.isfile(root):
                    h.update(os.path.basename(root).encode())
                    with open(root, "rb") as fh:
                        h.update(fh.read())
                    continue
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".venv")]
                    for name in sorted(filenames):
                        if name.endswith((".pyc", ".pyo")):
                            continue
                        full = os.path.join(dirpath, name)
                        h.update(os.path.relpath(full, self.server_dir).encode())
                        with open(full, "rb") as fh:
                            h.update(fh.read())
            return h.hexdigest()
        except Exception as e:
            logger.warning(f"[UnityMCP] Kaynak özeti hesaplanamadı: {e}")
            return ""

    def _server_source_changed(self) -> bool:
        """uvx cache'i baypas edilmeli mi?

        Neden bayrak gerekli: ``uvx --from <yerel dizin>`` cache'i, o dizindeki
        kaynak değişince KENDİLİĞİNDEN geçersizleşmiyor — ölçüldü (2026-07-27):
        kaynağa eklenen bir belirteç cache'li koşumda görünmedi, ``--no-cache``
        ile görünüyordu; ``--refresh`` de yakalamadı. Yani onsuz sunucu sessizce
        eski kodu çalıştırır.

        Neden HER ZAMAN kullanılmıyor: ``--no-cache`` bağımlılıkları her açılışta
        PyPI'dan çekiyor, yani yerel bir özelliği **internet zorunlu** hale
        getiriyor. Aynı ölçüm: ``--no-cache --offline`` BAŞARISIZ, cache'li
        ``--offline`` 1 saniyede çalıştı. Sahada yaşandı — kısıtlı bir public
        ağda sunucu ancak 2 dk 26 sn'de ayağa kalktı, Unity o sırada yeniden
        bağlanmaktan vazgeçti ve toggle 20 dakika sarıda kaldı; hiçbir katman
        sebebin ağ olduğunu söylemedi.

        Karar bu yüzden içerik özetine bağlı: son kullanıcıda kaynak hiç
        değişmez → hep cache → hızlı ve çevrimdışı çalışır. Geliştiricide kaynak
        değişince bir kez yavaş koşum olur, sonrası yine hızlıdır.
        """
        current = self._compute_server_source_hash()
        if not current:
            return True  # özet alınamadı → güvenli taraf: bayat kod çalıştırma
        try:
            with open(self._source_hash_path(), "r", encoding="utf-8") as fh:
                return fh.read().strip() != current
        except Exception:
            return True

    def _record_server_source_hash(self) -> None:
        """Sunucunun AYAKTA olduğu doğrulandıktan sonra çağrılır.

        İKİ koşul birden gerekiyor, ve ikincisi bir saha arızasından geldi:

        1. Sunucu gerçekten ayakta (health 200). Erken yazılırsa yarıda kalmış
           bir kurulum 'güncel' işaretlenir.
        2. Bu süreç `--no-cache` ile başlatılmış olmalı — yani çalışan build
           GERÇEKTEN bu kaynaktan derlendi. Aksi halde cache'ten gelen eski bir
           build'in sağlıklı yanıtı, güncel kaynağı "kurulmuş" saydırıyordu.

        Özet burada yeniden HESAPLANMIYOR, başlatma anında yakalanan değer
        yazılıyor: başlatmadan sonra kaynağa dokunulmuşsa o değişiklik çalışan
        sürece girmemiştir ve 'kurulu' sayılmamalıdır.
        """
        current = getattr(self, "_pending_source_hash", None)
        if not current:
            return
        try:
            path = self._source_hash_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(current)
        except Exception as e:
            logger.warning(f"[UnityMCP] Kaynak özeti yazılamadı: {e}")

    def _ensure_writable_resources(self) -> None:
        """Frozen build: bundled Server/MCPForUnity Program Files altında (yazılamaz).
        - uvx, paketi --from kaynağının içine egg-info yazarak derler → 'Erişim engellendi'
        - Unity, paket dizinine ~UnityDirMonSyncFile~ yazar → 'Couldn't create'
        Bu yüzden ikisini de yazılabilir kullanıcı dizinine kopyalar ve server_dir /
        local_mcp_source / unity_mcp_repo yollarını oraya yöneltir. Idempotent (mtime'a
        göre yeniden kopyalar). Asla exception fırlatmaz."""
        import sys
        if not getattr(sys, "frozen", False):
            return  # Dev: kaynak repo zaten yazılabilir
        import shutil
        base = os.path.join(os.path.expanduser("~"), ".unity_architect_ai", "unity-mcp")
        targets = [("Server", "pyproject.toml", "server_dir"),
                   ("MCPForUnity", "package.json", "local_mcp_source")]
        for sub, marker, attr in targets:
            src = os.path.join(self.project_root, "unity-mcp", sub)
            dst = os.path.join(base, sub)
            try:
                if not os.path.isdir(src):
                    continue
                need = not os.path.isdir(dst)
                if not need:
                    s, d = os.path.join(src, marker), os.path.join(dst, marker)
                    need = (not os.path.isfile(d)) or (
                        os.path.isfile(s) and os.path.getmtime(s) > os.path.getmtime(d)
                    )
                if need:
                    if os.path.isdir(dst):
                        shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(src, dst)
                setattr(self, attr, dst)
            except Exception as e:
                logger.warning(f"[UnityMCP] {sub} yazılabilir dizine kopyalanamadı: {e}")
        self.unity_mcp_repo = f"file:{self.local_mcp_source}"

    # ─── Subprocess Yönetimi ─────────────────────────────────────────────────

    def is_running(self) -> bool:
        """BİZİM Unity MCP sunucumuz ayakta mı?

        ÖNEMLİ — burada iki ayrı soru vardı ve tek boolean'a katlanmıştı:
        "8080'de biri dinliyor mu" ve "o biri BİZ miyiz". Eskiden ikincisine
        `/health`'in 200'ü cevap sayılıyordu; `/health` bilerek kimliksiz olduğu
        için yabancı bir sunucu bu kontrolü bedavaya geçiyor, toggle yeşile
        dönüyor ve trafik yabancıya gidiyordu (gerekçe ve ayırt edici test için
        bkz. `mcp_identity` modül başlığı). Artık:
          1. Kendi başlattığımız subprocess canlıysa → kesin bizimki, probe yok.
          2. Değilse → kimlik probe'u; YALNIZ `OURS` "ayakta" sayılır.
        """
        return self.port_occupant().identity == ServerIdentity.OURS

    def port_occupant(self) -> Occupant:
        """8080'i tutanın kimliği + kullanıcıya gösterilebilir kısa sebep (2 sn cache).

        Kendi sürecimiz canlıyken probe ATILMIYOR: süreç sahipliği probe'dan güçlü
        bir kanıt, ve her durum yoklamasında bir HTTP isteği doğurmanın anlamı yok.
        """
        if self.process and self.process.poll() is None:
            return Occupant(ServerIdentity.OURS, "kendi sürecimiz")
        import time
        now = time.monotonic()
        if now - self._identity_cache_ts < 2.0:
            return self._identity_cache_val
        self._identity_cache_val = probe_port_identity(self.mcp_port)
        self._identity_cache_ts = now
        return self._identity_cache_val

    def invalidate_identity_cache(self) -> None:
        """Cache'i düşür — sunucuyu başlattıktan/durdurduktan sonra çağrılır.

        2 sn'lik pencere normalde zararsız ama başlatma/durdurma anında yanlış
        tarafa düşüyor: `stop_server` sonrası hâlâ "ayakta" okunabiliyordu.
        """
        self._identity_cache_ts = 0.0

    def is_unity_running(self) -> bool:
        """Unity Editor süreci çalışıyor mu kontrol eder."""
        import platform
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq Unity.exe"],
                    capture_output=True, text=True, timeout=3,
                )
                return "Unity.exe" in result.stdout
            else:
                # Unity Hub'ı filtrele — sadece Unity Editor binary'sini eşleştir
                result = subprocess.run(
                    ["pgrep", "-f", "Unity.app/Contents/MacOS/Unity"],
                    capture_output=True, text=True, timeout=3,
                )
                return bool(result.stdout.strip())
        except Exception:
            return False

    def _get_uvx(self) -> str:
        """uvx binary'sini bulur (gömülü > PATH > yaygın kurulum dizinleri)."""
        import shutil, sys
        # Paketlenmiş app: uv installer'a gömülü (resources/uv/). Kullanıcının PC'sinde
        # uv kurulu olmasa da MCP çalışsın diye önce burayı dene.
        # Windows tek arch (flat: uv/uvx.exe). macOS iki arch (darwin-arm64/ + darwin-x64/)
        # bundle ettiği için çalışma-anı mimarisine göre alt-dizin seçilir.
        if sys.platform == "win32":
            bundled = os.path.join(self.project_root, "uv", "uvx.exe")
        else:
            import platform as _plat
            arch = "arm64" if _plat.machine() in ("arm64", "aarch64") else "x64"
            bundled = os.path.join(self.project_root, "uv", f"darwin-{arch}", "uvx")
        if os.path.isfile(bundled):
            return bundled
        found = shutil.which("uvx")
        if found:
            return found
        home = os.path.expanduser("~")
        if sys.platform == "win32":
            candidates = [
                os.path.join(home, ".local", "bin", "uvx.exe"),
                os.path.join(os.environ.get("APPDATA", ""), "uv", "bin", "uvx.exe"),
                "uvx",
            ]
        else:
            candidates = [
                os.path.join(home, ".local", "bin", "uvx"),
                "/usr/local/bin/uvx",
                "uvx",
            ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return "uvx"

    @staticmethod
    def _get_uv(uvx: str) -> str:
        """`uv` next to the chosen `uvx` (the bundled uv/ folder and every
        installer ship both), else the one on PATH."""
        import shutil
        import sys
        if os.path.isabs(uvx):
            sibling = os.path.join(os.path.dirname(uvx),
                                   "uv.exe" if sys.platform == "win32" else "uv")
            if os.path.isfile(sibling):
                return sibling
        return shutil.which("uv") or "uv"

    def _lock_constraints(self, uvx: str) -> Optional[str]:
        """Exports unity-mcp/Server/uv.lock as a constraints file for uvx.

        Why: `uvx --from <dir>` resolves the server's dependency tree afresh and
        ignores uv.lock, so only the two exact pins in pyproject held and every
        transitive package floated to whatever PyPI had that day. `--constraints`
        bounds each installed package to its locked version. `uv export` rather
        than parsing uv.lock by hand: the lock forks by Python version and
        platform, and the export keeps those markers.

        None -> launch unpinned (logged). A failed export must not take the
        Unity tools away; the old behaviour is the fallback, not an outage.
        """
        lock = os.path.join(self.server_dir, "uv.lock")
        if not os.path.isfile(lock):
            logger.warning("[UnityMCP] uv.lock yok (%s); sunucu sabitlenmeden başlatılıyor.", lock)
            return None
        out_dir = os.path.join(os.path.expanduser("~"), ".unity_architect_ai")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "unity_mcp_constraints.txt")
        cmd = [
            self._get_uv(uvx), "export",
            "--project", self.server_dir,
            "--frozen",  # the lock as it is; never re-resolve at launch
            "--format", "requirements.txt",
            "--no-hashes", "--no-header", "--no-annotate",
            "--no-emit-project",  # `-e .` is not a valid constraint
            "--no-dev", "--all-extras",
            "--output-file", out,
        ]
        import sys as _sys
        _app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _app_dir not in _sys.path:
            _sys.path.insert(0, _app_dir)
        from providers.cli_base import build_spawn_env
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                 env=build_spawn_env(family="uvx"))
        except Exception as e:
            logger.warning("[UnityMCP] uv.lock dışa aktarılamadı (%s); sunucu sabitlenmeden "
                           "başlatılıyor.", e)
            return None
        if res.returncode != 0 or not os.path.isfile(out):
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-1:] or [""]
            logger.warning("[UnityMCP] uv.lock dışa aktarılamadı (çıkış %s: %s); sunucu "
                           "sabitlenmeden başlatılıyor.", res.returncode, tail[0])
            return None
        return out

    @staticmethod
    def _load_or_create_local_api_token() -> str:
        """Yerel paylaşımlı sırrı kalıcı dosyadan okur, yoksa üretip yazar.

        Sır neden her başlatmada YENİDEN ÜRETİLMİYOR: rotasyon HTTP istemcileri
        için bedavaydı çünkü onların config'ini biz yazıyoruz, ama Unity Editor
        eklentisi bağlantısını kendi kuruyor ve sırrı bu dosyadan okuyor. Her
        sunucu açılışında değişen bir sır, eklentiyi her açılışta yeniden
        eşleşmeye zorlardı. Kalıcılığın bedeli düşük: bu sırrın savunduğu tehdit
        aynı kullanıcı olarak çalışan başka bir süreç, ve o süreç zaten
        ~/.codex/config.toml ve .mcp.json içindeki tokenları okuyabiliyor —
        rotasyon az şey satın alıyordu, kullanılabilirlik çok şey satıyor.

        Yol neden ~/.unity-mcp: unity-mcp'nin Python ve C# taraflarının her
        platformda zaten paylaştığı tek dizin (port registry, status dosyaları
        orada). C# eklentisi de sırrı oradan okuyor — ikinci bir konvansiyon
        icat etmiyoruz. 0600: aynı makinedeki diğer kullanıcılar okuyamasın.
        """
        # Dosya ilkelleri local_token_file'da ortak: bu sır ve backend bearer'ı
        # aynı üç kusuru taşıyordu (bağ takibi, var olan 0644'ün sıkılaştırılmaması,
        # chmod'dan önce yazma) ve üçü de orada tek yerde kapatıldı. İki ayrı kopya
        # olsaydı düzeltme yalnız birine uygulanırdı — bu denetimin tekrarlayan
        # bulgusu tam olarak buydu.
        import sys
        _app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _app not in sys.path:
            sys.path.insert(0, _app)
        from local_token_file import _open_secret_for_write, read_secret_file

        token_dir = os.path.join(os.path.expanduser("~"), ".unity-mcp")
        os.makedirs(token_dir, exist_ok=True)
        token_path = os.path.join(token_dir, "local-api-token")

        existing = read_secret_file(token_path)
        if existing:
            return existing

        token = secrets.token_urlsafe(32)
        with os.fdopen(_open_secret_for_write(token_path), "w", encoding="utf-8") as f:
            f.write(token)
        return token

    def start_server(self) -> bool:
        """
        Unity MCP HTTP sunucusunu başlatır.
        Unity'nin kendi arayüzüyle aynı komutu kullanır (uvx).
        Zaten çalışıyorsa veya başlatılıyorsa True döner (idempotent).
        """
        if self._starting or self.is_running():
            return True
        self.last_error = None

        # Port başkasının elindeyse eskiden uvx bind edemeden ölüyordu:
        # start_server yine True dönüyor, hata yalnız log dosyasına düşüyor ve
        # toggle sonsuza kadar sarıda kalıyordu. Portu zorla boşaltmak da
        # seçenek değil — o süreç bizim değil. Kalan tek doğru davranış:
        # başlatmayı reddet ve sebebi söyle.
        #
        # İKİ BAĞIMSIZ ÖLÇÜM var ve ikisi de gerekli:
        #   (a) kimlik probe'u — hiçbir dış araç istemiyor, doğrudan "8080'deki
        #       sunucunun kimlik kapısı var mı" sorusunu ölçüyor;
        #   (b) süreç sahibi sorgusu — kullanıcıya hangi programı kapatacağını
        #       söylüyor, ama `lsof`/`ps` yoksa SESSİZCE boş dönüyor (fail-open).
        # Yalnız (b)'ye dayanmak, aracın bulunmadığı makinede korumayı tamamen
        # kaybetmek demekti; (a) o boşluğu kapatıyor ve tersi de doğru — (a) tek
        # başına kullanıcıya eyleme dönüştürülebilir bir ad veremiyor.
        if self.port_occupant().identity == ServerIdentity.FOREIGN:
            self.last_error = self._blocked_reason(self.port_occupant())
            logger.error(f"[UnityMCP] {self.last_error}")
            return False

        from .mcp_port_guard import foreign_port_owner_infos, port_busy_message
        try:
            foreign = foreign_port_owner_infos(self.mcp_port)
        except Exception as e:
            # Sorgu aracı yoksa/patlarsa engelleme: yanlışlıkla reddetmek,
            # eski (ve çoğu zaman çalışan) davranışa düşmekten daha kötü.
            logger.debug(f"[UnityMCP] Port sahibi sorgulanamadı: {e}")
            foreign = []
        if foreign:
            self.last_error = port_busy_message(self.mcp_port, foreign)
            logger.error(f"[UnityMCP] {self.last_error}")
            return False

        self._starting = True

        self._ensure_writable_resources()  # frozen: Server'ı yazılabilir dizine taşı
        uvx = self._get_uvx()
        cmd = [uvx]
        # Özet BAŞLATMA ANINDA yakalanıyor ve yalnız cache baypas edildiyse
        # kaydedilecek (bkz. _record_server_source_hash). Sebep ölçüldü
        # 2026-07-27: özet health anında yeniden hesaplanıyordu ve başarılı bir
        # /health, çalışan sürecin O KAYNAKTAN doğduğunu kanıtlamıyor — cache'ten
        # gelen eski bir build de 200 döner. Sonuç, eski bir build'in "güncel"
        # damgalanması ve sonraki açılışların cache'e düşmesiydi: sunucu aylarca
        # bayat kod çalıştırabilirdi ve her şey sağlıklı görünürdü.
        self._pending_source_hash = None
        if self._server_source_changed():
            cmd.append("--no-cache")
            self._pending_source_hash = self._compute_server_source_hash()
        cmd += [
            "--from", self.server_dir,
            "mcp-for-unity",
            "--transport", "http",
            "--http-url", f"http://127.0.0.1:{self.mcp_port}",
            "--project-scoped-tools",
        ]

        # Log yazılabilir kullanıcı dizinine — project_root paketlenmiş app'te
        # Program Files'ı gösterebilir (yazma korumalı → PermissionError → start_server
        # patlıyordu). DB/app_data ile aynı kök.
        log_dir = os.path.join(os.path.expanduser("~"), ".unity_architect_ai")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "unity_mcp_server.log")
        try:
            log_file = open(log_path, "a", encoding="utf-8")
            # /api/command Unity Editor'de keyfi C# çalıştırabiliyor (execute_code) ve
            # 8080 makinedeki her sürece açık — sunucu bu sır olmadan o uçları hiç
            # açmıyor (fail-closed). Sadece çocuk sürecin ortamına konur; argv
            # `ps` ile herkese görünür olduğu için komut satırı bayrağı kullanılmaz.
            self.local_api_token = self._load_or_create_local_api_token()
            # Ortam bir İZİN LİSTESİNDEN kuruluyor, `{**os.environ}` DEĞİL:
            # ölçüldü (2026-07-28) ki uvx torunu kullanıcının bütün vendor
            # anahtarlarını ve veritabanı şifreleme anahtarını görüyordu — oysa
            # burada çalışan kod bizim değil (upstream mcp-for-unity + uvx'in
            # PyPI'dan indirdiği her şey). Gerekçe ve liste cli_base'de tek yerde.
            #
            # ⚠️ Yukarıdaki iki sır `overrides` ile geçiyor, yani izin listesi
            # onları ELEYEMEZ. Bu bilinçli: UNITY_MCP_LOCAL_API_TOKEN düşerse
            # sunucu /api uçlarını hiç açmaz (fail-closed) ve toggle sonsuza
            # kadar sarıda kalır.
            import sys as _sys
            _app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _app_dir not in _sys.path:
                _sys.path.insert(0, _app_dir)
            from providers.cli_base import build_spawn_env
            mcp_env = build_spawn_env(family="uvx", overrides={
                "LOCAL_APP_TOKEN": os.environ.get("LOCAL_APP_TOKEN", ""),
                "UNITY_MCP_LOCAL_API_TOKEN": self.local_api_token,
                # Fork'tan devraldığımız telemetri VARSAYILAN AÇIK geliyor ve
                # açılışta https://api-prod.coplay.dev/telemetry/events adresine
                # ping atıyor (`unity-mcp/Server/src/core/config.py`,
                # `telemetry_enabled: bool = True`). Kullanıcıya bunu söyleyen bir
                # yer yok, dolayısıyla rızası da yok → kaynakta kapatıyoruz.
                # Üç adın hepsi veriliyor: upstream `_is_disabled()` üçünü de
                # kabul ediyor (`core/telemetry.py`), biri kaldırılırsa diğerleri
                # tutsun — tek bir düz metin ada bağlı kalmak bu depoda daha önce
                # sessizce açılan bir yol üretti.
                "DISABLE_TELEMETRY": "true",
                "UNITY_MCP_DISABLE_TELEMETRY": "true",
                "MCP_DISABLE_TELEMETRY": "true",
                # FastMCP checks pypi.org for a newer release when it prints
                # its start banner; a local tool server has no business
                # phoning out (and it stalls a start on a filtered network).
                "FASTMCP_CHECK_FOR_UPDATES": "off",
            })
            constraints = self._lock_constraints(uvx)
            if constraints:
                # uvx options must precede the command; "--from" opens the tail.
                at = cmd.index("--from")
                cmd[at:at] = ["--constraints", constraints]
            self.process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=log_file,
                env=mcp_env,
            )
            logger.info(f"[UnityMCP] Sunucu başlatıldı (PID: {self.process.pid}, port: {self.mcp_port})")
            self._starting = False
            # Cache'de bu porta ait eski bir kimlik ölçümü kalmış olabilir
            # (ör. az önceki "yabancı" kararı). 2 sn boyunca ona güvenmek
            # başlattığımız sunucuyu "yabancı" göstermek demek.
            self.invalidate_identity_cache()
            return True
        except Exception as e:
            logger.error(f"[UnityMCP] Başlatılamadı: {e}")
            self.last_error = f"Unity MCP sunucusu başlatılamadı: {e}"
            self.local_api_token = None  # Sunucu ayakta değil — sır de geçersiz
            self._starting = False
            return False

    def stop_server(self) -> None:
        """
        MCP sunucusunu durdurur.

        Portu tutan süreç kendi `Popen` çocuğumuz DEĞİL, torunumuz: `uvx` aracı
        ayrı bir süreç olarak çalıştırıyor. O yüzden `self.process`'i kapatmak
        tek başına portu boşaltmaya yetmiyor ve porta ikinci bir geçiş şart.
        Ama o geçiş artık porttaki her şeyi değil, yalnız kimliği doğrulanan
        süreçleri kapatıyor (bkz. mcp_port_guard).
        """
        from .mcp_port_guard import collect_owned_listeners, terminate_port_listeners

        # Durdurmanın ardından 2 sn boyunca "hâlâ ayakta" okunmasın.
        self.invalidate_identity_cache()

        # Ata zinciri, ARADAKİ uvx ölmeden önce örneklenmeli: uvx gidince
        # dinleyici torun init'e evlat ediniliyor (ppid=1) ve "bizim" olduğu bir
        # daha ebeveynlikle kanıtlanamıyor.
        owned_pids = set()
        had_own_process = self.process is not None
        if had_own_process:
            try:
                owned_pids = collect_owned_listeners(self.mcp_port)
            except Exception as e:
                logger.debug(f"[UnityMCP] Süreç ağacı okunamadı: {e}")

        # Kendi subprocess'imizi durdur
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

        # `had_own_process` şartı is_running()'e ek: sunucumuzu biz başlattıysak
        # /health bir an cevap vermese bile torun süreci sızdırmadan kapatmalıyız.
        if had_own_process or self.is_running():
            try:
                cleanup = terminate_port_listeners(self.mcp_port, owned_pids=owned_pids)
                if cleanup.killed:
                    logger.info(
                        f"[UnityMCP] Port {self.mcp_port} LISTEN temizlendi "
                        f"(PID'ler: {cleanup.killed})"
                    )
                if cleanup.refused:
                    logger.warning(f"[UnityMCP] {cleanup.message}")
            except Exception as e:
                logger.warning(f"[UnityMCP] Port temizlenemedi: {e}")

        self._starting = False
        self.local_api_token = None
        # İkinci kez: yukarıdaki :465 `is_running()` çağrısı durdurma SÜRERKEN
        # ölçüm yapıp cache'i tazeliyor. O ölçüm artık geçersiz.
        self.invalidate_identity_cache()
        logger.info("[UnityMCP] Sunucu durduruldu.")

    # ─── Health & Status ─────────────────────────────────────────────────────

    def api_headers(self) -> dict:
        """Yerel REST uçları (/api/*) için auth başlığı.

        Sunucu bizim değilse (örn. önceki oturumdan kalan ya da Unity'nin kendi
        başlattığı süreç) sır elimizde yok → başlık boş, uçlar 401 döner. /health
        korumasız kaldığı için toggle'ın canlılık kontrolü bundan etkilenmez."""
        return {"X-API-Key": self.local_api_token} if self.local_api_token else {}

    def mcp_url(self, host: str = "localhost") -> Optional[str]:
        """CLI'lara yazılacak MCP transport URL'i. SIR İÇERMEZ.

        Sır ARTIK URL'de değil, `api_headers()`'ın döndürdüğü `X-API-Key`
        başlığında. Çağıran ikisini birlikte yazmalı:

            {"url": mcp_url(), "headers": api_headers()}

        Neden değişti: sır yol segmentindeyken URL'in gittiği HER yere gidiyordu
        — modelin okuyabildiği `workspace/.mcp.json`'a, kayıt komutlarının
        argv'sine (yani `ps` çıktısına) ve hata loglarına. Tek bir seçim dört
        ayrı denetim bulgusu üretti.

        Eski gerekçe "header desteği doğrulanmadı" idi. 2026-07-27'de ÖLÇÜLDÜ:
        claude, kimi ve codex üçü de yapılandırılan başlığı her MCP isteğinde
        gönderiyor. Doğrulanmamış bir varsayım güvenlik tasarımının temeli
        olmuştu; ölçüm onu çürüttü.

        ⚠️ Alan adı CLI'a göre değişiyor ve yanlışı SESSİZ: codex'te doğru anahtar
        `http_headers`; `headers` yazmak hata vermiyor ama başlığı da göndermiyor
        (ölçüldü — 5 istekte hiçbiri taşımadı). Config üreten her yer bunu bilmeli.

        None dönerse çağıran unityMCP kaydını HİÇ yazmamalı:
          - sunucu ayakta değil, ya da
          - sır bizde yok (sunucuyu biz başlatmadık; örn. Unity kendi başlattı).
        İkinci durumda başlıksız bağlantı 401 alır — kaydı yazmak CLI'ı
        bağlanamayan bir MCP'ye kilitler, hiç yazmamak doğru davranış.

        host: çağıran kendi mevcut davranışını korusun diye parametrik
        (bazı config'ler localhost, bazıları 127.0.0.1 yazıyordu).
        """
        if not self.local_api_token or not self.is_running():
            return None
        return f"http://{host}:{self.mcp_port}/mcp"

    async def check_health(self) -> bool:
        """HTTP sunucusunun ayakta olduğunu kontrol eder (/health)."""
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"http://localhost:{self.mcp_port}/health", timeout=2.0
                )
                healthy = res.status_code == 200
                if healthy:
                    # Kaynak özetini ancak sunucunun GERÇEKTEN ayakta olduğunu
                    # gördükten sonra yaz; yarıda kalan bir kurulumu 'güncel'
                    # işaretlemek sonraki açılışı eksik cache'e mahkûm eder.
                    self._record_server_source_hash()
                return healthy
        except Exception:
            return False

    async def check_unity_connected(self) -> bool:
        """Unity Editor'ün (plugin) sunucuya bağlı olup olmadığını kontrol eder."""
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"http://localhost:{self.mcp_port}/api/instances",
                    headers=self.api_headers(), timeout=2.0
                )
                if res.status_code == 200:
                    data = res.json()
                    return bool(data.get("instances"))
        except Exception:
            pass
        return False

    def _blocked_reason(self, occupant: Occupant) -> str:
        """8080'i tutan yabancı süreci kullanıcıya ADIYLA anlat.

        Süreç tablosu okunamazsa probe'un kendi gözlemine düşülüyor: hangi
        programın tuttuğunu söyleyemesek bile "port bizde değil" bilgisi
        kullanıcının elindeki tek eyleme dönüştürülebilir şey. Sessiz kalmak
        düzeltmeye çalıştığımız arızanın kendisi.
        """
        from .mcp_port_guard import foreign_port_owner_infos, port_busy_message
        try:
            owners = foreign_port_owner_infos(self.mcp_port)
        except Exception as e:
            logger.debug(f"[UnityMCP] Port sahibi sorgulanamadı: {e}")
            owners = []
        if owners:
            return port_busy_message(self.mcp_port, owners)
        return (
            f"{self.mcp_port} portunda bize ait olmayan bir sunucu var "
            f"({occupant.detail}). O sunucuyu kapatıp tekrar deneyin."
        )

    async def get_status(self) -> dict:
        """
        Tam durum bilgisini döner:
        {
          "status": "off" | "blocked" | "starting" | "running" | "connected",
          "pid": int | None,
          "port": 8080,
          "instances": [...],
          "reason": str | None   # yalnız "blocked" durumunda dolu
        }

        `reason` alanı bilerek BU uçtan dönüyor: sebep eskiden yalnız toggle
        POST'unun 500 gövdesinde vardı, yani kullanıcı toggle'a basmadan önce
        durumu asla öğrenemiyordu.
        """
        if not self.is_running():
            occupant = self.port_occupant()
            if occupant.identity == ServerIdentity.FOREIGN:
                return {
                    "status": UnityMCPStatus.BLOCKED,
                    "pid": None,
                    "port": self.mcp_port,
                    "instances": [],
                    "reason": self._blocked_reason(occupant),
                }
            return {"status": UnityMCPStatus.OFF, "pid": None, "port": self.mcp_port,
                    "instances": [], "reason": None}

        pid = self.process.pid if self.process else None

        healthy = await self.check_health()
        if not healthy:
            return {"status": UnityMCPStatus.STARTING, "pid": pid, "port": self.mcp_port,
                    "instances": [], "reason": None}

        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"http://localhost:{self.mcp_port}/api/instances",
                    headers=self.api_headers(), timeout=2.0
                )
                instances = res.json().get("instances", []) if res.status_code == 200 else []
        except Exception:
            instances = []

        unity_status = UnityMCPStatus.CONNECTED if instances else UnityMCPStatus.RUNNING
        return {
            "status": unity_status,
            "pid": pid,
            "port": self.mcp_port,
            "instances": instances,
            "reason": None,
        }

    # ─── Unity Paket Kurulumu ────────────────────────────────────────────────

    async def write_autoconnect_when_ready(self, workspace_path: str, timeout: int = 60) -> bool:
        """
        Server port'u açılana kadar bekler, sonra autoconnect scriptini yazar.
        Bu sayede Unity plugin'i bağlanmaya çalıştığında server zaten hazırdır.
        """
        for _ in range(timeout * 2):
            await asyncio.sleep(0.5)
            if self.is_running():
                await asyncio.sleep(1)  # Port açık ama HTTP handler tam init olmamış olabilir
                self._write_autoconnect_script(workspace_path, auto_start=True)
                logger.info("[UnityMCP] Server hazır — autoconnect scripti yazıldı.")
                return True
        logger.warning("[UnityMCP] Server %ds içinde hazır olmadı, autoconnect scripti yazılmadı.", timeout)
        return False

    def install_package(self, workspace_path: str, write_autoconnect: bool = True) -> bool:
        """
        Unity projesine unity-mcp paketini kurar ve otomatik bağlantı scriptini yazar.
        1. Packages/manifest.json → paket bağımlılığı eklenir
        2. Assets/Editor/UnityArchitectAIMCPSetup.cs → [InitializeOnLoad] script
           Bu script Unity açıldığında EditorPrefs'leri set ederek otomatik session başlatır.
        """
        if not workspace_path:
            return False
        self._ensure_writable_resources()  # frozen: MCPForUnity'yi yazılabilir dizine taşı
        manifest_path = os.path.join(workspace_path, "Packages", "manifest.json")
        if not os.path.exists(manifest_path):
            logger.error(f"[UnityMCP] manifest.json bulunamadı: {manifest_path}")
            return False
        try:
            # 1. Paketi manifest.json'a ekle
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest.setdefault("dependencies", {})["com.coplaydev.unity-mcp"] = self.unity_mcp_repo
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)

            # 2. Otomatik bağlantı scriptini yaz (write_autoconnect=False ise çağıran handle eder)
            if write_autoconnect:
                self._write_autoconnect_script(workspace_path, auto_start=True)

            logger.info(f"[UnityMCP] Paket + auto-connect scripti kuruldu: {workspace_path}")
            return True
        except Exception as e:
            logger.error(f"[UnityMCP] Paket kurulumu hatası: {e}")
            return False

    def _write_autoconnect_script(self, workspace_path: str, auto_start: bool = False):
        """
        Unity projesi Assets/Editor/ altına [InitializeOnLoad] C# scripti yazar.
        auto_start=True → AutoStartOnLoad=true + domain reload tetiklenir (Unity açıksa anında bağlanır)
        auto_start=False → AutoStartOnLoad=false (Unity açılınca bile bağlanmaz)
        """
        import time
        editor_dir = os.path.join(workspace_path, "Assets", "Editor")
        os.makedirs(editor_dir, exist_ok=True)
        # ⛔ BU AD ve aşağıdaki `namespace UnityArchitectAI` marka değişiminde
        # (8 Ağu 2026) BİLEREK DEĞİŞTİRİLMEDİ — ad değil, KULLANICININ Unity
        # projesindeki bir dosyanın adresi.
        # Sebep ölçüldü: bu fonksiyon her seferinde aynı yola ÜZERİNE yazıyor ve
        # kod tabanında eski dosyayı silen hiçbir yol yok. Ad değişirse mevcut
        # kullanıcının projesinde eski dosya kalır, aynı namespace'te aynı sınıf
        # İKİ KEZ tanımlanır ve Unity derlemesi kırılır — o noktada MCP köprüsü
        # hiç kalkmaz (bkz. derleme hatası varken köprü kalkmaz kuralı).
        # Değiştirilecekse ÖNCE eski dosyayı silen bir göç adımı yazılmalı.
        script_path = os.path.join(editor_dir, "UnityArchitectAIMCPSetup.cs")

        auto_start_val = "true" if auto_start else "false"
        timestamp = int(time.time())

        # Unity MCP eklentisi sistem PATH'inde uv/uvx arıyor; kullanıcının PC'sinde uv
        # kurulu olmasa da bizim gömülü uvx'imizi kullansın diye eklentinin uvx yol
        # override'ını (EditorPrefs "MCPForUnity.UvxPath") set ediyoruz. C# string için
        # ileri eğik çizgi kullanılır (kaçış gerektirmez; .NET Windows'ta da kabul eder).
        uvx_path = self._get_uvx()
        uvx_override = ""
        if os.path.isabs(uvx_path) and os.path.isfile(uvx_path):
            uvx_cs = uvx_path.replace("\\", "/")
            uvx_override = f'            EditorPrefs.SetString("MCPForUnity.UvxPath", "{uvx_cs}");\n'

        script = f"""\
// Gamachine — MCP bağlantı yapılandırması (güncelleme: {timestamp})
// Bu dosya Gamachine tarafından oluşturulmuştur. Silmeyin.
#if UNITY_EDITOR
using UnityEditor;

namespace UnityArchitectAI
{{
    [InitializeOnLoad]
    internal static class MCPAutoSetup
    {{
        static MCPAutoSetup()
        {{
            EditorPrefs.SetBool("MCPForUnity.UseHttpTransport", true);
            EditorPrefs.SetString("MCPForUnity.HttpUrl", "http://127.0.0.1:{self.mcp_port}");
            EditorPrefs.SetBool("MCPForUnity.AutoStartOnLoad", {auto_start_val});
            EditorPrefs.SetBool("MCPForUnity.ResumeHttpAfterReload", {auto_start_val});
{uvx_override}        }}
    }}
}}
#endif
"""
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)
        logger.info(f"[UnityMCP] Auto-connect scripti yazıldı (auto_start={auto_start}): {script_path}")


# Global singleton
unity_mcp_manager = UnityMCPManager()
