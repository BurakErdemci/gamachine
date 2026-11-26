import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import unity_lanes as ul  # noqa: E402

GB = 1024 ** 3


def _run(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "Assets").mkdir(parents=True)
    (proj / "ProjectSettings").mkdir()
    (proj / "Packages").mkdir()
    (proj / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.1.2f1\n")
    (proj / "Packages" / "manifest.json").write_text("{}")
    (proj / "Assets" / "a.txt").write_text("x")
    _run(proj, "init", "-q")
    _run(proj, "add", "-A")
    _run(proj, "commit", "-q", "-m", "init")
    calls = []
    monkeypatch.setattr(ul, "_install_package", lambda d: calls.append(d) or True)
    proj_s = str(proj)
    proj_s_calls = calls
    monkeypatch.setattr(ul, "_calls", proj_s_calls, raising=False)
    return proj_s


def _branches(project):
    out = subprocess.run(["git", "-C", project, "branch", "--list", "--format=%(refname:short)", "gamachine/*"],
                         capture_output=True, text=True).stdout
    return out.split()


def test_validate_name():
    assert ul.validate_name("lane-1") == "lane-1"
    for bad in ("", "-a", "A", "a_b", "a" * 33, "a/b", ".."):
        with pytest.raises(ValueError):
            ul.validate_name(bad)


def test_lane_dir():
    assert ul.lane_dir("/p", "x") == os.path.join("/p", ".gamachine", "lanes", "x")


def test_create_lane_and_exclude_once(project):
    a = ul.create_lane(project, "one")
    b = ul.create_lane(project, "two")
    assert os.path.isfile(os.path.join(a.dir, "Assets", "a.txt"))
    assert a.branch == "gamachine/one" and a.state == "created" and a.pid is None
    assert sorted(_branches(project)) == ["gamachine/one", "gamachine/two"]
    assert ul._calls == [a.dir, b.dir]
    exclude = open(os.path.join(project, ".git", "info", "exclude")).read().splitlines()
    assert exclude.count(".gamachine/") == 1


def test_create_refuses_existing_and_non_unity(project, tmp_path):
    ul.create_lane(project, "one")
    with pytest.raises(ul.LaneError):
        ul.create_lane(project, "one")
    with pytest.raises(ul.LaneError):
        ul.create_lane(str(tmp_path), "x")


def test_create_install_failure_rolls_back(project, monkeypatch):
    monkeypatch.setattr(ul, "_install_package", lambda d: False)
    with pytest.raises(ul.LaneError):
        ul.create_lane(project, "bad")
    assert not os.path.exists(ul.lane_dir(project, "bad"))
    assert _branches(project) == []


def test_create_without_commit(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    (proj / "Assets").mkdir(parents=True)
    (proj / "ProjectSettings").mkdir()
    _run(proj, "init", "-q")
    monkeypatch.setattr(ul, "_install_package", lambda d: True)
    with pytest.raises(ul.LaneError):
        ul.create_lane(str(proj), "x")


def test_remove_keeps_branch_unless_asked(project):
    a = ul.create_lane(project, "one")
    ul.remove_lane(project, a)
    assert not os.path.exists(a.dir)
    assert _branches(project) == ["gamachine/one"]
    b = ul.create_lane(project, "two")
    ul.remove_lane(project, b, delete_branch=True)
    assert _branches(project) == ["gamachine/one"]


def test_remove_refuses_escape(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("k")
    rec = ul.LaneRecord(name="evil", dir=ul.lane_dir(project, "evil"), branch="gamachine/evil")
    os.makedirs(ul.lanes_root(project), exist_ok=True)
    if sys.platform == "win32":
        res = subprocess.run(["cmd", "/c", "mklink", "/J", rec.dir, str(outside)], capture_output=True)
        if res.returncode != 0:
            pytest.skip("junction not available")
    else:
        try:
            os.symlink(outside, rec.dir)
        except OSError:
            pytest.skip("symlink not available")
    with pytest.raises(ul.LaneError):
        ul.remove_lane(project, rec)
    assert (outside / "keep.txt").exists()
    # a record pointing at a sibling of the lanes folder is refused too
    rec2 = ul.LaneRecord(name="x", dir=str(outside), branch="gamachine/x")
    with pytest.raises(ul.LaneError):
        ul.remove_lane(project, rec2)
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "rmdir", rec.dir], capture_output=True)
    else:
        os.unlink(rec.dir)


def test_memory_refusal():
    assert "ölçülemedi" in ul.memory_refusal(None, 0)
    assert ul.memory_refusal(8 * GB - 1, 0)
    assert "şerit başına 6 GB" in ul.memory_refusal(8 * GB - 1, 0)
    assert ul.memory_refusal(8 * GB, 0) is None
    assert ul.memory_refusal(9 * GB, 2, starting_lanes=0) is None
    assert ul.memory_refusal(14 * GB - 1, 0, starting_lanes=1)
    assert ul.memory_refusal(14 * GB, 0, starting_lanes=1) is None


def test_free_memory_is_int_or_none():
    v = ul.free_memory_bytes()
    assert v is None or v > 0


def test_resolve_unity_exe(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    (proj / "ProjectSettings").mkdir(parents=True)
    (proj / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.1.2f1\nm_EditorVersionWithRevision: x\n")
    hub = tmp_path / "hub"
    exe = ul._exe_under(str(hub), "6000.1.2f1")
    monkeypatch.setattr(ul, "_hub_roots", lambda: [str(tmp_path / "none"), str(hub)])
    with pytest.raises(ul.LaneError) as e:
        ul.resolve_unity_exe(str(proj))
    assert "6000.1.2f1" in str(e.value)
    os.makedirs(os.path.dirname(exe))
    open(exe, "w").close()
    assert ul.resolve_unity_exe(str(proj)) == exe


@pytest.mark.parametrize("version", [
    "..\\..\\evil", "../../evil", "C:\\Windows\\System32", "/usr/bin", "\\\\host\\share\\x", "6000.1.2f1/../../x",
])
def test_resolve_unity_exe_refuses_a_version_that_is_a_path(tmp_path, monkeypatch, version):
    proj = tmp_path / "p"
    (proj / "ProjectSettings").mkdir(parents=True)
    (proj / "ProjectSettings" / "ProjectVersion.txt").write_text(f"m_EditorVersion: {version}\n")
    hub = tmp_path / "hub" / "Editor"
    hub.mkdir(parents=True)
    monkeypatch.setattr(ul, "_hub_roots", lambda: [str(hub)])
    monkeypatch.setattr(ul.os.path, "isfile", lambda _p: True)  # any joined path would "exist"
    with pytest.raises(ul.LaneError, match="geçersiz"):
        ul.resolve_unity_exe(str(proj))


def test_expected_instance_hash_vector():
    # sha1("C:/proj/.gamachine/lanes/a/Assets")[:16]; Unity's dataPath uses forward slashes
    if sys.platform == "win32":
        assert ul.expected_instance_hash("C:\\proj\\.gamachine\\lanes\\a") == "1a894e514b7909ab"
        assert ul.expected_instance_hash("C:/proj/.gamachine/lanes/a/") == "1a894e514b7909ab"
    else:
        import hashlib
        want = hashlib.sha1(b"/proj/.gamachine/lanes/a/Assets").hexdigest()[:16]
        assert ul.expected_instance_hash("/proj/.gamachine/lanes/a") == want


class FakeProc:
    def __init__(self, rc=None):
        self.pid = 4242
        self.rc = rc
        self.terminated = False

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True
        self.rc = 0

    def kill(self):
        self.rc = -9

    def wait(self, timeout=None):
        return self.rc


@pytest.fixture
def lane(project, monkeypatch):
    monkeypatch.setattr(ul, "resolve_unity_exe", lambda d: "UNITY")
    return ul.create_lane(project, "one")


def test_start_editor_refuses_on_memory(lane):
    with pytest.raises(ul.LaneError):
        ul.start_editor(lane, free_bytes=GB, popen=lambda *a, **k: FakeProc())
    assert lane.state == "created"


def test_start_editor_spawn(lane):
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return FakeProc()

    ul.start_editor(lane, free_bytes=10 * GB, popen=fake_popen)
    assert seen["argv"][:4] == ["UNITY", "-batchmode", "-projectPath", lane.dir]
    assert seen["argv"][4] == "-logFile" and seen["argv"][5].endswith("gamachine-lane.log")
    assert seen["kw"]["env"]["UNITY_MCP_ALLOW_BATCH"] == "1"
    assert seen["kw"]["env"]["GAMACHINE_LANE_ID"] == "one"
    if sys.platform == "win32":
        assert seen["kw"]["creationflags"] & 0x08000000
    assert lane.pid == 4242 and lane.state == "starting"
    if lane.job is not None:
        lane.job.close()
        lane.job = None


def _started(lane, proc):
    lane.proc, lane.pid, lane.state = proc, proc.pid, "starting"


def test_wait_ready(lane):
    _started(lane, FakeProc())
    want = ul.expected_instance_hash(lane.dir)
    answers = iter([[], [{"hash": "zzz"}], [{"hash": want, "project": "p"}]])
    ul.wait_ready(lane, lambda: next(answers), sleep=lambda s: None)
    assert lane.state == "ready"


def test_wait_ready_timeout_includes_log_tail(lane):
    _started(lane, FakeProc())
    os.makedirs(os.path.dirname(ul.lane_log_path(lane.dir)), exist_ok=True)
    with open(ul.lane_log_path(lane.dir), "w") as fh:
        fh.write("".join(f"line{i}\n" for i in range(50)))
    t = [0.0]

    def clock():
        t[0] += 100
        return t[0]

    with pytest.raises(ul.LaneError) as e:
        ul.wait_ready(lane, lambda: [], timeout_s=300, sleep=lambda s: None, clock=clock)
    assert lane.state == "failed"
    assert "line49" in str(e.value) and "line30" in str(e.value) and "line29" not in str(e.value)


def test_wait_ready_process_exited(lane):
    _started(lane, FakeProc(rc=1))
    with pytest.raises(ul.LaneError):
        ul.wait_ready(lane, lambda: [], sleep=lambda s: None)
    assert lane.state == "failed"


def test_stop_editor_removes_lockfile(lane):
    proc = FakeProc()
    _started(lane, proc)
    lock = os.path.join(lane.dir, "Temp", "UnityLockfile")
    os.makedirs(os.path.dirname(lock))
    open(lock, "w").close()
    ul.stop_editor(lane)
    assert proc.terminated and lane.state == "stopped" and lane.pid is None
    assert not os.path.exists(lock)


def test_remove_running_lane_stops_it(lane):
    proc = FakeProc()
    _started(lane, proc)
    project = os.path.dirname(os.path.dirname(os.path.dirname(lane.dir)))
    ul.remove_lane(project, lane)
    assert proc.terminated and not os.path.exists(lane.dir)


def test_validate_name_refuses_newline_and_device_names():
    for bad in ("abc\n", "con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9", "COM1"):
        with pytest.raises(ValueError):
            ul.validate_name(bad)


def test_lanes_root_junction_outside_project_refused(project, tmp_path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("junction test is Windows-only")
    outside = tmp_path / "outside_gamachine"
    outside.mkdir()
    link = os.path.join(project, ".gamachine")
    res = subprocess.run(["cmd", "/c", "mklink", "/J", link, str(outside)], capture_output=True)
    if res.returncode != 0:
        pytest.skip("junction not available")
    git_calls = []
    monkeypatch.setattr(
        ul,
        "_git",
        lambda *a, **k: git_calls.append(a) or subprocess.CompletedProcess(a, 1, "", "mock"),
    )
    try:
        with pytest.raises(ul.LaneError):
            ul.create_lane(project, "one")
        assert git_calls == []
        rec = ul.LaneRecord(name="one", dir=ul.lane_dir(project, "one"), branch="gamachine/one")
        with pytest.raises(ul.LaneError):
            ul.remove_lane(project, rec)
        assert git_calls == []
    finally:
        subprocess.run(["cmd", "/c", "rmdir", link], capture_output=True)


def test_start_editor_refuses_double_start_and_allows_after_exit(lane):
    proc1 = FakeProc(rc=None)
    ul.start_editor(lane, free_bytes=10 * GB, popen=lambda *a, **k: proc1)
    try:
        with pytest.raises(ul.LaneError, match="zaten çalışıyor"):
            ul.start_editor(lane, free_bytes=10 * GB, popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
        assert lane.proc is proc1
        proc1.rc = 0
        proc2 = FakeProc(rc=None)
        ul.start_editor(lane, free_bytes=10 * GB, popen=lambda *a, **k: proc2)
        assert lane.proc is proc2
    finally:
        if lane.job is not None:
            lane.job.close()
            lane.job = None


def test_start_and_stop_editor_job_lifecycle(lane, monkeypatch):
    class FakeJob:
        def __init__(self):
            self.terminated = False
            self.closed = False

        def terminate(self):
            self.terminated = True

        def close(self):
            self.closed = True

    job = FakeJob()
    monkeypatch.setattr(ul.process_tree, "new_job_object_for", lambda _p: job)
    ul.start_editor(lane, free_bytes=10 * GB, popen=lambda *a, **k: FakeProc())
    assert lane.job is job
    ul.stop_editor(lane)
    assert job.terminated and job.closed and lane.job is None


def test_wait_ready_ignores_other_lane_hash_and_accepts_own(lane):
    _started(lane, FakeProc())
    other_hash = ul.expected_instance_hash(lane.dir + "-other")
    t = [0.0]

    def clock():
        t[0] += 100
        return t[0]

    with pytest.raises(ul.LaneError):
        ul.wait_ready(lane, lambda: [{"hash": other_hash}], timeout_s=300, sleep=lambda s: None, clock=clock)
    assert lane.state == "failed"

    _started(lane, FakeProc())
    own_hash = ul.expected_instance_hash(lane.dir)
    ul.wait_ready(lane, lambda: [{"hash": other_hash}, {"hash": own_hash}], sleep=lambda s: None)
    assert lane.state == "ready"


def test_resolve_unity_exe_refuses_exe_outside_hub_root(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    (proj / "ProjectSettings").mkdir(parents=True)
    (proj / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.1.2f1\n")
    hub = tmp_path / "hub"
    exe = ul._exe_under(str(hub), "6000.1.2f1")
    os.makedirs(os.path.dirname(exe))
    open(exe, "w").close()
    outside_exe = str(tmp_path / "outside" / "Unity.exe")
    orig_realpath = os.path.realpath
    monkeypatch.setattr(
        ul.os.path,
        "realpath",
        lambda p: outside_exe if os.path.normcase(p) == os.path.normcase(exe) else orig_realpath(p),
    )
    monkeypatch.setattr(ul, "_hub_roots", lambda: [str(hub)])
    with pytest.raises(ul.LaneError):
        ul.resolve_unity_exe(str(proj))


def test_remove_lane_refuses_mismatched_basename(project):
    two = ul.create_lane(project, "two")
    rec = ul.LaneRecord(name="one", dir=two.dir, branch="gamachine/one")
    with pytest.raises(ul.LaneError):
        ul.remove_lane(project, rec)
    assert os.path.isdir(two.dir)


def test_create_lane_reuses_existing_branch_and_rollback_preserves_it(project, monkeypatch):
    a = ul.create_lane(project, "one")
    ul.remove_lane(project, a)
    assert _branches(project) == ["gamachine/one"]

    b = ul.create_lane(project, "one")
    assert b.branch == "gamachine/one" and os.path.isdir(b.dir)

    ul.remove_lane(project, b)
    monkeypatch.setattr(ul, "_install_package", lambda _d: False)
    with pytest.raises(ul.LaneError):
        ul.create_lane(project, "one")
    assert not os.path.exists(ul.lane_dir(project, "one"))
    assert _branches(project) == ["gamachine/one"]


def test_resolve_unity_exe_accepts_china_version(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    (proj / "ProjectSettings").mkdir(parents=True)
    (proj / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.20f1c1\n")
    hub = tmp_path / "hub"
    exe = ul._exe_under(str(hub), "2022.3.20f1c1")
    os.makedirs(os.path.dirname(exe))
    open(exe, "w").close()
    monkeypatch.setattr(ul, "_hub_roots", lambda: [str(hub)])
    assert ul.resolve_unity_exe(str(proj)) == exe


def test_start_editor_passes_starting_lanes_to_memory_gate(lane):
    with pytest.raises(ul.LaneError):
        ul.start_editor(
            lane,
            free_bytes=13 * GB,
            running=0,
            starting=1,
            popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not spawn")),
        )
    assert lane.state == "created"


def test_wait_ready_probe_retries_then_succeeds(lane):
    _started(lane, FakeProc())
    want = ul.expected_instance_hash(lane.dir)
    seen = []
    probe_answers = iter([False, False, True])

    def fake_probe(inst):
        seen.append((inst, lane.state))
        return next(probe_answers)

    ul.wait_ready(
        lane,
        lambda: [{"hash": want, "project": "Proj"}],
        sleep=lambda s: None,
        probe=fake_probe,
    )
    assert lane.state == "ready"
    assert len(seen) == 3
    assert all(st == "starting" for _, st in seen)
    assert seen[0][0] == {"hash": want, "project": "Proj"}


def test_wait_ready_probe_never_succeeds_times_out(lane):
    _started(lane, FakeProc())
    want = ul.expected_instance_hash(lane.dir)
    t = [0.0]

    def clock():
        t[0] += 100
        return t[0]

    with pytest.raises(ul.LaneError):
        ul.wait_ready(
            lane,
            lambda: [{"hash": want, "project": "Proj"}],
            timeout_s=300,
            sleep=lambda s: None,
            clock=clock,
            probe=lambda inst: False,
        )
    assert lane.state == "failed"


def test_wait_ready_license_error_by_exit_code_198(lane):
    _started(lane, FakeProc(rc=198))
    with pytest.raises(ul.LaneError) as e:
        ul.wait_ready(lane, lambda: [], sleep=lambda s: None)
    assert lane.state == "failed"
    assert str(e.value) == (
        "Unity lisansı bulunamadı: Unity Hub'ı açıp oturum açın ve "
        "lisansın etkin olduğunu kontrol edin, sonra tekrar deneyin."
    )


def test_wait_ready_license_error_by_log_line(lane):
    _started(lane, FakeProc(rc=1))
    os.makedirs(os.path.dirname(ul.lane_log_path(lane.dir)), exist_ok=True)
    with open(ul.lane_log_path(lane.dir), "w", encoding="utf-8") as fh:
        fh.write("License check...\nNo valid Unity Editor license found. Please activate.\n")
    with pytest.raises(ul.LaneError) as e:
        ul.wait_ready(lane, lambda: [], sleep=lambda s: None)
    assert lane.state == "failed"
    assert "Unity lisansı bulunamadı" in str(e.value)


def test_stop_editor_waits_for_process_and_retries_lockfile_removal(lane, monkeypatch):
    class WaitSpyProc(FakeProc):
        def __init__(self):
            super().__init__(rc=0)  # already has rc (e.g. job.terminate ran first)
            self.wait_timeouts = []

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)
            return self.rc

    proc = WaitSpyProc()
    _started(lane, proc)
    lock = os.path.join(lane.dir, "Temp", "UnityLockfile")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    open(lock, "w").close()

    orig_remove = os.remove
    remove_attempts = [0]

    def flaky_remove(path):
        if os.path.normcase(path) == os.path.normcase(lock):
            remove_attempts[0] += 1
            if remove_attempts[0] == 1:
                raise PermissionError("locked by dying process")
        return orig_remove(path)

    sleeps = []
    monkeypatch.setattr(ul.os, "remove", flaky_remove)
    ul.stop_editor(lane, sleep=sleeps.append)

    assert proc.wait_timeouts == [10]
    assert remove_attempts[0] == 2
    assert sleeps == [0.5]
    assert not os.path.exists(lock)
    assert lane.state == "stopped"


def test_stop_editor_skips_wait_when_proc_is_none(lane):
    lane.proc = None
    lane.state = "starting"
    ul.stop_editor(lane, sleep=lambda s: None)
    assert lane.state == "stopped"


class _FakeRegistry:
    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.writes = []

    def read_all(self):
        return dict(self.store)

    def write(self, name, data):
        self.writes.append((name, data))
        self.store[name] = data


@pytest.fixture(autouse=True)
def _isolate_hub_registry(monkeypatch):
    monkeypatch.setattr(ul, "_registry", _FakeRegistry())


def _lane_reg_bytes(path, *, backslashes=False, trailing_slash=False, upper=False):
    text = os.path.abspath(path).replace("\\", "/")
    if trailing_slash and not text.endswith("/"):
        text += "/"
    if backslashes:
        text = text.replace("/", "\\")
    if upper:
        text = text.upper()
    return (text + "\x00").encode("utf-8")


def test_hub_snapshot_taken_at_start_editor(lane, monkeypatch):
    old_proj = b"C:/Unity Projeler/MainProj\x00"
    old_ws = b"C:/Unity Projeler/\x00"
    reg = _FakeRegistry({
        "LastUsedProjectPath_h1": old_proj,
        "kWorkspacePath_h2": old_ws,
    })
    monkeypatch.setattr(ul, "_registry", reg)

    ul.start_editor(lane, free_bytes=10 * GB, popen=lambda *a, **k: FakeProc())
    try:
        assert lane.hub_snapshot == {
            "LastUsedProjectPath_h1": old_proj,
            "kWorkspacePath_h2": old_ws,
        }
        # Mutating the store afterwards must not mutate the saved snapshot.
        reg.store["LastUsedProjectPath_h1"] = b"C:/Changed\x00"
        assert lane.hub_snapshot["LastUsedProjectPath_h1"] == old_proj
    finally:
        if lane.job is not None:
            lane.job.close()
            lane.job = None


def test_hub_wait_ready_restores_lane_values_and_leaves_unrelated_and_missing(lane, monkeypatch):
    old_proj = b"C:/Unity Projeler/MainProj\x00"
    old_base = b"C:/Unity Projeler/MainProj\x00"
    unrelated = b"C:/Unity Projeler/OtherProj\x00"
    lane_bytes = _lane_reg_bytes(lane.dir)
    reg = _FakeRegistry({
        "LastUsedProjectPath_h1": old_proj,
        "kProjectBasePath_h2": old_base,
    })
    monkeypatch.setattr(ul, "_registry", reg)

    ul.start_editor(lane, free_bytes=10 * GB, popen=lambda *a, **k: FakeProc())
    try:
        # Unity overwrote h1 to lane dir, h2 to an unrelated project, and added h9 not in snapshot.
        reg.store["LastUsedProjectPath_h1"] = lane_bytes
        reg.store["kProjectBasePath_h2"] = unrelated
        reg.store["LastUsedProjectPath_h9"] = lane_bytes

        want = ul.expected_instance_hash(lane.dir)
        ul.wait_ready(lane, lambda: [{"hash": want}], sleep=lambda s: None)

        assert lane.state == "ready"
        assert reg.store["LastUsedProjectPath_h1"] == old_proj
        assert reg.store["kProjectBasePath_h2"] == unrelated
        assert reg.store["LastUsedProjectPath_h9"] == lane_bytes
        assert reg.writes == [("LastUsedProjectPath_h1", old_proj)]
    finally:
        if lane.job is not None:
            lane.job.close()
            lane.job = None


def test_hub_stop_editor_restores_lane_values(lane, monkeypatch):
    old_proj = b"C:/Unity Projeler/MainProj\x00"
    reg = _FakeRegistry({"LastUsedProjectPath_h1": old_proj})
    monkeypatch.setattr(ul, "_registry", reg)

    ul.start_editor(lane, free_bytes=10 * GB, popen=lambda *a, **k: FakeProc())
    reg.store["LastUsedProjectPath_h1"] = _lane_reg_bytes(lane.dir)

    ul.stop_editor(lane, sleep=lambda s: None)

    assert lane.state == "stopped"
    assert reg.store["LastUsedProjectPath_h1"] == old_proj
    assert reg.writes == [("LastUsedProjectPath_h1", old_proj)]


def test_hub_kworkspace_parent_lanes_dir_is_restored(lane, monkeypatch):
    old_ws = b"C:/Unity Projeler/\x00"
    old_proj = b"C:/Unity Projeler/MainProj\x00"
    reg = _FakeRegistry({
        "kWorkspacePath_h1": old_ws,
        "LastUsedProjectPath_h2": old_proj,
    })
    monkeypatch.setattr(ul, "_registry", reg)

    ul.start_editor(lane, free_bytes=10 * GB, popen=lambda *a, **k: FakeProc())
    try:
        lanes_parent = os.path.dirname(os.path.abspath(lane.dir))
        reg.store["kWorkspacePath_h1"] = _lane_reg_bytes(lanes_parent, trailing_slash=True)
        # Non-kWorkspacePath_ pointing at parent lanes/ should NOT match.
        reg.store["LastUsedProjectPath_h2"] = _lane_reg_bytes(lanes_parent, trailing_slash=True)

        want = ul.expected_instance_hash(lane.dir)
        ul.wait_ready(lane, lambda: [{"hash": want}], sleep=lambda s: None)

        assert reg.store["kWorkspacePath_h1"] == old_ws
        assert reg.store["LastUsedProjectPath_h2"] == _lane_reg_bytes(lanes_parent, trailing_slash=True)
    finally:
        if lane.job is not None:
            lane.job.close()
            lane.job = None


def test_hub_restore_normalizes_case_and_slashes_and_ignores_sibling_prefix(lane):
    old = b"C:/Unity Projeler/Mine\x00"
    sibling = _lane_reg_bytes(lane.dir + "-other")
    lane.hub_snapshot = {
        "LastUsedProjectPath_h1": old,
        "kProjectBasePath_h2": old,
    }
    reg = _FakeRegistry({
        "LastUsedProjectPath_h1": _lane_reg_bytes(lane.dir, backslashes=True, upper=True),
        "kProjectBasePath_h2": sibling,
    })

    written = ul.restore_hub_values(lane, registry=reg)

    assert written == 1
    assert reg.store["LastUsedProjectPath_h1"] == old
    assert reg.store["kProjectBasePath_h2"] == sibling


def test_hub_restore_never_writes_back_another_lanes_path(lane):
    # The snapshot was taken while another lane's editor already pointed Unity at that lane.
    other_lane = os.path.join(os.path.dirname(lane.dir), "other")
    lane.hub_snapshot = {"LastUsedProjectPath_h1": _lane_reg_bytes(other_lane)}
    reg = _FakeRegistry({"LastUsedProjectPath_h1": _lane_reg_bytes(lane.dir)})

    assert ul.restore_hub_values(lane, registry=reg) == 0
    assert reg.writes == []


def test_hub_win_registry_noop_off_windows(monkeypatch):
    monkeypatch.setattr(ul.sys, "platform", "linux")
    reg = ul._WinRegistry()
    assert reg.read_all() == {}
    reg.write("LastUsedProjectPath_h1", b"C:/x\x00")


def test_hub_registry_errors_never_fail_start_wait_or_stop(lane, monkeypatch):
    class BrokenRegistry:
        def read_all(self):
            raise RuntimeError("boom read")

        def write(self, name, data):
            raise RuntimeError("boom write")

    monkeypatch.setattr(ul, "_registry", BrokenRegistry())
    ul.start_editor(lane, free_bytes=10 * GB, popen=lambda *a, **k: FakeProc())
    try:
        assert lane.hub_snapshot == {}
        lane.hub_snapshot = {"LastUsedProjectPath_h1": b"C:/Old\x00"}
        assert ul.restore_hub_values(lane, registry=BrokenRegistry()) == 0

        monkeypatch.setattr(ul, "restore_hub_values", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
        want = ul.expected_instance_hash(lane.dir)
        ul.wait_ready(lane, lambda: [{"hash": want}], sleep=lambda s: None)
        assert lane.state == "ready"
        ul.stop_editor(lane, sleep=lambda s: None)
        assert lane.state == "stopped"
    finally:
        if lane.job is not None:
            lane.job.close()
            lane.job = None
