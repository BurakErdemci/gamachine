"""macOS application identity, window facts and computer-use policy."""
import asyncio
import ctypes
import math
import os
import re
import sys
import threading
from functools import cache

import httpx

from unity_ai_mcp import approval_bridge
from ..driver_client import DriverClient, DriverError, DriverUnavailable
from .base import WindowListUnavailable, driver_command


HARD_DENY_BUNDLE_IDS = frozenset({
    "com.gamachine.app", "com.github.Electron", "com.trycua.driver",
    "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable",
    "com.mitchellh.ghostty", "net.kovidgoyal.kitty", "org.alacritty",
    "com.github.wez.wezterm", "dev.commandline.waveterm", "com.raphaelamorim.rio",
    "com.apple.systempreferences", "com.apple.Settings",
    "com.1password.1password", "com.bitwarden.desktop", "com.dashlane.dashlanephonefinal",
    "com.lastpass.LastPass", "com.nordsec.nordpass", "com.apple.Passwords",
    "com.apple.keychainaccess", "com.apple.ScriptEditor2", "com.apple.Automator",
    "com.apple.shortcuts", "com.apple.ScreenContinuity", "com.apple.SecurityAgent",
    "com.apple.LocalAuthentication.UIAgent",
})
_DENIED = frozenset(b.casefold() for b in HARD_DENY_BUNDLE_IDS)


def valid_bundle_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,254}", value) is not None


def is_hard_denied(bundle_id) -> bool:
    return isinstance(bundle_id, str) and bundle_id.casefold() in _DENIED


async def _lsappinfo_text(pid) -> str | None:
    if type(pid) is not int or pid <= 0:
        return None
    return await _run_lsappinfo("info", "-only", "bundleid", "-only", "name",
                                "-only", "applicationtype", f"pid:{pid}")


def _parse_lsappinfo(text) -> tuple[str, str | None] | None:
    bundles = re.findall(r'bundleID="([^"\n]+)"', text)
    if (len(bundles) != 1 or not valid_bundle_id(bundles[0])
            or re.search(r'type="Foreground"', text) is None
            or len(re.findall(r'\bASN:', text)) > 1):
        return None
    name = re.search(r'^"([^"\n]*)" ASN', text, re.MULTILINE)
    return bundles[0], (name.group(1) or None) if name else None


async def _lsappinfo_list_text() -> str | None:
    return await _run_lsappinfo("list")


