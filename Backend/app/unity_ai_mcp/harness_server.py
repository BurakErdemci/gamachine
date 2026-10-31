"""Harness-notes-only stdio MCP server for Claude SDK chats.

Claude chats load no unityai server (`strict_mcp_config`): its shell and file
tools would bypass the SDK's own approval. This server exposes only
harness_search, harness_read and harness_status, registered by the same
`register_harness_tools` the unityai server uses. Started per chat through
`harness_notes.claude_server_entry`; the frozen build reaches it as
`backend mcp-server --harness-only` (see `unity_ai_mcp.server.main`).
"""
from mcp.server.mcpserver import MCPServer

import harness_notes
from unity_ai_mcp.server import register_harness_tools


def create_server() -> MCPServer:
    mcp = MCPServer(
        name=harness_notes.CLAUDE_SERVER_NAME,
        instructions=(
            "Gamachine'in kendi araçları, özellikleri, ayarları ve sınırları hakkında kısa notlar. "
            "Bunlarla ilgili bir soruyu cevaplamadan önce harness_search ile ara, bulunan notu "
            "harness_read ile oku; canlı durum için harness_status."
        ),
    )
    register_harness_tools(mcp)
    return mcp


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
