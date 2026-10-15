"""Local stdio MCP test double; argv mcp/embedded/socket are deliberately ignored."""
import json
import os
import sys

sys.dont_write_bytecode = True

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

mcp = MCPServer(name="fake-cua")
snapshot = False
app_reads = 0
IMAGE = "aGVsbG8="


def log(tool, args):
    with open(os.environ["FAKE_CUA_LOG"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tool": tool, "args": args, "process": os.getpid()}) + "\n")


@mcp.tool()
def list_apps() -> CallToolResult:
    global app_reads
    app_reads += 1
    log("list_apps", {})
    bundle = ("com.apple.Notes" if os.environ.get("FAKE_CUA_REMAP") and app_reads > 1
              else "com.apple.TextEdit")
    return CallToolResult(content=[TextContent(type="text", text=json.dumps({"apps": [
        {"pid": 4242, "bundle_id": bundle, "name": "TextEdit", "running": True},
        {"pid": 44, "bundle_id": "com.apple.Terminal", "name": "Terminal", "running": True},
        {"pid": 0, "bundle_id": "com.example.Offline", "running": False},
    ]}))])


@mcp.tool()
def get_window_state(pid: int, window_id: int) -> CallToolResult:
    global snapshot
    snapshot = True
    log("get_window_state", {"pid": pid, "window_id": window_id})
    return CallToolResult(content=[TextContent(type="text", text="snapshot"),
                                   ImageContent(type="image", mime_type="image/png", data=IMAGE)])


@mcp.tool()
def click(pid: int, element_token: str = "", window_id: int = 0,
          x: float | None = None, y: float | None = None) -> CallToolResult:
    log("click", {"pid": pid, "element_token": element_token, "window_id": window_id, "x": x, "y": y})
    return CallToolResult(content=[TextContent(type="text", text="clicked")], is_error=x is not None and not snapshot)


@mcp.tool()
def type_text(pid: int, text: str) -> str:
    log("type_text", {"pid": pid, "text": text})
    if text == "exit-mid-call":
        os._exit(9)
    return "typed"


@mcp.tool()
def hotkey(pid: int, keys: list[str]) -> str:
    log("hotkey", {"pid": pid, "keys": keys})
    return "pressed"


@mcp.tool()
def run_steps(pid: int, steps: list[dict], foreground_fallback: bool = False) -> str:
    log("run_steps", {"pid": pid, "steps": steps, "foreground_fallback": foreground_fallback})
    return "done"


@mcp.tool()
def launch_app(bundle_id: str, creates_new_application_instance: bool = False,
               urls: list[str] | None = None) -> str:
    log("launch_app", {"bundle_id": bundle_id, "creates_new_application_instance": creates_new_application_instance,
                       "urls": urls})
    return "launched"


@mcp.tool()
def list_windows(pid: int, on_screen_only: bool = False) -> str:
    log("list_windows", {"pid": pid, "on_screen_only": on_screen_only})
    return "windows"


@mcp.tool()
def clipboard_read(include_text: bool = False) -> str:
    log("clipboard_read", {"include_text": include_text})
    return "clipboard"


@mcp.tool()
def clipboard_write(text: str) -> str:
    log("clipboard_write", {"text": text})
    return "written"


if __name__ == "__main__":
    mcp.run(transport="stdio")
