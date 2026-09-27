"""
``critical_actions`` in the ledger: which writes still raise a card in the
backend's "balanced" approval mode (owner decision, Burak, 27 Sep 2026).

``is_critical`` returning False lets a write run with no card, so these tests
pin the fail-closed side: unknown tools and actions, malformed rows, and
batch payloads a literal key read would misjudge are all critical.
"""
import copy

import pytest

from services.registry import tool_actions
from services.registry.tool_actions import _self_check, is_critical, load_ledger, tool_entry


@pytest.mark.parametrize("tool,params", [
    ("manage_gameobject", {"action": "delete"}),
    ("manage_gameobject", {"action": "DELETE"}),
    ("manage_asset", {"action": "move"}),
    ("manage_asset", {"action": "rename"}),
    ("manage_packages", {"action": "remove_package"}),
    ("execute_code", {"action": "execute"}),
    ("execute_code", {"action": "clear_history"}),
    ("execute_menu_item", {"menu_path": "File/Save Project"}),
    ("execute_custom_tool", {"tool_name": "x"}),
    ("game_hooks", {"action": "call"}),
    ("manage_build", {"action": "build"}),
    ("manage_build", {"action": "platform", "target": "Android"}),
    ("manage_build", {"action": "profiles", "activate": True}),
    ("manage_editor", {"action": "remove_tag"}),
    ("manage_editor", {"action": "deploy_package"}),
    ("delete_script", {"uri": "Assets/X.cs"}),
    ("manage_script", {"action": "delete"}),
    ("manage_shader", {"action": "delete"}),
    ("manage_texture", {"action": "delete"}),
    ("manage_ui", {"action": "delete"}),
    ("manage_scene", {"action": "load"}),
    ("manage_scene", {"action": "close_scene"}),
    ("manage_prefabs", {"action": "close_prefab_stage"}),
    ("manage_graphics", {"action": "pipeline_set_quality"}),
    ("manage_physics", {"action": "set_settings"}),
    ("manage_physics", {"action": "set_collision_matrix"}),
    # Fail closed.
    ("no_such_tool", {}),
    ("manage_components", {"action": "no_such_action"}),
    ("manage_gameobject", {}),
    ("manage_gameobject", {"action": 7}),
    ("manage_gameobject", {"action": "create", "Action": "delete"}),
    ("manage_gameobject", "not a mapping"),
])
def test_critical_calls(tool, params):
    assert is_critical(tool, params) is True


@pytest.mark.parametrize("tool,params", [
    ("manage_gameobject", {"action": "create"}),
    ("manage_gameobject", {"action": "modify"}),
    ("manage_components", {"action": "remove"}),
    ("manage_components", {"action": "add"}),
    ("manage_asset", {"action": "create"}),
    ("manage_scene", {"action": "save"}),
    ("manage_build", {"action": "platform"}),
    ("manage_build", {"action": "status"}),
    ("manage_editor", {"action": "add_tag"}),
    ("create_script", {"path": "Assets/X.cs"}),
    ("read_console", {}),
    ("run_tests", {}),
])
def test_routine_calls(tool, params):
    assert is_critical(tool, params) is False


def test_batch_is_critical_when_any_sub_call_is():
    add = {"tool": "manage_components", "params": {"action": "add"}}
    delete = {"tool": "manage_gameobject", "params": {"action": "delete"}}
    assert is_critical("batch_execute", {"commands": [add, add]}) is False
    assert is_critical("batch_execute", {"commands": [add, delete]}) is True
    assert is_critical("batch_execute", {"commands": [add, {"tool": "batch_execute",
                                                            "params": {"commands": [delete]}}]}) is True


