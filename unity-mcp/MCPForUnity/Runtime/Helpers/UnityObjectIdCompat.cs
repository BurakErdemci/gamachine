using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace MCPForUnity.Runtime.Helpers
{
    // Part of MCP for Unity's compat-shim family. See UnityCompatShims.cs in this
    // folder for the full list of shims, the audit policy, and the reflection pattern.
    /// <summary>
    /// Version-gated wrappers for the InstanceID ↔ EntityId migration introduced in Unity 6.5
    /// and tightened in 6.6.
    ///   Forward (Object → int): <see cref="GetInstanceIDCompat"/>
    ///   Reverse (int → Object, Editor-only): <see cref="InstanceIDToObjectCompat"/>
    /// </summary>
    public static class UnityObjectIdCompat
    {
        /// <summary>
        /// Returns a session-scoped int handle for the object. On 6.5+ truncates the
        /// EntityId's underlying ulong; lossy but stable within a session and preserves
        /// the int-shaped wire format that older consumers expect. For deserialization
        /// round-trips on 6.5+, prefer the full <c>entityID</c> field.
        /// </summary>
        public static int GetInstanceIDCompat(this Object obj)
        {
            if (obj == null)
            {
                return 0;
            }

#if UNITY_6000_5_OR_NEWER
            return (int)EntityId.ToULong(obj.GetEntityId());
#else
            // 6.4 GetInstanceID'yi obsolete işaretler ama GetEntityId ancak 6.5'te
            // güvenilir — bu dal bilerek eski API'de (shim'in varlık sebebi).
#pragma warning disable CS0618
            return obj.GetInstanceID();
#pragma warning restore CS0618
#endif
        }

#if UNITY_EDITOR
        /// <summary>
        /// Resolves an int instance ID handle back to a UnityEngine.Object.
        ///   Pre-6.0  : EditorUtility.InstanceIDToObject(int)
        ///   6.0–6.5  : EditorUtility.EntityIdToObject(int)  (implicit int→EntityId cast)
        ///   6.6+     : EditorUtility.EntityIdToObject(EntityId.FromULong(...)) on the bits
        ///              <see cref="GetInstanceIDCompat"/> handed out.
        /// </summary>
        public static Object InstanceIDToObjectCompat(int instanceId)
        {
#if UNITY_6000_6_OR_NEWER
            // InstanceIDToObject(int) throws when invoked through reflection on 6.6.8f1 (measured
            // 3 Nov 2026: every find_gameobjects call failed), so the int goes back to an EntityId.
            // GetInstanceIDCompat keeps the low 32 bits; sign-extending restores any id that fits.
            Object found = EditorUtility.EntityIdToObject(EntityId.FromULong((ulong)(long)instanceId));
            if (found == null && instanceId < 0)
                found = EditorUtility.EntityIdToObject(EntityId.FromULong((uint)instanceId));
            return found;
#elif UNITY_6000_3_OR_NEWER
            // 6.4 implicit int→EntityId cast'i obsolete işaretler; 6.6+ dalı zaten
            // reflection'a geçiyor — aradaki sürümlerde bilinçli eski davranış.
#pragma warning disable CS0618
            return EditorUtility.EntityIdToObject(instanceId);
#pragma warning restore CS0618
#else
            return EditorUtility.InstanceIDToObject(instanceId);
#endif
        }
#endif
    }
}
