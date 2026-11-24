import os
import re
import sys
import json
import shlex
import shutil
import logging
import tempfile
import threading
from .cli_base import BaseCLIProvider
from .families import kimi_cli_model
from .unityai_tool_text import mail_and_fallback, mcp_double_underscore

logger = logging.getLogger(__name__)

# One read-merge-write of the user's mcp.json at a time in this process.
_USER_MCP_LOCK = threading.Lock()


def _say(language: str, tr: str, en: str) -> str:
    return tr if language == "tr" else en


def _cmd_quote(arg: str) -> str:
    """Always double-quoted for a cmd.exe batch line: unlike list2cmdline, which
    leaves `&`, `^`, `|`, `<`, `>` bare when there is no space, so cmd would read
    them as syntax. Quotes and trailing backslashes follow the MS C runtime rules."""
    arg = re.sub(r'(\\*)"', r'\1\1\\"', arg)
    arg = re.sub(r'(\\+)$', r'\1\1', arg)
    return '"' + arg + '"'


class KimiProvider(BaseCLIProvider):
    """Moonshot Kimi Code CLI (subscription), one prompt-mode run per turn:
    `kimi --output-format stream-json -m <alias> -p <prompt>`, cwd = workspace.

    Approval: prompt mode always runs in the CLI's "auto" (Never Ask) permission
    mode and rejects --yolo/--auto/--plan next to -p (kimi.exe 2.5.3 source,
    `validateOptions`; doc reference/kimi-command). That is the one-shot
    pattern cursor (`--force`) and copilot (`--allow-tool`) use: the CLI never
    stops for a prompt, its built-in Write/Edit/Bash are DENIED by
    `[[permission.rules]]`, and every file/shell action goes through
    `mcp__unityai__*`, whose approval card follows Gamachine's own approval
    mode. The deny rules still bite in auto mode: `UserConfiguredDeny` runs
    before `AutoModeApprove` in the CLI's policy chain (kimi.exe 2.5.3,
    AgentPermissionPolicyService), and `kimi doctor config` accepts the block.

    NOT measured live (no Kimi subscription on the dev machine): a logged-in
    turn's stream. The parser follows the CLI's PromptJsonWriter source.
    """

    _KIMI_MANAGED_MARKER = "# >>> gamachine managed (permission rules) >>>"
    _KIMI_MANAGED_END = "# <<< gamachine managed <<<"
    # Marka değişiminden (8 Ağu 2026) önceki işaretçi. Bu blok KULLANICININ
    # `~/.kimi-code/config.toml`'una yazılıyor, dolayısıyla yalnız yeni işaretçiye
    # bakmak eski bloğu görmezden gelip İKİNCİ bir blok eklerdi.
    # ⚠️ Eski blok kaldırılmıyor, yalnız "zaten yönetiliyor" sayılıyor: kaldırma
    # kodu bu sağlayıcıda CANLI SINANAMIYOR (abonelik yok, CLI kurulu değil), ve
    # sınanmamış bir dosya-değiştirme kolu kullanıcının config'inde çalıştırılmaz.
    _KIMI_LEGACY_MARKER = "# >>> unity-architect managed (permission rules) >>>"
    _KIMI_NEWER_ALLOW_PATTERNS = ("mcp__gamachineComputer__*", "mcp__gamachineBrowser__*",
                                  "mcp__gamachineConnectors__*")
    # kimi -p runs in "auto": every built-in that is not denied runs. These are
    # the documented 2.5.x built-ins (reference/tools.md) that write, run a
    # command or spawn work; the read tools stay open. A bare "*" pattern is
    # not documented, so each tool is named.
    _KIMI_DENY_PATTERNS = ("Write", "Edit", "Bash", "Agent", "AgentSwarm", "Skill",
                           "CronCreate", "CronDelete", "TaskStop")
    _KIMI_PERMISSION_BLOCK = (
        "\n" + _KIMI_MANAGED_MARKER + "\n"
        "# Built-in yazma/shell araçları DENY (deny mutlak — --print/--yolo bunu ezmez).\n"
        "# Model dosya/shell işlemlerini yalnız mcp__unityai__* ile yapar → app onay kartı.\n"
        + "".join(f'[[permission.rules]]\ndecision = "deny"\npattern = "{tool}"\n\n'
                  for tool in _KIMI_DENY_PATTERNS) +
        '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__unityai__*"\n\n'
        '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__gamachineComputer__*"\n\n'
        '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__gamachineBrowser__*"\n\n'
        '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__gamachineConnectors__*"\n'
        + _KIMI_MANAGED_END + "\n"
    )

    # -- MCP: $KIMI_CODE_HOME/mcp.json --
    # kimi 2.5.3 has no MCP file flag; the user-level mcp.json is the only file
    # it loads without trusting the project (turn-1 sources S1/S3). The file is
    # the user's, so Gamachine owns a fixed set of entry names and merges them
    # in. Nothing per turn or per chat goes into it: every stdio server inherits
    # kimi's own env (entry env merged on top, measured with kimi 2.5.3), so the
    # workspace, backend URL, Unity URL and connector ids ride `_mcp_env`, and
    # the chat id and read-only flag ride the base spawn env. The file changes
    # only with machine-wide state: Unity running and the
    # computer/browser/connectors switches.
    #
    # Every entry points at a tiny launcher under $KIMI_CODE_HOME/gamachine/,
    # never at the install: the file outlives the install (moved, updated,
    # uninstalled), and a command missing on disk crashes the user's own kimi
    # (spawn ENOENT, rc 1, measured 15 Nov 2026). The launcher runs the real
    # server only inside a Gamachine turn (marker env var, real command line in
    # per-server env vars); otherwise, or when the real command is gone, it
    # exits 0 at once with no output, which kimi tolerates (measured). So the
    # launcher and file texts hold no install path and no secret.
    #
    # `enabled: false` instead of a listed-but-dead server (measured, kimi 2.5.3,
    # 15 Nov 2026): prompt mode waits for every server's first connect before
    # the first model request, and the unityMCP bridge answers `initialize` only
    # after 10-17 s with Unity off, so a listed bridge delayed each turn ~30 s;
    # a disabled one costs nothing.
    #
    # `unityai` keeps its bare name: the hint, the permission rules already in
    # users' config.toml and the guide tests name its tools mcp__unityai__*.
    # The others carry the gamachine prefix; the Unity one is not `unityMCP`,
    # which is the name a user's own Unity MCP server would have.
    _KIMI_UNITY_SERVER = "gamachineUnityMCP"
    _KIMI_TURN_ENV = "GAMACHINE_KIMI_TURN"
    _policy_refusal_message = (
        "⚠️ Kimi'nin izin kuralları config.toml dosyasına yazılamadığı için işlem başlatılmadı. "
        "~/.kimi-code/config.toml dosyasını (ya da KIMI_CODE_HOME klasörünü) kontrol edin.")
    _mcp_env = {}  # this turn's env for the MCP servers, set by _register_mcp
    _mcp_failure = None  # why this turn runs without Gamachine's MCP tools, if it does

    @staticmethod
    def _launcher_env_names(name: str):
        key = name.upper()
        return f"GAMACHINE_KIMI_EXE_{key}", f"GAMACHINE_KIMI_CMD_{key}"

    @classmethod
    def _launcher_text(cls, name: str) -> str:
        exe, cmd = cls._launcher_env_names(name)
        if sys.platform == "win32":
            return ("@echo off\r\n"
                    f'if not "%{cls._KIMI_TURN_ENV}%"=="1" exit /b 0\r\n'
                    f'if not exist "%{exe}%" exit /b 0\r\n'
                    f"%{cmd}%\r\n")
        return ("#!/bin/sh\n"
                f'[ "${cls._KIMI_TURN_ENV}" = "1" ] || exit 0\n'
                f'[ -e "${exe}" ] || exit 0\n'
                f'eval "exec ${cmd}"\n')

    @classmethod
    def _kimi_launcher_path(cls, name: str) -> str:
        suffix = ".cmd" if sys.platform == "win32" else ""
        return os.path.join(os.path.dirname(cls._kimi_config_path()), "gamachine", name + suffix)

    @staticmethod
    def _write_private(path: str, data: bytes, replace: bool = True, executable: bool = False):
        """Writes `data` to `path` through a temp file next to it that is written,
        fsynced and made private BEFORE it takes the final name, so the visible
        file is never readable by others and never half written. replace=False
        links it into place and raises FileExistsError when `path` exists. The
        temp file is gone on every exit."""
        from .workspace_config import harden_config_file
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".gamachine-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if not harden_config_file(tmp):
                logger.warning("[KimiProvider] %s could not be made private.", path)
            if executable and os.name != "nt":
                os.chmod(tmp, 0o700)
            if replace:
                os.replace(tmp, path)
            else:
                os.link(tmp, path)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    @classmethod
    def _backup_file(cls, path: str, is_complete):
        """Copies `path` to `<path>.gamachine-backup`. An existing backup that
        `is_complete(bytes)` accepts is never touched; one that is empty or cut
        off counts as missing and is redone from the current file. Returns None
        when a complete backup exists afterwards, else the error text."""
        backup = path + ".gamachine-backup"
        try:
            with open(path, "rb") as f:
                data = f.read()
            try:
                with open(backup, "rb") as f:
                    old = f.read()
            except FileNotFoundError:
                old = None
            if old is not None and is_complete(old):
                return None
            cls._write_private(backup, data, replace=old is not None)
        except FileExistsError:
            return None  # another writer made it first
        except OSError as e:
            return str(e)
        return None

    @classmethod
    def _write_launcher(cls, name: str):
        """Writes the launcher for `name` (atomic, private, only when missing or
        different); returns its path, or None when it could not be written. A
        file that is not one of ours is copied to `<name>.gamachine-backup` first."""
        path = cls._kimi_launcher_path(name)
        data = cls._launcher_text(name).encode("utf-8")
        try:
            with _USER_MCP_LOCK:
                try:
                    with open(path, "rb") as f:
                        current = f.read()
                except FileNotFoundError:
                    current = None
                if current == data:
                    return path
                if current is not None and cls._KIMI_TURN_ENV.encode() not in current:
                    error = cls._backup_file(path, bool)
                    if error:
                        logger.warning("[KimiProvider] %s is not a Gamachine launcher and could "
                                       "not be backed up (%s); left unchanged.", path, error)
                        return None
                    logger.warning("[KimiProvider] %s is not a Gamachine launcher; it is replaced, "
                                   "the original is kept as %s.gamachine-backup", path, path)
                cls._write_private(path, data, executable=True)
            return path
        except OSError as e:
            logger.warning("[KimiProvider] launcher %s could not be written: %s", path, e)
            return None

    @staticmethod
    def _real_command(argv) -> tuple:
        """(command path, full command line) the launcher runs for this turn.
        ValueError for a double quote in any part: no current argument has one,
        and the cmd.exe line cannot carry it safely."""
        if any('"' in part for part in argv):
            raise ValueError("the command contains a double quote")
        command = argv[0]
        if not os.path.isabs(command):
            command = shutil.which(command) or command
        argv = [command, *argv[1:]]
        if sys.platform == "win32":
            return command, " ".join(_cmd_quote(arg) for arg in argv)
        return command, shlex.join(argv)

    def _register_mcp(self, launcher: str, workspace: str, backend_url: str):
        """Brings $KIMI_CODE_HOME/mcp.json and its launchers up to date and keeps
        this turn's per-turn values for the spawn env."""
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        from computer_use import registration as computer_reg
        from browser_use import registration as browser_reg
        from connectors import registration as connectors_reg
        from .codex_unitymcp_bridge import bridge_argv

        unity_url = unity_mcp_manager.mcp_url()
        env = {"WORKSPACE_PATH": workspace, "WORKSPACE": workspace,
               "UNITYAI_URL": backend_url, connectors_reg.IDS_ENV: "",
               self._KIMI_TURN_ENV: "1"}
        if unity_url:
            env["UNITY_MCP_URL"] = unity_url
        specs = [("unityai", [launcher], True),
                 (self._KIMI_UNITY_SERVER, bridge_argv(), bool(unity_url))]
        for reg in (computer_reg, browser_reg, connectors_reg):
            entry = reg.stdio_entry()  # None while the user's switch is off
            specs.append((reg.SERVER_NAME, reg.server_argv(), entry is not None))
            if reg is connectors_reg and entry:
                env.update({key: entry["env"][key] for key in
                            (connectors_reg.IDS_ENV, connectors_reg.ROOT_ENV) if key in entry["env"]})
        servers = {}
        failure = None
        for name, argv, enabled in specs:
            exe_env, cmd_env = self._launcher_env_names(name)
            try:
                env[exe_env], env[cmd_env] = self._real_command(argv)
            except ValueError as e:
                logger.warning("[KimiProvider] %s server is not registered: %s", name, e)
                failure = failure or _say(
                    self.language, f"{name} sunucusunun komutu çift tırnak içeriyor",
                    f"the {name} server's command contains a double quote")
                servers[name] = {"command": self._kimi_launcher_path(name), "args": [],
                                 "enabled": False}
                continue
            path = self._write_launcher(name)
            if path is None and name == "unityai":
                failure = _say(self.language,
                               f"başlatıcı yazılamadı ({self._kimi_launcher_path(name)})",
                               f"could not write the launcher ({self._kimi_launcher_path(name)})")
            servers[name] = {"command": path or self._kimi_launcher_path(name), "args": [],
                             "enabled": bool(enabled and path)}
        self._mcp_env = env
        try:
            failure = self._write_user_mcp(servers) or failure
        except Exception as e:  # the turn still runs, without Gamachine's MCP tools
            logger.warning("[KimiProvider] mcp.json could not be updated: %s", e)
            failure = f"{type(e).__name__}: {e}"
        self._mcp_failure = failure
        # False refuses the turn (cli_base): without the deny rules in config.toml
        # kimi -p would run Agent, Skill, Cron* and the rest unasked.
        if not self._write_kimi_permissions():
            return False

    @classmethod
    def _kimi_mcp_path(cls) -> str:
        return os.path.join(os.path.dirname(cls._kimi_config_path()), "mcp.json")

    @classmethod
    def _is_own_launcher(cls, entry) -> bool:
        """An entry whose command is one of the launchers Gamachine writes."""
        command = entry.get("command") if isinstance(entry, dict) else None
        if not isinstance(command, str):
            return False
        folder = os.path.dirname(cls._kimi_launcher_path("x"))
        return os.path.normcase(os.path.dirname(os.path.abspath(command))) == os.path.normcase(folder)

    @staticmethod
    def _is_json_object(raw: bytes) -> bool:
        try:
            return isinstance(json.loads(raw.decode("utf-8-sig")), dict)
        except ValueError:
            return False

    @classmethod
    def _backup_user_mcp(cls, path: str):
        """Keeps the user's mcp.json as `<path>.gamachine-backup`; None when a
        complete backup exists (an earlier one is never overwritten), else the error."""
        return cls._backup_file(path, cls._is_json_object)

    @classmethod
    def _write_mcp_json(cls, path: str, data: dict) -> bool:
        """Atomic write of the user's mcp.json that keeps their non-ASCII text
        readable (escaped only when it holds a lone surrogate, which UTF-8 cannot
        carry); a symlink is written through."""
        try:
            payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            payload = json.dumps(data, indent=2).encode("ascii")
        try:
            cls._write_private(os.path.realpath(path), payload)
            return True
        except OSError as e:
            logger.warning("[KimiProvider] %s could not be written (%s)", path, e)
            return False

    def _write_user_mcp(self, ours: dict):
        """Merges Gamachine's entries into the user's mcp.json: their servers and
        other keys stay as they are, nothing is written when ours already match,
        and an unreadable file is left alone (rewriting it would lose theirs).

        A user entry under one of our names (`unityai` is fixed: the tool names
        mcp__unityai__* are) that is not one of our launchers is replaced only
        after the file is copied once to mcp.json.gamachine-backup.

        Returns None on success, else a short user-facing reason it failed."""
        from .agy_provider import _read_json_config
        path = self._kimi_mcp_path()
        with _USER_MCP_LOCK:
            data = _read_json_config(path)
            servers = data.get("mcpServers", {}) if data is not None else None
            if not isinstance(servers, dict):
                logger.warning("[KimiProvider] %s cannot be merged safely; left unchanged, "
                               "kimi runs without Gamachine's MCP tools.", path)
                return _say(self.language, f"{path} okunamadı ya da geçerli bir mcp.json değil",
                            f"{path} could not be read or is not a valid mcp.json")
            if all(servers.get(name) == entry for name, entry in ours.items()):
                return None
            foreign = [name for name in ours if name in servers
                       and not self._is_own_launcher(servers[name])]
            if foreign:
                error = self._backup_user_mcp(path)
                if error:
                    logger.warning("[KimiProvider] backup of %s failed (%s); left unchanged.",
                                   path, error)
                    return _say(self.language, f"{path} yedeklenemedi", f"{path} could not be backed up")
                logger.warning("[KimiProvider] %s already has an entry named %s that is not "
                               "Gamachine's; it is replaced, the original file is kept as "
                               "%s.gamachine-backup", path, ", ".join(foreign), path)
            data["mcpServers"] = {**servers, **ours}
            if not self._write_mcp_json(path, data):
                return _say(self.language, f"{path} yazılamadı", f"{path} could not be written")
            return None

    @staticmethod
    def _kimi_config_path() -> str:
        cfg_home = os.environ.get("KIMI_CODE_HOME") or os.path.expanduser("~/.kimi-code")
        return os.path.join(cfg_home, "config.toml")

    def _kimi_permissions_confirmed(self, path: str) -> bool:
        """True when config.toml, read back from disk, holds the current deny rules."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"[KimiProvider] config.toml okunamadı: {e}")
            return False
        start = content.find(self._KIMI_MANAGED_MARKER)
        if start >= 0:
            end = content.find(self._KIMI_MANAGED_END, start)
            return end >= 0 and (content[start:end + len(self._KIMI_MANAGED_END)]
                                 == self._KIMI_PERMISSION_BLOCK.strip("\n"))
        if self._KIMI_LEGACY_MARKER in content:
            return all(f'"{pattern}"' in content for pattern in
                       (*self._KIMI_DENY_PATTERNS, *self._KIMI_NEWER_ALLOW_PATTERNS))
        return False

    def _write_kimi_permissions(self) -> bool:
        """~/.kimi-code/config.toml'a deny/allow bloğunu idempotent EKLER (mevcut
        içeriği korur — kullanıcının kendi provider/login ayarları bozulmaz).
        True yalnız, kurallar dosyadan geri okunup doğrulandığında: aksi halde
        tur başlamaz (kimi -p "auto" modunda, yasaklanmayan her araç çalışır)."""
        path = self._kimi_config_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError as e:
            logger.warning(f"[KimiProvider] config dizini oluşturulamadı: {e}")
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
        except FileNotFoundError:
            existing = ""
        except Exception as e:
            # Appending to a file that could not be read could duplicate the block.
            logger.warning(f"[KimiProvider] config.toml okunamadı: {e}")
            return False
        start = existing.find(self._KIMI_MANAGED_MARKER)
        if start >= 0:
            # A block from an older build lacks the newer rules: replace it in
            # place when its text differs. A block with no end marker cannot be
            # replaced safely, so the turn is refused.
            end = existing.find(self._KIMI_MANAGED_END, start)
            if end < 0:
                logger.error(f"[KimiProvider] {path}: yönetilen blokta bitiş işaretçisi yok")
                return False
            end += len(self._KIMI_MANAGED_END)
            current = self._KIMI_PERMISSION_BLOCK.strip("\n")
            if existing[start:end] == current:
                return True
            tmp = None
            try:
                # mkstemp makes the file 0600; config.toml can hold provider keys,
                # so the original's mode (not the umask default) must carry over.
                # Replace the link's target, not the link: a symlinked config.toml
                # (dotfiles) must keep pointing at the refreshed file.
                target = os.path.realpath(path)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".config-", suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(existing[:start] + current + existing[end:])
                os.chmod(tmp, os.stat(target).st_mode & 0o7777)
                os.replace(tmp, target)
                tmp = None
                logger.info(f"[KimiProvider] permission.rules güncellendi: {path}")
            except Exception as e:
                logger.warning(f"[KimiProvider] config.toml güncellenemedi: {e}")
            finally:
                if tmp is not None:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
            return self._kimi_permissions_confirmed(path)
        to_write = self._KIMI_PERMISSION_BLOCK
        if self._KIMI_LEGACY_MARKER in existing:
            # The old block stays; only the rules it predates are appended.
            to_write = "".join(
                f'\n[[permission.rules]]\ndecision = "{decision}"\npattern = "{pattern}"\n'
                for decision, patterns in (("deny", self._KIMI_DENY_PATTERNS),
                                           ("allow", self._KIMI_NEWER_ALLOW_PATTERNS))
                for pattern in patterns
                if f'"{pattern}"' not in existing)
            if not to_write:
                return True  # zaten yazılı — idempotent
            if existing and not existing.endswith("\n"):
                to_write = "\n" + to_write
        try:
            # Created here only when absent; keep a new config.toml private.
            with open(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a", encoding="utf-8") as f:
                f.write(to_write)
            logger.info(f"[KimiProvider] permission.rules yazıldı: {path}")
        except Exception as e:
            logger.warning(f"[KimiProvider] config.toml yazılamadı: {e}")
        return self._kimi_permissions_confirmed(path)

    def _turn_notices(self) -> list:
        if not self._mcp_failure:
            return []
        return [{"type": "warning", "code": "kimi_no_gamachine_tools",
                 "message": _say(
                     self.language,
                     "⚠️ Kimi bu turda Gamachine araçları olmadan çalışıyor "
                     f"({self._mcp_failure}); dosya yazma ve komut çalıştırma kullanılamaz.",
                     "⚠️ Kimi is running this turn without Gamachine tools "
                     f"({self._mcp_failure}); file writes and command execution are unavailable.")}]

    def _build_hint(self, unity_running: bool) -> str:
        if self._mcp_failure:
            return (
                "TOOL POLICY — Gamachine's MCP tools (mcp__unityai__*, mcp__gamachine*__*) are NOT\n"
                "available in this turn, and your built-in Write, Edit and Bash tools are DENIED.\n"
                "You cannot create, edit or delete files or run shell commands now: say so in one\n"
                "short sentence when the request needs it, and answer anything else from what you\n"
                "can read. Never claim an action was done.\n\n")
        unity_section = ""
        if unity_running:
            unity_section = (
                "\nUNITY EDITOR (unityMCP tools — call DIRECTLY for ALL Unity work). Here the\n"
                "server is named gamachineUnityMCP: unityMCP/<tool> below and in the guide\n"
                "is the tool mcp__gamachineUnityMCP__<tool>.\n"
                "RULE: NEVER read .unity/.prefab/.asset files to answer Unity questions —\n"
                "      ALWAYS call the live editor via unityMCP tools instead.\n"
                "- Hierarchy/objects: unityMCP/manage_scene, find_gameobjects, manage_gameobject\n"
                "- Components/UI:      unityMCP/manage_components, manage_ui\n"
                "- Console/editor:     unityMCP/read_console, manage_editor\n"
                "- Materials/physics:  unityMCP/manage_material, manage_physics\n"
                "3D assets (meshy_*): call meshy MCP tools directly; they cost credits →\n"
                "  state the cost and get user confirmation first.\n"
            )
        return (
            "TOOL POLICY — follow exactly:\n"
            + unity_section +
            "\nFILE & SHELL operations — your built-in Write, Edit and Bash tools are DENIED.\n"
            "The ONLY way to create/edit/delete a file or run a shell command is the unityai\n"
            "MCP tools (they route through the app so the user can approve each action):\n"
            "- Create/edit a .cs or text file: mcp__unityai__save_file\n"
            "- Delete a file:                  mcp__unityai__delete_file\n"
            "- Shell (git, npm, mkdir, …):     mcp__unityai__run_terminal_command\n"
            "- Read a file:                    mcp__unityai__read_file\n"
            "- List a directory:               mcp__unityai__list_directory\n"
            + mail_and_fallback(mcp_double_underscore) +
            "Do NOT route unityMCP/meshy through unityai — call those as their own MCP tools.\n"
            "SCOPE: only the current workspace. Be concise; never say you cannot do something —\n"
            "use the tools. For file writes reply with ONE short sentence (the approval\n"
            "card already shows the code/diff); for questions give a complete, substantive answer.\n\n"
        )

    def _build_cmd(self, prompt: str, thinking_level: str = "medium", workspace: str = None) -> list:
        self._write_kimi_permissions()
        self._kimi_error_reported = False
        workspace = workspace or os.getcwd()

        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        unity_running = unity_mcp_manager.is_running()
        # unityai launcher'ın exec bitini garantile (paket kopyalama düşürebilir)
        self._ensure_exec(self._launcher_path("unityai"))

        hint = self._build_hint(unity_running)
        from agent_guide import provider_guide
        hint += provider_guide(self, project_open=bool(workspace), unity_running=unity_running)
        # The working directory is the spawn cwd (cli_base); kimi has no -w flag.
        # The prompt never starts with "/" (the hint comes first), so prompt
        # mode cannot read it as a slash command such as /goal.
        return [
            self._kimi_binary(),
            "--output-format", "stream-json",
            "-m", kimi_cli_model(self.binary_name),
            "-p", hint + prompt,
        ]

    @staticmethod
    def _kimi_binary() -> str:
        """`kimi` from PATH, else the official installer's ~/.kimi-code/bin copy:
        a backend started before the install still has the old PATH."""
        if shutil.which("kimi"):
            return "kimi"
        for name in ("kimi.exe", "kimi") if sys.platform == "win32" else ("kimi",):
            path = os.path.join(os.path.expanduser("~"), ".kimi-code", "bin", name)
            if os.path.isfile(path):
                return path
        return "kimi"

    def _turn_spawn_env(self) -> dict:
        env = {
            **super()._turn_spawn_env(),
            **self._mcp_env,
            # Prompt mode runs an update preflight before the turn; these keep a
            # chat turn from updating or relaunching the CLI (KIMI_CLI_* is the
            # legacy spelling the 2.5.3 binary still reads).
            "KIMI_CODE_NO_AUTO_UPDATE": "1",
            "KIMI_CLI_NO_AUTO_UPDATE": "1",
            "KIMI_DISABLE_TELEMETRY": "1",
        }
        from .effort_caps import map_effort
        env.update(map_effort("subscription", self.binary_name,
                              getattr(self, "_effort_level", "auto")).get("cli_env", {}))
        return env

    # ── stream-json ────────────────────────────────────────────────────────
    # kimi 2.5.3 `PromptJsonWriter` (prompt-render.ts) writes one JSON object
    # per line: a meta version line first; one FULL assistant message per model
    # step (text is buffered, not streamed; thinking is never written), with
    # `tool_calls[].function.arguments` as a JSON string; a tool message per
    # result; meta retry lines; a meta resume hint carrying the session id last.
    # Failures are stderr text plus exit 1, except "session occupied", which is
    # a role-less {"type": "error"} line and exit 4.
    def _stream_json_events(self, ev, text_so_far: str):
        if not isinstance(ev, dict):
            return []
        role = ev.get("role")
        if role == "assistant":
            out = []
            text = ev.get("content")
            if isinstance(text, list):  # defensive: content-part form, not written by 2.5.3
                text = "".join(part.get("text", "") for part in text
                               if isinstance(part, dict) and isinstance(part.get("text"), str))
            if isinstance(text, str) and text:
                # Each step is a whole message; keep consecutive steps apart.
                if text_so_far and not text_so_far.endswith("\n"):
                    text = "\n\n" + text
                out.append({"type": "delta", "text": text})
            for call in ev.get("tool_calls") or []:
                fn = call.get("function") if isinstance(call, dict) else None
                name = fn.get("name") if isinstance(fn, dict) else None
                if name:
                    out.append({"type": "thinking",
                                "text": self._kimi_tool_hint(name, fn.get("arguments"))})
            return out
        if role == "tool":
            result = ev.get("content")
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False) if result is not None else ""
            result = result[:200].strip()
            return [{"type": "thinking", "text": f"↩ {result}"}] if result else []
        if role == "meta":
            kind = ev.get("type")
            if kind == "session.resume_hint" and ev.get("session_id"):
                return [{"type": "session_meta", "session_id": str(ev["session_id"])}]
            if kind == "turn.step.retrying":
                reason = str(ev.get("error_message") or ev.get("error_name") or "").strip()[:160]
                attempt = f"{ev.get('next_attempt', '?')}/{ev.get('max_attempts', '?')}"
                return [{"type": "thinking",
                         "text": _say(self.language, f"↻ Kimi isteği yeniden deneniyor ({attempt})",
                                      f"↻ Retrying the Kimi request ({attempt})")
                                 + (f": {reason}" if reason else "")}]
            return []  # system.version and any newer meta line
        if ev.get("type") == "error":
            message = str(ev.get("message") or ev.get("code") or "").strip()
            self._kimi_error_reported = True
            return [{"type": "error", "content": f"❌ Kimi Code: {message or _say(self.language, 'bilinmeyen hata', 'unknown error')}",
                     "code": str(ev.get("code") or "kimi_error")}]
        return []  # goal.summary and lines this version does not write

    @staticmethod
    def _kimi_tool_hint(name: str, arguments) -> str:
        hint = f"🔧 `{name}`"
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
        except ValueError:
            return hint
        if isinstance(args, dict):
            for key in ("path", "file_path", "command", "action"):
                if args.get(key):
                    return hint + f" → `{str(args[key])[:80]}`"
        return hint

    # ── failures ───────────────────────────────────────────────────────────
    # STATUS_STACK_BUFFER_OVERRUN: measured on Windows (kimi 2.5.3, 14 Nov 2026)
    # on some error exits; asyncio reports it unsigned, other paths signed.
    _KIMI_CRASH_CODES = (3221226505, -1073740791)
    # Startup errors the CLI prints when no usable login/model exists (kimi.exe
    # 2.5.3: requireConfiguredModel, AuthSummaryService.ensureReady, auth errors).
    _KIMI_LOGIN_MARKERS = (
        "no model configured", "login required", "use /login", "run /login", "send /login", "(try /login)",
        "oauth login expired", "llm not set", "no provider configured",
        "has no credential configured", "no default model configured",
        "does not resolve to a configured provider",
    )
    _KIMI_STDERR_NOISE = ("Warning: this folder is not trusted", "Run kimi here and choose")

    def _failure_event(self, returncode, stderr_lines, text):
        if returncode in (0, None):
            return None
        detail = "\n".join(line for line in stderr_lines
                           if line.strip() and not line.startswith(self._KIMI_STDERR_NOISE))
        detail = detail.strip()[:400]
        if returncode in self._KIMI_CRASH_CODES:
            return {"type": "error", "code": "kimi_crashed", "reason": "process_exit",
                    "reset_session": True,
                    "content": (_say(self.language,
                                     "❌ Kimi Code CLI çöktü (çıkış kodu 0xC0000409); mesajın "
                                     "işlenmemiş olabilir. Tekrar dene; sürerse bir terminalde "
                                     "`kimi doctor` ile kurulumu kontrol et.",
                                     "❌ The Kimi Code CLI crashed (exit code 0xC0000409); your "
                                     "message may not have been processed. Try again; if it "
                                     "keeps happening, check the install with `kimi doctor` in "
                                     "a terminal.")
                                + (f"\n\n{detail}" if detail else ""))}
        if getattr(self, "_kimi_error_reported", False):
            return {}  # the stream's own error line already said it
        if text.strip():
            if not detail:
                return None  # an answer came: generic exit-code handling
            # rc 1 is kimi's only failure signal and the generic code reads it as
            # success, so a failure after the first step's text would look finished.
            event = {"type": "error", "code": "kimi_failed", "reason": "process_exit",
                     "content": _say(self.language,
                                     f"⚠️ Kimi Code turu yarıda kesildi (rc={returncode}): {detail}",
                                     f"⚠️ The Kimi Code turn was cut short (rc={returncode}): {detail}")}
            if returncode != 1:
                event["reset_session"] = True  # as the generic branch: a killed CLI can leave a half-written session
            return event
        low = detail.lower()
        if "is not configured in config.toml" in low:
            # An empty home answers this too, so a missing login lands here first.
            return {"type": "error", "code": "kimi_model_unavailable",
                    "reason": "provider_access", "retryable": False,
                    "content": _say(
                        self.language,
                        f"⚠️ Kimi Code `{kimi_cli_model(self.binary_name)}` modelini "
                        "tanımıyor. Kimi Code'a giriş yapmadıysan bir terminalde "
                        "`kimi login` çalıştır; giriş yaptıysan aboneliğin bu modeli "
                        "sunmuyor olabilir, model seçiciden başka bir Kimi modeli seç.",
                        f"⚠️ Kimi Code does not recognize the `{kimi_cli_model(self.binary_name)}` "
                        "model. If you have not logged in to Kimi Code, run `kimi login` in a "
                        "terminal; if you have, your subscription may not offer this model, so "
                        "pick another Kimi model in the model selector.")}
        if any(marker in low for marker in self._KIMI_LOGIN_MARKERS):
            return {"type": "error", "code": "kimi_not_logged_in",
                    "reason": "provider_access", "retryable": False,
                    "content": _say(
                        self.language,
                        "🔑 Kimi Code'a giriş yapılmamış. Bir terminalde `kimi login` "
                        "çalıştırıp giriş yap, sonra tekrar dene.",
                        "🔑 You are not logged in to Kimi Code. Run `kimi login` in a "
                        "terminal, log in, then try again.")}
        return {"type": "error", "code": "kimi_failed", "reason": "process_exit",
                "content": _say(
                    self.language,
                    f"⚠️ Kimi Code yanıt üretmedi (rc={returncode}): {detail or '(boş)'}",
                    f"⚠️ Kimi Code produced no response (rc={returncode}): {detail or '(empty)'}")}
