"""PreToolUse hook for agy (`backend agy-hook --state <file>`).

agy runs this for its built-in tool calls: a main child's hooks.json matches
".*" and, beside it, the exact name of every known writer and run_command
(MAIN_HOOK_MATCHERS, written by providers/agy_provider.py _write_step_gate),
so a build that does not read ".*" as a regex still sends those calls here.
It reads the hook payload on stdin and prints {"decision": "allow"|"deny"}.
When several entries match one call, agy may run the hook once per entry
(unmeasured): decide_once answers every run of one call with the first run's
decision, so a native write asks for one card, not two.

It is installed in every approval mode: the state file's "mode" decides.
"auto" allows every call. "balanced" (Safe Auto, Burak 27 Sep 2026) allows
known reads, the unityai bridge calls and the built-in writes and commands
action_risk calls routine; a critical native file write (NATIVE_WRITERS) asks
for a card, and other critical calls are denied with a pointer to the unityai
bridge. "closed" denies every call, and "side" holds only while a read-only
side question runs. A state whose mode this hook does not know (a "step" an
older Gamachine wrote included) allows reads only. In auto and balanced, the
fixed Unity file rule (unity_file_guard: no .meta writes/deletes/moves, no raw
writes to Unity YAML assets) and the secret file rule are checked first and
can only deny. A process spawned in auto must still be gated after a flip, and
agy reads its hooks only at start, so the mode cannot live in hooks.json.

Balanced is an allow-list (24 Nov 2026, after v4.0.2): agy 1.5.15 writes with
write_file and edit_file, names the hook only listed for 1.2.x builds, so
those writes ran with no card. Now a call passes only if its tool is a known
read (MAIN_READ_TOOLS), call_mcp_tool (the Unity MCP server gates its own
calls) or a tool with its own rule below; every other name, and a payload
without one, is denied.

A built-in writer's relative path is resolved against the workspace agy
writes into, the payload's workspacePaths[0], never the hook's own working
directory (audit, 25 Nov 2026: a relative or ".."-bearing path judged from a
different base could pass a write outside the workspace as routine). With no
such workspace in the payload a relative path is critical.

Why a top-level module and not providers/: importing anything under
`providers` runs the package __init__, which imports every provider SDK
(openai, anthropic, google.genai, ollama): 3.3 s measured, paid on every
gated tool call. This file imports only the standard library.

Fail closed: an unreadable state file or payload is a deny, and so is any
tool in "side" that is not a proven read, and any tool in balanced that no
rule here names.

The run_command allow rule (measured 25 Sep 2026, agy 1.2.8): agy runs
commands in Windows PowerShell 5.1 on Windows, and the unityai launcher there
is a .cmd file, so every argument is parsed a third time by cmd.exe. A
prefix match on the launcher path would pass `<launcher> x && evil`, a
PowerShell `$(...)` inside a double-quoted value (run before unityai starts),
or cmd.exe metacharacters (PowerShell 5.1 passes a value without spaces
unquoted). So the command line must be exactly one of these shapes, nothing
before or after:

  [& ]LAUNCHER SUB --flag VALUE ...                     (every platform)
  [PS_UTF8_PREFIX<newline>]@'<newline>BODY<newline>'@ | & LAUNCHER save-file
  ... --content-stdin                                    (Windows only)
  LAUNCHER save-file ... --content-stdin <<'UNITYAI_EOF'<newline>BODY
  <newline>UNITYAI_EOF                                   (posix only)

LAUNCHER is the exact launcher path (bare, "..." or '...'). A VALUE is one
token built only from allow-listed characters (_VALUE_CHARS). BODY is data:
both quoted forms are literal, and the only text that could end them early (a
line starting with a quote character and @, or a line equal to UNITYAI_EOF) is
refused inside it.

Quotes are not only ASCII (measured 25 Sep 2026, Windows PowerShell 5.1 via
-EncodedCommand): U+201C/U+201D/U+201E close a "..." string, U+2018/U+2019/
U+201A/U+201B close a '...' one, and a line starting with any of those four
plus @ ends a @'...'@ here-string. Each let a second statement run with no
card while this parser saw one token. So values are allow-listed, not
filtered through a deny-list: a character nobody thought of is refused.
"""
import hashlib
import json
import ntpath
import os
import re
import sys
import tempfile
import time

import unity_file_guard  # stdlib-only, like this file
import agy_native_approval  # stdlib-only approval bridge

