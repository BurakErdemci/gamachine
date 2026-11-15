import os
import re
import sys
import json
import shlex
import shutil
import logging
import subprocess
import tempfile
import threading
from .cli_base import BaseCLIProvider
from .families import kimi_cli_model
from .unityai_tool_text import mail_and_fallback, mcp_double_underscore

logger = logging.getLogger(__name__)

# One read-merge-write of the user's mcp.json at a time in this process.
_USER_MCP_LOCK = threading.Lock()


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
    _KIMI_PERMISSION_BLOCK = (
        "\n" + _KIMI_MANAGED_MARKER + "\n"
        "# Built-in yazma/shell araçları DENY (deny mutlak — --print/--yolo bunu ezmez).\n"
        "# Model dosya/shell işlemlerini yalnız mcp__unityai__* ile yapar → app onay kartı.\n"
        '[[permission.rules]]\ndecision = "deny"\npattern = "Write"\n\n'
        '[[permission.rules]]\ndecision = "deny"\npattern = "Edit"\n\n'
        '[[permission.rules]]\ndecision = "deny"\npattern = "Bash"\n\n'
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

    @classmethod
    def _write_launcher(cls, name: str):
        """Writes the launcher for `name` (atomic, private, only when missing or
        different); returns its path, or None when it could not be written."""
        from .workspace_config import harden_config_file
        path = cls._kimi_launcher_path(name)
        data = cls._launcher_text(name).encode("utf-8")
        tmp = None
        try:
            with _USER_MCP_LOCK:
                try:
                    with open(path, "rb") as f:
                        if f.read() == data:
                            return path
                except OSError:
                    pass
                os.makedirs(os.path.dirname(path), exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".gamachine-", suffix=".tmp")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
                tmp = None
                if not harden_config_file(path):
                    logger.warning("[KimiProvider] %s could not be made private.", path)
                if os.name != "nt":
                    os.chmod(path, 0o700)
            return path
        except OSError as e:
            logger.warning("[KimiProvider] launcher %s could not be written: %s", path, e)
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return None

    @staticmethod
    def _real_command(argv) -> tuple:
        """(command path, full command line) the launcher runs for this turn."""
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
            env[exe_env], env[cmd_env] = self._real_command(argv)
            path = self._write_launcher(name)
            if path is None and name == "unityai":
                failure = f"başlatıcı yazılamadı ({self._kimi_launcher_path(name)})"
            servers[name] = {"command": path or self._kimi_launcher_path(name), "args": [],
                             "enabled": bool(enabled and path)}
        self._mcp_env = env
        try:
            failure = self._write_user_mcp(servers) or failure
        except Exception as e:  # the turn still runs, without Gamachine's MCP tools
            logger.warning("[KimiProvider] mcp.json could not be updated: %s", e)
            failure = f"{type(e).__name__}: {e}"
        self._mcp_failure = failure

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
    def _backup_user_mcp(path: str):
        """Copies the user's mcp.json once to `<path>.gamachine-backup`, never over
        an existing backup. Returns None when a backup exists, else the error."""
        from .workspace_config import harden_config_file
        backup = path + ".gamachine-backup"
        try:
            with open(path, "rb") as src, open(backup, "xb") as dst:
                dst.write(src.read())
        except FileExistsError:
            return None
        except OSError as e:
            return str(e)
        if not harden_config_file(backup):
            logger.warning("[KimiProvider] %s could not be made private.", backup)
        return None

    @staticmethod
    def _write_mcp_json(path: str, data: dict) -> bool:
        """Atomic write of the user's mcp.json (temp file + os.replace) that keeps
        their non-ASCII text readable; a symlink is written through and the
        file's permission bits are kept."""
        import stat
        target = os.path.realpath(path)
        directory = os.path.dirname(target)
        tmp = None
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".gamachine-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            if os.name != "nt" and os.path.exists(target):
                os.chmod(tmp, stat.S_IMODE(os.stat(target).st_mode))  # mkstemp made it 0600
            os.replace(tmp, target)
            return True
        except OSError as e:
            logger.warning("[KimiProvider] %s could not be written (%s)", path, e)
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
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
        from .workspace_config import harden_config_file
        path = self._kimi_mcp_path()
        with _USER_MCP_LOCK:
            data = _read_json_config(path)
            servers = data.get("mcpServers", {}) if data is not None else None
            if not isinstance(servers, dict):
                logger.warning("[KimiProvider] %s cannot be merged safely; left unchanged, "
                               "kimi runs without Gamachine's MCP tools.", path)
                return f"{path} okunamadı ya da geçerli bir mcp.json değil"
            if all(servers.get(name) == entry for name, entry in ours.items()):
                return None
            foreign = [name for name in ours if name in servers
                       and not self._is_own_launcher(servers[name])]
            if foreign:
                error = self._backup_user_mcp(path)
                if error:
                    logger.warning("[KimiProvider] backup of %s failed (%s); left unchanged.",
                                   path, error)
                    return f"{path} yedeklenemedi"
                logger.warning("[KimiProvider] %s already has an entry named %s that is not "
                               "Gamachine's; it is replaced, the original file is kept as "
                               "%s.gamachine-backup", path, ", ".join(foreign), path)
            data["mcpServers"] = {**servers, **ours}
            if not self._write_mcp_json(path, data):
                return f"{path} yazılamadı"
            if not harden_config_file(os.path.realpath(path)):
                logger.warning("[KimiProvider] %s could not be made private.", path)
            return None

    @staticmethod
    def _kimi_config_path() -> str:
        cfg_home = os.environ.get("KIMI_CODE_HOME") or os.path.expanduser("~/.kimi-code")
        return os.path.join(cfg_home, "config.toml")

    def _write_kimi_permissions(self):
        """~/.kimi-code/config.toml'a deny/allow bloğunu idempotent EKLER (mevcut
        içeriği korur — kullanıcının kendi provider/login ayarları bozulmaz)."""
        path = self._kimi_config_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError as e:
            logger.warning(f"[KimiProvider] config dizini oluşturulamadı: {e}")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
        except FileNotFoundError:
            existing = ""
        except Exception as e:
            logger.warning(f"[KimiProvider] config.toml okunamadı: {e}")
            existing = ""
        start = existing.find(self._KIMI_MANAGED_MARKER)
        if start >= 0:
            # A block from an older build lacks the newer rules: replace it in
            # place when its text differs; a block with no end marker is left alone.
            end = existing.find(self._KIMI_MANAGED_END, start)
            if end < 0:
                return
            end += len(self._KIMI_MANAGED_END)
            current = self._KIMI_PERMISSION_BLOCK.strip("\n")
            if existing[start:end] == current:
                return
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
            return
        to_write = self._KIMI_PERMISSION_BLOCK
        if self._KIMI_LEGACY_MARKER in existing:
            # The old block stays; only the allow rules it predates are appended.
            to_write = "".join(
                f'\n[[permission.rules]]\ndecision = "allow"\npattern = "{pattern}"\n'
                for pattern in self._KIMI_NEWER_ALLOW_PATTERNS
                if f'"{pattern}"' not in existing)
            if not to_write:
                return  # zaten yazılı — idempotent
            if existing and not existing.endswith("\n"):
                to_write = "\n" + to_write
        try:
            # Created here only when absent; keep a new config.toml private.
            with open(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a", encoding="utf-8") as f:
                f.write(to_write)
            logger.info(f"[KimiProvider] permission.rules yazıldı: {path}")
        except Exception as e:
            logger.warning(f"[KimiProvider] config.toml yazılamadı: {e}")

    def _turn_notices(self) -> list:
        if not self._mcp_failure:
            return []
        return [{"type": "thinking", "code": "kimi_no_gamachine_tools",
                 "text": ("⚠️ Kimi bu turda Gamachine araçları olmadan çalışıyor "
                          f"({self._mcp_failure}); dosya yazma ve komut çalıştırma kullanılamaz.")}]

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
                         "text": f"↻ Kimi isteği yeniden deneniyor ({attempt})"
                                 + (f": {reason}" if reason else "")}]
            return []  # system.version and any newer meta line
        if ev.get("type") == "error":
            message = str(ev.get("message") or ev.get("code") or "").strip()
            self._kimi_error_reported = True
            return [{"type": "error", "content": f"❌ Kimi Code: {message or 'bilinmeyen hata'}",
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
        "no model configured", "login required", "use /login", "no provider configured",
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
                    "content": ("❌ Kimi Code CLI çöktü (çıkış kodu 0xC0000409); mesajın "
                                "işlenmemiş olabilir. Tekrar dene; sürerse bir terminalde "
                                "`kimi doctor` ile kurulumu kontrol et."
                                + (f"\n\n{detail}" if detail else ""))}
        if getattr(self, "_kimi_error_reported", False):
            return {}  # the stream's own error line already said it
        if text.strip():
            if not detail:
                return None  # an answer came: generic exit-code handling
            # rc 1 is kimi's only failure signal and the generic code reads it as
            # success, so a failure after the first step's text would look finished.
            return {"type": "error", "code": "kimi_failed", "reason": "process_exit",
                    "content": f"⚠️ Kimi Code turu yarıda kesildi (rc={returncode}): {detail}"}
        low = detail.lower()
        if "is not configured in config.toml" in low:
            # An empty home answers this too, so a missing login lands here first.
            return {"type": "error", "code": "kimi_model_unavailable",
                    "reason": "provider_access", "retryable": False,
                    "content": (f"⚠️ Kimi Code `{kimi_cli_model(self.binary_name)}` modelini "
                                "tanımıyor. Kimi Code'a giriş yapmadıysan bir terminalde "
                                "`kimi login` çalıştır; giriş yaptıysan aboneliğin bu modeli "
                                "sunmuyor olabilir, model seçiciden başka bir Kimi modeli seç.")}
        if any(marker in low for marker in self._KIMI_LOGIN_MARKERS):
            return {"type": "error", "code": "kimi_not_logged_in",
                    "reason": "provider_access", "retryable": False,
                    "content": ("🔑 Kimi Code'a giriş yapılmamış. Bir terminalde `kimi login` "
                                "çalıştırıp giriş yap, sonra tekrar dene.")}
        return {"type": "error", "code": "kimi_failed", "reason": "process_exit",
                "content": f"⚠️ Kimi Code yanıt üretmedi (rc={returncode}): {detail or '(boş)'}"}
