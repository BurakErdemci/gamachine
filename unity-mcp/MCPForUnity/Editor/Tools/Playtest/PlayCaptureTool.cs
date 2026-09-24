using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Playtest;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.Playtest
{
    /// <summary>
    /// play_capture {max_size=640 (&lt;=1280), format=jpeg|png, camera?}: renders the current (paused) frame of a game
    /// camera. Full-resolution PNG under Library/GamachineCaptures, downscaled image inline as base64.
    /// </summary>
    [McpForUnityTool("play_capture", AutoRegister = false, Group = "playtest")]
    public static class PlayCaptureTool
    {
        public static object HandleCommand(JObject @params)
        {
            @params ??= new JObject();
            if (!EditorApplication.isPlaying) return new ErrorResponse("play_capture needs play mode; call play_session start first.");
            if (PlaytestStepper.Running) return new ErrorResponse("a play_step is running; capture after it returns.");
            int maxSize = @params["max_size"]?.Value<int?>() ?? 640;
            if (maxSize < 16 || maxSize > 1280) return new ErrorResponse($"max_size must be in 16..1280 (got {maxSize}).");
            string format = @params["format"]?.ToString()?.ToLowerInvariant() ?? "jpeg";
            if (format == "jpg") format = "jpeg";
            if (format != "jpeg" && format != "png") return new ErrorResponse("format must be jpeg or png.");

            var cam = PlaytestCapture.FindCamera(@params["camera"]?.ToString(), out var err);
            if (cam == null) return new ErrorResponse(err);
            try
            {
                var c = PlaytestCapture.Capture(cam, PlaytestStepper.CaptureDir, "capture", maxSize, format == "jpeg");
                return new SuccessResponse($"Captured frame {c.Frame} from camera '{c.Camera}'.", new JObject
                {
                    ["frame"] = c.Frame,
                    ["path"] = c.Path,
                    ["width"] = c.ImageWidth,
                    ["height"] = c.ImageHeight,
                    ["full_width"] = c.Width,
                    ["full_height"] = c.Height,
                    ["camera"] = c.Camera,
                    ["paused"] = EditorApplication.isPaused,
                    ["render_ms"] = Math.Round(c.RenderMs, 2),
                    ["image_base64"] = c.ImageBase64,
                    ["mime"] = c.Mime,
                });
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Capture failed: {e.Message}");
            }
        }
    }
}
