"""
Codex'i KALICI INTERAKTIF session olarak süren köprü (codex app-server, JSON-RPC).

Headless `codex exec` (tek atımlık) yerine `codex app-server`'ı tek uzun-yaşayan
süreç olarak sürer — Claude'daki ClaudeSDKClient'ın muadili:
- Sohbet başına tek canlı app-server süreci → bağlam turlar arası korunur (thread).
- NATIVE onay: server `item/commandExecution/requestApproval` /
  `item/fileChange/requestApproval` gönderir → biz command_gates üzerinden onay
  kartına çevirip {"decision":"accept"|"decline"} döneriz (can_use_tool eşdeğeri).
- MCP izin onayı: `item/permissions/requestApproval` ayrı cevap şeması kullanır;
  kabulde istenen izin profili `permissions` alanıyla geri verilir.
- Abonelik (ChatGPT) auth: API key gerekmez — codex kendi login'ini kullanır.

Protokol (codex-cli 0.145.0, ampirik doğrulandı):
- Framing: NDJSON (her mesaj tek satır UTF-8 JSON + '\n'). Content-Length ve
  wire üzerinde `jsonrpc` alanı YOK.
- Akış: initialize → initialized(notif) → thread/start → turn/start → (stream) →
  turn/completed. Onaylar Server→Client request (id + method) olarak gelir.
- decision enum: accept | acceptForSession | decline | cancel.

Yield edilen event'ler AgentEvent.data şekline uyar (claude_sdk_session ile aynı):
text / thinking / tool_call / tool_result / command_approval_needed / response / done / error.
"""
import asyncio
import collections
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence, Set

import unity_file_guard
from agentic.command_gates import APPROVAL_GATES, APPROVAL_RESULTS, APPROVAL_TIMEOUT_S
from agentic.command_gates import register_gate, release_gate

logger = logging.getLogger(__name__)

# UnityMCP'nin 40+ araçlık şema/status mesajı tek NDJSON satırında asyncio'nun
# varsayılan 64 KiB StreamReader limitini aşabiliyor. Makul bir üst sınır koy;
# sınırsız buffer kullanma.
_APP_SERVER_STREAM_LIMIT = 8 * 1024 * 1024

# conversation_id → CodexSession (canlı, isteklerin ötesinde yaşar)
_SESSIONS: Dict[int, "CodexSession"] = {}

# Server→Client onay/etkileşim request method'ları (id + method ile gelir → cevaplanmalı)
_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    # v1 legacy
    "applyPatchApproval",
    "execCommandApproval",
}


def _resolve_codex_appserver_cmd() -> List[str]:
    """`codex app-server` için spawn listesi.

    Tercih: `node <codex.js> app-server` — kalıcı stdio JSON-RPC için cmd.exe
    sarmalayıcısından daha güvenilir VE doğrudan öldürülebilir (cmd /c, node alt
    sürecini öksüz bırakıyor). codex.js, `codex` shim'inin yanındaki
    node_modules/@openai/codex/bin/codex.js'ten türetilir. Bulunamazsa cmd shim'e düşülür.
    """
    import shutil
    node = shutil.which("node")
    codex_bin = shutil.which("codex")
    if node and codex_bin:
        base = os.path.dirname(codex_bin)  # ...\npm  (codex.cmd burada)
        candidates = [
            os.path.join(base, "node_modules", "@openai", "codex", "bin", "codex.js"),
            os.path.join(base, "node_modules", "@openai", "codex", "dist", "codex.js"),
        ]
        for js in candidates:
            if os.path.isfile(js):
                return [node, js, "app-server"]
    # Fallback: .cmd shim'i cmd.exe ile sar (Windows WinError 2 fix)
    from providers.cli_base import BaseCLIProvider
    return BaseCLIProvider._resolve_exec(["codex", "app-server"])


# ── Codex skill kataloğu (Skills galerisi için) ──────────────────────────────
# codex app-server `skills/list` metodundan dolar (initialize + initialized yeter,
# thread/turn YOK → sıfır inference). Claude'un _COMMANDS_META muadili.
_CODEX_SKILLS_CACHE: List[Dict] = []
_CODEX_WARMUP_LOCK = asyncio.Lock()


def get_codex_skills_meta() -> List[Dict]:
    return list(_CODEX_SKILLS_CACHE)


def _parse_codex_skills(result: Optional[dict]) -> List[Dict]:
    """skills/list result'ını [{name, description, displayName, insert}] yapar.
    insert: galeride tıklanınca girdiye yazılacak metin (Codex skill'leri
    defaultPrompt ile çağrılır; yoksa `$<isim>`)."""
    out: List[Dict] = []
    seen = set()
    if not isinstance(result, dict):
        return out
    try:
        for group in (result.get("data") or []):
            for sk in (group.get("skills") or []):
                if not isinstance(sk, dict) or sk.get("enabled") is False:
                    continue
                name = sk.get("name")
                if not name or name in seen:
                    continue
                seen.add(name)
                iface = sk.get("interface") or {}
                desc = (iface.get("shortDescription") or sk.get("description") or "").strip()
                insert = (iface.get("defaultPrompt") or f"${name} ").strip()
                out.append({
                    "name": name,
                    "description": desc,
                    "displayName": (iface.get("displayName") or "").strip(),
                    "insert": insert,
                })
    except Exception as e:
        logger.warning(f"[_parse_codex_skills] parse hatası: {e}")
    return out


