"""Cheap diagnostics from the current Unity editor session's log."""

import ntpath
import os
import re
import sys


def editor_log_path(platform: str | None = None, env=None, home=None) -> str | None:
    """Locate Editor.log without falling back to a previous editor session."""
    try:
        platform = sys.platform if platform is None else platform
        env = os.environ if env is None else env
        if platform == "win32":
            local = env.get("LOCALAPPDATA")
            return ntpath.join(local, "Unity", "Editor", "Editor.log") if local else None
        home = os.path.expanduser("~") if home is None else home
        if platform == "darwin":
            return os.path.join(home, "Library", "Logs", "Unity", "Editor.log")
        return os.path.join(home, ".config", "unity3d", "Editor.log")
    except Exception:
        return None


def bridge_compile_errors(path: str, limit: int = 5) -> list[str]:
    """Return distinct bridge errors from the latest compile in a bounded tail."""
    try:
        if not path or limit <= 0:
            return []
        with open(path, "rb") as log:
            log.seek(0, os.SEEK_END)
            start = max(0, log.tell() - 2 * 1024 * 1024)
            log.seek(start)
            tail = log.read(2 * 1024 * 1024)
        if start:
            # The tail may begin inside a line; only complete lines are diagnostics.
            tail = tail.partition(b"\n")[2]
        lines = tail.decode("utf-8", errors="replace").splitlines()
        last_compile = 0
        for index, line in enumerate(lines):
            if line.startswith("[ScriptCompilation] Requested script compilation"):
                last_compile = index + 1
        errors = []
        seen = set()
        for line in lines[last_compile:]:
            if "error CS" not in line or not (
                "MCPForUnity" in line or "com.coplaydev.unity-mcp" in line
            ):
                continue
            match = re.search(r"([^/\\\r\n]+\.cs\(\d+,\d+\):\s*error CS\d+:.*)", line)
            if match and match.group(1) not in seen:
                shortened = match.group(1)
                seen.add(shortened)
                errors.append(shortened)
                if len(errors) >= limit:
                    break
        return errors
    except Exception:
        return []
