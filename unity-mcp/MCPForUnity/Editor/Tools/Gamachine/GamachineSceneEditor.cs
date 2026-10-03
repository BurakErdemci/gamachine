using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.Gamachine
{
    [InitializeOnLoad]
    internal static class GamachineSceneEditorState
    {
        internal static readonly string Epoch = Guid.NewGuid().ToString("N");
        internal static int SceneVersion { get; private set; }
        internal static int HierarchyVersion { get; private set; }
        internal static int PropsVersion { get; private set; }
        internal static int SelectionVersion { get; private set; }

        static GamachineSceneEditorState()
        {
            EditorApplication.hierarchyChanged += HierarchyChanged;
            Undo.undoRedoPerformed += UndoRedoPerformed;
            ObjectChangeEvents.changesPublished += ObjectsChanged;
            Selection.selectionChanged += () => SelectionVersion = unchecked(SelectionVersion + 1);
        }

        private static void SceneChanged() => SceneVersion = unchecked(SceneVersion + 1);

        private static void HierarchyChanged()
        {
            SceneChanged();
            HierarchyVersion = unchecked(HierarchyVersion + 1);
        }

        private static void UndoRedoPerformed()
        {
            HierarchyChanged();
            PropsVersion = unchecked(PropsVersion + 1);
        }

        private static void ObjectsChanged(ref ObjectChangeEventStream stream)
        {
            SceneChanged();
            bool hierarchyChanged = false;
            bool propsChanged = false;
            try
            {
            for (int i = 0; i < stream.length; i++)
            {
                switch (stream.GetEventType(i))
                {
                    case ObjectChangeKind.ChangeScene:
                    case ObjectChangeKind.CreateGameObjectHierarchy:
                    case ObjectChangeKind.DestroyGameObjectHierarchy:
                    case ObjectChangeKind.ChangeGameObjectStructure:
                    case ObjectChangeKind.ChangeGameObjectStructureHierarchy:
                    case ObjectChangeKind.ChangeGameObjectParent:
                    case ObjectChangeKind.ChangeChildrenOrder:
                        hierarchyChanged = true;
                        break;
                    case ObjectChangeKind.ChangeGameObjectOrComponentProperties:
                        propsChanged = true;
                        stream.GetChangeGameObjectOrComponentPropertiesEvent(i, out var change);
                        // GameObject properties include the tree's name and activity fields.
                        if (!(GameObjectLookup.ResolveInstanceID(change.instanceId) is Component))
                            hierarchyChanged = true;
                        break;
                    case ObjectChangeKind.ChangeAssetObjectProperties:
                        propsChanged = true;
                        break;
                    default:
                        // Prefab updates, other structural kinds, and future kinds are conservative.
                        hierarchyChanged = true;
                        propsChanged = true;
                        break;
                }
            }
            }
            catch (Exception)
            {
                // A failed classification must not freeze the counters the app keys on (verify-f1).
                hierarchyChanged = true;
                propsChanged = true;
            }
            if (hierarchyChanged) HierarchyVersion = unchecked(HierarchyVersion + 1);
            if (propsChanged) PropsVersion = unchecked(PropsVersion + 1);
        }
    }

    // Resources remain callable by the app without tool advertisement or journal scopes.
    [McpForUnityResource("gm_editor_tree")]
    public static class GamachineEditorTree
    {
        public static object HandleCommand(JObject @params)
        {
            const int maxNodes = 20000;
            var scenes = new List<object>();
            var nodes = new List<object>();
            int total = 0;
            var activeScene = SceneManager.GetActiveScene();
            for (int sceneIndex = 0; sceneIndex < SceneManager.sceneCount; sceneIndex++)
            {
                var scene = SceneManager.GetSceneAt(sceneIndex);
                if (!scene.isLoaded) continue;
                var roots = scene.GetRootGameObjects();
                Array.Sort(roots, (left, right) =>
                    left.transform.GetSiblingIndex().CompareTo(right.transform.GetSiblingIndex()));
                var rootIds = new List<int>();
                var pending = new Stack<Transform>();
                for (int i = roots.Length - 1; i >= 0; i--) pending.Push(roots[i].transform);
                while (pending.Count > 0)
                {
                    var transform = pending.Pop();
                    var go = transform.gameObject;
                    if (GamachineSceneEditorReader.Hidden(go)) continue;
                    if (transform.parent == null) rootIds.Add(go.GetInstanceIDCompat());
                    total++;
                    if (nodes.Count < maxNodes)
                    {
                        int visibleChildren = 0;
                        for (int i = 0; i < transform.childCount; i++)
                            if (!GamachineSceneEditorReader.Hidden(transform.GetChild(i).gameObject)) visibleChildren++;
                        nodes.Add(new
                        {
                            id = go.GetInstanceIDCompat(),
                            name = go.name,
                            parentId = transform.parent != null
                                ? (int?)transform.parent.gameObject.GetInstanceIDCompat() : null,
                            index = transform.GetSiblingIndex(),
                            activeSelf = go.activeSelf,
                            activeInHierarchy = go.activeInHierarchy,
                            childCount = visibleChildren,
                            prefab = GamachineSceneEditorReader.Prefab(go),
                            scene = scene.path
                        });
                    }
                    for (int i = transform.childCount - 1; i >= 0; i--) pending.Push(transform.GetChild(i));
                }
                scenes.Add(new
                {
                    name = scene.name,
                    path = scene.path,
                    isDirty = scene.isDirty,
                    isLoaded = scene.isLoaded,
                    isActive = scene == activeScene,
                    rootIds
                });
            }
            return new SuccessResponse("Scene tree read.", new
            {
                epoch = GamachineSceneEditorState.Epoch,
                version = GamachineSceneEditorState.SceneVersion,
                scenes,
                nodes,
                total,
                truncated = total > maxNodes
            });
        }
    }

    [McpForUnityResource("gm_editor_inspect")]
    public static class GamachineEditorInspect
    {
        public static object HandleCommand(JObject @params)
        {
            var go = GamachineSceneEditorReader.Find(@params?["id"]);
            if (go == null) return new ErrorResponse("not_found");
            bool truncated = false;
            var groups = new List<object>();
            foreach (var component in go.GetComponents<Component>())
            {
                if (component == null)
                {
                    groups.Add(new
                    {
                        type = "missing",
                        label = "Missing Script",
                        componentId = (int?)null,
                        enabled = (bool?)null,
                        removable = true,
                        fields = new List<JObject>()
                    });
                    continue;
                }
                using (var serialized = new SerializedObject(component))
                {
                    serialized.Update();
                    var enabledProperty = serialized.FindProperty("m_Enabled");
                    bool? enabled = enabledProperty != null
                        && enabledProperty.propertyType == SerializedPropertyType.Boolean
                        ? (bool?)enabledProperty.boolValue : null;
                    bool locked = (component.hideFlags & HideFlags.NotEditable) != 0
                        || (go.hideFlags & HideFlags.NotEditable) != 0;
                    var fields = component is Transform transform
                        ? GamachineSceneEditorReader.TransformFields(transform, locked)
                        : GamachineSceneEditorReader.Fields(serialized, component.GetType(), locked, ref truncated);
                    groups.Add(new
                    {
                        type = component.GetType().FullName,
                        label = ObjectNames.GetInspectorTitle(component),
                        componentId = component.GetInstanceIDCompat(),
                        enabled,
                        removable = !(component is Transform) && !locked,
                        fields
                    });
                }
            }
            return new SuccessResponse("Scene object read.", new
            {
                node = new
                {
                    id = go.GetInstanceIDCompat(),
                    name = go.name,
                    activeSelf = go.activeSelf,
                    tag = go.tag,
                    layer = new { index = go.layer, name = LayerMask.LayerToName(go.layer) },
                    isStatic = go.isStatic,
                    prefab = GamachineSceneEditorReader.Prefab(go),
                    globalId = GlobalObjectId.GetGlobalObjectIdSlow(go).ToString()
                },
                groups,
                truncated
            });
        }
    }

    [McpForUnityResource("gm_editor_version")]
    public static class GamachineEditorVersion
    {
        public static object HandleCommand(JObject @params)
        {
            var selected = Selection.activeGameObject;
            return new SuccessResponse("Editor version read.", new
            {
                epoch = GamachineSceneEditorState.Epoch,
                scene = GamachineSceneEditorState.SceneVersion,
                hierarchy = GamachineSceneEditorState.HierarchyVersion,
                props = GamachineSceneEditorState.PropsVersion,
                selection = GamachineSceneEditorState.SelectionVersion,
                selectedId = selected != null ? (int?)selected.GetInstanceIDCompat() : null,
                playing = EditorApplication.isPlaying,
                compiling = EditorApplication.isCompiling
            });
        }
    }

    [McpForUnityResource("gm_editor_select")]
    public static class GamachineEditorSelect
    {
        public static object HandleCommand(JObject @params)
        {
            var token = @params?["id"];
            if (token == null) return new ErrorResponse("not_found");
            if (token.Type == JTokenType.Null)
            {
                Selection.activeObject = null;
                return new SuccessResponse("Selection cleared.", new { success = true });
            }
            var go = GamachineSceneEditorReader.Find(token);
            if (go == null) return new ErrorResponse("not_found");
            Selection.activeObject = go;
            EditorGUIUtility.PingObject(go);
            return new SuccessResponse("Scene object selected.", new { success = true });
        }
    }

    internal static class GamachineSceneEditorReader
    {
        internal static bool Hidden(GameObject go) => (go.hideFlags & HideFlags.HideInHierarchy) != 0;

        internal static GameObject Find(JToken token)
        {
            if (token == null || token.Type != JTokenType.Integer) return null;
            long value = token.Value<long>();
            if (value < int.MinValue || value > int.MaxValue) return null;
            var go = GameObjectLookup.ResolveInstanceID((int)value) as GameObject;
            return go != null && go.scene.IsValid() && go.scene.isLoaded ? go : null;
        }

        internal static string Prefab(GameObject go)
        {
            var status = PrefabUtility.GetPrefabInstanceStatus(go);
            if (status == PrefabInstanceStatus.MissingAsset) return "missing";
            if (!PrefabUtility.IsPartOfPrefabInstance(go)) return "none";
            return PrefabUtility.IsAnyPrefabInstanceRoot(go) ? "root" : "part";
        }

        internal static List<JObject> TransformFields(Transform transform, bool locked)
        {
            return new List<JObject>
            {
                Field("m_LocalPosition", "Position", "vec3", Vector(transform.localPosition), locked),
                Field("m_LocalRotation", "Rotation", "vec3", Vector(transform.localEulerAngles), locked),
                Field("m_LocalScale", "Scale", "vec3", Vector(transform.localScale), locked)
            };
        }

        private static float[] Vector(Vector3 value) => new[] { value.x, value.y, value.z };

        private static JObject Field(string path, string label, string kind, object value, bool readOnly)
        {
            return new JObject
            {
                ["path"] = path,
                ["label"] = label,
                ["kind"] = kind,
                ["value"] = value == null ? JValue.CreateNull() : JToken.FromObject(value),
                ["readonly"] = readOnly
            };
        }

        internal static List<JObject> Fields(SerializedObject serialized, Type type, bool locked, ref bool truncated)
        {
            var fields = new List<JObject>();
            var property = serialized.GetIterator();
            bool enterChildren = true;
            while (property.NextVisible(enterChildren))
            {
                enterChildren = false;
                if (property.propertyType == SerializedPropertyType.Generic && !property.isArray
                    && property.hasVisibleChildren && property.depth < 3)
                {
                    enterChildren = true;
                    continue;
                }
                if (fields.Count >= 400)
                {
                    truncated = true;
                    break;
                }
                string kind = "unsupported";
                object value = property.propertyType.ToString();
                string[] options = null;
                bool valueTruncated = false;
                if (property.isArray && property.propertyType != SerializedPropertyType.String)
                {
                    kind = "list";
                    value = property.arraySize;
                }
                else
                {
                    switch (property.propertyType)
                    {
                        case SerializedPropertyType.Float:
                            kind = "float"; value = property.doubleValue; break;
                        case SerializedPropertyType.Integer:
                            kind = "int"; value = property.longValue; break;
                        case SerializedPropertyType.Boolean:
                            kind = "bool"; value = property.boolValue; break;
                        case SerializedPropertyType.Enum:
                            kind = "enum"; value = property.enumValueIndex; options = property.enumDisplayNames; break;
                        case SerializedPropertyType.String:
                            kind = "string";
                            var text = property.stringValue ?? "";
                            if (text.Length > 2000) { text = text.Substring(0, 2000); truncated = true; valueTruncated = true; }
                            value = text;
                            break;
                        case SerializedPropertyType.Vector2:
                            kind = "vec2"; value = new[] { property.vector2Value.x, property.vector2Value.y }; break;
                        case SerializedPropertyType.Vector3:
                            kind = "vec3"; value = Vector(property.vector3Value); break;
                        case SerializedPropertyType.Vector4:
                            kind = "vec4";
                            var v4 = property.vector4Value;
                            value = new[] { v4.x, v4.y, v4.z, v4.w }; break;
                        case SerializedPropertyType.Vector2Int:
                            kind = "vec2"; value = new[] { property.vector2IntValue.x, property.vector2IntValue.y }; break;
                        case SerializedPropertyType.Vector3Int:
                            kind = "vec3";
                            var v3 = property.vector3IntValue;
                            value = new[] { v3.x, v3.y, v3.z }; break;
                        case SerializedPropertyType.Color:
                            kind = "color"; value = "#" + ColorUtility.ToHtmlStringRGBA(property.colorValue); break;
                        case SerializedPropertyType.ObjectReference:
                            kind = "ref";
                            var reference = property.objectReferenceValue;
                            value = reference == null ? null : new
                            {
                                name = reference.name,
                                type = reference.GetType().FullName,
                                id = reference.GetInstanceIDCompat()
                            };
                            break;
                        case SerializedPropertyType.LayerMask:
                            kind = "mask"; value = property.intValue;
                            options = new string[32];
                            for (int i = 0; i < options.Length; i++) options[i] = LayerMask.LayerToName(i);
                            break;
                    }
                }
                var field = Field(property.propertyPath, property.displayName, kind, value,
                    locked || !property.editable || property.propertyPath == "m_Script" || valueTruncated);
                if (valueTruncated) field["truncated"] = true;
                if (options != null) field["options"] = JArray.FromObject(options);
                if (!string.IsNullOrEmpty(property.tooltip)) field["tooltip"] = property.tooltip;
                var backingField = BackingField(type, property.propertyPath);
                var range = backingField?.GetCustomAttribute<RangeAttribute>();
                if (range != null) field["range"] = new JArray(range.min, range.max);
                fields.Add(field);
                if (property.propertyType == SerializedPropertyType.Generic && property.hasVisibleChildren)
                    truncated = true;
            }
            return fields;
        }

        private static FieldInfo BackingField(Type type, string path)
        {
            FieldInfo field = null;
            foreach (var part in path.Split('.'))
            {
                field = null;
                for (var current = type; current != null && field == null; current = current.BaseType)
                    field = current.GetField(part, BindingFlags.Instance | BindingFlags.Public
                        | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
                if (field == null) return null;
                type = field.FieldType;
            }
            return field;
        }
    }
}
