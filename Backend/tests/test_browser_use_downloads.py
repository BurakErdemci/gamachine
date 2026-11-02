"""Agent-browser downloads: one approval per agent download, blocked types, and the copy into the project."""
import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic import approval_mode, approval_policy, cards
from agentic.command_gates import GATE_OWNERS
from browser_use import gateway, policy, settings as browser_settings
from browser_use.gateway import BrowserGateway
from routes.conversation_routes import create_conversation_router

SECRET = "downloads-secret"
UI = {"X-Gamachine-UI-Secret": SECRET}
TOKEN = "cd" * 32
STATE = {"enabled": True, "halted": False, "deleted": False, "page_gen": 0, "stop_gen": 0,
         "runtime": {"running": True, "port": 4711, "token": "t"}}
DOWNLOAD_LINK = {"role": "link", "name": "Download", "tag": "a", "href": "https://files.example/pack.zip"}
NOTICE = {"id": "dabc123", "filename": "pack.zip", "bytes": 2048, "host": "cdn.files.example", "state": "awaiting_approval"}


class Host:
    """The Electron host: the next reply carries a paused download, resume and cancel report its new state."""

    def __init__(self, notice=NOTICE, element=None, completed=None):
        self.cmds = []
        self.notice = dict(notice) if notice else None
        self.element = element
        self.completed = completed or {}

    async def __call__(self, runtime, op, conversation_id, args):
        self.cmds.append((op, conversation_id, dict(args)))
        page = {"url": "https://files.example/", "title": "Files"}
        if op == "describe":
            return {"ok": True, **page, "element": self.element or {"role": "button", "name": "More", "tag": "button"}}
        if op in ("download_resume", "download_cancel"):
            state = "progressing" if op == "download_resume" else "cancelled"
            return {"ok": True, **page, "download": {**NOTICE, "id": args["id"], "state": state}}
        if op == "download_info":
            entry = self.completed.get(args["id"])
            if entry is None:
                return {"ok": False, **page, "error": "no_download"}
            return {"ok": True, **page, "download": entry}
        reply = {"ok": True, **page}
        if op == "downloads":
            reply["downloads"] = []
        if self.notice is not None:
            reply["download"], self.notice = self.notice, None
        return reply

    def ops(self):
        return [op for op, _, _ in self.cmds]


class Cards:
    def __init__(self, answer):
        self.answer = answer
        self.opened = []
        self.result = None

    def open_card(self, conversation_id, tool, params):
        self.opened.append((conversation_id, tool, params))
        return self.answer if isinstance(self.answer, dict) else {"status": "ok", "gate_id": "g1"}

    def wait_card(self, gate_id):
        assert gate_id == "g1"
        return self.result


def gateway_for(host, answer, **kwargs):
    deck = Cards(answer)
    return BrowserGateway(host, deck.open_card, deck.wait_card, **kwargs), deck


def run(handler, op, args, state=None):
    async def go():
        return await handler.call(3, op, args, lambda: dict(state or STATE))
    return asyncio.run(go())


def body(reply):
    return json.loads(reply["text"])


@pytest.fixture(autouse=True)
def short_waits(monkeypatch):
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 0.2)
    monkeypatch.setattr(gateway, "_SETTLE_POLL_S", 0.02)


# ---- policy ----

def test_agent_block_list_covers_the_decided_types():
    for suffix in (".exe", ".msi", ".ps1", ".js", ".py", ".sh", ".dll", ".jar", ".apk", ".lnk", ".command"):
        assert suffix in policy.AGENT_BLOCKED_DOWNLOAD_SUFFIXES
    assert policy.agent_blocked_suffix("Invoice.PDF.exe") == ".exe"
    assert policy.agent_blocked_suffix("setup.exe. ") == ".exe"
    assert policy.agent_blocked_suffix("song.mp3") == ""
    assert policy.agent_blocked_suffix(None) == ""


