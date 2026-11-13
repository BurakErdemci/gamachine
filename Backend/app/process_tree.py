"""End a process's descendants after the process itself is gone.

The Claude CLI starts background Bash and codex children. Closing the SDK
client only stops claude.exe: measured 7 Nov 2026, the nested bash.exe chain
stayed alive 30 s+ after the close, orphaned, and kept running. Once the parent
is dead `taskkill /T` finds no tree, so the descendants are collected while the
parent still lives and killed afterwards.
"""
import asyncio
import collections
import logging
import os
import signal
import subprocess
import sys
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

KILL_WAIT_S = 5.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _parent_map_windows(names: Optional[dict] = None) -> dict:
    """pid -> parent pid for every process, from a Toolhelp32 snapshot (no
    PowerShell or wmic window). When `names` is given it is filled with pid -> image name."""
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
            if names is not None:
                names[entry.th32ProcessID] = entry.szExeFile
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return parents


def _parent_map_posix() -> dict:
    from spawn_env import build_spawn_env

    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True,
                         text=True, timeout=KILL_WAIT_S, env=build_spawn_env()).stdout
    parents = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parents[int(parts[0])] = int(parts[1])
    return parents


def process_table() -> dict:
    return _parent_map_windows() if sys.platform == "win32" else _parent_map_posix()


def process_table_with_names() -> tuple:
    """(pid -> ppid, pid -> lowercased image basename) of every process; ({}, {}) when unreadable."""
    try:
        if sys.platform == "win32":
            names: dict = {}
            parents = _parent_map_windows(names)
            return parents, {pid: os.path.basename(name).lower() for pid, name in names.items()}
        from spawn_env import build_spawn_env

        done = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True,
                              text=True, timeout=KILL_WAIT_S, env=build_spawn_env())
        if done.returncode != 0:
            return {}, {}  # a partial table could hide the busy shell
        out = done.stdout
        parents, images = {}, {}
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                parents[int(parts[0])] = int(parts[1])
                images[int(parts[0])] = os.path.basename(parts[2].strip()).lower().lstrip("-")  # login shell: -zsh
        return parents, images
    except Exception as exc:
        logger.debug("[process-tree] process table with names unreadable: %s", exc)
        return {}, {}


# Measured 13 Nov 2026: a background `ping` the agent started ran as PING.EXE <- cmd.exe <- backend.exe
# <- cmd.exe <- powershell.exe <- agy.EXE; agy's own helpers (conhost, backend.exe, cmd.exe -> node.exe)
# never sit under a shell. cmd.exe is deliberately not listed: agy's MCP wrappers are cmd.exe -> node.exe.
_SHELL_IMAGES = {"powershell.exe", "pwsh.exe", "bash.exe", "sh.exe", "zsh.exe", "fish.exe", "dash.exe",
                 "powershell", "pwsh", "bash", "sh", "zsh", "fish", "dash"}


def has_running_shell_work(root_pid: Optional[int]) -> Optional[bool]:
    """True when a direct shell child of `root_pid` has a live descendant other than conhost.exe
    (work the agent left running); False when there is none; None when unknown."""
    if not root_pid:
        return None
    parents, images = process_table_with_names()
    if not parents:
        return None
    for shell, ppid in parents.items():
        if ppid != root_pid or images.get(shell) not in _SHELL_IMAGES:
            continue
        if any(images.get(pid) != "conhost.exe" for pid in _closure(parents, [shell]) - {shell}):
            return True
    return False


# --- Kill accounting -------------------------------------------------------
# Measured 12 Nov 2026: the Unity MCP server died with exit code 1 (what `taskkill /F` gives) and
# nothing in the logs named a killer. Every kill site writes one line here BEFORE it kills, so a
# silent death can be matched to the site that ended that pid or one of its ancestors.

_EXE_MAX = 260
_NOTE_MAX_PIDS = 8
_RECENT_KILLS = collections.deque(maxlen=50)  # (time.monotonic(), record)


