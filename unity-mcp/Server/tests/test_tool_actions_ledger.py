"""
Keeps ``services/registry/tool_actions.json`` honest against the live tool surface.

The ledger tells the approval gate which calls mutate Unity. A ledger that
silently drifts from the code is worse than no ledger: the gate keeps answering
confidently with stale data. Every check here exists because drift in that
direction is invisible at runtime -- a tool added upstream simply stops being
classified, and nothing complains.

The dead-entry check (``test_no_dead_ledger_entries``) is not hypothetical. The
Backend gate exempts ``manage_scene`` action ``get_info``, an action that does
not exist and never has, so that exemption has never matched anything. This test
makes that class of mistake a red build.
"""
import ast
import inspect
import sys
import textwrap
import typing
from pathlib import Path

import pytest

from services.registry import get_registered_tools
from services.registry.tool_actions import (
    READ,
    WRITE,
    classify,
    ledger_tool_names,
    load_ledger,
    tool_entry,
    _action_list,
    _is_read_by_param,
    _self_check,
)
from utils.module_discovery import discover_modules

# Tools whose action list cannot be read out of the source automatically: the
# action parameter is free text and the module exposes no single ALL_ACTIONS
# constant. Frozen on purpose -- if a tool gains or loses a closed action set,
# this list stops matching and the test says so, instead of quietly checking
# fewer tools than it used to.
#
# Currently empty: every action-taking tool declares either a Literal or an
# enforced ALL_ACTIONS, so all 45 are cross-checked. Keeping the mechanism (and
# this comment) means the day one of them goes free-text, the test demands an
# explicit decision instead of silently skipping it.
UNVERIFIABLE_ACTION_SETS: set[str] = set()


