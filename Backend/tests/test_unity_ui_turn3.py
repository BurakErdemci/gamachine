"""Fixes after the blind check of the Unity first-time flows (12 Nov 2026):
chat-title language on short Turkish requests, and the editor-loading scan
that must never sit on a request. Process queries are fakes."""
import asyncio
import logging
import subprocess
import threading
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import routes.mcp_routes as mr
import routes.scene_editor_routes as se
from agentic import chat_titles as ct
from unity_ai_mcp import unity_project_presence as upp

WIN_UNITY = r"C:\Program Files\Unity\Hub\Editor\6000.4.4f1\Editor\Unity.exe"
MAC_UNITY = "/Applications/Unity/Hub/Editor/6000.4.4f1/Unity.app/Contents/MacOS/Unity"


# ── F1: title language ───────────────────────────────────────────────────────

_PASTED_ERROR = ("Bu hatayı düzelt:\nAssets/Scripts/Player.cs(12,5): error CS0103: The name 'rb' does not exist "
                 "in the current context\n  at Player.Update () [0x00001] in <abc>:0\n"
                 "  at the end of the file in the project for the scene")
TURKISH = [
    "Player'a ziplama ekle",
    "Bu script'i duzelt",
    "oyuncu hareket etmiyor bak",
    _PASTED_ERROR,
    "Kamerayı oyuncunun arkasına koy",
    "Düşman yapay zekası ekle",
]
ENGLISH = [
    "Add a sphere on top of the cup",
    "NullReferenceException in Player.Update",
    "Why is my Rigidbody falling through the floor?",
    "Bu is not working, can you fix the player?",
]


def _user(text):
    return [{"role": "user", "content": text}]


@pytest.mark.parametrize("text", TURKISH)
@pytest.mark.parametrize("ui", ["tr", "en"])
def test_turkish_requests_come_out_turkish_under_either_ui(text, ui):
    assert ct.title_language(_user(text), ui) == "tr"


@pytest.mark.parametrize("text", ENGLISH)
def test_english_requests_come_out_english_under_an_english_ui(text):
    assert ct.title_language(_user(text), "en") == "en"


@pytest.mark.parametrize("text", [ENGLISH[0], ENGLISH[2], ENGLISH[3]])
def test_english_requests_come_out_english_under_a_turkish_ui(text):
    assert ct.title_language(_user(text), "tr") == "en"


def test_a_pasted_english_error_does_not_make_a_turkish_chat_english():
    assert ct.title_language(_user(_PASTED_ERROR), "tr") == "tr"


def test_the_ui_language_decides_when_the_words_do_not():
    assert ct.title_language(_user("Unity 6000.4.4f1"), "tr") == "tr"
    assert ct.title_language(_user("ok"), "en") == "en"


# ── F3: the editor-loading scan is never on the request path ────────────────

@pytest.fixture
def scan(monkeypatch, tmp_path):
    """A Windows process query that blocks until released and counts what it spawned."""
    project = tmp_path / "Deneme"
    (project / "Assets").mkdir(parents=True)
    state = {"calls": [], "gate": threading.Event(), "mode": "ok"}
    monkeypatch.setattr(mr, "_process_table", None)
    monkeypatch.setattr(mr, "_scan_failure_logged", False, raising=False)
    monkeypatch.setattr(mr.sys, "platform", "win32")

    def run(args, **kwargs):
        state["calls"].append(args[0])
        state["gate"].wait(3)
        if state["mode"] == "fail":
            raise subprocess.TimeoutExpired(args, 5)
        if args[0] == "tasklist":
            return subprocess.CompletedProcess(args, 0, '"Unity.exe","7001"\n', "")
        return subprocess.CompletedProcess(args, 0, f'12\t"{WIN_UNITY}" -projectPath "{project}"\n', "")

    monkeypatch.setattr(mr.subprocess, "run", run)
    state["project"] = project
    yield state
    state["gate"].set()
    _wait_idle()


def _wait_idle(timeout=5.0):
    lock = getattr(mr, "_scan_lock", None)
    end = time.time() + timeout
    while lock is not None and lock.locked() and time.time() < end:
        time.sleep(0.01)
    assert lock is None or not lock.locked()