async def fetch_codex_skills(cwd: Optional[str] = None, force: bool = False) -> List[Dict]:
    """Throwaway codex app-server açıp skills/list ile skill kataloğunu çeker.
    initialize + initialized yeter (thread/turn YOK → sıfır token). Sonuç cache'lenir."""
    global _CODEX_SKILLS_CACHE
    async with _CODEX_WARMUP_LOCK:
        if _CODEX_SKILLS_CACHE and not force:
            return list(_CODEX_SKILLS_CACHE)

        spawn = _resolve_codex_appserver_cmd()
        # İZİN LİSTESİ (bkz. cli_base.build_spawn_env). Bu spawn noktası sohbetten
        # bağımsız çalıştığı için gözden kaçmaya en açık olanı; ölçümde o da
        # altı canary'nin altısını birden çocuğa geçiriyordu.
        from providers.cli_base import build_spawn_env
        env = build_spawn_env(family="codex", overrides={"NO_COLOR": "1"})
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *spawn,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                cwd=(cwd if (cwd and os.path.isdir(cwd)) else None),
                limit=_APP_SERVER_STREAM_LIMIT,
            )

            req_id = 0

            async def send(method, params=None, notify=False):
                nonlocal req_id
                obj: Dict[str, Any] = {"method": method}
                rid = None
                if not notify:
                    req_id += 1
                    rid = req_id
                    obj["id"] = rid
                if params is not None:
                    obj["params"] = params
                proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
                await proc.stdin.drain()
                return rid

            await send("initialize", {"clientInfo": {"name": "gamachine", "version": "0.1.0"}})
            await send("initialized", notify=True)
            skills_id = await send("skills/list", {})

            result = None

            async def read_until():
                nonlocal result
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("id") == skills_id and ("result" in msg or "error" in msg):
                        result = msg.get("result")
                        return

            try:
                await asyncio.wait_for(read_until(), timeout=20)
            except asyncio.TimeoutError:
                logger.warning("[fetch_codex_skills] skills/list zaman aşımı")

            meta = _parse_codex_skills(result)
            if meta:
                _CODEX_SKILLS_CACHE = meta
            logger.info(f"[fetch_codex_skills] {len(meta)} Codex skill yakalandı")
            return list(_CODEX_SKILLS_CACHE)
        except Exception as e:
            logger.warning(f"[fetch_codex_skills] hata: {e}")
            return list(_CODEX_SKILLS_CACHE)
        finally:
            if proc is not None:
                try:
                    if proc.stdin and not proc.stdin.is_closing():
                        proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    # transport'ları düzgün kapat → "I/O operation on closed pipe" gürültüsü olmasın
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass


# How many finished turn ids to remember. Staleness here is measured in the
# milliseconds between one turn ending and the next starting, not in turns,
# so a handful covers every reachable ordering; the bound exists so a long
# session cannot grow the set without limit.
_RETIRED_TURN_IDS_MAX = 8
# fileChange items whose paths wait for their approval request. Codex asks for
# approval of one patch at a time, so a handful covers it.
_FILE_CHANGES_MAX = 64


def _change_refusal(path, kind, move_path, base: str):
    if kind == "delete":
        return unity_file_guard.check_delete(path, base)
    if move_path:
        return unity_file_guard.check_move(path, move_path, base)
    return unity_file_guard.check_write(path, base)


def _file_change_refusal(changes, base: str):
    """The Unity file rule over a v2 fileChange item's `changes`
    ([{path, kind: {type, move_path}, diff}], codex-cli 0.157.0 schema)."""
    for change in changes or ():
        if not isinstance(change, dict):
            continue
        kind = change.get("kind") if isinstance(change.get("kind"), dict) else {}
        refusal = _change_refusal(change.get("path"), kind.get("type"),
                                  kind.get("move_path"), base)
        if refusal is not None:
            return refusal
    return None


def _command_text(command) -> str:
    # v1 execCommandApproval sends argv; v2 sends one string.
    if isinstance(command, list):
        return " ".join(f'"{a}"' if isinstance(a, str) and " " in a and '"' not in a else str(a)
                        for a in command)
    return command if isinstance(command, str) else ""


def _notification_turn_id(params: dict) -> Optional[str]:
    """Turn id an app-server notification declares, or ``None`` when it declares none.

    Two shapes are on the wire: nested ``params.turn.id`` (``turn/completed``)
    and a flat ``params.turnId`` (item notifications). The flat one is matched by
    NORMALIZED KEY NAME, not by exact spelling — this file already paid for the
    other approach in `bul_onay_hakemi`: a fixed spelling list grows one edge per
    casing (``turnId`` / ``turn_id`` / ``TurnID``) and every miss fails silently,
    while "keep letters and digits, lowercase" is a finite rule that also covers
    spellings nobody has seen yet.

    Deliberately shallow: only the notification envelope is inspected. A turn id
    found deep inside an item payload would describe the item, not the message's
    own turn, and treating it as the message's turn would reject live events.
    """
    if not isinstance(params, dict):
        return None
    for key, value in params.items():
        if isinstance(key, str) and "".join(c for c in key if c.isalnum()).lower() == "turnid":
            if isinstance(value, str) and value:
                return value
    turn = params.get("turn")
    if isinstance(turn, dict):
        tid = turn.get("id")
        if isinstance(tid, str) and tid:
            return tid
    return None


def _describe_approval(method: str, params: dict) -> str:
    """Onay kartında gösterilecek kısa açıklama."""
    cmd = params.get("command")
    if cmd:
        return cmd if isinstance(cmd, str) else json.dumps(cmd, ensure_ascii=False)[:200]
    reason = params.get("reason")
    if reason:
        return str(reason)[:200]
    return method


# Hakem alanı VAR ama değeri okunamadı — "yok" ile karıştırılmaması gereken
# üçüncü durum. Köşeli parantezler bilerek: Codex'in hiçbir meşru hakem değeri
# bu biçimde olamaz, dolayısıyla gerçek bir değerle çakışamıyor.
HAKEM_BELIRSIZ = "<belirsiz>"


