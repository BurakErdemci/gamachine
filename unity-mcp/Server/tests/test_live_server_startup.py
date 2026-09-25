"""The real server, started the way the product starts it, answers over HTTP.

WHICH FAILURE IT CAME FROM
    The FastMCP 4 spike (25 Sep 2026) crashed at startup on a removed private
    import while the suite stayed green: no test ever started the server. The
    probe (_live_server_probe.py, a child interpreter; see its docstring for
    why) runs src/main.py with the product's flags on a free port, lists tools
    on every profile URL and stops it again.

WHAT IT PINS
    * The server starts and stays up.
    * /mcp serves EXACTLY the list it served before tool profiles existed, in
      the same order (PINNED_DEFAULT_LIST, captured from cf53613 on FastMCP
      3.4.7 with no Unity Editor connected).
    * /mcp/gamachine serves exactly what the Gamachine backend exports.
    * /mcp/full serves every group; an unknown profile is 404; every path is
      401 without the shared secret.
"""

import json
import pathlib
import subprocess
import sys

import pytest

PROBE = pathlib.Path(__file__).resolve().parent / "_live_server_probe.py"
SERVER_DIR = PROBE.parent.parent

PROFILE_PATHS = ["/mcp", "/mcp/gamachine", "/mcp/full", "/mcp/not-a-profile"]

# tools/list on /mcp before profiles, no Unity connected, HTTP transport,
# --project-scoped-tools (the product's flags). Order is part of the contract:
# clients cache on it.
PINNED_DEFAULT_LIST = [
    "batch_execute", "debug_request_context", "execute_custom_tool",
    "execute_menu_item", "find_gameobjects", "find_in_file", "game_hooks",
    "manage_asset", "manage_build", "manage_camera", "manage_components",
    "manage_editor", "manage_gameobject", "manage_graphics", "manage_input",
    "manage_material", "manage_packages", "manage_physics", "manage_prefabs",
    "manage_scene", "refresh_unity", "apply_text_edits", "create_script",
    "delete_script", "validate_script", "manage_script",
    "manage_script_capabilities", "get_sha", "manage_tools", "play_capture",
    "play_session", "play_step", "read_console", "run_playtest",
    "script_apply_edits", "set_active_instance",
]

# Backend/app/tools/unity_mcp_tools.py: EXPORTED_GROUPS and _select_exported.
# Restated rather than imported: the backend package cannot be imported from
# the server's environment.
BACKEND_EXPORTED_GROUPS = {"core", "playtest"}


@pytest.fixture(scope="module")
def report():
    completed = subprocess.run(
        [sys.executable, str(PROBE), *PROFILE_PATHS],
        cwd=str(SERVER_DIR), capture_output=True, text=True, timeout=240,
    )
    assert completed.returncode == 0, (
        f"live probe failed (exit {completed.returncode}):\n{completed.stderr[-4000:]}"
    )
    data = json.loads(completed.stdout)
    assert data.get("healthy"), (
        "server never answered /health; it probably crashed at startup.\n"
        f"exit code: {data.get('exit_code')}\n{data.get('server_output_tail')}"
    )
    return data


def test_server_starts_and_stays_up(report):
    assert report["still_running"], report.get("server_output_tail")


def test_default_list_is_unchanged_in_names_and_order(report):
    listed = report["old_era"]["/mcp"]
    assert listed["status"] == 200, listed
    assert listed["names"] == PINNED_DEFAULT_LIST


def test_gamachine_profile_equals_the_backend_export(report):
    default = report["old_era"]["/mcp"]
    exported = [n for n in default["names"]
                if set(default["groups"][n]) & BACKEND_EXPORTED_GROUPS]
    gamachine = report["old_era"]["/mcp/gamachine"]
    assert gamachine["status"] == 200, gamachine
    assert gamachine["names"] == exported
    assert len(exported) == 31


