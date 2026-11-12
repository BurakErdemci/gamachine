import asyncio
import logging
import os
import re
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
from unity_ai_mcp.unity_mcp_manager import unity_mcp_disabled, unity_mcp_manager
from unity_ai_mcp import unity_project_presence
from auth_utils import _check_token
from spawn_env import build_spawn_env
from routes.workspace_routes import require_saved_workspace_when_remote

router = APIRouter(prefix="/mcp", tags=["mcp"])
logger = logging.getLogger(__name__)


# ── A project's editor that is still opening ────────────────────────────────
# Unity writes Library/EditorInstance.json only once the project is open; the
# first open of a project took ~8 min (Mac, 12 Nov 2026), and in that window
# `editor_pid` says "not open" although our editor is on its way. The editor
# process already carries `-projectPath <project>`, so the process table tells
# the two apart. Reading it costs 0.5-1 s on Windows (PowerShell) and can hang,
# so no request ever waits for it: one background scan at a time fills a cache
# that every reader uses, failures included (a cold or failed cache reads as
# "not loading"). The status poll asks every 8 s; the cache lives 15 s.
_PROCESS_TABLE_TTL = 15.0
_SCAN_TIMEOUT = 5.0
_STALE_AFTER = 3 * _PROCESS_TABLE_TTL  # a cache nobody refreshed this long says nothing any more
_process_table: Optional[tuple] = None  # (monotonic time, [(command line, monotonic start or None)])
_scan_lock = threading.Lock()
_scan_failure_logged = False
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _etime_seconds(value: str) -> Optional[int]:
    """`ps` elapsed time ([[dd-]hh:]mm:ss) in seconds."""
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", value.strip())
    if match is None:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _query(args: list, deadline: float):
    return subprocess.run(args, creationflags=_CREATE_NO_WINDOW, env=build_spawn_env(),
                          capture_output=True, text=True, timeout=max(0.5, deadline - time.monotonic()))


def _read_unity_editors(deadline: Optional[float] = None) -> list:
    """(command line, seconds since start or None) of every running Unity editor."""
    if deadline is None:
        deadline = time.monotonic() + _SCAN_TIMEOUT
    if sys.platform == "win32":
        listed = _query(["tasklist", "/FI", "IMAGENAME eq Unity.exe", "/FO", "CSV", "/NH"], deadline)
        if "unity.exe" not in (listed.stdout or "").lower():
            return []  # the cheap check first: no editor, no PowerShell
        script = ("Get-CimInstance Win32_Process -Filter \"Name='Unity.exe'\" | ForEach-Object { "
                  "\"$([int]((Get-Date) - $_.CreationDate).TotalSeconds)`t$($_.CommandLine)\" }")
        out = _query(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], deadline)
        rows = []
        for line in (out.stdout or "").splitlines():
            head, _, command = line.partition("\t")
            if command.strip():
                rows.append((command.strip(), int(head) if head.strip().isdigit() else None))
        return rows
    # One `ps` for every process: the full command line comes with the table, so the
    # count does not grow with the editor's import workers.
    out = _query(["ps", "-axo", "pid=,etime=,command="], deadline)
    rows = []
    for line in (out.stdout or "").splitlines():
        match = re.match(r"^\s*\d+\s+(\S+)\s+((.*?/(?:Unity\.app/Contents/MacOS|Editor)/Unity)(?:\s.*)?)$", line)
        if match is not None and unity_project_presence._is_unity_editor_executable(match.group(3)):
            rows.append((match.group(2).strip(), _etime_seconds(match.group(1))))
    return rows


def refresh_process_table() -> None:
    """Read the process table into the cache (a failure is cached as "no editors" and logged once);
    does nothing while another scan is in flight."""
    global _process_table, _scan_failure_logged
    if not _scan_lock.acquire(blocking=False):
        return
    try:
        try:
            rows = _read_unity_editors()
            _scan_failure_logged = False
        except Exception as exc:
            rows = []
            if not _scan_failure_logged:
                _scan_failure_logged = True
                logger.warning("Unity editor scan failed (cached as 'no editor' for %.0f s): %s",
                               _PROCESS_TABLE_TTL, exc)
        now = time.monotonic()
        _process_table = (now, [(command, None if age is None else now - age) for command, age in rows])
    finally:
        _scan_lock.release()