def test_project_block_list_keeps_code_and_plugin_folders_out():
    for suffix in (".cs", ".asmdef", ".dll", ".so", ".jslib", ".cpp", ".java", ".swift"):
        assert suffix in policy.AGENT_BLOCKED_PROJECT_SUFFIXES
    assert policy.project_copy_refusal("Assets/Scripts/a.CS")
    assert policy.project_copy_refusal("Assets/Plugins/a.png")
    assert policy.project_copy_refusal("Assets/editor/a.png")
    assert policy.project_copy_refusal("Assets/Shaders/water.shader") == ""
    assert policy.project_copy_refusal("Assets/Audio/a.wav") == ""
    assert policy.file_type("a.TAR.GZ") == ".gz" and policy.file_type("noext") == ""


# ---- one approval per agent download ----

def test_approved_card_resumes_the_download():
    host = Host()
    handler, deck = gateway_for(host, {"status": "resolved", "approved": True})
    reply = run(handler, "scroll", {"direction": "down"})
    assert host.ops() == ["scroll", "download_resume"]
    assert body(reply)["download"]["state"] == "progressing"
    conversation_id, tool, params = deck.opened[0]
    assert conversation_id == 3 and tool == "browser_download"
    assert params["filename"] == "pack.zip" and params["bytes"] == 2048 and params["host"] == "cdn.files.example"
    assert params["risky"] is True and "pack.zip" in params["risk_reason"]
    assert params["page_url"] == "https://files.example/"


def test_rejected_or_denied_card_cancels_the_download():
    for answer in ({"status": "resolved", "approved": False}, {"status": "resolved", "approved": False, "error": "Browser off"}):
        host = Host()
        handler, _ = gateway_for(host, answer)
        reply = run(handler, "scroll", {"direction": "down"})
        assert host.ops() == ["scroll", "download_cancel"]
        assert body(reply)["download"]["state"] == "cancelled" and body(reply)["download"]["reason"]


def test_a_click_approved_as_starting_a_download_is_its_only_approval():
    host = Host(element=DOWNLOAD_LINK)
    handler, deck = gateway_for(host, {"status": "resolved", "approved": True})
    reply = run(handler, "click", {"snapshot_id": "s1", "ref": "4"})
    assert [tool for _, tool, _ in deck.opened] == ["browser_click"]
    assert "starts a download" in deck.opened[0][2]["risk_reason"]
    assert host.ops() == ["describe", "click", "download_resume"]
    assert body(reply)["download"]["state"] == "progressing"
    # Spent: the next paused download needs its own card.
    host.notice = dict(NOTICE, id="dnext")
    run(handler, "scroll", {"direction": "down"})
    assert [tool for _, tool, _ in deck.opened] == ["browser_click", "browser_download"]


def test_a_click_approval_older_than_the_window_does_not_count(monkeypatch):
    host = Host(element=DOWNLOAD_LINK, notice=None)
    clock = [100.0]
    handler, deck = gateway_for(host, {"status": "resolved", "approved": True}, now=lambda: clock[0])
    run(handler, "click", {"snapshot_id": "s1", "ref": "4"})
    clock[0] += gateway.DOWNLOAD_CLICK_WINDOW_S + 1
    host.notice = dict(NOTICE)
    run(handler, "scroll", {"direction": "down"})
    assert [tool for _, tool, _ in deck.opened] == ["browser_click", "browser_download"]


def test_unanswered_card_returns_needs_approval_and_a_later_answer_resumes():
    host = Host()
    handler, deck = gateway_for(host, "wait")

    async def go():
        reply = await handler.call(3, "downloads", {}, lambda: dict(STATE))
        assert reply["ok"] is True
        data = json.loads(reply["text"])
        assert data["needs_approval"] is True and data["download"]["note"].startswith("needs_approval: ")
        assert "download_resume" not in host.ops()
        deck.result = {"status": "resolved", "approved": True}
        for _ in range(100):
            if "download_resume" in host.ops():
                break
            await asyncio.sleep(0.02)
    asyncio.run(go())
    assert host.ops() == ["downloads", "download_resume"]
    assert host.cmds[-1] == ("download_resume", 3, {"id": "dabc123"})


