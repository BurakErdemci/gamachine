"""GET /mcp/unity/status says when this project's editor is still opening (12 Nov 2026: the
first open took ~8 min and the switch said the project was not open). Process queries are fakes."""
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.mcp_routes as mr
from unity_ai_mcp import unity_project_presence as upp

MAC_UNITY = "/Applications/Unity/Hub/Editor/6000.4.4f1/Unity.app/Contents/MacOS/Unity"
WIN_UNITY = r"C:\Program Files\Unity\Hub\Editor\6000.4.4f1\Editor\Unity.exe"


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "Unity Projects" / "Deneme"
    (path / "Assets").mkdir(parents=True)
    other = tmp_path / "Unity Projects" / "Other"
    (other / "Assets").mkdir(parents=True)
    return path, other


@pytest.fixture
def table(monkeypatch):
    """The process table each platform's query reads; rows are (pid, seconds, command line)."""
    rows = []
    calls = []
    monkeypatch.setattr(mr, "_process_table", None)
    # The app scans in a background thread; these tests read the result at once.
    monkeypatch.setattr(mr, "_start_scan", mr.refresh_process_table)

    def run(args, **kwargs):
        calls.append(args)
        out = ""
        if args[0] == "tasklist":
            out = "".join(f'"Unity.exe","{pid}","Console","1","512,000 K"\n' for pid, _, _ in rows)
        elif args[0] == "powershell":
            out = "".join(f"{seconds}\t{command}\n" for _, seconds, command in rows)
        elif args == ["ps", "-axo", "pid=,etime=,command="]:
            out = "".join(f"{pid} {seconds // 60:02d}:{seconds % 60:02d} {command}\n" for pid, seconds, command in rows)
        else:
            raise AssertionError(f"unexpected process query {args}")
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(mr.subprocess, "run", run)
    monkeypatch.setattr(upp.subprocess, "run", run)
    return rows, calls


@pytest.fixture
def client(monkeypatch):
    async def fake_status():
        return {"status": "off"}

    monkeypatch.setattr(mr.unity_mcp_manager, "get_status", fake_status)
    monkeypatch.setattr(mr, "_check_token", lambda token: None)
    monkeypatch.setattr(mr, "require_saved_workspace_when_remote", lambda *a, **k: None)
    monkeypatch.setattr(upp, "editor_pid", lambda workspace: None)
    app = FastAPI()
    app.include_router(mr.router)
    return TestClient(app)


def _status(client, workspace):
    return client.get("/mcp/unity/status", params={"workspace": str(workspace)}).json()


def test_mac_editor_started_on_the_project_reads_as_loading(client, project, table, monkeypatch):
    monkeypatch.setattr(mr.sys, "platform", "darwin")
    monkeypatch.setattr(upp.sys, "platform", "darwin")
    path, _ = project
    table[0].append((501, 134, f"{MAC_UNITY} -projectPath {path} -useHub -hubIPC -cloudEnvironment production"))
    body = _status(client, path)
    assert body["editor_open"] is False
    assert body["editor_loading"] is True
    assert body["editor_loading_s"] == 134


def test_windows_hub_quoted_path_reads_as_loading(client, project, table, monkeypatch):
    monkeypatch.setattr(mr.sys, "platform", "win32")
    path, _ = project
    table[0].append((7001, 61, f'"{WIN_UNITY}" -projectpath "{path}" -useHub -hubIPC'))
    body = _status(client, path)
    assert body["editor_loading"] is True and body["editor_loading_s"] == 61


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_another_project_or_an_import_worker_is_not_loading(client, project, table, monkeypatch, platform):
    monkeypatch.setattr(mr.sys, "platform", platform)
    monkeypatch.setattr(upp.sys, "platform", platform)
    path, other = project
    exe = MAC_UNITY if platform == "darwin" else WIN_UNITY
    table[0].append((601, 30, f'"{exe}" -projectPath "{other}" -useHub'))
    table[0].append((602, 30, f'"{exe}" -batchMode -projectPath "{path}" -name AssetImportWorker0'))
    body = _status(client, path)
    assert body["editor_open"] is False
    assert "editor_loading" not in body


def test_no_editor_running_skips_powershell_on_windows(client, project, table, monkeypatch):
    monkeypatch.setattr(mr.sys, "platform", "win32")
    path, _ = project
    assert "editor_loading" not in _status(client, path)
    assert [c[0] for c in table[1]] == ["tasklist"]


def test_an_open_editor_never_reads_the_table(client, project, table, monkeypatch):
    monkeypatch.setattr(upp, "editor_pid", lambda workspace: 4242)
    path, _ = project
    body = _status(client, path)
    assert body["editor_open"] is True and "editor_loading" not in body
    assert table[1] == []


def test_the_table_is_read_at_most_every_15_seconds_and_the_age_keeps_counting(client, project, table, monkeypatch):
    monkeypatch.setattr(mr.sys, "platform", "win32")
    clock = {"now": 1000.0}
    monkeypatch.setattr(mr.time, "monotonic", lambda: clock["now"])
    path, _ = project
    table[0].append((7001, 10, f"{WIN_UNITY} -projectPath {path}"))
    assert _status(client, path)["editor_loading_s"] == 10
    clock["now"] += 8
    assert _status(client, path)["editor_loading_s"] == 18
    assert [c[0] for c in table[1]] == ["tasklist", "powershell"]
    clock["now"] += 8
    _status(client, path)
    assert [c[0] for c in table[1]] == ["tasklist", "powershell", "tasklist", "powershell"]
