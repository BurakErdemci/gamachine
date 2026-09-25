using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Executes multiple MCP commands within a single Unity-side handler. Commands are executed sequentially
    /// on the main thread to preserve determinism and Unity API safety.
    ///
    /// Supports chaining: a string param value that is exactly "$[i]" or "$[i].some.path"
    /// is replaced with (part of) the result of the i-th command (0-based) before execution,
    /// e.g. "$[0].data.instanceID" — enabling create→configure→parent in one batch.
    /// </summary>
    [McpForUnityTool("batch_execute", AutoRegister = false)]
    public static class BatchExecute
    {
        /// <summary>Default limit when no EditorPrefs override is set.</summary>
        internal const int DefaultMaxCommandsPerBatch = 25;

        /// <summary>Hard ceiling to prevent extreme editor freezes regardless of user setting.</summary>
        internal const int AbsoluteMaxCommandsPerBatch = 100;

        /// <summary>
        /// Returns the user-configured max commands per batch, clamped between 1 and <see cref="AbsoluteMaxCommandsPerBatch"/>.
        /// </summary>
        internal static int GetMaxCommandsPerBatch()
        {
            int configured = EditorPrefs.GetInt(EditorPrefKeys.BatchExecuteMaxCommands, DefaultMaxCommandsPerBatch);
            return Math.Clamp(configured, 1, AbsoluteMaxCommandsPerBatch);
        }

        public static async Task<object> HandleCommand(JObject @params)
        {
            if (@params == null)
            {
                return new ErrorResponse("'commands' payload is required.");
            }

            var commandsToken = @params["commands"] as JArray;
            if (commandsToken == null || commandsToken.Count == 0)
            {
                return new ErrorResponse("Provide at least one command entry in 'commands'.");
            }

            int maxCommands = GetMaxCommandsPerBatch();
            if (commandsToken.Count > maxCommands)
            {
                return new ErrorResponse(
                    $"A maximum of {maxCommands} commands are allowed per batch (configurable in MCP Tools window, hard max {AbsoluteMaxCommandsPerBatch}).");
            }

            bool failFast = @params.Value<bool?>("failFast") ?? false;
            bool parallelRequested = @params.Value<bool?>("parallel") ?? false;
            int? maxParallel = @params.Value<int?>("maxParallelism");

            if (parallelRequested)
            {
                McpLog.Warn("batch_execute parallel mode requested, but commands will run sequentially on the main thread for safety.");
            }

            var commandResults = new List<object>(commandsToken.Count);
            // Serialized result per executed command, in lockstep with command index —
            // failed/skipped commands hold null so "$[i]" references resolve against
            // the right command (or fail loudly if that command produced nothing).
            var serializedResults = new List<JToken>(commandsToken.Count);
            int invocationSuccessCount = 0;
            int invocationFailureCount = 0;
            bool anyCommandFailed = false;

            foreach (var token in commandsToken)
            {
                if (token is not JObject commandObj)
                {
                    invocationFailureCount++;
                    anyCommandFailed = true;
                    commandResults.Add(new
                    {
                        tool = (string)null,
                        callSucceeded = false,
                        error = "Command entries must be JSON objects."
                    });
                    serializedResults.Add(null);
                    if (failFast)
                    {
                        break;
                    }
                    continue;
                }

                string toolName = commandObj["tool"]?.ToString();
                var rawParams = commandObj["params"] as JObject ?? new JObject();
                var commandParams = NormalizeParameterKeys(rawParams);

                if (string.IsNullOrWhiteSpace(toolName))
                {
                    invocationFailureCount++;
                    anyCommandFailed = true;
                    commandResults.Add(new
                    {
                        tool = toolName,
                        callSucceeded = false,
                        error = "Each command must include a non-empty 'tool' field."
                    });
                    serializedResults.Add(null);
                    if (failFast)
                    {
                        break;
                    }
                    continue;
                }

                string collisionError = StringCaseUtility.FindNormalizedKeyCollision(
                    rawParams.Properties().Select(p => p.Name), ToCamelCase);
                if (collisionError != null)
                {
                    invocationFailureCount++;
                    anyCommandFailed = true;
                    commandResults.Add(new
                    {
                        tool = toolName,
                        callSucceeded = false,
                        error = collisionError
                    });
                    serializedResults.Add(null);
                    if (failFast)
                    {
                        break;
                    }
                    continue;
                }

                if (!TryResolveReferences(commandParams, serializedResults, out string refError))
                {
                    invocationFailureCount++;
                    anyCommandFailed = true;
                    commandResults.Add(new
                    {
                        tool = toolName,
                        callSucceeded = false,
                        error = refError
                    });
                    serializedResults.Add(null);
                    if (failFast)
                    {
                        break;
                    }
                    continue;
                }

                // Block disabled tools (mirrors TransportCommandDispatcher check)
                var toolMeta = MCPServiceLocator.ToolDiscovery.GetToolMetadata(toolName);
                if (toolMeta != null && !MCPServiceLocator.ToolDiscovery.IsToolEnabled(toolName))
                {
                    invocationFailureCount++;
                    anyCommandFailed = true;
                    commandResults.Add(new
                    {
                        tool = toolName,
                        callSucceeded = false,
                        result = new ErrorResponse($"Tool '{toolName}' is disabled in the Unity Editor.")
                    });
                    serializedResults.Add(null);
                    if (failFast) break;
                    continue;
                }

                try
                {
                    var result = await CommandRegistry.InvokeCommandAsync(toolName, commandParams).ConfigureAwait(true);
                    bool callSucceeded = DetermineCallSucceeded(result);
                    if (callSucceeded)
                    {
                        invocationSuccessCount++;
                    }
                    else
                    {
                        invocationFailureCount++;
                        anyCommandFailed = true;
                    }

                    commandResults.Add(new
                    {
                        tool = toolName,
                        callSucceeded,
                        result
                    });
                    serializedResults.Add(callSucceeded ? SerializeResultForReferences(result) : null);

                    if (!callSucceeded && failFast)
                    {
                        break;
                    }
                }
                catch (Exception ex)
                {
                    invocationFailureCount++;
                    anyCommandFailed = true;
                    commandResults.Add(new
                    {
                        tool = toolName,
                        callSucceeded = false,
                        error = ex.Message
                    });
                    serializedResults.Add(null);

                    if (failFast)
                    {
                        break;
                    }
                }
            }

            bool overallSuccess = !anyCommandFailed;
            var data = new
            {
                results = commandResults,
                callSuccessCount = invocationSuccessCount,
                callFailureCount = invocationFailureCount,
                parallelRequested,
                parallelApplied = false,
                maxParallelism = maxParallel
            };

            return overallSuccess
                ? new SuccessResponse("Batch execution completed.", data)
                : new ErrorResponse("One or more commands failed.", data);
        }

        /// <summary>
        /// Matches whole-string reference values like "$[0]" or "$[0].data.instanceID".
        /// </summary>
        private static readonly Regex RefPattern = new(@"^\$\[(\d+)\](?:\.(.+))?$", RegexOptions.Compiled);

        /// <summary>
        /// Serializes a command result to a JToken so later commands can reference into it.
        /// JsonProperty attributes apply, so paths use lowercase keys: "$[i].data.…".
        /// </summary>
        internal static JToken SerializeResultForReferences(object result)
        {
            if (result == null)
            {
                return null;
            }

            try
            {
                return result as JToken ?? JToken.FromObject(result);
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[BatchExecute] Could not serialize result for reference resolution: {ex.Message}");
                return null;
            }
        }

        /// <summary>
        /// Replaces whole-string "$[i]" / "$[i].path" param values with data from prior command results.
        /// Returns false (with a descriptive error) on forward/out-of-range references, references to
        /// failed commands, or paths that don't exist in the referenced result.
        /// </summary>
        internal static bool TryResolveReferences(JObject parameters, IReadOnlyList<JToken> priorResults, out string error)
        {
            error = null;
            if (parameters == null)
            {
                return true;
            }

            // Materialize first: Replace() mutates the tree we'd otherwise be iterating.
            var stringValues = parameters.DescendantsAndSelf()
                .OfType<JValue>()
                .Where(v => v.Type == JTokenType.String)
                .ToList();

            foreach (var value in stringValues)
            {
                var raw = (string)value.Value;
                var match = RefPattern.Match(raw);
                if (!match.Success)
                {
                    continue;
                }

                int index = int.Parse(match.Groups[1].Value);
                if (index >= priorResults.Count)
                {
                    error = $"Reference '{raw}' points to command #{index}, which has not executed yet — references may only point to earlier commands in the batch.";
                    return false;
                }

                var source = priorResults[index];
                if (source == null)
                {
                    error = $"Reference '{raw}' points to command #{index}, which failed or produced no result.";
                    return false;
                }

                JToken resolved = match.Groups[2].Success
                    ? source.SelectToken(match.Groups[2].Value)
                    : source;
                if (resolved == null)
                {
                    error = $"Reference '{raw}': path '{match.Groups[2].Value}' not found in the result of command #{index}. Available top-level keys: {string.Join(", ", TopLevelKeys(source))}.";
                    return false;
                }

                value.Replace(resolved.DeepClone());
            }

            return true;
        }

        private static IEnumerable<string> TopLevelKeys(JToken token)
        {
            return token is JObject obj
                ? obj.Properties().Select(prop => prop.Name)
                : Enumerable.Empty<string>();
        }

        private static bool DetermineCallSucceeded(object result)
        {
            if (result == null)
            {
                return true;
            }

            if (result is IMcpResponse response)
            {
                return response.Success;
            }

            if (result is JObject obj)
            {
                var successToken = obj["success"];
                if (successToken != null && successToken.Type == JTokenType.Boolean)
                {
                    return successToken.Value<bool>();
                }
            }

            if (result is JToken token)
            {
                var successToken = token["success"];
                if (successToken != null && successToken.Type == JTokenType.Boolean)
                {
                    return successToken.Value<bool>();
                }
            }

            return true;
        }

        private static JObject NormalizeParameterKeys(JObject source)
        {
            if (source == null)
            {
                return new JObject();
            }

            var normalized = new JObject();
            foreach (var property in source.Properties())
            {
                string normalizedName = ToCamelCase(property.Name);
                normalized[normalizedName] = property.Value;
            }
            return normalized;
        }

        private static string ToCamelCase(string key) => StringCaseUtility.ToCamelCase(key);
    }
}
