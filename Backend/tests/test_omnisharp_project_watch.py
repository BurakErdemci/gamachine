"""C# files a running OmniSharp does not know are part of the project.

Measured 28 Sep 2026 (Windows, bundled OmniSharp, copy of the owner's Unity
project):
- A .cs file outside every csproj gets syntax errors only (CS1002); CS0029 and
  CS0103 never come. The same errors in an in-project file came in 0.86 s.
- An agent writes a new script; Unity adds it to Assembly-CSharp.csproj only
  when it regenerates the projects (file 00:44:55, csproj 00:56:31).
- A csproj rewritten on disk while OmniSharp runs: no effect in 30 s. The same
  rewrite followed by `workspace/didChangeWatchedFiles`: both semantic errors
  in 2.5 s. Nothing in Backend/app sent that notification.
- The Unity refresh posts to :8080, a server started only by the MCP toggle;
  after an app restart it was down (WinError 10061) and the refresh ran once,
  at OmniSharp start.
"""
import asyncio
import os

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from omnisharp import omnisharp_manager as om

_OLD, _NEW = 1_000_000, 2_000_000

_CSPROJ = """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="4.0" DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup>
{items}
  </ItemGroup>
</Project>
"""


def _write(path: str, body: str = "", mtime: int | None = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _csproj(ws: str, *includes: str, mtime: int = _OLD, name: str = "Assembly-CSharp.csproj") -> str:
    items = "\n".join(f'    <Compile Include="{i}" />' for i in includes)
    return _write(os.path.join(ws, name), _CSPROJ.format(items=items), mtime)


class _FakeClient:
    alive = True

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    def notify(self, method: str, params: dict) -> None:
        self.sent.append((method, params))

    async def stop(self) -> None:
        self.alive = False

    def of(self, method: str) -> list[dict]:
        return [p for m, p in self.sent if m == method]


@pytest.fixture
def ws(tmp_path):
    """A space in the folder name, like the owner's "ai proje"."""
    root = str(tmp_path / "ai proje")
    os.makedirs(os.path.join(root, "Assets", "Scripts"))
    return root


@pytest.fixture
def running(ws, monkeypatch):
    """A manager that believes OmniSharp is up on `ws`. The diagnostics wait is
    short-circuited and the Unity side is unreachable unless a test says so."""
    _write(os.path.join(ws, "ai proje.sln"), "", _OLD)
    _csproj(ws, r"Assets\Scripts\Player.cs")
    player = _write(os.path.join(ws, "Assets", "Scripts", "Player.cs"), "class Player {}", _OLD)
    mgr = om.OmniSharpManager()
    fake = _FakeClient()
    mgr._client = fake
    mgr._workspace = ws
    mgr._proj_mtimes = om._project_files(ws)   # what ensure_started records
    monkeypatch.setattr(om, "_unity_api_up", lambda: False)
    mgr._diag_ping[om._norm_key(player)] = float("inf")
    return mgr, fake, player


def _sync(mgr, path, text="class X {}"):
    mgr._diag_ping[om._norm_key(os.path.abspath(path))] = float("inf")

    async def go():
        out = await mgr.sync_document(path, text)
        if mgr._sync_task:
            await mgr._sync_task
        return out
    return asyncio.run(go())


class TestProjectFileChangesReachOmniSharp:
    def test_a_changed_csproj_is_announced_exactly_once(self, running):
        mgr, fake, player = running
        _csproj(mgr._workspace, r"Assets\Scripts\Player.cs", r"Assets\Scripts\New.cs",
                mtime=_NEW)
        _sync(mgr, player)
        _sync(mgr, player, "class Player { }")
        notes = fake.of("workspace/didChangeWatchedFiles")
        assert len(notes) == 1
        assert notes[0]["changes"] == [
            {"uri": om._path_to_uri(os.path.join(mgr._workspace, "Assembly-CSharp.csproj")),
             "type": 2}]

    def test_the_announcement_precedes_the_document_text(self, running):
        """OmniSharp should load the new project before it analyses the text,
        so the first answer can already carry the semantic errors."""
        mgr, fake, player = running
        _csproj(mgr._workspace, r"Assets\Scripts\Player.cs", mtime=_NEW)
        _sync(mgr, player)
        methods = [m for m, _ in fake.sent]
        assert methods.index("workspace/didChangeWatchedFiles") < methods.index("textDocument/didChange")

    def test_nothing_is_announced_when_no_project_file_changed(self, running):
        mgr, fake, player = running
        _sync(mgr, player)
        _sync(mgr, player, "class Player { }")
        assert fake.of("workspace/didChangeWatchedFiles") == []

    def test_a_new_and_a_removed_project_file_carry_their_own_change_types(self, running):
        mgr, fake, player = running
        _csproj(mgr._workspace, mtime=_NEW, name="Game.Runtime.csproj")
        os.remove(os.path.join(mgr._workspace, "ai proje.sln"))
        _sync(mgr, player)
        (note,) = fake.of("workspace/didChangeWatchedFiles")
        types = {c["uri"].rsplit("/", 1)[-1]: c["type"] for c in note["changes"]}
        assert types == {"Game.Runtime.csproj": 1, "ai%20proje.sln": 3}

    def test_the_late_diagnostics_read_also_picks_up_a_regenerated_csproj(self, running):
        """The editor re-asks after the change answer; a csproj Unity wrote in
        the meantime must reach OmniSharp without another keystroke."""
        mgr, fake, player = running
        _csproj(mgr._workspace, r"Assets\Scripts\Player.cs", mtime=_NEW)
        mgr.latest_diagnostics(player)
        assert len(fake.of("workspace/didChangeWatchedFiles")) == 1


class TestInProject:
    def test_a_listed_file_is_in_the_project(self, running):
        mgr, _, player = running
        assert mgr.in_project(player) is True

    def test_a_new_script_unity_has_not_listed_yet_is_not(self, running):
        mgr, _, _ = running
        assert mgr.in_project(os.path.join(mgr._workspace, "Assets", "Scripts", "New.cs")) is False

    def test_path_casing_and_separators_do_not_matter(self, running):
        """Unity writes `Assets\\Scripts\\Player.cs`; the renderer sends
        `assets/scripts/player.cs` spellings through `_abs`."""
        mgr, _, _ = running
        spelled = os.path.join(mgr._workspace, "assets/SCRIPTS/player.CS")
        assert mgr.in_project(spelled) is True

    def test_msbuild_escapes_and_spaces_in_include_are_understood(self, running):
        mgr, _, _ = running
        _csproj(mgr._workspace, r"Assets\My Scripts\Enemy%3BBoss.cs", mtime=_NEW)
        assert mgr.in_project(os.path.join(mgr._workspace, "Assets", "My Scripts", "Enemy;Boss.cs")) is True

    def test_the_answer_follows_a_regenerated_csproj(self, running):
        mgr, _, _ = running
        new = os.path.join(mgr._workspace, "Assets", "Scripts", "New.cs")
        assert mgr.in_project(new) is False
        _csproj(mgr._workspace, r"Assets\Scripts\Player.cs", r"Assets\Scripts\New.cs", mtime=_NEW)
        assert mgr.in_project(new) is True

    def test_an_sdk_style_project_covers_its_folder(self, tmp_path):
        ws = str(tmp_path / "sdk")
        _write(os.path.join(ws, "Tool.csproj"), '<Project Sdk="Microsoft.NET.Sdk"></Project>')
        mgr = om.OmniSharpManager()
        mgr._workspace = ws
        assert mgr.in_project(os.path.join(ws, "src", "A.cs")) is True

    def test_non_csharp_files_are_not_judged(self, running):
        mgr, _, _ = running
        assert mgr.in_project(os.path.join(mgr._workspace, "Assets", "a.shader")) is None

    def test_no_csproj_at_all_means_not_in_the_project(self, tmp_path):
        mgr = om.OmniSharpManager()
        mgr._workspace = str(tmp_path)
        assert mgr.in_project(str(tmp_path / "Assets" / "A.cs")) is False


class TestUnitySyncRetry:
    def _new_file(self, mgr):
        return _write(os.path.join(mgr._workspace, "Assets", "Scripts", "New.cs"), "class New {}", _NEW)

    def test_no_request_is_made_when_the_unity_server_is_down(self, running, monkeypatch):
        mgr, _, _ = running
        posts = []
        monkeypatch.setattr(om.urllib.request, "urlopen", lambda *a, **k: posts.append(a))
        _sync(mgr, self._new_file(mgr))
        assert posts == []

    def test_a_file_outside_the_project_asks_unity_once_per_interval(self, running, monkeypatch):
        mgr, _, _ = running
        monkeypatch.setattr(om, "_unity_api_up", lambda: True)
        calls = []
        monkeypatch.setattr(mgr, "_maybe_sync_csproj",
                            lambda ws, reason=None: calls.append((ws, reason)))
        new = self._new_file(mgr)
        _sync(mgr, new)
        _sync(mgr, new, "class New { }")
        assert len(calls) == 1
        assert calls[0][1]      # forced: a missing file is stale projects whatever the mtimes say
        mgr._sync_retry_after = 0.0          # the interval has passed
        _sync(mgr, new, "class New {  }")
        assert len(calls) == 2

    def test_a_file_in_the_project_does_not_ask_unity(self, running, monkeypatch):
        mgr, _, player = running
        monkeypatch.setattr(om, "_unity_api_up", lambda: True)
        calls = []
        monkeypatch.setattr(mgr, "_maybe_sync_csproj", lambda *a, **k: calls.append(a))
        _sync(mgr, player)
        assert calls == []

    def test_a_successful_refresh_is_announced_without_waiting_for_a_keystroke(
        self, running, monkeypatch
    ):
        mgr, fake, _ = running
        monkeypatch.setattr(om, "_unity_api_up", lambda: True)

        def unity_regenerates(ws, reason=None):
            _csproj(ws, r"Assets\Scripts\Player.cs", r"Assets\Scripts\New.cs", mtime=_NEW + 1)
            return None
        monkeypatch.setattr(mgr, "_maybe_sync_csproj", unity_regenerates)
        new = self._new_file(mgr)
        _sync(mgr, new)
        assert len(fake.of("workspace/didChangeWatchedFiles")) == 1
        assert mgr.in_project(new) is True


class TestLateDiagnosticsRoute:
    @pytest.fixture
    def api(self, running, monkeypatch):
        from routes import lsp_routes

        mgr, fake, player = running

        class _Db:
            def get_last_workspace(self, _uid):
                return mgr._workspace

        monkeypatch.setattr(lsp_routes, "get_omnisharp_manager", lambda: mgr)
        # The module keeps one router; once the app has registered on it, its
        # routes (and their database) would answer before these.
        monkeypatch.setattr(lsp_routes, "router", APIRouter())
        app = FastAPI()
        app.include_router(lsp_routes.create_lsp_router(_Db()))
        return TestClient(app), mgr, fake

    def test_diagnostics_published_after_the_change_answer_are_returned(self, api):
        client, mgr, _ = api
        new = _write(os.path.join(mgr._workspace, "Assets", "Scripts", "New.cs"), "x", _NEW)
        mgr._on_diags({"uri": om._path_to_uri(new), "diagnostics": [
            {"range": {"start": {"line": 4, "character": 2}, "end": {"line": 4, "character": 9}},
             "message": "CS0103", "severity": 1}]})
        body = client.get("/lsp/diagnostics", params={"path": "Assets/Scripts/New.cs"}).json()
        assert [p["message"] for p in body["problems"]] == ["CS0103"]
        assert body["problems"][0]["line"] == 5
        assert body["inProject"] is False

    def test_the_change_answer_says_whether_the_file_is_in_the_project(self, api):
        client, mgr, _ = api
        body = client.post("/lsp/change", json={"path": "Assets/Scripts/Player.cs",
                                                "text": "class Player {}"}).json()
        assert body["inProject"] is True

    def test_no_project_verdict_while_omnisharp_is_down(self, api):
        client, mgr, fake = api
        fake.alive = False
        body = client.get("/lsp/diagnostics", params={"path": "Assets/Scripts/New.cs"}).json()
        assert body["inProject"] is None
