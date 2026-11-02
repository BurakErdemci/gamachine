"""Install job steps, cancel, failure, remove and the ConnectorView state machine, with a fake runner:
no network, no uv, no Blender."""
import json
import shutil
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from connectors import catalog, installer as inst, settings, views

ADDON = ('bl_info = {{\n    "name": "MCP for Blender",\n    "version": (1, 8),\n}}\n'
         'ADDON_PROTOCOL_VERSION = {protocol}\n# {tag}\n')
APP = {"version": "5.2.0", "path": "C:/fake/blender.exe"}


class FakeRunner:
    """Plays uv and Blender: creates the files the real commands would and records every argv."""

    def __init__(self, fail=None, block=None):
        self.calls = []
        self.fail = fail            # first command word to fail: "venv", "pip", "enable", "check"
        self.block = block          # command word to block on until killed
        self.blocked = threading.Event()

    @staticmethod
    def kind(argv):
        if argv[1] in ("venv", "pip"):
            return argv[1]
        expr = argv[-1]
        return "disable" if "disable(" in expr else "enable" if "enable(" in expr else "check"

    def __call__(self, argv, family, env, cwd, register):
        kind = self.kind(argv)
        self.calls.append((kind, argv, env, family))
        if kind == self.block:
            killed = threading.Event()
            register(MagicMock(poll=lambda: None, kill=killed.set))
            self.blocked.set()
            killed.wait(10)
            return -9, "killed"
        register(MagicMock(poll=lambda: 0))
        if kind == self.fail:
            return 1, f"{kind} broke\nhash mismatch"
        if kind == "venv":
            python = Path(argv[2]) / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_text("")
            Path(env["UV_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
        elif kind == "pip":
            venv = Path(argv[argv.index("--python") + 1]).parents[1]
            (venv / "Scripts" / "mcp-for-blender.exe").write_text("")
            bundled = venv / "Lib" / "site-packages" / "blender_mcp" / "bundled" / "addon.py"
            bundled.parent.mkdir(parents=True)
            bundled.write_text(ADDON.format(protocol=15, tag="upstream"), encoding="utf-8")
        elif kind == "enable":
            return 0, "BlenderMCP addon registered\nGAMACHINE_OK True\n"
        elif kind == "check":
            return 0, "GAMACHINE_OK True True\n"
        elif kind == "disable":
            return 0, "GAMACHINE_OK\n"
        return 0, ""


@pytest.fixture
def entry():
    return catalog.load()["blender"]


@pytest.fixture
def make(tmp_path):
    def build(**runner_kwargs):
        runner = FakeRunner(**runner_kwargs)
        installer = inst.Installer(root=tmp_path / "root", runner=runner, uv=lambda: "uv",
                                   blender_overrides={"BLENDER_USER_SCRIPTS": str(tmp_path / "scripts")},
                                   platform="win32")
        return installer, runner
    return build


def _finish(installer, entry):
    job = installer.job(entry["id"])
    job.thread.join(10)
    assert job.state != "running"
    return job


def _addons(tmp_path):
    return tmp_path / "scripts" / "addons"


def test_install_runs_all_steps_and_writes_install_json_last(make, entry, tmp_path):
    installer, runner = make()
    job = installer.start(entry, APP)
    assert isinstance(job.view()["started_at"], float)
    _finish(installer, entry)
    assert job.state == "done" and job.steps_done == ["python", "package", "addon", "check"]
    assert [c[0] for c in runner.calls] == ["venv", "pip", "enable", "check"]

    venv_argv, pip_argv = runner.calls[0][1], runner.calls[1][1]
    assert venv_argv[:5] == ["uv", "venv", str(installer.version_dir(entry) / "venv"), "--python", "3.12"]
    assert "--managed-python" in venv_argv
    assert {"--require-hashes", "--no-deps"} <= set(pip_argv)
    assert pip_argv[pip_argv.index("-r") + 1] == str(catalog.lock_path(entry))
    uv_env = runner.calls[0][2]
    assert runner.calls[0][3] == runner.calls[1][3] == "uvx" and runner.calls[2][3] is None
    vdir = installer.version_dir(entry)
    assert uv_env["UV_PYTHON_INSTALL_DIR"] == str(vdir / "python")
    assert uv_env["UV_CACHE_DIR"] == str(vdir / "cache")
    assert uv_env["DISABLE_TELEMETRY"] == "true"
    blender_env = runner.calls[2][2]
    assert blender_env["BLENDER_USER_SCRIPTS"] == str(tmp_path / "scripts")
    assert runner.calls[2][1][1] == "--background"

    addon = _addons(tmp_path) / "blender_mcp.py"
    record = json.loads((vdir / "install.json").read_text(encoding="utf-8"))
    assert record["version"] == "2.2.6" and record["addon_owner"] == "gamachine"
    assert record["addon"]["path"] == str(addon)
    assert record["addon_files"] == [{"path": str(addon), "sha256": inst.sha256_file(addon), "backup": None}]
    assert record["lock_sha256"] == inst.sha256_file(catalog.lock_path(entry))
    assert not (vdir / "cache").exists()


@pytest.mark.parametrize("fail,code,steps", [
    ("venv", "python_failed", []),
    ("pip", "package_failed", ["python"]),
    ("enable", "addon_failed", ["python", "package"]),
    ("check", "check_failed", ["python", "package", "addon"]),
])
def test_failed_step_reports_and_cleans_up(make, entry, tmp_path, fail, code, steps):
    installer, _ = make(fail=fail)
    installer.start(entry, APP)
    job = _finish(installer, entry)
    assert job.state == "failed" and job.steps_done == steps
    assert job.error["code"] == code and "hash mismatch" in job.error["message"]
    assert not installer.version_dir(entry).exists()
    assert not (_addons(tmp_path) / "blender_mcp.py").exists()
    assert installer.installed(entry) is None


def test_cancel_kills_the_child_and_deletes_the_partial_folder(make, entry):
    installer, runner = make(block="pip")
    installer.start(entry, APP)
    assert runner.blocked.wait(5)
    assert installer.version_dir(entry).exists()
    assert installer.cancel(entry["id"]) is True
    job = installer.job(entry["id"])
    assert job.state == "cancelled" and job.steps_done == ["python"]
    assert not installer.version_dir(entry).exists()
    assert installer.cancel(entry["id"]) is False


def test_one_job_at_a_time(make, entry):
    installer, runner = make(block="venv")
    installer.start(entry, APP)
    assert runner.blocked.wait(5)
    with pytest.raises(inst.Busy):
        installer.start(entry, APP)
    with pytest.raises(inst.Busy):
        installer.remove(entry)
    installer.cancel(entry["id"])


def test_remove_deletes_only_files_whose_hash_still_matches(make, entry, tmp_path):
    installer, runner = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    addon = _addons(tmp_path) / "blender_mcp.py"
    addon.write_text("user edited", encoding="utf-8")
    # A file the user changed is theirs now: kept, and not a failure of Remove.
    assert installer.remove(entry) == []
    assert addon.read_text(encoding="utf-8") == "user edited"
    assert not (installer.root / "blender").exists()


def test_remove_disables_and_deletes_gamachine_addon(make, entry, tmp_path, monkeypatch):
    installer, runner = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    monkeypatch.setattr(inst.os.path, "isfile", lambda p: True if p == APP["path"] else Path(p).is_file())
    assert installer.remove(entry) == []
    assert runner.calls[-1][0] == "disable" and "'blender_mcp'" in runner.calls[-1][1][-1]
    assert not (_addons(tmp_path) / "blender_mcp.py").exists()
    assert not (installer.root / "blender").exists()


def test_compatible_user_addon_is_kept_and_never_removed(make, entry, tmp_path, monkeypatch):
    installer, runner = make()
    user = _addons(tmp_path) / "addon.py"
    user.parent.mkdir(parents=True)
    user.write_text(ADDON.format(protocol=16, tag="user"), encoding="utf-8")
    original = user.read_bytes()
    installer.start(entry, APP)
    _finish(installer, entry)
    record = installer.installed(entry)
    assert record["addon_owner"] == "user" and record["addon_files"] == []
    assert record["addon"]["module"] == "addon" and record["addon"]["sha256"] == inst.sha256_file(user)
    assert "'addon'" in runner.calls[2][1][-1]
    assert not (_addons(tmp_path) / "blender_mcp.py").exists()
    monkeypatch.setattr(inst.os.path, "isfile", lambda p: True if p == APP["path"] else Path(p).is_file())
    installer.remove(entry)
    assert user.read_bytes() == original
    assert all(c[0] != "disable" for c in runner.calls)


def test_incompatible_user_addon_needs_explicit_replace_and_is_restored_on_remove(make, entry, tmp_path):
    installer, _ = make()
    old = _addons(tmp_path) / "blender_mcp.py"
    old.parent.mkdir(parents=True)
    old.write_text(ADDON.format(protocol=9, tag="old"), encoding="utf-8")
    original = old.read_bytes()
    assert installer.addon_conflict(entry, APP) == {"path": str(old), "found_protocol": 9, "needed_protocol": 15}

    installer.start(entry, APP)
    job = _finish(installer, entry)
    assert job.state == "failed" and job.error["code"] == "addon_conflict" and not job.error["retryable"]
    assert old.read_bytes() == original

    installer.start(entry, APP, replace_addon=True)
    job = _finish(installer, entry)
    assert job.state == "done"
    record = installer.installed(entry)
    backup = Path(record["addon"]["backup"])
    assert backup.read_bytes() == original and "upstream" in old.read_text(encoding="utf-8")
    assert installer.addon_conflict(entry, APP) is None

    installer.remove(entry)
    assert old.read_bytes() == original and not backup.exists()


def test_reinstall_overwrites_gamachines_own_file_in_place(make, entry, tmp_path):
    installer, _ = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    installer.start(entry, APP)
    job = _finish(installer, entry)
    assert job.state == "done"
    assert [p.name for p in _addons(tmp_path).iterdir()] == ["blender_mcp.py"]



def test_failed_repair_keeps_the_record_so_remove_still_cleans_up(make, entry, tmp_path):
    installer, runner = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    addon = _addons(tmp_path) / "blender_mcp.py"
    runner.fail = "check"
    installer.start(entry, APP)
    assert _finish(installer, entry).state == "failed"
    assert addon.is_file() and installer.installed(entry)["addon_files"][0]["path"] == str(addon)
    assert installer.remove(entry) == []
    assert not addon.exists()

# ---------- ConnectorView state machine ----------

def _db(**rows):
    db = MagicMock()
    db.get_setting.side_effect = rows.get
    return db


def _detection(chosen=APP, found=None, custom=None):
    return {"found": found if found is not None else ([chosen] if chosen else []), "chosen": chosen,
            "custom_path": custom}


def _write_record(installer, entry, version):
    vdir = installer.version_dir(entry, version)
    exe = vdir / "venv" / "Scripts" / "mcp-for-blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    (vdir / "install.json").write_text(json.dumps({
        "version": version, "entry": str(exe), "addon_owner": "gamachine",
        "addon": {"path": "x/blender_mcp.py"}, "addon_files": [], "finished_at": "t"}), encoding="utf-8")
    return exe