# Every call the gate exempts from an approval card, pinned exactly.
#
# The rest of this file derives its expectations from the source: which tools
# exist, which actions they accept, which of them refresh the project. None of
# that can answer the one question that actually matters here -- whether a given
# action WRITES something in Unity. That answer lives in C# behaviour the Python
# side cannot inspect, and MCP annotations only describe whole tools, not
# actions. So for the read/write partition the ledger genuinely is the authority,
# and an audit red team demonstrated the cost on 2026-07-29: moving
# `manage_camera screenshot` from write_actions to read_actions exempted a call
# that writes a PNG to disk, and all 288 tests stayed green.
#
# This pin does NOT prove any row is correct -- nothing here can. It makes
# widening the exemption set LOUD: any addition, whether hand-written or slipped
# in while "cleaning up" the ledger, fails this test and forces a second,
# deliberate edit. Narrowing is loud too, which is the point -- 2026-07-29's
# preflight finding moved 30+ actions off this list, and that is exactly the
# kind of change that should never pass silently.
#
# Format: "tool:action", "tool:*" for a whole-tool read, and "tool:action?param"
# for a param_dependent rule (a read only while `param` is absent or falsy).
#
# To change it: state in the commit message which Unity behaviour justifies the
# new row, the way the ledger's own `evidence` field does.
PINNED_READ_SURFACE: frozenset[str] = frozenset({
    # get_compile_status reads SessionState and file mtimes only; no refresh.
    "compile_status:*",
    "debug_request_context:*",
    "execute_code:get_history",
    "find_in_file:*",
    "game_hooks:get",
    "game_hooks:list",
    "get_sha:*",
    "get_test_job:*",
    "manage_animation:animator_get_info",
    "manage_animation:animator_get_parameter",
    "manage_animation:clip_get_info",
    "manage_animation:controller_get_info",
    "manage_build:status",
    "manage_build:platform?target",
    "manage_build:settings?value",
    "manage_build:scenes?scenes",
    "manage_build:profiles?activate",
    "manage_camera:get_brain_status",
    "manage_camera:list_cameras",
    "manage_camera:ping",
    "manage_fbx:get_info",
    "manage_graphics:bake_get_settings",
    "manage_graphics:bake_status",
    "manage_graphics:feature_list",
    "manage_graphics:ping",
    "manage_graphics:pipeline_get_info",
    "manage_graphics:pipeline_get_settings",
    "manage_graphics:skybox_get",
    "manage_graphics:stats_get",
    "manage_graphics:stats_get_memory",
    "manage_graphics:stats_list_counters",
    "manage_graphics:volume_get_info",
    "manage_graphics:volume_list_effects",
    "manage_input:describe",
    "manage_material:get_material_info",
    "manage_material:ping",
    "manage_packages:get_package_info",
    "manage_packages:list_packages",
    "manage_packages:list_registries",
    "manage_packages:ping",
    "manage_packages:search_packages",
    "manage_packages:status",
    "manage_physics:get_collision_matrix",
    "manage_physics:get_rigidbody",
    "manage_physics:get_settings",
    "manage_physics:linecast",
    "manage_physics:overlap",
    "manage_physics:ping",
    "manage_physics:raycast",
    "manage_physics:raycast_all",
    "manage_physics:shapecast",
    "manage_physics:validate",
    "manage_probuilder:get_mesh_info",
    "manage_probuilder:ping",
    "manage_probuilder:select_faces",
    "manage_probuilder:validate_mesh",
    "manage_profiler:frame_debugger_get_events",
    "manage_profiler:get_counters",
    "manage_profiler:get_frame_timing",
    "manage_profiler:get_object_memory",
    "manage_profiler:memory_compare_snapshots",
    "manage_profiler:memory_list_snapshots",
    "manage_profiler:ping",
    "manage_profiler:profiler_status",
    "manage_script:read",
    "manage_script_capabilities:*",
    "manage_shader:read",
    "manage_sprite:get_info",
    "manage_tools:activate",
    "manage_tools:deactivate",
    "manage_tools:list_groups",
    "manage_tools:reset",
    "manage_tools:sync",
    "manage_ui:get_visual_tree",
    "manage_ui:list",
    "manage_ui:ping",
    "manage_ui:read",
    "manage_vfx:line_get_info",
    "manage_vfx:particle_get_info",
    "manage_vfx:ping",
    "manage_vfx:trail_get_info",
    "manage_vfx:vfx_get_info",
    "manage_vfx:vfx_list_assets",
    "manage_vfx:vfx_list_templates",
    "play_session:status",
    "read_console:get",
    # Owner decision 25 Sep 2026: status only reports the last playtest job.
    "run_playtest:status",
    "set_active_instance:*",
    "unity_docs:get_doc",
    "unity_docs:get_manual",
    "unity_docs:get_package_doc",
    "unity_docs:lookup",
    "unity_reflect:get_member",
    "unity_reflect:get_type",
    "unity_reflect:search",
    "validate_script:*",
})


@pytest.fixture(scope="module", autouse=True)
def _load_all_tools():
    """Import every tool module so the decorators populate the registry."""
    tools_dir = Path(__file__).parent.parent / "src" / "services" / "tools"
    list(discover_modules(tools_dir, "services.tools"))


def _registered() -> dict[str, dict]:
    return {tool["name"]: tool for tool in get_registered_tools()}


def _literal_members(annotation) -> set[str] | None:
    """
    Pull the members out of a Literal, however it is wrapped.

    Tools declare the action parameter in three different shapes across this
    codebase -- ``Annotated[Literal[...], "doc"]``,
    ``Optional[Annotated[Literal[...], "doc"]]`` and a named Literal alias --
    so the unwrapping has to survive Union and Annotated layers in any order.
    Handling only the first shape silently skipped two tools when this test was
    first written, which is the same "checks less than it appears to" failure
    the test exists to prevent.
    """
    seen = 0
    while seen < 8:
        seen += 1
        origin = typing.get_origin(annotation)
        if origin is typing.Literal:
            return {member for member in typing.get_args(annotation) if isinstance(member, str)}
        if origin is typing.Annotated or hasattr(annotation, "__metadata__"):
            annotation = typing.get_args(annotation)[0]
            continue
        if origin is typing.Union:
            candidates = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
            if len(candidates) != 1:
                return None
            annotation = candidates[0]
            continue
        return None
    return None


