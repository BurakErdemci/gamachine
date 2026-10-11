using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Playtest;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Profiling;

namespace MCPForUnity.Editor.Tools.Playtest
{
    [McpForUnityTool("play_run", AutoRegister = false, Group = "playtest")]
    public static class PlayRunTool
    {
        public static async Task<object> HandleCommand(JObject @params)
        {
            @params ??= new JObject();
            string action = @params["action"]?.ToString().ToLowerInvariant() ?? "start";
            if (action == "start")
            {
                string error = PlaytestRun.Start(@params, out var data);
                return error == null ? (object)new SuccessResponse("play_run started; poll status or wait.", data) : new ErrorResponse(error);
            }
            if (action == "stop") await PlaytestRun.Stop();
            else if (action != "status") return new ErrorResponse("action must be start, status or stop.");
            var status = PlaytestRun.Status();
            if (status == null) return new ErrorResponse("No play_run job in this editor session; a script reload or play mode exit ends a running job.");
            if (status["status"].ToString() == "running") return new PendingResponse("play_run is running; poll status.", 1.0, status);
            return new SuccessResponse("play_run finished.", status);
        }
    }

    internal static class PlaytestRun
    {
        private sealed class Spec
        {
            public double Seconds, SampleSeconds, CaptureSeconds;
            public int Frames, SampleFrames, CaptureFrames;
            public double Dt;
            public List<int> Seeds;
            public List<string> Watch;
            public StepSpec Step;
            public bool StopOnError;
            public string Camera;
            public int Runs => Seeds?.Count ?? 1;
        }

        private sealed class Job
        {
            public string Id = Guid.NewGuid().ToString("N");
            public Spec Spec;
            public Stopwatch Clock = Stopwatch.StartNew();
            public TaskCompletionSource<bool> Completion = new TaskCompletionSource<bool>();
            public JArray Results = new JArray();
            public JObject Current;
            public int RunIndex, StartFrame;
            public bool Done;
            public string EndBy, EndReason;
            public JObject Images = new JObject();
        }

        private static Job s_Job;
        public static bool Running => s_Job != null && !s_Job.Done;

        private static bool Present(JToken token) => token != null && token.Type != JTokenType.Null;
        private static bool Number(JToken token, double min, double max, out double value)
        {
            value = 0;
            if (token == null || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)) return false;
            value = token.Value<double>();
            return !double.IsNaN(value) && !double.IsInfinity(value) && value >= min && value <= max;
        }

