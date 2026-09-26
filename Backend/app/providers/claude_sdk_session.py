"""
Claude Code'u KALICI INTERAKTIF session olarak süren köprü (claude-agent-sdk).

Headless `claude -p` (stateless, onay kanalı yok) yerine ClaudeSDKClient kullanır:
- Sohbet başına tek canlı session → bağlam turlar arası korunur.
- can_use_tool → native onay; Write/Edit/Bash/MCP araçları kullanıcı onayından geçer.
- AskUserQuestion (Opus'un A/B/C sorması) → yapısal soru event'i; frontend chip'lerle
  gösterir, cevabı geri akıtırız.
- Abonelik auth (ANTHROPIC_API_KEY gerekmez — SDK kurulu CLI login'ini miras alır).

MİMARİ (tek sürekli reader):
CLI'dan mesajları session ömrü boyunca TEK reader task okur (`_reader_loop`).
Tur aktifken event'ler `_out_q`'ya (SSE'ye) akar; tur yokken / SSE koptuğunda
biten turun metni DB'ye kaydedilir (set_db_saver ile takılan callback).
Bu tasarım üç kritik bug'ı çözer:
1. DURDUR sonrası kilitlenme: tur kilidi artık pump'ı sonsuz beklemez; interrupt
   işlemezse session zorla resetlenir (sonraki mesaj temiz session'la başlar).
2. Arka plan subagent kaybı: SDK'nın task_started/progress/notification/updated
   sistem mesajları (SystemMessage alt sınıfları — eski kod bunları sessizce
   YUTUYORDU) artık canlı aktivite + görev kartı olarak akar. ResultMessage
   geldiğinde arka plan görevleri sürüyorsa tur SSE'de AÇIK TUTULUR; görevler
   bitince CLI'ın otonom devamı akar, gelmezse kısa bir dürtme (nudge) gönderilir
   → "proaktif uyanma".
3. Görünmezlik: include_partial_messages ile canlı thinking/text delta'ları +
   token sayacı (status event) akar — kullanıcı Claude'un çalıştığını GÖRÜR.

Yield edilen event'ler AgentEvent.data şekline uyar (type + alanlar): AgentRunner bunları
AgentEvent'e sarıp SSE'ye basar. Onay/soru beklemeleri command_gates üzerinden yürür.
"""
import asyncio
import json
import logging
import ntpath
import os
import posixpath
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set

from agentic.command_gates import (
    APPROVAL_GATES, APPROVAL_RESULTS,
    GATE_OWNERS,
    QUESTION_GATES, QUESTION_RESULTS,
    register_gate, release_gate,
)
# Onay bekleme süresi tek kaynaktan. `agentic` paketi zaten yukarıdaki satırla
# yükleniyor (yeni bağımlılık değil); ters yön (agent_runner → providers) ise
# döngü kurardı çünkü `agentic/__init__` agent_runner'ı import ediyor.
from agentic.command_gates import APPROVAL_TIMEOUT_S
# unityMCP salt-okuma sınıflandırması. `spawn_env` ile aynı desen: app kökündeki
# modül çıplak adla import ediliyor (`backend.spec` `pathex=['app']` taşıyor).
from unity_tool_policy import is_unity_mcp_read_only
import unity_file_guard

logger = logging.getLogger(__name__)

# conversation_id → ClaudeSDKSession (canlı, isteklerin ötesinde yaşar)
_SESSIONS: Dict[int, "ClaudeSDKSession"] = {}

# system/init'ten gelen slash komutları (chat'te '/' autocomplete için). Bir session
# başlayınca dolar; backend süreci boyunca kalır. Frontend GET /slash-commands ile okur.
_SLASH_COMMANDS_CACHE: List[str] = []
# Aktif skill isimleri (slash_commands'ın alt kümesi; frontend'de "skill" rozeti için).
_SKILLS_CACHE: List[str] = []
# Komut meta'sı: [{name, description, argumentHint}] — Skills galerisi (açıklamalı katalog)
# için. get_server_info().commands'tan warmup'ta dolar; isim-listesinden FARKLI olarak
# açıklama da taşır (system/init yalnızca isim verir, açıklama vermez).
_COMMANDS_META: List[Dict] = []
# Eşzamanlı /slash-commands isteklerinde tek warmup subprocess'i aç (çift spawn önleme).
_WARMUP_LOCK = asyncio.Lock()

# SSE koptuktan sonra biten turların asistan metnini DB'ye yazan callback
# (conversation_routes kayıt eder: lambda cid, text: db.add_message(cid, "assistant", text)).
# Provider katmanından DB'ye doğrudan import etmemek için köprü.
_DB_SAVE_CB: Optional[Callable[[int, str], None]] = None


def set_db_saver(cb: Callable[[int, str], None]) -> None:
    global _DB_SAVE_CB
    _DB_SAVE_CB = cb


class SessionBusyError(RuntimeError):
    """Önceki tur kilidi bırakmadı (sıkışmış session). AgentRunner yakalayıp resetler."""


def get_slash_commands() -> List[str]:
    return list(_SLASH_COMMANDS_CACHE)


def get_skills() -> List[str]:
    return list(_SKILLS_CACHE)


def get_commands_meta() -> List[Dict]:
    return list(_COMMANDS_META)


# Claude SDK'ya geçilen ayar katmanları — TEK KAYNAK.
#
# ⚠️ `"project"` BİLEREK YOK. `cwd` ürünün reposu değil, KULLANICININ AÇTIĞI
# Unity projesi (indirilmiş ya da klonlanmış olabilir; ürün içeriğini denetlemiyor).
# `"project"` açıkken o projeye konan bir `.claude/settings.json` onay kapısını
# dört ayrı yoldan düşürüyordu — canlı ölçüldü 2026-07-29:
#   1. `hooks.PreToolUse` — komut kapıdan ÖNCE ve KOŞULSUZ koşuyor; kapı `deny`
#      dese bile hook'un izi diskte kalıyor. Hook `{"permissionDecision":"allow"}`
#      dönerse kapı HİÇ çağrılmıyor. Yani böyle bir proje AÇMAK, o projeye keyfi
#      komut çalıştırma hakkı vermek demekti — model istemeden, kullanıcı onaylamadan.
#   2. `sandbox.enabled: true` → 4 Bash komutunun 4'ü kartsız geçti (sayaç 0/4).
#   3. `permissions.allow` → `Bash(touch:*)` eklenince sayaç 1 → 0, dosya yine diskte.
#   4. `env` → workspace dosyası ürünün süreç ortamına değişken enjekte ediyordu.
# `disallowed_tools` bunların hiçbirini kapatmıyor: built-in `Bash` o listede değil.
#
# ⚠️ Hiç GEÇMEMEK en kötü seçenek: SDK'da `None` = `["user","project","local"]`,
# yani `settings.local.json` de içeri girer. Liste boş bırakılmamalı.
#
# Kaybedilmeyen şeyler (ölçüldü, gerekçe sanılan ikisi de çürüktü):
#   · Komut menüsü HİÇ küçülmüyor. Ölçüldü 2026-08-01, aynı makinede iki tur:
#     `["project","user"]` → 114 komut, `["user"]` → 114 komut, kesişim farkı 0.
#     Yani `"project"`in getirdiği tek şey workspace'e ÖZEL komut/skill'lerdi ve
#     bu depoda öyle bir dosya yok. (`"user"`ın kendisi kritik: kullanıcının
#     kendi komutları ve eklentileri oradan geliyor — o yüzden liste boşaltılamaz.)
#   · Workspace `CLAUDE.md`/`ARCHITECT.md` wisdom'ını ürün KENDİ okuyor
#     (`agent_runner._get_architect_wisdom`) ve system prompt'a enjekte ediyor.
#   · unityMCP oturuma artık `.mcp.json` üzerinden değil, SDK'ya doğrudan geçilen
#     `mcp_servers` ile giriyor. Ölçüldü: SDK kaydı, user kapsamındaki AYNI ADLI
#     bayat kaydı (`localhost:8080`, BAŞLIKSIZ) EZİYOR — yani kimlik doğrulama
#     gölgelenmiyor. Ölçüm: aynı ada farklı port verilip isteğin nereye düştüğüne
#     bakıldı; `mcp_servers` geçilen iki turda da istek SDK'nın verdiği porta gitti.
CLAUDE_SETTING_SOURCES = ["user"]


async def warmup_slash_commands(cwd: Optional[str] = None,
                                setting_sources: Optional[List[str]] = None) -> List[str]:
    """İlk mesaj atılmadan kurulu TÜM slash komut + skill'leri yakala (cold-start fix).

    ClaudeSDKClient.get_server_info() connect anındaki 'initialize' handshake'inden
    komutları SIFIR inference (token) ile verir — bu bir kullanıcı turu değildir.
    Throwaway client kullanılır (conversation _SESSIONS'a DOKUNULMAZ); mcp_servers /
    can_use_tool VERİLMEZ (hızlı + yan etkisiz). Sonuç global cache'lere yazılır.
    """
    global _SLASH_COMMANDS_CACHE, _SKILLS_CACHE, _COMMANDS_META
    async with _WARMUP_LOCK:
        if _SLASH_COMMANDS_CACHE:
            return list(_SLASH_COMMANDS_CACHE)  # başka bir istek doldurmuş

        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

        ws = cwd if (cwd and os.path.isdir(cwd)) else None  # silinmiş/taşınmış ws → None
        opts = ClaudeAgentOptions(
            # Bu da gerçek bir CLI oturumu açıyor (cwd = kullanıcının projesi), yani
            # ayar katmanı burada da kapıyı ilgilendiriyor: `"project"` açık olsaydı
            # projenin SessionStart hook'u daha komut listelenirken koşardı.
            setting_sources=setting_sources or CLAUDE_SETTING_SOURCES,
            cwd=ws,
            # Same as the chat session: none of the owner's own MCP servers.
            strict_mcp_config=True,
        )
        client = ClaudeSDKClient(options=opts)
        cmds: List[str] = []
        meta: List[Dict] = []
        try:
            await client.__aenter__()
            info = await client.get_server_info() or {}
            # get_server_info → komutlar 'commands' anahtarında (dict listesi: name +
            # description + argumentHint). system/init'teki 'slash_commands' (string
            # listesi) anahtarından FARKLI ve AÇIKLAMA taşır → Skills galerisi bundan beslenir.
            # NOT: get_server_info'da 'skills' anahtarı YOK (yalnızca 'agents' = subagent
            # tipleri var; bunlar skill değil). Gerçek skill listesi canlı session'ın
            # system/init mesajından gelir → _SKILLS_CACHE'i burada DOLDURMUYORUZ.
            for c in (info.get("commands") or []):
                if isinstance(c, dict) and c.get("name"):
                    cmds.append(c["name"])
                    meta.append({
                        "name": c["name"],
                        "description": (c.get("description") or "").strip(),
                        "argumentHint": (c.get("argumentHint") or "").strip(),
                    })
                elif isinstance(c, str):  # eski CLI string verebilir
                    cmds.append(c)
                    meta.append({"name": c, "description": "", "argumentHint": ""})
        except Exception as e:
            logger.warning(f"[warmup_slash_commands] hata: {e}")
        finally:
            try:
                await client.__aexit__(None, None, None)  # subprocess sızıntısı önlemi
            except Exception:
                pass

        if cmds:
            _SLASH_COMMANDS_CACHE = cmds
        if meta:
            _COMMANDS_META = meta
        logger.info(f"[warmup_slash_commands] {len(cmds)} komut yakalandı (skill'ler ilk mesajda dolar)")
        return list(_SLASH_COMMANDS_CACHE)


_FILE_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
# Claude Code on Windows can run commands through a PowerShell tool as well as Bash.
_SHELL_TOOLS = ("Bash", "PowerShell")


def _unity_file_refusal(tool_name: str, inp: dict, workspace: str):
    inp = inp if isinstance(inp, dict) else {}
    if tool_name in _FILE_WRITE_TOOLS:
        for key in ("file_path", "path", "notebook_path"):
            refusal = unity_file_guard.check_write(inp.get(key), workspace)
            if refusal is not None:
                return refusal
        return None
    if tool_name in _SHELL_TOOLS:
        return unity_file_guard.check_shell(inp.get("command"), workspace)
    return None


