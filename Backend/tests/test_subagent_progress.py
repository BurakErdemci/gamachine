"""A running helper's live progress (agentic/subagents.py note_* / progress),
its JSON in /subagents/list, its lines in agent_list and agent_result, and the
fuse's Continue. Scripted events and an injected clock; every runner is the
fake of test_subagents and `network_guard` fails a test on any connection."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentic import cards, subagents
from tests.test_subagents import (CODEX, DONE, _Runner, _ep, _idle, _main, _post, _results,  # noqa: F401
                                  _start, _until, env, network_guard)


def ev(kind, **data):
    return SimpleNamespace(type=kind, data=data)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(subagents, "_now", lambda: now[0])
    return now


def _child(env, status="running"):
    parent = _main(env)
    row = env.db.create_subagent(parent, 1, "yardımcı", "subscription", CODEX, "high", status)
    return parent, row


# ── the record, from a scripted event sequence ───────────────────────────────
def test_progress_follows_a_scripted_turn_and_freezes_at_its_end(env, clock):
    _, row = _child(env)
    conv = row["child_conv"]
    assert subagents.progress(conv) is None
    subagents.note_turn_start(conv)
    assert subagents.progress(conv)["phase"] == "thinking"
    clock[0] = 1002
    subagents.note_event(conv, ev("text", content="Önce dosyaya bakayım.\nBakıyorum"))
    p = subagents.progress(conv)
    assert (p["phase"], p["last_said"]) == ("writing", "Bakıyorum")
    clock[0] = 1003
    subagents.note_event(conv, ev("tool_call", tool="Read", tool_id="t1",
                                  arguments={"file_path": "src/a.py", "limit": 10}))
    clock[0] = 1005
    subagents.note_event(conv, ev("tool_result", tool="Read", tool_id="t1", success=True))
    clock[0] = 1006
    subagents.note_event(conv, ev("tool_call", tool="Bash", arguments={"command": "npm test\n--watch"}))
    clock[0] = 1016
    subagents.note_event(conv, ev("tool_result", tool="Bash", success=False, summary="1 failed"))
    clock[0] = 1017
    subagents.note_event(conv, ev("tool_call", tool="commandExecution",
                                  arguments={"command": "x" * 200}))
    clock[0] = 1019
    subagents.note_event(conv, ev("status", tokens=5))   # activity only
    clock[0] = 1020
    p = subagents.progress(conv)
    assert p == {
        "phase": "running_tool",
        "step": {"tool": "commandExecution", "target": "x" * 79 + "…", "seconds": 3},
        "steps_done": 2, "steps_failed": 1, "elapsed_s": 20, "last_said": "Bakıyorum",
        "idle_s": 1, "card": None,
        "last_steps": [{"tool": "Read", "target": "src/a.py", "ok": True, "seconds": 2},
                       {"tool": "Bash", "target": "npm test", "ok": False, "seconds": 10}]}
    subagents.note_turn_end(conv)
    clock[0] = 5000
    frozen = subagents.progress(conv)
    assert frozen["elapsed_s"] == 20 and frozen["idle_s"] == 1 and frozen["step"] is None
    subagents.note_event(conv, ev("tool_result", tool="commandExecution"))  # after the end: ignored
    assert subagents.progress(conv)["steps_done"] == 2
    # A new turn starts from nothing.
    subagents.note_turn_start(conv)
    fresh = subagents.progress(conv)
    assert (fresh["steps_done"], fresh["last_steps"], fresh["last_said"]) == (0, [], "")


def test_only_the_last_five_steps_are_kept_and_text_closes_steps_without_results(env, clock):
    _, row = _child(env)
    conv = row["child_conv"]
    subagents.note_turn_start(conv)
    for i in range(7):
        subagents.note_event(conv, ev("tool_call", tool="Grep", arguments={"pattern": f"p{i}"}))
        subagents.note_event(conv, ev("tool_result", tool="Grep", success=True))
    subagents.note_event(conv, ev("tool_call", tool="TodoWrite", summary=""))  # never gets a result
    subagents.note_event(conv, ev("text", content="Plan hazır.\n\n"))
    p = subagents.progress(conv)
    assert [s["target"] for s in p["last_steps"]] == ["p3", "p4", "p5", "p6", ""]
    assert (p["steps_done"], p["phase"], p["step"], p["last_said"]) == (8, "writing", None, "Plan hazır.")


@pytest.mark.parametrize("data,target", [
    ({"tool": "fileChange", "arguments": {"changes": '[{"path": "Assets/A.cs", "kind": "add"}]'}},
     "Assets/A.cs"),
    ({"tool": "mcpToolCall", "arguments": {"server": "unityai",
                                           "arguments": '{"script": "const a = 1;\\nreturn a"}'}},
     "const a = 1;"),
    ({"tool": "WebFetch", "arguments": {"url": "https://example.test/x", "prompt": "read"}},
     "https://example.test/x"),
    ({"tool": "shell", "arguments": {"command": ["git", "status"]}}, "git status"),
    ({"tool": "Glob", "summary": "🔍 **/*.cs\nmore"}, "🔍 **/*.cs"),
])
def test_the_step_target_is_one_line_from_the_provider_shapes(env, data, target):
    _, row = _child(env)
    conv = row["child_conv"]
    subagents.note_turn_start(conv)
    subagents.note_event(conv, ev("tool_call", **data))
    assert subagents.progress(conv)["step"]["target"] == target


def test_an_open_card_reads_as_waiting_approval_with_its_title_and_age(env):
    _, row = _child(env)
    conv = row["child_conv"]
    subagents.note_turn_start(conv)
    subagents.note_event(conv, ev("tool_call", tool="Bash", arguments={"command": "rm -rf build"}))
    cards.open_card("w-1", conversation_id=conv, kind="command", tool="Bash", summary="rm -rf build")
    cards.get("w-1").created_at -= 42
    p = subagents.progress(conv)
    assert p["phase"] == "waiting_approval"
    assert p["card"]["title"] == "Bash rm -rf build" and 42 <= p["card"]["waiting_s"] <= 44
    assert "now: waiting_approval, Bash rm -rf build (4" in subagents.progress_line(p)
    cards.reset()
    # An in-stream gate event alone also reads as waiting, until the next event.
    subagents.note_event(conv, ev("command_approval_needed", gate_id="g"))
    assert subagents.progress(conv)["phase"] == "waiting_approval"
    subagents.note_event(conv, ev("tool_result", tool="Bash", success=True))
    assert subagents.progress(conv)["phase"] == "thinking"


def test_waiting_input_with_no_turn_reads_queued_and_delete_clears_the_record(env, clock):
    _, row = _child(env)
    conv = row["child_conv"]
    subagents.note_turn_start(conv)
    subagents.note_event(conv, ev("text", content="ilk tur bitti"))
    subagents.note_turn_end(conv)
    env.db.add_turn_input(conv, "user", "sıradaki iş", "user")
    p = subagents.progress(conv)
    assert (p["phase"], p["step"], p["steps_done"], p["elapsed_s"], p["last_said"]) == (
        "queued", None, 0, 0, "ilk tur bitti")
    subagents.forget_progress(conv)
    assert subagents.progress(conv)["last_said"] == ""   # still queued, no record left
    env.db.claim_turn_inputs(conv)
    assert subagents.progress(conv) is None


# ── agent_list and agent_result text ─────────────────────────────────────────
def test_agent_list_line_and_the_90_s_idle_marker_with_an_injected_clock(env, clock):
    parent, row = _child(env)
    conv = row["child_conv"]
    subagents.note_turn_start(conv)
    clock[0] = 1010
    subagents.note_event(conv, ev("text", content="Testleri çalıştırıyorum."))
    subagents.note_event(conv, ev("tool_call", tool="Bash", arguments={"command": "pytest -q"}))
    clock[0] = 1099   # 89 s quiet
    line = subagents.list_text(parent).splitlines()[1]
    assert line == ('  now: running_tool, Bash pytest -q (89 s) · 0 steps, 0 failed · 1 min 39 s'
                    ' · last said: "Testleri çalıştırıyorum."')
    clock[0] = 1100   # 90 s quiet
    assert subagents.list_text(parent).splitlines()[1].endswith(" · no activity for 1 min")
    clock[0] = 1010 + 185
    assert subagents.list_text(parent).splitlines()[1].endswith(" · no activity for 3 min")
    # A helper that is not running gets no line.
    env.db.set_subagent_status(row["id"], "done", last_result="R")
    assert len(subagents.list_text(parent).splitlines()) == 1


def test_agent_result_of_a_running_helper_has_the_full_progress(env, clock):
    parent, row = _child(env)
    conv = row["child_conv"]
    subagents.note_turn_start(conv)
    for i, ok in enumerate((True, False, True)):
        subagents.note_event(conv, ev("tool_call", tool="Read", arguments={"path": f"f{i}.py"}))
        clock[0] += 2
        subagents.note_event(conv, ev("tool_result", tool="Read", success=ok))
    subagents.note_event(conv, ev("text", content="Neredeyse bitti"))
    got = subagents.result(parent, row["id"])
    assert got["progress"]["steps_done"] == 3 and got["progress"]["steps_failed"] == 1
    text = got["text"]
    for part in ("Now: writing", "Steps: 3 done, 1 failed; elapsed 6 s", 'Last said: "Neredeyse bitti"',
                 "- Read f1.py: failed (2 s)", "- Read f2.py: ok (2 s)",
                 "arrives in this chat by itself"):
        assert part in text, part


# ── through the router: a live helper, a failing feed ────────────────────────
async def test_list_rows_carry_progress_while_the_helper_runs(env):
    parent = _main(env)
    started = await _start(env, parent, name="koşucu")
    child = started["chat_id"]
    _Runner.scripts[child] = [
        ("text", {"content": "Bakıyorum."}),
        ("tool_call", {"tool": "Read", "arguments": {"file_path": "a.py"}}),
        ("tool_result", {"tool": "Read", "success": True, "summary": "ok"}),
        ("tool_call", {"tool": "Bash", "arguments": {"command": "pytest -q"}}),
        "hold", ("response", {"content": "bitti"}), DONE]
    listing = _ep(env.srouter, "/subagents/list")

    async def progress():
        rows = (await listing(conversation_id=parent, x_session_token=""))["agents"]
        return rows[0]["progress"]

    async def ran_two_steps():
        p = await progress()
        return p is not None and p["step"] is not None and p["step"]["tool"] == "Bash"

    loop = asyncio.get_running_loop()
    end = loop.time() + 5
    while not await ran_two_steps():
        assert loop.time() < end, "the helper never reached its second step"
        await asyncio.sleep(0.005)
    p = await progress()
    assert p["phase"] == "running_tool" and p["step"]["target"] == "pytest -q"
    assert p["steps_done"] == 1 and p["last_steps"][0]["target"] == "a.py"
    listed = await listing(conversation_id=parent, x_session_token="")
    assert "  now: running_tool, Bash pytest -q" in listed["text"]
    assert listed["fuse"] == {"tripped": False, "count": 0, "limit": subagents.FUSE_LIMIT,
                              "paused": False, "held": 0}
    _Runner.holds[child].set()
    await _idle(parent, child)
    assert await progress() is None   # done: no progress
    assert any(c.endswith("bitti") for c in _results(env.db, parent))


async def test_a_failing_progress_feed_never_breaks_the_turn_or_its_result(env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("progress broke")

    for name in ("note_turn_start", "note_event", "note_turn_end"):
        monkeypatch.setattr(subagents, name, boom)
    parent = _main(env)
    started = await _start(env, parent)
    _Runner.scripts[started["chat_id"]] = [
        ("tool_call", {"tool": "Read", "arguments": {"path": "a"}}),
        ("tool_result", {"tool": "Read", "success": True}),
        ("response", {"content": "RAPOR TAMAM"}), DONE]
    await _idle()
    (result,) = _results(env.db, parent)
    assert result.endswith("RAPOR TAMAM")
    assert env.db.get_subagent(started["agent_id"])["status"] == "done"


# ── the fuse's Continue ──────────────────────────────────────────────────────
async def _trip_the_fuse(env, parent, held_text="TUTULAN"):
    for i in range(subagents.FUSE_LIMIT):
        await _start(env, parent, name=f"a{i}")
        await _idle()
    held = await _start(env, parent, name="tutulan")
    _Runner.replies[held["chat_id"]] = held_text
    await _idle()
    assert len(_Runner.of(parent)) == subagents.FUSE_LIMIT


async def test_continue_releases_the_held_results_once_and_resets_the_fuse(env):
    parent = _main(env)
    fuse = _ep(env.srouter, "/subagents/fuse")
    await _trip_the_fuse(env, parent)
    assert await fuse(conversation_id=parent, x_session_token="") == {
        "tripped": True, "count": subagents.FUSE_LIMIT, "limit": subagents.FUSE_LIMIT,
        "paused": False, "held": 1}
    env.in_flight.discard(parent)   # the user's click: no turn of the chat runs
    done = await _post(env, "/subagents/continue", conversation_id=parent)
    assert (done["status"], done["released"]) == ("started", 1)
    await _idle()
    turns = _Runner.of(parent)
    assert len(turns) == subagents.FUSE_LIMIT + 1 and "TUTULAN" in turns[-1]
    assert turns[-1].count(subagents.RESULT_MARKER) == 1
    assert subagents.fuse_count(parent) == 0
    assert (await fuse(conversation_id=parent, x_session_token=""))["held"] == 0
    with pytest.raises(HTTPException) as exc:
        await _post(env, "/subagents/continue", conversation_id=parent)
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "nothing_held"
    # The fuse counts again from zero: the next result starts its own turn.
    env.in_flight.add(parent)
    await _start(env, parent, name="sonra")
    await _idle()
    assert len(_Runner.of(parent)) == subagents.FUSE_LIMIT + 2
    assert sum("TUTULAN" in t for t in _Runner.of(parent)) == 1


async def test_continue_refuses_with_nothing_held_and_for_a_foreign_chat(env):
    parent = _main(env)
    with pytest.raises(HTTPException) as exc:
        await _post(env, "/subagents/continue", conversation_id=parent)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await _post(env, "/subagents/continue", conversation_id=parent + 999)
    assert exc.value.status_code == 404


async def test_continue_after_a_stop_releases_the_paused_results(env):
    parent = _main(env)
    child = (await _start(env, parent))["chat_id"]
    _Runner.scripts[child] = ["hold", ("response", {"content": "DURDUKTAN SONRA"}), DONE]
    await asyncio.sleep(0.02)
    subagents.pause_results(parent)        # the user's Stop in the parent
    _Runner.holds[child].set()
    await _until(lambda: len(_results(env.db, parent)) == 1)
    await _idle()
    assert _Runner.of(parent) == [] and subagents.fuse_state(parent)["paused"] is True
    env.in_flight.discard(parent)   # the user's click comes from an idle chat
    done = await _post(env, "/subagents/continue", conversation_id=parent)
    assert done["released"] == 1
    await _idle()
    assert "DURDUKTAN SONRA" in _Runner.of(parent)[-1]
    assert subagents.fuse_state(parent)["paused"] is False


# ── what the main agent is told ──────────────────────────────────────────────
def test_tools_guide_and_note_say_check_when_deciding_and_never_poll():
    import agent_guide
    import harness_notes
    from unity_ai_mcp import agent_tools_server
    tools = {t.name: t for t in asyncio.run(agent_tools_server.create_server().list_tools())}
    listing = tools["agent_list"].description
    assert "what it is doing now" in listing and "90 s" in listing and "do not call this in a loop" in listing
    assert "live progress" in tools["agent_result"].description
    assert "check agent_list when you decide" in tools["agent_start"].description
    for line in (agent_guide.SUBAGENTS_LINE_EN, agent_guide.SUBAGENTS_LINE_TR):
        assert "agent_list" in line and "\n" not in line
    assert "never poll it in a loop" in agent_guide.SUBAGENTS_LINE_EN
    assert "döngüde yoklama yapma" in agent_guide.SUBAGENTS_LINE_TR
    note = harness_notes.read("subagents")["content"]
    for fact in ("waiting_approval", "no activity for N min", "Continue", "never poll it in a loop"):
        assert fact in note, fact


# ── turn 2: the desktop's Stop, Continue only from an idle chat ──────────────
async def test_the_desktops_stop_takes_string_ids_and_needs_no_running_parent_turn(env):
    parent = _main(env)
    started = await _start(env, parent)
    child = started["chat_id"]
    _Runner.scripts[child] = [("text", {"content": "çalışıyorum"}), "hang"]
    await asyncio.sleep(0.02)
    env.in_flight.discard(parent)   # the user clicks Stop in an idle chat
    stopped = await _post(env, "/subagents/stop", conversation_id=str(parent),
                          agent_id=str(started["agent_id"]))
    assert stopped["status"] == "stopped"
    await _idle(parent, child)
    assert env.db.get_subagent(started["agent_id"])["status"] == "stopped"
    for bad in ("abc", "", None, True, "1.5"):
        with pytest.raises(HTTPException) as exc:
            await _post(env, "/subagents/stop", conversation_id=parent, agent_id=bad)
        assert exc.value.status_code == 404, bad


async def test_continue_is_refused_while_the_chat_or_a_helper_runs_a_turn(env):
    parent = _main(env)
    await _trip_the_fuse(env, parent)
    # The agent's own tool call comes from a running turn of the chat.
    with pytest.raises(HTTPException) as exc:
        await _post(env, "/subagents/continue", conversation_id=parent)
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "turn_running"
    env.in_flight.discard(parent)
    # A helper still at work: refused too.
    busy = await _start_unchecked(env, parent)
    _Runner.scripts[busy] = ["hang"]
    await asyncio.sleep(0.02)
    with pytest.raises(HTTPException) as exc:
        await _post(env, "/subagents/continue", conversation_id=parent)
    assert exc.value.status_code == 409 and exc.value.detail["code"] == "turn_running"
    assert subagents.fuse_state(parent)["held"] == 1 and subagents.fuse_count(parent) == subagents.FUSE_LIMIT
    env.in_flight.add(parent)
    await _post(env, "/subagents/stop", conversation_id=parent,
                agent_id=env.db.get_subagent_by_child(busy)["id"])
    await _idle()
    env.in_flight.discard(parent)
    done = await _post(env, "/subagents/continue", conversation_id=parent)
    assert done["released"] == 1


async def _start_unchecked(env, parent):
    env.in_flight.add(parent)
    try:
        return (await _start(env, parent, name="meşgul"))["chat_id"]
    finally:
        env.in_flight.discard(parent)


@pytest.mark.parametrize("command, shown", [
    ('"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -Command "Set-Content hello.txt hi"',
     "Set-Content hello.txt hi"),
    ('pwsh -NoProfile -c "npm test"', "npm test"),
    ("cmd /c dir /b", "dir /b"),
    ("bash -lc 'pytest -q tests/x.py'", "pytest -q tests/x.py"),
    (["bash", "-lc", "ls -la"], "ls -la"),
    ("git status", "git status"),
])
def test_progress_target_shows_the_command_inside_a_shell_wrapper(command, shown):
    # Measured live on Codex (25 Nov 2026): the wrapper used up the whole 80-char target.
    assert subagents._target_of({"command": command}) == shown
