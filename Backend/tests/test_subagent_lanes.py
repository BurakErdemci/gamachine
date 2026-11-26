"""Unity lane wiring for sub-agents (database.create_subagent, agentic/subagents.py,
routes/subagent_routes.py, unity_ai_mcp/tools/agent_tools.py).

Uses the fake runner and `network_guard` from test_subagents; monkeypatches
unity_lanes and the instance lister so no git, Unity or HTTP runs.
"""
import asyncio
import json
import os
import threading
import types

import httpx
import pytest
from fastapi import HTTPException

import unity_lanes
from agentic import subagents, turn_runner
from tests.test_subagents import (  # noqa: F401
    CODEX, DONE, _Runner, _ep, _idle, _main, _post, _results, _until, env, network_guard,
)
from unity_ai_mcp.tools import agent_tools


@pytest.fixture
def lanes_env(env, tmp_path, monkeypatch):
    proj = tmp_path / "UnityProj"
    (proj / "Assets").mkdir(parents=True)
    (proj / "ProjectSettings").mkdir()
    env.db.save_workspace(1, str(proj))

    state = types.SimpleNamespace(
        proj=str(proj),
        created=[],
        started=[],
        waited=[],
        stopped=[],
        create_exc=None,
        start_exc=None,
        wait_exc=None,
        wait_gate=None,
        free_bytes=16 * unity_lanes.GB,
        instances=[{"hash": "abc"}],
    )

    def fake_create(project, name):
        if state.create_exc is not None:
            raise state.create_exc
        d = unity_lanes.lane_dir(project, name)
        os.makedirs(os.path.join(d, "Assets"), exist_ok=True)
        rec = unity_lanes.LaneRecord(name=name, dir=d, branch=f"gamachine/{name}")
        state.created.append(rec)
        return rec

    def fake_start_editor(record, *, free_bytes=None, running=0, starting=0, **kw):
        if state.start_exc is not None:
            raise state.start_exc
        record.pid = 4242
        record.state = "starting"
        state.started.append((record, free_bytes, running, starting))
        return record

    def fake_wait_ready(record, list_instances, **kw):
        state.waited.append((record, list_instances, kw))
        gate = state.wait_gate
        if gate is not None:
            assert gate.wait(timeout=5.0), "wait_gate never opened"
        if state.wait_exc is not None:
            record.state = "failed"
            raise state.wait_exc
        record.state = "ready"
        return record

    def fake_stop_editor(record):
        record.pid = None
        record.state = "stopped"
        state.stopped.append(record)
        return record

    monkeypatch.setattr(unity_lanes, "create_lane", fake_create)
    monkeypatch.setattr(unity_lanes, "start_editor", fake_start_editor)
    monkeypatch.setattr(unity_lanes, "wait_ready", fake_wait_ready)
    monkeypatch.setattr(unity_lanes, "stop_editor", fake_stop_editor)
    monkeypatch.setattr(unity_lanes, "free_memory_bytes", lambda: state.free_bytes)
    monkeypatch.setattr(subagents, "_list_instances", lambda: list(state.instances))
    return env, state


async def _start_lane(env, parent, lane=None, model=CODEX, effort="high",
                      brief="Sahneyi güncelle.", name=None):
    return await _ep(env.srouter, "/subagents/start")(
        body={"conversation_id": parent, "model": model, "effort": effort,
              "brief": brief, "name": name, "lane": lane},
        x_session_token="",
    )


async def _lanes_idle(timeout=5.0):
    await _idle(timeout=timeout)
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while subagents._LANE_TASKS or subagents._STOP_TASKS:
        assert loop.time() < end, "lane task never finished"
        await asyncio.sleep(0.005)
    await _idle(timeout=timeout)


async def test_no_lane_behaves_synchronously_as_today(lanes_env, monkeypatch):
    env, state = lanes_env
    parent = _main(env)
    bg_calls = []
    real_start_bg = turn_runner.start_background_turn

    def spy(conv_id, text, origin="background"):
        bg_calls.append((conv_id, text, origin))
        return real_start_bg(conv_id, text, origin)

    monkeypatch.setattr(turn_runner, "start_background_turn", spy)
    started = await _start_lane(env, parent, lane="")
    assert "lane" not in started
    assert len(bg_calls) == 1 and bg_calls[0][0] == started["chat_id"]
    assert env.db.get_conversation_workspace(started["chat_id"]) == state.proj
    assert state.created == [] and state.started == [] and state.waited == []
    await _lanes_idle()
    assert state.stopped == []


