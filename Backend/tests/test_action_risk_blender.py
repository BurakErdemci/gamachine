"""action_risk.classify_blender_code: what balanced mode asks about in execute_blender_code."""
import textwrap

import pytest

import action_risk
from action_risk import classify, classify_blender_code
from agentic import approval_mode


def critical(code):
    return classify({"kind": "blender_code", "code": code}).critical


# The blender_approval probe's critical lines.
PROBE_CRITICAL = [
    "import os\nos.remove('C:/x')",
    "import subprocess\nsubprocess.run(['calc'])",
    "open('C:/x.txt','w').write('a')",
    "import urllib.request\nurllib.request.urlopen('http://x')",
    "bpy.ops.wm.save_mainfile()",
    "bpy.ops.wm.open_mainfile(filepath='a.blend')",
    "bpy.ops.wm.quit_blender()",
    "exec('print(1)')",
    "__import__('os').system('x')",
    "def (",
    "getattr(__builtins__, 'ev'+'al')('1')",
    "for o in list(bpy.data.objects):\n    bpy.data.objects.remove(o)",
]

PROBE_ROUTINE = [
    "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2)",
    "import bpy\nobj = bpy.context.active_object\nobj.location.x += 1\nobj.name = 'Crate'",
    "import bpy, math\nmat = bpy.data.materials.new('Wood')\nmat.use_nodes = True",
    "import bpy\nbpy.data.objects.remove(bpy.data.objects['Cube'])",
]

# Verbatim from mcp_for_blender 2.2.6, src/blender_mcp/guides/*.md: scene building the
# upstream guides teach must never raise a card in balanced mode.
UPSTREAM_ROUTINE = {
    "animation.md keyframes": '''
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end = 1, 120
        scene.render.fps = 24

        obj.location = (0, 0, 0); obj.keyframe_insert("location", frame=1)
        obj.location = (0, 0, 2); obj.keyframe_insert("location", frame=24)
        obj.rotation_euler.z = 3.1416; obj.keyframe_insert("rotation_euler", index=2, frame=48)
    ''',
    "animation.md fcurves": '''
        def fcurves(obj):
            ad = obj.animation_data
            if not ad or not ad.action:
                return []
            if hasattr(ad, "action_slot"):
                from bpy_extras import anim_utils
                cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
                return list(cb.fcurves) if cb else []
            return list(ad.action.fcurves)
    ''',
    "materials.md creating materials": '''
        mat = bpy.data.materials.new("Brushed Steel")
        mat.use_nodes = True   # no-op in 5.0+, where it's always on
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.62, 1)
        bsdf.inputs["Metallic"].default_value = 1.0
        bsdf.inputs["Roughness"].default_value = 0.35
        obj.data.materials.clear(); obj.data.materials.append(mat)
    ''',
    "bpy.md introspection": '''
        # Valid values of any enum property
        [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
        # Properties of a type
        [p.identifier for p in bpy.types.ShaderNodeTexSky.bl_rna.properties]
        # Operator arguments
        [(p.identifier, p.type) for p in bpy.ops.mesh.primitive_cube_add.get_rna_type().properties]
        # A node's sockets, in the mode you intend to use
        tree = bpy.data.node_groups.new("_probe", "ShaderNodeTree")
        n = tree.nodes.new("ShaderNodeMix"); n.data_type = "RGBA"
        print([(i, s.name, s.type) for i, s in enumerate(n.inputs) if s.enabled])
        bpy.data.node_groups.remove(tree)
    ''',
    "bpy.md viewport override": '''
        area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(area=area, region=region, active_object=obj, selected_objects=[obj]):
            bpy.ops.object.shade_smooth()
    ''',
    "retopology.md clean up": '''
        import bmesh
        bm = bmesh.new(); bm.from_mesh(obj.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=0.0001)
        loose = [v for v in bm.verts if not v.link_edges]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(obj.data); bm.free()
    ''',
    "retopology.md uvs": '''
        # in edit mode with everything selected, via a 3D viewport override
        bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.02)
    ''',
    "rigging.md simple rig": '''
        arm_data = bpy.data.armatures.new("Rig"); rig = bpy.data.objects.new("Rig", arm_data)
        bpy.context.scene.collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        root = arm_data.edit_bones.new("root"); root.head = (0, 0, 0); root.tail = (0, 0.3, 0)
        b = arm_data.edit_bones.new("arm"); b.head = (0, 0, 1); b.tail = (0, 0, 2); b.parent = root
        bpy.ops.object.mode_set(mode="OBJECT")
    ''',
    "rigging.md verify": '''
        pb = rig.pose.bones["upper_arm_fk.L"]; pb.rotation_mode = "XYZ"; pb.rotation_euler.x = 1.2
    ''',
    "rigging.md rigify": '''
        import addon_utils
        addon_utils.enable("rigify", default_set=True)   # ships with Blender
        bpy.ops.object.armature_human_metarig_add()     # needs a 3D viewport override
        metarig = bpy.context.active_object
    ''',
    "rigging.md skinning": '''
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")   # needs selection + active set
    ''',
    "animation.md pose keyframe": '''
        pb = arm.pose.bones["hand.L"]; pb.rotation_mode = "XYZ"
        pb.keyframe_insert("rotation_euler", frame=f)
    ''',
    "animation.md driver": '''
        obj.driver_add("rotation_euler", 2).driver.expression = "frame * 0.1"
    ''',
    "bpy.md selection": '''
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    ''',
    "retopology.md decimate": '''
        m = obj.modifiers.new("Decimate", "DECIMATE"); m.ratio = 0.1
        low = src.copy(); low.data = src.data.copy()
    ''',
    "materials.md colorspace": '''
        img.colorspace_settings.name = "Non-Color"
    ''',
}

