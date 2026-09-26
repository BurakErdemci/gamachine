"""
AgentRunner — Agentic Loop motoru.

AI'a araçlar (tools) verir, AI hangisini çağıracağına karar verir,
araç sonucunu alır, tekrar AI'a gönderir. İş bitene kadar döngü devam eder.

Her adımda bir SSE event callback'i çağırılır (thinking, tool_call, response, done).
"""
import os
import json
import time
import uuid
import hashlib
import logging
import asyncio
import contextlib
import re
import secrets
import subprocess
import tempfile
from collections import deque
from typing import Any, AsyncGenerator, Callable, Dict, List, NamedTuple, Optional

from agentic.command_gates import APPROVAL_GATES as _APPROVAL_GATES, APPROVAL_RESULTS as _APPROVAL_RESULTS
from agentic.command_gates import APPROVAL_TIMEOUT_S
from agentic.command_gates import GATE_OWNERS as _GATE_OWNERS
from agentic.command_gates import register_gate as _register_gate, release_gate as _release_gate

from agentic.command_safety import requires_approval as _is_dangerous_command


from google import genai
from google.genai import types as gtypes
import anthropic
import openai

from tools.tool_registry import (
    TOOL_DEFINITIONS, execute_tool, get_openai_tool_declarations,
    get_gemini_tool_declarations, _all_tool_definitions,
)
from prompts import SYSTEM_PROMPT
from providers.unity_script_tools import DISALLOWED_UNITY_TOOLS

logger = logging.getLogger(__name__)

# Last-resort ceiling, NOT the primary fuse. The primary fuse is the progress
# signal (`_ProgressGuard`): a runaway loop shows itself by repeating the same
# call, not by taking many steps, so counting steps cut healthy long runs short
# while letting a stuck one spin to the end anyway. This number only bounds a
# run that is still making progress but is unusually long.
#
# RAISED 60 -> 300 on 30 Aug 2026, and the reason is a measurement rather than a
# feeling. 60 was chosen when the fuse moved off step counting, and it was still
# a step count: a plain MCP session — "list these sixty folders" — reached it and
# stopped with `max_iterations`, which is exactly the failure the redesign was
# meant to end. Burak reported it from a running build ("tool adımları 50 60
# adımlı oluyor"), and `test_long_mcp_flow.py` reproduces it.
#
# What this number is for, stated plainly so the next person does not shrink it
# back: it bounds COST in the pathological case where every step is genuinely
# novel and the guard therefore never fires. It is not a judgement about how
# long healthy work may be — the guard makes that judgement, five repeats deep.
# A run that reaches 300 distinct, progressing steps is either extraordinary or
# a fuse we have not thought of yet; either way stopping to say so is right.
MAX_ITERATIONS = 300

# How many times the same (tool, arguments, RESULT) triple may occur inside the
# window before the run is declared stalled.
#
# Both halves of this were bought by an audit (30 Aug 2026), and each closes a
# hole the first version had:
#
# - The result is part of the signature because arguments alone cannot tell a
#   poll from a spin. The bundled Unity MCP `run_tests` tool explicitly asks the
#   caller to poll `get_test_job(job_id)`, so three identical calls returning
#   pending / pending / complete are the documented HAPPY path — and the first
#   version stopped the run on the third one, right before the model could read
#   the finished result.
# - Five, not three: a poll whose result genuinely does not move (a long Unity
#   compile) needs headroom. Five identical answers still means nothing new has
#   entered the context, and stopping to say so beats spinning to the ceiling.
_STALL_LIMIT = 5

# Repetitions are counted inside this window rather than requiring them to be
# consecutive. The first version reset its streak whenever the previous
# signature differed, so it recognised only a one-node self-loop: a model
# alternating read_file(A) / read_file(B) forever never tripped it and ran to
# the ceiling — measured at 60 provider calls, and 240 tool calls when the
# repeats arrived batched inside single responses.
_STALL_WINDOW = 24

# Sağlayıcı isteği için zaman aşımı ve bekleme sırasındaki kalp atışı aralığı.
#
# Zaman aşımı bir TAVAN değil bir SIGORTA: openai SDK'sının varsayılanı 600 sn
# ve o süre boyunca hiçbir şey söylenmiyordu. 300 sn, yavaş bir akıl yürütme
# modeline yer bırakırken sonsuza kadar asılı kalmayı engelliyor; asıl çözüm
# zaten kalp atışı, çünkü kullanıcı beklemeyi kendisi kesebiliyor (Durdur).
#
# Kalp atışı 15 sn: Burak 30 Ağu 2026'da iki dakika boyunca yalnız
# "düşünüyor..." gördü ve uygulamanın mı sağlayıcının mı takıldığını
# ayırt edemedi. Sessizlik burada bir arıza gibi okunuyor.
_SAGLAYICI_ZAMAN_ASIMI = 300.0
_KALP_ATISI_SN = 15.0

# Above this many characters a canonical argument or result string is hashed
# instead of kept: a single `write_file` call can carry a whole source file, and
# the guard compares signatures rather than reading them.
_STALL_ARG_MAX = 2000

# Onay bekleme süresi `command_gates`'ten gelir (tek kaynak, gerekçesi orada).


# Güvensiz bir projeden gelen dosya belleğe alınmadan önceki üst sınır. 1 MB,
# gerçek bir `.mcp.json`'ın binlerce katı; amaç doğruluk değil, kötü niyetli ya
# da bozuk bir dosyanın tur başlangıcını bloke etmesini engellemek.
_MCP_JSON_AZAMI_BAYT = 1024 * 1024

# Ürünün kalıcı yerel API anahtarının yeri. `unity_mcp_manager` bunu bir kez
# üretip tekrar tekrar OKUYOR (`_load_or_create_local_api_token`), yani değer
# oturumlar arası SABİT — sahipliğin kanıtlanabilir olmasının sebebi bu.
_URUN_SIR_YOLU = os.path.join(os.path.expanduser("~"), ".unity-mcp", "local-api-token")


def _urunun_sirri() -> Optional[str]:
    """Ürünün kalıcı yerel API anahtarı; okunamıyorsa ``None``.

    ⚠️ Anahtarı YARATMAZ. Yaratan yol `unity_mcp_manager`'ın işi; temizlik bir
    yan etki olarak sır dosyası oluşturmamalı. `None` dönerse temizlik hiçbir
    kaydı ürünün malı sayamaz ve dosyaya dokunmaz — doğru hata yönü bu.
    """
    try:
        from local_token_file import read_secret_file
        return read_secret_file(_URUN_SIR_YOLU) or None
    except Exception:
        return None


def _urunun_kaydi_mi(ad: str, tanim: object, sir: Optional[str]) -> bool:
    """Bu kayıt ürünün mü? SEZGİSEL DEĞİL, ÜRÜNÜN GERÇEK SIRRIYLA eşleşme arar.

    Bu fonksiyonun önceki altı hâli sahipliği TAHMİN ediyordu (sunucu adı, sonra
    `X-API-Key` başlığının VARLIĞI, sonra yerellik, sonra 43 karakterlik yol
    segmenti) ve yedi denetim turunun dördü tam da bu tahminlerin kenarlarında
    bulgu yazdı — sonuncusu YÜKSEK: kullanıcının `localhost:7777`de koşan kendi
    sunucusu, kendi anahtarıyla, ürünün malı sayılıp siliniyordu.

    Tahminin çürüdüğü yer şuydu: **ad da, başlığın varlığı da, yerellik de
    ürüne özgü değil.** Kullanıcı yerel MCP sunucusu çalıştırabilir, ona
    `unityMCP` diyebilir ve `X-API-Key` kullanabilir. Bu üç sinyalin hiçbiri
    sahiplik kanıtı üretmiyor, sadece daha dar bir tahmin üretiyor.

    Ölçülen çıkış yolu: ürünün anahtarı KALICI (`~/.unity-mcp/local-api-token`,
    varsa yeniden kullanılıyor). Yani "bu kayıt ürünün sırrını TAŞIYOR mu"
    sorusu kesin olarak cevaplanabiliyor ve kullanıcının kendi anahtarı ona
    eşit olmuyor. Sahiplik artık kanıt, tahmin değil.

    Üç kabul biçimi, üçü de değer eşleşmesine dayanıyor:
      1. `headers` içinde `X-API-Key` == ürünün sırrı (bugünkü biçim).
      2. URL yolunda ürünün sırrı (eski biçim; sır `headers` yerine
         `/mcp/<sır>` segmentindeydi, `e988258`..`c69f3eb` arası).
      3. `env["LOCAL_APP_TOKEN"]` == ürünün backend token'ı — `39994dd` öncesi
         `unityai` kaydı onu düz metin taşıyordu.

    ⚠️ ÜÇÜNCÜ BİÇİM DE **DEĞER** EŞLEŞMESİ. İlk yazımında yalnız ANAHTAR ADINA
    bakıyordu ve bu, aynı commit'te tasfiye edildiği iddia edilen sınıfın ta
    kendisiydi: kullanıcının `env: {"LOCAL_APP_TOKEN": "${LOCAL_APP_TOKEN}"}`
    yazan kaydı ürünün malı sayılıp siliniyordu — tek kayıtsa dosyanın tamamı.
    Denetim bunu YÜKSEK olarak yakaladı; üstelik bu docstring o sırada bile
    "üçü de değer eşleşmesi" diyordu, yani belge davranıştan ayrışmıştı.

    Token oturumlar arası dönebilir; dönmüşse eski bir kayıt eşleşmez ve
    temizlenmez. Bu doğru hata yönü: eşleşmeyen değer zaten GEÇERSİZ bir
    token'dır, kullanıcının silinen kaydı ise geri gelmez.

    Sunucu ADINA hiç bakılmıyor: ürünün sırrını taşıyan bir kayıt, adı ne olursa
    olsun ürünündür; taşımayan da değildir.
    """
    if not isinstance(tanim, dict):
        return False

    ortam = tanim.get("env")
    if isinstance(ortam, dict):
        deger = ortam.get("LOCAL_APP_TOKEN")
        if isinstance(deger, str) and deger:
            try:
                from local_token_file import read_local_app_token
                backend_token = read_local_app_token()
            except Exception:
                backend_token = ""
            if backend_token and secrets.compare_digest(deger, backend_token):
                return True

    if not sir:
        return False

    basliklar = tanim.get("headers")
    if isinstance(basliklar, dict):
        for anahtar, deger in basliklar.items():
            if anahtar.lower() == "x-api-key" and isinstance(deger, str):
                if secrets.compare_digest(deger, sir):
                    return True

    url = tanim.get("url")
    if isinstance(url, str) and sir in url:
        return True

    return False


def _oturum_yeniden_kurma_gerekceleri(mevcut, *, model, effort, workspace, mcp_servers) -> List[str]:
    """Cache'li oturumun CONNECT-TIME kimliği istenenden farklı mı? Farkların listesi.

    Modül düzeyinde ve saf, çünkü asıl çağrı yeri yüzlerce satırlık bir async
    metodun içinde ve oradan sınanamıyordu — bu depoda `_remove_project_mcp_json`
    de aynı gerekçeyle dışarı alınmıştı. Ölçülmüş bedeli var: karar gövdenin
    içindeyken testi kaynak taramasına düşüyordu ve bir mutasyon turu o testin
    KÖR olduğunu gösterdi (koşul `if False:` yapıldı, test yeşil kaldı).

    Karşılaştırılan dört şeyin dördü de connect-time kilitli:
      · effort — SDK'da `set_effort` yok.
      · model.
      · workspace — CANONICAL karşılaştırma; `abspath` symlink alias'ında
        yanlış "değişmedi"/"değişti" der. Ölçüldü (2026-07-28): workspace A ile
        açılan oturum B istendiğinde aynen dönüyordu ve model sessizce düşüyordu.
      · unityMCP kaydı — artık `.mcp.json` üzerinden değil doğrudan SDK
        seçeneği olarak geçiyor. Karşılaştırılmazsa Unity MCP kapalıyken açılan
        bir sohbet, MCP sonradan açılsa bile araçsız kalırdı ve kullanıcının
        bunu düzeltmesinin yolu olmazdı (denetim bulgusu; egzotik değil, en
        sıradan açılış sırası).
    """
    if mevcut is None:
        return []
    from providers.claude_sdk_session import _canonical as _canon

    gerekceler: List[str] = []
    if mevcut.effort != effort:
        gerekceler.append(f"effort {mevcut.effort}→{effort}")
    if mevcut.model != model:
        gerekceler.append(f"model {mevcut.model}→{model}")
    if _canon(mevcut.cwd or ".") != _canon(workspace):
        gerekceler.append(f"workspace {mevcut.cwd}→{workspace}")
    if (getattr(mevcut, "mcp_servers", None) or {}) != (mcp_servers or {}):
        onceki = "var" if getattr(mevcut, "mcp_servers", None) else "yok"
        simdi = "var" if mcp_servers else "yok"
        gerekceler.append(f"unityMCP kaydı {onceki}→{simdi}")
    return gerekceler


def _remove_project_mcp_json(workspace_path: Optional[str]) -> None:
    """Ürünün kendi `.mcp.json` kayıtlarını kullanıcının projesinden ÇIKARIR.

    ⚠️ BURAYA `.mcp.json` YAZAN KOD GERİ EKLENMEYECEK. Claude yolunda unityMCP
    artık SDK'ya doğrudan `mcp_servers` ile geçiliyor (bkz.
    `claude_sdk_session.CLAUDE_SETTING_SOURCES`), çünkü o dosyanın okunabilmesi
    `setting_sources` içinde `"project"` olmasını gerektiriyordu ve `"project"`
    onay kapısını dört ayrı yoldan düşürüyordu.

    Dosyanın kendisi ayrıca bir sır sızıntısıydı: `headers` içinde unityMCP
    `X-API-Key`'ini DÜZ METİN taşıyor ve kullanıcının deposunda duruyordu.
    Temizlik ucu zorunlu — yaratan adım kaldırıldıysa silen adım kalmalı,
    yoksa daha önce kurulmuş her projede sır diskte kalır.

    ⚠️ İŞLEM CERRAHİ: dosya silinmez, ürünün kayıtları ÇIKARILIR. İlk yazımda
    ikili bir tasarım vardı ("ya tamamı bizimse sil, ya hiç dokunma") ve denetim
    onu iki yönden birden çürüttü — üçü de canlı doğrulandı:

      · `mcpServers` anahtarı olmayan saf kullanıcı verisi (`{"notlar": ...}`)
        boş kümeye indirgeniyor, alt küme testini geçiyor ve SİLİNİYORDU.
        `{}`, `[]`, `null` de aynı yoldan siliniyordu.
      · Kullanıcının kendi `unityMCP` adlı kaydı ürünün malı sayılıp siliniyordu.
      · Ve asıl ironi: KARIŞIK dosyada (bizim sır + kullanıcının kaydı) dosya
        olduğu gibi bırakılıyordu — yani `X-API-Key` tam da temizliğin var olma
        sebebi olan durumda diskte kalıyordu.

    Cerrahi biçim üçünü birden kapatıyor: yalnız imzası ürüne ait kayıtlar
    çıkarılır, geri kalan her şey (yabancı sunucular, üst düzey kullanıcı
    anahtarları) korunur, ve dosya ancak geriye ürüne ait olmayan HİÇBİR ŞEY
    kalmadığında silinir.

    ⚠️ `.gitignore` girdisine DOKUNULMAZ. `.mcp.json`'ı CLI tabanlı sağlayıcılar
    (`cli_base._write_mcp_config`) hâlâ meşru olarak yazıyor; girdiyi kaldırmak
    kullanıcı Kimi'ye ya da Claude CLI'ına geçtiği an sırrı depoya açardı.
    `remove_gitignore_block` ayrıca ürünün BÜTÜN bloğunu siler — içinde
    `.cursor/mcp.json` ve `opencode.json` girdileri de var; buradan çağrılması
    başka sağlayıcıların sırlarını sessizce yok sayılmaz hâle getirirdi.
    """
    if not workspace_path or not os.path.isdir(workspace_path):
        return
    hedef = os.path.join(workspace_path, ".mcp.json")
    try:
        # Boyut ÖNCE: güvensiz bir projeden gelen dosya belleğe alınmadan
        # sınırlanıyor. Aksi hâlde çok büyük ama geçerli bir JSON, turu daha
        # başlamadan bloke edebilirdi (denetim bulgusu).
        if os.path.getsize(hedef) > _MCP_JSON_AZAMI_BAYT:
            logger.warning(
                "[ClaudeSession] .mcp.json beklenenden çok büyük (%d bayt), dokunulmuyor",
                os.path.getsize(hedef),
            )
            return
        with open(hedef, "r", encoding="utf-8") as f:
            icerik = json.load(f)
    except FileNotFoundError:
        return  # hiç yazılmamış, kullanıcı silmiş ya da bu tur zaten temizlendi
    except (OSError, ValueError) as e:
        # Okunamayan/bozuk dosya BİZİM olduğunu kanıtlayamaz → dokunulmaz.
        logger.warning(f"[ClaudeSession] .mcp.json okunamadı, dokunulmuyor: {e}")
        return

    # Beklenen biçimde değilse hiçbir şey iddia edilemez → dokunulmaz.
    # (`{}`/`[]`/`null` da buraya düşüyor: eskiden bunlar "boş sunucu kümesi"
    # sayılıp dosyayı SİLDİRİYORDU.)
    if not isinstance(icerik, dict) or not isinstance(icerik.get("mcpServers"), dict):
        return

    sunucular = icerik["mcpServers"]
    sir = _urunun_sirri()
    bizimkiler = [ad for ad, tanim in sunucular.items() if _urunun_kaydi_mi(ad, tanim, sir)]
    if not bizimkiler:
        return  # ürüne ait hiçbir şey yok; dosya bütünüyle kullanıcının

    for ad in bizimkiler:
        sunucular.pop(ad, None)

    # Geriye ürünün yazmadığı hiçbir şey kalmadıysa dosyanın kendisi bizimdi.
    if not sunucular and set(icerik.keys()) == {"mcpServers"}:
        try:
            os.remove(hedef)
        except OSError as e:
            logger.warning(f"[ClaudeSession] bayat .mcp.json silinemedi: {e}")
            return
        logger.info("[ClaudeSession] bayat .mcp.json kaldırıldı (unityMCP artık SDK'ya doğrudan geçiliyor)")
        return

    # Karışık dosya: sır çıkarılır, kullanıcının her şeyi yerinde kalır.
    try:
        _mcp_json_geri_yaz(hedef, icerik)
    except OSError as e:
        logger.warning(f"[ClaudeSession] .mcp.json güncellenemedi, sır diskte kalmış olabilir: {e}")
        return
    logger.info(
        "[ClaudeSession] .mcp.json'dan ürünün kayıtları çıkarıldı (%s); "
        "kullanıcının kayıtları korundu",
        ", ".join(sorted(bizimkiler)),
    )