async def _run_lsappinfo(*args) -> str | None:
    from . import current
    if current().name != "darwin":
        return None
    try:
        executable = os.path.join(os.sep, "usr", "bin", "lsappinfo")
        if not os.path.isfile(executable):
            return None
        # At most one child per call; reap it on timeout and cancellation.
        process = await asyncio.create_subprocess_exec(
            executable, *args, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return None
        return stdout.decode("utf-8", errors="replace") if process.returncode == 0 else None
    except Exception:
        return None


def _parse_lsappinfo_front(front_text, info_text) -> tuple[bool, int | None]:
    if not isinstance(front_text, str) or not isinstance(info_text, str):
        return False, None
    pids = re.findall(r'\bpid\s+=\s+(-?[0-9]+)\b', info_text)
    types = re.findall(r'\btype="([^"\n]*)"', info_text)
    if len(pids) != 1 or types != ["Foreground"]:
        return True, None
    pid = int(pids[0])
    return True, pid if pid > 0 else None


async def _lsappinfo_front() -> tuple[bool, int | None]:
    try:
        front_text = await _run_lsappinfo("front")
        if front_text is None:
            return False, None
        asns = re.findall(r'\bASN:0x[0-9a-fA-F]+-0x[0-9a-fA-F]+:', front_text)
        if len(asns) != 1:
            return False, None
        info_text = await _run_lsappinfo("info", "-only", "pid", "-only", "applicationtype", asns[0])
        if info_text is None:
            return False, None
        return _parse_lsappinfo_front(front_text, info_text)
    except Exception:
        return False, None


def _parse_lsappinfo_list(text) -> list[dict]:
    if not isinstance(text, str):
        return []
    headers = list(re.finditer(r'^\s*\d+\) "([^\n]*)" ASN:[^\n]*(?:\n|$)', text, re.MULTILINE))
    rows = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        # Fields in a quoted app name are not part of the record body.
        body = text[header.end():end]
        bundles = re.findall(r'\bbundleID="([^"\n]*)"', body)
        # The token also contains pid=N; only the separate pid = N field counts.
        pids = re.findall(r'\bpid\s+=\s+(-?[0-9]+)\b', body)
        types = re.findall(r'\btype="([^"\n]*)"', body)
        if (len(bundles) != 1 or not valid_bundle_id(bundles[0])
                or len(pids) != 1 or types != ["Foreground"]):
            continue
        pid = int(pids[0])
        if pid <= 0:
            continue
        row = {"bundle_id": bundles[0], "kind": "desktop", "name": header.group(1) or None,
               "pid": pid, "running": True}
        path = re.search(r'\bbundle path="([^"\n]*)"', body)
        if path and os.path.isabs(path.group(1)):
            row["launch_path"] = path.group(1)
        rows.append(row)
        if len(rows) == 256:
            break
    return rows


async def running_apps_from_os() -> list[dict]:
    try:
        return _parse_lsappinfo_list(await _lsappinfo_list_text())
    except Exception:
        return []


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


_CG_LOAD_LOCK = threading.Lock()


@cache
def _cg_libraries():
    try:
        frameworks = os.path.join(os.sep, "System", "Library", "Frameworks")
        cg = ctypes.CDLL(os.path.join(frameworks, "CoreGraphics.framework", "CoreGraphics"))
        cf = ctypes.CDLL(os.path.join(frameworks, "CoreFoundation.framework", "CoreFoundation"))
        pointer = ctypes.c_void_p
        signatures = [
            (cg.CGWindowListCopyWindowInfo, [ctypes.c_uint32, ctypes.c_uint32], pointer),
            (cg.CGRectMakeWithDictionaryRepresentation, [pointer, ctypes.POINTER(_CGRect)], ctypes.c_bool),
            (cf.CFArrayGetCount, [pointer], ctypes.c_long),
            (cf.CFArrayGetValueAtIndex, [pointer, ctypes.c_long], pointer),
            (cf.CFDictionaryGetValue, [pointer, pointer], pointer),
            (cf.CFGetTypeID, [pointer], ctypes.c_ulong),
            (cf.CFNumberGetTypeID, [], ctypes.c_ulong),
            (cf.CFBooleanGetTypeID, [], ctypes.c_ulong),
            (cf.CFStringGetTypeID, [], ctypes.c_ulong),
            (cf.CFDictionaryGetTypeID, [], ctypes.c_ulong),
            (cf.CFNumberGetValue, [pointer, ctypes.c_int, pointer], ctypes.c_bool),
            (cf.CFBooleanGetValue, [pointer], ctypes.c_bool),
            (cf.CFStringGetLength, [pointer], ctypes.c_long),
            (cf.CFStringGetMaximumSizeForEncoding, [ctypes.c_long, ctypes.c_uint32], ctypes.c_long),
            (cf.CFStringGetCString, [pointer, pointer, ctypes.c_long, ctypes.c_uint32], ctypes.c_bool),
            (cf.CFRelease, [pointer], None),
        ]
        for function, args, result in signatures:
            function.argtypes = args
            function.restype = result
        keys = {name: pointer.in_dll(cg, name).value for name in (
            "kCGWindowNumber", "kCGWindowOwnerPID", "kCGWindowLayer", "kCGWindowName",
            "kCGWindowOwnerName", "kCGWindowIsOnscreen", "kCGWindowAlpha", "kCGWindowBounds",
        )}
        return cg, cf, keys
    except Exception:
        return None


def _cg_window_list() -> list[dict] | None:
    from . import current
    if current().name != "darwin":
        return None
    try:
        # Serialize the first load, including concurrent worker-thread calls.
        with _CG_LOAD_LOCK:
            libraries = _cg_libraries()
        if libraries is None:
            return None
        cg, cf, keys = libraries
        array = cg.CGWindowListCopyWindowInfo(1 | 16, 0)
        if not array:
            return None
        try:
            rows = []
            number_type = cf.CFNumberGetTypeID()
            string_type = cf.CFStringGetTypeID()
            boolean_type = cf.CFBooleanGetTypeID()
            dictionary_type = cf.CFDictionaryGetTypeID()
            utf8 = 0x08000100
            for index in range(cf.CFArrayGetCount(array)):
                dictionary = cf.CFArrayGetValueAtIndex(array, index)
                if not dictionary or cf.CFGetTypeID(dictionary) != dictionary_type:
                    continue
                row = {}
                for name, key in keys.items():
                    value = cf.CFDictionaryGetValue(dictionary, key)
                    if not value:
                        continue
                    kind = cf.CFGetTypeID(value)
                    if name in ("kCGWindowName", "kCGWindowOwnerName") and kind == string_type:
                        size = cf.CFStringGetMaximumSizeForEncoding(cf.CFStringGetLength(value), utf8) + 1
                        buffer = ctypes.create_string_buffer(size)
                        if cf.CFStringGetCString(value, buffer, size, utf8):
                            row[name] = buffer.value.decode("utf-8")
                    elif name == "kCGWindowIsOnscreen" and kind == boolean_type:
                        row[name] = bool(cf.CFBooleanGetValue(value))
                    elif name == "kCGWindowBounds" and kind == dictionary_type:
                        rect = _CGRect()
                        if cg.CGRectMakeWithDictionaryRepresentation(value, ctypes.byref(rect)):
                            row[name] = {"X": rect.origin.x, "Y": rect.origin.y,
                                         "Width": rect.size.width, "Height": rect.size.height}
                    elif name in ("kCGWindowNumber", "kCGWindowOwnerPID", "kCGWindowLayer",
                                  "kCGWindowAlpha") and kind == number_type:
                        # CFNumber types: kCFNumberFloat64Type=6, kCFNumberSInt64Type=4.
                        numeric = ctypes.c_double() if name == "kCGWindowAlpha" else ctypes.c_int64()
                        if cf.CFNumberGetValue(value, 6 if name == "kCGWindowAlpha" else 4,
                                               ctypes.byref(numeric)):
                            row[name] = numeric.value
                rows.append(row)
            return rows
        finally:
            cf.CFRelease(array)
    except Exception:
        return None


def _panel_rows(raw, pid, base_z) -> list[dict]:
    if not isinstance(raw, list) or type(pid) is not int:
        return []
    rows = []
    for item in raw:
        try:
            if not isinstance(item, dict):
                continue
            owner, number, layer = (item.get(key) for key in (
                "kCGWindowOwnerPID", "kCGWindowNumber", "kCGWindowLayer"))
            if (type(owner) is not int or owner != pid or type(number) is not int or number <= 0
                    or type(layer) is not int or not 1 <= layer <= 23
                    or item.get("kCGWindowIsOnscreen") is not True):
                continue
            alpha = item.get("kCGWindowAlpha", 1.0)
            if type(alpha) not in (int, float) or not alpha > 0:
                continue
            bounds = item["kCGWindowBounds"]
            values = [bounds[key] for key in ("X", "Y", "Width", "Height")]
            if (any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
                    or values[2] < 40 or values[3] < 40):
                continue
            name, title = item.get("kCGWindowOwnerName"), item.get("kCGWindowName")
            rows.append({
                "window_id": number, "pid": owner,
                "app_name": name if isinstance(name, str) else None,
                "title": title if isinstance(title, str) else "", "layer": layer,
                "bounds": dict(zip(("x", "y", "width", "height"), map(int, values))),
                "is_on_screen": True, "on_current_space": True,
            })
            if len(rows) == 64:
                break
        except Exception:
            continue
    for position, row in enumerate(rows):
        row["z_index"] = base_z + len(rows) - position
    return rows


async def panel_windows_from_os(pid, base_z) -> list[dict]:
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_cg_window_list), 2.0)
        return _panel_rows(raw, pid, base_z)
    except Exception:
        return []


