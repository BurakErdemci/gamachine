import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from fastapi import APIRouter, Header, HTTPException

from auth_utils import _check_token, get_current_user, require_user
from providers import model_catalog
from schemas import AIConfigRequest, APIKeySaveRequest


logger = logging.getLogger(__name__)


# One forced `/available-models?refresh=true` costs 11 outbound calls (Ollama,
# the anonymous OpenRouter catalogue, and one per keyed provider), and nothing
# stopped it from being repeated back to back.
#
# 8 s: a person clicking "refresh" cannot have new information within 8 s of the
# previous refresh, and 8 s is shorter than the serial round trip of the eleven
# lookups themselves, so an honest single click is never throttled. What it
# costs: a second deliberate click inside 8 s is served from cache instead of
# re-fetching, and the user must wait out the remainder to force a real refresh.
_FORCED_REFRESH_MIN_INTERVAL_SECONDS = 8.0
# 0.0 = never refreshed. `time.monotonic()` is always > 0, so the first forced
# refresh of the process is always allowed.
_last_forced_refresh = 0.0


def _forced_refresh_allowed() -> bool:
    """Rate gate for the caller-controlled `refresh` flag.

    Single-user desktop app, so one process-wide timestamp is the whole state:
    there is no second user whose refresh this could starve.
    """
    global _last_forced_refresh
    now = time.monotonic()
    if now - _last_forced_refresh < _FORCED_REFRESH_MIN_INTERVAL_SECONDS:
        return False
    _last_forced_refresh = now
    return True


_WINDOWS_INSTALL_CMDS = {
    "cursor":   ("irm 'https://cursor.com/install?win32=true' | iex", False),
    "copilot":  ("npm install -g @github/copilot", True),
    "opencode": ("npm install -g opencode-ai", True),
    "claude":   ("npm install -g @anthropic-ai/claude-code", True),
    "codex":    ("npm install -g @openai/codex", True),
    # npm'deki 'agy'/'antigravity-cli' paketleri Google'ın değil (squatter).
    "agy":      ("irm 'https://antigravity.google/cli/install.ps1' | iex", False),
    # Kimi Code CLI resmi olarak macOS/Linux (Python 3.13+); Windows'ta kurulum
    # denenebilir ama desteklenmez — kurulu değilse UI temiz uyarı verir.
    "kimi":     ("pip install kimi-cli", False),
}

# macOS'ta mümkün olan yerlerde sağlayıcının resmi native installer'ını kullan.
# Böylece Finder'dan açılan GUI uygulamasının kısıtlı PATH'i veya Node/npm sürümü
# kurulumun önüne geçmez. Claude/Codex'in resmi standart kurulumu hâlâ npm'dir.
_MAC_INSTALL_CMDS = {
    "cursor":   ("curl https://cursor.com/install -fsS | bash", False),
    "copilot":  ("curl -fsSL https://gh.io/copilot-install | bash", False),
    "opencode": ("curl -fsSL https://opencode.ai/install | bash", False),
    "claude":   ("npm install -g @anthropic-ai/claude-code", True),
    "codex":    ("npm install -g @openai/codex", True),
    "agy":      ("curl -fsSL https://antigravity.google/cli/install.sh | bash", False),
}

_WINDOWS_LOGIN_CMDS = {
    "cursor":   "agent login",
    "copilot":  "copilot login",
    "codex":    "codex login",
    "opencode": "opencode auth login",
    "claude":   "claude",
    "agy":      "agy",
    "kimi":     "kimi",
}

_MAC_LOGIN_CMDS = {
    "cursor":   "cursor-agent login",
    "copilot":  "copilot login",
    "codex":    "codex login",
    "opencode": "opencode auth login",
    "claude":   "claude",
    "agy":      "agy",
    "kimi":     "kimi",
}

# Native installer'ların yaygın hedefleri. Script login aşamasına geçtiğinde
# yeni shell açmaya gerek kalmadan kurulan binary bulunabilsin.
_MAC_CLI_PATH = 'export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$HOME/.claude/bin:$HOME/.volta/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"'


async def _run_cli_capture(cmd: list, family: str | None, timeout: float = 20.0) -> str:
    """CLI komutunu çalıştırıp stdout'u döner (Windows'ta pencere açmadan).

    `family` ZORUNLU ve varsayılansız: bu fonksiyon üçüncü taraf CLI ikilileri
    çalıştırıyor (`cursor models`, `opencode models`, `cursor status`) ve
    2026-07-29'da canlı ölçüldü — `env=` verilmediği için çocuk süreç altı
    canary'nin ALTISINI da görüyordu (LOCAL_APP_TOKEN, API_KEY_ENCRYPTION_KEY,
    ve dört vendor anahtarı). Varsayılan bir aile koymak, yeni bir çağrı yerinin
    yanlış aileye sessizce düşmesi demek olurdu; burada sessiz yanlış aile
    "OpenCode'a Anthropic anahtarını vermek" anlamına geliyor.

    Modül düzeyinde duruyor (eskiden `create_config_router` içinde bir
    closure'dı) çünkü closure'ı test edebilmenin tek yolu HTTP ucundan geçmekti.
    """
    import subprocess as sp
    from providers.cli_base import build_spawn_env

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(sp, "CREATE_NO_WINDOW", 0),
        env=build_spawn_env(family),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return ""
    return out.decode("utf-8", errors="ignore")


