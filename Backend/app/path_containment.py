"""The one answer to "is this path inside that root".

A relative path is taken relative to `root` (not the process cwd), `~` is
expanded, and symlinks are resolved on BOTH sides, so a root that itself sits
under a link (macOS `/tmp` -> `/private/tmp`) still works while a link inside
the root that points out of it is outside. The comparison is per path
component (`Gamebackup` is not inside `Game`) and case-normalised on Windows.

An empty or non-string root is never "inside anything": every check fails
closed instead of treating a missing workspace as "no restriction".
"""
import os


def _joined(path, root):
    p = os.path.expanduser(path)
    return p if os.path.isabs(p) else os.path.join(os.path.expanduser(root), p)


def is_within(path: str, root: str) -> bool:
    """True when `path` (or `root` itself) is inside `root` after links are resolved."""
    if not isinstance(root, str) or not root or not isinstance(path, str):
        return False
    try:
        target = os.path.normcase(os.path.realpath(_joined(path, root)))
        base = os.path.normcase(os.path.realpath(os.path.expanduser(root)))
        return os.path.commonpath([target, base]) == base
    except (OSError, ValueError):
        return False  # ValueError: different drives, no common path


def resolve_within(path: str, root: str) -> str | None:
    """The realpath of `path` when it is inside `root`, else None."""
    if not is_within(path, root):
        return None
    return os.path.realpath(_joined(path, root))
