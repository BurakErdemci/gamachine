"""Python side of the `playtest` tool group (P4 contract).

The C# commands are written against the same contract in parallel, so these tests
pin the exact command names, param keys and result keys the wrappers use. A key
renamed on one side only would otherwise surface as a silent no-op in Unity.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp.server.server import ToolResult
from mcp.types import ImageContent, TextContent

import services.tools._playtest_common as common
from services.registry import DEFAULT_ENABLED_GROUPS, TOOL_GROUPS, get_registered_tools
from services.registry.tool_actions import READ, WRITE, classify
from services.tools.game_hooks import game_hooks
from services.tools.play_capture import play_capture
from services.tools.play_session import START_TIMEOUT_S, STOP_TIMEOUT_S, play_session
from services.tools.play_step import MAX_FRAMES, play_step, step_timeout_s
from services.tools.run_playtest import run_playtest
from utils.module_discovery import discover_modules

PLAYTEST_TOOLS = {"game_hooks", "play_session", "play_step", "play_capture", "run_playtest"}

JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0fakejpeg").decode()
PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfakepng").decode()


class FakeUnity:
    """Records every command and answers from a queue (last answer repeats)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[dict] = [{"success": True, "message": "ok", "data": {}}]

    async def send(self, send_fn, unity_instance, command, params):
        self.calls.append((command, dict(params)))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


@pytest.fixture
def unity(monkeypatch):
    fake = FakeUnity()
    monkeypatch.setattr(common, "send_with_unity_instance", fake.send)
    monkeypatch.setattr(common, "get_unity_instance_from_context", AsyncMock(return_value="inst"))

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(common, "sleep", no_sleep)
    return fake


def run(coro):
    return asyncio.run(coro)


CTX = SimpleNamespace()


# ---------------------------------------------------------------------------
# Group, default visibility, classification
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def registered():
    tools_dir = Path(__file__).parent.parent / "src" / "services" / "tools"
    list(discover_modules(tools_dir, "services.tools"))
    return {t["name"]: t for t in get_registered_tools()}


def test_every_playtest_tool_carries_the_group_tag(registered):
    for name in PLAYTEST_TOOLS:
        assert registered[name]["group"] == "playtest", name
        assert "group:playtest" in registered[name]["kwargs"]["tags"], name


def test_playtest_group_is_enabled_by_default_next_to_core():
    assert "playtest" in TOOL_GROUPS
    assert DEFAULT_ENABLED_GROUPS == {"core", "playtest"}


class _RecordingMCP:
    """Stands in for FastMCP: tests/integration/conftest.py stubs the real one for the whole session."""

    def __init__(self):
        self.disabled_tags: set[str] = set()

    def tool(self, **_kwargs):
        return lambda fn: fn

    def disable(self, *, tags, components):
        assert components == {"tool"}
        self.disabled_tags |= set(tags)


def test_http_startup_leaves_playtest_visible(monkeypatch):
    """register_all_tools disables every non-default group in HTTP mode; playtest must survive."""
    from core.config import config
    import services.registry.tool_registry as registry_module
    from services.tools import register_all_tools

    tools_dir = Path(__file__).parent.parent / "src" / "services" / "tools"
    list(discover_modules(tools_dir, "services.tools"))
    # register_all_tools swaps each registry entry's func for the wrapped one in place;
    # other tests inspect the raw function signatures, so put them back.
    saved = [(entry, entry["func"]) for entry in registry_module._tool_registry]
    monkeypatch.setattr(config, "transport_mode", "http")
    mcp = _RecordingMCP()
    try:
        register_all_tools(mcp)
    finally:
        for entry, func in saved:
            entry["func"] = func

    assert "group:playtest" not in mcp.disabled_tags
    assert "group:core" not in mcp.disabled_tags
    assert "group:vfx" in mcp.disabled_tags, "non-default groups must still start hidden"


