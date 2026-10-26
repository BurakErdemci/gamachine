"""Pure validation of the curated cua-driver 0.35.4 surface."""
from copy import deepcopy
from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import urlsplit

from . import platform
from .platform.macos import HARD_DENY_BUNDLE_IDS


CLIPBOARD_BUNDLE_ID = "com.gamachine.clipboard"


def valid_bundle_id(value) -> bool:
    return platform.current().valid_app_id(value)


def is_hard_denied(bundle_id) -> bool:
    return platform.current().is_hard_denied(bundle_id, None)


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
    "computer_bring_to_front": _tool("bring_to_front", "pid window_id", "pid"),
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


def _keys(args, allowed, required, *, batch=False):
    for key in args:
        if key not in allowed:
            if "element_token" in allowed and key in {"element", "element_index", "element_id", "element_ref", "index"}:
                return Refusal(
                    f'Forbidden argument: {key}. Use element_token: "<snapshot_id>:<row>" '
                    'from computer_window_state, e.g. row [39] of snapshot s00000029 is "s00000029:39".')
            return Refusal(f"Forbidden argument: {key}")
    for key in sorted(required):
        if key not in args:
            return Refusal(f"Missing required argument: {key}")
    if "pid" in args and (type(args["pid"]) is not int or args["pid"] <= 0):
        return Refusal("pid must be a positive integer")
    for key in _NUMERIC_KEYS.intersection(args):
        if batch and key == "delay_ms":
            continue  # The batch range check also rejects booleans.
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


def _step_keys(step, allowed):
    for key in step:
        if key not in allowed:
            return Refusal(
                f'Forbidden step key: {key}. A step is {{"tool": "type_text", "args": {{...}}}} '
                'or {"type_text": {...}}; tools: ' + ", ".join(sorted(STEP_TOOLS)))
    return _keys(step, allowed, ())


def _steps(args):
    if "delay_ms" in args and (type(args["delay_ms"]) is not int or not 0 <= args["delay_ms"] <= 2000):
        return Refusal("delay_ms must be an integer from 0 to 2000")
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
                value = tool if isinstance(tool, str) else repr(tool)[:40]
                reason = (
                    f"Unsupported step tool: {value}. Use one of: "
                    + ", ".join(sorted(STEP_TOOLS)) + " (no computer_ prefix)")
                if isinstance(tool, str) and tool.startswith("computer_") and tool[len("computer_"):] in STEP_TOOLS:
                    reason += f'. Did you mean "{tool[len("computer_"):]}"?'
                return Refusal(reason)
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
            refusal = _step_keys(step, {"wait_for", "expect", "timeout_ms"})
            if refusal:
                return refusal
            if not ("wait_for" in step or "expect" in step):
                return Refusal(
                    'Step requires a tool, wait_for or expect. A step is {"tool": "type_text", "args": {...}}')
        allowed = {"wait_for", "expect", "timeout_ms"}
        if tool:
            allowed.update({"tool", "args"} if "tool" in step else {tool})
        refusal = _step_keys(step, allowed)
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


def _valid_launch_url(url):
    if not isinstance(url, str) or not 1 <= len(url) <= 1024:
        return False
    if any(unicodedata.category(char) in ("Cc", "Cf") for char in url):
        return False
    if url.startswith("/"):
        return url == url.strip() and not any(part in (".", "..") for part in url.split("/"))
    if any(not "!" <= char <= "~" for char in url):
        return False
    try:
        parsed = urlsplit(url)
        return parsed.scheme.casefold() in {"http", "https"} and url.casefold().startswith(
            parsed.scheme.casefold() + "://") and bool(parsed.hostname)
    except ValueError:
        return False


def _split_keys(value):
    if value == "+":
        return [value]
    parts = [part.strip() for part in value.split("+") if part.strip()]
    return parts + (["+"] if value.endswith("++") else [])