@pytest.mark.parametrize("detection,state", [
    (_detection(chosen=None, found=[]), "app_missing"),
    (_detection(chosen=None, found=[{"version": "3.6.0", "path": "p"}]), "app_too_old"),
    (_detection(chosen={"version": "4.1.0", "path": "c"}, custom="c"), "app_too_old"),
    (_detection(chosen=None, found=[{"version": "5.2.0", "path": "p"}], custom="bad"), "app_missing"),
    (_detection(), "not_installed"),
])
def test_view_states_without_install(make, entry, detection, state):
    installer, _ = make()
    view = views.connector_view(entry, _db(), installer, detection, connected=lambda cid: True)
    assert view["state"] == state and view["installed"] is None
    assert view["enabled"] is False and view["connected"] is False


def test_view_ready_update_available_and_broken(make, entry):
    installer, _ = make()
    db = _db(**{settings.enabled_key("blender"): "1"})
    exe = _write_record(installer, entry, "2.2.6")
    view = views.connector_view(entry, db, installer, _detection(), connected=lambda cid: True)
    assert view["state"] == "ready" and view["enabled"] is True and view["connected"] is True
    assert view["installed"] == {"version": "2.2.6", "addon_path": "x/blender_mcp.py", "addon_owner": "gamachine"}
    assert set(view) == {"id", "name", "summary", "capabilities", "risk", "state", "enabled", "connected",
                         "app", "installed", "error"}
    assert set(view["app"]) == {"found", "chosen", "custom_path", "min_version", "download_url"}

    exe.unlink()
    view = views.connector_view(entry, db, installer, _detection(), connected=lambda cid: False)
    assert view["state"] == "error" and view["error"]["code"] == "broken_install"

    shutil.rmtree(installer.root / "blender")
    _write_record(installer, entry, "2.2.5")
    assert views.connector_view(entry, db, installer, _detection())["state"] == "update_available"


