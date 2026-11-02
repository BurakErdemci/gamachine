"""Rule-based risk classifier behind the "balanced" approval mode.

Owner decision (Burak, 27 Sep 2026): in balanced mode the AI works on its own
and only CRITICAL actions raise the approval card. The classification is
rule-based (no LLM), and anything the rules cannot prove routine is critical.

    classify(action) -> Risk(verdict, reason, detail)

`verdict` is "routine" or "critical"; `reason` is a stable code the UI maps to
text (the card shows "Kritik: <reason>"); `detail` names the program, path or
Unity tool.action behind it. Pure and deterministic apart from resolving paths
on the real file system (symlinks, 8.3 names), which is the point of it.

Owner decision (Burak, 2 Nov 2026, "BALANCED ÖLÇÜTÜ"): like computer use and
the browser, only irreversible or security-changing work gets a card. Shell
commands are therefore judged by a list of dangerous forms (delete/move,
network sends, installs, irreversible git, permissions, tool config paths,
leaving the workspace, control characters); any other program inside the
workspace is routine. Such a list was measured to be bypassable by spelling
(5 of 11 PowerShell forms, 27 Sep 2026); step mode keeps the safe list.

The fixed Unity file rules (unity_file_guard: .meta files, raw writes to Unity
YAML) run BEFORE this in every mode; nothing here relaxes them.

Standard library only: agy_step_gate imports this on every gated agy tool
call. `agentic` is a lazy package, so `agentic.command_safety` costs nothing
extra; the Unity ledger (unity_tool_policy) is imported only for Unity
actions, which never reach the agy hook.
"""
from __future__ import annotations

import ast
import glob
import ntpath
import os
import re
import shlex
from typing import Any, Iterable, Mapping, NamedTuple, Optional

from agentic import command_safety

ROUTINE = "routine"
CRITICAL = "critical"

KINDS = frozenset({
    "shell", "file_write", "file_delete", "file_move", "read", "read_outside",
    "unity", "mail", "permission", "blender_code", "other",
})


class Risk(NamedTuple):
    verdict: str
    reason: str
    detail: str = ""

    @property
    def critical(self) -> bool:
        return self.verdict != ROUTINE


def _routine(reason: str, detail: str = "") -> Risk:
    return Risk(ROUTINE, reason, detail)


def _critical(reason: str, detail: str = "") -> Risk:
    return Risk(CRITICAL, reason, _short(detail))


