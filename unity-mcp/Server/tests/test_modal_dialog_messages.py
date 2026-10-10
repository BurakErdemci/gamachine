"""Source checks are the only automated check available here without Unity."""

import re
from pathlib import Path


UNITY_ROOT = Path(__file__).resolve().parents[2] / "MCPForUnity"
EDITOR_ROOT = UNITY_ROOT / "Editor"
PROBE_PATH = EDITOR_ROOT / "Helpers" / "UnityModalDialogProbe.cs"
STEPPER_PATH = EDITOR_ROOT / "Tools" / "Playtest" / "PlaytestStepper.cs"
TRANSPORT_PATH = EDITOR_ROOT / "Services" / "Transport" / "Transports" / "WebSocketTransportClient.cs"
PROBE_CALL = "UnityModalDialogProbe.FindOpenDialogTitle()"


def test_probe_is_windows_only_and_finds_native_dialogs():
    source = PROBE_PATH.read_text(encoding="utf-8")
    assert "public static string FindOpenDialogTitle()" in source
    assert '"#32770"' in source
    assert "#if UNITY_EDITOR_WIN" in source
    for api in ("EnumWindows", "GetWindowThreadProcessId", "IsWindowVisible", "GetClassName", "GetWindowText"):
        assert api in source
    assert "CharSet.Unicode" in source


def test_stepper_checks_dialogs_on_start_and_tick():
    source = STEPPER_PATH.read_text(encoding="utf-8")
    assert "pass timeout_seconds" not in source
    start = source.split("public static Task<JObject> Start(", 1)[1].split("private static string ReadableError", 1)[0]
    tick = source.split("private static void Tick()", 1)[1].split("private static JObject CaptureEntry", 1)[0]
    assert PROBE_CALL in start
    assert PROBE_CALL in tick
    assert "SegmentStartedAtS" in tick
    assert "LastDialogCheckS" in tick
    assert "split very long runs into several play_step calls" in tick


def test_transport_checks_dialogs_in_command_timeout_branch():
    source = TRANSPORT_PATH.read_text(encoding="utf-8")
    execute = source.split("private async Task HandleExecuteAsync(", 1)[1]
    timeout = execute.split("catch (OperationCanceledException)", 1)[1].split("catch (Exception ex)", 1)[0]
    assert PROBE_CALL in timeout
    assert "may be blocking the editor; ask the user to close it." in timeout


def test_probe_meta_has_unique_guid():
    meta_path = PROBE_PATH.with_suffix(".cs.meta")
    meta = meta_path.read_text(encoding="utf-8")
    assert meta.startswith("fileFormatVersion: 2\n")
    match = re.search(r"^guid: ([0-9a-f]{32})$", meta, re.MULTILINE)
    assert match is not None
    guid = match.group(1)
    for other in UNITY_ROOT.rglob("*.meta"):
        if other != meta_path:
            assert guid not in other.read_text(encoding="utf-8"), f"Duplicate guid in {other}"
