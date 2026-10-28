import asyncio
from unittest.mock import AsyncMock

import pytest

from unity_ai_mcp import unity_mcp_manager as module
from unity_ai_mcp.editor_log import bridge_compile_errors, editor_log_path
from unity_ai_mcp.mcp_identity import Occupant, ServerIdentity


MARKER = "[ScriptCompilation] Requested script compilation because: refresh"


def error(filename="KeyframeAnimBuilder.cs", code="CS0234", package="MCPForUnity"):
    return f"bundle/{package}/Editor/{filename}(319,53): error {code}: missing type"


def test_platform_paths(tmp_path):
    assert editor_log_path("win32", {"LOCALAPPDATA": "Local"}, str(tmp_path)).replace("\\", "/") == "Local/Unity/Editor/Editor.log"
    assert editor_log_path("win32", {}, str(tmp_path)) is None
    assert editor_log_path("darwin", {}, str(tmp_path)) == str(tmp_path / "Library" / "Logs" / "Unity" / "Editor.log")
    assert editor_log_path("linux", {}, str(tmp_path)) == str(tmp_path / ".config" / "unity3d" / "Editor.log")


def test_only_last_compile_deduplicated_limited_and_bridge_errors(tmp_path):
    log = tmp_path / "Editor.log"
    log.write_text("\n".join([
        MARKER, error("Old.cs"), MARKER, error(package="OtherPackage"),
        error(), error(), error("Second.cs", "CS0103", "com.coplaydev.unity-mcp"),
        error("Third.cs"),
    ]), encoding="utf-8")
    assert bridge_compile_errors(str(log), 2) == [
        "KeyframeAnimBuilder.cs(319,53): error CS0234: missing type",
        "Second.cs(319,53): error CS0103: missing type",
    ]
    assert len(bridge_compile_errors(str(log))) == 3
    assert bridge_compile_errors(str(log), 0) == []
    log.write_text(error() + "\n" + MARKER + "\ncompile successful", encoding="utf-8")
    assert bridge_compile_errors(str(log)) == []


def test_tail_windows_paths_and_invalid_utf8(tmp_path):
    log = tmp_path / "Editor.log"
    windows_error = error().replace("/", chr(92))
    log.write_bytes((error("OutsideTail.cs") + "\n").encode() + b"x" * (2 * 1024 * 1024) + b"\n" + windows_error.encode() + b"\xff")
    lines = bridge_compile_errors(str(log))
    assert len(lines) == 1
    assert lines[0].startswith("KeyframeAnimBuilder.cs(319,53): error CS0234:")


def test_missing_unreadable_and_previous_log_ignored(tmp_path):
    (tmp_path / "Editor-prev.log").write_text(error(), encoding="utf-8")
    assert bridge_compile_errors(str(tmp_path / "Editor.log")) == []
    assert bridge_compile_errors(str(tmp_path)) == []


@pytest.mark.parametrize("status", ["off", "blocked", "starting", "connected", "running"])
def test_status_reads_compile_errors_only_when_running(monkeypatch, status):
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "is_running", lambda: status not in {"off", "blocked"})
    identity = ServerIdentity.FOREIGN if status == "blocked" else ServerIdentity.NONE
    monkeypatch.setattr(manager, "port_occupant", lambda: Occupant(identity, "test"))
    monkeypatch.setattr(manager, "_blocked_reason", lambda _: "port blocked")
    monkeypatch.setattr(manager, "check_health", AsyncMock(return_value=status != "starting"))
    response = type("Response", (), {"status_code": 200, "json": lambda _: {"instances": [{"id": "unity"}] if status == "connected" else []}})()
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda: client)
    monkeypatch.setattr(module, "editor_log_path", lambda: "current-log")
    calls = []

    def diagnostic(path):
        calls.append(path)
        return ["File.cs(1,2): error CS0234: missing type"]

    monkeypatch.setattr(module, "bridge_compile_errors", diagnostic)
    result = asyncio.run(manager.get_status())
    assert result["status"] == status
    if status == "running":
        assert result["reason"] is None
        assert result["reason_code"] == "bridge_compile_failed"
        assert result["reason_lines"] == ["File.cs(1,2): error CS0234: missing type"]
        assert calls == ["current-log"]
    else:
        assert calls == []
        assert result["reason"] == ("port blocked" if status == "blocked" else None)


@pytest.mark.parametrize("raises", [False, True])
def test_running_without_diagnostic_keeps_reason_empty(monkeypatch, raises):
    manager = module.UnityMCPManager()
    monkeypatch.setattr(manager, "is_running", lambda: True)
    monkeypatch.setattr(manager, "check_health", AsyncMock(return_value=True))
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = type("Response", (), {"status_code": 200, "json": lambda _: {"instances": []}})()
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda: client)

    def diagnostic(_):
        if raises:
            raise OSError("unreadable")
        return []

    monkeypatch.setattr(module, "bridge_compile_errors", diagnostic)
    result = asyncio.run(manager.get_status())
    assert result == {"status": "running", "pid": None, "port": 8080,
                      "instances": [], "reason": None}


@pytest.mark.parametrize("path", [
    "Assets/User.cs", "Assets/MCPForUnityExtra/User.cs",
    "Assets/com.coplaydev.unity-mcp-extra/User.cs",
])
def test_message_and_partial_component_names_are_not_bridge_paths(tmp_path, path):
    log = tmp_path / "Editor.log"
    log.write_text(f"{path}(1,2): error CS0234: MCPForUnity and com.coplaydev.unity-mcp missing", encoding="utf-8")
    assert bridge_compile_errors(str(log)) == []


@pytest.mark.parametrize("source", ["Assets/User.cs", "Assets/User.js"])
@pytest.mark.parametrize("diagnostic", ["error CS0001", "warning CS0001"])
def test_nested_bridge_path_in_message_is_not_the_source(tmp_path, source, diagnostic):
    log = tmp_path / "Editor.log"
    log.write_text(f'{source}(1,2): {diagnostic}: see "Packages/MCPForUnity/B.cs(2,3): error CS0002: nested"',
                   encoding="utf-8")
    assert bridge_compile_errors(str(log)) == []


def test_indented_source_with_spaces_and_parentheses(tmp_path):
    log = tmp_path / "Editor.log"
    log.write_text("   C:/My Project (copy)/MCPForUnity/Editor/A.cs(1,2): error CS0001: failure",
                   encoding="utf-8")
    assert bridge_compile_errors(str(log)) == ["A.cs(1,2): error CS0001: failure"]
