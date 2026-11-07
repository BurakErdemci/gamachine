"""End a process's descendants after the process itself is gone.

The Claude CLI starts background Bash and codex children. Closing the SDK
client only stops claude.exe: measured 7 Nov 2026, the nested bash.exe chain
stayed alive 30 s+ after the close, orphaned, and kept running. Once the parent
is dead `taskkill /T` finds no tree, so the descendants are collected while the
parent still lives and killed afterwards.
"""
import asyncio
import logging
import os
import signal
import subprocess
import sys
from typing import List, Optional

logger = logging.getLogger(__name__)

KILL_WAIT_S = 5.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _parent_map_windows() -> dict:
    """pid -> parent pid for every process, from a Toolhelp32 snapshot (no
    PowerShell or wmic window)."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        return {}
    parents = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return parents


def _parent_map_posix() -> dict:
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True,
                         text=True, timeout=KILL_WAIT_S).stdout
    parents = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parents[int(parts[0])] = int(parts[1])
    return parents


def process_table() -> dict:
    return _parent_map_windows() if sys.platform == "win32" else _parent_map_posix()


def collect_descendants(pid: Optional[int]) -> List[int]:
    """Every live descendant of `pid`, children before grandchildren. Empty
    when the pid is unknown or the process table cannot be read; never raises."""
    if not pid:
        return []
    try:
        parents = process_table()
    except Exception as exc:
        logger.debug("[process-tree] descendants of pid=%s unreadable: %s", pid, exc)
        return []
    found: List[int] = []
    frontier = [pid]
    while frontier:
        parent = frontier.pop(0)
        for child, ppid in parents.items():
            if ppid == parent and child != pid and child not in found:
                found.append(child)
                frontier.append(child)
    return found


async def kill_pids(pids: List[int]) -> None:
    """End these processes (and anything they started since). Never raises."""
    if not pids:
        return
    if sys.platform == "win32":
        from spawn_env import build_spawn_env

        args = ["taskkill", "/T", "/F"]
        for pid in pids:
            args += ["/PID", str(pid)]
        try:
            killer = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, env=build_spawn_env(),
                creationflags=_CREATE_NO_WINDOW)
            await asyncio.wait_for(killer.wait(), timeout=KILL_WAIT_S)
        except Exception as exc:
            logger.debug("[process-tree] taskkill pids=%s failed: %s", pids, exc)
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