def test_a_later_rejection_cancels():
    host = Host()
    handler, deck = gateway_for(host, "wait")

    async def go():
        await handler.call(3, "downloads", {}, lambda: dict(STATE))
        deck.result = {"status": "resolved", "approved": False}
        for _ in range(100):
            if "download_cancel" in host.ops():
                break
            await asyncio.sleep(0.02)
    asyncio.run(go())
    assert host.ops()[-1] == "download_cancel"


def test_agent_cannot_reach_the_host_only_download_ops():
    handler, _ = gateway_for(Host(), {"status": "resolved", "approved": True})
    for op in ("download_resume", "download_cancel", "download_info"):
        assert run(handler, op, {"id": "dabc123"})["error"] == f"Unknown browser tool browser_{op}."
    assert run(handler, "downloads", {"wait_ms": 30_001})["ok"] is False
    assert run(handler, "downloads", {"wait_ms": True})["ok"] is False


def test_downloads_forwards_wait_ms():
    host = Host(notice=None)
    handler, _ = gateway_for(host, {"status": "resolved", "approved": True})
    assert run(handler, "downloads", {"wait_ms": 5000})["ok"] is True
    assert host.cmds == [("downloads", 3, {"wait_ms": 5000})]


# ---- the copy into the project ----

@pytest.fixture
def places(tmp_path):
    downloads = tmp_path / "Downloads"
    project = tmp_path / "Game"
    downloads.mkdir()
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    return SimpleNamespace(downloads=downloads, project=project, tmp=tmp_path)


def completed(places, name, data=b"RIFF....WAVE", download_id="dwav1"):
    path = places.downloads / name
    path.write_bytes(data)
    return {download_id: {"id": download_id, "filename": name, "bytes": len(data), "state": "completed",
                          "savePath": str(path), "dir": str(places.downloads)}}


def copy(places, entries, dest, workspace=None, **extra):
    host = Host(notice=None, completed=entries)
    handler, _ = gateway_for(host, {"status": "resolved", "approved": True},
                             workspace=lambda cid: str(workspace or places.project))
    return run(handler, "download_to_project", {"id": next(iter(entries)), "dest": dest, **extra}), host


def test_copies_a_completed_wav_into_assets(places):
    reply, host = copy(places, completed(places, "a.wav"), "Assets/Audio/a.wav")
    assert reply["ok"] is True, reply
    assert body(reply) == {"ok": True, "path": "Assets/Audio/a.wav", "bytes": 12}
    assert (places.project / "Assets" / "Audio" / "a.wav").read_bytes() == b"RIFF....WAVE"
    assert host.ops() == ["download_info"]


def test_copies_a_zip_into_assets(places):
    reply, _ = copy(places, completed(places, "pack.zip", b"PK"), "Assets/x.zip")
    assert reply["ok"] is True and (places.project / "Assets" / "x.zip").read_bytes() == b"PK"


@pytest.mark.parametrize("name,dest,why", [
    ("notes.txt", "Assets/notes.cs", "never copied"),
    ("notes.txt", "Assets/notes.png", "keeps the downloaded file's type"),
    ("a.png", "Assets/Plugins/a.png", "Plugins or Editor"),
    ("a.png", "Assets/Editor/a.png", "Plugins or Editor"),
    ("lib.dll", "Assets/lib.dll", ".dll"),
    ("lib.dll", "Assets/lib.png", ".dll"),
    ("run.exe", "Assets/run.png", ".exe"),
    ("a.png", "C:/Windows/a.png", "relative to its root"),
    ("a.png", "/etc/a.png", "relative to its root"),
    ("a.png", "Assets/../../a.png", "relative to its root"),
    ("a.png", "", "relative to its root"),
    ("a.png", "Assets/a.png:evil", "relative to its root"),
])
def test_refuses_unsafe_copies(places, name, dest, why):
    reply, _ = copy(places, completed(places, name), dest)
    assert reply["ok"] is False and why in reply["error"], reply
    assert not any(p.is_file() for p in (places.project / "Assets").rglob("*"))