def test_full_profile_lists_every_group(report):
    from_full = report["old_era"]["/mcp/full"]
    assert from_full["status"] == 200, from_full
    groups = {g for gs in from_full["groups"].values() for g in gs}
    assert groups == {"core", "docs", "vfx", "animation", "ui", "scripting_ext",
                      "testing", "probuilder", "profiling", "playtest"}
    assert set(PINNED_DEFAULT_LIST) <= set(from_full["names"])


def test_unknown_profile_is_404(report):
    assert report["old_era"]["/mcp/not-a-profile"]["status"] == 404


@pytest.mark.parametrize("path", PROFILE_PATHS)
def test_every_profile_path_needs_the_shared_secret(report, path):
    assert report["old_era_no_key"][path]["status"] == 401


def test_manage_tools_toggle_is_an_explicit_error_not_a_false_success(report):
    call = report["old_era_calls"]["mcp_activate"]
    assert call["status"] == 200, call
    assert '"success":false' in call["text"].replace(" ", ""), call
    assert "/mcp/full" in call["text"], call


def test_list_groups_names_the_profile_it_was_called_on(report):
    call = report["old_era_calls"]["full_list_groups"]
    assert call["status"] == 200 and not call["is_error"], call
    assert '"path":"/mcp/full"' in call["text"].replace(" ", ""), call


def test_a_tool_outside_the_profile_is_refused(report):
    """manage_tools is a meta-tool: not part of what the backend exports."""
    call = report["old_era_calls"]["gamachine_manage_tools"]
    assert call["is_error"] or call["error"], call
    assert "/mcp/gamachine" in (call["text"] + str(call["error"])), call


# ── With two (fake) Unity Editors connected ─────────────────────────────────

def test_fake_editors_connected(report):
    assert report.get("editors_connected"), report.get("server_output_tail")


def test_gamachine_still_equals_the_backend_export_once_unity_filters(report):
    """With an Editor connected /mcp narrows to what Unity registered; the
    backend filters THAT list, so the profile has to follow it too."""
    default = report["with_editors"]["/mcp"]
    exported = [n for n in default["names"]
                if set(default["groups"][n]) & BACKEND_EXPORTED_GROUPS]
    assert report["with_editors"]["/mcp/gamachine"]["names"] == exported
    assert "manage_asset" not in exported  # the fake Editors do not register it


def test_full_profile_does_not_follow_unity_registration(report):
    full = report["with_editors"]["/mcp/full"]["names"]
    assert "manage_asset" in full and "manage_vfx" in full


ROUTING_EXPECTED = {
    # label: editors the call must reach (and nothing else)
    "query_param": ["ProbeA"],
    "header": ["ProbeB"],
    "argument": ["ProbeA"],
    "argument_beats_query": ["ProbeB"],
    "profile_path_keeps_query": ["ProbeB"],
    "set_active_instance": [],
    "unrouted_other_client": [],
    "unknown_default": [],
}


@pytest.mark.parametrize("label", sorted(ROUTING_EXPECTED))
def test_each_call_reaches_only_the_editor_it_names(report, label):
    assert report["routing"][label]["reached"] == ROUTING_EXPECTED[label], report["routing"][label]


def test_set_active_instance_says_it_pinned_nothing(report):
    text = report["routing"]["set_active_instance"]["text"].replace(" ", "")
    assert '"success":false' in text and '"pinned":false' in text


def test_one_clients_set_active_instance_does_not_route_another_client(report):
    """The old pin was keyed "global" for every client without a client_id,
    so this call used to land on ProbeA. Two Editors, no selection: refuse."""
    call = report["routing"]["unrouted_other_client"]
    assert "Multiple Unity instances" in call["text"], call


def test_an_unknown_connection_default_is_an_error_not_a_fallback(report):
    call = report["routing"]["unknown_default"]
    assert call["is_error"], call
    assert "ffff0000" in call["text"], call


def test_a_connected_client_hears_tools_list_changed_when_unity_registers(report):
    """PluginHub announces Unity's tool set to clients already connected; this
    rode on a FastMCP monkeypatch that FastMCP 4 removed."""
    assert "notifications/tools/list_changed" in report["list_changed_methods"], report[
        "list_changed_methods"]
