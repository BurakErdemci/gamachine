"""Regression tests for the 27 Nov 2026 Unity lane and scene-mode audit fixes (items 1-8)."""
import asyncio
import os
import subprocess
import threading
import time
import types
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import process_tree
import scene_mode
import unity_lanes as ul
from agentic import subagents
from database import DatabaseManager
from routes.workspace_routes import create_workspace_router
from tests.test_subagents import (  # noqa: F401
    CODEX, DONE, _Runner, _ep, _idle, _main, _post, _results, _until, env, network_guard,
)

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=GIT_ENV)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r


def _make_git_project(tmp_path, name="UnityProj"):
    proj = tmp_path / name
    (proj / "Assets").mkdir(parents=True)
    (proj / "ProjectSettings").mkdir()
    (proj / "Packages").mkdir()
    (proj / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.1.2f1\n")
    (proj / "Packages" / "manifest.json").write_text("{}")
    (proj / "Assets" / "a.txt").write_text("x\n")
    _git(proj, "init", "-q")
    _git(proj, "config", "user.name", "t")
    _git(proj, "config", "user.email", "t@t")
    _git(proj, "config", "core.autocrlf", "false")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "init")
    return str(proj)


def _branches(project):
    return _git(project, "branch", "--list", "--format=%(refname:short)", "gamachine/*").stdout.split()


class _FakeProc:
    def __init__(self, *, rc=None, wait_delay=0.0):
        self.pid = 777001
        self._rc = rc
        self.terminated = False
        self.wait_delay = wait_delay

    def poll(self):
        return self._rc

    def terminate(self):
        self.terminated = True
        self._rc = 0

    def kill(self):
        self._rc = -9

    def wait(self, timeout=None):
        if self.wait_delay:
            time.sleep(self.wait_delay)
        return self._rc


@pytest.fixture
def lane_env(env, tmp_path, monkeypatch):
    proj = _make_git_project(tmp_path)
    env.db.save_workspace(1, proj)
    hub = tmp_path / "hub"
    exe = ul._exe_under(str(hub), "6000.1.2f1")
    os.makedirs(os.path.dirname(exe))
    open(exe, "w").close()
    st = types.SimpleNamespace(
        proj=proj,
        procs=[],
        free=32 * ul.GB,
        popen_calls=0,
        stop_delay=0.0,
        instances_ok=True,
    )
    monkeypatch.setattr(ul, "_hub_roots", lambda: [str(hub)])
    monkeypatch.setattr(ul, "_install_package", lambda _d: True)
    monkeypatch.setattr(ul, "_yaml_merge_exe", lambda _p: None)
    monkeypatch.setattr(process_tree, "new_job_object_for", lambda _p: None)
    monkeypatch.setattr(ul, "free_memory_bytes", lambda: st.free)

    real_start = ul.start_editor

    def fake_popen(*_a, **_kw):
        st.popen_calls += 1
        p = _FakeProc(wait_delay=st.stop_delay)
        st.procs.append(p)
        return p

    monkeypatch.setattr(ul, "start_editor", lambda rec, **kw: real_start(rec, popen=fake_popen, **kw))
    real_wait = ul.wait_ready
    monkeypatch.setattr(ul, "wait_ready", lambda rec, li, **kw: real_wait(rec, li, **{**kw, "timeout_s": 6}))

    def list_instances():
        if not st.instances_ok:
            return []
        return [
            {"hash": ul.expected_instance_hash(rec.dir), "project": rec.name}
            for rec in list(subagents._LANES.values())
        ]

    monkeypatch.setattr(subagents, "_list_instances", list_instances)
    monkeypatch.setattr(subagents, "_probe_instance", lambda _inst: True)
    return env, st


async def _start_lane(env, parent, lane, name=None, brief="Work"):
    return await _ep(env.srouter, "/subagents/start")(
        body={"conversation_id": parent, "model": CODEX, "effort": "high", "brief": brief,
              "name": name, "lane": lane},
        x_session_token="",
    )


async def _lanes_idle(timeout=15.0):
    await _idle(timeout=timeout)
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while subagents._LANE_TASKS or subagents._STOP_TASKS:
        assert loop.time() < end, "lane task never finished"
        await asyncio.sleep(0.01)
    await _idle(timeout=timeout)