def _source_action_param(tool_info: dict) -> str | None:
    """
    The name of the action parameter according to the SOURCE, never the ledger.

    Asking the ledger which field carries the action makes the ledger its own
    oracle: erase `action_param`, set `tool_level: "read"`, and every check
    downstream agrees the row is fine while the gate stops classifying that
    tool's actions at all. Found by an audit red team, 2026-07-29 - the mutation
    left all three test files green and let `manage_camera action=screenshot`
    (which writes a PNG) through the live gate with no approval card.
    """
    try:
        params = inspect.signature(tool_info["func"]).parameters
    except (TypeError, ValueError):
        return None
    return "action" if "action" in params else None


def _source_parameter_names(tool_info: dict) -> set[str] | None:
    """Every parameter name the tool function actually declares, or None if unreadable."""
    try:
        return set(inspect.signature(tool_info["func"]).parameters)
    except (TypeError, ValueError):
        return None


def _closed_action_set(tool_name: str, tool_info: dict) -> set[str] | None:
    """
    The exhaustive action set declared by the source, or None if it is not
    machine-readable. Two shapes are supported: a Literal on the action
    parameter, and a module-level ALL_ACTIONS constant enforced at call time.
    """
    action_param = _source_action_param(tool_info)
    if not action_param:
        return None

    func = tool_info["func"]
    try:
        hints = typing.get_type_hints(func, include_extras=True)
    except Exception:
        hints = {}
    annotation = hints.get(action_param, inspect.Parameter.empty)

    literal_members = _literal_members(annotation)
    if literal_members is not None:
        return literal_members

    module = sys.modules.get(func.__module__)
    all_actions = getattr(module, "ALL_ACTIONS", None)
    if isinstance(all_actions, (list, tuple, set, frozenset)):
        return set(all_actions)
    return None


def _ledger_actions(tool_name: str) -> set[str]:
    entry = tool_entry(tool_name) or {}
    actions = set(entry.get("read_actions", [])) | set(entry.get("write_actions", []))
    actions |= {rule["action"] for rule in entry.get("param_dependent", [])}
    return actions


def test_ledger_is_self_consistent():
    assert _self_check() == []


@pytest.mark.parametrize("tool_name", sorted(ledger_tool_names()))
def test_action_param_matches_the_source_signature(tool_name):
    """
    The ledger may not decide for itself whether a tool takes an action.

    Without this, a row can erase `action_param` and declare `tool_level: read`;
    every other check then agrees, because every other check reads that same
    field. Measured by an audit red team on 2026-07-29: the erasure survived all
    three test files and the live gate allowed `manage_camera action=screenshot`
    - an action whose own ledger note records that it writes a PNG to disk -
    with no approval card.
    """
    tool_info = _registered().get(tool_name)
    if tool_info is None:
        pytest.skip("covered by test_no_dead_ledger_entries")

    entry = tool_entry(tool_name) or {}
    source_param = _source_action_param(tool_info)

    assert entry.get("action_param") == source_param, (
        f"{tool_name}: ledger says action_param={entry.get('action_param')!r} but the "
        f"source signature says {source_param!r}. A tool that takes an action must be "
        "classified per action; erasing the field silently exempts every action it has."
    )
    if source_param:
        assert entry.get("tool_level") is None, (
            f"{tool_name} takes an action parameter, so a blanket tool_level "
            f"({entry.get('tool_level')!r}) would classify all of its actions at once."
        )


