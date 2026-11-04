"""`show_image`: lets an agent show the user an image file inside the chat.

Only carries the call: the backend's `POST /chat-files` checks the file, keeps a
copy for the chat and adds the picture to it, and words every refusal. The chat
comes from GAMACHINE_CONVERSATION_ID, never from the model. Like the mailbox
tools, this file imports nothing from `agentic`, so the mail server stays light.
"""
import logging

import httpx
from mcp.server.mcpserver import MCPServer

from spawn_env import read_only_refusal
from unity_ai_mcp.approval_bridge import BACKEND_URL, _conversation_id_from_env, _get_headers
from unity_ai_mcp.tools.mailbox_tools import _NO_CHAT, _detail

logger = logging.getLogger(__name__)

SHOWN = "Gösterildi."
_UNREACHABLE = "Gamachine'e ulaşılamadı; görüntü gösterilemedi."


async def post_show_image(conversation_id: int, path: str, caption: str = "") -> str:
    """Ask the backend to show the image in that chat; the answer is what the model is told."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{BACKEND_URL}/chat-files", headers=_get_headers(),
                json={"conversation_id": conversation_id, "path": path, "caption": caption})
    except Exception as e:
        logger.warning("[show_image] failed: %s", e)
        return _UNREACHABLE
    return SHOWN if resp.status_code == 200 else _detail(resp)


async def show_image(path: str, caption: str = "") -> str:
    if (refusal := read_only_refusal()) is not None:
        return refusal
    conversation_id = _conversation_id_from_env()
    if conversation_id is None:
        return _NO_CHAT
    return await post_show_image(conversation_id, path, caption)


def register_show_tools(mcp: MCPServer) -> None:
    @mcp.tool(name="show_image")
    async def show_image_tool(path: str, caption: str = "") -> str:
        """Bir görüntü dosyasını kullanıcıya bu sohbetin içinde gösterir (oyun ekran görüntüsü, ürettiğin efekt vb.).
        path: dosyanın tam (mutlak) yolu; png, jpg, gif ya da webp, en fazla 20 MB.
        caption: görüntünün altında görünecek kısa açıklama (isteğe bağlı).
        Kullanıcıya görüntü göstermek için dosya yolunu metne yazmak ya da başka bir yere yüklemek yerine bunu kullan."""
        return await show_image(path, caption)
