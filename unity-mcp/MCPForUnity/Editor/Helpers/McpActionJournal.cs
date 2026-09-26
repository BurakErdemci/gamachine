using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>One top-level agent action: the Undo group opened for it and what it reports.</summary>
    internal sealed class McpActionScope
    {
        internal string ActionId;
        internal string Tool;
        internal string Action;
        internal string GroupName;
        internal int Group = -1;
        internal JToken Undoable;
        internal string Note;
        internal bool IsBatch;
        internal int TrackedSubCalls;
        internal readonly List<string> Warnings = new List<string>();
    }

    /// <summary>
    /// Gives every mutating top-level MCP command one named Undo group, reports it in the
    /// response as <c>undo</c>, collects prefab-link <c>warnings</c>, and appends one line per
    /// action to Library/GamachineActions/actions.jsonl (Library/ so writing it never triggers
    /// an asset import).
    /// </summary>
    public static class McpActionJournal
    {
        internal const string GroupPrefix = "MCP: ";
        internal const long MaxLogBytes = 5 * 1024 * 1024;
        const int MaxRemembered = 256;

        // AsyncLocal, not a static field: async handlers (batch_execute) run across frames and
        // other commands may be dispatched meanwhile; only continuations of this command see it.
        static readonly AsyncLocal<McpActionScope> CurrentScope = new AsyncLocal<McpActionScope>();
        static int _lastOpenedGroup = -1;
        static readonly Dictionary<string, JObject> Remembered = new Dictionary<string, JObject>();
        static readonly Queue<string> RememberedOrder = new Queue<string>();
        static readonly HashSet<string> UndoneIds = new HashSet<string>();

        static readonly HashSet<string> UntrackedTools = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "find_gameobjects", "read_console", "unity_reflect", "get_test_job", "run_tests",
            "run_playtest", "play_session", "play_step", "play_capture", "game_hooks",
            "manage_input", "manage_profiler",
            // Python preflight runs it ahead of read calls too; it has nothing Undo could revert.
            "refresh_unity",
        };

        static readonly Dictionary<string, HashSet<string>> UntrackedActions = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase)
        {
            ["manage_editor"] = Set("telemetry_status", "telemetry_ping", "play", "pause", "stop", "set_active_tool", "undo", "redo", "undo_action"),
            ["manage_scene"] = Set("get_hierarchy", "get_active", "get_build_settings", "get_loaded_scenes", "scene_view_frame"),
            ["manage_asset"] = Set("search", "get_info", "get_components"),
            ["manage_prefabs"] = Set("get_info", "get_hierarchy"),
            ["manage_script"] = Set("read", "validate", "get_sha"),
            ["execute_code"] = Set("get_history", "clear_history"),
            ["manage_animation"] = Set("animator_get_info", "animator_get_parameter", "controller_get_info", "clip_get_info"),
            ["manage_build"] = Set("status"),
            ["manage_camera"] = Set("ping", "get_brain_status", "list_cameras", "screenshot", "screenshot_multiview"),
            ["manage_fbx"] = Set("get_info"),
            ["manage_graphics"] = Set("ping", "volume_get_info", "volume_list_effects", "bake_status", "bake_get_settings", "stats_get", "stats_list_counters", "stats_get_memory", "pipeline_get_info", "pipeline_get_settings", "feature_list", "skybox_get"),
            ["manage_material"] = Set("ping", "get_material_info"),
            ["manage_packages"] = Set("list_packages", "search_packages", "get_package_info", "ping", "status", "list_registries"),
            ["manage_physics"] = Set("ping", "get_settings", "get_collision_matrix", "raycast", "raycast_all", "linecast", "shapecast", "overlap", "get_rigidbody", "validate"),
            ["manage_probuilder"] = Set("ping", "get_mesh_info", "select_faces", "validate_mesh"),
            ["manage_shader"] = Set("read"),
            ["manage_sprite"] = Set("get_info"),
            ["manage_ui"] = Set("ping", "read", "get_visual_tree", "list"),
            ["manage_vfx"] = Set("ping", "particle_get_info", "vfx_list_templates", "vfx_list_assets", "vfx_get_info", "line_get_info", "trail_get_info"),
        };

        // Tools whose effects live in files or project settings, outside Unity Undo.
        static readonly HashSet<string> FileTools = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "manage_asset", "manage_script", "manage_shader", "manage_texture", "manage_scriptable_object",
            "manage_packages", "manage_build", "manage_fbx", "manage_sprite",
        };

        static readonly Dictionary<string, HashSet<string>> FileActions = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase)
        {
            ["manage_editor"] = Set("add_tag", "remove_tag", "add_layer", "remove_layer", "deploy_package", "restore_package", "sync_csproj"),
            ["manage_scene"] = Set("create", "load", "save", "close_scene"),
            ["manage_prefabs"] = Set("create_from_gameobject", "modify_contents", "open_prefab_stage", "save_prefab_stage", "close_prefab_stage"),
            // GameObjectDelete uses Object.DestroyImmediate, which Undo cannot restore.
            ["manage_gameobject"] = Set("delete"),
            ["manage_material"] = Set("create"),
            ["manage_physics"] = Set("create_physics_material"),
            ["manage_vfx"] = Set("vfx_create_asset"),
        };

        static readonly Dictionary<string, HashSet<string>> FullyUndoableActions = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase)
        {
            ["manage_gameobject"] = Set("create", "modify", "duplicate", "move_relative", "look_at"),
            ["manage_components"] = Set("add", "remove", "set_property"),
        };

        static HashSet<string> Set(params string[] items) => new HashSet<string>(items, StringComparer.OrdinalIgnoreCase);

        internal static string LogPath =>
            Path.Combine(Directory.GetParent(Application.dataPath).FullName, "Library", "GamachineActions", "actions.jsonl");

        internal static McpActionScope Current => CurrentScope.Value;

        /// <summary>Adds a warning to the running action's response; a no-op outside a command.</summary>
        public static void Warn(string message)
        {
            var scope = CurrentScope.Value;
            if (scope != null && !string.IsNullOrEmpty(message) && !scope.Warnings.Contains(message))
            {
                scope.Warnings.Add(message);
            }
        }

        internal static bool IsTracked(string tool, string action)
        {
            if (string.IsNullOrEmpty(tool) || UntrackedTools.Contains(tool)) return false;
            return !(action != null && UntrackedActions.TryGetValue(tool, out var reads) && reads.Contains(action));
        }

        /// <summary>true, false or "partial": how much of this call Unity Undo can revert.</summary>
        internal static JToken ClassifyUndoable(string tool, string action, JObject @params)
        {
            if (FileTools.Contains(tool)) return false;
            action ??= string.Empty;
            if (FileActions.TryGetValue(tool, out var fileActions) && fileActions.Contains(action)) return false;
            if (string.Equals(tool, "manage_animation", StringComparison.OrdinalIgnoreCase)
                && (action.StartsWith("controller_", StringComparison.OrdinalIgnoreCase) || action.StartsWith("clip_", StringComparison.OrdinalIgnoreCase)))
            {
                return false;
            }
            if (FullyUndoableActions.TryGetValue(tool, out var undoable) && undoable.Contains(action))
            {
                var p = new ToolParams(@params ?? new JObject());
                if (string.Equals(tool, "manage_gameobject", StringComparison.OrdinalIgnoreCase))
                {
                    // SaveAsPrefabAsset writes a file; SetParent without Undo.SetTransformParent and
                    // new tags are not fully reverted.
                    if (action == "create" && p.GetBool("save_as_prefab")) return "partial";
                    if (action == "modify" && (p.Has("parent") || p.Has("tag"))) return "partial";
                }
                return true;
            }
            return "partial";
        }

        static string ReadAction(JObject @params)
        {
            var action = @params?["action"];
            return action != null && action.Type == JTokenType.String ? action.ToString().Trim().ToLowerInvariant() : null;
        }

        /// <summary>Opens an Undo group for a top-level command; null when the call is not tracked.</summary>
        internal static McpActionScope Begin(string tool, JObject @params, bool isResource)
        {
            try
            {
                if (isResource || CurrentScope.Value != null) return null;
                string action = ReadAction(@params);
                if (!IsTracked(tool, action)) return null;

                var scope = new McpActionScope
                {
                    ActionId = Guid.NewGuid().ToString("N").Substring(0, 8),
                    Tool = tool,
                    Action = action,
                    IsBatch = string.Equals(tool, "batch_execute", StringComparison.OrdinalIgnoreCase),
                };
                scope.GroupName = $"{GroupPrefix}{tool}{(string.IsNullOrEmpty(action) ? "" : " " + action)} #{scope.ActionId}";
                scope.Undoable = scope.IsBatch ? null : ClassifyUndoable(tool, action, @params);

                if (EditorApplication.isPlayingOrWillChangePlaymode)
                {
                    scope.Undoable = false;
                    scope.Note = "play mode: no undo group was opened; scene changes made in play mode are discarded when it exits";
                }
                else
                {
                    Undo.IncrementCurrentGroup();
                    Undo.SetCurrentGroupName(scope.GroupName);
                    scope.Group = Undo.GetCurrentGroup();
                    _lastOpenedGroup = scope.Group;
                }
                return scope;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>Makes <paramref name="scope"/> ambient for the handler; returns the value to restore.</summary>
        internal static McpActionScope Enter(McpActionScope scope)
        {
            var previous = CurrentScope.Value;
            if (scope != null) CurrentScope.Value = scope;
            return previous;
        }

        internal static void Exit(McpActionScope scope, McpActionScope previous)
        {
            if (scope != null) CurrentScope.Value = previous;
        }

        /// <summary>Called for commands composed inside another (batch_execute sub-calls): no own group.</summary>
        internal static void NoteNested(string tool, JObject @params)
        {
            try
            {
                var scope = CurrentScope.Value;
                if (scope == null) return;
                string action = ReadAction(@params);
                if (!IsTracked(tool, action)) return;
                scope.TrackedSubCalls++;
                if (!EditorApplication.isPlayingOrWillChangePlaymode)
                {
                    scope.Undoable = Combine(scope.Undoable, ClassifyUndoable(tool, action, @params));
                }
            }
            catch (Exception)
            {
                // Never break the command path over bookkeeping.
            }
        }

        static JToken Combine(JToken current, JToken next)
        {
            if (current == null) return next;
            return JToken.DeepEquals(current, next) ? current : "partial";
        }

        internal static async Task<object> Track(McpActionScope scope, Task<object> task)
        {
            object result;
            try
            {
                result = await task.ConfigureAwait(true);
            }
            catch (Exception ex)
            {
                Complete(scope, null, ex);
                throw;
            }
            return Complete(scope, result, null);
        }

        /// <summary>Collapses the group, writes the log line and adds undo/warnings to the result.</summary>
        internal static object Complete(McpActionScope scope, object result, Exception error)
        {
            if (scope == null) return result;
            try
            {
                if (scope.IsBatch && scope.TrackedSubCalls == 0 && scope.Warnings.Count == 0)
                {
                    return result;
                }

                if (scope.Group >= 0)
                {
                    if (_lastOpenedGroup == scope.Group)
                    {
                        Undo.CollapseUndoOperations(scope.Group);
                        // Keep whatever records next (editor callbacks, the user) out of this group.
                        Undo.IncrementCurrentGroup();
                    }
                    else
                    {
                        scope.Undoable = "partial";
                        scope.Note = "another command opened an undo group while this one was running, so its undo steps were not merged into one";
                    }
                }
                if (scope.Undoable == null) scope.Undoable = "partial";

                var undo = new JObject
                {
                    ["action_id"] = scope.ActionId,
                    ["group"] = scope.Group >= 0 ? (JToken)scope.Group : JValue.CreateNull(),
                    ["name"] = scope.Group >= 0 ? (JToken)scope.GroupName : JValue.CreateNull(),
                    ["undoable"] = scope.Undoable,
                };
                string note = scope.Note ?? NoteFor(scope.Undoable);
                if (note != null) undo["note"] = note;

                var annotated = Annotate(result, undo, scope.Warnings, out bool ok);
                if (error != null) ok = false;

                var line = new JObject
                {
                    ["ts"] = DateTime.UtcNow.ToString("o"),
                    ["action_id"] = scope.ActionId,
                    ["tool"] = scope.Tool,
                    ["action"] = scope.Action,
                    ["undo_group"] = undo["group"],
                    ["undo_name"] = undo["name"],
                    ["undoable"] = scope.Undoable,
                    ["prefab_warnings"] = new JArray(scope.Warnings),
                    ["ok"] = ok,
                };
                Remember(line);
                AppendLog(line);
                return annotated;
            }
            catch (Exception)
            {
                return result;
            }
        }

        static string NoteFor(JToken undoable)
        {
            if (undoable.Type == JTokenType.Boolean && !(bool)undoable)
                return "this action changed files or used APIs outside Unity Undo; undo cannot revert it";
            if (undoable.Type == JTokenType.String)
                return "undo reverts the scene/object changes; file or project-settings changes, if any, stay";
            return null;
        }

        static object Annotate(object result, JObject undo, List<string> warnings, out bool ok)
        {
            ok = true;
            if (result == null) return null;
            JObject obj;
            try
            {
                obj = result as JObject ?? JToken.FromObject(result) as JObject;
            }
            catch (Exception)
            {
                ok = !(result is IMcpResponse r) || r.Success;
                return result;
            }
            if (obj == null) return result;

            ok = obj.Value<bool?>("success") ?? true;
            obj["undo"] = undo;
            if (warnings.Count > 0)
            {
                if (obj["warnings"] is JArray existing)
                {
                    foreach (var w in warnings) existing.Add(w);
                }
                else if (obj["warnings"] == null)
                {
                    obj["warnings"] = new JArray(warnings);
                }
            }
            return obj;
        }

        static void Remember(JObject line)
        {
            string id = line.Value<string>("action_id");
            if (id == null || Remembered.ContainsKey(id)) return;
            Remembered[id] = line;
            RememberedOrder.Enqueue(id);
            while (RememberedOrder.Count > MaxRemembered)
            {
                Remembered.Remove(RememberedOrder.Dequeue());
            }
        }

        internal static void AppendLog(JObject line)
        {
            try
            {
                string path = LogPath;
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                var info = new FileInfo(path);
                if (info.Exists && info.Length > MaxLogBytes)
                {
                    string rotated = path + ".1";
                    if (File.Exists(rotated)) File.Delete(rotated);
                    File.Move(path, rotated);
                }
                File.AppendAllText(path, line.ToString(Formatting.None) + "\n");
            }
            catch (Exception)
            {
                // The log must never break the command it describes.
            }
        }

        /// <summary>The logged entry for an action, from memory or (after a domain reload) the log file.</summary>
        internal static JObject FindAction(string actionId, int? group)
        {
            bool Matches(JObject e) =>
                (actionId != null && e.Value<string>("action_id") == actionId)
                || (actionId == null && group.HasValue && e.Value<int?>("undo_group") == group);

            if (actionId != null && Remembered.TryGetValue(actionId, out var hit)) return hit;
            if (actionId == null)
            {
                JObject latest = null;
                foreach (var e in Remembered.Values) if (Matches(e)) latest = e;
                if (latest != null) return latest;
            }
            try
            {
                string path = LogPath;
                if (!File.Exists(path)) return null;
                var lines = File.ReadAllLines(path);
                for (int i = lines.Length - 1; i >= 0; i--)
                {
                    if (string.IsNullOrWhiteSpace(lines[i])) continue;
                    JObject e;
                    try { e = JObject.Parse(lines[i]); } catch (JsonException) { continue; }
                    if (e.Value<string>("tool") != null && e["undo_name"] != null && Matches(e)) return e;
                }
            }
            catch (Exception) { }
            return null;
        }

        static MethodInfo _getRecords;

        /// <summary>
        /// Whether the undo stack still holds a group named <paramref name="name"/>; null when
        /// Unity's internal Undo.GetRecords(List, List) is unavailable in this version.
        /// </summary>
        internal static bool? UndoStackContains(string name)
        {
            try
            {
                _getRecords ??= typeof(Undo).GetMethod("GetRecords", BindingFlags.Static | BindingFlags.NonPublic,
                    null, new[] { typeof(List<string>), typeof(List<string>) }, null);
                if (_getRecords == null) return null;
                var undoRecords = new List<string>();
                var redoRecords = new List<string>();
                _getRecords.Invoke(null, new object[] { undoRecords, redoRecords });
                return undoRecords.Contains(name);
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// Undoes one agent action, but only while it is the latest undo step: reverting further
        /// down the stack would also revert whatever the user did after it.
        /// </summary>
        internal static object UndoAction(string actionId, int? group)
        {
            if (string.IsNullOrEmpty(actionId) && !group.HasValue)
                return new ErrorResponse("undo_action needs 'action_id' (or 'undo_group') from a previous response's 'undo' object.");
            if (EditorApplication.isPlaying)
                return new ErrorResponse("undo_action is not available in play mode; exit play mode first.");

            var entry = FindAction(string.IsNullOrEmpty(actionId) ? null : actionId, group);
            string label = !string.IsNullOrEmpty(actionId) ? $"action_id '{actionId}'" : $"undo_group {group}";
            if (entry == null)
                return new ErrorResponse($"No recorded MCP action with {label}.");

            string id = entry.Value<string>("action_id");
            string name = entry.Value<string>("undo_name");
            var undoable = entry["undoable"];
            string what = $"{entry.Value<string>("tool")} {entry.Value<string>("action")}".Trim();

            if (undoable != null && undoable.Type == JTokenType.Boolean && !(bool)undoable)
                return new ErrorResponse($"Action {id} ({what}) cannot be undone: it changed files or used APIs outside Unity Undo.");
            if (string.IsNullOrEmpty(name))
                return new ErrorResponse($"Action {id} ({what}) has no undo group (it ran in play mode).");

            string latest = Undo.GetCurrentGroupName();
            bool? onStack = UndoStackContains(name);
            bool stillApplied = onStack ?? !UndoneIds.Contains(id);
            if (latest != name || !stillApplied)
            {
                string reason = !stillApplied
                    ? "it has already been undone"
                    : $"it is not the most recent undo step (the latest is '{latest}')";
                return new ErrorResponse(
                    $"Refusing to undo action {id} ({what}): {reason}. undo_action only reverts the latest step so later edits are never lost; "
                    + "use manage_editor action='undo' to step back one group at a time, or Unity's Edit > Undo.");
            }

            Undo.PerformUndo();
            UndoneIds.Add(id);
            AppendLog(new JObject
            {
                ["ts"] = DateTime.UtcNow.ToString("o"),
                ["tool"] = "manage_editor",
                ["action"] = "undo_action",
                ["target_action_id"] = id,
                ["ok"] = true,
            });

            bool partial = undoable != null && undoable.Type == JTokenType.String;
            return new SuccessResponse($"Undid {name}.", new
            {
                action_id = id,
                undone_group = name,
                undo_group = entry["undo_group"]?.Type == JTokenType.Integer ? entry.Value<int?>("undo_group") : null,
                tool = entry.Value<string>("tool"),
                action = entry.Value<string>("action"),
                undoable,
                note = partial ? "scene/object changes were reverted; file or project-settings changes, if any, were not" : null,
                next_group = Undo.GetCurrentGroupName(),
            });
        }
    }
}
