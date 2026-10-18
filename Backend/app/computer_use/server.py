"""Curated MCP proxy for computer use.

Each tool takes a single ``args`` dictionary. This preserves every allowed
driver argument without default injection; policy validates it before any I/O.
"""
from contextlib import asynccontextmanager
import asyncio
import json
import os

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from unity_ai_mcp import approval_bridge
from . import policy
from .driver_client import DriverClient, DriverError, DriverUnavailable, fetch_runtime


_TOOL_HINTS = {
    "computer_invoke_menu": (
        " Menu items are often disabled while the app is in the background; prefer the item's "
        "keyboard shortcut with computer_hotkey (cmd+s to save, cmd+o to open), which works in the "
        "background. Use computer_bring_to_front only if the shortcut is refused."
    ),
    "computer_bring_to_front": (
        " Brings the app's window to the front and leaves it there. Last resort: hotkeys, typing "
        "and element_token clicks reach background apps and their Open/Save panels, so use it "
        "only after a background attempt was refused. Pass window_id to verify that window is in front."
    ),
}
_ELEMENT_TOKEN_HINT = (
    ' element_token is `<snapshot_id>:N` from the latest computer_window_state of that window '
    '(e.g. "s00000003:18"); never a bare number.'
)


def _text(value, error=False):
    return CallToolResult(content=[TextContent(type="text", text=value)], is_error=error)


async def _batch_stop(params) -> str | None:
    try:
        body = {"params": {key: params.get(key) for key in ("bundle_id", "app_name", "pid", "window_id")}}
        conversation_id = approval_bridge._conversation_id_from_env()
        if type(conversation_id) is int:
            body["conversation_id"] = conversation_id
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"{approval_bridge.BACKEND_URL}/computer-use/batch-step",
                headers=approval_bridge._get_headers(), json=body)
        if not 200 <= response.status_code < 300:
            return "unverified"
        result = response.json()
        if isinstance(result, dict) and "stop" in result:
            stop = result["stop"]
            if stop is None or stop in ("halted", "disabled"):
                return stop
        return "unverified"
    except Exception:
        return "unverified"


def _foreground(args):
    if isinstance(args, dict):
        return args.get("delivery_mode") == "foreground" or args.get("foreground_fallback") is True or any(
            _foreground(value) for value in args.values() if isinstance(value, (dict, list)))
    if isinstance(args, list):
        return any(_foreground(value) for value in args)
    return False


async def _front_guard(driver, pid, window_id) -> str | None:
    if type(window_id) is not int:
        return "Foreground input needs window_id; nothing was sent."
    for attempt in range(2):
        result = await driver.call("bring_to_front", {"pid": pid, "window_id": window_id})
        if result.is_error is not True:
            report = result.structured_content
            try:
                if not isinstance(report, dict):
                    report = json.loads(next(block.text for block in result.content
                                             if isinstance(block, TextContent)))
                effect = report.get("exact_window_effect") if isinstance(report, dict) else None
                if isinstance(effect, dict) and effect.get("verified") is True:
                    return None
            except (ValueError, TypeError, StopIteration):
                pass
        if attempt == 0:
            await asyncio.sleep(0.3)
    return (
        f"Foreground input not sent: window {window_id} could not be verified as the front window "
        "(another window, for example Gamachine, may be on top). Ask the user to bring the app "
        "to the front, or use background delivery with element_token.")


_BUNDLE_SUFFIXES = (".app", ".bundle", ".framework", ".plugin", ".appex", ".prefpane",
                    ".workflow", ".action", ".shortcut", ".scptd", ".pkg", ".mpkg",
                    ".kext", ".xpc", ".terminal", ".command", ".tool",
                    ".saver", ".service", ".systemextension", ".wdgt", ".dext",
                    ".qlgenerator", ".mdimporter", ".component", ".colorpicker",
                    ".osax", ".menu", ".vst", ".vst3", ".au", ".clap")

