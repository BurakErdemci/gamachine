"""A path is judged by the folder it really names, on a real filesystem.

Darwin cases need a volume that folds case and NFC/NFD (the default APFS); the
fixture skips where the real filesystem does not. Nothing of ours is faked: the
secret paths come from a real HOME, the resume commands run in real shells.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import pytest

import action_risk
import claude_sessions
from browser_use import policy
from path_containment import is_within, resolve_within

SID = "12345678-1234-1234-1234-123456789abc"
NFC = unicodedata.normalize("NFC", "Çalışma")
NFD = unicodedata.normalize("NFD", "Çalışma")

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="APFS folds names only on macOS")
posix_only = pytest.mark.skipif(os.name == "nt", reason="the file names under test are illegal on Windows")


def write_verdict(path, ws):
    return action_risk.classify({"kind": "file_write", "paths": [str(path)], "workspace": str(ws)})


def read_verdict(path, ws):
    return action_risk.classify({"kind": "read", "paths": [str(path)], "workspace": str(ws)})


@pytest.fixture
def base(tmp_path):
    return tmp_path.resolve()


@pytest.fixture
def apfs(base):
    """(workspace stored NFD, its other spellings); skipped unless the volume folds them."""
    ws = base / NFD
    (ws / "Assets").mkdir(parents=True)
    # Not .upper(): that turns the dotless ı into I, which APFS keeps a different name.
    aliases = [base / NFC, base / NFD.replace("ma", "MA")]
    if not all(a.is_dir() and a.samefile(ws) for a in aliases):
        pytest.skip("this volume does not fold case and normalization")
    return ws, aliases


@darwin_only
def test_every_spelling_of_the_workspace_is_inside(apfs):
    ws, aliases = apfs
    for root, inner in [(ws, a) for a in aliases] + [(a, ws) for a in aliases]:
        for path in (inner / "Assets" / "a.cs", inner / "New" / "Deep" / "b.cs"):
            assert write_verdict(path, root).reason == "file_in_workspace", path
            assert read_verdict(path, root).reason == "read_in_workspace", path
            assert is_within(str(path), str(root)) is True
            assert resolve_within(str(path), str(root)) is not None


@darwin_only
@pytest.mark.parametrize("relative", [".git/config", ".GIT/config", ".mcp.json", "PROJECTSETTINGS/x.asset",
                                      "Projectſettings/x.asset", "packages/Manifest.json", ".mcp.jſon"])
def test_a_protected_path_stays_protected_through_every_spelling(apfs, relative):
    ws, aliases = apfs
    for root, inner in [(ws, aliases[0]), (aliases[1], ws)]:
        assert write_verdict(inner / relative, root).reason == "file_protected", inner / relative


@darwin_only
def test_the_workspace_root_under_another_spelling_is_protected(apfs):
    ws, aliases = apfs
    assert write_verdict(aliases[0], ws).reason == "file_protected"


@darwin_only
def test_a_sibling_with_a_similar_name_stays_outside_every_spelling(apfs, base):
    ws, aliases = apfs
    sibling = base / (NFC + "2")
    (sibling / "Assets").mkdir(parents=True)
    for root in (ws, *aliases):
        assert write_verdict(sibling / "Assets" / "a.cs", root).reason == "file_outside_workspace"
        assert write_verdict(sibling / "new.cs", root).reason == "file_outside_workspace"
        assert is_within(str(sibling / "new.cs"), str(root)) is False


@darwin_only
def test_a_link_that_leaves_the_root_stays_outside_every_spelling(apfs, base):
    ws, aliases = apfs
    outside = base / "outside"
    outside.mkdir()
    (ws / "escape").symlink_to(outside)
    (ws / "dangling").symlink_to(outside / "gone")
    for root in (ws, *aliases):
        for inner in (ws, *aliases):
            for path in (inner / "escape" / "x.cs", inner / "ESCAPE" / "x.cs", inner / "dangling" / "x.cs"):
                assert write_verdict(path, root).reason == "file_outside_workspace", path
                assert is_within(str(path), str(root)) is False, path


@darwin_only
def test_a_link_from_outside_into_the_workspace_is_inside(apfs, base):
    ws, aliases = apfs
    (base / "wslink").symlink_to(ws)
    assert write_verdict(base / "wslink" / "a.cs", aliases[0]).reason == "file_in_workspace"


@posix_only
def test_a_sibling_and_a_leaving_link_stay_outside_without_any_folding(base):
    ws = base / "Game"
    ws.mkdir()
    (base / "Gamebackup").mkdir()
    (base / "outside").mkdir()
    (ws / "escape").symlink_to(base / "outside")
    assert write_verdict(base / "Gamebackup" / "a.cs", ws).reason == "file_outside_workspace"
    assert write_verdict(ws / "escape" / "a.cs", ws).reason == "file_outside_workspace"
    assert is_within(str(base / "Gamebackup" / "a.cs"), str(ws)) is False
    assert is_within(str(ws / "escape" / "a.cs"), str(ws)) is False


def test_a_hard_link_of_a_file_root_is_not_inside_it(base):
    root = base / "root.txt"
    root.write_text("x", encoding="utf-8")
    twin = base / "twin.txt"
    os.link(root, twin)
    assert is_within(str(twin), str(root)) is False


def test_a_workspace_that_does_not_exist_is_judged_as_before(base):
    ws = base / "gone"
    assert write_verdict(ws / "a.cs", ws).reason == "file_in_workspace"
    assert write_verdict(base / "gone2" / "a.cs", ws).reason == "file_outside_workspace"


@pytest.fixture
def home(base, monkeypatch):
    profile = base / "Profile-café"
    profile.mkdir()
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("DB_PATH", raising=False)
    return profile


def test_a_secret_that_does_not_exist_yet_is_refused_at_its_plain_path(home):
    app = home / ".unity_architect_ai"
    app.mkdir()
    assert action_risk.gamachine_secret_target(str(app / "unity_master_v3.db-wal"))


@darwin_only
def test_a_future_secret_is_refused_through_any_spelling_of_its_folder(home):
    app = home / ".unity_architect_ai"
    app.mkdir()
    if not (home / ".UNITY_ARCHITECT_AI").is_dir():
        pytest.skip("this volume does not fold case")
    spellings = [home / ".UNITY_ARCHITECT_AI",
                 home.parent / unicodedata.normalize("NFD", home.name) / ".unity_architect_ai",
                 home.parent / home.name.upper() / ".Unity_Architect_AI"]
    for folder in spellings:
        assert action_risk.gamachine_secret_target(str(folder / "unity_master_v3.db-wal")), folder
        assert action_risk.gamachine_secret_target(str(folder / "UNITY_MAſTER_V3.DB-WAL")), folder
        assert action_risk.gamachine_secret_target(str(folder / "notes.txt")) is None, folder


@darwin_only
def test_a_secret_is_refused_when_its_folder_does_not_exist_either(home):
    if not home.parent.joinpath(home.name.upper()).is_dir():
        pytest.skip("this volume does not fold case")
    folder = home / ".UNITY_ARCHITECT_AI"
    assert action_risk.gamachine_secret_target(str(folder / "local-app-token"))
    assert action_risk.gamachine_secret_target(str(folder / "notes.txt")) is None
    assert action_risk.gamachine_secret_target(str(home / ".unity_architect_ai_2" / "local-app-token")) is None


def _resumes_in(shell, project, scratch):
    """The folder `claude --resume` starts in when the command runs in a real `shell`."""
    bindir = scratch / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\npwd -P\n", encoding="utf-8")
    stub.chmod(0o700)
    command = claude_sessions.command_for(str(project), SID)
    script = scratch / "resume.sh"
    script.write_text(command + "\n", encoding="utf-8")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    done = subprocess.run([shell, str(script)], cwd=scratch, env=env, capture_output=True, text=True, timeout=10)
    assert done.returncode == 0, done.stderr
    return Path(done.stdout.strip())


@posix_only
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_the_resume_command_lands_in_a_decomposed_folder(shell, base):
    if not shutil.which(shell):
        pytest.skip(f"{shell} is not installed")
    project = base / unicodedata.normalize("NFD", "Game café ı")
    project.mkdir()
    assert _resumes_in(shell, project, base).samefile(project)


@posix_only
@pytest.mark.parametrize("name", ["a\\b", "a\\\\b", "trail\\x"])
def test_a_backslash_in_a_posix_cwd_drops_the_cd(base, name):
    project = base / name
    project.mkdir()
    assert claude_sessions.command_for(str(project), SID) == f"claude --resume {SID}"


def test_a_combining_mark_without_a_letter_before_it_drops_the_cd():
    for cwd in ("/x/\u0301a", "/x/1\u0301", "/x/ \u0301a"):
        assert claude_sessions.command_for(cwd, SID) == f"claude --resume {SID}", cwd


@posix_only
@pytest.mark.parametrize("name", ["scene:preview.html", "aux.html", "aux/index.html", "preview.html ",
                                  "trail./index.html", "con.d/x.html"])
def test_legal_posix_names_open_in_the_agent_browser(base, name):
    file = base / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("<p>hi</p>", encoding="utf-8")
    assert policy.navigation_refusal(file.as_uri(), [str(base)]) == ""


@posix_only
def test_an_encoded_separator_stays_refused_on_posix(base):
    assert policy.navigation_refusal(f"{base.as_uri()}/a%5cb", [str(base)])
    assert policy.navigation_refusal(f"{base.as_uri()}/a%2fb", [str(base)])


@darwin_only
def test_the_agent_browser_opens_a_file_through_another_spelling_of_its_root(apfs):
    ws, aliases = apfs
    (ws / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    for alias in aliases:
        assert policy.navigation_refusal((alias / "index.html").as_uri(), [str(ws)]) == ""
        assert policy.navigation_refusal((ws / "index.html").as_uri(), [str(alias)]) == ""


@darwin_only
def test_gamachine_files_stay_closed_when_the_root_is_another_spelling_of_temp():
    temp = Path(os.path.realpath(tempfile.gettempdir()))
    alias = temp.parent / temp.name.swapcase()
    if not alias.is_dir() or not alias.samefile(temp):
        pytest.skip("this volume does not fold case")
    assert policy.navigation_refusal((temp / "gamachine.log").as_uri(), [str(temp)])
    assert policy.navigation_refusal((temp / "gamachine.log").as_uri(), [str(alias)])
    assert policy.navigation_refusal((alias / "gamachine.log").as_uri(), [str(temp)])