# ── 1. Concurrent merge_lane ─────────────────────────────────────────────────

def test_concurrent_same_and_multiple_lane_merges_serialized(tmp_path, monkeypatch):
    monkeypatch.setattr(ul, "_install_package", lambda _d: True)
    monkeypatch.setattr(ul, "_yaml_merge_exe", lambda _p: None)
    project = _make_git_project(tmp_path)

    # Same lane merged twice concurrently: one merges, second refuses cleanly.
    r_same = ul.create_lane(project, "same")
    open(os.path.join(r_same.dir, "Assets", "same.txt"), "w").write("same\n")
    _git(r_same.dir, "add", "-A")
    _git(r_same.dir, "commit", "-qm", "same")
    results_same = []
    t1 = threading.Thread(target=lambda: results_same.append(ul.merge_lane(project, r_same)))
    t2 = threading.Thread(target=lambda: results_same.append(ul.merge_lane(project, r_same)))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(r["result"] for r in results_same) == ["merged", "refused"]
    assert _git(project, "status", "--porcelain").stdout.strip() == ""
    assert not os.path.exists(os.path.join(project, ".git", "MERGE_HEAD"))
    assert os.path.isfile(os.path.join(project, "Assets", "same.txt"))

    # Multiple lanes merged concurrently: all merge cleanly without dirty tree.
    recs = []
    for n in ("a", "b", "c"):
        r = ul.create_lane(project, n)
        open(os.path.join(r.dir, "Assets", f"{n}.txt"), "w").write(n)
        _git(r.dir, "add", "-A")
        _git(r.dir, "commit", "-qm", n)
        recs.append(r)
    out = {}
    ts = [threading.Thread(target=lambda rec=r: out.__setitem__(rec.name, ul.merge_lane(project, rec))) for r in recs]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert all(out[n]["result"] == "merged" for n in ("a", "b", "c"))
    assert _git(project, "status", "--porcelain").stdout.strip() == ""
    assert not os.path.exists(os.path.join(project, ".git", "MERGE_HEAD"))
    for n in ("a", "b", "c"):
        assert os.path.isfile(os.path.join(project, "Assets", f"{n}.txt"))


# ── 2. Lane HEAD off its branch ──────────────────────────────────────────────

def test_merge_lane_refuses_detached_head_and_other_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(ul, "_install_package", lambda _d: True)
    monkeypatch.setattr(ul, "_yaml_merge_exe", lambda _p: None)
    project = _make_git_project(tmp_path)

    rec1 = ul.create_lane(project, "det")
    _git(rec1.dir, "checkout", "-q", "--detach")
    open(os.path.join(rec1.dir, "Assets", "det.txt"), "w").write("d")
    _git(rec1.dir, "add", "-A")
    _git(rec1.dir, "commit", "-qm", "detached commit")
    res1 = ul.merge_lane(project, rec1)
    assert res1["result"] == "refused"
    assert "gamachine/det" in res1["message"] and "commit" in res1["message"]
    assert os.path.isdir(rec1.dir)
    assert "gamachine/det" in _branches(project)

    rec2 = ul.create_lane(project, "other")
    _git(rec2.dir, "checkout", "-q", "-b", "feature")
    open(os.path.join(rec2.dir, "Assets", "feat.txt"), "w").write("f")
    _git(rec2.dir, "add", "-A")
    _git(rec2.dir, "commit", "-qm", "feature commit")
    res2 = ul.merge_lane(project, rec2)
    assert res2["result"] == "refused"
    assert "gamachine/other" in res2["message"] and "commit" in res2["message"]
    assert os.path.isdir(rec2.dir)
    assert "gamachine/other" in _branches(project)


# ── 3. lane_merge waits for editor stop after result ─────────────────────────