# Built-in agy tools that write files, plus send_command_input (it types
# into a running process, e.g. a shell a unityai bash call started). agy
# 1.5.15 writes with write_file (args Path, Content, Overwrite) and edits with
# edit_file (Target, Replacement and a path key), measured 24 Nov 2026; the
# rest are agy 1.2.x names. A side question's hooks.json still lists the
# 1.2.x names exactly, as it did (its SIDE_HOOK_MATCHERS hold the new ones).
GATED_TOOLS = (
    "write_to_file", "replace_file_content", "multi_replace_file_content",
    "sed_file", "notebook_edit", "send_command_input",
)
NATIVE_WRITERS = agy_native_approval.NATIVE_WRITERS
WRITE_TOOLS = GATED_TOOLS + tuple(t for t in NATIVE_WRITERS if t not in GATED_TOOLS)
RUN_TOOL = "run_command"
# Measured 24 Nov 2026 (agy 1.5.15): ".*" fires the hook for every built-in
# call and its deny stops the call.
ALL_TOOLS_MATCHER = ".*"
# Built-in tools that change files or run something under any agy build we
# read (the 1.2.x names come from the binary, see SIDE_HOOK_MATCHERS).
_OTHER_CHANGERS = (
    "delete_file", "delete_directory", "move", "write_blob", "file_change", "edit_notebook",
    "execute_notebook", "run_script", "shell_exec", "git_commit",
)
FAIL_REASON = "Gamachine's approval gate could not verify this call, so it is blocked."
BALANCED_MODE = "balanced"
BALANCED_REASON = (
    "Gamachine Safe Auto mode: this {what} is critical ({reason}{detail}), so it needs the "
    "user's approval. Do it through the unityai bridge in exactly the form given in your "
    "instructions (save-file, delete-file or bash) so an approval card is shown. Argument "
    "values may hold only ASCII letters, digits, spaces, Turkish letters and "
    "- _ . / \\ : , + = @ # ~ * ? [ ] {{ }} (no quotes inside, none of ; $ ` & | < > ^ % ! ( ), "
    "no typographic quotes or dashes), and nothing may come before or after the unityai call.")
RELATIVE_WRITE_REASON = (
    "Gamachine Safe Auto mode: agy did not say which workspace this relative path "
    "belongs to, so the file it would write cannot be shown on an approval card. Use the "
    "file's absolute path.")
UNKNOWN_STATE_REASON = (
    "Gamachine's approval gate state is not one this version knows, so only reading tools "
    "may run. Tell the user to send the message again; that rewrites the gate state.")
# The workspace hook entry (written by agy_provider._write_step_gate). The
# state file is shared by every agy child, so balanced risk and native writes find the
# workspace a call confines to by walking up from the hook's working
# directory to the directory whose .agents/hooks.json holds this entry.
STEP_GATE_KEY = "gamachine-step-gate"
STEP_GATE_HOOKS_FILE = ".agents/hooks.json"
# Written for a child whose session is closing (agy_session.close): it denies
# every call in either approval mode.
CLOSED_MODE = "closed"
CLOSED_REASON = "Gamachine closed this agy session; no tool may run in it."

# Written while a read-only side question runs on agy (Burak, 27 Sep 2026).
# A side question runs only while no main agy turn runs or waits (agy_session
# _MAIN_TURNS), so the one shared state file can say "side" for exactly that turn. Only the tools
# below, and Unity MCP calls the ledger proves to be reads, are allowed; any
# other call the hook sees is denied, so a tool nobody listed stays closed.
SIDE_MODE = "side"
SIDE_READ_TOOLS = frozenset({
    "view_file", "view_file_outline", "view_code_item", "view_content_chunk",
    "list_dir", "list_directory", "grep_search", "find_by_name", "codebase_search",
    "list_resources", "read_resource",
    # agy's own progress/UI steps; they touch nothing outside the turn.
    "task_boundary", "notify_user", "suggested_responses",
})
MCP_CALL_TOOL = "call_mcp_tool"
SIDE_MCP_SERVER = "unityMCP"
# Every name a side process's hooks.json matches besides the 1.2.x writers. The
# hook only sees a call its matcher names (measured for exact names), so the
# list is what agy 1.2.x ships, read from the binary on 27 Sep 2026 (tool
# handlers and CORTEX_STEP_TYPE_* names), plus ".*": agy's own hooks
# documentation calls the matcher a regex and ".*" compiles in any engine;
# whether this agy build honours it is unmeasured, so the names stay.
SIDE_HOOK_MATCHERS = (
    MCP_CALL_TOOL, "mcp_tool", "invoke_subagent", "define_subagent", "manage_subagents",
    "browser_subagent", "schedule", "send_message", "manage_inbox", "manage_task",
    "generate_image", "run_script", "shell_exec", "delete_file", "delete_directory", "move",
    "git_commit", "write_file", "edit_file", "edit_notebook", "execute_notebook",
    "write_blob", "file_change", "code_action", "clipboard", "run_extension_code",
    "restart_dev_server", "deploy_firebase", "set_up_firebase", "cloud_sql_execute_sql",
    "cloud_sql_update_schema", "set_up_cloud_sql", "install_applet_package",
    "install_applet_dependencies", "compile", "compile_applet", "lint_applet",
    "blaze_build_targets", "blaze_test_targets", "post_pr_review", "start_code_review",
    "propose_code", "agency_tool_call", "workspace_api", "rpc_action", "memory",
    "brain_update", "knowledge_generation", "ki_insertion", "ask_question",
    "command_status", "read_terminal", "search_web", "read_url_content",
    "open_browser_url", "read_browser_page", "list_browser_pages",
    "capture_browser_screenshot", "capture_browser_console_logs", "click_browser_pixel",
    "execute_browser_javascript", "browser_click_element", "browser_drag_pixel_to_pixel",
    "browser_get_dom", "browser_get_network_request", "browser_input",
    "browser_list_network_requests", "browser_mouse_down", "browser_mouse_up",
    "browser_mouse_wheel", "browser_move_mouse", "browser_press_key",
    "browser_refresh_page", "browser_resize_window", "browser_scroll",
    "browser_scroll_down", "browser_scroll_up", "browser_select_option", ".*",
)
SIDE_REASON = (
    "Gamachine: this is a read-only side question, so {what} is refused. Only reading "
    "tools may run here (view, list or search files, Unity MCP read actions). Answer from "
    "the conversation and what you can read; do not try another way to run it.")