def test_status_answers_at_once_while_the_scan_hangs(scan, monkeypatch):
    async def fake_status():
        return {"status": "off"}

    monkeypatch.setattr(mr.unity_mcp_manager, "get_status", fake_status)
    monkeypatch.setattr(mr, "_check_token", lambda token: None)
    monkeypatch.setattr(mr, "require_saved_workspace_when_remote", lambda *a, **k: None)
    monkeypatch.setattr(upp, "editor_pid", lambda workspace: None)
    app = FastAPI()
    app.include_router(mr.router)
    started = time.time()
    body = TestClient(app).get("/mcp/unity/status", params={"workspace": str(scan["project"])}).json()
    assert time.time() - started < 2.0
    assert body["editor_open"] is False and "editor_loading" not in body  # a cold cache reads as "not loading"
    scan["gate"].set()
    _wait_idle()
    assert mr.editor_loading(str(scan["project"])) is not None  # the next poll sees what the scan found


def test_concurrent_cold_polls_start_one_scan(scan):
    results = []
    threads = [threading.Thread(target=lambda: results.append(mr.editor_loading(str(scan["project"]))))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert results == [None] * 6
    scan["gate"].set()
    _wait_idle()
    assert scan["calls"].count("tasklist") == 1


def test_a_failed_scan_is_cached_for_15_seconds_and_logged_once(scan, monkeypatch, caplog):
    clock = {"now": 1000.0}
    monkeypatch.setattr(mr.time, "monotonic", lambda: clock["now"])
    scan["mode"] = "fail"
    scan["gate"].set()
    path = str(scan["project"])
    with caplog.at_level(logging.WARNING, logger=mr.logger.name):
        for step in range(5):
            assert mr.editor_loading(path) is None
            _wait_idle()
            clock["now"] += 8
    # polls at t=0, 8, 16, 24, 32: scans at 0, 16 and 32 only
    assert scan["calls"].count("tasklist") == 3
    assert len([r for r in caplog.records if r.name == mr.logger.name and "scan failed" in r.getMessage()]) == 1


def test_one_scan_has_an_overall_deadline(scan, monkeypatch):
    timeouts = []
    real = mr.subprocess.run

    def run(args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return real(args, **kwargs)

    monkeypatch.setattr(mr.subprocess, "run", run)
    scan["gate"].set()
    mr._read_unity_editors()
    assert timeouts and all(t <= 5.0 for t in timeouts)


def test_a_mac_scan_is_one_ps_call_however_many_workers_run(monkeypatch, tmp_path):
    project = tmp_path / "Game"
    (project / "Assets").mkdir(parents=True)
    monkeypatch.setattr(mr, "_process_table", None)
    monkeypatch.setattr(mr.sys, "platform", "darwin")
    calls = []
    rows = [f"{100 + i} 00:30 {MAC_UNITY} -batchMode -projectPath {project} -name AssetImportWorker{i}\n"
            for i in range(8)]
    rows.append(f"99 01:30 {MAC_UNITY} -projectPath {project}\n")

    def run(args, **kwargs):
        calls.append((args, kwargs.get("env") is not None))
        return subprocess.CompletedProcess(args, 0, "".join(rows), "")

    monkeypatch.setattr(mr.subprocess, "run", run)
    mr.refresh_process_table()
    assert len(calls) == 1 and calls[0][1] is True
    assert mr.editor_loading(str(project)) is not None


# ── F4: the other refusals say "still opening" too ──────────────────────────

@pytest.fixture
def opening(monkeypatch, tmp_path):
    project = tmp_path / "Deneme"
    (project / "Assets").mkdir(parents=True)
    monkeypatch.setattr(upp, "editor_pid", lambda workspace: None)
    monkeypatch.setattr(upp, "open_project_names", lambda: [])
    now = time.monotonic()
    monkeypatch.setattr(mr, "_process_table", (now, [(f'"{WIN_UNITY}" -projectPath "{project}"', now - 30)]))
    se._presence_cache.clear()
    return project


def test_the_scene_editor_refusal_says_the_editor_is_still_opening(opening):
    message = se._project_refusal(str(opening))
    assert "hâlâ açıyor" in message and "Deneme" in message


def test_the_workspace_switch_refusal_says_the_editor_is_still_opening(opening, monkeypatch):
    monkeypatch.setattr(mr, "unity_mcp_disabled", lambda: False)
    monkeypatch.setattr(mr.unity_mcp_manager, "is_unity_running", lambda: True)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(mr.start_mcp_for(str(opening), None))
    assert caught.value.status_code == 409 and "hâlâ açıyor" in caught.value.detail


def test_a_project_that_is_not_loading_keeps_the_old_refusal(opening, monkeypatch):
    monkeypatch.setattr(mr, "_process_table", (time.monotonic(), []))
    assert "açık değil" in se._project_refusal(str(opening))
