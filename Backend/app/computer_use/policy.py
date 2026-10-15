"""Pure validation of the curated cua-driver 0.35.4 surface."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit


HARD_DENY_BUNDLE_IDS = frozenset({
    "com.gamachine.app", "com.github.Electron", "com.trycua.driver",
    "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable",
    "com.mitchellh.ghostty", "net.kovidgoyal.kitty", "org.alacritty",
    "com.github.wez.wezterm", "dev.commandline.waveterm", "com.raphaelamorim.rio",
    "com.apple.systempreferences", "com.apple.Settings",
    "com.1password.1password", "com.bitwarden.desktop", "com.dashlane.dashlanephonefinal",
    "com.lastpass.LastPass", "com.nordsec.nordpass", "com.apple.Passwords",
    "com.apple.keychainaccess", "com.apple.ScriptEditor2", "com.apple.Automator",
    "com.apple.shortcuts", "com.apple.ScreenContinuity", "com.apple.SecurityAgent",
    "com.apple.LocalAuthentication.UIAgent",
})
_DENIED = frozenset(b.casefold() for b in HARD_DENY_BUNDLE_IDS)


def valid_bundle_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,254}", value) is not None


def is_hard_denied(bundle_id) -> bool:
    return isinstance(bundle_id, str) and bundle_id.casefold() in _DENIED


@dataclass(frozen=True)
class Tool:
    driver_tool: str | None
    allowed: frozenset[str]
    required: frozenset[str]


def _tool(driver, allowed, required=""):
    return Tool(driver, frozenset(allowed.split()), frozenset(required.split()))


# Explicitly transcribed from the pinned driver schemas; never discover tools
# dynamically, since a driver upgrade must not expand this permission boundary.
TOOLS = {
    "computer_list_apps": _tool("list_apps", ""),
    "computer_list_windows": _tool("list_windows", "pid on_screen_only", "pid"),
    "computer_window_state": _tool("get_window_state", "pid window_id include_accessibility_tree include_screenshot max_depth max_elements max_image_dimension max_dimension query since timeout_ms tree_format full_output verbose", "pid window_id"),
    "computer_zoom": _tool("zoom", "pid window_id x1 y1 x2 y2", "pid window_id x1 y1 x2 y2"),
    "computer_click": _tool("click", "action button capture_id count delivery_mode element_token from_zoom modifier pid window_id x y", "pid"),
    "computer_double_click": _tool("double_click", "delivery_mode element_token pid window_id x y", "pid"),
    "computer_right_click": _tool("right_click", "delivery_mode element_token modifier pid window_id x y", "pid"),
    "computer_scroll": _tool("scroll", "amount by delivery_mode direction element_token pid window_id x y", "pid direction"),
    "computer_drag": _tool("drag", "button delivery_mode duration_ms from_x from_y from_zoom modifier pid steps to_x to_y window_id", "pid from_x from_y to_x to_y"),
    "computer_type_text": _tool("type_text", "delay_ms delivery_mode element_token pid text window_id x y", "pid text"),
    "computer_set_value": _tool("set_value", "element_token pid value window_id", "pid value"),
    "computer_press_key": _tool("press_key", "delivery_mode element_token key modifiers pid window_id x y", "pid key"),
    "computer_hotkey": _tool("hotkey", "delivery_mode element_token keys pid window_id x y", "pid keys"),
    "computer_invoke_menu": _tool("invoke_menu", "path pid window_id", "pid window_id path"),
    "computer_launch_app": _tool("launch_app", "bundle_id creates_new_application_instance urls", "bundle_id"),
    "computer_clipboard_read": _tool("clipboard_read", "include_text"),
    "computer_clipboard_write": _tool("clipboard_write", "text", "text"),
    "computer_run_steps": _tool("run_steps", "pid window_id steps observe delay_ms delivery_mode foreground_fallback", "pid steps"),
    "computer_confirm_risky_step": _tool(None, "pid summary", "pid summary"),
}


@dataclass(frozen=True)
class Refusal:
    reason: str


@dataclass(frozen=True)
class Validated:
    driver_tool: str | None
    args: dict


STEP_TOOLS = frozenset("click double_click right_click set_value type_text press_key hotkey scroll drag".split())
_TARGET_KEYS = frozenset("role name nth timeout_ms".split())
_CHECK_KEYS = frozenset("role name text gone value value_contains enabled selected timeout_ms pid window_id".split())
# Numeric property names in the pinned schemas, including named step targets.
# `steps` is numeric only for drag, but a boolean is invalid for batches too.
_NUMERIC_KEYS = frozenset("pid window_id count x y x1 y1 x2 y2 from_x from_y to_x to_y duration_ms steps amount delay_ms max_depth max_elements max_image_dimension max_dimension timeout_ms nth".split())


def _keys(args, allowed, required):
    for key in args:
        if key not in allowed:
            return Refusal(f"Forbidden argument: {key}")
    for key in sorted(required):
        if key not in args:
            return Refusal(f"Missing required argument: {key}")
    if "pid" in args and (type(args["pid"]) is not int or args["pid"] <= 0):
        return Refusal("pid must be a positive integer")
    for key in _NUMERIC_KEYS.intersection(args):
        if isinstance(args[key], bool):
            return Refusal(f"{key} must not be a boolean")
    for key in ("keys", "modifiers"):
        if key in args and (not isinstance(args[key], list) or not all(isinstance(value, str) for value in args[key])):
            return Refusal(f"{key} must be a list of strings")
    if "key" in args and not isinstance(args["key"], str):
        return Refusal("key must be a string")


def _same_target(args, batch):
    for key in ("pid", "window_id"):
        if key in args and (key not in batch or type(args[key]) is not type(batch[key]) or args[key] != batch[key]):
            return Refusal(f"Step {key} must match batch {key}")


def _checks(value, batch, key):
    checks = value if isinstance(value, list) else [value]
    if len(checks) > 4:
        return Refusal(f"{key} permits at most 4 checks")
    for check in checks:
        if not isinstance(check, dict):
            return Refusal(f"{key} check must be an object")
        refusal = _keys(check, _CHECK_KEYS, ()) or _same_target(check, batch)
        if refusal:
            return refusal


def _steps(args):
    steps = args["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
        return Refusal("steps must be a list of 1..64 items")
    for step in steps:
        if not isinstance(step, dict):
            return Refusal("steps item must be an object")
        shorthand = STEP_TOOLS.intersection(step)
        if "tool" in step:
            tool = step["tool"]
            if not isinstance(tool, str) or tool not in STEP_TOOLS:
                return Refusal("Unsupported step tool")
            if shorthand:
                return Refusal("Step must contain exactly one tool")
            step_args = step.get("args", {})
        elif shorthand:
            if len(shorthand) != 1:
                return Refusal("Step must contain exactly one tool")
            tool = next(iter(shorthand))
            step_args = step[tool]
        else:
            tool = None
            step_args = {}
            refusal = _keys(step, {"wait_for", "expect", "timeout_ms"}, ())
            if refusal:
                return refusal
            if not ("wait_for" in step or "expect" in step):
                return Refusal("Step requires a tool, wait_for or expect")
        allowed = {"wait_for", "expect", "timeout_ms"}
        if tool:
            allowed.update({"tool", "args"} if "tool" in step else {tool})
        refusal = _keys(step, allowed, ())
        if refusal:
            return refusal
        if not isinstance(step_args, dict):
            return Refusal("Step args must be an object")
        if tool:
            spec = TOOLS["computer_" + tool]
            refusal = _keys(step_args, spec.allowed | _TARGET_KEYS, spec.required - {"pid"}) or _same_target(step_args, args)
            if refusal:
                return refusal
        for key in ("wait_for", "expect"):
            if key in step:
                refusal = _checks(step[key], args, key)
                if refusal:
                    return refusal
    if "observe" in args:
        observe = args["observe"]
        if not isinstance(observe, dict):
            return Refusal("observe must be an object")
        refusal = _keys(observe, TOOLS["computer_window_state"].allowed, ()) or _same_target(observe, args)
        if refusal:
            return refusal


def validate_call(tool, args) -> Validated | Refusal:
    if not isinstance(tool, str) or tool not in TOOLS:
        return Refusal("Unknown tool")
    if not isinstance(args, dict):
        return Refusal("args must be an object")
    spec = TOOLS[tool]
    refusal = _keys(args, spec.allowed, spec.required)
    if refusal:
        return refusal
    if tool == "computer_launch_app":
        if not valid_bundle_id(args["bundle_id"]):
            return Refusal("bundle_id must be a valid application identifier")
        if "urls" in args:
            urls = args["urls"]
            if not isinstance(urls, list) or not 1 <= len(urls) <= 16:
                return Refusal("urls must be a list of 1..16 strings")
            for url in urls:
                if not isinstance(url, str):
                    return Refusal("urls must contain strings")
                try:
                    allowed = url.startswith("/") or urlsplit(url).scheme.casefold() in {"http", "https", "file"}
                except ValueError:
                    allowed = False
                if not allowed:
                    return Refusal("urls must contain absolute paths or http, https, file URLs")
    if tool == "computer_clipboard_write" and not isinstance(args["text"], str):
        return Refusal("text must be a string")
    if tool == "computer_confirm_risky_step":
        summary = args["summary"]
        if not isinstance(summary, str) or not 1 <= len(summary) <= 500:
            return Refusal("summary must be a string of 1..500 characters")
    if tool == "computer_run_steps":
        refusal = _steps(args)
        if refusal:
            return refusal
    return Validated(spec.driver_tool, args)


_ALIASES = {"command": "cmd", "option": "opt", "alt": "opt", "control": "ctrl", "backspace": "delete", "escape": "esc"}
_RISKY = frozenset(frozenset(s.split("+")) for s in (
    "cmd+delete", "cmd+shift+delete", "cmd+opt+shift+delete", "cmd+opt+esc",
    "cmd+opt+shift+esc", "ctrl+cmd+q", "cmd+shift+q", "cmd+opt+shift+q"))


def risky_reason(tool, args) -> str:
    if tool == "computer_confirm_risky_step":
        return args.get("summary", "")
    if tool == "computer_run_steps":
        for step in args.get("steps", []):
            name = step.get("tool") or next((k for k in STEP_TOOLS if k in step), "")
            reason = risky_reason("computer_" + name, step.get("args", {}) if "tool" in step else step.get(name, {}))
            if reason:
                return reason
    if tool not in ("computer_hotkey", "computer_press_key"):
        return ""
    if tool == "computer_hotkey":
        keys = args.get("keys", [])
    else:
        modifiers = args.get("modifiers", [])
        if not isinstance(modifiers, list):
            return ""
        keys = [args.get("key", ""), *modifiers]
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        return ""
    normalized = frozenset(_ALIASES.get(k.casefold(), k.casefold()) for k in keys)
    return "Risky keyboard shortcut: " + "+".join(sorted(normalized)) if normalized in _RISKY else ""