async def identify_running_app(driver, pid) -> tuple[str | None, str | None]:
    bundle_id, name = (await driver.list_running_apps()).get(pid, (None, None))
    if valid_bundle_id(bundle_id):
        return bundle_id, name
    if type(pid) is not int or pid <= 0:
        return None, None
    try:
        text = await _lsappinfo_text(pid)
        identity = _parse_lsappinfo(text) if text is not None else None
        if identity is not None:
            return identity[0], name or identity[1]
    except Exception:
        pass
    return None, None


async def fetch_runtime() -> tuple[str, str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{approval_bridge.BACKEND_URL}/computer-use/settings",
                headers=approval_bridge._get_headers(),
            )
            response.raise_for_status()
            data = response.json()
        runtime = data.get("runtime") if isinstance(data, dict) else None
        if not isinstance(runtime, dict) or runtime.get("running") is not True:
            raise DriverUnavailable("Embedded computer-use driver is not running")
        binary, socket = runtime.get("driver_path"), runtime.get("socket_path")
        if (not isinstance(binary, str) or not os.path.isabs(binary)
                or os.path.basename(binary) != "cua-driver" or not os.path.isfile(binary)
                or not isinstance(socket, str) or not os.path.isabs(socket)):
            raise DriverUnavailable("Embedded computer-use driver runtime is invalid")
        return binary, socket
    except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
        raise DriverUnavailable("Embedded computer-use driver runtime is unavailable") from exc


