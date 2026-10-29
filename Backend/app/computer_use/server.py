"""Curated MCP proxy for computer use.

Each tool takes a single ``args`` dictionary. This preserves every allowed
driver argument; policy validates it and applies platform defaults before any I/O.
"""
from contextlib import asynccontextmanager, nullcontext
from collections import OrderedDict
import asyncio
import json
import re

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from spawn_env import read_only_refusal
from unity_ai_mcp import approval_bridge
from . import platform, policy
from .driver_client import DriverClient, DriverError, DriverUnavailable
from .platform.base import WindowListUnavailable
from .platform.macos import fetch_runtime


_TOOL_HINTS = {
    "computer_list_windows": (
        " Also lists the app's open panels and dialogs (layer above 0, e.g. Unity's New Scene window); "
        "the one with the highest z_index is in front. Read it with computer_window_state by its "
        "window_id: the main window's screenshot does not show it."
    ),
    "computer_invoke_menu": (
        " Menu items are often disabled while the app is in the background; prefer the item's "
        "keyboard shortcut with computer_hotkey (cmd+s to save, cmd+o to open), which works in the "
        "background. If the shortcut is refused, send it again with delivery_mode foreground and window_id."
    ),
    "computer_bring_to_front": (
        " Brings the app's window to the front and leaves it there. Last resort: macOS may refuse to "
        "activate the app; a refused background hotkey or click works when sent again with delivery_mode "
        "foreground and window_id, so use this only if that is refused too. Pass window_id to verify that window is in front."
    ),
}
_ELEMENT_TOKEN_HINT = (
    ' element_token is `<snapshot_id>:N` from the latest computer_window_state of that window '
    '(e.g. "s00000003:18"); never a bare number.'
)


def _text(value, error=False):
    return CallToolResult(content=[TextContent(type="text", text=value)], is_error=error)


def window_state_driver_args(args) -> dict:
    return {"tree_format": "both", **args}


def strip_unrequested_elements(report, args) -> dict:
    if args.get("tree_format") in {"elements", "both"}:
        return dict(report)
    return {key: value for key, value in report.items() if key != "elements"}


def _strip_snapshot_result(result, args):
    report = result.structured_content
    if isinstance(report, dict):
        report = strip_unrequested_elements(report, args)
    content = []
    for block in result.content:
        if isinstance(block, TextContent):
            try:
                payload = json.loads(block.text)
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict) and "elements" in payload:
                block = block.model_copy(update={"text": json.dumps(strip_unrequested_elements(payload, args))})
        content.append(block)
    return result.model_copy(update={"structured_content": report, "content": content})


async def _merge_running_apps(result):
    try:
        if result.is_error:
            return result
        structured = result.structured_content
        payloads = []
        texts = {}
        if isinstance(structured, dict) and isinstance(structured.get("apps"), list):
            payloads.append(structured)
        for index, block in enumerate(result.content):
            if not isinstance(block, TextContent):
                continue
            try:
                payload = json.loads(block.text)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("apps"), list):
                texts[index] = payload
                payloads.append(payload)
        if not payloads:
            return result
        seen = {row.get("pid") for payload in payloads for row in payload["apps"] if isinstance(row, dict)}
        extra = []
        for row in await platform.current().running_apps():
            if row["pid"] not in seen:
                extra.append(row)
                seen.add(row["pid"])
        if not extra:
            return result
        content = [
            block.model_copy(update={"text": json.dumps({**texts[index], "apps": texts[index]["apps"] + extra})})
            if index in texts else block
            for index, block in enumerate(result.content)
        ]
        if isinstance(structured, dict) and isinstance(structured.get("apps"), list):
            structured = {**structured, "apps": structured["apps"] + extra}
        return result.model_copy(update={"content": content, "structured_content": structured})
    except Exception:
        return result


