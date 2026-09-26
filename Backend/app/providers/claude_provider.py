from secret_redaction import redact_secrets
import json
import os
import sys
import logging
from .cli_base import BaseCLIProvider
from .unity_script_tools import DISALLOWED_UNITY_TOOLS

logger = logging.getLogger(__name__)




class ClaudeCodeProvider(BaseCLIProvider):

    # CANLI ÖLÇÜLDÜ 2026-08-01: `echo <prompt> | claude -p --model ...` cevabı
    # döndürdü, yani prompt metni argv'de olmadan okunuyor.
    prompt_via_stdin = True

    def _build_cmd(self, prompt: str, thinking_level: str = "medium", workspace: str = None) -> list:
        full_id = self.binary_name
        # Built-in tehlikeli araçları blokla → Claude MCP'lerimizi kullanmak ZORUNDA kalır
        # bypassPermissions modu --allowedTools'u pasifleştirdiği için sadece deny-list işe yarar
        disallowed = ",".join([
            "Bash",          # → mcp__unityai__bash
            "Write",         # → mcp__unityai__save_file
            "Edit",          # → mcp__unityai__save_file
            "MultiEdit",     # → mcp__unityai__save_file
            "NotebookEdit",
            # Every unityMCP tool that writes a .cs file, so all C# goes through
            # the approved mcp__unityai__save_file. Scripts are attached with
            # manage_gameobject/manage_components.
            *DISALLOWED_UNITY_TOOLS,
        ])
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        unity_running = unity_mcp_manager.is_running()
        unity_section = ""
        if unity_running:
            unity_section = (
                "\nUNITY EDITOR — unityMCP tools (use these for ALL Unity scene/UI operations):\n"
                "- Scene hierarchy:               mcp__unityMCP__manage_scene action=get_hierarchy\n"
                "- Create/modify GameObjects:     mcp__unityMCP__manage_gameobject\n"
                "- Add/remove/edit components:    mcp__unityMCP__manage_components\n"
                "- UI elements (Canvas/Button):   mcp__unityMCP__manage_ui\n"
                "- Materials/shaders:             mcp__unityMCP__manage_material\n"
                "- Console logs:                  mcp__unityMCP__read_console\n"
                "RULE: Never write Editor scripts to create scene objects — use unityMCP tools directly.\n"
                "C# SCRIPTS (.cs): ALWAYS create/edit them with mcp__unityai__save_file (user approval).\n"
                "  NEVER use unityMCP for writing .cs files. To attach a script to a GameObject, first\n"
                "  create the .cs with save_file, then attach via mcp__unityMCP__manage_gameobject.\n"
            )
        subagent_prefix = (
            "SUBAGENT EXECUTION MODE: You are a subagent dispatched to execute a specific task. "
            "Do NOT invoke any skills, do NOT brainstorm, do NOT offer visual companions or mockups, "
            "do NOT ask clarifying questions. Execute the task IMMEDIATELY using available MCP tools. "
            "Respond in Turkish (Türkçe).\n"
            + unity_section + "\n"
        )
        cmd = [
            "claude", "--model", full_id,
            "--permission-mode", "bypassPermissions",
            "--disallowedTools", disallowed,
            # Without strict mode the CLI also loads every MCP server of the
            # owner's own Claude Code (measured 26 Sep 2026: about 60, Gmail,
            # Drive, Vercel and a second Unity server among them), and under
            # bypassPermissions text in a compacted conversation could steer
            # the model into them. Same fix as the chat path (95b5e81).
            "--strict-mcp-config",
            "--mcp-config", json.dumps({"mcpServers": self._product_mcp_servers(workspace)}),
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
        ]
        yuk = subagent_prefix + prompt
        if self.prompt_via_stdin:
            # `-p` KALIYOR, yalnız DEĞERİ düşüyor: claude'u etkileşimsiz kipe
            # sokan bayrak bu ve metni stdin'den okuyor (canlı ölçüldü).
            cmd.append("-p")
            self._stdin_payload = yuk
        else:
            cmd += ["-p", yuk]
        return cmd

    def _product_mcp_servers(self, workspace: str = None) -> dict:
        """The only MCP servers this CLI run may load (see --strict-mcp-config)."""
        from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
        # Same entry `_write_mcp_config` writes: no LOCAL_APP_TOKEN, the
        # launcher reads it from the 0600 token file.
        servers = {
            "unityai": {
                "command": self._launcher_path("run_mcp_server"),
                "args": ["--workspace", workspace or os.getcwd()],
                "env": {"UNITYAI_URL": os.environ.get(
                    "UNITYAI_URL", os.environ.get("ANTIGRAVITY_URL", "http://localhost:8000"))},
            }
        }
        unity_mcp_url = unity_mcp_manager.mcp_url()
        if unity_mcp_url:
            # HTTP, not the stdio bridge the other CLIs get: measured, the
            # Claude CLI leaves a stdio unityMCP `pending` and connects HTTP
            # (see [[codex-unitymcp-stdio-bridge]]). The key rides in argv,
            # as the Claude SDK does for chat sessions, and never in a file
            # the model can read.
            servers["unityMCP"] = {
                "type": "http",
                "url": unity_mcp_url,
                "headers": unity_mcp_manager.api_headers(),
            }
        return servers

    @staticmethod
    def _resolve_exec(cmd: list) -> list:
        # cmd.exe re-parses a batch shim's arguments and the JSON's escaped
        # quotes leave its values unquoted there. Measured 26 Sep 2026: a
        # workspace path "C:\R&echo INJECTED&\x" inside --mcp-config ran
        # `echo INJECTED` through a .cmd shim. So spawn the exe behind the npm
        # shim, as the chat path does, and never hand the JSON to cmd.exe.
        if sys.platform == "win32" and cmd and cmd[0] == "claude":
            from .claude_sdk_session import claude_ikilisini_coz
            exe = claude_ikilisini_coz()
            if exe:
                return [exe, *cmd[1:]]
        spawn = BaseCLIProvider._resolve_exec(cmd)
        if spawn[:2] == ["cmd", "/c"] and "--mcp-config" in cmd:
            raise RuntimeError(
                "Claude Code yalnız bir .cmd kabuğu olarak bulundu ve arkasındaki "
                "claude.exe bulunamadı; MCP yapılandırması cmd.exe'den güvenle "
                "geçirilemiyor. Claude Code'u yeniden kurun.")
        return spawn

    # Class-level on purpose: providers are built per request and the cleanup
    # below is meant to run once per backend process.
    _stale_user_scope_cleaned = False

    def _register_mcp(self, launcher: str, workspace: str, backend_url: str):
        """Registers nothing: `_build_cmd` passes the product's servers inline.

        Older versions added `unityai` and `unityMCP` to the owner's user scope
        on every call, where his own Claude Code sessions loaded them too. This
        removes those two, once per process. `claude mcp remove --scope user`
        matches the key exactly (2.1.283 source: `mcpServers?.[name]`), so the
        owner's own `UnityMCP` is never touched.
        """
        if ClaudeCodeProvider._stale_user_scope_cleaned:
            return
        import subprocess as sp
        from .cli_base import build_spawn_env, env_family

        if not self._cli_installed("claude"):
            logger.warning("[CLIProvider] claude CLI bulunamadı, eski MCP kaydı temizliği atlandı.")
            return
        ClaudeCodeProvider._stale_user_scope_cleaned = True

        # Allow-list env, never the parent's (measured 2026-07-29: without
        # env= the child saw LOCAL_APP_TOKEN, the DB key and vendor keys).
        _env = build_spawn_env(env_family(self.binary_name))
        for name in ("unityai", "unityMCP"):
            try:
                sp.run(self._resolve_exec(["claude", "mcp", "remove", name, "--scope", "user"]),
                       capture_output=True, timeout=5, env=_env)
            except Exception as e:
                logger.warning(f"[CLIProvider] Claude {name} eski kaydı silinemedi: "
                               f"{redact_secrets(str(e))}")