# Salt-okunur / yan-etkisiz araçlar → onay sormadan otomatik izin (gürültü azaltma).
# MUTASYON araçları (Bash, Write/Edit, manage_gameobject/components/ui/material,
# execute_menu_item, run_tests, manage_build vb.) bilerek DIŞARIDA → onayda kalır.
_AUTO_ALLOW_TOOLS = {
    "Read", "Glob", "Grep", "LS", "NotebookRead",
    "TodoWrite", "ToolSearch", "WebFetch", "WebSearch",
}

# Chat'te gösterilmeyecek düşük-değerli iç araçlar (görünürlük; onay akışını etkilemez).
# NOT: "Task" artık BURADA DEĞİL — subagent çağrıları "Subagent" chip'i olarak görünür.
_NOISE_TOOLS = {"ToolSearch", "Skill"}
# Plan/todo araçları → ham döküm yerine tek "plan yapıyor" sinyali.
_PLAN_TOOLS = {"TodoWrite", "TaskCreate", "TaskUpdate"}

# Uygulama ortamını Claude'a tanıtan system prompt eki (claude_code preset'ine append —
# skill/slash komutları BOZULMAZ). "Bekleme moduna geçme" sapmasını ve gereksiz
# subagent token yakımını hedefler.
_APP_SYSTEM_APPEND = (
    "[ORTAM] Gamachine masaüstü uygulamasının sohbet arayüzü içinden "
    "kullanılıyorsun (Unity odaklı). Arka plan görevleri (background subagent/Bash) "
    "desteklenir: bir arka plan görevi bittiğinde sistem seni OTOMATİK uyandırır ve "
    "kaldığın yerden devam edersin. Bu yüzden kendini 'bekleme moduna' alma, bekleme "
    "döngüsü kurma ya da görev bitti mi diye tekrar tekrar yoklama. Kullanıcının "
    "abonelik kotasını korumak için: küçük/orta işleri subagent açmadan doğrudan kendin "
    "yap; subagent'ları yalnızca gerçekten paralellik gerektiren büyük işlerde kullan."
)

# Metin/thinking delta'ları bu boyuta ulaşınca SSE'ye basılır (event spam azaltma).
_DELTA_FLUSH_CHARS = 48
# SDK'nın CLI stdout'undaki TEK bir NDJSON satırı için tavanı. Varsayılanı 1 MiB
# (`subprocess_cli._DEFAULT_MAX_BUFFER_SIZE`) ve aşıldığında `SDKJSONDecodeError`
# fırlatıp oturumu komple düşürüyor — kırpma ya da atlama yok.
#
# Bu ürün için 1 MiB gerçekçi değil: kullanıcının sohbete yapıştırdığı görsel
# diske yazılıp Claude'a `Read` ile açtırılıyor (bkz. providers/_attachments.py),
# ve Read'in sonucu görüntüyü base64 olarak stdout'a geri koyuyor. base64 ham
# boyutun ~4/3'ü olduğundan yalnızca ~750 KB'lık bir PNG tavanı aşırmaya yetiyor.
# Sahada gözlenen desen tam da bu: iki fotoğrafla patlıyor, üç fotoğrafla
# patlamıyor — belirleyici olan ADET değil, en büyük tek satırın boyutu.
#
# Değer, aynı sınıf için ephemeral CLI yolunda zaten seçilmiş olan tavanla
# hizalı (`cli_base._CLI_STREAM_LIMIT_BYTES`); iki yolun aynı girdide farklı
# davranması başlı başına bir arıza kaynağıydı.
_SDK_STDOUT_LIMIT_BYTES = 32 * 1024 * 1024


def claude_ikilisini_coz() -> Optional[str]:
    """PATH'teki `claude` bir Windows toplu-iş kabuğuysa ARKASINDAKİ gerçek exe'yi bul.

    Neden gerekli (8 Ağu 2026, kullanıcıda canlı çıktı): SDK'nın gömülü `claude.exe`'si
    pakette taşınmıyor artık (ürün kararı: habersiz üçüncü taraf AI dağıtmıyoruz), o
    yüzden SDK PATH'e düşüyor. Ama Claude Code'un npm kurulumu PATH'e yalnız
    `claude.cmd` kabuğunu koyuyor ve SDK `.cmd`/`.bat` çalıştırmayı **bilerek
    reddediyor** — cmd.exe argüman enjeksiyonuna açık ve güvenilir kaçış yok
    (`subprocess_cli._reject_windows_batch_cli`). Sonuç: Claude Code KURULU olduğu
    hâlde oturum "Refusing to execute batch script" ile açılmıyordu.

    Kabuğun kendisi hedefi düz metin taşıyor, yani tahmin etmeye gerek yok:

        "%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe" %*

    Önce kabuğu OKUYUP içindeki `.exe` yolunu çıkarıyoruz (en doğrusu; npm yerleşimi
    değişse bile çalışır), tutmazsa bilinen yerleşimi deniyoruz.

    None dönerse SDK kendi arama sırasına devam eder — yani bu fonksiyon bir
    iyileştirme, zorunlu bir bağımlılık değil.
    """
    import shutil
    import sys as _sys
    import re as _re

    if _sys.platform != "win32":
        return None  # .cmd sorunu yalnız Windows'ta var

    bulunan = shutil.which("claude.exe")
    if bulunan:
        return bulunan

    kabuk = shutil.which("claude")
    if not kabuk or not kabuk.lower().endswith((".cmd", ".bat")):
        return None  # zaten native ya da hiç yok → SDK kendi işini yapsın

    # 1) Kabuğun içindeki tırnaklı .exe yolunu oku.
    try:
        with open(kabuk, "r", encoding="utf-8", errors="replace") as fh:
            metin = fh.read()
        m = _re.search(r'"([^"]*?\.exe)"', metin, _re.IGNORECASE)
        if m:
            ham = m.group(1).replace("%dp0%", os.path.dirname(kabuk))
            aday = os.path.normpath(os.path.expandvars(ham))
            if os.path.isfile(aday):
                logger.info(f"[ClaudeSDK] npm kabuğunun arkasındaki ikili bulundu: {aday}")
                return aday
    except OSError as e:
        logger.warning(f"[ClaudeSDK] claude kabuğu okunamadı: {e}")

    # 2) Bilinen npm yerleşimi.
    aday = os.path.join(os.path.dirname(kabuk), "node_modules", "@anthropic-ai",
                        "claude-code", "bin", "claude.exe")
    if os.path.isfile(aday):
        logger.info(f"[ClaudeSDK] bilinen npm yerleşiminden bulundu: {aday}")
        return aday

    logger.warning("[ClaudeSDK] PATH'te yalnız .cmd kabuğu var, arkasındaki exe bulunamadı")
    return None
# Arka plan görevleri bitince CLI kendiliğinden devam etmezse bu süre sonra dürtülür.
_TASKS_DONE_GRACE_S = 20.0
# Nudge sonrası devam turu hiç gelmezse turu bitirme emniyeti.
_NUDGE_FALLBACK_S = 180.0
# Result geldi + görevler sürüyor: görevler hiç bitmezse turu bitirme emniyeti.
_TASKS_WATCHDOG_S = 900.0
_NUDGE_MESSAGE = (
    "[Sistem] Arka plan görevlerin tamamlandı. Sonuçlarını değerlendirip kaldığın "
    "yerden kısaca devam et ve işi sonuçlandır."
)


def _canonical(path: str, base: str = "") -> Path:
    """Yolu symlink'leri çözerek mutlaklaştırır; henüz VAR OLMAYAN yollarda da çalışır.

    `Path.resolve(strict=False)` var olan öneki kanonikleştirip kalanını olduğu gibi
    ekler — yeni dosya yazımında (hedef henüz yok) doğru sonucu veren tek yol budur.
    Şablon: `tools/file_tools._validate_path`.

    Göreli yol `base`'e (workspace) göre çözülür, backend sürecinin cwd'sine göre
    DEĞİL: Claude CLI'ın çalışma dizini workspace olduğu için modelin verdiği
    "Assets/X.cs" gerçekten workspace'e görelidir. Eski `os.path.abspath` bunu
    backend cwd'sine bağlıyordu — meşru göreli yollar "dışarıda" sayılabiliyordu.
    """
    p = Path(path).expanduser()
    if not p.is_absolute() and base:
        p = Path(base).expanduser() / p
    return p.resolve(strict=False)


def _path_in_workspace(path: str, workspace: str) -> bool:
    """path, workspace kökünün altında mı? (path-traversal koruması).

    `realpath` İKİ TARAFA da uygulanır. Yalnız hedefe uygulamak meşru kurulumları
    kırardı: macOS'ta `/tmp → /private/tmp` bağı yüzünden workspace'in KENDİSİ bir
    bağın altında olabiliyor. Yalnız `abspath` kullanmak ise koruma bırakmıyordu —
    ölçüldü (2026-07-28): `ws/link → /disari` kurulumunda
    `_path_in_workspace(ws/link/escape.cs, ws)` True dönüyordu, yani "workspace
    dışına yazımı kullanıcıya SORMADAN reddet" dalı %100 atlatılıyordu.
    """
    if not workspace:
        return True  # workspace tanımsızsa kısıtlama yok
    try:
        ws = _canonical(workspace)
        target = _canonical(path, workspace)
        if target == ws:
            return True
        return ws in target.parents
    except Exception:
        return False


# Yol tabanlı salt-okuma araçları: hedefleri workspace dışına düşebilir, o yüzden
# adım modunda onay kartına tabidirler (aşağıdaki `_read_target_outside_workspace`).
# `TodoWrite`/`ToolSearch` bilerek YOK — onların bir dosya sistemi hedefi yok.
# `WebFetch`/`WebSearch` de YOK: ağ yüzeyi ayrı bir sorun, bu turda kapsam dışı.
_PATH_READ_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead"}

# Glob deseninde joker başlamadan önceki sabit önek çıkarılırken bakılan karakterler.
_GLOB_MAGIC = set("*?[")


def _glob_literal_root(pattern: str) -> str:
    """`/Users/**/*.key` → `/Users`, `C:\\Users\\**\\*.key` → `C:\\Users`.

    Desenin joker İÇERMEYEN sabit önekini verir.

    Neden gerekli: `Glob` yol argümanı olmadan da mutlak desenle çağrılabiliyor
    (ölçülen kaçış tam olarak buydu). Desenin tamamını yol sanmak yanlış olurdu;
    tarama gerçekte bu sabit kökün altında yapılıyor.

    Neden "böl ve birleştir" DEĞİL, "ilk jokerden önceki son ayraçtan kes":
      1. `os.sep` ile bölmek macOS'ta `C:\\Users\\**\\*.key`'i HİÇ bölmüyordu —
         tek segment jokerli görünüyor, kök `/` çıkıyordu (ölçüldü 2026-07-28).
      2. İki ayraçla bölüp TEK ayraçla birleştirmek UNC önekini bozardı:
         `\\\\sunucu\\pay` → `//sunucu/pay`. Kesme yöntemi desenin kendi ayracını
         aynen koruyor, yani `_path_in_workspace`'e giden dize Windows'ta da
         geçerli bir yol olarak kalıyor.
    """
    magic = next((i for i, ch in enumerate(pattern) if ch in _GLOB_MAGIC), -1)
    if magic < 0:
        return pattern or os.sep  # joker yok → desenin tamamı yol
    head = pattern[:magic]
    kesim = max(head.rfind("/"), head.rfind("\\"))
    if kesim < 0:
        return os.sep  # jokerin önünde hiç ayraç yok (ör. `*.key`)
    # `/**/*.key` gibi durumda kök ayracın KENDİSİ; boş dize döndürmek olmaz.
    return head[:kesim] or head[:kesim + 1]