_DOCUMENT_HANDLER_DENY = frozenset({
    "com.apple.finder", "com.apple.terminal", "com.googlecode.iterm2", "org.python.pythonlauncher",
    "dev.warp.warp-stable", "com.mitchellh.ghostty", "net.kovidgoyal.kitty", "org.alacritty",
    "co.zeit.hyper", "com.github.wez.wezterm", "com.apple.installer", "com.apple.archiveutility",
    "com.apple.scripteditor2", "com.apple.automator", "com.apple.shortcuts",
})

_RUNNABLE_FILE_SUFFIXES = (
    ".sh", ".bash", ".zsh", ".csh", ".ksh", ".fish", ".command", ".tool", ".py", ".pyw", ".pyc",
    ".rb", ".pl", ".php", ".scpt", ".applescript", ".jxa", ".workflow", ".shortcut", ".action",
    ".terminal", ".webloc", ".inetloc", ".fileloc", ".url", ".desktop", ".jar", ".pkg", ".mpkg",
    ".dmg", ".iso", ".img", ".sparseimage", ".sparsebundle", ".mobileconfig", ".provisionprofile",
    ".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm", ".lnk", ".exe", ".msi", ".bat", ".cmd",
    ".ps1", ".vbs", ".wsf", ".reg", ".so", ".dylib", ".bundle", ".kext", ".plugin", ".prefpane",
    ".saver", ".qlgenerator", ".app",
)


def _is_bundle_dir(path):
    return (os.path.basename(path).casefold().endswith(_BUNDLE_SUFFIXES)
            or os.path.exists(os.path.join(path, "Contents", "Info.plist"))
            or os.path.exists(os.path.join(path, "Contents", "MacOS")))


LAUNCH_NAME_TIMEOUT = 3.0


def _resolve_launch_targets(urls, bundle_id):
    """Return the urls with every path replaced by its checked real path, or None."""
    resolved = []
    for url in urls:
        if not url.startswith("/"):
            resolved.append(url)
            continue
        try:
            real_path = os.path.realpath(url)
            if not (os.path.isdir(real_path) or (
                    os.path.isfile(real_path)
                    and bundle_id.casefold() not in _DOCUMENT_HANDLER_DENY
                    and not os.path.basename(real_path).casefold().endswith(_RUNNABLE_FILE_SUFFIXES)
                    and not os.path.basename(real_path).casefold().endswith(_BUNDLE_SUFFIXES)
                    and os.stat(real_path).st_mode & 0o111 == 0)):
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


def _safe_launch_targets(urls, bundle_id):
    return _resolve_launch_targets(urls, bundle_id) is not None