def test_refuses_an_existing_file_unless_overwrite_and_only_under_assets(places):
    (places.project / "Assets" / "a.wav").write_bytes(b"old")
    entries = completed(places, "a.wav")
    reply, _ = copy(places, entries, "Assets/a.wav")
    assert reply["ok"] is False and "overwrite" in reply["error"]
    assert (places.project / "Assets" / "a.wav").read_bytes() == b"old"
    reply, _ = copy(places, entries, "Assets/a.wav", overwrite=True)
    assert reply["ok"] is True and (places.project / "Assets" / "a.wav").read_bytes() == b"RIFF....WAVE"
    (places.project / "ProjectSettings" / "a.wav").write_bytes(b"old")
    reply, _ = copy(places, entries, "ProjectSettings/a.wav", overwrite=True)
    assert reply["ok"] is False and "Assets/" in reply["error"]


def test_refuses_a_download_that_is_not_completed_or_not_in_the_downloads_folder(places):
    reply, _ = copy(places, {"dx": {"id": "dx", "filename": "a.wav", "state": "progressing"}}, "Assets/a.wav")
    assert reply["ok"] is False
    outside = places.tmp / "a.wav"
    outside.write_bytes(b"x")
    entries = {"dx": {"id": "dx", "filename": "a.wav", "state": "completed", "savePath": str(outside),
                      "dir": str(places.downloads)}}
    reply, _ = copy(places, entries, "Assets/a.wav")
    assert reply["ok"] is False and "Downloads folder" in reply["error"]
    reply, _ = copy(places, {"dmissing": {}}, "Assets/a.wav")
    assert reply["ok"] is False


def test_refuses_a_dest_through_a_link(places):
    target = places.tmp / "elsewhere"
    target.mkdir()
    link = places.project / "Assets" / "Linked"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows without the symlink privilege: a junction is the link a user can make.
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, OSError):
            pytest.skip("this machine cannot make a directory link")
    reply, _ = copy(places, completed(places, "a.wav"), "Assets/Linked/a.wav")
    assert reply["ok"] is False and "link" in reply["error"]
    assert not (target / "a.wav").exists()


def test_refuses_when_no_project_is_open(places):
    reply, _ = copy(places, completed(places, "a.wav"), "Assets/a.wav", workspace=places.tmp / "none")
    assert reply["ok"] is False and "No project" in reply["error"]


# ---- through the backend route: cards off, and a card answered after the wait ----

class RouteHost(Host):
    async def __call__(self, runtime, op, conversation_id, args):
        return await super().__call__(runtime, op, conversation_id, args)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_policy, "conversation_turn_in_flight", lambda cid: cid in (1, 2))
    monkeypatch.setattr(gateway, "APPROVAL_WAIT_S", 0.05)
    host = RouteHost()
    monkeypatch.setattr(gateway, "post_cmd", host)
    rows = {}
    db = MagicMock()
    db.db_path = str(tmp_path / "db.sqlite")
    db.get_setting.side_effect = rows.get
    db.set_setting.side_effect = rows.__setitem__
    db.get_conversation_owner.return_value = 1
    db.get_conversation_parent.return_value = None
    db.get_side_of.return_value = None
    db.get_last_workspace.return_value = str(tmp_path / "Game")
    approval_mode.set_ui_secret(SECRET)
    approval_mode.set_mode("auto", source="test")
    browser_settings.set_enabled(db, True)
    cards.reset()
    app = FastAPI()
    app.include_router(create_conversation_router(db, {}))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/browser/runtime", json={"running": True, "port": 4711, "token": TOKEN},
                           headers=UI).status_code == 200
        yield SimpleNamespace(db=db, rows=rows, client=client, host=host, tmp=tmp_path)
    for card in cards.list_pending():
        GATE_OWNERS.pop(card["card_id"], None)
    cards.reset()
    approval_mode.set_ui_secret("")


def downloads(env):
    return env.client.post("/browser/command", json={"tool": "browser_downloads", "args": {}, "conversation_id": 1}).json()


def wait_for(predicate):
    for _ in range(200):
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_cards_off_resumes_without_a_card(env):
    assert env.client.put("/browser/settings/cards", json={"cards_off": True}, headers=UI).status_code == 200
    reply = downloads(env)
    assert reply["ok"] is True and json.loads(reply["text"])["download"]["state"] == "progressing"
    assert cards.list_pending() == []
    assert env.host.ops() == ["downloads", "download_resume"]