@pytest.mark.parametrize("tool_name", sorted(ledger_tool_names()))
def test_param_dependent_rules_name_a_real_parameter(tool_name):
    """
    A `param_dependent` rule reads a SIBLING parameter to decide read vs write.
    That name must exist in the source, because a name that does not exist is
    always absent, and absent is the read side of every rule we have.

    So a single typo turns a gated action into an exempt one, silently and in
    the fail-OPEN direction - the opposite of everything else in the reader,
    which treats "cannot prove read" as write. `_is_read_by_param` already
    fails closed on an unknown `read_when`; the parameter name had no such
    guard. Reproduced by an audit red team on 2026-07-29: renaming
    manage_build.platform's `target` to `target_typo` made
    `platform target=android` classify as read - a call that runs
    SwitchActiveBuildTarget and reimports the project - with all suites green.

    The lowercase assertion closes the neighbouring door: the server normalises
    camelCase parameters before validation, but the gate classifies the RAW
    payload, so a rule keyed to a camelCase name would miss the snake_case call
    (and vice versa) and land on the read side by absence. Every parameter this
    project declares is already lowercase; keeping it that way keeps the two
    spellings from ever diverging.
    """
    entry = tool_entry(tool_name) or {}
    rules = entry.get("param_dependent", [])
    if not rules:
        pytest.skip("no param_dependent rules")

    tool_info = _registered().get(tool_name)
    if tool_info is None:
        pytest.skip("covered by test_no_dead_ledger_entries")

    declared = _source_parameter_names(tool_info)
    assert declared is not None, (
        f"{tool_name}: the signature is unreadable, so a param_dependent rule cannot "
        "be checked against it. Unclassifiable is not the same as correct."
    )

    for rule in rules:
        param = rule["param"]
        assert param in declared, (
            f"{tool_name}.{rule['action']}: the rule pivots on parameter {param!r}, which "
            f"the function does not declare. A missing parameter always reads as absent, "
            f"so this rule would classify every such call as read. Declared: "
            f"{sorted(declared)}"
        )
        assert param == param.lower(), (
            f"{tool_name}.{rule['action']}: parameter {param!r} is not lowercase. The gate "
            "classifies the payload before the server normalises camelCase, so a mixed-case "
            "rule name can miss the parameter entirely and fall through to read."
        )


def _exempted_read_surface() -> set[str]:
    """Every call the ledger currently exempts, in PINNED_READ_SURFACE's notation."""
    surface: set[str] = set()
    for tool_name in sorted(ledger_tool_names()):
        entry = tool_entry(tool_name) or {}
        if entry.get("tool_level") == READ:
            surface.add(f"{tool_name}:*")
        for action in entry.get("read_actions", []):
            surface.add(f"{tool_name}:{action}")
        for rule in entry.get("param_dependent", []):
            surface.add(f"{tool_name}:{rule['action']}?{rule['param']}")
    return surface


def test_the_exempted_read_surface_is_pinned():
    """The set of cardless calls may not change without someone saying so."""
    actual = _exempted_read_surface()
    added = sorted(actual - PINNED_READ_SURFACE)
    removed = sorted(PINNED_READ_SURFACE - actual)

    assert not added, (
        f"These calls became exempt from the approval gate without the pin being "
        f"updated: {added}. Source cannot tell whether an action writes in Unity, so "
        "this list is the only thing standing between a mistaken ledger row and a "
        "cardless mutation. If the widening is intended, add the rows here and say in "
        "the commit message which Unity behaviour justifies each one."
    )
    assert not removed, (
        f"These calls are no longer exempt: {removed}. Narrowing is usually correct - "
        "it means something was found to leave a trace - but it changes what the user "
        "sees on every turn, so it is not something to discover later from a diff. "
        "Update the pin in the same commit as the reclassification."
    )


def test_every_registered_tool_is_classified():
    missing = sorted(set(_registered()) - ledger_tool_names())
    assert not missing, (
        f"Tools reachable through MCP but absent from tool_actions.json: {missing}. "
        "They currently classify as 'write' (fail-closed), so the gate will card them, "
        "but the classification is a guess until it is written down."
    )


def test_no_dead_ledger_entries():
    stale = sorted(ledger_tool_names() - set(_registered()))
    assert not stale, (
        f"tool_actions.json classifies tools that are no longer registered: {stale}. "
        "A rule that can never match reads as protection while providing none."
    )