def _keyboard_syntax(tool, args):
    original = args
    args = dict(args)
    for field in ("keys", "modifiers"):
        value = args.get(field)
        if isinstance(value, list):
            args[field] = [part for token in value for part in
                           (_split_keys(token) if isinstance(token, str) and len(token) > 1
                            and "+" in token else [token])]
    if tool == "hotkey":
        if isinstance(args.get("keys"), str):
            return {**args, "keys": _split_keys(args["keys"])}
        if "keys" not in args and isinstance(args.get("key"), str):
            modifiers = args.get("modifiers", [])
            if isinstance(modifiers, str):
                modifiers = _split_keys(modifiers)
            if isinstance(modifiers, list) and all(isinstance(value, str) for value in modifiers):
                # "key": "cmd+q" is one chord, so it gets the same risky-shortcut card; a lone "+" stays a key.
                normalized = {**args, "keys": modifiers + (_split_keys(args["key"]) or [args["key"]])}
                normalized.pop("key")
                normalized.pop("modifiers", None)
                return normalized
    elif tool == "press_key":
        if isinstance(args.get("modifiers"), str):
            args["modifiers"] = _split_keys(args["modifiers"])
        key = args.get("key")
        if isinstance(key, str) and len(key) > 1 and "+" in key:
            parts = _split_keys(key)
            modifiers = args.get("modifiers", [])
            if parts and isinstance(modifiers, list):
                args["key"] = parts[-1]
                args["modifiers"] = modifiers + parts[:-1]
    return original if args == original else args


def _normalized_keyboard_args(tool, args):
    return platform.current().normalize_keys(tool, _keyboard_syntax(tool, args))


def _keyboard_targets(tool, args):
    if tool in ("computer_hotkey", "computer_press_key"):
        yield tool[len("computer_"):], args
    elif tool == "computer_run_steps" and isinstance(args.get("steps"), list):
        for step in args["steps"]:
            if not isinstance(step, dict):
                continue
            shorthand = STEP_TOOLS.intersection(step)
            if "tool" in step:
                name = step["tool"]
                step_args = step.get("args")
                if shorthand:
                    continue
            elif len(shorthand) == 1:
                name = next(iter(shorthand))
                step_args = step[name]
            else:
                continue
            if name in ("hotkey", "press_key") and isinstance(step_args, dict):
                yield name, step_args


def _normalize_args(tool, args):
    observe_boolean = tool == "computer_run_steps" and type(args.get("observe")) is bool
    if not observe_boolean and not any(
            _normalized_keyboard_args(name, target) is not target
            for name, target in _keyboard_targets(tool, args)):
        return args
    args = deepcopy(args)
    for name, target in _keyboard_targets(tool, args):
        normalized = _normalized_keyboard_args(name, target)
        if normalized is not target:
            target.clear()
            target.update(normalized)
    if observe_boolean:
        if args["observe"]:
            args["observe"] = {}
        else:
            args.pop("observe")
    return args


def validate_call(tool, args) -> Validated | Refusal:
    if not isinstance(tool, str) or tool not in TOOLS:
        return Refusal("Unknown tool")
    if tool in platform.current().unsupported_tools:
        return Refusal("Bu araç bu işletim sisteminde henüz kullanılamıyor.")
    if not isinstance(args, dict):
        return Refusal("args must be an object")
    args = _normalize_args(tool, args)
    spec = TOOLS[tool]
    refusal = _keys(args, spec.allowed, spec.required, batch=tool == "computer_run_steps")
    if refusal:
        return refusal
    if tool == "computer_launch_app":
        if not valid_bundle_id(args["bundle_id"]):
            return Refusal("bundle_id must be a valid application identifier")
        if args["bundle_id"].casefold() == CLIPBOARD_BUNDLE_ID.casefold():
            return Refusal("bundle_id is reserved for the clipboard tool")
        if "urls" in args:
            urls = args["urls"]
            if not isinstance(urls, list) or not 1 <= len(urls) <= 16:
                return Refusal("urls must be a list of 1..16 strings")
            for url in urls:
                if not _valid_launch_url(url):
                    return Refusal("urls must be http(s) URLs with a host or absolute paths without control characters or dot segments")
    if tool == "computer_clipboard_read" and "include_text" in args and type(args["include_text"]) is not bool:
        return Refusal("include_text must be a boolean")
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
    for name, target in _keyboard_targets(tool, args):
        keys = target.get("keys", []) if name == "hotkey" else [target.get("key", ""), *target.get("modifiers", [])]
        reason = platform.current().refused_chord(
            frozenset(key.casefold() for key in keys))
        if reason:
            return Refusal(reason)
    return Validated(spec.driver_tool, args)


