"""Fixed Unity file-protection rule, applied in every approval mode.

Inside a Unity project (a directory holding both Assets/ and ProjectSettings/;
the file must lie under one of those two) an agent may not:
  - write, create, delete, move or rename a .meta file: Unity owns them and a
    lost or duplicated .meta breaks the asset's GUID and every reference to it;
  - write a Unity-serialized YAML asset (scene, prefab, material, ...) as raw
    text, or delete/move one as a raw file, which orphans its .meta.
The Unity MCP tools stay the route for all of these: Unity writes the file and
moves the .meta with it. Outside a Unity project nothing is refused.

This is a rule, not an approval card: a refusal is returned to the model with
what to use instead, and no mode can switch it off.

Standard library only: agy_step_gate imports this on every gated agy tool call,
and importing anything under `providers` costs 3.3 s (see agy_step_gate).
The unity-mcp server keeps its own copy for Unity MCP tool calls
(unity-mcp/Server/src/services/protection_rules.py); it cannot import this one.
"""
import os
import re
from typing import List, NamedTuple, Optional

# Unity's text-serialized native asset types. ProjectSettings/*.asset are YAML
# too, and are covered by ".asset".
YAML_ASSET_EXTENSIONS = frozenset(ext.lower() for ext in (
    ".unity", ".prefab", ".asset", ".mat", ".controller", ".overrideController",
    ".anim", ".mask", ".physicMaterial", ".physicsMaterial", ".physicsMaterial2D",
    ".playable", ".signal", ".lighting", ".terrainlayer", ".spriteatlas",
    ".spriteatlasv2", ".guiskin", ".fontsettings", ".cubemap", ".flare",
    ".renderTexture", ".mixer", ".brush", ".preset", ".giparams", ".shadervariants",
    ".scenetemplate", ".vfx", ".vfxoperator", ".vfxblock",
    # preset libraries (Color/Gradient/Curve pickers)
    ".colors", ".gradients", ".curves", ".curvesNormalized", ".particleCurves",
    ".particleCurvesSigned", ".particleDoubleCurves", ".particleDoubleCurvesSigned",
))
META_EXTENSION = ".meta"

META_MESSAGE = ("Refused: {path} is a Unity .meta file. Unity owns .meta files; "
                "target the asset's own path (not its .meta) with manage_asset move/rename/delete, so the GUID stays intact.")
YAML_WRITE_MESSAGE = (
    "Refused: {path} is a Unity-serialized YAML asset and must not be written as raw text. "
    "Use the Unity MCP tools (manage_scene, manage_prefabs, manage_gameobject, "
    "manage_components, manage_material, manage_asset ...) so Unity writes the file.")
YAML_REMOVE_MESSAGE = (
    "Refused: deleting or moving {path} as a raw file orphans its .meta and loses its GUID. "
    "Use manage_asset (delete/move/rename) so Unity handles the asset and its .meta together.")

META_SUMMARY_TR = ("🚫 Unity koruması: {path} bir .meta dosyası; .meta dosyalarını Unity "
                   "yönetir, elle yazılamaz, silinemez, taşınamaz.")
YAML_WRITE_SUMMARY_TR = ("🚫 Unity koruması: {path} Unity'nin YAML varlığı; ham metin olarak "
                         "yazılamaz, Unity araçlarıyla değiştirilmeli.")
YAML_REMOVE_SUMMARY_TR = ("🚫 Unity koruması: {path} ham dosya olarak silinemez ya da taşınamaz "
                          "(.meta dosyası öksüz kalır); manage_asset kullanılmalı.")


class Refusal(NamedTuple):
    path: str
    message: str   # to the model
    summary: str   # to the UI (Turkish)


def _meta(path: str) -> Refusal:
    return Refusal(path, META_MESSAGE.format(path=path), META_SUMMARY_TR.format(path=path))


def _yaml_write(path: str) -> Refusal:
    return Refusal(path, YAML_WRITE_MESSAGE.format(path=path),
                   YAML_WRITE_SUMMARY_TR.format(path=path))


def _yaml_remove(path: str) -> Refusal:
    return Refusal(path, YAML_REMOVE_MESSAGE.format(path=path),
                   YAML_REMOVE_SUMMARY_TR.format(path=path))


def _leaf(path: str) -> str:
    leaf = re.split(r"[\\/]", path.strip().strip("\"'").rstrip("\\/"))[-1]
    if len(leaf) >= 2 and leaf[1] == ":" and leaf[0].isalpha():
        leaf = leaf[2:]  # drive-relative "C:x.meta"
    # Windows writes `a.prefab::$DATA` to a.prefab itself and drops trailing dots
    # and spaces, so `a.prefab. ` and `a.meta::$DATA` name the protected file.
    leaf = leaf.split(":", 1)[0]
    return leaf.rstrip(" .").lower()


