"""Regression tests for macOS reclaimable memory gate and GAMACHINE_TOKEN_DIR dev override."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import local_token_file  # noqa: E402
import spawn_env  # noqa: E402
import unity_lanes as ul  # noqa: E402

GB = 1024 ** 3

# Measured on a 16 GB M4 Mac (27 Nov 2026): free+inactive = 8.24 GB while memorystatus_level = 81%.
_MEASURED_VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              182256.
Pages active:                            300000.
Pages inactive:                          320710.
Pages speculative:                        40000.
Pages throttled:                              0.
Pages wired down:                        150000.
Pages purgeable:                          60000.
"""
_MEASURED_MEMSIZE = 16 * GB


def test_darwin_free_memory_uses_measured_numbers_and_admits_one_lane(monkeypatch):
    seen_calls = []

    def fake_run(argv, *a, **kw):
        seen_calls.append((argv, kw))
        if argv[0] == "vm_stat":
            return subprocess.CompletedProcess(argv, 0, _MEASURED_VM_STAT, "")
        if argv[0] == "sysctl":
            key = argv[-1]
            if key == "kern.memorystatus_level":
                return subprocess.CompletedProcess(argv, 0, "81\n", "")
            if key == "hw.memsize":
                return subprocess.CompletedProcess(argv, 0, f"{_MEASURED_MEMSIZE}\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")

    monkeypatch.setattr(ul.sys, "platform", "darwin")
    monkeypatch.setattr(ul.subprocess, "run", fake_run)

    got = ul.free_memory_bytes()
    assert got == (_MEASURED_MEMSIZE * 81) // 100
    assert got >= 12 * GB
    assert ul.memory_refusal(got, running_lanes=0, starting_lanes=0) is None
    assert [c[0] for c in seen_calls] == [
        ["vm_stat"],
        ["sysctl", "-n", "kern.memorystatus_level"],
        ["sysctl", "-n", "hw.memsize"],
    ]
    for _, kw in seen_calls:
        assert kw["capture_output"] is True
        assert kw["text"] is True
        assert kw["timeout"] == 10
        assert isinstance(kw["env"], dict)


def test_darwin_free_memory_counts_speculative_and_purgeable_when_vm_stat_wins(monkeypatch):
    vm_out = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              100000.