def risky_reason(tool, args) -> str:
    if tool == "computer_confirm_risky_step":
        return args.get("summary", "")
    if tool == "computer_run_steps":
        reasons = []
        for step in args.get("steps", []):
            name = step.get("tool") or next((k for k in STEP_TOOLS if k in step), "")
            reason = risky_reason("computer_" + name, step.get("args", {}) if "tool" in step else step.get(name, {}))
            if reason and reason not in reasons:
                reasons.append(reason)
        return " · ".join(reasons)
    if tool not in ("computer_hotkey", "computer_press_key"):
        return ""
    args = _normalized_keyboard_args(tool[len("computer_"):], args)
    if tool == "computer_hotkey":
        keys = args.get("keys", [])
    else:
        modifiers = args.get("modifiers", [])
        if not isinstance(modifiers, list):
            return ""
        keys = [args.get("key", ""), *modifiers]
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        return ""
    normalized = frozenset(k.casefold() for k in keys)
    return platform.current().risky_chord(normalized)


_INPUT_TOOLS = frozenset({
    "computer_click", "computer_double_click", "computer_right_click", "computer_type_text",
    "computer_set_value", "computer_press_key", "computer_hotkey", "computer_drag",
    "computer_clipboard_write",
})
_ELEMENT_TOOLS = frozenset({
    "computer_click", "computer_double_click", "computer_right_click", "computer_set_value",
    "computer_type_text", "computer_press_key",
})
_CRITICAL_NAME = re.compile(
    r"\b(?:send|gönder|delete|sil|remove|kaldır|buy|satın al|purchase|pay|öde|order|sipariş|"
    r"checkout|confirm|onayla|submit|publish|yayınla|post|paylaş|share|transfer|uninstall)\b",
    re.IGNORECASE | re.UNICODE,
)


def _normalized_element_name(name):
    name = unicodedata.normalize("NFKC", name)
    name = "".join(char for char in name if unicodedata.category(char) != "Cf")
    name = "".join((" " if index and name[index - 1].islower() and char.isupper() else "") + char
                   for index, char in enumerate(name))
    return name.translate(str.maketrans({"_": " ", "-": " ", ".": " "})).casefold()


def critical_reason(tool, args, *, app_id, resolve) -> str:
    risk = risky_reason(tool, args)
    if risk:
        return risk
    if tool == "computer_run_steps":
        for step in args.get("steps", []):
            name = step.get("tool") or next(
                (k for k in step if k in STEP_TOOLS or "computer_" + k in _INPUT_TOOLS), "")
            step_args = step.get("args", {}) if "tool" in step else step.get(name, {})
            target = {**{key: args[key] for key in ("pid", "window_id", "element_token") if key in args},
                      **step_args}
            reason = critical_reason("computer_" + name, target, app_id=app_id, resolve=resolve)
            if reason:
                return reason
        return ""
    if tool in _INPUT_TOOLS and platform.current().read_only_app(app_id):
        return "Tarayıcı, posta veya sohbet uygulamasına girdi gönderilecek."
    nested = args.get("target") if isinstance(args.get("target"), dict) else {}
    addressed = "element_token" in args or any(key in source for source in (args, nested) for key in ("name", "role"))
    if tool in _ELEMENT_TOOLS and addressed:
        known, name = resolve(args)
        if known is False:
            return "Hedef öğe doğrulanamadı; kritik işlem olabilir."
        if isinstance(name, str) and _CRITICAL_NAME.search(_normalized_element_name(name)):
            return "Kritik işlem düğmesi: " + name
    return ""
