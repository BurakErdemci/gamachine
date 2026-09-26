"""
Approval Bridge — MCP server'dan Antigravity backend'e onay isteği gönderir.

Akış:
  MCP tool çağrısı → approval_bridge → POST /mcp-approval-request (retry ile)
  → Frontend 1s polling ile yakalar → kullanıcı onaylar/reddeder
  → GET /mcp-approval-result/{gate_id} ile sonuç alınır
"""
import uuid
import asyncio
import logging
import time
import httpx
from typing import Any

import os
logger = logging.getLogger(__name__)
BACKEND_URL = os.environ.get("UNITYAI_URL", os.environ.get("ANTIGRAVITY_URL", "http://localhost:8000"))


def _get_headers() -> dict:
    """Backend çağrıları için auth başlığı; token yoksa boş dict (dev modu).

    Token artık ortamda GELMEYEBİLİR: bu süreci claude/codex gibi bir CLI
    başlatıyor ve bizim ortamımızı miras almıyor. Config dosyasına ya da argv'ye
    yazmak yerine 0600 bir dosyadan okunuyor — sebebi local_token_file'da.
    """
    import sys
    _app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _app not in sys.path:
        sys.path.insert(0, _app)
    from local_token_file import read_local_app_token

    token = read_local_app_token()
    if not token:
        logger.warning(
            "[approval_bridge] LOCAL_APP_TOKEN bulunamadı (ne ortamda ne dosyada) "
            "— backend çağrıları kimliksiz gidiyor ve fail-closed kapıda 503 alacak."
        )
    return {"X-Session-Token": token} if token else {}


def _conversation_id_from_env() -> int | None:
    """The chat this bridge process was spawned for, from GAMACHINE_CONVERSATION_ID.

    Only a plain positive integer counts; anything else sends no owner, since
    a card attributed to the wrong chat is worse than an unowned one.
    """
    raw = os.environ.get("GAMACHINE_CONVERSATION_ID", "")
    if not (raw.isascii() and raw.isdigit()):
        return None
    value = int(raw)
    return value if 0 < value < 2**63 else None


