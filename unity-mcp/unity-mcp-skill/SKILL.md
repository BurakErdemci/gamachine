---
name: unity-mcp-orchestrator
description: Orchestrate Unity Editor via MCP (Model Context Protocol) tools and resources. Use when working with Unity projects through MCP for Unity - creating/modifying GameObjects, editing scripts, managing scenes, running tests, or any Unity Editor automation. Provides best practices, tool schemas, and workflow patterns for effective Unity-MCP integration.
---

# Unity-MCP Operator Guide

This skill helps you effectively use the Unity Editor with MCP tools and resources.

## Template Notice

Examples in `references/workflows.md` and `references/tools-reference.md` are reusable templates. They may be inaccurate across Unity versions, package setups (UGUI/TMP/Input System), and project-specific conventions. Please check console, compilation errors, or use screenshot after implementation.

Before applying a template:
- Validate targets/components first via resources and `find_gameobjects`.
- Treat names, enum values, and property payloads as placeholders to adapt.

## Quick Start: Resource-First Workflow

**Always read relevant resources before using tools.** This prevents errors and provides the necessary context.

```
1. Check editor state     → mcpforunity://editor/state
2. Understand the scene   → mcpforunity://scene/gameobject-api
3. Find what you need     → find_gameobjects or resources
4. Take action            → tools (manage_gameobject, create_script, script_apply_edits, apply_text_edits, validate_script, delete_script, get_sha, etc.)
5. Verify results         → read_console, manage_camera(action="screenshot"), resources
```

## Critical Best Practices

### 1. After Writing/Editing Scripts: Read the Compile Verdict

```python
# create_script, script_apply_edits, apply_text_edits and manage_script(action="create")
# wait for the compile their write causes (wait_for_compile defaults to true)
# and return its verdict in data.compile.
result = create_script(path="Assets/Scripts/Foo.cs", contents="...")
verdict = result["data"]["compile"]["verdict"]
# "clean"  -> the code compiles and the domain reload finished; new types are usable
# "errors" -> data.compile.errors lists CS code, file and line
# anything else (compiling / pending / stale / timeout / unknown) is NOT clean

# After writing .cs files with your OWN file tools, Unity has not seen them yet:
refresh_unity(compile="request")   # result carries data.compile

# Live verdict without refreshing (read-only):
compile_status()
```

**Why:** An empty `read_console` list is not a clean compile: the console reads 0 errors before and while Unity compiles. When the verdict is not `clean`, `read_console` adds a top-level `compile_state` field saying so. Only `clean` means new components/types can be used.

### 2. Never Touch `.meta` Files or Write Unity YAML Assets as Text

Unity owns `.meta` files: a lost or duplicated one breaks the asset's GUID and every reference to it. Any MCP call that would write, delete, move or rename a `.meta` file is refused, in every approval mode. Move, rename or delete the asset itself with `manage_asset`, and Unity moves its `.meta` with it.

Scenes, prefabs, materials and other Unity YAML assets (`.unity`, `.prefab`, `.asset`, `.mat`, ...) are changed through the tools (`manage_scene`, `manage_prefabs`, `manage_gameobject`, `manage_components`, `manage_material`, `manage_asset`), not by editing the file as text. Gamachine refuses raw text writes to them from its own file tools and shells.

### 3. Undo One Action

