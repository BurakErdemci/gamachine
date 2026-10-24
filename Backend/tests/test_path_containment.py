"""path_containment: the one "is this path inside that root" answer."""
import os
import sys

import pytest

from path_containment import is_within, resolve_within


@pytest.fixture
def tree(tmp_path):
    base = tmp_path.resolve()
    ws = base / "Game"
    (ws / "Assets").mkdir(parents=True)
    (base / "Gamebackup" / "Assets").mkdir(parents=True)
    outside = base / "outside"
    outside.mkdir()
    (base / "wslink").symlink_to(ws)
    (ws / "escape").symlink_to(outside)
    return base, ws


def test_inside_and_root_itself(tree):
    _, ws = tree
    assert is_within(str(ws / "Assets" / "A.cs"), str(ws)) is True
    assert is_within(str(ws), str(ws)) is True
    assert resolve_within(str(ws), str(ws)) == str(ws)


def test_relative_path_is_taken_against_the_root_not_the_cwd(tree, monkeypatch):
    base, ws = tree
    monkeypatch.chdir(base)
    assert is_within("Assets/New/Deep.cs", str(ws)) is True
    assert resolve_within("Assets/New/Deep.cs", str(ws)) == str(ws / "Assets" / "New" / "Deep.cs")


def test_symlinked_root_works_on_either_side(tree):
    base, ws = tree
    assert is_within(str(base / "wslink" / "Assets" / "A.cs"), str(ws)) is True
    assert is_within(str(ws / "Assets" / "A.cs"), str(base / "wslink")) is True


def test_escaping_symlink_is_outside(tree):
    _, ws = tree
    assert is_within(str(ws / "escape" / "x.cs"), str(ws)) is False
    assert resolve_within(str(ws / "escape" / "x.cs"), str(ws)) is None


def test_sibling_with_the_same_prefix_is_outside(tree):
    base, ws = tree
    assert is_within(str(base / "Gamebackup" / "Assets" / "x.cs"), str(ws)) is False
    assert is_within("../Gamebackup/x.cs", str(ws)) is False
    assert resolve_within("../Gamebackup/x.cs", str(ws)) is None


def test_home_shorthand_is_expanded_and_then_checked(tree, monkeypatch):
    _, ws = tree
    monkeypatch.setenv("HOME", str(ws / "Assets"))
    monkeypatch.setenv("USERPROFILE", str(ws / "Assets"))
    assert is_within("~/x.cs", str(ws)) is True
    assert is_within("~/x.cs", str(ws / "Assets" / "nope")) is False


def test_outside_home_shorthand_is_outside(tree, monkeypatch):
    base, ws = tree
    monkeypatch.setenv("HOME", str(base / "outside"))
    monkeypatch.setenv("USERPROFILE", str(base / "outside"))
    assert is_within("~/x.cs", str(ws)) is False


@pytest.mark.parametrize("root", ["", None, 5, b"/tmp"])
def test_missing_or_non_string_root_is_never_inside(root):
    assert is_within("/etc/passwd", root) is False
    assert is_within("Assets/A.cs", root) is False
    assert resolve_within("Assets/A.cs", root) is None


@pytest.mark.parametrize("path", [None, 5, b"/tmp/x", ["/tmp/x"]])
def test_non_string_path_is_never_inside(tree, path):
    _, ws = tree
    assert is_within(path, str(ws)) is False
    assert resolve_within(path, str(ws)) is None


def test_different_drive_value_error_means_outside(tree, monkeypatch):
    _, ws = tree

    def boom(_paths):
        raise ValueError("Paths don't have the same drive")

    monkeypatch.setattr(os.path, "commonpath", boom)
    assert is_within(str(ws / "Assets"), str(ws)) is False
    assert resolve_within(str(ws / "Assets"), str(ws)) is None


def test_os_error_means_outside(tree, monkeypatch):
    _, ws = tree

    def boom(_p):
        raise OSError("loop")

    monkeypatch.setattr(os.path, "realpath", boom)
    assert is_within(str(ws / "Assets"), str(ws)) is False