def _short(text: Any, limit: int = 160) -> str:
    text = "" if text is None else str(text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def classify(action: Any) -> Risk:
    """Never raises: a malformed action, or a bug below, is critical."""
    try:
        return _classify(_normalize(action))
    except Exception as exc:  # noqa: BLE001 - fail closed, whatever went wrong
        return _critical("unknown_action", f"{type(exc).__name__}")


def _normalize(action: Any) -> dict:
    if not isinstance(action, Mapping):
        raise TypeError("action is not a mapping")
    out = dict(action)
    paths = out.get("paths")
    if isinstance(paths, str):
        paths = [paths]
    out["paths"] = list(paths) if isinstance(paths, (list, tuple)) else []
    return out


def _classify(action: dict) -> Risk:
    kind = action.get("kind")
    workspace = action.get("workspace") or ""
    workspace = workspace if isinstance(workspace, str) else ""
    cwd = action.get("cwd") or ""
    cwd = cwd if isinstance(cwd, str) else ""
    if kind == "shell":
        return classify_shell(action.get("command"), cwd=cwd, workspace=workspace)
    if kind == "file_write":
        return _classify_write(action["paths"], cwd or workspace, workspace)
    if kind == "file_delete":
        return _critical("file_delete", _first(action["paths"]))
    if kind == "file_move":
        return _critical("file_move", _first(action["paths"]))
    if kind == "read":
        return _classify_read(action["paths"], cwd or workspace, workspace)
    if kind == "read_outside":
        return _critical("read_outside_workspace", _first(action["paths"]))
    if kind == "unity":
        return classify_unity(action.get("tool"), action.get("args"))
    if kind == "blender_code":
        return classify_blender_code(action.get("code"))
    if kind == "mail":
        # Owner decision (Burak, 27 Sep 2026): notes between chats are not critical.
        return _routine("mail")
    if kind == "permission":
        return _critical("permission_request", action.get("tool") or "")
    if kind == "other":
        return _classify_other(action.get("tool"))
    return _critical("unknown_action", action.get("tool") or kind or "")


# Claude's built-in read, agent and planning tools. Its write and shell tools map
# to file_write and shell before this; a built-in added later stays critical.
_CLAUDE_ROUTINE_TOOLS = frozenset({
    "Task", "Agent", "Skill", "WebFetch", "WebSearch", "Read", "Glob", "Grep", "LS",
    "TodoWrite", "TodoRead", "NotebookRead", "ToolSearch", "AskUserQuestion", "ExitPlanMode",
    "EnterPlanMode", "BashOutput", "KillShell", "ListMcpResourcesTool", "ReadMcpResourceTool",
})


def _classify_other(tool: Any) -> Risk:
    # An MCP tool reaching here belongs to a server Gamachine does not own (its
    # own servers are allowed or carded before the classifier).
    if isinstance(tool, str) and tool.startswith("mcp__"):
        return _critical("mcp_unknown_tool", tool)
    if tool in _CLAUDE_ROUTINE_TOOLS:
        return _routine("builtin_tool", tool)
    return _critical("unknown_action", tool if isinstance(tool, str) else "other")


def _first(paths: list) -> str:
    return str(paths[0]) if paths else ""


# ── Paths ────────────────────────────────────────────────────────────────────

# Agent and tool configuration directories: a write there can change what the
# agents (or their gates) do next, so it is critical wherever it sits.
_PROTECTED_DIRS = frozenset({
    ".git", ".claude", ".cursor", ".opencode", ".agents", ".gemini", ".codex", ".vscode",
    "projectsettings",
})
_PROTECTED_NAMES = frozenset({".mcp.json", "opencode.json"})
_PACKAGES_FILES = frozenset({"manifest.json", "packages-lock.json"})
# An NTFS 8.3 alias that realpath could not expand may name any protected entry.
_SHORT_NAME = re.compile(r"^[^~]{1,6}~\d+(\.[^.]{0,3})?$")


def _part(name: str) -> str:
    # Windows drops trailing dots and spaces and reads `x::$DATA` as x.
    return name.split(":", 1)[0].rstrip(" .").lower()


def _real(path: str) -> str:
    real = os.path.realpath(path)
    # Windows realpath answers with the extended prefix for some names
    # (measured: a "ProjectSettings. " component came back as \\?\C:\...).
    if real.startswith("\\\\?\\UNC\\"):
        real = "\\\\" + real[8:]
    elif real.startswith("\\\\?\\"):
        real = real[4:]
    return os.path.normcase(real)


def _resolve(path: Any, base: str) -> Optional[str]:
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return None
    p = os.path.expanduser(path.strip())
    if not os.path.isabs(p) or (os.name == "nt" and not ntpath.splitdrive(p)[0]
                                 and not p.startswith(("\\\\", "//"))):
        # Relative, or drive-relative/rooted on Windows ("\\x", "D:x"): only a
        # known base makes it mean anything.
        if not base:
            return None
        p = os.path.join(base, p)
    return _real(p)


def _relative_parts(path: Any, base: str, workspace: str) -> Optional[list]:
    """The parts of `path` below the workspace, or None when it is not inside."""
    if not workspace:
        return None
    root = _real(os.path.abspath(os.path.expanduser(workspace)))
    target = _resolve(path, base or workspace)
    if target is None:
        return None
    if target == root:
        return []
    prefix = root if root.endswith(os.sep) else root + os.sep
    if not target.startswith(prefix):
        return None
    return [p for p in re.split(r"[\\/]", target[len(prefix):]) if p]


def protected_reason(parts: list) -> Optional[str]:
    names = [_part(p) for p in parts]
    if not names:
        return "workspace root"
    # Other dot files (.gitignore, .editorconfig, .env) are ordinary workspace files.
    if any(n in _PROTECTED_DIRS for n in names) or names[-1] in _PROTECTED_NAMES:
        return "/".join(parts)
    if len(names) >= 2 and names[-2] == "packages" and names[-1] in _PACKAGES_FILES:
        return "/".join(parts)
    if any(_SHORT_NAME.match(n) for n in names):
        return "/".join(parts)
    return None


def _classify_write(paths: list, base: str, workspace: str) -> Risk:
    if not paths:
        return _critical("unknown_action", "file write without a path")
    for path in paths:
        parts = _relative_parts(path, base, workspace)
        if parts is None:
            return _critical("file_outside_workspace", path)
        if protected_reason(parts) is not None:
            return _critical("file_protected", path)
    return _routine("file_in_workspace", _first(paths))


def _classify_read(paths: list, base: str, workspace: str) -> Risk:
    for path in paths:
        if _relative_parts(path, base, workspace) is None:
            return _critical("read_outside_workspace", path)
    return _routine("read_in_workspace")


# ── Unity ────────────────────────────────────────────────────────────────────

UNITY_PREFIX = "mcp__unityMCP__"


# Owner decision (2 Nov 2026): these writes are critical in the shared ledger
# (tool_actions.json, which step mode and the Unity server also read) but are
# not destructive, so balanced runs them without a card. Deletes, removes,
# moves, renames, code, packages, dialogs and Save As stay critical.
_UNITY_BALANCED_ROUTINE = {
    "manage_scene": frozenset({"create", "load", "close_scene"}),
    "manage_build": frozenset({"build", "batch", "cancel", "platform", "profiles",
                                "settings", "scenes"}),
    "manage_physics": frozenset({"set_settings", "set_collision_matrix"}),
    "manage_graphics": frozenset({"pipeline_set_quality", "pipeline_set_settings"}),
}


# Owner rule (2 Nov 2026): whatever the ledger says, an action that removes,
# deletes or destroys something is irreversible, on every Unity tool. clear* is not: it resets logs and
# transient state (read_console clear, particle_clear); tool_actions.json still marks the destructive ones.
_UNITY_DESTRUCTIVE = re.compile(r"(?:^|_)(?:remove|delete|destroy)")


def _unity_destructive(args: Mapping) -> bool:
    return any(isinstance(k, str) and k.replace("_", "").lower() == "action" and isinstance(v, str)
               and _UNITY_DESTRUCTIVE.search(v.lower()) for k, v in args.items())


def _unity_balanced_routine(bare: str, args: Mapping) -> bool:
    allowed = _UNITY_BALANCED_ROUTINE.get(bare)
    if not allowed:
        return False
    # One spelling of the action key only: the server folds case and underscores.
    keys = [k for k in args if isinstance(k, str) and k.replace("_", "").lower() == "action"]
    if keys != ["action"]:
        return False
    action = args["action"]
    return isinstance(action, str) and action.lower() in allowed


def classify_unity(tool: Any, args: Any) -> Risk:
    if not isinstance(tool, str) or not tool:
        return _critical("unity_unknown_tool", "")
    bare = tool[len(UNITY_PREFIX):] if tool.startswith(UNITY_PREFIX) else tool
    if args is None:
        args = {}
    if not isinstance(args, Mapping):
        return _critical("unity_unknown_tool", bare)
    import unity_tool_policy  # stdlib-only, loads the ledger by path
    risk = unity_tool_policy.unity_action_risk(bare, args)
    detail = bare
    action = args.get("action")
    if isinstance(action, str) and action:
        detail = f"{bare}.{action}"
    if risk in ("read", "routine") and _unity_destructive(args):
        return _critical("unity_critical_action", detail)
    if risk == "read":
        return _routine("unity_read", detail)
    if risk == "routine" or (risk == "critical" and _unity_balanced_routine(bare, args)):
        return _routine("unity_routine_write", detail)
    if risk == "unknown":
        return _critical("unity_unknown_tool", detail)
    return _critical("unity_critical_action", detail)


# ── Blender code ─────────────────────────────────────────────────────────────

# Owner decision (Burak, 2 Nov 2026): in balanced mode ordinary scene building
# (objects, materials, modifiers, single deletes, selections) runs with no card;
# only code that reaches outside the scene asks. This reads the code, it does
# not sandbox it: getattr on bpy objects with names the code builds at run time
# can still hide intent, and the card in step mode is the stronger guard.
_BLENDER_SYSTEM_MODULES = frozenset({
    "os", "sys", "subprocess", "shutil", "socket", "ctypes", "urllib", "http", "requests",
    "pathlib", "importlib", "multiprocessing", "threading", "webbrowser",
})
# A safe list, as for shell commands: an import outside it is critical, since a
# list of dangerous modules always misses one (io, pickle, tempfile, runpy, nt...).
# addon_utils stays: upstream's rigging guide enables Rigify with it. operator
# (attrgetter/methodcaller), functools, dataclasses and string (Formatter) reach
# attributes by run-time names, so they are not on it.
_BLENDER_MODULES = frozenset({
    "bpy", "bpy_extras", "bmesh", "mathutils", "bl_math", "addon_utils", "math", "cmath",
    "random", "colorsys", "collections", "itertools", "statistics",
    "typing", "enum", "copy", "re", "json", "time", "datetime",
    "decimal", "fractions", "heapq", "bisect", "textwrap", "numpy",
})
_BLENDER_BUILTINS = {
    "open": "file access", "exec": "runs code", "eval": "runs code", "compile": "runs code",
    "__import__": "dynamic import", "globals": "namespace access", "locals": "namespace access",
    "vars": "namespace access", "breakpoint": "runs code",
}
# Allowed only as a direct call on a bpy object with a literal attribute name.
_BLENDER_ATTR_BUILTINS = frozenset({"getattr", "setattr", "delattr"})
_BLENDER_INTERNAL_ATTRS = frozenset({
    "f_globals", "f_locals", "f_builtins", "f_back", "gi_frame", "cr_frame", "ag_frame",
    "tb_frame", "tb_next", "gi_code", "co_code", "func_globals",
})
# Methods that write a file wherever they are called (Image.save/save_render/unpack,
# ndarray.tofile) or run a text block as a module.
_BLENDER_WRITE_METHODS = frozenset({"save", "save_render", "unpack", "tofile"})
_NUMPY_FILE_FUNCS = frozenset({
    "save", "savez", "savez_compressed", "load", "loadtxt", "savetxt", "genfromtxt",
    "fromfile", "memmap", "fromregex", "DataSource",
})
_BLENDER_PREFIXES = (
    ("bpy.ops.wm", "window-manager operator"),
    ("bpy.ops.preferences", "preferences"),
    ("bpy.context.preferences", "preferences"),
    ("bpy.ops.script", "script operator"),
    ("bpy.ops.extensions", "extensions operator"),
    ("bpy.ops.file", "file operator"),
    ("bpy.ops.text.run_script", "runs a text block"),
    ("bpy.ops.outliner.orphans_purge", "mass delete"),
    ("bpy.data.orphans_purge", "mass delete"),
    ("bpy.data.batch_remove", "mass delete"),
    ("bpy.data.libraries.write", "library file access"),
    ("bpy.data.libraries.load", "library file access"),
    ("bpy.data.texts.load", "file read"),
    ("bpy.data.images.load", "file read"),
    ("bpy.data.fonts.load", "file read"),
    ("bpy.data.sounds.load", "file read"),
    ("bpy.data.movieclips.load", "file read"),
    ("bpy.data.volumes.load", "file read"),
    ("bpy.ops.sequencer.render_animation", "file write"),
    ("bpy.ops.ptcache", "cache on disk"),
    ("bpy.ops.fluid.bake", "cache on disk"),
    ("bpy.ops.dynamicpaint.bake", "cache on disk"),
    ("bpy.ops.sound.mixdown", "file write"),
    ("bpy.ops.console", "runs code"),
    ("bpy.app.handlers", "handlers or timers"),
    ("bpy.app.timers", "handlers or timers"),
    ("bpy.utils.execfile", "runs a file"),
    ("bpy.utils.load_scripts", "runs scripts"),
)
_LOOPS = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp,
          ast.GeneratorExp)