def _function_asks_for_refresh(tool_info: dict) -> bool:
    """
    Does THIS tool function ask preflight to refresh a dirty project?

    Per function, not per module. The first version of this check searched the
    module text and flagged `get_test_job`, which merely shares run_tests.py
    with a tool that does refresh - a false positive that would have forced a
    needless approval card on a pure job poll. Module-level granularity is the
    same mistake the ledger itself exists to avoid, one level up.
    """
    func = tool_info["func"]
    # The registered callable is wrapped by log_execution/telemetry in the live
    # server, so unwrap to reach the function whose source we care about.
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    try:
        src = textwrap.dedent(inspect.getsource(func))
        tree = ast.parse(src)
    except (OSError, TypeError, SyntaxError) as exc:
        # Fail CLOSED. The first version returned False here, and the caller
        # reads False as "this tool does not refresh" and returns without
        # checking the row at all - a tripwire that answers "all clear" exactly
        # when it has gone blind. Reproduced by an audit red team on
        # 2026-07-29: with inspect.getsource forced to raise for
        # find_gameobjects, a hand-widened `tool_level: read` row passed this
        # check unchallenged. An instrument that cannot see its subject must
        # say so, not vote yes.
        raise AssertionError(
            f"{tool_info.get('name', func)}: cannot read this tool's source to decide "
            f"whether it calls preflight(refresh_if_dirty=True) "
            f"({type(exc).__name__}: {exc}). Unclassifiable is not the same as 'no "
            "refresh' - fix the inspection before trusting this row."
        ) from exc
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        if name != "preflight":
            continue
        for kw in node.keywords:
            if kw.arg == "refresh_if_dirty" and isinstance(kw.value, ast.Constant) \
                    and kw.value.value is True:
                return True
    return False


@pytest.mark.parametrize("tool_name", sorted(ledger_tool_names()))
def test_preflight_refreshing_tools_have_no_read_actions(tool_name):
    """
    A tool that may refresh, import and recompile before doing its own work has
    no read actions, whatever its own semantics are.

    preflight(refresh_if_dirty=True) runs refresh_unity(mode=if_dirty,
    scope=all, compile=request) when the Editor reports external changes -
    asset import, script compilation, possibly a domain reload. It runs BEFORE
    the tool body, so a "read" action on such a tool is not a read.

    Found by an audit red team on 2026-07-29 and reproduced against the main
    tree: find_gameobjects was classified a whole-tool read while calling this,
    and manage_asset, manage_prefabs and manage_scene had read actions doing the
    same. The user's decision was that the call must keep working correctly and
    raise an approval card in step mode, so every action on these tools is a
    write. The cost is real and accepted: manage_scene get_hierarchy now cards.
    """
    tool_info = _registered().get(tool_name)
    if tool_info is None:
        pytest.skip("covered by test_no_dead_ledger_entries")

    if not _function_asks_for_refresh(tool_info):
        return

    entry = tool_entry(tool_name) or {}
    assert entry.get("tool_level") != READ, (
        f"{tool_name} calls preflight(refresh_if_dirty=True), so the whole tool "
        "cannot be a read."
    )
    assert not entry.get("read_actions"), (
        f"{tool_name} calls preflight(refresh_if_dirty=True). These actions are still "
        f"classified read and would pass with no approval card: "
        f"{entry.get('read_actions')}. Either they become writes, or the refresh goes."
    )
    assert not entry.get("param_dependent"), (
        f"{tool_name} has param_dependent rules with a read branch, but the preflight "
        "refresh makes every call on this tool a write regardless of its parameters."
    )
    assert entry.get("preflight_refresh") is True, (
        f"{tool_name} is classified write because of the preflight refresh, so its row "
        "must carry \"preflight_refresh\": true - otherwise the next reader sees an "
        "over-strict classification with no reason and relaxes it."
    )


def test_the_refresh_detector_fails_closed_when_it_cannot_read_source(monkeypatch):
    """
    Blinding the detector must break the tripwire, not satisfy it.

    Promoted from an audit probe (2026-07-29). The detector used to answer False
    on OSError/TypeError/SyntaxError, and its caller reads False as "this tool
    does not refresh" and returns without checking the row - so a hand-widened
    `find_gameobjects: tool_level: read` passed while the tool does in fact
    refresh. The probe that found it can no longer measure this (it asserted on
    a return value that no longer happens), which is why the assertion lives
    here instead.
    """
    tool_info = _registered().get("find_gameobjects")
    assert tool_info is not None, "find_gameobjects is the fixture this test needs"
    assert _function_asks_for_refresh(tool_info), (
        "find_gameobjects no longer calls preflight(refresh_if_dirty=True); pick another "
        "refreshing tool as the fixture rather than deleting this test."
    )

    def _blind(_func):
        raise OSError("simulated source-loader failure")

    monkeypatch.setattr(inspect, "getsource", _blind)
    with pytest.raises(AssertionError, match="cannot read this tool's source"):
        _function_asks_for_refresh(tool_info)


