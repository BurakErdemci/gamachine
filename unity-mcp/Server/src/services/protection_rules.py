"""
Fixed Unity file rule for Unity MCP tool calls: no call may write, delete, move
or rename a ``.meta`` file. Unity owns them; a lost or duplicated ``.meta``
breaks the asset's GUID and every reference to it.

It is a rule, not an approval card: the middleware applies it before the
approval gate, so it also holds in auto mode and no card is shown for a call
that would be refused anyway.

The Gamachine backend keeps its own copy for its raw-file tools and shells
(Backend/app/unity_file_guard.py), which also refuses raw writes to Unity YAML
assets; this server's tools are the route that makes Unity write those, so only
the ``.meta`` half applies here. Script tools need no check: ManageScript builds
``{name}.cs`` from a name matching ``^[a-zA-Z_][a-zA-Z0-9_]*$``.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator, Mapping

from services.registry.tool_actions import (
    WRITE, _key_folded, classify, sole_value, tool_entry,
)

META_MESSAGE = (
    "Refused: {path} is a Unity .meta file. Unity owns .meta files; "
    "target the asset's own path (not its .meta) with manage_asset move/rename/delete, so the GUID stays intact."
)

# Folded parameter names (``_key_folded``) that can carry a file path.
_PATH_KEY_SUFFIXES = (
    "path", "paths", "file", "files", "filename", "folder", "dir", "directory",
    "destination", "dest", "uri", "uris", "target", "targets",
)

# Classified write by the ledger only for their AssetDatabase refresh
# (manage_asset.py preflight); they do not touch the file they name.
_FILE_READ_ACTIONS = {"manage_asset": {"search", "get_info", "get_components"}}

_MAX_DEPTH = 16


def _names_meta(value: str) -> bool:
    leaf = re.split(r"[\\/]", value.strip().strip("\"'").rstrip("\\/"))[-1]
    # Windows writes `x.meta::$DATA` to x.meta and drops trailing dots/spaces.
    leaf = leaf.split(":", 1)[0].rstrip(" .")
    return leaf.lower().endswith(".meta")


def _is_path_key(key: Any) -> bool:
    return isinstance(key, str) and _key_folded(key).endswith(_PATH_KEY_SUFFIXES)


def _find_meta(value: Any, path_key: bool, depth: int = 0) -> str | None:
    """First path-like value naming a .meta file, anywhere in ``value``."""
    if depth > _MAX_DEPTH:
        # Too deep to walk: refuse if a .meta is mentioned at all.
        text = json.dumps(value, default=str)
        return "(deeply nested value)" if ".meta" in text.lower() else None
    if isinstance(value, str):
        if path_key and _names_meta(value):
            return value
        stripped = value.strip()
        if stripped[:1] in ("{", "["):
            # Several tools accept JSON-encoded objects (properties, commands).
            try:
                decoded = json.loads(stripped)
            except ValueError:
                return None
            return _find_meta(decoded, path_key, depth + 1)
        return None
    if isinstance(value, Mapping):
        for key, inner in value.items():
            hit = _find_meta(inner, _is_path_key(key), depth + 1)
            if hit:
                return hit
    elif isinstance(value, (list, tuple)):
        for inner in value:
            hit = _find_meta(inner, path_key, depth + 1)
            if hit:
                return hit
    return None


def _nested_calls(entry: Mapping[str, Any], params: Mapping[str, Any]) -> Iterator[tuple[Any, Any]]:
    """Every (name, params) a batch could dispatch, matched with the collision
    fold like ``tool_actions.nested_tool_names``. A command whose name or params
    cannot be read yields (None, command) so the caller scans it whole."""
    tool_key = _key_folded(entry.get("recursive_tool_key", "tool"))
    params_key = _key_folded(entry.get("recursive_params_key", "params"))
    commands_key = _key_folded(entry["recursive_field"])
    for key, commands in params.items():
        if not (isinstance(key, str) and _key_folded(key) == commands_key):
            continue
        if isinstance(commands, str):
            try:
                commands = json.loads(commands)
            except ValueError:
                yield None, commands
                continue
        if not isinstance(commands, (list, tuple)):
            yield None, commands
            continue
        for command in commands:
            if not isinstance(command, Mapping):
                yield None, command
                continue
            names = [v for k, v in command.items()
                     if isinstance(k, str) and _key_folded(k) == tool_key]
            inners = [v for k, v in command.items()
                      if isinstance(k, str) and _key_folded(k) == params_key]
            if not names or any(not isinstance(n, str) for n in names) \
                    or any(not isinstance(p, Mapping) for p in inners):
                yield None, command
                continue
            for name in names:
                for inner in inners or [{}]:
                    yield name, inner


def _touches_file(tool_name: str, params: Mapping[str, Any]) -> bool:
    if classify(tool_name, params) != WRITE:
        return False
    action = sole_value(params, "action")
    reads = _FILE_READ_ACTIONS.get(tool_name, ())
    return not (isinstance(action, str) and action.lower() in reads)


def meta_refusal(tool_name: str, params: Any, *, _depth: int = 0) -> str | None:
    """The refusal message when this call would write/delete/move a .meta file."""
    if not isinstance(params, Mapping):
        return None
    entry = tool_entry(tool_name)
    if entry and entry.get("recursive_field"):
        if _depth >= _MAX_DEPTH:
            hit = _find_meta(params, True)
            return META_MESSAGE.format(path=hit) if hit else None
        for name, inner in _nested_calls(entry, params):
            if name is None:
                hit = _find_meta(inner, True)
                refusal = META_MESSAGE.format(path=hit) if hit else None
            else:
                refusal = meta_refusal(name, inner, _depth=_depth + 1)
            if refusal:
                return refusal
        return None
    if not _touches_file(tool_name, params):
        return None
    hit = _find_meta(params, False)
    return META_MESSAGE.format(path=hit) if hit else None
