import os
import json
import logging
import tempfile
from .cli_base import BaseCLIProvider, conversation_env
from .oneshot_cli import resolve_copilot_cmd, split_model_id

logger = logging.getLogger(__name__)


class CopilotProvider(BaseCLIProvider):
    """GitHub Copilot CLI — -p (programatik mod) + resmi session resume.

    • İlk tur: --session-id=<bizim ürettiğimiz uuid> (UUID'i BİZ seçeriz,
      yakalama derdi yok). Sonraki turlar: --resume=<uuid>.
    • Çıktı: --output-format json → JSONL event akışı
      (assistant.message_delta / assistant.reasoning_delta / assistant.message
      / result) — cli_base'de işlenir.
    • İzinler: built-in write/shell REDDEDİLİR, unityai + unityMCP araçlarına
      izin verilir → tüm dosya/shell işleri onaylı unityai MCP'den geçer.
    • MCP: --additional-mcp-config @<dosya> ile SESSION-BAZLI enjekte edilir
      (global ~/.copilot config'ine dokunmayız).
    """

    resume_session_id = None   # önceki turların uuid'i (--resume)
    # CANLI ÖLÇÜLDÜ 2026-08-01: bayraksız çağrıda borulanmış stdin prompt olarak
    # okunuyor. `-p -` YOLU ÇÜRÜDÜ — aşağıdaki gerekçeye bak.
    prompt_via_stdin = True

    fresh_session_id = None    # ilk tur için bizim ürettiğimiz uuid (--session-id)
    _mcp_cfg_path = None       # bu tur için yazılan geçici MCP config dosyası

    async def analyze_code(self, *args, **kwargs):
        try:
            async for event in super().analyze_code(*args, **kwargs):
                yield event
        finally:
            self._drop_mcp_cfg()

    def _drop_mcp_cfg(self):
        """Deletes this turn's temp MCP config once copilot has exited; one
        file per turn otherwise piles up in the temp dir."""
        path, self._mcp_cfg_path = self._mcp_cfg_path, None
        if path:
            try:
                os.remove(path)
            except OSError as e:
                logger.warning("[CopilotProvider] %s silinemedi: %s", path, e)

    def _build_cmd(self, prompt: str, thinking_level: str = "medium", workspace: str = None) -> list:
        base = resolve_copilot_cmd()
        if not base:
            return ["copilot-not-found", prompt]

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
            "- Respond in Turkish (Türkçe).\n"
            + unity_section +
            "\nFILE & TERMINAL operations — your built-in write and shell tools are\n"
            "DENIED by policy. Use ONLY the unityai MCP tools:\n"
            "- Create/edit files:  unityai save_file\n"
            "- Delete files:       unityai delete_file\n"
            "- Shell commands:     unityai run_terminal_command\n"
            "- Read file:          unityai read_file  |  List dir: unityai list_directory\n"
            "Be concise. Never claim you cannot do something — use the tools.\n\n"
        )

        cmd = [
            *base,
            "--output-format", "json",
            "--no-color",
            "--no-ask-user",
            "--no-auto-update",
            "--deny-tool", "write",
            "--deny-tool", "shell",
            "--allow-tool", "unityai",
        ]
        if unity_running:
            cmd += ["--allow-tool", "unityMCP"]
        cmd += ["--model", model or "auto"]
        if self.resume_session_id:
            cmd += [f"--resume={self.resume_session_id}"]
        elif self.fresh_session_id:
            cmd += [f"--session-id={self.fresh_session_id}"]
        if self._mcp_cfg_path:
            cmd += ["--additional-mcp-config", f"@{self._mcp_cfg_path}"]
        # Effort: kayıtçıdan (effort_caps) — v1.0.60+ --effort gerçek bir flag.
        # 'auto' modeli effort kabul etmez (canlı doğrulanmış hata) → kayıtçı zaten
        # copilot-auto için boş döner; desteklenmeyen seviye/model'de flag hiç eklenmez.
        try:
            from .effort_caps import map_effort
            _flags = map_effort("subscription", f"copilot-{model or 'auto'}",
                                getattr(self, "_effort_level", "auto")).get("cli_flags")
            if _flags:
                cmd += _flags
        except Exception:
            pass
        if self.prompt_via_stdin:
            # ⚠️ `-p` BAYRAĞI DA DÜŞÜYOR — diğer üçünden farkı bu. Ölçüldü
            # 2026-08-01: `-p -` stdin anlamına GELMİYOR, copilot "-" dizesini
            # düz metin prompt sanıp ona cevap verdi (kredi de harcadı).
            # Bayraksız + borulanmış stdin ise tek seferlik koşup cevabı bastı.
            self._stdin_payload = prompt
        else:
            cmd += ["-p", prompt]
        return cmd

    def _register_mcp(self, launcher: str, workspace: str, backend_url: str):
        """Session-bazlı MCP config dosyası yazar (--additional-mcp-config @path)."""
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager

        self._drop_mcp_cfg()
        path = None
        try:
            # The file is this turn's alone, so it can name the chat too.
            # copilot also passes its own env to these servers (measured), and
            # an entry's env wins, so both carry the same value or neither.
            owner_env = conversation_env(getattr(self, "_conversation_id", None))
            unityai_env = {"UNITYAI_URL": backend_url, "WORKSPACE": workspace, **owner_env}
            # Token config dosyasına yazılmıyor — 0600 dosyadan okunuyor
            # (bkz. local_token_file). Bu dosya model tarafından okunabilir.

            servers = {
                "unityai": {
                    "type": "local",
                    "command": launcher,
                    "args": ["--workspace", workspace],
                    "env": unityai_env,
                    "tools": ["*"],
                }
            }
            # K3: `headers` taşıyan `http` kayıt yerine stdio köprüsü — sır
            # config dosyasına HİÇ girmiyor, köprü onu token dosyasından kendi
            # okuyor. `unityai` zaten aynı sözlükte `type: "local"` şeklinde,
            # yani şema bu dosyada kanıtlı.
            unity_mcp_url = unity_mcp_manager.mcp_url()
            if unity_mcp_url:
                from .codex_unitymcp_bridge import bridge_argv
                _argv = bridge_argv()
                servers["unityMCP"] = {
                    "type": "local",
                    "command": _argv[0],
                    "args": _argv[1:],
                    "env": {"UNITY_MCP_URL": unity_mcp_url, **owner_env},
                    "tools": ["*"],
                }

            fd, path = tempfile.mkstemp(prefix="uai_copilot_mcp_", suffix=".json")
            os.close(fd)
            # `mkstemp` dosyayı 0600 ile yaratıyor ama Windows'ta POSIX izin
            # bitleri etkisiz (ölçüldü: `local_token_file` docstring'i), o yüzden
            # ACL burada da açıkça kısıtlanıyor. Doğrulama turu bulgusu: bu
            # yazıcı hardener'ı hiç çağırmıyordu.
            #
            # K3 sonrası: dosya artık `X-API-Key` TAŞIMIYOR, o yüzden
            # sertleştirememek unityMCP'yi DÜŞÜRMÜYOR — korunacak sır yokken
            # kaydı atmak sebepsiz işlev kaybı olurdu. Sertleştirme yine de
            # deneniyor (dosya workspace yollarını ve `unityai` ortamını
            # taşıyor) ve başarısızlık sessiz değil.
            from .workspace_config import harden_config_file

            if not harden_config_file(path):
                logger.warning(
                    "[CopilotProvider] %s izinleri kısıtlanamadı; dosya dizin "
                    "izinlerini miras alıyor.", path,
                )
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": servers}, f, indent=2)
            self._mcp_cfg_path = path
            logger.info(f"[CopilotProvider] session MCP config yazıldı: {path}")
        except Exception as e:
            self._mcp_cfg_path = None
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
            logger.warning(f"[CopilotProvider] MCP config yazılamadı: {e}")
