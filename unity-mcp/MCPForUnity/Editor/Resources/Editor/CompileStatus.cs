using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Resources.Editor
{
    /// <summary>
    /// Live compile status: epoch counters, the last compile's compiler errors and
    /// whether scripts changed on disk after it started. Read-only; never refreshes.
    /// Unlike get_editor_state this is read on the main thread at call time, not
    /// from a cached snapshot.
    /// </summary>
    [McpForUnityResource("get_compile_status")]
    public static class CompileStatus
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                bool scan = ParamCoercion.CoerceBool(@params?["scan_changes"], true);
                return new SuccessResponse("Retrieved compile status.", CompileTracker.GetStatus(scan));
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error getting compile status: {e.Message}");
            }
        }
    }
}
