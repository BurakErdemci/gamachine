using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Playtest;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Playtest
{
    /// <summary>
    /// game_hooks: list | get {names?} | call {name, args?} over the game's [GameHook] members and runtime hooks.
    /// </summary>
    [McpForUnityTool("game_hooks", AutoRegister = false, Group = "playtest")]
    public static class GameHooksTool
    {
        public static object HandleCommand(JObject @params)
        {
            @params ??= new JObject();
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            PlaytestHookDiscovery.Ensure();
            switch (action)
            {
                case "list":
                    var hooks = GameHooks.List();
                    return new SuccessResponse($"{hooks.Count} hooks.", new
                    {
                        hooks = hooks.Select(h => new JObject { ["name"] = h.Name, ["kind"] = h.Kind, ["type"] = h.Type }).ToList(),
                        problems = PlaytestHookDiscovery.Problems.Count > 0 ? PlaytestHookDiscovery.Problems : null,
                    });
                case "get":
                    var names = @params["names"] is JArray arr
                        ? arr.Select(t => t.ToString()).ToList()
                        : @params["name"] != null ? new[] { @params["name"].ToString() }.ToList() : GameHooks.StateNames();
                    var values = new JObject();
                    foreach (var n in names)
                    {
                        if (!GameHooks.TryGet(n, out var v, out var err))
                            return new ErrorResponse(err, new { name = n, close_matches = GameHooks.CloseMatches(n, 3) });
                        values[n] = v;
                    }
                    return new SuccessResponse($"{values.Count} values.", new { values });
                case "call":
                    string name = @params["name"]?.ToString();
                    if (string.IsNullOrEmpty(name)) return new ErrorResponse("'name' is required for call.");
                    var args = @params["args"] as JObject;
                    if (@params["args"] != null && @params["args"].Type != JTokenType.Null && args == null)
                        return new ErrorResponse("'args' must be an object.");
                    if (!GameHooks.TryCall(name, args, out var result, out var error))
                        return new ErrorResponse(error, new { name, close_matches = GameHooks.CloseMatches(name, 3) });
                    return new SuccessResponse($"Called {name}.", new JObject { ["result"] = result });
                default:
                    return new ErrorResponse($"Unknown action '{action}'. Use list, get or call.");
            }
        }
    }
}