async def _merge_panel_windows(result, pid):
    try:
        if result.is_error:
            return result
        structured = result.structured_content
        payloads = []
        texts = {}
        if isinstance(structured, dict) and isinstance(structured.get("windows"), list):
            payloads.append(structured)
        for index, block in enumerate(result.content):
            if not isinstance(block, TextContent):
                continue
            try:
                payload = json.loads(block.text)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("windows"), list):
                texts[index] = payload
                payloads.append(payload)
        if not payloads:
            return result
        rows = [row for payload in payloads for row in payload["windows"] if isinstance(row, dict)]
        base_z = max((row["z_index"] for row in rows if type(row.get("z_index")) is int), default=0)
        seen = {row.get("window_id") for row in rows}
        extra = []
        for row in await platform.current().panel_windows(pid, base_z):
            if row["window_id"] not in seen:
                extra.append(row)
                seen.add(row["window_id"])
        if not extra:
            return result
        content = [
            block.model_copy(update={"text": json.dumps({**texts[index], "windows": texts[index]["windows"] + extra})})
            if index in texts else block
            for index, block in enumerate(result.content)
        ]
        if isinstance(structured, dict) and isinstance(structured.get("windows"), list):
            structured = {**structured, "windows": structured["windows"] + extra}
        return result.model_copy(update={"content": content, "structured_content": structured})
    except Exception:
        return result


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


def _front_flags(result):
    flags = {"process_activated": None, "focused": None}

    def collect(value):
        if isinstance(value, dict):
            for key in flags:
                if flags[key] is None and type(value.get(key)) is bool:
                    flags[key] = value[key]
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    try:
        collect(result.structured_content)
        texts = [block.text for block in result.content if isinstance(block, TextContent)]
        for text in texts:
            try:
                collect(json.loads(text))
            except (ValueError, TypeError):
                pass
        for key, pattern in (("process_activated", r"process_activated=(true|false)"),
                             ("focused", r"\bfocused=(true|false)")):
            if flags[key] is None:
                for text in texts:
                    match = re.search(pattern, text)
                    if match:
                        flags[key] = match.group(1) == "true"
                        break
    except Exception:
        pass
    return flags["process_activated"], flags["focused"]


def _window_rows(result):
    payloads = [result.structured_content]
    for block in result.content:
        if isinstance(block, TextContent):
            try:
                payloads.append(json.loads(block.text))
            except (ValueError, TypeError):
                pass
    rows = []
    seen = set()
    for payload in payloads:
        values = payload.get("windows") if isinstance(payload, dict) else payload
        if not isinstance(values, list):
            continue
        for row in values:
            if not isinstance(row, dict):
                continue
            window_id = row.get("window_id")
            if type(window_id) is int and window_id not in seen:
                seen.add(window_id)
                rows.append(row)
    return rows


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
                if platform.current().front_verified(report, pid, window_id):
                    return None
            except (ValueError, TypeError, StopIteration):
                pass
        if attempt == 0:
            await asyncio.sleep(0.3)
    try:
        panels = await platform.current().panel_windows(pid, 0)
        is_panel = any(row["window_id"] == window_id for row in panels)
    except Exception:
        is_panel = False
    if is_panel:
        try:
            await driver.call("bring_to_front", {"pid": pid})
            for attempt in range(2):
                try:
                    top_window = await platform.current().top_window_is(pid, window_id)
                except WindowListUnavailable:
                    await platform.current().frontmost_pid(driver)
                    break
                # Read the foreground app after the window snapshot, immediately before input.
                frontmost = await platform.current().frontmost_pid(driver)
                if frontmost == pid and top_window and platform.current().foreground_window_is(window_id):
                    return None
                if attempt == 0:
                    await asyncio.sleep(0.3)
        except Exception:
            pass
        return (
            f"Foreground input not sent: panel window {window_id} could not be verified as the top window "
            "of the frontmost app (another window or app may be on top). Read the app's windows "
            "again with computer_list_windows.")
    if _front_flags(result) == (True, False):
        try:
            windows = _window_rows(await driver.call("list_windows", {"pid": pid}))
        except Exception:
            windows = []
        candidates = [row for row in windows if row["window_id"] != window_id
                      and row.get("pid", pid) == pid and row.get("on_current_space") is not False
                      and type(row.get("z_index")) is int]
        if candidates:
            other = max(candidates, key=lambda row: row["z_index"])
            other_id = other["window_id"]
            title = other.get("title")
            title = title[:80] if isinstance(title, str) and title else "untitled"
            return (
                f'Foreground input not sent: the app is in front, but its window {other_id} ("{title}") '
                f'has focus, not window {window_id}. This is usually a dialog or panel such as '
                f'Go to Folder, Open or Save. Send the input to window_id {other_id} instead.')
        return (
            f"Foreground input not sent: the app is in front, but another of its windows has focus, "
            f"not window {window_id} (usually a dialog or panel such as Go to Folder, Open or Save). "
            "Call computer_list_windows for this pid and send the input to that window_id.")
    return (
        f"Foreground input not sent: window {window_id} could not be verified as the front window "
        "(another window, for example Gamachine, may be on top). Ask the user to bring the app "
        "to the front, or use background delivery with element_token.")


