"""Import of an existing Claude Code session into a chat (synthetic session files only)."""
import json
import os
import shutil
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import claude_sessions
from database import DatabaseManager
from routes import claude_session_routes
from routes.claude_session_routes import create_claude_session_router
from routes.conversation_routes import _build_handoff_context

SID = "3f2a91c0-1111-4222-8333-444455556666"
MARK = "⌨️"


def _lines(work):
    base = {"sessionId": SID, "cwd": str(work), "isSidechain": False}
    return [
        {"type": "attachment", **base},
        {"type": "user", **base, "timestamp": "2026-11-03T10:00:00Z",
         "message": {"role": "user", "content": "Merhaba, oyunu yapalim\nikinci satir"}},
        {"type": "assistant", **base, "timestamp": "2026-11-03T10:00:05Z",
         "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [
             {"type": "thinking", "thinking": "SECRET"}, {"type": "text", "text": "Tamam."},
             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}},
        {"type": "user", **base, "toolUseResult": {"stdout": "x"},
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "OUT"}]}},
        {"type": "user", **base, "isMeta": True,
         "message": {"role": "user", "content": "Base directory for this skill: x"}},
        {"type": "user", **dict(base, isSidechain=True),
         "message": {"role": "user", "content": "SIDECHAIN"}},
        {"type": "user", **base, "message": {"role": "user", "content": "<command-name>/x</command-name>"}},
        {"type": "ai-title", **base, "title": "t"},
        {"type": "user", **dict(base, cwd=str(work / "Assets")),
         "message": {"role": "user", "content": [{"type": "text", "text": "Ikinci istek"}]}},
        {"type": "assistant", **base,
         "message": {"role": "assistant", "model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "Bitti."}]}},
    ]


def _write(cfg, work, lines=None):
    folder = cfg / "projects" / "".join(c if c.isalnum() else "-" for c in str(work))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{SID}.jsonl"
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in (lines or _lines(work))) + "\nnot json\n",
                    encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    work = tmp_path / "proje"
    work.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("LOCAL_APP_TOKEN", "tok")
    published = []
    monkeypatch.setattr(claude_session_routes.CHANNEL, "publish", lambda frame: published.append(frame) or True)
    db = DatabaseManager(db_path=str(tmp_path / "t.db"))
    app = FastAPI()
    app.include_router(create_claude_session_router(db))
    client = TestClient(app)
    client.headers.update({"X-Session-Token": "tok"})
    return {"cfg": cfg, "work": work, "db": db, "client": client, "published": published,
            "path": _write(cfg, work), "tmp": tmp_path}


def test_extraction_exact_and_first_cwd(env):
    info = claude_sessions.read_session(env["path"])
    assert [(m["role"], m["content"]) for m in info["messages"]] == [
        ("user", "Merhaba, oyunu yapalim\nikinci satir"), ("assistant", "Tamam."),
        ("user", "Ikinci istek"), ("assistant", "Bitti.")]
    assert info["cwd"] == str(env["work"])
    assert info["model"] == "claude-sonnet-5-5"
    assert info["title"] == "Merhaba, oyunu yapalim"
    assert info["total"] == 4


def test_cap_keeps_last_and_reports_total(env):
    info = claude_sessions.read_session(env["path"], limit=3)
    assert info["total"] == 4
    assert [m["content"] for m in info["messages"]] == ["Tamam.", "Ikinci istek", "Bitti."]


def test_bad_unknown_and_traversal_ids(env):
    c = env["client"]
    assert c.get("/claude-sessions/not-a-uuid/preview").status_code == 400
    assert c.get("/claude-sessions/not-a-uuid/preview").json() == {"error": "bad_session_id"}
    assert c.get("/claude-sessions/00000000-0000-4000-8000-000000000000/preview").status_code == 404
    assert c.get("/claude-sessions/00000000-0000-4000-8000-000000000000/preview").json() == {"error": "session_not_found"}
    for bad in ("../x", "*.jsonl", "..\\x"):
        assert claude_sessions.find_session(bad) is None
        assert c.post("/claude-sessions/import", json={"session_id": bad}).status_code == 400
    assert c.post("/claude-sessions/import", json={}).status_code == 400
    assert c.get(f"/claude-sessions/{SID}/preview", headers={"X-Session-Token": "wrong"}).status_code in (401, 403)