_BUNDLE_SUFFIXES = (".app", ".bundle", ".framework", ".plugin", ".appex", ".prefpane",
                    ".workflow", ".action", ".shortcut", ".scptd", ".pkg", ".mpkg",
                    ".kext", ".xpc", ".terminal", ".command", ".tool",
                    ".saver", ".service", ".systemextension", ".wdgt", ".dext",
                    ".qlgenerator", ".mdimporter", ".component", ".colorpicker",
                    ".osax", ".menu", ".vst", ".vst3", ".au", ".clap")

_DOCUMENT_HANDLER_DENY = frozenset({
    "com.apple.finder", "com.apple.terminal", "com.googlecode.iterm2", "org.python.pythonlauncher",
    "dev.warp.warp-stable", "com.mitchellh.ghostty", "net.kovidgoyal.kitty", "org.alacritty",
    "co.zeit.hyper", "com.github.wez.wezterm", "com.apple.installer", "com.apple.archiveutility",
    "com.apple.scripteditor2", "com.apple.automator", "com.apple.shortcuts",
})

_RUNNABLE_FILE_SUFFIXES = (
    ".sh", ".bash", ".zsh", ".csh", ".ksh", ".fish", ".command", ".tool", ".py", ".pyw", ".pyc",
    ".rb", ".pl", ".php", ".scpt", ".applescript", ".jxa", ".workflow", ".shortcut", ".action",
    ".terminal", ".webloc", ".inetloc", ".fileloc", ".url", ".desktop", ".jar", ".pkg", ".mpkg",
    ".dmg", ".iso", ".img", ".sparseimage", ".sparsebundle", ".mobileconfig", ".provisionprofile",
    ".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm", ".lnk", ".exe", ".msi", ".bat", ".cmd",
    ".ps1", ".vbs", ".wsf", ".reg", ".so", ".dylib", ".bundle", ".kext", ".plugin", ".prefpane",
    ".saver", ".qlgenerator", ".app",
)


def _is_bundle_dir(path):
    return (os.path.basename(path).casefold().endswith(_BUNDLE_SUFFIXES)
            or os.path.exists(os.path.join(path, "Contents", "Info.plist"))
            or os.path.exists(os.path.join(path, "Contents", "MacOS")))


def _resolve_launch_targets(urls, bundle_id):
    """Return the urls with every path replaced by its checked real path, or None."""
    resolved = []
    for url in urls:
        if not url.startswith("/"):
            resolved.append(url)
            continue
        try:
            real_path = os.path.realpath(url)
            if not (os.path.isdir(real_path) or (
                    os.path.isfile(real_path)
                    and bundle_id.casefold() not in _DOCUMENT_HANDLER_DENY
                    and not os.path.basename(real_path).casefold().endswith(_RUNNABLE_FILE_SUFFIXES)
                    and not os.path.basename(real_path).casefold().endswith(_BUNDLE_SUFFIXES)
                    and os.stat(real_path).st_mode & 0o111 == 0)):
                return None
            current = real_path
            while True:
                if _is_bundle_dir(current):
                    return None
                parent = os.path.dirname(current)
                if parent == current:
                    break
                current = parent
        except (OSError, ValueError):
            return None
        resolved.append(real_path)
    return resolved


def _safe_launch_targets(urls, bundle_id):
    return _resolve_launch_targets(urls, bundle_id) is not None


_ALIASES = {"command": "cmd", "option": "opt", "alt": "opt", "control": "ctrl", "backspace": "delete", "escape": "esc"}
_RISKY = frozenset(frozenset(s.split("+")) for s in (
    "cmd+delete", "cmd+shift+delete", "cmd+opt+shift+delete", "cmd+opt+esc",
    "cmd+opt+shift+esc", "ctrl+cmd+q", "cmd+shift+q", "cmd+opt+shift+q"))