LAUNCH_NAME_TIMEOUT = 3.0
# Snapshot rows: `- [14] Button "Pencil" [actions=[toggle]]`. The role part may not
# contain "[" so a quoted attribute value is never read as the name; a row with no
# quoted name is known and unnamed. A name containing `" [` is cut there.
_NAMED_ROW = re.compile(r'^\s*- \[(\d+)\] [^"\[\n]*?"(.*?)"(?: \[.*)?\s*$')
_UNNAMED_ROW = re.compile(r'^\s*- \[(\d+)\] [^"\[\n]*(?:\[.*)?$')


def _resolve_launch_targets(urls, bundle_id):
    return platform.current().resolve_launch_targets(urls, bundle_id)


def _safe_launch_targets(urls, bundle_id):
    return _resolve_launch_targets(urls, bundle_id) is not None


class BackendApprovals:
    def preflight(self):
        return None

    async def dispatch_refusal(self, params) -> str | None:
        return None

    async def request(self, tool, params):
        named_critical = params.get("named_critical")
        params = {key: value for key, value in params.items()
                  if key not in {"confirmed", "confirm_text", "element_name", "critical_reason", "named_critical"}}
        if named_critical:
            existing = params.get("risk_reason") if params.get("risky") else ""
            params.update(risky=True, risk_reason=named_critical if not existing or existing in named_critical
                          else f"{existing} · {named_critical}")
        return await approval_bridge.request_approval(tool, params=params, workspace_path="")

    async def batch_stop(self, params):
        return await _batch_stop({key: value for key, value in params.items()
                                  if key not in {"confirmed", "confirm_text", "element_name", "critical_reason", "named_critical"}})


