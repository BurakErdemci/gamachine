"""
MCP Dosya Araçları — save_file/delete_file direkt yazar, bash onay gerektirir.
"""
import logging
import os
from pathlib import Path
from mcp.server.mcpserver import MCPServer

from unity_ai_mcp.approval_bridge import request_approval
import unity_file_guard

logger = logging.getLogger(__name__)


def register_file_tools(mcp: MCPServer, get_workspace: callable):

    @mcp.tool()
    async def read_file(path: str) -> str:
        """Bir dosyayı okur."""
        workspace = get_workspace()
        abs_path = _resolve(path, workspace)
        if not os.path.exists(abs_path):
            return f"Hata: Dosya bulunamadı: {path}"
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        content = "".join(lines[:500])
        suffix = f"\n... ({total - 500} satır daha)" if total > 500 else ""
        return content + suffix

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
    workspace_resolved = Path(workspace).expanduser().resolve(strict=False)
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = workspace_resolved / p
    resolved = p.resolve(strict=False)
    try:
        resolved.relative_to(workspace_resolved)
    except ValueError:
        raise PermissionError(f"Güvenlik: {path} workspace dışında.")
    return str(resolved)
