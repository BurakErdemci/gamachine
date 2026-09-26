# Unity MCP integration

The embedded Unity Editor bridge: 52 tools in ten groups, zero-install setup, and
the input system that lets the agent actually play the game it just built.

---

## 🎮 Unity MCP Integration

Unifies the [CoplayDev/unity-mcp](https://github.com/CoplayDev/unity-mcp) project, giving the AI direct control of the Unity Editor.

### Setup: fully automatic
1. Open your Unity project (the Editor must be running)
2. Click the **Unity MCP toggle** in the app
3. `unity_mcp_manager` starts the MCP server with the bundled `uvx`, installs the package, and connects to the Editor
4. When the toggle turns green (`Unity connected ✓`) it's ready

> Because `uv`/`uvx` is bundled in the packaged app, the user doesn't need to install it separately. If the Unity Editor is closed the toggle can't connect — open Unity first.

> **Approval behavior:** In step mode, every unityMCP call classified as a **write** opens an approval card on every provider (the Unity MCP server asks before the call reaches Unity); read-classified calls such as `read_console`, `compile_status` or `find_in_file` pass without one. Scene queries such as `manage_scene action=get_hierarchy` or `find_gameobjects` are write-classified and open a card: those tools first refresh a project changed outside the Editor, which can compile scripts. In auto mode no card appears. See [Approval scope](security.md#️-approval-scope-what-is-and-isnt-confirmed-an-honesty-note).

> **Fixed rule, every mode:** no Unity MCP call may write, delete, move or rename a `.meta` file; the server refuses it before any approval card. Target the asset's own path with `manage_asset` instead, so the GUID stays intact. Agents' own file tools are also refused raw writes to Unity YAML assets (scenes, prefabs, materials ...): these tools are the route for those. See [Fixed Unity file rule](security.md#5-fixed-unity-file-rule-every-approval-mode).

### Tools

| Category | Tools |
|---|---|
| Scene | `manage_scene`, `find_gameobjects`, `manage_gameobject`, `set_active_instance` |
| Components | `manage_components`, `manage_physics`, `manage_animation` |
| **Input (new)** | **`manage_input`** — keyboard/mouse/gamepad/UI input into the running game |
| UI/Camera | `manage_ui`, `manage_camera` (incl. screenshots) |
| Prefab/Asset | `manage_prefabs`, `manage_scriptable_object`, `manage_asset`, `manage_fbx` |
| Visual | `manage_material`, `manage_shader`, `manage_texture`, `manage_graphics`, `manage_sprite`, `manage_vfx` |
| Script | `manage_script`, `script_apply_edits`, `apply_text_edits`, `create_script`, `delete_script`, `validate_script`, `get_sha`, `manage_script_capabilities`, `find_in_file`, `read_console`, `compile_status` |
| Test & Profiling | `run_tests`, `get_test_job`, `manage_profiler` |
| **Playtest** | `play_session`, `play_step`, `play_capture`, `game_hooks`, `run_playtest` — deterministic play sessions: frame stepping with input, captures, game-exposed hooks, scenario files |
| Build | `manage_build`, `manage_packages`, `manage_editor`, `refresh_unity`, `manage_probuilder` |
| Discovery | `unity_docs`, `unity_reflect`, `manage_tools`, `execute_custom_tool`, `debug_request_context` |
| Orchestration | `batch_execute` (up to 25 commands per call), `execute_code`, `execute_menu_item` |

Not every client sees all of them. The server (fastmcp 4, MCP protocol
2026-07-28; clients on the older 2025 handshake still work) decides the list in two
layers: each tool belongs to a **group** (core and playtest are on by default, the
other eight are opt-in in the Editor's Tools tab), and the **URL** a client connects
to is a profile that fixes which groups it gets. Every URL needs the `X-API-Key`
header; an unknown profile answers 404.

| URL | Tools |
|---|---|
| `/mcp` | Groups enabled in the Unity Editor's Tools tab (core + playtest by default) plus five server meta-tools (`manage_tools`, `set_active_instance`, `debug_request_context`, `execute_custom_tool`, `manage_script_capabilities`): 37 tools before an Editor has reported its toggles |
| `/mcp/gamachine` | core + playtest, no meta-tools: 32 tools, the set the backend exports to API models |
| `/mcp/full` | Every group, regardless of the Editor's toggles: 52 tools |

The URL fixes the profile, not a frozen list. `/mcp/gamachine` and `/mcp/full` list
the same tools for as long as the server runs. `/mcp` filters every `tools/list`
against the groups currently enabled, and those change whenever the Editor registers
its tools (when it connects or reconnects, and when its Tools tab toggles change). The
server then sends `tools/list_changed` to the clients it tracks, so a client on `/mcp`
should list the tools again when it gets one.
`manage_tools(action="list_groups")` reports which profile the call came in on;
`activate`, `deactivate` and `reset` change nothing and return an error naming these URLs. To reach an opt-in group
(docs, vfx, profiling, ...), enable it in the Tools tab (affects `/mcp`) or
connect to `/mcp/full`.

### Compile verdicts: an empty console is not a clean compile

Unity's console reads 0 errors before and while a compile runs, so "no errors in
`read_console`" proved nothing. The Editor plugin now numbers every script compile
(`CompileTracker`, kept in `SessionState` so it survives the domain reload) and the
server turns that into one verdict:

| Verdict | Meaning |
|---|---|
| `clean` | The code as it is now compiles and the domain reload after it has finished |
| `errors` | The last compile failed; the compiler errors come with CS code, file and line |
| `compiling` | A compile is running; the error list is not final |
| `pending` | An asset import or the domain reload after a compile is still running |
| `stale` | Script files changed on disk since the last compile started; call `refresh_unity` |
| `timeout` | A waiting call gave up after 90 s without a final state |
| `unknown` | The status could not be read or trusted (plugin too old, malformed status, Editor restarted during a wait); not the same as clean |

Where it shows up:

- **`compile_status`** — read-only, never refreshes or compiles; returns the live verdict.
- **Script tools wait by default.** `create_script`, `script_apply_edits`,
  `apply_text_edits` and `manage_script action=create` wait for the compile their
  write causes and return the verdict in `data.compile`. `wait_for_compile=false`
  returns right after the write.
- **`refresh_unity`** returns `data.compile` with `compile="request"`, or whenever the
  refresh picked up changed scripts. After writing `.cs` files with any other tool,
  call it: Unity does not import them on its own while it is unfocused.
- **`read_console`** (`action=get`) adds a top-level `compile_state` whenever the
  verdict is not `clean`; an empty list next to it is not a pass.

Only `clean` means new types can be used.

### Undoing one agent action

Every mutating MCP call is one named Unity undo step, `MCP: <tool> <action> #<id>`;
a `batch_execute` call is one step for all its sub-calls. (Reads are not tracked, and
neither are the play-session, input, test, profiler and refresh tools, which have
nothing Undo could revert.) The response carries it:

```json
"undo": {"action_id": "3f9a1c2e", "group": 812, "name": "MCP: manage_gameobject create #3f9a1c2e", "undoable": true}
```

`undoable` is `true`, `false` or `"partial"` (scene/object changes revert, file or
project-settings changes stay), with a `note` when it is not `true`.

`manage_editor action=undo_action action_id=<id>` reverts exactly that action, but
only while it is still the most recent undo step. Anything later (another agent
call, or the user's own edit) makes it refuse, so a later edit is never lost; step
back with `manage_editor action=undo` or Edit > Undo instead. It also refuses in play
mode and for `undoable: false` actions.

Not fully undoable, and reported as such: deleting a GameObject (it uses
`DestroyImmediate`) and file operations (`manage_asset`, `manage_script`, scene
save/load, prefab creation ...). Calls made in play mode get no undo group.

Each action is also logged, one JSON line per call, to
`<project>/Library/GamachineActions/actions.jsonl` (rotated at 5 MB; under `Library/`
so writing it never triggers an import).

**Prefab-link warnings.** Deleting or reparenting an object inside a prefab
instance, removing one of its components, unpacking an instance or deleting a
prefab asset adds `warnings: ["prefab_link: ..."]` to the response, naming the
instance and asset affected. Warnings never refuse the call.

### 🎮 The AI can now play the game (`manage_input`)

Entering play mode and taking screenshots already worked — what was missing was **acting**. The AI could start the game and watch it, but not play it; that was the open link in the loop.

`manage_input` queues events into Unity Input System's **virtual devices** (`QueueStateEvent`). Because the events are produced from inside the process, **no window focus is required** — the AI can play while you do something else, and your keyboard is not hijacked.

```
"Start the game, walk forward with W for 2 seconds, jump with space, then take a screenshot"
```

Actions: `describe`, `key`, `mouse_move`, `mouse_button`, `scroll`, `gamepad`, `ui_click`, `sequence`, `reset`.

> ⚠️ **A permanent limit — call `describe` first.** Only game code written against the **new Input System** sees these events. If your project uses the legacy `UnityEngine.Input` (`Input.GetKey`), virtual-device input **will not reach it**; the only thing that still works there is `ui_click`, which triggers uGUI buttons. `describe` reports the project's input backend — but it only reads the project setting, it does not measure which API the game code actually uses.

### A fork that evolves on agent feedback

The tools in this fork are continuously improved based on feedback from real overnight agent sessions (Claude, GLM):

- **Token economy** — `get_hierarchy` returns a lightweight summary by default (`detail:"full"` for everything); `find_gameobjects` results ship with a `name+path` summary (no N+1 follow-up calls)
- **Smart search** — `match_mode: exact|contains|prefix` on `find_gameobjects` ("Prop_" finds every prop)
- **Write-compile-verify in one turn** — script writes wait for the compile by default and return its verdict (`data.compile`, see [Compile verdicts](#compile-verdicts-an-empty-console-is-not-a-clean-compile)) in the same response
- **Batch chaining** — `"$[0].data.instanceID"` references enable create→configure→parent in a single `batch_execute`
- **Honest feedback** — script changes during play mode carry a warning; a modify call that changes nothing is reported as `no_op`

### Multiple Unity instances

With one Editor open, every call goes to it automatically. If more than one project is open, name the target on each call with `unity_instance="Name@hash"` (from the `mcpforunity://instances` resource), or fix it for a whole connection with `?instance=Name@hash` on the MCP URL or an `X-Unity-Instance` header. Without one of these the call is refused. `set_active_instance` no longer pins a selection: it only checks an identifier and says how to route to it.

---

## Fork provenance (measured 31 Aug 2026)

`unity-mcp/` is a **vendored copy**, not a submodule: there is no upstream remote
and no pinned commit anywhere in the tree. Everything below was measured, because
nothing recorded it before.

| What | Value |
|---|---|
| Vendored at | upstream `680ba458` (9.6.9-beta.7, May 2026) — a plain file copy; measured by tree diff 17 Sep 2026, the earlier `dc539b6` does not exist upstream |
| `Server/pyproject.toml` | `9.6.8` — byte-identical to upstream tag `v9.6.8` |
| `MCPForUnity/package.json` | `9.6.9-beta.7` — **no upstream tag carries this**; it comes from upstream's `beta` branch, which is also its default branch |
| Local commits since | **45**, touching this directory |

Two consequences, and they are the reason this section exists:

**The two halves report different versions, and the split is ours.** Upstream
`v9.6.8` has both files at `9.6.8`; here the Unity plugin is ahead. So the
mismatch was introduced on this side and is not inherited. It is currently
harmless — nothing reads either version at runtime — but anyone comparing the
tree against a tag has to know which half to compare.

**Do not "just pull upstream".** Those 45 commits are real work, not drift:
`manage_input` (the agent can play the game), the `execute_code` schema fix, the
`manage_sprite` line that became upstream PR #1338, and the brand rename. A merge
that treats this directory as a clean copy silently reverts them.

The workable path, if a sync is ever wanted: add upstream as a remote, diff this
tree against `v9.6.8` to recover the true local patch set, then replay that set
onto the newer tag. That has never been done, and this note does not claim it is
cheap — it claims that doing it blind is expensive.

```bash
# Where the divergence actually is:
git log --oneline -- unity-mcp | wc -l          # 45 local commits
gh api repos/CoplayDev/unity-mcp/tags -q '.[].name' | head   # upstream is on v10.x now
```

---

---

[← Back to the README](../README.md)