_DATA_REMOVE = re.compile(r"bpy\.data\.[^.]+\.remove")
_OBJECT_DELETE = frozenset({"bpy.ops.object.delete", "bpy.ops.outliner.delete"})
_RENDER_OPS = frozenset({"bpy.ops.render.render", "bpy.ops.render.opengl"})
_BAKE_OPS = frozenset({"bpy.ops.object.bake", "bpy.ops.object.bake_image"})
_BLENDER_MAX_CHARS = 200_000


def _dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + ".")


def _blender_path_reason(path: str) -> Optional[str]:
    segs = path.split(".")
    if segs[0] in _BLENDER_SYSTEM_MODULES:
        return "system module"
    if segs[0] == "numpy" and len(segs) > 1 and segs[1] in _NUMPY_FILE_FUNCS:
        return "file access"
    for prefix, what in _BLENDER_PREFIXES:
        if _under(path, prefix):
            return what
    if len(segs) >= 3 and segs[:2] == ["bpy", "ops"]:
        group, op = segs[2], (segs[3] if len(segs) > 3 else "")
        # "open" or "open_*", not "opengl" (a viewport render that writes nothing by itself).
        if group.startswith(("export_", "import_")) or op.startswith(("export", "import", "open_")) or op == "open":
            return "file operator"
        if group == "fluid" and op.startswith("bake"):
            return "cache on disk"
        if op.startswith("save"):
            return "file write"
    if len(segs) >= 3 and segs[:2] == ["bpy", "utils"] and segs[2].startswith(("register", "unregister")):
        return "registers code"
    return None


