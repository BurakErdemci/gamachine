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
            EntityId entityId = obj.GetEntityId();
            int handle = (int)EntityId.ToULong(entityId);
#if UNITY_EDITOR && UNITY_6000_6_OR_NEWER
            lock (s_handles) s_handles[handle] = entityId;
#endif
            return handle;
#else
            // 6.4 GetInstanceID'yi obsolete işaretler ama GetEntityId ancak 6.5'te
            // güvenilir — bu dal bilerek eski API'de (shim'in varlık sebebi).
#pragma warning disable CS0618
            return obj.GetInstanceID();
#pragma warning restore CS0618
#endif
        }

#if UNITY_EDITOR
#if UNITY_6000_6_OR_NEWER
        // Every int handle GetInstanceIDCompat gave out in this domain, with its full EntityId.
        // Measured on 6.6.8f1 (3 Nov 2026): EntityIdToObject(EntityId.FromULong(handle)) finds
        // nothing, so the truncated int cannot be turned back into the EntityId it came from.
        private static readonly System.Collections.Generic.Dictionary<int, EntityId> s_handles =
            new System.Collections.Generic.Dictionary<int, EntityId>();
#endif

        /// <summary>
        /// Resolves an int instance ID handle back to a UnityEngine.Object.
        ///   Pre-6.0  : EditorUtility.InstanceIDToObject(int)
        ///   6.0–6.5  : EditorUtility.EntityIdToObject(int)  (implicit int→EntityId cast)
        ///   6.6+     : the EntityId recorded when <see cref="GetInstanceIDCompat"/> handed the
        ///              int out; after a domain reload, a scan of the loaded objects.
        /// </summary>
        public static Object InstanceIDToObjectCompat(int instanceId)
        {
#if UNITY_6000_6_OR_NEWER
            // InstanceIDToObject(int) throws when invoked through reflection on 6.6.8f1.
            EntityId entityId;
            bool known;
            lock (s_handles) known = s_handles.TryGetValue(instanceId, out entityId);
            if (known)
            {
                Object recorded = EditorUtility.EntityIdToObject(entityId);
                if (recorded != null)
                    return recorded;
            }
            // A handle from before the last domain reload (every script compile clears the map).
            foreach (Object candidate in Resources.FindObjectsOfTypeAll<Object>())
            {
                if (candidate != null && candidate.GetInstanceIDCompat() == instanceId)
                    return candidate;
            }
            return null;
#elif UNITY_6000_3_OR_NEWER
            // 6.4 marks the implicit int→EntityId cast obsolete; until 6.6 it still resolves,
            // so these versions keep the old call on purpose.
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