SIDE_MESHY_REASON = (
    "Gamachine: this is a read-only side question; meshy calls cost credits and are "
    "refused here. Answer from the conversation.")
# What balanced, and a state the hook does not know, let through with no rule
# of their own: the side question's reads, plus reads that only a main turn
# needs (web search, the output of a command run_command already passed) and
# agy asking the user a question. Anything else, a tool added in a later agy included, is denied.
MAIN_READ_TOOLS = SIDE_READ_TOOLS | frozenset({
    "search_web", "read_url_content", "command_status", "read_terminal",
    "list_permissions", "ask_question",
})
UNKNOWN_TOOL_REASON = (
    "Gamachine Safe Auto mode: the built-in tool {name!r} is not one the approval gate "
    "knows, so it is blocked. Read with view_file, list_dir or grep_search; create or edit "
    "files with write_file or edit_file (an approval card is shown when one is needed); "
    "delete, move or run anything only through the unityai bridge, in exactly the form "
    "given in your instructions.")
# The hooks.json matchers of every main (non-side) child, in every mode
# (audit H1, 25 Nov 2026): ".*" alone left the gate fully open on an agy build
# that does not read a matcher as a regex, so the exact name of every known
# writer, run_command and every other known changer stands beside it. A call
# that two entries match may then run the hook twice; decide_once gives both
# runs one decision and at most one card.
MAIN_HOOK_MATCHERS = (ALL_TOOLS_MATCHER,) + tuple(dict.fromkeys(
    WRITE_TOOLS + (RUN_TOOL,) + _OTHER_CHANGERS))

_SUBCOMMANDS = {
    # flag -> takes a value
    "save-file": {"--path": True, "--content": True, "--content-stdin": False},
    "delete-file": {"--path": True},
    "read-file": {"--path": True},
    "list-dir": {"--path": True},
    "bash": {"--command": True},
}
_REQUIRED = {"save-file": {"--path"}, "delete-file": {"--path"}, "read-file": {"--path"},
             "list-dir": set(), "bash": {"--command"}}
_BARE = re.compile(r"[A-Za-z0-9_.\-/\\:]+")
_FORBIDDEN = set("\"'$`&|<>^%!();")
# Turkish letters, circumflexed vowels included (kâğıt): the only non-ASCII a
# value may hold. Measured 25 Sep 2026: all reach unityai intact through
# PowerShell 5.1 -> unityai.cmd %* -> Python argv.
_TURKISH_LETTERS = "çÇğĞıİöÖşŞüÜâÂîÎûÛ"
_VALUE_CHARS = frozenset({chr(c) for c in range(0x20, 0x7F)} - _FORBIDDEN
                         | set(_TURKISH_LETTERS))
# What PowerShell 5.1 may read as a quote. U+FF02/U+FF07 did not close a string
# in the measurement; they are listed anyway at no cost.
_QUOTE_LIKE = frozenset("\"'‘’‚‛“”„＂＇")
_HEREDOC = " <<'UNITYAI_EOF'"
_HEREDOC_END = "UNITYAI_EOF"
# Windows PowerShell 5.1 pipes text to a native program in $OutputEncoding,
# ASCII by default: Turkish letters reached unityai as '?' (measured). This
# exact line, and nothing else, may precede the here-string. No BOM ($false),
# which unityai would otherwise save as the file's first character.
PS_UTF8_PREFIX = "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)"


