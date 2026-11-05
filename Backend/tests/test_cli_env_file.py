"""The CLI env file lives in the per-user data folder and the wrapper reads it as data."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from providers.agy_provider import AgyProvider

BACKEND = Path(__file__).resolve().parents[1]
HOSTILE = "/Users/x/Unity Projects/My (copy) $HOME `id` 'q' \"d\""


def _write(home: Path, launcher_dir: Path, workspace: str):
    p = AgyProvider.__new__(AgyProvider)
    p._write_cli_env(str(launcher_dir / "run_mcp_server.sh"), workspace, "http://127.0.0.1:8888")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    launcher = tmp_path / "App Bundle" / "Backend"
    home.mkdir()
    launcher.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home, launcher


def test_env_file_is_outside_the_launcher_folder(dirs):
    home, launcher = dirs
    _write(home, launcher, HOSTILE)
    assert not (launcher / ".unityai_cli.env").exists()
    assert list(launcher.iterdir()) == []
    assert (home / ".unity_architect_ai" / ".unityai_cli.env").read_text().splitlines() == [
        "UNITYAI_URL=http://127.0.0.1:8888", f"WORKSPACE={HOSTILE}"]


@pytest.mark.skipif(os.name == "nt", reason="bash wrapper")
def test_wrapper_reads_a_hostile_path_literally(dirs):
    home, launcher = dirs
    _write(home, launcher, HOSTILE)
    shutil.copy(BACKEND / "unityai", launcher / "unityai")
    fake = launcher / "backend"
    fake.write_text('#!/bin/bash\nprintf "%s|%s" "$WORKSPACE" "$UNITYAI_URL"\n')
    fake.chmod(0o755)
    r = subprocess.run(["bash", str(launcher / "unityai")], capture_output=True, text=True,
                       env={"HOME": str(home), "PATH": os.environ["PATH"]}, cwd=str(home))
    assert r.stderr == ""
    assert r.stdout == f"{HOSTILE}|http://127.0.0.1:8888"
