"""Cursor / Copilot / OpenCode / Kimi — one-shot CLI ortak altyapısı.

Bu CLI'lar Claude (SDK session) ve Codex (app-server) gibi CANLI bir süreç
tutmaz; her tur ephemeral bir subprocess'tir. Cursor/Copilot/OpenCode bağlamı
resmi resume mekanizmasıyla, Kimi ise her tur kırpılmış transcript ile sürdürür:

  • cursor  : `agent create-chat` ile chatId baştan üretilir; her tur
              `--resume <chatId>`. (stream-json çıktı, Claude formatına çok yakın)
  • copilot : İLK turda --session-id=<bizim ürettiğimiz uuid>, sonraki
              turlarda --resume=<uuid>. (JSONL çıktı)
  • opencode: İlk turun JSON event'lerindeki sessionID yakalanır; sonraki
              turlarda `-s <sessionID>`. (JSON event çıktı)
  • kimi     : Doğrulanmış resume yok; her tur sohbet transcript'i enjekte edilir.

Windows'ta npm/ps1 shim'leri cmd.exe sarmalaması gerektirir ve cmd.exe çok
satırlı argv'yi bozar (claude/codex'te stdin fallback ile çözmüştük). Burada
shim'i tamamen ATLAYIP gerçek entry point'i doğrudan spawn ederiz:
  • cursor  : <LOCALAPPDATA>/cursor-agent/versions/<en yeni>/node.exe + index.js
  • copilot : node + <APPDATA>/npm/node_modules/@github/copilot/npm-loader.js
  • opencode: <APPDATA>/npm/node_modules/opencode-ai/bin/opencode.exe (native!)
macOS/Linux'ta bunlar POSIX binary/script'tir. Finder'dan açılan uygulamanın
PATH'i kullanıcı bin dizinlerini taşımayabildiğinden yaygın kurulum yolları da
doğrudan taranır.
"""
import os
import sys
import glob
import shutil
import logging
from typing import Dict, List, Optional, Tuple

from .saglayici_sahipligi import SaglayiciSahipligi, oturumu_kapat

logger = logging.getLogger(__name__)

CLI_KEYS = ("cursor", "copilot", "opencode", "kimi")


# ─────────────────────────────────────────────────────────────────
# Sohbet başına resume durumu (agy_session ile aynı kalıp)
# ─────────────────────────────────────────────────────────────────
class OneShotSession(SaglayiciSahipligi):
    def __init__(self, cli: str, conversation_id: int):
        self.cli = cli
        self.conversation_id = conversation_id
        self.session_id: Optional[str] = None  # CLI'in resume anahtari
        self.ctx_injected: bool = False        # transcript ilk turda enjekte edildi mi
        self.auto_approve: bool = False
        # Durdur icin calisan subprocess sahipleri - tek yuva DEGIL kume.
        # Gerekce ve olcum: `saglayici_sahipligi.py`.
        self._sahiplik_kur()


_SESSIONS: Dict[Tuple[str, int], OneShotSession] = {}


def get_session(cli: str, conversation_id: int) -> OneShotSession:
    key = (cli, conversation_id)
    s = _SESSIONS.get(key)
    if s is None:
        s = OneShotSession(cli, conversation_id)
        _SESSIONS[key] = s
    return s


async def close_session(cli: str, conversation_id: int) -> None:
    # SONUNCUSU degil HEPSI - gerekce saglayici_sahipligi.py'de.
    await oturumu_kapat(_SESSIONS.pop((cli, conversation_id), None))


async def close_all_sessions() -> None:
    for cli, conversation_id in list(_SESSIONS):
        await close_session(cli, conversation_id)


async def close_conversation_sessions(conversation_id: int) -> bool:
    """Bir sohbetin çalışan/bayat tüm one-shot CLI session'larını kapatır."""
    keys = [
        (cli, conv_id)
        for cli, conv_id in list(_SESSIONS)
        if conv_id == conversation_id
    ]
    for cli, conv_id in keys:
        await close_session(cli, conv_id)
    return bool(keys)


# ─────────────────────────────────────────────────────────────────
# Binary çözümleme (shim'siz doğrudan spawn)
# ─────────────────────────────────────────────────────────────────
def _npm_global_root() -> str:
    """Windows'ta npm global kök klasörü (APPDATA/npm)."""
    return os.path.join(os.environ.get("APPDATA", ""), "npm")


