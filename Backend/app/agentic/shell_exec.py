"""The one path an approved shell command takes to run.

`tools/file_tools.run_command`, `unity_ai_mcp/tools/bash_tool._run_command` and
`unityai_cli.cmd_bash` are built on it; they differ only in who shows the
approval card and how the result is written. The protection rule (protected-file
refusal -> argv for a command that may run unapproved -> the shell only for an
approved raw string) lives here once: a security fix on this path used to have
to be applied in three places.
"""
import os
import re
import subprocess
from typing import NamedTuple

import unity_file_guard
from agentic.command_safety import auto_safe_argv
from path_containment import resolve_within


def shell_refusal(command: str, workspace: str) -> str | None:
    """The refusal text for a command that touches a protected file, else None."""
    refusal = unity_file_guard.check_shell(command, workspace)
    return None if refusal is None else refusal.message


def plan(command: str, workspace: str) -> list[str] | None:
    """The argv of a command that may run unapproved; None = the raw string needs approval.

    The decision and the token list come from ONE call. Taking them separately
    (asking "is it safe" and then running the raw string) let the checked text
    and the executed text drift apart; findings C1/C2/C3/P2 all went through
    that gap.
    """
    return auto_safe_argv(command, workspace)


def run(command: str, argv: list[str] | None, workspace: str) -> subprocess.CompletedProcess:
    """Runs the command; `subprocess.TimeoutExpired` propagates.

    A command that passed without approval never reaches a shell: its argv runs
    directly, so `%VAR%` expansion, a second parse and chaining are structurally
    impossible. An approved raw string still runs through the shell: the user saw
    exactly that string and approved it (the caller got the approval BEFORE this
    call). `GIT_TERMINAL_PROMPT=0` keeps git and similar tools from prompting.
    """
    return subprocess.run(
        command if argv is None else argv,
        shell=argv is None,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _parse_file_write(command: str):
    """(path, content) when a shell command writes a file, else None.

    Recognised forms: python3/python -c with open(..., 'w').write(...),
    printf/echo 'content' > path, printf/echo ... | dd of=path, dd ... of=path.
    """
    c = command.strip()

    # python3 -c "...open('path','w')...write('content')..."
    m = re.search(
        r"""open\(\s*['"]([^'"]+)['"]\s*,\s*['"]w['"]\).*?\.write\(\s*['"](.+?)['"]\s*\)""",
        c, re.DOTALL
    )
    if m:
        path = m.group(1)
        content = m.group(2).replace("\\n", "\n").replace("\\'", "'").replace('\\"', '"')
        return path, content

    # printf 'content' > path  /  echo 'content' > path
    m = re.search(r'(?:printf|echo)\s+[\'"](.+?)[\'"]\s*>\s*(\S+)', c, re.DOTALL)
    if m:
        content = m.group(1).replace("\\n", "\n")
        return m.group(2), content

    # printf 'content' | dd of=path  /  echo 'content' | dd of=path
    m = re.search(r'(?:printf|echo)\s+[\'"](.+?)[\'"]\s*\|.*?dd\s+of=(\S+)', c, re.DOTALL)
    if m:
        content = m.group(1).replace("\\n", "\n")
        return m.group(2), content

    # dd if=... of=path
    m = re.search(r'\bdd\b.*?\bof=(\S+)', c)
    if m:
        return m.group(1), ""  # the content cannot be parsed, the path is known

    return None


class FileWrite(NamedTuple):
    """A shell command turned into a file write.

    For empty (or whitespace-only) `content`, `abs_path` and `refusal` are not
    computed: each caller refuses with its own "empty file" text. A set `refusal`
    means `unity_file_guard.check_write` refused the write.
    """
    path: str
    content: str
    abs_path: str | None
    original: str
    refusal: str | None


def prepare_file_write(command: str, workspace: str) -> FileWrite | None:
    """Everything the approval card needs when the command writes a file, else None.

    A target outside the workspace raises the same `PermissionError` text as `_resolve`.
    """
    parsed = _parse_file_write(command)
    if not parsed:
        return None
    path, content = parsed
    if not content.strip():
        return FileWrite(path, content, None, "", None)
    abs_path = resolve_within(path, workspace)
    if abs_path is None:
        raise PermissionError(f"Güvenlik: {path} workspace dışında.")
    refusal = unity_file_guard.check_write(abs_path, workspace)
    if refusal is not None:
        return FileWrite(path, content, abs_path, "", refusal.message)
    original = ""
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()
        except Exception:
            pass
    return FileWrite(path, content, abs_path, original, None)