Pages inactive:                          300000.
Pages speculative:                       200000.
Pages purgeable:                         250000.
"""
    expected_vm = (100000 + 300000 + 200000 + 250000) * 16384  # ~12.97 GB

    def fake_run(argv, *a, **kw):
        if argv[0] == "vm_stat":
            return subprocess.CompletedProcess(argv, 0, vm_out, "")
        if argv[0] == "sysctl" and argv[-1] == "kern.memorystatus_level":
            return subprocess.CompletedProcess(argv, 0, "50\n", "")
        if argv[0] == "sysctl" and argv[-1] == "hw.memsize":
            return subprocess.CompletedProcess(argv, 0, f"{_MEASURED_MEMSIZE}\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "err")

    monkeypatch.setattr(ul.sys, "platform", "darwin")
    monkeypatch.setattr(ul.subprocess, "run", fake_run)

    assert ul.free_memory_bytes() == expected_vm


def test_darwin_free_memory_vm_stat_only_when_sysctl_unavailable(monkeypatch):
    expected_vm = (182256 + 320710 + 40000 + 60000) * 16384

    def fake_run(argv, *a, **kw):
        if argv[0] == "vm_stat":
            return subprocess.CompletedProcess(argv, 0, _MEASURED_VM_STAT, "")
        return subprocess.CompletedProcess(argv, 1, "", "unknown oid")

    monkeypatch.setattr(ul.sys, "platform", "darwin")
    monkeypatch.setattr(ul.subprocess, "run", fake_run)

    assert ul.free_memory_bytes() == expected_vm


def test_darwin_free_memory_sysctl_only_when_vm_stat_fails(monkeypatch):
    def fake_run(argv, *a, **kw):
        if argv[0] == "vm_stat":
            raise OSError("vm_stat missing")
        if argv[0] == "sysctl" and argv[-1] == "kern.memorystatus_level":
            return subprocess.CompletedProcess(argv, 0, "81\n", "")
        if argv[0] == "sysctl" and argv[-1] == "hw.memsize":
            return subprocess.CompletedProcess(argv, 0, f"{_MEASURED_MEMSIZE}\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "err")

    monkeypatch.setattr(ul.sys, "platform", "darwin")
    monkeypatch.setattr(ul.subprocess, "run", fake_run)

    assert ul.free_memory_bytes() == (_MEASURED_MEMSIZE * 81) // 100


@pytest.mark.parametrize("pct_out", ["not-an-int\n", "-1\n", "101\n"])
def test_darwin_free_memory_returns_none_when_neither_parses(monkeypatch, pct_out):
    def fake_run(argv, *a, **kw):
        if argv[0] == "vm_stat":
            return subprocess.CompletedProcess(argv, 0, "garbled output\n", "")
        if argv[0] == "sysctl" and argv[-1] == "kern.memorystatus_level":
            return subprocess.CompletedProcess(argv, 0, pct_out, "")
        if argv[0] == "sysctl" and argv[-1] == "hw.memsize":
            return subprocess.CompletedProcess(argv, 0, f"{_MEASURED_MEMSIZE}\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "err")

    monkeypatch.setattr(ul.sys, "platform", "darwin")
    monkeypatch.setattr(ul.subprocess, "run", fake_run)

    assert ul.free_memory_bytes() is None


def test_gamachine_token_dir_moves_token_and_backend_url_at_call_time(tmp_path, monkeypatch):
    dir1 = tmp_path / "lane-one"
    dir2 = tmp_path / "lane-two"
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    monkeypatch.setenv("GAMACHINE_TOKEN_DIR", str(dir1))
    assert local_token_file.token_path() == os.path.join(str(dir1), "local-app-token")
    assert local_token_file.backend_url_path() == os.path.join(str(dir1), "backend-url")
    assert local_token_file.write_local_app_token("tok-1") is True
    assert local_token_file.write_backend_url("http://127.0.0.1:19001") is True
    assert (dir1 / "local-app-token").read_text(encoding="utf-8") == "tok-1"
    assert (dir1 / "backend-url").read_text(encoding="utf-8") == "http://127.0.0.1:19001"
    assert local_token_file.read_local_app_token() == "tok-1"
    assert local_token_file.read_backend_url() == "http://127.0.0.1:19001"

    # Changing GAMACHINE_TOKEN_DIR in-process without re-importing switches files immediately.
    monkeypatch.setenv("GAMACHINE_TOKEN_DIR", str(dir2))
    assert local_token_file.token_path() == os.path.join(str(dir2), "local-app-token")
    assert local_token_file.backend_url_path() == os.path.join(str(dir2), "backend-url")
    assert local_token_file.read_local_app_token() == ""
    assert local_token_file.read_backend_url() == ""
    assert local_token_file.write_local_app_token("tok-2") is True
    assert local_token_file.write_backend_url("http://127.0.0.1:19002") is True
    assert local_token_file.read_local_app_token() == "tok-2"
    assert local_token_file.read_backend_url() == "http://127.0.0.1:19002"
    # First folder stays untouched by second folder's writes.
    assert (dir1 / "local-app-token").read_text(encoding="utf-8") == "tok-1"

    assert local_token_file.write_local_app_token("") is True
    assert not (dir2 / "local-app-token").exists()
    assert local_token_file.write_backend_url("") is True
    assert not (dir2 / "backend-url").exists()


def test_gamachine_token_dir_retains_hardlink_and_redirection_safety_checks(tmp_path, monkeypatch):
    override = tmp_path / "override"
    override.mkdir()
    tok = override / "local-app-token"
    tok.write_text("planted", encoding="utf-8")
    os.link(tok, override / "second-name")
    url_file = override / "backend-url"
    url_file.write_text("http://127.0.0.1:19009", encoding="utf-8")
    os.link(url_file, override / "second-url-name")

    monkeypatch.setenv("GAMACHINE_TOKEN_DIR", str(override))
    monkeypatch.delenv("LOCAL_APP_TOKEN", raising=False)

    assert local_token_file.read_local_app_token() == ""
    assert local_token_file.write_local_app_token("new-secret") is False
    assert local_token_file.read_backend_url() == ""
    assert local_token_file.write_backend_url("http://127.0.0.1:19010") is False


def test_gamachine_token_dir_unset_blank_or_relative_keeps_expanduser_default(tmp_path):
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
    fake_home = str(tmp_path / "fake-home")
    os.makedirs(fake_home, exist_ok=True)
    expected_dir = os.path.join(fake_home, ".unity_architect_ai")
    expected_tok = os.path.join(expected_dir, "local-app-token")
    expected_url = os.path.join(expected_dir, "backend-url")

    code = (
        "import json, local_token_file as t, os;"
        "print(json.dumps({'exp': os.path.expanduser('~'), 'tok': t.token_path(), 'url': t.backend_url_path()}))"
    )
    import json

    for val in (None, "", "   ", "relative/token-dir"):
        env = dict(os.environ, HOME=fake_home, USERPROFILE=fake_home)
        if val is None:
            env.pop("GAMACHINE_TOKEN_DIR", None)
        else:
            env["GAMACHINE_TOKEN_DIR"] = val
        res = subprocess.run([sys.executable, "-c", code], cwd=app_dir, env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout.strip())
        assert os.path.normcase(data["exp"]) == os.path.normcase(fake_home)
        assert os.path.normcase(data["tok"]) == os.path.normcase(expected_tok)
        assert os.path.normcase(data["url"]) == os.path.normcase(expected_url)


def test_spawn_env_passes_gamachine_token_dir_to_children(tmp_path, monkeypatch):
    assert "GAMACHINE_TOKEN_DIR" in spawn_env._BASE_ENV_ALLOWLIST

    token_dir = str(tmp_path / "tok-dir")
    monkeypatch.setenv("GAMACHINE_TOKEN_DIR", token_dir)
    assert spawn_env.build_spawn_env()["GAMACHINE_TOKEN_DIR"] == token_dir
    assert spawn_env.build_spawn_env(family="claude")["GAMACHINE_TOKEN_DIR"] == token_dir

    monkeypatch.delenv("GAMACHINE_TOKEN_DIR", raising=False)
    assert "GAMACHINE_TOKEN_DIR" not in spawn_env.build_spawn_env()