def _glob_absolute_root(pattern: str) -> Optional[str]:
    """Mutlak bir Glob deseninin sabit kökü; desen göreli ise None.

    None'ın anlamı `_read_tool_target`'taki ile aynı: "workspace içi say". Göreli
    desenler gerçekten workspace'i tarar; onları dışarı saymak her aramada kart
    çıkarır ve kullanıcıyı refleks-onaya alıştırırdı.

    Hangi arızadan doğdu (dış denetim + mimarın kendi ölçümü, 2026-07-28): eski
    kontrol `pat.startswith(("/", "~"))` idi, yani YALNIZ POSIX mutlak yolunu
    tanıyordu. Sürücü harfi ve UNC tanınmadığı için

        Glob C:\\Users\\**\\*.key      -> hedef=None -> KARTSIZ
        Glob C:/Users/**/*.key        -> hedef=None -> KARTSIZ
        Glob \\\\sunucu\\pay\\**\\*.key -> hedef=None -> KARTSIZ

    üçü de "workspace içi" varsayılanına düşüyordu — ürünün ANA PLATFORMUNDA kapı
    tümüyle atlatılabiliyordu.

    Neden `ntpath` + `posixpath`, `os.path.isabs` değil: `os.path` koşulan
    platformun kuralını uygular ve macOS'ta `C:\\Users` mutlak DEĞİLDİR. Tek
    başına `os.path.isabs` kullansaydık kapı yalnız Windows runner'da doğru
    davranır, geliştirmede ve CI'da (ikisi de POSIX) sessizce boş kalırdı. Bu iki
    modül ise koşulan platformdan bağımsız çağrılabiliyor.

    Kapsama kararı BURADA verilmiyor — kök `_path_in_workspace`'e gidiyor ve
    karar orada, tek yerde kalıyor. İkinci bir yol mantığı yazmak bu depoda
    tekrar eden "uyuşması gereken iki yer" arızası.
    """
    pat = (pattern or "").strip()
    if not pat:
        return None
    if pat.startswith("~"):
        return _glob_literal_root(os.path.expanduser(pat))
    if posixpath.isabs(pat) or ntpath.isabs(pat):
        return _glob_literal_root(pat)
    return None


def _read_tool_target(tool_name: str, inp: dict) -> Optional[str]:
    """Okuma aracının dosya sistemi hedefini çıkarır; çıkarılamıyorsa None.

    None = "workspace içi say". Gerekçe: bu araçların varsayılan çalışma dizini
    session'ın cwd'si, o da workspace — yani yolsuz `Grep(pattern=...)` ya da
    göreli `Glob("**/*.cs")` gerçekten workspace'i tarar. Bilinmeyeni "dışarıda"
    saymak her aramada onay kartı çıkarırdı ve kullanıcıyı refleks-onaya alıştırırdı.

    NOT: `Grep`'in `pattern`'ı REGEX'tir, yol değil — bilerek yok sayılıyor.
    `Glob`'un `pattern`'ı ise yol deseni, o yüzden yalnız orada değerlendiriliyor.
    """
    if tool_name == "Read":
        return inp.get("file_path") or None
    if tool_name == "NotebookRead":
        return inp.get("notebook_path") or inp.get("file_path") or None
    if tool_name == "LS":
        return inp.get("path") or None
    if tool_name in ("Glob", "Grep"):
        p = inp.get("path")
        if p:
            return p
        if tool_name == "Glob":
            # Mutlaksa sabit kök, göreli ise None. İki yol konvansiyonunu da
            # tanıması şart — gerekçesi `_glob_absolute_root`'ta.
            return _glob_absolute_root(inp.get("pattern") or "")
        return None
    return None


def _read_target_outside_workspace(tool_name: str, inp: dict, workspace: str) -> bool:
    """Yol tabanlı okuma aracı workspace DIŞINI mı hedefliyor?

    Kapsama kararı tek yerden (`_path_in_workspace`) geliyor — ikinci bir yol
    mantığı yazmak, bu depoda tekrar eden "uyuşması gereken iki yer" arızası.
    """
    if tool_name not in _PATH_READ_TOOLS:
        return False
    target = _read_tool_target(tool_name, inp or {})
    if not target:
        return False
    return not _path_in_workspace(target, workspace)


def _bos_mu(metin) -> bool:
    """Gövde GÖZLE boş mu — ölçüt bir liste değil, KATEGORİ.

    Üç turda üç kez aşıldı: `""` → `"  \\t "` → U+3164 HANGUL FILLER →
    varyasyon seçicileri (`variation_selector_blank_no_warning`). Her seferinde
    ölçüte bir karakter eklendi ve kırmızı takım bir sonrakini buldu; yani liste
    tutmak yakınsamıyor.

    Yeni ölçüt: metinde MÜREKKEP BIRAKAN tek bir karakter var mı? Boşluklar,
    kontrol/format karakterleri, birleşen işaretler (varyasyon seçicileri dahil)
    ve bilinen boş-çizilenler mürekkep bırakmıyor. Aksanlı gerçek metin bundan
    etkilenmiyor: aksanın altındaki TABAN harf görünür bir karakter.
    """
    if not isinstance(metin, str):
        return True
    return all(
        parca.isspace()
        or parca in _GORUNMEZLER
        or unicodedata.category(parca) in _GORUNMEZ_KATEGORILER
        for parca in metin
    )


# Mürekkep bırakmayan Unicode kategorileri: kontrol/format/ayrılmış (C*),
# birleşen işaretler (Mn/Me — varyasyon seçicileri buraya düşüyor) ve ayırıcılar.
_GORUNMEZ_KATEGORILER = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Mn", "Me", "Zs", "Zl", "Zp"})

# Kategorisi "görünür" diyen ama pratikte boş çizilen karakterler (çoğu `Lo`).
#
# Doğrulama turu 2 bunları `strip()` tabanlı "gözle boş" ölçütünün ve `Cf`/`Cc`
# tabanlı kaçışın ARASINDAN geçirdi (`invisible_nonwhitespace_write.py`,
# U+3164 HANGUL FILLER — kategori `Lo`). İkisi de kategoriye bakıyordu, bu
# karakterlerin kategorisi ise görünürlükleri hakkında hiçbir şey söylemiyor.
#
# ⚠️ LİSTE TÜKETİCİ DEĞİL ve öyleymiş gibi davranılmamalı — Unicode'un
# "default ignorable" kümesinin tamamı `unicodedata` üzerinden sorgulanamıyor.
# Buradaki karakterler bilinen boş-çizilenler; yeni bir örnek çıkarsa listeye
# eklenir. Kalan risk raporda açıkça yazılı.
_GORUNMEZLER = frozenset("ㅤᅟᅠ⠀ﾠ឴឵᠎")


def _kirp(metin: str) -> str:
    """Onay kartındaki bir yaprağı gösterime hazırlar. KIRPMA YOK — sebebi ölçüldü.

    Burada 4000 karakterlik bir sınır vardı ve denetim onu kırdı
    (1 Ağu 2026, `approval_hidden_suffix.py`): 4000 zararsız karakterin ARDINA
    yıkıcı bir son ek konulduğunda kart zararsız başlangıcı + "gizlendi" notunu
    gösteriyor, Onayla ise gövdenin TAMAMINI yetkilendiriyordu. Kullanıcının
    göremediği bir şeyi onaylaması, kartın var olma sebebini ortadan kaldırıyor.

    ⛔ Sınırı BÜYÜTMEK çözüm değildi: eşik nereye konursa konsun "eşikten
    sonrasını gizle" sınıfı ayakta kalırdı ve saldırgan eşiği zaten biliyor.
    Ölçüt değişti — kart, yetkilendirdiği metnin TAMAMINI gösteriyor.

    Bunun bedeli SSE'de daha büyük bir olay; kabul edilebilir çünkü bu olay
    onay başına BİR kez çıkıyor (sohbet çipi gövdeyi hiç taşımıyor, bkz.
    `tam_govde`) ve gövdenin boyu modelin çıktı sınırıyla zaten bağlı. Kart
    kendi içinde kaydırılıyor, yani uzun gövde düğmeleri de itmiyor.
    """
    # ⛔ BURADA KAÇIŞ YOK. Kaçış TEK yerde, `_describe_tool`'un çıkışında.
    # Doğrulama turu 2 bunu kırdı (`double_escape_write_body.py`): `_kirp` de
    # kaçırıyordu, çıkış da kaçırıyordu → tek bir ters bölü kartta DÖRT ters
    # bölü olarak görünüyordu. Yani kart, onaylanan içeriği yanlış gösteriyordu
    # ve "idempotent" diye yazdığım docstring ters bölü çiftlemesi eklendiği an
    # yanlışa dönmüştü. İki kaçış noktası, tanım gereği tek çıkış noktası değil.
    return metin if isinstance(metin, str) else str(metin)


def _gorunur_kil(metin: str, *, ters_bolu_cift: bool = True) -> str:
    """Görünmez / yazı yönünü değiştiren karakterleri GÖRÜNÜR kaçışlara çevirir.

    Denetim bulgusu (1 Ağu 2026, `approval_bidi_controls.py`): gövdedeki bir
    RLO (U+202E) tarayıcıda uygulanıyor ve kartta okunan sıra, diske yazılacak
    bayt sırasından FARKLI olabiliyor. React ham HTML'i engelliyor ama bunlar
    HTML değil, metnin kendisi — yani kaçış bu sınıfı görmüyor.

    ÖLÇÜT BİR LİSTE DEĞİL KATEGORİ: Unicode `Cf` (format) ve `Cc` (kontrol)
    sınıfındaki her karakter kaçırılıyor, satırsonu ve sekme hariç. Elle yazılmış
    bir karakter listesi, listede olmayan bir sonraki karakterle sessizce
    aşılırdı; kategori ölçütü yeni eklenen karakterleri de kapsıyor.
    """
    def _cevir(parca: str) -> str:
        if parca in ("\n", "\t"):
            return parca
        if unicodedata.category(parca) in ("Cf", "Cc") or parca in _GORUNMEZLER:
            return f"\\u{ord(parca):04X}"
        if parca == "\\" and ters_bolu_cift:
            # TERS BÖLÜ DE KAÇIRILIYOR — yoksa kaçış kendi çıktısıyla ÇAKIŞIR.
            # ⚠️ JSON bölümünde bu KAPALI (`ters_bolu_cift=False`): orada ters
            # bölüyü JSON'un kendi kaçışı zaten tekilleştiriyor, ikinci bir
            # çiftleme tek bir ters bölüyü DÖRT ters bölü gösterirdi — yani
            # düzelttiğimiz yanlış-gösterim sınıfını JSON'da geri açardı.
            # Doğrulama turu bulgusu (`approval_escape_collision.py`): gerçek bir
            # U+202E ile düz metin olarak yazılmış altı karakterlik `‮`
            # kartta AYNI görünüyordu, yani iki farklı bayt dizisi ayırt
            # edilemiyordu. Ters bölü çiftlenince gerçek kontrol `‮`,
            # düz metin ise `\\u202E` olarak çıkıyor.
            return "\\\\"
        return parca

    return "".join(_cevir(parca) for parca in metin)


