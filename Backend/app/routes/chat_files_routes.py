"""Images an agent shows in a chat: the `show_image` tool posts here, the
renderer (desktop or paired browser) reads the bytes back."""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response

import chat_files
from auth_utils import _check_token, require_conversation_owner
from remote import desktop_channel

logger = logging.getLogger(__name__)


def create_chat_files_router(db):
    router = APIRouter()

    @router.post("/chat-files")
    async def add_chat_file(body: dict, x_session_token: str = Header(alias="X-Session-Token", default="")):
        conv_id = body.get("conversation_id")
        if type(conv_id) is not int:
            raise HTTPException(status_code=422, detail="conversation_id gerekli")
        require_conversation_owner(db, x_session_token, conv_id)
        # A side chat is read-only by design: it shows nothing to the user.
        if db.get_side_of(conv_id) is not None:
            raise HTTPException(status_code=403, detail="Yan sohbet salt okunur; görüntü gösterilemez.")
        record, reason = await asyncio.to_thread(
            chat_files.add_file, db, conv_id, body.get("path"), body.get("caption"), True)
        if record is None:
            # The chat was deleted while the image was being stored: nothing is kept.
            raise HTTPException(status_code=404 if reason == "gone" else 400, detail=chat_files.REASONS[reason])
        content = chat_files.markdown(record)
        message_id, created_at = record["message_id"], record["created_at"]
        # Owner-checked per stream in /wake-stream-all; no renderer listening is
        # fine, the row is stored and shows on the next read.
        desktop_channel.CHANNEL.publish({
            "type": chat_files.FRAME_TYPE, "conversation_id": conv_id,
            "message": {"id": message_id, "role": "assistant", "content": content, "created_at": created_at}})
        return {"id": record["id"], "message_id": message_id}

    @router.get("/chat-files/{file_id}")
    async def get_chat_file(file_id: str, max_bytes: Optional[int] = Query(default=None, ge=1),
                            x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        record = chat_files.get_file(db, file_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Görüntü bulunamadı")
        require_conversation_owner(db, x_session_token, record["conversation_id"])
        data, mime = await asyncio.to_thread(chat_files.read_image, db, record, max_bytes)
        if data is None:
            if max_bytes is not None:
                raise HTTPException(status_code=413, detail="Görüntü istenen boyuta sığmıyor")
            raise HTTPException(status_code=404, detail="Görüntü bulunamadı")
        return Response(content=data, media_type=mime, headers={
            "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    return router