def bul_onay_hakemi(govde) -> "str | None":
    """`thread/start` yanıtında AKTİF onay hakemini arar; bulamazsa ``None``.

    ⚠️ Neden derin ve biçimden bağımsız arama (dış denetim bulgusu,
    ``fail-open-validation``): ilk sürüm yalnız ``result.approvalsReviewer`` ve
    ``result.thread.approvalsReviewer`` adreslerine bakıyordu. Probe ile
    üretildi: değer ``approvals_reviewer`` (snake_case) ya da bir düzey daha
    derinde geldiğinde arama BOŞ dönüyordu, ``None`` dalı da bilerek geçirgen
    olduğu için oturum **sessizce başlıyordu** — yani gerçekte ``auto_review``
    koşarken ürün hiçbir şey fark etmiyordu. "Bulamadım" ile "yok" aynı sonuca
    çıkıyordu ve bu, muhafızı olmadığı hâle döndürüyordu.

    Ölçüt ADRES değil ANAHTAR ADI: ``approvals_reviewer`` / ``approvalsReviewer``
    hangi derinlikte olursa olsun yakalanıyor. Bu deponun kayıtlı dersi — bir
    muhafız her turda yeni bir kenar veriyorsa eşiği daraltma, ölçütü değiştir.

    İlk bulunan değer dönüyor: aynı yanıtta iki farklı hakem taşınması
    beklenmiyor, ve taşınsaydı ikisinden biri zaten ``user`` olmayacağı için
    çağıran fail-closed dalına düşerdi.

    ⚠️ ÜÇÜNCÜ bir durum var (doğrulama turu bulgusu
    ``deep-reviewer-search-still-fail-open-on-non-string-values``): ölçüt
    "adres" yerine "anahtar adı" olunca bile arama iki yerde sessizce ``None``
    dönüyordu — anahtar bulunup DEĞERİ dizge olmadığında (ör.
    ``{"approvalsReviewer": {"name": "auto_review"}}``) ve arama derinlik
    sınırında kesildiğinde. ``None`` dalı bilerek geçirgen olduğu için ikisi de
    "yok" sayılıyor ve oturum başlıyordu; yani kapatıldığı söylenen fail-open
    sınıfı iki kenardan hâlâ açıktı.

    Artık üç durum ayrı: **değer** (doğrula), ``None`` (alan gerçekten yok →
    uyar ve geç), ``HAKEM_BELIRSIZ`` (alan var ama okunamadı, ya da arama
    eksik kaldı → fail-closed). "Bulamadım" ile "yok" bir daha aynı şey değil.
    """
    # ⚠️ ÖLÇÜT ÜÇÜNCÜ KEZ DEĞİŞTİ (dış denetim, DeepSeek, 2 Ağu 2026 —
    # `onay-hakemi-key-vocabulary`). Sıra şuydu: iki SABİT ADRES → anahtar ADI
    # (`-` → `_` normalizasyonuyla) → ve orada da kaçış vardı, çünkü
    # normalizasyon AYIRICILARI SAYIYORDU. `approvals reviewer`,
    # `approvals__reviewer`, `approvals.reviewer` ve `Approvals Reviewer`
    # yazımlarının dördü de sessizce `None`'a düşüyor, `None` dalı bilerek
    # geçirgen olduğu için muhafız hiç konuşmadan açılıyordu (canlı ölçüldü).
    #
    # ⭐ Bu deponun kayıtlı dersi burada üçüncü kez ödendi: bir muhafız her
    # turda yeni bir kenar veriyorsa eşiği/listeyi genişletme, ÖLÇÜTÜ değiştir.
    # Ayırıcı saymak bitmeyen bir liste; "harf ve rakam DIŞINDAKİ her şeyi at"
    # sonlu bir kural. Yeni bir ayırıcı icat edilse de bu kural onu kapsıyor.
    def _sadelestir(ad: str) -> str:
        return "".join(ch for ch in ad if ch.isalnum()).lower()

    hedef = {"approvalsreviewer"}
    # Arama eksik kaldıysa YOKLUK iddia edilemez; bunu çağırana taşımak için.
    kesildi = False

    def gez(dugum, derinlik: int = 0):
        nonlocal kesildi
        # Derinlik sınırı: kötü biçimli ya da döngüsel bir yanıt bu aramayı
        # sonsuza sürüklememeli. Sınır cömert tutuldu (ölçülen yanıtlar 3-4
        # seviye) çünkü buraya çarpmak artık turu KESİYOR — dar bir sınır,
        # meşru bir yanıtta ürünü durdururdu.
        if derinlik > 12:
            kesildi = True
            return None
        if isinstance(dugum, dict):
            for k, v in dugum.items():
                if isinstance(k, str) and _sadelestir(k) in hedef:
                    if isinstance(v, str):
                        return v
                    # Anahtar VAR ama değeri okunamıyor: bu "yok" değil,
                    # "doğrulanamadı". Geçirgen dala düşmesi tam olarak
                    # kapatmaya çalıştığımız açıktı.
                    return HAKEM_BELIRSIZ
                bulunan = gez(v, derinlik + 1)
                if bulunan is not None:
                    return bulunan
        elif isinstance(dugum, (list, tuple)):
            for v in dugum:
                bulunan = gez(v, derinlik + 1)
                if bulunan is not None:
                    return bulunan
        return None

    bulunan = gez(govde)
    if bulunan is None and kesildi:
        # Aramayı tamamlayamadık; "yok" demek burada bir iddia olurdu.
        return HAKEM_BELIRSIZ
    return bulunan


def dogrula_onay_hakemi(sonuc, conversation_id="?") -> "str | None":
    """`thread/start` sonucundaki hakem `user` değilse FIRLATIR.

    ⚠️ Bu gövde `start()`'ın içinde satır içiydi ve tam olarak bu yüzden
    testlerden kaçmıştı (dış denetim bulgusu `test-double-divergence`): testler
    yerel bir KOPYAYI çağırıyordu, dolayısıyla bu blok silinse ya da koşulu
    tersine dönse hiçbir test kırılmıyordu. Ayrı bir gövde olması, testin
    ürünün kendisini çalıştırabilmesi için — kopyalanabilir bir mantık er ya da
    geç kopyasından ayrışıyor.

    Dönüş, bulunan hakem (ya da bulunamadıysa ``None``) — çağıranın loglaması
    için değil, testin ölçebilmesi için.
    """
    hakem = bul_onay_hakemi(sonuc)
    if hakem == HAKEM_BELIRSIZ:
        # Alan VAR ama okunamadı (dizge olmayan değer, ya da arama derinlik
        # sınırında kesildi). Sabitlemenin tuttuğunu söyleyemiyoruz, dolayısıyla
        # tur başlamıyor — "doğrulayamadım" burada "sorun yok" demek değil.
        raise RuntimeError(
            "Codex onay hakemi alanı okunamadı: yanıt beklenmedik bir biçimde "
            "geldi, onayı kimin vereceği DOĞRULANAMADI; tur başlatılmadı."
        )
    if hakem is not None and hakem != "user":
        # Fail-closed: onayı kimin verdiğini bilmiyorsak tur başlamamalı.
        # `auto_review` ve eski `guardian_subagent`'ın İKİSİ de reddediliyor —
        # ikisi de kararı kullanıcıdan alıp bir modele veriyor.
        raise RuntimeError(
            "Codex onay hakemi 'user' değil: "
            f"{hakem!r}. Onay isteği kullanıcıya değil bir modele "
            "gidecekti; tur başlatılmadı. `~/.codex/config.toml` içindeki "
            "`approvals_reviewer` ayarını kontrol edin."
        )
    if hakem is None:
        # Alanı HİÇBİR YERDE bulamadık. Turu kırmıyoruz (alanı döndürmeyen bir
        # Codex sürümü olabilir ve orada açığın var olduğu ölçülmedi), ama
        # sessiz de kalmıyoruz: sabitlemenin doğrulanamadığı tek durum bu.
        logger.warning(
            f"[CodexSession:{conversation_id}] thread/start yanıtında onay "
            "hakemi alanı YOK — sabitlendiği DOĞRULANAMADI."
        )
    return hakem


