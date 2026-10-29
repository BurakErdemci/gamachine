import os
import json
import logging
import tempfile
from .cli_base import BaseCLIProvider
from .unityai_tool_text import mail_and_fallback, mcp_double_underscore

logger = logging.getLogger(__name__)


class KimiProvider(BaseCLIProvider):
    """Moonshot Kimi Code CLI (subscription). Print modunda stream-json ile sürülür.

    GÜVENLİK: `kimi --print` otomatik `--yolo` (tüm tool onay bypass) açar. Ama
    `[[permission.rules]]` `deny` kuralları YOLO tarafından EZİLMEZ (CLI önce tüm
    deny'leri değerlendirir — kaynakla doğrulandı). Bu yüzden built-in Write/Edit/Bash
    DENY edilir → model dosya/shell işlemlerini yalnız `mcp__unityai__*` ile yapar →
    onay kartı app'te çıkar (agy'deki _AGY_DISABLED_TOOLS mantığının eşdeğeri).

    NOT (canlı doğrulanmadı — abonelik yok): deny kuralının fiilen Write'ı blokladığı,
    stream-json event şemasının cli_base parser'ıyla eşleştiği ve `--mcp-config-file`'ın
    .mcp.json shape'ini okuduğu bir Kimi hesabıyla test edilmeli.
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
    _KIMI_PERMISSION_BLOCK = (
        "\n" + _KIMI_MANAGED_MARKER + "\n"
        "# Built-in yazma/shell araçları DENY (deny mutlak — --print/--yolo bunu ezmez).\n"
        "# Model dosya/shell işlemlerini yalnız mcp__unityai__* ile yapar → app onay kartı.\n"
        '[[permission.rules]]\ndecision = "deny"\npattern = "Write"\n\n'
        '[[permission.rules]]\ndecision = "deny"\npattern = "Edit"\n\n'
        '[[permission.rules]]\ndecision = "deny"\npattern = "Bash"\n\n'
        '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__unityai__*"\n\n'
        '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__gamachineComputer__*"\n\n'
        '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__gamachineBrowser__*"\n'
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
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config-", suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(existing[:start] + current + existing[end:])
                os.chmod(tmp, os.stat(path).st_mode & 0o7777)
                os.replace(tmp, path)
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
        if self._KIMI_LEGACY_MARKER in existing:
            return  # zaten yazılı — idempotent
        try:
            # Created here only when absent; keep a new config.toml private.
            with open(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a", encoding="utf-8") as f:
                f.write(self._KIMI_PERMISSION_BLOCK)
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
        workspace = workspace or os.getcwd()

        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        unity_running = unity_mcp_manager.is_running()
        # unityai launcher'ın exec bitini garantile (paket kopyalama düşürebilir)
        self._ensure_exec(self._launcher_path("unityai"))

        hint = self._build_hint(unity_running)
        from agent_guide import provider_guide
        hint += provider_guide(self, project_open=bool(workspace), unity_running=unity_running)
        # binary_name = kimi model slug'ı (kimi-k3 / kimi-k2.7-code) → -m ile geçer.
        # K3 düşünmesi always-on; kimi CLI ayrı effort knob'u sunmaz → thinking_level yok sayılır.
        cmd = [
            "kimi", "--print",
            "--output-format", "stream-json", "--verbose",
            "-w", workspace,
        ]
        if self._mcp_cfg_path:
            cmd += ["--mcp-config-file", self._mcp_cfg_path]
        else:
            logger.warning("[KimiProvider] per-turn MCP config yok; "
                           "--mcp-config-file verilmedi.")
        cmd += ["-m", self.binary_name, "-p", hint + prompt]
        return cmd