def test_view_installing_and_error_follow_the_job(make, entry):
    installer, runner = make(block="venv")
    installer.start(entry, APP)
    assert runner.blocked.wait(5)
    assert views.connector_view(entry, _db(), installer, _detection())["state"] == "installing"
    installer.cancel(entry["id"])
    assert views.connector_view(entry, _db(), installer, _detection())["state"] == "not_installed"

    failing, _ = make(fail="pip")
    failing.start(entry, APP)
    _finish(failing, entry)
    view = views.connector_view(entry, _db(), failing, _detection())
    assert view["state"] == "error" and view["error"]["code"] == "package_failed"


# ---------- fix round 2026-11-02: a working install survives, Remove leaves nothing ----------

FOREIGN = b'bl_info = {"name": "My own tools"}\nvaluable = True\n'


def test_another_addons_file_under_our_name_is_never_overwritten_without_consent(make, entry, tmp_path):
    installer, _ = make()
    foreign = _addons(tmp_path) / "blender_mcp.py"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(FOREIGN)
    assert installer.addon_conflict(entry, APP) == {"path": str(foreign), "found_protocol": None,
                                                    "needed_protocol": 15}
    installer.start(entry, APP)
    job = _finish(installer, entry)
    assert job.state == "failed" and job.error["code"] == "addon_conflict"
    assert foreign.read_bytes() == FOREIGN and installer.installed(entry) is None