OTHER_CRITICAL = {
    "alias of bpy.ops": "from bpy import ops\nops.wm.save_mainfile()",
    "aliased export": "w = bpy.ops\nw.export_scene.gltf(filepath='x.glb')",
    "export operator": "bpy.ops.export_scene.fbx(filepath='C:/x.fbx')",
    "import operator": "bpy.ops.import_scene.obj(filepath='C:/x.obj')",
    "getattr reaching wm": "getattr(bpy.ops, 'wm').quit_blender()",
    "getattr on a non-bpy object": "getattr(math, 'pi')",
    "setattr with a built name": "setattr(bpy.context.object, 'na' + 'me', 'x')",
    "dunder attribute": "bpy.__class__",
    "globals": "globals()['x'] = 1",
    "eval": "eval('1+1')",
    "compile": "compile('1', 'x', 'eval')",
    "open as a value": "f = open\nf('x')",
    "unlisted module": "import io",
    "star import": "from bpy.utils import *",
    "relative import": "from . import x",
    "library write": "bpy.data.libraries.write('C:/x.blend', set())",
    "library load": "with bpy.data.libraries.load('C:/x.blend') as (src, dst):\n    pass",
    "handlers": "bpy.app.handlers.frame_change_pre.append(print)",
    "timers": "bpy.app.timers.register(print)",
    "register_class": "bpy.utils.register_class(Panel)",
    "unregister_class": "bpy.utils.unregister_class(Panel)",
    "text block as module": "bpy.data.texts['t'].as_module()",
    "preferences": "bpy.context.preferences.filepaths.use_scripts_auto_execute = True",
    "image save": "img = bpy.data.images['x']\nimg.filepath_raw = 'C:/x.png'\nimg.save()",
    "render to disk": "bpy.ops.render.render(write_still=True)",
    "numpy file": "import numpy as np\nnp.save('x.npy', [1])",
    "remove in a comprehension": "[bpy.data.meshes.remove(m) for m in bpy.data.meshes]",
    "remove in a while loop": "while bpy.data.objects:\n    bpy.data.objects.remove(bpy.data.objects[0])",
    "remover passed to map": "list(map(bpy.data.meshes.remove, bpy.data.meshes))",
    "aliased collection removed in a loop": "objs = bpy.data.objects\nfor o in list(objs):\n    objs.remove(o)",
    "delete after select all": "bpy.ops.object.select_all(action='SELECT')\nbpy.ops.object.delete()",
    "delete after select by type": "bpy.ops.object.select_by_type(type='MESH')\nbpy.ops.object.delete()",
    "delete after selecting in a loop": ("for o in bpy.context.scene.objects:\n    o.select_set(True)\n"
                                         "bpy.ops.object.delete()"),
    "orphans purge": "bpy.ops.outliner.orphans_purge()",
    "system module reached as an attribute": "bpy.utils.os.remove('x')",
    "frame internals": "def f():\n    pass\nf.gi_frame",
}

