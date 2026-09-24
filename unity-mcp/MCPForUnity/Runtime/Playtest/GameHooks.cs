using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.Playtest
{
    /// <summary>
    /// Registry of the game's playtest hooks: static members marked <see cref="GameHookAttribute"/> (fed in by the
    /// editor's discovery) plus instance-bound hooks registered at runtime. Runtime registrations win over an
    /// attribute hook with the same name.
    /// </summary>
    public static class GameHooks
    {
        public const string KindState = "state";
        public const string KindAction = "action";

        public sealed class HookInfo
        {
            public string Name;
            public string Kind;
            public string Type;
        }

        private sealed class Entry
        {
            public string Name;
            public string Kind;
            public string Type;
            public Func<object> Getter;
            public Func<JObject, object> Invoke;
        }

        private static readonly Dictionary<string, Entry> s_Attributed = new Dictionary<string, Entry>(StringComparer.Ordinal);
        private static readonly Dictionary<string, Entry> s_Runtime = new Dictionary<string, Entry>(StringComparer.Ordinal);

        /// <summary>Registers an instance-bound readable hook, e.g. <c>GameHooks.State("enemy.count", () => enemies.Count)</c>.</summary>
        public static void State(string name, Func<object> getter)
        {
            if (string.IsNullOrEmpty(name)) throw new ArgumentException("hook name is empty", nameof(name));
            if (getter == null) throw new ArgumentNullException(nameof(getter));
            s_Runtime[name] = new Entry { Name = name, Kind = KindState, Type = "object", Getter = getter };
        }

        /// <summary>Registers an instance-bound callable hook; <paramref name="action"/> receives the call's args object.</summary>
        public static void Action(string name, Func<JObject, object> action)
        {
            if (string.IsNullOrEmpty(name)) throw new ArgumentException("hook name is empty", nameof(name));
            if (action == null) throw new ArgumentNullException(nameof(action));
            s_Runtime[name] = new Entry { Name = name, Kind = KindAction, Type = "(args)", Invoke = action };
        }

        public static void Unregister(string name)
        {
            if (name != null) s_Runtime.Remove(name);
        }

        /// <summary>
        /// Replaces the attribute-declared hooks. Called by the editor with the members found by TypeCache; non-static
        /// members are rejected (returned as problems) because there is no instance to read them from.
        /// </summary>
        public static List<string> SetAttributedMembers(IEnumerable<MemberInfo> members)
        {
            var problems = new List<string>();
            s_Attributed.Clear();
            foreach (var m in members)
            {
                var attr = m.GetCustomAttribute<GameHookAttribute>();
                if (attr == null || string.IsNullOrEmpty(attr.Name)) continue;
                string where = $"{m.DeclaringType?.FullName}.{m.Name}";
                Entry e = null;
                switch (m)
                {
                    case PropertyInfo p:
                        var getter = p.GetGetMethod(true);
                        if (getter == null || !getter.IsStatic) { problems.Add($"{attr.Name}: {where} must be a static property with a getter"); continue; }
                        e = new Entry { Kind = KindState, Type = FriendlyName(p.PropertyType), Getter = () => p.GetValue(null) };
                        break;
                    case FieldInfo f:
                        if (!f.IsStatic) { problems.Add($"{attr.Name}: {where} must be a static field"); continue; }
                        e = new Entry { Kind = KindState, Type = FriendlyName(f.FieldType), Getter = () => f.GetValue(null) };
                        break;
                    case MethodInfo mi:
                        if (!mi.IsStatic || mi.ContainsGenericParameters) { problems.Add($"{attr.Name}: {where} must be a static non-generic method"); continue; }
                        var ps = mi.GetParameters();
                        e = new Entry
                        {
                            Kind = KindAction,
                            Type = $"{FriendlyName(mi.ReturnType)}({string.Join(", ", ps.Select(x => FriendlyName(x.ParameterType) + " " + x.Name))})",
                            Invoke = args => InvokeMethod(mi, ps, args),
                        };
                        break;
                }
                if (e == null) continue;
                e.Name = attr.Name;
                if (s_Attributed.TryGetValue(attr.Name, out var existing))
                {
                    problems.Add($"{attr.Name}: declared twice ({existing.Type} and {where}); keeping the first");
                    continue;
                }
                s_Attributed[attr.Name] = e;
            }
            return problems;
        }

        private static object InvokeMethod(MethodInfo mi, ParameterInfo[] ps, JObject args)
        {
            var values = new object[ps.Length];
            if (ps.Length == 1 && ps[0].ParameterType == typeof(JObject))
            {
                values[0] = args ?? new JObject();
            }
            else
            {
                for (int i = 0; i < ps.Length; i++)
                {
                    var token = args?[ps[i].Name];
                    if (token != null) values[i] = PlaytestJson.FromJson(token, ps[i].ParameterType);
                    else if (ps[i].HasDefaultValue) values[i] = ps[i].DefaultValue;
                    else throw new ArgumentException($"missing argument '{ps[i].Name}'");
                }
            }
            try
            {
                return mi.Invoke(null, values);
            }
            catch (TargetInvocationException ex) when (ex.InnerException != null)
            {
                throw ex.InnerException;
            }
        }

        private static Entry Find(string name)
        {
            if (name == null) return null;
            if (s_Runtime.TryGetValue(name, out var e)) return e;
            return s_Attributed.TryGetValue(name, out e) ? e : null;
        }

        public static bool Exists(string name) => Find(name) != null;

        public static List<HookInfo> List()
        {
            var merged = new Dictionary<string, Entry>(s_Attributed, StringComparer.Ordinal);
            foreach (var kv in s_Runtime) merged[kv.Key] = kv.Value;
            return merged.Values.OrderBy(e => e.Name, StringComparer.Ordinal)
                .Select(e => new HookInfo { Name = e.Name, Kind = e.Kind, Type = e.Type }).ToList();
        }

        public static List<string> StateNames() =>
            List().Where(h => h.Kind == KindState).Select(h => h.Name).ToList();

        /// <summary>Reads a hook as JSON. State hooks return their value; an action hook is called with no args.</summary>
        public static bool TryGet(string name, out JToken value, out string error)
        {
            value = null;
            var e = Find(name);
            if (e == null)
            {
                error = UnknownMessage(name);
                return false;
            }
            try
            {
                value = PlaytestJson.ToJson(e.Kind == KindState ? e.Getter() : e.Invoke(null));
                error = null;
                return true;
            }
            catch (Exception ex)
            {
                error = $"hook '{name}' threw {ex.GetType().Name}: {ex.Message}";
                return false;
            }
        }

        /// <summary>Calls a hook with an args object; the return value comes back as JSON.</summary>
        public static bool TryCall(string name, JObject args, out JToken result, out string error)
        {
            result = null;
            var e = Find(name);
            if (e == null)
            {
                error = UnknownMessage(name);
                return false;
            }
            try
            {
                result = PlaytestJson.ToJson(e.Kind == KindState ? e.Getter() : e.Invoke(args));
                error = null;
                return true;
            }
            catch (Exception ex)
            {
                error = $"hook '{name}' threw {ex.GetType().Name}: {ex.Message}";
                return false;
            }
        }

        public static string UnknownMessage(string name)
        {
            var close = CloseMatches(name, 3);
            return close.Count > 0
                ? $"Unknown hook '{name}'. Close matches: {string.Join(", ", close)}"
                : $"Unknown hook '{name}'. Known hooks: {string.Join(", ", List().Select(h => h.Name))}";
        }

        public static List<string> CloseMatches(string name, int max)
        {
            name ??= string.Empty;
            return List().Select(h => h.Name)
                .Select(n => (n, d: Distance(name.ToLowerInvariant(), n.ToLowerInvariant()) - (n.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0 && name.Length > 0 ? 100 : 0)))
                .Where(x => x.d <= Math.Max(3, name.Length / 2))
                .OrderBy(x => x.d).ThenBy(x => x.n, StringComparer.Ordinal)
                .Take(max).Select(x => x.n).ToList();
        }

        private static int Distance(string a, string b)
        {
            var d = new int[a.Length + 1, b.Length + 1];
            for (int i = 0; i <= a.Length; i++) d[i, 0] = i;
            for (int j = 0; j <= b.Length; j++) d[0, j] = j;
            for (int i = 1; i <= a.Length; i++)
                for (int j = 1; j <= b.Length; j++)
                    d[i, j] = Math.Min(Math.Min(d[i - 1, j] + 1, d[i, j - 1] + 1), d[i - 1, j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1));
            return d[a.Length, b.Length];
        }

        private static string FriendlyName(Type t)
        {
            if (t == typeof(void)) return "void";
            if (t == typeof(bool)) return "bool";
            if (t == typeof(int)) return "int";
            if (t == typeof(float)) return "float";
            if (t == typeof(double)) return "double";
            if (t == typeof(string)) return "string";
            if (t == typeof(long)) return "long";
            if (t == typeof(object)) return "object";
            if (t.IsGenericType)
            {
                var n = t.Name;
                int tick = n.IndexOf('`');
                if (tick > 0) n = n.Substring(0, tick);
                return $"{n}<{string.Join(", ", t.GetGenericArguments().Select(FriendlyName))}>";
            }
            return t.Name;
        }
    }
}
