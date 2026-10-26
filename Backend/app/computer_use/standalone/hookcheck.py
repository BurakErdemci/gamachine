"""Check whether Claude Code asks the owner before local confirmations."""
import json
import os
from pathlib import Path
import re


def confirm_hook_installed(paths=None) -> bool:
    try:
        if paths is None:
            override = os.environ.get("GAMACHINE_CU_CLAUDE_SETTINGS")
            paths = (override.split(os.pathsep) if override is not None else
                     [Path.home() / ".claude" / "settings.json", Path.home() / ".claude" / "settings.local.json"])
        for path in paths:
            try:
                settings = json.loads(Path(path).read_text(encoding="utf-8"))
                entries = settings["hooks"]["PreToolUse"]
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("matcher"), str):
                        continue
                    try:
                        matches = re.fullmatch(entry["matcher"], "mcp__gamachineComputerLocal__computer_click")
                    except re.error:
                        continue
                    hooks = entry.get("hooks")
                    if matches and isinstance(hooks, list) and any(
                            isinstance(hook, dict) and isinstance(hook.get("command"), str)
                            and "guard-computer-use-confirm" in hook["command"] for hook in hooks):
                        return True
            except (OSError, ValueError, TypeError, KeyError):
                continue
    except Exception:
        return False
    return False