def _start_scan() -> None:
    if not _scan_lock.locked():
        threading.Thread(target=refresh_process_table, name="unity-editor-scan", daemon=True).start()


def _unity_editors() -> list:
    """The cached process table (start times stay exact); a stale or cold cache starts a background scan."""
    now = time.monotonic()
    table = _process_table
    if table is None or now - table[0] >= _PROCESS_TABLE_TTL:
        _start_scan()
        table, now = _process_table, time.monotonic()  # inline in tests, a moment later in the app
    if table is None or now - table[0] >= _STALE_AFTER:
        return []
    return [(command, None if start is None else max(0, int(now - start))) for command, start in table[1]]


def _project_paths(command: str) -> list:
    # Unity Hub quotes the path on Windows (`-projectpath "C:\...\My Game"`); unquoted ones,
    # spaces included, are resolved the way unity_project_presence resolves them.
    quoted = re.search(r'(?<!\S)-projectPath\s+"([^"]+)"', command, re.IGNORECASE)
    return [quoted.group(1)] if quoted else unity_project_presence._project_candidates(command)


def editor_loading(workspace_path) -> Optional[dict]:
    """{"seconds": n or None} when a Unity editor was started on this project but has not opened it
    yet (call it only when `editor_pid` found none); None otherwise or when the cache has nothing."""
    try:
        for command, seconds in _unity_editors():
            if unity_project_presence._is_import_worker(command):
                continue
            if any(unity_project_presence._same_dir(path, workspace_path) for path in _project_paths(command)):
                return {"seconds": seconds}
    except Exception:
        pass
    return None


def project_mismatch_detail(workspace_path) -> Optional[str]:
    """unity_project_presence.mcp_mismatch_detail, saying "still opening" while this project's editor loads."""
    detail = unity_project_presence.mcp_mismatch_detail(workspace_path)
    if detail is None or editor_loading(workspace_path) is None:
        return detail
    name = os.path.basename(os.path.normpath(workspace_path))
    return f"Unity bu projeyi hâlâ açıyor: {name}. Açılması bitince tekrar dene."


class MCPToggleRequest(BaseModel):
    enabled: bool
    workspace_path: Optional[str] = None
    language: Optional[str] = None


@router.post("/unity/toggle")
async def toggle_unity_mcp(req: MCPToggleRequest, background_tasks: BackgroundTasks, x_session_token: str = Header(alias="X-Session-Token", default=""),
                           x_ui_lang: str | None = Header(default=None, alias="X-UI-Lang")):
    """
    Unity MCP sunucusunu başlatır veya durdurur.
    Toggle ON için Unity Editor'ün açık olması ZORUNLUDUR.
    """
    _check_token(x_session_token)
    if req.workspace_path:
        require_saved_workspace_when_remote(req.workspace_path, x_session_token)
    async with unity_mcp_manager.mcp_lock:
        unity_mcp_manager.reset_supervision()
        if req.enabled:
            return await start_mcp_for(req.workspace_path, background_tasks, req.language or x_ui_lang)
        return stop_mcp(req.workspace_path)