def bare_word(word: str) -> bool:
    """True when the gate accepts `word` unquoted; any other launcher path must be quoted."""
    return _BARE.fullmatch(word) is not None


def _value_ok(text: str) -> bool:
    # A trailing backslash would make bash and the Windows argv parser read
    # the closing quote as escaped, so they would split tokens differently
    # from this parser.
    if text.endswith("\\"):
        return False
    return all(c in _VALUE_CHARS for c in text)


def _ends_here_string(line: str) -> bool:
    # PowerShell 5.1 ends @'...'@ at a column-0 quote character followed by @,
    # curly ones included (measured); indented, it is data.
    return len(line) >= 2 and line[0] in _QUOTE_LIKE and line[1] == "@"


def _tokens(line: str):
    """Space-separated tokens as (quote, text); None when anything else appears."""
    out, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c == " ":
            i += 1
            continue
        if c in "\"'":
            j = line.find(c, i + 1)
            if j < 0:
                return None
            out.append((c, line[i + 1:j]))
            i = j + 1
            if i < n and line[i] != " ":
                return None  # "a"b concatenation
            continue
        j = line.find(" ", i)
        j = n if j < 0 else j
        word = line[i:j]
        if word != "&" and not bare_word(word):
            return None
        out.append(("", word))
        i = j
    return out


def _same_path(a: str, b: str, windows: bool) -> bool:
    if windows:
        # ntpath, not os.path: `windows` says which rules apply, whatever OS
        # this runs on (on Linux os.path.normcase does not fold case).
        return ntpath.normcase(a) == ntpath.normcase(b)
    return a == b


def _invocation_ok(line: str, launcher: str, windows: bool, *, stdin: bool) -> bool:
    toks = _tokens(line)
    if not toks:
        return False
    call_op = toks[0] == ("", "&")
    if call_op:
        if not windows:
            return False
        toks = toks[1:]
    if len(toks) < 2 or ("", "&") in toks:
        return False
    quote, first = toks[0]
    if not launcher or not _same_path(first, launcher, windows):
        return False
    if any(c in _QUOTE_LIKE or c in "$`" for c in first):
        return False  # PowerShell would end or expand the string inside the path
    if windows and quote and not call_op:
        return False  # a quoted path without & is a string expression, not a call
    squote, sub = toks[1]
    if squote or sub not in _SUBCOMMANDS:
        return False
    spec, seen, rest = _SUBCOMMANDS[sub], set(), toks[2:]
    i = 0
    while i < len(rest):
        fquote, flag = rest[i]
        if fquote or flag not in spec or flag in seen:
            return False
        seen.add(flag)
        if spec[flag]:
            if i + 1 >= len(rest):
                return False
            vquote, value = rest[i + 1]
            if vquote and not _value_ok(value):
                return False
            if not vquote and value == "&":
                return False
            i += 2
        else:
            i += 1
    if not _REQUIRED[sub] <= seen:
        return False
    uses_stdin = "--content-stdin" in seen
    if uses_stdin != stdin:
        return False
    if uses_stdin and "--content" in seen:
        return False
    return True


def unityai_command_allowed(command: str, launcher: str, windows: bool) -> bool:
    if not isinstance(command, str) or not isinstance(launcher, str) or not launcher:
        return False
    text = command.replace("\r\n", "\n")
    if "\r" in text:
        return False
    if text.endswith("\n"):
        text = text[:-1]
    if "\n" not in text:
        return _invocation_ok(text, launcher, windows, stdin=False)
    lines = text.split("\n")
    if windows:
        # PowerShell single-quoted here-string: literal, and it ends at the
        # first line that STARTS with '@ (column 0).
        if lines[0] == PS_UTF8_PREFIX:
            lines = lines[1:]
        if lines[0] != "@'":
            return False
        if len(lines) < 2 or any(_ends_here_string(line) for line in lines[1:-1]):
            return False
        tail = lines[-1]
        if not tail.startswith("'@ | "):
            return False
        return _invocation_ok(tail[len("'@ | "):], launcher, windows, stdin=True)
    if not lines[0].endswith(_HEREDOC) or lines[-1] != _HEREDOC_END:
        return False
    if any(line == _HEREDOC_END for line in lines[1:-1]):
        return False
    return _invocation_ok(lines[0][:-len(_HEREDOC)], launcher, windows, stdin=True)