@pytest.mark.parametrize("params", [
    {},
    {"commands": []},
    {"commands": "x"},
    {"commands": [7]},
    {"commands": [{"params": {"action": "add"}}]},
    {"commands": [{"tool": "manage_components", "params": "x"}]},
    # Spellings a C# normaliser may read instead of the literal keys.
    {"commands": [{"tool": "manage_components", "params": {"action": "add"}}],
     "Commands": [{"tool": "manage_gameobject", "params": {"action": "delete"}}]},
    {"Commands": [{"Tool": "manage_gameobject", "Params": {"action": "delete"}}]},
    {"commands": [{"tool": "manage_components", "Tool": "manage_gameobject",
                   "params": {"action": "delete"}}]},
    {"commands": [{"tool": "manage_components", "params": {"action": "add"},
                   "Params": {"action": "remove"}}]},
])
def test_batch_payloads_that_prove_nothing_are_critical(params):
    assert is_critical("batch_execute", params) is True


def test_a_too_deep_batch_is_critical():
    inner = {"tool": "manage_components", "params": {"action": "add"}}
    for _ in range(tool_actions._MAX_DEPTH + 1):
        inner = {"tool": "batch_execute", "params": {"commands": [inner]}}
    assert is_critical("batch_execute", {"commands": [inner]}) is True


def test_every_critical_action_is_a_write_in_the_real_ledger():
    assert _self_check() == []
    for name, entry in load_ledger()["tools"].items():
        marks = entry.get("critical_actions")
        if marks in (None, "*"):
            continue
        for action in marks:
            # A critical action must be one the gate asks about at all.
            params = {entry["action_param"]: action}
            assert tool_actions.classify(name, params) == "write" or any(
                rule.get("action") == action for rule in entry.get("param_dependent", [])), (name, action)


def _patched_ledger(monkeypatch, tool, **changes):
    ledger = copy.deepcopy(load_ledger())
    ledger["tools"][tool].update(changes)
    monkeypatch.setattr(tool_actions, "_ledger_cache", ledger)
    monkeypatch.setattr(tool_actions, "load_ledger", lambda refresh=False: ledger)
    return ledger


@pytest.mark.parametrize("changes,problem", [
    ({"critical_actions": ["create_but_not_real"]}, "is not a write action"),
    ({"critical_actions": "delete"}, "must be '*' or a list"),
    ({"critical_actions": [7]}, "must be '*' or a list"),
])
def test_self_check_rejects_a_malformed_critical_field(monkeypatch, changes, problem):
    _patched_ledger(monkeypatch, "manage_gameobject", **changes)
    assert any(problem in p for p in _self_check()), _self_check()


def test_self_check_rejects_critical_on_a_read_tool_and_on_the_batch_tool(monkeypatch):
    ledger = copy.deepcopy(load_ledger())
    ledger["tools"]["read_console"]["critical_actions"] = ["get"]
    ledger["tools"]["find_in_file"]["critical_actions"] = "*"
    ledger["tools"]["batch_execute"]["critical_actions"] = "*"
    monkeypatch.setattr(tool_actions, "load_ledger", lambda refresh=False: ledger)
    problems = _self_check()
    assert any("read_console" in p and "not a write action" in p for p in problems), problems
    assert any("find_in_file" in p and "no write" in p for p in problems), problems
    assert any("batch_execute" in p and "nested-call" in p for p in problems), problems


def test_a_malformed_critical_field_fails_closed_at_runtime(monkeypatch):
    _patched_ledger(monkeypatch, "manage_components", critical_actions="remove")
    assert tool_entry("manage_components")["critical_actions"] == "remove"
    assert is_critical("manage_components", {"action": "add"}) is True