        private static string Parse(JObject p, out Spec spec)
        {
            spec = new Spec();
            if (!Number(p["seconds"] ?? new JValue(120), 1, 1800, out spec.Seconds)) return "seconds must be a finite number in 1..1800.";
            if (!Number(p["sample_seconds"] ?? new JValue(5), 0.5, 60, out spec.SampleSeconds)) return "sample_seconds must be a finite number in 0.5..60.";
            if (!Number(p["capture_every_seconds"] ?? new JValue(Math.Max(1, spec.Seconds / 8)), 1, double.MaxValue, out spec.CaptureSeconds))
                return "capture_every_seconds must be a finite number >= 1.";
            foreach (string name in new[] { "autopilot", "stop_on_error" })
                if (p[name] != null && p[name].Type != JTokenType.Boolean) return name + " must be a boolean.";
            spec.StopOnError = p["stop_on_error"]?.Value<bool>() ?? true;
            if (Present(p["seeds"]))
            {
                if (!(p["seeds"] is JArray seeds) || seeds.Count < 1 || seeds.Count > 50) return "seeds must contain 1..50 integers.";
                spec.Seeds = new List<int>();
                foreach (var seed in seeds)
                {
                    if (seed.Type != JTokenType.Integer || !long.TryParse(seed.ToString(), out long n) || n < int.MinValue || n > int.MaxValue)
                        return "seeds must contain signed 32-bit integers.";
                    spec.Seeds.Add((int)n);
                }
                if (spec.Seconds * seeds.Count > 3600) return "seconds * number of seeds must be <= 3600.";
                if (GameHooks.KindOf("level.restart") != GameHooks.KindAction)
                    return "Batch runs need a level.restart action hook (GameHooks.Action(\"level.restart\", args => ...)) so each seed restarts without leaving play mode.";
            }
            if ((p["autopilot"]?.Value<bool>() ?? true) && GameHooks.KindOf("autopilot") != GameHooks.KindAction)
                return "autopilot requires an 'autopilot' action hook before starting.";
            spec.Watch = GameHooks.StateNames();
            if (Present(p["watch"]))
            {
                if (!(p["watch"] is JArray names) || names.Any(n => n.Type != JTokenType.String || string.IsNullOrWhiteSpace(n.Value<string>())))
                    return "watch must be a list of non-empty hook names.";
                spec.Watch = names.Select(n => n.Value<string>().Trim()).ToList();
            }
            if (spec.Watch.Count > 20) return "watch must contain at most 20 hooks; select watch explicitly when the game has more state hooks.";
            foreach (string hook in spec.Watch)
                if (GameHooks.KindOf(hook) != GameHooks.KindState) return $"watch hook '{hook}' must exist and be readable state.";
            if (Present(p["camera"]) && (p["camera"].Type != JTokenType.String || string.IsNullOrWhiteSpace(p["camera"].Value<string>())))
                return "camera must be a non-empty string.";
            spec.Camera = Present(p["camera"]) ? p["camera"].Value<string>() : null;
            if (PlaytestCapture.FindCamera(spec.Camera, out string cameraError) == null) return cameraError;
            spec.Dt = PlaytestSession.FixedDt;
            if (spec.Dt <= 0 || double.IsNaN(spec.Dt) || double.IsInfinity(spec.Dt)) return "play session has no valid fixed timestep.";
            double frames = Math.Ceiling(spec.Seconds / spec.Dt);
            if (frames > int.MaxValue) return "seconds exceeds the frame budget for this session's fixed timestep.";
            spec.Frames = (int)frames;
            spec.SampleFrames = (int)Math.Min(int.MaxValue, Math.Max(1, Math.Ceiling(spec.SampleSeconds / spec.Dt)));
            spec.CaptureFrames = (int)Math.Min(int.MaxValue, Math.Max(1, Math.Ceiling(spec.CaptureSeconds / spec.Dt)));
            int firstFrames = Math.Min(600, Math.Min(spec.Frames, spec.SampleFrames));
            if (spec.Seeds == null) firstFrames = Math.Min(firstFrames, spec.CaptureFrames);
            if (Present(p["input"]))
            {
                if (!(p["input"] is JArray inputs)) return "input must be a list.";
                if (inputs.Count > 0 && !PlaytestInput.Supported) return PlaytestInput.UnsupportedMessage;
                foreach (var item in inputs)
                {
                    if (!(item is JObject e)) return "each input must be an object.";
                    if (e["frame"]?.Type != JTokenType.Integer || !Number(e["frame"], 0, firstFrames - 1, out _))
                        return $"input.frame must be an integer in 0..{firstFrames - 1} (the first segment).";
                    var controls = new[] { "key", "button", "axis" }.Where(k => Present(e[k])).ToList();
                    if (controls.Count != 1 || e[controls[0]].Type != JTokenType.String || string.IsNullOrWhiteSpace(e[controls[0]].Value<string>()))
                        return "input needs exactly one non-empty key, button or axis.";
                    if (e["do"]?.Type != JTokenType.String || !new[] { "press", "release", "tap" }.Contains(e["do"].Value<string>()))
                        return "input.do must be press, release or tap.";
                    if (Present(e["value"]) && !Number(e["value"], -float.MaxValue, float.MaxValue, out _)) return "input.value must be a finite number.";
                    if (e.Properties().Any(x => !new[] { "frame", "do", "value", "key", "button", "axis" }.Contains(x.Name))) return "input has unknown keys.";
                }
            }
            if (Present(p["until"]))
            {
                if (!(p["until"] is JObject until) || until["hook"]?.Type != JTokenType.String || string.IsNullOrWhiteSpace(until["hook"].Value<string>()))
                    return "until must be an object with a non-empty hook.";
                if (until["op"]?.Type != JTokenType.String || !PlaytestJson.Operators.Contains(until["op"].Value<string>())) return "until.op must be == != < > <= >=.";
                if (until["value"] == null) return "until.value is required.";
                if (GameHooks.KindOf(until["hook"].Value<string>()) != GameHooks.KindState) return "until.hook must exist and be readable state.";
            }
            var stepParams = new JObject { ["frames"] = firstFrames, ["autopilot"] = p["autopilot"] ?? new JValue(true) };
            if (Present(p["input"])) stepParams["input"] = p["input"].DeepClone();
            if (Present(p["until"])) stepParams["until"] = p["until"].DeepClone();
            string error = PlaytestStepper.Parse(stepParams, out spec.Step);
            if (error != null) return error;
            spec.Step.Watch = new List<string>();
            return null;
        }

