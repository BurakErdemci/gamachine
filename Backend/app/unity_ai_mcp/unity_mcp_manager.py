import os
import sys
import subprocess
import asyncio
import httpx
import logging
import json
import secrets
import hashlib
import fnmatch
import importlib.util
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Optional

from spawn_env import build_spawn_env
from .mcp_identity import Occupant, ServerIdentity, probe_port_identity
from . import unity_project_presence
from .editor_log import bridge_compile_errors, editor_log_path

logger = logging.getLogger(__name__)

_GENERATED_DIR_PATTERNS = ("__pycache__", "*.egg-info", "build", ".venv")
_DIGEST_FILE = ".gamachine-source-digest"  # marks Gamachine's own resource copy
_FILES_RECORD = ".gamachine-files.json"  # {sourceDigest, files: {relative path: sha256}} of what we copied
_PACKAGE_NAME = "com.coplaydev.unity-mcp"
# Written by every Gamachine before 2 Nov 2026 as the first line of Assets/Editor/UnityArchitectAIMCPSetup.cs.
_LEGACY_SCRIPT_HEADER = "// Gamachine — MCP bağlantı yapılandırması"
_EXCLUDE_MARKER = "# Gamachine (local, not committed)"
# Self-healing: a dead server is restarted at most _RESTART_CAP times per _RESTART_WINDOW_S.
_SUPERVISE_INTERVAL_S = 10
_HEALTH_FAILS_TO_RESTART = 3
_RESTART_CAP = 3
_RESTART_WINDOW_S = 600
_STARTUP_GRACE_S = 120  # uvx may be installing; health failures do not count yet
_GAVE_UP_MESSAGES = {
    "tr": "Unity bağlantı sunucusu tekrar tekrar kapandı; Unity MCP'yi kapatıp açın.",
    "en": "The Unity connection server kept shutting down; turn Unity MCP off and on again.",
}


def _server_log_path() -> str:
    # Writable user dir: project_root may be a write-protected Program Files in the packaged app.
    return os.path.join(os.path.expanduser("~"), ".unity_architect_ai", "unity_mcp_server.log")


class _ManifestChanged(Exception):
    """The live manifest changed while its replacement was being staged."""


class _Declined(Exception):
    """The installed package or the manifest is the user's; the message is the reason shown to them."""


class _Refused(Exception):
    """A path Gamachine would write is a link or resolves outside the folder it belongs to."""


def _is_link(path: str) -> bool:
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def _is_network_path(path: str) -> bool:
    """\\\\host\\share or //host/share: on Windows any lookup there sends the user's NTLM credentials to that host."""
    return path.startswith(("//", "\\\\")) or os.path.splitdrive(path)[0].startswith(("//", "\\\\"))


def _trusted_git_dir(project: str, git_dir: str) -> bool:
    """A git dir named by a file inside the project (.git pointer, commondir) is used only when it stays on the
    project's own share (or off the network) and really is a git dir; checked before anything else touches it."""
    if _is_network_path(git_dir) and os.path.normcase(os.path.splitdrive(git_dir)[0]) \
            != os.path.normcase(os.path.splitdrive(project)[0]):
        return False
    return os.path.isfile(os.path.join(git_dir, "HEAD"))


def _require_plain(root: str, *paths: str) -> None:
    """Raise _Refused when a path is a link/junction or its real location leaves root."""
    real_root = os.path.normcase(os.path.realpath(root))
    for path in paths:
        if _is_link(path):
            raise _Refused(f"{path} is a link")
        real = os.path.normcase(os.path.realpath(path))
        try:
            inside = os.path.commonpath([real, real_root]) == real_root
        except ValueError:  # another drive
            inside = False
        if not inside:
            raise _Refused(f"{path} resolves outside {root}")


_PACKAGE_DIR = ("Packages", _PACKAGE_NAME)
_LEGACY_SCRIPT_NAME = "UnityArchitectAIMCPSetup.cs"


def _legacy_paths(workspace_path: str) -> list:
    """Assets, Assets/Editor, the old script and its .meta."""
    assets = os.path.join(workspace_path, "Assets")
    editor = os.path.join(assets, "Editor")
    script = os.path.join(editor, _LEGACY_SCRIPT_NAME)
    return [assets, editor, script, script + ".meta"]


def _settings_paths(workspace_path: str) -> list:
    folder = os.path.join(workspace_path, "UserSettings", "Gamachine")
    return [os.path.dirname(folder), folder, os.path.join(folder, "autoconnect.json")]


def _package_paths(workspace_path: str) -> list:
    packages = os.path.join(workspace_path, "Packages")
    return [packages, os.path.join(packages, "manifest.json"), os.path.join(workspace_path, *_PACKAGE_DIR)]


def _is_unity_project(workspace_path: str) -> bool:
    return all(os.path.isdir(os.path.join(workspace_path, name)) for name in ("Assets", "ProjectSettings"))


