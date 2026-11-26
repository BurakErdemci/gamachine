using System;
using System.Collections.Generic;
using System.Threading;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor
{
    /// <summary>
    /// The plugin's only route to EditorPrefs. In a normal editor every call goes straight to
    /// EditorPrefs. In a lane editor (see <see cref="IsLane"/>) writes and deletes stay inside
    /// this editor process instead: EditorPrefs are machine-wide per Unity version, so a lane
    /// writing them would retarget, reconnect or switch off the user's own editor.
    /// Lane reads see the lane's own writes and deletes first, then fall back to the user's prefs.
    /// </summary>
    [InitializeOnLoad]
    internal static class McpPrefs
    {
        internal const string LaneIdEnvVar = "GAMACHINE_LANE_ID";
        internal const string AllowBatchEnvVar = "UNITY_MCP_ALLOW_BATCH";

        // SessionState lives as long as the editor process and survives domain reloads, which is
        // exactly the lifetime a lane needs (resume-after-reload must outlive the reload).
        internal const string SessionKey = "MCPForUnity.LanePrefs";

        private enum Kind { Deleted, Bool, Int, String }

        [Serializable]
        private class Entry
        {
            public string key;
            public Kind kind;
            public string value;
        }

        [Serializable]
        private class Snapshot
        {
            public List<Entry> entries = new List<Entry>();
        }

        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Entry> Overrides = new Dictionary<string, Entry>();
        private static readonly int MainThreadId;
        private static readonly bool LaneProcess;

        /// <summary>Tests set this to run the lane path inside a normal editor; null means detect.</summary>
        internal static bool? LaneOverrideForTests;

        static McpPrefs()
        {
            MainThreadId = Thread.CurrentThread.ManagedThreadId;
            LaneProcess = Application.isBatchMode
                && (!string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(LaneIdEnvVar))
                    || !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(AllowBatchEnvVar)));
            if (!LaneProcess) return;

            LoadFromSession();
            // Writes made off the main thread cannot reach SessionState; save them before the reload.
            AssemblyReloadEvents.beforeAssemblyReload += FlushToSession;
        }

        /// <summary>
        /// True in a batch-mode editor that may talk to the MCP server (GAMACHINE_LANE_ID or
        /// UNITY_MCP_ALLOW_BATCH set). Such an editor must not write shared prefs or stop the server.
        /// </summary>
        public static bool IsLane => LaneOverrideForTests ?? LaneProcess;

        public static bool GetBool(string key, bool defaultValue = false)
        {
            if (IsLane && TryGetOverride(key, out Entry e))
                return e.kind == Kind.Bool ? e.value == "1" : defaultValue;
            return EditorPrefs.GetBool(key, defaultValue);
        }

        public static int GetInt(string key, int defaultValue = 0)
        {
            if (IsLane && TryGetOverride(key, out Entry e))
                return e.kind == Kind.Int && int.TryParse(e.value, out int v) ? v : defaultValue;
            return EditorPrefs.GetInt(key, defaultValue);
        }

        public static string GetString(string key, string defaultValue = "")
        {
            if (IsLane && TryGetOverride(key, out Entry e))
                return e.kind == Kind.String ? e.value : defaultValue;
            return EditorPrefs.GetString(key, defaultValue);
        }

        public static bool HasKey(string key)
        {
            if (IsLane && TryGetOverride(key, out Entry e))
                return e.kind != Kind.Deleted;
            return EditorPrefs.HasKey(key);
        }

        public static void SetBool(string key, bool value)
        {
            if (IsLane) { Put(key, Kind.Bool, value ? "1" : "0"); return; }
            EditorPrefs.SetBool(key, value);
        }

        public static void SetInt(string key, int value)
        {
            if (IsLane) { Put(key, Kind.Int, value.ToString(System.Globalization.CultureInfo.InvariantCulture)); return; }
            EditorPrefs.SetInt(key, value);
        }

        public static void SetString(string key, string value)
        {
            if (IsLane) { Put(key, Kind.String, value ?? string.Empty); return; }
            EditorPrefs.SetString(key, value);
        }

        public static void DeleteKey(string key)
        {
            // A lane delete is a tombstone: without it the lane would read the user's value back.
            if (IsLane) { Put(key, Kind.Deleted, null); return; }
            EditorPrefs.DeleteKey(key);
        }

        private static bool TryGetOverride(string key, out Entry entry)
        {
            lock (Gate)
            {
                return Overrides.TryGetValue(key, out entry);
            }
        }

        private static void Put(string key, Kind kind, string value)
        {
            lock (Gate)
            {
                Overrides[key] = new Entry { key = key, kind = kind, value = value };
            }
            if (Thread.CurrentThread.ManagedThreadId == MainThreadId)
                FlushToSession();
        }

        private static void FlushToSession()
        {
            var snapshot = new Snapshot();
            lock (Gate)
            {
                snapshot.entries.AddRange(Overrides.Values);
            }
            SessionState.SetString(SessionKey, JsonUtility.ToJson(snapshot));
        }

        private static void LoadFromSession()
        {
            string json = SessionState.GetString(SessionKey, string.Empty);
            if (string.IsNullOrEmpty(json)) return;
            Snapshot snapshot;
            try { snapshot = JsonUtility.FromJson<Snapshot>(json); }
            catch { return; }
            if (snapshot?.entries == null) return;
            lock (Gate)
            {
                foreach (Entry e in snapshot.entries)
                {
                    if (!string.IsNullOrEmpty(e?.key)) Overrides[e.key] = e;
                }
            }
        }

        /// <summary>Drops the lane store in memory and in SessionState.</summary>
        internal static void ResetLaneStoreForTests()
        {
            lock (Gate)
            {
                Overrides.Clear();
            }
            SessionState.EraseString(SessionKey);
        }

        /// <summary>Forgets the in-memory store and reloads it from SessionState, as a domain reload does.</summary>
        internal static void SimulateDomainReloadForTests()
        {
            lock (Gate)
            {
                Overrides.Clear();
            }
            LoadFromSession();
        }
    }
}