def test_route_card_shows_the_measured_fields_and_a_later_approval_resumes(env):
    reply = downloads(env)
    data = json.loads(reply["text"])
    assert data["needs_approval"] is True and data["download"]["state"] == "awaiting_approval"
    [pending] = cards.list_pending()
    card = cards.get(pending["card_id"])
    assert card.tool == "browser_download" and "pack.zip" in card.risk
    params = env.client.get("/mcp-pending").json()["pending"][pending["card_id"]]["params"]
    assert params["filename"] == "pack.zip" and params["bytes"] == 2048 and params["host"] == "cdn.files.example"
    answered = env.client.post(f"/mcp-approval-respond/{pending['card_id']}", json={"approved": True}, headers=UI)
    assert answered.status_code == 200
    assert wait_for(lambda: "download_resume" in env.host.ops()), env.host.ops()
    assert "download_cancel" not in env.host.ops()


def test_route_later_rejection_cancels(env):
    downloads(env)
    [pending] = cards.list_pending()
    env.client.post(f"/mcp-approval-respond/{pending['card_id']}", json={"approved": False}, headers=UI)
    assert wait_for(lambda: "download_cancel" in env.host.ops()), env.host.ops()


def test_route_copy_uses_the_chat_workspace(env):
    project = env.tmp / "Game"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    folder = env.tmp / "Downloads"
    folder.mkdir()
    (folder / "a.wav").write_bytes(b"wav")
    env.host.notice = None
    env.host.completed = {"dwav1": {"id": "dwav1", "filename": "a.wav", "state": "completed",
                                    "savePath": str(folder / "a.wav"), "dir": str(folder)}}
    reply = env.client.post("/browser/command", json={"tool": "browser_download_to_project",
                                                      "args": {"id": "dwav1", "dest": "Assets/a.wav"},
                                                      "conversation_id": 1}).json()
    assert reply["ok"] is True, reply
    assert (project / "Assets" / "a.wav").read_bytes() == b"wav"


# ---- turn 2: blocker fixes ----

class ScriptedHost(Host):
    """A host whose next non-download reply is merged with `extra` (a URL, a refusal, more notices)."""

    def __init__(self, extra=None, tab_id=None, **kwargs):
        super().__init__(**kwargs)
        self.extra = dict(extra or {})
        self.tab_id = tab_id

    async def __call__(self, runtime, op, conversation_id, args):
        reply = await super().__call__(runtime, op, conversation_id, args)
        if op == "describe" and self.tab_id is not None:
            reply["tab_id"] = self.tab_id
        if op not in ("describe", "download_resume", "download_cancel", "download_info") and self.extra:
            reply.update(self.extra)
            self.extra = {}
        return reply


def tools(deck):
    return [tool for _, tool, _ in deck.opened]


def test_a_page_named_button_does_not_turn_another_approval_into_a_download_pass():
    # blind SEC-1: the pass comes from the rule that opened the card, not from the card's text.
    element = {"role": "button", "name": "Delete draft starts a download", "tag": "button"}
    host = Host(element=element, notice=None)
    handler, deck = gateway_for(host, {"status": "resolved", "approved": True})
    assert run(handler, "click", {"snapshot_id": "s1", "ref": "4"})["ok"] is True
    host.notice = dict(NOTICE)
    run(handler, "scroll", {"direction": "down"})
    assert tools(deck) == ["browser_click", "browser_download"]
    assert policy.download_click("click", DOWNLOAD_LINK) is True
    assert policy.download_click("click", element) is False
    assert policy.download_click("click", {**DOWNLOAD_LINK, "name": "Delete"}) is False


