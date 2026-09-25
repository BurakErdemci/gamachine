import logging
import os
import sys

# ── UTF-8 stdio (Windows fix) ─────────────────────────────────────────────
# Windows'ta GUI/subprocess olarak spawn edilen Python, stdout/stderr için locale
# code page'i (cp1252) kullanır → Türkçe karakter (ş, ğ, ı...) yazınca UnicodeEncodeError
# ile çöker. Bu HTTP loglarını, MCP stdio JSON-RPC'sini ve unityai CLI çıktısını etkiler.
# Her şeyden (subcommand dispatch, logging, print) ÖNCE stdio'yu UTF-8'e sabitle.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ── PATH augmentasyonu (GUI-launched app fix) ─────────────────────────────
# GUI'den (Finder/dmg, Explorer/NSIS) açılan uygulamalar minimal PATH alır —
# kullanıcının CLI'ları (claude/codex/gemini/agy npm-global veya ~/.local/bin'de)
# bu PATH'te olmadığı için bulunamaz ve spawn patlar (terminalden açınca çalışıyordu).
# Yaygın kurulum dizinlerini PATH'e ekle ki hem shutil.which (cli-availability)
# hem de CLI subprocess spawn'ları çalışsın.
if sys.platform == "win32":
    _appdata = os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
    _localappdata = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
    _extra_paths = [
        os.path.join(_appdata, "npm"),                              # npm -g (claude/codex/gemini)
        os.path.join(os.path.expanduser("~"), ".local", "bin"),     # uv/pipx (agy, uvx)
        os.path.join(_localappdata, "Microsoft", "WindowsApps"),    # winget shims
        os.path.join(_localappdata, "Programs"),
    ]
else:
    _extra_paths = [
        os.path.expanduser("~/.local/bin"),
        "/opt/homebrew/bin", "/opt/homebrew/sbin",
        "/usr/local/bin", "/usr/local/sbin",
        os.path.expanduser("~/.npm-global/bin"),
        os.path.expanduser("~/bin"),
    ]
_cur_path = os.environ.get("PATH", "").split(os.pathsep)
os.environ["PATH"] = os.pathsep.join(
    [p for p in _extra_paths if p and p not in _cur_path] + _cur_path
)

# ── Frozen binary subcommand dispatch ─────────────────────────────────────
# Paketlenmiş app'te venv/python yok; launcher scriptleri donmuş 'backend'
# binary'sini alt-komutlarla çağırır:
#   backend mcp-server [--workspace X]  → unity_ai_mcp.server.main (Claude/Codex MCP)
#   backend unityai <save-file|...>     → unityai_cli.main (agy köprüsü)
#   backend codex-mcp-bridge <http_url> → providers.codex_unitymcp_bridge (Codex stdio köprüsü)
#   backend agy-hook --state <file>     → agy_step_gate.main (agy step-mode PreToolUse hook)
# FastAPI/uvicorn app'i kurmadan erken dön — bu komutlar HTTP server başlatmaz.
if len(sys.argv) > 1 and sys.argv[1] in ("mcp-server", "unityai", "codex-mcp-bridge", "agy-hook"):
    _sub_mode = sys.argv[1]
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    if _sub_mode == "mcp-server":
        from unity_ai_mcp.server import main as _sub_main
        _sub_main()
        sys.exit(0)
    elif _sub_mode == "codex-mcp-bridge":
        # Codex 0.14x, yerel FastMCP streamable-HTTP MCP'yi bozdu (OAuth-discovery-first;
        # openai/codex #26955). unityMCP'yi Codex'e stdio köprüsüyle veriyoruz.
        from providers.codex_unitymcp_bridge import main as _sub_main
        _sub_main()
        sys.exit(0)
    elif _sub_mode == "agy-hook":
        # Runs on every gated agy tool call, so it must not import providers/
        # (that package pulls in every provider SDK, 3.3 s measured).
        from agy_step_gate import main as _sub_main
        sys.exit(_sub_main())
    else:  # unityai
        from unityai_cli import main as _sub_main
        sys.exit(_sub_main())