@pytest.mark.parametrize(
    "tool, params, expected",
    [
        ("game_hooks", {"action": "list"}, READ),
        ("game_hooks", {"action": "get", "names": ["a"]}, READ),
        ("game_hooks", {"action": "call", "name": "level.restart"}, WRITE),
        ("play_session", {"action": "status"}, READ),
        ("play_session", {"action": "start"}, WRITE),
        ("play_session", {"action": "stop"}, WRITE),
        ("play_session", {}, WRITE),
        ("play_step", {"frames": 1}, WRITE),
        ("play_capture", {}, WRITE),
        ("run_playtest", {}, WRITE),
        ("run_playtest", {"action": "start"}, WRITE),
        ("run_playtest", {"action": "status"}, WRITE),
    ],
)
def test_classification(tool, params, expected):
    assert classify(tool, params) == expected


# ---------------------------------------------------------------------------
# game_hooks
# ---------------------------------------------------------------------------

def test_game_hooks_list_sends_exact_command(unity):
    unity.responses = [{"success": True, "data": {"hooks": [{"name": "p", "kind": "state", "type": "Vector3"}]}}]
    result = run(game_hooks(CTX, action="list"))
    assert unity.calls == [("game_hooks", {"action": "list"})]
    assert result["data"]["hooks"][0]["kind"] == "state"


def test_game_hooks_get_sends_names(unity):
    run(game_hooks(CTX, action="get", names=["player.position", " coins.collected "]))
    assert unity.calls == [("game_hooks", {"action": "get", "names": ["player.position", "coins.collected"]})]


def test_game_hooks_get_without_names_asks_for_all(unity):
    run(game_hooks(CTX, action="get"))
    assert unity.calls == [("game_hooks", {"action": "get"})]


def test_game_hooks_call_sends_name_and_args(unity):
    run(game_hooks(CTX, action="call", name="level.restart", args={"seed": 7}))
    assert unity.calls == [("game_hooks", {"action": "call", "name": "level.restart", "args": {"seed": 7}})]


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"action": "call"}, "requires 'name'"),
        ({"action": "call", "name": "  "}, "requires 'name'"),
        ({"action": "call", "name": "x", "args": [1]}, "args must be an object"),
        ({"action": "get", "names": [""]}, "names must be a list"),
        ({"action": "teleport"}, "Unknown action"),
    ],
)
def test_game_hooks_rejects_bad_params_before_unity(unity, kwargs, fragment):
    result = run(game_hooks(CTX, **kwargs))
    assert result["success"] is False and fragment in result["error"]
    assert unity.calls == []


# ---------------------------------------------------------------------------
# play_session
# ---------------------------------------------------------------------------

def test_play_session_start_sends_contract_keys_and_timeout(unity):
    run(play_session(CTX, action="start", scene="Assets/Scenes/Loop.unity", seed=7, fixed_dt=0.02, paused=False))
    assert unity.calls == [("play_session", {
        "action": "start", "scene": "Assets/Scenes/Loop.unity", "seed": 7, "fixed_dt": 0.02,
        "paused": False, "timeout_seconds": START_TIMEOUT_S,
    })]


def test_play_session_start_defaults(unity):
    run(play_session(CTX, action="start"))
    command, params = unity.calls[0]
    assert command == "play_session"
    assert params == {"action": "start", "seed": 0, "fixed_dt": 1 / 60, "paused": True,
                      "timeout_seconds": START_TIMEOUT_S}


def test_play_session_status_and_stop(unity):
    run(play_session(CTX, action="status"))
    run(play_session(CTX, action="stop", seed=5))
    assert unity.calls == [
        ("play_session", {"action": "status"}),
        ("play_session", {"action": "stop"}),
    ]


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"scene": "Scenes/Loop.unity"}, "scene must be"),
        ({"scene": "Assets/Loop.prefab"}, "scene must be"),
        ({"scene": "Assets/../Loop.unity"}, "scene must be"),
        ({"fixed_dt": 0}, "fixed_dt"),
        ({"fixed_dt": -0.1}, "fixed_dt"),
        ({"fixed_dt": 2.0}, "fixed_dt"),
        ({"seed": 1.5}, "seed"),
    ],
)
def test_play_session_start_rejects_bad_params(unity, kwargs, fragment):
    result = run(play_session(CTX, action="start", **kwargs))
    assert result["success"] is False and fragment in result["error"]
    assert unity.calls == []


