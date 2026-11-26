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
    assert ul.memory_refusal(5 * GB - 1, 0)
    assert ul.memory_refusal(5 * GB, 0) is None
    assert ul.memory_refusal(20 * GB, 3) is None


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