class ComputerServer:
    def __init__(self, driver=None, approvals=None, *, runtime=None):
        self.driver = driver
        self.approvals = approvals if approvals is not None else BackendApprovals()
        self.runtime = runtime
        self._driver_lock = asyncio.Lock()
        # Fronting and the input it guards must not interleave with another call's fronting.
        self._front_lock = asyncio.Lock()
        self._snapshots = OrderedDict()

    def _cache_snapshot(self, result, args):
        report = result.structured_content
        if result.is_error or not isinstance(report, dict):
            return
        if isinstance(report.get("observation"), dict):
            report = report["observation"]
        snapshot_id, tree = report.get("snapshot_id"), report.get("tree_markdown")
        if not isinstance(snapshot_id, str):
            return
        rows = {}
        frames = []
        complete = False
        elements = report.get("elements")
        if isinstance(elements, list) and elements:
            complete = True
            for element in elements:
                if not isinstance(element, dict):
                    complete = False
                    continue
                token = element.get("element_token")
                label = element.get("label", "")
                label = label if isinstance(label, str) else ""
                if isinstance(token, str):
                    rows.setdefault(token, label)
                # x/y clicks are screenshot pixels: "frame" is screen coordinates and only
                # "screenshot_frame" shares their space (measured 26 Oct 2026, Paint on both
                # monitors: Share frame x=521 vs screenshot_frame x=212).
                frame = element.get("screenshot_frame")
                if (isinstance(frame, dict) and all(type(frame.get(key)) in (int, float)
                                                    for key in ("x", "y", "w", "h"))
                        and frame["w"] > 0 and frame["h"] > 0):
                    # The window root spans every point and is named after the document.
                    root = element.get("depth") == 0 or str(element.get("role", "")).casefold() == "window"
                    frames.append((dict(frame), label, root))
                else:
                    complete = False
        elif isinstance(tree, str):
            for line in tree.splitlines():
                # Driver rows contain nested action brackets and quoted values after the name.
                match = _NAMED_ROW.match(line) or _UNNAMED_ROW.match(line)
                if match:
                    rows.setdefault(f"{snapshot_id}:{int(match.group(1))}",
                                    match.group(2) if match.re is _NAMED_ROW else "")
        else:
            return
        key = (args["pid"], args.get("window_id"))
        if not (isinstance(elements, list) and elements):
            # A snapshot without structured elements must not erase the frames of the last one;
            # a structured one always replaces them, so stale labels never classify a new click.
            _, _, frames, complete = self._snapshots.get(key, (None, None, [], False))
        self._snapshots.pop(key, None)
        self._snapshots[key] = (snapshot_id, rows, frames, complete)
        while len(self._snapshots) > 16:
            self._snapshots.popitem(last=False)

    def _element_name(self, args):
        token = args.get("element_token")
        if not isinstance(token, str):
            return None
        snapshot_id, separator, row = token.rpartition(":")
        if not separator or not row.isdecimal():
            return None
        for (pid, window_id), (cached_id, rows, _, _) in reversed(self._snapshots.items()):
            if (pid == args.get("pid") and cached_id == snapshot_id
                    and ("window_id" not in args or args["window_id"] == window_id)):
                return rows.get(token)
        return None

    def _element_at(self, args):
        x, y = args.get("x"), args.get("y")
        if type(x) not in (int, float) or type(y) not in (int, float) or args.get("from_zoom"):
            # Non-numeric points and zoom-image coordinates cannot be hit-tested in screenshot space.
            return False, None
        if "window_id" in args:
            window_id = args["window_id"]
            snapshot = (self._snapshots.get((args.get("pid"), window_id))
                        if type(window_id) is int else None)
        else:
            snapshot = next((value for (pid, _), value in reversed(self._snapshots.items())
                             if pid == args.get("pid") and value[2]), None)
        if snapshot is None or not snapshot[2]:
            # No snapshot, or one without frames: the point cannot be classified.
            return False, None
        containing = [(frame["w"] * frame["h"], label) for frame, label, root in snapshot[2]
                      if not root and frame["x"] <= x < frame["x"] + frame["w"]
                      and frame["y"] <= y < frame["y"] + frame["h"]]
        if containing:
            return True, min(containing, key=lambda item: item[0])[1]
        # Empty space is only verified when every element of the snapshot had a frame.
        return (True, "") if snapshot[3] else (False, None)

    def _resolver(self, args):
        def resolve(target):
            return self._resolve_target({**{key: args[key] for key in ("pid", "window_id") if key in args},
                                         **target})
        return resolve

    def _resolve_target(self, target):
        if "element_token" in target:
            name = self._element_name(target)
            return name is not None, name
        nested = target.get("target") if isinstance(target.get("target"), dict) else {}
        for source in (target, nested):
            if "name" in source:
                return isinstance(source["name"], str), source["name"] if isinstance(source["name"], str) else None
        if any(key in source for source in (target, nested) for key in ("role", "nth")):
            return False, None
        if "x" in target and "y" in target:
            return self._element_at(target)
        return True, None

    async def _dispatch_refusal(self, params):
        check = getattr(self.approvals, "dispatch_refusal", None)
        refusal = await check(params) if check is not None else None
        return refusal if isinstance(refusal, str) else None

    async def _runtime_driver(self):
        async with self._driver_lock:
            binary, socket = await (self.runtime or fetch_runtime)()
            if self.driver is None or (self.driver.binary, self.driver.socket) != (binary, socket):
                if self.driver is not None:
                    await self.driver.close()
                self._snapshots.clear()
                self.driver = DriverClient(binary, socket)
            return self.driver

    async def call(self, tool, args):
        refusal = read_only_refusal() or self.approvals.preflight()
        if isinstance(refusal, str):
            return _text(refusal, True)
        confirmed = False
        confirm_text = None
        if getattr(self.approvals, "accepts_confirm", False) and isinstance(args, dict) and "confirm" in args:
            args = dict(args)
            confirmation = args.pop("confirm", None)
            confirm_text = confirmation.strip() if isinstance(confirmation, str) else None
            confirmed = confirm_text is not None and 0 < len(confirm_text) <= 500
        validated = policy.validate_call(tool, args)
        if isinstance(validated, policy.Refusal):
            return _text(validated.reason, True)
        args = validated.args
        window_id = args.get("window_id") if type(args.get("window_id")) is int else None
        try:
            driver = None
            forward_args = validated.args
            if tool == "computer_window_state":
                forward_args = window_state_driver_args(args)
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
                bundle_id, app_name, refusal = await platform.current().identify(driver, pid, window_id=window_id)
                if refusal is not None:
                    return _text(refusal, True)
            elif tool == "computer_launch_app":
                bundle_id = args["bundle_id"]
                launch_args = platform.current().launch_target(bundle_id)
                if launch_args is None:
                    return _text("Launch target is no longer available or permitted.", True)
                forward_args = {key: value for key, value in forward_args.items() if key != "bundle_id"}
                forward_args.update(launch_args)
                try:
                    driver = await self._runtime_driver()
                    app_name = await asyncio.wait_for(driver.installed_app_name(bundle_id), LAUNCH_NAME_TIMEOUT)
                except Exception:
                    # Name lookup is cosmetic; never block or refuse the launch.
                    app_name = None
            elif tool == "computer_clipboard_read":
                bundle_id, app_name = policy.CLIPBOARD_BUNDLE_ID, "Clipboard"
            risk = policy.risky_reason(tool, args)
            element_name = self._element_name(args)
            target_resolver = self._resolver(args)
            def resolve(target):
                nonlocal element_name
                known, name = target_resolver(target)
                if name is not None:
                    element_name = name
                return known, name
            critical = policy.critical_reason(tool, args, app_id=bundle_id, resolve=resolve)
            named_critical = policy.critical_reason(
                tool, args, app_id=bundle_id, resolve=resolve, strict=False) if critical else ""
            params = {"bundle_id": bundle_id, "app_name": app_name, "pid": pid,
                      "confirmed": confirmed, "confirm_text": confirm_text,
                      "element_name": element_name, "critical_reason": critical,
                      "named_critical": named_critical,
                      "risky": bool(risk), "risk_reason": risk,
                      "summary": args.get("summary") or app_name or bundle_id or tool,
                      "foreground": _foreground(args) or tool == "computer_bring_to_front",
                      # bool is an int subclass; a window id is never True/False.
                      "window_id": args.get("window_id") if type(args.get("window_id")) is int else None}
            try:
                result = await self.approvals.request(tool, params)
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
                current_bundle, _, refusal = await platform.current().identify(driver, pid, window_id=window_id)
                if refusal is not None:
                    return _text(refusal, True)
                if (not policy.valid_bundle_id(bundle_id) or not policy.valid_bundle_id(current_bundle)
                        or current_bundle.casefold() != bundle_id.casefold()):
                    return _text("Hedef uygulama onaydan sonra değişti; işlem reddedildi.", True)
            if driver is None:
                driver = await self._runtime_driver()
            if tool == "computer_run_steps" and len(args["steps"]) >= 2:
                return await self._run_steps_split(driver, forward_args, params)
            if tool == "computer_bring_to_front":
                async with self._front_lock:
                    refusal = await self._dispatch_refusal(params)
                    if refusal is not None:
                        return _text(refusal, True)
                    return await driver.call(validated.driver_tool, forward_args)
            if _foreground(forward_args):
                refusal, result = await self._fronted_call(
                    driver, pid, forward_args.get("window_id"), validated.driver_tool, forward_args, params)
                if refusal is not None:
                    return _text(refusal, True)
            else:
                refusal = await self._dispatch_refusal(params)
                if refusal is not None:
                    return _text(refusal, True)
                result = await driver.call(validated.driver_tool, forward_args)
            if tool in {"computer_window_state", "computer_run_steps"}:
                self._cache_snapshot(result, args)
            if tool == "computer_window_state":
                result = _strip_snapshot_result(result, args)
            if tool == "computer_list_windows":
                return await _merge_panel_windows(result, pid)
            return await _merge_running_apps(result) if tool == "computer_list_apps" else result
        except DriverUnavailable:
            return _text("Bilgisayar kullanımı sürücüsü kullanılamıyor.", True)
        except DriverError:
            return _text("Bilgisayar kullanımı sürücüsüyle bağlantı kesildi; işlem yinelenmedi.", True)

    async def _fronted_call(self, driver, pid, window_id, tool, args, params=None):
        async with self._front_lock:
            refusal = await self._dispatch_refusal(params or {})
            if refusal is not None:
                return refusal, None
            refusal = await _front_guard(driver, pid, window_id)
            if refusal is not None:
                return refusal, None
            refusal = await self._dispatch_refusal(params or {})
            if refusal is not None:
                return refusal, None
            result = await driver.call(tool, args)
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
        has_foreground = any(_foreground({**base, **step}) for step in steps)
        async with self._front_lock if has_foreground else nullcontext():
            for i, step in enumerate(steps):
                if i > 0:
                    if isinstance(delay_ms, int) and delay_ms > 0:
                        await asyncio.sleep(delay_ms / 1000)
                stop_reason = await self.approvals.batch_stop(params)
                if stop_reason is not None:
                    break
                if i > 0 and "pid" in base:
                    window_id = base.get("window_id") if type(base.get("window_id")) is int else None
                    current, _, refusal = await platform.current().identify(driver, base["pid"], window_id=window_id)
                    if (refusal is not None or not policy.valid_bundle_id(current)
                            or current.casefold() != params["bundle_id"].casefold()):
                        entries.append({"index": i, "ok": False, "code": "target_changed",
                                        "message": "Hedef uygulama adımlar arasında değişti; "
                                                   "kalan adımlar çalıştırılmadı."})
                        failed_step = i
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
                    refusal = await self._dispatch_refusal(params)
                    if refusal is not None:
                        return _text(refusal, True)
                    refusal = await _front_guard(driver, base["pid"], window_id)
                    if refusal is not None:
                        entries.append({"index": i, "ok": False, "code": "foreground_unverified",
                                        "message": refusal})
                        failed_step = i
                        break
                refusal = await self._dispatch_refusal(params)
                if refusal is not None:
                    return _text(refusal, True)
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
                self._cache_snapshot(result, call_args)
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
            if hasattr(self.runtime, "aclose"):
                await self.runtime.aclose()


def create_server(driver=None, *, approvals=None, runtime=None, name="gamachineComputer", instructions_extra: str = "",
                  require_chat: bool = False):
    handler = ComputerServer(driver, approvals, runtime=runtime)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await handler.close()

    instructions = platform.current().instructions
    if instructions_extra:
        instructions += " " + instructions_extra
    mcp = MCPServer(name=name, lifespan=lifespan, instructions=instructions)

    def register(name):
        async def invoke(args: dict) -> CallToolResult:
            # agy shares one global config, so this server also starts for runs
            # outside Gamachine; without a chat id Stop and chat ownership cannot apply to them.
            if require_chat and approval_bridge._conversation_id_from_env() is None:
                return _text("The computer server needs a chat; this server was started without one.", True)
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
        if name not in platform.current().unsupported_tools:
            register(name)
    return mcp


def main():
    from unity_ai_mcp.server import _redirect_stderr_to_log
    _redirect_stderr_to_log()
    create_server(require_chat=True).run(transport="stdio")


if __name__ == "__main__":
    main()