def _deny(reason: str) -> dict:
    return {"decision": "deny", "reason": reason}


# agy 1.2.x file writers name their target in TargetFile, one variant in
# target_file (read from the agy binary's tool schemas, 26 Sep 2026); agy
# 1.5.15's write_file names it Path, and edit_file's path key was not recorded
# (24 Nov 2026), so every usual path spelling counts as a target. A rule that
# reads targets can only deny on one, so reading too many keys adds no allow.
# notebook_edit takes only .ipynb paths, so it can never name a protected Unity file.
_FILE_WRITERS = ("write_to_file", "replace_file_content", "multi_replace_file_content",
                 "sed_file", "write_file", "edit_file")
_TARGET_KEYS = agy_native_approval.PATH_KEYS


def _write_targets(args: dict) -> list:
    return [value for key, value in args.items()
            if isinstance(key, str) and key.replace("_", "").lower() in _TARGET_KEYS]


def _bases(base, cwd: str) -> list:
    """Every base a relative path may be read against: the agy workspace
    and the hook's own directory. Only for the rules that can only deny."""
    return [b for b in dict.fromkeys((base, cwd)) if b]


def _unity_file_refusal(raw: bytes, base=None):
    """The fixed Unity file rule (unity_file_guard), in every gated mode.

    It can only deny. An unreadable payload returns None and is left to the
    mode rules, so this adds no allow path and changes no mode's verdict for
    calls the rule does not name. A relative path is checked against the agy
    workspace and the hook's directory both.
    """
    try:
        call = json.loads(raw.decode("utf-8"))["toolCall"]
        name = call["name"]
        args = call.get("args") or {}
    except Exception:
        return None
    if not isinstance(args, dict):
        return None
    if name == RUN_TOOL:
        cwd = args.get("Cwd") if isinstance(args.get("Cwd"), str) else ""
        return unity_file_guard.check_shell(args.get("CommandLine"), cwd or os.getcwd())
    if name in _FILE_WRITERS:
        for value in _write_targets(args):
            for where in _bases(base, os.getcwd()):
                refusal = unity_file_guard.check_write(value, where)
                if refusal is not None:
                    return refusal
    return None


def _action_risk():
    import action_risk  # stdlib-only; imported only when a gate rule needs it
    return action_risk


def _secret_refusal(raw: bytes, cwd: str, base=None):
    """Gamachine's own key/token files are never written, in any mode (owner
    decision, 4 Nov 2026). Like the Unity file rule it can only deny."""
    try:
        call = json.loads(raw.decode("utf-8"))["toolCall"]
        name = call["name"]
        args = call.get("args") or {}
    except Exception:
        return None
    if name not in _FILE_WRITERS or not isinstance(args, dict):
        return None
    action_risk = _action_risk()
    for value in _write_targets(args):
        for where in _bases(base, cwd):
            if action_risk.gamachine_secret_target(value, where) is not None:
                return action_risk.secret_refusal(value)
    return None


def _agy_workspace(raw: bytes):
    """The workspace agy writes a relative path into: the payload's
    workspacePaths[0] when it is an absolute path, else None."""
    try:
        paths = json.loads(raw.decode("utf-8")).get("workspacePaths")
    except Exception:
        return None
    if not isinstance(paths, list) or not paths:
        return None
    first = paths[0]
    if not isinstance(first, str) or "\x00" in first or _needs_base(first):
        return None
    return first


def _needs_base(path) -> bool:
    """True when `path` means nothing without a base directory: relative,
    or rooted without a drive on Windows ("\\x"), as action_risk reads it."""
    if not isinstance(path, str):
        return False  # not a path at all: action_risk calls it critical
    p = os.path.expanduser(path.strip())
    if not os.path.isabs(p):
        return True
    return os.name == "nt" and not ntpath.splitdrive(p)[0] and not p.startswith(("\\\\", "//"))


def _hook_workspace(start: str):
    """The nearest directory at or above `start` whose hooks file holds
    Gamachine's entry; None when there is none (balanced then allows
    nothing it would have to confine)."""
    directory = os.path.abspath(start)
    while True:
        try:
            with open(os.path.join(directory, *STEP_GATE_HOOKS_FILE.split("/")),
                      encoding="utf-8-sig") as f:
                if STEP_GATE_KEY in json.load(f):
                    return directory
        except (OSError, ValueError, TypeError):
            pass
        parent = os.path.dirname(directory)
        if not parent or parent == directory:
            return None
        directory = parent