class _BlenderScan(ast.NodeVisitor):
    """Collects every critical finding with its position; the first one wins."""

    def __init__(self, aliases: dict, parents: dict):
        self.aliases, self.parents = aliases, parents
        self.found: list = []
        self.loops = 0
        self.mass_select: list = []
        self.deletes: list = []

    def flag(self, node, what: str, name: str) -> None:
        self.found.append(((getattr(node, "lineno", 0), getattr(node, "col_offset", 0)), what, name))

    def chains(self, node) -> set:
        return _blender_chains(node, self.aliases)

    def generic_visit(self, node):
        loop = isinstance(node, _LOOPS)
        self.loops += loop
        super().generic_visit(node)
        self.loops -= loop

    def visit_Import(self, node):
        for alias in node.names:
            self._module(node, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.level or not node.module:
            self.flag(node, "relative import", node.module or ".")
        else:
            self._module(node, node.module)
            for alias in node.names:
                if alias.name == "*":
                    self.flag(node, "star import", node.module)
                elif (why := _blender_path_reason(f"{node.module}.{alias.name}")) is not None:
                    self.flag(node, why, f"{node.module}.{alias.name}")
        self.generic_visit(node)

    def _module(self, node, name: str) -> None:
        root = name.split(".")[0]
        if root in _BLENDER_SYSTEM_MODULES:
            self.flag(node, "system module", name)
        elif root not in _BLENDER_MODULES:
            self.flag(node, "module outside the scene-building list", name)

    def visit_Name(self, node):
        if node.id in _BLENDER_BUILTINS:
            self.flag(node, _BLENDER_BUILTINS[node.id], node.id)
        elif _dunder(node.id):
            self.flag(node, "dunder name", node.id)
        elif node.id in _BLENDER_SYSTEM_MODULES:
            self.flag(node, "system module", node.id)
        elif node.id in _BLENDER_ATTR_BUILTINS and not self._bpy_attr_call(node):
            self.flag(node, "dynamic attribute access", node.id)
        self.generic_visit(node)

    def _bpy_attr_call(self, node) -> bool:
        call = self.parents.get(node)
        if not isinstance(call, ast.Call) or call.func is not node or len(call.args) < 2:
            return False
        name = call.args[1]
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)) or _dunder(name.value):
            return False
        roots = self.chains(call.args[0])
        if not roots or not all(r == "bpy" or r.startswith("bpy.") for r in roots):
            return False
        for root in roots:
            if (why := _blender_path_reason(f"{root}.{name.value}")) is not None:
                self.flag(call, why, f"{root}.{name.value}")
        return True

    def visit_Constant(self, node):
        # getattr(x, "__cla" + "ss__") and friends: a dunder name assembled from strings.
        if isinstance(node.value, str) and "__" in node.value:
            self.flag(node, "dunder string", node.value)

    def visit_Attribute(self, node):
        if _dunder(node.attr):
            self.flag(node, "dunder attribute", node.attr)
        elif node.attr in _BLENDER_INTERNAL_ATTRS:
            self.flag(node, "interpreter internals", node.attr)
        elif node.attr in _BLENDER_SYSTEM_MODULES:
            self.flag(node, "system module", node.attr)
        elif node.attr == "as_module":
            self.flag(node, "runs a text block", node.attr)
        parent = self.parents.get(node)
        called = isinstance(parent, ast.Call) and parent.func is node
        if called and node.attr in _BLENDER_WRITE_METHODS:
            self.flag(node, "file write", node.attr)
        if not (isinstance(parent, ast.Attribute) and parent.value is node):
            for path in self.chains(node):
                if (why := _blender_path_reason(path)) is not None:
                    self.flag(node, why, path)
                if _DATA_REMOVE.fullmatch(path) and (self.loops or not called):
                    self.flag(node, "removes data in a loop", path)
                if called:
                    self._op_call(parent, path)
        self.generic_visit(node)

    def _op_call(self, call, path: str) -> None:
        pos = (call.lineno, call.col_offset)
        if path.startswith("bpy.ops."):
            # A render with animation=True writes every frame to the output path.
            writes = {"write_still"} | ({"animation"} if path in _RENDER_OPS else set())
            for kw in call.keywords:
                if kw.arg in writes and not (isinstance(kw.value, ast.Constant) and not kw.value.value):
                    self.flag(call, "file write", f"{path}({kw.arg})")
                elif (path in _BAKE_OPS and kw.arg == "save_mode"
                      and not (isinstance(kw.value, ast.Constant) and kw.value.value == "INTERNAL")):
                    self.flag(call, "file write", f"{path}(save_mode)")
        if path in _OBJECT_DELETE:
            if self.loops:
                self.flag(call, "removes data in a loop", path)
            self.deletes.append((pos, path))
        elif path.startswith("bpy.ops.object.select_"):
            action = next((kw.value for kw in call.keywords if kw.arg == "action"), None)
            if not (path == "bpy.ops.object.select_all" and isinstance(action, ast.Constant)
                    and action.value == "DESELECT"):
                self.mass_select.append(pos)
        elif path.endswith(".select_set") and self.loops:
            self.mass_select.append(pos)


def _blender_chains(node, aliases: dict) -> set:
    """Dotted paths `node` may name, with import and assignment aliases expanded."""
    parts = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Subscript):
            parts.append("[]")
            node = node.value
        elif isinstance(node, ast.Call):
            parts.append("()")
            node = node.func
        elif isinstance(node, ast.Name):
            tail = ".".join(reversed(parts))
            return {f"{root}.{tail}" if tail else root for root in aliases.get(node.id, {node.id})}
        else:
            return set()


def _blender_aliases(tree) -> dict:
    """name -> the dotted paths it is bound to by imports, assignments and loop targets."""
    aliases: dict = {}

    def bind(name: str, paths) -> bool:
        before = aliases.get(name)
        aliases[name] = (before or set()) | set(paths)
        return aliases[name] != before

    for _ in range(5):
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        changed |= bind(a.asname, {a.name})
                    else:
                        changed |= bind(a.name.split(".")[0], {a.name.split(".")[0]})
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for a in node.names:
                    changed |= bind(a.asname or a.name, {f"{node.module}.{a.name}"})
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                paths = _blender_chains(node.value, aliases)
                for target in targets:
                    if isinstance(target, ast.Name) and paths:
                        changed |= bind(target.id, paths)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                paths = {p + ".[]" for p in _blender_chains(node.iter, aliases)}
                if isinstance(node.target, ast.Name) and paths:
                    changed |= bind(node.target.id, paths)
        if not changed:
            break
    return aliases


def classify_blender_code(code: Any) -> Risk:
    """execute_blender_code's code: critical when it reaches files, programs,
    the network, the open .blend, Blender's own code hooks, or removes data en
    masse; everything else (bpy scene building, maths) is routine."""
    if not isinstance(code, str):
        return _critical("blender_code: no code", type(code).__name__)
    if len(code) > _BLENDER_MAX_CHARS:
        return _critical("blender_code: too long to review", f"{len(code)} characters")
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return _critical("blender_code: unparsable", type(exc).__name__)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    scan = _BlenderScan(_blender_aliases(tree), parents)
    scan.visit(tree)
    for pos, path in scan.deletes:
        if any(sel < pos for sel in scan.mass_select):
            scan.flag(ast.Pass(lineno=pos[0], col_offset=pos[1]), "mass delete after select all", path)
    if scan.found:
        _, what, name = min(scan.found, key=lambda f: f[0])
        return _critical(f"blender_code: {what} ({_short(name, 60)})", name)
    return _routine("blender_code")


# ── Shell ────────────────────────────────────────────────────────────────────

# The only characters a build/test allow-list command may hold. Allowed, not
# filtered: quotes, $ ` ; & | < > ( ) { } @ # , % ! ^ * ? [ ] ~ and anything
# typographic change what a shell runs or how it splits the words, in one of
# bash, cmd.exe or PowerShell.
_TURKISH_LETTERS = "çÇğĞıİöÖşŞüÜâÂîÎûÛ"
_BUILD_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-./\\:=+"
    + _TURKISH_LETTERS)