def _trusted_mcp_config(forward_env: Sequence[str] = ()) -> dict:
    """IDE'nin kendi MCP'leri için Codex-katmanı onay politikasını üret.

    unityai içindeki kalıcı dosya/terminal mutasyonları kendi ``approval_bridge``
    kapısından geçer. unityMCP sahne işlemleri de tasarım gereği doğrudan çalışır.
    Bu nedenle Codex'in ikinci bir MCP onayı istemesi hem gereksizdir hem de
    app-server istemcisinde çift-onay üretir.

    ``forward_env``: names Codex should hand to our stdio servers. Codex gives a
    stdio MCP child only its default set + ``env`` + ``env_vars`` (measured,
    0.157.0: GAMACHINE_CONVERSATION_ID in the app-server's env did not reach the
    child; ``env_vars`` in this thread config did, the same override as ``-c``
    on the app-server argv did not).
    """
    configured = _configured_codex_mcp_names()
    servers = {}
    if "unityai" in configured:
        servers["unityai"] = {"default_tools_approval_mode": "approve"}
    try:
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        if unity_mcp_manager.is_running() and "unityMCP" in configured:
            servers["unityMCP"] = {"default_tools_approval_mode": "approve"}
    except Exception as exc:
        # Unity yöneticisi henüz yüklenmemişse unityai yine kullanılabilir.
        logger.debug("[CodexSession] unityMCP onay politikası belirlenemedi: %s", exc)
    if forward_env:
        # Registered names only: an entry without a transport makes Codex
        # reject the whole thread/start.
        for name in ("unityai", "unityMCP"):
            if name in configured:
                servers.setdefault(name, {})["env_vars"] = list(forward_env)
    return {"mcp_servers": servers}


def _configured_codex_mcp_names() -> Set[str]:
    """Kullanıcının global Codex config'indeki MCP adlarını güvenle oku.

    Thread config'inde mevcut olmayan bir MCP için yalnız
    ``default_tools_approval_mode`` vermek, transport alanı bulunmadığından
    Codex'in tüm ``thread/start`` isteğini reddetmesine yol açar.
    """
    try:
        import tomllib

        codex_home = os.environ.get("CODEX_HOME")
        if not codex_home:
            codex_home = os.path.join(os.path.expanduser("~"), ".codex")
        with open(os.path.join(codex_home, "config.toml"), "rb") as fh:
            config = tomllib.load(fh)
        servers = config.get("mcp_servers", {})
        return set(servers) if isinstance(servers, dict) else set()
    except Exception as exc:
        logger.debug("[CodexSession] Codex MCP config adları okunamadı: %s", exc)
        return set()


