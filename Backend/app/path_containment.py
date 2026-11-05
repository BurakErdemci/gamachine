"""The one answer to "is this path inside that root".

A relative path is taken relative to `root` (not the process cwd), `~` is
expanded, and symlinks are resolved on BOTH sides, so a root that itself sits
under a link (macOS `/tmp` -> `/private/tmp`) still works while a link inside
the root that points out of it is outside. The comparison is per path
component (`Gamebackup` is not inside `Game`) and case-normalised on Windows.
Elsewhere a spelling that differs is settled by filesystem identity, never by
folding strings: APFS opens one folder under NFC, NFD and case variants, but a
case-sensitive volume keeps `Game` and `GAME` apart.

An empty or non-string root is never "inside anything": every check fails
closed instead of treating a missing workspace as "no restriction".
"""
import os
import stat


def _joined(path, root):
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else os.path.join(os.path.expanduser(root), p)


def parts_below(target: str, root: str) -> list | None:
    """The names from `root` down to `target` (both realpaths) by file identity, else None.

    A target that does not exist yet is judged by its deepest existing ancestor.
    Windows answers None: its paths are settled by normcase alone.
    """
    if os.name == "nt":
        return None
    try:
        found = os.stat(root)
        # A file root can have a hard link elsewhere; a zero inode identifies nothing.
        if not stat.S_ISDIR(found.st_mode) or not found.st_ino:
            return None
        wanted = (found.st_dev, found.st_ino)
        names = []
        current = target
        while True:
            try:
                found = os.stat(current)
            except (FileNotFoundError, NotADirectoryError):
                pass
            else:
                if (found.st_dev, found.st_ino) == wanted:
                    return names[::-1]
            parent, name = os.path.split(current)
            if parent == current:
                return None
            names.append(name)
            current = parent
    except (OSError, ValueError):
        return None  # unreadable ancestor or link loop: cannot be proved inside


def is_within(path: str, root: str) -> bool:
    """True when `path` (or `root` itself) is inside `root` after links are resolved."""
    if not isinstance(root, str) or not root or not isinstance(path, str):
        return False
    try:
        target = os.path.normcase(os.path.realpath(_joined(path, root)))
        base = os.path.normcase(os.path.realpath(os.path.expanduser(root)))
        return os.path.commonpath([target, base]) == base or parts_below(target, base) is not None
    except (OSError, ValueError):
        return False  # ValueError: different drives, no common path


def resolve_within(path: str, root: str) -> str | None:
    """The realpath of `path` when it is inside `root`, else None."""
    if not is_within(path, root):
        return None
    return os.path.realpath(_joined(path, root))
