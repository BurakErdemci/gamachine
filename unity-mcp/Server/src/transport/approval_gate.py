"""Onay kapısı — unityMCP mutasyon araçları kullanıcı onayı olmadan geçmez.

Bu modül K1'in boğazı. Gerekçesi ölçüldü (28–29 Tem 2026): 9 sağlayıcının
9'u da Unity'ye aynı uçtan (`127.0.0.1:8080/mcp`) gidiyor ve hiçbiri kendi
kapısını kurmuyor — Cursor `--trust --approve-mcps --force` ile, Copilot
`--allow-tool unityMCP` ile, Codex `default_tools_approval_mode="approve"` ile
geliyor. Ürünün kendi izin geri çağırımı yalnız Anthropic yolunu kapsıyordu,
yani altı sağlayıcıda mutasyon araçları onaysız çalışıyordu. Kapıyı buraya
koymanın sebebi bu: **tek boğaz, sağlayıcıdan bağımsız.**

Tasarımın iki kuralı:

1. **Politika burada DEĞİL.** Sunucu yalnız "bu çağrı yazma mı" sorusunu kütükten
   cevaplar ve yazma ise backend'e sorar. Modun (auto/plan/step) ne olduğunu,
   kartın kime gideceğini, oto-onay verilip verilmeyeceğini backend bilir. İki
   yerde politika tutmak, ikisinin zamanla ayrışması demektir.
2. **Fail-CLOSED.** Backend'e ulaşılamıyorsa (ürün kapalı, port kapalı, token
   yok) mutasyon REDDEDİLİR, okumalar çalışmaya devam eder. Kullanıcı kararı
   (29 Tem): reddedilen alternatif *"ürün yoksa kapıyı hiç kurma"* idi — o,
   güvenlik sınırını "uygulama açık mı" sorusuna bağlar ve kapı "ürünü kapat"
   denerek atlatılır.

⚠️ Sır ORTAMDAN okunuyor, dosyadan değil. Ürün token'ı zaten bu sürecin
ortamına koyuyor (`unity_mcp_manager.py` → `overrides={"LOCAL_APP_TOKEN": ...}`)
ve ürünün kendi doğruluk kaynağı da ortam (`main.py` onu dosyaya YAZAN taraf).
Sırrın ikinci bir okuyucusunu buraya yazmak, `local_token_file`'da sertleştirilen
bağ/junction/TOCTOU korumalarının sertleştirilmemiş bir kopyasını üretirdi.
Token yoksa backend kimliksiz çağrıyı reddeder ve kapı fail-closed davranır —
yani eksik token kabule değil REDDE dönüşür.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Mapping

import httpx

from services.registry.tool_actions import classify

logger = logging.getLogger("mcp-for-unity-server")

_BACKEND_URL = os.environ.get(
    "UNITYAI_URL", os.environ.get("ANTIGRAVITY_URL", "http://localhost:8000")
)

# Budgets are shared with Backend/app/unity_ai_mcp/approval_bridge.py (same
# endpoints) and bound the backend's card sweep (MCP_PENDING_TTL in
# Backend/app/routes/conversation_routes.py, "wait + margin"). This file's wait
# dropped to 150 s on 25 Sep 2026; until those two follow, a card this gate
# gave up on is withdrawn explicitly (_withdraw_card) rather than left to the
# sweep.
#
# ⚠️ Bütçeler DUVAR SAATİNE bağlı, deneme SAYISINA değil — ve fark 33 dakika.
# Denetim turu ölçtü (31 Tem 2026): "180 sn" diye yazılmış yoklama döngüsü,
# yanıt vermeyen ama bağlantı kabul eden bir backend'de her turda 0,5 sn uyku
# ÜSTÜNE 5 sn HTTP zaman aşımı harcıyor → 360 × 5,5 sn ≈ 1980 sn. O sürede
# çağrıyı bekleyen asistan turu da kilitli kalıyor. Sayıyla ifade edilen bir
# bütçe, adım başına gecikme değiştiğinde sessizce büyüyor; saatle ifade edilen
# büyümüyor.
_POST_BUTCESI = 10.0       # sn — ürün henüz açılıyorsa yetişsin
_BEKLEME_ADIMI = 0.5
# 150 s, not 180: agy cancels every MCP call at exactly 180 s ("timed out after
# 3m0s", not configurable) and tells the model it timed out. With 10 s of POST
# budget plus a 180 s card wait, an approval near the end was dispatched to
# Unity after agy had already given up, and the model's retry wrote twice
# (audit 25 Sep 2026). 10 + 150 = 160 s leaves Unity 20 s to run the call and
# answer inside agy's deadline. The cancel agy sends at 180 s is honoured too
# (see _onay_iste); this budget covers an approval that lands just before it.
_BEKLEME_BUTCESI = 150.0
# Withdrawing a card is best effort and must not hold up the refusal.
_GERI_CEKME_BUTCESI = 3.0

# Background withdrawals, referenced so the event loop cannot drop them early.
_pending_withdrawals: set[asyncio.Task] = set()


def parse_conversation_id(value: Any) -> int | None:
    """A positive int, or None for anything else.

    Strict on purpose: a card attributed to the wrong chat is worse than an
    unowned one, so " 7", "+7", "7.0", "٧" (int() accepts Unicode digits) and
    True are all refused rather than coerced.
    """
    if type(value) is int:
        return value if 0 < value < 2**63 else None
    # 2**63 - 1 has 19 digits. The length cap also keeps int() below its
    # 4300-digit limit, past which it raises ValueError instead of returning.
    if isinstance(value, str) and len(value) <= 19 and value.isascii() and value.isdigit():
        number = int(value)
        return number if 0 < number < 2**63 else None
    return None


class ApprovalDenied(RuntimeError):
    """The gate refused; the call does NOT run.

    FastMCP does not turn this into a tool error by itself (it is not a
    FastMCPError): the MCP path converts it to ToolError in
    UnityInstanceMiddleware.on_call_tool, /api/command answers 403.
    """


def _headers() -> dict[str, str]:
    token = os.environ.get("LOCAL_APP_TOKEN", "")
    if not token:
        logger.warning(
            "[approval-gate] LOCAL_APP_TOKEN ortamda yok — onay çağrısı kimliksiz "
            "gidiyor ve backend'in fail-closed kapısında reddedilecek."
        )
    return {"X-Session-Token": token} if token else {}


async def _withdraw_card(gate_id: str) -> None:
    """Take a card nobody is waiting for off the user's screen.

    The backend has no withdraw route; resolving the card as not approved
    through /mcp-approval-respond is what removes it from /mcp-pending, and a
    click that arrives later gets "gate_expired" instead of a false "ok".
    Without this the card stayed up to the backend's 200 s sweep, and an
    approval on it reported success while nothing ran.
    """
    try:
        async with httpx.AsyncClient(timeout=_GERI_CEKME_BUTCESI) as client:
            await client.post(
                f"{_BACKEND_URL}/mcp-approval-respond/{gate_id}",
                json={"approved": False},
                headers=_headers(),
            )
    except Exception as e:
        logger.warning("[approval-gate] could not withdraw card %s: %s", gate_id, e)


def _withdraw_card_in_background(gate_id: str) -> None:
    # A separate task, because the cancelled one cannot await anything more:
    # the MCP dispatcher cancels through an anyio scope, which re-raises on
    # every await inside it.
    task = asyncio.get_running_loop().create_task(_withdraw_card(gate_id))
    _pending_withdrawals.add(task)
    task.add_done_callback(_pending_withdrawals.discard)


async def _onay_iste(tool_name: str, params: Mapping[str, Any],
                     conversation_id: int | None = None) -> dict:
    """Backend'e sorar. Dönen sözlükte `approved` bool'u vardır.

    A client that gives up on the call cancels this coroutine: mcp 2.2 cancels
    the handler on `notifications/cancelled` in the session era (agy's) and on
    a closed response stream in 2026-07-28 (measured 25 Sep 2026, both eras:
    polling stops, nothing is dispatched). The card is then withdrawn so the
    user cannot approve a call that will never run.
    """
    gate_id = uuid.uuid4().hex[:10]
    try:
        return await _ask_and_wait(gate_id, tool_name, params, conversation_id)
    except asyncio.CancelledError:
        logger.info("[approval-gate] %s cancelled by the client; withdrawing card %s",
                    tool_name, gate_id)
        _withdraw_card_in_background(gate_id)
        raise


async def _ask_and_wait(gate_id: str, tool_name: str, params: Mapping[str, Any],
                        conversation_id: int | None = None) -> dict:
    govde: dict[str, Any] = {
        "gate_id": gate_id,
        "tool": tool_name,
        "params": dict(params or {}),
        # Workspace'i BİLEREK boş gönderiyoruz: bu süreç ürünün açtığı tek
        # sunucu ve workspace oturum ortasında değişebiliyor, yani başlangıçta
        # geçirilen bir değer bayat olurdu. Backend hangi workspace'in aktif
        # olduğunu kendi bilir.
        "workspace_path": "",
    }
    conversation_id = parse_conversation_id(conversation_id)
    if conversation_id is not None:
        # A claim, not a fact: the backend accepts it only for a chat with a
        # turn in flight and otherwise keeps the card unowned.
        govde["conversation_id"] = conversation_id

    gonderildi = False
    bitis = time.monotonic() + _POST_BUTCESI
    deneme = 0
    while time.monotonic() < bitis:
        deneme += 1
        try:
            # Zaman aşımı KALAN bütçeye kırpılıyor. Sınır tek başına yalnız yeni
            # bir denemenin BAŞLAMASINI engelliyordu; süren deneme kendi 8 sn'sini
            # harcayabildiği için "10 sn" fiilen ~18 sn olabiliyordu (denetim
            # bulgusu, 31 Tem 2026). 0,5 sn taban: sıfır zaman aşımı isteği
            # anında öldürür ve bütçenin son dilimini boşa harcardı.
            kalan = max(0.5, bitis - time.monotonic())
            async with httpx.AsyncClient(timeout=min(8.0, kalan)) as client:
                resp = await client.post(
                    f"{_BACKEND_URL}/mcp-approval-request",
                    json=govde,
                    headers=_headers(),
                )
            if resp.status_code == 200:
                veri = resp.json()
                if veri.get("status") == "resolved":
                    return veri
                gonderildi = True
                break
            # 401/503: kimlik ya da fail-closed kapı. Yeniden denemek bunu
            # değiştirmez, ve 10 sn boşuna beklemek kullanıcıyı bekletir.
            if resp.status_code in (401, 403):
                return {
                    "approved": False,
                    "error": f"Onay servisi kimliği reddetti (HTTP {resp.status_code}).",
                }
        except Exception as e:
            logger.warning("[approval-gate] POST %s başarısız: %s", deneme, e)
        # Bütçe dolduysa uyumuyoruz. Koşulsuz uyku, son denemenin ardından
        # ilan edilen süreye tam bir saniye ekliyordu (3. denetim turu).
        if time.monotonic() + 1.0 >= bitis:
            break
        await asyncio.sleep(1.0)

    if not gonderildi:
        return {
            "approved": False,
            "error": "Onay servisine ulaşılamadı; işlem güvenlik nedeniyle reddedildi.",
        }

    bitis = time.monotonic() + _BEKLEME_BUTCESI
    i = 0
    while time.monotonic() < bitis:
        i += 1
        await asyncio.sleep(_BEKLEME_ADIMI)
        try:
            # Kalan bütçeden fazlasını tek bir istekte harcamıyoruz: yanıt
            # vermeyen bir backend'de sabit 5 sn, bütçeyi adım adım aşan şeyin
            # ta kendisiydi.
            kalan = max(0.5, bitis - time.monotonic())
            async with httpx.AsyncClient(timeout=min(5.0, kalan)) as client:
                res = await client.get(
                    f"{_BACKEND_URL}/mcp-approval-result/{gate_id}",
                    headers=_headers(),
                )
            veri = res.json()
            if veri.get("status") != "pending":
                return veri
        except Exception as e:
            if i % 20 == 1:
                logger.warning("[approval-gate] sonuç yoklaması hatası: %s", e)

    await _withdraw_card(gate_id)
    return {
        "approved": False,
        "error": f"Onay zaman aşımına uğradı ({int(_BEKLEME_BUTCESI)} sn).",
    }


_BAKIM_BASLIGI = "X-UnityAI-Maintenance"


def urun_bakim_cagrisi_mi(request: Any) -> bool:
    """İstek, MODELİN değil ÜRÜNÜN kendi bakım işi mi?

    Kapının varlık sebebi *"kullanıcı habersizken proje değişmesin"*. Ürünün
    kendi bakım çağrısı (bugün tek örnek: workspace açılırken `manage_editor
    sync_csproj`) model kaynaklı değil — onu kartla sormak, kullanıcıya kendi
    tıklamasının sonucunu onaylatmak olurdu ve refleks-onaya alıştırırdı.

    İşaret `LOCAL_APP_TOKEN`: ürünün backend oturum sırrı. Seçilme sebebi
    `/api/command`'ın kendi paylaşılan sırrının YETMEMESİ — o sır `unity-mcp`
    CLI'ında da var, yani "bu çağrı üründen geldi" sorusunu ayırt etmiyor.

    ⚠️ Bunun NE OLMADIĞI yazılı olsun: bu bir güvenlik sınırı DEĞİL, bir KÖKEN
    işareti. Sırrı okuyabilen yerel bir süreç onu taklit edebilir — ama aynı
    süreç zaten backend'in `/mcp-approval-respond` ucuna gidip kendi kartını
    onaylayabilir, yani bu işaret yeni bir zayıflık AÇMIYOR. Tek kullanıcılı
    yerel bir makinede sırrı okuyabilmek ile kontrol sahibi olmak aynı şey.

    Token yoksa hiçbir istek bakım sayılmaz (fail-CLOSED): boş dize eşleşmesi,
    başlığı boş gönderen herkesi muaf yapardı.
    """
    token = os.environ.get("LOCAL_APP_TOKEN", "")
    if not token:
        return False
    try:
        gelen = request.headers.get(_BAKIM_BASLIGI, "")
    except Exception:
        return False
    return bool(gelen) and gelen == token


async def kapiyi_gec(
    tool_name: str,
    params: Mapping[str, Any] | None,
    hedef: str | None = None,
    conversation_id: int | None = None,
) -> None:
    """Yazma ise onay ister; onay yoksa `ApprovalDenied` fırlatır.

    Okuma araçlarında hiçbir ağ çağrısı yapılmaz — keşif araçları (hiyerarşi
    okuma, konsol okuma) her turda çağrılıyor ve onlara ağ maliyeti bindirmek
    kapıyı kullanıcının kapatmak isteyeceği bir şeye çevirirdi.

    Sınıflandırma kütükten geliyor (`tool_actions.json`, 45 araç) ve kütük
    FAIL-CLOSED: bilinmeyen araç ya da bilinmeyen action `write` sayılıyor.
    `batch_execute` gibi alt-çağrı taşıyan araçlarda `classify` özyineliyor,
    yani dış ada bakıp iç mutasyonu kaçırmıyor.
    """
    if classify(tool_name, params or {}) == "read":
        return

    # HEDEF karta yazılıyor. Sebebi bir denetim bulgusu (31 Tem 2026, med):
    # `_inject_unity_instance` yönlendirme argümanını mesajdan `pop` ediyor ve
    # kapı ondan SONRA koştuğu için kart hangi Unity projesinin değişeceğini
    # göstermiyordu. Birden fazla Editor bağlıyken kullanıcı "A projesinde"
    # sanıp onaylıyor, komut B'de koşuyordu.
    #
    # ⚠️ Bu, bu dosyada yazılı bir iddiayı da düzeltiyor: "kapı enjeksiyondan
    # sonra koşuyor, böylece gösterilen parametreler Unity'ye gidecek olanlarla
    # AYNI" deniyordu. Yanlıştı — `pop` edilen argüman tam olarak eksik olandı.
    gosterilecek = dict(params or {})
    if hedef:
        gosterilecek["unity_instance"] = hedef

    sonuc = await _onay_iste(tool_name, gosterilecek, conversation_id=conversation_id)
    # Doğruluk (truthiness) DEĞİL kimlik karşılaştırması. Ölçüldü (31 Tem 2026):
    # `bool("false")`, `bool("no")` ve `bool([0])` hepsi `True` dönüyor, yani
    # bozuk ya da sürüm-uyumsuz bir yanıt onay sayılıyordu. Bu dosyanın kendi
    # sözleşmesi "bozuk yanıt = RED" diyor; doğruluk onu sessizce çeviriyordu.
    if sonuc.get("approved") is True:
        if sonuc.get("automatic"):
            logger.info("[approval-gate] %s otomatik onaylandı (aktif auto turu).", tool_name)
        else:
            logger.info("[approval-gate] %s kullanıcı tarafından onaylandı.", tool_name)
        return

    sebep = sonuc.get("error") or "Kullanıcı onayı verilmedi."
    logger.warning("[approval-gate] %s REDDEDİLDİ: %s", tool_name, sebep)
    raise ApprovalDenied(
        f"'{tool_name}' onay gerektiriyor ve onaylanmadı: {sebep}"
    )