async def test_lane_waits_for_editor_ready_before_turn_and_includes_lane_note(lanes_env):
    env, state = lanes_env
    parent = _main(env)
    gate = threading.Event()
    state.wait_gate = gate

    started = await _start_lane(env, parent, lane="scene-a", name="sahneci")
    child = started["chat_id"]
    expected_dir = unity_lanes.lane_dir(state.proj, "scene-a")

    assert started["status"] == "running"
    assert started["lane"] == "scene-a"
    assert "first Unity import" in started["text"]
    assert "arrives" in started["text"]
    assert env.db.get_conversation_workspace(child) == expected_dir

    # Let the background task enter wait_ready; turn must NOT start until wait_ready returns.
    await _until(lambda: len(state.waited) == 1)
    assert _Runner.of(child) == []
    assert not turn_runner.busy(child)

    _Runner.replies[child] = "Sahne güncellendi."
    gate.set()
    await _lanes_idle()

    (brief,) = _Runner.of(child)
    assert brief.startswith("Sahneyi güncelle.")
    note = subagents.LANE_NOTE.format(name="scene-a")
    assert note in brief and "<name>" not in brief
    assert subagents.RESULT_CONTRACT in brief
    assert state.started[0][1] == state.free_bytes and state.started[0][2] == 0
    assert state.waited[0][1]() == state.instances
    assert len(state.created) == 1 and len(state.stopped) == 1


async def test_existing_git_worktree_is_reused_without_create_lane(lanes_env):
    env, state = lanes_env
    parent = _main(env)
    existing_dir = unity_lanes.lane_dir(state.proj, "reused")
    os.makedirs(existing_dir)
    with open(os.path.join(existing_dir, ".git"), "w", encoding="utf-8") as fh:
        fh.write("gitdir: ../.git/worktrees/reused\n")

    started = await _start_lane(env, parent, lane="reused")
    await _lanes_idle()

    assert state.created == []
    assert len(state.started) == 1 and state.started[0][0].dir == existing_dir
    assert len(_Runner.of(started["chat_id"])) == 1
    assert len(state.stopped) == 1


async def test_wait_ready_error_sets_error_stops_editor_and_delivers_no_turn(lanes_env):
    env, state = lanes_env
    parent = _main(env)
    state.wait_exc = unity_lanes.LaneError("bağlantı zaman aşımı")

    started = await _start_lane(env, parent, lane="broken")
    child = started["chat_id"]
    await _lanes_idle()

    assert _Runner.of(child) == []
    assert len(state.stopped) == 1
    row = env.db.get_subagent(started["agent_id"])
    assert row["status"] == "error"
    assert row["last_result"] == "error: lane not started (bağlantı zaman aşımı)"
    (res,) = _results(env.db, parent)
    assert ": error\n" in res and "error: lane not started (bağlantı zaman aşımı)" in res
    assert len(_Runner.of(parent)) == 1


async def test_start_editor_error_does_not_call_stop_editor(lanes_env):
    env, state = lanes_env
    parent = _main(env)
    state.start_exc = unity_lanes.LaneError("yetersiz bellek")

    started = await _start_lane(env, parent, lane="oom")
    await _lanes_idle()

    assert state.stopped == []
    assert _Runner.of(started["chat_id"]) == []
    row = env.db.get_subagent(started["agent_id"])
    assert row["status"] == "error"
    assert "error: lane not started (yetersiz bellek)" in row["last_result"]
    assert len(_results(env.db, parent)) == 1


async def test_lane_refusals_bad_lane_no_project_and_lane_busy(lanes_env):
    env, state = lanes_env
    parent = _main(env)

    cat = await env.catalog(1)

    # bad_lane (both SubagentError.code and HTTP 400 via route)
    with pytest.raises(subagents.SubagentError) as serr:
        subagents.start(parent, 1, model=CODEX, effort="high", brief="x",
                        catalog=cat, lane="Bad_Name")
    assert (serr.value.status, serr.value.code) == (400, "bad_lane")
    with pytest.raises(HTTPException) as exc:
        await _start_lane(env, parent, lane="Bad_Name")
    assert exc.value.status_code == 400 and "Şerit adı" in str(exc.value.detail)

    # lane_busy while starting, and while ready
    gate = threading.Event()
    state.wait_gate = gate
    first = await _start_lane(env, parent, lane="busy-1")
    child = first["chat_id"]
    with pytest.raises(subagents.SubagentError) as serr:
        subagents.start(parent, 1, model=CODEX, effort="high", brief="x",
                        catalog=cat, lane="busy-1")
    assert (serr.value.status, serr.value.code) == (409, "lane_busy")
    with pytest.raises(HTTPException) as exc:
        await _start_lane(env, parent, lane="busy-1")
    assert exc.value.status_code == 409 and "çalışıyor" in str(exc.value.detail)

    _Runner.scripts[child] = ["hold", ("response", {"content": "ok"}), DONE]
    gate.set()
    await _until(lambda: len(_Runner.of(child)) == 1)
    with pytest.raises(HTTPException) as exc:
        await _start_lane(env, parent, lane="busy-1")
    assert exc.value.status_code == 409 and "çalışıyor" in str(exc.value.detail)

    _Runner.holds[child].set()
    await _lanes_idle()

    # no_project
    env.db.save_workspace(1, "")
    with pytest.raises(subagents.SubagentError) as serr:
        subagents.start(parent, 1, model=CODEX, effort="high", brief="x",
                        catalog=cat, lane="valid-name")
    assert (serr.value.status, serr.value.code) == (400, "no_project")
    with pytest.raises(HTTPException) as exc:
        await _start_lane(env, parent, lane="valid-name")
    assert exc.value.status_code == 400 and "açık değil" in str(exc.value.detail)


