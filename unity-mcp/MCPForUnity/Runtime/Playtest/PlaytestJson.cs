using System;
using System.Collections;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Runtime.Playtest
{
    /// <summary>
    /// Hook value serialisation (vectors as arrays, enums as names), the comparison used by
    /// until/expect, and the stable state hash of run_playtest.
    /// </summary>
    public static class PlaytestJson
    {
        /// <summary>Tolerance for numeric ==/!= in until/expect: the closed-loop "same end state" bar.</summary>
        public const double Tolerance = 1e-4;

        private const int MaxDepth = 6;

        public static JToken ToJson(object value) => ToJson(value, 0);

        private static JToken ToJson(object value, int depth)
        {
            switch (value)
            {
                case null: return JValue.CreateNull();
                case JToken t: return t;
                case string s: return new JValue(s);
                case bool b: return new JValue(b);
                // float -> shortest round-trip text -> double, so 0.1f reads 0.1 and not 0.100000001490116.
                case float f: return Number(float.IsNaN(f) || float.IsInfinity(f) ? double.NaN : double.Parse(f.ToString("R", CultureInfo.InvariantCulture), CultureInfo.InvariantCulture));
                case double d: return Number(d);
                case decimal m: return new JValue(m);
                case int or long or short or byte or sbyte or uint or ushort or ulong: return new JValue(Convert.ToInt64(value, CultureInfo.InvariantCulture));
                case Enum e: return new JValue(e.ToString());
                case Vector2 v: return Arr(v.x, v.y);
                case Vector3 v: return Arr(v.x, v.y, v.z);
                case Vector4 v: return Arr(v.x, v.y, v.z, v.w);
                case Quaternion q: return Arr(q.x, q.y, q.z, q.w);
                case Color c: return Arr(c.r, c.g, c.b, c.a);
                case Color32 c: return new JArray(c.r, c.g, c.b, c.a);
                case Vector2Int v: return new JArray(v.x, v.y);
                case Vector3Int v: return new JArray(v.x, v.y, v.z);
                case Rect r: return Arr(r.x, r.y, r.width, r.height);
                case UnityEngine.Object o:
                    return o == null ? JValue.CreateNull() : new JObject { ["name"] = o.name, ["type"] = o.GetType().Name };
            }

            if (depth >= MaxDepth) return new JValue(value.ToString());

            if (value is IDictionary dict)
            {
                var obj = new JObject();
                foreach (DictionaryEntry kv in dict) obj[Convert.ToString(kv.Key, CultureInfo.InvariantCulture)] = ToJson(kv.Value, depth + 1);
                return obj;
            }
            if (value is IEnumerable seq)
            {
                var arr = new JArray();
                foreach (var item in seq) arr.Add(ToJson(item, depth + 1));
                return arr;
            }

            try
            {
                var token = JToken.FromObject(value);
                return token;
            }
            catch (Exception)
            {
                return new JValue(value.ToString());
            }
        }

        private static JToken Number(double d)
        {
            if (double.IsNaN(d) || double.IsInfinity(d)) return new JValue(d.ToString(CultureInfo.InvariantCulture));
            return new JValue(d);
        }

        private static JArray Arr(params float[] xs) => new JArray(xs.Select(x => ToJson(x)).ToArray());

        /// <summary>Converts a JSON argument into a hook parameter type (arrays or {x,y,z} for vectors, names for enums).</summary>
        public static object FromJson(JToken token, Type type)
        {
            if (token == null || token.Type == JTokenType.Null)
                return type.IsValueType ? Activator.CreateInstance(type) : null;
            if (type == typeof(JToken)) return token;
            if (type == typeof(JObject)) return token as JObject;
            if (type == typeof(object)) return token;
            if (type.IsEnum) return Enum.Parse(type, token.ToString(), true);
            if (type == typeof(Vector2)) { var f = Floats(token, "x", "y"); return new Vector2(f[0], f[1]); }
            if (type == typeof(Vector3)) { var f = Floats(token, "x", "y", "z"); return new Vector3(f[0], f[1], f[2]); }
            if (type == typeof(Vector4)) { var f = Floats(token, "x", "y", "z", "w"); return new Vector4(f[0], f[1], f[2], f[3]); }
            if (type == typeof(Quaternion)) { var f = Floats(token, "x", "y", "z", "w"); return new Quaternion(f[0], f[1], f[2], f[3]); }
            if (type == typeof(Color)) { var f = Floats(token, "r", "g", "b", "a"); return new Color(f[0], f[1], f[2], f[3]); }
            return token.ToObject(type);
        }

        private static float[] Floats(JToken token, params string[] keys)
        {
            var result = new float[keys.Length];
            if (token is JArray arr)
            {
                for (int i = 0; i < keys.Length && i < arr.Count; i++) result[i] = arr[i].Value<float>();
            }
            else if (token is JObject obj)
            {
                for (int i = 0; i < keys.Length; i++) result[i] = obj[keys[i]]?.Value<float>() ?? 0f;
            }
            else
            {
                throw new ArgumentException($"expected an array or object, got {token.Type}");
            }
            return result;
        }

        public static readonly string[] Operators = { "==", "!=", "<", ">", "<=", ">=" };

        /// <summary>
        /// Evaluates <c>actual op expected</c>. Numbers compare with <see cref="Tolerance"/> for ==/!=, arrays
        /// element by element; ordering operators accept numbers only. Returns false (with a reason) on a type mismatch.
        /// </summary>
        public static bool Compare(JToken actual, string op, JToken expected, out string error)
        {
            error = null;
            if (!Operators.Contains(op))
            {
                error = $"unknown op '{op}' (use {string.Join(", ", Operators)})";
                return false;
            }
            if (op == "==") return JsonEquals(actual, expected);
            if (op == "!=") return !JsonEquals(actual, expected);

            if (!IsNumber(actual) || !IsNumber(expected))
            {
                error = $"op '{op}' needs numbers (got {actual?.Type.ToString() ?? "null"} and {expected?.Type.ToString() ?? "null"})";
                return false;
            }
            double a = actual.Value<double>(), e = expected.Value<double>();
            switch (op)
            {
                case "<": return a < e;
                case ">": return a > e;
                case "<=": return a <= e + Tolerance;
                default: return a >= e - Tolerance;
            }
        }

        private static bool IsNumber(JToken t) => t != null && (t.Type == JTokenType.Integer || t.Type == JTokenType.Float);

        private static bool JsonEquals(JToken a, JToken b)
        {
            bool aNull = a == null || a.Type == JTokenType.Null, bNull = b == null || b.Type == JTokenType.Null;
            if (aNull || bNull) return aNull && bNull;
            if (IsNumber(a) && IsNumber(b)) return Math.Abs(a.Value<double>() - b.Value<double>()) <= Tolerance;
            if (a is JArray aa && b is JArray ba)
            {
                if (aa.Count != ba.Count) return false;
                for (int i = 0; i < aa.Count; i++) if (!JsonEquals(aa[i], ba[i])) return false;
                return true;
            }
            if (a is JObject ao && b is JObject bo)
            {
                if (ao.Count != bo.Count) return false;
                foreach (var p in ao) if (!JsonEquals(p.Value, bo[p.Key])) return false;
                return true;
            }
            if (a.Type == JTokenType.String || b.Type == JTokenType.String)
                return string.Equals(a.ToString(), b.ToString(), StringComparison.Ordinal);
            return JToken.DeepEquals(a, b);
        }

        /// <summary>
        /// Stable hash of a state object: keys sorted, numbers rounded to 1e-4 (the determinism bar), so two runs that
        /// agree to 1e-4 hash equal. First 16 hex chars of SHA-256.
        /// </summary>
        public static string StateHash(JToken state)
        {
            var sb = new StringBuilder();
            Canonical(state, sb);
            using var sha = SHA256.Create();
            var bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(sb.ToString()));
            var hex = new StringBuilder(16);
            for (int i = 0; i < 8; i++) hex.Append(bytes[i].ToString("x2"));
            return hex.ToString();
        }

        private static void Canonical(JToken t, StringBuilder sb)
        {
            switch (t)
            {
                case null:
                    sb.Append("null");
                    return;
                case JObject o:
                    sb.Append('{');
                    bool first = true;
                    foreach (var p in o.Properties().OrderBy(p => p.Name, StringComparer.Ordinal))
                    {
                        if (!first) sb.Append(',');
                        first = false;
                        sb.Append('"').Append(p.Name).Append("\":");
                        Canonical(p.Value, sb);
                    }
                    sb.Append('}');
                    return;
                case JArray a:
                    sb.Append('[');
                    for (int i = 0; i < a.Count; i++)
                    {
                        if (i > 0) sb.Append(',');
                        Canonical(a[i], sb);
                    }
                    sb.Append(']');
                    return;
            }
            if (IsNumber(t))
            {
                double r = Math.Round(t.Value<double>(), 4, MidpointRounding.AwayFromZero);
                if (r == 0) r = 0; // folds -0 into 0
                sb.Append(r.ToString("0.####", CultureInfo.InvariantCulture));
                return;
            }
            sb.Append(t.Type == JTokenType.Null ? "null" : t.ToString(Newtonsoft.Json.Formatting.None));
        }
    }
}
