using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Playtest
{
    /// <summary>
    /// run_playtest {path?, glob?="Assets/Playtests/**/*.playtest.json", seed_override?}: starts a job over the
    /// scenario files and answers pending; action "status" polls it and returns data.results when done.
    /// </summary>
    [McpForUnityTool("run_playtest", AutoRegister = false, Group = "playtest", RequiresPolling = true, PollAction = "status", MaxPollSeconds = 1800)]
    public static class RunPlaytestTool
    {
        public static object HandleCommand(JObject @params)
        {
            @params ??= new JObject();
            string action = @params["action"]?.ToString()?.ToLowerInvariant() ?? "start";
            if (action == "status") return Status();
            if (action != "start") return new ErrorResponse($"Unknown action '{action}'. Use start (default) or status.");

            string path = @params["path"]?.Type == JTokenType.Null ? null : @params["path"]?.ToString();
            string glob = @params["glob"]?.Type == JTokenType.Null ? null : @params["glob"]?.ToString();
            int? seed = @params["seed_override"]?.Type == JTokenType.Null ? null : @params["seed_override"]?.Value<int?>();
            var err = PlaytestRunner.Start(string.IsNullOrWhiteSpace(path) ? null : path, string.IsNullOrWhiteSpace(glob) ? null : glob, seed, out _);
            if (err != null) return new ErrorResponse(err);
            return Status();
        }

        private static object Status()
        {
            var d = PlaytestRunner.StatusData(includeResults: true);
            if (d == null) return new ErrorResponse("No run_playtest job in this editor session.");
            if (d["status"]?.ToString() == "running")
                return new PendingResponse($"run_playtest {d["done"]}/{d["total"]} done, now {d["current"]} ({d["phase"]}); poll status.", 1.0, d);
            return new SuccessResponse($"run_playtest: {d["passed"]}/{d["total"]} scenarios passed.", d);
        }
    }
}