        public static string Start(JObject p, out JObject data)
        {
            data = null;
            if (!PlaytestSession.Active) return "no active play session; call play_session start first.";
            if (PlaytestSession.Busy || PlaytestStepper.Running || Running) return "a play session operation, step or play_run job is already running.";
            PlaytestHookDiscovery.Ensure();
            string error = Parse(p, out var spec);
            if (error != null) return error;
            var job = s_Job = new Job { Spec = spec };
            EditorApplication.update += Tick;
            EditorApplication.playModeStateChanged += OnPlayMode;
            AssemblyReloadEvents.beforeAssemblyReload += OnReload;
            // Starting on the next editor update lets the command return before any game hook runs.
            EditorApplication.delayCall += () => { _ = Execute(job); };
            data = new JObject { ["job_id"] = job.Id, ["runs"] = spec.Runs, ["seconds"] = spec.Seconds, ["status"] = "running" };
            return null;
        }

        private static void End(Job job, string by, string reason)
        {
            if (job == null || job.Done || job.EndBy != null) return;
            job.EndBy = by;
            job.EndReason = reason;
            if (EditorApplication.isPlaying) EditorApplication.isPaused = true;
            PlaytestStepper.Abort(reason);
            PlaytestDriver.Autopilot = false;
        }

        private static void Tick()
        {
            if (!Running) return;
            if (s_Job.Clock.Elapsed.TotalSeconds >= 3600) End(s_Job, "wall_clock", "Job reached the 3600 s wall clock limit.");
            else if (!PlaytestSession.Active) End(s_Job, "aborted", "Play session ended while play_run was running.");
        }
        private static void OnPlayMode(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.EnteredEditMode)
                End(s_Job, "aborted", "Play mode exited while play_run was running.");
        }
        private static void OnReload() => End(s_Job, "aborted", "Scripts reloaded while play_run was running.");

        public static async Task Stop()
        {
            if (!Running) return;
            var job = s_Job;
            End(job, "stopped", "Stopped by play_run stop.");
            await job.Completion.Task;
        }

        private static async Task Execute(Job job)
        {
            bool previousStopOnError = PlaytestDriver.StopOnError;
            try
            {
                PlaytestDriver.StopOnError = job.Spec.StopOnError;
                for (int index = 0; index < job.Spec.Runs; index++)
                {
                    job.RunIndex = index;
                    await RunOne(job, index);
                    if (job.EndBy != null) break;
                }
                BuildImages(job);
            }
            catch (Exception ex)
            {
                job.Images["result_error"] = ex.GetType().Name + ": " + ex.Message;
                if (job.Current != null && !job.Results.Contains(job.Current))
                {
                    job.Current["stopped_by"] = "aborted";
                    job.Current["stop_reason"] = ex.Message;
                    job.Results.Add(job.Current);
                }
            }
            finally
            {
                PlaytestDriver.StopOnError = previousStopOnError;
                PlaytestDriver.Autopilot = false;
                EditorApplication.update -= Tick;
                EditorApplication.playModeStateChanged -= OnPlayMode;
                AssemblyReloadEvents.beforeAssemblyReload -= OnReload;
                job.Clock.Stop();
                job.Done = true;
                job.Completion.TrySetResult(true);
            }
        }

        private static async Task RunOne(Job job, int index)
        {
            var spec = job.Spec;
            int? seed = spec.Seeds == null ? (int?)null : spec.Seeds[index];
            var clock = Stopwatch.StartNew();
            long memory = UnityEngine.Profiling.Profiler.GetTotalAllocatedMemoryLong();
            var result = job.Current = new JObject { ["index"] = index, ["seed"] = seed.HasValue ? new JValue(seed.Value) : JValue.CreateNull(),
                ["errors"] = 0, ["warnings"] = 0, ["timeline"] = new JArray(), ["captures"] = new JArray(), ["first_errors"] = new JArray() };
            var errors = new Dictionary<string, JObject>();
            job.StartFrame = Time.frameCount;
            int frames = 0, nextSample = spec.SampleFrames, timed = 0;
            long nextCapture = spec.CaptureFrames;
            string stoppedBy = "seconds", reason = null;
            try
            {
                if (seed.HasValue && job.EndBy == null)
                {
                    UnityEngine.Random.InitState(seed.Value);
                    if (!GameHooks.TryCall("level.restart", new JObject { ["seed"] = seed.Value }, out _, out string restartError))
                        throw new InvalidOperationException("level.restart: " + restartError);
                }
                Sample(result, spec, 0);
                while (frames < spec.Frames && job.EndBy == null)
                {
                    if (!PlaytestSession.Active) { End(job, "aborted", "Play session ended."); break; }
                    int segment = Math.Min(600, Math.Min(spec.Frames - frames, nextSample - frames));
                    if (spec.Seeds == null && timed < 8) segment = (int)Math.Min(segment, nextCapture - frames);
                    var step = new StepSpec { Frames = segment, Autopilot = spec.Step.Autopilot,
                        Input = frames == 0 ? spec.Step.Input : null, UntilHook = spec.Step.UntilHook,
                        UntilOp = spec.Step.UntilOp, UntilValue = spec.Step.UntilValue, Watch = new List<string>(),
                        TimeoutS = Math.Min(25 + segment / 30.0, Math.Max(0.1, 3600 - job.Clock.Elapsed.TotalSeconds)),
                        ErrorObserver = (message, frame, exact) =>
                        {
                            lock (errors)
                            {
                                if (errors.TryGetValue(message, out var known)) known["count"] = known["count"].Value<int>() + 1;
                                else if (errors.Count < 20) errors[message] = new JObject { ["message"] = message,
                                    ["frame"] = frame, ["count"] = 1, ["frame_source"] = exact ? "game frame" : "last editor-observed frame (background-thread log)" };
                            }
                        } };
                    int before = Time.frameCount;
                    var data = await PlaytestStepper.Start(step);
                    frames += Math.Max(0, Time.frameCount - before);
                    result["frames"] = frames;
                    result["errors"] = result["errors"].Value<int>() + (data["errors"]?.Value<int>() ?? 0);
                    result["warnings"] = result["warnings"].Value<int>() + (data["warnings"]?.Value<int>() ?? 0);
                    if (data["autopilot_error"] != null) result["autopilot_error"] = data["autopilot_error"].DeepClone();
                    if (job.EndBy != null) break;
                    if (spec.StopOnError && (data["errors"]?.Value<int>() ?? 0) > 0)
                    {
                        stoppedBy = "error";
                        reason = data["stop_reason"]?.ToString();
                        Capture(job, result, frames, "error");
                        break;
                    }
                    if (data["error"] != null || data["result_error"] != null || data["stopped_by"]?.ToString() == "error")
                    {
                        stoppedBy = "aborted";
                        reason = (data["error"] ?? data["stop_reason"] ?? data["result_error"])?.ToString() ?? "Segment failed.";
                        End(job, stoppedBy, reason);
                        break;
                    }
                    if (data["stopped_by"]?.ToString() == "until") { stoppedBy = "until"; break; }
                    if (frames >= nextSample)
                    {
                        Sample(result, spec, frames);
                        nextSample = (int)Math.Min(int.MaxValue, (long)frames + spec.SampleFrames);
                    }
                    if (spec.Seeds == null && timed < 8 && frames >= nextCapture)
                    {
                        Capture(job, result, frames, "timed");
                        timed++;
                        nextCapture += spec.CaptureFrames;
                    }
                    if (frames == 0) throw new InvalidOperationException("Segment advanced no frames.");
                }
            }
            catch (Exception ex) { stoppedBy = "aborted"; reason = ex.GetType().Name + ": " + ex.Message; End(job, stoppedBy, reason); }
            finally
            {
                frames = Math.Max(frames, Math.Max(0, Time.frameCount - job.StartFrame));
                result["stopped_by"] = job.EndBy ?? stoppedBy;
                if ((job.EndReason ?? reason) != null) result["stop_reason"] = job.EndReason ?? reason;
                lock (errors) result["first_errors"] = new JArray(errors.Values.Select(e => e.DeepClone()));
                Sample(result, spec, frames);
                result["final_state"] = PlaytestStepper.ReadState(spec.Watch);
                Capture(job, result, frames, "end");
                clock.Stop();
                result["frames"] = frames;
                result["game_seconds"] = frames * spec.Dt;
                result["wall_seconds"] = clock.Elapsed.TotalSeconds;
                result["speed_x"] = clock.Elapsed.TotalSeconds > 0 ? frames * spec.Dt / clock.Elapsed.TotalSeconds : 0;
                result["editor_ms_per_frame"] = frames > 0 ? clock.Elapsed.TotalMilliseconds / frames : 0;
                result["timing_source"] = "editor-side wall clock, including samples and captures";
                result["gc_alloc_mb"] = (UnityEngine.Profiling.Profiler.GetTotalAllocatedMemoryLong() - memory) / (1024.0 * 1024.0);
                result["memory_source"] = "UnityEngine.Profiling.Profiler.GetTotalAllocatedMemoryLong delta (allocated memory, not cumulative GC allocations)";
                job.Results.Add(result);
            }
        }

        private static void Sample(JObject result, Spec spec, int frames)
        {
            var timeline = (JArray)result["timeline"];
            if (timeline.Count > 0 && timeline.Last["t"].Value<double>() == frames * spec.Dt) return;
            if (timeline.Count >= 300)
                for (int i = timeline.Count - 1; i >= 0; i--) if (i % 2 == 1) timeline.RemoveAt(i);
            timeline.Add(new JObject { ["t"] = frames * spec.Dt, ["frame"] = Time.frameCount, ["values"] = PlaytestStepper.ReadState(spec.Watch) });
        }

        private static void Capture(Job job, JObject result, int frames, string why)
        {
            if (!EditorApplication.isPlaying) return;
            var entry = new JObject { ["frame"] = Time.frameCount, ["t"] = frames * job.Spec.Dt, ["why"] = why };
            try
            {
                var camera = PlaytestCapture.FindCamera(job.Spec.Camera, out string error);
                if (camera == null) throw new InvalidOperationException(error);
                string seed = result["seed"].Type == JTokenType.Null ? "current" : result["seed"].ToString();
                var capture = PlaytestCapture.Capture(camera, PlaytestStepper.CaptureDir,
                    $"run{job.RunIndex}_s{seed}_{job.Id}_{why}", 0, false);
                entry["path"] = capture.Path;
            }
            catch (Exception ex) { entry["error"] = ex.Message; }
            ((JArray)result["captures"]).Add(entry);
        }

        private static void BuildImages(Job job)
        {
            var captures = job.Results.OfType<JObject>().SelectMany(r => ((JArray)r["captures"]).OfType<JObject>())
                .Where(c => c["path"] != null && (job.Spec.Seeds == null || c["why"].ToString() == "end")).ToList();
            // In batch mode the run order is also the chronological frame order, even if a restart resets game time.
            if (job.Spec.Seeds == null) captures = captures.OrderBy(c => c["frame"].Value<int>()).ToList();
            var selected = captures.Take(12).ToList();
            if (selected.Count > 0)
            {
                string path = Path.Combine(PlaytestStepper.CaptureDir, $"playrun_{job.Id}_sheet.jpg");
                var sheet = PlaytestCapture.ComposeContactSheet(selected.Select(c => c["path"].Value<string>()).ToList(), path);
                job.Images["sheet_path"] = sheet.Path;
                job.Images["sheet_order"] = new JArray(selected.Select(c => c.DeepClone()));
                job.Images["image_base64"] = sheet.ImageBase64;
                job.Images["mime"] = sheet.Mime;
            }
            var lastRun = job.Results.Last as JObject;
            var last = (lastRun?["captures"] as JArray)?.OfType<JObject>().LastOrDefault(c => c["why"].ToString() == "end" && c["path"] != null);
            if (last != null)
            {
                var frame = PlaytestCapture.LoadInlineImage(last["path"].Value<string>(), 640);
                job.Images["last_frame"] = new JObject { ["path"] = frame.Path, ["width"] = frame.ImageWidth, ["height"] = frame.ImageHeight,
                    ["image_base64"] = frame.ImageBase64, ["mime"] = frame.Mime };
            }
        }

        private static JObject Stats(IEnumerable<double> numbers)
        {
            var values = numbers.ToList();
            return new JObject { ["mean"] = values.Average(), ["min"] = values.Min(), ["max"] = values.Max() };
        }

        private static JObject Summary(Job job)
        {
            var results = job.Results.OfType<JObject>().ToList();
            var counts = new JObject();
            foreach (var group in results.GroupBy(r => r["stopped_by"].Value<string>())) counts[group.Key] = group.Count();
            var hooks = new JObject();
            foreach (string name in job.Spec.Watch)
            {
                var tokens = results.Select(r => r["final_state"]?[name]).ToList();
                if (tokens.Count > 0 && tokens.All(t => t != null && (t.Type == JTokenType.Integer || t.Type == JTokenType.Float)
                    && !double.IsNaN(t.Value<double>()) && !double.IsInfinity(t.Value<double>())))
                    hooks[name] = Stats(tokens.Select(t => t.Value<double>()));
            }
            return new JObject { ["runs"] = results.Count, ["by_stopped_by"] = counts,
                ["game_seconds"] = results.Count > 0 ? Stats(results.Select(r => r["game_seconds"]?.Value<double>() ?? 0)) : null,
                ["errors_total"] = results.Sum(r => r["errors"].Value<int>()),
                ["runs_with_errors"] = results.Count(r => r["errors"].Value<int>() > 0), ["final_state"] = hooks };
        }

        public static JObject Status()
        {
            var job = s_Job;
            if (job == null) return null;
            int frames = job.Current?["frames"]?.Value<int>() ?? 0;
            if (!job.Done && job.Current != null) frames = Math.Max(frames, Math.Max(0, Time.frameCount - job.StartFrame));
            var data = new JObject { ["job_id"] = job.Id, ["status"] = job.Done ? "done" : "running",
                ["run_index"] = job.RunIndex, ["runs"] = job.Spec.Runs, ["elapsed_wall_s"] = job.Clock.Elapsed.TotalSeconds,
                ["progress"] = new JObject { ["run"] = job.RunIndex, ["game_seconds"] = frames * job.Spec.Dt } };
            if (job.Done)
            {
                data["results"] = job.Results.DeepClone();
                if (job.Spec.Runs > 1) data["summary"] = Summary(job);
                foreach (var property in job.Images.Properties()) data[property.Name] = property.Value.DeepClone();
            }
            return data;
        }
    }
}