@pytest.mark.parametrize("case", ["page_change", "stop", "failed_click", "late", "other_tab", "other_file"])
def test_the_click_pass_is_bound_to_its_click(case):
    # blind LC-4: valid only for the first agent download within 10 s, on that tab, page and Stop generation.
    state = dict(STATE)
    clock = [100.0]
    host = ScriptedHost(element=DOWNLOAD_LINK, notice=None, tab_id=7,
                        extra={"ok": False, "error": "timeout"} if case == "failed_click" else None)
    handler, deck = gateway_for(host, {"status": "resolved", "approved": True}, now=lambda: clock[0])
    asyncio.run(handler.call(3, "click", {"snapshot_id": "s1", "ref": "4"}, lambda: dict(state)))
    notice = dict(NOTICE, tab_id=7)
    if case == "page_change":
        host.extra = {"url": "https://other.example/"}
    elif case == "stop":
        state["stop_gen"] = 1
    elif case == "late":
        clock[0] += gateway.DOWNLOAD_CLICK_WINDOW_S + 1
    elif case == "other_tab":
        notice["tab_id"] = 8
    elif case == "other_file":
        notice.update(filename="other.zip", host="elsewhere.example")
    host.notice = notice
    reply = asyncio.run(handler.call(3, "scroll", {"direction": "down"}, lambda: dict(state)))
    assert tools(deck) == ["browser_click", "browser_download"], (case, body(reply))
    assert "approved_by" not in body(reply)["download"]


def test_the_click_pass_covers_its_own_download_once():
    host = ScriptedHost(element=DOWNLOAD_LINK, notice=None, tab_id=7)
    handler, deck = gateway_for(host, {"status": "resolved", "approved": True})
    run(handler, "click", {"snapshot_id": "s1", "ref": "4"})
    host.notice = dict(NOTICE, tab_id=7)
    host.extra = {"downloadsPending": [dict(NOTICE, id="dsecond", tab_id=7)]}
    reply = body(run(handler, "scroll", {"direction": "down"}))
    assert reply["download"]["approved_by"] == "the click card"
    # Only the first: the second download of that reply gets its own card.
    assert tools(deck) == ["browser_click", "browser_download"]
    assert "approved_by" not in reply["downloadsPending"][0]


def test_every_paused_download_of_one_reply_gets_its_card():
    # blind LC-1: the host reports several; the gateway settles each.
    blocked = dict(NOTICE, id="dblocked", filename="x.exe", state="blocked", reason=".exe files are never downloaded")
    host = ScriptedHost(extra={"downloadsPending": [dict(NOTICE, id="dtwo", filename="two.mp3"), blocked]})
    handler, deck = gateway_for(host, {"status": "resolved", "approved": True})
    reply = body(run(handler, "scroll", {"direction": "down"}))
    assert [params["filename"] for _, _, params in deck.opened] == ["pack.zip", "two.mp3"]
    assert [(op, args.get("id")) for op, _, args in host.cmds if op == "download_resume"] == [
        ("download_resume", "dabc123"), ("download_resume", "dtwo")]
    assert reply["download"]["state"] == "progressing"
    assert [d["state"] for d in reply["downloadsPending"]] == ["progressing", "blocked"]


def test_unanswered_cards_of_several_downloads_all_need_approval():
    host = ScriptedHost(extra={"downloadsPending": [dict(NOTICE, id="dtwo", filename="two.mp3")]})
    handler, deck = gateway_for(host, None)
    reply = body(run(handler, "scroll", {"direction": "down"}))
    assert reply["needs_approval"] is True
    assert "note" in reply["download"] and "note" in reply["downloadsPending"][0]


def test_a_user_control_refusal_still_opens_the_download_card():
    # blind LC-2: the refusal drops the reply, not the paused download.
    host = ScriptedHost(notice=None, extra={"ok": False, "error": "user_control", "download": dict(NOTICE)})
    handler, deck = gateway_for(host, {"status": "resolved", "approved": False})
    reply = run(handler, "snapshot", {})
    assert reply == {"ok": False, "error": gateway.USER_CONTROL}
    assert tools(deck) == ["browser_download"]
    assert "download_cancel" in host.ops()