def _yazma_govdesi(tool_name: str, inp: dict) -> str:
    """Bir yazma aracının kartta GÖSTERİLECEK gövdesi.

    NEDEN VAR (K9): kart yalnız `Write → yol` yazıyordu, yani kullanıcı dosyaya
    NE yazılacağını görmeden onaylıyordu. Aynı üründe kardeş yol bunu zaten
    doğru yapıyor — unityMCP köprüsünün `write_file`'ı ya tam içeriği ya diff'i
    çiziyor. Asimetri bilgilendirilmiş onayı zayıflatıyordu: onay kartının tek
    işi kullanıcıya NE onayladığını göstermek.

    Edit/MultiEdit'te diff metin olarak veriliyor (eski/yeni), çünkü kararı
    değiştiren şey tam olarak o iki dize.
    """
    # BOŞ GÖVDE SESSİZ GEÇEMEZ. Denetim bulgusu (1 Ağu 2026,
    # `approval_empty_write.py`): `content=""` ile bir yazma, gövde "yok" sayılıp
    # yalnız `Write → yol` üretiyordu — yani var olan bir dosyayı SIFIR BAYTA
    # indiren işlem, kartta içeriksiz bir özetten ayırt edilemiyordu. Kullanıcı
    # silme etkisi olan bir işlemi hiçbir uyarı görmeden onaylıyordu.
    #
    # ⚠️ ÖLÇÜT "boş dize" DEĞİL "GÖZLE BOŞ". Doğrulama turu ilk hâli kırdı
    # (`whitespace_write_ambiguous.py`): uyarı Python doğruluk testine bağlıydı,
    # yani yalnız `""`'i kapsıyordu; `"   \t\n  "` sıradan gövde yolundan geçip
    # kartta bomboş bir alan olarak çiziliyordu — aynı yıkıcı etki, uyarısız.
    #
    # ⚠️ UYARI METNİ ARACA GÖRE. İkinci kırılma (`notebook_empty_false_zero_
    # byte_warning.py`): `Write`'ın "dosya sıfır bayta inecek" cümlesi
    # `NotebookEdit`'e de uygulanıyordu, oysa boş bir HÜCRE kaynağı defteri
    # sıfırlamıyor. Kartın yanlış bir şey söylemesi, az şey söylemesinden kötü.
    # ⚠️ UYARILAR BİLMEDİĞİMİZ BİR ETKİYİ İDDİA ETMİYOR.
    #
    # Üçüncü doğrulama turu iki kez aynı sınıftan vurdu: "dosyanın mevcut içeriği
    # silinecek" cümlesi YENİ bir dosya yazımında olmayan bir içeriğin silindiğini
    # iddia ediyordu (`new_empty_file_warning_overstates`), ve boş kaynaklı bir
    # `insert` "hücrenin içeriği silinecek" diyordu, oysa hücre EKLENİYORDU
    # (`notebook_insert_warning_misstates`). Backend dosyanın var olup olmadığını
    # bilmiyor; bilmediğini söylemek yerine YAPILACAK ŞEYİ söylüyor.
    BOS_DOSYA = "⚠️ YAZILACAK İÇERİK GÖZLE BOŞ (dosyaya boş/görünmez içerik yazılacak)."
    BOS_HUCRE = "⚠️ HÜCRE KAYNAĞI GÖZLE BOŞ (hücreye boş/görünmez kaynak yazılacak)."
    SIL_HUCRE = ("⚠️ HÜCRE SİLİNİYOR — bu hücre (içeriği ve üst verisiyle) "
                 "defterden kaldırılacak.")
    if tool_name == "Write":
        icerik = inp.get("content", "")
        return BOS_DOSYA if _bos_mu(icerik) else _kirp(icerik)
    if tool_name == "NotebookEdit":
        kaynak = inp.get("new_source", "")
        if inp.get("edit_mode") == "delete":
            return SIL_HUCRE
        return BOS_HUCRE if _bos_mu(kaynak) else _kirp(kaynak)
    if tool_name == "Edit":
        return (f"- {_kirp(inp.get('old_string', ''))}\n"
                f"+ {_kirp(inp.get('new_string', ''))}")
    if tool_name == "MultiEdit":
        duzenlemeler = inp.get("edits") or []
        if not isinstance(duzenlemeler, list):
            return _kirp(json.dumps(inp, ensure_ascii=False))
        parcalar = []
        for i, d in enumerate(duzenlemeler, 1):
            d = d if isinstance(d, dict) else {}
            parcalar.append(f"[{i}/{len(duzenlemeler)}]\n"
                            f"- {_kirp(d.get('old_string', ''))}\n"
                            f"+ {_kirp(d.get('new_string', ''))}")
        return "\n\n".join(parcalar)
    return ""