def _balanced_risk(name: str, args, cwd: str, base=None):
    """action_risk's verdict for a built-in agy call in balanced mode.
    `base`: the agy workspace a writer's relative path is resolved against."""
    action_risk = _action_risk()
    workspace = _hook_workspace(cwd)
    if not workspace or not isinstance(args, dict):
        return action_risk.Risk(action_risk.CRITICAL, "unknown_action", name)
    if name == RUN_TOOL:
        run_cwd = args.get("Cwd") if isinstance(args.get("Cwd"), str) and args.get("Cwd") else cwd
        return action_risk.classify({"kind": "shell", "command": args.get("CommandLine"),
                                     "cwd": run_cwd, "workspace": workspace})
    if name in _FILE_WRITERS:
        targets = _write_targets(args)
        if not base:
            unknown = next((t for t in targets if _needs_base(t)), None)
            if unknown is not None:
                return action_risk.Risk(action_risk.CRITICAL, "file_outside_workspace", unknown)
        return action_risk.classify({"kind": "file_write", "paths": targets,
                                     "cwd": base or "", "workspace": workspace})
    return action_risk.Risk(action_risk.CRITICAL, "unknown_action", name)


def _balanced_deny(what: str, risk) -> dict:
    detail = f": {risk.detail}" if risk.detail else ""
    return _deny(BALANCED_REASON.format(what=what, reason=risk.reason, detail=detail))


def _one_arg(args: dict, names):
    """The single value under any spelling of `names` (case and "_" folded);
    None when absent, _AMBIGUOUS when two spellings disagree."""
    found = [value for key, value in args.items()
             if isinstance(key, str) and key.replace("_", "").lower() in names]
    if not found:
        return None
    first = found[0]
    return first if all(value == first for value in found[1:]) else _AMBIGUOUS


_AMBIGUOUS = object()
_MCP_SERVER_KEYS = frozenset({"servername", "server", "mcpserver", "mcpservername"})
_MCP_TOOL_KEYS = frozenset({"toolname", "tool", "name", "mcptoolname"})
_MCP_ARGS_KEYS = frozenset({"arguments", "args", "toolargs", "toolarguments", "input",
                            "toolinput", "parameters", "params"})


def _side_mcp_decision(args) -> dict:
    """call_mcp_tool in a side turn: only a unityMCP call the ledger proves to
    be a read. The payload's field names are not measured, so every usual
    spelling is read and anything unclear is denied."""
    refused = _deny(SIDE_REASON.format(what="this MCP call"))
    if not isinstance(args, dict):
        return refused
    server = _one_arg(args, _MCP_SERVER_KEYS)
    tool = _one_arg(args, _MCP_TOOL_KEYS)
    if not isinstance(server, str) or not isinstance(tool, str) or not tool:
        return refused
    if "meshy" in server.lower():
        return _deny(SIDE_MESHY_REASON)
    if server != SIDE_MCP_SERVER:
        return _deny(SIDE_REASON.format(what=f"a call to the MCP server {server!r}"))
    params = _one_arg(args, _MCP_ARGS_KEYS)
    if params is None:
        params = {}
    if isinstance(params, str):
        try:
            params = json.loads(params) if params.strip() else {}
        except ValueError:
            return refused
    if not isinstance(params, dict):
        return refused
    import unity_tool_policy  # stdlib-only; imported only in a side turn
    try:
        read = (unity_tool_policy.ledger_available() and unity_tool_policy.is_unity_mcp_read_only(
            unity_tool_policy.UNITY_MCP_PREFIX + tool, params))
    except Exception:
        read = False
    if read:
        return {"decision": "allow"}
    return _deny(SIDE_REASON.format(what=f"the Unity action {tool!r} (it is not a proven read)"))


def _side_decision(raw: bytes) -> dict:
    try:
        call = json.loads(raw.decode("utf-8"))["toolCall"]
        name = call["name"]
        args = call.get("args") or {}
    except Exception:
        return _deny(FAIL_REASON)
    if not isinstance(name, str):
        return _deny(FAIL_REASON)
    if name in SIDE_READ_TOOLS:
        return {"decision": "allow"}
    if name == MCP_CALL_TOOL:
        return _side_mcp_decision(args)
    return _deny(SIDE_REASON.format(what=f"the tool {name!r}"))


def _reads_only_decision(raw: bytes) -> dict:
    """A state file whose mode this hook does not know: reads only."""
    try:
        name = json.loads(raw.decode("utf-8"))["toolCall"]["name"]
    except Exception:
        return _deny(FAIL_REASON)
    if isinstance(name, str) and name in MAIN_READ_TOOLS:
        return {"decision": "allow"}
    return _deny(UNKNOWN_STATE_REASON)