def _latest_cursor_version_dir() -> Optional[str]:
    """<LOCALAPPDATA>/cursor-agent/versions/ altındaki en yeni sürüm klasörü.
    Klasör adı YYYY.MM.DD-hash formatında → ada göre sıralama kronolojiktir."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cursor-agent", "versions")
    try:
        dirs = [d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
    except Exception:
        return None
    if not dirs:
        return None
    return max(dirs, key=os.path.basename)


def resolve_posix_cli(name: str) -> Optional[str]:
    """GUI uygulamasının PATH'inde görünmeyen yaygın kullanıcı CLI yollarını çöz."""
    found = shutil.which(name)
    if found and not found.lower().endswith(".ps1"):
        return found
    if sys.platform == "win32":
        return None

    candidates = [
        os.path.expanduser(f"~/.local/bin/{name}"),
        os.path.expanduser(f"~/.volta/bin/{name}"),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    if name == "opencode":
        candidates.insert(0, os.path.expanduser("~/.opencode/bin/opencode"))
    elif name == "claude":
        candidates[:0] = [
            os.path.expanduser("~/.claude/bin/claude"),
            os.path.expanduser("~/.claude/local/claude"),
        ]

    # npm, nvm ve fnm kurulumları Finder'dan başlatılan uygulamanın PATH'inde
    # olmaz. En yeni node sürümünün global bin dizinini doğrudan tara.
    candidates.extend(sorted(
        glob.glob(os.path.expanduser(f"~/.nvm/versions/node/*/bin/{name}")),
        reverse=True,
    ))
    candidates.extend(sorted(
        glob.glob(os.path.expanduser(f"~/.fnm/node-versions/*/installation/bin/{name}")),
        reverse=True,
    ))
    candidates.extend(sorted(
        glob.glob(os.path.expanduser(
            f"~/Library/Application Support/fnm/node-versions/*/installation/bin/{name}"
        )),
        reverse=True,
    ))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def resolve_cursor_cmd() -> Optional[List[str]]:
    """Cursor agent'ı çalıştıracak komut listesi (arg'lar hariç) veya None."""
    if sys.platform == "win32":
        vdir = _latest_cursor_version_dir()
        if vdir:
            node = os.path.join(vdir, "node.exe")
            entry = os.path.join(vdir, "index.js")
            if os.path.exists(node) and os.path.exists(entry):
                return [node, entry]
    # POSIX (mac/linux) veya fallback: PATH'teki agent
    for name in ("cursor-agent", "agent"):
        p = resolve_posix_cli(name)
        if p:
            return [p]
    return None


def resolve_copilot_cmd() -> Optional[List[str]]:
    """GitHub Copilot CLI komutu veya None."""
    if sys.platform == "win32":
        loader = os.path.join(_npm_global_root(), "node_modules", "@github", "copilot", "npm-loader.js")
        node = shutil.which("node")
        if node and os.path.exists(loader):
            return [node, loader]
    p = resolve_posix_cli("copilot")
    if p:
        return [p]
    return None


def resolve_opencode_cmd() -> Optional[List[str]]:
    """OpenCode komutu veya None. Windows'ta npm paketi NATIVE exe taşır."""
    if sys.platform == "win32":
        exe = os.path.join(_npm_global_root(), "node_modules", "opencode-ai", "bin", "opencode.exe")
        if os.path.exists(exe):
            return [exe]
    p = resolve_posix_cli("opencode")
    if p:
        return [p]
    return None


_RESOLVERS = {
    "cursor": resolve_cursor_cmd,
    "copilot": resolve_copilot_cmd,
    "opencode": resolve_opencode_cmd,
}


def resolve_cli_cmd(cli: str) -> Optional[List[str]]:
    fn = _RESOLVERS.get(cli)
    return fn() if fn else None


def cli_installed(cli: str) -> bool:
    return resolve_cli_cmd(cli) is not None


# ─────────────────────────────────────────────────────────────────
# Model kimliği eşlemesi
#   Bizim ID                     → CLI --model değeri
#   cursor-composer-2.5          → composer-2.5
#   copilot-claude-sonnet-5      → claude-sonnet-5
#   opencode:opencode/big-pickle → opencode/big-pickle
# ─────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────
# Plan yetenekleri (öğrenilmiş): adlı modeller bu planda çalışıyor mu?
# Kaynaklar: (a) /cli-models içindeki tek seferlik ucuz probe,
#            (b) agent_runner'daki canlı Auto-fallback (turda plan hatası).
# Dosya: ~/.unity_architect_ai/cli_plan_caps.json
#   {"cursor": {"named_models": false, "checked_at": 1234567890}, ...}
# ─────────────────────────────────────────────────────────────────
_CAPS_PATH = os.path.expanduser(os.path.join("~", ".unity_architect_ai", "cli_plan_caps.json"))
_CAPS_TTL = 7 * 24 * 3600  # planlar değişebilir → haftada bir yeniden öğren


def get_plan_caps() -> dict:
    try:
        import json as _json
        with open(_CAPS_PATH, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def get_named_models_cap(cli: str) -> Optional[bool]:
    """True/False = biliniyor; None = bilinmiyor veya bayat (yeniden öğren)."""
    import time
    entry = get_plan_caps().get(cli)
    if not isinstance(entry, dict) or "named_models" not in entry:
        return None
    if time.time() - entry.get("checked_at", 0) > _CAPS_TTL:
        return None
    return bool(entry["named_models"])


def set_named_models_cap(cli: str, available: bool) -> None:
    try:
        import json as _json, time
        caps = get_plan_caps()
        caps[cli] = {"named_models": bool(available), "checked_at": time.time()}
        os.makedirs(os.path.dirname(_CAPS_PATH), exist_ok=True)
        with open(_CAPS_PATH, "w", encoding="utf-8") as f:
            _json.dump(caps, f, indent=2)
        logger.info(f"[plan-caps] {cli}.named_models={available}")
    except Exception as e:
        logger.warning(f"[plan-caps] yazılamadı: {e}")


# Plan kısıtı hata kalıbı (cursor/copilot canlı yakalandı)
import re as _re
PLAN_ERROR_RE = _re.compile(
    r"(named models unavailable|free plans can only use auto|"
    r"is not available|switch to auto|upgrade plans)", _re.I)

# Codex plan kısıtı (ikisi de canlı yakalandı 2026-07-13):
#   "The 'gpt-5.6-sol' model is not supported when using Codex with a ChatGPT account."
#   "unexpected status 404 Not Found: Model not found gpt-5.6-luna-free-1p-..."
CODEX_PLAN_ERROR_RE = _re.compile(
    r"(model is not supported when using codex|model not found)", _re.I)

# Kota/limit tükenmesi (tüm CLI'lar) → kullanıcıya dostane "hakkın doluyor/doldu" mesajı
QUOTA_ERROR_RE = _re.compile(
    r"(usage limit|rate limit|quota|too many requests|\b429\b|"
    r"out of (free )?credits|limit reached|hakk?ınız)", _re.I)

# OpenCode Go kimi zaman gerçek rate-limit nedenini kendi loguna yazıp JSON
# event'inde yalnız genel bir upstream hatası döndürüyor.
UPSTREAM_ERROR_RE = _re.compile(
    r"(upstream request failed|service unavailable|temporarily unavailable)",
    _re.I,
)

# OpenCode access refusals: 403 with isRetryable=false, so retrying never
# helps. Both measured 26 Sep 2026 on opencode 1.18.25. The Go one arrives
# wrapped in "Upstream request failed", so it must be checked BEFORE
# UPSTREAM_ERROR_RE or it reads as a transient rate limit.
#   "Upstream request failed: An active OpenCode Go subscription is required to use Go models."
#   "Error from provider (Console): OpenCode's free tier can only be used from within OpenCode"
# The free-tier refusal is per model (ling-3.0-flash-fin-free refused,
# space-bunny-free served) and fired only while the workspace opencode.json
# set permission.bash to "deny" (4/4 refused with it, 5/5 served without).
OPENCODE_GO_SUBSCRIPTION_RE = _re.compile(
    r"opencode go subscription is required|subscription is required to use go models",
    _re.I,
)
OPENCODE_FREE_TIER_RE = _re.compile(
    r"free tier can only be used from within opencode|FreeTierError", _re.I)


def opencode_access_message(msg: str) -> Optional[str]:
    """User-facing text for an OpenCode plan/free-tier refusal, else None.

    An error that also names a rate limit or quota is left to the quota
    mapping: a transient limit must never read as "retrying will not help".
    """
    if QUOTA_ERROR_RE.search(msg or ""):
        return None
    if OPENCODE_GO_SUBSCRIPTION_RE.search(msg or ""):
        return ("🔒 Bu model OpenCode Go aboneliği gerektiriyor ve OpenCode hesabında "
                "etkin bir Go aboneliği görünmüyor. Bu geçici bir yoğunluk değil; tekrar "
                "denemek işe yaramaz. Go aboneliğini etkinleştir ya da model seçiciden "
                "\"(Ücretsiz)\" etiketli bir OpenCode modeli veya başka bir sağlayıcı seç. "
                "Oturum bağlamı korundu.")
    if OPENCODE_FREE_TIER_RE.search(msg or ""):
        return ("🔒 OpenCode bu ücretsiz modeli yalnız kendi uygulaması içinden "
                "kullandırıyor ve Gamachine'den gelen isteği reddetti. Bu geçici bir "
                "yoğunluk değil; tekrar denemek işe yaramaz. Başka bir ücretsiz OpenCode "
                "modeli ya da başka bir sağlayıcı seç. Oturum bağlamı korundu.")
    return None


def clear_plan_caps() -> None:
    """↻ Yenile: Cursor/Copilot named-model yeteneklerini baştan öğren."""
    try:
        if os.path.exists(_CAPS_PATH):
            os.remove(_CAPS_PATH)
            logger.info("[plan-caps] sıfırlandı (kullanıcı yenilemesi)")
    except Exception as e:
        logger.warning(f"[plan-caps] sıfırlanamadı: {e}")


async def probe_named_models(cli: str, timeout: float = 30.0) -> Optional[bool]:
    """Ucuz adlı-model probe'u: planın adlı modelleri destekleyip desteklemediğini
    tek seferlik öğrenir. True/False döner; belirsizse None (karar verme)."""
    import asyncio
    import subprocess as sp
    # Ortam izin listesi buraya da uygulanır: bu da üçüncü taraf bir CLI ikilisi
    # çalıştırıyor ve `env=` verilmediğinde ebeveynin TAMAMINI devralıyordu —
    # yani sağlayıcı spawn'larında kapatılan sızıntının altıncı yolu. Yetenek
    # probe'u olması fark etmiyor: sızan sır aynı sır (ölçüldü 2026-07-28,
    # 6/6 canary çocuğa geçiyordu). Import gecikmeli, çünkü `cli_base` bu
    # modülü kendisi de kullanıyor ve üst düzey import döngü riski taşıyor.
    from .cli_base import build_spawn_env, env_family
    base = resolve_cli_cmd(cli)
    if not base:
        return None
    if cli == "cursor":
        cmd = [*base, "-p", "--model", "gpt-5.2", "--output-format", "json", "--trust", "Reply: 1"]
    elif cli == "copilot":
        cmd = [*base, "--model", "claude-haiku-4.5", "-s", "--no-color", "--no-auto-update", "-p", "Reply: 1"]
    else:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=build_spawn_env(env_family(cli)),
            creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        blob = (out.decode("utf-8", "ignore") + "\n" + err.decode("utf-8", "ignore"))
        if PLAN_ERROR_RE.search(blob):
            set_named_models_cap(cli, False)
            return False
        if proc.returncode == 0:
            set_named_models_cap(cli, True)
            return True
        return None  # başka tür hata (auth vb.) → plan hakkında hüküm verme
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return None
    except Exception:
        return None


def split_model_id(our_id: str) -> Tuple[Optional[str], str]:
    """(cli_key, cli_model) döndürür; eşleşme yoksa (None, our_id)."""
    if our_id.startswith("cursor-"):
        return "cursor", our_id[len("cursor-"):]
    if our_id.startswith("copilot-"):
        return "copilot", our_id[len("copilot-"):]
    if our_id.startswith("opencode:"):
        return "opencode", our_id.split(":", 1)[1]
    return None, our_id
