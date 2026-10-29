from secret_redaction import redact_secrets
import os
import json
import logging
from .cli_base import BaseCLIProvider
from .oneshot_cli import resolve_cursor_cmd, split_model_id
from .unityai_tool_text import mail_and_fallback, on_unityai_server
from .workspace_config import ensure_gitignored, guvenli_config_yaz, read_user_json_config

logger = logging.getLogger(__name__)




class CursorProvider(BaseCLIProvider):
    """Cursor CLI (`agent`) — print-mode + resmi --resume ile tur bazlı sürülür.

    Çıktı formatı stream-json, Claude Code'un stream-json'ına çok yakın
    (type: system/user/assistant/thinking/result) — cli_base parser'ı
    çoğunu zaten anlıyor; cursor'a özgü eklemeler cli_base'de işlenir.
    Yazma izni İSTEMEZ (--force verilmez): dosya/shell işleri prompt'la
    unityai MCP'ye yönlendirilir → onay kartı bizim UI'da çıkar.
    """
    _policy_refusal_message = (
        "⚠️ Güvenlik ayarları çalışma klasörüne yazılamadığı için işlem başlatılmadı. "
        "Klasördeki .cursor/cli.json dosyasını kontrol edin.")

    # agent_runner her tur spawn öncesi doldurur:
    resume_session_id = None   # önceki turun chatId'si (--resume)

    def _build_cmd(self, prompt: str, thinking_level: str = "medium", workspace: str = None) -> list:
        base = resolve_cursor_cmd()
        if not base:
            # cli_base._cli_installed bunu yakalayıp temiz hata verir
            return ["cursor-agent-not-found", prompt]

        _, model = split_model_id(self.binary_name)

        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        unity_running = unity_mcp_manager.is_running()
        unity_section = ""
        if unity_running:
            unity_section = (
                "\nUNITY EDITOR — unityMCP tools (use for ALL Unity scene/UI operations):\n"
                "- Scene hierarchy:            unityMCP manage_scene action=get_hierarchy\n"
                "- Create/modify GameObjects:  unityMCP manage_gameobject\n"
                "- Components:                 unityMCP manage_components\n"
                "- UI (Canvas/Button/Text):    unityMCP manage_ui\n"
                "- Console logs:               unityMCP read_console\n"
                "RULE: NEVER read .unity/.prefab/.asset files to answer Unity questions —\n"
                "      always query the live editor via unityMCP.\n"
            )

        mcp_hint = (
            "IMPORTANT — follow exactly:\n"
            + unity_section +
            "\nFILE & TERMINAL operations — use ONLY the tools of the unityai MCP server\n"
            "(your own write/shell tools are sandboxed and will be denied):\n"
            "- Create/edit files:  save_file (unityai MCP server)\n"
            "- Delete files:       delete_file (unityai MCP server)\n"
            "- Shell commands:     run_terminal_command (unityai MCP server)\n"
            "- Read file:          read_file  |  List dir: list_directory (unityai MCP server)\n"
            + mail_and_fallback(on_unityai_server) +
            "Be concise. Never claim you cannot do something — use the tools.\n\n"
        )

        cmd = [
            *base,
            "-p",
            "--output-format", "stream-json",
            "--stream-partial-output",
            "--trust",
            "--approve-mcps",
            # --force ŞART: headless modda MCP tool çağrıları onay bekleyip
            # reddediliyor (canlı doğrulandı). Native Write/Shell ise
            # .cursor/cli.json deny-list'i ile kapalı ("unless explicitly
            # denied") → tüm yazma/shell unityai MCP onayından geçer.
            "--force",
        ]
        # 'auto' da AÇIKÇA geçirilir: bayraksız çağrıda CLI kayıtlı varsayılan
        # (adlı) modeli dener ve Free planda "Named models unavailable" ile ölür
        # (canlı doğrulandı 2026-07-13).
        cmd += ["--model", model or "auto"]
        if self.resume_session_id:
            cmd += ["--resume", self.resume_session_id]
        # Prompt SON pozisyonel arg (cli_base'in cmd/c stdin-pop fallback'iyle uyumlu;
        # doğrudan node spawn'da çok satırlı argv zaten güvenli).
        from agent_guide import provider_guide
        cmd.append(mcp_hint + provider_guide(
            self, project_open=bool(workspace), unity_running=unity_running) + prompt)
        return cmd

    def _write_cli_policy(self, workspace: str) -> bool:
        """Write the Write/Shell deny-list into .cursor/cli.json; False if it is not in place."""
        # İzin politikası: --force ile birlikte native Write/Shell'i deny-list'le
        # kapat (şema hem allow hem deny İSTİYOR; BOM'suz yazılmalı).
        cli_existing = read_user_json_config(workspace, ".cursor/cli.json")
        if cli_existing is None:
            logger.error("[CursorProvider] Cannot read .cursor/cli.json safely; "
                         "the permission policy was not written and the turn is refused.")
            return False

        def _strings(value) -> list:
            return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []

        perms = cli_existing.get("permissions")
        if not isinstance(perms, dict):
            perms = {}
        deny = _strings(perms.get("deny"))
        for rule in ("Write(**)", "Shell(**)"):
            if rule not in deny:
                deny.append(rule)
        cli_existing["permissions"] = {"allow": _strings(perms.get("allow")), "deny": deny}
        # ⚠️ Bu dosya K4'ün EN SİNSİ vakası: Cursor'un `Write`/`Shell`
        # deny-list'ini taşıyor. Yönlendirilirse görünen sonuç bir dosyanın
        # ezilmesi DEĞİL, politikanın sessizce kaybı olur — kısıt hedef
        # workspace'e hiç uygulanmaz ve kimse fark etmez.
        if not guvenli_config_yaz(workspace, ".cursor/cli.json",
                                  json.dumps(cli_existing, indent=2)):
            logger.error("[CursorProvider] .cursor/cli.json güvenli yazılamadı; "
                         "izin politikası UYGULANMADI.")
            return False
        logger.info("[CursorProvider] .cursor/cli.json izin politikası yazıldı.")
        ensure_gitignored(workspace, [".cursor/cli.json"])
        return True

    def _register_mcp(self, launcher: str, workspace: str, backend_url: str):
        """Cursor, workspace'teki .cursor/mcp.json'ı otomatik yükler."""
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

        try:
            cfg_dir = os.path.join(workspace, ".cursor")
            os.makedirs(cfg_dir, exist_ok=True)

            unityai_env = {"UNITYAI_URL": backend_url, "WORKSPACE": workspace}
            # Token config dosyasına yazılmıyor — 0600 dosyadan okunuyor
            # (bkz. local_token_file). Bu dosya model tarafından okunabilir.

            config = {
                "mcpServers": {
                    "unityai": {
                        "command": launcher,
                        "args": ["--workspace", workspace],
                        "env": unityai_env,
                    }
                }
            }
            # K3: `headers` içinde düz metin `X-API-Key` yerine stdio köprüsü —
            # sır bu dosyaya HİÇ girmiyor.
            #
            # ⚠️ CURSOR'DA ÖLÇÜLMEDİ: `cursor-agent` bu makinede kurulu değil
            # (ölçüldü 1 Ağu 2026, `command -v`). Şema yine de sağlam bir
            # temele dayanıyor: HEMEN YUKARIDAKİ `unityai` kaydı aynı
            # `command`/`args`/`env` biçiminde ve cursor onu okuyor. Aynı biçim
            # opencode ve copilot'ta canlı doğrulandı. Cursor bu kaydı yine de
            # yok sayarsa sonuç, unityMCP'nin görünmemesi olur — sessiz bir
            # sızıntı değil, görülebilir bir eksik. Kurulunca ölçülmeli.
            unity_mcp_url = unity_mcp_manager.mcp_url()
            if unity_mcp_url:
                from .codex_unitymcp_bridge import bridge_argv
                _argv = bridge_argv()
                config["mcpServers"]["unityMCP"] = {
                    "command": _argv[0],
                    "args": _argv[1:],
                    "env": {"UNITY_MCP_URL": unity_mcp_url},
                }

            # Kullanıcının kendi .cursor/mcp.json'ı varsa bizim server'ları üstüne
            # ekle, diğer kayıtlarına dokunma.
            # The deny policy goes first: whatever happens to the MCP entry, the CLI
            # must never start with native Write/Shell open.
            if not self._write_cli_policy(workspace):
                return False

            existing = read_user_json_config(workspace, ".cursor/mcp.json")
            if existing is None:
                logger.error("[CursorProvider] Cannot preserve .cursor/mcp.json; "
                             "MCP config was not written.")
                return True
            user_servers = existing.get("mcpServers")
            if not isinstance(user_servers, dict):
                user_servers = {}
            merged = {**existing, "mcpServers": {**user_servers, **config["mcpServers"]}}
            # Unity MCP kapalıysa VEYA sır bizde yoksa bayat kaydı temizle: ikinci
            # durumda eski URL artık 404 alır, CLI bağlanamayan MCP'de takılır.
            if not unity_mcp_url:
                merged["mcpServers"].pop("unityMCP", None)
            self._apply_computer_servers(merged["mcpServers"], lambda e: e, chat_id=False)

            # ⚠️ Düz `open` DEĞİL: bu dosya kullanıcının projesinde ve yolu
            # yönlendirilmiş olabilir (K4, iki vektör de ayrıcalıksız ölçüldü).
            # `sir_tasiyor` artık FALSE: K3'ten sonra dosya BİZİM sırrımızı
            # taşımıyor, dolayısıyla sertleştirme başarısızlığının unityMCP'yi
            # düşürmesi sebepsiz işlev kaybı olurdu. Sertleştirme yine de her
            # zaman deneniyor — dosya kullanıcının KENDİ üçüncü-parti MCP
            # kayıtlarını (kendi başlıklarıyla) koruyor.
            if not guvenli_config_yaz(workspace, ".cursor/mcp.json",
                                      json.dumps(merged, indent=2)):
                logger.error("[CursorProvider] .cursor/mcp.json güvenli "
                             "yazılamadı; MCP kaydı UYGULANMADI.")
                return True
            logger.info("[CursorProvider] .cursor/mcp.json yazıldı (unityMCP stdio köprüsü).")
            # Dosyayı yazan nokta girdisini de yazar: sır taşımasa da
            # kullanıcının deposunda duruyor ve 29 Tem ölçümünde gerçek bir
            # projede İZLENİYORDU.
            ensure_gitignored(workspace, [".cursor/mcp.json"])

            return True
        except Exception as e:
            logger.warning(f"[CursorProvider] MCP kaydı yapılamadı: {redact_secrets(str(e))}")
            return False