def _annotations(tool_info: dict) -> dict:
    """MCP ToolAnnotations as a plain dict, whatever object shape carries them."""
    ann = (tool_info.get("kwargs") or {}).get("annotations") or {}
    if isinstance(ann, dict):
        return ann
    return {
        key: getattr(ann, key, None)
        for key in ("readOnlyHint", "destructiveHint")
    }


@pytest.mark.parametrize("tool_name", sorted(ledger_tool_names()))
def test_tool_level_agrees_with_the_tools_own_annotations(tool_name):
    """
    A whole-tool classification must not contradict the tool's MCP annotations.

    This closes the second half of the self-referential-oracle class. Deriving
    `action_param` from the signature protects tools that HAVE actions; a tool
    with no action parameter has no action list to compare against, so flipping
    its `tool_level` from write to read was caught by nothing. Measured
    2026-07-29 while checking other forms of the class after the first fix:
    `refresh_unity` and `run_tests` both flipped to read with the whole suite
    still green - and both trigger asset import, compilation and domain reload.

    The annotations are the independent signal: they live on the decorator in
    the tool's own source, not in this ledger. 40 of 45 tools carry them.
    """
    tool_info = _registered().get(tool_name)
    if tool_info is None:
        pytest.skip("covered by test_no_dead_ledger_entries")

    entry = tool_entry(tool_name) or {}
    ann = _annotations(tool_info)
    tool_level = entry.get("tool_level")

    if ann.get("destructiveHint") is True:
        assert tool_level != READ, (
            f"{tool_name} declares destructiveHint=True but the ledger classifies the "
            "whole tool as read. Nothing else can catch this: with no action parameter "
            "there is no action list to compare against."
        )

    if ann.get("readOnlyHint") is True:
        assert tool_level != WRITE, (
            f"{tool_name} declares readOnlyHint=True but the ledger classifies the whole "
            "tool as write. One of the two is wrong; a needless card is cheap, but a "
            "contradiction left standing means neither source can be trusted next time."
        )
        assert not entry.get("write_actions"), (
            f"{tool_name} declares readOnlyHint=True yet the ledger lists write actions: "
            f"{entry.get('write_actions')}"
        )


@pytest.mark.parametrize("tool_name", sorted(ledger_tool_names()))
def test_declared_actions_match_source(tool_name):
    tool_info = _registered().get(tool_name)
    if tool_info is None:
        pytest.skip("covered by test_no_dead_ledger_entries")

    source_actions = _closed_action_set(tool_name, tool_info)
    if source_actions is None:
        assert tool_name in UNVERIFIABLE_ACTION_SETS or not (tool_entry(tool_name) or {}).get("action_param"), (
            f"{tool_name} declares an action_param but no closed action set could be read "
            "from the source, and it is not on the frozen UNVERIFIABLE_ACTION_SETS list."
        )
        return

    assert tool_name not in UNVERIFIABLE_ACTION_SETS, (
        f"{tool_name} now exposes a machine-readable action set; remove it from "
        "UNVERIFIABLE_ACTION_SETS so it gets checked."
    )

    ledger_actions = _ledger_actions(tool_name)
    unclassified = sorted(source_actions - ledger_actions)
    invented = sorted(ledger_actions - source_actions)
    assert not unclassified, (
        f"{tool_name}: actions exist in the source but are unclassified: {unclassified}"
    )
    assert not invented, (
        f"{tool_name}: ledger classifies actions the source does not accept: {invented}"
    )


def test_unknown_tool_is_treated_as_a_mutation():
    assert classify("mcp__something__brand_new", {"action": "get"}) == WRITE


def test_unknown_action_is_treated_as_a_mutation():
    assert classify("manage_scene", {"action": "teleport_everything"}) == WRITE