from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

# .env'yi diğer modüller import edilmeden önce yükle
load_dotenv(Path(__file__).parent.parent / ".env")

_REQUIRE_LOCAL_APP_TOKEN = os.environ.get("REQUIRE_LOCAL_APP_TOKEN", "").lower() in {
    "1", "true", "yes", "on",
}
if _REQUIRE_LOCAL_APP_TOKEN and not os.environ.get("LOCAL_APP_TOKEN"):
    raise RuntimeError(
        "LOCAL_APP_TOKEN is required when REQUIRE_LOCAL_APP_TOKEN is enabled."
    )

# Token'ı 0600 bir dosyaya bırak: MCP sunucusunu CLI'lar başlatıyor, bizim
# ortamımızı miras almıyorlar ve sırrı config/argv üzerinden taşımak onu
# okunabilir kılıyordu (bkz. local_token_file modül docstring'i).
from local_token_file import write_local_app_token, token_path  # noqa: E402

# ⚠️ Dönüş DEĞERİ okunuyor (bulgu S2). Eskiden yutuluyordu ve bu, sessiz bir
# yarım-çalışma bırakıyordu: backend'in KENDİ süreci token'ı ortamdan okuduğu
# için sağlıklı görünüyor, ama dosyadan okuyan çocuklar (MCP sunucusu) sırrı
# hiç bulamıyordu. Kullanıcıya görünen tek belirti "Unity araçları çalışmıyor".
#
# `False` artık masum bir G/Ç hatası da değil: yazma yolu sabit bağ ve
# yönlendirme kontrolünden geçiyor (`safe_paths._dogrula_kimlik`), yani bu dönüş
# "token yolunda BAŞKASININ yerleştirdiği bir şey var" anlamına da gelebiliyor.
#
# Şiddet ortama göre: kimlik doğrulama ZORUNLU kılınmışsa açılış reddediliyor —
# vaat edilen kapıyı kuramadan açılmak, kapıyı hiç vaat etmemekten kötü.
# Zorunlu değilse (dev) açılışı kırmıyoruz, ama sessiz de kalmıyoruz.
if not write_local_app_token(os.environ.get("LOCAL_APP_TOKEN", "")):
    _mesaj = (
        f"Yerel uygulama token'ı {token_path()} dosyasına yazılamadı. "
        "MCP sunucusu gibi ayrı başlatılan süreçler sırrı bulamayacak."
    )
    if _REQUIRE_LOCAL_APP_TOKEN:
        raise RuntimeError(_mesaj)
    logging.getLogger(__name__).error("%s Ayrıntı için önceki [local-token] satırına bakın.", _mesaj)

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import DatabaseManager
from routes import (
    create_analysis_router,
    create_auth_router,
    create_config_router,
    create_conversation_router,
    create_workspace_router,
    create_lsp_router,
    create_mcp_router,
    create_transcribe_router,
)


# Root logger seviyesini explicit INFO yap
logging.basicConfig(level=logging.INFO)
logging.root.setLevel(logging.INFO)

# Sır maskesi log işleyicilerine burada takılıyor — `basicConfig`'ten HEMEN
# sonra, çünkü o çağrı kök işleyiciyi yaratıyor ve maske ondan önce takılamaz.
# Çağrı yerlerine tek tek `redact_secrets` eklemek yerine tek nokta seçildi:
# denetimde iki sağlayıcının hata yolunda çağrının HİÇ olmadığı bulundu ve
# unutulan çağrı, olmayan korumadır (bkz. secret_redaction docstring).
from secret_redaction import install_log_redaction  # noqa: E402

install_log_redaction()

logger = logging.getLogger(__name__)