# ⚠️ ÇAĞIRANLAR İÇİN: dönen sözlükteki `approved` GERÇEK `True` mi diye bakın,
# doğruluk (truthiness) ile değil. Ölçüldü (31 Tem 2026): `bool("false")`,
# `bool("no")` ve `bool([0])` hepsi `True`. Bozuk ya da sürüm-uyumsuz bir yanıt
# böylece onay sayılıyordu.
#
# Bu not burada, çünkü sınıfın SEKİZ yazımı vardı ve ilk düzeltme yalnız üçünü
# kapatmıştı; kalan beşi (file_tools, bash_tool, unityai_cli) doğrulama turunda
# bulundu. Kuralı tek bir çağrı yerine yazmak, sonraki çağıranın onu görmemesi
# demek — o yüzden kaynağın kendisine yazıldı.
async def request_approval(
    tool_name: str,
    params: dict[str, Any],
    workspace_path: str,
) -> dict:
    """
    Tehlikeli bir tool çağrısı için kullanıcı onayı ister.
    Backend'e ulaşana kadar 10 saniye boyunca retry yapar.
    Ulaşabilirse 60 saniye kullanıcı cevabını bekler.
    """
    gate_id = uuid.uuid4().hex[:10]
    logger.info(f"[approval_bridge] {tool_name} için onay isteniyor (gate: {gate_id})")

    # POST — 10 saniyelik DUVAR SAATİ bütçesi, 1 sn aralıkla retry.
    #
    # ⚠️ İlk düzeltmede yalnız aşağıdaki yoklama döngüsü saate bağlanmıştı; bu
    # faz `range(10)` olarak kalmıştı ve her deneme kendi 8 sn'sini + 1 sn
    # uykusunu harcayabildiği için "10 sn" fiilen ~90 SANİYE olabiliyordu — ve
    # bu, 180 sn'lik kullanıcı beklemesi daha BAŞLAMADAN önce. Aynı sınıfın
    # yarısını düzeltip diğer yarısını bırakmak, düzeltilmiş sanılan bir
    # gecikme üretiyordu (3. denetim turu, 31 Tem 2026).
    _POST_BUTCESI = 10.0
    body = {
        "gate_id": gate_id,
        "tool": tool_name,
        "params": params,
        "workspace_path": workspace_path,
        "approval_turn_token": os.environ.get("UNITYAI_APPROVAL_TURN_TOKEN", ""),
    }
    conversation_id = _conversation_id_from_env()
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    posted = False
    immediate_result = None
    post_bitis = time.monotonic() + _POST_BUTCESI
    attempt = -1
    while time.monotonic() < post_bitis:
        attempt += 1
        try:
            kalan = max(0.5, post_bitis - time.monotonic())
            async with httpx.AsyncClient(timeout=min(8.0, kalan)) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/mcp-approval-request",
                    json=body,
                    headers=_get_headers(),
                )
                if resp.status_code == 200:
                    posted = True
                    data = resp.json()
                    if data.get("status") == "resolved":
                        immediate_result = data
                    logger.info(f"[approval_bridge] POST başarılı (deneme {attempt+1})")
                    break
        except Exception as e:
            logger.warning(f"[approval_bridge] POST denemesi {attempt+1} başarısız: {e}")
            # Bütçe dolduysa uyumuyoruz: son denemenin ardından gelen uyku,
            # ilan edilen süreyi tam bir saniye aşan kuyruk oluyordu.
            if time.monotonic() + 1.0 >= post_bitis:
                break
            await asyncio.sleep(1.0)

    if not posted:
        logger.warning(f"[approval_bridge] Backend'e ulaşılamadı — {tool_name} reddediliyor")
        return {
            "approved": False,
            "error": "Onay servisine ulaşılamadı; işlem güvenlik nedeniyle reddedildi.",
        }

    if immediate_result is not None:
        logger.info(
            f"[approval_bridge] Aktif Auto turunda anında çözüldü (gate: {gate_id})"
        )
        return immediate_result

    # Kullanıcı cevabını bekle — bütçe DUVAR SAATİNE bağlı, deneme sayısına
    # değil. Eskiden `for i in range(360)` idi ve "180 sn" diye yazılıydı; ama
    # her tur 0,5 sn uykuya EK olarak 5 sn'ye kadar HTTP zaman aşımı
    # harcayabildiği için, bağlantıyı kabul edip yanıt vermeyen bir backend'de
    # 360 × 5,5 ≈ 1980 sn (33 dakika) sürüyordu — ve o sürede çağrıyı bekleyen
    # asistan turu da kilitli kalıyordu. Ölçüm ve sınıf denetim turundan
    # (31 Tem 2026); kardeş istemci `unity-mcp/Server/src/transport/
    # approval_gate.py` aynı anda aynı şekilde düzeltildi. İkisinin bütçesi
    # BİLEREK aynı: aynı kartı bekleyen iki istemciden birinin erken pes etmesi,
    # kartın bir tarafta yaşayıp diğerinde ölmesi demek.
    # 150 s, not 180: agy cancels every MCP call at exactly 180 s and its timeout
    # cannot be configured, so an approval near 180 s would run after agy had
    # already told the model "timed out" (measured 25 Sep 2026, commit d6c2232).
    _BEKLEME_BUTCESI = 150.0
    logger.info(f"[approval_bridge] Kullanıcı cevabı bekleniyor (gate: {gate_id})")
    bitis = time.monotonic() + _BEKLEME_BUTCESI
    i = 0
    while time.monotonic() < bitis:
        i += 1
        await asyncio.sleep(0.5)
        try:
            kalan = max(0.5, bitis - time.monotonic())
            async with httpx.AsyncClient(timeout=min(5.0, kalan)) as client:
                res = await client.get(f"{BACKEND_URL}/mcp-approval-result/{gate_id}", headers=_get_headers())
                data = res.json()
                if data.get("status") != "pending":
                    logger.info(f"[approval_bridge] Cevap alındı: {data}")
                    return data
        except Exception as e:
            if i % 10 == 1:  # Her ~5 saniyede bir logla
                logger.warning(f"[approval_bridge] Polling hatası: {e}")
            continue

    logger.warning(f"[approval_bridge] Zaman aşımı (gate: {gate_id})")
    return {"approved": False, "error": f"Zaman aşımı ({int(_BEKLEME_BUTCESI)}s)"}
