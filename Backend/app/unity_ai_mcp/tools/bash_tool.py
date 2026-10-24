"""
MCP Bash Aracı — güvenli komutlar direkt, tehlikeli komutlar onay gerektirir.
Dosya yazmaya çalışan terminal komutları DiffViewer'a yönlendirilir.
"""
import os
import subprocess
from mcp.server.mcpserver import MCPServer

from unity_ai_mcp.approval_bridge import request_approval
from agentic import shell_exec
from agentic.shell_exec import _parse_file_write  # noqa: F401  (eski içe aktarmalar için)

from agentic.command_safety import (  # noqa: F401  (tek kaynak — bkz command_safety.py)
    is_auto_safe as _is_safe,
)


def register_bash_tool(mcp: MCPServer, get_workspace: callable):

    async def _run_command(command: str) -> str:
        workspace = get_workspace()
        refused = shell_exec.shell_refusal(command, workspace)
        if refused is not None:
            return f"❌ {refused}"

        # Terminalle dosya yazma girişimi → DiffViewer'a yönlendir
        file_write = shell_exec.prepare_file_write(command, workspace)
        if file_write:
            path, new_content = file_write.path, file_write.content
            # Boş içerikli test dosyaları → reddet, write_file kullanmasını söyle
            if not new_content.strip():
                return "❌ Boş dosya oluşturma reddedildi. Dosya içeriğiyle birlikte mcp__unityai__save_file kullan."
            if file_write.refusal is not None:
                return f"❌ {file_write.refusal}"
            abs_path = file_write.abs_path

            result = await request_approval(
                tool_name="write_file",
                params={"path": path, "content": new_content, "original": file_write.original},
                workspace_path=workspace,
            )
            if result.get("approved") is not True:
                return f"❌ Reddedildi: {path}"
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return f"✅ Yazıldı: {path}"

        argv = shell_exec.plan(command, workspace)
        if argv is None:
            result = await request_approval(
                tool_name="bash",
                params={"command": command},
                workspace_path=workspace,
            )
            if result.get("approved") is not True:
                return f"❌ Komut reddedildi: {command}"

        try:
            proc = shell_exec.run(command, argv, workspace)
            output = (proc.stdout or "") + (proc.stderr or "")
            if len(output) > 8000:
                output = output[:8000] + "\n... (kısaltıldı)"
            return output or "(çıktı yok)"
        except subprocess.TimeoutExpired:
            return "❌ Zaman aşımı (300s)"
        except Exception as e:
            return f"❌ Hata: {str(e)}"

    @mcp.tool(
        description=(
            "Run a terminal/shell command in the workspace. Safe read-only commands may run directly; "
            "dangerous commands such as rm, mkdir, mv, git push, npm install, chmod, and writes "
            "request approval in the Antigravity UI before execution."
        )
    )
    async def bash(command: str) -> str:
        """
        Terminal komutu çalıştırır.
        Güvenli komutlar (git status, ls vb.) direkt çalışır.
        Tehlikeli komutlar (git push, rm, npm install vb.) ONAY GEREKTİRİR.
        """
        return await _run_command(command)

    @mcp.tool(
        name="run_terminal_command",
        description=(
            "Execute a terminal command in the workspace through Antigravity's approval bridge."
        ),
    )
    async def run_terminal_command(command: str) -> str:
        return await _run_command(command)

    @mcp.tool(
        name="execute_shell_command",
        description=(
            "Alias for run_terminal_command. Runs shell commands like git, mkdir, rm, mv, npm, "
            "python, and ls in the workspace, with approval for dangerous operations."
        ),
    )
    async def execute_shell_command(command: str) -> str:
        return await _run_command(command)