def file_kind(path: str) -> Optional[str]:
    """"meta", "yaml" or None, from the file name alone."""
    if not isinstance(path, str) or not path.strip():
        return None
    leaf = _leaf(path)
    if leaf.endswith(META_EXTENSION):
        return "meta"
    if os.path.splitext(leaf)[1] in YAML_ASSET_EXTENSIONS:
        return "yaml"
    return None


# A generated NTFS 8.3 name keeps the first three letters of the extension after
# a "~<n>" tail: LongAssetName.prefab -> LONGAS~1.PRE, x.png.meta -> XPNG~1.MET.
_SHORT_NAME = re.compile(r"~\d+\.([a-z0-9]{1,3})$")
_SHORT_EXTENSIONS = {META_EXTENSION[1:4]: "meta"}
for _ext in YAML_ASSET_EXTENSIONS:
    _SHORT_EXTENSIONS.setdefault(_ext[1:4], "yaml")


def _alias_kind(path: str, base: str) -> Optional[str]:
    """The kind of the file Windows opens for `path` when its own name hides it.

    realpath resolves an existing file (or the existing part of the path) by
    handle and returns long names (measured, Python 3.13: LONGAS~1.PRE ->
    LongAssetName.prefab, PROJEC~1 -> ProjectSettings). A name that does not
    resolve but has the shape of an alias counts as the file it would alias:
    after a `cd` in a shell command it may name a file `base` cannot reach."""
    if os.name != "nt":
        return None
    try:
        real = os.path.realpath(_absolute(path, base))
        if os.path.lexists(real):
            return file_kind(real)
    except (OSError, ValueError):
        pass
    match = _SHORT_NAME.search(_leaf(path))
    return _SHORT_EXTENSIONS.get(match.group(1)) if match else None


def _kind(path, base: str) -> Optional[str]:
    kind = file_kind(path)
    if kind is None and isinstance(path, str) and path.strip():
        kind = _alias_kind(path, base)
    return kind


def _is_project_root(directory: str) -> bool:
    return (os.path.isdir(os.path.join(directory, "Assets"))
            and os.path.isdir(os.path.join(directory, "ProjectSettings")))


def _root_above(abs_path: str) -> Optional[str]:
    child, parent = abs_path, os.path.dirname(abs_path)
    while parent and parent != child:
        if (os.path.basename(child).lower() in ("assets", "projectsettings")
                and _is_project_root(parent)):
            return parent
        child, parent = parent, os.path.dirname(parent)
    return None


def _absolute(path: str, base: str) -> str:
    p = os.path.expanduser(path.strip().strip("\"'"))
    if not os.path.isabs(p) and base:
        p = os.path.join(os.path.expanduser(base), p)
    return os.path.abspath(p)


def in_unity_project(path: str, base: str = "") -> bool:
    """Does `path` (relative ones against `base`) lie under a Unity project's
    Assets/ or ProjectSettings/? A symlink into a project counts as inside."""
    try:
        absolute = _absolute(path, base)
        if _root_above(absolute):
            return True
        real = os.path.realpath(absolute)
        return real != absolute and _root_above(real) is not None
    except (OSError, ValueError):
        return False


def _base_in_project(base: str) -> bool:
    if not base:
        return False
    try:
        directory = os.path.abspath(os.path.expanduser(base))
    except (OSError, ValueError):
        return False
    while True:
        if _is_project_root(directory):
            return True
        parent = os.path.dirname(directory)
        if not parent or parent == directory:
            return False
        directory = parent


def check_write(path, base: str = "") -> Optional[Refusal]:
    """Refusal for writing, creating or overwriting `path` as raw text."""
    kind = _kind(path, base)
    if kind is None or not in_unity_project(path, base):
        return None
    return _meta(path) if kind == "meta" else _yaml_write(path)


def check_delete(path, base: str = "") -> Optional[Refusal]:
    """Refusal for deleting `path` as a raw file (not through Unity)."""
    kind = _kind(path, base)
    if kind is None or not in_unity_project(path, base):
        return None
    return _meta(path) if kind == "meta" else _yaml_remove(path)


def check_move(source, destination, base: str = "") -> Optional[Refusal]:
    """Refusal for moving/renaming `source` to `destination` as raw files."""
    return check_delete(source, base) or (
        check_write(destination, base) if destination else None)


# ── Shell commands: a heuristic, not a guarantee ─────────────────────────────
#
# A shell can reach a file in ways no parser of the command string follows
# (variables, scripts, `python -c`, `cd` then a relative name). This catches
# the direct forms: a delete/move verb together with a protected file token, a
# redirect or write cmdlet or in-place edit aimed at one, and a copy onto one.
# Reads (cat, type, grep, Get-Content ...) name no verb here and pass.

