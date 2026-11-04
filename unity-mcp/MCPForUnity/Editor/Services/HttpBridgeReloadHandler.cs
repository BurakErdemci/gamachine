using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Windows;
using UnityEditor;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Ensures HTTP transports resume after domain reloads similar to the legacy stdio bridge.
    /// </summary>
    [InitializeOnLoad]
    internal static class HttpBridgeReloadHandler
    {
        private static readonly TimeSpan[] ResumeRetrySchedule =
        {
            TimeSpan.Zero,
            TimeSpan.FromSeconds(1),
            TimeSpan.FromSeconds(3),
            TimeSpan.FromSeconds(5),
            TimeSpan.FromSeconds(10),
            TimeSpan.FromSeconds(30)
        };

        // Mirrors WebSocketTransportClient.ReconnectTailInterval — the two paths recover
        // from the same condition and drifting apart would make the behaviour unpredictable.
        private static readonly TimeSpan ResumeTailInterval = TimeSpan.FromSeconds(30);

        private static int _tailLoopRunning;

        static HttpBridgeReloadHandler()
        {
            // AssetImportWorker'da bridge resume etme — ana editörün session'ını evict
            // ediyordu (bkz. HttpAutoStartHandler'daki açıklama).
            if (AssetDatabase.IsAssetImportWorkerProcess()) return;

            AssemblyReloadEvents.beforeAssemblyReload += OnBeforeAssemblyReload;
            AssemblyReloadEvents.afterAssemblyReload += OnAfterAssemblyReload;
        }

        private static void OnBeforeAssemblyReload()
        {
            try
            {
                var transport = MCPServiceLocator.TransportManager;
                bool shouldResume = transport.IsRunning(TransportMode.Http);

                if (shouldResume)
                {
                    EditorPrefs.SetBool(EditorPrefKeys.ResumeHttpAfterReload, true);
                }
                else
                {
                    EditorPrefs.DeleteKey(EditorPrefKeys.ResumeHttpAfterReload);
                }

                if (shouldResume)
                {
                    // beforeAssemblyReload is synchronous; force a synchronous teardown so we do not
                    // leave an orphaned socket due to an unfinished async close handshake.
                    transport.ForceStop(TransportMode.Http);
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Failed to evaluate HTTP bridge reload state: {ex.Message}");
            }
        }

        private static void OnAfterAssemblyReload()
        {
            bool resume = false;
            try
            {
                if (!EditorPrefs.GetBool(EditorPrefKeys.AutoStartOnLoad, false))
                {
                    EditorPrefs.DeleteKey(EditorPrefKeys.ResumeHttpAfterReload);
                    return;
                }

                // Only resume HTTP if it is still the selected transport.
                bool useHttp = EditorConfigurationCache.Instance.UseHttpTransport;
                resume = useHttp && EditorPrefs.GetBool(EditorPrefKeys.ResumeHttpAfterReload, false);
                if (resume)
                {
                    EditorPrefs.DeleteKey(EditorPrefKeys.ResumeHttpAfterReload);
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Failed to read HTTP bridge reload flag: {ex.Message}");
                resume = false;
            }

            if (!resume)
            {
                return;
            }

            // If the editor is not compiling, attempt an immediate restart without relying on editor focus.
            bool isCompiling = EditorApplication.isCompiling;
            try
            {
                var pipeline = Type.GetType("UnityEditor.Compilation.CompilationPipeline, UnityEditor");
                var prop = pipeline?.GetProperty("isCompiling", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
                if (prop != null) isCompiling |= (bool)prop.GetValue(null);
            }
            catch { }

            if (!isCompiling)
            {
                _ = ResumeHttpWithRetriesAsync();
                return;
            }

            // Fallback when compiling: schedule on the editor loop
            EditorApplication.delayCall += () =>
            {
                _ = ResumeHttpWithRetriesAsync();
            };
        }

        private static async Task ResumeHttpWithRetriesAsync()
        {
            for (int i = 0; i < ResumeRetrySchedule.Length; i++)
            {
                if (!EditorPrefs.GetBool(EditorPrefKeys.AutoStartOnLoad, false)) return;

                int attempt = i + 1;
                McpLog.Debug($"[HTTP Reload] Resume attempt {attempt}/{ResumeRetrySchedule.Length}");

                TimeSpan delay = ResumeRetrySchedule[i];
                if (delay > TimeSpan.Zero)
                {
                    McpLog.Debug($"[HTTP Reload] Waiting {delay.TotalSeconds:0.#}s before resume attempt {attempt}");
                    try { await Task.Delay(delay); }
                    catch { return; }
                }

                // Abort retries if the user disabled MCP or switched transports while waiting.
                if (!EditorPrefs.GetBool(EditorPrefKeys.AutoStartOnLoad, false)) return;
                if (!EditorConfigurationCache.Instance.UseHttpTransport)
                {
                    return;
                }

                try
                {
                    bool started = await MCPServiceLocator.TransportManager.StartAsync(TransportMode.Http, quiet: true);
                    if (started)
                    {
                        McpLog.Debug($"[HTTP Reload] Resume succeeded on attempt {attempt}");
                        MCPForUnityEditorWindow.RequestHealthVerification();
                        return;
                    }

                    var state = MCPServiceLocator.TransportManager.GetState(TransportMode.Http);
                    string reason = string.IsNullOrWhiteSpace(state?.Error) ? "no error detail" : state.Error;
                    McpLog.Debug($"[HTTP Reload] Resume attempt {attempt} failed: {reason}");
                }
                catch (Exception ex)
                {
                    McpLog.Debug($"[HTTP Reload] Resume attempt {attempt} threw: {ex.Message}");
                }
            }

            if (!EditorPrefs.GetBool(EditorPrefKeys.AutoStartOnLoad, false)) return;
            if (MCPServiceLocator.Server.IsLocalHttpServerReachable())
            {
                McpLog.WarnGamachineConnectionFailed();
            }
            else
            {
                McpLog.Debug("[HTTP Reload] No local server is listening; skipping connection warning.");
            }

            await ResumeTailLoopAsync();
        }

        /// <summary>
        /// Keeps trying to resume after <see cref="ResumeRetrySchedule"/> is exhausted,
        /// until MCP is turned off in the app (AutoStartOnLoad cleared) or the editor closes.
        /// </summary>
        /// <remarks>
        /// Without this the finite schedule (~49 s total) is a hard deadline: a server that
        /// takes longer to become reachable leaves the bridge permanently dead, and the only
        /// recovery is a manual Connect from the editor window. That breaks the product's
        /// core promise that the MCP server is driven entirely from the app's toggle with no
        /// Unity-side steps.
        ///
        /// Measured on 2026-07-27: the local server is launched through `uvx --no-cache`,
        /// which rebuilds its dependencies on every start and took roughly two minutes —
        /// so the 49 s budget was not merely tight, it was guaranteed to be exceeded, and
        /// the bridge stayed dead until the user intervened by hand.
        ///
        /// WebSocketTransportClient.AttemptReconnectAsync retries for the same reason, but it
        /// only covers a connection that was established and then dropped — not one that
        /// never came up after a domain reload. This closes that gap.
        ///
        /// Unbounded since 4 Nov 2026: the server died with nobody present and a 5 min
        /// budget left the editor disconnected for good, so Gamachine's own supervisor
        /// restarts the server and this loop finds it again without any click.
        /// </remarks>
        private static async Task ResumeTailLoopAsync()
        {
            // One tail loop at a time: every domain reload re-enters this handler, and
            // without the guard each reload would leave another loop running forever.
            if (Interlocked.CompareExchange(ref _tailLoopRunning, 1, 0) != 0)
            {
                return;
            }

            try
            {
                while (true)
                {
                    if (!EditorPrefs.GetBool(EditorPrefKeys.AutoStartOnLoad, false)) return;

                    try { await Task.Delay(ResumeTailInterval); }
                    catch { return; }

                    // The user may have disabled MCP or switched transports while waiting.
                    if (!EditorPrefs.GetBool(EditorPrefKeys.AutoStartOnLoad, false)) return;
                    if (!EditorConfigurationCache.Instance.UseHttpTransport)
                    {
                        return;
                    }

                    try
                    {
                        if (await MCPServiceLocator.TransportManager.StartAsync(TransportMode.Http, quiet: true))
                        {
                            McpLog.Info("[HTTP Reload] Bridge resumed after extended retry", false);
                            MCPForUnityEditorWindow.RequestHealthVerification();
                            return;
                        }
                    }
                    catch (Exception ex)
                    {
                        McpLog.Debug($"[HTTP Reload] Tail resume attempt threw: {ex.Message}");
                    }
                }
            }
            finally
            {
                Interlocked.Exchange(ref _tailLoopRunning, 0);
            }
        }
    }
}