_DOTNET_COMMON_VALUE = {"-c", "--configuration", "-f", "--framework", "-v", "--verbosity"}
_DOTNET_COMMON_FLAGS = {"--nologo", "--no-restore", "--no-dependencies"}
_DOTNET = {
    "build": (_DOTNET_COMMON_VALUE, _DOTNET_COMMON_FLAGS | {"--no-incremental"}),
    "test": (_DOTNET_COMMON_VALUE | {"--filter"}, _DOTNET_COMMON_FLAGS | {"--no-build"}),
    # Restore only from the lock file: without it a restore resolves versions
    # from the network.
    "restore": ({"-v", "--verbosity"}, {"--locked-mode", "--nologo"}),
}
_NPM_SCRIPTS = {"build", "test", "lint"}
_TSC_VALUE = {"-p", "--project"}
_TSC_FLAGS = {"--noemit", "--pretty", "--skiplibcheck"}
# pytest flags that only select, report or stop; --basetemp (deletes its
# directory), --junitxml and plugin loading are deliberately missing.
_PYTEST_VALUE = {"-k", "-m", "--tb", "--maxfail", "--durations"}
_PYTEST_FLAGS = {"-q", "-v", "-vv", "-x", "-s", "-l", "--quiet", "--verbose", "--exitfirst",
                 "--lf", "--last-failed", "--ff", "--failed-first", "--co", "--collect-only",
                 "--no-header", "--disable-warnings", "--showlocals"}
_PYTHONS = {"python", "python3", "py"}
_GIT_READS = {"status", "log", "diff", "show", "branch", "remote", "stash", "fetch"}


def _program(token: str) -> Optional[str]:
    """Bare program name, lower case; None for anything with a path in it."""
    if not token or re.search(r"[\\/:]", token):
        return None
    name = token.lower()
    for ext in (".exe", ".cmd"):
        if name.endswith(ext):
            name = name[:-len(ext)]
    return name


def _flags_ok(tokens: list, value_flags: set, flags: set, *, positional: bool) -> bool:
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        if low.startswith("-"):
            name, eq, _ = low.partition("=")
            if name in value_flags:
                if eq:
                    i += 1
                    continue
                if i + 1 >= len(tokens) or tokens[i + 1].startswith("-"):
                    return False
                i += 2
                continue
            if low in flags and not eq:
                i += 1
                continue
            return False
        if not positional:
            return False
        i += 1
    return True


def _pytest_args_ok(tokens: list) -> bool:
    rest = []
    i = 0
    while i < len(tokens):
        # `-p no:<plugin>` only turns a plugin off; any other -p loads code.
        if tokens[i] == "-p" and i + 1 < len(tokens) and tokens[i + 1].startswith("no:"):
            i += 2
            continue
        if re.fullmatch(r"-r[a-zA-Z]+", tokens[i]):
            i += 1
            continue
        rest.append(tokens[i])
        i += 1
    return _flags_ok(rest, _PYTEST_VALUE, _PYTEST_FLAGS, positional=True)


def _build_form(tokens: list) -> Optional[str]:
    """The allow-list entry `tokens` matches exactly, else None."""
    head = _program(tokens[0])
    args = tokens[1:]
    if head == "dotnet" and args:
        spec = _DOTNET.get(args[0].lower())
        if spec is None:
            return None
        if args[0].lower() == "restore" and "--locked-mode" not in [a.lower() for a in args]:
            return None
        return f"dotnet {args[0].lower()}" if _flags_ok(args[1:], *spec, positional=True) else None
    if head == "npm" and args:
        if args[0] == "test":
            rest = args[1:]
            form = "npm test"
        elif args[0] == "run" and len(args) >= 2 and args[1] in _NPM_SCRIPTS:
            rest = args[2:]
            form = f"npm run {args[1]}"
        else:
            return None
        # Anything after `--` goes to the project's own script.
        return form if not rest or rest[0] == "--" else None
    if head == "npx" and args:
        if args[0] in ("--no-install", "--no"):
            args = args[1:]
        if not args or args[0] != "tsc":
            return None
        low = [a.lower() for a in args[1:]]
        if "--noemit" not in low:
            return None
        return "npx tsc --noEmit" if _flags_ok(args[1:], _TSC_VALUE, _TSC_FLAGS, positional=False) else None
    if head == "pytest":
        return "pytest" if _pytest_args_ok(args) else None
    if head in _PYTHONS and len(args) >= 2 and args[0] == "-m" and args[1] == "pytest":
        return "python -m pytest" if _pytest_args_ok(args[2:]) else None
    return None


def _confinement_root(cwd: str, workspace: str) -> "tuple[Optional[str], bool]":
    """(directory relative arguments resolve against, cwd is acceptable)."""
    if not workspace:
        return (cwd or None), not cwd
    if not cwd:
        return workspace, True
    if _relative_parts(cwd, workspace, workspace) is None:
        return None, False
    return _resolve(cwd, workspace), True


def classify_shell(command: Any, *, cwd: str = "", workspace: str = "", _depth: int = 0) -> Risk:
    if not isinstance(command, str) or not command.strip():
        return _critical("shell_unparseable", "")
    if _depth > 3:
        return _critical("shell_inline_code", "nested too deep")
    root, cwd_ok = _confinement_root(cwd, workspace)
    raw = command.strip()
    tokens = command_safety._tokenize(raw)
    # Bash expands `{..,x}/secret` to `../secret`; the safe list reads one name.
    if tokens and root and _brace_escapes(tokens, root):
        return _critical("shell_outside_workspace", _verb(tokens[0]))
    if cwd_ok and command_safety.is_auto_safe(command, root):
        return _routine("shell_safe_list", raw.split()[0])
    if cwd_ok and all(ch in _BUILD_CHARS for ch in raw):
        if tokens:
            form = _build_form(tokens)
            # Build/test programs are native executables that take their
            # arguments literally (`-p no:cacheprovider`, `test_x.py::name`);
            # only PowerShell's own cmdlets read `Name:` as a provider.
            if form and command_safety._stays_in_workspace(tokens, root, native=True):
                return _routine("shell_build_allowlist", form)
    return _shell_reason(raw, tokens, cwd_ok, root, cwd, workspace, _depth)


