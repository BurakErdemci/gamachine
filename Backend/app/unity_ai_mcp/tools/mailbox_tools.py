"""Chat mailbox tools for the MCP servers (unityai and the Claude mail server).

The tools only carry the call: the backend's `/mailbox/*` routes decide
everything (who may send, the card, the limits) and word the answer, so this
file imports nothing from `agentic` - the mail server stays a light process.
The sending chat comes from GAMACHINE_CONVERSATION_ID, never from the model.
"""
import asyncio
import logging
import time

import httpx
from mcp.server.mcpserver import MCPServer

from unity_ai_mcp.approval_bridge import BACKEND_URL, _conversation_id_from_env, _get_headers

logger = logging.getLogger(__name__)

# Same budget as approval_bridge: agy cuts every MCP call at 180 s.
_CARD_WAIT_S = 150.0
_POLL_S = 0.5

_NO_CHAT = "Bu araç yalnız bir Gamachine sohbetinin içinden kullanılabilir."
_UNREACHABLE = "Gamachine'e ulaşılamadı; not gönderilmedi."


def _detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = None
    return str(detail or f"HTTP {resp.status_code}")


async def list_chats() -> str:
    conversation_id = _conversation_id_from_env()
    params = {"conversation_id": conversation_id} if conversation_id is not None else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{BACKEND_URL}/mailbox/chats", params=params,
                                    headers=_get_headers())
    except Exception as e:
        logger.warning("[mailbox] list failed: %s", e)
        return "Gamachine'e ulaşılamadı; sohbet listesi alınamadı."
    if resp.status_code != 200:
        return f"Sohbet listesi alınamadı: {_detail(resp)}"
    return str(resp.json().get("text") or "Başka sohbet yok.")


async def send_chat_message(to_chat_id: int, message: str) -> str:
    conversation_id = _conversation_id_from_env()
    if conversation_id is None:
        return _NO_CHAT
    headers = _get_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{BACKEND_URL}/mailbox/send", headers=headers,
                json={"conversation_id": conversation_id, "to": to_chat_id, "body": message})
    except Exception as e:
        logger.warning("[mailbox] send failed: %s", e)
        return _UNREACHABLE
    if resp.status_code != 200:
        return f"Not gönderilmedi: {_detail(resp)}"
    data = resp.json()
    if data.get("status") != "pending":
        return str(data.get("message") or data.get("status"))

    # Step mode: the card is in the sender's chat; the backend applies the
    # decision itself, this only waits to report it.
    mail_id = data.get("mail_id")
    deadline = time.monotonic() + _CARD_WAIT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_S)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{BACKEND_URL}/mailbox/status/{mail_id}",
                                        params={"conversation_id": conversation_id},
                                        headers=headers)
            if resp.status_code == 404:
                # The row went with a deleted chat.
                return f"Not gönderilmedi: {_detail(resp)}"
            status = resp.json()
        except Exception:
            continue
        if status.get("status") != "pending_approval":
            return str(status.get("message") or status.get("status"))
    # Deny the card, so an answer after this point cannot send a note the
    # model was told was not sent.
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{BACKEND_URL}/mailbox/cancel/{mail_id}",
                                     json={"conversation_id": conversation_id}, headers=headers)
        final = resp.json()
        if final.get("status") in ("queued", "delivered"):
            return str(final.get("message") or final.get("status"))
    except Exception:
        pass
    return f"Not gönderilmedi: onay {int(_CARD_WAIT_S)} saniye içinde gelmedi."


def register_mailbox_tools(mcp: MCPServer) -> None:
    @mcp.tool(name="list_chats")
    async def list_chats_tool() -> str:
        """Kullanıcının bu uygulamadaki diğer sohbetlerini listeler (numara, başlık, dal mı, çalışıyor mu).
        Dallar da geçerli hedeftir. Kullanıcı hedefi `@<numara>` ile andıysa o numarayı kullan.
        Aynı ya da benzer başlıklı sohbetler arasında tahmin etme; kullanıcıdan hedefi `@` ile
        seçmesini iste."""
        return await list_chats()

    @mcp.tool(name="send_chat_message")
    async def send_chat_message_tool(to_chat_id: int, message: str) -> str:
        """Kullanıcının başka bir sohbetine ya da dalına (başka bir AI oturumuna) not bırakır. O sohbet
        boştaysa notla hemen uyanır, çalışıyorsa turu bitince okur. Adım modunda kullanıcı önce onaylar.
        to_chat_id: kullanıcı `@<numara>` ile andıysa o numara; yoksa list_chats'te tek bir sohbet
        açıkça uyuyorsa onun numarası. Emin değilsen gönderme, kullanıcıdan hedefi `@` ile seçmesini iste.
        Yalnız gerçekten o sohbetin bilmesi gereken bir şey için kullan (en fazla 4000 karakter)."""
        return await send_chat_message(to_chat_id, message)
