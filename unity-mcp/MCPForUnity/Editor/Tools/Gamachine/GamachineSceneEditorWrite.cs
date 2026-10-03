using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.Gamachine
{
    internal static class GamachineSceneEditorWriter
    {
        internal static List<JObject> CreateMenu()
        {
            var items = new List<JObject>();
            foreach (string item in Unsupported.GetSubmenus("GameObject"))
            {
                var segments = item.Split('/');
                bool topLevel = item == "GameObject/Create Empty" || item == "GameObject/Camera";
                if ((!topLevel && segments.Length < 3) || item.EndsWith("...", StringComparison.Ordinal))
                    continue;
                items.Add(new JObject
                {
                    ["item"] = item,
                    ["category"] = topLevel ? "" : segments[1],
                    ["label"] = topLevel ? segments[1] : string.Join("/", segments, 2, segments.Length - 2)
                });
            }
            return items;
        }

        internal static string Ready() =>
            EditorApplication.isCompiling || EditorApplication.isUpdating ? "compiling" : null;

        internal static string Resolve(JToken token, out GameObject go)
        {
            go = null;
            string error = Ready();
            if (error != null) return error;
            go = GamachineSceneEditorReader.Find(token);
            if (go == null) return "not_found";
            return GamachineSceneEditorReader.Hidden(go) || (go.hideFlags & HideFlags.NotEditable) != 0
                ? "locked" : null;
        }

        internal static HashSet<int> Roots()
        {
            var roots = new HashSet<int>();
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.isLoaded) continue;
                foreach (var go in scene.GetRootGameObjects()) roots.Add(go.GetInstanceIDCompat());
            }
            return roots;
        }

        internal static HashSet<int> Objects()
        {
            var objects = new HashSet<int>();
            foreach (var go in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsInactive.Include, FindObjectsSortMode.None))
                objects.Add(go.GetInstanceIDCompat());
            return objects;
        }

        internal static object Run(string failureCode, Func<UndoStep, object> command)
        {
            var step = new UndoStep();
            try
            {
                return command(step);
            }
            catch (Exception)
            {
                try { step.Revert(); }
                catch (Exception) { }
                return new ErrorResponse(failureCode);
            }
        }

        internal sealed class UndoStep
        {
            private int? group;
            private string name;
            private UnityEngine.Object[] selection;

            internal void Begin(string groupName)
            {
                selection = Selection.objects;
                Undo.FlushUndoRecordObjects();
                Undo.IncrementCurrentGroup();
                group = Undo.GetCurrentGroup();
                name = groupName;
                Undo.SetCurrentGroupName(name);
            }

            internal void Complete()
            {
                Undo.FlushUndoRecordObjects();
                Undo.SetCurrentGroupName(name);
                Undo.CollapseUndoOperations(group.Value);
                Undo.IncrementCurrentGroup();
                group = null;
            }

            internal void Revert()
            {
                if (!group.HasValue) return;
                try
                {
                    Undo.RevertAllDownToGroup(group.Value);
                    Selection.objects = selection;
                }
                finally
                {
                    group = null;
                    Undo.IncrementCurrentGroup();
                }
            }

            internal object Fail()
            {
                Revert();
                return new ErrorResponse("create_failed");
            }
        }
    }

    [McpForUnityResource("gm_editor_create_menu")]
    public static class GamachineEditorCreateMenu
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run(
            "create_failed", step => new SuccessResponse("Create menu read.", new
            {
                items = GamachineSceneEditorWriter.CreateMenu()
            }));
    }

    [McpForUnityResource("gm_editor_create")]
    public static class GamachineEditorCreate
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("create_failed", step =>
        {
            string error = GamachineSceneEditorWriter.Ready();
            if (error != null) return new ErrorResponse(error);
            var itemToken = @params?["item"];
            string item = itemToken?.Type == JTokenType.String ? itemToken.Value<string>() : null;
            JObject entry = GamachineSceneEditorWriter.CreateMenu().Find(candidate => candidate.Value<string>("item") == item);
            if (entry == null) return new ErrorResponse("invalid_item");
            GameObject parent = null;
            var parentId = @params?["parentId"];
            if (parentId != null && parentId.Type != JTokenType.Null)
            {
                error = GamachineSceneEditorWriter.Resolve(parentId, out parent);
                if (error != null) return new ErrorResponse(error);
            }

            var rootsBefore = GamachineSceneEditorWriter.Roots();
            var objectsBefore = GamachineSceneEditorWriter.Objects();
            string label = entry.Value<string>("label");
            string groupName = "Gamachine: Create " + label.Substring(label.LastIndexOf('/') + 1);
            step.Begin(groupName);
            Selection.activeGameObject = parent;
            if (!EditorApplication.ExecuteMenuItem(item)) return step.Fail();
            var created = Selection.activeGameObject;
            if (created == null || objectsBefore.Contains(created.GetInstanceIDCompat())) return step.Fail();

            // UI items already honour a selected Canvas parent (measured 3 Oct); the rest land at a new root.
            bool placed = parent != null && created.transform.IsChildOf(parent.transform);
            if (parent != null && !placed)
            {
                var createdRoots = GamachineSceneEditorWriter.Roots();
                createdRoots.ExceptWith(rootsBefore);
                if (!createdRoots.Contains(created.transform.root.gameObject.GetInstanceIDCompat())) return step.Fail();
                var createdRoot = created.transform.root;
                bool replaceCanvas = parent.GetComponentInParent<Canvas>(true) != null
                    && createdRoot.GetComponent<Canvas>() != null;
                // Menus create scene roots; move that root so UI scaffolding stays intact.
                var moved = replaceCanvas ? created.transform : createdRoot;
                Undo.SetTransformParent(moved, parent.transform, false, groupName);
                Undo.RecordObject(moved, groupName);
                if (!(moved is RectTransform))
                {
                    moved.localPosition = Vector3.zero;
                    moved.localRotation = Quaternion.identity;
                }
                moved.SetAsLastSibling();
                if (replaceCanvas) Undo.DestroyObjectImmediate(createdRoot.gameObject);
            }
            Selection.activeGameObject = created;
            var response = new SuccessResponse("Scene object created.", new { id = created.GetInstanceIDCompat(), name = created.name });
            step.Complete();
            return response;
        });
    }

    [McpForUnityResource("gm_editor_rename")]
    public static class GamachineEditorRename
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("write_failed", step =>
        {
            string error = GamachineSceneEditorWriter.Resolve(@params?["id"], out var go);
            if (error != null) return new ErrorResponse(error);
            var token = @params?["name"];
            string name = token?.Type == JTokenType.String ? token.Value<string>().Trim() : "";
            if (name.Length == 0 || name.Length > 256) return new ErrorResponse("invalid_name");
            foreach (char character in name)
                if (char.IsControl(character)) return new ErrorResponse("invalid_name");
            string groupName = "Gamachine: Rename " + go.name;
            step.Begin(groupName);
            // RecordObject omits unchanged values; even a no-op call needs its own step.
            if (go.name == name) Undo.RegisterCompleteObjectUndo(go, groupName);
            Undo.RecordObject(go, groupName);
            go.name = name;
            PrefabUtility.RecordPrefabInstancePropertyModifications(go);
            var response = new SuccessResponse("Scene object renamed.", new { id = go.GetInstanceIDCompat(), name = go.name });
            step.Complete();
            return response;
        });
    }

    [McpForUnityResource("gm_editor_set_active")]
    public static class GamachineEditorSetActive
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("write_failed", step =>
        {
            string error = GamachineSceneEditorWriter.Resolve(@params?["id"], out var go);
            if (error != null) return new ErrorResponse(error);
            var token = @params?["active"];
            if (token?.Type != JTokenType.Boolean) return new ErrorResponse("invalid_value");
            bool active = token.Value<bool>();
            string groupName = "Gamachine: " + (active ? "Activate " : "Deactivate ") + go.name;
            step.Begin(groupName);
            if (go.activeSelf == active) Undo.RegisterCompleteObjectUndo(go, groupName);
            Undo.RecordObject(go, groupName);
            go.SetActive(active);
            PrefabUtility.RecordPrefabInstancePropertyModifications(go);
            var response = new SuccessResponse("Scene object activity set.", new
            {
                id = go.GetInstanceIDCompat(), activeSelf = go.activeSelf, activeInHierarchy = go.activeInHierarchy
            });
            step.Complete();
            return response;
        });
    }

    [McpForUnityResource("gm_editor_duplicate")]
    public static class GamachineEditorDuplicate
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("create_failed", step =>
        {
            string error = GamachineSceneEditorWriter.Resolve(@params?["id"], out var go);
            if (error != null) return new ErrorResponse(error);
            string groupName = "Gamachine: Duplicate " + go.name;
            step.Begin(groupName);
            Selection.objects = new UnityEngine.Object[] { go };
            Unsupported.DuplicateGameObjectsUsingPasteboard();
            var duplicate = Selection.activeGameObject;
            if (duplicate == null || duplicate == go) return step.Fail();
            var response = new SuccessResponse("Scene object duplicated.", new { id = duplicate.GetInstanceIDCompat(), name = duplicate.name });
            step.Complete();
            return response;
        });
    }

    [McpForUnityResource("gm_editor_delete")]
    public static class GamachineEditorDelete
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("write_failed", step =>
        {
            string error = GamachineSceneEditorWriter.Resolve(@params?["id"], out var go);
            if (error != null) return new ErrorResponse(error);
            if (PrefabUtility.IsPartOfPrefabInstance(go) && !PrefabUtility.IsOutermostPrefabInstanceRoot(go))
                return new ErrorResponse("prefab_part");
            int id = go.GetInstanceIDCompat();
            var selected = Selection.activeGameObject;
            bool clearSelection = selected != null && (selected == go || selected.transform.IsChildOf(go.transform));
            step.Begin("Gamachine: Delete " + go.name);
            Undo.DestroyObjectImmediate(go);
            if (clearSelection) Selection.activeObject = null;
            var response = new SuccessResponse("Scene object deleted.", new { id });
            step.Complete();
            return response;
        });
    }
}