def test_replacing_another_addons_file_backs_it_up_beside_an_existing_backup_and_remove_restores_it(
        make, entry, tmp_path):
    installer, _ = make()
    foreign = _addons(tmp_path) / "blender_mcp.py"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(FOREIGN)
    taken = foreign.with_name("blender_mcp.py.bak")
    taken.write_bytes(b"the user's own backup")
    installer.start(entry, APP, replace_addon=True)
    assert _finish(installer, entry).state == "done"
    backup = foreign.with_name("blender_mcp.py.1.bak")
    assert backup.read_bytes() == FOREIGN and taken.read_bytes() == b"the user's own backup"
    assert installer.installed(entry)["addon_files"][0]["backup"] == str(backup)
    assert installer.remove(entry) == []
    assert foreign.read_bytes() == FOREIGN and not backup.exists()
    assert taken.read_bytes() == b"the user's own backup"


@pytest.mark.parametrize("fail", ["pip", "check"])
def test_a_failed_repair_leaves_the_working_install_untouched(make, entry, tmp_path, fail):
    installer, runner = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    before = installer.installed(entry)
    addon = _addons(tmp_path) / "blender_mcp.py"
    addon_bytes = addon.read_bytes()
    runner.fail = fail
    installer.start(entry, APP)
    assert _finish(installer, entry).state == "failed"
    after = installer.installed(entry)
    assert {k: v for k, v in after.items()} == before
    assert Path(after["entry"]).is_file() and Path(after["python"]).is_file()
    assert addon.read_bytes() == addon_bytes
    assert [p.name for p in (installer.root / "blender").iterdir()] == ["2.2.6"]
    db = _db(**{settings.enabled_key("blender"): "1"})
    view = views.connector_view(entry, db, installer, _detection())
    assert view["state"] == "ready" and view["error"] is None
    assert installer.job("blender").view()["error"]["code"] in ("package_failed", "check_failed")


def test_a_cancelled_repair_leaves_the_working_install_untouched(make, entry):
    installer, runner = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    before = installer.installed(entry)
    runner.block = "pip"
    runner.blocked.clear()
    installer.start(entry, APP)
    assert runner.blocked.wait(5)
    assert installer.cancel("blender")
    assert installer.installed(entry) == before and Path(before["entry"]).is_file()


def test_a_successful_repair_swaps_in_the_new_build_and_drops_the_old_one(make, entry):
    installer, _ = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    old = installer.installed(entry)
    installer.start(entry, APP)
    assert _finish(installer, entry).state == "done"
    new = installer.installed(entry)
    assert new["dir"] != old["dir"] and Path(new["entry"]).is_file()
    assert not Path(old["dir"]).exists()
    assert [r["dir"] for r in installer._records(entry)] == [new["dir"]]


