"""Check whether Claude Code asks the owner before local confirmations."""
import json
import os
from pathlib import Path
import re

from .. import policy


def confirm_hook_installed(paths=None) -> bool:
    try:
        if paths is None:
            override = os.environ.get("GAMACHINE_CU_CLAUDE_SETTINGS")
            paths = (override.split(os.pathsep) if override is not None else
                     [Path.home() / ".claude" / "settings.json", Path.home() / ".claude" / "settings.local.json"])
        loaded = []
        for path in paths:
            try:
                loaded.append(json.loads(Path(path).read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
        # Claude Code merges the settings files and disableAllHooks is one switch for all of them.
        if any(isinstance(settings, dict) and settings.get("disableAllHooks") is True for settings in loaded):
            return False
        required = ["mcp__gamachineComputerLocal__" + tool for tool in policy.TOOLS]
        for settings in loaded:
            try:
                entries = settings["hooks"]["PreToolUse"]
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("matcher"), str):
                        continue
                    try:
                        matches = all(re.fullmatch(entry["matcher"], name) for name in required)
                    except re.error:
                        continue
                    hooks = entry.get("hooks")
                    if matches and isinstance(hooks, list) and any(
                            isinstance(hook, dict) and isinstance(hook.get("command"), str)
                            and hook.get("type") == "command" and hook["command"].strip() in {
                                'bash "$HOME/.claude/hooks/guard-computer-use-confirm.sh"',
                                'bash ~/.claude/hooks/guard-computer-use-confirm.sh',
                            } for hook in hooks):
                        return True
            except (OSError, ValueError, TypeError, KeyError):
                continue
    except Exception:
        return False
    return False
