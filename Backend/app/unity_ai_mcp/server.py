"""
UnityAI MCP Server

Claude Code ve Codex'in bağlandığı MCP sunucusu.
Tehlikeli operasyonlar (write_file, delete_file, bash) UnityAI UI'dan onay alır.

Kullanım:
  python -m app.mcp.server --workspace /path/to/unity/project

Claude Code (.mcp.json veya settings.json):
  {
    "mcpServers": {
      "unityai": {
        "command": "python",
        "args": ["-m", "app.mcp.server", "--workspace", "/path/to/project"],
        "env": {"UNITYAI_URL": "http://localhost:8000"}
      }
    }
  }

Codex (~/.codex/config.toml):
  [mcp_servers.unityai]
  command = "python"
  args = ["-m", "app.mcp.server", "--workspace", "/path/to/project"]
  [mcp_servers.unityai.env]
  UNITYAI_URL = "http://localhost:8000"
"""
import os
import sys
import argparse
import asyncio

from mcp.server.mcpserver import MCPServer

from unity_ai_mcp.tools.file_tools import register_file_tools
from unity_ai_mcp.tools.bash_tool import register_bash_tool

_workspace_path: str = ""


def get_workspace() -> str:
    return _workspace_path or os.getcwd()


def create_server(workspace: str = "") -> MCPServer:
    global _workspace_path
    if workspace:
        _workspace_path = workspace

    mcp = MCPServer(
        name="unityai",
        instructions=(
            "Unity projesi üzerinde çalışan bir AI asistanısın. "
            "Dosya okuma ve dizin listeleme özgürce yapılabilir. "
            "Dosya yazma, silme ve terminal komutları kullanıcı onayı gerektirir; "
            "her tehlikeli operasyon için IDE'de onay kartı gösterilir. "
            "Dosya oluşturma/düzenleme için `save_file`, terminal komutları için "
            "`bash`, `run_terminal_command` veya `execute_shell_command` kullanılabilir."
        ),
    )

    register_file_tools(mcp, get_workspace)
    register_bash_tool(mcp, get_workspace)

    return mcp


def main():
    global _workspace_path

    parser = argparse.ArgumentParser(description="UnityAI MCP Server")
    parser.add_argument("--workspace", default=os.environ.get("WORKSPACE_PATH", ""))
    args, _ = parser.parse_known_args()
    _workspace_path = args.workspace or os.getcwd()

    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