class CodexSession:
    """Tek bir sohbete ait kalıcı codex app-server süreç sarmalayıcısı."""

    def __init__(
        self,
        conversation_id: int,
        *,
        model: Optional[str] = None,
        cwd: Optional[str] = None,
        approval_timeout: float = APPROVAL_TIMEOUT_S,
        auto_approve: bool = False,
        effort: Optional[str] = None,
    ):
        self.conversation_id = conversation_id
        self.model = model
        self.cwd = cwd
        # Reasoning effort (minimal..xhigh; max yalnız gpt-5.6). Launch-time config'tir
        # (`-c model_reasoning_effort=`) — oturum ortasında değişemez; değişince
        # agent_runner session'ı yeniden kurar (Claude'daki desenin aynısı).
        self.effort = effort
        self.approval_timeout = approval_timeout
        # Oto mod: True ise onay kartı GÖSTERİLMEZ, gelen onay isteklerine otomatik "accept".
        self.auto_approve = auto_approve

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task] = None
        self._started = False
        self._turn_lock = asyncio.Lock()
        self._req_id = 0
        self._pending: Dict[int, asyncio.Future] = {}   # bizim istek id → Future
        self._out_q: Optional[asyncio.Queue] = None      # aktif tur event kuyruğu
        self._final_text = ""
        self.thread_id: Optional[str] = None
        self._current_turn_id: Optional[str] = None
        self._cancel_event: Optional[asyncio.Event] = None
        # Aktif turun sonlanma olayı gitti mi — bkz. `_emit_terminal`.
        self._terminal_sent = False
        # Sonlanmış turların kimlikleri. Biten bir tur SONSUZA DEK bayattır;
        # `_handle_notification` bunu, canlı turun kimliği henüz bilinmezken
        # bile reddedebilmek için kullanıyor (bkz. oradaki gerekçe).
        self._retired_turn_ids: "collections.deque[str]" = collections.deque(
            maxlen=_RETIRED_TURN_IDS_MAX)
        self._active_gate_ids: Set[str] = set()
        # itemId -> changes of fileChange items (see `_remember_file_change`).
        self._file_changes: "collections.OrderedDict[str, list]" = collections.OrderedDict()
        # İlk turda DB bağlam özeti enjekte edildi mi (sonraki turlarda thread hatırlıyor)
        self._ctx_injected = False

    # ── Süreç yaşam döngüsü ──────────────────────────────────────────────
    @property
    def is_live(self) -> bool:
        """app-server süreci ŞU AN ayakta mı — bkz. `ClaudeSDKSession.is_live`.

        Claude'daki `_broken` bayrağının burada karşılığı yok çünkü gerek yok:
        okuma döngüsü süreç öldüğünde `_started`ı zaten False'a çekiyor, yani
        tek bayrak hem "hiç başlamadı" hem "öldü" durumunu taşıyor.
        """
        return self._started

    async def start(self):
        if self._started:
            return
        spawn = _resolve_codex_appserver_cmd()
        if self.effort:
            # Global -c override subcommand'dan sonra da geçerli (codex exec ile aynı desen)
            spawn = spawn + ["-c", f"model_reasoning_effort={self.effort}"]
        # Abonelik auth: API key env'lerini ENJEKTE ETME (codex kendi login'ini kullanır).
        # İZİN LİSTESİ: kullanıcının ortamında zaten duran OPENAI_* geçer (env ile
        # giriş yapan kurulumlar kırılmasın), ama Anthropic/Gemini anahtarları ve
        # backend sırları geçmez — ölçüm ve gerekçe cli_base.build_spawn_env'de.
        from providers.cli_base import build_spawn_env
        from spawn_env import conversation_env
        owner_env = conversation_env(self.conversation_id)
        env = build_spawn_env(family="codex", overrides={"NO_COLOR": "1", **owner_env})
        self._proc = await asyncio.create_subprocess_exec(
            *spawn,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=(self.cwd if (self.cwd and os.path.isdir(self.cwd)) else None),
            limit=_APP_SERVER_STREAM_LIMIT,
        )
        self._read_task = asyncio.create_task(self._read_loop())

        # 1) initialize + initialized (zorunlu)
        await self._request("initialize", {
            "clientInfo": {"name": "gamachine", "version": "0.1.0"},
        }, timeout=30)
        await self._notify("initialized")

        # 2) thread/start — kalıcı thread. read-only sandbox + on-request → her mutasyon
        #    escalation ile native onaya gider (oto modda otomatik accept'lenir).
        params: Dict[str, Any] = {
            "cwd": self.cwd or os.getcwd(),
            "approvalPolicy": "on-request",
            "sandbox": "read-only",
            # ⚠️ ONAYI KİMİN VERECEĞİNİ SABİTLE — ölçülmüş açık, 2 Ağu 2026.
            #
            # Bu alan gönderilmediğinde Codex, reviewer'ı kullanıcının KENDİ
            # `~/.codex/config.toml` dosyasından okuyor. O dosyada
            # `approvals_reviewer = "auto_review"` yazıyorsa onay isteği ürünün
            # kapısına HİÇ gelmiyor; bir LLM alt-ajanına ("guardian") gidiyor ve
            # o `{"outcome":"allow"}` diyerek yazımı onaylıyor.
            #
            # Sahada üretildi: kullanıcı Adım Adım modunda dosya oluşturulduğunu
            # gördü, hiçbir onay kartı çıkmadı. Tur kaydında guardian alt-ajanı
            # `approval_policy: "never"` ile koşmuş durumda.
            #
            # ⭐ Sınıfın şekli: ürünün güvenlik vaadi, ürünün KONTROL ETMEDİĞİ
            # bir dış ayara bağlıydı. Varsayılana güvenmek yetmiyor — varsayılan
            # `user` ama config onu sessizce eziyor. Bu yüzden AÇIKÇA yazılıyor.
            #
            # Ölçüm (Codex 0.146.0, canlı 3 tur): alan yokken yanıt
            # `auto_review` döndü; `"user"` ile `user` döndü. Şema:
            # `ApprovalsReviewer = "user" | "auto_review" | "guardian_subagent"`.
            "approvalsReviewer": "user",
            # Eski ``codex exec`` yolu bu iki override'ı CLI ``-c`` bayrağıyla
            # geçiriyordu. Kalıcı app-server yolu da aynı güven modelini thread
            # config'i üzerinden taşımalı; aksi halde salt-okunur MCP araçları
            # bile "user rejected MCP tool call" ile düşer.
            "config": _trusted_mcp_config(forward_env=tuple(owner_env)),
        }
        if self.model:
            params["model"] = self.model
        resp = await self._request("thread/start", params, timeout=30)
        if (resp or {}).get("error"):
            error = (resp or {}).get("error") or {}
            raise RuntimeError(
                f"Codex thread/start başarısız: {error.get('message') or error}"
            )
        _result = (resp or {}).get("result") or {}
        self.thread_id = (_result.get("thread") or {}).get("id")
        if not self.thread_id:
            raise RuntimeError("Codex thread/start yanıtında thread id bulunamadı.")

        # ⚠️ İSTEDİĞİMİZİ ALDIK MI? Yanıt AKTİF reviewer'ı geri veriyor, yani
        # doğrulama bize ek bir tur ya da tek bir token'a mal olmuyor. Bunu
        # istemekle almak arasındaki farkı ölçmeden geçmek, bu deponun tekrar
        # tekrar ödediği bedel: bir bayrağı GÖNDERDİĞİNİ doğrulayan test,
        # bayrağın IŞIRDIĞINI doğrulamıyor.
        dogrula_onay_hakemi(_result, self.conversation_id)
        self._started = True
        logger.info(f"[CodexSession:{self.conversation_id}] başlatıldı (model={self.model or 'default'}, thread={self.thread_id})")

    async def close(self):
        # Önce okuma görevini durdur (kill'DEN ÖNCE — Windows Proactor'da kill + bekleyen
        # readline yarışı proc.wait()'i kilitleyebiliyor), SONRA süreci öldür.
        rt = self._read_task
        self._read_task = None
        if rt is not None:
            rt.cancel()
            try:
                await rt
            except BaseException:
                pass
        if self._proc is not None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                pass
        self._proc = None
        self._started = False
        # Bekleyen istek future'larını serbest bırak (yoksa _request sonsuz bekler)
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    # ── Düşük seviye JSON-RPC (NDJSON) ───────────────────────────────────
    async def _send(self, obj: dict):
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("codex app-server süreci yok")
        self._proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def _request(self, method: str, params: dict, timeout: float = 60) -> dict:
        self._req_id += 1
        rid = self._req_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        # Codex app-server JSON-RPC 2.0 semantiği kullanır fakat wire mesajında
        # ``jsonrpc`` alanını kabul etmez (0.145.0'da alan sessiz timeout üretir).
        await self._send({"id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    async def _notify(self, method: str, params: Optional[dict] = None):
        obj: Dict[str, Any] = {"method": method}
        if params is not None:
            obj["params"] = params
        await self._send(obj)

    async def _read_loop(self):
        """Tek okuma döngüsü: cevap / server-request (onay) / notification ayrımı."""
        assert self._proc and self._proc.stdout
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break  # süreç öldü / EOF
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(msg)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception(f"[CodexSession:{self.conversation_id}] read loop hatası")
        finally:
            # Süreç bittiyse aktif turu kapat
            self._started = False
            await self._emit_terminal({
                "type": "error",
                "message": "Codex app-server süreci beklenmedik şekilde kapandı.",
            })

    async def _dispatch(self, msg: dict):
        has_id = "id" in msg
        has_method = "method" in msg
        if has_id and not has_method:
            # bizim isteğimize cevap
            fut = self._pending.get(msg["id"])
            if fut and not fut.done():
                fut.set_result(msg)
        elif has_id and has_method:
            # Server→Client request (onay / etkileşim) → arka planda işle (read loop bloklamasın)
            asyncio.create_task(self._handle_server_request(msg))
        elif has_method:
            # notification (stream)
            self._remember_file_change(msg)
            await self._handle_notification(msg)

    def _remember_file_change(self, msg: dict) -> None:
        """Keep a fileChange item's paths for its approval request.

        `item/fileChange/requestApproval` carries only itemId; the paths are in
        the item notifications. This runs in the read loop, in wire order, and
        before the stale-turn filter and the no-queue return of
        `_handle_notification`, so every item seen is remembered.
        """
        method = msg.get("method")
        params = msg.get("params") or {}
        if method in ("item/started", "item/completed"):
            item = params.get("item") or {}
            if not isinstance(item, dict) or item.get("type") != "fileChange":
                return
            item_id, changes = item.get("id"), item.get("changes")
            if method == "item/completed":
                self._file_changes.pop(item_id, None)
                return
        elif method == "item/fileChange/patchUpdated":
            item_id, changes = params.get("itemId"), params.get("changes")
        else:
            return
        if isinstance(item_id, str) and isinstance(changes, list):
            self._file_changes[item_id] = changes
            self._file_changes.move_to_end(item_id)
            while len(self._file_changes) > _FILE_CHANGES_MAX:
                self._file_changes.popitem(last=False)

    def _unity_file_refusal(self, method: str, params: dict):
        base = self.cwd or ""
        if method == "item/fileChange/requestApproval":
            return _file_change_refusal(self._file_changes.get(params.get("itemId")), base)
        if method == "applyPatchApproval":
            changes = params.get("fileChanges")
            for path, change in (changes.items() if isinstance(changes, dict) else ()):
                change = change if isinstance(change, dict) else {}
                refusal = _change_refusal(path, change.get("type"), change.get("move_path"), base)
                if refusal is not None:
                    return refusal
            return None
        if method in ("item/commandExecution/requestApproval", "execCommandApproval"):
            cwd = params.get("cwd") if isinstance(params.get("cwd"), str) else ""
            return unity_file_guard.check_shell(_command_text(params.get("command")), cwd or base)
        return None

    # ── Server→Client request: native onay köprüsü ───────────────────────
    async def _handle_server_request(self, msg: dict):
        rid = msg["id"]
        method = msg.get("method", "")
        params = msg.get("params", {}) or {}
        try:
            if method in _APPROVAL_METHODS:
                decision = await self._resolve_approval(method, params)
                if method == "item/permissions/requestApproval":
                    # Codex 0.145+ bu request için ``decision`` kabul etmez.
                    # Kabulde talep edilen profili aynen grant et; redde boş
                    # profil dön. ``scope=turn`` izni oturumlar arasında kalıcı
                    # hale getirmeden mevcut turla sınırlar.
                    permissions = params.get("permissions", {}) if decision == "accept" else {}
                    await self._send({
                        "id": rid,
                        "result": {
                            "permissions": permissions,
                            "scope": "turn",
                        },
                    })
                else:
                    await self._send({"id": rid, "result": {"decision": decision}})
            elif method == "item/tool/requestUserInput":
                # Auto turunda modelin yapılandırılmış soru aracıyla "devam edeyim
                # mi?" diye beklemesini de engelle. Gerçek onaylar yukarıdaki
                # requestApproval yollarından zaten otomatik kabul edilir.
                value = (
                    "Proceed using your best judgment without asking for confirmation."
                    if self.auto_approve else ""
                )
                await self._send({"id": rid, "result": {"value": value}})
            else:
                # Bilinmeyen server-request: takılmamak için boş cevap (ör. token refresh)
                await self._send({"id": rid, "result": {}})
        except Exception as e:
            logger.warning(f"[CodexSession:{self.conversation_id}] server-request cevap hatası ({method}): {e}")

    async def _resolve_approval(self, method: str, params: dict) -> str:
        """Onay isteğini kartla/oto çöz → 'accept' | 'decline'."""
        # Fixed Unity file rule, before the mode: no card, and auto cannot pass it.
        # Codex's decline has no reason field, so the model only sees a decline.
        refusal = self._unity_file_refusal(method, params)
        if refusal is not None:
            logger.warning(f"[CodexSession:{self.conversation_id}] Unity file rule declined {method}: {refusal.path}")
            if self._out_q is not None:
                await self._out_q.put({"type": "tool_result", "tool": method, "success": False,
                                       "summary": refusal.summary})
            return "decline"
        # Oto mod: kart gösterme, otomatik onayla
        if self.auto_approve:
            return "accept"

        out_q = self._out_q
        gate_id = uuid.uuid4().hex
        # gate'i emit'ten ÖNCE kaydet
        ev = register_gate(gate_id, self.conversation_id)
        self._active_gate_ids.add(gate_id)
        if out_q is not None:
            await out_q.put({
                "type": "command_approval_needed",
                "gate_id": gate_id,
                "tool": method,
                "command": _describe_approval(method, params),
            })
        res = await self._wait_gate(ev, APPROVAL_GATES, APPROVAL_RESULTS, gate_id)
        return "accept" if bool(res) else "decline"

    async def _wait_gate(self, ev: asyncio.Event, gates: dict, results: dict, gate_id: str):
        try:
            await asyncio.wait_for(ev.wait(), timeout=self.approval_timeout)
            return results.pop(gate_id, None)
        except asyncio.TimeoutError:
            logger.warning(f"[CodexSession:{self.conversation_id}] onay zaman aşımı gate={gate_id}")
            return None
        finally:
            release_gate(gate_id)

    # ── Turun TEK sonlanma olayı ─────────────────────────────────────────
    async def _emit_terminal(self, event: dict) -> bool:
        """Put the turn's one terminal event on the queue, then the sentinel.

        The contract is exactly one terminal per turn — never zero, never two.
        Both ends were reachable here (audit, 30 Aug 2026). Zero: `cancel_turn`
        set `_cancel_event`, which nothing consumed, so a rejected interrupt
        left the caller blocked in `out_q.get()` forever and Stop hung the turn.
        Two: once the cancel path emits its own terminal, the app-server's later
        `turn/completed` would add a second, which is the double-terminal bug
        fixed elsewhere the same day. This flag is the single gate both go
        through, so whichever ending arrives first is the turn's ending.
        """
        q = self._out_q
        if q is None or self._terminal_sent:
            return False
        self._terminal_sent = True
        # The turn is over, so its id is stale from here on. Recorded at the one
        # gate every ending goes through, which is why no ending can forget to.
        if self._current_turn_id:
            self._retired_turn_ids.append(self._current_turn_id)
        await q.put(event)
        await q.put(None)  # sentinel → stream biter
        return True

    # ── Notification → event dict eşlemesi ───────────────────────────────
    async def _handle_notification(self, msg: dict):
        out_q = self._out_q
        if out_q is None:
            return  # tur dışı bildirim (ör. remoteControl/status) — yok say
        method = msg.get("method", "")
        params = msg.get("params", {}) or {}

        # ── Bu bildirim ŞU ANKİ tura mı ait? ─────────────────────────────
        # A notification names its turn and the session already tracks the turn
        # that is running, so no parallel notion of "which turn" is introduced
        # here. When both ids are known and differ, the message is about a turn
        # NOBODY IS WAITING FOR — typically the app-server's delayed
        # `turn/completed` for a turn we cancelled, landing after the next turn
        # installed its own queue and reset `_terminal_sent`. It is DROPPED, not
        # forwarded and not queued for later: the old turn already received its
        # one terminal from `cancel_turn`, and forwarding would end the live turn
        # early while showing the dead turn's answer as its own (measured,
        # 30 Aug 2026).
        #
        # The test lives at the TOP of the handler, not inside `turn/completed`:
        # `error` is the other terminal producer and would end the live turn the
        # same way, while a stale `item/*` would splice the dead turn's text and
        # tool output into the live turn's transcript. A gate that covers one
        # message type while another stays open is this repo's most repeated
        # failure.
        #
        # Only two KNOWN and DIFFERENT ids reject. An unknown id on either side
        # is not evidence of staleness: `stream()` clears `_current_turn_id` and
        # only learns the new one when `turn/start` returns, so the live turn's
        # own first notifications can legitimately arrive while the session-side
        # id is still None — rejecting those would drop real work.
        #
        # That rule is right and stays, but it left a window, and the third
        # verification round measured it (30 Aug 2026): between `stream()`
        # installing the new queue and `turn/start` replying, the session-side
        # id is None, so a delayed completion for the turn just cancelled was
        # accepted and spent the new turn's terminal gate — the new turn ended
        # instantly carrying the dead turn's answer. Worse, nothing downstream
        # could notice: the stale `done` carries no `stop_reason`, so
        # `_normalize_session_event` stamps it `complete` and it reads as a
        # perfectly ordinary success.
        #
        # The window is closed from the other side instead of by tightening the
        # rule above. A turn that has ENDED is stale forever, whatever the live
        # turn's id happens to be at this instant — so retired ids are
        # remembered and always rejected. `_RETIRED_TURN_IDS_MAX` bounds the
        # memory; a handful is plenty because staleness is measured in the
        # milliseconds between one turn ending and the next starting, not in
        # turns.
        msg_turn_id = _notification_turn_id(params)
        if msg_turn_id and msg_turn_id in self._retired_turn_ids:
            logger.debug(
                "[CodexSession:%s] BİTMİŞ tura ait bildirim atlandı "
                "(method=%s, bildirim=%s)",
                self.conversation_id, method, msg_turn_id,
            )
            return
        if msg_turn_id and self._current_turn_id and msg_turn_id != self._current_turn_id:
            logger.debug(
                "[CodexSession:%s] geçmiş tura ait bildirim atlandı "
                "(method=%s, bildirim=%s, aktif=%s)",
                self.conversation_id, method, msg_turn_id, self._current_turn_id,
            )
            return

        if method == "item/agentMessage/delta":
            delta = params.get("delta", "")
            if delta:
                self._final_text += delta
                await out_q.put({"type": "text", "content": delta})

        elif method == "item/started":
            item = params.get("item", {}) or {}
            itype = item.get("type", "")
            # Metin/akıl yürütme delta ile gelir; userMessage = kullanıcının kendi mesajının
            # yankısı (araç değil) → gizle.
            if itype in ("agentMessage", "reasoning", "userMessage"):
                return
            # Araç başlangıcı (commandExecution / fileChange / mcp tool vb.)
            await out_q.put({"type": "tool_call", "tool": itype or "codex",
                             "tool_id": item.get("id"),
                             "arguments": self._item_args(item),
                             "summary": self._item_summary(item)})

        elif method == "item/completed":
            item = params.get("item", {}) or {}
            itype = item.get("type", "")
            if itype == "userMessage":
                return  # kullanıcı mesajı yankısı — gösterme
            if itype == "agentMessage":
                # Final metin zaten delta'larla akıtıldı; tekrar ekleme (çift sayım önlemi)
                txt = item.get("text", "")
                if txt and not self._final_text:
                    self._final_text = txt
                return
            if itype == "reasoning":
                txt = item.get("text", "")
                if txt:
                    await out_q.put({"type": "thinking", "text": txt})
                return
            # Araç sonucu — çıktı (komut stdout'u / değişiklik dökümü) chip'in ÇIKTI paneline
            exit_code = item.get("exitCode")
            success = (exit_code in (0, None)) and item.get("status") not in ("declined", "failed")
            await out_q.put({"type": "tool_result", "tool": itype or "codex",
                             "tool_id": item.get("id"),
                             "success": success,
                             "summary": self._item_summary(item),
                             "output": self._item_output(item)})

        elif method == "turn/completed":
            # Tur zaten sonlandıysa (iptal) `response` de basma: sentinel'den
            # sonrası tüketilmiyor, ama turun cevabını ikinci kez kuyruğa koymak
            # kaydı bozacak bir yarış bırakır.
            if self._terminal_sent:
                return
            await out_q.put({"type": "response", "content": self._final_text})
            await self._emit_terminal({"type": "done", "session_id": self.thread_id})

        elif method == "error":
            # willRetry=true → GEÇİCİ hata (codex kendi yeniden deniyor): turu
            # ÖLDÜRME, ham JSON'u kullanıcıya basma; kısa bir durum notu göster.
            # (Canlı yakalandı: "Reconnecting... 2/5" + willRetry:true ham error
            # olarak yüzeye vuruyor ve turu bitiriyordu.)
            if params.get("willRetry"):
                _note = params.get("message", "yeniden bağlanılıyor…")
                await out_q.put({"type": "thinking", "text": f"🔁 Codex: {_note}"})
                return
            _err = params.get("message") or json.dumps(params, ensure_ascii=False)[:300]
            _details = ((params.get("codexErrorInfo") or {}).get("additionalDetails")
                        or params.get("additionalDetails") or "")
            if _details and _details not in _err:
                _err = f"{_err} — {_details}"[:400]
            await self._emit_terminal({"type": "error", "message": _err})

    @staticmethod
    def _item_summary(item: dict) -> str:
        for k in ("command", "path", "title", "name"):
            v = item.get(k)
            if v:
                return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)[:160]
        return item.get("type", "")

    @staticmethod
    def _item_args(item: dict) -> dict:
        """Chip'in PARAMETRELER paneli için item'ın anlamlı girdi alanları (kırpılmış)."""
        out: Dict[str, Any] = {}
        for k in ("command", "cwd", "path", "name", "arguments", "changes", "title"):
            v = item.get(k)
            if v in (None, "", [], {}):
                continue
            if isinstance(v, str) and len(v) > 1200:
                v = v[:1200] + f"… [+{len(v) - 1200} karakter]"
            elif not isinstance(v, (str, int, float, bool)):
                v = json.dumps(v, ensure_ascii=False)[:1200]
            out[k] = v
        return out

    @staticmethod
    def _item_output(item: dict) -> str:
        """Chip'in ÇIKTI paneli için item sonucu (komut çıktısı / araç sonucu)."""
        for k in ("aggregatedOutput", "output", "stdout", "result", "error"):
            v = item.get(k)
            if not v:
                continue
            txt = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=2)
            txt = txt.strip()
            if len(txt) > 3000:
                txt = txt[:3000] + "\n… [çıktı kırpıldı]"
            return txt
        return ""

    # ── İptal (Durdur) ───────────────────────────────────────────────────
    async def cancel_turn(self):
        """Bekleyen onayları reddet + turn/interrupt. _turn_lock ALMAZ (deadlock önlemi)."""
        for gid in list(self._active_gate_ids):
            if gid in APPROVAL_GATES:
                APPROVAL_RESULTS[gid] = False
                APPROVAL_GATES[gid].set()
        try:
            if self._started and self.thread_id and self._current_turn_id:
                await self._request("turn/interrupt", {
                    "threadId": self.thread_id, "turnId": self._current_turn_id,
                }, timeout=10)
        except Exception as e:
            logger.warning(f"[CodexSession:{self.conversation_id}] interrupt hatası: {e}")
        if self._cancel_event is not None:
            self._cancel_event.set()
        # A cancelled turn is a turn that ENDED, so it owes the caller a
        # terminal event — whether or not the interrupt was accepted. Emitted
        # unconditionally rather than only on the rejected path: `_emit_terminal`
        # is the single gate, so if the app-server's own `turn/completed` gets
        # there first this is a no-op, and if it never comes (rejected
        # interrupt, dead process) the caller is not left waiting on a queue
        # nobody will ever write to. The partial answer is not lost by ending
        # here: it already reached the client as `text` events, and the turn
        # record falls back to that stream when no `response` arrives.
        await self._emit_terminal({
            "type": "done",
            "session_id": self.thread_id,
            "stop_reason": "cancelled",
            "stop_message": "⏹ Tur durduruldu.",
        })
        logger.info(f"[CodexSession:{self.conversation_id}] tur iptal edildi (cancel_turn)")

    # ── Mesaj akışı: bir tur gönder, event dict'leri yield et ────────────
    async def usage_card_text(self) -> str:
        """`/usage` için: canlı app-server'dan account/rateLimits + account/read çekip
        /usage kartının parse ettiği formatta metin üretir (model turu YOK → sıfır token).
        Claude'un /usage'ına benzer: pencere başına 'X% used · resets <tarih>'."""
        if not self._started:
            await self.start()
        try:
            rl_resp = await self._request("account/rateLimits/read", {}, timeout=15)
        except Exception as e:
            return f"Codex kullanım bilgisi alınamadı: {e}"
        try:
            acc_resp = await self._request("account/read", {}, timeout=10)
        except Exception:
            acc_resp = {}

        rl = (((rl_resp or {}).get("result") or {}).get("rateLimits")) or {}
        acc = (((acc_resp or {}).get("result") or {}).get("account")) or {}

        acct_type = acc.get("type") or "ChatGPT"
        if acct_type.lower() == "chatgpt":
            acct_type = "ChatGPT"
        # NOT: planType (account/read='go', rateLimits='plus') tutarsız/yanlış geliyor →
        # yanlış plan göstermemek için plan adını yazmıyoruz.
        head = f"{acct_type} aboneliğiyle Codex kullanımı"
        lines = [head]

        def _fmt_window(w) -> Optional[str]:
            if not isinstance(w, dict):
                return None
            pct = w.get("usedPercent")
            if pct is None:
                return None
            mins = w.get("windowDurationMins") or 0
            if mins and mins < 1440:
                label = f"{mins // 60} saatlik pencere" if mins >= 60 else f"{mins} dakikalık pencere"
            elif mins:
                label = f"{mins // 1440} günlük pencere"
            else:
                label = "Kullanım penceresi"
            resets = w.get("resetsAt")
            reset_str = ""
            if isinstance(resets, (int, float)):
                try:
                    reset_str = " · resets " + datetime.fromtimestamp(resets).strftime("%d.%m %H:%M")
                except Exception:
                    reset_str = ""
            return f"{label}: {pct}% used{reset_str}"

        for w in (rl.get("primary"), rl.get("secondary")):
            ln = _fmt_window(w)
            if ln:
                lines.append(ln)

        credits = rl.get("credits") or {}
        if credits.get("hasCredits"):
            lines.append(f"Kredi bakiyesi: {credits.get('balance', '0')}")

        if len(lines) == 1:
            lines.append("Şu anda raporlanacak kullanım penceresi yok.")
        return "\n".join(lines)

    async def stream(self, message: str, image_paths: Optional[List[str]] = None) -> AsyncGenerator[dict, None]:
        if not self._started:
            await self.start()

        async with self._turn_lock:
            out_q: asyncio.Queue = asyncio.Queue()
            self._out_q = out_q
            self._cancel_event = asyncio.Event()
            self._terminal_sent = False
            self._active_gate_ids.clear()
            self._final_text = ""
            self._current_turn_id = None

            # Görseller app-server'a native 'localImage' input item'ı olarak gider
            # (şema: LocalImageUserInput → path). Yollar diske yazılmış temp dosyalar.
            input_items = [{"type": "text", "text": message}]
            for p in (image_paths or []):
                input_items.append({"type": "localImage", "path": p})

            try:
                resp = await self._request("turn/start", {
                    "threadId": self.thread_id,
                    "input": input_items,
                }, timeout=60)
                self._current_turn_id = (((resp or {}).get("result") or {}).get("turn") or {}).get("id")
            except Exception as e:
                logger.exception(f"[CodexSession:{self.conversation_id}] turn/start hatası")
                self._out_q = None
                yield {"type": "error", "message": f"Codex turu başlatılamadı: {e}"}
                return

            try:
                while True:
                    ev = await out_q.get()
                    if ev is None:
                        break
                    yield ev
            finally:
                self._out_q = None
                self._cancel_event = None
                self._active_gate_ids.clear()


def get_session(conversation_id: int, **kwargs) -> CodexSession:
    """conversation_id için canlı session'ı getir; yoksa oluştur."""
    sess = _SESSIONS.get(conversation_id)
    if sess is None:
        sess = CodexSession(conversation_id, **kwargs)
        _SESSIONS[conversation_id] = sess
    return sess


def peek_session(conversation_id: int) -> "CodexSession | None":
    """Canlı session'ı getir ama YOKSA KURMA — bkz. `claude_sdk_session.peek_session`.

    Ölçüt Claude'daki ile AYNI olmak zorunda: denetim (30 Ağu 2026) iki `peek`in
    ayrıştığını buldu — Claude kopuk oturumu eliyordu, bu hiçbir şey elemiyordu,
    yani aynı GET sağlayıcıya göre farklı davranıyordu.
    """
    sess = _SESSIONS.get(conversation_id)
    if sess is None or not sess.is_live:
        return None
    return sess


async def close_session(conversation_id: int):
    sess = _SESSIONS.pop(conversation_id, None)
    if sess is not None:
        await sess.close()


async def close_all_sessions():
    for cid in list(_SESSIONS.keys()):
        await close_session(cid)