class ComputerServer:
    def __init__(self, driver=None):
        self.driver = driver
        self._driver_lock = asyncio.Lock()
        # Fronting and the input it guards must not interleave with another call's fronting.
        self._front_lock = asyncio.Lock()

    async def _runtime_driver(self):
        async with self._driver_lock:
            binary, socket = await fetch_runtime()
            if self.driver is None or (self.driver.binary, self.driver.socket) != (binary, socket):
                if self.driver is not None:
                    await self.driver.close()
                self.driver = DriverClient(binary, socket)
            return self.driver

    async def call(self, tool, args):
        validated = policy.validate_call(tool, args)
        if isinstance(validated, policy.Refusal):
            return _text(validated.reason, True)
        try:
            driver = None
            forward_args = validated.args
            if tool == "computer_launch_app" and "urls" in args:
                resolved = _resolve_launch_targets(args["urls"], args["bundle_id"])
                if resolved is None:
                    return _text(
                        "urls must target existing directories, or plain document files the app opens "
                        "(not scripts, executables, installers or files inside app bundles; "
                        "not with Finder or a terminal)", True)
                forward_args = {**validated.args, "urls": resolved}
            bundle_id = app_name = pid = None
            if "pid" in args:
                pid = args["pid"]
                driver = await self._runtime_driver()
                bundle_id, app_name = (await driver.list_running_apps()).get(pid, (None, None))
            elif tool == "computer_launch_app":
                bundle_id = args["bundle_id"]
                try:
                    driver = await self._runtime_driver()
                    app_name = await asyncio.wait_for(driver.installed_app_name(bundle_id), LAUNCH_NAME_TIMEOUT)
                except Exception:
                    # Name lookup is cosmetic; never block or refuse the launch.
                    app_name = None
            elif tool == "computer_clipboard_read":
                bundle_id, app_name = policy.CLIPBOARD_BUNDLE_ID, "Clipboard"
            risk = policy.risky_reason(tool, args)
            params = {"bundle_id": bundle_id, "app_name": app_name, "pid": pid,
                      "risky": bool(risk), "risk_reason": risk,
                      "summary": args.get("summary") or app_name or bundle_id or tool,
                      "foreground": _foreground(args) or tool == "computer_bring_to_front",
                      # bool is an int subclass; a window id is never True/False.
                      "window_id": args.get("window_id") if type(args.get("window_id")) is int else None}
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
                current_bundle, _ = (await driver.list_running_apps()).get(pid, (None, None))
                if (not policy.valid_bundle_id(bundle_id) or not policy.valid_bundle_id(current_bundle)
                        or current_bundle.casefold() != bundle_id.casefold()):
                    return _text("Hedef uygulama onaydan sonra değişti; işlem reddedildi.", True)
            if driver is None:
                driver = await self._runtime_driver()
            if tool == "computer_run_steps" and len(args["steps"]) >= 2:
                return await self._run_steps_split(driver, forward_args, params)
            if tool == "computer_bring_to_front":
                async with self._front_lock:
                    return await driver.call(validated.driver_tool, forward_args)
            if _foreground(forward_args):
                refusal, result = await self._fronted_call(
                    driver, pid, forward_args.get("window_id"), validated.driver_tool, forward_args)
                return _text(refusal, True) if refusal is not None else result
            return await driver.call(validated.driver_tool, forward_args)
        except DriverUnavailable:
            return _text("Bilgisayar kullanımı sürücüsü kullanılamıyor.", True)
        except DriverError:
            return _text("Bilgisayar kullanımı sürücüsüyle bağlantı kesildi; işlem yinelenmedi.", True)

    async def _fronted_call(self, driver, pid, window_id, tool, args):
        async with self._front_lock:
            try:
                previous = await driver.frontmost_pid()
            except Exception:
                previous = None
            refusal = await _front_guard(driver, pid, window_id)
            if refusal is not None:
                return refusal, None
            result = await driver.call(tool, args)
            # The guard fronts persistently, unlike the driver's brief fronting.
            if type(previous) is int and previous != pid:
                try:
                    await driver.call("bring_to_front", {"pid": previous})
                except Exception:
                    # The action already ran; an error here must not make the agent repeat it.
                    pass
            return None, result

    async def _run_steps_split(self, driver, forward_args, params):
        steps = forward_args["steps"]
        total = len(steps)
        base = {key: forward_args[key] for key in (
            "pid", "window_id", "delivery_mode", "foreground_fallback") if key in forward_args}
        delay_ms = forward_args.get("delay_ms")
        entries = []
        failed_step = None
        stop_reason = None
        observation = {}
        non_text = []
        for i, step in enumerate(steps):
            if i > 0:
                if isinstance(delay_ms, int) and delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000)
            stop_reason = await _batch_stop(params)
            if stop_reason is not None:
                break
            call_args = {**base, "steps": [step]}
            if "observe" in forward_args and i == total - 1:
                call_args["observe"] = forward_args["observe"]
            if _foreground({**base, **step}):
                step_args = step.get("args", {}) if "tool" in step else next(
                    (step[key] for key in step if key in policy.STEP_TOOLS), {})
                window_id = step_args.get("window_id")
                if type(window_id) is not int:
                    window_id = base.get("window_id")
                refusal, result = await self._fronted_call(driver, base["pid"], window_id, "run_steps", call_args)
                if refusal is not None:
                    entries.append({"index": i, "ok": False, "code": "foreground_unverified",
                                    "message": refusal})
                    failed_step = i
                    break
            else:
                result = await driver.call("run_steps", call_args)
            report = result.structured_content
            try:
                if not (isinstance(report, dict) and isinstance(report.get("steps"), list)):
                    report = json.loads(next(block.text for block in result.content
                                             if isinstance(block, TextContent)))
                if (not isinstance(report, dict) or not isinstance(report.get("steps"), list)
                        or len(report["steps"]) != 1 or not isinstance(report["steps"][0], dict)):
                    raise ValueError("invalid step report")
            except (ValueError, TypeError, StopIteration):
                return CallToolResult(content=[TextContent(
                    type="text", text=f"Adım {i+1}/{total} sonucu okunamadı; kalan adımlar çalıştırılmadı.")]
                    + result.content, is_error=True)
            entry = {**report["steps"][0], "index": i}
            entries.append(entry)
            observation = {"observation": report["observation"]} if "observation" in report else {}
            non_text = [block for block in result.content if not isinstance(block, TextContent)]
            if result.is_error is True or report.get("ok") is not True or entry.get("ok") is not True:
                failed_step = i
                break
        executed = len(entries)
        report = {"executed": executed, "total": total, "failed_step": failed_step,
                  "ok": executed == total and failed_step is None and stop_reason is None,
                  "steps": entries, **observation}
        if stop_reason is not None:
            reasons = {
                "halted": (
                    f"Kullanıcı bilgisayar kullanımını bu tur için durdurdu; {executed}/{total} adım çalıştı, "
                    "kalanlar çalıştırılmadı. Bu turda bilgisayar araçlarını yeniden deneme."),
                "disabled": (
                    f"Bilgisayar kullanımı kapatıldı; {executed}/{total} adım çalıştı, kalanlar çalıştırılmadı."),
                "unverified": (
                    f"Durdurma durumu doğrulanamadı; {executed}/{total} adım çalıştı, kalanlar çalıştırılmadı."),
            }
            report = {"halted": True, "stop_reason": stop_reason,
                      "halt_reason": reasons[stop_reason], **report}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(report, ensure_ascii=False))]
                              + non_text, is_error=not report["ok"])

    async def close(self):
        async with self._driver_lock:
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
        "Terminals, System Settings and Gamachine are off limits. "
        "Every tool takes exactly one parameter named args; put all arguments inside it, "
        'e.g. {"args": {"pid": 123, "window_id": 45}}. '
        "To check a file the task created, look only in the folder the task names; "
        "never search the whole home folder, macOS blocks other apps' data and warns the user. "
        "Work in the background: hotkeys, typing and element_token clicks reach background apps, "
        "including their Open/Save panels. To open a file: computer_launch_app with the app's bundle_id "
        "and urls set to the file's absolute path; only if that is refused, cmd+o, then type the full path starting with / "
        "(the panel opens its own go-to field; do not press cmd+shift+g), return, return. To save: cmd+s. "
        "Use delivery_mode foreground or computer_bring_to_front only after "
        "a background attempt was refused."
    ))

    def register(name):
        async def invoke(args: dict) -> CallToolResult:
            return await handler.call(name, args)
        spec = policy.TOOLS[name]
        description = (
            "Computer-use operation. Allowed args: " + ", ".join(sorted(spec.allowed))
            + ". Required: " + ", ".join(sorted(spec.required)) + "."
        )
        description += _TOOL_HINTS.get(name, "")
        if "element_token" in spec.allowed:
            description += _ELEMENT_TOKEN_HINT
        if name == "computer_run_steps":
            description += (
                '\n\nEach step is {"tool": NAME, "args": {...}} or the shorthand {NAME: {...}}, '
                "where NAME is one of: " + ", ".join(sorted(policy.STEP_TOOLS))
                + " (no computer_ prefix). A step may add wait_for, expect (a check object "
                "or a list of up to 4: " + ", ".join(sorted(policy._CHECK_KEYS - {"pid", "window_id"}))
                + ") and timeout_ms. Step args use the same names as the matching tool "
                "and inherit pid/window_id from the batch. 1-64 steps; delay_ms 0-2000 between steps. "
                'Example: {"args": {"pid": 123, "window_id": 45, "steps": '
                '[{"tool": "type_text", "args": {"text": "hello "}}, '
                '{"tool": "press_key", "args": {"key": "return"}}]}}'
            )
        mcp.tool(name=name, description=description)(invoke)

    for name in policy.TOOLS:
        register(name)
    return mcp


def main():
    from unity_ai_mcp.server import _redirect_stderr_to_log
    _redirect_stderr_to_log()
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
