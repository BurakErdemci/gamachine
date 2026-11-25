"""Sub-agent-only stdio MCP server for Claude SDK chats.

Claude chats load no unityai server (`strict_mcp_config`), so the agent_*
tools come from this small server, registered by the same
`register_agent_tools` the unityai server uses. Started per chat through
`agentic.subagents.claude_server_entry` (never for a sub-agent's own chat);
the frozen build reaches it as `backend mcp-server --agents-only` (see
`unity_ai_mcp.server.main`).
"""
from mcp.server.mcpserver import MCPServer

from unity_ai_mcp.tools.agent_tools import register_agent_tools

SERVER_NAME = "gamachineAgents"


def create_server() -> MCPServer:
    mcp = MCPServer(
        name=SERVER_NAME,
        instructions=(
            "Gamachine sub-agents: start other connected CLI models as background helpers "
            "(agent_models, agent_start), message, stop, list and read them. Depth 1; start only "
            "what the job needs; ask the user before parallel writers in one folder; results "
            "arrive in this chat by themselves: check agent_list when deciding or when the user "
            "asks, and never poll it in a loop."
        ),
    )
    register_agent_tools(mcp)
    return mcp


def main():
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