def _mcp_json_geri_yaz(hedef: str, icerik: dict) -> None:
    """Geçici dosya + `os.replace`: yarı yazılmış bir `.mcp.json` bırakmaz.

    İzinler KORUNUYOR: `mkstemp` 0600 veriyor, oysa geriye kalan içerik artık
    kullanıcının kendi yapılandırması. Ürünün, sırrını çıkardığı bir dosyanın
    erişim haklarını sessizce değiştirme hakkı yok.

    ⚠️ Bu yol şu an sembolik bağ/junction'a karşı sertleştirilmiş DEĞİL; aynı
    sınıf K4'ün konusu ve çözümü `local_token_file._dogrula_kimlik`'te ölçülmüş
    hâliyle duruyor (açtıktan SONRA tanıtıcının gerçek yolunu sorar, junction'ı
    ve sabit bağı da yakalar). K4 bu noktayı da o ortak yardımcıya bağlayacak.
    """
    dizin = os.path.dirname(hedef) or "."
    try:
        onceki_kip = os.stat(hedef).st_mode & 0o777
    except OSError:
        onceki_kip = None
    fd, gecici = tempfile.mkstemp(dir=dizin, prefix=".mcp.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(icerik, f, indent=2)
        if onceki_kip is not None:
            os.chmod(gecici, onceki_kip)
        os.replace(gecici, hedef)
    except BaseException:
        try:
            os.unlink(gecici)
        except OSError:
            pass
        raise


class AgentEvent:
    """SSE'ye gönderilecek bir event."""
    def __init__(self, event_type: str, data: dict):
        self.type = event_type  # thinking | tool_call | tool_result | response | error | done
        self.data = data
        self.timestamp = time.time()

    def to_sse(self) -> str:
        payload = json.dumps({"type": self.type, **self.data}, ensure_ascii=False)
        return f"data: {payload}\n\n"


# ── Termination contract (the frontend consumes this) ───────────────────────
#
# `done` payload: {"iterations": int, "stop_reason": "complete"|"max_iterations"
# |"no_progress"|"max_tokens", "max_reached": bool}. Before this contract the ceiling emitted
# a `done` byte-identical to a normal finish apart from a `max_reached` flag
# nothing read, so a truncated run was indistinguishable from a successful one.
#
# The invariant, stated exactly — an audit (30 Aug 2026) caught an earlier
# version of this comment claiming more than the code delivers:
#   * EVERY `done` carries `stop_reason`, the CLI/SDK paths included, so a
#     reader never has to treat a missing field as "probably fine";
#   * a run that FAILS terminates with `error` and no `done` at all. That is the
#     older contract and the UI already reads it, so wrapping a failure in a
#     `done` would put the same interruption on screen twice.
# A turn therefore ends with exactly one of `done` or `error`, never both and
# never neither — `test_terminal_event_is_exactly_one` holds that line.
#
# The two exit texts live here and only here; each of the three loops used to
# carry its own wording, and none of the three said why the run had stopped.
#
# These are NOT streamed as a `response` event any more. The UI renders the stop
# reason as its own localized notice, so streaming the text too showed the same
# warning twice. They ride on `done.stop_message` instead, and the route layer
# persists that into the stored message — a cut-short turn writes ONLY this text
# to the DB (the loops' intermediate output goes out as `text` events, which are
# never persisted), so dropping it would leave an empty turn in the history.
#
# Neither text promises a summary of the partial work: there is none to promise
# while intermediate text stays unpersisted.
# ⚠️ Neither text promises to pick up where the run left off. It cannot: the
# intermediate output of a cut-short turn goes out as `text` events and is never
# persisted, so the next turn's handoff context contains the original request and
# this warning — nothing else. An audit (30 Aug 2026) drove exactly that path and
# found the earlier wording ("kaldığım yerden sürdürmemi istersen") offering a
# continuation the code has no state for. Persisting partial work is a separate
# piece of work; until it exists, the text asks for a restart, not a resume.
_STOP_TEXTS = {
    "max_iterations": (
        "⚠️ Bu istek için ayrılan adım sayısı doldu ve burada durdum. İş yarım "
        "kalmış olabilir; ne kadarının bittiğini yazarsan kaldığı yerden yeni "
        "bir istekle devam edebiliriz."
    ),
    "no_progress": (
        "⚠️ Aynı çağrıyı aynı argümanlarla yapıp aynı cevabı almaya başladım "
        "(5 kez), yani artık yeni bilgi üretmiyordum; boşuna dönmemek için "
        "durdum. Adım sayısı yüzünden DEĞİL — farklı adımlar atsaydım devam "
        "ederdim. Ne yapmamı istediğini biraz daha açarsan farklı bir yol "
        "deneyebilirim."
    ),
    "max_tokens": (
        "⚠️ Cevabım tek yanıt için ayrılan uzunluk sınırına takıldı ve yarıda "
        "kesildi; yukarıdaki metin eksik olabilir. İşi daha küçük parçalara "
        "bölerek yeni bir istekle devam edebiliriz."
    ),
}


# Anthropic agent loop output cap per family. The loop sends no `thinking`
# param, but per the claude-api skill Fable/Mythos, Opus 5, Opus 5.5 and
# Sonnet 5 think by default (omitting `thinking` runs adaptive), and thinking
# tokens count toward max_tokens: at the old 4096 a turn could spend the cap
# thinking and return cut short. 16000 is the skill's non-streaming default
# (all these families allow 128K), and it stays under the SDK's non-streaming
# guard, which raises above ~21.3K (3600 s * max_tokens / 128000 > 600 s).
# Billing is per generated token, so a higher cap costs only when used.
# Families that run without thinking here keep 4096, the whole of which is
# visible output.
ANTHROPIC_LOOP_MAX_TOKENS_THINKING = 16000
ANTHROPIC_LOOP_MAX_TOKENS = 4096


def _anthropic_loop_max_tokens(model_name: str) -> int:
    from providers.effort_caps import claude_version
    m = (model_name or "").lower()
    if "fable" in m or "mythos" in m:
        return ANTHROPIC_LOOP_MAX_TOKENS_THINKING
    version = claude_version(m)
    # Unrecognised ids are treated as newer than the table, like the thinking
    # and effort gates do.
    if version is None or version >= (5, 0):
        return ANTHROPIC_LOOP_MAX_TOKENS_THINKING
    return ANTHROPIC_LOOP_MAX_TOKENS


def provider_retry_code(err_msg: str) -> "str | None":
    """Sağlayıcı hatası yeniden denenmeli mi: `"429"` · `"503"` · `None`.

    TEK kopya olması bilinçli. İki sağlayıcı döngüsü (Gemini, OpenAI-uyumlu)
    aynı sınıflandırmayı ayrı ayrı yazıyordu ve 30 Ağu 2026'da biri düzeltilip
    diğeri unutuldu — bu depoda en sık ödenen bedel tam olarak bu: kapı bir
    dala konuyor, diğeri açık kalıyor.

    Kod araması SINIR İLE: `"429" in err_msg` bir sayının içinde de eşleşiyor,
    yani "token count 4293" diyen alakasız bir hata kota sanılıp üç kez boşuna
    yeniden deneniyor ve sonra kullanıcıya kota diye raporlanıyordu. Aşağıya
    eklenen METİN kalıpları bu sınırı gevşetmez; sayısal eşleşme çıpalı kalır.

    `rate limit reached` OpenAI'ın kendi sözcükleri ("Rate limit reached for
    requests") ve gövdesinde `429` sayısı ya da `too many requests` ifadesi
    geçmiyor — 30 Ağu 2026 denetimi bu hatanın yeniden denenmeden doğrudan
    genel hata olarak kullanıcıya çıktığını ölçtü. Daha geniş olan çıplak
    `rate limit` BİLEREK alınmadı: "you can raise your rate limit" gibi bir
    yönlendirme cümlesi kotayı ANLATIR ama kota hatası DEĞİLDİR, ve bu
    fonksiyonun geçmişteki tek arızası tam olarak fazla geniş eşleşmeydi.
    """
    m = (err_msg or "").lower()
    if (re.search(r"(?<!\d)429(?!\d)", m) or "too many requests" in m
            or "resource_exhausted" in m or "rate limit reached" in m):
        return "429"
    if re.search(r"(?<!\d)503(?!\d)", m) or "service unavailable" in m or "unavailable" in m:
        return "503"
    return None


def _no_progress_text(tool_name: "str | None") -> str:
    """Duruş metnine tekrarlayan aracın adını koy.

    "İlerleme kaydedemedim" bir teşhis değil; "`list_directory` aynı cevabı 5
    kez döndürdü" kullanıcının üzerine hareket edebileceği bir cümle. Araç adı
    bilinmiyorsa genel metne düşülüyor — uydurulmuş bir ad, hiç ad olmamasından
    kötü olurdu.
    """
    if not tool_name:
        return _STOP_TEXTS["no_progress"]
    return (
        f"⚠️ `{tool_name}` aracını aynı argümanlarla 5 kez çağırdım ve her "
        "seferinde aynı cevabı aldım, yani artık yeni bilgi üretmiyordum; "
        "boşuna dönmemek için durdum. Adım sayısı yüzünden DEĞİL — farklı "
        "adımlar atsaydım devam ederdim. Ne yapmamı istediğini biraz daha "
        "açarsan farklı bir yol deneyebilirim."
    )


def _split_data_url(url: str) -> "tuple[str, str]":
    """`data:<mime>;base64,<data>` -> (mime, data).

    The mime was hardcoded to image/jpeg while the only image source was the
    editor screenshot; Unity MCP returns PNG, and Anthropic rejects a media
    type that does not match the bytes.
    """
    head, _, data = url.partition(",")
    mime = head[5:].split(";", 1)[0] if head.startswith("data:") else ""
    return (mime or "image/jpeg"), data


def _gemini_fn_response(fc, name: str, response: dict):
    """Gemini function-response part; carries the call id when present.

    gemini-3.7/3.8 require `call_id` + `name` on every FunctionResponse
    (breaking change, 2026-08-13). Without an id the field is omitted, so
    older models and SDK versions without `id` keep working unchanged.
    """
    kwargs = {"name": name, "response": response}
    call_id = getattr(fc, "id", None)
    if call_id:
        kwargs["id"] = call_id
    return gtypes.Part(function_response=gtypes.FunctionResponse(**kwargs))


def _done_event(iterations: int, stop_reason: str = "complete", **extra) -> "AgentEvent":
    """Builds the `done` event from one place (the contract above).

    `max_reached` is carried for backwards compatibility only: an old consumer
    reading that flag keeps working, a new one reads `stop_reason`.
    """
    return AgentEvent("done", {
        "iterations": iterations,
        "stop_reason": stop_reason,
        "max_reached": stop_reason != "complete",
        **extra,
    })


def _normalize_session_event(etype: str, payload: dict) -> "AgentEvent":
    """Brings CLI/SDK session events in line with the `done` contract.

    It only does work for `done`. Those events are produced under `providers/`
    and were forwarded verbatim, which made them the only paths without a
    `stop_reason`. Adding the field at this single gate rather than in each of
    the five paths also covers a session provider added later.
    """
    if etype != "done":
        return AgentEvent(etype, payload)
    data = dict(payload)
    return _done_event(data.pop("iterations", 1),
                       data.pop("stop_reason", "complete"), **data)


def _stop_events(iterations: int, stop_reason: str,
                 repeated_tool: "str | None" = None) -> "list[AgentEvent]":
    """The events a cut-short run sends to the user.

    One event, not two: the reason is shown by the UI's own notice, so a second
    copy as chat text was the same warning twice. `stop_message` keeps the text
    reaching the layer that persists the turn.
    """
    metin = (_no_progress_text(repeated_tool) if stop_reason == "no_progress"
             else _STOP_TEXTS[stop_reason])
    ekstra = {"repeated_tool": repeated_tool} if repeated_tool else {}
    return [_done_event(iterations, stop_reason, stop_message=metin, **ekstra)]


_TURN_TEARDOWN = (asyncio.CancelledError, GeneratorExit,
                  KeyboardInterrupt, SystemExit)


def _is_turn_teardown(exc: BaseException) -> bool:
    """True when `exc` means the turn was STOPPED, not that it failed.

    A stopped turn owes nobody a terminal event: the consumer that would read
    it is the one that walked away. That exclusion used to be expressed as
    `except Exception`, which is too wide by exactly one shape: structured
    concurrency (TaskGroup, anyio — what the provider SDKs sit on) reports a
    sibling failure as a `BaseExceptionGroup`, so a real provider error
    travelling next to a `CancelledError` was excluded too and the turn ended
    with zero terminals (audit, 30 Aug 2026).

    Hence the rule for groups: teardown only when EVERY leaf is teardown. One
    real failure inside makes the whole group a failure, because that failure
    is what the user needs told.
    """
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(_is_turn_teardown(alt)
                                            for alt in exc.exceptions)
    return isinstance(exc, _TURN_TEARDOWN)


async def _guarantee_terminal(events: "AsyncGenerator[AgentEvent, None]",
                              model_name: str) -> "AsyncGenerator[AgentEvent, None]":
    """Forwards a provider loop's events and GUARANTEES a terminal one.

    The contract this repo keeps failing at: a turn emits exactly one terminal
    event — one `done` carrying `stop_reason`, or one `error`. Never zero.
    Every hole found so far was closed where it was found (`choices=[]`,
    `content.parts=None`, a part without `function_call`) and the next
    unpredicted provider shape opened a new one, because an exception raised
    OUTSIDE the request `try` leaves the generator with no terminal at all.
    This closes the CLASS rather than the case: a loop cannot end silently
    even when something nobody anticipated escapes it.

    Cancellation is deliberately NOT converted into an event — see
    `_is_turn_teardown` for what counts as cancellation and why the test is
    not simply `except Exception`.
    """
    terminal = 0
    try:
        try:
            async for _ev in events:
                if _ev.type in ("done", "error"):
                    terminal += 1
                    if terminal > 1:
                        # DROPPED, not forwarded. The contract is about what the
                        # consumer sees, and a second terminal lets the same turn
                        # be ended or persisted twice downstream — forwarding it
                        # made this wrapper a reporter instead of a guarantee.
                        # Still logged at error level: a loop emitting two
                        # terminals is a defect that must stay visible, never be
                        # quietly normalised.
                        logger.error(f"[provider] {model_name} turu {terminal}. "
                                     f"terminal olayı ({_ev.type}) yaydı — "
                                     f"sözleşme tam olarak bir tane diyor, "
                                     f"fazlası tüketiciye geçirilmedi")
                        continue
                yield _ev
        except BaseException as e:
            if _is_turn_teardown(e):
                raise
            logger.error(f"[provider] {model_name} döngüsü beklenmedik bir hatayla "
                         f"bitti: {e}", exc_info=True)
            if terminal == 0:
                yield AgentEvent("error", {
                    "code": "provider_loop_crashed",
                    "message": (
                        f"`{model_name}` turu beklenmedik bir hatayla kesildi "
                        f"({type(e).__name__}). Tekrar dene ya da başka bir modele geç."
                    )})
            return
        if terminal == 0:
            # The loop returned normally without saying how it ended. Silence is
            # the failure being reported: the UI would sit on a spinner forever.
            yield AgentEvent("error", {
                "code": "provider_no_terminal",
                "message": (
                    f"`{model_name}` turu nasıl bittiğini bildirmeden kapandı. "
                    "Bu backend tarafında bir arıza; turu tekrarla."
                )})
        # No duplicate branch here any more: an extra terminal is reported at
        # the moment it is dropped, above. Reporting it after the fact was the
        # bug — by then both had already reached the consumer.
    finally:
        # Closing the wrapper must close what it wraps, deterministically. The
        # inner loop's `finally` is what hands an abandoned provider request
        # back (see `_await_provider`); leaving that to the garbage collector
        # would make the stop path non-deterministic.
        await events.aclose()


def _gemini_part_view(part) -> "tuple[str, object, bool]":
    """(text, function_call, thought) for one Gemini content part.

    EVERY field of a `types.Part` is optional, and an object that drifted from
    the SDK's shape may not define the attribute at all. The audit (30 Aug
    2026) produced a part carrying only `text`; the plain `p.function_call`
    read raised AttributeError outside the request `try` and the turn ended
    with no terminal event. Absent means "not that kind of part" — never a
    crash.
    """
    return (getattr(part, "text", None) or "",
            getattr(part, "function_call", None),
            bool(getattr(part, "thought", False)))


def _consume_abandoned_request(istek: "asyncio.Task") -> None:
    """Reads the outcome of a provider request whose turn is already gone.

    Without this, a task nobody awaits ends its life with an unretrieved
    exception, which asyncio reports much later, out of context, with no turn
    to attribute it to.
    """
    if istek.cancelled():
        return
    hata = istek.exception()
    if hata is not None:
        logger.warning(f"[provider] sahipsiz kalan sağlayıcı isteği hata ile "
                       f"bitti: {hata!r}")


class _ApprovalDecision(NamedTuple):
    """The gate's answer, plus the sentence the model and the user are told.

    `summary` exists so a refusal, a timeout and an internal failure stop being
    the same sentence: they lead to the same fail-closed outcome, but only one
    of them is a decision the user actually made.
    """

    approved: bool
    summary: str


def _canonical_blob(value: object) -> str:
    """A comparable string for an argument dict or a tool result.

    `sort_keys` is required: providers can hand back the same argument dict in
    a different key order, and without sorting two identical calls would look
    different. `default=str` keeps the signature from blowing up on a
    non-JSON-serializable value — this signature is a hint, not a security
    decision.

    ⚠️ Callers pass the OBJECT, never a string they serialized themselves. An
    audit (30 Aug 2026) found all three loops handing `record` the truncated
    `result_str` they had just built for the transcript, and that one mistake
    broke the guard in both directions at once:
      · the string was cut at 8,000 characters, so two genuinely different
        results sharing a long prefix looked identical and a healthy run was
        stopped as stalled — the user lost work;
      · the string was already serialized, so `sort_keys` had nothing left to
        sort and a repeating result whose key order varied read as progress.
    Over `_STALL_ARG_MAX` the value is HASHED here rather than cut, so length is
    bounded without two different results collapsing into one signature.
    """
    try:
        blob = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        blob = repr(value)
    if len(blob) > _STALL_ARG_MAX:
        blob = hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()
    return blob


def _canonical_call(tool_name: str, tool_args: object, result: object = None) -> str:
    """Signature of one completed tool call: what was asked, and what came back."""
    return f"{tool_name}::{_canonical_blob(tool_args)}=>{_canonical_blob(result)}"


class _ProgressGuard:
    """Declares a stall when the same call keeps returning the same answer.

    Why this and not a step count: a runaway loop shows itself by learning
    nothing new, not by running long. Counting steps cut healthy long runs short
    and let a stuck one spin to the end anyway.

    Two properties the first version lacked, both bought by an audit:
    repetitions are counted over a WINDOW rather than consecutively (an A/B/A/B
    cycle is a stall too), and the RESULT is part of the signature (a poll whose
    answer changes is progress, not repetition).
    """

    def __init__(self):
        self._recent: "deque[str]" = deque(maxlen=_STALL_WINDOW)
        self._stalled = False
        # Hangi aracın tekrarı sigortayı attırdı. Kullanıcıya "ilerleme yok"
        # demek bir teşhis değil; "şu araç aynı cevabı beş kez döndürdü"
        # eyleme çevrilebilir. Ölçüldü 30 Ağu 2026: kart "5 adım", teknik
        # ayrıntı "iterations=1" diyordu ve ikisi de olanı anlatmıyordu.
        self.repeated_tool: str | None = None

    def record(self, tool_name: str, tool_args: object, result: object = None) -> None:
        sig = _canonical_call(tool_name, tool_args, result)
        self._recent.append(sig)
        if self._recent.count(sig) >= _STALL_LIMIT:
            self._stalled = True
            if self.repeated_tool is None:
                self.repeated_tool = tool_name

    @property
    def stalled(self) -> bool:
        return self._stalled


# conversation_id → son tur'u işleyen subscription CLI'ı ('claude'|'codex'|'agy').
# CLI değişince (örn. Claude→Codex) hedef provider'ın bayat session'ı kapatılır →
# bir sonraki turda tam transcript yeniden enjekte edilir (kaldığı yerden devam).
_LAST_SUB_PROVIDER: Dict[int, str] = {}

# Yeni CLI'a geçmiş enjekte edilirken kullanılan başlık (context = tam transcript).
# Guardrail cümlesi ÖZELLİKLE agy için önemli: agy --dangerously-skip-permissions ile
# tam otonom çalışıyor ve sistem prompt append kanalı yok (Claude'daki _APP_SYSTEM_APPEND
# muadili yok). Guardrail olmadan, "kaldığımız yeri biliyor musun?" gibi belirsiz/meta
# sorularda agy bunu bir araştırma görevi sanıp dosya/web taramaya sapabiliyor (canlı
# gözlendi: alakasız bir CLI-flag konusunu araştırıp kafası karışmış "Clarification
# Required" ile bitirdi). Codex/Claude aynı bağlamla doğru yanıt verdiği için context
# içeriği sorunlu değildi — eksik olan yalnız "doğrudan bundan cevapla" talimatıydı.
_HANDOFF_HEADER = (
    "[ÖNCEKİ KONUŞMA BAĞLAMI — bu geçmiş sana YETERLİ bağlamı veriyor. Kullanıcı "
    "'kaldığımız yeri/ne yaptığımızı biliyor musun' tarzı bir şey soruyorsa, dosya "
    "okuma/tarama/web araması YAPMADAN doğrudan bu geçmişten özetleyerek yanıtla.]"
)

_CODEX_AUTO_MODE_INSTRUCTION = (
    "[ÇALIŞMA MODU: OTOMATİK] Kullanıcının verdiği görevi tamamlamak için gerekli "
    "dosya değişikliklerini, komutları ve MCP araçlarını doğrudan uygula. "
    "\"Yapayım mı?\", \"devam edeyim mi?\" veya benzeri izin/onay soruları sorma. "
    "Gerçekten eksik ve sonucu değiştirecek zorunlu bilgi yoksa en iyi teknik "
    "kararını vererek otonom devam et."
)


class AgentRunner:
    """
    Tek bir kullanıcı isteğini agentic loop ile çalıştırır.
    Gemini'nin native function calling özelliğini kullanır.
    """

    def __init__(
        self,
        provider_type: str,
        api_key: str,
        model_name: str,
        workspace_path: str,
        language: str = "tr",
        context: str = "",
        thinking_level: str = "medium",
        conversation_id: Optional[int] = None,
        images: Optional[List[str]] = None,
        generation_mode: str = "auto",
        effort_level: str = "medium",
        ultracode: bool = False,
        videos: Optional[List[dict]] = None,
        resume_id: Optional[str] = None,
    ):
        # CLI'ın kendi diskindeki oturumu geri çağıran kimlik. Route yükleyip
        # veriyor (DB orada); burada yalnız taşınıyor. None ise davranış eski:
        # DB transcript'i enjekte edilir.
        self.resume_id = resume_id
        self.provider_type = provider_type
        self.api_key = api_key
        self.model_name = model_name
        self.workspace_path = workspace_path
        self.language = language
        self.context = context
        self.thinking_level = thinking_level
        self.conversation_id = conversation_id
        self.images = images
        self.generation_mode = generation_mode  # auto | plan | step
        # Claude-only (subscription + claude-* model): effort_level → --effort,
        # ultracode → mesaja keyword enjeksiyonu. Diğer sağlayıcılarda yok sayılır.
        self.effort_level = effort_level
        self.ultracode = ultracode
        self.videos = videos  # [{"kind":"path"|"url", ...}] → _prepare_videos ile kareye çevrilir
        self.use_thinking = thinking_level != "off"

    def _get_architect_wisdom(self) -> str:
        """
        Proje kök dizininde ARCHITECT.md veya .claude.md varsa okur.
        Bu dosya proje kurallarını (naming convention, patterns vb.) içerir.
        """
        wisdom_paths = ["ARCHITECT.md", ".claude.md", "CLAUDE.md"]
        for p in wisdom_paths:
            full_path = os.path.join(self.workspace_path, p)
            if os.path.exists(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        return f"\n\n[PROJE KURALLARI (ARCHITECT.md)]\n{content}\n"
                except Exception as e:
                    logger.warning(f"Wisdom dosyası okunamadı ({p}): {e}")
        return ""

    async def _reset_session_for(self, provider: str) -> None:
        """CLI değişiminde hedef provider'ın bu sohbete ait canlı session'ını kapatır.
        Böylece bir sonraki tur 'ilk tur' sayılır ve tam transcript (self.context)
        yeniden enjekte edilir → yeni CLI aradaki turları da görüp kaldığı yerden devam eder."""
        if self.conversation_id is None:
            return
        try:
            if provider in ("cursor", "copilot", "opencode", "kimi"):
                from providers.oneshot_cli import close_session as _close_oneshot
                await _close_oneshot(provider, self.conversation_id)
                logger.info(f"[handoff] {provider} session resetlendi (CLI değişimi)")
                return
            if provider == "codex":
                from providers.codex_session import close_session
            elif provider == "agy":
                from providers.agy_session import close_session
            else:
                from providers.claude_sdk_session import close_session
            await close_session(self.conversation_id)
            logger.info(f"[handoff] {provider} session resetlendi (CLI değişimi) → tam transcript yeniden enjekte edilecek")
        except Exception as e:
            logger.warning(f"[handoff] {provider} session reset hatası: {e}")

    def _approval_prompt(self, tool_name: str, tool_args: dict) -> str | None:
        """Onay kartı gerekiyorsa kartta gösterilecek metni, gerekmiyorsa None döner.

        2026-07-27 denetiminde ölçülen asimetri: `rm dosya` onay kartı çıkarıyor
        ama aynı dosyayı silen `delete_file` çıkarmıyordu — model, kapıyı geçmek
        yerine kapısı olmayan aracı seçebiliyordu. İki araç aynı geri alınamaz
        etkiyi üretiyorsa aynı kapıdan geçmeliler.

        `write_file` BİLEREK kapsam dışı: kod yazmak ürünün asıl işi ve her
        yazımda onay istemek kullanıcıyı refleks-onaya alıştırır — bu kapıyı
        güçlendirmez, tamamen değersizleştirir. Silme ise seyrek ve geri alınamaz.
        Yazma zaten `_validate_path` ile workspace'e hapsedilmiş durumda.

        Global auto mode shows no card on any path (owner decision, 25 Sep 2026).
        Read live rather than from `self.generation_mode` so a flip to step
        bites on the very next call of a running turn.
        """
        from agentic import approval_mode
        if approval_mode.is_auto():
            return None
        if tool_name == "run_command":
            command = tool_args.get("command", "")
            return command if _is_dangerous_command(command, self.workspace_path) else None
        if tool_name == "delete_file":
            return f"delete_file {tool_args.get('file_path', '?')}"
        return None

    @contextlib.contextmanager
    def _approval_gate(self, command_text: str):
        """Owns one approval gate for as long as the caller is inside the block.

        Why a context manager rather than the open/await pair this replaced:
        cleanup used to live in `_await_approval`'s `finally`, and the caller
        has to YIELD the approval event before waiting — the answer arrives over
        a separate HTTP route and cannot come before the client has seen the
        card. A client that closed the stream while the generator was suspended
        on that yield therefore never reached the wait, and both registry
        entries stayed behind; a late answer could then flip an abandoned gate to
        approved with nobody left to consume it. Wrapping the yield makes the
        generator's own unwind run the cleanup.

        The id is drawn until it is unused: the previous 10-hex-character id was
        written into both registries without checking, so two concurrent turns
        that drew the same value shared one slot and one turn could be resumed
        by the other's answer.

        `_APPROVAL_RESULTS` is seeded False; of the three copies this replaced
        only the Gemini one did that, the other two relied on a missing key.
        """
        # Bounded on purpose: a `while taken: redraw` loop never ends if the id
        # source keeps returning the same value, which is exactly what a test
        # that pins `uuid4` does — a guard against collisions must not become a
        # hang of its own.
        gate_id = uuid.uuid4().hex
        if gate_id in _APPROVAL_GATES:
            for _suffix in range(1, 1000):
                _candidate = f"{gate_id}-{_suffix}"
                if _candidate not in _APPROVAL_GATES:
                    gate_id = _candidate
                    break
        _register_gate(gate_id, self.conversation_id)
        try:
            yield (AgentEvent("command_approval_needed",
                              {"command": command_text, "gate_id": gate_id}), gate_id)
        finally:
            # RESULTS'ı da düşür: yazan taraf conversation_routes, temizleyen
            # yoktu → gate_id başına kalıcı girdi birikiyordu.
            _release_gate(gate_id)

    async def _await_approval(self, gate_id: str) -> "_ApprovalDecision":
        """Waits for the user's answer. Anything other than an explicit yes is a no.

        Fail-closed, but it does not invent a decision the user never made: a
        timeout and an internal failure each carry their own summary. The three
        copies this replaced all told the model "user rejected" whatever
        happened, so a waiter that crashed looked exactly like a refusal — and
        the model then reasoned on a rejection that never occurred.

        `except Exception` is deliberately narrow: two of the copies used a bare
        `except:`, which also swallowed the CancelledError raised when the user
        presses Stop and let the run continue.
        """
        event = _APPROVAL_GATES.get(gate_id)
        if event is None:
            logger.warning("Onay kapısı kaydı bulunamadı: %s", gate_id)
            return _ApprovalDecision(False, "❌ Onay kaydı bulunamadı, araç çalıştırılmadı.")
        try:
            await asyncio.wait_for(event.wait(), timeout=APPROVAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            return _ApprovalDecision(False, "❌ Onay süresi doldu, araç çalıştırılmadı.")
        except Exception:
            logger.exception("Onay beklenirken hata")
            return _ApprovalDecision(False, "❌ Onay alınamadı (iç hata), araç çalıştırılmadı.")
        if bool(_APPROVAL_RESULTS.get(gate_id, False)):
            return _ApprovalDecision(True, "")
        return _ApprovalDecision(False, "❌ Kullanıcı tarafından reddedildi.")

    async def _execute_tool_with_approval(
        self, tool_name: str, tool_args: dict
    ) -> tuple[dict, list]:
        """
        Tool'u çalıştırır ve (result_dict, extra_events) döndürür.

        NOT: Tehlikeli run_command onayı çağıran API yollarında (_run_gemini /
        _run_anthropic / _run_openai) ZATEN inline yapılıyor (onaylanmazsa orada
        'continue' edilir, buraya hiç gelmez). Eskiden burada İKİNCİ kez sorulması
        onaylanan komutlarda çift-onaya yol açıyordu; o yüzden bu metot artık
        yalnızca aracı çalıştırır. İmza (tuple) geriye-uyum için korunur
        (çağrılar 'result, _ = ...' biçiminde).
        """
        result = await asyncio.to_thread(
            execute_tool, tool_name, tool_args, self.workspace_path, self.conversation_id
        )
        return result, []

    def _identity_note(self) -> str:
        """Modelin kendini DOĞRU tanıması için system prompt'a eklenir. LLM'ler hangi model
        olduklarını güvenilir bilmez (ör. GLM 'ben Claude'um' diyebilir). Sadece API
        sağlayıcılarında; CLI/abonelik ve ollama'da kimlik zaten net."""
        if self.provider_type in ("subscription", "ollama"):
            return ""
        return (f"\n\n[MODEL KİMLİĞİN] Sen '{self.model_name}' modelisin ve "
                f"'{self.provider_type}' sağlayıcısı üzerinden çalışıyorsun. Kimliğin veya hangi "
                f"model olduğun sorulursa BUNU söyle; farklı bir model (Claude/GPT/Gemini vb.) "
                f"olduğunu İDDİA ETME.")

    async def _await_provider(
        self, istek: "asyncio.Task", *, iptal_edilebilir: bool = True
    ) -> AsyncGenerator[AgentEvent, None]:
        """Sağlayıcı isteği beklenirken kalp atışı yayar; tur ölürse isteği iptal eder.

        ÜÇ döngünün de buradan geçmesi kasıtlı. Kalp atışı ilk yazımında yalnız
        OpenAI-uyumlu isteğin etrafına konmuştu; Gemini bloklayan SDK çağrısını
        `asyncio.to_thread` ile, Anthropic `messages.create`'i doğrudan bekliyor
        ve ikisi de sessiz kalıyordu (denetim, 30 Ağu 2026). Bu deponun adı
        konmuş yinelenen arızası tam olarak bu: kapı bir dala konuyor, diğerleri
        açık kalıyor — o yüzden bekleme mantığı üç yerde değil, burada.

        `finally` iptali ikinci bir bulgunun cevabı: tüketici generator'ı
        kapattığında (kullanıcı "Durdur"a bastığında oluyor) isteği kimse
        iptal etmiyordu ve sağlayıcı çağrısı sahipsiz olarak 300 sn'lik zaman
        aşımına kadar koşmaya devam ediyordu. Art arda durdurulan turlar bu
        istekleri biriktiriyordu.

        `iptal_edilebilir=False` Gemini için: iş `asyncio.to_thread` ile havuz
        thread'inde ve `Task.cancel()` o thread'e ULAŞMIYOR — iptal etmek
        bloklayan çağrıyı durdurmaz, yalnız sonucunu hiç kimsenin okumadığı bir
        `CancelledError`'a çevirir. İptal etmiyoruz ki thread'in istisnası
        sessizce yutulmuş olmasın; beklemenin sesli olması yine de kazanılıyor.
        Bunun bedeli o dalda ayrıca ödeniyor: iptal edilemeyen istek sahipsiz
        kalmasın diye sonucunu okuyan bir done-callback bağlanıyor (aşağıya bak).
        """
        gecen = 0.0
        try:
            while True:
                biten, _ = await asyncio.wait({istek}, timeout=_KALP_ATISI_SN)
                if biten:
                    return
                gecen += _KALP_ATISI_SN
                yield AgentEvent("status", {"detail": (
                    f"⏳ {self.model_name} {gecen:g} sn'dir yanıt vermedi — sağlayıcı "
                    f"hâlâ işliyor (Durdur ile iptal edebilirsin)"
                )})
        finally:
            # `done()` ise DOKUNULMAZ: biten bir isteğin sonucunu (ya da
            # istisnasını) çağıran `result()` ile okuyor, burada beklemek onu
            # yutardı. Yalnız gerçekten askıda kalmış bir istek iptal edilir.
            if not istek.done():
                if iptal_edilebilir:
                    istek.cancel()
                    try:
                        await istek
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        # Tur artık yok; bu istisnayı anlatacak bir kullanıcı da yok.
                        # Yine de kayda geçiyor — sessizce yutulan hata bu depoda
                        # ayrı bir bulgu sınıfı.
                        logger.debug("[provider] iptal edilen istek hata ile bitti",
                                     exc_info=True)
                else:
                    # Gemini: the blocking SDK call sits in a pool thread and
                    # NOTHING here can stop it — `Task.cancel()` does not reach
                    # a thread, and `to_thread` has no interruption point. That
                    # stays impossible, so the task legitimately outlives the
                    # turn; what is fixable is OWNERSHIP. Without this callback
                    # the abandoned task's outcome is never retrieved, so a
                    # provider failure after a stopped turn is either lost or
                    # resurfaces later as an unattributable asyncio warning.
                    # The callback fires whenever the thread finishes, costs
                    # nothing while it runs, and keeps a reference to the task
                    # alive so the loop cannot drop it half-finished.
                    istek.add_done_callback(_consume_abandoned_request)

    async def _prepare_videos(self, user_message: str) -> "tuple[str, list[dict]]":
        """Videoları (yerel dosya + mesaja YAPIŞTIRILAN URL) kare data-URI'leri + transkripte
        çevirip mevcut görsel hattına enjekte eder. Kareler self.images'a katılır (sağlayıcı
        yolları DEĞİŞMEZ), transkript+bağlam bloğu user_message'ın başına eklenir. URL'ler
        ayrı UI'dan değil doğrudan mesaj metninden otomatik yakalanır.

        Returns (message, warnings). Failure is still SOFT — a video error must
        not kill the chat — but no longer SILENT: the second item is a list of
        dicts to be emitted as `warning` events. Before this, every exception
        collapsed into one text string and NO event reached the user, so a
        missing program and a dead link looked identical on screen.
        """
        from providers import video_extract
        videos = list(self.videos or [])
        for _u in video_extract.detect_video_urls(user_message):
            videos.append({"kind": "url", "url": _u})
        if not videos:
            return user_message, []
        blocks, all_uris, warnings = [], [], []
        for src in videos:
            # One stop switch per video. `asyncio.to_thread` hands the work to a
            # pool thread, and cancelling the AWAIT does not reach that thread:
            # the audit (30 Aug 2026) measured a stopped chat leaving yt-dlp
            # downloading and its temp directory live until the 600-second
            # subprocess timeout, so repeated cancelled turns kept consuming
            # executor threads, bandwidth and workspace disk.
            cancel = video_extract.ExtractionCancel()
            try:
                res = await asyncio.to_thread(
                    video_extract.extract, src, self.workspace_path,
                    f"vid_conv{self.conversation_id}", cancel)
                all_uris.extend(res.frame_data_uris)
                blocks.append(video_extract.build_video_block(res.meta, res.transcript))
            except asyncio.CancelledError:
                # Kills the live ffmpeg/yt-dlp child and drops the per-video temp
                # directory BEFORE the cancellation continues upward — leaving an
                # orphaned subprocess behind would only move the leak. Cancelling
                # the turn is not a video failure, so no warning is emitted.
                cancel.cancel()
                raise
            # The raw detail stays in the log on both branches. What leaves the
            # backend — to the renderer, to the stored history, and into the
            # model's own prompt — is the stage and the error kind: a subprocess
            # failure's text is its whole argv, carrying the home directory, the
            # workspace path and the URL with its query string.
            except video_extract.VideoPipelineError as e:
                logger.warning(f"[video] aşama={e.stage} kod={e.code}: {e.detail}")
                warnings.append({"code": e.code, "message": e.message,
                                 "detail": e.client_detail})
                blocks.append(f"\n\n[VİDEO] Bir video işlenemedi ({e.message}). Metinle devam et.\n")
            except Exception as e:
                # An unclassified error must be visible too, otherwise the silence
                # comes back through exactly the hole we just closed.
                logger.warning(f"[video] sınıflandırılmamış çıkarım hatası: {e}", exc_info=True)
                _msg = "Video işlenemedi; video atlandı, sohbet metinle sürüyor."
                warnings.append({
                    "code": "video_extract_failed",
                    "message": _msg,
                    "detail": f"aşama: bilinmiyor · {type(e).__name__}",
                })
                blocks.append(f"\n\n[VİDEO] Bir video işlenemedi ({_msg}). Metinle devam et.\n")
        if all_uris:
            self.images = (self.images or []) + all_uris
        message = ("".join(blocks) + "\n" + user_message) if blocks else user_message
        return message, warnings

    async def run(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        """Turun MODUNU kaydeder, sonra asıl döngüyü çalıştırır.

        Neden ince bir sarmalayıcı: unityMCP onay kapısı (K1) ayrı bir süreçte
        ve tur anahtarını taşıyamıyor, dolayısıyla "şu an Auto turu koşuyor mu"
        sorusuna backend'in cevap verebilmesi gerekiyor. Kayıt tek bir yerde
        duruyor çünkü sağlayıcı başına kaydetmek (bugün yalnız OpenCode'da
        olduğu gibi) diğer sekiz yolu sessizce dışarıda bırakırdı.

        `with` bilerek: `run` bir async generator ve tüketici onu erken
        kapatabiliyor (kullanıcı "Durdur"a bastığında oluyor). `try/finally`
        yerine bağlam yöneticisi, istisna ve generator kapanışının ikisinde de
        sayacı düşürüyor — açık kalan bir sayaç, tur bittikten sonra da
        oto-onay veren bir pencere demekti.
        """
        from agentic.approval_policy import ambient_turn
        with ambient_turn(self.workspace_path or ".",
                          getattr(self, "generation_mode", "auto"),
                          getattr(self, "conversation_id", None)):
            async for _event in self._run_inner(user_message):
                yield _event

    async def _run_inner(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        """
        Agentic loop'u çalıştırır. Her adımda AgentEvent yield eder.
        """
        user_message, _video_warnings = await self._prepare_videos(user_message)
        # Video warnings are emitted BEFORE the provider branch: `_prepare_videos`
        # runs for every provider, so emitting here closes all nine paths at once —
        # putting it inside the branches would again close only one.
        for _w in _video_warnings:
            yield AgentEvent("warning", _w)
        if self.provider_type == "google":
            async for event in self._run_gemini(user_message):
                yield event
        elif self.provider_type == "anthropic":
            async for event in self._run_anthropic(user_message):
                yield event
        elif self.provider_type in ("openai", "openrouter", "deepseek", "groq", "moonshot", "z-ai", "nvidia"):
            async for event in self._run_openai(user_message):
                yield event
        elif self.provider_type == "subscription":
            # claude-* → kalıcı interaktif SDK session (native onay + AskUserQuestion + skill/slash).
            # codex (gpt-*) → kalıcı app-server session (native onay). agy → disk-resume CLI.
            # cursor/copilot/opencode → one-shot + resmi resume; Kimi → transcript'li one-shot.
            _name = (self.model_name or "claude").lower()
            if _name.startswith("cursor-"):
                _cur = "cursor"
            elif _name.startswith("copilot-"):
                _cur = "copilot"
            elif _name.startswith("opencode:"):
                _cur = "opencode"
            elif _name.startswith("kimi-"):
                _cur = "kimi"
            elif _name.startswith("gpt-"):
                _cur = "codex"
            elif _name.startswith(("gemini", "agy-")):
                _cur = "agy"
            else:
                _cur = "claude"

            # CLI'lar arası "kaldığı yerden devam": provider değiştiyse hedef CLI'ın
            # (varsa) bayat session'ını kapat → ilk-tur enjeksiyonu tetiklenir, tam
            # transcript (self.context) yeniden verilir → aradaki turları da görür.
            if self.conversation_id is not None:
                _prev = _LAST_SUB_PROVIDER.get(self.conversation_id)
                if _prev and _prev != _cur:
                    await self._reset_session_for(_cur)
                _LAST_SUB_PROVIDER[self.conversation_id] = _cur

            if _cur == "codex":
                async for event in self._run_codex_session(user_message):
                    yield event
            elif _cur == "agy":
                async for event in self._run_agy_session(user_message):
                    yield event
            elif _cur in ("cursor", "copilot", "opencode", "kimi"):
                async for event in self._run_oneshot_cli_session(user_message, _cur):
                    yield event
            else:
                async for event in self._run_claude_session(user_message):
                    yield event
        else:
            # Diğer provider'lar için basit fallback (function calling yok)
            async for event in self._run_simple(user_message):
                yield event

    # ═══════════════════════════════════════════════
    # GEMINI AGENTIC LOOP (Native Function Calling)
    # ═══════════════════════════════════════════════
    async def _run_gemini(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        async for _ev in _guarantee_terminal(self._gemini_loop(user_message),
                                             self.model_name):
            yield _ev

    async def _gemini_loop(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        client = genai.Client(api_key=self.api_key)

        # Tool tanımlarını Gemini formatına çevir
        # Built-in + Unity MCP araçları (şema Gemini için sanitize edilir → 40+ Unity tool da gelir).
        _gemini_decls = get_gemini_tool_declarations()[0]["function_declarations"]
        tools = [gtypes.Tool(function_declarations=[
            gtypes.FunctionDeclaration(
                name=d["name"],
                description=d["description"],
                parameters=d["parameters"],
            )
            for d in _gemini_decls
        ])]

        system_instruction = f"""{SYSTEM_PROMPT}

Sen Unity projesi üzerinde çalışan bir AI asistanısın. Sana verilen araçları kullanarak projeyi keşfedebilir, dosyaları okuyabilir, arama yapabilir ve kod yazabilirsin.

[ÇALIŞMA PRENSİBİ - HAYATİ KURALLAR]
1. ÖNCE projeyi keşfet (dosya oku, ara).
2. KOD YAZARKEN: KESİNLİKLE parça kod (snippet) verme. Sadece değişen yeri değil, dosyanın TAMAMINI yazmak ZORUNDASIN.
3. KOD BLOKLARI: Her kod bloğunun İSTİSNASIZ İLK SATIRINA yolu ekle:
// path: Assets/Scripts/Tam/Dosya/Yolu.cs

4. `write_file` aracını C# kodu yazmak için KULLANMA. Bunun yerine, kodu Markdown kod bloğu (```csharp ... ```) içinde ver. Kullanıcı arayüzden onaylayacaktır.
5. Kullanıcının en son verdiği teknik talimatları (örn: Update/Timer/Zırh) asla unutma, her güncellemede bunları koru.
6. Eğer tam dosyayı yazmazsan sistem çalışmaz ve kullanıcıya hatalı bilgi vermiş olursun.

[BAĞLAM]
{self.context or "Yeni sohbet."}

[DİL]
Kullanıcıyla {'Türkçe' if self.language == 'tr' else 'İngilizce'} konuş."""

        # Thinking config — kayıtçıdan (effort_caps): gemini-3.x → thinking_level (enum),
        # gemini-2.5 → thinking_budget (token). İkisi birlikte ASLA gönderilmez (3.x'te 400).
        # auto → None → modelin kendi varsayılanı.
        from providers.effort_caps import map_effort as _map_effort
        _eff = _map_effort("google", self.model_name,
                           self.effort_level or self.thinking_level or "auto")
        thinking_config = None
        try:
            if "gemini_thinking_level" in _eff:
                thinking_config = gtypes.ThinkingConfig(thinking_level=_eff["gemini_thinking_level"])
            elif "gemini_thinking_budget" in _eff:
                thinking_config = gtypes.ThinkingConfig(thinking_budget=_eff["gemini_thinking_budget"])
        except TypeError:
            # Eski google-genai SDK thinking_level bilmiyorsa güvenli bütçeye düş
            thinking_config = gtypes.ThinkingConfig(thinking_budget=4096)

        config = gtypes.GenerateContentConfig(
            system_instruction=system_instruction + self._identity_note(),
            tools=tools,
            thinking_config=thinking_config,
        )

        # İlk mesaj
        parts = [gtypes.Part(text=user_message)]
        if self.images:
            for img_data in self.images:
                try:
                    # data:image/png;base64,.... formatını ayıkla
                    if "," in img_data:
                        header, base64_str = img_data.split(",", 1)
                        mime_type = header.split(":")[1].split(";")[0]
                        parts.append(gtypes.Part(inline_data=gtypes.Blob(mime_type=mime_type, data=base64_str)))
                    else:
                        # Fallback
                        parts.append(gtypes.Part(inline_data=gtypes.Blob(mime_type="image/jpeg", data=img_data)))
                except Exception as e:
                    logger.error(f"Gemini image parsing hatası: {e}")

        contents = [gtypes.Content(role="user", parts=parts)]

        _turn_t0 = time.time()   # footer: tur süresi + token
        _turn_in = _turn_out = 0
        _progress = _ProgressGuard()
        for iteration in range(MAX_ITERATIONS):
            logger.info(f"  🔄 Agentic Loop iterasyon {iteration + 1}")
            
            # Rate limit (15 RPM) için güvenli mola (her 4s bir hak doluyor, 5s garantidir)
            if iteration > 0:
                await asyncio.sleep(5.0)

            # Retry mekanizması (429 hataları için daha agresif)
            response = None
            _son_hata = ""          # son denemenin sebebi — tükenince kullanıcıya bu gidiyor
            for retry in range(3):
                try:
                    # Bekleme SESSİZ olmamalı — kalp atışı üç sağlayıcı yolunda
                    # da aynı yardımcıdan geliyor (bkz. `_await_provider`).
                    _istek = asyncio.create_task(asyncio.to_thread(
                        client.models.generate_content,
                        model=self.model_name,
                        contents=contents,
                        config=config,
                    ))
                    async for _kalp in self._await_provider(_istek,
                                                            iptal_edilebilir=False):
                        yield _kalp
                    response = _istek.result()
                    break
                except Exception as e:
                    err_msg = str(e).lower()
                    # 429 (Hız Sınırı) / 503 (Servis Kesintisi) → bekle ve tekrar dene.
                    #
                    # Kod araması SINIR İLE: eski hâli `"429" in err_msg` idi ve
                    # içinde 4293 gibi bir sayı geçen HERHANGİ bir hatayı kota
                    # sanıp üç kez yeniden deniyordu — sonra da onu kullanıcıya
                    # "kota" diye raporluyordu. Yanlış sınıflandırma, yanlış
                    # teşhis ve 60 saniye boşa bekleme, hepsi tek `in` yüzünden.
                    _kod = provider_retry_code(err_msg)
                    if _kod:
                        _son_hata = _kod
                        wait_time = (retry + 1) * 10
                        logger.warning(f"  ⚠️ Google API Hatası ({_kod}). {wait_time}s bekleniyor... (Deneme {retry+1}/3)")
                        # Bekleme SESSİZ olmamalı: üç deneme 60 saniye sürüyor ve
                        # o süre boyunca arayüze tek olay gitmiyordu — kullanıcı
                        # donmuş bir uygulama görüyor. Sebebi söyleyip bekliyoruz.
                        yield AgentEvent("status", {"detail": (
                            f"🚦 Google {_kod} döndü ({'kota/hız sınırı' if _kod == '429' else 'servis meşgul'}) — "
                            f"{wait_time} sn bekleniyor, deneme {retry + 1}/3"
                        )})
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        # 400 / diğer hatalar — tam hata mesajını logla
                        logger.error(f"  ❌ Gemini API hatası [{self.model_name}]: {str(e)}", exc_info=True)
                        # "Function calling is not enabled" seçilen modelin araç
                        # çağıramadığı anlamına geliyor ve bu bir YAPILANDIRMA
                        # sorunu, geçici bir arıza değil. Ham JSON'u kullanıcının
                        # yüzüne basmak onu hata metnini çözmeye zorluyordu;
                        # yapılacak şey tek: araç çağırabilen bir model seç.
                        if "function calling is not enabled" in err_msg:
                            # `code` + hazır `message`: arayüz kodu tanıyorsa
                            # kendi dilinde yazar, tanımıyorsa bu metni gösterir.
                            # Sadece kod göndermek eski arayüzü boş bırakırdı,
                            # sadece metin göndermek İngilizce arayüzde Türkçe
                            # cümle bırakıyordu — ikisi birden ikisini de kapatıyor.
                            yield AgentEvent("error", {
                                "code": "model_no_tools",
                                "model": self.model_name,
                                "message": (
                                    f"`{self.model_name}` modeli araç çağırmayı desteklemiyor, "
                                    "bu yüzden Unity/dosya araçlarını kullanamıyor. Araç gerektiren "
                                    "işler için araç çağırabilen bir model seç."
                                )})
                            return
                        yield AgentEvent("error", {"message": f"AI hatası: {str(e)}"})
                        return

            if not response:
                # Eski metin "AI yanıt vermeyi reddetti (Rate Limit)." idi ve iki
                # şeyi birden yanlış söylüyordu: reddeden model değil sağlayıcı,
                # ve 503 (servis kesintisi) de aynı cümleyle "rate limit" diye
                # raporlanıyordu. Hangi kodun geldiği loglarda vardı, kullanıcıda
                # yoktu — yani teşhis için gereken tek bilgi atılıyordu.
                _kodlar = {"429": "provider_quota", "503": "provider_unavailable"}
                _aciklama = {
                    "429": "Google kota/hız sınırı nedeniyle üç denemede de isteği reddetti. "
                           "Bir süre bekleyip tekrar dene ya da başka bir modele geç.",
                    "503": "Google servisi üç denemede de meşgul döndü (503). Bu geçici; "
                           "birazdan tekrar dene.",
                }.get(_son_hata, "Sağlayıcıya üç denemede de ulaşılamadı.")
                yield AgentEvent("error", {
                    "code": _kodlar.get(_son_hata, "provider_unreachable"),
                    "message": _aciklama,
                })
                return

            if not response.candidates:
                yield AgentEvent("error", {"message": "AI yanıt üretemedi."})
                return

            _um = getattr(response, "usage_metadata", None)
            if _um:
                _turn_in += getattr(_um, "prompt_token_count", 0) or 0
                _turn_out += getattr(_um, "candidates_token_count", 0) or 0

            candidate = response.candidates[0]
            # `candidates` boşluğu YUKARIDA zaten kapalı; kapalı olmayan `parts`
            # idi. Google bir adayı içeriksiz (`content=None`) ya da parçasız
            # döndürebiliyor ve `for part in None` bir `TypeError`'ı generator'ın
            # dışına taşırdı — OpenAI yolundaki `choices[0]` ile aynı sınıf,
            # terminalsiz biten tur. Boş listeye düşmek turu doğal yoluna
            # sokuyor: araç çağrısı yok → boş final yanıt → tek `done`.
            parts = getattr(candidate.content, "parts", None) or []

            # Her parça TEK yerde şekline çevriliyor (`_gemini_part_view`): alan
            # yokluğu bir arıza değil, "bu parça o türden değil" demek.
            _views = [(p, *_gemini_part_view(p)) for p in parts]

            # Thinking varsa yield et
            for _p, _text, _fc, _thought in _views:
                if _thought and _text:
                    yield AgentEvent("thinking", {"text": _text})

            # Tool call var mı kontrol et. İsimsiz bir `function_call`
            # çağrılabilir değil — araç sayılmıyor, böylece tur araçsız yolundan
            # doğal olarak tek `done` ile kapanıyor.
            tool_calls = [(p, fc) for p, _t, fc, _th in _views
                          if fc is not None and getattr(fc, "name", None)]
            text_parts = [t for _p, t, _fc, th in _views if t and not th]

            if not tool_calls:
                # Tool call yok = AI işini bitirdi, final yanıt
                final_text = "\n".join(text_parts)
                yield AgentEvent("turn_usage", {
                    "input_tokens": _turn_in, "output_tokens": _turn_out, "cost_usd": None,
                    "duration_ms": int((time.time() - _turn_t0) * 1000),
                })
                yield AgentEvent("response", {"content": final_text})
                yield _done_event(iteration + 1)
                return

            # Tool call'ları çalıştır
            function_response_parts = []
            screenshot_parts = []  # Gemini: görsel tool-role Content'e KONMAZ (400) → ayrı user-content

            for part, fc in tool_calls:
                tool_name = fc.name
                _args = getattr(fc, "args", None)
                tool_args = dict(_args) if _args else {}

                yield AgentEvent("tool_call", {
                    "tool": tool_name,
                    "arguments": tool_args,
                    "iteration": iteration + 1,
                })

                # Tehlikeli komut kontrolü ve onay yield'ı
                _approval_text = self._approval_prompt(tool_name, tool_args)
                if _approval_text:
                    with self._approval_gate(_approval_text) as (_gate_event, _gate_id):
                        yield _gate_event
                        _decision = await self._await_approval(_gate_id)
                    if not _decision.approved:
                        result = {"success": False, "summary": _decision.summary}
                        # Tool result olarak ilet
                        yield AgentEvent("tool_result", {
                            "tool": tool_name,
                            "success": False,
                            "summary": _decision.summary,
                        })
                        # AI'a tool sonucunu bildir (döngü devam etsin diye)
                        function_response_parts.append(
                            _gemini_fn_response(fc, tool_name, result)
                        )
                        # Re-asking for a call the user keeps refusing is a stall
                        # like any other, so it is recorded rather than skipped.
                        _progress.record(tool_name, tool_args, result)
                        if _progress.stalled:
                            break
                        continue

                # Normal tool execution
                result, _ = await self._execute_tool_with_approval(tool_name, tool_args)

                screenshot_b64 = result.pop("image_base64", None)
                result_str = json.dumps(result, ensure_ascii=False)
                if len(result_str) > 8000:
                    result_str = result_str[:8000] + "... (kısaltıldı)"

                yield AgentEvent("tool_result", {
                    "tool": tool_name,
                    "success": result.get("success", False),
                    "summary": self._summarize_result(tool_name, result),
                })

                function_response_parts.append(
                    _gemini_fn_response(fc, tool_name, {"result": result_str})
                )
                # Recorded with the RESULT OBJECT — never `result_str`, which is
                # the truncated copy built for the transcript (see
                # `_canonical_blob`) — and checked per call rather than at the
                # end of the batch: a response carrying A,A,A,B used to reset the
                # streak before it was ever inspected.
                _progress.record(tool_name, tool_args, result)
                if _progress.stalled:
                    break
                if screenshot_b64:
                    import base64 as _b64
                    _mime, _data = _split_data_url(screenshot_b64)
                    raw_bytes = _b64.b64decode(_data)
                    # Gemini tool-role Content'ine inline_data KONULAMAZ (400 INVALID_ARGUMENT).
                    # Görseli ayrı user-role Content olarak biriktir (aşağıda eklenir).
                    screenshot_parts.append(
                        gtypes.Part(inline_data=gtypes.Blob(mime_type=_mime, data=raw_bytes))
                    )

            # Arada metin varsa (AI'ın açıklaması) yield et
            for _t in text_parts:
                yield AgentEvent("text", {"content": _t})

            # AI'ın yanıtını ve tool sonuçlarını geçmişe ekle
            contents.append(candidate.content)
            # Rol "user", "tool" DEĞİL. Google'ın kendi SDK'sı araç sonuçlarını
            # tam olarak böyle sarıyor (`google/genai/models.py`, otomatik
            # fonksiyon çağırma dalı: `types.Content(role='user', parts=...)`),
            # ve uç "tool"u reddediyor:
            #   400 INVALID_ARGUMENT — "Role 'tool' is not supported. Please use
            #   a valid role: SYSTEM, SYSTEM_1, USER, ASSISTANT, DEVELOPER,
            #   CONTEXT, USER_CONTEXT, MODEL, USER."
            # Sahada 30 Ağu 2026'da yakalandı: Gemini yolu İLK araç çağrısında
            # ölüyordu, yani bu yolun araçlı hali hiç çalışmamıştı. Satır 30 May
            # 2026'dan beri böyleydi ("tool" OpenAI'ın konvansiyonu, Google'ın
            # değil) — kimse tetiklemediği için sessiz kaldı.
            contents.append(gtypes.Content(role="user", parts=function_response_parts))
            # Screenshot(lar) → AYRI user-role Content (Gemini tool-role'a görsel kabul etmez → 400)
            if screenshot_parts:
                contents.append(gtypes.Content(
                    role="user",
                    parts=[gtypes.Part(text="(capture_unity_screenshot çıktısı — ekran görüntüsü:)")] + screenshot_parts,
                ))

            if _progress.stalled:
                logger.warning(f"  ⛔ Gemini loop {iteration + 1}. turda ilerlemeyi durdurdu")
                for _ev in _stop_events(iteration + 1, "no_progress", _progress.repeated_tool):
                    yield _ev
                return

        for _ev in _stop_events(MAX_ITERATIONS, "max_iterations"):
            yield _ev

    # ═══════════════════════════════════════════════
    # ANTHROPIC AGENTIC LOOP (Claude 3.5 Sonnet vb.)
    # ═══════════════════════════════════════════════
    async def _run_anthropic(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        async for _ev in _guarantee_terminal(self._anthropic_loop(user_message),
                                             self.model_name):
            yield _ev

    async def _anthropic_loop(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        
        system_instruction = f"""{SYSTEM_PROMPT}

Sen Unity projesi üzerinde çalışan bir AI asistanısın. Sana verilen araçları kullanarak projeyi keşfedebilir, dosyaları okuyabilir, arama yapabilir ve kod yazabilirsin.

[ÇALIŞMA PRENSİBİ - HAYATİ KURALLAR]
1. ÖNCE projeyi keşfet (dosya oku, ara).
2. KOD YAZARKEN: KESİNLİKLE parça kod (snippet) verme. Sadece değişen yeri değil, dosyanın TAMAMINI yazmak ZORUNDASIN.
3. KOD BLOKLARI: Her kod bloğunun İSTİSNASIZ İLK SATIRINA yolu ekle:
// path: Assets/Scripts/Tam/Dosya/Yolu.cs

4. `write_file` aracını C# kodu yazmak için KULLANMA. Bunun yerine, kodu Markdown kod bloğu (```csharp ... ```) içinde ver. Kullanıcı arayüzden onaylayacaktır.
5. Kullanıcının en son verdiği teknik talimatları asla unutma, her güncellemede bunları koru.
6. Eğer tam dosyayı yazmazsan sistem çalışmaz ve kullanıcıya hatalı bilgi vermiş olursun.

[BAĞLAM]
{self.context or "Yeni sohbet."}"""
        
        # Tool formatı
        anthropic_tools = []
        for t in _all_tool_definitions():
            # Anthropic expects input_schema instead of parameters
            anthropic_tools.append({
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"]
            })

        # İlk mesaj içeriği
        user_parts = [{"type": "text", "text": user_message}]
        if self.images:
            for img_data in self.images:
                try:
                    if "," in img_data:
                        header, base64_str = img_data.split(",", 1)
                        mime_type = header.split(":")[1].split(";")[0]
                        user_parts.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": base64_str
                            }
                        })
                except Exception as e:
                    logger.warning(f"Görsel işlenemedi: {e}")

        # Anthropic API: sistem talimatı messages listesine DEĞİL, create()'in
        # system= parametresine gider (aşağıda system_instruction olarak geçiliyor).
        # messages'a "system" rolü koymak API 400 döndürür.
        messages = []
        _turn_t0 = time.time()   # footer: tur süresi + token
        _turn_in = _turn_out = 0

        # Sistem önekini tur başında BİR kez kur (deterministik) ve cache_control ile
        # işaretle → statik system prefix 2..N. iterasyonlarda cache'ten okunur (~%90 tasarruf).
        _sys_text = system_instruction + self._get_architect_wisdom() + self._identity_note()
        _sys_blocks = [{"type": "text", "text": _sys_text,
                        "cache_control": {"type": "ephemeral"}}]

        _progress = _ProgressGuard()
        for iteration in range(MAX_ITERATIONS):
            logger.info(f"  🔄 Anthropic Agentic Loop iterasyon {iteration + 1}")
            
            # Tool formatı
            anthropic_tools = []
            for t in _all_tool_definitions():
                anthropic_tools.append({
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"]
                })
            # Prompt caching: son tool'a breakpoint → tüm 'tools' prefix'i (statik ve
            # büyük ~24k tok Unity MCP şemaları) cache'lenir; tekrar turlarda ~%90 tasarruf.
            if anthropic_tools:
                anthropic_tools[-1] = {**anthropic_tools[-1],
                                       "cache_control": {"type": "ephemeral"}}

            # İlk mesaj içeriği
            if iteration == 0:
                user_parts = [{"type": "text", "text": user_message}]
                if self.images:
                    for img_data in self.images:
                        try:
                            if "," in img_data:
                                header, base64_str = img_data.split(",", 1)
                                mime_type = header.split(":")[1].split(";")[0]
                                user_parts.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": mime_type,
                                        "data": base64_str
                                    }
                                })
                        except Exception as e:
                            logger.error(f"Anthropic image parsing hatası: {e}")
                messages.append({"role": "user", "content": user_parts})
            
            try:
                # Effort: kayıtçıdan; extra_body ile geçer (output_config.effort —
                # SDK sürümü parametreyi tanımasa da httpx gövdesine girer). auto → {}.
                from providers.effort_caps import map_effort as _map_effort
                _eff_body = _map_effort("anthropic", self.model_name,
                                        self.effort_level or self.thinking_level or "auto"
                                        ).get("anthropic_extra_body")
                # Kalp atışı: OpenAI ve Gemini yollarıyla aynı yardımcı.
                _istek = asyncio.create_task(client.messages.create(
                    model=self.model_name,
                    max_tokens=_anthropic_loop_max_tokens(self.model_name),
                    system=_sys_blocks,
                    messages=messages,
                    tools=anthropic_tools,
                    **({"extra_body": _eff_body} if _eff_body else {}),
                ))
                async for _kalp in self._await_provider(_istek):
                    yield _kalp
                response = _istek.result()
            except Exception as e:
                yield AgentEvent("error", {"message": f"Claude hatası: {str(e)}"})
                return

            _u = getattr(response, "usage", None)
            if _u:
                _turn_in += getattr(_u, "input_tokens", 0) or 0
                _turn_out += getattr(_u, "output_tokens", 0) or 0

            # OpenAI'daki `choices[0]` ile aynı sınıf: bloklar üzerinde koşulmadan
            # önce gerçekten bir dizi olduğu doğrulanıyor. `None` gelirse eskiden
            # `TypeError` generator'ın dışına taşınır ve tur terminalsiz biterdi.
            _bloklar = getattr(response, "content", None) or []
            messages.append({"role": "assistant", "content": _bloklar})

            tool_calls = [block for block in _bloklar if block.type == "tool_use"]
            text_blocks = [block for block in _bloklar if block.type == "text"]
            
            for text_block in text_blocks:
                if text_block.text:
                    yield AgentEvent("text", {"content": text_block.text})

            if getattr(response, "stop_reason", None) == "max_tokens":
                # Used to pass as a normal finish. A tool_use cut at the cap
                # carries partial input, so no tool of this turn is run.
                partial_text = "\n".join(b.text for b in text_blocks if b.text)
                yield AgentEvent("turn_usage", {
                    "input_tokens": _turn_in, "output_tokens": _turn_out, "cost_usd": None,
                    "duration_ms": int((time.time() - _turn_t0) * 1000),
                })
                if partial_text:
                    yield AgentEvent("response", {"content": partial_text})
                for _ev in _stop_events(iteration + 1, "max_tokens"):
                    yield _ev
                return

            if not tool_calls:
                # Final yanıt
                final_text = "\n".join(b.text for b in text_blocks if b.text)
                yield AgentEvent("turn_usage", {
                    "input_tokens": _turn_in, "output_tokens": _turn_out, "cost_usd": None,
                    "duration_ms": int((time.time() - _turn_t0) * 1000),
                })
                yield AgentEvent("response", {"content": final_text})
                yield _done_event(iteration + 1)
                return

            tool_results = []
            for tool_call in tool_calls:
                yield AgentEvent("tool_call", {
                    "tool": tool_call.name,
                    "arguments": tool_call.input,
                    "iteration": iteration + 1,
                })

                # Terminal Onay Katmanı
                _approval_text = self._approval_prompt(tool_call.name, tool_call.input)
                if _approval_text:
                    with self._approval_gate(_approval_text) as (_gate_event, _gate_id):
                        yield _gate_event
                        _decision = await self._await_approval(_gate_id)
                    if not _decision.approved:
                        _red = {"success": False, "summary": _decision.summary}
                        result_str = json.dumps(_red, ensure_ascii=False)
                        tool_results.append({"type": "tool_result", "tool_use_id": tool_call.id, "content": result_str})
                        # Re-asking for a call the user keeps refusing is a stall
                        # like any other, so it is recorded rather than skipped —
                        # as the OBJECT, like every other `record` call site.
                        _progress.record(tool_call.name, tool_call.input, _red)
                        if _progress.stalled:
                            break
                        continue

                result, _ = await self._execute_tool_with_approval(tool_call.name, tool_call.input)

                screenshot_b64 = result.pop("image_base64", None)
                result_str = json.dumps(result, ensure_ascii=False)
                if len(result_str) > 8000:
                    result_str = result_str[:8000] + "... (kısaltıldı)"

                yield AgentEvent("tool_result", {
                    "tool": tool_call.name,
                    "success": result.get("success", False),
                    "summary": self._summarize_result(tool_call.name, result),
                })

                if screenshot_b64:
                    _mime, _data = _split_data_url(screenshot_b64)
                    content = [
                        {"type": "text", "text": result_str},
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": _mime,
                            "data": _data,
                        }},
                    ]
                else:
                    content = result_str

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": content
                })
                # Recorded with the RESULT OBJECT — never `result_str`, which is
                # the truncated copy built for the transcript (see
                # `_canonical_blob`) — and checked per call rather than at the
                # end of the batch: a response carrying A,A,A,B used to reset the
                # streak before it was ever inspected.
                _progress.record(tool_call.name, tool_call.input, result)
                if _progress.stalled:
                    break

            messages.append({"role": "user", "content": tool_results})

            if _progress.stalled:
                logger.warning(f"  ⛔ Anthropic loop {iteration + 1}. turda ilerlemeyi durdurdu")
                for _ev in _stop_events(iteration + 1, "no_progress", _progress.repeated_tool):
                    yield _ev
                return

        for _ev in _stop_events(MAX_ITERATIONS, "max_iterations"):
            yield _ev

    # ═══════════════════════════════════════════════
    # OPENAI AGENTIC LOOP (OpenAI, DeepSeek, vb.)
    # ═══════════════════════════════════════════════
    async def _run_openai(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        async for _ev in _guarantee_terminal(self._openai_loop(user_message),
                                             self.model_name):
            yield _ev

    async def _openai_loop(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        # GPT-6 does tool calling ONLY through the Responses API: Chat
        # Completions accepts the model but never returns tool_calls, so the
        # agent loop would silently degrade into a tool-less chat. Explicit
        # error instead, until this path is ported to the Responses API.
        if ((self.provider_type or "").lower() == "openai"
                and (self.model_name or "").lower().startswith("gpt-6")):
            yield AgentEvent("error", {"message": (
                f"'{self.model_name}' bu sürümde bulut API anahtarıyla araç kullanamıyor: "
                "GPT-6 araç çağrısını yalnız Responses API'si üzerinden yapıyor, "
                "uygulama ise Chat Completions kullanıyor. Codex (abonelik) yolundan "
                "'Codex (GPT-6 Astra)' seçeneğini kullanın ya da başka bir bulut modeli seçin."
            )})
            return

        client = openai.AsyncOpenAI(api_key=self.api_key)
        
        # DeepSeek OpenRouter veya custom base URL'ler için:
        if self.provider_type == "openrouter":
            client.base_url = "https://openrouter.ai/api/v1"
        elif self.provider_type == "deepseek":
            client.base_url = "https://api.deepseek.com"
        elif self.provider_type == "groq":
            client.base_url = "https://api.groq.com/openai/v1"
        elif self.provider_type == "moonshot":
            client.base_url = "https://api.moonshot.ai/v1"
        elif self.provider_type == "z-ai":
            client.base_url = "https://api.z.ai/api/paas/v4"
        elif self.provider_type == "nvidia":
            # NVIDIA NIM — OpenAI-uyumlu, tek nvapi- key ile ücretsiz model havuzu
            client.base_url = "https://integrate.api.nvidia.com/v1"

        system_instruction = f"""{SYSTEM_PROMPT}

Sen Unity projesi üzerinde çalışan bir AI asistanısın. Sana verilen araçları kullanarak projeyi keşfedebilir, dosyaları okuyabilir, arama yapabilir ve kod yazabilirsin.

[ÇALIŞMA PRENSİBİ - HAYATİ KURALLAR]
1. ÖNCE projeyi keşfet (dosya oku, ara).
2. KOD YAZARKEN: KESİNLİKLE parça kod (snippet) verme. Sadece değişen yeri değil, dosyanın TAMAMINI yazmak ZORUNDASIN.
3. KOD BLOKLARI: Her kod bloğunun İSTİSNASIZ İLK SATIRINA yolu ekle:
// path: Assets/Scripts/Tam/Dosya/Yolu.cs

4. `write_file` aracını C# kodu yazmak için KULLANMA. Bunun yerine, kodu Markdown kod bloğu (```csharp ... ```) içinde ver. Kullanıcı arayüzden onaylayacaktır.
5. Kullanıcının en son verdiği teknik talimatları asla unutma, her güncellemede bunları koru.
6. Eğer tam dosyayı yazmazsan sistem çalışmaz ve kullanıcıya hatalı bilgi vermiş olursun.

[BAĞLAM]
{self.context or "Yeni sohbet."}"""
        
        openai_tools = get_openai_tool_declarations()
        
        # İlk mesaj içeriği
        user_content = [{"type": "text", "text": user_message}]
        if self.images:
            for img_data in self.images:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": img_data} # OpenAI direkt data URL kabul eder
                })

        # Prompt caching: statik system öneki (SYSTEM_PROMPT + transcript + kimlik) tekrar
        # turlarda cache'ten okunsun. OpenRouter cache_control breakpoint'ini DESTEKLER
        # (OR→Anthropic/Gemini için explicit ZORUNLU). DeepSeek/OpenAI/Moonshot(Kimi)/z-ai(GLM)
        # zaten OTOMATİK prefix-cache yapar → düz string yeterli; bilinmeyen alanı reddedebilen
        # sağlayıcılara cache_control göndermeyerek riski sıfırlıyoruz.
        _sys = system_instruction + self._identity_note()
        if self.provider_type == "openrouter":
            system_msg = {"role": "system", "content": [
                {"type": "text", "text": _sys, "cache_control": {"type": "ephemeral"}},
            ]}
        else:
            system_msg = {"role": "system", "content": _sys}

        messages = [
            system_msg,
            {"role": "user", "content": user_content}
        ]

        _turn_t0 = time.time()   # footer: tur süresi + token
        # Reasoning/effort: kayıtçıdan (effort_caps) — auto/desteklenmeyen → hiçbir
        # parametre gitmez. request_params üst-seviye (reasoning_effort), extra_body
        # sağlayıcıya özel gövde (NIM chat_template_kwargs, deepseek/z-ai thinking).
        from providers.effort_caps import map_effort as _map_effort
        _eff = _map_effort(self.provider_type, self.model_name,
                           self.effort_level or self.thinking_level or "auto")
        _effort_params = _eff.get("request_params", {})
        _effort_extra_body = _eff.get("extra_body")

        _turn_in = _turn_out = 0
        _progress = _ProgressGuard()
        for iteration in range(MAX_ITERATIONS):
            logger.info(f"  🔄 OpenAI Agentic Loop iterasyon {iteration + 1}")
            
            # Rate limit koruması için kısa mola
            if iteration > 0:
                await asyncio.sleep(5.0)

            # Retry mekanizması
            response = None
            _son_hata = ""
            for retry in range(3):
                try:
                    # İstek bir göreve alınıyor ki BEKLERKEN konuşabilelim.
                    # Ölçüldü 30 Ağu 2026 (Burak, NVIDIA NIM üzerinde bir
                    # DeepSeek modeli): iki dakika boyunca ekranda yalnız
                    # "düşünüyor..." vardı ve kullanıcı uygulamanın mı yoksa
                    # sağlayıcının mı takıldığını bilemiyordu. openai SDK'sının
                    # varsayılan zaman aşımı 600 sn, yani hiçbir şey söylemeden
                    # on dakika beklenebiliyordu.
                    _istek = asyncio.create_task(client.chat.completions.create(
                        model=self.model_name,
                        messages=messages,
                        tools=openai_tools,
                        tool_choice="auto",
                        timeout=_SAGLAYICI_ZAMAN_ASIMI,
                        **_effort_params,
                        **({"extra_body": _effort_extra_body} if _effort_extra_body else {}),
                    ))
                    async for _kalp in self._await_provider(_istek):
                        yield _kalp
                    response = _istek.result()
                    break
                except Exception as e:
                    err_msg = str(e).lower()
                    logger.error(f"  ❌ OpenAI/OpenRouter API hatası [{self.provider_type} / {self.model_name}]: {str(e)}", exc_info=True)
                    _kod = provider_retry_code(err_msg)
                    if _kod:
                        _son_hata = _kod
                        wait_time = (retry + 1) * 10
                        logger.warning(f"  ⚠️ OpenAI/OpenRouter Hatası ({_kod}). {wait_time}s bekleniyor... (Deneme {retry+1}/3)")
                        yield AgentEvent("status", {"detail": (
                            f"🚦 Sağlayıcı {_kod} döndü "
                            f"({'kota/hız sınırı' if _kod == '429' else 'servis meşgul'}) — "
                            f"{wait_time} sn bekleniyor, deneme {retry + 1}/3"
                        )})
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        yield AgentEvent("error", {"message": f"OpenAI/API hatası: {str(e)}"})
                        return
            
            if not response:
                # Gemini yolundaki ile AYNI sözleşme: reddeden model değil
                # sağlayıcı, ve 429 ile 503 aynı cümleye sıkıştırılmıyor.
                _kodlar = {"429": "provider_quota", "503": "provider_unavailable"}
                _aciklama = {
                    "429": "Sağlayıcı kota/hız sınırı nedeniyle üç denemede de isteği reddetti. "
                           "Bir süre bekleyip tekrar dene ya da başka bir modele geç.",
                    "503": "Sağlayıcı üç denemede de meşgul döndü (503). Bu geçici; "
                           "birazdan tekrar dene.",
                }.get(_son_hata, "Sağlayıcıya üç denemede de ulaşılamadı.")
                yield AgentEvent("error", {
                    "code": _kodlar.get(_son_hata, "provider_unreachable"),
                    "message": _aciklama,
                })
                return

            _u = getattr(response, "usage", None)
            if _u:
                _turn_in += getattr(_u, "prompt_tokens", 0) or 0
                _turn_out += getattr(_u, "completion_tokens", 0) or 0

            # `choices[0]` KONTROLSÜZ indekslenemez. Sözdizimsel olarak geçerli
            # ama `choices=[]` olan bir sağlayıcı cevabı `IndexError`'ı
            # generator'ın DIŞINA taşıyordu ve tur HİÇBİR terminal olay
            # üretmeden bitiyordu (denetim, 30 Ağu 2026) — sözleşme her turun
            # tam olarak bir `done` ya da bir `error` ile bitmesi. Bozuk cevap
            # bir arıza, o yüzden tek `error`.
            _secenekler = getattr(response, "choices", None) or []
            if not _secenekler:
                yield AgentEvent("error", {
                    "code": "provider_malformed_response",
                    "message": (
                        f"`{self.model_name}` boş bir yanıt döndürdü (hiç seçenek yok). "
                        "Bu sağlayıcı tarafında bir arıza; tekrar dene ya da başka bir "
                        "modele geç."
                    )})
                return
            message = _secenekler[0].message

            # API formatında mesaja ekle
            msg_dict = {"role": "assistant"}
            if message.content:
                msg_dict["content"] = message.content
            if message.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    } for tc in message.tool_calls
                ]
            messages.append(msg_dict)
            
            if not message.tool_calls:
                # Final yanıt
                final_text = message.content or "Tamamlandı."
                yield AgentEvent("turn_usage", {
                    "input_tokens": _turn_in, "output_tokens": _turn_out, "cost_usd": None,
                    "duration_ms": int((time.time() - _turn_t0) * 1000),
                })
                yield AgentEvent("response", {"content": final_text})
                yield _done_event(iteration + 1)
                return

            if message.content:
                yield AgentEvent("text", {"content": message.content})
                
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except:
                    tool_args = {}
                    
                yield AgentEvent("tool_call", {
                    "tool": tool_name,
                    "arguments": tool_args,
                    "iteration": iteration + 1,
                })

                # Terminal Onay Katmanı
                _approval_text = self._approval_prompt(tool_name, tool_args)
                if _approval_text:
                    with self._approval_gate(_approval_text) as (_gate_event, _gate_id):
                        yield _gate_event
                        _decision = await self._await_approval(_gate_id)
                    if not _decision.approved:
                        messages.append({"role": "tool", "tool_call_id": tool_call.id,
                                         "name": tool_name, "content": _decision.summary})
                        # Re-asking for a call the user keeps refusing is a stall
                        # like any other, so it is recorded rather than skipped.
                        _progress.record(tool_name, tool_args, _decision.summary)
                        if _progress.stalled:
                            break
                        continue

                result, _ = await self._execute_tool_with_approval(tool_name, tool_args)

                screenshot_b64 = result.pop("image_base64", None)
                result_str = json.dumps(result, ensure_ascii=False)
                if len(result_str) > 8000:
                    result_str = result_str[:8000] + "... (kısaltıldı)"

                yield AgentEvent("tool_result", {
                    "tool": tool_name,
                    "success": result.get("success", False),
                    "summary": self._summarize_result(tool_name, result),
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_name,
                    "content": result_str
                })
                # Recorded with the RESULT OBJECT — never `result_str`, which is
                # the truncated copy built for the transcript (see
                # `_canonical_blob`) — and checked per call rather than at the
                # end of the batch: a response carrying A,A,A,B used to reset the
                # streak before it was ever inspected.
                _progress.record(tool_name, tool_args, result)
                if _progress.stalled:
                    break
                if screenshot_b64:
                    messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "[Screenshot — yukarıdaki tool sonucuyla ilgili görsel]"},
                            {"type": "image_url", "image_url": {"url": screenshot_b64}},
                        ],
                    })

            if _progress.stalled:
                logger.warning(f"  ⛔ OpenAI loop {iteration + 1}. turda ilerlemeyi durdurdu")
                for _ev in _stop_events(iteration + 1, "no_progress", _progress.repeated_tool):
                    yield _ev
                return

        for _ev in _stop_events(MAX_ITERATIONS, "max_iterations"):
            yield _ev

    # ═══════════════════════════════════════════════
    # BASIT FALLBACK (Function calling olmayan provider'lar)
    # ═══════════════════════════════════════════════
    async def _run_simple(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        """Function calling desteklemeyen provider'lar için basit akış."""
        from ai_providers import AIProviderManager

        yield AgentEvent("thinking", {"text": "Direkt yanıt hazırlıyorum..."})

        try:
            logger.info(f"[AgentRunner] Starting simple run for provider: {self.provider_type}")
            provider = AIProviderManager.get_provider({
                "provider_type": self.provider_type,
                "api_key": self.api_key,
                "model_name": self.model_name,
            })

            prompt = f"{SYSTEM_PROMPT}\n\n[BAĞLAM]\n{self.context}\n\n[KULLANICI]\n{user_message}"

            if self.provider_type == "subscription":
                # Subscription ajanları için mod bilgisini ilet
                is_step_mode = (getattr(self, 'generation_mode', 'plan') == 'step')
                full_text = ""
                async for event in provider.analyze_code_with_thinking(prompt, thinking_level=self.thinking_level, cwd=self.workspace_path, interactive=is_step_mode):
                    if event["type"] == "tool_call":
                        yield AgentEvent("tool_call", {"tool": event["tool"], "summary": event["summary"]})
                    elif event["type"] == "thinking":
                        yield AgentEvent("thinking", {"text": event["text"]})
                    elif event["type"] == "error":
                        yield AgentEvent("error", {"message": event["content"]})
                    elif event["type"] == "final":
                        full_text = event["text"]
                
                yield AgentEvent("response", {"content": full_text})
            else:
                if self.thinking_level != "off" and hasattr(provider, "analyze_code_with_thinking"):
                    logger.info(f"[AgentRunner] Requesting thinking response (Level: {self.thinking_level}) at {self.workspace_path}")
                    text, thinking, duration = await asyncio.to_thread(
                        provider.analyze_code_with_thinking, prompt, thinking_level=self.thinking_level, cwd=self.workspace_path
                    )
                    if thinking:
                        yield AgentEvent("thinking", {"text": thinking, "duration_ms": duration})
                else:
                    logger.info(f"[AgentRunner] Requesting standard analysis from {self.provider_type} at {self.workspace_path}")
                    text = await asyncio.to_thread(provider.analyze_code, prompt, thinking_level=self.thinking_level, cwd=self.workspace_path)

                logger.info(f"[AgentRunner] Response received ({len(text) if text else 0} chars).")
                yield AgentEvent("response", {"content": text})

            yield _done_event(1)

        except Exception as e:
            logger.error(f"[AgentRunner] Error in simple run: {str(e)}", exc_info=True)
            yield AgentEvent("error", {"message": str(e)})

    # ═══════════════════════════════════════════════
    # AGY KALICI SESSION (disk-resume + conversation-db okuma)
    # ═══════════════════════════════════════════════
    async def _run_agy_session(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        """Send one stdin turn to the conversation's persistent agy process."""
        from providers.agy_session import _RESUME_IDS, get_session
        from providers._attachments import materialize_images, cleanup_dir
        from secret_redaction import redact_secrets

        session = get_session(
            self.conversation_id, resume_id=self.resume_id,
            cwd=self.workspace_path or ".",
        )
        session.auto_approve = (self.generation_mode == "auto")
        image_paths, attachment_dir = materialize_images(
            self.images, self.workspace_path, f"agy_conv{self.conversation_id}")
        message = user_message
        if self.context:
            # Fresh agy conversation = no id for _start to resume: the id comes
            # from resume_id/_RESUME_IDS (get_session) or the first turn's init
            # event, and _start re-reads the store when the workspace changed.
            # A resumed conversation already holds its own history on disk.
            cwd = os.path.abspath(self.workspace_path or ".")
            resume_uuid = (session.session_id if session.cwd == cwd
                           else _RESUME_IDS.get((self.conversation_id, cwd)))
            if not resume_uuid:
                message = f"{user_message}\n\n{_HANDOFF_HEADER}\n{self.context}"
        if image_paths:
            message += "\n\nAttached images; open these files to inspect them:\n"
            message += "\n".join(f"- {path}" for path in image_paths)
        turn_complete = False
        try:
            async with contextlib.aclosing(session.stream(
                message, model=self.model_name, cwd=self.workspace_path or ".",
            )) as events:
                async for event in events:
                    payload = dict(event)
                    event_type = payload.pop("type", "text")
                    if event_type == "error":
                        detail = (payload.get("message") or payload.get("content")
                                  or payload.get("text") or "agy session failed without details.")
                        payload["message"] = redact_secrets(str(detail))
                    turn_complete = event_type == "done"
                    yield _normalize_session_event(event_type, payload)
                    if event_type == "error":
                        return
        except (asyncio.CancelledError, GeneratorExit):
            if not turn_complete:
                await session.close()
            raise
        except Exception as exc:
            payload = {"message": f"agy session failed: {redact_secrets(str(exc))}"}
            if getattr(exc, "code", None):
                # A coded error (AgyStepGateError) lets the UI use its own language.
                payload.update(exc.params, code=exc.code)
            yield AgentEvent("error", payload)
        finally:
            cleanup_dir(attachment_dir)

    # ═══════════════════════════════════════════════
    # CURSOR / COPILOT / OPENCODE / KIMI — one-shot CLI
    # ═══════════════════════════════════════════════
    async def _run_oneshot_cli_session(self, user_message: str, cli_key: str) -> AsyncGenerator[AgentEvent, None]:
        """Cursor/Copilot/OpenCode/Kimi'yi tur bazlı (ephemeral subprocess) sürer.

        Bağlam, CLI'ların RESMİ resume mekanizmasıyla korunur (agy'nin aksine
        ilk üçünde resmi ve çalışır — 2026-07-13 canlı doğrulandı):
          cursor  → --resume <chatId>   (chatId ilk turun event'lerinden yakalanır)
          copilot → --session-id=<bizim uuid> (tur 1) / --resume=<uuid> (sonrası)
          opencode→ -s <sessionID>      (sessionID her event'te gelir)
          kimi    → doğrulanmış resume yok; her tur kırpılmış transcript
        İlk turda (resume anahtarı yokken) tam transcript enjekte edilir;
        sonraki turlarda CLI kendi hafızasından devam eder.
        """
        import re as _re
        from ai_providers import AIProviderManager
        from providers.oneshot_cli import get_session

        def _make_provider(model_name: str):
            p = AIProviderManager.get_provider({
                "provider_type": self.provider_type,
                "model_name": model_name,
                "api_key": getattr(self, "api_key", ""),
            })
            # Every process this provider spawns works for this chat only
            # (cli_base turns it into GAMACHINE_CONVERSATION_ID, if valid).
            p._conversation_id = self.conversation_id
            return p

        provider = _make_provider(self.model_name)

        sess = get_session(cli_key, self.conversation_id)
        sess.auto_approve = (getattr(self, "generation_mode", "auto") == "auto")
        provider.resume_session_id = sess.session_id
        if cli_key == "copilot" and not sess.session_id:
            # Copilot'ta session UUID'ini BİZ üretiriz (--session-id) → yakalama derdi yok.
            import uuid as _uuid
            provider.fresh_session_id = str(_uuid.uuid4())
            sess.session_id = provider.fresh_session_id

        # Plan kısıtı tespiti: Cursor/Copilot free planlarda adlı modeller kapalı.
        # Hata mesajı bu kalıba uyarsa turu 'auto' modeliyle OTOMATİK tekrarlarız
        # ve öğrenilen kısıt cli_plan_caps.json'a yazılır (model seçici soluklaştırır).
        from providers.oneshot_cli import PLAN_ERROR_RE as _PLAN_ERR, set_named_models_cap
        _current_model = (self.model_name or "").lower()
        _can_fallback = (
            cli_key in ("cursor", "copilot")
            and _current_model not in (f"{cli_key}-auto",)
        )

        # İlk turda transcript enjeksiyonu (sonraki turlarda CLI resume hatırlar).
        enriched_prompt = user_message
        # Kimi CLI'nın doğrulanmış resume mekanizması yok; her turda kırpılmış
        # transcript verilir. Diğer one-shot CLI'lar resmi session resume kullanır.
        if self.context and (cli_key == "kimi" or not sess.ctx_injected):
            _CTX_CAP = 24000  # Windows argv sınırı (~32K) + mcp_hint payı
            _ctx = self.context
            if len(_ctx) > _CTX_CAP:
                _ctx = "…[eski geçmiş kırpıldı — en yeni kısım korundu]\n" + _ctx[-_CTX_CAP:]
            enriched_prompt = f"{user_message}\n\n{_HANDOFF_HEADER}\n{_ctx}"
        sess.ctx_injected = (cli_key != "kimi")

        # Görseller: dosyaya yaz + yolu prompt'a enjekte (CLI read aracıyla açar).
        from providers._attachments import materialize_images, cleanup_dir
        _img_paths, _att_dir = materialize_images(
            self.images, self.workspace_path, f"{cli_key}_conv{self.conversation_id}")
        if _img_paths:
            _lines = "\n".join(f"- {p}" for p in _img_paths)
            enriched_prompt += ("\n\n[EKLİ GÖRSELLER] Kullanıcı bu turda görsel ekledi. "
                                "İncelemen için (read aracıyla aç):\n" + _lines)

        # Effort seviyesi provider'a attr olarak geçer (agy _resume_uuid deseni):
        # copilot _build_cmd --effort'a, opencode _register_mcp opencode.json'a çevirir.
        provider._effort_level = self.effort_level or self.thinking_level or "auto"

        final_text = ""
        got_error = False
        reset_session = False
        approval_turn_token = None
        if cli_key == "opencode":
            from agentic.approval_policy import begin_opencode_turn
            approval_turn_token = begin_opencode_turn(
                self.workspace_path or ".",
                getattr(self, "generation_mode", "auto"),
            )
            provider._approval_turn_token = approval_turn_token
        try:
            for attempt in (1, 2):
                got_error = False
                _plan_error = False
                sess.active_provider = provider
                async for event in provider.analyze_code(
                    enriched_prompt,
                    thinking_level="medium" if self.use_thinking else "off",
                    cwd=self.workspace_path or ".",
                    interactive=True,
                ):
                    etype = event.get("type")
                    if etype == "session_meta":
                        _sid = event.get("session_id")
                        if _sid and not sess.session_id:
                            sess.session_id = _sid
                            logger.info(f"[{cli_key}Session] conv={self.conversation_id} resume anahtarı: {_sid}")
                    elif etype == "delta":
                        yield AgentEvent("text", {"content": event.get("text", "")})
                    elif etype == "thinking":
                        yield AgentEvent("thinking", {"text": event.get("text", "")})
                    elif etype == "final":
                        final_text = event.get("text", "")
                    elif etype == "error":
                        _msg = event.get("content", "")
                        if event.get("reset_session"):
                            reset_session = True
                        if attempt == 1 and _can_fallback and _PLAN_ERR.search(_msg):
                            # Plan bu modeli desteklemiyor → hatayı GÖSTERME, Auto ile tekrarla.
                            _plan_error = True
                        else:
                            got_error = True
                            from providers.oneshot_cli import (
                                QUOTA_ERROR_RE as _QRE,
                                UPSTREAM_ERROR_RE as _URE,
                            )
                            if _QRE.search(_msg):
                                _msg = (f"⏳ {cli_key.capitalize()} kullanım hakkın dolmuş görünüyor "
                                        f"(plan kotası). Kota yenilenene kadar başka bir sağlayıcı "
                                        f"seçebilirsin (örn. NVIDIA ücretsiz havuzu veya OpenCode).\n\n"
                                        + _msg[:200])
                            elif cli_key == "opencode" and _URE.search(_msg):
                                _msg = (
                                    "⏳ Kimi/OpenCode sağlayıcısı isteği geçici olarak "
                                    "reddetti. Bu genellikle sağlayıcı yoğunluğu veya rate "
                                    "limit nedeniyle olur; birkaç dakika sonra tekrar dene. "
                                    "Oturum bağlamı korundu.\n\n" + _msg[:200]
                                )
                            yield AgentEvent("error", {"message": _msg})

                if not _plan_error:
                    # Adlı model başarıyla çalıştıysa planın desteklediğini öğren
                    # (upgrade sonrası soluk modeller kendiliğinden açılır).
                    if attempt == 1 and not got_error and _can_fallback:
                        set_named_models_cap(cli_key, True)
                    break

                # ── Auto fallback (yalnız cursor/copilot, tek sefer) ──
                set_named_models_cap(cli_key, False)
                logger.info(f"[{cli_key}Session] plan kısıtı → auto fallback (model={self.model_name})")
                yield AgentEvent("thinking", {
                    "text": (f"ℹ️ Aboneliğin bu modeli desteklemiyor — **Auto** ile devam ediyorum. "
                             f"(Kalıcı çözüm: model seçiciden {cli_key.capitalize()} Auto'yu seç.)")
                })
                provider = _make_provider(f"{cli_key}-auto")
                if cli_key == "copilot":
                    # --session-id yoksa YARATIR, varsa devam eder (CLI help'inden) —
                    # ilk tur patladıysa session hiç doğmamış olabilir, --resume ölürdü.
                    provider.fresh_session_id = sess.session_id
                    provider.resume_session_id = None
                else:
                    provider.resume_session_id = sess.session_id
        except (asyncio.CancelledError, GeneratorExit):
            # SSE bağlantısı kesildiğinde sonraki tur yarım OpenCode/Cursor/Copilot
            # session'ını resume etmesin. Tam transcript temiz session'a verilecek.
            sess.session_id = None
            sess.ctx_injected = False
            raise
        finally:
            if sess.active_provider is provider:
                sess.active_provider = None
            if approval_turn_token:
                from agentic.approval_policy import end_opencode_turn
                end_opencode_turn(approval_turn_token)
            cleanup_dir(_att_dir)

        if reset_session:
            # SIGKILL/timeout sonrası CLI'ın disk oturumu yarım kalmış olabilir.
            # Resume etme; bir sonraki tur temiz session açsın ve request route'un
            # verdiği tam sohbet transcript'ini yeniden enjekte etsin.
            sess.session_id = None
            sess.ctx_injected = False
            logger.warning(
                f"[{cli_key}Session] conv={self.conversation_id} yarım/fatal tur "
                "sonrası resume anahtarı sıfırlandı")

        if got_error:
            return

        if not final_text:
            final_text = f"⚠️ {cli_key} bu tur için bir yanıt üretmedi."
        yield AgentEvent("response", {"content": final_text})
        yield _done_event(1, session_id=sess.session_id)

    # ═══════════════════════════════════════════════
    # CLAUDE KALICI SESSION (claude-agent-sdk, native onay)
    # ═══════════════════════════════════════════════
    async def _run_claude_session(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        """
        Claude Code'u sohbet başına KALICI interaktif session olarak sürer.
        - Bağlam turlar arası korunur (DB geçmişi prompt'a basılmaz; session hatırlar).
        - Onay native: can_use_tool → command_gates → frontend onay kartı.
        - AskUserQuestion (A/B/C) → question_needed event → frontend seçim kartı.
        """
        import subprocess as _sp
        from providers.cli_base import BaseCLIProvider, build_spawn_env, env_family
        from providers.claude_sdk_session import (
            get_session, close_session, _SESSIONS, SessionBusyError,
            CLAUDE_SETTING_SOURCES as _CLAUDE_SETTING_SOURCES,
        )

        # MCP: SADECE unityMCP (Unity sahne kontrolü) session'a girsin. Eski 'unityai'
        # (mcp__unityai__bash/save_file + kendi onay köprüsü) ARTIK GİRMESİN — terminal/yazma
        # built-in Bash/Write üzerinden native can_use_tool onayına gitsin (Seçenek 1).
        #
        # Kayıt SDK'ya DOĞRUDAN geçiliyor (aşağıda `mcp_servers=`), workspace'e
        # `.mcp.json` yazılarak DEĞİL. Sebep: o dosyanın okunması `setting_sources`
        # içinde `"project"` gerektiriyordu ve `"project"` onay kapısını dört ayrı
        # yoldan düşürüyordu — gerekçe `claude_sdk_session.CLAUDE_SETTING_SOURCES`.
        #
        # try'ın DIŞINDA: aşağıdaki `_session_kwargs` bu değişkeni okuyor. İçeride
        # kalsaydı bir istisna onu tanımsız bırakır ve tur NameError ile ölürdü —
        # üstelik tam da unityMCP kurulamadığı anda.
        mcp_servers_cfg: dict = {}
        try:
            from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
            # URL'i ELLE KURMA: mcp_url() tek kaynak. Sunucu bizim değilse ya da
            # ayakta değilse None döner. Sır URL'de DEĞİL — api_headers()'tan gelir.
            unity_mcp_url = unity_mcp_manager.mcp_url()
            if unity_mcp_url:
                # "type": "http" ZORUNLU — yoksa Claude Code bunu stdio sunucu sanıp
                # 'command' arar, bulamayınca "invalid MCP server config" ile ATLAR
                # (Unity araçları manage_scene/manage_gameobject vb. görünmez).
                _unity_headers = dict(unity_mcp_manager.api_headers())
                # The session is this conversation's alone (_SESSIONS is keyed
                # by it), so the header names the chat its approval cards
                # belong to. The backend trusts it only while a turn runs.
                if type(self.conversation_id) is int and self.conversation_id > 0:
                    _unity_headers["X-Gamachine-Conversation"] = str(self.conversation_id)
                mcp_servers_cfg["unityMCP"] = {
                    "type": "http",
                    "url": unity_mcp_url,
                    "headers": _unity_headers,
                }
            # Önceki sürümlerin bu projeye yazdığı `.mcp.json` artık okunmuyor;
            # düz metin `X-API-Key` taşıdığı için diskte de bırakılmıyor.
            _remove_project_mcp_json(self.workspace_path)
            # Önceki sürümlerin user-scope'a yazdığı unityai kaydını temizle
            # (_resolve_exec @staticmethod — provider örneği yaratmaya gerek yok)
            # env= ZORUNLU: bu da bir üçüncü taraf CLI spawn'ı ve Claude SDK yolu
            # her turda buradan geçiyor. Verilmezse `claude` süreci
            # LOCAL_APP_TOKEN'ı ve kullanıcının export ettiği tüm vendor
            # anahtarlarını görür (aynı sınıf 2026-07-29'da canary ile ölçüldü).
            _sp.run(BaseCLIProvider._resolve_exec(["claude", "mcp", "remove", "unityai", "--scope", "user"]),
                    capture_output=True, timeout=5,
                    env=build_spawn_env(env_family("claude")))
        except Exception as e:
            logger.warning(f"[ClaudeSession] MCP temizleme/yazma hatası: {e}")

        model = self.model_name if (self.model_name or "").startswith("claude-") else None

        # Effort: kayıtçıdan (effort_caps) eşlenir — "auto" veya desteklenmeyen seviye →
        # None → SDK'ya effort GEÇMEZ, model kendi varsayılanıyla çalışır (Claude'da high).
        # effort connect-time KİLİTLİ (SDK'da set_effort yok). Cache'li session'ın effort'u
        # farklıysa close+recreate gerekir: bu, o sohbetteki CANLI session'ı sıfırlar (DB
        # bağlam özeti aşağıda yeniden enjekte edilir). Böylece seçim gerçekten etki eder.
        from providers.effort_caps import map_effort as _map_effort
        _lvl = self.effort_level or self.thinking_level or "auto"
        desired_effort = _map_effort("subscription", self.model_name or "claude-",
                                     "low" if _lvl == "off" else _lvl).get("sdk_effort")
        # Cache'li session'ın CONNECT-TIME kimliği (workspace + model + effort) istenenden
        # farklıysa yeniden kurulmalı. Eskiden yalnız `effort` karşılaştırılıyordu;
        # ölçüldü (2026-07-28): workspace A ile açılan session B istendiğinde aynen
        # dönüyordu (etkin cwd = A) ve model sessizce düşüyordu. Codex yolundaki
        # desenin aynısı (`_run_codex_session`), farkı: karşılaştırma CANONICAL —
        # `abspath` symlink alias'ında yanlış "değişmedi"/"değişti" der.
        _workspace = self.workspace_path or "."
        _existing = _SESSIONS.get(self.conversation_id)
        _reasons = _oturum_yeniden_kurma_gerekceleri(
            _existing, model=model, effort=desired_effort,
            workspace=_workspace, mcp_servers=mcp_servers_cfg,
        )
        if _reasons:
            logger.info(f"[ClaudeSession] {', '.join(_reasons)}; session yeniden kuruluyor "
                        f"(canlı bağlam sıfırlanır, DB özeti korunur)")
            await close_session(self.conversation_id)

        _session_kwargs = dict(
            model=model,
            cwd=_workspace,
            resume_id=self.resume_id,      # None ise SDK'ya `resume` hiç verilmiyor
            permission_mode="default",
            # ⚠️ `"project"`i buraya geri EKLEME — gerekçe ve canlı ölçüm
            # `claude_sdk_session.CLAUDE_SETTING_SOURCES`'ta. Kullanıcının açtığı
            # Unity projesine konan bir `.claude/settings.json` kapıyı düşürüyordu.
            setting_sources=list(_CLAUDE_SETTING_SOURCES),
            # unityMCP artık workspace dosyası üzerinden değil buradan giriyor.
            mcp_servers=mcp_servers_cfg,
            effort=desired_effort,                 # Claude-only; None ise CLI varsayılanı
            # Old unityai tools stay off even if something loads them, and so does
            # every unityMCP tool that can write a .cs file (see unity_script_tools):
            # C# goes through the built-in Write, which passes the card and the file guard.
            disallowed_tools=[
                *DISALLOWED_UNITY_TOOLS,
                "mcp__unityai__bash",
                "mcp__unityai__save_file",
            ],
        )

        # Görsel: Claude Code SDK/headless satır-içi image-block'u modele SUNMUYOR
        # (SDK ContentBlock union'ında ImageBlock yok → blok sessizce düşer). Bu yüzden
        # agy ile aynı yol: görseli temp'e yaz + mesaja yol enjekte → Claude kendi Read
        # aracıyla açar (Read görselleri görsel olarak modele sunar). Auto modda otomatik.
        from providers._attachments import materialize_images, cleanup_dir
        _img_paths, _att_dir = materialize_images(
            self.images, self.workspace_path, f"claude_conv{self.conversation_id}")
        _img_suffix = ""
        if _img_paths:
            _lines = "\n".join(f"- {p}" for p in _img_paths)
            _img_suffix = ("\n\n[EKLİ GÖRSELLER] Kullanıcı bu turda görsel ekledi. "
                           "İncelemen için Read aracıyla aç:\n" + _lines)

        # Sıkışmış/kopuk session'da bir kez otomatik reset + yeniden dene: "Durdur"
        # sonrası ya da CLI çökmesi sonrası kullanıcı mesajı asla "düşünüyor"da kalmaz.
        for _attempt in (1, 2):
            session = get_session(self.conversation_id, **_session_kwargs)

            # Oto mod → onay sormadan otomatik izin; Adım/Plan modu → her işlemde onay kartı.
            # Session kalıcı olduğu için mod her turda güncellenir (kullanıcı ortada değiştirebilir).
            session.auto_approve = (self.generation_mode == "auto")

            # İlk turda proje bağlamını ekle; sonraki turlarda session zaten hatırlıyor.
            message = user_message
            # ⚠️ `resume` varken transcript AYRICA enjekte EDİLMEZ: CLI kendi tam
            # geçmişini zaten geri yüklüyor, üstüne bir de bizim özetimizi koymak
            # modele aynı konuşmayı İKİ KEZ gösterirdi (ve 20.000 karakteri boşa
            # harcardı). Kimlik yoksa eski yol aynen sürüyor.
            if self.context and not session.session_id and not self.resume_id:
                message = f"{user_message}\n\n{_HANDOFF_HEADER}\n{self.context}"
            # Ultracode (Claude-only): SDK'da option YOK → tek yol mesaja keyword enjeksiyonu.
            # CLI bu kelimeyi görünce çok-ajanlı ultracode akışını tetikler (belgesiz; sürüme bağlı).
            if self.ultracode:
                message = f"{message}\n\nultracode"
            # Ekli görsel yolları (varsa) mesaja eklenir → Claude Read ile açar.
            message = f"{message}{_img_suffix}"

            _yielded = 0
            try:
                async for ev in session.stream(message):
                    _yielded += 1
                    etype = ev.pop("type", "text")
                    yield _normalize_session_event(etype, ev)
                cleanup_dir(_att_dir)
                return
            except Exception as e:
                # Event akmadan patladıysa (busy/kopuk) güvenle resetleyip tekrar dene;
                # akış ortasında patladıysa retry çift metin basar → direkt hata göster.
                if _attempt == 1 and _yielded == 0:
                    logger.warning(f"[ClaudeSession] session sıkışmış/kopuk ({e}) → reset + retry")
                    yield AgentEvent("status", {
                        "detail": "⚠️ Claude session yanıt vermedi — yeniden başlatılıyor…",
                    })
                    try:
                        await close_session(self.conversation_id)
                    except Exception:
                        logger.exception("[ClaudeSession] reset sırasında close hatası")
                    continue
                logger.exception("[ClaudeSession] stream hatası")
                cleanup_dir(_att_dir)
                # ⚠️ `redact_secrets` ŞART: bu metin SSE ile tarayıcıya gidiyor ve
                # oturum yapılandırması artık unityMCP `X-API-Key`'ini taşıyor.
                # Log tarafı global bir filtreyle korunuyor (`install_log_redaction`),
                # ama o filtre bu olay nesnesine uğramıyor — koruma sanılan yerde
                # yoktu (denetim bulgusu, `raw-secret-exception-to-client`).
                from secret_redaction import redact_secrets as _redact
                yield AgentEvent("error", {"message": f"Claude session hatası: {_redact(str(e))}"})
                return

    # ═══════════════════════════════════════════════
    # CODEX KALICI SESSION (codex app-server, native onay)
    # ═══════════════════════════════════════════════
    async def _run_codex_session(self, user_message: str) -> AsyncGenerator[AgentEvent, None]:
        """
        Codex'i sohbet başına KALICI app-server session olarak sürer (claude muadili).
        - Bağlam turlar arası korunur (thread; DB geçmişi her turda prompt'a basılmaz).
        - Onay native: item/commandExecution|fileChange/requestApproval → command_gates
          → frontend onay kartı (Claude SDK yoluyla AYNI kartlar, yeni UI yok).
        - Abonelik (ChatGPT) auth — API key gerekmez.
        """
        from providers.codex_session import get_session, close_session, _SESSIONS
        from providers.effort_caps import map_effort as _map_effort

        # Effort kayıtçıdan (auto/desteklenmeyen → None → codex varsayılanı medium).
        # Launch-time config olduğundan effort DEĞİŞİNCE session yeniden kurulur
        # (Claude deseninin aynısı; thread bağlamı sıfırlanır, DB özeti yeniden gider).
        _lvl = self.effort_level or self.thinking_level or "auto"
        desired_effort = _map_effort("subscription", self.model_name or "gpt-",
                                     _lvl).get("cli_config", {}).get("model_reasoning_effort")
        _existing = _SESSIONS.get(self.conversation_id)
        _workspace = os.path.abspath(self.workspace_path or ".")
        _effort_changed = (
            _existing is not None
            and getattr(_existing, "effort", None) != desired_effort
        )
        _workspace_changed = (
            _existing is not None
            and os.path.abspath(getattr(_existing, "cwd", None) or ".") != _workspace
        )
        if _existing is not None and (_effort_changed or _workspace_changed):
            reasons = []
            if _effort_changed:
                reasons.append(f"effort {_existing.effort}→{desired_effort}")
            if _workspace_changed:
                reasons.append("workspace değişti")
            logger.info(
                "[CodexSession] %s; session yeniden kuruluyor",
                ", ".join(reasons),
            )
            await close_session(self.conversation_id)
            _existing = None

        if _existing is None:
            # Kalıcı app-server yolu, BaseCLIProvider.analyze_code üzerinden
            # geçmediği için eski tek-atımlık Codex yolundaki MCP kayıt yenilemesi
            # burada açıkça yapılmalı. Aksi halde global config silinmiş/önceki
            # workspace'i göstermeye devam eder ve tool call startup'ta düşer.
            from providers.codex_provider import CodexProvider
            _codex_provider = CodexProvider(binary_name=self.model_name or "codex")
            try:
                await asyncio.to_thread(
                    _codex_provider._write_mcp_config,
                    _workspace,
                )
            except Exception as exc:
                logger.exception("[CodexSession] MCP kaydı güncellenemedi")
                yield AgentEvent("error", {
                    "message": f"Codex MCP yapılandırması güncellenemedi: {exc}",
                })
                return

        session = get_session(
            self.conversation_id,
            model=self.model_name,
            cwd=_workspace,
            effort=desired_effort,
        )
        # Oto mod → onay otomatik accept; Adım/Plan modu → her mutasyonda onay kartı.
        session.auto_approve = (self.generation_mode == "auto")

        # /usage → canlı app-server'dan kullanım kartı metni (model turu YOK → sıfır token).
        # Ham user_message'a bakılır (bağlam wrapping'inden ÖNCE).
        if user_message.strip().lower() == "/usage":
            try:
                text = await session.usage_card_text()
            except Exception as e:
                text = f"Codex kullanım bilgisi alınamadı: {e}"
            yield AgentEvent("text", {"content": text})
            yield AgentEvent("response", {"content": text})
            yield _done_event(1, session_id=session.thread_id)
            return

        # İlk turda proje bağlamını ekle; sonraki turlarda thread zaten hatırlıyor.
        message = user_message
        if self.context and not session._ctx_injected:
            message = f"{user_message}\n\n{_HANDOFF_HEADER}\n{self.context}"
            session._ctx_injected = True
        if self.generation_mode == "auto":
            # Native requestApproval zaten otomatik kabul ediliyor. Bu kısa talimat
            # modelin ayrıca metin içinde "yapayım mı?" diye durmasını engeller.
            message = f"{message}\n\n{_CODEX_AUTO_MODE_INSTRUCTION}"

        # Görseller Codex'e native 'localImage' input item'ı olarak gider → dosya yolu
        # gerekiyor. Base64'leri tura özel temp klasörüne yaz; tur sonunda temizle.
        from providers._attachments import materialize_images, cleanup_dir
        image_paths, _att_dir = materialize_images(
            self.images, self.workspace_path, f"codex_conv{self.conversation_id}")
        from providers.oneshot_cli import CODEX_PLAN_ERROR_RE, QUOTA_ERROR_RE
        try:
            async for ev in session.stream(message, image_paths=image_paths):
                etype = ev.pop("type", "text")
                if etype == "error":
                    _msg = str(ev.get("message", ""))
                    if CODEX_PLAN_ERROR_RE.search(_msg):
                        # Codex'in account/plan sinyali tutarsız: hatayı açıkla fakat
                        # modeli kalıcı olarak kilitleme; sonraki turda yeniden denenebilir.
                        ev["message"] = (
                            f"Codex bu turda **{self.model_name}** modelini hesabın için "
                            f"kabul etmedi. Başka bir Codex modeli deneyebilir veya erişim "
                            f"yenilendiğinde bu modeli tekrar seçebilirsin.\n\n{_msg[:200]}")
                    elif QUOTA_ERROR_RE.search(_msg):
                        ev["message"] = (
                            "⏳ Codex kullanım hakkın dolmuş görünüyor (plan kotası). "
                            "Kota yenilenene kadar başka bir sağlayıcı seçebilirsin "
                            "(örn. NVIDIA ücretsiz havuzu veya OpenCode).\n\n" + _msg[:200])
                yield _normalize_session_event(etype, ev)
        except Exception as e:
            logger.exception("[CodexSession] stream hatası")
            # ⚠️ `redact_secrets` ŞART — gerekçe Claude yolundaki ikiziyle (bkz.
            # `_run_claude_session` istisna dalı) birebir aynı: bu metin SSE ile
            # tarayıcıya gidiyor ve oturum yapılandırması unityMCP `X-API-Key`'ini
            # taşıyor. Claude yolu bunu yapıyordu, Codex yolu YAPMIYORDU — yani
            # sınıf kapatılmış sanılıyordu ama yalnız raporun adını verdiği yol
            # kapanmıştı. İki yolu birden koruyan test:
            # `tests/test_session_hata_redaksiyonu.py`.
            from secret_redaction import redact_secrets as _redact
            yield AgentEvent("error", {"message": f"Codex session hatası: {_redact(str(e))}"})
        finally:
            cleanup_dir(_att_dir)

    def _summarize_result(self, tool_name: str, result: dict) -> str:
        """Tool sonucunu kısa özetle."""
        if not result.get("success"):
            return f"❌ {result.get('error', 'Bilinmeyen hata')}"

        if tool_name == "read_file":
            lines = result.get("total_lines", 0)
            trunc = " (kısaltıldı)" if result.get("truncated") else ""
            return f"📄 {result.get('path', '?')} — {lines} satır{trunc}"
        elif tool_name == "search_in_project":
            return f"🔍 '{result.get('query', '')}' — {result.get('total_matches', 0)} eşleşme"
        elif tool_name == "find_files":
            return f"📁 '{result.get('pattern', '')}' — {result.get('count', 0)} dosya"
        elif tool_name == "list_directory":
            return f"📂 {result.get('path', '')} — {result.get('count', 0)} öğe"
        elif tool_name == "write_file":
            return f"✏️ {result.get('path', '')} yazıldı"
        return "✅ Tamamlandı"