async def start_mcp_for(workspace_path, background_tasks, language=None):
    if unity_mcp_disabled():
        # start_server refuses under the switch with no last_error, which surfaced as a bare 500.
        raise HTTPException(status_code=409,
                            detail="Unity MCP is disabled for this backend (GAMACHINE_NO_UNITY_MCP).")
    if language in ("tr", "en"):
        unity_mcp_manager.language = language
    # Unity açık değilse hemen hata dön
    if not unity_mcp_manager.is_unity_running():
        raise HTTPException(
            status_code=409,
            detail="Unity Editor açık değil. Lütfen önce Unity'yi açın."
        )

    if workspace_path:
        detail = await asyncio.to_thread(project_mismatch_detail, workspace_path)
        if detail is not None:
            raise HTTPException(status_code=409, detail=detail)

    if unity_mcp_manager.is_running():
        status = await unity_mcp_manager.get_status()
        return {"status": "already_running", **status}

    success = unity_mcp_manager.start_server(reason="toggle_on")
    if not success:
        # Sebebi olduğu gibi geçir: en sık vaka 8080'in başka bir süreçte
        # olması ve o süreci öldürmek artık seçenek değil, dolayısıyla
        # kullanıcının ne yapacağını bilmesi tek çıkış yolu.
        raise HTTPException(
            # Durum kodu bilerek 500'de bırakıldı: 409 bu uçta zaten "Unity
            # açık değil" anlamına geliyor ve frontend onu ayrı ele alıyor.
            status_code=500,
            detail=unity_mcp_manager.last_error or "Unity MCP sunucusu başlatılamadı."
        )

    unity_mcp_manager.enabled = True
    unity_mcp_manager.generation += 1
    generation = unity_mcp_manager.generation
    unity_mcp_manager.active_workspace = workspace_path
    package_installed = False
    if workspace_path:
        # Copy the package now, but turn autoconnect on only once the server is ready.
        # Böylece Unity plugin bağlanmaya çalıştığında server zaten ayakta olur
        # ve Unity Console'da "Connection failed" hatası çıkmaz.
        # Off the event loop: the first copy took 2.8 s and an update 4.8 s (measured 2 Nov 2026).
        package_installed = await asyncio.to_thread(
            unity_mcp_manager.install_package, workspace_path, write_autoconnect=False)
        if not package_installed:
            # A refused install must be visible: stop the server again and say why.
            reason = unity_mcp_manager.last_install_error or "Gamachine could not install the Unity package."
            stop_mcp()
            raise HTTPException(status_code=409, detail=reason)
        background_tasks.add_task(unity_mcp_manager.write_autoconnect_when_ready,
                                  workspace_path, generation=generation)

    return {
        "status": "started",
        "port": unity_mcp_manager.mcp_port,
        "package_installed": package_installed,
    }


def stop_mcp(workspace_path=None):
    unity_mcp_manager.stop_server(reason="toggle_off")
    unity_mcp_manager.enabled = False
    unity_mcp_manager.generation += 1
    unity_mcp_manager.active_workspace = None
    from tools.unity_mcp_tools import unload_unity_tools
    unload_unity_tools()
    # An isolated backend (GAMACHINE_NO_UNITY_MCP) must leave the project's autoconnect file alone.
    if workspace_path and not unity_mcp_disabled():
        unity_mcp_manager._write_autoconnect_settings(workspace_path, auto_start=False)
    return {"status": "stopped"}


@router.get("/unity/status")
async def get_unity_mcp_status(workspace: Optional[str] = None, x_session_token: str = Header(alias="X-Session-Token", default="")):
    """Unity MCP sunucusunun ve Unity Editor bağlantısının durumunu döner."""
    _check_token(x_session_token)
    if workspace:
        require_saved_workspace_when_remote(workspace, x_session_token)
    from tools.unity_mcp_tools import get_unity_tool_definitions, load_unity_tools_async
    status = await unity_mcp_manager.get_status()

    # Unity bağlandıysa ve tool listesi boşsa yükle
    if status["status"] == "connected" and not get_unity_tool_definitions():
        asyncio.create_task(load_unity_tools_async())

    if workspace:
        status["editor_open"] = await asyncio.to_thread(unity_project_presence.editor_pid, workspace) is not None
        loading = None if status["editor_open"] else editor_loading(workspace)
        if loading is not None:
            status["editor_loading"] = True
            if loading["seconds"] is not None:
                status["editor_loading_s"] = loading["seconds"]
    return status


@router.get("/unity/console")
async def get_unity_console(x_session_token: str = Header(alias="X-Session-Token", default="")):
    """Unity Editor Console loglarını döner (read_console MCP tool)."""
    _check_token(x_session_token)
    from tools.unity_mcp_tools import is_unity_tool, call_unity_tool
    if not is_unity_tool("read_console"):
        return {"logs": [], "connected": False}
    try:
        # Off the event loop and short: a UI poll must not hold the backend for
        # the agent-call budget.
        import asyncio
        result = await asyncio.to_thread(call_unity_tool, "read_console", {"count": 50}, 15.0)
        logs = result.get("logs") or result.get("result") or []
        return {"logs": logs, "connected": True}
    except Exception as e:
        return {"logs": [], "connected": False, "error": str(e)}


def create_mcp_router():
    return router