async def test_child_finishing_and_stop_stop_editor_once_and_keep_folder(lanes_env):
    env, state = lanes_env
    parent = _main(env)

    # Normal finish
    first = await _start_lane(env, parent, lane="done-lane")
    await _lanes_idle()
    assert len(state.stopped) == 1
    assert state.stopped[0].state == "stopped"
    assert os.path.isdir(unity_lanes.lane_dir(state.proj, "done-lane"))

    # Explicit stop while turn is running
    second = await _start_lane(env, parent, lane="stop-lane")
    c2 = second["chat_id"]
    _Runner.scripts[c2] = ["hang"]
    await _until(lambda: len(_Runner.of(c2)) == 1)
    stopped = await _post(env, "/subagents/stop", conversation_id=parent,
                          agent_id=second["agent_id"])
    assert stopped["status"] == "stopped"
    await _lanes_idle()
    assert len(state.stopped) == 2
    assert state.stopped[1].state == "stopped"


async def test_message_to_stopped_lane_restarts_editor_before_turn(lanes_env):
    env, state = lanes_env
    parent = _main(env)

    started = await _start_lane(env, parent, lane="resume-lane")
    child = started["chat_id"]
    await _lanes_idle()
    assert len(state.started) == 1 and len(state.waited) == 1 and len(state.stopped) == 1

    # Message while stopped: must run start_editor + wait_ready before starting the turn.
    gate = threading.Event()
    state.wait_gate = gate
    sent = await _post(env, "/subagents/message", conversation_id=parent,
                       agent_id=started["agent_id"], text="Bir sahne daha")
    assert sent["status"] == "started"
    await _until(lambda: len(state.waited) == 2)
    assert len(_Runner.of(child)) == 1  # second turn has not started yet

    _Runner.scripts[child] = ["hold", ("response", {"content": "ikinci bitti"}), DONE]
    gate.set()
    await _until(lambda: len(_Runner.of(child)) == 2)
    assert _Runner.of(child)[1] == "Bir sahne daha"

    # Message while ready: queues without restarting the editor.
    state.wait_gate = None
    queued = await _post(env, "/subagents/message", conversation_id=parent,
                         agent_id=started["agent_id"], text="Üçüncü iş")
    assert queued["status"] == "queued"
    assert len(state.started) == 2 and len(state.waited) == 2

    _Runner.holds[child].set()
    await _lanes_idle()
    assert _Runner.of(child)[2] == "Üçüncü iş"
    assert len(state.stopped) == 2


