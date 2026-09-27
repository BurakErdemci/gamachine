"""PreToolUse hook for agy (`backend agy-hook --state <file>`).

agy runs this for its built-in file writers, run_command and
send_command_input (see providers/agy_provider.py, _write_step_gate), and a
side question's process also for every tool in SIDE_HOOK_MATCHERS. It
reads the hook payload on stdin and prints {"decision": "allow"|"deny"}.
A fifth state, "side", holds only while a read-only side question runs.

It is installed in every approval mode: the state file's "mode" decides.
"auto" allows every call, "step" applies the rules below, and "balanced"
(Burak, 27 Sep 2026) allows what step allows plus the built-in writes and
commands action_risk calls routine; a critical one is denied with a pointer
to the unityai bridge, which raises the card. In all three, the fixed Unity
file rule (unity_file_guard: no .meta writes/deletes/moves, no raw writes to
Unity YAML assets) is checked first and can only deny. A process spawned in
auto must still be gated after a flip, and agy reads its hooks only at
start, so the mode cannot live in hooks.json.

Why a top-level module and not providers/: importing anything under
`providers` runs the package __init__, which imports every provider SDK
(openai, anthropic, google.genai, ollama): 3.3 s measured, paid on every
gated tool call. This file imports only the standard library.

Fail closed: an unreadable state file or payload is a deny, and so is any
tool in "side" that is not a proven read. A tool outside the step set in
auto/step/balanced is allowed: before side questions no hook ran for it.

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
import json
import ntpath
import os
import re
import sys
import tempfile

import unity_file_guard  # stdlib-only, like this file

# Built-in agy tools that write files, plus send_command_input (it types
# into a running process, e.g. a shell a unityai bash call started).
GATED_TOOLS = (
    "write_to_file", "replace_file_content", "multi_replace_file_content",
    "sed_file", "notebook_edit", "send_command_input",
)
RUN_TOOL = "run_command"

WRITE_REASON = ("Gamachine step mode: built-in file writes are blocked. Write files only "
                "with the unityai bridge (unityai save-file), so an approval card is shown.")
RUN_REASON = (
    "Gamachine step mode: shell commands are blocked; only the unityai bridge may run, in "
    "exactly the form given in your instructions (save-file, delete-file, read-file, "
    "list-dir, bash). Argument values may hold only ASCII letters, digits, spaces, Turkish "
    "letters and - _ . / \\ : , + = @ # ~ * ? [ ] { } (no quotes inside, none of "
    "; $ ` & | < > ^ % ! ( ), no typographic quotes or dashes), and nothing may come "
    "before or after the unityai call.")
FAIL_REASON = "Gamachine step gate could not verify this call, so it is blocked."
BALANCED_MODE = "balanced"
BALANCED_REASON = (
    "Gamachine Safe Auto mode: this {what} is critical ({reason}{detail}), so it needs the "
    "user's approval. Do it through the unityai bridge in exactly the form given in your "
    "instructions (save-file, delete-file or bash) so an approval card is shown; argument "
    "values follow the same character rules as in step mode.")
# The workspace hook entry (written by agy_provider._write_step_gate). The
# state file is shared by every agy child, so balanced mode finds the
# workspace a call confines to by walking up from the hook's working
# directory to the directory whose .agents/hooks.json holds this entry.
STEP_GATE_KEY = "gamachine-step-gate"
STEP_GATE_HOOKS_FILE = ".agents/hooks.json"
# Written for a child whose session is closing (agy_session.close): it denies
# every call in either approval mode.
CLOSED_MODE = "closed"
CLOSED_REASON = "Gamachine closed this agy session; no tool may run in it."

# Written while a read-only side question runs on agy (Burak, 27 Sep 2026).
# agy runs one turn machine-wide and the side turn holds that lock, so the one
# shared state file can say "side" for exactly that turn. Only the tools
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
# Every name a side process's hooks.json matches besides the step tools. The
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
        if word != "&" and not _BARE.fullmatch(word):
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


# agy's file writers name their target in TargetFile, one variant in target_file
# (read from the agy binary's tool schemas, 26 Sep 2026). notebook_edit takes
# only .ipynb paths, so it can never name a protected Unity file.
_FILE_WRITERS = ("write_to_file", "replace_file_content", "multi_replace_file_content", "sed_file")


def _unity_file_refusal(raw: bytes):
    """The fixed Unity file rule (unity_file_guard), in every mode.

    It can only deny. An unreadable payload returns None and is left to the
    mode rules, so this adds no allow path and changes no mode's verdict for
    calls the rule does not name.
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
        for key, value in args.items():
            if isinstance(key, str) and key.replace("_", "").lower() == "targetfile":
                refusal = unity_file_guard.check_write(value, os.getcwd())
                if refusal is not None:
                    return refusal
    return None


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


