"""
MCP Bash Aracı — güvenli komutlar direkt, tehlikeli komutlar onay gerektirir.
Dosya yazmaya çalışan terminal komutları DiffViewer'a yönlendirilir.
"""
import os
import re
import subprocess
from mcp.server.mcpserver import MCPServer

from unity_ai_mcp.approval_bridge import request_approval
from unity_ai_mcp.tools.file_tools import _resolve
import unity_file_guard

from agentic.command_safety import (  # noqa: F401  (tek kaynak — bkz command_safety.py)
    auto_safe_argv as _auto_safe_argv,
    is_auto_safe as _is_safe,
)


def _parse_file_write(command: str):
    """
    Terminal komutu bir dosyaya içerik yazıyorsa (path, content) döner, değilse None.
    Desteklenen kalıplar:
      - python3 -c / python -c ile open(..., 'w').write(...)
      - printf 'content' > path
      - echo 'content' > path
      - touch path  (boş dosya)
    """
    c = command.strip()

    # python3 -c "...open('path','w')...write('content')..."
    m = re.search(
        r"""open\(\s*['"]([^'"]+)['"]\s*,\s*['"]w['"]\).*?\.write\(\s*['"](.+?)['"]\s*\)""",
        c, re.DOTALL
    )
    if m:
        path = m.group(1)
        content = m.group(2).replace("\\n", "\n").replace("\\'", "'").replace('\\"', '"')
        return path, content

    # printf 'content' > path  /  echo 'content' > path
    m = re.search(r'(?:printf|echo)\s+[\'"](.+?)[\'"]\s*>\s*(\S+)', c, re.DOTALL)
    if m:
        content = m.group(1).replace("\\n", "\n")
        return m.group(2), content

    # printf 'content' | dd of=path  /  echo 'content' | dd of=path
    m = re.search(r'(?:printf|echo)\s+[\'"](.+?)[\'"]\s*\|.*?dd\s+of=(\S+)', c, re.DOTALL)
    if m:
        content = m.group(1).replace("\\n", "\n")
        return m.group(2), content

    # dd if=... of=path  (genel dd yazma)
    m = re.search(r'\bdd\b.*?\bof=(\S+)', c)
    if m:
        return m.group(1), ""  # İçerik parse edilemez ama path bilinir

    return None


def register_bash_tool(mcp: MCPServer, get_workspace: callable):

    async def _run_command(command: str) -> str:
        workspace = get_workspace()
        refusal = unity_file_guard.check_shell(command, workspace)
        if refusal is not None:
            return f"❌ {refusal.message}"

        # Terminalle dosya yazma girişimi → DiffViewer'a yönlendir
        file_write = _parse_file_write(command)
        if file_write:
            path, new_content = file_write
            # Boş içerikli test dosyaları → reddet, write_file kullanmasını söyle
            if not new_content.strip():
                return "❌ Boş dosya oluşturma reddedildi. Dosya içeriğiyle birlikte mcp__unityai__save_file kullan."
            abs_path = _resolve(path, workspace)
            refusal = unity_file_guard.check_write(abs_path, workspace)
            if refusal is not None:
                return f"❌ {refusal.message}"
            original = ""
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        original = f.read()
                except Exception:
                    pass

            result = await request_approval(
                tool_name="write_file",
                params={"path": path, "content": new_content, "original": original},
                workspace_path=workspace,
            )
            if result.get("approved") is not True:
                return f"❌ Reddedildi: {path}"
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return f"✅ Yazıldı: {path}"

        # Karar ve token listesi TEK çağrıdan geliyor. Ayrı ayrı almak (önce
        # "güvenli mi" sorup sonra ham dizgeyi çalıştırmak) denetlenen metin ile
        # çalıştırılan metnin ayrışmasına izin veriyordu; C1/C2/C3/P2'nin dördü
        # de o aralıktan geçiyordu.
        argv = _auto_safe_argv(command, workspace)
        if argv is None:
            result = await request_approval(
                tool_name="bash",
                params={"command": command},
                workspace_path=workspace,
            )
            if result.get("approved") is not True:
                return f"❌ Komut reddedildi: {command}"

        # Onaysız geçen komut kabuğa HİÇ verilmiyor: argv doğrudan çalışıyor,
        # yani `%VAR%` genişlemesi, ikinci ayrıştırma ve zincirleme yapısal
        # olarak imkânsız. Onaylanmış komut kabukla çalışmaya devam ediyor —
        # kullanıcı ham dizgeyi gördü ve tam olarak onu onayladı.
        hedef, kabuk_kullan = (command, True) if argv is None else (argv, False)

        try:
            proc = subprocess.run(
                hedef,
                shell=kabuk_kullan,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
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