def _replace_with_retry(src: str, dst: str, budget_s: float = 2.0) -> None:
    """Replace dst with src, retrying on Windows sharing violations (PermissionError)."""
    deadline = time.monotonic() + budget_s
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _read_json_with_retry(path: str, budget_s: float = 0.5):
    """Read and decode JSON from path, retrying transient sharing/half-visible errors."""
    deadline = time.monotonic() + budget_s
    while True:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            raise
        except (PermissionError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _decide(raw: bytes, state_path: str, windows: bool = None, cwd: str = None) -> tuple:
    windows = (sys.platform == "win32") if windows is None else windows
    cwd = os.getcwd() if cwd is None else cwd
    try:
        state = _read_json_with_retry(state_path)
        mode = state["mode"]
        launcher = state["launcher"]
    except Exception:
        return _deny(FAIL_REASON), None
    if mode == SIDE_MODE:
        return _side_decision(raw), mode
    if mode == CLOSED_MODE:
        return _deny(CLOSED_REASON), mode
    if mode not in ("auto", BALANCED_MODE):
        return _reads_only_decision(raw), mode
    base = _agy_workspace(raw)
    refusal = _unity_file_refusal(raw, base)
    if refusal is not None:
        return _deny(refusal.message), mode
    secret = _secret_refusal(raw, cwd, base)
    if secret is not None:
        return _deny(secret), mode
    if mode == "auto":
        return {"decision": "allow"}, mode
    try:
        call = json.loads(raw.decode("utf-8"))["toolCall"]
        name = call["name"]
    except Exception:
        return _deny(FAIL_REASON), mode
    if not isinstance(name, str):
        return _deny(FAIL_REASON), mode
    if name in MAIN_READ_TOOLS:
        return {"decision": "allow"}, mode
    if name == MCP_CALL_TOOL:
        # Unchanged from before the hook saw it: the Unity MCP server's own
        # approval gate decides its calls, in every provider.
        return {"decision": "allow"}, mode
    if name != RUN_TOOL and name not in WRITE_TOOLS:
        return _deny(UNKNOWN_TOOL_REASON.format(name=name)), mode
    if name == RUN_TOOL:
        try:
            command = call["args"]["CommandLine"]
        except Exception:
            return _deny(FAIL_REASON), mode
        if unityai_command_allowed(command, launcher, windows):
            return {"decision": "allow"}, mode
        risk = _balanced_risk(name, call.get("args"), cwd)
        if not risk.critical:
            return {"decision": "allow"}, mode
        return _balanced_deny("command", risk), mode
    if name in NATIVE_WRITERS:
        # Before the routine check too: a routine write_file or edit_file with
        # an argument nobody measured is refused, not waved through.
        try:
            agy_native_approval._normalized_args(call.get("args"), name)
        except ValueError as error:
            return _deny(str(error)), mode
    risk = _balanced_risk(name, call.get("args"), cwd, base)
    if not risk.critical:
        return {"decision": "allow"}, mode
    if name not in NATIVE_WRITERS:
        return _balanced_deny("file write" if name in _FILE_WRITERS else "call", risk), mode
    workspace = _hook_workspace(cwd)
    if not workspace:
        return _deny("Native approval requires a hook workspace."), mode
    if not base and any(_needs_base(t) for t in _write_targets(call.get("args"))):
        # The card would have to guess which file agy writes.
        return _deny(RELATIVE_WRITE_REASON), mode
    token = agy_native_approval.state_path.set(state_path)
    try:
        allowed, reason = agy_native_approval.request_native_write(
            name, call.get("args"), workspace, base=base or workspace)
        return ({"decision": "allow"} if allowed is True else _deny(reason)), mode
    finally:
        agy_native_approval.state_path.reset(token)


def decide(raw: bytes, state_path: str, windows: bool = None, cwd: str = None) -> dict:
    return _decide(raw, state_path, windows=windows, cwd=cwd)[0]


_ORIGINAL_DECIDE = decide


# One decision per tool call (audit H1, 25 Nov 2026). A call that several
# hooks.json entries match may run this hook once per entry, in turn or at
# once (unmeasured on agy 1.5.15); without this a native write would ask for
# two cards and could get two answers. The first run claims the call's file
# in CALLS_DIR (O_EXCL) and writes its decision there; every other run of the
# same call waits for that decision and returns it. A claim lives for
# CALL_RECORD_SECONDS, longer than a card's whole budget, and is pruned after.
CALLS_DIR = "calls"
CALL_RECORD_SECONDS = 300.0
# A twin waits as long as the first run may wait for its card.
TWIN_WAIT_SECONDS = agy_native_approval.TOTAL_SECONDS + 10.0
_TWIN_POLL_SECONDS = 0.05


def _call_key(raw: bytes):
    """conversationId + stepIdx + tool name + args, hashed; None when the
    payload does not name the call (no dedupe then)."""
    try:
        payload = json.loads(raw.decode("utf-8"))
        conversation, step = payload.get("conversationId"), payload.get("stepIdx")
        call = payload["toolCall"]
        name, args = call["name"], call.get("args")
    except Exception:
        return None
    if isinstance(conversation, bool) or isinstance(step, bool) or not isinstance(name, str):
        return None
    if not isinstance(conversation, (str, int)) or conversation == "":
        return None
    if not isinstance(step, (str, int)) or step == "":
        return None
    try:
        material = json.dumps([conversation, step, name, args], sort_keys=True,
                              ensure_ascii=True, default=repr)
    except (TypeError, ValueError, RecursionError):
        return None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]