def _describe_tool(tool_name: str, inp: dict, *, tam_govde: bool = False) -> str:
    """Onay kartı metnini üretir ve TEK ÇIKIŞ NOKTASINDA görünür kılar.

    Sarmalayıcı ayrı duruyor çünkü denetimin bidi bulgusu (F5) yalnız yazma
    gövdesinde değil, kartın HER dalında yaşıyordu: dosya YOLU, `Bash` komutu,
    `WebFetch` adresi — hepsi modelin seçtiği metin ve hepsi doğrudan karta
    gidiyordu. Tek tek dalları yamamak sınıfı kapatmaz; yarın eklenen dal onu
    sessizce geri açar. Kaçış bu yüzden dalların içinde değil, çıkışta.

    `_gorunur_kil` idempotent: kaçırılmış bir dizide artık Cf/Cc karakteri
    kalmadığı için ikinci geçiş onu değiştirmiyor.
    """
    ozet = _describe_tool_ham(tool_name, inp, tam_govde=tam_govde)
    if not tam_govde:
        return _gorunur_kil(ozet)

    # ⭐ KARTIN YETKİLİ BÖLÜMÜ: onaylanan girdinin TAMAMI.
    #
    # Üç doğrulama turu bu kararı zorladı. Kart, keyfi bir araç girdisini ELLE
    # YAZILMIŞ düzyazıyla özetliyordu; özet tanım gereği kayıplı, dolayısıyla her
    # turda bir sonraki eksik alan bulundu: `content` eklendi → `cell_id` yoktu →
    # `cell_type` yoktu → `Bash`ta `run_in_background` yoktu. Bu, eşik
    # kovalamanın düzyazı hâli: vaka kapatıyor, SINIFI kapatmıyor.
    #
    # Ölçüt değişti. Düzyazı artık yalnızca BAŞLIK (okunurluk için); kartın
    # yetkilendirdiği şeyi gösteren bölüm ham girdinin kendisi. Böylece iki
    # özellik İNŞA GEREĞİ doğru oluyor:
    #   • kart, Onayla'nın yetkilendirdiği her alanı gösterir (alan atlanamaz),
    #   • iki farklı girdi asla aynı kartı üretemez (çakışma imkânsız).
    try:
        # ⚠️ `ensure_ascii=True` BİLİNÇLİ — kartın AYIRT ETME yeteneği buradan geliyor.
        #
        # Denge şu: okunabilir bölüm içeriği olduğu gibi göstermeli (bir yol
        # `C:\Assets` diye görünmeli), ama o zaman gerçek bir U+202E ile düz metin
        # olarak yazılmış `\u202E` aynı görünür. Ters bölüyü çiftleyerek ayırmak
        # denendi ve içerik gösterimini bozdu (`double_escape_write_body`).
        # `ensure_ascii=True` ikisini de bedelsiz çözüyor: gerçek kontrol JSON'da
        # `\u202e`, düz metin ise `\\u202E` olarak çıkıyor. Türkçe karakterler bu
        # bölümde kaçışlı görünür — okunacak yer YUKARIDAKİ bölüm, burası KESİN olan.
        tam = json.dumps(inp, ensure_ascii=True, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        tam = repr(inp)
    # İki bölüm İKİ FARKLI kaçışla: düzyazı başlıkta ters bölü çiftleniyor
    # (orada tekilleştirecek başka bir katman yok), JSON'da çiftlenmiyor (JSON
    # zaten `\\` yazıyor). Görünmez/yön değiştiren karakterler İKİSİNDE DE
    # kaçırılıyor — `ensure_ascii=False` onları ham bırakıyor, yani JSON tek
    # başına yeterli bir savunma değil.
    # HİÇBİR BÖLÜMDE ters bölü çiftlenmiyor:
    #   • okunabilir başlık/gövde, içeriği OLDUĞU GİBİ göstermeli — bir C# yolu
    #     `C:\Assets` orada `C:\\Assets` diye görünürse kart, onaylanan içeriği
    #     yanlış gösterir (üçüncü tur bunu `double_escape_write_body` ile ölçtü);
    #   • JSON bölümünde zaten JSON'un kendi `\\` kuralı var, üstüne ikinci bir
    #     çiftleme binmesi aynı yanlış-gösterimi oraya taşırdı.
    # Ayırt etme işi (gerçek U+202E ile düz metin `\u202E`) JSON bölümüne ait:
    # orada ilki `\u202E`, ikincisi `\\u202E` olarak çıkıyor, yani kart iki
    # farklı bayt dizisini hâlâ ayırt ediyor — ama bunu okunurluğu bozmadan yapıyor.
    return (_gorunur_kil(ozet, ters_bolu_cift=False)
            + "\n\n── ONAYLANAN GİRDİ (tamamı) ──\n"
            + _gorunur_kil(tam, ters_bolu_cift=False))


def _describe_tool_ham(tool_name: str, inp: dict, *, tam_govde: bool = False) -> str:
    """Kart metninin ham hâli — kaçış YAPMAZ, çağıranı `_describe_tool`.

    `tam_govde` İKİ ÇAĞIRANI ayırıyor ve varsayılanı KAPALI:
      • ONAY KARTI (True) — kullanıcı burada karar veriyor, yazılacak içeriği
        görmek zorunda (K9).
      • SOHBET ÇİPİ (False) — burada karar yok, olan biteni özetleyen bir
        etiket var. Çip zaten `truncate` ile ~200 piksele kısılıyor, yani
        gövdeyi göndermek ekranda hiçbir şey kazandırmadan SSE akışını
        şişirirdi. Aynı gerekçeyle `_trim_args` argümanları 1200'de kesiyor;
        bu bayrak o kararı korumak için var.
    """
    try:
        if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            # ⚠️ `notebook_path` DA OKUNUYOR. Doğrulama turu bulgusu
            # (`notebook_approval_fields_omitted.py`): `NotebookEdit` hedefini bu
            # anahtarda taşıyor, biz yalnız `file_path`/`path`'e bakıyorduk →
            # kart `NotebookEdit → ?` yazıyordu. Üstelik hücre ve işlem türü de
            # yoktu, yani AYRI iki işlem (güvenli bir hücrede değiştirme vs
            # kritik bir hücreyi silme) birbirinin AYNI kartı üretiyordu.
            yol = inp.get("file_path", inp.get("path", inp.get("notebook_path", "?")))
            if tool_name == "NotebookEdit":
                # Hedefi tekilleştiren alanlar başlığa giriyor: kart, onayladığı
                # işlemi başka bir işlemden ayırt edilebilir kılmak zorunda.
                # `cell_type` de burada: doğrulama turu 2 ölçtü ki aynı kaynağı
                # `code` ya da `markdown` hücresi olarak eklemek FARKLI iki işlem
                # ama kartları birbirinin aynısıydı (`notebook_cell_type_collision`).
                _hucre = inp.get("cell_id", "?")
                _kip = inp.get("edit_mode", "replace")
                _tip = inp.get("cell_type", "?")
                yol = f"{yol} · hücre={_hucre} · işlem={_kip} · tür={_tip}"
            govde = _yazma_govdesi(tool_name, inp) if tam_govde else ""
            # Gövde gerçekten boşsa (ör. boş dosya yazımı) başlığın altına boş
            # satır koyup kartı yanıltıcı biçimde "içerik yok gibi" göstermeyelim.
            return f"{tool_name} → {yol}\n\n{govde}" if govde else f"{tool_name} → {yol}"
        if tool_name == "Bash":
            komut = inp.get("command", "")
            if not tam_govde:
                return komut
            # ONAY KARTINDA ÇALIŞTIRMA KİPİ DE GÖRÜNÜYOR. Doğrulama turu 2
            # (`bash_fields_omitted.py`): aynı komutu ön planda 1 sn zaman aşımıyla
            # ya da arka planda 10 dk ile çalıştırmak AYNI kartı üretiyordu, oysa
            # Onayla iki farklı sözlüğü yetkilendiriyor. Kullanıcı, onayladığı
            # çocuğun turdan sonra da yaşayıp yaşamayacağını göremiyordu.
            _ek = [f"{a}={inp[a]}" for a in ("timeout", "run_in_background",
                                             "description") if a in inp]
            return f"{komut}\n[{' · '.join(_ek)}]" if _ek else komut
        if tool_name == "Read":
            return inp.get("file_path", "")
        if tool_name in ("LS", "NotebookRead"):
            # Onay kartı workspace dışı okumada da çıkıyor → kullanıcının karar
            # verebilmesi için HEDEF YOL görünmeli, ham JSON değil.
            return inp.get("path", inp.get("notebook_path", inp.get("file_path", "")))
        if tool_name in ("Glob", "Grep"):
            pat = inp.get("pattern", "")
            path = inp.get("path", "")
            return f"{pat} @ {path}" if path else pat
        if tool_name == "WebFetch":
            return inp.get("url", "")
        if tool_name == "WebSearch":
            return inp.get("query", "")
        # MCP ve tanınmayan araçlar. 160 karakterlik kesme SADECE çip için:
        # doğrulama turu (`mcp_approval_suffix_truncated.py`) gösterdi ki onay
        # kartında bu kesme, C3'ün tam olarak aynı kusuruydu — 220 zararsız
        # karakterin ardındaki `operation=DESTRUCTIVE_...` alanı kartta hiç
        # görünmüyor ama Onayla sözlüğün TAMAMINI yetkilendiriyordu. Yazma
        # araçlarında kaldırdığım kesmenin bu daldaki ikizi.
        _yuk = json.dumps(inp, ensure_ascii=False)
        return f"{tool_name} {_yuk if tam_govde else _yuk[:160]}"
    except Exception:
        return tool_name


def _trim_args(inp: dict, cap: int = 1200) -> dict:
    """Tool girdisini chip'in 'PARAMETRELER' panelinde göstermek için kırpar
    (örn. Write'ın dosya içeriği devasa olabilir — SSE'yi şişirmesin)."""
    out: Dict[str, Any] = {}
    try:
        for k, v in (inp or {}).items():
            if isinstance(v, str) and len(v) > cap:
                out[k] = v[:cap] + f"… [+{len(v) - cap} karakter]"
            else:
                out[k] = v
    except Exception:
        return inp or {}
    return out


def _tool_result_text(block) -> str:
    """ToolResultBlock.content → düz metin (str | [{'type':'text',...}] | TextBlock listesi)."""
    c = getattr(block, "content", None)
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for item in c:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif hasattr(item, "text"):
                parts.append(getattr(item, "text") or "")
        return "\n".join(p for p in parts if p)
    return ""


class ClaudeSDKSession:
    """Tek bir sohbete ait kalıcı ClaudeSDKClient sarmalayıcısı."""

    def __init__(
        self,
        conversation_id: int,
        *,
        model: Optional[str] = None,
        cwd: Optional[str] = None,
        permission_mode: str = "default",
        setting_sources: Optional[List[str]] = None,
        mcp_servers: Optional[dict] = None,
        disallowed_tools: Optional[List[str]] = None,
        approval_timeout: float = APPROVAL_TIMEOUT_S,
        auto_approve: bool = False,
        effort: Optional[str] = None,
        resume_id: Optional[str] = None,
    ):
        # CLI'ın KENDİ diskindeki oturumu geri çağıran kimlik (DB'de saklanıyor).
        # Uygulama yeniden başladığında tam transcript bu sayede geri geliyor;
        # yoksa yalnız 20.000 karakterlik DB enjeksiyonu kalıyor (%71 kayıp ölçüldü).
        self.resume_id = resume_id
        self.conversation_id = conversation_id
        self.model = model
        self.cwd = cwd
        self.permission_mode = permission_mode
        # Reasoning effort (Claude-only): "low"|"medium"|"high"|"xhigh"|"max".
        # connect-time'da --effort olarak verilir; oturum ortasında DEĞİŞTİRİLEMEZ.
        self.effort = effort
        # Oto mod: True ise araç onayı kartı GÖSTERİLMEZ, otomatik izin verilir
        # (path-traversal koruması yine uygulanır). Adım modunda False → her işlemde kart.
        self.auto_approve = auto_approve
        # Varsayılan da güvenli tarafta: çağıran unutursa kapı yine düşmesin.
        self.setting_sources = setting_sources if setting_sources is not None else list(CLAUDE_SETTING_SOURCES)
        self.mcp_servers = mcp_servers or {}
        self.disallowed_tools = disallowed_tools or []
        self.approval_timeout = approval_timeout

        self._client = None
        self._started = False
        self._broken = False          # reader/transport öldü → get_session taze kurar
        self._turn_lock = asyncio.Lock()
        self._out_q: Optional[asyncio.Queue] = None
        self.session_id: Optional[str] = None
        # İptal (Durdur) için: aktif tur boyunca bekleyen gate'ler + iptal sinyali
        self._cancel_event: Optional[asyncio.Event] = None
        self._active_gate_ids: Set[str] = set()

        # ── Sürekli reader + tur durum makinesi ──────────────────────────
        self._reader_task: Optional[asyncio.Task] = None
        self._turn_active = False
        self._final_text = ""          # tur boyunca biriken asistan metni (TextBlock'lardan)
        self._last_result_text = ""    # son ResultMessage.result (slash komut çıktıları için)
        self._saw_text_delta = False   # partial delta geldiyse blok metnini tekrar basma
        self._saw_thinking_delta = False
        self._txt_buf = ""             # delta birleştirme tamponları (SSE spam azaltma)
        self._think_buf = ""
        self._turn_tokens = 0          # tur boyunca üretilen output token (canlı sayaç)
        self._msg_tokens_seen = 0      # aktif API mesajının son bilinen output_tokens'ı
        # Arka plan görevleri (session ömürlü — görev turlar arası sürebilir).
        self._active_tasks: Dict[str, str] = {}   # task_id → açıklama
        # Names of tasks that FINISHED this turn. Kept so the AUTO-WAKE notice can
        # say "which task finished" — the name removed from `_active_tasks` at
        # completion time wasn't stored anywhere else.
        self._done_tasks: List[str] = []
        # Bash shells started with `run_in_background`: tool_use_id → command.
        # Deliberately NOT put into `_active_tasks` — there is NO SDK event that
        # reports a background shell ending (TaskUpdated/TaskNotification only fire
        # for Task-based work and carry no `tool_use_id`). If it were in
        # `_active_tasks`, it would never drain, the ResultMessage branch would keep
        # the SSE open, and every turn that starts a background server would look
        # "still running" until the 900s watchdog. The only way out: the model
        # closing the shell with `BashOutput`/`KillShell` — if that output says
        # "completed", the finish is MEASURED.
        self._bg_shells: Dict[str, str] = {}
        self._result_pending = False   # Result geldi ama arka plan görevleri sürüyor
        self._nudges = 0               # görevler bitince gönderilen dürtme sayısı (tur başına)
        self._turn_started_at = 0.0
        self._cancel_requested = False
        self._grace_task: Optional[asyncio.Task] = None
        self._usage_event: Optional[dict] = None
        # tool_use_id → araç adı: ToolResultBlock geldiğinde çıktıyı doğru chip'e
        # bağlamak için (frontend tool_id ile eşleştirir). pop-on-use → küçük kalır.
        self._tool_names: Dict[str, str] = {}
        # API stall teşhisi: CLI'dan son mesajın geldiği an + son bilinen limit durumu.
        # Heartbeat bunlarla "neden bekliyoruz"u kullanıcıya SÖYLER (kör bekleme yerine).
        self._last_cli_msg_at: float = time.time()
        # Son İÇERİK ilerlemesi (ping/keepalive HARİÇ). Fable derin düşünürken dakikalarca
        # içerik gelmez ama ping akar → iki zaman ayrışınca "derin düşünme" teşhisi konur.
        self._last_progress_at: float = time.time()
        self._rate_limit: Optional[Dict[str, Any]] = None

    # ── Yaşam döngüsü ────────────────────────────────────────────────────
    @property
    def is_live(self) -> bool:
        """Altındaki CLI süreci ŞU AN ayakta mı.

        Cache'te bir session NESNESİ olması sürecin var olduğu anlamına gelmiyor:
        nesne kurulur, süreç ancak ilk `start()` ile doğar. Salt-okunur uçlar bu
        ayrımı yapmak zorunda — yapmadıklarında `stream()`/`usage_card_text()`
        kendiliğinden `start()` çağırıyor ve bir GET süreç doğuruyordu
        (denetim, 30 Ağu 2026).
        """
        return self._started and not self._broken

    async def start(self):
        if self._started:
            return
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

        opts_kwargs: Dict[str, Any] = dict(
            permission_mode=self.permission_mode,
            setting_sources=self.setting_sources,
            # Only the MCP servers this app passes in `mcp_servers`. Without it
            # the "user" setting source loaded every server of the owner's own
            # Claude Code (measured 26 Sep 2026: ~60, among them a second Unity
            # server "UnityMCP", Gmail, Drive, Vercel). The model used that
            # UnityMCP, so its calls carried no X-Gamachine-Conversation header
            # (approval cards went unowned) and `mcp__UnityMCP__manage_script`
            # slipped past the `mcp__unityMCP__manage_script` ban.
            strict_mcp_config=True,
            can_use_tool=self._can_use_tool,
            cwd=self.cwd,
            # Canlı thinking/text delta'ları + token sayacı için şart.
            include_partial_messages=True,
            # claude_code preset'i korunur (skill/slash bozulmaz); ortam bilgisi eklenir.
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": _APP_SYSTEM_APPEND},
            # Görsel taşıyan tek bir stdout satırı 1 MiB varsayılanını aşınca oturum
            # komple düşüyordu; gerekçe ve ölçüm _SDK_STDOUT_LIMIT_BYTES'ta.
            max_buffer_size=_SDK_STDOUT_LIMIT_BYTES,
        )
        # Windows'ta npm kurulumu PATH'e yalnız `claude.cmd` koyuyor ve SDK toplu-iş
        # kabuğu çalıştırmayı reddediyor → gerçek exe'yi biz gösteriyoruz.
        # None ise anahtar HİÇ verilmiyor: SDK'nın kendi arama sırası bozulmasın.
        _cli = claude_ikilisini_coz()
        if _cli:
            opts_kwargs["cli_path"] = _cli
        # Kaldığın yerden devam: CLI kendi transcript'ini diskte tutuyor, biz yalnız
        # kimliği saklıyoruz. Kimlik geçersizse (dosya silinmiş, başka makine) SDK
        # hata verir ve `stream()` içindeki mevcut "session sıkışmış → reset + retry"
        # dalı temiz bir oturumla yeniden dener; yani başarısızlık ÖLÜMCÜL DEĞİL.
        if self.resume_id:
            opts_kwargs["resume"] = self.resume_id
            logger.info(f"[ClaudeSDKSession:{self.conversation_id}] resume={self.resume_id}")
        if self.model:
            opts_kwargs["model"] = self.model
        if self.effort:
            opts_kwargs["effort"] = self.effort  # ClaudeAgentOptions.effort → --effort
        if self.mcp_servers:
            opts_kwargs["mcp_servers"] = self.mcp_servers
        if self.disallowed_tools:
            opts_kwargs["disallowed_tools"] = self.disallowed_tools

        self._client = ClaudeSDKClient(options=ClaudeAgentOptions(**opts_kwargs))
        await self._client.__aenter__()
        self._started = True
        self._broken = False
        self._reader_task = asyncio.create_task(self._reader_loop())
        logger.info(f"[ClaudeSDKSession:{self.conversation_id}] başlatıldı (model={self.model}, effort={self.effort or '-'})")

    async def close(self):
        # Önce reader'ı durdur (transport kapanırken yarım okuma gürültüsü olmasın),
        # sonra client'ı kapat. Reader'ın KENDİ içinden çağrılırsa kendini iptal etmez.
        self._cancel_grace()
        # Closing the session ends every card it opened. Without this the gate
        # and its owner outlived the session: the waiter sat out the full
        # approval timeout, and the stale owner entry kept blocking AUTO-WAKE
        # for the conversation it named. wake=True releases the waiter now.
        for gid in list(self._active_gate_ids):
            release_gate(gid, wake=True)
        self._active_gate_ids.clear()
        rt = self._reader_task
        self._reader_task = None
        if rt is not None and rt is not asyncio.current_task():
            rt.cancel()
            try:
                await rt
            except BaseException:
                pass
        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.__aexit__(None, None, None), timeout=10)
            except Exception as e:
                logger.warning(f"[ClaudeSDKSession:{self.conversation_id}] kapatma hatası: {e}")
        self._client = None
        self._started = False
        self._turn_active = False

    # ── can_use_tool: native onay + AskUserQuestion köprüsü ──────────────
    async def _can_use_tool(self, tool_name: str, input_data: dict, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        out_q = self._out_q
        gate_id = uuid.uuid4().hex

        # Yol tabanlı okuma araçları workspace DIŞINI hedefliyorsa auto-allow'dan
        # DÜŞÜRÜLÜR ve aşağıdaki normal onay kartı akışına girer.
        # Ölçüldü (2026-07-28): auto-allow dalı workspace kontrolünden ÖNCE geldiği
        # için adım modunda Read("/etc/passwd"), Glob("/Users/**/*.key"), LS("/")
        # kartsız izin alıyordu.
        # Neden hard-deny DEĞİL: workspace dışı okuma meşru olabilir (Unity kurulum
        # dizini, paket önbelleği, log). Reddetmek çalışan kullanımı kırardı; karar
        # kullanıcıya bırakılıyor.
        # Neden yalnız adım modunda: oto modda workspace dışına çıkabilmek kullanıcının
        # açıkça kabul ettiği bir taviz (28 Tem 2026 kararı) — orada kart çıkmaz.
        _outside_read = (
            not self.auto_approve
            and _read_target_outside_workspace(tool_name, input_data, self.cwd or "")
        )

        # Salt-okunur araçlar → onay sormadan otomatik izin (gürültü azaltma)
        if tool_name in _AUTO_ALLOW_TOOLS and not _outside_read:
            return PermissionResultAllow(updated_input=input_data)
        # unityMCP salt-okuma sorguları (Unity'de hiçbir kalıcı iz bırakmaz) → otomatik izin.
        #
        # Liste burada DEĞİL, kütükte: `unity-mcp/Server/src/services/registry/
        # tool_actions.json`. Elle yazılmış hali üç yönden birden bozuktu ve
        # üçü de sessizdi (ölçüldü 2026-07-29):
        #   - ÖLÜ: `manage_scene` action'ı `get_info` diye bir şey yok, o muafiyet
        #     hiçbir zaman eşleşmedi.
        #   - DAR: gerçekten zararsız 9 araç (`unity_reflect`, `find_in_file`,
        #     `get_sha`, …) ve ~40 okuma action'ı listede yoktu; keşif araçları her
        #     turda kart çıkarıyordu, ki bu refleks-onaya alıştırır.
        #   - GENİŞ: `read_console` koşulsuz muaftı, oysa `action="clear"` konsolu siler.
        # Kütük kaynağa karşı bir ayrışma testiyle bağlı; buraya kopyalamak
        # kütüğün kapatmak için var olduğu sapma sınıfını geri getirirdi.
        if is_unity_mcp_read_only(tool_name, input_data):
            return PermissionResultAllow(updated_input=input_data)

        # AskUserQuestion → A/B/C seçim kartı
        if tool_name == "AskUserQuestion":
            # gate'i emit'ten ÖNCE kaydet (yarış önleme)
            ev = register_gate(gate_id, self.conversation_id, kind="question")
            self._active_gate_ids.add(gate_id)
            if out_q is not None:
                await out_q.put({"type": "question_needed", "gate_id": gate_id,
                                 "questions": input_data.get("questions", [])})
            answers = await self._wait_gate(ev, QUESTION_GATES, QUESTION_RESULTS, gate_id, "soru")
            if answers is None:
                return PermissionResultDeny(message="Kullanıcı soruyu yanıtlamadı (zaman aşımı).")
            return PermissionResultAllow(updated_input={**input_data, "answers": answers})

        # Path güvenliği: workspace dışına yazımı kullanıcıya sormadan reddet
        if tool_name in _FILE_WRITE_TOOLS:
            fp = input_data.get("file_path", input_data.get("path", ""))
            if fp and not _path_in_workspace(fp, self.cwd or ""):
                logger.warning(f"[ClaudeSDKSession:{self.conversation_id}] workspace dışı yazım reddedildi: {fp}")
                if out_q is not None:
                    await out_q.put({"type": "tool_result", "tool": tool_name, "success": False,
                                     "summary": f"🚫 Workspace dışı yazım engellendi: {fp}"})
                return PermissionResultDeny(message=f"'{fp}' workspace dışında — güvenlik nedeniyle reddedildi.")

        refusal = _unity_file_refusal(tool_name, input_data, self.cwd or "")
        if refusal is not None:
            logger.warning(f"[ClaudeSDKSession:{self.conversation_id}] Unity file rule refused {tool_name}: {refusal.path}")
            if out_q is not None:
                await out_q.put({"type": "tool_result", "tool": tool_name, "success": False,
                                 "summary": refusal.summary})
            return PermissionResultDeny(message=refusal.message)

        # Oto mod: onay kartı gösterme, otomatik izin ver (path güvenliği yukarıda uygulandı)
        if self.auto_approve:
            return PermissionResultAllow(updated_input=input_data)

        # Normal araç → onay kartı (adım modu)
        # gate'i emit'ten ÖNCE kaydet
        ev = register_gate(gate_id, self.conversation_id)
        self._active_gate_ids.add(gate_id)
        if out_q is not None:
            await out_q.put({
                "type": "command_approval_needed",
                "gate_id": gate_id,
                "tool": tool_name,
                "title": getattr(context, "title", None) or getattr(context, "display_name", None),
                # Kart = kullanıcının karar verdiği yer → gövde TAM gider.
                "command": _describe_tool(tool_name, input_data, tam_govde=True),
            })
        res = await self._wait_gate(ev, APPROVAL_GATES, APPROVAL_RESULTS, gate_id, "onay")
        if bool(res):
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(message="Kullanıcı işlemi reddetti.")

    async def _wait_gate(self, ev: asyncio.Event, gates: dict, results: dict,
                         gate_id: str, label: str):
        """Frontend cevabını bekler; sonucu (bool veya answers dict) döndürür, yoksa None."""
        try:
            await asyncio.wait_for(ev.wait(), timeout=self.approval_timeout)
            return results.pop(gate_id, None)
        except asyncio.TimeoutError:
            logger.warning(f"[ClaudeSDKSession:{self.conversation_id}] {label} zaman aşımı gate={gate_id}")
            return None
        finally:
            release_gate(gate_id)

    # ── İptal (Durdur) ───────────────────────────────────────────────────
    async def cancel_turn(self):
        """Kullanıcı 'Durdur' dediğinde aktif turu iptal eder.
        1) Bekleyen onay/soru gate'lerini 'reddedildi' ile çözer (yoksa can_use_tool
           300sn bloklu kalır). 2) SDK turunu interrupt() eder. 3) Tur makul sürede
           bitmezse session'ı ZORLA resetler — böylece sonraki mesaj asla eski kilide
           takılmaz. _turn_lock ALMAZ (deadlock önlemi — cancel ayrı request task'ından gelir)."""
        self._cancel_requested = True
        self._cancel_grace()
        for gid in list(self._active_gate_ids):
            if gid in APPROVAL_GATES:
                APPROVAL_RESULTS[gid] = False
                APPROVAL_GATES[gid].set()
            if gid in QUESTION_GATES:
                QUESTION_RESULTS[gid] = None
                QUESTION_GATES[gid].set()
        if self._cancel_event is not None:
            self._cancel_event.set()

        interrupted = False
        try:
            if self._client is not None:
                await asyncio.wait_for(self._client.interrupt(), timeout=8.0)
                interrupted = True
        except Exception as e:
            logger.warning(f"[ClaudeSDKSession:{self.conversation_id}] interrupt hatası: {e}")

        if interrupted:
            # Interrupt kabul edildi → CLI kısa sürede Result üretip turu bitirmeli.
            for _ in range(40):  # ≤10 sn
                if not self._turn_active:
                    logger.info(f"[ClaudeSDKSession:{self.conversation_id}] tur iptal edildi (interrupt)")
                    return
                await asyncio.sleep(0.25)

        # Interrupt başarısız ya da tur hâlâ aktif → sert reset. Sonraki mesaj taze
        # session açar (session_id None → DB transcript'i yeniden enjekte edilir).
        logger.warning(f"[ClaudeSDKSession:{self.conversation_id}] interrupt işlemedi → session zorla resetleniyor")
        await self._finish_turn(error="⏹ Tur durduruldu (session resetlendi).")
        if _SESSIONS.get(self.conversation_id) is self:
            _SESSIONS.pop(self.conversation_id, None)
        await self.close()

    # ── Sürekli reader: CLI'dan gelen HER mesajı işler ───────────────────
    async def _reader_loop(self):
        try:
            async for msg in self._client.receive_messages():
                await self._on_message(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[ClaudeSDKSession:{self.conversation_id}] reader koptu")
            self._broken = True
            await self._finish_turn(error=f"Claude session koptu: {e}")
            if _SESSIONS.get(self.conversation_id) is self:
                _SESSIONS.pop(self.conversation_id, None)
            # Kendi içinden tam close çağrılmaz (kendini iptal etmesin) — subprocess
            # zaten ölmüş; artıkları arka plan task'ı temizler (close, current_task
            # kontrolü sayesinde reader'ı kendi kendine iptal ettirmez).
            asyncio.create_task(self.close())

    async def _emit(self, ev: dict):
        """Aktif tur SSE'sine bas; SSE yoksa sessizce düş (metin zaten _final_text'te
        birikiyor; tur bitince DB'ye kaydedilir)."""
        q = self._out_q
        if q is not None:
            await q.put(ev)

    def _begin_turn(self):
        self._turn_active = True
        self._final_text = ""
        self._last_result_text = ""
        self._saw_text_delta = False
        self._saw_thinking_delta = False
        self._txt_buf = ""
        self._think_buf = ""
        self._turn_tokens = 0
        self._msg_tokens_seen = 0
        self._result_pending = False
        self._nudges = 0
        # The new turn's wake notice must not carry PREVIOUS turn's task names.
        self._done_tasks = []
        self._turn_started_at = time.time()
        self._cancel_requested = False
        self._usage_event = None
        self._cancel_grace()

    async def _flush_deltas(self):
        if self._think_buf:
            t, self._think_buf = self._think_buf, ""
            await self._emit({"type": "thinking", "text": t})
        if self._txt_buf:
            t, self._txt_buf = self._txt_buf, ""
            await self._emit({"type": "text", "content": t})

    async def _finish_turn(self, error: Optional[str] = None):
        """Turu kapat: usage + response + done + sentinel. SSE koptuysa metni DB'ye yaz."""
        if not self._turn_active:
            return
        self._turn_active = False
        self._result_pending = False
        self._cancel_grace()
        await self._flush_deltas()
        final = self._final_text or self._last_result_text
        q = self._out_q
        if q is not None:
            if self._usage_event:
                await q.put(self._usage_event)
            if error:
                # ⚠️ Maskeleme BURADA, çağrı yerinde değil: oturumdan çıkan her
                # hata olayı bu boğazdan geçiyor ve buradan doğrudan SSE'ye,
                # yani tarayıcıya gidiyor. Oturum yapılandırması artık unityMCP
                # `X-API-Key`'ini taşıdığı için istisna metni sırrı taşıyabilir.
                # Denetim bulgusu: dış döngüde yapılan maskeleme reader
                # döngüsünden gelen hataları KAPSAMIYORDU — bir korumayı
                # çağrı yerine koymak, unutulan çağrı kadar koruma demek.
                from secret_redaction import redact_secrets as _redact
                # ONE terminal event per turn: on the error path the turn ends
                # with `error` and MUST NOT also send `done`. It used to send
                # both, and `_normalize_session_event` then stamped the `done`
                # with the default `stop_reason="complete"` — so a transport
                # failure reached the UI as a failure AND a successful
                # completion, and the user could not tell which was true
                # (audit, 30 Aug 2026).
                #
                # The partial text still goes out first: `response` carries
                # content, not termination, and dropping it would turn a
                # reporting bug into data loss. Ending the stream is the
                # sentinel's job, not `done`'s — see `stream()`, which breaks
                # on `None`.
                if final:
                    await q.put({"type": "response", "content": final})
                await q.put({"type": "error", "message": _redact(str(error))})
            else:
                await q.put({"type": "response", "content": final})
                await q.put({"type": "done", "session_id": self.session_id})
            await q.put(None)  # sentinel → stream() biter
        elif final and not error:
            # SSE kapalıyken biten tur (Durdur/kopma sonrası otonom devam) → kaybolmasın.
            if _DB_SAVE_CB is not None:
                try:
                    _DB_SAVE_CB(self.conversation_id, final)
                    logger.info(f"[ClaudeSDKSession:{self.conversation_id}] otonom tur yanıtı DB'ye kaydedildi ({len(final)} kr)")
                except Exception:
                    logger.exception("[ClaudeSDKSession] otonom yanıt DB kaydı başarısız")
            # This text written to the DB stays INVISIBLE until the chat is reloaded.
            # If a background task actually finished this turn, a wake notice is left.
            # The condition is kept narrow: waking up on a disconnect where Stop was
            # pressed, or where no task finished at all, would mean restarting on its
            # own a job the user had shut down.
            if self._done_tasks and not self._cancel_requested:
                self._queue_wake("tasks_done_saved")

    # ── Grace/nudge zamanlayıcıları (arka plan görev orkestrasyonu) ──────
    def _cancel_grace(self):
        gt = self._grace_task
        self._grace_task = None
        if gt is not None and not gt.done():
            gt.cancel()

    def _schedule_grace(self, delay: float, action: str):
        """action: 'nudge' → görevler bitti, CLI kendiliğinden devam etmezse dürt;
        'finish' → emniyet: turu bitir."""
        self._cancel_grace()
        self._grace_task = asyncio.create_task(self._grace_fire(delay, action))

    async def _grace_fire(self, delay: float, action: str):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if action != "nudge" and not self._turn_active:
            return  # 'finish' yalnızca aktif turda anlamlı; nudge tur kapandıktan sonra da
                    # çalışabilir (görev geç bitti → devam turu tetiklenir, yanıt DB'ye düşer)
        if action == "nudge" and (self._turn_active and not self._result_pending):
            return  # bu arada yeni bir kullanıcı turu başlamış → nudge araya girmesin
        if action == "nudge" and self._nudges < 1 and not self._cancel_requested and not self._broken:
            self._nudges += 1
            logger.info(f"[ClaudeSDKSession:{self.conversation_id}] görevler bitti, otonom devam gelmedi → nudge")
            await self._emit({"type": "status", "detail": "🔔 Arka plan görevleri bitti — devam ettiriliyor…",
                              "tokens": self._turn_tokens})
            try:
                await self._client.query(_NUDGE_MESSAGE)
                self._result_pending = False
                self._schedule_grace(_NUDGE_FALLBACK_S, "finish")
            except Exception:
                logger.exception("[ClaudeSDKSession] nudge gönderilemedi")
                await self._finish_turn()
        else:
            await self._finish_turn()

    def _queue_wake(self, reason: str) -> None:
        """Leave an AUTO-WAKE notice for work that finished with no SSE listening.

        This exists because of a measured blind spot: while `_out_q is None`, the
        nudge went to the LIVE CLI session, whose reply only landed in the DB, and
        the user saw nothing until the chat was reloaded. So the "proactive wake"
        was talking into a void. The notice is queued here instead; `/wake-stream`
        carries it to the client, and the client starts the next turn itself.
        """
        names = ", ".join(self._done_tasks[-3:]) if self._done_tasks else "background work"
        self._done_tasks = []
        try:
            from agentic.wake_queue import enqueue as _wake_enqueue
            _wake_enqueue(self.conversation_id, f"{reason}|{names}")
        except Exception:
            logger.exception("[ClaudeSDKSession] failed to enqueue wake notification")

    async def _maybe_tasks_drained(self):
        """Aktif arka plan görevi kalmadıysa: kısa bir süre CLI'ın otonom devam turunu
        bekle; gelmezse nudge ile devam ettir (proaktif uyanma). Tur watchdog'la
        kapanmış olsa bile çalışır — geç biten görevin sonucu kaybolmaz (hayalet tur
        + DB kaydı)."""
        if self._active_tasks or self._cancel_requested:
            return
        if self._out_q is None:
            # SSE is closed: no one to nudge. Queue a wake notice instead of a nudge.
            self._queue_wake("tasks_done")
            return
        if (self._turn_active and self._result_pending) or not self._turn_active:
            self._schedule_grace(_TASKS_DONE_GRACE_S, "nudge")

    # ── Mesaj çevirici + tur durum makinesi ──────────────────────────────
    async def _on_message(self, msg):
        from claude_agent_sdk import (
            AssistantMessage, RateLimitEvent, ResultMessage, StreamEvent, SystemMessage,
            TaskNotificationMessage, TaskProgressMessage, TaskStartedMessage,
            TaskUpdatedMessage, TERMINAL_TASK_STATUSES,
            TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock, UserMessage,
        )

        self._last_cli_msg_at = time.time()
        # ping/keepalive stream-event'leri ilerleme SAYILMAZ (Fable düşünürken sırf ping akar)
        if not (isinstance(msg, StreamEvent) and (msg.event or {}).get("type") == "ping"):
            self._last_progress_at = time.time()

        # Kullanım limiti sinyali — eskiden sessizce düşüyordu; kullanıcı limit
        # yüzünden bekleyen turu "dondu" sanıyordu. Artık açıkça gösterilir.
        if isinstance(msg, RateLimitEvent):
            info = msg.rate_limit_info
            self._rate_limit = {"status": info.status, "resets_at": info.resets_at,
                                "type": info.rate_limit_type, "utilization": info.utilization}
            if info.status == "rejected":
                when = ""
                if info.resets_at:
                    try:
                        from datetime import datetime as _dt
                        when = " — " + _dt.fromtimestamp(info.resets_at).strftime("%H:%M") + "'de sıfırlanır"
                    except Exception:
                        pass
                await self._emit({"type": "status",
                                  "detail": (
                                      "🚦 Claude bu isteği kullanım penceresi sinyaliyle "
                                      f"bekletiyor ({info.rate_limit_type or 'pencere'}){when}. "
                                      "Bu, hesabındaki tüm Claude erişiminin kapandığı anlamına "
                                      "gelmeyebilir; CLI otomatik yeniden deniyor. Durdur ile "
                                      "başka modele geçebilirsin."
                                  ),
                                  "tokens": self._turn_tokens})
            elif info.status == "allowed_warning":
                pct = f" (%{int(info.utilization * 100)})" if isinstance(info.utilization, (int, float)) else ""
                await self._emit({"type": "status",
                                  "detail": f"⚠️ Claude kullanım limitine yaklaşılıyor{pct}",
                                  "tokens": self._turn_tokens, "heartbeat": True})
            return

        # Task yaşam döngüsü — SystemMessage ALT SINIFLARI, önce bunlar kontrol edilir.
        if isinstance(msg, TaskStartedMessage):
            desc = msg.description or "arka plan görevi"
            self._active_tasks[msg.task_id] = desc
            await self._emit({"type": "status", "scope": "subagent",
                              "detail": f"🤖 Görev başladı: {desc}",
                              "tokens": self._turn_tokens})
            return
        if isinstance(msg, TaskProgressMessage):
            u = msg.usage or {}
            detail = f"🤖 {msg.description or 'Görev sürüyor'}"
            if msg.last_tool_name:
                detail += f" · {msg.last_tool_name}"
            await self._emit({"type": "status", "scope": "subagent", "detail": detail,
                              "tokens": u.get("total_tokens") or self._turn_tokens})
            return
        if isinstance(msg, TaskNotificationMessage):
            desc = self._active_tasks.pop(msg.task_id, None) or "Görev"
            self._done_tasks.append(desc)
            ok = msg.status == "completed"
            icon = "✅" if ok else ("🛑" if msg.status == "stopped" else "❌")
            await self._emit({"type": "tool_result", "tool": "Subagent", "success": ok,
                              "summary": f"{icon} {desc}: {(msg.summary or msg.status)[:200]}"})
            await self._emit({"type": "status",
                              "detail": f"{icon} Görev bitti: {desc}",
                              "tokens": self._turn_tokens})
            await self._maybe_tasks_drained()
            return
        if isinstance(msg, TaskUpdatedMessage):
            if msg.status in TERMINAL_TASK_STATUSES and msg.task_id in self._active_tasks:
                desc = self._active_tasks.pop(msg.task_id, "Görev")
                self._done_tasks.append(desc)
                ok = msg.status == "completed"
                await self._emit({"type": "tool_result", "tool": "Subagent", "success": ok,
                                  "summary": f"{'✅' if ok else '❌'} {desc}: {msg.status}"})
                await self._maybe_tasks_drained()
            return

        if isinstance(msg, StreamEvent):
            await self._on_stream_event(msg)
            return

        if isinstance(msg, SystemMessage):
            data = getattr(msg, "data", None) or {}
            if getattr(msg, "subtype", None) == "init" and isinstance(data, dict):
                self.session_id = data.get("session_id") or self.session_id
                # Slash komutlarını + skill'leri cache'le ('/' autocomplete için).
                # Canlı session daha günceldir → warmup değerinin üzerine yazar.
                global _SLASH_COMMANDS_CACHE, _SKILLS_CACHE
                sc = data.get("slash_commands")
                if isinstance(sc, list) and sc:
                    _SLASH_COMMANDS_CACHE = sc
                sk = data.get("skills")
                if isinstance(sk, list) and sk:
                    _SKILLS_CACHE = list(sk)
            return

        if isinstance(msg, AssistantMessage):
            if getattr(msg, "parent_tool_use_id", None):
                return  # subagent iç trafiği ana transcript'e karışmasın
            _api_err = getattr(msg, "error", None)
            if _api_err:
                _err_tr = {
                    "rate_limit": "kullanım limiti", "server_error": "sunucu hatası (Anthropic)",
                    "billing_error": "faturalama sorunu", "authentication_failed": "oturum/auth hatası",
                    "invalid_request": "geçersiz istek",
                }.get(_api_err, _api_err)
                await self._emit({"type": "status",
                                  "detail": f"⚠️ Claude API: {_err_tr} — CLI otomatik yeniden deniyor…",
                                  "tokens": self._turn_tokens})
            if not self._turn_active:
                # Tur kapandıktan SONRA gelen asistan mesajı = otonom devam turu
                # (watchdog/stop sonrası geciken task bitişi). "Hayalet tur" aç ki
                # Result geldiğinde metni DB'ye kaydedilsin — hiçbir yanıt kaybolmasın.
                self._begin_turn()
            self._cancel_grace()  # asistan aktif → otonom devam başladı, bitirme sayacı dursun
            for b in msg.content:
                if isinstance(b, TextBlock):
                    self._final_text += b.text
                    if not self._saw_text_delta:
                        await self._emit({"type": "text", "content": b.text})
                elif isinstance(b, ThinkingBlock):
                    if not self._saw_thinking_delta and b.thinking:
                        await self._emit({"type": "thinking", "text": b.thinking})
                elif isinstance(b, ToolUseBlock):
                    await self._flush_deltas()  # sıra korunumu: metin → araç
                    inp = b.input or {}
                    tool_id = getattr(b, "id", None)
                    if tool_id:
                        self._tool_names[tool_id] = b.name
                    if b.name in ("Task", "Agent"):
                        desc = inp.get("description") or inp.get("prompt", "")[:80] or "subagent"
                        await self._emit({"type": "tool_call", "tool": "Subagent",
                                          "tool_id": tool_id,
                                          "arguments": _trim_args(inp),
                                          "summary": f"🤖 {desc}"})
                        await self._emit({"type": "status",
                                          "detail": f"🤖 Subagent çalışıyor: {desc}",
                                          "tokens": self._turn_tokens})
                    elif b.name in _PLAN_TOOLS:
                        # Plan/todo: ham döküm yerine tek "plan yapıyor" sinyali
                        await self._emit({"type": "tool_call", "tool": "TodoWrite", "summary": ""})
                    elif b.name in _NOISE_TOOLS:
                        continue  # düşük-değerli iç araçları gizle
                    elif b.name == "Bash" and inp.get("run_in_background"):
                        self._bg_shells[tool_id or str(len(self._bg_shells))] = (
                            str(inp.get("command") or "arka plan komutu")[:120]
                        )
                        await self._emit({"type": "tool_call", "tool": b.name,
                                          "tool_id": tool_id,
                                          "arguments": _trim_args(inp),
                                          "summary": _describe_tool(b.name, inp)})
                    else:
                        await self._emit({"type": "tool_call", "tool": b.name,
                                          "tool_id": tool_id,
                                          "arguments": _trim_args(inp),
                                          "summary": _describe_tool(b.name, inp)})
            return

        if isinstance(msg, UserMessage):
            # Araç SONUÇLARI user-mesajı olarak döner (ToolResultBlock) → chip'in
            # "ÇIKTI" paneli buradan dolar. Subagent iç trafiği yine dışarıda.
            if getattr(msg, "parent_tool_use_id", None):
                return
            content = getattr(msg, "content", None)
            if not isinstance(content, list):
                return
            for b in content:
                if not isinstance(b, ToolResultBlock):
                    continue
                name = self._tool_names.pop(getattr(b, "tool_use_id", ""), None)
                if not name or name in _NOISE_TOOLS or name in _PLAN_TOOLS:
                    continue
                txt = (_tool_result_text(b) or "").strip()
                if len(txt) > 3000:
                    txt = txt[:3000] + "\n… [çıktı kırpıldı]"
                ok = not bool(getattr(b, "is_error", False))
                first_line = txt.splitlines()[0][:120] if txt else ("tamam" if ok else "hata")
                await self._emit({
                    "type": "tool_result",
                    "tool": "Subagent" if name in ("Task", "Agent") else name,
                    "tool_id": getattr(b, "tool_use_id", None),
                    "success": ok,
                    "summary": first_line,
                    "output": txt,
                })
                # The only measurable signal that a background shell has FINISHED:
                # `BashOutput`'s "completed" status (or the shell being killed).
                # When a shell ends on its own, the SDK produces no event at all —
                # so a finish not caught here is never caught anywhere.
                if self._bg_shells and name in ("BashOutput", "KillShell"):
                    if name == "KillShell" or "completed" in txt.lower():
                        _sid, _cmd = next(iter(self._bg_shells.items()))
                        self._bg_shells.pop(_sid, None)
                        self._done_tasks.append(f"background command ({_cmd})")
                        await self._maybe_tasks_drained()
            return

        if isinstance(msg, ResultMessage):
            self._last_result_text = getattr(msg, "result", "") or ""
            self._usage_event = self._build_usage_event(msg)
            if getattr(msg, "is_error", False) or self._cancel_requested or not self._active_tasks:
                await self._finish_turn()
            else:
                # Arka plan görevleri sürüyor → turu (SSE'yi) AÇIK tut; kullanıcı
                # süreci canlı izler, görevler bitince devam otomatik akar.
                self._result_pending = True
                names = ", ".join(list(self._active_tasks.values())[:3])
                await self._flush_deltas()
                await self._emit({"type": "status",
                                  "detail": f"⏳ {len(self._active_tasks)} arka plan görevi sürüyor ({names}) — bitince devam edilecek",
                                  "tokens": self._turn_tokens})
                self._schedule_grace(_TASKS_WATCHDOG_S, "finish")
            return

    async def _on_stream_event(self, msg):
        """Partial (canlı) akış: thinking/text delta'ları + token sayacı."""
        if getattr(msg, "parent_tool_use_id", None):
            return  # subagent delta'ları ana akışa karışmasın (TaskProgress zaten raporlar)
        e = msg.event or {}
        et = e.get("type")
        if et == "content_block_delta":
            d = e.get("delta") or {}
            dt = d.get("type")
            if dt == "thinking_delta":
                self._saw_thinking_delta = True
                self._think_buf += d.get("thinking", "")
                if len(self._think_buf) >= _DELTA_FLUSH_CHARS:
                    t, self._think_buf = self._think_buf, ""
                    await self._emit({"type": "thinking", "text": t})
            elif dt == "text_delta":
                self._saw_text_delta = True
                self._txt_buf += d.get("text", "")
                if len(self._txt_buf) >= _DELTA_FLUSH_CHARS:
                    t, self._txt_buf = self._txt_buf, ""
                    await self._emit({"type": "text", "content": t})
        elif et in ("content_block_stop", "message_stop"):
            await self._flush_deltas()
        elif et == "message_start":
            self._msg_tokens_seen = 0
            self._cancel_grace()  # yeni model mesajı başladı → otonom devam geldi
        elif et == "message_delta":
            out = ((e.get("usage") or {}).get("output_tokens"))
            if isinstance(out, int) and out > 0:
                self._turn_tokens += max(0, out - self._msg_tokens_seen)
                self._msg_tokens_seen = out
                await self._emit({"type": "status", "tokens": self._turn_tokens,
                                  "heartbeat": True})

    def _build_usage_event(self, msg) -> dict:
        u = getattr(msg, "usage", None) or {}
        inp = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) \
            + (u.get("cache_read_input_tokens") or 0)
        return {
            "type": "turn_usage",
            "input_tokens": inp,
            "output_tokens": u.get("output_tokens") or self._turn_tokens or 0,
            "cost_usd": getattr(msg, "total_cost_usd", None),
            "duration_ms": getattr(msg, "duration_ms", None)
                           or int((time.time() - self._turn_started_at) * 1000),
        }

    # ── Mesaj akışı: bir tur gönder, event dict'leri yield et ────────────
    async def stream(self, message: str, lock_timeout: float = 10.0) -> AsyncGenerator[dict, None]:
        if not self._started:
            await self.start()
        if self._broken:
            raise SessionBusyError("Claude session kopmuş (reader ölü).")

        if lock_timeout <= 0:
            # `lock_timeout=0` = "meşguldür de bekleme". Yan kanallar (rapor ucu)
            # bunu kullanır: eskiden çağıran önce `session_busy()` sorup sonra
            # buraya giriyordu, ve iki işlem arasında başlayan bir tur "anında
            # busy" vaadini yalanlıyordu (denetim, 30 Ağu 2026).
            #
            # Kontrol ile alma arasında ASKIYA ALMA NOKTASI YOK: asyncio.Lock
            # serbestken `acquire()` hiç await etmeden döner, yani bu iki satır
            # event loop açısından bölünemez. (`wait_for(..., timeout=0)` burada
            # ÇALIŞMAZ: coroutine'i henüz koşmamış bir task'a sarıp hemen iptal
            # eder, yani kilit boşken bile TimeoutError üretirdi.)
            if self._turn_lock.locked():
                raise SessionBusyError("Bir tur akıyor.")
            await self._turn_lock.acquire()
        else:
            # Kilidi SINIRLI bekle: önceki tur sıkıştıysa sonsuza dek "düşünüyor"da
            # kalınmaz — SessionBusyError fırlar, AgentRunner session'ı resetleyip dener.
            try:
                await asyncio.wait_for(self._turn_lock.acquire(), timeout=lock_timeout)
            except asyncio.TimeoutError:
                raise SessionBusyError("Önceki tur kilidi bırakmadı.")

        try:
            out_q: asyncio.Queue = asyncio.Queue()
            self._out_q = out_q
            self._cancel_event = asyncio.Event()
            self._active_gate_ids.clear()
            self._begin_turn()
            await self._client.query(message)
            while True:
                try:
                    ev = await asyncio.wait_for(out_q.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    # Kalp atışı: SSE'yi canlı tut + kullanıcıya NEDEN beklediğini söyle.
                    hb: Dict[str, Any] = {"type": "status", "heartbeat": True,
                                          "tokens": self._turn_tokens}
                    stall = int(time.time() - self._last_cli_msg_at)
                    if self._rate_limit and self._rate_limit.get("status") == "rejected":
                        hb["detail"] = (
                            "🚦 Claude isteği kullanım penceresi nedeniyle bekliyor — "
                            "bu tüm hesap erişiminin kapandığı anlamına gelmeyebilir "
                            "(Durdur ile başka modele geçebilirsin)"
                        )
                    elif stall >= 90:
                        # CLI'dan uzun süredir hiç mesaj yok → model düşünüyor olabilir ama
                        # limit/yoğunluk backoff'u da olabilir. Kör "düşünüyor" yerine dürüst bilgi.
                        hb["detail"] = (f"⏳ Claude API {stall // 60} dk {stall % 60} sn'dir sessiz — "
                                        "uzun düşünme ya da limit/yoğunluk (otomatik yeniden deneniyor)")
                    elif self._active_tasks:
                        hb["detail"] = f"⏳ {len(self._active_tasks)} arka plan görevi sürüyor"
                    yield hb
                    continue
                if ev is None:
                    break
                yield ev
        finally:
            # NOT: reader'ı BEKLEMEYİZ/iptal etmeyiz (eski 'await task' kilidi burdaydı).
            # SSE koptuysa tur CLI'da sürer; reader biten turun metnini DB'ye kaydeder.
            self._out_q = None
            self._cancel_event = None
            self._active_gate_ids.clear()
            self._turn_lock.release()


def _identity_mismatch(sess: "ClaudeSDKSession", kwargs: dict) -> Optional[str]:
    """Cache'li session istenen CONNECT-TIME kimliğiyle uyuşuyor mu? Uyuşmuyorsa sebep.

    `cwd`, `model` ve `effort` SDK'ya bağlanma anında verilir ve oturum ortasında
    değiştirilemez. Ölçüldü (2026-07-28): cache anahtarı yalnız `conversation_id`
    olduğu için workspace A ile açılan session B istendiğinde aynen dönüyordu
    (etkin cwd = A), model ve effort de sessizce düşüyordu. Yani kullanıcının
    seçimi kabul ediliyormuş gibi görünüp uygulanmıyordu.

    `auto_approve` bilerek DIŞARIDA: o canlı olarak her turda güncelleniyor
    (`session.auto_approve = ...`), kimliğin parçası değil.

    cwd karşılaştırması canonical: `abspath` ile karşılaştırmak aynı dizinin
    symlink alias'ında yanlışlıkla "değişti" der ve session'ı boşuna sıfırlardı.
    """
    if "cwd" in kwargs:
        want = _canonical(kwargs.get("cwd") or ".")
        have = _canonical(sess.cwd or ".")
        if want != have:
            return f"workspace {have} → {want}"
    if "model" in kwargs and kwargs.get("model") != sess.model:
        return f"model {sess.model} → {kwargs.get('model')}"
    if "effort" in kwargs and kwargs.get("effort") != sess.effort:
        return f"effort {sess.effort} → {kwargs.get('effort')}"
    return None


def _discard(conversation_id: int, sess: "ClaudeSDKSession") -> None:
    """Session'ı cache'ten düşür ve artıklarını arka planda kapat."""
    _SESSIONS.pop(conversation_id, None)
    try:
        asyncio.create_task(sess.close())
    except RuntimeError:
        # Çalışan bir event loop yok (senkron bağlam/test). Kapatmayı sessizce
        # atlamak subprocess sızdırırdı; en azından görünür kılıyoruz.
        logger.warning(f"[ClaudeSDKSession:{conversation_id}] loop yok — kapatma atlandı")


def get_session(conversation_id: int, **kwargs) -> ClaudeSDKSession:
    """conversation_id için canlı session'ı getir; yoksa (veya kopmuşsa) oluştur."""
    sess = _SESSIONS.get(conversation_id)
    if sess is not None and sess._broken:
        _discard(conversation_id, sess)
        sess = None
    if sess is not None:
        reason = _identity_mismatch(sess, kwargs)
        if reason:
            logger.info(f"[ClaudeSDKSession:{conversation_id}] {reason}; session yeniden kuruluyor")
            _discard(conversation_id, sess)
            sess = None
    if sess is None:
        sess = ClaudeSDKSession(conversation_id, **kwargs)
        _SESSIONS[conversation_id] = sess
    return sess


def peek_session(conversation_id: int) -> Optional["ClaudeSDKSession"]:
    """Canlı session'ı getir ama YOKSA KURMA.

    `get_session` eksik session'ı kuruyor, ve kurmak bir CLI süreci doğurmak
    demek. Kullanıcının bir düğmeye basması bunu tetiklememeli: rapor
    isteyen bir uç, raporlayacağı şeyi var etmemeli.

    "Canlı" = nesne var VE süreci ayakta. Başlatılmamış bir nesneyi döndürmek de
    süreç doğurmaya davetiyeydi: çağıran onu canlı sanıp `stream()` çağırıyor,
    `stream()` de `start()` ediyordu. Codex tarafındaki eşi aynı ölçütü
    kullanıyor — denetim ikisinin AYRIŞTIĞINI bulmuştu (30 Ağu 2026): burada
    `_broken` eleniyordu, orada hiçbir şey elenmiyordu.
    """
    sess = _SESSIONS.get(conversation_id)
    if sess is None or not sess.is_live:
        return None
    return sess


def session_busy(conversation_id: int) -> bool:
    """Bir tur akıyor mu.

    `stream()` tur kilidini 10 saniye bekleyip `SessionBusyError` atıyor.
    Yan kanal bunu beklememeli: kullanıcı düğmeye bastığında ya cevap ya da
    "şu an meşgul" görmeli, 10 saniyelik bir donma değil.
    """
    sess = peek_session(conversation_id)
    return bool(sess and sess._turn_lock.locked())


async def close_session(conversation_id: int):
    sess = _SESSIONS.pop(conversation_id, None)
    if sess is not None:
        await sess.close()


async def close_all_sessions():
    for cid in list(_SESSIONS.keys()):
        await close_session(cid)
