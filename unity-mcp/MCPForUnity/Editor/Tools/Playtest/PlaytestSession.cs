using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Playtest;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools.Playtest
{
    /// <summary>
    /// Play session lifecycle. Entering play mode reloads the domain (~2.5 s after isPlaying flips, the game is up after
    /// 5-6 s, measured in P2), so start/stop are operations persisted in SessionState and advanced from
    /// EditorApplication.update in whichever domain is current; callers poll with status (the fork's PendingResponse
    /// pattern, as in manage_packages).
    /// </summary>
    [InitializeOnLoad]
    internal static class PlaytestSession
    {
        private const string Key = "MCPForUnity.Playtest.Session";
        public const string OpStart = "starting";
        public const string OpStop = "stopping";

        [Serializable]
        private class State
        {
            public string op = "";
            public string phase = "";
            public double opStarted;
            public double timeout = 60;
            public string scene = "";
            public int seed;
            public float fixedDt = 1f / 60f;
            public bool paused = true;
            public bool active;
            public string restoreScene = "";
            public float origCaptureDt;
            public float origFixedDt;
            public bool origRunInBackground;
            public bool enteredPlay;
            public string outcome = "";
            public bool outcomeIsError;
            public string outcomeMessage = "";
        }

        // Switching play mode in the same editor tick as the command can reload the domain before the reply is
        // sent (measured: "plugin session disconnected while awaiting command_result"); give the reply time to go out.
        private const double ReplyGraceSeconds = 0.5;

        private static State s = new State();
        private static bool s_StartedInThisDomain;
        private static int s_PhaseTicks;

        static PlaytestSession()
        {
            var json = SessionState.GetString(Key, "");
            if (!string.IsNullOrEmpty(json))
            {
                try { s = JsonUtility.FromJson<State>(json) ?? new State(); }
                catch { s = new State(); }
            }
            // Runtime statics (driver, virtual devices) do not survive a reload; a session left "active" by a
            // reload in play mode is re-applied, one left over in edit mode is closed.
            if (s.active && !EditorApplication.isPlaying) s.active = false;
            EditorApplication.update += Tick;
            EditorApplication.playModeStateChanged += OnPlayModeChanged;
        }

        private static void Save() => SessionState.SetString(Key, JsonUtility.ToJson(s));

        private static double Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

        public static bool Active => s.active && EditorApplication.isPlaying && PlaytestDriver.Installed;
        public static bool Busy => !string.IsNullOrEmpty(s.op);
        public static string Op => s.op;
        public static string Phase => s.phase;
        public static float FixedDt => s.fixedDt;

        public static JObject StatusData()
        {
            return new JObject
            {
                ["playing"] = EditorApplication.isPlaying,
                ["paused"] = EditorApplication.isPaused,
                ["frame"] = Time.frameCount,
                ["time"] = PlaytestJson.ToJson(Time.time),
                ["scene"] = SceneManager.GetActiveScene().path,
                ["session"] = Active,
                ["hooks"] = HookCount(),
            };
        }

        private static int HookCount()
        {
            PlaytestHookDiscovery.Ensure();
            return GameHooks.List().Count;
        }

        public static JObject PendingData() => new JObject
        {
            ["op"] = s.op,
            ["phase"] = s.phase,
            ["elapsed_s"] = Math.Round(Now - s.opStarted, 1),
            ["scene"] = s.scene,
        };

        /// <summary>Starts a session. Returns an error message, or null when the start is under way (poll status).</summary>
        public static string BeginStart(string scene, int seed, float fixedDt, bool paused, double timeoutS, bool restoreSceneOnStop)
        {
            if (Busy) return $"play_session is busy ({s.op}, phase {s.phase}); poll status or stop first.";
            if (fixedDt <= 0f || fixedDt > 1f) return $"fixed_dt must be in (0, 1] (got {fixedDt}).";
            if (EditorApplication.isCompiling) return "Unity is compiling; retry when it is done.";

            var active = SceneManager.GetActiveScene();
            if (!string.IsNullOrEmpty(scene))
            {
                if (AssetDatabase.LoadAssetAtPath<SceneAsset>(scene) == null) return $"Scene not found: {scene}";
                if (EditorApplication.isPlaying && active.path != scene)
                    return $"In play mode on '{active.path}'; stop the session before starting on '{scene}'.";
            }

            s.op = OpStart;
            s.opStarted = Now;
            s.timeout = timeoutS > 0 ? timeoutS : 60;
            s.scene = scene ?? "";
            s.seed = seed;
            s.fixedDt = fixedDt;
            s.paused = paused;
            s.enteredPlay = false;
            s.outcome = "";
            s_PhaseTicks = 0;

            if (EditorApplication.isPlaying)
            {
                // Adopt the running play mode: no reload needed.
                s.phase = "apply";
                Save();
                return null;
            }

            if (!string.IsNullOrEmpty(scene))
            {
                for (int i = 0; i < SceneManager.sceneCount; i++)
                {
                    var sc = SceneManager.GetSceneAt(i);
                    if (sc.isDirty)
                    {
                        s.op = "";
                        Save();
                        return $"Scene '{(string.IsNullOrEmpty(sc.path) ? sc.name : sc.path)}' has unsaved changes; save or discard them, or omit 'scene' to play the open scene.";
                    }
                }
                if (active.path != scene)
                {
                    if (restoreSceneOnStop && string.IsNullOrEmpty(s.restoreScene)) s.restoreScene = active.path;
                    EditorSceneManager.OpenScene(scene, OpenSceneMode.Single);
                }
            }

            s.phase = "enter_requested";
            Save();
            s_StartedInThisDomain = true;
            return null;
        }

        /// <summary>Stops the session. Returns an error, or null when the stop is under way (poll status).</summary>
        public static string BeginStop()
        {
            if (s.op == OpStop) return null;
            if (s.op == OpStart) Fail("start cancelled by stop");
            RestoreRuntime();
            s.op = OpStop;
            s.phase = "exiting";
            s.opStarted = Now;
            s.timeout = 60;
            s.outcome = "";
            Save();
            return null;
        }

        /// <summary>Hands over the result of the last finished start/stop once; false when there is none.</summary>
        public static bool TakeOutcome(out JObject data, out bool isError, out string message)
        {
            data = null;
            isError = false;
            message = null;
            if (Busy || string.IsNullOrEmpty(s.outcome)) return false;
            data = JObject.Parse(s.outcome);
            isError = s.outcomeIsError;
            message = s.outcomeMessage;
            s.outcome = "";
            Save();
            return true;
        }

        private static void Finish(string message, JObject data)
        {
            s.op = "";
            s.phase = "";
            s.outcome = data.ToString(Newtonsoft.Json.Formatting.None);
            s.outcomeIsError = false;
            s.outcomeMessage = message;
            Save();
        }

        private static void Fail(string message)
        {
            var data = PendingData();
            s.op = "";
            s.phase = "";
            s.outcome = data.ToString(Newtonsoft.Json.Formatting.None);
            s.outcomeIsError = true;
            s.outcomeMessage = message;
            Save();
        }

        private static void OnPlayModeChanged(PlayModeStateChange change)
        {
            switch (change)
            {
                case PlayModeStateChange.EnteredPlayMode:
                    if (s.op == OpStart && s.phase == "entering")
                    {
                        s.enteredPlay = true;
                        s.phase = "first_frame";
                        s_PhaseTicks = 0;
                        Save();
                    }
                    break;
                case PlayModeStateChange.ExitingPlayMode:
                    // Covers a stop pressed in the editor as well as our own.
                    if (s.active) RestoreRuntime();
                    break;
                case PlayModeStateChange.EnteredEditMode:
                    if (s.op == OpStart) Fail("play mode ended during start (compile errors or a stop in the editor?)");
                    break;
            }
        }

        private static void Tick()
        {
            if (string.IsNullOrEmpty(s.op)) return;
            if (Now - s.opStarted > s.timeout)
            {
                Fail($"play_session {(s.op == OpStart ? "start" : "stop")} timed out after {s.timeout:0}s in phase '{s.phase}'");
                return;
            }
            s_PhaseTicks++;
            try
            {
                if (s.op == OpStart) TickStart();
                else TickStop();
            }
            catch (Exception e)
            {
                Fail($"play_session {s.op} failed in phase '{s.phase}': {e.Message}");
            }
        }

        private static void TickStart()
        {
            switch (s.phase)
            {
                case "enter_requested":
                    if (Now - s.opStarted < ReplyGraceSeconds) return;
                    s.phase = "entering";
                    Save();
                    // Entering paused makes the first frame the only unstepped one; level.restart resets it anyway.
                    EditorApplication.isPaused = true;
                    EditorApplication.isPlaying = true;
                    return;
                case "entering":
                    // A domain loaded in play mode is past the reload even if the event raced the subscription.
                    if (EditorApplication.isPlaying && !s_StartedInThisDomain && !EditorApplication.isCompiling)
                    {
                        s.phase = "first_frame";
                        s_PhaseTicks = 0;
                        Save();
                    }
                    return;
                case "first_frame":
                    if (!EditorApplication.isPlaying || EditorApplication.isCompiling || EditorApplication.isUpdating) return;
                    if (!SceneManager.GetActiveScene().isLoaded) return;
                    if (Time.frameCount < 1)
                    {
                        // Entered paused before any frame ran: run exactly one so Awake/Start/first Update are done.
                        if (s_PhaseTicks > 30 && EditorApplication.isPaused) EditorApplication.Step();
                        return;
                    }
                    s.phase = "apply";
                    Save();
                    return;
                case "apply":
                    Apply();
                    return;
            }
        }

        private static void Apply()
        {
            PlaytestDriver.Install();
            if (!s.active)
            {
                s.origRunInBackground = Application.runInBackground;
                s.origCaptureDt = Time.captureDeltaTime;
                s.origFixedDt = Time.fixedDeltaTime;
            }
            Application.runInBackground = true;
            Time.captureDeltaTime = s.fixedDt;
            Time.fixedDeltaTime = s.fixedDt;
            UnityEngine.Random.InitState(s.seed);
            var input = PlaytestInput.BeginSession();
            var settingsObj = PlaytestInput.SettingsObject;
            if (settingsObj != null) input["settings_is_asset"] = EditorUtility.IsPersistent(settingsObj);
            s.active = true;
            Save();

            PlaytestHookDiscovery.Ensure();
            bool restarted = false;
            if (GameHooks.Exists("level.restart"))
            {
                if (!GameHooks.TryCall("level.restart", new JObject { ["seed"] = s.seed }, out _, out var err))
                {
                    Fail("level.restart failed: " + err);
                    return;
                }
                restarted = true;
            }

            EditorApplication.isPaused = s.paused;
            var data = StatusData();
            data["paused"] = s.paused;
            data["seed"] = s.seed;
            data["fixed_dt"] = PlaytestJson.ToJson(s.fixedDt);
            data["restarted"] = restarted;
            data["input"] = input;
            Finish($"Play session started{(restarted ? " (level.restart called)" : "")}.", data);
        }

        private static void TickStop()
        {
            if (s.phase == "exiting")
            {
                if (Now - s.opStarted < ReplyGraceSeconds) return;
                s.phase = "exit_requested";
                Save();
                if (EditorApplication.isPlaying) EditorApplication.isPlaying = false;
                return;
            }
            if (EditorApplication.isPlaying || EditorApplication.isPlayingOrWillChangePlaymode) return;
            if (EditorApplication.isCompiling || EditorApplication.isUpdating) return;
            EditorApplication.isPaused = false;
            string restored = null;
            if (!string.IsNullOrEmpty(s.restoreScene))
            {
                var active = SceneManager.GetActiveScene();
                if (active.path != s.restoreScene && !active.isDirty && AssetDatabase.LoadAssetAtPath<SceneAsset>(s.restoreScene) != null)
                {
                    EditorSceneManager.OpenScene(s.restoreScene, OpenSceneMode.Single);
                    restored = s.restoreScene;
                }
                s.restoreScene = "";
            }
            var data = StatusData();
            data["restored_scene"] = restored;
            Finish("Play session stopped.", data);
        }

        /// <summary>Undoes everything the session changed at runtime; safe to call more than once.</summary>
        private static void RestoreRuntime()
        {
            if (!s.active) return;
            PlaytestStepper.Abort("play session stopped");
            PlaytestDriver.Uninstall();
            PlaytestInput.EndSession();
            Time.captureDeltaTime = s.origCaptureDt;
            Time.fixedDeltaTime = s.origFixedDt;
            Application.runInBackground = s.origRunInBackground;
            s.active = false;
            Save();
        }
    }
}
