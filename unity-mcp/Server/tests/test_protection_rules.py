"""The fixed .meta rule (services/protection_rules.py) and where it is wired.

No Unity MCP call may write, delete, move or rename a .meta file. It is refused
before the approval gate, so it holds in auto mode (the backend answering
{"approved": True, "automatic": True}) and step mode shows no card for it.
"""

import asyncio
import json
import pathlib
import subprocess
import sys

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from services.protection_rules import meta_refusal  # noqa: E402
from transport import approval_gate  # noqa: E402

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
PROBE = pathlib.Path(__file__).resolve().parent / "_meta_rule_probe.py"


def _batch(*calls):
    return {"commands": [{"tool": t, "params": p} for t, p in calls]}


REFUSED = [
    ("manage_asset", {"action": "delete", "path": "Assets/x.meta"}),
    ("manage_asset", {"action": "move", "path": "Assets/a.png", "destination": "Assets/a.png.meta"}),
    ("manage_asset", {"action": "rename", "path": "Assets/Foo.prefab.META"}),
    ("manage_asset", {"action": "create", "path": "Assets\\Sub\\x.meta"}),
    ("manage_asset", {"action": "delete", "path": "Assets/x.meta::$DATA"}),
    ("manage_asset", {"action": "delete", "path": "Assets/x.meta. "}),
    ("manage_asset", {"action": "create_folder", "path": "Assets/x.meta"}),
    # key-spelling variants a normaliser may turn into `path`
    ("manage_asset", {"action": "delete", "Path": "Assets/x.meta"}),
    ("manage_asset", {"action": "delete", "path_": "Assets/x.meta"}),
    ("manage_asset", {"action": "delete", "asset_path": "Assets/x.meta"}),
    # an ambiguous action cannot borrow get_info's exemption
    ("manage_asset", {"action": "get_info", "action_": "delete", "path": "Assets/x.meta"}),
    ("manage_asset", {"action": "get_info", "Action": "delete", "path": "Assets/x.meta"}),
    # nested / JSON-encoded parameters
    ("manage_material", {"action": "create", "properties": {"material_path": "Assets/m.mat.meta"}}),
    ("manage_material", {"action": "create", "properties": json.dumps({"path": "Assets/x.meta"})}),
    ("manage_prefabs", {"action": "create_from_gameobject", "prefab_path": "Assets/P.prefab.meta"}),
    ("manage_scene", {"action": "save", "path": "Assets/Scenes/Main.unity.meta"}),
    ("unknown_custom_tool", {"file": "Assets/x.meta"}),
    ("execute_custom_tool", {"tool_name": "x", "parameters": {"target_path": "Assets/x.meta"}}),
    # batch_execute, recursively and with spelling variants
    ("batch_execute", _batch(("manage_asset", {"action": "delete", "path": "Assets/x.meta"}))),
    ("batch_execute", {"Commands": [{"Tool": "manage_asset",
                                     "Params": {"action": "delete", "path": "Assets/x.meta"}}]}),
    ("batch_execute", {"commands_": [{"tool_": "manage_asset",
                                      "params_": {"action": "delete", "path": "Assets/x.meta"}}]}),
    ("batch_execute", _batch(("batch_execute", _batch(
        ("manage_asset", {"action": "move", "path": "Assets/a.meta", "destination": "Assets/b.meta"}))))),
    ("batch_execute", {"commands": json.dumps(
        [{"tool": "manage_asset", "params": {"action": "delete", "path": "Assets/x.meta"}}])}),
    ("batch_execute", {"commands": [{"tool": 7, "params": {"path": "Assets/x.meta"}}]}),
    ("batch_execute", {"commands": [{"tool": "manage_asset",
                                     "params": json.dumps({"action": "delete", "path": "Assets/x.meta"})}]}),
]


@pytest.mark.parametrize("tool,params", REFUSED)
def test_refused(tool, params):
    refusal = meta_refusal(tool, params)
    assert refusal is not None, (tool, params)
    assert "Unity owns .meta files" in refusal and "manage_asset" in refusal