# Owner decision (Burak, 2 Nov 2026, "BALANCED ÖLÇÜTÜ"): like computer use and
# the browser, a card only for irreversible or security-changing work. A shell
# command is critical when it deletes or moves, sends over the network, installs,
# rewrites git history or the remote, widens permissions, touches a tool config
# path, leaves the workspace, or chains with control characters; any other
# program inside the workspace runs. This is a danger list, so it can be
# bypassed by spelling (measured 27 Sep 2026); the owner accepted that for
# balanced, and step mode keeps the safe list.
_WORDS = re.compile(r"[\s;|&(){}<>`,]+")
_INNER_WORDS = re.compile(r"[\s;|&(){}<>`,\[\]=+:]+")
_DELETE_MOVE = frozenset({
    "rm", "del", "erase", "rmdir", "rd", "unlink", "shred", "rimraf", "remove-item", "ri",
    "mv", "move", "ren", "rename", "move-item", "mi", "rename-item", "rni", "clear-content",
    "clc", "clear-item", "cli", "diskpart", "cipher", "sdelete",
})
_FIND_DESTRUCTIVE = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir"})
_ROBOCOPY_DESTRUCTIVE = frozenset({"/mir", "/purge", "/mov", "/move"})
_POWERSHELLS = frozenset({"powershell", "pwsh", "powershell_ise"})
_POSIX_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_POSIX_SYSTEM_DIRS = ("/bin/", "/usr/bin/", "/usr/local/bin/")
_CODE_INTERPRETERS = frozenset({
    "python", "python3", "py", "node", "deno", "bun", "perl", "ruby", "php",
})
_SCRIPT_HOSTS = frozenset({"cmd", "wscript", "cscript", "mshta"})
_INTERPRETERS = _POWERSHELLS | _POSIX_SHELLS | _CODE_INTERPRETERS | _SCRIPT_HOSTS
_INLINE_FLAGS = frozenset({
    "-c", "-e", "-p", "-r", "-command", "-encodedcommand", "-enc", "-ec", "-en", "-encoded",
    "/c", "/k", "/r", "--eval", "--print", "--command",
})
_INLINE_VERBS = frozenset({
    "iex", "invoke-expression", "invoke-command", "icm", "start-process", "saps",
    "rundll32", "regsvr32", "msiexec", "schtasks", "set-executionpolicy", "add-type",
    "invoke-item",
})
_NETWORK = frozenset({
    "start-bitstransfer", "certutil", "bitsadmin", "scp", "sftp", "ftp", "ssh", "nc", "ncat",
    "telnet", "rsync", "net.webclient", "system.net.webclient", "send-mailmessage", "gh", "twine",
})
# Downloaders: critical only when they send data or upload.
_HTTP_CLIENTS = frozenset({"curl", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod"})
_CURL_SEND = frozenset({"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
                        "--data-ascii", "-F", "--form", "--form-string", "-T", "--upload-file",
                        "--json"})
_WGET_SEND = ("--post-data", "--post-file", "--body-data", "--body-file")
_READ_METHODS = frozenset({"get", "head", "options"})
_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun", "pip", "pip3", "pipx", "uv",
                               "gem", "cargo", "go", "composer", "nuget"})
_INSTALL_SUBS = frozenset({
    "install", "i", "ci", "add", "update", "up", "upgrade", "uninstall", "remove", "rm", "un",
    "link", "publish", "exec", "x", "dlx", "get", "sync", "restore", "tool",
})
_SYSTEM_INSTALLERS = frozenset({
    "winget", "choco", "scoop", "apt", "apt-get", "brew", "dnf", "yum", "pacman", "snap",
    "install-module", "install-package", "install-script", "npx", "pnpx", "dotnet-install",
})
_PERMISSION = frozenset({
    "chmod", "chown", "chgrp", "icacls", "cacls", "takeown", "set-acl", "sudo", "runas",
    "gsudo", "netsh", "reg", "setx",
})
# git global options that take the next word as their value.
_GIT_VALUE_OPTIONS = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace",
                                "--config-env", "--exec-path"})
# Options that make git run another program.
_GIT_RUNS_PROGRAMS = ("-c", "--config-env", "--exec-path", "--upload-pack", "--receive-pack")
_GIT_HISTORY = frozenset({"rebase", "filter-branch", "filter-repo", "update-ref", "prune",
                          "send-email"})
# Inline code: language-level delete, network and outside-path forms.
_INLINE_API = re.compile(
    r"\bos\.(?:remove|unlink|rmdir|removedirs|rename|replace)\b|\bshutil\.(?:rmtree|move)\b"
    r"|\.(?:unlink|rmdir)\s*\(|\b(?:rmtree|rmSync|unlinkSync|rmdirSync|renameSync|send2trash)\b"
    r"|\bfs\.(?:promises\.)?(?:rm|unlink|rmdir|rename)\b|\b(?:File|Directory)\.(?:Delete|Move)\b"
    r"|\bunlink\s*\(|\burllib\b|\burlopen\b|\brequests\.|\bhttp\.client\b|\bhttpx\b|\baiohttp\b"
    r"|\bsocket\b|\bsmtplib\b|\bftplib\b|\bparamiko\b|\bfetch\s*\(|\bXMLHttpRequest\b"
    r"|\bWebSocket\b|\bhttps?\.(?:get|request)\b|\bnet\.(?:connect|createConnection)\b"
    r"|\bLWP\b|\bWebClient\b|\bhttps?://|\bensurepip\b|\bpip\.main\b",
    re.IGNORECASE)
_INLINE_OUTSIDE = re.compile(
    r"(?:^|[\s'\"(=,\[])(?:[A-Za-z]:[\\/]|\\\\|//|~[\\/]|/(?![/\s*])|\.\.(?:[\\/]|['\"]|$))")
_URL = re.compile(r"^https?://", re.IGNORECASE)
# PowerShell resolves these to provider cmdlets, where `Name:` is a drive.
_PS_PROVIDER_HEADS = frozenset({
    "ls", "ll", "la", "cat", "echo", "pwd", "diff", "dir", "gci", "gc", "type", "sc", "ac",
    "ni", "md", "mkdir", "copy", "cp", "cpi", "cd", "sl", "tee", "sp", "gp", "ii", "write",
})
_BRACE = re.compile(r"\{([^{}]*,[^{}]*)\}")
_BRACE_MAX = 64