def _balanced_risk(name: str, args, cwd: str):
    """action_risk's verdict for a built-in agy call in balanced mode."""
    import action_risk  # stdlib-only; imported only in balanced mode
    workspace = _hook_workspace(cwd)
    if not workspace or not isinstance(args, dict):
        return action_risk.Risk(action_risk.CRITICAL, "unknown_action", name)
    if name == RUN_TOOL:
        run_cwd = args.get("Cwd") if isinstance(args.get("Cwd"), str) and args.get("Cwd") else cwd
        return action_risk.classify({"kind": "shell", "command": args.get("CommandLine"),
                                     "cwd": run_cwd, "workspace": workspace})
    if name in _FILE_WRITERS:
        targets = [value for key, value in args.items()
                   if isinstance(key, str) and key.replace("_", "").lower() == "targetfile"]
        return action_risk.classify({"kind": "file_write", "paths": targets,
                                     "cwd": cwd, "workspace": workspace})
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


def decide(raw: bytes, state_path: str, windows: bool = None, cwd: str = None) -> dict:
    windows = (sys.platform == "win32") if windows is None else windows
    cwd = os.getcwd() if cwd is None else cwd
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        mode = state["mode"]
        launcher = state["launcher"]
    except Exception:
        return _deny(FAIL_REASON)
    if mode == SIDE_MODE:
        return _side_decision(raw)
    if mode in ("auto", "step", BALANCED_MODE):
        refusal = _unity_file_refusal(raw)
        if refusal is not None:
            return _deny(refusal.message)
    if mode == "auto":
        return {"decision": "allow"}
    if mode == CLOSED_MODE:
        return _deny(CLOSED_REASON)
    if mode not in ("step", BALANCED_MODE):
        return _deny(FAIL_REASON)
    try:
        call = json.loads(raw.decode("utf-8"))["toolCall"]
        name = call["name"]
    except Exception:
        return _deny(FAIL_REASON)
    if not isinstance(name, str):
        return _deny(FAIL_REASON)
    if name != RUN_TOOL and name not in GATED_TOOLS:
        # Only a side process's hooks.json names more tools than these
        # (SIDE_HOOK_MATCHERS). Outside a side turn such a call keeps the
        # verdict it had when no hook ran for it: allowed.
        return {"decision": "allow"}
    if name == RUN_TOOL:
        try:
            command = call["args"]["CommandLine"]
        except Exception:
            return _deny(FAIL_REASON)
        if unityai_command_allowed(command, launcher, windows):
            return {"decision": "allow"}
        if mode == BALANCED_MODE:
            risk = _balanced_risk(name, call.get("args"), cwd)
            if not risk.critical:
                return {"decision": "allow"}
            return _balanced_deny("command", risk)
        return _deny(RUN_REASON)
    if mode == BALANCED_MODE:
        risk = _balanced_risk(name, call.get("args"), cwd)
        if not risk.critical:
            return {"decision": "allow"}
        return _balanced_deny("file write" if name in _FILE_WRITERS else "call", risk)
    return _deny(WRITE_REASON)


def write_state(path: str, mode: str, launcher: str) -> None:
    """Atomic, so a hook never reads half a file (it would deny, not leak)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "launcher": launcher}, f)
        os.replace(tmp, path)
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
        decision = decide(raw, state_path)
    except Exception:
        decision = _deny(FAIL_REASON)
    sys.stdout.write(json.dumps(decision) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
