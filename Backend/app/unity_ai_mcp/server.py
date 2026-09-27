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
from unity_ai_mcp.tools.mailbox_tools import register_mailbox_tools

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
    register_mailbox_tools(mcp)

    return mcp


def _open_append_shared(path: str):
    """Open `path` for appending so several server processes can write at once.

    Windows: an append-only handle (FILE_APPEND_DATA without FILE_WRITE_DATA)
    makes every write land at the current end of file atomically. Measured
    27 Sep 2026: three processes x 5000 lines through plain open(path, "a")
    kept 8123 of 15000 lines (the C runtime seeks to the end, then writes, so
    writers overwrite each other); through this handle all 15000 survived.
    POSIX O_APPEND already appends atomically.
    """
    if os.name != "nt":
        return open(path, "a", encoding="utf-8", errors="backslashreplace", buffering=1)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    file_append_data = 0x0004
    share_all = 0x0001 | 0x0002 | 0x0004  # read | write | delete
    open_always = 4
    handle = kernel32.CreateFileW(path, file_append_data, share_all, None,
                                  open_always, 0x80, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    return open(fd, "a", encoding="utf-8", errors="backslashreplace", buffering=1)


def _redirect_stderr_to_log() -> None:
    """Send this process's diagnostics to UNITYAI_MCP_LOG_FILE, if set.

    run_mcp_server.cmd used to do this with `2>> mcp_server.log`; cmd.exe opens
    that file without write sharing, so while one unityai server ran every
    other launch died at the redirection before Python started (measured
    27 Sep 2026: OpenCode "MCP error -32000: Connection closed", an agy
    branch lost its mail tools). If the file cannot be opened (for example a
    launcher of the old kind still holds it) the server keeps stderr, which
    the MCP client captures, rather than failing to start.
    """
    path = os.environ.get("UNITYAI_MCP_LOG_FILE", "").strip()
    if not path:
        return
    try:
        sys.stderr = _open_append_shared(path)
    except OSError:
        return


def main():
    global _workspace_path

    _redirect_stderr_to_log()

    parser = argparse.ArgumentParser(description="UnityAI MCP Server")
    parser.add_argument("--workspace", default=os.environ.get("WORKSPACE_PATH", ""))
    args, _ = parser.parse_known_args()
    _workspace_path = args.workspace or os.getcwd()

    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
