"""Windows executable identity, window facts and computer-use policy."""
import asyncio
import ntpath
import os
import re

from .base import WindowListUnavailable


CLIPBOARD_APP_ID = "com.gamachine.clipboard"
DENY = frozenset({
    "gamachine.exe", "cua-driver.exe", "cua-driver-uia.exe", "cmd.exe",
    "powershell.exe", "pwsh.exe", "windowsterminal.exe", "openconsole.exe",
    "conhost.exe", "wsl.exe", "wslhost.exe", "bash.exe", "mintty.exe",
    "wezterm-gui.exe", "alacritty.exe", "explorer.exe", "taskmgr.exe",
    "regedit.exe", "mmc.exe", "control.exe", "systemsettings.exe", "msconfig.exe",
    "consent.exe", "logonui.exe", "credentialuibroker.exe", "1password.exe",
    "bitwarden.exe", "keepass.exe", "keepassxc.exe", "nordpass.exe",
    "wscript.exe", "cscript.exe", "mshta.exe", "python.exe", "pythonw.exe", "node.exe",
    "powershell_ise.exe", "wt.exe", "sh.exe", "git-bash.exe", "rundll32.exe", "msiexec.exe",
    "regedt32.exe", "sechealthui.exe", "useraccountcontrolsettings.exe", "resmon.exe", "perfmon.exe",
    "py.exe", "pyw.exe", "electron.exe",
    # Agent CLIs ship as native executables now, so denying node.exe no longer covers them.
    "claude.exe", "codex.exe",
    "exodus.exe", "ledger live.exe", "trezor suite.exe", "electrum.exe",
    "atomic wallet.exe", "binance.exe", "tws.exe", "terminal64.exe",
})
READ_ONLY = frozenset({
    "msedge.exe", "chrome.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe",
    "outlook.exe", "olk.exe", "hxoutlook.exe", "thunderbird.exe", "whatsapp.exe",
    "whatsapp.root.exe", "telegram.exe", "discord.exe", "slack.exe", "ms-teams.exe",
    "teams.exe", "signal.exe", "messenger.exe",
})
ALIASES = {
    "cmd": "ctrl", "command": "ctrl", "meta": "ctrl", "super": "ctrl",
    "opt": "alt", "option": "alt", "return": "enter",
}
_CHORD_ALIASES = {**ALIASES, "windows": "win", "lwin": "win", "rwin": "win",
                  "escape": "esc", "control": "ctrl", "del": "delete"}
_REFUSED = tuple(map(frozenset, (
    {"ctrl", "alt", "delete"}, {"ctrl", "alt", "end"}, {"ctrl", "shift", "esc"},
    {"alt", "tab"}, {"alt", "esc"}, {"ctrl", "esc"},
)))
_APP_ID = re.compile(r'win:[a-z]:\\[^<>"|?*:\x00-\x1f]{1,255}\.exe')
_ENV_KEYS = ("PATH", "SYSTEMROOT", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
             "ProgramData", "ALLUSERSPROFILE", "TEMP", "TMP", "USERNAME",
             "HOMEDRIVE", "HOMEPATH")
_INTEGRITY_REFUSAL = (
    "Bu uygulama yönetici olarak çalışıyor; Windows yönetici pencerelerine dışarıdan "
    "girdi gönderilmesine izin vermez. Uygulamayı normal başlatıp yeniden dene."
)

WINDOWS_INSTRUCTIONS = (
    "Read a window with computer_window_state before acting. Prefer element_token over x,y. "
    "Batch known steps with computer_run_steps. Call computer_confirm_risky_step before "
    "deleting, purchasing, sending/publishing, overwriting or downloading with the user's account. "
    "Terminals, Settings, File Explorer, Task Manager and Gamachine are off limits. "
    "Every tool takes exactly one parameter named args; put all arguments inside it, "
    'e.g. {"args": {"pid": 123, "window_id": 45}}. '
    "To check a file the task created, look only in the folder the task names; "
    "never search the whole user folder. "
    "Work in the background: hotkeys, typing and element_token clicks reach background apps, "
    "including their Open/Save panels. To open a file: ctrl+o in the app, type the full path "
    "starting with the drive letter (for example C:\\Users\\...), enter. To save: ctrl+s. "
    "computer_launch_app is not available on Windows. "
    "In an Open or Save panel, set the file name with computer_set_value on the name field's element_token; "
    "typed text often does not reach these panels. In Unity, open a scene by double-clicking it in the "
    "Project window or with File > Open Recent Scene rather than the Open panel. "
    "If a hotkey or click is refused in the background (e.g. Find/Replace ctrl+h, background_unavailable), "
    "send the same call again with delivery_mode foreground and window_id: it brings the window to the front "
    "and leaves it there. Use computer_bring_to_front only if that is refused too."
    " Send multi-step foreground work (for example renaming: return, type the name, return) as ONE "
    "computer_run_steps with delivery_mode foreground."
)


