using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Playtest;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Playtest
{
    internal sealed class StepSpec
    {
        public int Frames;
        public JArray Input;
        public bool Autopilot;
        public List<string> Watch;
        public string UntilHook;
        public string UntilOp;
        public JToken UntilValue;
        public bool CaptureEnd;
        public List<int> CaptureFrames = new List<int>();
        public int ImageMaxSize = 640;
        public bool Jpeg = true;
        public bool InlineImage = true;
        public string Camera;
        public string CaptureLabel = "step";
        public double TimeoutS = 25;
    }

    /// <summary>
    /// Advances a paused play session by N frames with the Debug.Break driver (never wall clock): un-pause, let
    /// PlaytestFrameGate break at the next stop frame (a capture frame or the last one), repeat. Console errors and
    /// warnings of the stepped range are counted from Application.logMessageReceivedThreaded.
    /// </summary>
    internal static class PlaytestStepper
    {
        public static readonly string CaptureDir = Path.Combine(Path.GetDirectoryName(Application.dataPath) ?? ".", "Library", "GamachineCaptures");

        private sealed class Run
        {
            public StepSpec Spec;
            public TaskCompletionSource<JObject> Tcs;
            public string Phase = "pausing";
            public int F0;
            public int Target;
            public int SegmentStart;
            public HashSet<int> CaptureAt = new HashSet<int>();
            public JArray Captures = new JArray();
            public Stopwatch Clock;
            public int Errors;
            public int Warnings;
            public List<string> FirstErrors = new List<string>();
            public readonly object Lock = new object();
        }

        private static Run s_Run;

        public static bool Running => s_Run != null;

        /// <summary>Parses play_step params (also used for scenario steps). Returns an error or null.</summary>
        public static string Parse(JObject p, out StepSpec spec)
        {
            spec = new StepSpec();
            var framesTok = p["frames"];
            if (framesTok == null) return "'frames' is required (1..3600).";
            if (!int.TryParse(framesTok.ToString(), out spec.Frames) || spec.Frames < 1 || spec.Frames > 3600)
                return $"'frames' must be an integer in 1..3600 (got {framesTok}).";

            if (p["input"] != null && p["input"].Type != JTokenType.Null)
            {
                if (!(p["input"] is JArray inputs)) return "'input' must be a list.";
                foreach (var t in inputs)
                {
                    if (!(t is JObject o)) return "every input entry must be an object.";
                    int rel = o["frame"]?.Value<int?>() ?? 0;
                    if (rel < 0 || rel >= spec.Frames) return $"input frame {rel} is outside 0..{spec.Frames - 1}.";
                }
                spec.Input = inputs;
            }

            spec.Autopilot = p["autopilot"]?.Type == JTokenType.Boolean && p["autopilot"].Value<bool>();

            if (p["watch"] is JArray w) spec.Watch = w.Select(x => x.ToString()).ToList();
            else if (p["watch"] != null && p["watch"].Type != JTokenType.Null) return "'watch' must be a list of hook names.";

            if (p["until"] is JObject until)
            {
                spec.UntilHook = until["hook"]?.ToString();
                spec.UntilOp = until["op"]?.ToString() ?? "==";
                spec.UntilValue = until["value"];
                if (string.IsNullOrEmpty(spec.UntilHook)) return "'until.hook' is required.";
                if (!PlaytestJson.Operators.Contains(spec.UntilOp)) return $"'until.op' must be one of {string.Join(" ", PlaytestJson.Operators)}.";
            }
            else if (p["until"] != null && p["until"].Type != JTokenType.Null) return "'until' must be an object {hook, op, value}.";

            var cap = p["capture"];
            if (cap is JArray frames)
            {
                foreach (var f in frames)
                {
                    int rel = f.Value<int>();
                    if (rel < 0 || rel >= spec.Frames) return $"capture frame {rel} is outside 0..{spec.Frames - 1}.";
                    spec.CaptureFrames.Add(rel);
                }
            }
            else if (cap != null && cap.Type != JTokenType.Null)
            {
                var mode = cap.ToString();
                if (mode == "end") spec.CaptureEnd = true;
                else if (mode != "none") return "'capture' must be \"none\", \"end\" or a list of frames.";
            }

            if (p["timeout_seconds"] != null && double.TryParse(p["timeout_seconds"].ToString(), out var to) && to > 0) spec.TimeoutS = to;
            return null;
        }

        public static Task<JObject> Start(StepSpec spec)
        {
            var tcs = new TaskCompletionSource<JObject>();
            if (s_Run != null) { tcs.SetResult(Error("a play_step is already running")); return tcs.Task; }
            if (!PlaytestSession.Active) { tcs.SetResult(Error("no active play session; call play_session start first")); return tcs.Task; }

            PlaytestHookDiscovery.Ensure();
            var readErr = ReadableError(spec.UntilHook);
            if (readErr == null && spec.Watch != null) readErr = spec.Watch.Select(ReadableError).FirstOrDefault(e => e != null);
            if (readErr != null) { tcs.SetResult(Error(readErr)); return tcs.Task; }

            s_Run = new Run { Spec = spec, Tcs = tcs, Clock = Stopwatch.StartNew() };
            if (!EditorApplication.isPaused) EditorApplication.isPaused = true;
            EditorApplication.update += Tick;
            return tcs.Task;
        }

        private static string ReadableError(string hook)
        {
            if (hook == null) return null;
            var kind = GameHooks.KindOf(hook);
            if (kind == null) return GameHooks.UnknownMessage(hook);
            return kind == GameHooks.KindState ? null : GameHooks.NotReadableMessage(hook);
        }

        public static void Abort(string reason)
        {
            if (s_Run != null) Complete("error", reason);
        }

        private static JObject Error(string message) => new JObject { ["error"] = message };

        private static void Begin(Run r)
        {
            r.F0 = Time.frameCount;
            r.Target = r.F0 + r.Spec.Frames;
            if (r.Spec.Input != null)
            {
                foreach (JObject e in r.Spec.Input)
                {
                    int rel = e["frame"]?.Value<int?>() ?? 0;
                    var err = PlaytestInput.Schedule(r.F0 + 1 + rel, e);
                    if (err != null)
                    {
                        PlaytestInput.ClearQueue();
                        Complete(null, "input: " + err);
                        return;
                    }
                }
            }
            foreach (var rel in r.Spec.CaptureFrames) r.CaptureAt.Add(r.F0 + 1 + rel);

            PlaytestDriver.Autopilot = r.Spec.Autopilot;
            PlaytestDriver.ClearErrorFlag();
            PlaytestDriver.ClearAutopilotError();
            Application.logMessageReceivedThreaded += OnLog;
            r.Phase = "running";
            NextSegment(r);
        }

        private static void NextSegment(Run r)
        {
            int now = Time.frameCount;
            int stop = r.Target;
            foreach (var f in r.CaptureAt) if (f > now && f < stop) stop = f;
            r.SegmentStart = now;
            PlaytestDriver.Arm(stop, r.Spec.UntilHook, r.Spec.UntilOp, r.Spec.UntilValue);
            EditorApplication.isPaused = false;
        }

        private static void Tick()
        {
            var r = s_Run;
            if (r == null) { EditorApplication.update -= Tick; return; }
            try
            {
                if (!EditorApplication.isPlaying || !PlaytestDriver.Installed) { Complete("error", "play mode ended while stepping"); return; }
                if (r.Clock.Elapsed.TotalSeconds > r.Spec.TimeoutS)
                {
                    EditorApplication.isPaused = true;
                    Complete("error", $"timed out after {r.Spec.TimeoutS:0.#}s at frame {Time.frameCount - r.F0}/{r.Spec.Frames} (pass timeout_seconds for long steps)");
                    return;
                }
                if (r.Phase == "pausing")
                {
                    if (EditorApplication.isPaused) Begin(r);
                    return;
                }
                if (!EditorApplication.isPaused || PlaytestDriver.Armed || Time.frameCount <= r.SegmentStart) return;

                int f = Time.frameCount;
                if (r.CaptureAt.Remove(f)) r.Captures.Add(CaptureEntry(r, 0));
                if (PlaytestDriver.ErrorBreak) Complete("error", null);
                else if (PlaytestDriver.UntilHit) Complete("until", null);
                else if (f >= r.Target) Complete("frames", null);
                else NextSegment(r);
            }
            catch (Exception e)
            {
                Complete("error", $"{e.GetType().Name}: {e.Message}");
            }
        }

        private static JObject CaptureEntry(Run r, int inlineSize)
        {
            var cam = PlaytestCapture.FindCamera(r.Spec.Camera, out var err);
            if (cam == null) return new JObject { ["frame"] = Time.frameCount, ["error"] = err };
            var c = PlaytestCapture.Capture(cam, CaptureDir, r.Spec.CaptureLabel, inlineSize, r.Spec.Jpeg);
            var o = new JObject { ["frame"] = c.Frame, ["path"] = c.Path };
            if (inlineSize > 0)
            {
                o["width"] = c.ImageWidth;
                o["height"] = c.ImageHeight;
                o["full_width"] = c.Width;
                o["full_height"] = c.Height;
                o["image_base64"] = c.ImageBase64;
                o["mime"] = c.Mime;
            }
            return o;
        }

        private static void OnLog(string condition, string stackTrace, LogType type)
        {
            var r = s_Run;
            if (r == null) return;
            lock (r.Lock)
            {
                if (type == LogType.Warning) r.Warnings++;
                else if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert)
                {
                    r.Errors++;
                    if (r.FirstErrors.Count < 3) r.FirstErrors.Add($"{type}: {condition}");
                    PlaytestDriver.NotifyErrorLogged();
                }
            }
        }

        private static void Complete(string stoppedBy, string error)
        {
            var r = s_Run;
            s_Run = null;
            EditorApplication.update -= Tick;
            Application.logMessageReceivedThreaded -= OnLog;
            PlaytestDriver.Disarm();
            PlaytestDriver.Autopilot = false;
            if (r == null) return;

            if (stoppedBy == null)
            {
                r.Tcs.TrySetResult(Error(error));
                return;
            }

            var data = new JObject();
            try
            {
                data["frame"] = Time.frameCount;
                data["frames_stepped"] = Time.frameCount - r.F0;
                data["time"] = PlaytestJson.ToJson(Time.time);
                data["stopped_by"] = stoppedBy;
                if (error != null) data["stop_reason"] = error;
                if (PlaytestDriver.AutopilotError != null) data["autopilot_error"] = PlaytestDriver.AutopilotError;
                if (r.Spec.Autopilot && !GameHooks.Exists("autopilot")) data["autopilot_missing"] = true;
                data["state"] = ReadState(r.Spec.Watch);
                lock (r.Lock)
                {
                    data["errors"] = r.Errors;
                    data["warnings"] = r.Warnings;
                    data["first_errors"] = new JArray(r.FirstErrors);
                }
                if (r.Spec.CaptureEnd && EditorApplication.isPlaying)
                {
                    var end = CaptureEntry(r, r.Spec.InlineImage ? r.Spec.ImageMaxSize : 0);
                    if (end["image_base64"] != null) data["image"] = end;
                    r.Captures.Add(new JObject { ["frame"] = end["frame"], ["path"] = end["path"] });
                }
                data["captures"] = r.Captures;
            }
            catch (Exception e)
            {
                data["result_error"] = $"{e.GetType().Name}: {e.Message}";
            }
            r.Tcs.TrySetResult(data);
        }

        public static JObject ReadState(List<string> names)
        {
            var state = new JObject();
            foreach (var n in names ?? GameHooks.StateNames())
                state[n] = GameHooks.TryGet(n, out var v, out var err) ? v : new JObject { ["error"] = err };
            return state;
        }
    }
}
