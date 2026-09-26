import os
import sys
import json
import time
import shutil
import logging
from typing import Optional
from .cli_base import BaseCLIProvider
from agy_step_gate import CLOSED_MODE, GATED_TOOLS, PS_UTF8_PREFIX, RUN_TOOL, write_state

logger = logging.getLogger(__name__)

# Step mode gate for agy's own file-writing tools.
#
# `disabledTools` no longer turns them off: agy 1.2.8 still lists and runs
# write_to_file with it set (measured 25 Sep 2026, the file was created). No
# `toolPermission` value separates them from MCP calls either: request-review
# still lets write_to_file edit the workspace and refuses MCP calls, strict
# refuses everything. A workspace PreToolUse hook does separate them (measured:
# writes denied with our reason, call_mcp_tool untouched, even under
# always-proceed). With the hook, a file write has to go through
# `unityai save-file`, which raises an approval card.
#
# run_command is gated too: the hook (`backend agy-hook`, agy_step_gate.py)
# allows only the exact unityai bridge call shapes and denies every other
# command line. The rule and why it is strict are in agy_step_gate.py.
#
# The hook is installed in both approval modes; its state file says which one
# is on (auto allows every call), so a flip reaches a running agy on its next
# tool call. See _write_step_gate.
STEP_GATE_TOOLS = GATED_TOOLS + (RUN_TOOL,)
STEP_GATE_KEY = "gamachine-step-gate"

# agy hands its whole environment, Gemini/Google keys included, to every stdio
# MCP server it starts (measured: the child saw both 39-character keys). An
# entry's own env wins over the inherited one (measured: set to "", the child
# saw them empty), so the entries Gamachine writes blank them; neither the
# unityai server nor the Unity MCP bridge reads them. Entries Gamachine does
# not write (meshy, playwright, added by the user or the IDE) keep the leak.
AGY_CHILD_SECRET_BLANKS = {
    "GEMINI_API_KEY": "", "GOOGLE_API_KEY": "", "GOOGLE_APPLICATION_CREDENTIALS": "",
}
STEP_GATE_HOOKS_FILE = ".agents/hooks.json"


def _gate_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".unity_architect_ai", "agy")


class AgyStepGateError(RuntimeError):
    """The gate is not verifiably installed, so agy is not started (in either
    mode, see _write_step_gate). The message reaches the user as is, hence
    Turkish; a `code` (with its `params`) lets the UI show its own language."""

    def __init__(self, message: str, *, code: Optional[str] = None, **params):
        super().__init__(message)
        self.code = code
        self.params = params


_GATE_HEAD = ("Adım adım onay modu açık, ama onay kapısı kurulamadığı için agy "
              "başlatılmadı (kapı olmadan agy her yazma ve komutu onay sormadan çalıştırırdı).\n")
_GATE_HEAD_AUTO = ("Onay kapısı kurulamadığı için agy başlatılmadı. Otomatik moddasınız, ama "
                   "kapı olmadan başlayan bir agy, konuşma sırasında adım adım onay moduna "
                   "geçseniz de her yazma ve komutu onay sormadan çalıştırmaya devam ederdi.\n")


def _gate_head(step_mode: bool) -> str:
    return _GATE_HEAD if step_mode else _GATE_HEAD_AUTO


def _gate_hooks_unreadable(path: str, detail, step_mode: bool = True) -> str:
    return (_gate_head(step_mode)
            + f"Sorun: {path} dosyası okunamıyor ya da geçerli bir JSON nesnesi değil ({detail}).\n"
            "Bu dosyada size ait hook'lar olabileceği için Gamachine ona dokunmadı. Dosyayı "
            "düzeltin (en dışta { ... } olan geçerli bir JSON olmalı) ya da içinde sizin "
            "eklediğiniz bir şey yoksa silin; sonra mesajınızı yeniden gönderin.")


def _gate_write_failed(path: str, detail, step_mode: bool = True) -> str:
    return (_gate_head(step_mode)
            + f"Sorun: {path} yazılamadı ({detail}).\n"
            "Dosyanın ve klasörünün yazılabilir olduğundan emin olun (izin, dolu disk, dosyayı "
            "kilitleyen bir program ya da çalışma klasörünün dışına yönlendirilmiş bir klasör "
            "olabilir); sonra mesajınızı yeniden gönderin.")


