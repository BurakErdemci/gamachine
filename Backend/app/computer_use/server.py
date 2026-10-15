"""Curated MCP proxy for computer use.

Each tool takes a single ``args`` dictionary. This preserves every allowed
driver argument without default injection; policy validates it before any I/O.
"""
from contextlib import asynccontextmanager
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from unity_ai_mcp import approval_bridge
from . import policy
from .driver_client import DriverClient, DriverError, DriverUnavailable


def _text(value, error=False):
    return CallToolResult(content=[TextContent(type="text", text=value)], is_error=error)


def _foreground(args):
    if isinstance(args, dict):
        return args.get("delivery_mode") == "foreground" or args.get("foreground_fallback") is True or any(
            _foreground(value) for value in args.values() if isinstance(value, (dict, list)))
    if isinstance(args, list):
        return any(_foreground(value) for value in args)
    return False


_BUNDLE_SUFFIXES = (".app", ".bundle", ".framework", ".plugin", ".appex", ".prefpane",
                    ".workflow", ".action", ".shortcut", ".scptd", ".pkg", ".mpkg",
                    ".kext", ".xpc", ".terminal", ".command", ".tool",
                    ".saver", ".service", ".systemextension", ".wdgt", ".dext",
                    ".qlgenerator", ".mdimporter", ".component", ".colorpicker",
                    ".osax", ".menu", ".vst", ".vst3", ".au", ".clap")


def _is_bundle_dir(path):
    return (os.path.basename(path).casefold().endswith(_BUNDLE_SUFFIXES)
            or os.path.exists(os.path.join(path, "Contents", "Info.plist"))
            or os.path.exists(os.path.join(path, "Contents", "MacOS")))


def _resolve_launch_targets(urls):
    """Return the urls with every path replaced by its checked real path, or None."""
    resolved = []
    for url in urls:
        if not url.startswith("/"):
            resolved.append(url)
            continue
        try:
            real_path = os.path.realpath(url)
            if not os.path.isdir(real_path):
                return None
            current = real_path
            while True:
                if _is_bundle_dir(current):
                    return None
                parent = os.path.dirname(current)
                if parent == current:
                    break
                current = parent
        except (OSError, ValueError):
            return None
        resolved.append(real_path)
    return resolved


def _safe_launch_targets(urls):
    return _resolve_launch_targets(urls) is not None


class ComputerServer:
    def __init__(self, driver=None):
        self.driver = driver

    async def call(self, tool, args):
        validated = policy.validate_call(tool, args)
        if isinstance(validated, policy.Refusal):
            return _text(validated.reason, True)
        try:
            forward_args = validated.args
            if tool == "computer_launch_app" and "urls" in args:
                resolved = _resolve_launch_targets(args["urls"])
                if resolved is None:
                    return _text("urls must target existing directories outside executable bundles", True)
                forward_args = {**validated.args, "urls": resolved}
            bundle_id = app_name = pid = None
            if "pid" in args:
                pid = args["pid"]
                if self.driver is None:
                    self.driver = DriverClient()
                bundle_id, app_name = (await self.driver.list_running_apps()).get(pid, (None, None))
            elif tool == "computer_launch_app":
                bundle_id = args["bundle_id"]
            elif tool == "computer_clipboard_read":
                bundle_id, app_name = policy.CLIPBOARD_BUNDLE_ID, "Clipboard"
            risk = policy.risky_reason(tool, args)
            params = {"bundle_id": bundle_id, "app_name": app_name, "pid": pid,
                      "risky": bool(risk), "risk_reason": risk,
                      "summary": args.get("summary") or app_name or bundle_id or tool,
                      "foreground": _foreground(args)}
            try:
                result = await approval_bridge.request_approval(tool, params=params, workspace_path="")
            except Exception:
                result = {"approved": False, "error": "İşlem onayı doğrulanamadı."}
            if not isinstance(result, dict):
                result = {"approved": False, "error": "İşlem onayı doğrulanamadı."}
            approved = result.get("approved") is True
            reason = result.get("error") or "İşlem için onay verilmedi."
            if tool == "computer_confirm_risky_step":
                return _text("approved" if approved else "rejected: " + str(reason), not approved)
            if not approved:
                return _text(str(reason), True)
            if pid is not None:
                current_bundle, _ = (await self.driver.list_running_apps()).get(pid, (None, None))
                if (not policy.valid_bundle_id(bundle_id) or not policy.valid_bundle_id(current_bundle)
                        or current_bundle.casefold() != bundle_id.casefold()):
                    return _text("Hedef uygulama onaydan sonra değişti; işlem reddedildi.", True)
            if self.driver is None:
                self.driver = DriverClient()
            return await self.driver.call(validated.driver_tool, forward_args)
        except DriverUnavailable:
            return _text("Bilgisayar kullanımı sürücüsü kullanılamıyor.", True)
        except DriverError:
            return _text("Bilgisayar kullanımı sürücüsüyle bağlantı kesildi; işlem yinelenmedi.", True)

    async def close(self):
        if self.driver is not None:
            await self.driver.close()


def create_server(driver=None):
    handler = ComputerServer(driver)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await handler.close()

    mcp = MCPServer(name="gamachineComputer", lifespan=lifespan, instructions=(
        "Read a window with computer_window_state before acting. Prefer element_token over x,y. "
        "Batch known steps with computer_run_steps. Call computer_confirm_risky_step before "
        "deleting, purchasing, sending/publishing, overwriting or downloading with the user's account. "
        "Terminals, System Settings and Gamachine are off limits. Each tool accepts an args object."
    ))

    def register(name):
        async def invoke(args: dict) -> CallToolResult:
            return await handler.call(name, args)
        spec = policy.TOOLS[name]
        mcp.tool(name=name, description=(
            "Computer-use operation. Allowed args: " + ", ".join(sorted(spec.allowed))
            + ". Required: " + ", ".join(sorted(spec.required)) + "."
        ))(invoke)

    for name in policy.TOOLS:
        register(name)
    return mcp


def main():
    from unity_ai_mcp.server import _redirect_stderr_to_log
    _redirect_stderr_to_log()
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
