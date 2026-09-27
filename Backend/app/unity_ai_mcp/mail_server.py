"""Mail-only stdio MCP server for Claude SDK chats.

Claude chats load no unityai server (`strict_mcp_config`), and must not: its
shell and file tools would bypass the SDK's own approval. This server exposes
only `list_chats` and `send_chat_message`. It is started per chat with
GAMACHINE_CONVERSATION_ID in its env (see `agentic.mailbox.claude_server_entry`).
"""
from mcp.server.mcpserver import MCPServer

from unity_ai_mcp.tools.mailbox_tools import register_mailbox_tools


def create_server() -> MCPServer:
    mcp = MCPServer(
        name="gamachineMail",
        instructions=(
            "Gamachine sohbet posta kutusu: kullanıcının diğer sohbetlerini listele "
            "ve gerekirse birine kısa bir not bırak. Not kullanıcıdan değil senden gider. "
            "Hedefi kullanıcı `@<numara>` ile verdiyse onu kullan; aynı başlıklı sohbetler "
            "arasında tahmin etme, kullanıcıdan `@` ile seçmesini iste."
        ),
    )
    register_mailbox_tools(mcp)
    return mcp


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