def _atomic_write_text(path, text, *, expected_bytes=None, newline=None):
    """Stage beside the target; optionally reject a stale manifest snapshot."""
    descriptor, staged = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".gamachine-", suffix=".tmp")
    try:
        os.close(descriptor)
        with open(staged, "w", encoding="utf-8", newline=newline) as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        if expected_bytes is not None:
            with open(path, "rb") as live:
                if live.read() != expected_bytes:
                    raise _ManifestChanged()
        os.replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def _tree_digest(path) -> str:
    """Hash relative paths and contents, excluding generated directories."""
    digest = hashlib.sha256()
    paths = []

    def fail(error):
        raise error

    for root, directories, files in os.walk(path, onerror=fail):
        directories[:] = [name for name in directories
                          if not any(fnmatch.fnmatchcase(name, pattern)
                                     for pattern in _GENERATED_DIR_PATTERNS)]
        for name in files:
            if name == _DIGEST_FILE:
                continue
            full_path = os.path.join(root, name)
            relative = os.path.relpath(full_path, path).replace(os.sep, "/")
            paths.append((relative, full_path))
    for relative, full_path in sorted(paths):
        digest.update(relative.encode("utf-8") + b"\0")
        with open(full_path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _file_hash(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _installed_files(root) -> dict:
    """relative path -> full path of every file under root except our two bookkeeping files; links are not followed."""
    found = {}
    for directory, directories, files in os.walk(root):
        directories[:] = [name for name in directories if not _is_link(os.path.join(directory, name))]
        for name in files:
            full_path = os.path.join(directory, name)
            relative = os.path.relpath(full_path, root).replace(os.sep, "/")
            if relative not in (_DIGEST_FILE, _FILES_RECORD) and not _is_link(full_path):
                found[relative] = full_path
    return found


def _parent_dirs(relative: str) -> set:
    """'a/b/c.cs' -> {'a', 'a/b'}."""
    parts = relative.split("/")
    return {"/".join(parts[:i]) for i in range(1, len(parts))}


def _complete_digest(path) -> Optional[str]:
    """The stored source digest when it still matches the tree, else None."""
    try:
        with open(os.path.join(path, _DIGEST_FILE), encoding="utf-8") as stored:
            digest = stored.read()
        return digest if digest == _tree_digest(path) else None
    except Exception:
        return None


def _bring_editor_forward(project_path: str) -> None:
    try:
        if sys.platform != "darwin":
            logger.debug("[UnityMCP] Editor activation is only supported on macOS.")
            return
        pid = unity_project_presence.editor_pid(project_path)
        if pid is None:
            logger.debug("[UnityMCP] No editor found for %s.", project_path)
            return
        pid = int(pid)
        # Unity imports the autoconnect script only when the editor gains focus (measured 25 Oct 2026).
        # Two editors share one bundle: open -a picks the first; lsappinfo setfront fails in the background (measured 29 Oct 2026).
        script = ("ObjC.import('AppKit'); "
                  f"$.NSRunningApplication.runningApplicationWithProcessIdentifier({pid})"
                  ".activateWithOptions($.NSApplicationActivateAllWindows);")
        try:
            result = subprocess.run(["osascript", "-l", "JavaScript", "-e", script], timeout=5,
                                    env=build_spawn_env(), capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip() == "true":
                return
            logger.debug("[UnityMCP] PID activation failed for %s (rc=%s, output=%r); falling back to the bundle.",
                         pid, result.returncode, result.stdout)
        except Exception:
            logger.debug("[UnityMCP] PID activation failed for %s; falling back to the bundle.", pid, exc_info=True)
        result = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], timeout=5, capture_output=True, text=True,
                                env=build_spawn_env())
        executable = result.stdout.strip()
        if result.returncode != 0 or ".app/" not in executable:
            logger.debug("[UnityMCP] Could not resolve an editor bundle for PID %s.", pid)
            return
        bundle = executable.split(".app/", 1)[0] + ".app"
        result = subprocess.run(["open", "-a", bundle], timeout=5, env=build_spawn_env())
        if result.returncode != 0:
            logger.debug("[UnityMCP] Editor activation failed for %s.", bundle)
    except Exception:
        logger.debug("[UnityMCP] Could not bring the editor forward.", exc_info=True)


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
        self._autoconnect_workspaces: set[str] = set()
        self.active_workspace: Optional[str] = None
        # None until a toggle sets it: an off-write then keeps the language already on disk.
        self.language: Optional[str] = None
        self.enabled = False  # MCP is intended on (true while the server is still starting)
        self.generation = 0  # bumped on every start/stop so stale readiness tasks can detect a restart
        self._mcp_lock: Optional[asyncio.Lock] = None
        # port_occupant() kimlik probe'u cache'i (perf). Değer artık bool DEĞİL:
        # "biri dinliyor mu" ile "o biri BİZ miyiz" ayrı sorular ve ikincisi
        # cevaplanmadan toggle yeşile dönüyordu.
        self._identity_cache_ts = 0.0
        self._identity_cache_val = Occupant(ServerIdentity.NONE, "henüz ölçülmedi")
        # MCP sunucusunun yerel REST uçlarının (/api/*) paylaşımlı sırrı. Her
        # start_server'da yenilenir; sunucu bizim değilse None kalır.
        self.local_api_token: Optional[str] = None
        # start_server'ın başarısızlık SEBEBİ. Port çakışmasında kullanıcıya
        # gösterilecek metin buradan geliyor; sadece "başlatılamadı" demek
        # kullanıcıyı çözümsüz bırakıyordu.
        self.last_error: Optional[str] = None
        # Supervision state (see supervise_once). `enabled` is the "user wants it on" flag.
        self._health_fails = 0
        self._restart_times: list[float] = []
        self._gave_up = False
        self._started_at = float("-inf")
        # Set while the supervisor stops a dead server to restart it: stop_server then leaves
        # autoconnect.json alone, or Unity reads autoStart=false and never resumes (4 Nov 2026).
        self._restarting = False
        # Why install_package last returned False (shown to the user by the toggle route).
        self.last_install_error: Optional[str] = None
        # Windows dialog probe (unity-mcp/Server/src/transport/unity_dialog_probe.py), loaded once.
        self._dialog_probe = None
        self._dialog_cache: dict[str, dict] = {}  # normalised workspace path -> {project, value, time}
        self._dialog_inflight: dict[str, asyncio.Future] = {}
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

    def _ensure_writable_resources(self) -> None:
        """Copy frozen resources to writable storage, refreshing by source content.

        The packager does not preserve mtimes; mtime checks left 284 stale files
        in a measured install. Stage each refresh before replacing the old copy.
        Keep the previous destination usable on failure. Never raises.
        """
        if not getattr(sys, "frozen", False):
            return  # Dev: kaynak repo zaten yazılabilir

        def remove_leftover(path):
            def retry(operation, target, error):
                os.chmod(target, os.stat(target).st_mode | stat.S_IWRITE)
                operation(target)

            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, onerror=retry)
            except Exception as error:
                logger.warning("[UnityMCP] Could not remove leftover %s: %s", path, error)

        base = os.path.join(os.path.expanduser("~"), ".unity_architect_ai", "unity-mcp")
        targets = [("Server", "server_dir"), ("MCPForUnity", "local_mcp_source")]
        for sub, attr in targets:
            src = os.path.join(self.project_root, "unity-mcp", sub)
            dst = os.path.join(base, sub)
            suffix = f"-{os.getpid()}-{time.time_ns()}"
            staged, old = dst + ".new" + suffix, dst + ".old" + suffix
            try:
                backups = sorted((path for path in Path(base).glob(sub + ".old*") if path.is_dir()),
                                 key=lambda path: path.stat().st_mtime_ns, reverse=True)
                if backups and _complete_digest(dst) is None:
                    recovered = next((path for path in backups if _complete_digest(path) is not None), None)
                    if recovered is not None:
                        if os.path.exists(dst):
                            os.replace(dst, old)
                        os.replace(recovered, dst)
                if not os.path.isdir(src):
                    if _complete_digest(dst) is not None:
                        setattr(self, attr, dst)
                    continue
                source_digest = _tree_digest(src)
                if _complete_digest(dst) != source_digest:
                    shutil.copytree(src, staged)
                    with open(os.path.join(staged, ".gamachine-source-digest"), "w", encoding="utf-8") as stored:
                        stored.write(source_digest)
                    if _complete_digest(staged) is None:
                        raise OSError("Staged resources do not match the source digest")
                    if os.path.isdir(dst):
                        old = dst + f".old-{os.getpid()}-{time.time_ns()}"
                        os.replace(dst, old)
                    os.replace(staged, dst)
                if _complete_digest(dst) is None:
                    raise OSError("Destination resources are incomplete")
                setattr(self, attr, dst)
                # Only retire backups after a usable destination has been retained or promoted.
                for pattern in (sub + ".old*", sub + ".new*"):
                    for leftover in Path(base).glob(pattern):
                        remove_leftover(leftover)
            except Exception as e:
                logger.warning(f"[UnityMCP] {sub} yazılabilir dizine kopyalanamadı: {e}")
                remove_leftover(staged)
                if not os.path.isdir(dst) and os.path.isdir(old):
                    try:
                        os.replace(old, dst)
                    except Exception:
                        logger.warning("[UnityMCP] Could not restore %s", dst, exc_info=True)
                if _complete_digest(dst) is not None:
                    setattr(self, attr, dst)
                elif getattr(self, attr) == dst:
                    # An earlier refresh selected dst, but it is no longer complete.
                    if os.path.isdir(src):
                        setattr(self, attr, src)
                    else:
                        logger.warning("[UnityMCP] %s is incomplete and no bundled source is available", dst)
        self.unity_mcp_repo = f"file:{self.local_mcp_source}"

    # ─── Subprocess Yönetimi ─────────────────────────────────────────────────

    @property
    def mcp_lock(self) -> asyncio.Lock:
        """Serializes toggle and workspace-switch requests; created lazily inside the running loop."""
        if self._mcp_lock is None:
            self._mcp_lock = asyncio.Lock()
        return self._mcp_lock

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
        # Windows is flat (uv/uvx.exe). macOS ships Apple silicon only (uv/darwin-arm64/):
        # Intel Macs are not supported, so there is no x64 subdirectory to choose.
        if sys.platform == "win32":
            bundled = os.path.join(self.project_root, "uv", "uvx.exe")
        else:
            bundled = os.path.join(self.project_root, "uv", "darwin-arm64", "uvx")
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
        # Freshness comes from [tool.uv] cache-keys in unity-mcp/Server/pyproject.toml;
        # --no-cache is not used because it re-downloads every dependency on each launch.
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
        log_path = _server_log_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
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
            self._started_at = time.monotonic()
            self._health_fails = 0
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

        if self._restarting:
            return
        try:
            # reset_autoconnect_off drops only known-off workspaces, so the next stop retries failures.
            for workspace_path in tuple(self._autoconnect_workspaces):
                self.reset_autoconnect_off(workspace_path)
        except Exception as e:
            logger.debug("[UnityMCP] Autoconnect cleanup skipped: %s", e)

    # ─── Supervision ─────────────────────────────────────────────────────────

    def reset_supervision(self) -> None:
        """A user toggle starts the restart budget over."""
        self._restart_times.clear()
        self._health_fails = 0
        self._gave_up = False

    @staticmethod
    def _log_tail(lines: int = 20) -> str:
        try:
            with open(_server_log_path(), "r", encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-lines:]).rstrip()
        except OSError as e:
            return f"(log unreadable: {e})"

    async def supervise_loop(self) -> None:
        while True:
            await asyncio.sleep(_SUPERVISE_INTERVAL_S)
            try:
                await self.supervise_once()
            except Exception:
                logger.warning("[UnityMCP] Supervision pass failed.", exc_info=True)

    async def supervise_once(self) -> None:
        """One supervision pass: restart our server if the user wants it on and it died.

        Dead = our uvx process exited or never started (a failed restart), or /health failed
        _HEALTH_FAILS_TO_RESTART passes in a row. A user stop clears `enabled`, so it never
        restarts; a foreign process on the port is left alone.
        """
        if not self.enabled or self._starting or self._gave_up:
            return
        process = self.process
        exit_code = process.poll() if process is not None else None
        if process is not None and exit_code is None:
            if await self.check_health():
                self._health_fails = 0
                return
            if time.monotonic() - self._started_at < _STARTUP_GRACE_S:
                return
            self._health_fails += 1
            if self._health_fails < _HEALTH_FAILS_TO_RESTART:
                return
            reason = f"/health failed {self._health_fails} checks in a row (process still alive)"
        else:
            reason = (f"process exited with code {exit_code}" if process is not None
                      else "no server process (the last start failed)")
            occupant = await asyncio.to_thread(self.port_occupant)
            if occupant.identity == ServerIdentity.FOREIGN:
                return
            if occupant.identity == ServerIdentity.OURS and await self.check_health():
                return  # the uvx wrapper is gone but the server it started still answers

        async with self.mcp_lock:
            # A toggle may have run while we waited for the lock: it replaces the process.
            if not self.enabled or self._starting or self._gave_up or self.process is not process:
                return
            if process is not None and process.poll() is None and await self.check_health():
                self._health_fails = 0  # recovered while we waited
                return
            now = time.monotonic()
            self._restart_times = [t for t in self._restart_times if now - t < _RESTART_WINDOW_S]
            if len(self._restart_times) >= _RESTART_CAP:
                self._gave_up = True
                self.last_error = _GAVE_UP_MESSAGES["en" if self.language == "en" else "tr"]
                logger.error(f"[UnityMCP] Server died again ({reason}); {_RESTART_CAP} restarts within "
                             f"{_RESTART_WINDOW_S // 60} min used up, not restarting.")
                return
            self._restart_times.append(now)
            logger.warning(f"[UnityMCP] Server is dead: {reason}. Restart {len(self._restart_times)}/{_RESTART_CAP}. "
                           f"Last log lines:\n{self._log_tail()}")
            self._restarting = True
            try:
                await self._run_to_completion(self.stop_server)
            finally:
                self._restarting = False
            if not await self._run_to_completion(self.start_server):
                logger.error(f"[UnityMCP] Restart failed: {self.last_error}")
                return
            logger.info("[UnityMCP] Server restarted.")
            workspace, generation = self.active_workspace, self.generation
        # Outside the lock: the readiness wait may take a minute and must not hold a toggle back.
        if workspace:
            await self.write_autoconnect_when_ready(workspace, generation=generation)

    @staticmethod
    async def _run_to_completion(function):
        """to_thread whose cancellation waits for the thread: shutdown cancels the supervisor and then
        calls stop_server, which must not run before an in-flight start_server has spawned uvx."""
        future = asyncio.ensure_future(asyncio.to_thread(function))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await asyncio.wait([future])
            raise

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

    def _load_dialog_probe(self):
        if self._dialog_probe is None:
            path = os.path.join(self.server_dir, "src", "transport", "unity_dialog_probe.py")
            spec = importlib.util.spec_from_file_location("gamachine_unity_dialog_probe", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._dialog_probe = module
        return self._dialog_probe

    async def _probe_dialog(self, key: str, project: str) -> Optional[dict]:
        """One probe run for a workspace; its cache entry is stored only after it returns, under that workspace."""
        dialog = None
        try:
            probe = self._load_dialog_probe()
            found = await asyncio.to_thread(lambda: probe.dialogs_for_project(probe.find_unity_dialogs(), project))
            if found:
                dialog = {"title": str(found[0].get("title", "")), "buttons": list(found[0].get("buttons") or [])}
        except Exception:
            logger.debug("[UnityMCP] Unity dialog probe failed.", exc_info=True)
        self._dialog_cache[key] = {"project": project, "value": dialog, "time": time.monotonic()}
        return dialog

    async def _blocking_dialog(self) -> Optional[dict]:
        """A native Unity dialog of the active project, which holds the connection silently (measured 2 Nov 2026)."""
        if sys.platform != "win32" or not self.enabled or not self.active_workspace:
            return None
        # The editor title carries only the folder name, but two workspaces may share it: cache per full path.
        key = os.path.normcase(os.path.realpath(self.active_workspace))
        project = os.path.basename(os.path.normpath(self.active_workspace))
        cached = self._dialog_cache.get(key)
        if cached and time.monotonic() - cached["time"] < 2.0:
            return cached["value"]
        # Concurrent polls of one workspace share a single probe; shield keeps it alive if one poll is cancelled.
        probe = self._dialog_inflight.get(key)
        if probe is None:
            probe = self._dialog_inflight[key] = asyncio.ensure_future(self._probe_dialog(key, project))
            probe.add_done_callback(lambda _done: self._dialog_inflight.pop(key, None))
        return await asyncio.shield(probe)

    async def get_status(self) -> dict:
        """_status() plus `dialog`: {title, buttons} of a Unity dialog blocking the connection.

        Present only while such a dialog is up (absent = null), like reason_code: the status
        dict keeps its exact shape for every caller that compares it whole.
        """
        result = await self._status()
        if result["status"] != UnityMCPStatus.CONNECTED:
            dialog = await self._blocking_dialog()
            if dialog is not None:
                result["dialog"] = dialog
        return result

    async def _status(self) -> dict:
        """
        Tam durum bilgisini döner:
        {
          "status": "off" | "blocked" | "starting" | "running" | "connected",
          "pid": int | None,
          "port": 8080,
          "instances": [...],
          "reason": str | None,  # blocked only
          "reason_code": str,  # present only for running bridge errors and the supervisor's
                               # gave-up "blocked" state ("supervisor_gave_up"); translated by the renderer
          "reason_lines": list[str]  # present only for running bridge errors; raw compiler diagnostics
        }

        `reason` alanı bilerek BU uçtan dönüyor: sebep eskiden yalnız toggle
        POST'unun 500 gövdesinde vardı, yani kullanıcı toggle'a basmadan önce
        durumu asla öğrenemiyordu.
        """
        if self.enabled and self._gave_up:
            # Before the liveness check: a stuck process that is still alive would read "starting",
            # which disables the toggle. The renderer shows `reason` for "blocked".
            return {"status": UnityMCPStatus.BLOCKED, "pid": self.process.pid if self.process else None,
                    "port": self.mcp_port, "instances": [], "reason": self.last_error,
                    "reason_code": "supervisor_gave_up"}
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
        reason_code = None
        reason_lines = []
        if unity_status == UnityMCPStatus.RUNNING:
            try:
                errors = bridge_compile_errors(editor_log_path())
                if errors:
                    reason_code = "bridge_compile_failed"
                    reason_lines = errors
            except Exception:
                logger.debug("[UnityMCP] Could not read bridge compile errors.", exc_info=True)
        result = {
            "status": unity_status,
            "pid": pid,
            "port": self.mcp_port,
            "instances": instances,
            "reason": None,
        }
        if reason_code is not None:
            result.update(reason_code=reason_code, reason_lines=reason_lines)
        return result

    # ─── Unity Paket Kurulumu ────────────────────────────────────────────────

    async def write_autoconnect_when_ready(self, workspace_path: str, timeout: int = 60,
                                           generation: Optional[int] = None) -> bool:
        """
        Server /health cevap verene kadar bekler, sonra autoconnect scriptini yazar.
        Bu sayede Unity plugin'i bağlanmaya çalıştığında server zaten hazırdır.
        is_running() yalnız süreç canlı mı diye bakar; port henüz dinlemiyor olabilir ve Unity
        erken yazılmış dosyada ServerReachable başarısız olup otomatik bağlanmayı kalıcı kapatır.
        """
        for _ in range(timeout * 2):
            await asyncio.sleep(0.5)
            if self.is_running() and await self.check_health():
                await asyncio.sleep(1)  # Port açık ama HTTP handler tam init olmamış olabilir
                # A disable or project switch must not be undone by an older readiness task.
                if not self.is_running() or (
                    generation is not None and generation != self.generation
                ) or (
                    self.active_workspace is not None and self.active_workspace != workspace_path
                ):
                    return False
                self._write_autoconnect_settings(workspace_path, auto_start=True)
                _bring_editor_forward(workspace_path)
                logger.info("[UnityMCP] Server hazır — autoconnect scripti yazıldı.")
                return True
        logger.warning("[UnityMCP] Server %ds içinde hazır olmadı, autoconnect scripti yazılmadı.", timeout)
        return False

    def install_package(self, workspace_path: str, write_autoconnect: bool = True) -> bool:
        """
        Puts the unity-mcp package into the Unity project without touching its tracked files.
        1. Packages/com.coplaydev.unity-mcp/ = a copy of our MCPForUnity (embedded package;
           Unity records only a machine-independent "file:com.coplaydev.unity-mcp" lock entry).
        2. Only once that copy is in place: drop the manifest entry and the Assets script older
           Gamachines wrote (a failed copy leaves manifest.json and Assets exactly as they were).
        3. The package folder and UserSettings/Gamachine/ are excluded in .git/info/exclude.
        The package's GamachineAutoConnect.cs applies UserSettings/Gamachine/autoconnect.json.
        """
        self.last_install_error = None
        if not workspace_path:
            return False
        if not _is_unity_project(workspace_path):
            logger.debug("[UnityMCP] %s is not a Unity project (no Assets and ProjectSettings); not touched.",
                         workspace_path)
            self.last_install_error = f"{workspace_path} is not a Unity project (no Assets and ProjectSettings)."
            return False
        self._ensure_writable_resources()  # frozen: MCPForUnity'yi yazılabilir dizine taşı
        manifest_path = os.path.join(workspace_path, "Packages", "manifest.json")
        if not os.path.exists(manifest_path):
            logger.error(f"[UnityMCP] manifest.json bulunamadı: {manifest_path}")
            self.last_install_error = f"Packages/manifest.json was not found in {workspace_path}."
            return False
        try:
            _require_plain(workspace_path, *_legacy_paths(workspace_path), *_settings_paths(workspace_path),
                           *_package_paths(workspace_path))
            source = self._manifest_dependency(manifest_path)
            if source is not None and not self._is_our_manifest_source(source):
                # An embedded copy would override the user's own choice of the package.
                raise _Declined(f"manifest.json uses another {_PACKAGE_NAME} ({source}); "
                                "Gamachine left it as it is.")
            self._sweep_staging(workspace_path)
            self._ensure_git_exclude(workspace_path)
            # The bootstrap does nothing without the data file, so it must exist before the package
            # loads; a later change of it is what reloads the editor.
            if write_autoconnect:
                self._write_autoconnect_settings(workspace_path, auto_start=True)
            else:
                self._write_autoconnect_settings(workspace_path, auto_start=False, create_only=True)
            self._ensure_package_copy(workspace_path)
            if not self._migrate_manifest(manifest_path):
                self.last_install_error = (f"manifest.json uses another {_PACKAGE_NAME}; "
                                           "Gamachine left it as it is.")
                return False
            self._remove_legacy_script(workspace_path)
            logger.info(f"[UnityMCP] Paket + auto-connect ayarları kuruldu: {workspace_path}")
            return True
        except _Declined as reason:
            logger.warning("[UnityMCP] %s", reason)
            self.last_install_error = str(reason)
            return False
        except _Refused as error:
            logger.error("[UnityMCP] Package install refused: %s", error)
            self.last_install_error = f"{error}; Gamachine did not write there."
            return False
        except Exception as e:
            logger.error(f"[UnityMCP] Paket kurulumu hatası: {e}")
            self.last_install_error = f"Package install failed: {e}"
            return False

    def _is_our_manifest_source(self, value) -> bool:
        """A `file:` value Gamachine wrote: this install's sources, the writable copy, or an older Gamachine checkout."""
        if not isinstance(value, str) or not value.startswith("file:"):
            return False
        path = value[len("file:"):].replace("\\", "/").rstrip("/")
        if not os.path.isabs(path):
            return False
        path = os.path.normpath(path)
        ours = {os.path.normcase(os.path.abspath(source)) for source in (
            self.local_mcp_source,
            os.path.join(self.project_root, "unity-mcp", "MCPForUnity"),
            os.path.join(os.path.expanduser("~"), ".unity_architect_ai", "unity-mcp", "MCPForUnity"),
        )}
        if os.path.normcase(path) in ours:
            return True
        # The manifest comes from the project, so never probe a network path.
        if _is_network_path(path):
            return False
        # Any other clone is the user's own, unless a Gamachine backend sits at its root.
        parent = os.path.dirname(path)
        if os.path.normcase(os.path.basename(path)) != os.path.normcase("MCPForUnity") \
                or os.path.normcase(os.path.basename(parent)) != os.path.normcase("unity-mcp"):
            return False
        return os.path.isfile(os.path.join(os.path.dirname(parent), "Backend", "app", "unity_ai_mcp",
                                           "unity_mcp_manager.py"))

    @staticmethod
    def _manifest_dependency(manifest_path: str):
        with open(manifest_path, "rb") as source:
            dependencies = json.loads(source.read()).get("dependencies")
        return dependencies.get(_PACKAGE_NAME) if isinstance(dependencies, dict) else None

    def _migrate_manifest(self, manifest_path: str) -> bool:
        """Removes only a dependency Gamachine wrote, keeping the file's formatting; False = not removed."""
        for attempt in range(3):
            with open(manifest_path, "rb") as source:
                original_bytes = source.read()
            manifest = json.loads(original_bytes)
            dependencies = manifest.get("dependencies")
            value = dependencies.get(_PACKAGE_NAME) if isinstance(dependencies, dict) else None
            if value is None:
                return True
            if not self._is_our_manifest_source(value):
                logger.warning("[UnityMCP] %s comes from %r in the manifest; it is left as it is.",
                               _PACKAGE_NAME, value)
                return False
            del dependencies[_PACKAGE_NAME]
            original_text = original_bytes.decode("utf-8-sig")
            indent = re.search(r"^([ \t]+)\S", original_text, re.M)
            text = json.dumps(manifest, indent=indent.group(1) if indent else 2, ensure_ascii=False)
            if original_text.endswith("\n"):
                text += "\n"
            if "\r\n" in original_text:
                text = text.replace("\n", "\r\n")
            if original_bytes.startswith(b"\xef\xbb\xbf"):
                text = "﻿" + text
            try:
                _atomic_write_text(manifest_path, text, expected_bytes=original_bytes, newline="")
                logger.info("[UnityMCP] Removed the machine-specific %s entry from %s.", _PACKAGE_NAME, manifest_path)
                return True
            except _ManifestChanged:
                logger.warning("[UnityMCP] Manifest changed during installation (attempt %s/3).", attempt + 1)
        logger.error("[UnityMCP] Manifest kept changing; installation cancelled without replacing it.")
        return False

    @staticmethod
    def _remove_legacy_script(workspace_path: str, only_if_autostart: bool = False) -> bool:
        """Deletes the Assets script (and .meta) an older Gamachine wrote; only_if_autostart limits it to one that connects on load."""
        script_path = _legacy_paths(workspace_path)[2]
        try:
            _require_plain(workspace_path, *_legacy_paths(workspace_path))
        except _Refused as error:
            logger.error("[UnityMCP] Old autoconnect script left in place: %s", error)
            return False
        if not os.path.isfile(script_path):
            return False
        with open(script_path, "r", encoding="utf-8-sig", errors="replace") as script:
            text = script.read()
        if not text.startswith(_LEGACY_SCRIPT_HEADER):
            return False
        if only_if_autostart and '"MCPForUnity.AutoStartOnLoad", true' not in text:
            return False
        os.remove(script_path)
        if os.path.isfile(script_path + ".meta"):
            os.remove(script_path + ".meta")
        logger.info("[UnityMCP] Removed the old Gamachine autoconnect script: %s", script_path)
        return True

    @staticmethod
    def _sweep_staging(workspace_path: str) -> None:
        """Removes staging folders of earlier installs (ours: package.new-*, package.old-*), nowhere but UserSettings/Gamachine."""
        stage_dir = os.path.join(workspace_path, "UserSettings", "Gamachine")
        if not os.path.isdir(stage_dir):
            return
        installed = os.path.lexists(os.path.join(workspace_path, *_PACKAGE_DIR))
        for name in os.listdir(stage_dir):
            path = os.path.join(stage_dir, name)
            # An .old folder is the only copy left when a replace died halfway, so it goes only beside a package.
            if (name.startswith("package.new-") or (installed and name.startswith("package.old-"))) \
                    and os.path.isdir(path) and not _is_link(path):
                shutil.rmtree(path, ignore_errors=True)

    def _ensure_package_copy(self, workspace_path: str) -> None:
        """Packages/com.coplaydev.unity-mcp = our MCPForUnity. Raises _Declined when the folder is not ours
        or the user changed a file we copied; anything the user or Unity added is carried into a new copy."""
        target = os.path.join(workspace_path, *_PACKAGE_DIR)
        manifest_path = os.path.join(workspace_path, "Packages", "manifest.json")
        source = self.local_mcp_source
        source_digest = _tree_digest(source)
        changed = f"Packages/{_PACKAGE_NAME} was changed by hand; Gamachine left it as it is."
        extras = {}  # relative path -> full path of files that are not ours
        exists = os.path.lexists(target)
        if exists:
            record_path = os.path.join(target, _FILES_RECORD)
            if not os.path.isdir(target) or _is_link(record_path) or not os.path.isfile(record_path):
                raise _Declined(f"Packages/{_PACKAGE_NAME} was not made by Gamachine; Gamachine left it as it is.")
            installed = _installed_files(target)
            try:
                with open(record_path, encoding="utf-8") as stored:
                    record = json.load(stored)
                recorded, stored_digest = dict(record["files"]), record["sourceDigest"]
            except (OSError, ValueError, KeyError, TypeError):
                raise _Declined(changed)
            if any(path in installed and _file_hash(installed[path]) != digest
                   for path, digest in recorded.items()):
                raise _Declined(changed)
            missing = [path for path in recorded if path not in installed]
            extras = {path: full for path, full in installed.items() if path not in recorded}
            if stored_digest == source_digest and not missing:
                return
            if missing:
                logger.warning("[UnityMCP] %s is incomplete; copying the package again.", target)
        # Staged in our excluded settings folder, not in Packages/ where Unity would see a half copy.
        stage_dir = os.path.join(workspace_path, "UserSettings", "Gamachine")
        os.makedirs(stage_dir, exist_ok=True)
        suffix = f"-{os.getpid()}-{time.time_ns()}"
        staged = os.path.join(stage_dir, "package.new" + suffix)
        old = os.path.join(stage_dir, "package.old" + suffix)

        def skipped(directory, names):
            return [name for name in names if name in (_DIGEST_FILE, _FILES_RECORD)
                    or _is_link(os.path.join(directory, name))
                    or any(fnmatch.fnmatchcase(name, pattern) for pattern in _GENERATED_DIR_PATTERNS)]

        try:
            shutil.copytree(source, staged, ignore=skipped)
            if _tree_digest(staged) != source_digest:
                raise OSError("Staged package does not match the source digest")
            copied = {path: _file_hash(full) for path, full in _installed_files(staged).items()}
            # Casefolded on every platform so a case-only or file/directory clash never merges into the shipped copy.
            staged_files = {path.casefold() for path in copied}
            staged_paths = staged_files.union(*map(_parent_dirs, staged_files))
            clash = next((path for path in sorted(extras) if path.casefold() in staged_paths
                          or _parent_dirs(path.casefold()) & staged_files), None)
            if clash:
                raise _Declined(f"Packages/{_PACKAGE_NAME}/{clash} was added by hand and the new package "
                                "has a file at the same path; Gamachine left it as it is.")
            for path, full in extras.items():
                destination = os.path.join(staged, *path.split("/"))
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copy2(full, destination)
            with open(os.path.join(staged, _FILES_RECORD), "w", encoding="utf-8") as stored:
                json.dump({"sourceDigest": source_digest, "files": copied}, stored, indent=2)
            # The user may have set their own package in the manifest while the copy ran.
            current = self._manifest_dependency(manifest_path)
            if current is not None and not self._is_our_manifest_source(current):
                raise _Declined(f"manifest.json uses another {_PACKAGE_NAME} ({current}); "
                                "Gamachine left it as it is.")
            if exists:
                os.replace(target, old)
            try:
                os.replace(staged, target)
            except Exception:
                if exists and not os.path.lexists(target):
                    os.replace(old, target)
                raise
        except _Declined:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        except Exception as e:
            logger.error("[UnityMCP] Could not copy the package into %s: %s", target, e)
            shutil.rmtree(staged, ignore_errors=True)
            raise _Declined(f"Could not copy the package into {target}: {e}")
        shutil.rmtree(old, ignore_errors=True)
        logger.info("[UnityMCP] Package copied into %s.", target)

    @staticmethod
    def _ensure_git_exclude(workspace_path: str) -> bool:
        """Lists our two folders in the repo's local info/exclude; no git process, never .gitignore."""
        if not _is_unity_project(workspace_path):
            return False
        project = os.path.abspath(workspace_path)
        work_tree = project
        while True:
            dot_git = os.path.join(work_tree, ".git")
            if os.path.isdir(dot_git):
                git_dir = dot_git
                break
            if os.path.isfile(dot_git):
                with open(dot_git, encoding="utf-8") as pointer:
                    line = pointer.readline().strip()
                if not line.startswith("gitdir:"):
                    return False
                git_dir = os.path.normpath(os.path.join(work_tree, line[len("gitdir:"):].strip()))
                if not _trusted_git_dir(project, git_dir):
                    return False
                break
            parent = os.path.dirname(work_tree)
            if parent == work_tree:
                return False
            work_tree = parent
        # The gitdir the project points at is checked before commondir is followed, then the common dir too.
        _require_plain(git_dir, git_dir, os.path.join(git_dir, "info"), os.path.join(git_dir, "info", "exclude"))
        # A linked worktree's info/ lives in the common dir: git ignores <worktree gitdir>/info/exclude.
        common = os.path.join(git_dir, "commondir")
        if os.path.isfile(common):
            with open(common, encoding="utf-8") as pointer:
                git_dir = os.path.normpath(os.path.join(git_dir, pointer.readline().strip()))
            if not _trusted_git_dir(project, git_dir):
                return False
        if not os.path.isdir(git_dir):
            return False
        exclude = os.path.join(git_dir, "info", "exclude")
        _require_plain(git_dir, git_dir, os.path.dirname(exclude), exclude)
        relative = os.path.relpath(project, work_tree).replace(os.sep, "/")
        prefix = "" if relative == "." else "".join("\\" + c if c in "\\*?[" else c for c in relative) + "/"
        patterns = [f"/{prefix}Packages/{_PACKAGE_NAME}/", f"/{prefix}UserSettings/Gamachine/"]
        existing = ""
        if os.path.isfile(exclude):
            with open(exclude, encoding="utf-8", errors="replace", newline="") as current:
                existing = current.read()
        lines = {line.strip() for line in existing.splitlines()}
        missing = [pattern for pattern in patterns if pattern not in lines]
        if not missing:
            return True
        addition = "" if not existing or existing.endswith("\n") else "\n"
        if _EXCLUDE_MARKER not in lines:
            addition += _EXCLUDE_MARKER + "\n"
        addition += "".join(pattern + "\n" for pattern in missing)
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        with open(exclude, "a", encoding="utf-8", newline="") as output:
            output.write(addition)
        return True

    @staticmethod
    def _autoconnect_path(workspace_path: str) -> str:
        return os.path.join(workspace_path, "UserSettings", "Gamachine", "autoconnect.json")

    def _read_autoconnect(self, workspace_path: str) -> Optional[dict]:
        path = self._autoconnect_path(workspace_path)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        return data if isinstance(data, dict) else None

    def reset_autoconnect_off(self, workspace_path) -> bool:
        try:
            if not isinstance(workspace_path, str) or not workspace_path or not os.path.isdir(workspace_path):
                if isinstance(workspace_path, str):
                    self._autoconnect_workspaces.discard(workspace_path)
                return False
            if not _is_unity_project(workspace_path):
                self._autoconnect_workspaces.discard(workspace_path)
                return False
            # A failed install leaves the script of an older Gamachine, which would autostart against a dead server.
            legacy_removed = self._remove_legacy_script(workspace_path, only_if_autostart=True)
            try:
                data = self._read_autoconnect(workspace_path)
            except ValueError:
                data = None
            if not data or data.get("autoStart") is not True:
                self._autoconnect_workspaces.discard(workspace_path)
                return legacy_removed
            self._write_autoconnect_settings(workspace_path, auto_start=False)
            return True
        except Exception as e:
            logger.debug("[UnityMCP] Autoconnect reset skipped: %s", e)
            return False

    def _write_autoconnect_settings(self, workspace_path: str, auto_start: bool = False,
                                    create_only: bool = False):
        """
        Writes UserSettings/Gamachine/autoconnect.json, read by the package's GamachineAutoConnect.cs.
        auto_start=True → the editor connects when our server answers on the port.
        auto_start=False → the editor does not connect on load.
        Unity compiles a changed script only when its window is focused but notices this file while
        unfocused (measured 2 Nov 2026), so every changing value lives here.
        create_only writes the file only when it does not exist yet (exclusive create), so a late
        install can never overwrite a newer on/off write.
        Nothing is written for a folder that is not a Unity project or whose path crosses a link.
        """
        if not _is_unity_project(workspace_path):
            logger.debug("[UnityMCP] %s is not a Unity project; no autoconnect settings written.", workspace_path)
            return
        try:
            _require_plain(workspace_path, *_settings_paths(workspace_path))
        except _Refused as error:
            logger.error("[UnityMCP] Autoconnect settings not written: %s", error)
            return
        path = self._autoconnect_path(workspace_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        language = self.language if self.language in ("tr", "en") else None
        if language is None:
            try:
                previous = (self._read_autoconnect(workspace_path) or {}).get("language")
            except (OSError, ValueError):
                previous = None
            language = previous if previous in ("tr", "en") else "en"

        # Unity MCP eklentisi sistem PATH'inde uv/uvx arıyor; kullanıcının PC'sinde uv
        # kurulu olmasa da bizim gömülü uvx'imizi kullansın diye eklentinin uvx yol
        # override'ını (EditorPrefs "MCPForUnity.UvxPath") veriyoruz.
        uvx_path = self._get_uvx()
        if not (os.path.isabs(uvx_path) and os.path.isfile(uvx_path)):
            uvx_path = ""
        settings = {
            "autoStart": bool(auto_start),
            "port": self.mcp_port,
            "language": language,
            "uvxPath": uvx_path.replace("\\", "/"),
            # A new value on every write, so re-enabling always changes the file and reloads the editor.
            "revision": secrets.token_hex(8),
        }
        text = json.dumps(settings, indent=2) + "\n"
        if create_only:
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                return
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
                output.write(text)
        else:
            _atomic_write_text(path, text)
        if auto_start:
            self._autoconnect_workspaces.add(workspace_path)
        else:
            self._autoconnect_workspaces.discard(workspace_path)
        logger.info(f"[UnityMCP] Auto-connect ayarları yazıldı (auto_start={auto_start}): {path}")


# Global singleton
unity_mcp_manager = UnityMCPManager()
