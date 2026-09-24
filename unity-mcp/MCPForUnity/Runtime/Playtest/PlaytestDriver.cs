using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using Unity.Profiling;
using UnityEngine;

namespace MCPForUnity.Runtime.Playtest
{
    /// <summary>
    /// Frame driver of a playtest session. The editor un-pauses the game; <see cref="PlaytestFrameGate"/> calls
    /// Debug.Break() at the end of the target frame (or when <c>until</c> holds / an error was logged), so frames
    /// are advanced by the player loop itself. Measured in P2: EditorApplication.Step() advances frames but the
    /// Input System then runs only editor updates, so wasPressedThisFrame never reaches the game.
    /// </summary>
    public static class PlaytestDriver
    {
        private static GameObject s_Host;

        public static bool Installed => s_Host != null;

        /// <summary>True while a step range runs; the gate breaks and autopilot runs only while armed.</summary>
        public static bool Armed { get; private set; }

        public static int StopAtFrame { get; private set; }
        public static bool UntilHit { get; private set; }
        public static bool ErrorBreak { get; private set; }
        public static bool Autopilot { get; set; }
        public static bool StopOnError { get; set; } = true;
        public static string AutopilotError { get; private set; }

        private static string s_UntilHook;
        private static string s_UntilOp;
        private static JToken s_UntilValue;
        private static volatile bool s_ErrorLogged;

        public static void Install()
        {
            if (s_Host != null) return;
            s_Host = new GameObject("[MCPForUnity Playtest Driver]");
            s_Host.hideFlags = HideFlags.HideInHierarchy | HideFlags.NotEditable;
            UnityEngine.Object.DontDestroyOnLoad(s_Host);
            s_Host.AddComponent<PlaytestFramePrologue>();
            s_Host.AddComponent<PlaytestFrameGate>();
        }

        public static void Uninstall()
        {
            Disarm();
            PlaytestPerf.Stop();
            if (s_Host != null) UnityEngine.Object.Destroy(s_Host);
            s_Host = null;
        }

        /// <summary>Arms the gate: the game breaks at the end of <paramref name="stopAtFrame"/> or earlier on until/error.</summary>
        public static void Arm(int stopAtFrame, string untilHook, string untilOp, JToken untilValue)
        {
            StopAtFrame = stopAtFrame;
            s_UntilHook = untilHook;
            s_UntilOp = untilOp;
            s_UntilValue = untilValue;
            UntilHit = false;
            ErrorBreak = false;
            Armed = true;
        }

        public static void Disarm()
        {
            Armed = false;
        }

        /// <summary>Thread-safe: called from Application.logMessageReceivedThreaded for errors in the stepped range.</summary>
        public static void NotifyErrorLogged() => s_ErrorLogged = true;

        public static void ClearErrorFlag() => s_ErrorLogged = false;

        public static void ClearAutopilotError() => AutopilotError = null;

        /// <summary>
        /// Called once per stepped frame before input is dispatched (from the Input System's before-update hook,
        /// or from the earliest Update when the Input System is absent). Returns the autopilot's input list, if any.
        /// </summary>
        internal static JArray RunAutopilot()
        {
            if (!Armed || !Autopilot || !GameHooks.Exists("autopilot")) return null;
            if (!GameHooks.TryCall("autopilot", null, out var result, out var error))
            {
                AutopilotError = error;
                Debug.LogError("[Playtest] autopilot: " + error);
                return null;
            }
            return result as JArray;
        }

        internal static void EndOfFrame()
        {
            if (PlaytestPerf.Active) PlaytestPerf.Sample();
            if (!Armed) return;

            bool stop = false;
            if (s_UntilHook != null && GameHooks.TryGet(s_UntilHook, out var value, out _)
                && PlaytestJson.Compare(value, s_UntilOp, s_UntilValue, out _))
            {
                UntilHit = true;
                stop = true;
            }
            if (StopOnError && s_ErrorLogged)
            {
                ErrorBreak = true;
                stop = true;
            }
            if (Time.frameCount >= StopAtFrame) stop = true;

            if (stop)
            {
                Armed = false;
                Debug.Break();
            }
        }
    }

    [DefaultExecutionOrder(-32000)]
    [AddComponentMenu("")]
    internal sealed class PlaytestFramePrologue : MonoBehaviour
    {
        private void Update()
        {
            if (!PlaytestInput.DrivesPrologue) PlaytestInput.FramePrologue();
        }
    }

    [DefaultExecutionOrder(32000)]
    [AddComponentMenu("")]
    internal sealed class PlaytestFrameGate : MonoBehaviour
    {
        private void LateUpdate() => PlaytestDriver.EndOfFrame();
    }

    /// <summary>
    /// Free-running performance segment: frame time from FrameTimingManager (falling back to unscaled delta time
    /// when the editor reports no timings) plus render and memory counters from ProfilerRecorder.
    /// </summary>
    public static class PlaytestPerf
    {
        public static bool Active { get; private set; }

        private static readonly List<double> s_FrameMs = new List<double>();
        private static readonly List<double> s_DeltaMs = new List<double>();
        private static readonly List<double> s_DrawCalls = new List<double>();
        private static readonly List<double> s_Batches = new List<double>();
        private static readonly List<double> s_Triangles = new List<double>();
        private static readonly List<double> s_GcAlloc = new List<double>();
        private static readonly FrameTiming[] s_Timing = new FrameTiming[1];
        private static readonly List<ProfilerRecorder> s_DrawRecs = new List<ProfilerRecorder>();
        private static readonly List<ProfilerRecorder> s_BatchRecs = new List<ProfilerRecorder>();
        private static ProfilerRecorder s_TriRec, s_GcRec;
        private static string s_DrawSource, s_BatchSource;