# Codex safeauto, 27 Sep 2026: writes that were routine by name but destroy or
# overwrite in one form. Each routine form sits next to its critical twin.
@pytest.mark.parametrize("tool,params,critical", [
    ("manage_graphics", {"action": "bake_clear"}, True),
    ("manage_graphics", {"action": "bake_start"}, False),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/P.prefab",
                        "delete_child": "ImportantChild"}, True),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/P.prefab",
                        "deleteChild": [{"name": "A"}]}, True),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/P.prefab",
                        "properties": {"delete_child": "A"}}, True),
    ("manage_prefabs", {"action": "modify_contents", "prefab_path": "Assets/P.prefab",
                        "delete_child": None, "position": [0, 1, 0]}, False),
    ("manage_prefabs", {"action": "create_from_gameobject", "target": "R",
                        "prefab_path": "Assets/Prefabs/Existing.prefab", "allow_overwrite": True}, True),
    ("manage_prefabs", {"action": "create_from_gameobject", "target": "R",
                        "prefab_path": "Assets/Prefabs/Existing.prefab", "AllowOverwrite": "TRUE"}, True),
    ("manage_prefabs", {"action": "create_from_gameobject", "target": "R",
                        "prefab_path": "Assets/Prefabs/Existing.prefab", "allow_overwrite": 1}, True),
    ("manage_prefabs", {"action": "create_from_gameobject", "target": "R",
                        "prefab_path": "Assets/Prefabs/Existing.prefab",
                        "allow_overwrite": False, "allowOverwrite": True}, True),
    ("manage_prefabs", {"action": "create_from_gameobject", "target": "R",
                        "prefab_path": "Assets/Prefabs/Existing.prefab", "allow_overwrite": False}, False),
    ("manage_prefabs", {"action": "create_from_gameobject", "target": "R",
                        "prefab_path": "Assets/Prefabs/Existing.prefab", "allow_overwrite": "false"}, False),
    ("manage_prefabs", {"action": "create_from_gameobject", "target": "R",
                        "prefab_path": "Assets/Prefabs/New.prefab"}, False),
    ("manage_scene", {"action": "save", "name": "Existing", "path": "Assets/Scenes"}, True),
    ("manage_scene", {"action": "SAVE", "Name": "Existing"}, True),
    ("manage_scene", {"action": "save", "path": "Assets/Scenes"}, False),
    ("manage_scene", {"action": "save"}, False),
    ("manage_scene", {"action": "create", "name": "NewScene", "path": "Assets/Scenes"}, True),
    ("manage_scene", {"action": "create", "name": "N", "template": "2d_basic"}, True),
])
def test_parameter_forms_that_destroy_or_overwrite(tool, params, critical):
    assert is_critical(tool, params) is critical


def test_batch_applies_the_parameter_rule_to_sub_calls():
    save = {"tool": "manage_scene", "params": {"action": "save"}}
    save_as = {"tool": "manage_scene", "params": {"action": "save", "name": "Existing"}}
    delete_child = {"tool": "manage_prefabs",
                    "params": {"action": "modify_contents", "delete_child": "A"}}
    assert is_critical("batch_execute", {"commands": [save, save]}) is False
    assert is_critical("batch_execute", {"commands": [save, save_as]}) is True
    assert is_critical("batch_execute", {"commands": [save, {"tool": "batch_execute",
                                                             "params": {"commands": [delete_child]}}]}) is True


@pytest.mark.parametrize("rules,problem", [
    ([{"action": "no_such_action", "param": "x", "when": "present"}], "is not a write action"),
    ([{"action": "close_prefab_stage", "param": "x", "when": "present"}], "already critical"),
    ([{"action": "modify_contents", "param": "", "when": "present"}], "no usable 'param'"),
    ([{"action": "modify_contents", "param": "x", "when": "equals"}], "unsupported when"),
    ([7], "not an object"),
    ({"action": "modify_contents"}, "must be a list"),
])
def test_self_check_rejects_a_malformed_critical_when(monkeypatch, rules, problem):
    _patched_ledger(monkeypatch, "manage_prefabs", critical_when=rules)
    assert any(problem in p for p in _self_check()), _self_check()


def test_self_check_rejects_critical_when_on_the_batch_tool(monkeypatch):
    _patched_ledger(monkeypatch, "batch_execute",
                    critical_when=[{"action": "x", "param": "y", "when": "present"}])
    assert any("batch_execute" in p and "nested-call" in p for p in _self_check())


@pytest.mark.parametrize("rules", [
    "modify_contents",
    [7],
    [{"action": "modify_contents", "param": "", "when": "present"}],
    [{"action": "modify_contents", "param": "delete_child", "when": "equals"}],
])
def test_a_malformed_critical_when_fails_closed_at_runtime(monkeypatch, rules):
    _patched_ledger(monkeypatch, "manage_prefabs", critical_when=rules)
    assert is_critical("manage_prefabs", {"action": "modify_contents", "position": [0, 0, 0]}) is True