def test_play_session_start_polls_pending_through_a_reload(unity):
    """PendingResponse -> re-send with action=status; a retry hint mid-reload is not a failure."""
    final = {"success": True, "data": {"playing": True, "paused": True, "frame": 1, "time": 0.0167,
                                       "scene": "Assets/Scenes/Loop.unity", "session": True, "hooks": 6}}
    unity.responses = [
        {"success": True, "_mcp_status": "pending", "_mcp_poll_interval": 0.5, "message": "entering play"},
        {"success": False, "error": "Unity session not available; please retry", "hint": "retry"},
        {"success": True, "_mcp_status": "pending", "message": "waiting for first frame"},
        final,
    ]
    result = run(play_session(CTX, action="start", seed=3))
    assert result == {**final, "transport_retries": 1}
    assert [c[1]["action"] for c in unity.calls] == ["start", "status", "status", "status"]
    assert unity.calls[0][1]["seed"] == 3
    assert all(c[1] == {"action": "status"} for c in unity.calls[1:])


class FakeClock:
    """monotonic() that advances only when the poll loop sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch, unity):
    fake = FakeClock()
    monkeypatch.setattr(common, "monotonic", fake)
    monkeypatch.setattr(common, "sleep", fake.sleep)
    return fake


def test_play_session_start_reports_when_still_pending(unity, clock):
    unity.responses = [{"success": True, "_mcp_status": "pending", "message": "phase=reload"}]
    result = run(play_session(CTX, action="start"))
    assert result["success"] is False
    assert "still pending" in result["error"] and "phase=reload" in result["error"]
    assert clock.now >= START_TIMEOUT_S + common.POLL_SLACK_S
    assert [c[1]["action"] for c in unity.calls].count("start") == 1


def test_play_session_start_returns_a_phase_timeout_from_polling(unity):
    phase_error = {"success": False, "error": "play_session start timed out in phase first_frame",
                   "data": {"phase": "first_frame"}}
    unity.responses = [{"success": True, "_mcp_status": "pending"}, phase_error]
    assert run(play_session(CTX, action="start")) == phase_error
    assert [c[1]["action"] for c in unity.calls] == ["start", "status"]


def test_play_session_failure_is_returned_unchanged(unity):
    failure = {"success": False, "error": "Scene 'Assets/B.unity' has unsaved changes", "code": None}
    unity.responses = [failure]
    assert run(play_session(CTX, action="start", scene="Assets/A.unity")) == failure
    assert len(unity.calls) == 1


# ---------------------------------------------------------------------------
# play_step
# ---------------------------------------------------------------------------

def test_play_step_minimal_call(unity):
    run(play_step(CTX, frames=60))
    assert unity.calls == [("play_step", {"frames": 60, "timeout_seconds": step_timeout_s(60)})]


def test_play_step_full_call_passes_contract_keys(unity):
    inp = [
        {"frame": 0, "key": "space", "do": "tap"},
        {"frame": 10, "axis": "leftStick/x", "do": "press", "value": 1.0},
        {"frame": 20, "button": "mouse0", "do": "release"},
    ]
    until = {"hook": "level.won", "op": "==", "value": True}
    run(play_step(CTX, frames=120, input=inp, autopilot=True, watch=["player.position"],
                  until=until, capture=[0, 119]))
    command, params = unity.calls[0]
    assert command == "play_step"
    assert params == {"frames": 120, "input": inp, "autopilot": True, "watch": ["player.position"],
                      "until": until, "capture": [0, 119], "timeout_seconds": step_timeout_s(120)}


def test_play_step_timeout_stays_under_backend_call_budget():
    # Backend/app/tools/unity_mcp_tools.py CALL_TIMEOUT_S = 240
    assert step_timeout_s(MAX_FRAMES) < 240


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"frames": 0}, "frames must be"),
        ({"frames": 3601}, "frames must be"),
        ({"frames": 10, "input": [{"frame": 10, "key": "a", "do": "tap"}]}, "frame must be an integer in 0..9"),
        ({"frames": 10, "input": [{"frame": -1, "key": "a", "do": "tap"}]}, "frame must be"),
        ({"frames": 10, "input": [{"frame": 0, "do": "tap"}]}, "exactly one of key, button, axis"),
        ({"frames": 10, "input": [{"frame": 0, "key": "a", "button": "mouse0", "do": "tap"}]}, "exactly one"),
        ({"frames": 10, "input": [{"frame": 0, "key": "a", "do": "hold"}]}, "do must be one of"),
        ({"frames": 10, "input": [{"frame": 0, "key": "a"}]}, "do must be one of"),
        ({"frames": 10, "input": [{"frame": 0, "axis": "leftStick/x", "do": "press", "value": "hi"}]}, "value must be a number"),
        ({"frames": 10, "input": [{"frame": 0, "key": "a", "do": "tap", "action": "x"}]}, "unknown keys"),
        ({"frames": 10, "until": {"hook": "h", "op": "=>", "value": 1}}, "until.op"),
        ({"frames": 10, "until": {"op": "==", "value": 1}}, "until.hook"),
        ({"frames": 10, "until": {"hook": "h", "op": "=="}}, "until.value"),
        ({"frames": 10, "capture": "start"}, "capture must be"),
        ({"frames": 10, "capture": [10]}, "capture frames"),
        ({"frames": 10, "capture": []}, "capture frames"),
        ({"frames": 10, "watch": [""]}, "watch must be"),
    ],
)
def test_play_step_rejects_bad_params_before_unity(unity, kwargs, fragment):
    result = run(play_step(CTX, **kwargs))
    assert result["success"] is False and fragment in result["error"], result
    assert unity.calls == []


def test_play_step_without_image_returns_plain_dict(unity):
    data = {"frame": 60, "time": 1.0, "stopped_by": "frames", "state": {}, "errors": 0, "warnings": 0,
            "first_errors": [], "captures": []}
    unity.responses = [{"success": True, "message": "stepped", "data": data}]
    result = run(play_step(CTX, frames=60))
    assert result == {"success": True, "message": "stepped", "data": data}


def test_play_step_capture_end_returns_one_image_and_no_base64_in_text(unity):
    """Contract: play_step carries the capture fields under data.image, not flat in data."""
    image = {"frame": 60, "path": "Library/GamachineCaptures/a.png", "width": 640, "height": 360,
             "image_base64": JPEG_B64, "mime": "image/jpeg"}
    data = {"frame": 60, "time": 1.0, "stopped_by": "until", "state": {"level.won": True},
            "errors": 0, "warnings": 1, "first_errors": [],
            "captures": [{"frame": 60, "path": "Library/GamachineCaptures/a.png"}], "image": image}
    unity.responses = [{"success": True, "message": "stepped", "data": data}]
    result = run(play_step(CTX, frames=60, capture="end"))
    assert unity.calls[0][1]["capture"] == "end"
    payload = _assert_image_result(result, JPEG_B64, "image/jpeg", expect_keys={"stopped_by", "captures", "state"})
    assert payload["data"]["image"] == {k: v for k, v in image.items() if k != "image_base64"}


def test_play_step_ignores_flat_image_fields(unity):
    """Only data.image is an image for play_step; a stray flat key is left as data."""
    unity.responses = [{"success": True, "data": {"frame": 1, "image_base64": JPEG_B64}}]
    assert isinstance(run(play_step(CTX, frames=1)), dict)


# ---------------------------------------------------------------------------
# play_capture
# ---------------------------------------------------------------------------

def _assert_image_result(result, b64, mime, expect_keys=()):
    assert isinstance(result, ToolResult)
    texts = [b for b in result.content if isinstance(b, TextContent)]
    images = [b for b in result.content if isinstance(b, ImageContent)]
    assert len(images) == 1 and len(texts) == 1
    assert images[0].data == b64 and images[0].mimeType == mime
    assert b64 not in texts[0].text
    assert "image_base64" not in texts[0].text
    assert ", " not in texts[0].text and ": " not in texts[0].text, "text part must be compact JSON"
    payload = json.loads(texts[0].text)
    assert payload["success"] is True
    for key in expect_keys:
        assert key in payload["data"]
    return payload


def test_play_capture_defaults(unity):
    run(play_capture(CTX))
    assert unity.calls == [("play_capture", {"max_size": 640, "format": "jpeg"})]


def test_play_capture_passes_camera_and_format(unity):
    run(play_capture(CTX, max_size=1280, format="PNG", camera="Main Camera"))
    assert unity.calls == [("play_capture", {"max_size": 1280, "format": "png", "camera": "Main Camera"})]


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"max_size": 1281}, "max_size"),
        ({"max_size": 0}, "max_size"),
        ({"max_size": 15}, "max_size"),
        ({"format": "gif"}, "format"),
        ({"camera": " "}, "camera"),
    ],
)
def test_play_capture_rejects_bad_params(unity, kwargs, fragment):
    result = run(play_capture(CTX, **kwargs))
    assert result["success"] is False and fragment in result["error"]
    assert unity.calls == []


def test_play_capture_image_uses_mime_from_unity(unity):
    data = {"frame": 12, "path": "Library/GamachineCaptures/f12.png", "width": 1920, "height": 1080,
            "image_base64": PNG_B64, "mime": "image/png"}
    unity.responses = [{"success": True, "message": "captured", "data": data}]
    result = run(play_capture(CTX, format="png"))
    _assert_image_result(result, PNG_B64, "image/png", expect_keys={"frame", "path", "width", "height", "mime"})


def test_play_capture_without_mime_sniffs_the_bytes(unity):
    unity.responses = [{"success": True, "data": {"frame": 1, "image_base64": JPEG_B64}}]
    result = run(play_capture(CTX))
    _assert_image_result(result, JPEG_B64, "image/jpeg")


def test_play_capture_failure_is_plain_dict(unity):
    failure = {"success": False, "error": "play_capture needs play mode; call play_session start first."}
    unity.responses = [failure]
    assert run(play_capture(CTX)) == failure


# ---------------------------------------------------------------------------
# run_playtest
# ---------------------------------------------------------------------------

NO_JOB = {"success": False, "error": "No run_playtest job in this editor session."}


def _done(job_id, results=()):
    return {"success": True, "message": "done", "data": {
        "job_id": job_id, "status": "done", "done": len(results), "total": len(results),
        "passed": len(results), "failed": 0, "seconds": 1.0, "results": list(results)}}


def _running(job_id):
    return {"success": True, "_mcp_status": "pending", "_mcp_poll_interval": 1.0, "message": "running",
            "data": {"job_id": job_id, "status": "running", "done": 0, "total": 1,
                     "current": "Assets/Playtests/a.playtest.json", "phase": "steps", "elapsed_s": 1.0}}


def test_run_playtest_default_leaves_glob_to_unity(unity):
    unity.responses = [NO_JOB, _done("new")]
    run(run_playtest(CTX))
    assert unity.calls == [("run_playtest", {"action": "status"}), ("run_playtest", {"action": "start"})]


def test_run_playtest_path_and_seed_override(unity):
    unity.responses = [NO_JOB, _done("new")]
    run(run_playtest(CTX, path="Assets/Playtests/LoopLab/walk.playtest.json", seed_override=9))
    assert unity.calls[1] == ("run_playtest", {"action": "start",
                                               "path": "Assets/Playtests/LoopLab/walk.playtest.json",
                                               "seed_override": 9})


def test_run_playtest_accepts_a_folder_path(unity):
    """RunPlaytestTool: path may be a file or a folder (all *.playtest.json under it)."""
    unity.responses = [NO_JOB, _done("new")]
    run(run_playtest(CTX, path="Assets/Playtests/LoopLab"))
    assert unity.calls[1] == ("run_playtest", {"action": "start", "path": "Assets/Playtests/LoopLab"})


def test_run_playtest_glob(unity):
    unity.responses = [NO_JOB, _done("new")]
    run(run_playtest(CTX, glob="Assets/Playtests/LoopLab/*.playtest.json"))
    assert unity.calls[1][1] == {"action": "start", "glob": "Assets/Playtests/LoopLab/*.playtest.json"}


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"path": "a.playtest.json", "glob": "Assets/**"}, "either path or glob"),
        ({"path": "C:/x.playtest.json"}, "under Assets/"),
        ({"path": "Assets/../x.playtest.json"}, "under Assets/"),
        ({"glob": "../**/*.json"}, "under Assets/"),
        ({"seed_override": "x"}, "seed_override"),
        ({"action": "cancel"}, "Unknown action"),
    ],
)
def test_run_playtest_rejects_bad_params(unity, kwargs, fragment):
    result = run(run_playtest(CTX, **kwargs))
    assert result["success"] is False and fragment in result["error"]
    assert unity.calls == []


def test_run_playtest_polls_pending_and_returns_results(unity):
    results = [{"scenario": "walk-to-goal", "passed": True, "failed_expect": [], "frames": 480,
                "sim_time": 8.0, "state_end": {"level.won": True}, "state_hash": "ab12",
                "errors": 0, "warnings": 0, "first_errors": [], "perf": None, "captures": []}]
    unity.responses = [NO_JOB, _running("j1"), _done("j1", results)]
    result = run(run_playtest(CTX, glob="Assets/Playtests/**/*.playtest.json"))
    assert result["data"]["results"] == results
    assert "transport_retries" not in result
    assert unity.calls[2] == ("run_playtest", {"action": "status"})


def test_run_playtest_status_action_only_reads(unity):
    unity.responses = [_running("j1"), _done("j1")]
    result = run(run_playtest(CTX, action="status"))
    assert result["data"]["status"] == "done"
    assert [c[1] for c in unity.calls] == [{"action": "status"}, {"action": "status"}]


# ---------------------------------------------------------------------------
# Lost replies: status is retried, start/stop/run are never re-sent
# ---------------------------------------------------------------------------

LOST = {"success": False, "error": "Unity plugin session abc disconnected while awaiting command_result",
        "hint": "retry"}
STARTED = {"success": True, "message": "Play session started.", "data": {
    "playing": True, "paused": True, "frame": 3, "time": 0.05, "scene": "Assets/Scenes/Loop.unity",
    "session": True, "hooks": 7, "seed": 0, "fixed_dt": 0.0167, "restarted": True}}
IDLE = {"success": True, "message": "Play session status.", "data": {
    "playing": False, "paused": False, "frame": 0, "time": 0.0, "scene": "Assets/Scenes/Loop.unity",
    "session": False, "hooks": 7}}
STOPPED = {"success": True, "message": "Play session stopped.", "data": {**IDLE["data"], "restored_scene": None}}
PENDING = {"success": True, "_mcp_status": "pending", "_mcp_poll_interval": 0.5, "message": "phase reload",
           "data": {"op": "start", "phase": "reload", "elapsed_s": 2.0, "scene": ""}}


def _actions(unity):
    return [c[1].get("action") for c in unity.calls]


def test_lost_status_polls_are_retried_until_the_outcome(unity, clock):
    unity.responses = [PENDING, LOST, LOST, PENDING, STARTED]
    result = run(play_session(CTX, action="start"))
    assert result["success"] is True and result["data"]["restarted"] is True
    assert result["transport_retries"] == 2 and "initial_reply_lost" not in result
    assert _actions(unity) == ["start", "status", "status", "status", "status"]


def test_a_raised_transport_error_while_polling_is_retried(unity, clock, monkeypatch):
    answers = [PENDING, RuntimeError("socket closed"), STARTED]

    async def send(_fn, _inst, command, params):
        unity.calls.append((command, dict(params)))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(common, "send_with_unity_instance", send)
    result = run(play_session(CTX, action="start"))
    assert result["success"] is True and result["transport_retries"] == 1
    assert _actions(unity) == ["start", "status", "status"]


def test_lost_start_reply_is_not_resent_and_status_finds_the_outcome(unity, clock):
    unity.responses = [LOST, PENDING, STARTED]
    result = run(play_session(CTX, action="start"))
    assert result["success"] is True and result["initial_reply_lost"] is True
    assert _actions(unity) == ["start", "status", "status"]


def test_lost_start_reply_with_no_session_is_an_error(unity, clock):
    """The start never reached Unity (or its outcome reply was lost): status shows no session."""
    unity.responses = [LOST, IDLE]
    result = run(play_session(CTX, action="start"))
    assert result["success"] is False and "reply was lost" in result["error"]
    assert _actions(unity) == ["start", "status"]


def test_lost_outcome_poll_of_a_failed_start_is_an_error(unity, clock):
    """Unity hands the outcome over once; if that reply is lost the retry sees plain status."""
    unity.responses = [PENDING, LOST, IDLE]
    result = run(play_session(CTX, action="start"))
    assert result["success"] is False and "no session is running" in result["error"]
    assert result["transport_retries"] == 1


def test_real_unity_error_while_polling_is_final(unity, clock):
    failure = {"success": False, "error": "play_session start timed out after 60s in phase 'reload'",
               "data": {"op": "start", "phase": "reload"}}
    unity.responses = [PENDING, failure]
    assert run(play_session(CTX, action="start")) == failure
    assert _actions(unity) == ["start", "status"]


def test_lost_stop_reply_is_not_resent(unity, clock):
    unity.responses = [LOST, STOPPED]
    result = run(play_session(CTX, action="stop"))
    assert result["success"] is True and result["initial_reply_lost"] is True
    assert _actions(unity) == ["stop", "status"]


def test_lost_stop_reply_while_still_playing_is_an_error(unity, clock):
    unity.responses = [LOST, STARTED]
    result = run(play_session(CTX, action="stop"))
    assert result["success"] is False and "still in play mode" in result["error"]
    assert _actions(unity) == ["stop", "status"]


def test_transport_gap_gives_up_without_resending(unity, clock):
    unity.responses = [LOST]
    result = run(play_session(CTX, action="start"))
    assert result["success"] is False and "no reply from Unity" in result["error"]
    assert _actions(unity).count("start") == 1
    assert common.TRANSPORT_GAP_S <= clock.now < START_TIMEOUT_S + common.POLL_SLACK_S


def test_run_playtest_lost_start_reply_learns_the_new_job(unity, clock):
    unity.responses = [_done("old"), LOST, _running("new"), LOST, _done("new")]
    result = run(run_playtest(CTX, path="Assets/Playtests/LoopLab"))
    assert result["success"] is True and result["data"]["job_id"] == "new"
    assert result["initial_reply_lost"] is True and result["transport_retries"] == 1
    assert _actions(unity) == ["status", "start", "status", "status", "status"]


def test_run_playtest_lost_start_reply_with_only_the_old_job_is_an_error(unity, clock):
    unity.responses = [_done("old"), LOST, _done("old")]
    result = run(run_playtest(CTX))
    assert result["success"] is False and "no new job" in result["error"]
    assert _actions(unity) == ["status", "start", "status"]


def test_play_step_is_never_retried(unity, clock):
    unity.responses = [LOST]
    assert run(play_step(CTX, frames=10)) == LOST
    assert len(unity.calls) == 1
