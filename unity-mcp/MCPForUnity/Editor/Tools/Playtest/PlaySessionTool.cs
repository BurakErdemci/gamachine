using System.Globalization;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Playtest
{
    /// <summary>
    /// play_session: start {scene?, seed=0, fixed_dt=1/60, paused=true} | stop | status. start and stop span a domain
    /// reload, so they answer with a pending result and finish through status polls.
    /// </summary>
    [McpForUnityTool("play_session", AutoRegister = false, Group = "playtest", RequiresPolling = true, PollAction = "status", MaxPollSeconds = 120)]
    public static class PlaySessionTool
    {
        public static object HandleCommand(JObject @params)
        {
            @params ??= new JObject();
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            switch (action)
            {
                case "start":
                {
                    string scene = @params["scene"]?.Type == JTokenType.Null ? null : @params["scene"]?.ToString();
                    int seed = @params["seed"]?.Value<int?>() ?? 0;
                    float fixedDt = @params["fixed_dt"]?.Value<float?>() ?? 1f / 60f;
                    bool paused = @params["paused"]?.Value<bool?>() ?? true;
                    double timeout = @params["timeout_seconds"]?.Value<double?>() ?? 60;
                    var err = PlaytestSession.BeginStart(string.IsNullOrWhiteSpace(scene) ? null : scene, seed, fixedDt, paused, timeout, true);
                    if (err != null) return new ErrorResponse(err);
                    return Status();
                }
                case "stop":
                {
                    var err = PlaytestSession.BeginStop();
                    if (err != null) return new ErrorResponse(err);
                    return Status();
                }
                case "status":
                    return Status();
                default:
                    return new ErrorResponse($"Unknown action '{action}'. Use start, stop or status.");
            }
        }

        private static object Status()
        {
            if (PlaytestSession.Busy)
            {
                var d = PlaytestSession.PendingData();
                return new PendingResponse(
                    $"play_session {(PlaytestSession.Op == PlaytestSession.OpStart ? "start" : "stop")} in progress (phase {PlaytestSession.Phase}, {d["elapsed_s"]?.ToObject<double>().ToString(CultureInfo.InvariantCulture)}s); poll status.",
                    0.5, d);
            }
            if (PlaytestSession.TakeOutcome(out var data, out var isError, out var message))
                return isError ? new ErrorResponse(message, data) : new SuccessResponse(message, data);
            return new SuccessResponse("Play session status.", PlaytestSession.StatusData());
        }
    }
}