ALLOWED = [
    ("manage_asset", {"action": "delete", "path": "Assets/Foo.prefab"}),
    ("manage_asset", {"action": "move", "path": "Assets/a.png", "destination": "Assets/b.png"}),
    ("manage_asset", {"action": "get_info", "path": "Assets/x.meta"}),
    ("manage_asset", {"action": "search", "path": "Assets/x.meta"}),
    ("manage_asset", {"action": "GET_INFO", "path": "Assets/x.meta"}),
    ("manage_material", {"action": "get_material_info", "material_path": "Assets/x.meta"}),
    ("find_in_file", {"uri": "Assets/x.meta", "pattern": "guid"}),
    ("manage_gameobject", {"action": "create", "name": "metadata.meta"}),  # a name, not a path
    ("manage_script", {"action": "create", "name": "Foo", "path": "Assets/Scripts"}),
    ("batch_execute", _batch(("manage_asset", {"action": "get_info", "path": "Assets/x.meta"}),
                             ("manage_asset", {"action": "delete", "path": "Assets/Foo.prefab"}))),
    ("manage_asset", {"action": "delete"}),
    ("manage_asset", None),
]


@pytest.mark.parametrize("tool,params", ALLOWED)
def test_allowed(tool, params):
    assert meta_refusal(tool, params) is None


def test_deep_batch_nesting_still_refused():
    params = {"action": "delete", "path": "Assets/x.meta"}
    tool = "manage_asset"
    for _ in range(40):
        params, tool = _batch((tool, params)), "batch_execute"
    assert meta_refusal(tool, params) is not None


# ── `target` of an object tool names a scene object, not a file ──────────────

SCENE_OBJECT = "Assets/x.meta"  # a hierarchy path; nothing on disk is named

SCENE_TARGET_ALLOWED = [
    ("manage_gameobject", {"action": "modify", "target": SCENE_OBJECT, "name": "Renamed"}),
    ("manage_gameobject", {"action": "modify", "Target": SCENE_OBJECT, "search_method": "by_path"}),
    ("manage_gameobject", {"action": "look_at", "target": "Cam", "look_at_target": SCENE_OBJECT}),
    ("manage_gameobject", {"action": "delete", "target": SCENE_OBJECT}),
    ("manage_material", {"action": "assign_material_to_renderer", "target": SCENE_OBJECT,
                         "material_path": "Assets/M.mat"}),
    ("manage_components", {"action": "add", "target": SCENE_OBJECT, "component_type": "Rigidbody"}),
    ("manage_camera", {"action": "screenshot", "view_target": SCENE_OBJECT}),
    ("manage_physics", {"action": "add_joint", "target": SCENE_OBJECT, "joint_type": "hinge"}),
    ("manage_prefabs", {"action": "create_from_gameobject", "target": SCENE_OBJECT,
                        "prefab_path": "Assets/P.prefab"}),
    ("manage_scene", {"action": "move_to_scene", "target": SCENE_OBJECT}),
    ("batch_execute", _batch(("manage_gameobject", {"action": "modify", "target": SCENE_OBJECT, "name": "R"}))),
    ("batch_execute", {"Commands": [{"Tool": "manage_material", "Params": {
        "action": "assign_material_to_renderer", "target": SCENE_OBJECT, "material_path": "Assets/M.mat"}}]}),
    ("batch_execute", {"commands": json.dumps([{"tool": "manage_gameobject", "params": {
        "action": "modify", "target": SCENE_OBJECT, "name": "R"}}])}),
]


@pytest.mark.parametrize("tool,params", SCENE_TARGET_ALLOWED)
def test_object_target_ending_in_meta_is_allowed(tool, params):
    assert meta_refusal(tool, params) is None


SCENE_TARGET_STILL_REFUSED = [
    # a real path parameter next to an object target
    ("manage_material", {"action": "assign_material_to_renderer", "target": "Cube",
                         "material_path": "Assets/M.mat.meta"}),
    ("manage_prefabs", {"action": "create_from_gameobject", "target": "Cube",
                        "prefab_path": "Assets/P.prefab.meta"}),
    # an object key only counts at the top level of that tool's params
    ("manage_gameobject", {"action": "modify", "target": "Cube",
                           "component_properties": {"target": "Assets/x.meta"}}),
    ("manage_gameobject", {"action": "modify", "target": json.dumps({"path": "Assets/x.meta"})}),
    # `target` stays a path for every other tool
    ("manage_scriptable_object", {"action": "modify", "target": "Assets/Data.asset.meta"}),
    ("unknown_custom_tool", {"target": "Assets/x.meta"}),
    ("manage_asset", {"action": "delete", "path": "Assets/X.meta"}),
    ("manage_asset", {"action": "delete", "target": "Assets/X.meta"}),
    ("batch_execute", _batch(("manage_gameobject", {"action": "modify", "target": SCENE_OBJECT}),
                             ("manage_asset", {"action": "delete", "path": "Assets/X.meta"}))),
    ("batch_execute", {"commands": [{"tool": "manage_asset", "params": {"action": "delete",
                                                                         "target": "Assets/X.meta"}}]}),
]


