"""`show_image`: an agent shows the user an image file in the chat.

Contract under test: chat_files.add_file keeps a copy of a plain PNG/JPEG/GIF/WEBP
(by its first bytes, not its name) in the data dir's chat_files/<chat>/ and refuses
everything else with a reason code (secret file, text renamed .png, over 20 MiB, a
hard-linked file, a UNC or relative path, a full per-chat quota); deleting a chat
takes its rows and folder; POST /chat-files adds an assistant message with a
`gamachine-file:` image and puts a frame on the owner's /wake-stream-all only;
GET /chat-files/<id> is owner-checked and can re-encode under ?max_bytes; the
tool calls the route with the chat id from its environment.
"""
import asyncio
import io
import json
import os
import sqlite3
import types
from contextlib import closing

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import chat_files
import routes.conversation_routes as cr
from agentic import wake_queue
from database import DatabaseManager
from remote import desktop_channel
from routes.chat_files_routes import create_chat_files_router
from unity_ai_mcp.tools import show_tools

H = {"X-Session-Token": ""}
UNC = chr(92) * 2 + "server" + chr(92) + "share" + chr(92) + "a.png"


def _image(fmt, size=(8, 8), color=(200, 30, 30)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()


def _noise_png(side=1200):
    out = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    (tmp_path / "data").mkdir()
    db = DatabaseManager(str(tmp_path / "data" / "cf.db"))
    monkeypatch.setenv("DB_PATH", db.db_path)
    wake_queue.reset_all()
    app = FastAPI()
    file_router = create_chat_files_router(db)
    app.include_router(file_router)
    src = tmp_path / "src"
    src.mkdir()
    with TestClient(app) as client:
        yield types.SimpleNamespace(db=db, client=client, app=app, router=file_router, src=src,
                                    root=os.path.join(str(tmp_path / "data"), "chat_files"))
    wake_queue.reset_all()


def _write(env, name, data):
    path = env.src / name
    path.write_bytes(data)
    return str(path)


def _rows(env, sql, args=()):
    with closing(sqlite3.connect(env.db.db_path)) as conn:
        return conn.execute(sql, args).fetchall()


# ── the store ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt,mime,ext", [("PNG", "image/png", ".png"), ("JPEG", "image/jpeg", ".jpg"),
                                          ("GIF", "image/gif", ".gif"), ("WEBP", "image/webp", ".webp")])
def test_an_image_is_stored_by_its_first_bytes(env, fmt, mime, ext):
    chat = env.db.create_conversation(1, "A")
    data = _image(fmt)
    # The name is not trusted: the detected type decides the stored extension.
    record, reason = chat_files.add_file(env.db, chat, _write(env, "shot.dat", data), "ekran")
    assert reason is None and record["mime"] == mime and record["size"] == len(data)
    stored = os.path.join(env.root, str(chat), record["id"] + ext)
    assert os.path.isfile(stored)
    with open(stored, "rb") as f:
        assert f.read() == data
    assert chat_files.get_file(env.db, record["id"])["name"] == "shot.dat"
    assert os.listdir(os.path.join(env.root, str(chat))) == [record["id"] + ext]


def test_text_renamed_png_is_refused(env):
    chat = env.db.create_conversation(1, "A")
    assert chat_files.add_file(env.db, chat, _write(env, "fake.png", b"just some text"), "") == (None, "not_image")
    assert _rows(env, "SELECT * FROM chat_files") == []
    assert not os.path.exists(env.root)


def test_a_gamachine_secret_file_is_refused_even_with_image_bytes(env):
    chat = env.db.create_conversation(1, "A")
    secret = os.path.join(os.path.dirname(env.db.db_path), "api_key_fernet.key")
    with open(secret, "wb") as f:
        f.write(_image("PNG"))
    assert chat_files.add_file(env.db, chat, secret, "") == (None, "secret")


