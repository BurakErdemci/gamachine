using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Playtest;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.Playtest
{
    /// <summary>
    /// Feeds the [GameHook] members of the loaded assemblies into the runtime registry, once per domain.
    /// </summary>
    internal static class PlaytestHookDiscovery
    {
        private static bool s_Done;

        public static List<string> Problems { get; private set; } = new List<string>();

        public static void Ensure()
        {
            if (s_Done) return;
            var members = new List<MemberInfo>();
            members.AddRange(TypeCache.GetMethodsWithAttribute<GameHookAttribute>());
            members.AddRange(TypeCache.GetFieldsWithAttribute<GameHookAttribute>());
            members.AddRange(FindProperties());
            Problems = GameHooks.SetAttributedMembers(members);
            foreach (var p in Problems) McpLog.Warn("[Playtest] " + p);
            s_Done = true;
        }

        // TypeCache has no property query; only assemblies that reference the runtime assembly can carry the attribute.
        private static IEnumerable<MemberInfo> FindProperties()
        {
            string runtimeName = typeof(GameHookAttribute).Assembly.GetName().Name;
            var result = new List<MemberInfo>();
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (asm.IsDynamic) continue;
                bool candidate = asm.GetName().Name == runtimeName
                    || asm.GetReferencedAssemblies().Any(r => r.Name == runtimeName);
                if (!candidate) continue;
                Type[] types;
                try { types = asm.GetTypes(); }
                catch (ReflectionTypeLoadException e) { types = e.Types.Where(t => t != null).ToArray(); }
                foreach (var t in types)
                {
                    foreach (var p in t.GetProperties(BindingFlags.Static | BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                    {
                        if (p.IsDefined(typeof(GameHookAttribute), false)) result.Add(p);
                    }
                }
            }
            return result;
        }
    }
}
