"""Images an agent shows the user in a chat (the `show_image` tool).

The tool names a file on disk; this module checks it and keeps a COPY under
<data dir>/chat_files/<conversation_id>/<id>.<ext>, so the picture survives the
agent's temp cleanup and a reload, and goes away with the chat. The data dir is
the folder of the database (`db.db_path`), the same base the Fernet key uses.

There is no folder restriction: the tool call is the agent's explicit act. What
is refused is anything that is not a plain image file: a relative, UNC or device
path, a Gamachine secret file, a linked or special file, a file over 20 MiB, and
a file whose first bytes are not PNG/JPEG/GIF/WEBP (the extension is not trusted).
"""
import io
import logging
import ntpath
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import closing
from typing import Any, Dict, Iterable, Optional, Tuple

import action_risk

logger = logging.getLogger(__name__)

FRAME_TYPE = "chat_file"
MAX_BYTES = 20 * 1024 * 1024
QUOTA_BYTES = 300 * 1024 * 1024
CAPTION_MAX = 200
FOLDER = "chat_files"

# Refusal code -> what the agent is told (Turkish, like the tool's other answers).
REASONS = {
    "bad_path": "Dosya yolu mutlak ve yerel bir yol olmalı.",
    "secret": "Bu dosya Gamachine'in gizli dosyalarından biri; gösterilemez.",
    "not_found": "Dosya bulunamadı ya da okunamadı.",
    "not_file": "Bu bir düz dosya değil (bağlantı ya da özel dosya).",
    "too_large": "Dosya 20 MB'tan büyük; gösterilemez.",
    "not_image": "Dosya png, jpg, gif ya da webp bir görüntü değil.",
    "quota": "Bu sohbetin görüntü kotası (300 MB) dolu.",
    "failed": "Görüntü saklanamadı.",
}

_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
_lock = threading.Lock()


def sniff_mime(head: bytes) -> Optional[str]:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def clean_caption(caption: Any) -> str:
    """One line without brackets or backslashes, so it cannot break the markdown
    image `![caption](gamachine-file:id)` it is written into."""
    text = caption if isinstance(caption, str) else ""
    for ch in "[]\\":
        text = text.replace(ch, " ")
    return " ".join(text.split())[:CAPTION_MAX].strip()


def _root(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), FOLDER)


def _folder(db_path: str, conversation_id: int) -> str:
    return os.path.join(_root(db_path), str(int(conversation_id)))


def file_path(db_path: str, record: Dict[str, Any]) -> str:
    return os.path.join(_folder(db_path, record["conversation_id"]), record["id"] + _EXT[record["mime"]])


def _is_local_path(path: str) -> bool:
    """A plain absolute path on a local disk, decided from the text alone.

    Mixed slashes such as `\\/host/share` or `/\\host\\share` passed a
    starts-with check yet Windows opens them as UNC paths, and touching a UNC
    path sends the user's NTLM hash to that host (measured 4 Nov 2026). So the
    drive part must be exactly one letter, after normalising the slashes, and a
    mapped network drive is refused too.
    """
    if os.name != "nt":
        return path.startswith("/") and not path.startswith("//")
    norm = path.replace("/", "\\")
    drive, rest = ntpath.splitdrive(norm)
    if not re.fullmatch(r"[A-Za-z]:", drive) or not rest.startswith("\\"):
        return False
    try:
        import ctypes
        # DRIVE_REMOTE = 4: a letter mapped to a network share.
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") != 4
    except Exception:
        return False


