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

import ntpath
import os
import re
from typing import Any, Iterable, Mapping, NamedTuple, Optional

from agentic import command_safety

ROUTINE = "routine"
CRITICAL = "critical"

KINDS = frozenset({
    "shell", "file_write", "file_delete", "file_move", "read", "read_outside",
    "unity", "mail", "permission", "other",
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
            if form and command_safety._stays_in_workspace(tokens, root):
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