async def test_lane_merge_waits_for_editor_stop_but_refuses_while_turn_runs(lane_env):
    env, st = lane_env
    st.stop_delay = 0.8
    parent = _main(env)
    _Runner.scripts = {}
    started = await _start_lane(env, parent, "wait-stop", name="h")
    child = started["chat_id"]

    # Hold turn open -> lane_merge must refuse 409 lane_busy.
    _Runner.scripts[child] = ["hold", ("response", {"content": "bitti"}), DONE]
    await _until(lambda: len(_Runner.of(child)) == 1, timeout=10)
    with pytest.raises(subagents.SubagentError) as busy_exc:
        await subagents.merge_lane(parent, 1, "wait-stop")
    assert (busy_exc.value.status, busy_exc.value.code) == (409, "lane_busy")

    # Release turn -> editor stop starts in background; lane_merge immediately after result waits and succeeds.
    _Runner.holds[child].set()
    await _until(lambda: len(_results(env.db, parent)) == 1, timeout=10)
    res = await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="wait-stop")
    assert res["result"] == "empty"
    await _lanes_idle()


# ── 4. Two lanes started simultaneously with room for one ────────────────────

async def test_two_simultaneous_lanes_memory_gate_admits_only_one(lane_env, monkeypatch):
    env, st = lane_env
    st.free = 9 * ul.GB
    gate = threading.Barrier(2, timeout=0.4)

    def gated_free():
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        return st.free

    monkeypatch.setattr(ul, "free_memory_bytes", gated_free)
    parent = _main(env)
    _Runner.scripts = {}
    a = await _start_lane(env, parent, "mem-a", name="a")
    b = await _start_lane(env, parent, "mem-b", name="b")
    await _lanes_idle()

    statuses = sorted([
        env.db.get_subagent(a["agent_id"])["status"],
        env.db.get_subagent(b["agent_id"])["status"],
    ])
    assert statuses == ["done", "error"]
    assert st.popen_calls == 1


# ── 5. agent_message after restart and after merge ───────────────────────────

async def test_message_after_restart_and_after_merge_recreates_and_runs_editor(lane_env):
    env, st = lane_env
    parent = _main(env)
    started = await _start_lane(env, parent, "reopen", name="h")
    child = started["chat_id"]
    await _lanes_idle()
    assert st.popen_calls == 1

    # Simulated backend restart: in-memory _LANES/_CHILD_LANE wiped.
    subagents.configure(env.db)
    await _post(env, "/subagents/message", conversation_id=parent, agent_id=started["agent_id"], text="after restart")
    await _lanes_idle()
    assert st.popen_calls == 2
    assert _Runner.of(child)[-1] == "after restart"

    # Merge & remove worktree/branch, then send another message -> recreates lane and starts editor.
    merged = await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="reopen")
    assert merged["result"] == "empty"
    assert not os.path.exists(ul.lane_dir(st.proj, "reopen"))

    await _post(env, "/subagents/message", conversation_id=parent, agent_id=started["agent_id"], text="after merge")
    await _lanes_idle()
    assert st.popen_calls == 3
    lane_ws = ul.lane_dir(st.proj, "reopen")
    assert os.path.isdir(lane_ws)
    assert "gamachine/reopen" in _branches(st.proj)
    child_kws = [k for k in _Runner.kwargs if k["conversation_id"] == child]
    assert child_kws[-1]["workspace_path"] == lane_ws
    assert _Runner.of(child)[-1] == "after merge"


# ── 6. Failed lane start cleanup and retry real reason ───────────────────────

async def test_failed_start_cleans_created_lane_preserves_existing_and_retries_real_reason(lane_env, monkeypatch):
    env, st = lane_env
    parent = _main(env)

    # Memory failure on newly created lane removes worktree and branch.
    st.free = 1 * ul.GB
    s1 = await _start_lane(env, parent, "fail-new", name="h1")
    await _lanes_idle()
    assert env.db.get_subagent(s1["agent_id"])["status"] == "error"
    assert not os.path.exists(ul.lane_dir(st.proj, "fail-new"))
    assert "gamachine/fail-new" not in _branches(st.proj)

    # Retrying after create_lane failure still reports create_lane's real reason.
    monkeypatch.setattr(ul, "create_lane", lambda _p, _n: (_ for _ in ()).throw(ul.LaneError("git kök klasörü gerekli")))
    s2 = await _start_lane(env, parent, "fail-create", name="h2")
    await _lanes_idle()
    assert "git kök klasörü gerekli" in env.db.get_subagent(s2["agent_id"])["last_result"]
    await _post(env, "/subagents/message", conversation_id=parent, agent_id=s2["agent_id"], text="tekrar")
    await _lanes_idle()
    assert "git kök klasörü gerekli" in env.db.get_subagent(s2["agent_id"])["last_result"]