def _gate_not_verified(path: str, detail, step_mode: bool = True) -> str:
    return (_gate_head(step_mode)
            + f"Sorun: {path} yazıldı ama geri okunduğunda onay kapısı doğrulanamadı ({detail}).\n"
            "Dosyayı aynı anda değiştiren başka bir program olabilir; onu kapatıp mesajınızı "
            "yeniden gönderin.")


def gate_state_path() -> str:
    return os.path.join(_gate_dir(), "step-gate.json")


def write_gate_state(auto: bool, closed: bool = False) -> None:
    """Mode and launcher the hook reads on every call ("auto" allows every
    call, "step" applies agy_step_gate's grammar). Written at spawn and on
    every mode flip, so a flip in either direction bites on a running agy's
    next tool call. `closed` writes CLOSED_MODE, which denies every call: a
    closing session's child never needs a tool again (agy_session.close)."""
    if closed:
        write_state(gate_state_path(), CLOSED_MODE, "")
        return
    launcher = AgyProvider()._launcher_path("unityai")
    write_state(gate_state_path(), "auto" if auto else "step", launcher)


def _read_json_config(path: str, default: dict = None) -> Optional[dict]:
    """A config file's top-level object, `default` (or {}) when the file is
    missing or empty, None when it exists but cannot be used.

    None means leave the file alone: these files hold the user's own entries
    (meshy, playwright, trustedWorkspaces), and rewriting an unparseable one
    from scratch deleted them (audit 25 Sep 2026: a stray comma, a cp1254
    byte or a top-level array each lost the user's data).
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return dict(default or {})
    except OSError as e:
        logger.warning("[agy] %s could not be read (%s); left untouched", path, e)
        return None
    try:
        text = raw.decode("utf-8-sig")
        data = json.loads(text) if text.strip() else dict(default or {})
    except ValueError as e:
        logger.warning("[agy] %s is not valid UTF-8 JSON (%s); left untouched", path, e)
        return None
    if not isinstance(data, dict):
        logger.warning("[agy] %s top level is not an object; left untouched", path)
        return None
    return data


def _write_json_config(path: str, data: dict) -> bool:
    """Atomic write (temp file + os.replace), so a crash or a full disk
    mid-write cannot leave half a file. A symlinked config is written through
    to its target, and the target's permission bits are kept."""
    import stat
    import tempfile
    target = os.path.realpath(path)
    directory = os.path.dirname(target)
    tmp = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".gamachine-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt" and os.path.exists(target):
            os.chmod(tmp, stat.S_IMODE(os.stat(target).st_mode))  # mkstemp made it 0600
        os.replace(tmp, target)
        return True
    except OSError as e:
        logger.warning("[agy] %s could not be written (%s)", path, e)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def _short_path(path: str) -> str:
    """8.3 form of a Windows path, or the path unchanged if there is none.

    agy hands a hook command to the shell with its quotes escaped, so a quoted
    path with a space fails (measured: '\\"C:\\...' not found). A failing hook
    still blocks the tool, but the model then sees a shell error instead of the
    reason, so the path is shortened to lose the space.
    """
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
        return buf.value if 0 < n < len(buf) else path
    except Exception:
        return path