def _exe_path_windows(pid: int) -> Optional[str]:
    """Executable path of a process (no arguments: command lines carry secrets); None when unreadable."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(_EXE_MAX * 2)
        size = wintypes.DWORD(len(buffer))
        if not k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value[:_EXE_MAX]
    except Exception:
        return None
    finally:
        k32.CloseHandle(handle)


def _image_path_posix(pid: int) -> Optional[str]:
    try:
        from spawn_env import build_spawn_env

        out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=KILL_WAIT_S, env=build_spawn_env()).stdout.strip()
        return out[:_EXE_MAX] if out else None
    except Exception:
        return None


def _describe_pid(pid: int, names: dict, parents: dict) -> dict:
    image = names.get(pid)
    exe = None
    try:
        if sys.platform == "win32":
            exe = _exe_path_windows(pid)
        else:
            exe = _image_path_posix(pid)
            if exe:
                image = image or os.path.basename(exe)
    except Exception:
        pass
    return {"pid": pid, "image": image, "exe": exe, "ppid": parents.get(pid)}


def _closure(parents: dict, roots) -> set:
    """`roots` plus every descendant of them in the process table."""
    found = set(roots)
    frontier = list(found)
    while frontier:
        parent = frontier.pop()
        for child, ppid in parents.items():
            if ppid == parent and child not in found:
                found.add(child)
                frontier.append(child)
    return found


def note_kill(site: str, pids) -> None:
    """Writes one kill_accounting diag line (and keeps it in memory for the exit context) BEFORE the
    caller kills `pids`: site, each pid's image, executable path and parent pid (never arguments), and whether the Unity MCP server
    process (the uvx child we started, or anything under it) is among the targets or their
    descendants. Never raises."""
    try:
        pids = [p for p in pids if isinstance(p, int)]
        if not pids:
            return
        names: dict = {}
        try:
            parents = _parent_map_windows(names) if sys.platform == "win32" else _parent_map_posix()
        except Exception:
            parents = {}
        mcp_pid = None
        try:
            from unity_ai_mcp import unity_mcp_manager as manager_module

            mcp_pid = _pid_of(manager_module.unity_mcp_manager.process)
        except Exception:
            pass
        mcp_tree = _closure(parents, [mcp_pid]) if mcp_pid else set()
        hit = sorted(_closure(parents, pids) & mcp_tree) if mcp_tree else []
        record = {"site": site, "targets": [_describe_pid(p, names, parents) for p in pids[:_NOTE_MAX_PIDS]],
                  "target_count": len(pids), "mcp_pid": mcp_pid, "mcp_in_targets": bool(hit),
                  "mcp_hit_pids": hit[:_NOTE_MAX_PIDS]}
        _RECENT_KILLS.append((time.monotonic(), record))
        from unity_ai_mcp.unity_mcp_manager import unity_diag

        unity_diag("kill_accounting", **record)
    except Exception as exc:
        logger.debug("[process-tree] kill accounting for %s failed: %s", site, exc)


def recent_kills(within_s: float = 120.0, limit: int = 5) -> list:
    """The last `limit` kill_accounting records from the last `within_s` seconds, oldest first,
    each with `age_s`."""
    now = time.monotonic()
    fresh = [(now - at, record) for at, record in list(_RECENT_KILLS) if now - at <= within_s]
    return [{**record, "age_s": round(age, 1)} for age, record in fresh[-limit:]]


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
    note_kill("process_tree.kill_pids", pids)
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


def _descendants_of(proc) -> List[int]:
    try:
        return collect_descendants(_pid_of(proc))
    except Exception as exc:
        logger.debug("[process-tree] collect for pid=%s failed: %s", _pid_of(proc), exc)
        return []


def _pid_of(proc) -> Optional[int]:
    pid = getattr(proc, "pid", None)
    return pid if isinstance(pid, int) else None


def _kill_pids_sync(pids: List[int]) -> None:
    """Sync twin of kill_pids for the sync kill sites. Never raises.

    On Windows taskkill is started and not waited for: these sites run on the
    event loop (some under the agy gate lock), and a blocking wait of up to
    KILL_WAIT_S would freeze the whole backend while the OS ends the tree."""
    if not pids:
        return
    note_kill("process_tree._kill_pids_sync", pids)
    if sys.platform == "win32":
        from spawn_env import build_spawn_env

        args = ["taskkill", "/T", "/F"]
        for pid in pids:
            args += ["/PID", str(pid)]
        try:
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=build_spawn_env(),
                             creationflags=_CREATE_NO_WINDOW)
        except Exception as exc:
            logger.debug("[process-tree] taskkill pids=%s failed: %s", pids, exc)
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


async def end_tree(proc) -> bool:
    """Kill `proc` and everything it started. True when proc.kill() went
    through; never raises. The descendants are collected first because once the
    parent is dead nothing links them to it (a Popen or an asyncio Process)."""
    descendants = _descendants_of(proc)
    killed = True
    note_kill("process_tree.end_tree", [_pid_of(proc)])
    try:
        proc.kill()
    except Exception as exc:
        killed = False
        logger.debug("[process-tree] kill pid=%s failed: %s", _pid_of(proc), exc)
    await kill_pids(descendants)
    return killed


def end_tree_sync(proc, *, strict: bool = False) -> bool:
    """Blocking end_tree for sync kill sites. With strict=True a failing
    proc.kill() is re-raised after the descendants are killed, for callers that
    act on that error."""
    descendants = _descendants_of(proc)
    note_kill("process_tree.end_tree_sync", [_pid_of(proc)])
    try:
        proc.kill()
    except Exception as exc:
        _kill_pids_sync(descendants)
        if strict:
            raise
        logger.debug("[process-tree] kill pid=%s failed: %s", _pid_of(proc), exc)
        return False
    _kill_pids_sync(descendants)
    return True


def creation_time(pid: int):
    """An opaque start-time stamp of a live process, None when it is gone or
    unreadable. Two processes that had the same pid never share one."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            try:
                created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
                if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, exited, kernel, user))):
                    return None
                return (created.dwHighDateTime << 32) | created.dwLowDateTime
            finally:
                kernel32.CloseHandle(handle)
        from spawn_env import build_spawn_env

        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=KILL_WAIT_S, env=build_spawn_env()).stdout.strip()
        return out or None
    except Exception as exc:
        logger.debug("[process-tree] creation time of pid=%s unreadable: %s", pid, exc)
        return None