def test_agent_start_tool_posts_lane_only_when_non_empty(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"text": "OK"})

    real = httpx.AsyncClient
    monkeypatch.setattr(agent_tools, "BACKEND_URL", "http://backend.stub")
    monkeypatch.setattr(
        agent_tools.httpx, "AsyncClient",
        lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setenv("GAMACHINE_CONVERSATION_ID", "42")

    asyncio.run(agent_tools.agent_start("gpt-6-luna", "do x", lane="x"))
    asyncio.run(agent_tools.agent_start("gpt-6-luna", "do y", lane=""))
    assert calls == [
        {"conversation_id": 42, "model": "gpt-6-luna", "brief": "do x",
         "effort": "auto", "name": "", "lane": "x"},
        {"conversation_id": 42, "model": "gpt-6-luna", "brief": "do y",
         "effort": "auto", "name": ""},
    ]


def test_list_instances_helper_reads_mcp_instances_and_handles_errors(monkeypatch):
    seen = []

    def fake_get(url, *, headers, timeout):
        seen.append((url, headers, timeout))
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {"instances": [{"hash": "deadbeef"}]},
        )

    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    monkeypatch.setattr(unity_mcp_manager, "mcp_port", 19001)
    monkeypatch.setattr(unity_mcp_manager, "api_headers", lambda: {"X-Test": "1"})
    monkeypatch.setattr(subagents.httpx, "get", fake_get)

    assert subagents._list_instances() == [{"hash": "deadbeef"}]
    assert seen == [("http://localhost:19001/api/instances", {"X-Test": "1"}, 3.0)]

    monkeypatch.setattr(subagents.httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert subagents._list_instances() == []


async def test_lane_passes_starting_running_counts_and_probe_to_unity_lanes(lanes_env):
    env, state = lanes_env
    parent = _main(env)

    gate_a = threading.Event()
    state.wait_gate = gate_a
    first = await _start_lane(env, parent, lane="lane-a")
    await _until(lambda: len(state.waited) == 1)

    # Start second lane while lane-a is still in state "starting".
    gate_b = threading.Event()
    state.wait_gate = gate_b
    second = await _start_lane(env, parent, lane="lane-b")
    await _until(lambda: len(state.waited) == 2)

    # Release lane-a so it transitions to "ready", holding its turn open.
    c_a = first["chat_id"]
    _Runner.scripts[c_a] = ["hold", ("response", {"content": "a done"}), DONE]
    gate_a.set()
    await _until(lambda: len(_Runner.of(c_a)) == 1)

    # Start third lane while lane-a is "ready" and lane-b is "starting".
    state.wait_gate = None
    await _start_lane(env, parent, lane="lane-c")
    await _until(lambda: len(state.waited) == 3)

    # started tuples: (record, free_bytes, running, starting)
    assert (state.started[0][2], state.started[0][3]) == (0, 0)
    assert (state.started[1][2], state.started[1][3]) == (0, 1)
    assert (state.started[2][2], state.started[2][3]) == (1, 1)

    for _, _, kw in state.waited:
        assert kw.get("timeout_s") == 900
        assert kw.get("probe") is subagents._probe_instance

    gate_b.set()
    _Runner.holds[c_a].set()
    await _lanes_idle()


def test_probe_instance_posts_read_console_and_handles_responses(monkeypatch):
    seen = []

    def make_post(status_code, payload):
        def fake_post(url, *, headers, json, timeout):
            seen.append((url, headers, json, timeout))
            return types.SimpleNamespace(status_code=status_code, json=lambda: payload)

        return fake_post

    from unity_ai_mcp.unity_mcp_manager import unity_mcp_manager
    monkeypatch.setattr(unity_mcp_manager, "mcp_port", 19002)
    monkeypatch.setattr(unity_mcp_manager, "api_headers", lambda: {"X-Probe": "yes"})

    inst = {"project": "BenchClone", "hash": "1234567890abcdef"}
    monkeypatch.setattr(subagents.httpx, "post", make_post(200, {"status": "success", "result": {}}))
    assert subagents._probe_instance(inst) is True
    assert seen == [(
        "http://localhost:19002/api/command",
        {"X-Probe": "yes"},
        {
            "type": "read_console",
            "params": {"action": "get", "count": 1},
            "unity_instance": "BenchClone@1234567890abcdef",
        },
        10.0,
    )]

    monkeypatch.setattr(subagents.httpx, "post", make_post(500, {"status": "error"}))
    assert subagents._probe_instance(inst) is False

    monkeypatch.setattr(subagents.httpx, "post", make_post(200, {"status": "error"}))
    assert subagents._probe_instance(inst) is False

    monkeypatch.setattr(subagents.httpx, "post", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert subagents._probe_instance(inst) is False


async def test_lane_merge_busy_unknown_merged_and_conflict(lanes_env, monkeypatch):
    env, state = lanes_env
    parent = _main(env)

    # unknown_lane -> 404
    with pytest.raises(HTTPException) as exc:
        await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="missing-lane")
    assert exc.value.status_code == 404

    # Start a lane and hold it while starting/running -> 409 lane_busy
    gate = threading.Event()
    state.wait_gate = gate
    started = await _start_lane(env, parent, lane="merge-a")
    child = started["chat_id"]
    await _until(lambda: len(state.waited) == 1)

    with pytest.raises(subagents.SubagentError) as serr:
        await subagents.merge_lane(parent, 1, "merge-a")
    assert (serr.value.status, serr.value.code) == (409, "lane_busy")
    with pytest.raises(HTTPException) as exc:
        await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="merge-a")
    assert exc.value.status_code == 409

    # Even if state is stopped, a child with status RUNNING must still refuse 409 lane_busy.
    _Runner.scripts[child] = ["hold", ("response", {"content": "done"}), DONE]
    gate.set()
    await _until(lambda: len(_Runner.of(child)) == 1)
    lane_key = (1, subagents._real_path(state.proj), "merge-a")
    with subagents._LOCK:
        subagents._LANES[lane_key].state = "stopped"
    with pytest.raises(subagents.SubagentError) as serr:
        await subagents.merge_lane(parent, 1, "merge-a")
    assert (serr.value.status, serr.value.code) == (409, "lane_busy")
    with subagents._LOCK:
        subagents._LANES[lane_key].state = "ready"

    _Runner.holds[child].set()
    await _lanes_idle()

    # Conflict keeps the registry entry and describes resolution.
    monkeypatch.setattr(
        unity_lanes, "merge_lane",
        lambda _p, _rec, **_kw: {"result": "conflict", "files": ["Assets/Main.unity"], "message": "conflict"},
    )
    conflict_res = await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="merge-a")
    assert conflict_res["result"] == "conflict"
    assert conflict_res["files"] == ["Assets/Main.unity"]
    assert "Assets/Main.unity" in conflict_res["text"]
    assert "aborted" in conflict_res["text"] and "nothing changed" in conflict_res["text"]
    assert lane_key in subagents._LANES

    # Merged drops registry key and _CHILD_LANE mapping, and ends with refresh_unity instruction.
    monkeypatch.setattr(
        unity_lanes, "merge_lane",
        lambda _p, _rec, **_kw: {"result": "merged", "files": ["Assets/Main.unity"], "message": "merged"},
    )
    merged_res = await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="merge-a")
    assert merged_res["result"] == "merged"
    assert merged_res["files"] == ["Assets/Main.unity"]
    assert merged_res["text"].endswith("Run refresh_unity in the user's editor so it picks up the merged files.")
    assert lane_key not in subagents._LANES
    assert child not in subagents._CHILD_LANE

    # Disk-only worktree fallback (e.g. after app restart when _LANES is empty).
    disk_dir = unity_lanes.lane_dir(state.proj, "disk-only")
    os.makedirs(disk_dir, exist_ok=True)
    with open(os.path.join(disk_dir, ".git"), "w", encoding="utf-8") as fh:
        fh.write("gitdir: ../.git/worktrees/disk-only\n")
    seen_records = []

    def fake_disk_merge(proj, rec, **_kw):
        seen_records.append((proj, rec.name, rec.dir, rec.branch))
        return {"result": "empty", "files": [], "message": "empty"}

    monkeypatch.setattr(unity_lanes, "merge_lane", fake_disk_merge)
    empty_res = await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="disk-only")
    assert empty_res["result"] == "empty"
    assert seen_records == [(state.proj, "disk-only", disk_dir, "gamachine/disk-only")]