def test_import_creates_bound_chat(env):
    c, db, work = env["client"], env["db"], str(env["work"])
    r = c.post("/claude-sessions/import", json={"session_id": SID})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workspace"] == work and body["imported"] == 4 and body["total"] == 4
    assert body["model_name"] == "claude-sonnet-5-5"
    conv = body["chat_id"]
    with sqlite3.connect(db.db_path) as con:
        ws, ptype, model = con.execute(
            "select workspace, provider_type, model_name from conversations where id=?", (conv,)).fetchone()
    assert (ws, ptype, model) == (work, "subscription", "claude-sonnet-5-5")
    rows = db.get_conversation_messages(conv)
    assert [r_["role"] for r_ in rows] == ["user", "assistant", "user", "assistant", "system"]
    assert rows[-1]["content"].startswith(MARK) and "4 mesaj" in rows[-1]["content"]
    assert sum(1 for r_ in rows if r_["content"].startswith(MARK)) == 1
    assert db.get_cli_session(conv, "claude", work) == SID
    assert env["published"] and env["published"][0]["type"] == "conversation_created"
    assert env["published"][0]["conversation_id"] == conv
    s = c.get(f"/conversations/{conv}/claude-session").json()
    assert s["session_id"] == SID and s["workspace"] == work
    assert s["command"] == f'cd "{work}"; claude --resume {SID}'


def test_import_capped_divider_and_missing_cwd(env, monkeypatch):
    monkeypatch.setattr(claude_session_routes, "IMPORT_LIMIT", 2)
    r = env["client"].post("/claude-sessions/import", json={"session_id": SID}).json()
    assert r["imported"] == 2 and r["total"] == 4
    last = env["db"].get_conversation_messages(r["chat_id"])[-1]["content"]
    assert "son 2" in last and "toplam 4" in last
    gone = env["tmp"] / "silinmis"
    _write(env["cfg"], gone)
    res = env["client"].post("/claude-sessions/import", json={"session_id": SID})
    assert res.status_code == 409 and res.json()["error"] == "cwd_missing"


def test_modified_recently(env):
    pv = env["client"].get(f"/claude-sessions/{SID}/preview").json()
    assert pv["modified_recently"] is True and pv["cwd_exists"] is True and pv["importable"] == 4
    old = time.time() - 1200
    os.utime(env["path"], (old, old))
    assert env["client"].get(f"/claude-sessions/{SID}/preview").json()["modified_recently"] is False


def test_chat_without_claude_session(env):
    conv = env["db"].create_conversation(1, "x")
    assert env["client"].get(f"/conversations/{conv}/claude-session").json() == {"session_id": None}
    env["db"].set_conversation_model(conv, "ollama", "llama3")
    assert env["client"].get(f"/conversations/{conv}/claude-session").json() == {"session_id": None}


def test_handoff_context_skips_divider():
    msgs = [{"role": "user", "content": "hello"},
            {"role": "system", "content": f"{MARK} Terminal oturumundan aktarildi · 1 mesaj"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "current"}]
    text = _build_handoff_context("", msgs)
    assert "hello" in text and "hi" in text and "Terminal" not in text


def test_command_for_drops_cd_on_quote_or_control_char():
    assert claude_sessions.command_for('C:\\a"b', SID) == f"claude --resume {SID}"
    assert claude_sessions.command_for("C:\\a\nb", SID) == f"claude --resume {SID}"
    cwd = "C:\\a b" if os.name == "nt" else "/a b"
    assert claude_sessions.command_for(cwd, SID) == f'cd "{cwd}"; claude --resume {SID}'


def test_command_for_drops_cd_on_shell_special_chars():
    for bad in ("$", "`", "'", "!", ";", "&", "|", "[", "%", "“", "’", "—", " ", "\t"):
        assert claude_sessions.command_for(f"C:\\a{bad}b", SID) == f"claude --resume {SID}", bad
    # Turkish letters and parentheses are safe inside "..." in all three shells.
    cwd = "C:\\Oyunçuk 2 (yedek)" if os.name == "nt" else "/Oyunçuk 2 (yedek)"
    assert claude_sessions.command_for(cwd, SID) == f'cd "{cwd}"; claude --resume {SID}'


def _session_with(env, cwd, lines):
    base = {"sessionId": SID, "cwd": cwd, "isSidechain": False}
    _write(env["cfg"], env["work"], [{**base, **x} for x in lines])