class AgyProvider(BaseCLIProvider):

    @staticmethod
    def _agy_binary() -> str:
        """agy CLI'ını kullanıcının PC'sinde bulur (gömülü değil — dış kurulum).
        PATH'te yoksa yaygın kurulum yollarına bakar, son çare 'agy'."""
        found = shutil.which("agy")
        if found:
            return found
        for cand in (
            os.path.expanduser("~/.local/bin/agy"),
            "/opt/homebrew/bin/agy",
            "/usr/local/bin/agy",
        ):
            if os.path.exists(cand):
                return cand
        return "agy"  # PATH'e güven (subprocess başlatılınca çözülür)

    def _build_cmd(self, prompt: str = "", thinking_level: str = "medium", workspace: str = None) -> list:
        """Build persistent stream argv; user content is sent only through stdin."""
        self._pending_agy_model = self._AGY_MODEL_MAP.get(
            self.binary_name, "Gemini 3.6 Flash (High)")
        cmd = [self._agy_binary(), "--input-format", "stream-json",
               "--output-format", "stream-json", "-p="]
        if workspace:
            cmd += ["--add-dir", workspace]
        if getattr(self, "_resume_uuid", None):
            cmd += ["--conversation", self._resume_uuid]
        return cmd

    async def analyze_code(self, prompt: str, max_tokens: int = 4096,
                           images=None, thinking_level: str = "medium",
                           cwd: Optional[str] = None, interactive: bool = False):
        """One-shot path for callers that have no conversation (project analysis,
        compact summary, security check, validator).

        The persistent session belongs to a conversation; these callers own none,
        so a throwaway session runs exactly one turn and is closed. The generic
        one-shot spawn in cli_base no longer covers agy (removed with the
        stream-json migration, 2026-09-05), which would have left these callers
        with a bare error. Emits the same delta/final/error events the other CLI
        providers' one-shot path emits.
        """
        from providers.agy_session import AgyStreamSession
        workspace = cwd or "."
        session = AgyStreamSession(-(int(time.time() * 1000) & 0x7FFFFFFF), cwd=workspace)
        collected = ""
        try:
            async for ev in session.stream(prompt, model=self.binary_name, cwd=workspace):
                kind = ev.get("type")
                if kind == "text":
                    piece = ev.get("content", "")
                    collected += piece
                    yield {"type": "delta", "text": piece}
                elif kind == "response":
                    yield {"type": "final", "text": ev.get("content") or collected}
                elif kind == "error":
                    yield {"type": "error", "content": ev.get("message", "")}
                    return
        finally:
            await session.close()

    @staticmethod
    def _unityai_call_rules(unityai_cli: str, windows: bool = None) -> str:
        """The exact call shapes agy_step_gate allows, per platform.

        agy runs commands in Windows PowerShell 5.1 on Windows (measured). The
        bash heredoc once given here does not parse there; the model then fell
        back to a double-quoted here-string, which expanded $HOME inside the
        file content before unityai saw it (measured: the card showed the
        expanded text). The single-quoted here-string is literal.
        """
        windows = (sys.platform == "win32") if windows is None else windows
        if windows:
            call = f'& "{unityai_cli}"'
            write = (
                "1. CREATE or EDIT a file — pipe the content with a single-quoted\n"
                "   PowerShell here-string (the closing '@ starts its own line). Keep the\n"
                "   first line exactly as shown: without it non-ASCII letters become '?':\n"
                "   run_command:\n"
                f"   {PS_UTF8_PREFIX}\n"
                "   @'\n"
                "   ...full file content here...\n"
                f"   '@ | {call} save-file --path \"<rel/path>\" --content-stdin\n"
                "   Use @' '@ (single quotes) ONLY — @\" \"@ expands $ and corrupts the file.\n")
        else:
            call = f'"{unityai_cli}"' if " " in unityai_cli else unityai_cli
            write = (
                "1. CREATE or EDIT a file — pipe the content via stdin (handles multiline):\n"
                f"   run_command: {call} save-file --path \"<rel/path>\" --content-stdin <<'UNITYAI_EOF'\n"
                "   ...full file content here...\n"
                "   UNITYAI_EOF\n")
        return (
            write
            + "   FORBIDDEN for writing files: python -c, printf, echo, cat, tee, Set-Content,\n"
            "   Out-File or redirection (>). They bypass approval and are refused.\n"
            f"2. DELETE a file:    run_command: {call} delete-file --path \"<rel/path>\"\n"
            "3. SHELL commands (git, npm, mkdir, rm, mv, etc.), one command per call:\n"
            f"   run_command: {call} bash --command \"<shell command>\"\n")

    def _stream_instructions(self) -> str:
        """Keep the existing tool approval bridge guidance on the stdin path."""
        unityai_cli = self._launcher_path("unityai")
        self._ensure_exec(unityai_cli)
        return (
            "IMPORTANT: You MUST respond in Turkish (Türkçe) at all times.\n\n"
            "AVAILABLE MCP TOOLS — call these DIRECTLY when the task needs them:\n"
            "- unityMCP: Unity editor operations (manage_gameobject, manage_scene,\n"
            "  manage_fbx, manage_animation, manage_material, refresh_unity, read_console,\n"
            "  run_tests, find_gameobjects, etc.). Use directly for Unity queries/actions.\n"
            "- meshy: 3D asset generation (meshy_text_to_3d, meshy_image_to_3d, meshy_rig,\n"
            "  meshy_animate, meshy_retexture, meshy_check_balance, etc.). Use directly.\n"
            "  Meshy calls cost credits — state the cost and get user confirmation first.\n"
            "Do NOT route unityMCP/meshy through the unityai CLI — call them as MCP tools.\n\n"
            "RESUMING A LONG TASK: You remember previous turns. If earlier you started a\n"
            "long async job (e.g. meshy_text_to_3d returns a task id and generation takes\n"
            "minutes) and it looks unfinished/interrupted, do NOT restart it — call the\n"
            "status tool (meshy_get_task_status with the task id from your history) and\n"
            "continue from where you left off (download / refine / place in scene).\n\n"
            "EFFICIENCY — answer directly, do NOT flail:\n"
            "- Respond to the user's actual request. Do NOT go on filesystem expeditions.\n"
            "- Do NOT call list_dir / grep_search / view_file / invoke_subagent / schedule\n"
            "  unless the task genuinely requires it. No self-scheduling, no timers, no\n"
            "  probing the .system_generated / brain / transcript folders. Just do the task.\n\n"
            "You have a command-line tool 'unityai' for file WRITES, DELETES and shell.\n"
            "Your own write_to_file/replace_file_content tools are DISABLED on purpose —\n"
            "the ONLY way to create, edit, delete a file or run shell is via run_command\n"
            "calling 'unityai' with its ABSOLUTE PATH:\n"
            f"  {unityai_cli}\n\n"
            "CRITICAL RULES — follow exactly:\n"
            + self._unityai_call_rules(unityai_cli) +
            "4. To READ a file or LIST a directory you MAY use your own view_file / list_dir.\n"
            "5. Run unityai ALONE: nothing before or after it in the same command, no\n"
            "   && ; | or redirection. Values in --path/--command may hold only ASCII\n"
            "   letters, digits, spaces, Turkish letters and - _ . / \\ : , + = @ # ~ * ? [ ] { }.\n"
            "   No quotes inside a value, none of ; $ ` & | < > ^ % ! ( ), and no\n"
            "   typographic quotes or dashes — such a command is refused.\n\n"
            "Every write, delete and shell command MUST go through unityai so the user can\n"
            "approve it in the IDE. SCOPE: Only the current workspace. No unprompted test files.\n\n"
            "REPLY STYLE — match the reply length to the task:\n"
            "- For file WRITE / DELETE / shell actions: reply with ONE short Turkish\n"
            "  sentence stating what you did (e.g. 'TestScripts.cs oluşturuldu.'). NEVER\n"
            "  paste the file's full content/code block — the IDE approval card already\n"
            "  shows the code and diff. NEVER explain approval mechanics ('onayınızı\n"
            "  bekliyor', 'onay verdikten sonra', 'komutu çalıştırdım'). Don't repeat.\n"
            "- For QUESTIONS, reading/analysis, or reports (e.g. 'read the GDD and tell me\n"
            "  the rules', 'check MCP access', 'what does X do'): give a COMPLETE, substantive\n"
            "  Turkish answer — actually report the findings, rules, values, or console output\n"
            "  you gathered. Do NOT collapse it into a passive one-liner like 'öğrenildi',\n"
            "  'incelendi' or 'test edildi'. Be genuinely informative, not terse.\n"
            "- LANGUAGE: ALWAYS reply in the language the user writes in (Turkish user →\n"
            "  Turkish reply), including resumed conversations. Never drift to English.\n\n"
        )

    @staticmethod
    def _hook_argv(state_path: str) -> list:
        """argv of `backend agy-hook`, dev and frozen (same split as bridge_argv)."""
        if getattr(sys, "frozen", False):
            return [sys.executable, "agy-hook", "--state", state_path]
        main_py = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        return [sys.executable, main_py, "agy-hook", "--state", state_path]

    @staticmethod
    def _step_gate_command() -> str:
        """Writes the shim that runs `backend agy-hook` and returns the hook
        command. Rewritten on every call, so an edited copy cannot linger.

        A shim because agy re-escapes quotes in a hook command (measured), so
        the backend's own path, which may hold spaces (Program Files), cannot
        be written into hooks.json directly; inside the shim quoting works.
        """
        gate_dir = _gate_dir()
        os.makedirs(gate_dir, exist_ok=True)
        argv = AgyProvider._hook_argv(os.path.join(gate_dir, "step-gate.json"))
        if sys.platform == "win32":
            path = os.path.join(gate_dir, "step-gate.cmd")
            line = " ".join('"%s"' % a.replace("%", "%%") for a in argv)
            body = "@echo off\r\n" + line + "\r\n"
        else:
            import shlex
            path = os.path.join(gate_dir, "step-gate.sh")
            body = "#!/bin/sh\nexec " + " ".join(shlex.quote(a) for a in argv) + "\n"
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        if sys.platform != "win32":
            os.chmod(path, 0o700)
        command = _short_path(path) if sys.platform == "win32" else path
        if " " in command:
            logger.warning("[agy] step gate path has a space (%s); the hook will fail "
                           "closed and the model sees a shell error, not the reason", command)
        return command

    def _write_step_gate(self, workspace: str, step_mode: bool) -> bool:
        """Installs Gamachine's hook entry in the workspace `.agents/hooks.json`
        in EVERY mode, keeping every other hook the user has there, and writes
        the state file the hook reads on every call. Returns True; anything
        short of a verified gate raises AgyStepGateError, and the caller must
        not spawn.

        Why every mode: agy reads hooks.json only when it starts, and it always
        runs under toolPermission always-proceed. A process spawned without the
        entry could never be tightened: a flip to step during its turn, or
        between this call and the spawn, left step mode with an agy whose
        writes and shell commands ran with no card (verification round, 26 Sep
        2026). With the entry installed, the mode lives only in the state file:
        agy_session rewrites it on every flip, and the flip bites on the next
        tool call.

        Why auto mode refuses too: spawning ungated in auto would be safe only
        if a later flip to step could still reach that process, and nothing
        can (it has no hook to consult; killing it on the flip races its next
        tool call). The cost is that a broken hooks.json blocks agy in auto as
        well; the message says how to fix it.

        The hook's state file is written first: a hook that cannot read it
        denies. A malformed hooks.json is refused, not backed up and rewritten:
        agy's own parser may accept what json.load rejects (unmeasured), so the
        user's hooks could be live, and replacing the file would switch them
        off even with a backup kept.
        """
        from .workspace_config import ensure_gitignored, guvenli_config_yaz
        path = os.path.join(os.path.realpath(workspace), *STEP_GATE_HOOKS_FILE.split("/"))
        state_path = gate_state_path()
        try:
            write_gate_state(auto=not step_mode)
        except OSError as e:
            # A stale "auto" state file would make an installed hook allow everything.
            logger.error("[agy] gate state not written (%s)", e)
            raise AgyStepGateError(_gate_write_failed(state_path, e, step_mode)) from e
        existed = os.path.exists(path)
        hooks = {}
        if existed:
            try:
                with open(path, encoding="utf-8-sig") as f:
                    hooks = json.load(f)
                if not isinstance(hooks, dict):
                    raise ValueError("top level is not an object")
            except (OSError, ValueError) as e:
                logger.error("[agy] %s is unreadable (%s); left untouched", path, e)
                raise AgyStepGateError(_gate_hooks_unreadable(path, e, step_mode)) from e
        try:
            command = self._step_gate_command()
        except OSError as e:
            raise AgyStepGateError(_gate_write_failed(_gate_dir(), e, step_mode)) from e
        hooks[STEP_GATE_KEY] = {"PreToolUse": [
            {"matcher": tool, "hooks": [{"type": "command", "command": command, "timeout": 10}]}
            for tool in STEP_GATE_TOOLS
        ]}
        if not guvenli_config_yaz(workspace, STEP_GATE_HOOKS_FILE, json.dumps(hooks, indent=2)):
            logger.error("[agy] %s could not be written; gate not installed", path)
            raise AgyStepGateError(_gate_write_failed(path, "güvenli yazma reddedildi", step_mode))
        if not existed:
            # Only a file we created: it holds an absolute path of this machine.
            ensure_gitignored(workspace, [STEP_GATE_HOOKS_FILE])
        problem = self._step_gate_problem(path, state_path, command, step_mode)
        if problem:
            logger.error("[agy] gate not verified: %s", problem)
            raise AgyStepGateError(_gate_not_verified(path, problem, step_mode))
        return True

    def _step_gate_problem(self, path: str, state_path: str, command: str,
                           step_mode: bool = True) -> Optional[str]:
        """What agy and the hook will read, re-read from disk; None when it is our gate."""
        want_mode = "step" if step_mode else "auto"
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
            if state.get("mode") != want_mode or state.get("launcher") != self._launcher_path("unityai"):
                return f"{state_path} beklenen modu ({want_mode}) göstermiyor"
            with open(path, encoding="utf-8-sig") as f:
                entries = json.load(f)[STEP_GATE_KEY]["PreToolUse"]
            want = [{"matcher": tool, "hooks": [{"type": "command", "command": command, "timeout": 10}]}
                    for tool in STEP_GATE_TOOLS]
            if entries != want:
                return f"{path} içindeki {STEP_GATE_KEY} kaydı beklenenden farklı"
            if not os.path.isfile(command):
                return f"{command} bulunamadı"
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            return f"{type(e).__name__}: {e}"
        return None

    def _set_agy_model(self, agy_model_name: str, workspace: str = ""):
        """~/.gemini/antigravity-cli/settings.json ve global ~/.gemini/settings.json
        içindeki modeli, trustedWorkspaces, toolPermission ve disabledTools'u günceller.

        Model seçimi SADECE buradan yapılır (komut satırında --model YOK — o derail
        tetikliyor, bkz. _build_cmd). Her çağrıda "model" key'i GÜNCEL değere yazılır;
        bu sayede eski stale değer (örn. önceki oturumdan "Gemini 3.5 Flash (High)")
        üzerine yazılır — canlı doğrulandı (2026-07-24): stale key kalınca model
        kendini yanlış tanıtıyordu, güncel display-name yazılınca düzeliyor."""
        # 1. Lokal antigravity-cli settings.json, 2. global ~/.gemini/settings.json.
        # toolPermission always-proceed: --dangerously-skip-permissions flag'i YERİNE
        # (canlı doğrulandı: geçerli değer, flag'siz auto-approve → skill-derail'i tetiklemez).
        for path, default in (
            ("~/.gemini/antigravity-cli/settings.json", {"colorScheme": "dark", "trustedWorkspaces": []}),
            ("~/.gemini/settings.json", {}),
        ):
            settings_path = os.path.expanduser(path)
            settings = _read_json_config(settings_path, default)
            if settings is None:
                continue
            trusted = settings.get("trustedWorkspaces", [])
            if workspace and not isinstance(trusted, list):
                logger.warning("[agy] %s trustedWorkspaces is not a list; left untouched", settings_path)
                continue
            settings["model"] = agy_model_name
            settings["toolPermission"] = "always-proceed"
            settings["disabledTools"] = self._AGY_DISABLED_TOOLS
            if workspace:
                if workspace not in trusted:
                    trusted.append(workspace)
                settings["trustedWorkspaces"] = trusted
            _write_json_config(settings_path, settings)

        logger.info(f"[CLIProvider] agy model → {agy_model_name}, trusted → {workspace}")

    def _write_cli_env(self, launcher: str, workspace: str, backend_url: str):
        """Backend/.unityai_cli.env yazar — 'unityai' wrapper bu dosyayı source eder.
        launcher = .../Backend/run_mcp_server.sh → backend_dir = .../Backend."""
        backend_dir = os.path.dirname(launcher)
        env_path = os.path.join(backend_dir, ".unityai_cli.env")
        lines = [
            f"UNITYAI_URL={backend_url}",
            f"WORKSPACE={workspace}",
        ]
        # LOCAL_APP_TOKEN bu env dosyasına yazılmıyor; 0600 dosyadan okunuyor.
        try:
            with open(env_path, "w") as f:
                f.write("\n".join(lines) + "\n")
            logger.info(f"[CLIProvider] unityai CLI env yazıldı: {env_path}")
        except Exception as e:
            logger.warning(f"[CLIProvider] unityai CLI env yazılamadı: {e}")

    def _register_mcp(self, launcher: str, workspace: str, backend_url: str):
        """agy config dosyalarını günceller.

        ÖNEMLİ: agy --print modu HİÇBİR MCP server'ını yüklemez (stdio da HTTP da)
        — doğrudan test edildi. Bu yüzden agy mcp__unityai__* araçlarını GÖREMEZ.
        Bunun yerine agy, run_command built-in aracıyla 'unityai' CLI'ını çağırır
        (bkz. cli_base mcp_hint). Buradaki MCP kaydı sadece agy interaktif modda
        kullanılırsa diye tutulur; --print akışında etkisizdir. Asıl iş .unityai_cli.env
        + disabledTools (agy'nin gerçek write araçlarını kapatma) ile yapılır.
        """
        # Bu env sözlüğü ~/.gemini/settings.json'a yazılıyor — token oraya
        # girmiyor: dosya paylaşımlı (Jarvan da aynı dosyayı kullanıyor) ve
        # sırrın config'e yayılması denetimin C grubu bulgusuydu.
        env = {"UNITYAI_URL": backend_url, "WORKSPACE": workspace, **AGY_CHILD_SECRET_BLANKS}

        # 0. unityai CLI env dosyası — agy run_command env'i propagate etmese bile
        #    CLI doğru backend'e/token'a/workspace'e bağlanmayı garanti eder.
        self._write_cli_env(launcher, workspace, backend_url)
        unityai_entry = {
            "command": launcher, "args": ["--workspace", workspace],
            "env": env, "trust": True,
        }

        # Each file is read with _read_json_config: one that exists but cannot
        # be parsed is skipped and left as it is, never rebuilt from scratch.
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        # K3: `serverUrl` + `headers` (düz metin `X-API-Key`) yerine stdio
        # köprüsü. Bu dosya `~/.gemini`'de duruyor ve başka bir asistanla
        # PAYLAŞILIYOR (bkz. [[jarvan-asistan]]) — yani sırrın buraya
        # yazılması, ürünün kendi izole etmediği bir yüzeye sır koymaktı.
        # Köprü sırrı token dosyasından kendi okuyor.
        #
        # Şekil `unityai_entry` ile AYNI (`command`/`args`/`env`/`trust`) ve o
        # kayıt bu dosyada çalışıyor. Aynı biçim opencode ve copilot'ta canlı
        # doğrulandı (1 Ağu 2026). None → kayıt silinir.
        unity_mcp_url = unity_mcp_manager.mcp_url(host="127.0.0.1")
        ours = {"unityai": dict(unityai_entry)}
        if unity_mcp_url:
            from .codex_unitymcp_bridge import bridge_argv
            _argv = bridge_argv()
            ours["unityMCP"] = {
                "command": _argv[0], "args": _argv[1:],
                "env": {"UNITY_MCP_URL": unity_mcp_url, **AGY_CHILD_SECRET_BLANKS},
                "trust": True,
            }

        def servers_of(cfg: Optional[dict], path: str) -> Optional[dict]:
            if cfg is None:
                return None
            servers = cfg.setdefault("mcpServers", {})
            if not isinstance(servers, dict):
                logger.warning("[agy] %s mcpServers is not an object; left untouched", path)
                return None
            return servers

        # 1. ~/.gemini/antigravity-cli/mcp_config.json
        config_path = os.path.expanduser("~/.gemini/antigravity-cli/mcp_config.json")
        config = _read_json_config(config_path)
        config_servers = servers_of(config, config_path)
        if config_servers is not None:
            config_servers.pop("antigravity", None)
            config_servers.update(ours)
            if not unity_mcp_url:
                config_servers.pop("unityMCP", None)
            # disabledTools mcp_config.json'da OLMAMALI — agy geçersiz key görünce tüm dosyayı
            # yoksayarak MCP server'ları başlatmaz. disabledTools sadece settings.json'da olmalı.
            config.pop("disabledTools", None)
            if _write_json_config(config_path, config):
                logger.info(f"[CLIProvider] agy mcp_config.json güncellendi: {backend_url} → {workspace}")

        # 1b. GÖÇ SONRASI yol: ~/.gemini/config/mcp_config.json
        # KRİTİK (2026-07-14 canlı doğrulandı, agy 1.1.2): agy artık MCP server'larını
        # BU göç-sonrası yoldan okuyor (~/.gemini/config/.migrated marker'ı mevcut; CLI
        # issue #60). Eski antigravity-cli/mcp_config.json ARTIK OKUNMUYOR → sadece oraya
        # yazınca agy hiçbir MCP tool'u görmüyordu (yalnız built-in default_api:*). Aynı
        # config'i buraya da yazınca --print modunda unityMCP + meshy + unityai tool'ları
        # görünüyor (canlı: meshy_check_balance çağrısı PASS). Migrated dosyada zaten olan
        # (IDE'nin eklediği meshy/playwright gibi) server'ları koru; taze unityai/unityMCP öncelikli.
        migrated_path = os.path.expanduser("~/.gemini/config/mcp_config.json")
        migrated_cfg = _read_json_config(migrated_path)
        merged_servers = servers_of(migrated_cfg, migrated_path)
        if merged_servers is not None:
            # taze unityai/unityMCP kazanır; eski dosya okunamadıysa yalnız bizimkiler
            merged_servers.update(config_servers if config_servers is not None else ours)
            merged_servers.pop("antigravity", None)
            if not unity_mcp_url:
                # The merge above only adds; without this an old entry (e.g.
                # the pre-K3 keyless http one) survives here while the server
                # is off, and agy tries it on every start.
                merged_servers.pop("unityMCP", None)
            migrated_cfg.pop("disabledTools", None)  # geçersiz key → agy tüm dosyayı yoksayar
            if _write_json_config(migrated_path, migrated_cfg):
                logger.info(f"[CLIProvider] agy MIGRATED mcp_config.json yazıldı ({len(merged_servers)} server): {migrated_path}")

        # 2. ~/.gemini/antigravity-cli/settings.json, 3. global ~/.gemini/settings.json.
        # No unityMCP here, and an old one is removed. agy does not read
        # mcpServers from settings.json at all (measured 25 Sep 2026, agy
        # 1.2.8: a server declared only here was never started), and the old
        # keyless entry only answered 401. Adding the X-API-Key header would
        # work for agy's own http client (measured), but would put the secret
        # back into a ~/.gemini file shared with another assistant (K3). The
        # stdio bridge in mcp_config.json above is the one working entry.
        for path, default in (
            ("~/.gemini/antigravity-cli/settings.json", {"colorScheme": "dark", "trustedWorkspaces": []}),
            ("~/.gemini/settings.json", {}),
        ):
            settings_path = os.path.expanduser(path)
            settings = _read_json_config(settings_path, default)
            servers = servers_of(settings, settings_path)
            if servers is None:
                continue
            servers.pop("antigravity", None)
            servers["unityai"] = dict(unityai_entry)
            servers.pop("unityMCP", None)
            # --dangerously-skip-permissions flag'i YERİNE (canlı doğrulandı: geçerli
            # değer, flag'siz auto-approve → skill-derail'i tetiklemez)
            settings["toolPermission"] = "always-proceed"
            settings["disabledTools"] = self._AGY_DISABLED_TOOLS
            if _write_json_config(settings_path, settings):
                logger.info(f"[CLIProvider] agy {settings_path} güncellendi: {backend_url} → {workspace}")
