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
from typing import Any, Dict, Iterable, List, Optional, Tuple

import action_risk

logger = logging.getLogger(__name__)

FRAME_TYPE = "chat_file"
MAX_BYTES = 20 * 1024 * 1024
# Width x height above which an image is refused: a 137 KiB PNG of 12000x12000
# needed 1.4 GB to re-encode (measured 4 Nov 2026).
MAX_PIXELS = 40_000_000
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
    "not_image": "Dosya png, jpg, gif ya da webp bir görüntü değil (ya da bozuk).",
    "too_many_pixels": "Görüntü çok büyük (40 megapikselden fazla); gösterilemez.",
    "gone": "Sohbet artık yok.",
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
        # The descriptor must be the file that was checked, not one swapped in since.
        checked = os.stat(real)
        if (info.st_dev, info.st_ino) != (checked.st_dev, checked.st_ino):
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


def _check_decodes(data: bytes) -> Optional[str]:
    """None when the bytes are a whole image of a sane size, else a refusal code.
    The size comes from the header, before anything is decoded."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                return "too_many_pixels"
            img.verify()
        # verify() leaves the image unusable and does not read the pixel data.
        with Image.open(io.BytesIO(data)) as img:
            img.load()
    except Image.DecompressionBombError:
        return "too_many_pixels"
    except Exception:
        return "not_image"
    return None


def markdown(record: Dict[str, Any]) -> str:
    return f"![{record['caption']}](gamachine-file:{record['id']})"


def add_file(db: Any, conversation_id: int, src_path: Any, caption: Any = "",
             post: bool = False) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Check and copy an image into the chat's folder: (record, None), or (None, refusal code).

    With `post` the chat's message `![caption](gamachine-file:id)` is written in the
    same transaction as the row, after checking that the chat still exists ("gone"
    if not), so a deleted chat cannot get a row back and `prune_unreferenced` cannot
    take this image before its message exists. The record then carries `message_id`
    and `created_at`.
    """
    data, reason = _read_source(src_path)
    if reason:
        return None, reason
    mime = sniff_mime(data[:16])
    if mime is None:
        return None, "not_image"
    reason = _check_decodes(data)
    if reason:
        return None, reason
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
                conn.execute("BEGIN IMMEDIATE")
                if post and conn.execute("SELECT 1 FROM conversations WHERE id = ?",
                                         (record["conversation_id"],)).fetchone() is None:
                    return None, "gone"
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
                        if post:
                            record["message_id"], record["created_at"] = db.insert_message_in(
                                conn, record["conversation_id"], "assistant", markdown(record))
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


_REF = re.compile(r"gamachine-file:([A-Za-z0-9-]{8,64})")
_COLUMNS = ("id", "conversation_id", "name", "caption", "mime", "size", "created")


def copy_for_branch(conn: sqlite3.Connection, db_path: str, source_id: int, new_id: int,
                    made: List[str]) -> None:
    """Give the branch `new_id` its own copy of every image its copied messages
    show (new id, new file, markdown rewritten, rows counted in its quota), so
    deleting the source chat cannot take the branch's pictures. Runs in the
    caller's transaction; the files made are appended to `made`, for the caller
    to remove if that transaction fails. An image whose file is gone keeps its
    old id."""
    fresh: Dict[str, str] = {}
    rows = conn.execute("SELECT id, content FROM messages WHERE conversation_id = ? "
                        "AND content LIKE '%gamachine-file:%'", (new_id,)).fetchall()

    def swap(match):
        old = match.group(1)
        if old not in fresh:
            fresh[old] = _clone(conn, db_path, source_id, new_id, old, made) or old
        return "gamachine-file:" + fresh[old]

    for message_id, content in rows:
        rewritten = _REF.sub(swap, content or "")
        if rewritten != content:
            conn.execute("UPDATE messages SET content = ? WHERE id = ?", (rewritten, message_id))


def _clone(conn: sqlite3.Connection, db_path: str, source_id: int, new_id: int,
           old: str, made: List[str]) -> Optional[str]:
    row = conn.execute("SELECT " + ", ".join(_COLUMNS) + " FROM chat_files WHERE id = ? AND conversation_id = ?",
                       (old, source_id)).fetchone()
    if row is None:
        return None
    record = dict(zip(_COLUMNS, row))
    src = file_path(db_path, record)
    record["id"], record["conversation_id"] = str(uuid.uuid4()), int(new_id)
    dst = file_path(db_path, record)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    except OSError as e:
        # Keeping the source's id would leave the branch pointing at a file its
        # source owns, gone with that chat's next delete or compaction. Fail the
        # branch instead; create_branch rolls back and removes the copies made.
        logger.warning("[chat_files] branch copy of %s failed: %s", old, e)
        raise
    made.append(dst)
    conn.execute("INSERT INTO chat_files (" + ", ".join(_COLUMNS) + ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                 tuple(record[c] for c in _COLUMNS))
    return record["id"]


def discard_files(paths: Iterable[str]) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def prune_unreferenced(db_path: str, conversation_id: int) -> None:
    """Delete the chat's images that none of its messages shows any more (a
    compaction removes the messages, not the files). Failures are logged."""
    gone: List[str] = []
    try:
        with _lock, closing(sqlite3.connect(db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            shown = set()
            for (content,) in conn.execute("SELECT content FROM messages WHERE conversation_id = ? "
                                           "AND content LIKE '%gamachine-file:%'", (conversation_id,)):
                shown.update(_REF.findall(content or ""))
            rows = conn.execute("SELECT " + ", ".join(_COLUMNS) + " FROM chat_files WHERE conversation_id = ?",
                                (conversation_id,)).fetchall()
            with conn:
                for row in rows:
                    if row[0] not in shown:
                        conn.execute("DELETE FROM chat_files WHERE id = ?", (row[0],))
                        gone.append(file_path(db_path, dict(zip(_COLUMNS, row))))
    except (OSError, sqlite3.Error) as e:
        logger.warning("[chat_files] prune failed: %s", e)
    discard_files(gone)


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


def _to_8bit(img: Any) -> Any:
    """16-bit and float grayscale to 8-bit `L`; `convert("RGB")` alone clips them to white."""
    if img.mode.startswith("I;16"):
        return img.point(lambda v: v * (1 / 256)).convert("L")
    if img.mode in ("I", "F"):
        low, high = img.getextrema()
        if low >= 0 and high <= 255:
            return img.convert("L")
        scale = 1 / 256 if img.mode == "I" and low >= 0 and high <= 65535 else 255 / max(high - low, 1)
        offset = 0 if scale == 1 / 256 else -low * scale
        return img.point(lambda v: v * scale + offset).convert("L")
    return img


def _shrink(data: bytes, max_bytes: int) -> Optional[bytes]:
    """The image as a JPEG of at most `max_bytes`, trying smaller sides and lower
    quality in turn; None when none fits (or the image cannot be decoded)."""
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                return None
            img.seek(0)
            if img.format == "JPEG":
                # The decoder reads it at a fraction of the size when that is enough.
                img.draft("RGB", (1600, 1600))
            img.load()
            small = ImageOps.exif_transpose(_to_8bit(img))
            small.thumbnail((1600, 1600))
            if small.mode in ("RGBA", "LA", "P", "PA"):
                rgba = small.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.getchannel("A"))
            else:
                flat = small.convert("RGB")
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