Each mutating response carries `undo: {action_id, group, name, undoable}`. To revert your own last action, call `manage_editor(action="undo_action", action_id=...)`. It is refused if anything was done after it (so the user's later edits are never lost) and for `undoable: false` (file changes). A `batch_execute` call is one undo step. `warnings: ["prefab_link: ..."]` in a response means the change breaks or restructures a prefab link; it was still applied.

### 4. Use `batch_execute` for Multiple Operations

```python
# 10-100x faster than sequential calls
batch_execute(
    commands=[
        {"tool": "manage_gameobject", "params": {"action": "create", "name": "Cube1", "primitive_type": "Cube"}},
        {"tool": "manage_gameobject", "params": {"action": "create", "name": "Cube2", "primitive_type": "Cube"}},
        {"tool": "manage_gameobject", "params": {"action": "create", "name": "Cube3", "primitive_type": "Cube"}}
    ],
    parallel=True  # Hint only: Unity may still execute sequentially
)
```

**Max 25 commands per batch by default (configurable in Unity MCP Tools window, max 100).** Use `fail_fast=True` for dependent operations.

**Tip:** Also use `batch_execute` for discovery — batch multiple `find_gameobjects` calls instead of calling them one at a time:
```python
batch_execute(commands=[
    {"tool": "find_gameobjects", "params": {"search_term": "Camera", "search_method": "by_component"}},
    {"tool": "find_gameobjects", "params": {"search_term": "Player", "search_method": "by_tag"}},
    {"tool": "find_gameobjects", "params": {"search_term": "GameManager", "search_method": "by_name"}}
])
```

### 5. Use Screenshots to Verify Visual Results

```python
# Basic screenshot (saves to Assets/, returns file path only)
manage_camera(action="screenshot")

# Inline screenshot (returns base64 PNG directly to the AI)
manage_camera(action="screenshot", include_image=True)

# Use a specific camera and cap resolution for smaller payloads
manage_camera(action="screenshot", camera="MainCamera", include_image=True, max_resolution=512)

# Batch surround: captures front/back/left/right/top/bird_eye around the scene
manage_camera(action="screenshot", batch="surround", max_resolution=256)

# Batch surround centered on a specific object
manage_camera(action="screenshot", batch="surround", view_target="Player", max_resolution=256)

# Positioned screenshot: place a temp camera and capture in one call
manage_camera(action="screenshot", view_target="Player", view_position=[0, 10, -10], max_resolution=512)

# Scene View screenshot: capture what the developer sees in the editor
manage_camera(action="screenshot", capture_source="scene_view", include_image=True)

# Scene View framed on a specific object
manage_camera(action="screenshot", capture_source="scene_view", view_target="Canvas", include_image=True)
```

**Best practices for AI scene understanding:**
- Use `include_image=True` when you need to *see* the scene, not just save a file.
- Use `batch="surround"` for a comprehensive overview (6 angles, one command).
- Use `view_target`/`view_position` to capture from a specific viewpoint without needing a scene camera.
- Use `capture_source="scene_view"` to see the editor viewport (gizmos, wireframes, grid).
- Keep `max_resolution` at 256–512 to balance quality vs. token cost.

```python
# Agentic camera loop: point, shoot, analyze
manage_gameobject(action="look_at", target="MainCamera", look_at_target="Player")
manage_camera(action="screenshot", camera="MainCamera", include_image=True, max_resolution=512)
# → Analyze image, decide next action

# Multi-view screenshot (6-angle contact sheet)
manage_camera(action="screenshot_multiview", max_resolution=480)

# Scene View for editor-level inspection (shows gizmos, debug overlays, etc.)
manage_camera(action="screenshot", capture_source="scene_view", view_target="Player", include_image=True)
```

### 6. Check Console After Major Changes

```python
read_console(
    action="get",
    types=["error", "warning"],  # Focus on problems
    count=10,
    format="detailed"
)
```

### 7. Always Check `editor_state` Before Complex Operations

```python
# Read mcpforunity://editor/state to check:
# - is_compiling: Wait if true
# - is_domain_reload_pending: Wait if true  
# - ready_for_tools: Only proceed if true
# - blocking_reasons: Why tools might fail
```

## Parameter Type Conventions

These are common patterns, not strict guarantees. `manage_components.set_property` payload shapes can vary by component/property; if a template fails, inspect the component resource payload and adjust.

### Vectors (position, rotation, scale, color)
```python
# Both forms accepted:
position=[1.0, 2.0, 3.0]        # List
position="[1.0, 2.0, 3.0]"     # JSON string
```

### Booleans
```python
# Both forms accepted:
include_inactive=True           # Boolean
include_inactive="true"         # String
```

### Colors
```python
# Auto-detected format:
color=[255, 0, 0, 255]         # 0-255 range
color=[1.0, 0.0, 0.0, 1.0]    # 0.0-1.0 normalized (auto-converted)
```

### Paths
```python
# Assets-relative (default):
path="Assets/Scripts/MyScript.cs"

# URI forms:
uri="mcpforunity://path/Assets/Scripts/MyScript.cs"
uri="file:///full/path/to/file.cs"
```

## Core Tool Categories

| Category | Key Tools | Use For |
|----------|-----------|---------|
| **Scene** | `manage_scene`, `find_gameobjects` | Scene operations, finding objects |
| **Objects** | `manage_gameobject`, `manage_components` | Creating/modifying GameObjects |
| **Scripts** | `create_script`, `script_apply_edits`, `validate_script`, `compile_status` | C# code management (writes wait for the compile and return its verdict in `data.compile`) |
| **Assets** | `manage_asset`, `manage_prefabs` | Asset operations. **Prefab instantiation** is done via `manage_gameobject(action="create", prefab_path="...")`, not `manage_prefabs`. |
| **Editor** | `manage_editor`, `execute_menu_item`, `read_console` | Editor control, package deployment (`deploy_package`/`restore_package` actions) |
| **Testing** | `run_tests`, `get_test_job` | Unity Test Framework |
| **Batch** | `batch_execute` | Parallel/bulk operations |
| **Camera** | `manage_camera` | Camera management (Unity Camera + Cinemachine). **Tier 1** (always available): create, target, lens, priority, list, screenshot. **Tier 2** (requires `com.unity.cinemachine`): brain, body/aim/noise pipeline, extensions, blending, force/release. 7 presets: follow, third_person, freelook, dolly, static, top_down, side_scroller. Resource: `mcpforunity://scene/cameras`. Use `ping` to check Cinemachine availability. See [tools-reference.md](references/tools-reference.md#camera-tools). |
| **Graphics** | `manage_graphics` | Rendering and post-processing management. 33 actions across 5 groups: **Volume** (create/configure volumes and effects, URP/HDRP), **Bake** (lightmaps, light probes, reflection probes, Edit mode only), **Stats** (draw calls, batches, memory), **Pipeline** (quality levels, pipeline settings), **Features** (URP renderer features: add, remove, toggle, reorder). Resources: `mcpforunity://scene/volumes`, `mcpforunity://rendering/stats`, `mcpforunity://pipeline/renderer-features`. Use `ping` to check pipeline status. See [tools-reference.md](references/tools-reference.md#graphics-tools). |
| **Packages** | `manage_packages` | Install, remove, search, and manage Unity packages and scoped registries. Query actions: list installed, search registry, get info, ping, poll status. Mutating actions: add/remove packages, embed for editing, add/remove scoped registries, force resolve. Validates identifiers, warns on git URLs, checks dependents before removal (`force=true` to override). See [tools-reference.md](references/tools-reference.md#package-tools). |
| **Physics** | `manage_physics` | Manage 3D and 2D physics (21 actions). Settings, collision matrix, materials, joints (14 types). Queries: `raycast`, `raycast_all`, `linecast`, `shapecast` (sphere/box/capsule sweep), `overlap`. Forces: `apply_force` (AddForce/AddTorque/AddExplosionForce with ForceMode). Rigidbody: `get_rigidbody`, `configure_rigidbody` (mass, drag, gravity, constraints, collision detection). Validation: scene-wide checks. Simulation: `simulate_step` in edit mode. See [tools-reference.md](references/tools-reference.md#physics-tools). |
| **ProBuilder** | `manage_probuilder` | 3D modeling, mesh editing, complex geometry. **When `com.unity.probuilder` is installed, prefer ProBuilder shapes over primitive GameObjects** for editable geometry, multi-material faces, or complex shapes. Supports 12 shape types, face/edge/vertex editing, smoothing, and per-face materials. See [ProBuilder Guide](references/probuilder-guide.md). |
| **UI** | `manage_ui`, `batch_execute` with `manage_gameobject` + `manage_components` | **UI Toolkit**: Use `manage_ui` to create UXML/USS files, attach UIDocument, inspect visual trees. **uGUI (Canvas)**: Use `batch_execute` for Canvas, Panel, Button, Text, Slider, Toggle, Input Field. **Read `mcpforunity://project/info` first** to detect uGUI/TMP/Input System/UI Toolkit availability. (see [UI workflows](references/workflows.md#ui-creation-workflows)) |
| **Docs** | `unity_reflect`, `unity_docs` | API verification and documentation lookup. **`unity_reflect`** inspects live C# APIs via reflection (requires Unity connection): `search` types across assemblies, `get_type` for member summary, `get_member` for full signatures. **`unity_docs`** fetches official docs from docs.unity3d.com (no Unity connection needed): `get_doc` (ScriptReference), `get_manual` (Manual pages), `get_package_doc` (package docs), `lookup` (parallel search all sources + project assets). **Trust hierarchy: reflection > project assets > docs.** Workflow: `unity_reflect` search -> get_type -> get_member -> `unity_docs` lookup. See [tools-reference.md](references/tools-reference.md#docs-tools). |

## Common Workflows

### Creating a New Script and Using It

```python
# 1. Create the script (waits for the compile and returns its verdict)
result = create_script(
    path="Assets/Scripts/PlayerController.cs",
    contents="using UnityEngine;\n\npublic class PlayerController : MonoBehaviour\n{\n    void Update() { }\n}"
)

# 2. Check the verdict: only "clean" means the type exists
# result["data"]["compile"]["verdict"] == "clean"
# On "errors", fix the listed CS errors; on anything else call compile_status()

# 3. Only then attach to GameObject
manage_gameobject(action="modify", target="Player", components_to_add=["PlayerController"])
```

### Finding and Modifying GameObjects

```python
# 1. Find by name/tag/component (returns IDs only)
result = find_gameobjects(search_term="Enemy", search_method="by_tag", page_size=50)

# 2. Get full data via resource
# mcpforunity://scene/gameobject/{instance_id}

# 3. Modify using the ID
manage_gameobject(action="modify", target=instance_id, position=[10, 0, 0])
```

### Running and Monitoring Tests

```python
# 1. Start test run (async)
result = run_tests(mode="EditMode", test_names=["MyTests.TestSomething"])
job_id = result["data"]["job_id"]   # refused (error "compile" / "busy") when scripts are not compiled clean

# 2. Poll for completion: data.status is "running", "succeeded" or "failed"
result = get_test_job(job_id=job_id, wait_timeout=60, include_failed_tests=True)
# data.result.summary holds the counts; error "compile" means the pass does not count
```

## Pagination Pattern

Large queries return paginated results. Always follow `next_cursor`:

```python
cursor = 0
all_items = []
while True:
    result = manage_scene(action="get_hierarchy", page_size=50, cursor=cursor)
    all_items.extend(result["data"]["items"])
    if not result["data"].get("next_cursor"):
        break
    cursor = result["data"]["next_cursor"]
```

## Multi-Instance Workflow

With exactly one Unity Editor connected, every call reaches it automatically. When multiple Unity Editors are running, route each call; the server does not remember a selection:

```python
# 1. List instances via resource: mcpforunity://instances
# 2. Pass unity_instance on each call (Name@hash or a hash prefix)
manage_scene(action="get_active", unity_instance="MyProject@abc123")
# Or fix the instance for the whole connection in the client config:
#   ?instance=MyProject@abc123 on the MCP URL, or an X-Unity-Instance header
```

`set_active_instance` does not pin anything: it only checks an identifier and returns `success=false` with the exact `Name@hash` and how to route to it. With several Editors connected and no instance given, the call is refused.

## Error Recovery

| Symptom | Cause | Solution |
|---------|-------|----------|
| Tools return "busy" | Compilation in progress | Wait, check `editor_state` or `compile_status` |
| Console is empty but a new type is missing | Unity has not compiled yet | Call `compile_status`; after your own file writes, `refresh_unity(compile="request")` |
| "stale_file" error | File changed since SHA | Re-fetch SHA with `get_sha`, retry |
| Connection lost | Domain reload | Wait ~5s, reconnect |
| Commands hit the wrong Editor or are refused | Several instances connected | Pass `unity_instance` on the call (see Multi-Instance Workflow) |
| "not part of /mcp/..." or "not enabled on /mcp" | Tool's group is outside this URL's profile | Connect to `/mcp/full`, or enable the group in the Unity Editor's tool settings and run `manage_tools(action="sync")` |

## Reference Files

For detailed schemas and examples:

- **[tools-reference.md](references/tools-reference.md)**: Complete tool documentation with all parameters
- **[resources-reference.md](references/resources-reference.md)**: All available resources and their data
- **[workflows.md](references/workflows.md)**: Extended workflow examples and patterns