def test_missing_action_without_default_is_treated_as_a_mutation():
    assert classify("manage_scene", {}) == WRITE


def test_declared_default_action_is_applied():
    # read_console defaults to "get" when action is omitted (read_console.py:52).
    assert classify("read_console", {}) == READ
    assert classify("read_console", {"action": "clear"}) == WRITE


def test_param_dependent_action_flips_on_a_sibling_parameter():
    # manage_build settings: reading a PlayerSettings property vs writing one.
    # manage_build does NOT call preflight(refresh_if_dirty=True), so its read
    # branch survives; manage_scene's validate rule was retired when the whole
    # tool became a write.
    assert classify("manage_build", {"action": "settings", "property": "companyName"}) == READ
    assert classify("manage_build", {"action": "settings", "property": "companyName", "value": "x"}) == WRITE
    assert classify("manage_build", {"action": "scenes"}) == READ
    assert classify("manage_build", {"action": "scenes", "scenes": ["A.unity"]}) == WRITE


def test_a_param_dependent_pivot_is_seen_under_every_spelling():
    """
    camelCase must not walk past a param_dependent rule.

    Promoted from an external audit probe (2026-07-29). The server normalises
    camelCase to snake_case before FastMCP validates, but the approval gate
    classifies the RAW payload - so `autoRepair` was invisible here while the
    C# side read it happily, and an invisible pivot is the READ side of every
    rule we have. The audit's concrete case (`manage_scene validate
    autoRepair=true` -> ValidateScene(true)) is gone because manage_scene is
    now write throughout, which is exactly why this test uses a synthetic rule:
    the mechanism outlived its instance and would return with the next
    multi-word pivot parameter.
    """
    rule = {"action": "status", "param": "auto_repair", "read_when": "omitted"}
    assert _is_read_by_param(rule, {}) is True, "absent must still read"
    for spelling in ("auto_repair", "autoRepair", "AutoRepair"):
        assert _is_read_by_param(rule, {spelling: True}) is False, (
            f"{spelling} carries the pivot value, so the call is a write"
        )

    falsy_rule = {"action": "status", "param": "auto_repair", "read_when": "falsy"}
    assert _is_read_by_param(falsy_rule, {"autoRepair": False}) is True
    assert _is_read_by_param(falsy_rule, {"autoRepair": True}) is False


@pytest.mark.parametrize("tool_name, params", [
    ("game_hooks", {"action": "get", "action_": "call", "name": "level.restart"}),
    ("manage_script", {"action": "read", "action_": "delete", "name": "X", "path": "Assets"}),
    ("manage_script", {"action_": "delete", "action": "read", "name": "X", "path": "Assets"}),
    ("manage_script", {"action": "read", "Action": "delete", "name": "X", "path": "Assets"}),
    ("manage_script", {"action": "read", "a_ction": "delete", "name": "X", "path": "Assets"}),
    ("read_console", {"action": "get", "properties": {"action": "clear"}}),
    ("read_console", {"action": "get", "properties": '{"action": "clear"}'}),
    ("run_playtest", {"action": "status", "action_": "start"}),
    ("run_playtest", {"action_": "start", "action": "status"}),
    ("run_playtest", {"action": "status", "Action": "start"}),
    ("run_playtest", {"action": "status", "properties": {"action": "start"}}),
])
def test_colliding_action_keys_are_writes(tool_name, params):
    """
    Two keys that any layer can fold into `action` make the call a write.

    Promoted from an external audit probe (2026-09-25): C# BatchExecute renames
    `action_` to `action` and the later key overwrites the earlier, so
    `{action: "get", action_: "call"}` executed `call` while this classifier
    read `get`. manage_animation / manage_vfx additionally match `action`
    case-insensitively and flatten `properties` into the top level.
    """
    assert classify(tool_name, params) == WRITE
    batch = {"commands": [{"tool": tool_name, "params": params}]}
    assert classify("batch_execute", batch) == WRITE