def test_over_20_mib_is_refused(env):
    chat = env.db.create_conversation(1, "A")
    big = _write(env, "big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (chat_files.MAX_BYTES - 7))
    assert os.path.getsize(big) == chat_files.MAX_BYTES + 1
    assert chat_files.add_file(env.db, chat, big, "") == (None, "too_large")


def test_a_hard_linked_file_is_refused(env):
    chat = env.db.create_conversation(1, "A")
    path = _write(env, "a.png", _image("PNG"))
    link = str(env.src / "b.png")
    os.link(path, link)
    assert chat_files.add_file(env.db, chat, link, "") == (None, "not_file")


def test_unc_relative_and_missing_paths_are_refused(env):
    chat = env.db.create_conversation(1, "A")
    assert chat_files.add_file(env.db, chat, UNC, "") == (None, "bad_path")
    assert chat_files.add_file(env.db, chat, "//server/share/a.png", "") == (None, "bad_path")
    assert chat_files.add_file(env.db, chat, "a.png", "") == (None, "bad_path")
    assert chat_files.add_file(env.db, chat, "", "") == (None, "bad_path")
    assert chat_files.add_file(env.db, chat, None, "") == (None, "bad_path")
    assert chat_files.add_file(env.db, chat, str(env.src / "nope.png"), "") == (None, "not_found")
    assert _rows(env, "SELECT * FROM chat_files") == []


def test_a_folder_is_refused(env):
    chat = env.db.create_conversation(1, "A")
    record, reason = chat_files.add_file(env.db, chat, str(env.src), "")
    assert record is None and reason in ("not_file", "not_found")


def test_the_per_chat_quota_is_enforced(env, monkeypatch):
    chat, other = env.db.create_conversation(1, "A"), env.db.create_conversation(1, "B")
    data = _image("PNG")
    monkeypatch.setattr(chat_files, "QUOTA_BYTES", int(len(data) * 1.5))
    path = _write(env, "a.png", data)
    assert chat_files.add_file(env.db, chat, path, "")[1] is None
    assert chat_files.add_file(env.db, chat, path, "") == (None, "quota")
    assert chat_files.add_file(env.db, other, path, "")[1] is None
    assert len(_rows(env, "SELECT * FROM chat_files WHERE conversation_id = ?", (chat,))) == 1


def test_caption_is_one_line_without_brackets_and_capped():
    assert chat_files.clean_caption("a [b]\n c\\") == "a b c"
    assert len(chat_files.clean_caption("x" * 500)) == chat_files.CAPTION_MAX
    assert chat_files.clean_caption(None) == ""


@pytest.mark.parametrize("how", ["single", "family"])
def test_deleting_a_chat_removes_its_rows_and_folder(env, how):
    chat, keep = env.db.create_conversation(1, "A"), env.db.create_conversation(1, "B")
    side = env.db.create_side_chat(chat, 1)
    path = _write(env, "a.png", _image("PNG"))
    for cid in (chat, keep, side):
        assert chat_files.add_file(env.db, cid, path, "")[1] is None
    assert sorted(os.listdir(env.root)) == sorted(str(c) for c in (chat, keep, side))
    if how == "single":
        env.db.delete_conversation(chat)
    else:
        env.db.delete_conversation_family_and_sides(chat)
    assert os.listdir(env.root) == [str(keep)]
    assert [r[0] for r in _rows(env, "SELECT conversation_id FROM chat_files")] == [keep]


# ── POST /chat-files ─────────────────────────────────────────────────────────

def test_post_stores_the_image_and_adds_an_assistant_message(env):
    chat = env.db.create_conversation(1, "A")
    r = env.client.post("/chat-files", headers=H, json={
        "conversation_id": chat, "path": _write(env, "a.png", _image("PNG")), "caption": "ilk [ekran]\nçıktı"})
    assert r.status_code == 200
    file_id, message_id = r.json()["id"], r.json()["message_id"]
    rows = env.db.get_conversation_messages(chat)
    assert [(m["id"], m["role"], m["content"]) for m in rows] == [
        (message_id, "assistant", f"![ilk ekran çıktı](gamachine-file:{file_id})")]
    assert chat_files.get_file(env.db, file_id)["caption"] == "ilk ekran çıktı"


def test_post_refusals_are_worded_in_turkish_and_leave_no_message(env):
    chat = env.db.create_conversation(1, "A")
    r = env.client.post("/chat-files", headers=H, json={
        "conversation_id": chat, "path": _write(env, "a.png", b"text"), "caption": ""})
    assert r.status_code == 400 and r.json()["detail"] == chat_files.REASONS["not_image"]
    assert env.db.get_conversation_messages(chat) == []


def test_post_is_owner_checked(env):
    foreign = env.db.create_conversation(2, "x")
    path = _write(env, "a.png", _image("PNG"))
    assert env.client.post("/chat-files", headers=H, json={
        "conversation_id": foreign, "path": path}).status_code == 403
    assert env.client.post("/chat-files", headers=H, json={
        "conversation_id": 9999, "path": path}).status_code == 404
    assert env.client.post("/chat-files", headers=H, json={"path": path}).status_code == 422
    assert env.db.get_conversation_messages(foreign) == []
    assert _rows(env, "SELECT * FROM chat_files") == []


def _stream_route(router):
    return next(r for r in router.routes if getattr(r, "path", "") == "/wake-stream-all")


async def _frame(resp, timeout):
    it = resp.body_iterator
    while True:
        chunk = await asyncio.wait_for(it.__anext__(), timeout=timeout)
        chunk = chunk if isinstance(chunk, str) else chunk.decode("utf-8")
        if chunk.startswith("data: "):
            return json.loads(chunk[6:])


def test_post_publishes_a_frame_on_the_owners_wake_stream_only(env, monkeypatch):
    monkeypatch.setattr(cr, "WAKE_ALL_POLL_S", 0.02)
    chat = env.db.create_conversation(1, "A")
    foreign = env.db.create_conversation(2, "x")
    stream = _stream_route(cr.create_conversation_router(env.db, {}))
    add = next(r for r in env.router.routes if getattr(r, "path", "") == "/chat-files" and "POST" in r.methods)
    path = _write(env, "a.png", _image("PNG"))

    async def run():
        resp = await stream.endpoint(x_session_token="")
        try:
            # The stream listens once its first frame is asked for.
            reader = asyncio.ensure_future(_frame(resp, 3.0))
            await asyncio.sleep(0.2)
            # A frame for a chat the stream's user does not own never reaches it.
            desktop_channel.CHANNEL.publish({"type": chat_files.FRAME_TYPE, "conversation_id": foreign,
                                             "message": {"id": 1}})
            posted = await add.endpoint({"conversation_id": chat, "path": path, "caption": "c"}, x_session_token="")
            return posted, await reader
        finally:
            await resp.body_iterator.aclose()

    posted, frame = asyncio.run(run())
    assert frame["type"] == "chat_file" and frame["conversation_id"] == chat
    assert frame["message"]["id"] == posted["message_id"] and frame["message"]["role"] == "assistant"
    assert frame["message"]["content"] == f"![c](gamachine-file:{posted['id']})"
    assert frame["message"]["created_at"]


def test_the_browser_remote_gets_the_chat_file_frame_and_route():
    from remote import ui_dispatch
    assert "chat_file" in ui_dispatch.BROWSER_FRAME_TYPES
    assert ui_dispatch.http_allowed("GET", "/chat-files/abc-123")
    assert not ui_dispatch.http_allowed("POST", "/chat-files")


# ── GET /chat-files/{id} ─────────────────────────────────────────────────────

def test_get_returns_the_stored_bytes_with_their_mime(env):
    chat = env.db.create_conversation(1, "A")
    data = _image("JPEG")
    record, _ = chat_files.add_file(env.db, chat, _write(env, "a.jpg", data), "")
    r = env.client.get(f"/chat-files/{record['id']}", headers=H)
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == "image/jpeg"
    assert env.client.get("/chat-files/no-such-id", headers=H).status_code == 404


def test_get_is_owner_checked_and_dies_with_the_chat(env):
    foreign = env.db.create_conversation(2, "x")
    record, reason = chat_files.add_file(env.db, foreign, _write(env, "a.png", _image("PNG")), "")
    assert reason is None
    assert env.client.get(f"/chat-files/{record['id']}", headers=H).status_code == 403
    mine = env.db.create_conversation(1, "A")
    record, _ = chat_files.add_file(env.db, mine, _write(env, "b.png", _image("PNG")), "")
    env.db.delete_conversation(mine)
    assert env.client.get(f"/chat-files/{record['id']}", headers=H).status_code == 404


def test_get_with_max_bytes_re_encodes_a_large_png_under_the_limit(env):
    chat = env.db.create_conversation(1, "A")
    data = _noise_png()
    assert len(data) > 3_000_000
    record, _ = chat_files.add_file(env.db, chat, _write(env, "n.png", data), "")
    limit = 480_000
    r = env.client.get(f"/chat-files/{record['id']}", params={"max_bytes": limit}, headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert 0 < len(r.content) <= limit
    with Image.open(io.BytesIO(r.content)) as img:
        assert img.format == "JPEG" and max(img.size) <= 1600
    # A file that already fits is sent as it is; one that cannot fit is a 413.
    small, _ = chat_files.add_file(env.db, chat, _write(env, "s.png", _image("PNG")), "")
    assert env.client.get(f"/chat-files/{small['id']}", params={"max_bytes": limit}, headers=H).headers[
        "content-type"] == "image/png"
    assert env.client.get(f"/chat-files/{record['id']}", params={"max_bytes": 1000}, headers=H).status_code == 413


# ── the agent tool ───────────────────────────────────────────────────────────

@pytest.fixture
def tool(env, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(show_tools.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.ASGITransport(app=env.app), timeout=kw.get("timeout")))
    return show_tools.show_image


def test_the_tool_posts_with_the_chat_from_its_environment(env, tool, monkeypatch):
    chat = env.db.create_conversation(1, "A")
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", str(chat))
    path = _write(env, "a.png", _image("PNG"))
    assert asyncio.run(tool(path, "başlık")) == "Gösterildi."
    assert [m["content"].startswith("![başlık](gamachine-file:") for m in env.db.get_conversation_messages(chat)] == [True]
    assert asyncio.run(tool(_write(env, "x.png", b"text"), "")) == chat_files.REASONS["not_image"]
    assert asyncio.run(tool(UNC, "")) == chat_files.REASONS["bad_path"]


def test_the_tool_needs_a_chat_and_a_reachable_backend(env, tool, monkeypatch):
    monkeypatch.delenv("GAMACHINE_CONVERSATION_ID", raising=False)
    assert "yalnız bir Gamachine sohbetinin içinden" in asyncio.run(tool("C:/a.png", ""))
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", "1")

    def broken(**kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(show_tools.httpx, "AsyncClient", broken)
    assert asyncio.run(tool("C:/a.png", "")) == "Gamachine'e ulaşılamadı; görüntü gösterilemedi."


def test_the_tool_is_on_the_mail_server_and_in_the_claude_allow_set():
    from agentic import mailbox
    assert "mcp__gamachineMail__show_image" in mailbox.CLAUDE_TOOL_NAMES
    assert "show_image" not in mailbox.MAIL_TOOLS


def test_the_api_loop_carries_the_tool_and_injects_its_own_chat(env, tool):
    from tools import tool_registry
    assert tool_registry.is_async_tool("show_image")
    assert "show_image" in {t["name"] for t in tool_registry.TOOL_DEFINITIONS}
    chat, other = env.db.create_conversation(1, "A"), env.db.create_conversation(1, "B")
    path = _write(env, "a.png", _image("PNG"))

    async def call(**args):
        # A model-supplied conversation_id is dropped; the runner's own id is used.
        return await tool_registry.execute_tool_async("show_image", {**args, "conversation_id": other}, ".", chat)

    assert asyncio.run(call(path=path, caption="x")) == {"success": True, "content": "Gösterildi."}
    assert [len(env.db.get_conversation_messages(c)) for c in (chat, other)] == [1, 0]
    refused = asyncio.run(call(path=_write(env, "t.png", b"text")))
    assert refused == {"success": False, "error": chat_files.REASONS["not_image"]}
    none = asyncio.run(tool_registry.execute_tool_async("show_image", {"path": path}, ".", None))
    assert none["success"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows path forms")
def test_mixed_slash_unc_forms_are_refused_before_any_file_system_call(env, monkeypatch):
    # `\/host/share` and `/\host\share` passed a starts-with check, yet Windows opens
    # them as UNC paths and sends the user's NTLM hash to that host (4 Nov 2026).
    bs = chr(92)
    touched = []
    monkeypatch.setattr(chat_files.action_risk, "gamachine_secret_target",
                        lambda p: touched.append(p) or False)
    monkeypatch.setattr(chat_files.os.path, "realpath", lambda p: touched.append(p) or p)
    chat = env.db.create_conversation(1, "A")
    for path in (bs + "/server/share/a.png", "/" + bs + "server" + bs + "share" + bs + "a.png",
                 bs + bs + "?" + bs + "UNC" + bs + "server" + bs + "share" + bs + "a.png",
                 bs + bs + "." + bs + "C:" + bs + "a.png", "/a.png"):
        assert chat_files.add_file(env.db, chat, path, "") == (None, "bad_path"), path
    assert touched == []


@pytest.mark.skipif(os.name != "nt", reason="Windows path forms")
def test_a_path_that_resolves_to_a_share_is_refused(env, monkeypatch):
    bs = chr(92)
    src = _write(env, "a.png", _image("PNG"))
    monkeypatch.setattr(chat_files.action_risk, "gamachine_secret_target", lambda p: False)
    monkeypatch.setattr(chat_files.os.path, "realpath",
                        lambda p: bs + bs + "server" + bs + "share" + bs + "a.png")
    assert chat_files.add_file(env.db, env.db.create_conversation(1, "A"), src, "") == (None, "bad_path")


# ── audit fix round (4 Nov 2026) ─────────────────────────────────────────────

def _post(env, chat, name="a.png", data=None, caption="c"):
    r = env.client.post("/chat-files", headers=H, json={
        "conversation_id": chat, "path": _write(env, name, data or _image("PNG")), "caption": caption})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_branch_gets_its_own_copy_of_the_pictures_and_survives_the_source(env):
    chat = env.db.create_conversation(1, "A")
    posted = _post(env, chat, caption="ilk")
    branch = env.db.create_branch(chat)["id"]
    [msg] = env.db.get_conversation_messages(branch)
    new_id = msg["content"].split("gamachine-file:")[1].rstrip(")")
    assert new_id != posted["id"] and msg["content"].startswith("![ilk](")
    record = chat_files.get_file(env.db, new_id)
    assert record["conversation_id"] == branch and record["size"] == chat_files.get_file(env.db, posted["id"])["size"]
    assert os.path.isfile(chat_files.file_path(env.db.db_path, record))
    # The source goes away; the branch still shows its picture.
    env.db.delete_conversation(chat)
    assert env.client.get(f"/chat-files/{new_id}", headers=H).status_code == 200
    assert [r[0] for r in _rows(env, "SELECT conversation_id FROM chat_files")] == [branch]


def test_a_branch_whose_picture_cannot_be_copied_is_not_created(env, monkeypatch):
    # Keeping the source's id would leave the branch on a file its source owns (Codex, 4 Nov 2026).
    chat = env.db.create_conversation(1, "A")
    _post(env, chat, caption="ilk")
    before = _rows(env, "SELECT id FROM conversations")

    def full_disk(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(chat_files.shutil, "copyfile", full_disk)
    with pytest.raises(OSError):
        env.db.create_branch(chat)
    assert _rows(env, "SELECT id FROM conversations") == before
    assert len(_rows(env, "SELECT id FROM chat_files")) == 1


def test_a_truncated_or_signature_only_image_is_refused(env):
    chat = env.db.create_conversation(1, "A")
    whole = _noise_png(64)
    for name, data in (("sig.png", b"\x89PNG\r\n\x1a\n"), ("cut.png", whole[:len(whole) // 2]),
                       ("sig.jpg", b"\xff\xd8\xff\xe0" + b"0" * 20)):
        assert chat_files.add_file(env.db, chat, _write(env, name, data), "") == (None, "not_image"), name
    assert _rows(env, "SELECT * FROM chat_files") == []


def _bomb_png():
    out = io.BytesIO()
    Image.new("1", (12000, 12000)).save(out, "PNG")
    assert len(out.getvalue()) < 1_000_000
    return out.getvalue()


def test_a_decompression_bomb_is_refused_before_it_is_decoded(env, monkeypatch):
    chat = env.db.create_conversation(1, "A")
    bomb = _bomb_png()

    def never(self, *a, **k):
        raise AssertionError("the pixels were decoded")

    monkeypatch.setattr(Image.Image, "load", never)
    assert chat_files.add_file(env.db, chat, _write(env, "bomb.png", bomb), "") == (None, "too_many_pixels")
    # A stored file that is a bomb (from before the check) is not re-encoded either.
    assert chat_files._shrink(bomb, 1000) is None
    assert _rows(env, "SELECT * FROM chat_files") == []


def test_a_16_bit_grey_png_is_not_turned_white_by_the_re_encode():
    out = io.BytesIO()
    Image.new("I;16", (64, 64), 16384).save(out, "PNG")
    shrunk = chat_files._shrink(out.getvalue(), 10_000_000)
    with Image.open(io.BytesIO(shrunk)) as img:
        assert abs(img.convert("L").getpixel((5, 5)) - 64) <= 3


def test_the_re_encode_keeps_the_exif_orientation():
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    out = io.BytesIO()
    Image.new("RGB", (40, 20), (10, 200, 10)).save(out, "JPEG", exif=exif)
    shrunk = chat_files._shrink(out.getvalue(), 10_000_000)
    with Image.open(io.BytesIO(shrunk)) as img:
        assert img.size == (20, 40)


@pytest.mark.parametrize("kept", [False, True])
def test_compacting_frees_the_pictures_of_the_deleted_messages(env, kept):
    chat = env.db.create_conversation(1, "A")
    first = _post(env, chat)
    last_text = env.db.add_message(chat, "user", "devam")
    later = _post(env, chat, "b.png", _image("PNG", color=(1, 2, 3)))
    env.db.compact_conversation(chat, "özet", through_id=last_text if kept else None)
    left = [r[0] for r in _rows(env, "SELECT id FROM chat_files")]
    folder = os.listdir(os.path.join(env.root, str(chat)))
    if kept:
        # Only the picture inside the compacted range goes; the later one stays.
        assert left == [later["id"]] and len(folder) == 1
    else:
        assert left == [] and folder == []
    assert first["id"] not in left


def test_post_for_a_chat_deleted_meanwhile_keeps_nothing_and_is_404(env, monkeypatch):
    chat = env.db.create_conversation(1, "A")
    real = chat_files._check_decodes

    def delete_then_check(data):
        env.db.delete_conversation(chat)
        return real(data)

    monkeypatch.setattr(chat_files, "_check_decodes", delete_then_check)
    r = env.client.post("/chat-files", headers=H, json={"conversation_id": chat, "path": _write(env, "a.png", _image("PNG"))})
    assert r.status_code == 404
    assert _rows(env, "SELECT * FROM chat_files") == [] and _rows(env, "SELECT * FROM messages") == []
    assert not os.path.exists(os.path.join(env.root, str(chat)))


def test_show_image_is_refused_in_a_read_only_run_and_in_a_side_chat(env, tool, monkeypatch):
    chat = env.db.create_conversation(1, "A")
    side = env.db.create_side_chat(chat, 1)
    path = _write(env, "a.png", _image("PNG"))
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", str(chat))
    monkeypatch.setenv("GAMACHINE_READ_ONLY", "1")
    assert asyncio.run(tool(path, "")).startswith("Read-only turn")
    assert env.db.get_conversation_messages(chat) == []
    monkeypatch.delenv("GAMACHINE_READ_ONLY")
    r = env.client.post("/chat-files", headers=H, json={"conversation_id": side, "path": path})
    assert r.status_code == 403
    assert env.db.get_conversation_messages(side) == [] and _rows(env, "SELECT * FROM chat_files") == []


def test_a_file_swapped_after_the_check_is_refused(env, monkeypatch):
    src = _write(env, "a.png", _image("PNG"))
    real_stat = os.stat

    def other_file(path, *a, **k):
        info = real_stat(path, *a, **k)
        if os.path.normcase(str(path)) == os.path.normcase(os.path.realpath(src)):
            return types.SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1)
        return info

    monkeypatch.setattr(chat_files.os, "stat", other_file)
    monkeypatch.setattr(chat_files.action_risk, "gamachine_secret_target", lambda p: False)
    assert chat_files.add_file(env.db, env.db.create_conversation(1, "A"), src, "") == (None, "not_file")