def record_descendants(root_pid: Optional[int], known: dict) -> None:
    """Adds the live descendants of `root_pid` to `known` (pid -> creation_time).
    A descendant whose start time cannot be read is not recorded: it could not
    be told apart from a later process with the same pid. Never raises."""
    for pid in collect_descendants(root_pid):
        if pid not in known:
            stamp = creation_time(pid)
            if stamp is not None:
                known[pid] = stamp


def end_recorded_sync(known: dict) -> None:
    """Ends every recorded process that is still the same process (same start
    time), plus anything it started. Never raises."""
    _kill_pids_sync([pid for pid, stamp in list(known.items()) if creation_time(pid) == stamp])


class JobObject:
    """Windows job object with KILL_ON_JOB_CLOSE holding a process and everything
    it starts, however deep and even after the process itself has exited: a
    grandchild whose parent is gone is still in the job, where the process
    table no longer links it to anything. `terminate()` ends all of them; closing
    the handle (or the backend dying) does the same. Windows only."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic),
                        ("IoInfo", ctypes.c_uint64 * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._ctypes = ctypes
        self._k32 = k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            self.close()
            raise OSError(err, "SetInformationJobObject failed")

    def assign(self, proc) -> None:
        if not self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise OSError(self._ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def terminate(self) -> None:
        if self.handle:
            self._k32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


def new_job_object_for(proc):
    """A JobObject holding `proc`, or None when this is not Windows or the OS refuses."""
    if sys.platform != "win32":
        return None
    try:
        job = JobObject()
    except Exception as exc:
        logger.debug("[process-tree] job object unavailable: %s", exc)
        return None
    try:
        job.assign(proc)
    except Exception as exc:
        logger.debug("[process-tree] pid=%s not assigned to a job object: %s", _pid_of(proc), exc)
        job.close()
        return None
    return job