_DELETE_MOVE_VERBS = frozenset({
    "rm", "del", "erase", "rmdir", "rd", "unlink", "remove-item", "ri",
    "mv", "move", "ren", "rename", "move-item", "mi", "rename-item", "rni",
})
_WRITE_VERBS = frozenset({
    "set-content", "sc", "add-content", "ac", "out-file", "tee", "tee-object",
    "new-item", "ni", "clear-content", "clc", "touch", "dd", "truncate",
})
_COPY_VERBS = frozenset({"cp", "copy", "copy-item", "cpi", "xcopy", "robocopy"})
_IN_PLACE_EDITORS = frozenset({"sed", "perl"})

_TOKEN = re.compile(r"""
    (?P<sep>&&|\|\||[;&|\n])
  | (?P<redir>(?:\d|&|\*)?>>?)
  | "(?P<dq>[^"]*)"
  | '(?P<sq>[^']*)'
  | (?P<word>[^\s"'<>|;&]+)
  | (?P<other>\S)
""", re.VERBOSE)

_MAX_NESTING = 4


def _verb(word: str) -> str:
    name = re.split(r"[\\/]", word)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _segments(command: str):
    """[(words, redirect_targets, nested_strings)] per `;`/`&&`/`|` segment."""
    segments, words, targets, nested = [], [], [], []
    redirect_next = False
    for m in _TOKEN.finditer(command):
        if m.group("sep") is not None:
            segments.append((words, targets, nested))
            words, targets, nested = [], [], []
            redirect_next = False
            continue
        if m.group("redir") is not None:
            redirect_next = True
            continue
        text = next((m.group(g) for g in ("dq", "sq", "word") if m.group(g) is not None), None)
        if text is None:
            continue
        if m.group("word") is None and re.search(r"\s", text):
            nested.append(text)
        if redirect_next:
            targets.append(text)
            redirect_next = False
        else:
            words.append(text)
    segments.append((words, targets, nested))
    return segments


def _candidates(word: str) -> List[str]:
    out = [word]
    if "=" in word:
        out.append(word.rsplit("=", 1)[1])  # dd of=..., --output=...
    return out


def _protected_kind(token: str, base: str, base_in_project: bool) -> Optional[str]:
    kind = _kind(token, base)
    if kind is None:
        return None
    if in_unity_project(token, base):
        return kind
    stripped = os.path.expanduser(token.strip().strip("\"'"))
    if (base_in_project and not os.path.isabs(stripped)
            and ".." not in re.split(r"[\\/]", stripped)):
        # A relative name run from inside a Unity project: after a `cd` in the
        # same command it may point anywhere in it.
        return kind
    return None


def _check_shell(command: str, base: str, base_in_project: bool, depth: int) -> Optional[Refusal]:
    for words, redirect_targets, nested in _segments(command):
        verbs = {_verb(w) for w in words}
        in_place = bool(verbs & _IN_PLACE_EDITORS) and any(
            w == "--in-place" or re.fullmatch(r"-[a-zA-Z]*i\S*", w) for w in words)
        write_targets = list(redirect_targets)
        if verbs & _WRITE_VERBS or in_place:
            write_targets.extend(words)
        if verbs & _COPY_VERBS:
            operands = [w for w in words if not w.startswith("-") and _verb(w) not in _COPY_VERBS]
            if operands:
                write_targets.append(operands[-1])
        for word in write_targets:
            for token in _candidates(word):
                kind = _protected_kind(token, base, base_in_project)
                if kind:
                    return _meta(token) if kind == "meta" else _yaml_write(token)
        if verbs & _DELETE_MOVE_VERBS:
            for word in words:
                for token in _candidates(word):
                    kind = _protected_kind(token, base, base_in_project)
                    if kind:
                        return _meta(token) if kind == "meta" else _yaml_remove(token)
        if depth < _MAX_NESTING:
            # `cmd /c "del x.meta"`, `powershell -Command "Remove-Item x.meta"`
            for text in nested:
                refusal = _check_shell(text, base, base_in_project, depth + 1)
                if refusal:
                    return refusal
    return None


def check_shell(command, base: str = "") -> Optional[Refusal]:
    """Refusal for a shell command that deletes/moves/writes a protected file.

    Heuristic only (see above). Relative names count as inside when `base`
    (the command's working directory) is inside a Unity project.
    """
    if not isinstance(command, str) or not command.strip():
        return None
    return _check_shell(command, base or "", _base_in_project(base or ""), 0)