        // Unity 6.x replaced "Draw Calls Count"/"Batches Count" with one counter per submission path (measured on
        // 6000.4: only the split names exist); the Stats window's Batches is their sum there as well.
        private static readonly string[] SplitDrawCounters =
        {
            "Standard Draw Calls Count", "Standard Indirect Draw Calls Count", "Standard Instanced Draw Calls Count",
            "SRP Batcher Draw Calls Count", "BRG Draw Calls Count", "BRG Indirect Draw Calls Count",
            "Null Geometry Draw Calls Count", "Null Geometry Indirect Draw Calls Count",
        };

        public static void Start()
        {
            Stop();
            s_FrameMs.Clear();
            s_DeltaMs.Clear();
            s_DrawCalls.Clear();
            s_Batches.Clear();
            s_Triangles.Clear();
            s_GcAlloc.Clear();
            s_DrawSource = StartGroup(s_DrawRecs, "Draw Calls Count");
            s_BatchSource = StartGroup(s_BatchRecs, "Batches Count");
            s_TriRec = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Triangles Count");
            s_GcRec = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "GC Allocated In Frame");
            Active = true;
        }

        internal static void Sample()
        {
            FrameTimingManager.CaptureFrameTimings();
            if (FrameTimingManager.GetLatestTimings(1, s_Timing) > 0 && s_Timing[0].cpuFrameTime > 0)
                s_FrameMs.Add(s_Timing[0].cpuFrameTime);
            s_DeltaMs.Add(Time.unscaledDeltaTime * 1000.0);
            AddSum(s_DrawRecs, s_DrawCalls);
            AddSum(s_BatchRecs, s_Batches);
            Add(s_TriRec, s_Triangles);
            Add(s_GcRec, s_GcAlloc);
        }

        private static void Add(ProfilerRecorder r, List<double> into)
        {
            if (r.Valid && r.Count > 0) into.Add(r.LastValue);
        }

        private static void AddSum(List<ProfilerRecorder> group, List<double> into)
        {
            double sum = 0;
            bool any = false;
            foreach (var r in group)
            {
                if (!r.Valid || r.Count == 0) continue;
                sum += r.LastValue;
                any = true;
            }
            if (any) into.Add(sum);
        }

        private static string StartGroup(List<ProfilerRecorder> group, string legacyName)
        {
            group.Clear();
            var legacy = ProfilerRecorder.StartNew(ProfilerCategory.Render, legacyName);
            if (legacy.Valid)
            {
                group.Add(legacy);
                return legacyName;
            }
            legacy.Dispose();
            foreach (var name in SplitDrawCounters)
            {
                var r = ProfilerRecorder.StartNew(ProfilerCategory.Render, name);
                if (r.Valid) group.Add(r);
                else r.Dispose();
            }
            return group.Count > 0 ? "sum of per-path draw call counters" : null;
        }

        public static void Stop()
        {
            Active = false;
            foreach (var r in s_DrawRecs) r.Dispose();
            foreach (var r in s_BatchRecs) r.Dispose();
            s_DrawRecs.Clear();
            s_BatchRecs.Clear();
            s_TriRec.Dispose();
            s_GcRec.Dispose();
        }

        /// <summary>Stops sampling and returns the perf block of a run_playtest result.</summary>
        public static JObject Finish()
        {
            Stop();
            // The first sample includes the un-pause transition; drop it when there is enough data.
            List<double> frame = s_FrameMs.Count >= 10 ? s_FrameMs : s_DeltaMs;
            string source = s_FrameMs.Count >= 10 ? "frame_timing_cpu" : "unscaled_delta_time";
            var ms = frame.Skip(frame.Count > 2 ? 1 : 0).ToList();
            return new JObject
            {
                ["frame_ms_p50"] = Round(Percentile(ms, 0.5), 3),
                ["frame_ms_p95"] = Round(Percentile(ms, 0.95), 3),
                ["draw_calls"] = Mean(s_DrawCalls),
                ["batches"] = Mean(s_Batches),
                ["triangles"] = Mean(s_Triangles),
                ["gc_alloc_kb_per_frame"] = s_GcAlloc.Count > 0 ? Round(s_GcAlloc.Average() / 1024.0, 3) : JValue.CreateNull(),
                ["mono_mb"] = Round(UnityEngine.Profiling.Profiler.GetMonoUsedSizeLong() / (1024.0 * 1024.0), 2),
                ["frames"] = s_DeltaMs.Count,
                ["frame_ms_source"] = source,
                ["draw_calls_source"] = s_DrawSource,
                ["batches_source"] = s_BatchSource,
                ["quality_level"] = QualitySettings.names.Length > 0 ? QualitySettings.names[QualitySettings.GetQualityLevel()] : null,
                ["target_frame_rate"] = Application.targetFrameRate,
            };
        }

        private static JToken Mean(List<double> xs) => xs.Count > 0 ? Round(xs.Average(), 1) : JValue.CreateNull();

        private static JToken Round(double v, int digits) => double.IsNaN(v) ? JValue.CreateNull() : new JValue(Math.Round(v, digits));

        private static double Percentile(List<double> xs, double q)
        {
            if (xs.Count == 0) return double.NaN;
            var s = xs.OrderBy(x => x).ToList();
            return s[Math.Min(s.Count - 1, (int)Math.Floor(q * (s.Count - 1) + 0.5))];
        }
    }
}