def _basename(value):
    return value.rsplit("\\", 1)[-1].casefold() if isinstance(value, str) else ""


def _chord(keys):
    parts = (part for key in keys for part in
             ([part.strip() for part in key.split("+") if part.strip()]
              if len(key) > 1 and "+" in key else [key]))
    return frozenset(_CHORD_ALIASES.get(key.casefold(), key.casefold()) for key in parts if key)


class WindowsPlatform:
    name = "win32"
    driver_basename = "cua-driver.exe"
    unsupported_tools = frozenset({"computer_launch_app", "computer_invoke_menu"})
    instructions = WINDOWS_INSTRUCTIONS

    def __init__(self, win32=None):
        if win32 is None:
            from . import _win32
            win32 = _win32
        self.win32 = win32
        self.win32.set_dpi_awareness()

    def valid_app_id(self, value) -> bool:
        if not isinstance(value, str):
            return False
        if value.casefold() == CLIPBOARD_APP_ID:
            return True
        return (value == value.casefold() and _APP_ID.fullmatch(value) is not None
                and all(segment not in {"", ".", ".."} for segment in value[7:].split("\\")))

    def app_id_for(self, pid) -> str | None:
        path = self.win32.image_path(pid)
        app_id = "win:" + path.casefold() if isinstance(path, str) else None
        return app_id if self.valid_app_id(app_id) else None

    def is_hard_denied(self, app_id: str, pid: int | None) -> bool:
        if isinstance(app_id, str) and app_id.casefold() == CLIPBOARD_APP_ID:
            return False
        name = _basename(app_id)
        if name in DENY or (name.startswith(("dashlane", "lastpass")) and name.endswith(".exe")):
            return True
        if type(pid) is int:
            current = os.getpid()
            parents = self.win32.parent_pids()
            seen = set()
            for _ in range(64):
                if current == pid:
                    return True
                if current in seen or not current:
                    break
                seen.add(current)
                current = parents.get(current)
        return False

    def is_unity_editor(self, app_id: str) -> bool:
        return _basename(app_id) == "unity.exe"

    def read_only_app(self, app_id: str) -> bool:
        return self.valid_app_id(app_id) and _basename(app_id) in READ_ONLY

    def front_verified(self, report, pid, window_id) -> bool:
        try:
            return (isinstance(report, dict) and report.get("landed_on_target") is True
                    and int(report["now_fg_hwnd"], 16) == int(report["target_hwnd"], 16) == window_id
                    and self.win32.foreground_window() == window_id
                    and self.real_pid(window_id, self.win32.window_pid(window_id)) == pid)
        except Exception:
            return False

    def normalize_keys(self, tool: str, args: dict) -> dict:
        updates = {}
        fields = ("keys",) if tool == "hotkey" else ("key", "modifiers") if tool == "press_key" else ()
        for field in fields:
            value = args.get(field)
            if field == "key" and isinstance(value, str):
                mapped = ALIASES.get(value.casefold(), value)
            elif field != "key" and isinstance(value, list):
                mapped = [ALIASES.get(key.casefold(), key) if isinstance(key, str) else key for key in value]
            else:
                continue
            updates[field] = mapped
        if all(value == args[field] for field, value in updates.items()):
            return args
        return {**args, **updates}

    def refused_chord(self, keys: frozenset[str]) -> str | None:
        normalized = _chord(keys)
        if "win" in normalized or any(chord <= normalized for chord in _REFUSED):
            return "Bu kısayol Windows'ta gönderilmez: " + "+".join(sorted(normalized))
        return None

    def risky_chord(self, keys: frozenset[str]) -> str:
        normalized = _chord(keys)
        if normalized == {"alt", "f4"}:
            return "Uygulamadan çıkılsın mı? Kaydedilmemiş iş kaybolabilir."
        if normalized in ({"shift", "delete"}, {"ctrl", "shift", "delete"}):
            return "Riskli klavye kısayolu: " + "+".join(sorted(normalized))
        return ""

    def resolve_launch_targets(self, urls, app_id) -> None:
        return None

    def driver_command(self, binary: str, socket: str) -> tuple[list[str], dict]:
        if self.win32.self_elevated():
            from ..driver_client import DriverUnavailable
            raise DriverUnavailable(
                "Gamachine yönetici olarak çalışıyor; bilgisayar kullanımı yönetici yetkisiyle "
                "başlatılmaz. Normal başlatın.")
        env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
        env.update(CUA_DRIVER_EMBEDDED="1", DO_NOT_TRACK="1",
                   CUA_DRIVER_RS_TELEMETRY_ENABLED="0", CUA_DRIVER_RS_UPDATE_CHECK="0")
        return [binary, "mcp", "--embedded", "--socket", socket], env

    def real_pid(self, hwnd, pid):
        if _basename(self.win32.image_path(pid)) == "applicationframehost.exe":
            for child in self.win32.child_windows(hwnd):
                info = self.win32.window_info(child)
                if info and info.get("class_name") == "Windows.UI.Core.CoreWindow":
                    child_pid = self.win32.window_pid(child)
                    if type(child_pid) is int and child_pid > 0 and child_pid != pid:
                        return child_pid
        return pid

    async def identify(self, driver, pid) -> tuple[str | None, str | None, str | None]:
        if type(pid) is not int or pid <= 0:
            return None, None, None

        def read():
            app_id = self.app_id_for(pid)
            if app_id is None or _basename(app_id) == "applicationframehost.exe":
                return None, None, None
            path = self.win32.image_path(pid)
            name = ntpath.splitext(ntpath.basename(path))[0] if path else None
            level = self.win32.integrity(pid)
            own_level = self.win32.integrity(os.getpid())
            if level is None or own_level is None or level > own_level:
                return app_id, name, _INTEGRITY_REFUSAL
            return app_id, name, None

        return await asyncio.to_thread(read)

    async def running_apps(self) -> list[dict]:
        return []

    async def frontmost_pid(self, driver) -> int | None:
        def read():
            hwnd = self.win32.foreground_window()
            if not hwnd:
                return None
            pid = self.real_pid(hwnd, self.win32.window_pid(hwnd))
            return pid if type(pid) is int and pid > 0 else None

        try:
            return await asyncio.to_thread(read)
        except Exception:
            return None

    async def top_window_is(self, pid, window_id) -> bool:
        def read():
            windows = self.win32.top_level_windows()
            if windows is None:
                raise WindowListUnavailable
            for hwnd in windows:
                info = self.win32.window_info(hwnd)
                if (info and info["visible"] and not info["cloaked"] and not info["minimized"]
                        and (info["owner"] == 0 or hwnd == window_id)
                        and self.real_pid(hwnd, self.win32.window_pid(hwnd)) == pid):
                    return hwnd == window_id
            return False

        return await asyncio.to_thread(read)

    async def panel_windows(self, pid, base_z) -> list[dict]:
        def read():
            rows = []
            path = self.win32.image_path(pid)
            name = ntpath.splitext(ntpath.basename(path))[0] if path else None
            for hwnd in self.win32.top_level_windows() or []:
                info = self.win32.window_info(hwnd)
                if (not info or not info["owner"] or not info["visible"] or info["cloaked"]
                        or self.real_pid(hwnd, self.win32.window_pid(hwnd)) != pid):
                    continue
                x, y, width, height = info["bounds"]
                if width < 40 or height < 40:
                    continue
                rows.append({
                    "window_id": hwnd, "pid": pid, "app_name": name,
                    "title": info["title"] if isinstance(info.get("title"), str) else "",
                    "layer": 1, "bounds": dict(zip(("x", "y", "width", "height"),
                                                   map(int, (x, y, width, height)))),
                    "is_on_screen": True, "on_current_space": True,
                })
                if len(rows) == 64:
                    break
            for position, row in enumerate(rows):
                row["z_index"] = base_z + len(rows) - position
            return rows

        try:
            return await asyncio.to_thread(read)
        except Exception:
            return []
