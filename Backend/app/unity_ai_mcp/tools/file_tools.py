"""
MCP Dosya Araçları — save_file/delete_file direkt yazar, bash onay gerektirir.
"""
import logging
import os
from mcp.server.mcpserver import MCPServer

from unity_ai_mcp.approval_bridge import request_approval
import unity_file_guard
from path_containment import resolve_within

logger = logging.getLogger(__name__)


def register_file_tools(mcp: MCPServer, get_workspace: callable):

    @mcp.tool()
    async def read_file(path: str, start_line: int = 1, max_lines: int = 500) -> str:
        """Reads a text file. Long files: pass start_line (1-based) and max_lines (1..2000, default 500) to read any range; the footer says where to continue."""
        if (
            not isinstance(start_line, int)
            or isinstance(start_line, bool)
            or start_line < 1
        ):
            return "Hata: start_line 1 veya daha büyük bir tam sayı olmalı."
        if (
            not isinstance(max_lines, int)
            or isinstance(max_lines, bool)
            or not 1 <= max_lines <= 2000
        ):
            return "Hata: max_lines 1 ile 2000 arasında bir tam sayı olmalı."
        workspace = get_workspace()
        abs_path = _resolve(path, workspace)
        if not os.path.exists(abs_path):
            return f"Hata: Dosya bulunamadı: {path}"
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        if total > 0 and start_line > total:
            return f"Hata: start_line {start_line} dosyanın {total} satırını aşıyor."
        first = start_line
        last = min(total, start_line - 1 + max_lines)
        content = "".join(lines[first - 1:last])
        if last < total:
            return content + (
                f"\n... ({first}-{last} / {total} satır gösterildi; "
                f"devamı için start_line={last + 1})"
            )
        return content

    @mcp.tool()
    async def list_directory(path: str = ".") -> str:
        """Klasör içeriğini listeler."""
        workspace = get_workspace()
        abs_path = _resolve(path, workspace)
        if not os.path.isdir(abs_path):
            return f"Hata: Klasör bulunamadı: {path}"
        items = []
        for entry in sorted(os.listdir(abs_path)):
            full = os.path.join(abs_path, entry)
            tag = "/" if os.path.isdir(full) else ""
            items.append(f"{entry}{tag}")
        return "\n".join(items)

    @mcp.tool(name="save_file")
    async def write_file(path: str, content: str) -> str:
        """Dosya oluşturur veya düzenler. ONAY GEREKTİRİR."""
        workspace = get_workspace()
        abs_path = _resolve(path, workspace)
        refusal = unity_file_guard.check_write(abs_path, workspace)
        if refusal is not None:
            return f"❌ {refusal.message}"

        original = ""
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
                if original.strip() == content.strip():
                    return "Dosya zaten aynı içerikte, değişiklik yok."
            except Exception:
                pass

        result = await request_approval(
            tool_name="write_file",
            params={"path": path, "content": content, "original": original},
            workspace_path=workspace,
        )

        if result.get("approved") is not True:
            return f"❌ Yazma reddedildi: {path}"

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"[file_tools] Yazıldı: {path}")
        return f"✅ Yazıldı: {path}"

    @mcp.tool()
    async def delete_file(path: str) -> str:
        """Dosyayı siler. ONAY GEREKTİRİR."""
        workspace = get_workspace()
        abs_path = _resolve(path, workspace)
        refusal = unity_file_guard.check_delete(abs_path, workspace)
        if refusal is not None:
            return f"❌ {refusal.message}"

        if not os.path.exists(abs_path):
            return f"Hata: Dosya bulunamadı: {path}"

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()
        except Exception:
            original = ""

        result = await request_approval(
            tool_name="delete_file",
            params={"path": path, "original": original},
            workspace_path=workspace,
        )

        if result.get("approved") is not True:
            return f"❌ Silme reddedildi: {path}"

        os.remove(abs_path)
        return f"🗑️ Silindi: {path}"


def _resolve(path: str, workspace: str) -> str:
    resolved = resolve_within(path, workspace)
    if resolved is None:
        raise PermissionError(f"Güvenlik: {path} workspace dışında.")
    return resolved
