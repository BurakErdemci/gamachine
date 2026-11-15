import os
import sys
import json
import shutil
import logging
import tempfile
from .cli_base import BaseCLIProvider
from .families import kimi_cli_model
from .unityai_tool_text import mail_and_fallback, mcp_double_underscore

logger = logging.getLogger(__name__)


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

    _mcp_cfg_path = None  # this turn's temp MCP config file

    async def analyze_code(self, *args, **kwargs):
        try:
            async for event in super().analyze_code(*args, **kwargs):
                yield event
        finally:
            self._drop_mcp_cfg()

    def _drop_mcp_cfg(self):
        """Deletes this turn's temp MCP config once kimi has exited."""
        path, self._mcp_cfg_path = self._mcp_cfg_path, None
        if path:
            try:
                os.remove(path)
            except OSError as e:
                logger.warning("[KimiProvider] %s silinemedi: %s", path, e)

    def _register_mcp(self, launcher: str, workspace: str, backend_url: str):
        """Writes a per-turn file with only the product servers. The workspace
        .mcp.json is the project's own and must never be handed to kimi: its
        servers would start without a prompt."""
        self._drop_mcp_cfg()
        path = None
        try:
            servers = self._product_mcp_servers_stdio(launcher, workspace, backend_url)
            self._apply_computer_servers(servers, lambda e: e, chat_id=True)
            fd, path = tempfile.mkstemp(prefix="uai_kimi_mcp_", suffix=".json")
            os.close(fd)
            from .workspace_config import harden_config_file
            if not harden_config_file(path):
                logger.warning(
                    "[KimiProvider] %s izinleri kısıtlanamadı; dosya dizin "
                    "izinlerini miras alıyor.", path)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": servers}, f, indent=2)
            self._mcp_cfg_path = path
        except Exception as e:
            self._mcp_cfg_path = None
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
            logger.warning(f"[KimiProvider] MCP config yazılamadı: {e}")

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

    def _build_hint(self, unity_running: bool) -> str:
        unity_section = ""
        if unity_running:
            unity_section = (
                "\nUNITY EDITOR (unityMCP MCP tools — call DIRECTLY for ALL Unity work):\n"
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
        if self._mcp_cfg_path:
            # kimi 2.5.3 has no MCP file flag (`--mcp-config-file` is an unknown
            # option, rc 1): it reads only $KIMI_CODE_HOME/mcp.json and, in a
            # trusted folder, the project's .mcp.json / .kimi-code/mcp.json.
            # How the per-turn file reaches it is an open decision.
            logger.warning("[KimiProvider] per-turn MCP config is not passed to kimi "
                           "(the CLI has no flag for it).")
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
        "no model configured", "login required", "/login", "no provider configured",
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
            return None  # an answer came: generic exit-code handling
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
