"""Step-mode PreToolUse hook for agy (`backend agy-hook --state <file>`).

agy runs this for its built-in file writers, run_command and
send_command_input (see providers/agy_provider.py, _write_step_gate). It
reads the hook payload on stdin and prints {"decision": "allow"|"deny"}.

Why a top-level module and not providers/: importing anything under
`providers` runs the package __init__, which imports every provider SDK
(openai, anthropic, google.genai, ollama): 3.3 s measured, paid on every
gated tool call. This file imports only the standard library.

Fail closed: an unreadable state file, payload or unknown tool is a deny.

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
token that holds none of the characters PowerShell, POSIX shells or cmd.exe
treat specially. BODY is data: both quoted forms are literal, and the only
text that could end them early (a line starting with '@, or a line equal to
UNITYAI_EOF) is refused inside it.
"""
import json
import os
import re
import sys
import tempfile

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
    "list-dir, bash). Argument values may not contain quotes or any of $ ` & | < > ^ % ! ( ), "
    "and nothing may come before or after the unityai call.")
FAIL_REASON = "Gamachine step gate could not verify this call, so it is blocked."

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
_FORBIDDEN = set("\"'$`&|<>^%!()")
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
    return not any(c in _FORBIDDEN or ord(c) < 0x20 or c == "\x7f" for c in text)


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
        return os.path.normcase(a) == os.path.normcase(b)
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
        end = next((k for k in range(1, len(lines)) if lines[k].startswith("'@")), None)
        if end is None or end != len(lines) - 1:
            return False
        tail = lines[end]
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


def decide(raw: bytes, state_path: str, windows: bool = None) -> dict:
    windows = (sys.platform == "win32") if windows is None else windows
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        mode = state["mode"]
        launcher = state["launcher"]
    except Exception:
        return _deny(FAIL_REASON)
    if mode == "auto":
        return {"decision": "allow"}
    if mode != "step":
        return _deny(FAIL_REASON)
    try:
        call = json.loads(raw.decode("utf-8"))["toolCall"]
        name = call["name"]
    except Exception:
        return _deny(FAIL_REASON)
    if name == RUN_TOOL:
        try:
            command = call["args"]["CommandLine"]
        except Exception:
            return _deny(FAIL_REASON)
        if unityai_command_allowed(command, launcher, windows):
            return {"decision": "allow"}
        return _deny(RUN_REASON)
    if name in GATED_TOOLS:
        return _deny(WRITE_REASON)
    return _deny(FAIL_REASON)


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