def test_image_prompt_and_wrapper_lines(env):
    user = lambda c: {"type": "user", "message": {"role": "user", "content": c}}
    _session_with(env, str(env["work"]), [
        user([{"type": "text", "text": "Bu resme bak"}, {"type": "image", "source": {"type": "base64", "data": "AA"}}]),
        user([{"type": "image", "source": {"type": "base64", "data": "AA"}}]),
        user("<bash-input>ls</bash-input>"), user("<bash-stdout>x</bash-stdout>"), user("<bash-stderr>e</bash-stderr>"),
        user("<command-message>m</command-message>"), user("<command-args>a</command-args>"),
        user("<user-prompt-submit-hook>h</user-prompt-submit-hook>"),
        user("Caveat: The messages below were generated by the user while running local commands. DO NOT respond."),
        user([{"type": "tool_result", "tool_use_id": "t", "content": "OUT"}, {"type": "text", "text": "T"}]),
        # A person's own prompt may start with "Caveat:"; only Claude Code's wrapper is skipped.
        user("Caveat: bu benim notum"),
    ])
    info = claude_sessions.read_session(env["path"])
    assert [m["content"] for m in info["messages"]] == ["Bu resme bak", "[resim]", "Caveat: bu benim notum"]


def test_session_id_with_trailing_newline_is_400(env):
    c = env["client"]
    bad = SID + "\n"
    assert claude_sessions.find_session(bad) is None
    r = c.post("/claude-sessions/import", json={"session_id": bad})
    assert r.status_code == 400 and r.json() == {"error": "bad_session_id"}


@pytest.mark.parametrize("cwd", [".", "..", "rel/dir", "\\\\server\\share\\x", "//server/share", "C:\\a\nb"])
def test_unusable_cwd_never_reaches_isdir(env, monkeypatch, cwd):
    assert claude_sessions.cwd_usable(cwd) is False
    _session_with(env, cwd, [{"type": "user", "message": {"role": "user", "content": "hi"}}])
    calls = []
    monkeypatch.setattr(claude_session_routes.os.path, "isdir", lambda p: calls.append(p) or True)
    pv = env["client"].get(f"/claude-sessions/{SID}/preview").json()
    assert pv["cwd_exists"] is False
    r = env["client"].post("/claude-sessions/import", json={"session_id": SID})
    assert r.status_code == 409 and r.json() == {"error": "cwd_unsupported", "cwd": cwd}
    assert calls == []


def test_linked_folder_outside_the_store_is_ignored(env):
    outside = env["tmp"] / "outside"
    outside.mkdir()
    (outside / f"{SID}.jsonl").write_text("{}", encoding="utf-8")
    shutil.rmtree(env["path"].parent)
    try:
        os.symlink(outside, env["cfg"] / "projects" / "linked", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not permitted: {exc}")
    assert claude_sessions.find_session(SID) is None
    assert env["client"].get(f"/claude-sessions/{SID}/preview").status_code == 404


def test_command_for_drops_cd_on_trailing_separator():
    for cwd in ("C:\\Users\\x\\", "/home/x/"):
        assert claude_sessions.command_for(cwd, SID) == f"claude --resume {SID}"


def test_only_what_survived_the_last_compaction_is_imported(env):
    # Shape measured on a real Claude Code 2.1.3xx file: a system/compact_boundary line
    # lists the kept messages (compactMetadata.preservedMessages), then a summary line.
    user = lambda c, u: {"type": "user", "uuid": u, "message": {"role": "user", "content": c}}
    asst = lambda t, u: {"type": "assistant", "uuid": u,
                         "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": t}]}}
    boundary = lambda kept: {"type": "system", "subtype": "compact_boundary", "uuid": "b" + kept[0],
                             "compactMetadata": {"preservedMessages": {"uuids": kept, "allUuids": kept}}}
    summary = {"type": "user", "uuid": "s", "isCompactSummary": True,
               "message": {"role": "user", "content": "This session is being continued..."}}
    _session_with(env, str(env["work"]), [
        user("eski 1", "u1"), asst("eski cevap 1", "a1"),
        boundary(["a1"]), summary,
        user("orta", "u2"), asst("orta cevap", "a2"), user("kalan soru", "u3"), asst("kalan cevap", "a3"),
        boundary(["u3", "a3"]), summary,
        user("son soru", "u4"), asst("son cevap", "a4"),
    ])
    info = claude_sessions.read_session(env["path"])
    assert [m["content"] for m in info["messages"]] == ["kalan soru", "kalan cevap", "son soru", "son cevap"]
    assert info["total"] == 4