def test_a_failed_update_keeps_the_older_version_usable(make, entry):
    installer, runner = make()
    exe = _write_record(installer, entry, "2.2.5")
    db = _db(**{settings.enabled_key("blender"): "1"})
    assert views.connector_view(entry, db, installer, _detection())["state"] == "update_available"
    runner.fail = "pip"
    installer.start(entry, APP)
    assert _finish(installer, entry).state == "failed"
    assert exe.is_file()
    view = views.connector_view(entry, db, installer, _detection())
    assert view["state"] == "update_available" and view["enabled"] is True


def test_add_ons_written_for_two_blender_builds_are_both_recorded_and_removed(make, entry, tmp_path, monkeypatch):
    installer, _ = make()
    old_app, new_app = {"version": "4.2.0", "path": "C:/old/blender.exe"}, APP
    dirs = {"4.2.0": tmp_path / "b42" / "addons", "5.2.0": tmp_path / "b52" / "addons"}
    monkeypatch.setattr(installer, "addons_dir", lambda app: dirs[app["version"]])
    for app in (old_app, new_app):
        installer.start(entry, app)
        assert _finish(installer, entry).state == "done"
    record = installer.installed(entry)
    assert sorted(a["path"] for a in record["addons"]) == sorted(str(d / "blender_mcp.py") for d in dirs.values())
    assert {a["app"]["version"] for a in record["addons"]} == {"4.2.0", "5.2.0"}
    assert all(a["owner"] == "gamachine" and a["sha256"] for a in record["addons"])
    assert len(record["addon_files"]) == 2
    monkeypatch.setattr(inst.os.path, "isfile", lambda p: True if p in (old_app["path"], APP["path"])
                        else Path(p).is_file())
    runner = installer.runner
    assert installer.remove(entry) == []
    assert not any((d / "blender_mcp.py").exists() for d in dirs.values())
    disabled = [c for c in runner.calls if c[0] == "disable"]
    assert sorted(c[1][0] for c in disabled) == sorted([old_app["path"], APP["path"]])


def test_an_addon_left_by_an_interrupted_install_is_recognised_as_ours(make, entry, tmp_path):
    installer, runner = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    addon = _addons(tmp_path) / "blender_mcp.py"
    shutil.rmtree(installer.root)  # the app died after writing the add-on: no install record
    assert addon.is_file() and installer.installed(entry) is None
    fresh, _ = make()
    fresh.start(entry, APP)
    assert _finish(fresh, entry).state == "done"
    record = fresh.installed(entry)
    assert record["addon_owner"] == "gamachine" and record["addon_files"][0]["path"] == str(addon)
    assert fresh.remove(entry) == []
    assert not addon.exists()


def test_remove_that_cannot_delete_an_addon_file_keeps_the_records_and_is_retried(make, entry, tmp_path,
                                                                                  monkeypatch):
    installer, _ = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    addon = _addons(tmp_path) / "blender_mcp.py"
    real = inst._remove_written
    monkeypatch.setattr(inst, "_remove_written", lambda path, sha, restore: f"{path}: in use")
    problems = installer.remove(entry)
    assert problems == [f"{addon}: in use"]
    assert installer.installed(entry) is not None and addon.is_file()
    view = views.connector_view(entry, _db(), installer, _detection())
    assert view["state"] == "error" and view["error"]["code"] == "remove_incomplete"
    assert str(addon) in view["error"]["message"]
    monkeypatch.setattr(inst, "_remove_written", real)
    assert installer.remove(entry) == []
    assert not addon.exists() and not (installer.root / "blender").exists()
    assert views.connector_view(entry, _db(), installer, _detection())["state"] == "not_installed"


def test_remove_that_cannot_delete_the_folder_says_so_reads_not_installed_and_is_retried(make, entry,
                                                                                       monkeypatch):
    installer, _ = make()
    installer.start(entry, APP)
    _finish(installer, entry)
    real = inst.shutil.rmtree

    def locked(path, *args, **kwargs):
        raise PermissionError(32, "in use", str(Path(path) / "venv" / "Scripts" / "mcp-for-blender.exe"))
    monkeypatch.setattr(inst.shutil, "rmtree", locked)
    problems = installer.remove(entry)
    assert len(problems) == 1 and "mcp-for-blender.exe" in problems[0]
    assert installer.installed(entry) is None
    assert views.connector_view(entry, _db(), installer, _detection())["state"] == "not_installed"
    monkeypatch.setattr(inst.shutil, "rmtree", real)
    assert installer.remove(entry) == []
    assert not (installer.root / "blender").exists()