def _verb(word: str) -> str:
    name = re.split(r"[\\/]", word.strip("'\"“”‘’"))[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _brace_variants(token: str) -> Optional[list]:
    """Every word bash makes of `token`'s comma braces; None past _BRACE_MAX."""
    out, todo = [], [token]
    while todo:
        word = todo.pop()
        m = _BRACE.search(word)
        if m is None:
            out.append(word)
        else:
            todo.extend(word[:m.start()] + alt + word[m.end():] for alt in m.group(1).split(","))
        if len(out) + len(todo) > _BRACE_MAX:
            return None
    return out


def _brace_escapes(tokens: list, root: str) -> bool:
    for token in tokens[1:]:
        if "{" not in token:
            continue
        variants = _brace_variants(token)
        if variants is None or not command_safety._stays_in_workspace([tokens[0]] + variants, root):
            return True
    return False


def _unquoted(raw: str) -> Optional[str]:
    """`raw` without the inside of '...' and "..." strings; None when a quote is open."""
    out, quote = [], None
    for ch in raw:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        else:
            out.append(ch)
    return None if quote else "".join(out)


def _trigger(words: list, *, inline: bool = False) -> Optional[Risk]:
    """The delete, network, install, permission or git-history trigger in `words`.
    In inline code any download counts, not only a send."""
    lows = [w.strip("'\"“”‘’") for w in words]
    verbs = [_verb(w) for w in words]
    for i, v in enumerate(verbs):
        if v in _DELETE_MOVE and not (v == "mv" and i and verbs[i - 1] == "git"):
            return _critical("shell_delete_move", v)
        if v == "find" and any(w.lower() in _FIND_DESTRUCTIVE for w in lows[i + 1:]):
            return _critical("shell_delete_move", "find")
        if v == "robocopy" and any(w.lower() in _ROBOCOPY_DESTRUCTIVE for w in lows[i + 1:]):
            return _critical("shell_delete_move", "robocopy")
        if v == "pytest" and any(w.lower().startswith("--basetemp") for w in lows[i + 1:]):
            # pytest empties its --basetemp directory before the run.
            return _critical("shell_delete_move", "pytest --basetemp")
    for v in verbs:
        if v in _INLINE_VERBS:
            return _critical("shell_inline_code", v)
    for i, (v, w) in enumerate(zip(verbs, lows)):
        if v in _NETWORK or "net.webclient" in w.lower():
            return _critical("shell_network", v)
        if v in _HTTP_CLIENTS and (inline or _sends(v, lows[i + 1:])):
            return _critical("shell_network", v)
        if inline and _URL.match(w):
            return _critical("shell_network", w)
        if v in ("dotnet", "msbuild") and any(re.search(r"https?://", a, re.I) for a in lows[i + 1:]):
            # -p:RestoreSources=<url> and the like pull packages from that host.
            return _critical("shell_network", v)
    for i, v in enumerate(verbs):
        nxt = verbs[i + 1] if i + 1 < len(verbs) else ""
        if v in _SYSTEM_INSTALLERS:
            return _critical("shell_installer", v)
        if v in _PACKAGE_MANAGERS and nxt in _INSTALL_SUBS:
            return _critical("shell_installer", f"{v} {nxt}")
        if v in _PYTHONS and nxt == "-m" and i + 2 < len(verbs) and verbs[i + 2] in ("pip", "venv", "ensurepip"):
            return _critical("shell_installer", f"{v} -m {verbs[i + 2]}")
        if v == "dotnet" and nxt in ("add", "remove", "tool", "new", "workload", "nuget", "restore"):
            return _critical("shell_installer", f"dotnet {nxt}")
        if v in _PERMISSION:
            return _critical("shell_permission", v)
        if v == "git":
            risk = _git_risk(lows[i + 1:])
            if risk is not None:
                return risk
    return None


def _method_of(args: list, i: int, sep: str) -> str:
    arg = args[i]
    if sep in arg:
        return arg.split(sep, 1)[1].lower()
    return args[i + 1].lower() if i + 1 < len(args) else ""


def _sends(verb: str, args: list) -> bool:
    if verb == "curl":
        for i, a in enumerate(args):
            if a in _CURL_SEND or (a.startswith("--") and a.split("=", 1)[0] in _CURL_SEND):
                return True
            if re.fullmatch(r"-[A-Za-z]+", a) and set(a[1:]) & {"d", "F", "T"}:
                return True
            if a.startswith("-X"):
                method = a[2:].lower() or _method_of(args, i, "=")
            elif a.split("=", 1)[0] == "--request":
                method = _method_of(args, i, "=")
            else:
                continue
            if method not in _READ_METHODS:
                return True
        return False
    if verb == "wget":
        for i, a in enumerate(args):
            low = a.lower()
            if low.startswith(_WGET_SEND):
                return True
            if low.startswith("--method") and _method_of(args, i, "=") not in _READ_METHODS:
                return True
        return False
    # Invoke-WebRequest / Invoke-RestMethod; PowerShell accepts any unique prefix.
    for i, a in enumerate(args):
        low = a.lower()
        if low.startswith(("-b", "-inf", "-fo")):
            return True
        if low.startswith("-me") and _method_of(args, i, ":") not in _READ_METHODS:
            return True
    return False


def _git_risk(args: list) -> Optional[Risk]:
    """Irreversible git, a global config change, or an option that runs a program."""
    for a in args:
        low = a.lower()
        if any(low == o or low.startswith(o + "=") for o in _GIT_RUNS_PROGRAMS) \
                or low.startswith(("--upload-pack", "--receive-pack")):
            return _critical("shell_git_write", f"git {low}")
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in _GIT_VALUE_OPTIONS else 1
    if i >= len(args):
        return None
    sub = _verb(args[i])
    rest = [a.lower() if a.startswith("--") else a for a in args[i + 1:]]
    shorts = "".join(a[1:] for a in rest if re.fullmatch(r"-[A-Za-z]+", a))
    detail = f"git {sub}"
    if sub == "push" or sub in _GIT_HISTORY:
        return _critical("shell_git_write", detail)
    if sub == "reset" and "--hard" in rest:
        return _critical("shell_git_write", detail + " --hard")
    if sub == "clean" and "n" not in shorts and "--dry-run" not in rest:
        return _critical("shell_git_write", detail)
    # Owner rule (2 Nov 2026): commands that throw away uncommitted work are irreversible.
    force = "f" in shorts or "--force" in rest
    if sub == "checkout" and ("--" in rest or "." in rest or force):
        return _critical("shell_git_write", detail)
    if sub == "switch" and (force or "--discard-changes" in rest):
        return _critical("shell_git_write", detail)
    if sub == "restore" and not (("--staged" in rest or "S" in shorts)
                                 and "--worktree" not in rest and "W" not in shorts):
        return _critical("shell_git_write", detail)
    if sub == "stash" and rest[:1] and rest[0].lower() in ("drop", "clear"):
        return _critical("shell_git_write", f"{detail} {rest[0].lower()}")
    if sub == "branch" and (set(shorts) & {"D", "M", "C"} or (
            ("d" in shorts or "--delete" in rest) and ("f" in shorts or "--force" in rest))):
        return _critical("shell_git_write", detail)
    if sub == "reflog" and rest[:1] and rest[0].lower() in ("expire", "delete"):
        return _critical("shell_git_write", f"{detail} {rest[0].lower()}")
    if sub == "gc" and any(a.startswith("--prune") for a in rest):
        return _critical("shell_git_write", detail + " --prune")
    if sub == "config" and any(a in ("--global", "--system") for a in rest):
        return _critical("shell_git_write", detail)
    return None


# Bash, not this module, splits a wrapped script: it removes quotes (`"grep"`),
# unescapes `.\.` to `..`, expands globs (a match may be a junction out) and
# binds attached flag values, and the safe list's argv guarantee does not hold.
_POSIX_SCRIPT_UNSAFE = re.compile(r"[\"'\\*?\[\]]|(?:^|\s)-[A-Za-z]+[.\\/:~]")


def _posix_script(raw: str) -> Optional[str]:
    """The script after a POSIX shell's -c flag, quoted the way the shell reads it
    (the Windows tokenizer keeps single quotes and drops double ones)."""
    try:
        words = shlex.split(raw, posix=True)
    except ValueError:
        return None
    flag = next((i for i, w in enumerate(words) if re.fullmatch(r"-[a-z]*c[a-z]*", w)), None)
    if flag is None or flag + 1 >= len(words):
        return None
    return words[flag + 1]


def _posix_script_unreadable(script: str) -> bool:
    if _POSIX_SCRIPT_UNSAFE.search(script):
        return True
    words = script.split()
    head = _verb(words[0]) if words else ""
    shorts = "".join(w[1:] for w in words[1:] if re.fullmatch(r"-[A-Za-z]+", w))
    if head == "tree":
        # -o writes the listing to a file; -R reruns with -o.
        return bool(set(shorts) & {"o", "R"}) or any(w.startswith("--output") for w in words)
    if head in ("grep", "egrep", "fgrep"):
        # -R follows symlinks out of the tree.
        return "R" in shorts or "--dereference-recursive" in words
    return False


def _interpreter_risk(raw: str, tokens: Optional[list], verbs: list, cwd: str, workspace: str,
                      depth: int) -> Optional[Risk]:
    """Inline code. PowerShell and cmd always ask: their quoting, comma arrays and
    provider paths differ from what this module reads. A POSIX shell's script is
    classified by these same rules. Other languages ask only when the code holds a
    delete, network, install or outside-path trigger."""
    for v in verbs:
        if v in _POWERSHELLS:
            if "-file" in verbs and not any(w in _INLINE_FLAGS for w in verbs):
                break
            return _critical("shell_inline_code", v)
        if v in _SCRIPT_HOSTS:
            return _critical("shell_inline_code", v)
    if not tokens:
        return None
    for i, tok in enumerate(tokens):
        v = _verb(tok)
        if v in _POSIX_SHELLS:
            flag = next((j for j in range(i + 1, len(tokens))
                         if re.fullmatch(r"-[a-z]*c[a-z]*", tokens[j])), None)
            if flag is None:
                return None
            if re.search(r"[\\/]", tok) and not tok.startswith(_POSIX_SYSTEM_DIRS):
                # Not a system shell: a workspace binary may stand behind the path.
                return _critical("shell_inline_code", v)
            script = _posix_script(raw)
            if script is None:
                return _critical("shell_unparseable", v)
            if _posix_script_unreadable(script):
                return _critical("shell_inline_code", v)
            inner = classify_shell(script, cwd=cwd, workspace=workspace, _depth=depth + 1)
            return inner if inner.critical else None
        if v in _CODE_INTERPRETERS:
            flag = next((j for j in range(i + 1, len(tokens))
                         if tokens[j].lower() in _INLINE_FLAGS), None)
            if flag is None:
                return None
            code = tokens[flag + 1] if flag + 1 < len(tokens) else ""
            found = _trigger([w for w in _INNER_WORDS.split(code) if w], inline=True)
            if found is not None:
                return _critical("shell_inline_risky", f"{v}: {found.detail}")
            if _INLINE_API.search(code) or _INLINE_OUTSIDE.search(code):
                return _critical("shell_inline_risky", v)
            return None
    return None


def _shell_reason(raw: str, tokens: Optional[list], cwd_ok: bool, root: Optional[str],
                  cwd: str, workspace: str, depth: int) -> Risk:
    words = [w for w in _WORDS.split(raw) if w]
    verbs = [_verb(w) for w in words]
    interpreted = any(v in _INTERPRETERS for v in verbs)
    unquoted = _unquoted(raw)
    if interpreted or tokens is None or unquoted is None:
        scan = words
    else:
        # Quoted text is one argument (a commit message, a file name), not a verb.
        scan = [w for w in _WORDS.split(unquoted) if w] + tokens
    found = _trigger(scan)
    if found is not None:
        return found
    if interpreted:
        found = _interpreter_risk(raw, tokens, verbs, cwd, workspace, depth)
        if found is not None:
            return found
    head = verbs[0] if verbs else ""
    if unquoted is None or tokens is None:
        return _critical("shell_unparseable", head)
    if (any(ch in unquoted for ch in command_safety._CONTROL_CHARS)
            or any(ch in raw for ch in "$`\n\r")):
        return _critical("shell_metachar", head)
    if not cwd_ok:
        return _critical("shell_outside_workspace", head)
    args = []
    for tok in tokens[1:]:
        if _URL.match(tok):
            continue
        args.append(tok)
        if "%" in tok:
            args.append(os.path.expandvars(tok))
        if root and any(ch in tok for ch in "*?["):
            # A match may be a junction that leads out of the workspace; each
            # prefix counts, since `lin*/new` names a file that does not exist yet.
            parts = re.split(r"[\\/]", tok)
            for k in range(1, len(parts) + 1):
                prefix = "/".join(parts[:k])
                if any(ch in prefix for ch in "*?["):
                    try:
                        args.extend(glob.glob(prefix, root_dir=root))
                    except (OSError, ValueError):
                        pass
    native = not ("-" in head or head in _PS_PROVIDER_HEADS)
    if not command_safety._stays_in_workspace([tokens[0]] + args, root, native=native):
        return _critical("shell_outside_workspace", head)
    if workspace and head != "git":
        for tok in args:
            candidate = command_safety._attached_flag_value(tok) if tok.startswith("-") else tok
            parts = _relative_parts(candidate, root or workspace, workspace) if candidate else None
            if parts and protected_reason(parts) is not None:
                return _critical("shell_tool_config", "/".join(parts))
    return _routine("shell_in_workspace", head)


def classify_many(actions: Iterable[Any]) -> Risk:
    """The first critical verdict among `actions`, else routine."""
    last = None
    for action in actions:
        risk = classify(action)
        if risk.critical:
            return risk
        last = risk
    return last if last is not None else _critical("unknown_action", "no action")
