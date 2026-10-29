"""Tiny stdio MCP server standing in for gamachineComputer / gamachineBrowser.
argv: <label> <pidfile-base>. Tools: whoami (echoes the chat id from env), screenshot (PNG ImageContent)."""
import base64, os, sys
from mcp.server.mcpserver import MCPServer, Image

label, pidbase = sys.argv[1], sys.argv[2]
with open(f"{pidbase}.{label}.pid", "w") as f:
    f.write(str(os.getpid()))
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
mcp = MCPServer(name=f"fake-{label}")

@mcp.tool()
def whoami() -> str:
    """Return the chat id this server process was started with."""
    return f"{label} chat={os.environ.get('GAMACHINE_CONVERSATION_ID', 'NONE')}"

@mcp.tool()
def screenshot() -> Image:
    """Return a 1x1 PNG."""
    return Image(data=PNG, format="png")

mcp.run(transport="stdio")