def _read_source(path: Any) -> Tuple[Optional[bytes], Optional[str]]:
    """The source's bytes, or a refusal code. The file is opened once and every
    check runs on that descriptor, so the path cannot be swapped in between."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return None, "bad_path"
    path = path.strip()
    # Before any call that touches the file system (the secret check stats the path).
    if not _is_local_path(path):
        return None, "bad_path"
    try:
        if action_risk.gamachine_secret_target(path):
            return None, "secret"
        real = os.path.realpath(path)
        # A junction or symlink can still lead to a share: judge where it resolved to.
        if not _is_local_path(real):
            return None, "bad_path"
        if action_risk.gamachine_secret_target(real):
            return None, "secret"
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(real, flags)
    except OSError:
        return None, "not_found"
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            return None, "not_file"
        if info.st_size > MAX_BYTES:
            return None, "too_large"
        with os.fdopen(fd, "rb", closefd=False) as src:
            data = src.read(MAX_BYTES + 1)
    except OSError:
        return None, "not_found"
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        return None, "too_large"
    return data, None


def add_file(db: Any, conversation_id: int, src_path: Any, caption: Any = "") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Check and copy an image into the chat's folder: (record, None), or (None, refusal code)."""
    data, reason = _read_source(src_path)
    if reason:
        return None, reason
    mime = sniff_mime(data[:16])
    if mime is None:
        return None, "not_image"
    record = {
        "id": str(uuid.uuid4()), "conversation_id": int(conversation_id),
        "name": os.path.basename(src_path.strip().replace("\\", "/"))[:200],
        "caption": clean_caption(caption), "mime": mime, "size": len(data), "created": time.time(),
    }
    folder = _folder(db.db_path, conversation_id)
    final = file_path(db.db_path, record)
    temp = os.path.join(folder, record["id"] + ".tmp")
    # One writer at a time, so two images cannot both pass the quota check.
    with _lock:
        try:
            with closing(sqlite3.connect(db.db_path)) as conn:
                used = conn.execute("SELECT COALESCE(SUM(size), 0) FROM chat_files WHERE conversation_id = ?",
                                    (record["conversation_id"],)).fetchone()[0]
                if used + record["size"] > QUOTA_BYTES:
                    return None, "quota"
                os.makedirs(folder, exist_ok=True)
                with open(temp, "wb") as out:
                    out.write(data)
                os.replace(temp, final)
                try:
                    with conn:
                        conn.execute(
                            "INSERT INTO chat_files (id, conversation_id, name, caption, mime, size, created) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (record["id"], record["conversation_id"], record["name"], record["caption"],
                             record["mime"], record["size"], record["created"]))
                except sqlite3.Error:
                    os.unlink(final)
                    raise
        except (OSError, sqlite3.Error) as e:
            logger.warning("[chat_files] store failed: %s", e)
            for leftover in (temp, final):
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
            return None, "failed"
    return record, None


def get_file(db: Any, file_id: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(file_id, str):
        return None
    with closing(sqlite3.connect(db.db_path)) as conn:
        row = conn.execute(
            "SELECT id, conversation_id, name, caption, mime, size, created FROM chat_files WHERE id = ?",
            (file_id,)).fetchone()
    if row is None:
        return None
    return dict(zip(("id", "conversation_id", "name", "caption", "mime", "size", "created"), row))


def _shrink(data: bytes, max_bytes: int) -> Optional[bytes]:
    """The image as a JPEG of at most `max_bytes`, trying smaller sides and lower
    quality in turn; None when none fits (or the image cannot be decoded)."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.seek(0)
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.getchannel("A"))
            else:
                flat = img.convert("RGB")
        for side in (1600, 1200, 900):
            small = flat.copy()
            small.thumbnail((side, side))
            for quality in (85, 70):
                out = io.BytesIO()
                small.save(out, "JPEG", quality=quality)
                if out.tell() <= max_bytes:
                    return out.getvalue()
    except Exception as e:
        logger.warning("[chat_files] re-encode failed: %s", e)
    return None


def read_image(db: Any, record: Dict[str, Any], max_bytes: Optional[int] = None) -> Tuple[Optional[bytes], str]:
    """(bytes, mime) of a stored image; with `max_bytes` smaller than the file,
    a re-encoded JPEG that fits, else (None, mime) for "does not fit"."""
    try:
        with open(file_path(db.db_path, record), "rb") as f:
            data = f.read()
    except OSError:
        return None, record["mime"]
    if max_bytes is None or len(data) <= max_bytes:
        return data, record["mime"]
    return _shrink(data, max_bytes), "image/jpeg"


def remove_folders(db_path: str, conversation_ids: Iterable[int]) -> None:
    """Delete the picture folders of deleted chats. Only a folder this module made
    (a number under chat_files/) is touched; a failure is logged, never raised."""
    root = _root(db_path)
    for cid in conversation_ids:
        folder = _folder(db_path, cid)
        if os.path.dirname(folder) != root or not os.path.isdir(folder):
            continue
        try:
            shutil.rmtree(folder)
        except OSError as e:
            logger.warning("[chat_files] could not remove %s: %s", folder, e)