async def test_lane_merge_reports_cleanup_error_when_lane_dir_not_removed(lanes_env, monkeypatch):
    env, state = lanes_env
    parent = _main(env)

    await _start_lane(env, parent, lane="cleanup-fail")
    await _lanes_idle()

    monkeypatch.setattr(
        unity_lanes, "merge_lane",
        lambda _p, _rec, **_kw: {
            "result": "merged",
            "files": ["Assets/Main.unity"],
            "cleanup_error": "Şerit silinemedi: dosya kilitli",
            "message": "merged with cleanup error",
        },
    )
    res = await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="cleanup-fail")
    assert res["result"] == "merged"
    assert res["files"] == ["Assets/Main.unity"]
    assert "could not be removed" in res["text"]
    assert "Şerit silinemedi: dosya kilitli" in res["text"]
    assert res["text"].endswith("Run refresh_unity in the user's editor so it picks up the merged files.")

    disk_dir = unity_lanes.lane_dir(state.proj, "empty-cleanup-fail")
    os.makedirs(disk_dir, exist_ok=True)
    with open(os.path.join(disk_dir, ".git"), "w", encoding="utf-8") as fh:
        fh.write("gitdir: ../.git/worktrees/empty-cleanup-fail\n")
    monkeypatch.setattr(
        unity_lanes, "merge_lane",
        lambda _p, _rec, **_kw: {
            "result": "empty",
            "files": [],
            "cleanup_error": "Şerit silinemedi: klasör kilitli",
            "message": "empty with cleanup error",
        },
    )
    empty_res = await _post(env, "/subagents/lane-merge", conversation_id=parent, lane="empty-cleanup-fail")
    assert empty_res["result"] == "empty"
    assert "no new commits" in empty_res["text"]
    assert "could not be removed" in empty_res["text"]
    assert "Şerit silinemedi: klasör kilitli" in empty_res["text"]