def test_a_lone_renamed_action_key_must_be_a_read_under_every_reading():
    # Batch normalisation turns `action_` into `action`; a direct call ignores it
    # and falls back to the default. read_console: "clear" is a write either way
    # it is read, "get" is a read either way.
    assert classify("read_console", {"action_": "clear"}) == WRITE
    assert classify("read_console", {"action_": "get"}) == READ
    # manage_script has no default, so the ignored reading cannot be proven a read.
    assert classify("manage_script", {"action_": "read"}) == WRITE
    assert classify("manage_script", {"action": "read", "name": "X"}) == READ
    # run_playtest defaults to the write `start`, so a lone renamed `status`
    # is read by a direct call as start.
    assert classify("run_playtest", {"action_": "status"}) == WRITE
    assert classify("run_playtest", {"action": "status"}) == READ
    assert classify("run_playtest", {"action": "STATUS"}) == READ
    assert classify("run_playtest", {}) == WRITE
    assert classify("run_playtest", {"action": "start", "path": "Assets/Playtests"}) == WRITE


def test_a_param_dependent_pivot_must_be_absent_under_every_spelling():
    rule = {"action": "status", "param": "auto_repair", "read_when": "omitted"}
    assert _is_read_by_param(rule, {"auto_repair": None, "autoRepair": True}) is False
    falsy_rule = {"action": "status", "param": "auto_repair", "read_when": "falsy"}
    assert _is_read_by_param(falsy_rule, {"auto_repair": False, "autoRepair": True}) is False
    assert _is_read_by_param(falsy_rule, {"auto_repair": False, "autoRepair": 0}) is True


def test_a_malformed_action_list_cannot_become_a_wildcard():
    """
    `action in read_actions` is a substring test when read_actions is a string.

    Promoted from an external audit probe (2026-07-29): with
    `"read_actions": "list_packages"`, the actions `list`, `list_p` and even `s`
    all classified as read. The ledger is hand-edited data that grants
    exemptions from a security gate, so a wrong shape has to fail closed.
    """
    entry = {"read_actions": "list_packages", "write_actions": ["add"]}
    assert _action_list(entry, "read_actions") == ()
    assert _action_list(entry, "write_actions") == ("add",)
    assert _action_list({"read_actions": ["ok", 7, None]}, "read_actions") == ("ok",)


def test_batch_execute_is_a_write_when_any_inner_call_writes():
    # manage_scene is no longer usable as the "read" half of this test: the
    # preflight refresh made every one of its actions a write.
    reads_only = {"commands": [
        {"tool": "unity_reflect", "params": {"action": "search"}},
        {"tool": "read_console", "params": {"action": "get"}},
    ]}
    assert classify("batch_execute", reads_only) == READ

    one_write = {"commands": [
        {"tool": "unity_reflect", "params": {"action": "search"}},
        {"tool": "manage_gameobject", "params": {"action": "delete"}},
    ]}
    assert classify("batch_execute", one_write) == WRITE


def test_batch_execute_rejects_payloads_it_cannot_read():
    assert classify("batch_execute", {}) == WRITE
    assert classify("batch_execute", {"commands": []}) == WRITE
    assert classify("batch_execute", {"commands": "not-a-list"}) == WRITE
    assert classify("batch_execute", {"commands": [{"no_tool_key": 1}]}) == WRITE


def test_batch_execute_recursion_is_bounded():
    payload = {"tool": "manage_scene", "params": {"action": "get_hierarchy"}}
    for _ in range(12):
        payload = {"tool": "batch_execute", "params": {"commands": [payload]}}
    # Deeper than the recursion budget: refused rather than resolved.
    assert classify("batch_execute", payload["params"]) == WRITE


def test_execute_code_is_never_read_regardless_of_safety_checks():
    # safety_checks is model-settable and therefore not a gate input.
    assert classify("execute_code", {"action": "execute", "safety_checks": True}) == WRITE
    assert classify("execute_code", {"action": "execute", "safety_checks": False}) == WRITE
    assert classify("execute_code", {"action": "replay"}) == WRITE
    assert classify("execute_code", {"action": "get_history"}) == READ


def test_every_ledger_entry_cites_evidence():
    for name, entry in load_ledger()["tools"].items():
        assert entry.get("evidence"), f"{name} has no evidence reference"
