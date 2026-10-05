using System;
using System.Collections.Generic;
using System.Globalization;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditorInternal;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Gamachine
{
    internal static class GamachineSceneEditorComponents
    {
        internal static string ResolveComponent(JToken token, out Component component)
        {
            component = null;
            string error = GamachineSceneEditorWriter.Ready();
            if (error != null) return error;
            if (token?.Type != JTokenType.Integer || !Integer(token, int.MinValue, int.MaxValue, out var id))
                return "not_found";
            component = GameObjectLookup.ResolveInstanceID((int)id) as Component;
            if (component == null || EditorUtility.IsPersistent(component)
                || !component.gameObject.scene.IsValid() || !component.gameObject.scene.isLoaded)
                return "not_found";
            return GamachineSceneEditorReader.Hidden(component.gameObject)
                || (component.gameObject.hideFlags & HideFlags.NotEditable) != 0
                || (component.hideFlags & HideFlags.NotEditable) != 0 ? "locked" : null;
        }

        private static bool Number(JToken token, out double value)
        {
            value = 0;
            return token != null && (token.Type == JTokenType.Integer || token.Type == JTokenType.Float)
                && double.TryParse(token.ToString(Newtonsoft.Json.Formatting.None), NumberStyles.Float, CultureInfo.InvariantCulture, out value)
                && !double.IsNaN(value) && !double.IsInfinity(value);
        }

        internal static bool Integer(JToken token, long min, long max, out long value)
        {
            value = 0;
            if (token == null || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float))
                return false;
            if (token.Type == JTokenType.Integer)
                return long.TryParse(token.ToString(Newtonsoft.Json.Formatting.None), NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
                    && value >= min && value <= max;
            if (!Number(token, out var floating) || Math.Truncate(floating) != floating) return false;
            if (!decimal.TryParse(token.ToString(Newtonsoft.Json.Formatting.None), NumberStyles.Float, CultureInfo.InvariantCulture, out var number)
                || decimal.Truncate(number) != number || number < min || number > max)
                return false;
            value = (long)number;
            return true;
        }

        internal static bool Vector(JToken token, int length, bool integral, out double[] values)
        {
            values = new double[length];
            if (!(token is JArray array) || array.Count != length) return false;
            for (int i = 0; i < length; i++)
            {
                if (integral)
                {
                    if (!Integer(array[i], int.MinValue, int.MaxValue, out var integer)) return false;
                    values[i] = integer;
                }
                else if (!Number(array[i], out values[i]) || Math.Abs(values[i]) > float.MaxValue)
                    return false;
            }
            return true;
        }

        private static bool PropertyInteger(SerializedProperty property, JToken token, out long value)
        {
            long min;
            long max;
            switch (property.numericType)
            {
                case SerializedPropertyNumericType.Int8: min = sbyte.MinValue; max = sbyte.MaxValue; break;
                case SerializedPropertyNumericType.UInt8: min = 0; max = byte.MaxValue; break;
                case SerializedPropertyNumericType.Int16: min = short.MinValue; max = short.MaxValue; break;
                case SerializedPropertyNumericType.UInt16: min = 0; max = ushort.MaxValue; break;
                case SerializedPropertyNumericType.Int32: min = int.MinValue; max = int.MaxValue; break;
                case SerializedPropertyNumericType.UInt32: min = 0; max = uint.MaxValue; break;
                case SerializedPropertyNumericType.Int64: min = long.MinValue; max = long.MaxValue; break;
                case SerializedPropertyNumericType.UInt64: min = 0; max = long.MaxValue; break;
                default: value = 0; return false;
            }
            return Integer(token, min, max, out value);
        }

        internal static bool Setter(SerializedProperty property, JToken token, out Action apply)
        {
            apply = null;
            switch (property.propertyType)
            {
                case SerializedPropertyType.Float:
                    if (!Number(token, out var number)
                        || (property.type != "double" && Math.Abs(number) > float.MaxValue)) return false;
                    apply = () => { if (property.type == "double") property.doubleValue = number;
                                    else property.floatValue = (float)number; };
                    break;
                case SerializedPropertyType.Integer:
                    if (!PropertyInteger(property, token, out var integer)) return false;
                    apply = () => property.longValue = integer;
                    break;
                case SerializedPropertyType.Boolean:
                    if (token?.Type != JTokenType.Boolean) return false;
                    apply = () => property.boolValue = token.Value<bool>();
                    break;
                case SerializedPropertyType.Enum:
                    if (token?.Type != JTokenType.Integer
                        || !Integer(token, 0, property.enumNames.Length - 1, out var index)) return false;
                    apply = () => property.enumValueIndex = (int)index;
                    break;
                case SerializedPropertyType.String:
                    if (token?.Type != JTokenType.String || token.Value<string>().Length > 16384) return false;
                    apply = () => property.stringValue = token.Value<string>();
                    break;
                case SerializedPropertyType.Vector2:
                    if (!Vector(token, 2, false, out var v2)) return false;
                    apply = () => property.vector2Value = new Vector2((float)v2[0], (float)v2[1]);
                    break;
                case SerializedPropertyType.Vector3:
                    if (!Vector(token, 3, false, out var v3)) return false;
                    apply = () => property.vector3Value = new Vector3((float)v3[0], (float)v3[1], (float)v3[2]);
                    break;
                case SerializedPropertyType.Vector4:
                    if (!Vector(token, 4, false, out var v4)) return false;
                    apply = () => property.vector4Value = new Vector4((float)v4[0], (float)v4[1], (float)v4[2], (float)v4[3]);
                    break;
                case SerializedPropertyType.Vector2Int:
                    if (!Vector(token, 2, true, out var v2i)) return false;
                    apply = () => property.vector2IntValue = new Vector2Int((int)v2i[0], (int)v2i[1]);
                    break;
                case SerializedPropertyType.Vector3Int:
                    if (!Vector(token, 3, true, out var v3i)) return false;
                    apply = () => property.vector3IntValue = new Vector3Int((int)v3i[0], (int)v3i[1], (int)v3i[2]);
                    break;
                case SerializedPropertyType.Color:
                    if (token?.Type != JTokenType.String) return false;
                    string text = token.Value<string>();
                    if ((text.Length != 7 && text.Length != 9) || text[0] != '#'
                        || !ColorUtility.TryParseHtmlString(text, out var color)) return false;
                    apply = () => property.colorValue = color;
                    break;
                case SerializedPropertyType.LayerMask:
                    if (token?.Type != JTokenType.Integer
                        || !Integer(token, int.MinValue, uint.MaxValue, out var mask)) return false;
                    apply = () => property.intValue = mask > int.MaxValue ? unchecked((int)(uint)mask) : (int)mask;
                    break;
                default:
                    return false;
            }
            return true;
        }

        internal static JObject ReadField(Component component, string field)
        {
            if (component is Transform transform)
                return GamachineSceneEditorReader.TransformFields(transform, false)
                    .Find(entry => entry.Value<string>("path") == field);
            using var serialized = new SerializedObject(component);
            bool truncated = false;
            return GamachineSceneEditorReader.Fields(serialized, component.GetType(), false, ref truncated)
                .Find(entry => entry.Value<string>("path") == field);
        }

        internal sealed class MenuEntry
        {
            internal string Item;
            internal string Category;
            internal string Label;
            internal Type Type;
        }

        private static readonly Lazy<List<MenuEntry>> menu = new Lazy<List<MenuEntry>>(BuildMenu);
        internal static List<MenuEntry> Menu => menu.Value;

        private static void Index(Dictionary<string, List<Type>> index, string key, Type type)
        {
            if (!index.TryGetValue(key, out var types)) index[key] = types = new List<Type>();
            if (!types.Contains(type)) types.Add(type);
        }

        private static List<MenuEntry> BuildMenu()
        {
            var attributes = new Dictionary<string, List<Type>>(StringComparer.Ordinal);
            var names = new Dictionary<string, List<Type>>(StringComparer.Ordinal);
            foreach (var type in TypeCache.GetTypesDerivedFrom<Component>())
            {
                if (type.IsAbstract || type.IsGenericType || type.ContainsGenericParameters) continue;
                foreach (AddComponentMenu attribute in type.GetCustomAttributes(typeof(AddComponentMenu), false))
                    Index(attributes, "Component/" + attribute.componentMenu, type);
                Index(names, ObjectNames.NicifyVariableName(type.Name), type);
            }
            var entries = new List<MenuEntry>();
            foreach (string item in Unsupported.GetSubmenus("Component"))
            {
                var segments = item.Split('/');
                if (segments.Length < 3) continue;
                // Explicit menu attributes take precedence over the unique nicified-name fallback.
                if (!attributes.TryGetValue(item, out var types))
                    names.TryGetValue(segments[segments.Length - 1], out types);
                if (types == null || types.Count != 1) continue;
                var type = types[0];
                if (type == typeof(Transform) || type == typeof(RectTransform)) continue;
                entries.Add(new MenuEntry
                {
                    Item = item, Category = segments[1],
                    Label = string.Join("/", segments, 2, segments.Length - 2), Type = type
                });
            }
            return entries;
        }

        internal static bool Present(GameObject go, Type type)
        {
            // ComponentOps' single-instance guard uses this same inherited attribute.
            if (!Attribute.IsDefined(type, typeof(DisallowMultipleComponent), true)) return false;
            foreach (var component in go.GetComponents<Component>())
                if (component != null && component.GetType() == type) return true;
            return false;
        }

        internal static bool Required(Component component)
        {
            var type = component.GetType();
            foreach (var other in component.gameObject.GetComponents<Component>())
            {
                if (other == null || other == component) continue;
                foreach (RequireComponent requirement in other.GetType().GetCustomAttributes(typeof(RequireComponent), true))
                {
                    if ((requirement.m_Type0 != null && requirement.m_Type0.IsAssignableFrom(type))
                        || (requirement.m_Type1 != null && requirement.m_Type1.IsAssignableFrom(type))
                        || (requirement.m_Type2 != null && requirement.m_Type2.IsAssignableFrom(type))) return true;
                }
            }
            return false;
        }
    }

    [McpForUnityResource("gm_editor_set_field")]
    public static class GamachineEditorSetField
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("write_failed", step =>
        {
            string error = GamachineSceneEditorComponents.ResolveComponent(@params?["componentId"], out var component);
            if (error != null) return new ErrorResponse(error);
            if (@params?["field"]?.Type != JTokenType.String) return new ErrorResponse("invalid_value");
            string field = @params.Value<string>("field");
            string leaf = field.Substring(field.LastIndexOf('.') + 1);
            if (leaf.StartsWith("m_", StringComparison.Ordinal)) leaf = leaf.Substring(2);
            string groupName = "Gamachine: Set " + ObjectNames.NicifyVariableName(leaf);
            var value = @params["value"];
            if (component is Transform transform)
            {
                if (field != "m_LocalPosition" && field != "m_LocalRotation" && field != "m_LocalScale")
                    return new ErrorResponse("invalid_value");
                if (!GamachineSceneEditorComponents.Vector(value, 3, false, out var numbers))
                    return new ErrorResponse("invalid_value");
                var vector = new Vector3((float)numbers[0], (float)numbers[1], (float)numbers[2]);
                step.Begin(groupName);
                Undo.RegisterCompleteObjectUndo(transform, groupName);
                Undo.RecordObject(transform, groupName);
                if (field == "m_LocalPosition") transform.localPosition = vector;
                // The Inspector exposes Euler angles for this quaternion property.
                else if (field == "m_LocalRotation") transform.localEulerAngles = vector;
                else transform.localScale = vector;
            }
            else
            {
                using var serialized = new SerializedObject(component);
                var property = serialized.FindProperty(field);
                if (property == null) return new ErrorResponse("not_found");
                if (!property.editable || field == "m_Script") return new ErrorResponse("locked");
                if (property.propertyType == SerializedPropertyType.String && property.stringValue.Length > 2000)
                    return new ErrorResponse("locked");
                if (!GamachineSceneEditorComponents.Setter(property, value, out var apply))
                    return new ErrorResponse("invalid_value");
                step.Begin(groupName);
                apply();
                if (!serialized.ApplyModifiedProperties()) Undo.RegisterCompleteObjectUndo(component, groupName);
            }
            PrefabUtility.RecordPrefabInstancePropertyModifications(component);
            var response = new SuccessResponse("Component field set.", new
            {
                componentId = component.GetInstanceIDCompat(),
                field = GamachineSceneEditorComponents.ReadField(component, field)
            });
            step.Complete();
            return response;
        });
    }

    [McpForUnityResource("gm_editor_component_enable")]
    public static class GamachineEditorComponentEnable
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("write_failed", step =>
        {
            string error = GamachineSceneEditorComponents.ResolveComponent(@params?["componentId"], out var component);
            if (error != null) return new ErrorResponse(error);
            if (@params?["enabled"]?.Type != JTokenType.Boolean) return new ErrorResponse("invalid_value");
            using var serialized = new SerializedObject(component);
            var property = serialized.FindProperty("m_Enabled");
            if (property == null || property.propertyType != SerializedPropertyType.Boolean)
                return new ErrorResponse("invalid_value");
            if (!property.editable) return new ErrorResponse("locked");
            bool enabled = @params.Value<bool>("enabled");
            string groupName = "Gamachine: " + (enabled ? "Enable " : "Disable ") + ObjectNames.GetInspectorTitle(component);
            step.Begin(groupName);
            property.boolValue = enabled;
            if (!serialized.ApplyModifiedProperties()) Undo.RegisterCompleteObjectUndo(component, groupName);
            PrefabUtility.RecordPrefabInstancePropertyModifications(component);
            var response = new SuccessResponse("Component enabled state set.", new
            {
                componentId = component.GetInstanceIDCompat(), enabled = property.boolValue
            });
            step.Complete();
            return response;
        });
    }

    [McpForUnityResource("gm_editor_component_menu")]
    public static class GamachineEditorComponentMenu
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("write_failed", step =>
        {
            string error = GamachineSceneEditorWriter.Resolve(@params?["id"], out var go);
            if (error != null) return new ErrorResponse(error);
            var items = new List<JObject>();
            foreach (var entry in GamachineSceneEditorComponents.Menu)
                items.Add(new JObject
                {
                    ["item"] = entry.Item, ["category"] = entry.Category, ["label"] = entry.Label,
                    ["present"] = GamachineSceneEditorComponents.Present(go, entry.Type)
                });
            return new SuccessResponse("Component menu read.", new { items });
        });
    }

    [McpForUnityResource("gm_editor_add_component")]
    public static class GamachineEditorAddComponent
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("add_failed", step =>
        {
            string error = GamachineSceneEditorWriter.Resolve(@params?["id"], out var go);
            if (error != null) return new ErrorResponse(error);
            string item = @params?["item"]?.Type == JTokenType.String ? @params.Value<string>("item") : null;
            var entry = GamachineSceneEditorComponents.Menu.Find(candidate => candidate.Item == item);
            if (entry == null) return new ErrorResponse("invalid_item");
            if (GamachineSceneEditorComponents.Present(go, entry.Type)) return new ErrorResponse("already_present");
            string leaf = entry.Label.Substring(entry.Label.LastIndexOf('/') + 1);
            step.Begin("Gamachine: Add " + leaf);
            var component = ComponentOps.AddComponent(go, entry.Type, out error);
            if (component == null || error != null)
            {
                step.Revert();
                // Built-in single-instance types (Rigidbody, ...) carry no DisallowMultipleComponent, so Present()
                // cannot see them; Unity just refuses the second one.
                return new ErrorResponse(go.GetComponent(entry.Type) != null ? "already_present" : "add_failed");
            }
            PrefabUtility.RecordPrefabInstancePropertyModifications(component);
            var response = new SuccessResponse("Component added.", new
            {
                componentId = component.GetInstanceIDCompat(), type = component.GetType().FullName,
                label = ObjectNames.GetInspectorTitle(component)
            });
            step.Complete();
            return response;
        });
    }

    [McpForUnityResource("gm_editor_component_action")]
    public static class GamachineEditorComponentAction
    {
        public static object HandleCommand(JObject @params) => GamachineSceneEditorWriter.Run("write_failed", step =>
        {
            string error = GamachineSceneEditorComponents.ResolveComponent(@params?["componentId"], out var component);
            if (error != null) return new ErrorResponse(error);
            string action = @params?["action"]?.Type == JTokenType.String ? @params.Value<string>("action") : null;
            if (action != "reset" && action != "remove" && action != "up" && action != "down")
                return new ErrorResponse("invalid_value");
            int componentId = component.GetInstanceIDCompat();
            string title = ObjectNames.GetInspectorTitle(component);
            if (action == "remove")
            {
                if (component is Transform) return new ErrorResponse("locked");
                if (PrefabUtility.GetCorrespondingObjectFromSource(component) != null && !PrefabUtility.IsAddedComponentOverride(component))
                    return new ErrorResponse("prefab_part");
                if (GamachineSceneEditorComponents.Required(component)) return new ErrorResponse("required");
                step.Begin("Gamachine: Remove " + title);
                Undo.DestroyObjectImmediate(component);
            }
            else if (action == "reset")
            {
                string groupName = "Gamachine: Reset " + title;
                step.Begin(groupName);
                Undo.RegisterCompleteObjectUndo(component, groupName);
                Undo.RecordObject(component, groupName);
                Unsupported.SmartReset(component);
                PrefabUtility.RecordPrefabInstancePropertyModifications(component);
            }
            else
            {
                if (component is Transform) return new ErrorResponse("invalid_value");
                var components = component.gameObject.GetComponents<Component>();
                int index = Array.IndexOf(components, component);
                if ((action == "up" && index <= 1) || (action == "down" && index == components.Length - 1))
                    return new ErrorResponse("invalid_value");
                step.Begin("Gamachine: Move " + title + " " + action);
                bool moved = action == "up" ? ComponentUtility.MoveComponentUp(component) : ComponentUtility.MoveComponentDown(component);
                if (!moved)
                {
                    step.Revert();
                    return new ErrorResponse("invalid_value");
                }
            }
            var response = new SuccessResponse("Component action applied.", new { componentId, action });
            step.Complete();
            return response;
        });
    }
}