@pytest.mark.parametrize("tool,params", SCENE_TARGET_STILL_REFUSED)
def test_path_parameters_next_to_object_targets_still_refused(tool, params):
    assert meta_refusal(tool, params) is not None, (tool, params)


# ── an NTFS 8.3 alias of a .meta file is the .meta file ──────────────────────
# ManageTexture create writes its `path` with File.WriteAllBytes, and Windows
# opens the long file through its short name; AssetDatabase is never asked.

@pytest.mark.parametrize("path", [
    "Assets/ABCDEF~1.MET",
    "Assets\\Textures\\XPNG~12.met",
    "Assets/ABCDEF~1.MET::$DATA",
    "Assets/ABCDEF~1.MET. ",
])
def test_short_name_of_a_meta_file_is_refused(path):
    assert meta_refusal("manage_texture", {"action": "create", "path": path}) is not None
    assert meta_refusal("batch_execute", _batch(("manage_asset", {"action": "delete", "path": path}))) is not None


@pytest.mark.parametrize("path", ["Assets/ABCDEF~1.PNG", "Assets/backup~1/x.png", "Assets/a.met"])
def test_names_that_are_not_meta_aliases_pass(path):
    assert meta_refusal("manage_texture", {"action": "create", "path": path}) is None


def test_deep_nesting_refuses_a_meta_short_name():
    params = {"action": "delete", "path": "Assets/ABCDEF~1.MET"}
    tool = "manage_asset"
    for _ in range(40):
        params, tool = _batch((tool, params)), "batch_execute"
    assert meta_refusal(tool, params) is not None


# ── wiring: UnityInstanceMiddleware.on_call_tool ─────────────────────────────


class _Message:
    def __init__(self, name, args):
        self.name = name
        self.arguments = args


class _Context:
    def __init__(self, name, args):
        self.message = _Message(name, args)


def _middleware(monkeypatch):
    from transport.unity_instance_middleware import UnityInstanceMiddleware
    mw = UnityInstanceMiddleware()

    async def no_injection(_ctx):
        return None

    monkeypatch.setattr(mw, "_inject_unity_instance", no_injection)
    return mw


@pytest.mark.parametrize("approval", [
    {"approved": True, "automatic": True},  # auto mode
    {"approved": True},                     # step mode, user clicks yes
])
@pytest.mark.parametrize("tool,params", [
    ("manage_asset", {"action": "delete", "path": "Assets/x.meta"}),
    ("batch_execute", {"Commands": [{"tool": "manage_asset",
                                     "params": {"action": "rename", "Path": "Assets/x.meta"}}]}),
])
def test_middleware_refuses_before_the_gate(monkeypatch, approval, tool, params):
    from transport import unity_instance_middleware
    mw = _middleware(monkeypatch)
    asked, reached = [], []

    async def ask(tool_name, shown, **_kw):
        asked.append(tool_name)
        return approval

    async def call_next(_ctx):
        reached.append(True)
        return "REACHED UNITY"

    monkeypatch.setattr(approval_gate, "_onay_iste", ask)
    with pytest.raises(unity_instance_middleware.ToolError) as raised:
        asyncio.run(mw.on_call_tool(_Context(tool, params), call_next))
    assert "Unity owns .meta files" in str(raised.value)
    assert asked == [], "an approval card was raised for a call the rule refuses"
    assert reached == []


def test_middleware_passes_other_calls_to_the_gate(monkeypatch):
    mw = _middleware(monkeypatch)
    asked = []

    async def ask(tool_name, shown, **_kw):
        asked.append(tool_name)
        return {"approved": True, "automatic": True}

    async def call_next(_ctx):
        return "REACHED UNITY"

    monkeypatch.setattr(approval_gate, "_onay_iste", ask)
    out = asyncio.run(mw.on_call_tool(
        _Context("manage_asset", {"action": "delete", "path": "Assets/Foo.prefab"}), call_next))
    assert out == "REACHED UNITY" and asked == ["manage_asset"]


# ── wiring: /api/command ─────────────────────────────────────────────────────


def test_api_command_route_refuses():
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(_SRC)],
        cwd=str(SERVER_DIR), capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    results = json.loads(completed.stdout)
    assert len(results) == 3
    for result in results:
        assert result["status"] == 403, result
        assert "Unity owns .meta files" in result["body"]["error"], result
