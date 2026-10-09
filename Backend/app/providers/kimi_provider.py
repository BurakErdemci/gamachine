import os
import logging
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
        '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__unityai__*"\n'
        + _KIMI_MANAGED_END + "\n"
    )

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
        if (self._KIMI_MANAGED_MARKER in existing
                or self._KIMI_LEGACY_MARKER in existing):
            return  # zaten yazılı — idempotent
        try:
            with open(path, "a", encoding="utf-8") as f:
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
        mcp_json = os.path.join(workspace, ".mcp.json")

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
            "--mcp-config-file", mcp_json,
            "-m", self.binary_name,
            "-p", hint + prompt,
        ]
        return cmd