def _resolve_general_cli(name: str) -> str | None:
    """PATH ve macOS kullanıcı kurulum dizinlerinden genel CLI binary'sini bul."""
    import shutil

    found = shutil.which(name)
    if found:
        return found
    if sys.platform != "win32":
        from providers.oneshot_cli import resolve_posix_cli
        return resolve_posix_cli(name)
    return None


def _parse_opencode_models(raw: str) -> list:
    """OpenCode'un kendi ücretsiz ve Go abonelik modellerini UI modeline çevir."""
    models = []
    for line in raw.splitlines():
        line = line.strip()
        if "/" not in line or " " in line:
            continue
        if not line.startswith(("opencode/", "opencode-go/")):
            continue
        provider, short = line.split("/", 1)
        pretty = short.replace("-", " ").title()
        if short.endswith("-free"):
            pretty = pretty[:-5].strip() + " (Ücretsiz)"
        elif provider == "opencode-go":
            pretty += " (Go)"
        models.append({"id": f"opencode:{line}", "name": pretty, "provider": "subscription"})
    return models


def _open_visible_terminal(shell_command: str, platform: str | None = None) -> None:
    """Komutu kullanıcının görebileceği, interaktif bir sistem terminalinde aç."""
    platform = platform or sys.platform
    if platform == "win32":
        CREATE_NEW_CONSOLE = 0x00000010
        subprocess.Popen(
            ["powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", shell_command],
            creationflags=CREATE_NEW_CONSOLE,
        )
        return

    if platform == "darwin":
        # AppleScript ile Terminal'i kontrol etmek paketlenmiş uygulamada ek
        # Automation izni ister. Geçici .command dosyasını LaunchServices ile
        # açmak aynı görünür terminali izin diyaloğu olmadan ve argüman kaçış
        # problemi yaratmadan sağlar.
        fd, script_path = tempfile.mkstemp(prefix="unityai-cli-", suffix=".command")
        try:
            script = (
                "#!/bin/zsh\n"
                "trap 'rm -f -- \"$0\"' EXIT\n"
                "[ -f \"$HOME/.zprofile\" ] && source \"$HOME/.zprofile\"\n"
                "[ -f \"$HOME/.zshrc\" ] && source \"$HOME/.zshrc\"\n"
                f"{_MAC_CLI_PATH}\n"
                f"{shell_command}\n"
                "echo\n"
                "echo 'Bu pencereyi kapatabilirsiniz.'\n"
                "/bin/zsh -il\n"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(script)
            os.chmod(script_path, 0o700)
            completed = subprocess.run(
                ["/usr/bin/open", "-a", "Terminal", script_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "Terminal.app açılamadı.").strip()
                raise HTTPException(500, detail)
        except Exception:
            try:
                os.unlink(script_path)
            except OSError:
                pass
            raise
        return

    raise HTTPException(501, "Otomatik kurulum şu anda Windows ve macOS'ta destekleniyor.")


def _install_terminal_command(cli: str, install_cmd: str, needs_npm: bool, platform: str) -> str:
    """Platform kabuğuna uygun kurulum + başarılıysa giriş komutunu üret."""
    if platform == "win32":
        return (
            f"Write-Host '=== {cli} kurulumu basliyor ===' -ForegroundColor Cyan; {install_cmd}; "
            f"Write-Host ''; Write-Host '=== Bitti. Bu pencereyi kapatip uygulamada Yenile''ye basabilirsiniz. ===' "
            f"-ForegroundColor Green"
        )

    npm_guard = ""
    if needs_npm:
        npm_guard = (
            "if ! command -v npm >/dev/null 2>&1; then "
            "echo 'Node.js/npm bulunamadi. Once https://nodejs.org adresinden Node.js kurun.'; "
            "install_status=127; else "
        )
        install_tail = "; install_status=$?; fi"
    else:
        install_tail = "; install_status=$?"

    login_cmd = _MAC_LOGIN_CMDS.get(cli, "")
    login_step = (
        f"echo; echo '=== Kurulum tamamlandi. Simdi hesap girisi aciliyor. ==='; "
        f"{_MAC_CLI_PATH}; {login_cmd}; "
        if login_cmd else ""
    )
    return (
        f"echo '=== {cli} kurulumu basliyor ==='; "
        f"{npm_guard}{install_cmd}{install_tail}; "
        "if [ \"$install_status\" -eq 0 ]; then "
        f"{login_step}"
        "echo; echo '=== Kurulum tamamlandi. Uygulamada Yenile butonuna basin. ==='; "
        "else echo; echo '=== Kurulum basarisiz. Yukaridaki hatayi kontrol edin. ==='; fi"
    )


def create_config_router(db):
    router = APIRouter()

    @router.post("/save-ai-config")
    async def save_config(req: AIConfigRequest, x_session_token: str = Header(alias="X-Session-Token")):
        user_id, _ = require_user(db, x_session_token, req.user_id)
        # 'CLI_SESSION' abonelik modunun placeholder'ıdır, GERÇEK key değildir.
        # Frontend state'inde bayat kalıp bulut modele geçişte buraya sızıyor ve
        # kullanıcının gerçek API key'inin ÜZERİNE yazıyordu (nvidia 401 bug'ı,
        # 2026-07-13 canlı yakalandı) → asla key olarak kaydetme.
        if req.api_key and req.api_key != "CLI_SESSION" and req.provider_type not in ("ollama", "subscription"):
            db.save_api_key(user_id, req.provider_type, req.api_key)
        db.save_ai_config(user_id, req.provider_type, req.model_name, "")
        return {"status": "success"}

    @router.get("/get-ai-config/{user_id}")
    async def get_config(user_id: int, x_session_token: str = Header(alias="X-Session-Token")):
        require_user(db, x_session_token, user_id)
        provider_type, model_name, _, _ = db.get_ai_config(user_id)
        has_key = provider_type != "ollama" and bool(db.get_api_key(user_id, provider_type))
        return {
            "provider_type": provider_type,
            "model_name": model_name,
            "has_key": has_key,
        }

    @router.get("/provider-ready/{user_id}")
    async def provider_ready(user_id: int, refresh: bool = False,
                             x_session_token: str = Header(alias="X-Session-Token")):
        """Seçili sağlayıcı GERÇEKTEN kullanılabilir mi? Sohbet kapısının tek kaynağı.

        Model seçilmiş olması yetmiyor: arkasındaki şeyin var olması gerekiyor
        (bulut → API anahtarı, CLI → kurulu, Ollama → servis ayakta). Sebep bir
        ürün kararı: kullanıcıya hiçbir şeyi habersiz kurmuyoruz, dolayısıyla
        sağlayıcısı olmayan kullanıcı bozuk bir sohbete DÜŞMEMELİ — ilk mesajında
        ham bir istisna görmek yerine ne yapması gerektiğini görüyor.

        ⚠️ Bu uç METİN DÖNDÜRMÜYOR, KOD döndürüyor (`needs`). Sebebi ölçülmüş bir
        borç: backend'de kullanıcıya görünen ~300 sabit Türkçe metin var ve
        çeviri mekanizması YOK. Buraya cümle koymak o borcu büyütürdü; metni
        frontend kendi sözlüğünden kuruyor.

        `needs` = None | "apikey" | "install" | "login" | "service"
        `kind`  = "api" | "cli" | "local"
        """
        require_user(db, x_session_token, user_id)
        provider_type, model_name, _, _ = db.get_ai_config(user_id)

        if provider_type == "ollama":
            # Yerel servis: kurulu olması yetmez, AYAKTA olması gerekiyor.
            import httpx  # modül düzeyinde import edilmiyor; yalnız bu dal kullanıyor
            ayakta = False
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    ayakta = (await c.get("http://localhost:11434/api/tags")).status_code == 200
            except Exception:
                ayakta = False
            return {"ready": ayakta, "kind": "local", "provider": "ollama",
                    "needs": None if ayakta else "service"}

        if provider_type != "subscription":
            # Bulut sağlayıcı → anahtar şart.
            var = bool(db.get_api_key(user_id, provider_type))
            return {"ready": var, "kind": "api", "provider": provider_type,
                    "needs": None if var else "apikey"}

        # CLI sağlayıcı. Aile eşlemesi `env_family` ile yapılıyor, ELDE yeniden
        # yazılmıyor: aynı önekleri `manager.get_provider` de kullanıyor ve ikisinin
        # ayrışmaması bir testle sabitlenmiş durumda.
        from providers.cli_base import env_family
        aile = env_family(model_name or "claude")
        doctor = await cli_doctor(refresh=refresh, x_session_token=x_session_token)
        durum = doctor.get(aile) or {}
        if not durum.get("installed"):
            return {"ready": False, "kind": "cli", "provider": aile, "needs": "install"}
        # `loggedIn` None = ÖLÇÜLEMEDİ, False = ölçüldü ve giriş yok. None'ı
        # "giriş yok" saymak, durumu hiç ölçülmeyen CLI'lerde (claude/codex/agy/kimi)
        # çalışan bir kurulumu yanlışlıkla kilitlerdi — bilmemek, yokluk değildir.
        if durum.get("loggedIn") is False:
            return {"ready": False, "kind": "cli", "provider": aile, "needs": "login"}
        return {"ready": True, "kind": "cli", "provider": aile, "needs": None}

    @router.get("/api-keys/{user_id}")
    async def get_api_keys(user_id: int, x_session_token: str = Header(alias="X-Session-Token")):
        require_user(db, x_session_token, user_id)
        keys = db.get_all_api_keys(user_id)
        masked = {}
        for provider, key in keys.items():
            if len(key) > 8:
                masked[provider] = f"{'•' * (len(key) - 4)}{key[-4:]}"
            else:
                masked[provider] = "••••••••"
        return {"keys": masked, "providers_with_keys": list(keys.keys())}

    @router.post("/api-keys/save")
    async def save_api_key(req: APIKeySaveRequest, x_session_token: str = Header(alias="X-Session-Token")):
        user_id, _ = get_current_user(db, x_session_token)
        if not req.provider_type:
            raise HTTPException(400, "provider_type gerekli.")
        if not req.api_key:
            raise HTTPException(400, "API key boş olamaz.")
        db.save_api_key(user_id, req.provider_type, req.api_key)
        return {"status": "success", "message": f"{req.provider_type} API key kaydedildi."}

    @router.delete("/api-keys/{user_id}/{provider_type}")
    async def delete_api_key(user_id: int, provider_type: str, x_session_token: str = Header(alias="X-Session-Token")):
        require_user(db, x_session_token, user_id)
        db.delete_api_key(user_id, provider_type)
        return {"status": "success"}

    def _merge_live_cloud(cloud: list, user_id: int, force: bool) -> dict:
        """Bulut model listesini CANLI kur; elle yazılı katalog yok.

        Her kaynak TEK bir soruyu cevaplıyor ve rolleri karışmıyor:

          * sağlayıcının kendi `/v1/models`i (kullanıcının anahtarıyla)
            -> "bu hesap neyi çağırabiliyor" — listenin kaynağı budur.
          * OpenRouter'in ACIK katalogu (anahtar gerekmiyor, olculdu 30 Agu
            2026: HTTP 200, 396 model) -> "bu modelin duzgun adi, baglam
            penceresi, fiyati ve OpenRouter karsiligi ne" — yalniz kunye.

        Anahtar yoksa ya da canli liste alinamazsa sağlayıcı GORUNMEZ OLMUYOR:
        OpenRouter katalogundaki o ad alaninin modelleri yedek liste olarak
        gosteriliyor, `verified: False` ile. Bos bir grup, "bu saglayici yok"
        diye okunurdu; oysa dogru cumle "hesabinda dogrulanmadi".

        Uc hal ayri kaliyor: `available: True` · `available: False` ·
        alan HIC yok (bilinmiyor).
        """
        durum: dict[str, str] = {}
        or_katalog = model_catalog.openrouter_catalog(force=force) or {}

        def _kunye(saglayici: str, mid: str) -> dict:
            or_id = model_catalog.openrouter_id_for(saglayici, mid)
            kayit = or_katalog.get(or_id or "")
            alanlar: dict = {}
            if or_id and saglayici != "openrouter":
                alanlar["openrouter_id"] = or_id
            if kayit:
                alanlar["name"] = kayit["name"]
                if kayit.get("context_length"):
                    alanlar["context_length"] = kayit["context_length"]
                fiyat = (kayit.get("pricing") or {}).get("prompt")
                try:
                    if fiyat is not None and float(fiyat) > 0:
                        alanlar["paid"] = True
                except (TypeError, ValueError):
                    pass
            return alanlar

        for saglayici in model_catalog.supported_providers():
            anahtar = db.get_api_key(user_id, saglayici) or ""
            canli = model_catalog.list_models(saglayici, anahtar, force=force)
            if canli:
                durum[saglayici] = "live"
                for mid in sorted(canli):
                    if not model_catalog.is_chat_model(mid):
                        continue
                    model = {"id": mid, "name": canli[mid], "provider": saglayici,
                             "available": True, "verified": True, "source": "live"}
                    model.update(_kunye(saglayici, mid))
                    cloud.append(model)
                continue

            durum[saglayici] = "no_key" if not (isinstance(anahtar, str) and anahtar) else "unknown"
            ns = model_catalog.openrouter_id_for(saglayici, "x")
            onek = ns.rsplit("/", 1)[0] + "/" if ns and "/" in ns else None
            if not onek:
                continue
            for or_id, kayit in sorted(or_katalog.items()):
                if not or_id.startswith(onek) or not model_catalog.is_chat_model(or_id):
                    continue
                yerel = model_catalog.native_id_from_openrouter(
                    saglayici, or_id.split("/", 1)[1])
                if not yerel:
                    continue
                model = {"id": yerel, "name": kayit["name"], "provider": saglayici,
                         "verified": False, "source": "openrouter",
                         "openrouter_id": or_id}
                if kayit.get("context_length"):
                    model["context_length"] = kayit["context_length"]
                cloud.append(model)
        return durum

    # Copilot'un programatik model listeleme komutu yok → statik liste (dinamik
    # endpoint'ten servis edilir ki plan-caps disabled bayrakları eklenebilsin).
    # Also feeds /available-models: the same ids used to be typed out in both
    # endpoints, and two hand-kept copies drift (hygiene audit, 5 Sep 2026).
    _COPILOT_MODELS = [
        {"id": "copilot-auto",                   "name": "Copilot Auto (Önerilen)", "provider": "subscription"},
        {"id": "copilot-claude-sonnet-5",        "name": "Claude Sonnet 5",         "provider": "subscription"},
        {"id": "copilot-claude-fable-5",         "name": "Claude Fable 5",          "provider": "subscription"},
        {"id": "copilot-claude-opus-4.8",        "name": "Claude Opus 4.8",         "provider": "subscription"},
        {"id": "copilot-claude-haiku-4.5",       "name": "Claude Haiku 4.5",         "provider": "subscription"},
        {"id": "copilot-gpt-5.6-sol",            "name": "GPT-5.6 Sol",             "provider": "subscription"},
        {"id": "copilot-gpt-5.6-luna",           "name": "GPT-5.6 Luna",            "provider": "subscription"},
        {"id": "copilot-gpt-5.5",                "name": "GPT-5.5",                 "provider": "subscription"},
        {"id": "copilot-gpt-5.4-mini",           "name": "GPT-5.4 Mini",            "provider": "subscription"},
        {"id": "copilot-gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro",          "provider": "subscription"},
    ]

    # Codex modelleri: statik liste. Hesap/plan cevabı tutarsız olabildiği için
    # bunlara kalıcı UI kilidi uygulanmaz; kullanıcı modeli her zaman deneyebilir.
    _CODEX_MODELS = [
        # Ids as listed by Codex's own model catalog (~/.codex/models_cache.json,
        # fetched 24 Sep 2026 by codex-cli 0.156.1: gpt-6-astra/sol/luna visible).
        {"id": "gpt-6-astra",   "name": "GPT-6 Astra",    "provider": "subscription"},
        {"id": "gpt-6-sol",     "name": "GPT-6 Sol",      "provider": "subscription"},
        {"id": "gpt-6-luna",    "name": "GPT-6 Luna",     "provider": "subscription"},
        {"id": "gpt-5.6-terra", "name": "GPT-5.6 Terra",  "provider": "subscription"},
        {"id": "gpt-5.6-sol",   "name": "GPT-5.6 Sol",    "provider": "subscription"},
        {"id": "gpt-5.6-luna",  "name": "GPT-5.6 Luna",   "provider": "subscription"},
        {"id": "gpt-5.5",       "name": "GPT-5.5",        "provider": "subscription"},
        {"id": "gpt-5.4",       "name": "GPT-5.4",        "provider": "subscription"},
        {"id": "gpt-5.4-mini",  "name": "GPT-5.4 Mini",   "provider": "subscription"},
    ]

    @router.get("/available-models")
    async def get_available_models(
        refresh: bool = False,
        x_session_token: str = Header(alias="X-Session-Token", default=""),
    ):
        # Kimliksizken de iş yapıyordu: yanıt üretmek için Ollama'yı
        # (127.0.0.1:11434) yokluyor, yani makinede hangi yerel modellerin
        # kurulu olduğunu doğrulanmamış bir çağırana söylüyordu. Küçük ama
        # keşif adımı; kapı işten ÖNCE.
        _check_token(x_session_token)
        # A forced refresh that arrives too soon after the previous one is
        # downgraded to a cached read; the endpoint still answers, just without
        # eleven fresh outbound calls.
        if refresh and not _forced_refresh_allowed():
            logger.info("Yenile isteği kısıldı; önbellekten yanıtlanıyor")
            refresh = False
        models = {
            "local": [],
            # Bulut listesi artık ELLE YAZILMIYOR (Karar: Burak, 30 Ağu 2026).
            # Boş kuruluyor; `_merge_live_cloud` sağlayıcıların kendi
            # `/v1/models` cevabından dolduruyor, künyeyi de OpenRouter'ın
            # açık kataloğundan alıyor. Silinen 40 satırlık sözlük, listelediği
            # şeyden ayrışıyordu: Groq modeli 16 Ağu'da kapanmıştı ve katalog
            # hâlâ onu tek seçenek olarak sunuyordu.
            "cloud": [],
            "subscription": [
                {"id": "claude-sonnet-5",      "name": "Claude Sonnet 5 (CLI)",         "provider": "subscription"},
                {"id": "claude-fable-5-1",     "name": "Claude Fable 5.1 (CLI)",        "provider": "subscription"},
                {"id": "claude-fable-5",       "name": "Claude Fable 5 (CLI)",          "provider": "subscription"},
                {"id": "claude-opus-5-5",      "name": "Claude Opus 5.5 (CLI)",         "provider": "subscription"},
                {"id": "claude-opus-5",        "name": "Claude Opus 5 (CLI)",           "provider": "subscription"},
                {"id": "claude-opus-4-8",      "name": "Claude 4.8 Opus (CLI)",         "provider": "subscription"},
                {"id": "claude-sonnet-4-6",    "name": "Claude 4.6 Sonnet (CLI)",       "provider": "subscription"},
                {"id": "claude-haiku-4-5",     "name": "Claude 4.5 Haiku (CLI)",        "provider": "subscription"},
                *[{**model, "name": f"Codex ({model['name']})"} for model in _CODEX_MODELS],
                {"id": "kimi-k3",              "name": "Kimi K3 (CLI)",                 "provider": "subscription"},
                {"id": "kimi-k2.7-code",       "name": "Kimi K2.7 Code (CLI)",          "provider": "subscription"},
                {"id": "gemini-3.8-flash",             "name": "Gemini 3.8 Flash (Önerilen)", "provider": "subscription"},
                {"id": "gemini-3.8-flash-medium",      "name": "Gemini 3.8 Flash (Medium)",   "provider": "subscription"},
                {"id": "gemini-3.7-flash",             "name": "Gemini 3.7 Flash",            "provider": "subscription"},
                {"id": "gemini-3.7-flash-medium",      "name": "Gemini 3.7 Flash (Medium)",   "provider": "subscription"},
                {"id": "gemini-3.6-flash",             "name": "Gemini 3.6 Flash",            "provider": "subscription"},
                {"id": "gemini-3.6-flash-medium",      "name": "Gemini 3.6 Flash (Medium)",   "provider": "subscription"},
                {"id": "gemini-3.5-flash",             "name": "Gemini 3.5 Flash",            "provider": "subscription"},
                {"id": "gemini-3.5-flash-medium",      "name": "Gemini 3.5 Flash (Medium)",   "provider": "subscription"},
                {"id": "gemini-3.1-pro-preview",       "name": "Gemini 3.1 Pro (High)",       "provider": "subscription"},
                {"id": "gemini-3.1-pro-low",           "name": "Gemini 3.1 Pro (Low)",        "provider": "subscription"},
                # NOT: 3.5 Flash Lite / 3 Flash / 3.1 Flash Lite / 2.5-* agy 1.1.5
                # `agy models` listesinde YOK — subscription'dan çıkarıldı (Cloud'da geçerli).
                {"id": "agy-claude-sonnet-4-6",        "name": "Claude Sonnet 4.6 (Thinking)", "provider": "subscription"},
                {"id": "agy-gpt-oss-120b",             "name": "GPT-OSS 120B (Medium)",       "provider": "subscription"},
                # GitHub Copilot CLI (statik — copilot'un programatik model listesi yok;
                # ID'ler CLI'ın kendi model seçicisinden alındı, 2026-07-13)
                *_COPILOT_MODELS,
                # Cursor ve OpenCode modelleri DİNAMİK: /cli-models/{cli} endpoint'i
                # kullanıcının hesabına/kurulumuna göre canlı liste döner.
            ]
        }

        try:
            def fetch_ollama():
                req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
                with urllib.request.urlopen(req, timeout=3) as response:
                    return json.loads(response.read())

            ollama_data = await asyncio.to_thread(fetch_ollama)
            for model in ollama_data.get("models", []):
                model_name = model.get("name", "")
                if model_name:
                    models["local"].append(
                        {
                            "id": model_name,
                            "name": model_name.split(":")[0].title() + " (Local)",
                            "provider": "ollama",
                        }
                    )
        except Exception as exc:
            logger.warning(f"Ollama list fetch failed: {exc}")

        # Canlı bulut listesi: ağ çağrısı yapıyor, o yüzden hem önbellekli hem
        # de asla yükseltmiyor. Burada patlamak, elle yazılı katalogla gayet iyi
        # çalışan model seçicisini komple çökertirdi.
        try:
            _user_id, _ = get_current_user(db, x_session_token)
            models["cloud_sources"] = await asyncio.to_thread(
                _merge_live_cloud, models["cloud"], _user_id, refresh
            )
        except Exception as exc:
            logger.warning(f"Canlı bulut model listesi birleştirilemedi: {exc}")
            models["cloud_sources"] = {}

        return models

    @router.get("/effort-capabilities")
    async def effort_capabilities(
        provider: str = "",
        model: str = "",
        x_session_token: str = Header(alias="X-Session-Token", default=""),
    ):
        """Aktif provider+model'in GERÇEKTEN desteklediği effort seviyeleri.
        Frontend seçici bunu gösterir — desteklenmeyen seviye hiç listelenmez."""
        # Bu uç yan etkili/bilgi sızdırıcı: token SORULUYOR ama 2026-07-27
        # denetimine kadar HİÇ doğrulanmıyordu (imzada vardı, gövdede yoktu).
        _check_token(x_session_token)
        from providers.effort_caps import get_effort_caps
        return get_effort_caps(provider, model)

    @router.get("/cli-availability")
    async def cli_availability(x_session_token: str = Header(alias="X-Session-Token", default="")):
        """CLI sağlayıcılarının kullanıcı PC'sinde kurulu olup olmadığını döner.
        Bunlar gömülü DEĞİL — kullanıcının kurmuş olması gerekir.
        Frontend, kurulu olmayan bir CLI modeli seçilince uyarı gösterir."""
        _check_token(x_session_token)
        from providers.agy_provider import AgyProvider
        from providers.oneshot_cli import cli_installed

        # _agy_binary() yaygın kurulum yollarına da bakar; "agy" dönerse sadece PATH'e kalmış demektir.
        agy_ok = AgyProvider._agy_binary() != "agy" or bool(_resolve_general_cli("agy"))
        return {
            "claude": bool(_resolve_general_cli("claude")),
            "codex": bool(_resolve_general_cli("codex")),
            "agy": agy_ok,
            "kimi": bool(_resolve_general_cli("kimi")),
            "cursor": cli_installed("cursor"),
            "copilot": cli_installed("copilot"),
            "opencode": cli_installed("opencode"),
        }

    # ── Dinamik CLI model listeleri (cursor: hesaba göre; opencode: kuruluma göre) ──
    _cli_models_cache: dict = {}   # cli → (timestamp, models)
    _CLI_MODELS_TTL = 300          # 5 dk — CLI listeleri nadiren değişir

    def _parse_cursor_models(raw: str) -> list:
        """`agent models` çıktısı: 'id - Display Name' satırları.
        '-fast' hız varyantları listeyi 2x şişiriyor → elenir (id'yi bilen
        kullanıcı yine seçebilir; UI listesi derli toplu kalsın)."""
        models = []
        for line in raw.splitlines():
            line = line.strip()
            if " - " not in line or line.lower().startswith("available"):
                continue
            mid, _, name = line.partition(" - ")
            mid = mid.strip()
            name = name.strip()
            if not mid or mid.endswith("-fast"):
                continue
            # "Auto (current, default)" → "Auto"
            name = name.split(" (current")[0].split(" (default")[0].strip()
            models.append({"id": f"cursor-{mid}", "name": name, "provider": "subscription"})
        return models

    def _apply_plan_caps(cli: str, models: list) -> list:
        """Yalnız Cursor/Copilot'un doğrulanmış ``Auto-only`` planını işaretle.

        Codex account/plan sinyali model erişimiyle tutarlı olmadığı için Codex
        modelleri hiçbir koşulda burada kilitlenmez.
        """
        if cli not in ("cursor", "copilot"):
            return models
        from providers.oneshot_cli import get_named_models_cap
        if get_named_models_cap(cli) is False:
            for m in models:
                if m["id"] not in (f"{cli}-auto",):
                    m["disabled"] = True
                    m["disabled_reason"] = "plan"
        return models

    @router.get("/cli-models/{cli}")
    async def cli_models(cli: str, x_session_token: str = Header(alias="X-Session-Token", default="")):
        """Cursor/OpenCode: canlı model listesi; Copilot/Codex: statik liste.
        Yalnız Cursor/Copilot Auto-only planı disabled bayrağı üretir. 5 dk cache."""
        _check_token(x_session_token)
        import time
        from providers.oneshot_cli import resolve_cli_cmd, get_named_models_cap, probe_named_models

        if cli not in ("cursor", "opencode", "copilot", "codex"):
            raise HTTPException(404, "Desteklenen: cursor, opencode, copilot, codex")

        import copy as _copy
        cached = _cli_models_cache.get(cli)
        if cached and time.time() - cached[0] < _CLI_MODELS_TTL:
            # Bayraklar cache'e YAZILMAZ — plan bilgisi turda değişebilir (canlı
            # öğrenme) → her istekte taze uygulanır.
            return {"models": _apply_plan_caps(cli, _copy.deepcopy(cached[1])), "installed": True}

        codex_binary = _resolve_general_cli("codex") if cli == "codex" else None
        base = resolve_cli_cmd(cli) if cli != "codex" else ([codex_binary] if codex_binary else None)
        if not base:
            return {"models": [], "installed": False}

        if cli == "copilot":
            import copy
            models = copy.deepcopy(_COPILOT_MODELS)
        elif cli == "codex":
            import copy
            models = copy.deepcopy(_CODEX_MODELS)
        else:
            try:
                # Aile ÇIPLAK CLI adından çözülüyor ("cursor" / "opencode").
                # env_family artık çıplak adı da tanıyor; tanımasaydı OpenCode
                # "claude" ailesine düşer ve vendor anahtarlarını görürdü.
                from providers.cli_base import env_family
                raw = await _run_cli_capture([*base, "models"], env_family(cli))
                models = _parse_cursor_models(raw) if cli == "cursor" else _parse_opencode_models(raw)
            except Exception as exc:
                logger.warning(f"cli-models({cli}) alınamadı: {exc}")
                models = []

        # Plan yeteneği bilinmiyorsa TEK SEFERLİK ucuz probe (sonuç haftalık cache'li
        # dosyada). İlk açılışta grup birkaç sn "yükleniyor" gösterir, sonrası anlık.
        if cli in ("cursor", "copilot") and get_named_models_cap(cli) is None:
            await probe_named_models(cli)

        if models:
            _cli_models_cache[cli] = (time.time(), _copy.deepcopy(models))
        return {"models": _apply_plan_caps(cli, models), "installed": True}

    # ── CLI doktoru: kurulu mu + giriş yapılmış mı ──────────────────
    _doctor_cache: dict = {}

    @router.get("/cli-doctor")
    async def cli_doctor(refresh: bool = False, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        import time
        from providers.oneshot_cli import cli_installed, resolve_cli_cmd
        from providers.agy_provider import AgyProvider

        if not refresh and _doctor_cache.get("t", 0) > time.time() - 60:
            return _doctor_cache["data"]
        if refresh:
            # Kullanıcı yenilemesi = "durumu baştan öğren": Cursor/Copilot plan
            # yetenekleri de sıfırlanır ve probe ile yeniden öğrenilir.
            from providers.oneshot_cli import clear_plan_caps
            clear_plan_caps()
            _cli_models_cache.clear()

        async def cursor_login() -> bool | None:
            base = resolve_cli_cmd("cursor")
            if not base:
                return None
            from providers.cli_base import env_family
            out = await _run_cli_capture([*base, "status"], env_family("cursor"), timeout=10)
            low = out.lower()
            # DİKKAT: "not logged in" metni "logged in" alt-dizesini içerir →
            # negatifi ÖNCE kontrol et, yoksa çıkış yapmış kullanıcı "giriş yapmış" görünür.
            if "not logged in" in low or "not authenticated" in low or "please log in" in low:
                return False
            if "logged in" in low or "logged in as" in low:
                return True
            return False if out.strip() else None

        def copilot_login() -> bool | None:
            # Login durumu config dosyasında — spawn'sız, anlık.
            import json as _json
            try:
                p = os.path.expanduser(os.path.join("~", ".copilot", "config.json"))
                with open(p, "r", encoding="utf-8") as f:
                    txt = "\n".join(l for l in f.read().splitlines() if not l.strip().startswith("//"))
                return bool((_json.loads(txt) or {}).get("lastLoggedInUser"))
            except Exception:
                return None

        agy_ok = AgyProvider._agy_binary() != "agy" or bool(_resolve_general_cli("agy"))
        data = {
            "claude":   {"installed": bool(_resolve_general_cli("claude")),  "loggedIn": None},
            "codex":    {"installed": bool(_resolve_general_cli("codex")),   "loggedIn": None},
            "agy":      {"installed": agy_ok,                        "loggedIn": None},
            "kimi":     {"installed": bool(_resolve_general_cli("kimi")),    "loggedIn": None},
            "cursor":   {"installed": cli_installed("cursor"),       "loggedIn": None},
            "copilot":  {"installed": cli_installed("copilot"),      "loggedIn": None},
            "opencode": {"installed": cli_installed("opencode"),     "loggedIn": True},  # auth opsiyonel (ücretsiz modeller)
        }
        if data["cursor"]["installed"]:
            try:
                data["cursor"]["loggedIn"] = await cursor_login()
            except Exception:
                pass
        if data["copilot"]["installed"]:
            data["copilot"]["loggedIn"] = copilot_login()
        if not data["opencode"]["installed"]:
            data["opencode"]["loggedIn"] = None

        _doctor_cache["t"] = time.time()
        _doctor_cache["data"] = data
        return data

    # ── Tek tık kurulum / giriş: kullanıcının GÖREBİLECEĞİ bir terminal
    #    penceresi açar (kurulum çıktısı + tarayıcı login akışı orada yaşar). ──
    @router.post("/cli-install/{cli}")
    async def cli_install(cli: str, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        import shutil
        install_map = _MAC_INSTALL_CMDS if sys.platform == "darwin" else _WINDOWS_INSTALL_CMDS
        entry = install_map.get(cli)
        if not entry:
            raise HTTPException(404, f"'{cli}' için otomatik kurulum desteklenmiyor.")
        cmd, needs_npm = entry
        # Windows GUI süreci terminalle aynı PATH'i görür. macOS'ta npm nvm/fnm
        # üzerinden yalnız interaktif shell'de bulunabilir; kontrol terminal
        # scriptinin içinde yapılır.
        if sys.platform == "win32" and needs_npm and not shutil.which("npm"):
            raise HTTPException(412, "Bu CLI'ın kurulumu için Node.js gerekiyor. Önce nodejs.org'dan Node.js kurun (npm ile birlikte gelir).")
        _open_visible_terminal(_install_terminal_command(cli, cmd, needs_npm, sys.platform))
        _doctor_cache.clear()
        return {"status": "started", "message": "Kurulum penceresi açıldı."}

    @router.post("/cli-login/{cli}")
    async def cli_login(cli: str, x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        login_map = _MAC_LOGIN_CMDS if sys.platform == "darwin" else _WINDOWS_LOGIN_CMDS
        cmd = login_map.get(cli)
        if not cmd:
            raise HTTPException(404, f"'{cli}' için giriş akışı desteklenmiyor.")
        if sys.platform == "darwin":
            visible_cmd = (
                f"echo '=== {cli} girisi: acilan tarayicida hesabinizla giris yapin ==='; "
                f"{_MAC_CLI_PATH}; {cmd}"
            )
        else:
            visible_cmd = (
                f"Write-Host '=== {cli} girisi: acilan tarayicida hesabinizla giris yapin ===' "
                f"-ForegroundColor Cyan; {cmd}"
            )
        _open_visible_terminal(visible_cmd)
        _doctor_cache.clear()
        return {"status": "started", "message": "Giriş penceresi açıldı."}

    return router
