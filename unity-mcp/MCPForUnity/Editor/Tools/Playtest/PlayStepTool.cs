using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Playtest
{
    /// <summary>
    /// play_step {frames, input?, autopilot?, watch?, until?, capture?}: advances the paused session frame-exactly and
    /// returns the watched state, console error counts and captures.
    /// </summary>
    [McpForUnityTool("play_step", AutoRegister = false, Group = "playtest")]
    public static class PlayStepTool
    {
        public static async Task<object> HandleCommand(JObject @params)
        {
            @params ??= new JObject();
            var err = PlaytestStepper.Parse(@params, out var spec);
            if (err != null) return new ErrorResponse(err);
            if (@params["max_size"] != null) spec.ImageMaxSize = System.Math.Max(16, System.Math.Min(1280, @params["max_size"].Value<int>()));
            if (@params["format"]?.ToString() == "png") spec.Jpeg = false;
            spec.Camera = @params["camera"]?.ToString();

            var data = await PlaytestStepper.Start(spec);
            if (data["error"] != null && data["stopped_by"] == null) return new ErrorResponse(data["error"].ToString());
            string msg = $"Stepped {data["frames_stepped"]} frames (stopped by {data["stopped_by"]}); errors {data["errors"]}.";
            return new SuccessResponse(msg, data);
        }
    }
}