OTHER_ROUTINE = {
    "single delete after deselect": ("bpy.ops.object.select_all(action='DESELECT')\n"
                                     "bpy.data.objects['Cube'].select_set(True)\nbpy.ops.object.delete()"),
    "getattr on a bpy object": "obj = bpy.context.active_object\nprint(getattr(obj, 'location'))",
    "list filter": "meshes = [o for o in bpy.data.objects if o.type == 'MESH']",
    "maths": "import math, random\nfrom mathutils import Vector, Matrix\nv = Vector((1, 0, 0)) * math.pi\n"
             "m = Matrix.Rotation(random.random(), 4, 'Z')",
    "light and camera": ("light = bpy.data.lights.new('Key', 'AREA')\nlo = bpy.data.objects.new('Key', light)\n"
                         "bpy.context.scene.collection.objects.link(lo)\n"
                         "cam = bpy.data.cameras.new('Cam')\nco = bpy.data.objects.new('Cam', cam)\n"
                         "bpy.context.scene.camera = co"),
    "modifier": "mod = obj.modifiers.new('Bevel', 'BEVEL')\nmod.width = 0.02\nmod.segments = 3",
    "render engine in try": ("try:\n    bpy.context.scene.render.engine = 'BLENDER_EEVEE_NEXT'\n"
                             "except TypeError as exc:\n    print(exc)"),
    "list remove is not data removal": "names = ['a', 'b']\nfor n in list(names):\n    names.remove(n)",
    "empty code": "",
}


@pytest.mark.parametrize("code", PROBE_CRITICAL)
def test_probe_critical_lines_are_critical(code):
    assert critical(code)


@pytest.mark.parametrize("code", PROBE_ROUTINE)
def test_probe_routine_lines_are_routine(code):
    assert not critical(code)


@pytest.mark.parametrize("name", sorted(UPSTREAM_ROUTINE))
def test_upstream_guide_scene_building_is_routine(name):
    risk = classify_blender_code(textwrap.dedent(UPSTREAM_ROUTINE[name]))
    assert risk.verdict == action_risk.ROUTINE, risk


@pytest.mark.parametrize("name", sorted(OTHER_CRITICAL))
def test_reaching_outside_the_scene_is_critical(name):
    assert critical(OTHER_CRITICAL[name]), name


@pytest.mark.parametrize("name", sorted(OTHER_ROUTINE))
def test_ordinary_scene_code_is_routine(name):
    assert not critical(OTHER_ROUTINE[name]), name


@pytest.mark.parametrize("code", [None, 3, b"import bpy", ["x"]])
def test_non_text_is_critical(code):
    assert critical(code)


def test_reason_names_the_trigger():
    assert classify_blender_code("open('C:/x.txt')").reason == "blender_code: file access (open)"
    assert classify_blender_code("import os").reason == "blender_code: system module (os)"
    assert classify_blender_code("def (").reason == "blender_code: unparsable"
    risk = classify_blender_code("bpy.ops.wm.quit_blender()")
    assert risk.reason == "blender_code: window-manager operator (bpy.ops.wm.quit_blender)"
    assert classify_blender_code("x = 1") == action_risk.Risk(action_risk.ROUTINE, "blender_code")


def test_the_first_trigger_in_the_code_is_named():
    risk = classify_blender_code("import bpy\nbpy.ops.wm.save_mainfile()\nimport os")
    assert "bpy.ops.wm.save_mainfile" in risk.reason


def test_oversized_code_is_critical_without_parsing():
    assert critical("x = 1\n" * 50_000)


def test_classify_is_pure():
    code = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    assert classify_blender_code(code) == classify_blender_code(code)


@pytest.mark.parametrize("mode,code,card", [
    ("auto", "import os", False), ("balanced", "import os", True),
    ("balanced", "import bpy\nbpy.ops.mesh.primitive_cube_add()", False), ("step", "x = 1", True),
])
def test_needs_card_follows_the_mode(mode, code, card):
    assert approval_mode.needs_card({"kind": "blender_code", "code": code}, mode=mode).card is card
