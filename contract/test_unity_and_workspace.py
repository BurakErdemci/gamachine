"""Promises of a backend run with GAMACHINE_NO_UNITY_MCP=1: it leaves Unity and the project alone."""
import json

from client import USER_ID

DISABLED_DETAIL = "Unity MCP is disabled for this backend (GAMACHINE_NO_UNITY_MCP)."
STATUSES = {"off", "blocked", "starting", "running", "connected"}


def _unity_project(path):
    (path / "Assets").mkdir(parents=True)
    (path / "ProjectSettings").mkdir()
    autoconnect = path / "UserSettings" / "Gamachine" / "autoconnect.json"
    autoconnect.parent.mkdir(parents=True)
    # What a live Gamachine leaves in a project it is connected to.
    autoconnect.write_text(json.dumps({"autoStart": True, "revision": "live"}), encoding="utf-8")
    return path


def _snapshot(root):
    """Every path with its bytes (files) and mtime: a new, changed or removed entry shows."""
    return {p: (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns) for p in root.rglob("*")}


def test_unity_toggle_on_is_refused_while_unity_mcp_is_disabled(api):
    """POST /mcp/unity/toggle enabled=true answers 409 with the switch's reason and starts nothing."""
    response = api.post("/mcp/unity/toggle", json={"enabled": True})
    assert response.status_code == 409
    assert response.json()["detail"] == DISABLED_DETAIL
    assert api.ok("GET", "/mcp/unity/status")["status"] == "off"


def test_unity_toggle_off_answers_stopped(api):
    """POST /mcp/unity/toggle enabled=false answers {"status": "stopped"}."""
    assert api.ok("POST", "/mcp/unity/toggle", json={"enabled": False}) == {"status": "stopped"}


def test_unity_status_answers_a_valid_shape(api):
    """GET /mcp/unity/status answers a known status, an int port, an instance list, a pid and a reason."""
    status = api.ok("GET", "/mcp/unity/status")
    assert status["status"] in STATUSES
    assert isinstance(status["port"], int)
    assert isinstance(status["instances"], list)
    assert status["pid"] is None or isinstance(status["pid"], int)
    assert "reason" in status


def test_saving_a_workspace_leaves_the_project_folders_untouched(api, tmp_path):
    """POST /save-workspace succeeds and changes no file in the new or the previous project folder."""
    first, second = _unity_project(tmp_path / "First"), _unity_project(tmp_path / "Second")
    before = _snapshot(tmp_path)
    for project in (first, second):
        answer = api.ok("POST", "/save-workspace", json={"user_id": USER_ID, "path": str(project)})
        assert answer["status"] == "success"
    assert _snapshot(tmp_path) == before
    assert api.ok("GET", f"/last-workspace/{USER_ID}")["path"] == str(second)
    recent = {w["path"] for w in api.ok("GET", f"/recent-workspaces/{USER_ID}")["workspaces"]}
    assert {str(first), str(second)} <= recent