def _prune_calls(folder: str, now: float) -> None:
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(path) > CALL_RECORD_SECONDS:
                os.unlink(path)
        except OSError:
            pass


def _read_decision(path: str):
    try:
        decision = _read_json_with_retry(path)
    except FileNotFoundError:
        return False
    except json.JSONDecodeError as err:
        return None if err.doc == "" else False
    except OSError:
        return None
    if not isinstance(decision, dict) or decision.get("decision") not in ("allow", "deny"):
        return False
    return decision


def _state_mode(state_path: str):
    try:
        return _read_json_with_retry(state_path)["mode"]
    except Exception:
        return None


def _twin_decision(claim: str, state_path: str) -> dict:
    deadline = time.monotonic() + TWIN_WAIT_SECONDS
    while True:
        decision = _read_decision(claim)
        if decision is False:
            return _deny(FAIL_REASON)  # pruned or garbled: nothing to share
        if decision is not None:
            out = {k: v for k, v in decision.items() if k != "mode"}
            if decision["decision"] == "allow":
                recorded_mode = decision.get("mode")
                current_mode = _state_mode(state_path)
                # A flip from balanced to auto only loosens: the first run's
                # allow (often a card just approved by that very flip) still
                # stands. Measured live 25 Nov 2026: agy 1.5.15 runs a twin for
                # every writer call and applies the twin's deny.
                loosened = recorded_mode == BALANCED_MODE and current_mode == "auto"
                if (current_mode not in ("auto", BALANCED_MODE, SIDE_MODE)
                        or recorded_mode is None
                        or (current_mode != recorded_mode and not loosened)):
                    # Closed, broken, or tightened since the first run decided.
                    return _deny(FAIL_REASON)
            return out
        if time.monotonic() >= deadline:
            return _deny(FAIL_REASON)
        time.sleep(_TWIN_POLL_SECONDS)


def _record_decision(claim: str, decision: dict, mode=None) -> None:
    folder = os.path.dirname(claim)
    record = {**decision, "mode": mode if mode is not None else decision.get("mode")}
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".call-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f)
        _replace_with_retry(tmp, claim)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def decide_once(raw: bytes, state_path: str, windows: bool = None, cwd: str = None) -> dict:
    """decide(), once per tool call however many hooks.json entries match it."""
    key = _call_key(raw)
    if key is None:
        return decide(raw, state_path, windows=windows, cwd=cwd)
    folder = os.path.join(os.path.dirname(os.path.abspath(state_path)), CALLS_DIR)
    claim = os.path.join(folder, key + ".json")
    try:
        os.makedirs(folder, exist_ok=True)
        _prune_calls(folder, time.time())
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return _twin_decision(claim, state_path)
    except OSError:
        # No record possible: decide alone (at worst a second card, never
        # a looser decision).
        return decide(raw, state_path, windows=windows, cwd=cwd)
    os.close(fd)
    mode = None
    try:
        if decide is _ORIGINAL_DECIDE:
            decision, mode = _decide(raw, state_path, windows=windows, cwd=cwd)
        else:
            decision = decide(raw, state_path, windows=windows, cwd=cwd)
            mode = _state_mode(state_path)
    except Exception:
        decision = _deny(FAIL_REASON)
    try:
        _record_decision(claim, decision, mode)
    except Exception:
        # A twin then times out and denies.
        pass
    return decision


def write_state(path: str, mode: str, launcher: str, workspace=None) -> None:
    """Atomic, so a hook never reads half a file (it would deny, not leak)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "launcher": launcher, "workspace": workspace}, f)
        _replace_with_retry(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        raw = sys.stdin.buffer.read()
        state_path = argv[argv.index("--state") + 1]
        decision = decide_once(raw, state_path)
    except Exception:
        decision = _deny(FAIL_REASON)
    sys.stdout.write(json.dumps(decision) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