async def test_failed_start_preserves_preexisting_worktree(lane_env):
    env, st = lane_env
    parent = _main(env)
    ul.create_lane(st.proj, "preexisting")
    st.free = 1 * ul.GB
    started = await _start_lane(env, parent, "preexisting", name="h")
    await _lanes_idle()
    assert env.db.get_subagent(started["agent_id"])["status"] == "error"
    assert os.path.isdir(ul.lane_dir(st.proj, "preexisting"))
    assert "gamachine/preexisting" in _branches(st.proj)


# ── 7. Hub values with two lanes ─────────────────────────────────────────────

def test_hub_values_restored_with_two_concurrent_lanes_both_orders(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    hub = tmp_path / "hub"
    exe = ul._exe_under(str(hub), "6000.1.2f1")
    os.makedirs(os.path.dirname(exe))
    open(exe, "w").close()
    for n in ("a", "b"):
        os.makedirs(proj / ".gamachine" / "lanes" / n / "ProjectSettings")
        (proj / ".gamachine" / "lanes" / n / "ProjectSettings" / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.1.2f1\n"
        )
    monkeypatch.setattr(ul, "_hub_roots", lambda: [str(hub)])
    monkeypatch.setattr(process_tree, "new_job_object_for", lambda _p: None)

    class _Reg:
        def __init__(self, initial):
            self.v = dict(initial)

        def read_all(self):
            return dict(self.v)

        def write(self, name, data):
            self.v[name] = data

    def enc(p):
        return (str(p) + "\x00").encode("utf-8")

    reg = _Reg({"LastUsedProjectPath_h1": enc(proj)})
    monkeypatch.setattr(ul, "_registry", reg)
    a = ul.LaneRecord(name="a", dir=str(proj / ".gamachine" / "lanes" / "a"), branch="gamachine/a")
    b = ul.LaneRecord(name="b", dir=str(proj / ".gamachine" / "lanes" / "b"), branch="gamachine/b")

    # Order 1: A starts -> registers A -> B starts (sees A) -> registers B -> A & B restore & stop
    ul.start_editor(a, free_bytes=32 * ul.GB, popen=lambda *_a, **_k: _FakeProc())
    reg.v["LastUsedProjectPath_h1"] = enc(a.dir)
    ul.start_editor(b, free_bytes=32 * ul.GB, popen=lambda *_a, **_k: _FakeProc())
    reg.v["LastUsedProjectPath_h1"] = enc(b.dir)
    ul.restore_hub_values(a)
    ul.restore_hub_values(b)
    ul.stop_editor(a, sleep=lambda _s: None)
    ul.stop_editor(b, sleep=lambda _s: None)
    assert reg.v["LastUsedProjectPath_h1"] == enc(proj)

    # Order 2: A starts -> registers A -> B starts -> A restores -> registers B -> B restores -> both stop
    ul.start_editor(a, free_bytes=32 * ul.GB, popen=lambda *_a, **_k: _FakeProc())
    reg.v["LastUsedProjectPath_h1"] = enc(a.dir)
    ul.start_editor(b, free_bytes=32 * ul.GB, popen=lambda *_a, **_k: _FakeProc())
    ul.restore_hub_values(a)
    reg.v["LastUsedProjectPath_h1"] = enc(b.dir)
    ul.restore_hub_values(b)
    ul.stop_editor(a, sleep=lambda _s: None)
    ul.stop_editor(b, sleep=lambda _s: None)
    assert reg.v["LastUsedProjectPath_h1"] == enc(proj)


# ── 8. Scene mode retry & non-file / route 400 errors ────────────────────────

def test_scene_mode_retries_permission_error_and_route_turns_oserror_into_400(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "Assets").mkdir(parents=True)
    (proj / "ProjectSettings").mkdir()

    # Transient PermissionError during os.replace succeeds on retry.
    orig_replace = os.replace
    attempts = [0]

    def flaky_replace(src, dst):
        attempts[0] += 1
        if attempts[0] <= 2:
            raise PermissionError("reader locked")
        return orig_replace(src, dst)

    monkeypatch.setattr(scene_mode.os, "replace", flaky_replace)
    monkeypatch.setattr(scene_mode.time, "sleep", lambda _s: None)
    assert scene_mode.set(str(proj), "code") == "code"
    assert attempts[0] == 3
    assert scene_mode.get(str(proj)) == "code"

    # Non-file at target path raises ValueError.
    target = proj / "UserSettings" / "Gamachine" / "scene_mode.json"
    target.unlink()
    target.mkdir()
    with pytest.raises(ValueError):
        scene_mode.set(str(proj), "team")
    target.rmdir()

    # POST /workspace/scene-mode turns ValueError and OSError into 400, never 500.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db = DatabaseManager(str(tmp_path / "scene.db"))
    db.save_workspace(1, str(proj))
    app = FastAPI()
    app.include_router(create_workspace_router(db))
    with TestClient(app, raise_server_exceptions=False) as client:
        target.mkdir()
        r_dir = client.post("/workspace/scene-mode", json={"mode": "team"})
        assert r_dir.status_code == 400
        assert "not a file" in r_dir.json()["detail"]

        monkeypatch.setattr(scene_mode, "set", lambda _w, _m: (_ for _ in ()).throw(PermissionError("locked")))
        r_perm = client.post("/workspace/scene-mode", json={"mode": "team"})
        assert r_perm.status_code == 400
        assert "locked" in r_perm.json()["detail"]


# ── 9. Fix2: merge lock, scope_workspace missing lane, create_lane prune ─────

async def test_merging_lane_refuses_message_start_and_second_merge_then_clears(lane_env, monkeypatch):
    env, st = lane_env
    parent = _main(env)
    started = await _start_lane(env, parent, "merge-lock", name="h")
    await _lanes_idle()

    entered = threading.Event()
    release = threading.Event()
    real_merge = ul.merge_lane

    def slow_merge(project, record, **kw):
        entered.set()
        assert release.wait(timeout=10.0)
        return real_merge(project, record, **kw)

    monkeypatch.setattr(ul, "merge_lane", slow_merge)
    merge_task = asyncio.create_task(subagents.merge_lane(parent, 1, "merge-lock"))
    await _until(entered.is_set, timeout=5)

    expected_msg = 'Lane "merge-lock" is being merged; send the message after lane_merge returns.'
    calls_before = st.popen_calls

    with pytest.raises(subagents.SubagentError) as msg_exc:
        subagents.message(parent, started["agent_id"], "devam")
    assert (msg_exc.value.status, msg_exc.value.code, msg_exc.value.message) == (409, "lane_busy", expected_msg)

    cat = await env.catalog(1)
    with pytest.raises(subagents.SubagentError) as start_exc:
        subagents.start(parent, 1, model=CODEX, effort="high", brief="yeni", catalog=cat, lane="merge-lock")
    assert (start_exc.value.status, start_exc.value.code, start_exc.value.message) == (409, "lane_busy", expected_msg)

    with pytest.raises(subagents.SubagentError) as merge2_exc:
        await subagents.merge_lane(parent, 1, "merge-lock")
    assert (merge2_exc.value.status, merge2_exc.value.code, merge2_exc.value.message) == (409, "lane_busy", expected_msg)

    assert st.popen_calls == calls_before
    assert len(env.db.list_subagents(parent)) == 1

    release.set()
    res = await merge_task
    assert res["result"] == "empty"
    assert not subagents._MERGING_LANES

    # Also clears on error exit (e.g. 404 unknown_lane).
    with pytest.raises(subagents.SubagentError) as unk_exc:
        await subagents.merge_lane(parent, 1, "merge-lock")
    assert (unk_exc.value.status, unk_exc.value.code) == (404, "unknown_lane")
    assert not subagents._MERGING_LANES


async def test_scope_workspace_keeps_missing_lane_folder_and_never_falls_back(lane_env):
    from unity_lane_paths import scope_workspace

    env, st = lane_env
    parent = _main(env)
    started = await _start_lane(env, parent, "gone-lane", name="h")
    await _lanes_idle()
    res = await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="gone-lane")
    assert res["result"] == "empty"
    lane_ws = ul.lane_dir(st.proj, "gone-lane")
    assert not os.path.exists(lane_ws)
    assert os.path.realpath(scope_workspace(env.db, started["chat_id"], 1)) == os.path.realpath(lane_ws)