class _SuppressPollingEndpoints(logging.Filter):
    """Yüksek frekanslı polling endpoint loglarını filtreler (CPU & log spam azaltma)."""
    _SUPPRESS = ("/mcp-pending", "/health")
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(ep in msg for ep in self._SUPPRESS)

logging.getLogger("uvicorn.access").addFilter(_SuppressPollingEndpoints())

# MCP server subprocess'leri için ANTIGRAVITY_URL'yi şimdiden set et
# (PORT env var Electron tarafından dinamik olarak geçilir)
_host = os.environ.get("HOST", "127.0.0.1")
_port = os.environ.get("PORT", "8000")
os.environ["ANTIGRAVITY_URL"] = f"http://{_host}:{_port}"  # Her başlatmada güncelle


def _resolve_db_path() -> str:
    db_path = os.environ.get("DB_PATH")
    if db_path:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        return db_path

    home_dir = str(Path.home())
    db_folder = os.path.join(home_dir, ".unity_architect_ai")
    os.makedirs(db_folder, exist_ok=True)
    return os.path.join(db_folder, "unity_master_v3.db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Backend başlarken: 8080'de geçen oturumdan kalmış KENDİ MCP sunucumuz varsa
    # temizle. Eskiden porttaki her LISTEN sahibi öldürülüyordu; 8080 çok yaygın
    # bir port olduğu için kullanıcının alakasız bir dev server'ı da haber
    # verilmeden ölüyordu. Artık kimliği doğrulanan süreç öldürülüyor, doğrulanamayan
    # yalnız loglanıyor — gerekçe ve yöntem için bkz. mcp_port_guard modül docstring'i.
    try:
        from unity_ai_mcp.mcp_port_guard import DEFAULT_MCP_PORT, terminate_port_listeners
        cleanup = terminate_port_listeners(DEFAULT_MCP_PORT)
        for pid in cleanup.killed:
            logger.info(f"[Startup] Orphan MCP süreci temizlendi (PID: {pid})")
        if cleanup.refused:
            logger.warning(f"[Startup] {cleanup.message}")
    except Exception as e:
        logger.debug(f"[Startup] Port temizleme atlandı: {e}")

    yield

    # Backend kapanınca Unity MCP subprocess'i de durdur
    try:
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        from tools.unity_mcp_tools import unload_unity_tools
        unity_mcp_manager.stop_server()
        unload_unity_tools()
        logger.info("[Shutdown] Unity MCP sunucusu durduruldu.")
    except Exception as e:
        logger.warning(f"[Shutdown] Unity MCP durdurulamadı: {e}")

    # Canlı Claude/Codex SDK session'larını kapat (subprocess sızdırma önlemi)
    try:
        from providers.claude_sdk_session import close_all_sessions as _close_claude_all
        await _close_claude_all()
        from providers.codex_session import close_all_sessions as _close_codex_all
        await _close_codex_all()
        from providers.agy_session import close_all_sessions as _close_agy_all
        await _close_agy_all()
        logger.info("[Shutdown] Claude/Codex/agy session'ları kapatıldı.")
    except Exception as e:
        logger.warning(f"[Shutdown] session'lar kapatılamadı: {e}")

    # OmniSharp sidecar'ı durdur (subprocess sızdırma önlemi)
    try:
        from omnisharp.omnisharp_manager import get_omnisharp_manager
        await get_omnisharp_manager().stop()
        logger.info("[Shutdown] OmniSharp sidecar durduruldu.")
    except Exception as e:
        logger.warning(f"[Shutdown] OmniSharp durdurulamadı: {e}")


db_path = _resolve_db_path()

# İnteraktif API dökümanı (/docs, /redoc, /openapi.json) yalnız KAYNAKTAN
# koşarken açık. Sebebi: bu üç uç her ucun adını ve gövde şemasını kimliksiz
# açıklıyor — veri vermiyor, yan etkisi yok, ama makinedeki her sürece hazır bir
# saldırı yüzeyi haritası veriyor. Geliştirirken faydası gerçek, dağıtılan üründe
# yok.
#
# Gösterge PyInstaller'ın `sys.frozen` işareti. İlk hâli "LOCAL_APP_TOKEN var mı"
# idi ve ÖLÇÜLDÜ Kİ İKİ YÖNDE DE YANLIŞTI (2026-07-27 denetimi):
#   - background.ts token'ı DEV'DE DE üretip veriyor → geliştiricinin dökümanı
#     kapanıyordu, yani korunmak istenen kullanım bozuluyordu;
#   - donmuş binary Electron sarmalayıcısı olmadan doğrudan çalıştırılınca token
#     olmuyor → paketlenmiş süreç dökümanın tamamını açıyordu.
# Token bir İSTEK KİMLİĞİ, dağıtım biçimi göstergesi değil. `sys.frozen` ise
# doğrudan sorulan soruyu cevaplıyor: bu ikili paketlenmiş mi?
_DOCS_ENABLED = not getattr(sys, "frozen", False)

app = FastAPI(
    title="Gamachine",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)
db = DatabaseManager(db_path=db_path)
PROGRESS_STORE = {}

from agentic import approval_mode  # noqa: E402

approval_mode.bind_store(db)

_ALLOWED_ORIGINS = [
    "http://localhost:8888",    # Nextron dev renderer
    "http://127.0.0.1:8888",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "app://.",                  # Electron production scheme
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Session-Token"],
)

app.include_router(create_auth_router(db))
app.include_router(create_config_router(db))
app.include_router(create_analysis_router(db))
app.include_router(create_workspace_router(db))
app.include_router(create_lsp_router(db))
app.include_router(create_conversation_router(db, PROGRESS_STORE))
app.include_router(create_mcp_router())
app.include_router(create_transcribe_router())


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "gamachine"}





