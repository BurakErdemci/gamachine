using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Playtest;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.Playtest
{
    /// <summary>
    /// run_playtest job: for each *.playtest.json scenario, start a session (scene, seed, fixed_dt), run its steps,
    /// check expects, run the optional free-running perf segment, stop. Job state lives in SessionState because every
    /// scenario enters play mode (domain reload); only the in-play part runs as an in-memory task.
    /// </summary>
    [InitializeOnLoad]
    internal static class PlaytestRunner
    {
        private const string Key = "MCPForUnity.Playtest.Runner";
        private const string DefaultGlob = "Assets/Playtests/**/*.playtest.json";

        // Bounds on the editor time one request can claim (the four LoopLab scenarios step 737 frames in total).
        internal const int MaxScenariosPerJob = 100;
        internal const int MaxFramesPerScenario = 36000;
        internal const int MaxFramesPerJob = 108000;
        internal const double MaxPerfSecondsPerScenario = 120;
        internal const double MaxPerfSecondsPerJob = 600;

        [Serializable]
        private class Job
        {
            public string jobId = "";
            public string[] files = new string[0];
            public int index;
            public string phase = "";
            public string results = "[]";
            public string current = "";
            public string originalScene = "";
            public bool hasSeedOverride;
            public int seedOverride;
            public double started;
            public double finished;
            public bool done;
        }

        private static Job s_Job;
        private static Task<JObject> s_StepsTask;

        static PlaytestRunner()
        {
            var json = SessionState.GetString(Key, "");
            if (!string.IsNullOrEmpty(json))
            {
                try { s_Job = JsonUtility.FromJson<Job>(json); }
                catch { s_Job = null; }
            }
            EditorApplication.update += Tick;
        }

        private static void Save() => SessionState.SetString(Key, s_Job == null ? "" : JsonUtility.ToJson(s_Job));

        private static double Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

        public static bool Running => s_Job != null && !s_Job.done;

        public static string Start(string path, string glob, int? seedOverride, out string jobId)
        {
            jobId = null;
            if (Running) return $"run_playtest job {s_Job.jobId} is still running; poll status.";
            if (PlaytestSession.Busy || PlaytestSession.Active || EditorApplication.isPlaying)
                return "Unity is in play mode or a play session is open; stop it before run_playtest.";
            var files = FindScenarios(path, glob, out var err);
            if (err != null) return err;
            if (files.Count == 0) return $"No scenario files matched {(path ?? glob ?? DefaultGlob)}.";
            if (files.Count > MaxScenariosPerJob)
                return $"{files.Count} scenario files matched; one run_playtest job runs at most {MaxScenariosPerJob}. Narrow path or glob.";
            int totalFrames = 0;
            double totalPerf = 0;
            foreach (var file in files)
            {
                // Other load errors are reported as that scenario's result when it runs, as before.
                var sc = LoadScenario(file, out var loadErr, out int frames);
                if (frames > MaxFramesPerScenario) return $"{file}: {loadErr}";
                totalFrames += frames;
                totalPerf += PerfSeconds(sc);
            }
            if (totalFrames > MaxFramesPerJob)
                return $"The matched scenarios step {totalFrames} frames in total; one run_playtest job steps at most {MaxFramesPerJob}. Split the run with path or glob.";
            if (totalPerf > MaxPerfSecondsPerJob)
                return $"The matched scenarios request {totalPerf:0.#} s of perf sampling; one run_playtest job allows at most {MaxPerfSecondsPerJob:0} s. Split the run with path or glob.";

            s_Job = new Job
            {
                jobId = Guid.NewGuid().ToString("N").Substring(0, 12),
                files = files.ToArray(),
                phase = "load",
                originalScene = SceneManager.GetActiveScene().path,
                hasSeedOverride = seedOverride.HasValue,
                seedOverride = seedOverride ?? 0,
                started = Now,
            };
            s_StepsTask = null;
            Save();
            jobId = s_Job.jobId;
            return null;
        }

        public static JObject StatusData(bool includeResults)
        {
            if (s_Job == null) return null;
            var d = new JObject
            {
                ["job_id"] = s_Job.jobId,
                ["status"] = s_Job.done ? "done" : "running",
                ["done"] = Math.Min(s_Job.index, s_Job.files.Length),
                ["total"] = s_Job.files.Length,
            };
            if (!s_Job.done)
            {
                d["current"] = s_Job.index < s_Job.files.Length ? s_Job.files[s_Job.index] : null;
                d["phase"] = s_Job.phase;
                d["elapsed_s"] = Math.Round(Now - s_Job.started, 1);
                return d;
            }
            var results = JArray.Parse(s_Job.results);
            d["passed"] = results.Count(r => r["passed"]?.Value<bool>() == true);
            d["failed"] = results.Count(r => r["passed"]?.Value<bool>() != true);
            d["seconds"] = Math.Round(s_Job.finished - s_Job.started, 1);
            if (includeResults) d["results"] = results;
            return d;
        }

        private static string ProjectRoot => Path.GetFullPath(Path.GetDirectoryName(Application.dataPath) ?? ".");

        /// <summary>
        /// Full path of <paramref name="path"/> (project-relative or rooted) when it lies under the project's Assets
        /// folder and nothing below Assets on the way is a junction or symbolic link; otherwise null and an error.
        /// </summary>
        internal static string ConfineToAssets(string projectRoot, string path, out string error)
        {
            error = null;
            char[] seps = { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar };
            string assets = Path.GetFullPath(Path.Combine(projectRoot, "Assets")).TrimEnd(seps);
            string full;
            try
            {
                full = Path.GetFullPath(Path.Combine(projectRoot, path ?? "")).TrimEnd(seps);
            }
            catch (Exception e)
            {
                error = $"invalid path '{path}': {e.Message}";
                return null;
            }
            var cmp = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            if (!full.StartsWith(assets + Path.DirectorySeparatorChar, cmp) && !string.Equals(full, assets, cmp))
            {
                error = $"path '{path}' is outside the project's Assets folder.";
                return null;
            }
            // Links are refused, not followed: this runtime has no API that reads a link's target, and a link below
            // Assets can point anywhere on disk while the path still looks project-relative.
            for (string p = full; p != null && p.Length > assets.Length; p = Path.GetDirectoryName(p))
            {
                if ((File.Exists(p) || Directory.Exists(p)) && (File.GetAttributes(p) & FileAttributes.ReparsePoint) != 0)
                {
                    error = $"path '{path}' goes through a junction or symbolic link ({p}); playtest files must be real files under Assets.";
                    return null;
                }
            }
            return full;
        }

        private static List<string> FindScenarios(string path, string glob, out string error)
        {
            error = null;
            string root = ProjectRoot;
            string Rel(string full) => full.Substring(root.Length).TrimStart('\\', '/').Replace('\\', '/');

            List<string> Confined(IEnumerable<string> files, out string err)
            {
                err = null;
                var list = new List<string>();
                foreach (var f in files)
                {
                    var full = ConfineToAssets(root, f, out err);
                    if (full == null) return null;
                    list.Add(Rel(full));
                }
                return list.OrderBy(x => x, StringComparer.Ordinal).ToList();
            }

            if (!string.IsNullOrEmpty(path))
            {
                string full = ConfineToAssets(root, path, out error);
                if (full == null) return null;
                if (File.Exists(full)) return new List<string> { Rel(full) };
                if (Directory.Exists(full))
                    return Confined(Directory.GetFiles(full, "*.playtest.json", SearchOption.AllDirectories), out error);
                error = $"path not found: {path}";
                return null;
            }

            var pattern = "^" + Regex.Escape((glob ?? DefaultGlob).Replace('\\', '/'))
                .Replace(@"\*\*/", "(.*/)?").Replace(@"\*\*", ".*").Replace(@"\*", "[^/]*").Replace(@"\?", "[^/]") + "$";
            var rx = new Regex(pattern, RegexOptions.IgnoreCase);
            return Confined(Directory.GetFiles(Path.Combine(root, "Assets"), "*.json", SearchOption.AllDirectories)
                .Where(f => rx.IsMatch(Rel(Path.GetFullPath(f)))), out error);
        }

        private static void Tick()
        {
            if (s_Job == null || s_Job.done) return;
            try
            {
                Advance();
            }
            catch (Exception e)
            {
                var cur = Current() ?? new JObject { ["scenario"] = CurrentFile() };
                cur["error"] = $"runner: {e.GetType().Name}: {e.Message}";
                cur["passed"] = false;
                SetCurrent(cur);
                s_Job.phase = "stop";
                Save();
            }
        }

        private static string CurrentFile() => s_Job.index < s_Job.files.Length ? s_Job.files[s_Job.index] : null;

        private static JObject Current() => string.IsNullOrEmpty(s_Job.current) ? null : JObject.Parse(s_Job.current);

        private static void SetCurrent(JObject o) => s_Job.current = o.ToString(Newtonsoft.Json.Formatting.None);

        private static void Advance()
        {
            switch (s_Job.phase)
            {
                case "load":
                {
                    // Same grace as play_session: the first scenario enters play mode, so let the start reply go out.
                    if (s_Job.index == 0 && Now - s_Job.started < 0.5) return;
                    if (s_Job.index >= s_Job.files.Length) { Finish(); return; }
                    string file = CurrentFile();
                    var sc = LoadScenario(file, out var err, out _);
                    var result = new JObject { ["scenario"] = sc?["name"]?.ToString() ?? Path.GetFileName(file), ["path"] = file };
                    SetCurrent(result);
                    if (err != null) { Fail(err, stop: false); return; }
                    string startErr = PlaytestSession.BeginStart(sc["scene"]?.ToString(), ScenarioSeed(sc), ScenarioDt(sc), true, 120, false);
                    if (startErr != null) { Fail("play_session start: " + startErr, stop: false); return; }
                    s_Job.phase = "starting";
                    Save();
                    return;
                }
                case "starting":
                {
                    if (PlaytestSession.Busy) return;
                    if (PlaytestSession.TakeOutcome(out _, out var isError, out var message) && isError)
                    {
                        Fail("play_session start: " + message, stop: true);
                        return;
                    }
                    var sc = LoadScenario(CurrentFile(), out _, out _);
                    s_StepsTask = RunScenario(sc, Current());
                    s_Job.phase = "steps";
                    Save();
                    return;
                }
                case "steps":
                {
                    if (s_StepsTask == null)
                    {
                        Fail("interrupted by a domain reload while stepping", stop: true);
                        return;
                    }
                    if (!s_StepsTask.IsCompleted) return;
                    var result = s_StepsTask.IsFaulted
                        ? WithError(Current(), s_StepsTask.Exception?.GetBaseException().Message)
                        : s_StepsTask.Result;
                    s_StepsTask = null;
                    SetCurrent(result);
                    s_Job.phase = "stop";
                    Save();
                    return;
                }
                case "stop":
                {
                    if (PlaytestSession.Busy) return;
                    PlaytestSession.BeginStop();
                    s_Job.phase = "stopping";
                    Save();
                    return;
                }
                case "stopping":
                {
                    if (PlaytestSession.Busy) return;
                    PlaytestSession.TakeOutcome(out _, out _, out _);
                    Append();
                    return;
                }
            }
        }

        private static JObject WithError(JObject o, string error)
        {
            o ??= new JObject();
            o["passed"] = false;
            o["error"] = error;
            return o;
        }

        private static void Fail(string error, bool stop)
        {
            SetCurrent(WithError(Current(), error));
            if (stop)
            {
                s_Job.phase = "stop";
                Save();
            }
            else Append();
        }

        private static void Append()
        {
            var results = JArray.Parse(s_Job.results);
            results.Add(Current());
            s_Job.results = results.ToString(Newtonsoft.Json.Formatting.None);
            s_Job.current = "";
            s_Job.index++;
            s_Job.phase = "load";
            Save();
        }

        private static void Finish()
        {
            var active = SceneManager.GetActiveScene();
            if (!string.IsNullOrEmpty(s_Job.originalScene) && active.path != s_Job.originalScene && !active.isDirty && !EditorApplication.isPlaying)
                UnityEditor.SceneManagement.EditorSceneManager.OpenScene(s_Job.originalScene, UnityEditor.SceneManagement.OpenSceneMode.Single);
            s_Job.done = true;
            s_Job.finished = Now;
            s_Job.phase = "done";
            Save();
        }

        private static int ScenarioSeed(JObject sc) => s_Job.hasSeedOverride ? s_Job.seedOverride : sc["seed"]?.Value<int?>() ?? 0;

        private static float ScenarioDt(JObject sc) => sc["fixed_dt"]?.Value<float?>() ?? 1f / 60f;

        /// <summary>Reads and validates a scenario; <paramref name="frames"/> is the sum of its steps' frames.</summary>
        private static JObject LoadScenario(string file, out string error, out int frames)
        {
            error = null;
            frames = 0;
            JObject sc;
            string full = ConfineToAssets(ProjectRoot, file, out error);
            if (full == null) return null;
            try
            {
                sc = JObject.Parse(File.ReadAllText(full));
            }
            catch (Exception e)
            {
                error = $"cannot read scenario: {e.Message}";
                return null;
            }
            if (sc["name"] == null) sc["name"] = Path.GetFileName(file).Replace(".playtest.json", "");
            if (!(sc["steps"] is JArray steps) || steps.Count == 0) { error = "scenario needs a non-empty 'steps' list"; return sc; }
            foreach (var st in steps)
            {
                if (!(st is JObject so)) { error = "every step must be an object"; return sc; }
                var perr = PlaytestStepper.Parse(so, out var spec);
                if (perr != null) { error = "step: " + perr; return sc; }
                frames += spec.Frames;
            }
            if (frames > MaxFramesPerScenario)
            {
                error = $"scenario steps {frames} frames; a scenario steps at most {MaxFramesPerScenario}.";
                return sc;
            }
            if (sc["expect"] != null && !(sc["expect"] is JArray)) { error = "'expect' must be a list"; return sc; }
            if (sc["perf"] != null && !(sc["perf"] is JObject)) { error = "'perf' must be an object {seconds}"; return sc; }
            return sc;
        }

        private static async Task<JObject> RunScenario(JObject sc, JObject result)
        {
            int frames = 0, errors = 0, warnings = 0;
            var firstErrors = new JArray();
            var captures = new JArray();
            string runError = null;
            double simStart = Time.timeAsDouble;

            foreach (JObject step in (JArray)sc["steps"])
            {
                PlaytestStepper.Parse(step, out var spec);
                spec.InlineImage = false;
                spec.CaptureLabel = result["scenario"]?.ToString() ?? "scenario";
                if (step["timeout_seconds"] == null) spec.TimeoutS = 600;
                var data = await PlaytestStepper.Start(spec);
                if (data["stopped_by"] == null)
                {
                    runError = data["error"]?.ToString() ?? "step failed";
                    break;
                }
                frames += data["frames_stepped"]?.Value<int>() ?? 0;
                errors += data["errors"]?.Value<int>() ?? 0;
                warnings += data["warnings"]?.Value<int>() ?? 0;
                foreach (var e in (JArray)data["first_errors"]) if (firstErrors.Count < 3) firstErrors.Add(e);
                foreach (var c in (JArray)data["captures"]) if (c["path"] != null) captures.Add(c["path"]);
                if (data["stopped_by"]?.ToString() == "error")
                {
                    runError = data["stop_reason"]?.ToString() ?? "stopped by a console error";
                    break;
                }
            }

            double simTime = Time.timeAsDouble - simStart;
            var stateEnd = PlaytestStepper.ReadState(null);

            var failed = new JArray();
            if (sc["expect"] is JArray expects)
            {
                foreach (JObject ex in expects)
                {
                    string hook = ex["hook"]?.ToString();
                    string op = ex["op"]?.ToString() ?? "==";
                    var entry = new JObject { ["hook"] = hook, ["op"] = op, ["value"] = ex["value"] };
                    if (!GameHooks.TryGet(hook, out var actual, out var herr))
                    {
                        entry["error"] = herr;
                        failed.Add(entry);
                        continue;
                    }
                    if (!PlaytestJson.Compare(actual, op, ex["value"], out var cerr))
                    {
                        entry["actual"] = actual;
                        if (cerr != null) entry["error"] = cerr;
                        failed.Add(entry);
                    }
                }
            }

            JObject perf = null;
            double perfSeconds = sc["perf"]?["seconds"]?.Value<double?>() ?? 0;
            if (runError == null && perfSeconds > 0)
            {
                // Real frames: stepped frames run at a fixed captured delta and say nothing about frame time.
                float captureDt = Time.captureDeltaTime;
                Time.captureDeltaTime = 0f;
                PlaytestPerf.Start();
                EditorApplication.isPaused = false;
                await WaitSeconds(Math.Min(perfSeconds, MaxPerfSecondsPerScenario));
                EditorApplication.isPaused = true;
                perf = PlaytestPerf.Finish();
                perf["seconds"] = perfSeconds;
                Time.captureDeltaTime = captureDt;
            }

            result["passed"] = failed.Count == 0 && runError == null && errors == 0;
            result["failed_expect"] = failed;
            result["frames"] = frames;
            result["sim_time"] = Math.Round(simTime, 6);
            result["state_end"] = stateEnd;
            result["errors"] = errors;
            result["warnings"] = warnings;
            result["first_errors"] = firstErrors;
            if (perf != null) result["perf"] = perf;
            result["captures"] = captures;
            result["state_hash"] = PlaytestJson.StateHash(stateEnd);
            if (runError != null) result["error"] = runError;
            return result;
        }

        /// <summary>Perf seconds the scenario will actually sample; 0 when unreadable (the run reports that error).</summary>
        private static double PerfSeconds(JObject sc)
        {
            try
            {
                var seconds = sc?["perf"] is JObject perf ? perf["seconds"]?.Value<double?>() ?? 0 : 0;
                return Math.Max(0, Math.Min(seconds, MaxPerfSecondsPerScenario));
            }
            catch (Exception e) when (e is FormatException || e is InvalidCastException)
            {
                return 0;
            }
        }

        private static Task WaitSeconds(double seconds)
        {
            var tcs = new TaskCompletionSource<bool>();
            double end = EditorApplication.timeSinceStartup + seconds;
            void Poll()
            {
                if (EditorApplication.timeSinceStartup < end && EditorApplication.isPlaying) return;
                EditorApplication.update -= Poll;
                tcs.TrySetResult(true);
            }
            EditorApplication.update += Poll;
            return tcs.Task;
        }
    }
}