def test_create_lane_recreates_missing_worktree_with_force_without_pruning(tmp_path, monkeypatch):
    import shutil
    import stat

    monkeypatch.setattr(ul, "_install_package", lambda _d: True)
    project = _make_git_project(tmp_path)
    rec = ul.create_lane(project, "hand-del")

    def _rw(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(rec.dir, onerror=_rw)
    assert not os.path.exists(rec.dir)

    seen_git = []
    real_git = ul._git

    def spy_git(proj, *args, **kw):
        seen_git.append(args)
        return real_git(proj, *args, **kw)

    monkeypatch.setattr(ul, "_git", spy_git)
    rec2 = ul.create_lane(project, "hand-del")
    assert os.path.isdir(rec2.dir)
    assert not any(len(a) >= 2 and a[0] == "worktree" and a[1] == "prune" for a in seen_git)
    add_calls = [a for a in seen_git if len(a) >= 2 and a[0] == "worktree" and a[1] == "add"]
    assert add_calls and "-f" in add_calls[0]

    # When the lane folder exists, create_lane refuses without pruning or adding.
    seen_git.clear()
    with pytest.raises(ul.LaneError):
        ul.create_lane(project, "hand-del")
    assert not any(len(a) >= 2 and a[0] == "worktree" and a[1] in ("prune", "add") for a in seen_git)


async def test_cancelled_merge_keeps_lane_marked_until_thread_returns(lane_env, monkeypatch):
    env, st = lane_env
    parent = _main(env)
    started = await _start_lane(env, parent, "cancel-merge", name="h")
    await _lanes_idle()
    d = ul.lane_dir(st.proj, "cancel-merge")
    open(os.path.join(d, "Assets", "work.txt"), "w").write("lane\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "lane work")
    await _post(env, "/subagents/stop", conversation_id=parent, agent_id=started["agent_id"])
    await _lanes_idle()

    entered = threading.Event()
    release = threading.Event()
    real_merge = ul.merge_lane

    def slow_merge(project, record, **kw):
        entered.set()
        assert release.wait(timeout=10.0)
        return real_merge(project, record, **kw)

    monkeypatch.setattr(ul, "merge_lane", slow_merge)
    merge_task = asyncio.create_task(subagents.merge_lane(parent, 1, "cancel-merge"))
    await _until(entered.is_set, timeout=5)

    merge_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await merge_task

    # Worker thread is still running: mark must remain and start/message must be refused.
    assert subagents._MERGING_LANES
    calls_before = st.popen_calls
    with pytest.raises(subagents.SubagentError) as msg_exc:
        subagents.message(parent, started["agent_id"], "devam")
    assert (msg_exc.value.status, msg_exc.value.code) == (409, "lane_busy")

    cat = await env.catalog(1)
    with pytest.raises(subagents.SubagentError) as start_exc:
        subagents.start(parent, 1, model=CODEX, effort="high", brief="yeni", catalog=cat, lane="cancel-merge")
    assert (start_exc.value.status, start_exc.value.code) == (409, "lane_busy")
    assert st.popen_calls == calls_before

    release.set()
    await _until(lambda: not subagents._MERGING_LANES, timeout=10)
    await _lanes_idle()
    assert os.path.isfile(os.path.join(st.proj, "Assets", "work.txt"))


def test_create_lane_keeps_other_missing_worktree_registered(tmp_path, monkeypatch):
    import shutil
    import stat

    monkeypatch.setattr(ul, "_install_package", lambda _d: True)
    project = _make_git_project(tmp_path)

    ext = tmp_path / "ext_wt"
    _git(project, "worktree", "add", "-b", "usr/feature", str(ext))
    hidden = tmp_path / "ext_wt_hidden"
    os.rename(ext, hidden)

    def _rw(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    rec = ul.create_lane(project, "reopen-me")
    shutil.rmtree(rec.dir, onerror=_rw)
    rec2 = ul.create_lane(project, "reopen-me")
    assert os.path.isdir(rec2.dir)

    os.rename(hidden, ext)
    assert _git(ext, "status", "--short").returncode == 0
