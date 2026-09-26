using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditorInternal; // Required for tag management
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Handles editor control actions including play mode control, tool selection,
    /// and tag/layer management. For reading editor state, use MCP resources instead.
    /// </summary>
    [McpForUnityTool("manage_editor", AutoRegister = false)]
    public static class ManageEditor
    {
        // Constant for starting user layer index
        private const int FirstUserLayerIndex = 8;

        // Constant for total layer count
        private const int TotalLayerCount = 32;

        /// <summary>
        /// Main handler for editor management actions.
        /// </summary>
        public static object HandleCommand(JObject @params)
        {
            // Step 1: Null parameter guard (consistent across all tools)
            if (@params == null)
            {
                return new ErrorResponse("Parameters cannot be null.");
            }

            // Step 2: Wrap parameters
            var p = new ToolParams(@params);

            // Step 3: Extract and validate required parameters
            var actionResult = p.GetRequired("action");
            if (!actionResult.IsSuccess)
            {
                return new ErrorResponse(actionResult.ErrorMessage);
            }
            string action = actionResult.Value.ToLowerInvariant();

            // Parameters for specific actions
            string tagName = p.Get("tagName");
            string layerName = p.Get("layerName");
            // Route action
            switch (action)
            {
                // Play Mode Control
                case "play":
                    try
                    {
                        if (!EditorApplication.isPlaying)
                        {
                            EditorApplication.isPlaying = true;
                            return new SuccessResponse("Entered play mode.");
                        }
                        return new SuccessResponse("Already in play mode.");
                    }
                    catch (Exception e)
                    {
                        return new ErrorResponse($"Error entering play mode: {e.Message}");
                    }
                case "pause":
                    try
                    {
                        if (EditorApplication.isPlaying)
                        {
                            EditorApplication.isPaused = !EditorApplication.isPaused;
                            return new SuccessResponse(
                                EditorApplication.isPaused ? "Game paused." : "Game resumed."
                            );
                        }
                        return new ErrorResponse("Cannot pause/resume: Not in play mode.");
                    }
                    catch (Exception e)
                    {
                        return new ErrorResponse($"Error pausing/resuming game: {e.Message}");
                    }
                case "stop":
                    try
                    {
                        if (EditorApplication.isPlaying)
                        {
                            EditorApplication.isPlaying = false;
                            return new SuccessResponse("Exited play mode.");
                        }
                        return new SuccessResponse("Already stopped (not in play mode).");
                    }
                    catch (Exception e)
                    {
                        return new ErrorResponse($"Error stopping play mode: {e.Message}");
                    }

                // Tool Control
                case "set_active_tool":
                    var toolNameResult = p.GetRequired("toolName", "'toolName' parameter required for set_active_tool.");
                    if (!toolNameResult.IsSuccess)
                        return new ErrorResponse(toolNameResult.ErrorMessage);
                    return SetActiveTool(toolNameResult.Value);

                // Tag Management
                case "add_tag":
                    var addTagResult = p.GetRequired("tagName", "'tagName' parameter required for add_tag.");
                    if (!addTagResult.IsSuccess)
                        return new ErrorResponse(addTagResult.ErrorMessage);
                    return AddTag(addTagResult.Value);
                case "remove_tag":
                    var removeTagResult = p.GetRequired("tagName", "'tagName' parameter required for remove_tag.");
                    if (!removeTagResult.IsSuccess)
                        return new ErrorResponse(removeTagResult.ErrorMessage);
                    return RemoveTag(removeTagResult.Value);
                // Layer Management
                case "add_layer":
                    var addLayerResult = p.GetRequired("layerName", "'layerName' parameter required for add_layer.");
                    if (!addLayerResult.IsSuccess)
                        return new ErrorResponse(addLayerResult.ErrorMessage);
                    return AddLayer(addLayerResult.Value);
                case "remove_layer":
                    var removeLayerResult = p.GetRequired("layerName", "'layerName' parameter required for remove_layer.");
                    if (!removeLayerResult.IsSuccess)
                        return new ErrorResponse(removeLayerResult.ErrorMessage);
                    return RemoveLayer(removeLayerResult.Value);
                // --- Settings (Example) ---
                // case "set_resolution":
                //     int? width = @params["width"]?.ToObject<int?>();
                //     int? height = @params["height"]?.ToObject<int?>();
                //     if (!width.HasValue || !height.HasValue) return new ErrorResponse("'width' and 'height' parameters required.");
                //     return SetGameViewResolution(width.Value, height.Value);
                // case "set_quality":
                //     // Handle string name or int index
                //     return SetQualityLevel(@params["qualityLevel"]);

                // Package Deployment
                case "deploy_package":
                    return DeployPackage();
                case "restore_package":
                    return RestorePackage();

                // Project File Sync
                case "sync_csproj":
                    return SyncCsproj();

                // Undo/Redo
                case "undo":
                {
                    string groupName = Undo.GetCurrentGroupName();
                    Undo.PerformUndo();
                    string message = string.IsNullOrEmpty(groupName)
                        ? "Undo performed (stack may be empty)."
                        : $"Undid: {groupName}";
                    if (EditorApplication.isPlaying)
                        message += " Warning: undo during play mode may have unexpected effects.";
                    return new SuccessResponse(message, new
                    {
                        undone_group = string.IsNullOrEmpty(groupName) ? (string)null : groupName,
                        next_group = Undo.GetCurrentGroupName()
                    });
                }
                case "undo_action":
                    return McpActionJournal.UndoAction(p.Get("action_id"), p.GetInt("undo_group"));
                case "redo":
                {
                    Undo.PerformRedo();
                    string nextGroup = Undo.GetCurrentGroupName();
                    string message = "Redo performed.";
                    if (EditorApplication.isPlaying)
                        message += " Warning: redo during play mode may have unexpected effects.";
                    return new SuccessResponse(message, new
                    {
                        current_group = string.IsNullOrEmpty(nextGroup) ? (string)null : nextGroup
                    });
                }

                default:
                    return new ErrorResponse(
                        $"Unknown action: '{action}'. Supported actions: play, pause, stop, set_active_tool, add_tag, remove_tag, add_layer, remove_layer, deploy_package, restore_package, sync_csproj, undo, undo_action, redo. For prefab editing (open/save/close prefab stage), use manage_prefabs. Use MCP resources for reading editor state, project info, tags, layers, selection, windows, prefab stage, and active tool."
                    );
            }
        }

        // --- Tool Control Methods ---

        private static object SetActiveTool(string toolName)
        {
            try
            {
                Tool targetTool;
                if (Enum.TryParse<Tool>(toolName, true, out targetTool)) // Case-insensitive parse
                {
                    // Check if it's a valid built-in tool
                    if (targetTool != Tool.None && targetTool <= Tool.Custom) // Tool.Custom is the last standard tool
                    {
                        UnityEditor.Tools.current = targetTool;
                        return new SuccessResponse($"Set active tool to '{targetTool}'.");
                    }
                    else
                    {
                        return new ErrorResponse(
                            $"Cannot directly set tool to '{toolName}'. It might be None, Custom, or invalid."
                        );
                    }
                }
                else
                {
                    // Potentially try activating a custom tool by name here if needed
                    // This often requires specific editor scripting knowledge for that tool.
                    return new ErrorResponse(
                        $"Could not parse '{toolName}' as a standard Unity Tool (View, Move, Rotate, Scale, Rect, Transform, Custom)."
                    );
                }
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error setting active tool: {e.Message}");
            }
        }

        // --- Tag Management Methods ---

        private static object AddTag(string tagName)
        {
            if (string.IsNullOrWhiteSpace(tagName))
                return new ErrorResponse("Tag name cannot be empty or whitespace.");

            // Check if tag already exists
            if (System.Linq.Enumerable.Contains(InternalEditorUtility.tags, tagName))
            {
                return new ErrorResponse($"Tag '{tagName}' already exists.");
            }

            try
            {
                // Add the tag using the internal utility
                InternalEditorUtility.AddTag(tagName);
                // Force save assets to ensure the change persists in the TagManager asset
                AssetDatabase.SaveAssets();
                return new SuccessResponse($"Tag '{tagName}' added successfully.");
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to add tag '{tagName}': {e.Message}");
            }
        }

        private static object RemoveTag(string tagName)
        {
            if (string.IsNullOrWhiteSpace(tagName))
                return new ErrorResponse("Tag name cannot be empty or whitespace.");
            if (tagName.Equals("Untagged", StringComparison.OrdinalIgnoreCase))
                return new ErrorResponse("Cannot remove the built-in 'Untagged' tag.");

            // Check if tag exists before attempting removal
            if (!System.Linq.Enumerable.Contains(InternalEditorUtility.tags, tagName))
            {
                return new ErrorResponse($"Tag '{tagName}' does not exist.");
            }

            try
            {
                // Remove the tag using the internal utility
                InternalEditorUtility.RemoveTag(tagName);
                // Force save assets
                AssetDatabase.SaveAssets();
                return new SuccessResponse($"Tag '{tagName}' removed successfully.");
            }
            catch (Exception e)
            {
                // Catch potential issues if the tag is somehow in use or removal fails
                return new ErrorResponse($"Failed to remove tag '{tagName}': {e.Message}");
            }
        }

        // --- Layer Management Methods ---

        private static object AddLayer(string layerName)
        {
            if (string.IsNullOrWhiteSpace(layerName))
                return new ErrorResponse("Layer name cannot be empty or whitespace.");

            // Access the TagManager asset
            SerializedObject tagManager = GetTagManager();
            if (tagManager == null)
                return new ErrorResponse("Could not access TagManager asset.");

            SerializedProperty layersProp = tagManager.FindProperty("layers");
            if (layersProp == null || !layersProp.isArray)
                return new ErrorResponse("Could not find 'layers' property in TagManager.");

            // Check if layer name already exists (case-insensitive check recommended)
            for (int i = 0; i < TotalLayerCount; i++)
            {
                SerializedProperty layerSP = layersProp.GetArrayElementAtIndex(i);
                if (
                    layerSP != null
                    && layerName.Equals(layerSP.stringValue, StringComparison.OrdinalIgnoreCase)
                )
                {
                    return new ErrorResponse($"Layer '{layerName}' already exists at index {i}.");
                }
            }

            // Find the first empty user layer slot (indices 8 to 31)
            int firstEmptyUserLayer = -1;
            for (int i = FirstUserLayerIndex; i < TotalLayerCount; i++)
            {
                SerializedProperty layerSP = layersProp.GetArrayElementAtIndex(i);
                if (layerSP != null && string.IsNullOrEmpty(layerSP.stringValue))
                {
                    firstEmptyUserLayer = i;
                    break;
                }
            }

            if (firstEmptyUserLayer == -1)
            {
                return new ErrorResponse("No empty User Layer slots available (8-31 are full).");
            }

            // Assign the name to the found slot
            try
            {
                SerializedProperty targetLayerSP = layersProp.GetArrayElementAtIndex(
                    firstEmptyUserLayer
                );
                targetLayerSP.stringValue = layerName;
                // Apply the changes to the TagManager asset
                tagManager.ApplyModifiedProperties();
                // Save assets to make sure it's written to disk
                AssetDatabase.SaveAssets();
                return new SuccessResponse(
                    $"Layer '{layerName}' added successfully to slot {firstEmptyUserLayer}."
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to add layer '{layerName}': {e.Message}");
            }
        }

        private static object RemoveLayer(string layerName)
        {
            if (string.IsNullOrWhiteSpace(layerName))
                return new ErrorResponse("Layer name cannot be empty or whitespace.");

            // Access the TagManager asset
            SerializedObject tagManager = GetTagManager();
            if (tagManager == null)
                return new ErrorResponse("Could not access TagManager asset.");

            SerializedProperty layersProp = tagManager.FindProperty("layers");
            if (layersProp == null || !layersProp.isArray)
                return new ErrorResponse("Could not find 'layers' property in TagManager.");

            // Find the layer by name (must be user layer)
            int layerIndexToRemove = -1;
            for (int i = FirstUserLayerIndex; i < TotalLayerCount; i++) // Start from user layers
            {
                SerializedProperty layerSP = layersProp.GetArrayElementAtIndex(i);
                // Case-insensitive comparison is safer
                if (
                    layerSP != null
                    && layerName.Equals(layerSP.stringValue, StringComparison.OrdinalIgnoreCase)
                )
                {
                    layerIndexToRemove = i;
                    break;
                }
            }

            if (layerIndexToRemove == -1)
            {
                return new ErrorResponse($"User layer '{layerName}' not found.");
            }

            // Clear the name for that index
            try
            {
                SerializedProperty targetLayerSP = layersProp.GetArrayElementAtIndex(
                    layerIndexToRemove
                );
                targetLayerSP.stringValue = string.Empty; // Set to empty string to remove
                // Apply the changes
                tagManager.ApplyModifiedProperties();
                // Save assets
                AssetDatabase.SaveAssets();
                return new SuccessResponse(
                    $"Layer '{layerName}' (slot {layerIndexToRemove}) removed successfully."
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to remove layer '{layerName}': {e.Message}");
            }
        }

        // --- Package Deployment Methods ---

        private static object DeployPackage()
        {
            try
            {
                var result = MCPServiceLocator.Deployment.DeployFromStoredSource();
                if (!result.Success)
                    return new ErrorResponse(result.Message);

                return new SuccessResponse(result.Message, new
                {
                    source_path = result.SourcePath,
                    target_path = result.TargetPath,
                    backup_path = result.BackupPath
                });
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Deploy failed: {e.Message}");
            }
        }

        private static object RestorePackage()
        {
            try
            {
                var result = MCPServiceLocator.Deployment.RestoreLastBackup();
                if (!result.Success)
                    return new ErrorResponse(result.Message);

                return new SuccessResponse(result.Message, new
                {
                    target_path = result.TargetPath,
                    backup_path = result.BackupPath
                });
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Restore failed: {e.Message}");
            }
        }

        // --- Project File Sync ---

        /// <summary>
        /// Regenerates the external-editor .sln/.csproj files. These are the only source
        /// of truth for C# code intelligence: an editor that reads them can only resolve
        /// symbols in files they list.
        ///
        /// This deliberately does NOT call CodeEditor.CurrentCodeEditor.SyncAll(). When no
        /// external IDE is installed on the machine, CurrentCodeEditor falls back to
        /// DefaultExternalCodeEditor, whose SyncAll() body is a single `ret` — verified
        /// against UnityEditor.CoreModule IL on 2026-07-27. That path reports success while
        /// writing nothing, which is exactly how a project's csproj files silently stayed
        /// four days stale while Unity kept compiling. UnityEditor.SyncVS.SyncSolution(),
        /// CodeEditorProjectSync and the "Assets/Open C# Project" menu item all funnel into
        /// the same no-op, so none of them is a usable alternative.
        ///
        /// Instead a project generator shipped with one of Unity's IDE packages is driven
        /// directly. Reflection is used on purpose: a direct reference would break
        /// compilation of this tool whenever the package is absent.
        ///
        /// Two details below are load-bearing and both were established by live measurement
        /// on 2026-07-27, after a wrong guess at each of them cost a round trip.
        ///
        /// First, which type. Microsoft.Unity.VisualStudio.Editor.ProjectGeneration is a
        /// TEMPLATE base class, not a usable generator: its GetProjectHeader assigns
        /// `headerBuilder = default` and ProjectText then dereferences that null. Driving it
        /// writes the .sln and throws NullReferenceException before writing a single .csproj.
        /// Only the concrete subclasses are usable. SdkStyleProjectGeneration is one, but it
        /// deletes the .sln and replaces it with .slnx, so it is not used here.
        ///
        /// Second, which method. Sync() first asks every AssetPostprocessor whether it
        /// already generated the projects and skips generation entirely if any says yes.
        /// com.unity.ide.rider answers yes whenever Unity's stored editor path merely *looks*
        /// like Rider — it checks `FileInfo(path).Name.StartsWith("rider")` with no existence
        /// check. On a machine where Rider was uninstalled but the preference still points at
        /// it, that is a dead lock: Rider vetoes everyone else while never generating
        /// anything itself. Rider's own generator is immune because its Sync() raises the
        /// flag that disables that veto, so it is tried first; the Visual Studio one is
        /// reached through GenerateAndWriteSolutionAndProjects(), which contains no veto
        /// check. Neither needs any setup beyond the constructor.
        ///
        /// Registering this application as Unity's external script editor would also work,
        /// but is deliberately not done — that preference belongs to the user, and someone
        /// merely trying the app out should not have their Unity setup changed.
        /// </summary>
        private static object SyncCsproj()
        {
            // Ordered candidates: assembly-qualified type, then the method that writes files.
            var candidates = new[]
            {
                new[] { "Packages.Rider.Editor.ProjectGeneration.ProjectGeneration, Unity.Rider.Editor", "Sync" },
                new[] { "Microsoft.Unity.VisualStudio.Editor.LegacyStyleProjectGeneration, Unity.VisualStudio.Editor", "GenerateAndWriteSolutionAndProjects" },
            };

            var attempts = new List<string>();
            foreach (var candidate in candidates)
            {
                var type = Type.GetType(candidate[0]);
                if (type == null)
                {
                    attempts.Add($"{candidate[0]}: type not found");
                    continue;
                }

                var method = type.GetMethod(
                    candidate[1], BindingFlags.Public | BindingFlags.Instance, null, Type.EmptyTypes, null);
                if (method == null)
                {
                    attempts.Add($"{candidate[0]}: {candidate[1]}() not found");
                    continue;
                }

                try
                {
                    method.Invoke(Activator.CreateInstance(type), null);
                }
                catch (Exception e)
                {
                    // Reflection wraps whatever the generator throws; the inner exception is
                    // the one worth reporting, and the failing frame is the only thing that
                    // says which assumption of a package we do not own was broken.
                    var cause = (e as TargetInvocationException)?.InnerException ?? e;
                    attempts.Add($"{candidate[0]}: {cause.Message} @ {cause.StackTrace?.Split('\n')[0]?.Trim()}");
                    continue;
                }

                // The count is reported back so the caller can tell a real regeneration from
                // a silent no-op. The previous implementation could not, and that is the
                // whole reason this failure went unnoticed for days.
                var projectDirectory = Directory.GetParent(Application.dataPath)?.FullName;
                var csprojCount = projectDirectory == null
                    ? 0
                    : Directory.GetFiles(projectDirectory, "*.csproj").Length;

                return new SuccessResponse(
                    $"Project files regenerated ({csprojCount} csproj).",
                    new { generator = type.FullName, csproj_count = csprojCount });
            }

            return new ErrorResponse(
                "Project file regeneration failed: no usable generator.",
                data: new { attempts });
        }

        // --- Helper Methods ---

        /// <summary>
        /// Gets the SerializedObject for the TagManager asset.
        /// </summary>
        private static SerializedObject GetTagManager()
        {
            try
            {
                // Load the TagManager asset from the ProjectSettings folder
                UnityEngine.Object[] tagManagerAssets = AssetDatabase.LoadAllAssetsAtPath(
                    "ProjectSettings/TagManager.asset"
                );
                if (tagManagerAssets == null || tagManagerAssets.Length == 0)
                {
                    McpLog.Error("[ManageEditor] TagManager.asset not found in ProjectSettings.");
                    return null;
                }
                // The first object in the asset file should be the TagManager
                return new SerializedObject(tagManagerAssets[0]);
            }
            catch (Exception e)
            {
                McpLog.Error($"[ManageEditor] Error accessing TagManager.asset: {e.Message}");
                return null;
            }
        }

        // --- Example Implementations for Settings ---
        /*
        private static object SetGameViewResolution(int width, int height) { ... }
        private static object SetQualityLevel(JToken qualityLevelToken) { ... }
        */
    }
}