INSTRUCTIONS = (
        "Read a window with computer_window_state before acting. Prefer element_token over x,y. "
        "Batch known steps with computer_run_steps. Call computer_confirm_risky_step before "
        "deleting, purchasing, sending/publishing, overwriting or downloading with the user's account. "
        "Terminals, System Settings and Gamachine are off limits. "
        "Every tool takes exactly one parameter named args; put all arguments inside it, "
        'e.g. {"args": {"pid": 123, "window_id": 45}}. '
        "To check a file the task created, look only in the folder the task names; "
        "never search the whole home folder, macOS blocks other apps' data and warns the user. "
        "Work in the background: hotkeys, typing and element_token clicks reach background apps, "
        "including their Open/Save panels. To open a file: computer_launch_app with the app's bundle_id "
        "and urls set to the file's absolute path; only if that is refused, cmd+o, then type the full path starting with / "
        "(the panel opens its own go-to field; do not press cmd+shift+g), return, return. To save: cmd+s. "
        "In an Open or Save panel, set the file name with computer_set_value on the name field's element_token; "
        "typed text often does not reach these panels. In Unity, open a scene by double-clicking it in the "
        "Project window or with File > Open Recent Scene rather than the Open panel. "
        "If a hotkey or click is refused in the background (e.g. Find/Replace cmd+alt+f, background_unavailable), "
        "send the same call again with delivery_mode foreground and window_id: it brings the window to the front "
        "and leaves it there. Use computer_bring_to_front only if that is refused too."
        " Send multi-step foreground work (for example renaming: return, type the name, return) as ONE "
        "computer_run_steps with delivery_mode foreground."
)

class MacPlatform:
    name = "darwin"
    instructions = INSTRUCTIONS

    def valid_app_id(self, value) -> bool:
        return valid_bundle_id(value)

    def is_hard_denied(self, app_id: str, pid: int | None) -> bool:
        return is_hard_denied(app_id)

    def is_unity_editor(self, app_id: str) -> bool:
        return isinstance(app_id, str) and app_id.casefold().startswith("com.unity3d.unityeditor")

    def normalize_keys(self, tool: str, args: dict) -> dict:
        return args

    def refused_chord(self, keys: frozenset[str]) -> str | None:
        return None

    def risky_chord(self, keys: frozenset[str]) -> str:
        normalized = frozenset(_ALIASES.get(k.casefold(), k.casefold()) for k in keys)
        if normalized == {"cmd", "q"}:
            return "Uygulamadan çıkılsın mı? Kaydedilmemiş iş kaybolabilir."
        return "Riskli klavye kısayolu: " + "+".join(sorted(normalized)) if normalized in _RISKY else ""

    def resolve_launch_targets(self, urls, app_id) -> list | None:
        return _resolve_launch_targets(urls, app_id)

    def driver_command(self, binary: str, socket: str) -> tuple[list[str], dict]:
        return driver_command(binary, socket)

    async def identify(self, driver, pid) -> tuple[str | None, str | None, str | None]:
        app_id, name = await identify_running_app(driver, pid)
        return app_id, name, None

    async def running_apps(self) -> list[dict]:
        return await running_apps_from_os()

    async def panel_windows(self, pid, base_z) -> list[dict]:
        return await panel_windows_from_os(pid, base_z)

    async def top_window_is(self, pid, window_id) -> bool:
        raw = await asyncio.wait_for(asyncio.to_thread(_cg_window_list), 2.0)
        if not isinstance(raw, list):
            raise WindowListUnavailable
        top = next((row for row in raw if isinstance(row, dict)
                    and row.get("kCGWindowOwnerPID") == pid
                    and row.get("kCGWindowIsOnscreen") is True
                    and type(row.get("kCGWindowLayer")) is int
                    and 0 <= row["kCGWindowLayer"] <= 23
                    and type(row.get("kCGWindowAlpha", 1.0)) in (int, float)
                    and row.get("kCGWindowAlpha", 1.0) > 0), None)
        return top is not None and top.get("kCGWindowNumber") == window_id

    async def frontmost_pid(self, driver) -> int | None:
        try:
            answered, pid = await _lsappinfo_front()
            if answered:
                return pid
            result = await driver.call("list_apps", {})
            if result.is_error:
                return None
            # A reply may carry the same rows as structured content and as text.
            active = []
            for row in driver._app_rows(result):
                if row.get("active") is True and row.get("running") is not False and row.get("pid") not in active:
                    active.append(row.get("pid"))
            if len(active) != 1:
                return None
            pid = active[0]
            return pid if type(pid) is int and pid > 0 else None
        except Exception:
            return None


class UnsupportedPlatform(MacPlatform):
    name = "unsupported"
