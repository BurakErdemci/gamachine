"""Rule-based risk classifier behind the "balanced" approval mode.

Owner decision (Burak, 27 Sep 2026): in balanced mode the AI works on its own
and only CRITICAL actions raise the approval card. The classification is
rule-based (no LLM), and anything the rules cannot prove routine is critical.

    classify(action) -> Risk(verdict, reason, detail)

`verdict` is "routine" or "critical"; `reason` is a stable code the UI maps to
text (the card shows "Kritik: <reason>"); `detail` names the program, path or
Unity tool.action behind it. Pure and deterministic apart from resolving paths
on the real file system (symlinks, 8.3 names), which is the point of it.

Shell commands use a SAFE LIST, not a list of dangerous commands: a
dangerous-list approach was measured to be bypassable on Windows PowerShell
(5 of 11 forms, Burak, 27 Sep 2026). A command is routine only when
command_safety calls it auto-safe or it is one of a few exact build/test forms
below; the scan for a reason on a critical command only names it, it never
makes anything routine.

The fixed Unity file rules (unity_file_guard: .meta files, raw writes to Unity
YAML) run BEFORE this in every mode; nothing here relaxes them.

Standard library only: agy_step_gate imports this on every gated agy tool
call. `agentic` is a lazy package, so `agentic.command_safety` costs nothing
extra; the Unity ledger (unity_tool_policy) is imported only for Unity
actions, which never reach the agy hook.
"""
from __future__ import annotations

import ast
import ntpath
import os
import re
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
    return _critical("unknown_action", action.get("tool") or kind or "")


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
    if names[0].startswith("."):
        return "/".join(parts)
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
    if risk == "read":
        return _routine("unity_read", detail)
    if risk == "routine":
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
# addon_utils stays: upstream's rigging guide enables Rigify with it.
_BLENDER_MODULES = frozenset({
    "bpy", "bpy_extras", "bmesh", "mathutils", "bl_math", "addon_utils", "math", "cmath",
    "random", "colorsys", "collections", "itertools", "functools", "operator", "statistics",
    "typing", "dataclasses", "enum", "copy", "string", "re", "json", "time", "datetime",
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
    ("bpy.app.handlers", "handlers or timers"),
    ("bpy.app.timers", "handlers or timers"),
    ("bpy.utils.execfile", "runs a file"),
    ("bpy.utils.load_scripts", "runs scripts"),
)
_LOOPS = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp,
          ast.GeneratorExp)
_DATA_REMOVE = re.compile(r"bpy\.data\.[^.]+\.remove")
_OBJECT_DELETE = frozenset({"bpy.ops.object.delete", "bpy.ops.outliner.delete"})
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
        if group.startswith(("export_", "import_")) or op.startswith(("export", "import")):
            return "export/import operator"
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
            for kw in call.keywords:
                if kw.arg == "write_still" and not (isinstance(kw.value, ast.Constant) and not kw.value.value):
                    self.flag(call, "file write", f"{path}(write_still)")
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


def classify_shell(command: Any, *, cwd: str = "", workspace: str = "") -> Risk:
    if not isinstance(command, str) or not command.strip():
        return _critical("shell_unparseable", "")
    root, cwd_ok = _confinement_root(cwd, workspace)
    if cwd_ok and command_safety.is_auto_safe(command, root):
        return _routine("shell_safe_list", command.strip().split()[0])
    raw = command.strip()
    if cwd_ok and all(ch in _BUILD_CHARS for ch in raw):
        tokens = command_safety._tokenize(raw)
        if tokens:
            form = _build_form(tokens)
            # Build/test programs are native executables that take their
            # arguments literally (`-p no:cacheprovider`, `test_x.py::name`);
            # only PowerShell's own cmdlets read `Name:` as a provider.
            if form and command_safety._stays_in_workspace(tokens, root, native=True):
                return _routine("shell_build_allowlist", form)
    return _shell_reason(raw, cwd_ok)


# Naming a critical command. None of this decides routine; it only picks the
# reason the card shows, so a miss here costs a vaguer reason, not a hole.
_WORDS = re.compile(r"[\s;|&(){}<>`,]+")
_DELETE_MOVE = frozenset({
    "rm", "del", "erase", "rmdir", "rd", "unlink", "shred", "rimraf", "remove-item", "ri",
    "mv", "move", "ren", "rename", "move-item", "mi", "rename-item", "rni", "clear-content",
    "clc", "clear-item", "cli", "diskpart", "cipher", "sdelete",
})
_INTERPRETERS = frozenset({
    "powershell", "pwsh", "cmd", "bash", "sh", "zsh", "dash", "python", "python3", "py",
    "node", "deno", "bun", "perl", "ruby", "php", "wscript", "cscript", "mshta",
})
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
    "curl", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod",
    "start-bitstransfer", "certutil", "bitsadmin", "scp", "sftp", "ftp", "ssh", "nc", "ncat",
    "telnet", "rsync", "net.webclient", "system.net.webclient",
})
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


def _verb(word: str) -> str:
    name = re.split(r"[\\/]", word.strip("'\"“”‘’"))[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _shell_reason(raw: str, cwd_ok: bool) -> Risk:
    words = [w for w in _WORDS.split(raw) if w]
    verbs = [_verb(w) for w in words]
    lows = [w.lower().strip("'\"“”‘’") for w in words]
    for v in verbs:
        if v in _DELETE_MOVE:
            return _critical("shell_delete_move", v)
    interpreter = next((v for v in verbs if v in _INTERPRETERS), None)
    if interpreter and any(w in _INLINE_FLAGS for w in lows):
        return _critical("shell_inline_code", interpreter)
    for v in verbs:
        if v in _INLINE_VERBS:
            return _critical("shell_inline_code", v)
    for v, w in zip(verbs, lows):
        if v in _NETWORK or "http://" in w or "https://" in w or "net.webclient" in w:
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
        if v == "git" and nxt and nxt not in _GIT_READS:
            return _critical("shell_git_write", f"git {nxt}")
    if any(ch in raw for ch in command_safety._CONTROL_CHARS) or re.search(r"[{}@%^!\"'“”‘’]", raw):
        return _critical("shell_metachar", verbs[0] if verbs else "")
    if not cwd_ok:
        return _critical("shell_outside_workspace", verbs[0] if verbs else "")
    head = verbs[0] if verbs else ""
    tokens = command_safety._tokenize(raw) or []
    if tokens and _build_form(tokens) and all(ch in _BUILD_CHARS for ch in raw):
        # An allow-listed form, so only the confinement can have failed.
        return _critical("shell_outside_workspace", head)
    return _critical("shell_unknown_program", head)


def classify_many(actions: Iterable[Any]) -> Risk:
    """The first critical verdict among `actions`, else routine."""
    last = None
    for action in actions:
        risk = classify(action)
        if risk.critical:
            return risk
        last = risk
    return last if last is not None else _critical("unknown_action", "no action")