def test_blocked_type_list_has_the_windows_launchers_installers_and_images():
    # blind SEC-4 (the agreed set).
    for suffix in ("url", "msixbundle", "appxbundle", "appinstaller", "application", "gadget", "vb", "ws", "sct", "chm",
                   "inf", "msp", "mst", "jnlp", "xll", "xla", "xlam", "scf", "iso", "img", "vhd", "vhdx", "library-ms",
                   "settingcontent-ms", "diagcab", "website", "lnk", "scr", "hta", "cpl"):
        assert policy.agent_blocked_suffix(f"file.pdf.{suffix.upper()}. ") == f".{suffix}"
        assert policy.project_copy_refusal(f"Assets/file.{suffix}")
    assert policy.agent_blocked_suffix("song.wav") == ""


@pytest.mark.parametrize("name,dest", [
    ("pre-commit", ".git/hooks/pre-commit"),
    ("tasks.json", ".vscode/tasks.json"),
    ("a.png", "ProjectSettings/a.png"),
    ("a.png", "Packages/a.png"),
    ("a.png", "Assets/.hidden/a.png"),
    ("a.png", "Assets/.a.png"),
    ("a.png", "Assets/Plugins./a.png"),
    ("a.png", "Assets/Editor./a.png"),
    ("a.png", "Assets/PLUGINS /a.png"),
    ("a.png", "Assets/Plugins​/a.png"),
    ("a.png", "Assets/Art/a?.png"),
    ("a.png", "Assets/Art/a.png."),
    ("a.png", "Assets/Art/a\u0001.png"),
    ("readme", "Assets/readme"),
    ("a.png", "Assets/a"),
])
def test_copies_only_into_assets_with_plain_names_and_a_type(places, name, dest):
    # blind SEC-2, SEC-3: Assets/ only, no dot names, nothing Windows would rename, a file type on both ends.
    (places.project / ".git" / "hooks").mkdir(parents=True)
    (places.project / ".vscode").mkdir()
    reply, host = copy(places, completed(places, name), dest)
    assert reply["ok"] is False, reply
    assert not [p for p in places.project.rglob("*") if p.is_file()]


def test_copy_into_assets_is_case_insensitive(places):
    reply, _ = copy(places, completed(places, "a.wav"), "assets/Audio/a.wav")
    assert reply["ok"] is True, reply
    assert (places.project / "Assets" / "Audio" / "a.wav").is_file()


def test_a_page_change_keeps_an_open_download_card(env, monkeypatch):
    # blind LC-3: a download often moves the page; its card stays until answered.
    first = downloads(env)
    assert json.loads(first["text"])["download"]["state"] == "awaiting_approval"
    assert len(cards.list_pending()) == 1
    env.client.post("/browser/command", json={"tool": "browser_scroll", "args": {"direction": "down"}, "conversation_id": 1})
    original = Host.__call__

    async def moved(self, runtime, op, conversation_id, args):
        reply = await original(self, runtime, op, conversation_id, args)
        return {**reply, "url": "https://files.example/thanks"} if "url" in reply else reply
    monkeypatch.setattr(RouteHost, "__call__", moved)
    env.client.post("/browser/command", json={"tool": "browser_scroll", "args": {"direction": "down"}, "conversation_id": 1})
    time.sleep(0.3)
    [pending] = cards.list_pending()
    assert "download_cancel" not in env.host.ops()
    env.client.post(f"/mcp-approval-respond/{pending['card_id']}", json={"approved": True}, headers=UI)
    assert wait_for(lambda: "download_resume" in env.host.ops()), env.host.ops()


@pytest.mark.parametrize("language", ["EN", "TR"])
def test_the_agent_guide_tells_the_download_rules(language):
    # blind ADJ-1: the guide injected into every browser turn names the tools and the rules.
    import agent_guide
    text = getattr(agent_guide, f"BROWSER_GUIDE_{language}")
    for needle in ("browser_downloads", "browser_download_to_project", "Assets/", ".exe", ".lnk", ".iso"):
        assert needle in text, needle
    card_off = {"EN": "none when the user turned browser cards off", "TR": "kartlarını kapattıysa kart çıkmaz"}
    one_card = {"EN": "one approval card", "TR": "bir kez onay kartı"}
    assert card_off[language] in text and one_card[language] in text