def _read_ui_secret_from_stdin(timeout_s: float = 5.0) -> None:
    """Electron main writes the UI secret as the first stdin line and closes the pipe.

    Stdin, not env or a file: the Unity MCP server and model-run children inherit
    the environment and can read the token file, and the secret exists precisely
    so that they cannot flip the approval mode. Read before uvicorn starts, i.e.
    before any child is spawned; the pipe is empty afterwards.
    """
    if os.environ.pop("GAMACHINE_UI_SECRET_STDIN", "") != "1" or sys.stdin is None:
        return
    import threading

    box: list = []

    def _read() -> None:
        try:
            box.append(sys.stdin.readline())
        except Exception as exc:
            box.append(exc)

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(timeout_s)
    line = box[0] if box and isinstance(box[0], str) else ""
    if line.strip():
        approval_mode.set_ui_secret(line.strip())
        logger.info("[approval-mode] UI secret received from Electron main.")
    else:
        logger.error("[approval-mode] UI secret expected on stdin but not received; "
                     "approval-mode writes will be refused.")


if __name__ == "__main__":
    _read_ui_secret_from_stdin()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    # MCP server subprocess'leri bu URL'yi kullanır
    os.environ["ANTIGRAVITY_URL"] = f"http://{host}:{port}"
    # Opt-in autoreload, for the Docker dev container where the source is bind
    # mounted: without it a host edit reaches the container's filesystem and
    # still does not reach the running process, which was the whole point of
    # mounting it. Off by default and refused in a frozen build, because reload
    # NEEDS the import string and that is exactly what PyInstaller cannot
    # resolve (see below).
    if (os.environ.get("UVICORN_RELOAD", "").lower() in {"1", "true", "yes", "on"}
            and not getattr(sys, "frozen", False)):
        uvicorn.run("main:app", host=host, port=port, reload=True)
        raise SystemExit(0)
    # app objesini doğrudan ver — string "main:app" frozen binary'de import edilemiyor
    # ("Could not import module 'main'"). reload=False olduğu için obje geçişi sorunsuz.
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=False
    )
