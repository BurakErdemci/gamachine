using System;
using System.Buffers;
using System.Collections.Generic;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    /// <summary>
    /// Maintains a persistent WebSocket connection to the MCP server plugin hub.
    /// Handles registration, keep-alives, and command dispatch back into Unity via
    /// <see cref="TransportCommandDispatcher"/>.
    /// </summary>
    public class WebSocketTransportClient : IMcpTransportClient, IDisposable
    {
        private const string TransportDisplayName = "websocket";
        private static readonly TimeSpan[] ReconnectSchedule =
        {
            TimeSpan.Zero,
            TimeSpan.FromSeconds(1),
            TimeSpan.FromSeconds(3),
            TimeSpan.FromSeconds(5),
            TimeSpan.FromSeconds(10),
            TimeSpan.FromSeconds(30)
        };
        private static readonly TimeSpan ReconnectTailInterval = TimeSpan.FromSeconds(30);
        // After this the tail loop stops (owner's decision, 2026-09-26: retrying forever is
        // not needed); turning Unity MCP off and on in the app reconnects via a reload.
        private static readonly TimeSpan ReconnectTailBudget = HttpBridgeReloadHandler.ResumeTailBudget;

        private static readonly TimeSpan DefaultKeepAliveInterval = TimeSpan.FromSeconds(15);
        private static readonly TimeSpan DefaultCommandTimeout = TimeSpan.FromSeconds(30);

        private readonly IToolDiscoveryService _toolDiscoveryService;
        private ClientWebSocket _socket;
        private CancellationTokenSource _lifecycleCts;
        private CancellationTokenSource _connectionCts;
        private Task _receiveTask;
        private Task _keepAliveTask;
        private readonly SemaphoreSlim _sendLock = new(1, 1);

        private Uri _endpointUri;
        private string _sessionId;
        private string _projectHash;
        private string _projectName;
        private string _projectPath;
        private string _unityVersion;
        private TimeSpan _keepAliveInterval = DefaultKeepAliveInterval;
        private volatile bool _isConnected;
        private int _isReconnectingFlag;
        // Her Establish yeni bir "bağlantı nesli" açar; arka plan döngüleri ve
        // kapanış olayları kendi nesline bağlıdır, bayat nesilden gelenler yok
        // sayılır. Aksi halde eski soketlerin döngüleri hayalet reconnect tetikleyip
        // sağlıklı session'ı evict ettiriyordu (kronik 15-30 sn eviction döngüsü).
        private int _connectionGen;
        private TransportState _state = TransportState.Disconnected(TransportDisplayName, "Transport not started");
        private string _apiKey;
        // Local scope reads its secret from a file that may appear after this
        // transport started (the server writes it on first launch), so every
        // connection attempt re-reads it. Captured on the main thread because
        // the scope itself comes from EditorPrefs.
        private bool _useLocalTokenFile;
        private bool _disposed;

        public WebSocketTransportClient(IToolDiscoveryService toolDiscoveryService = null)
        {
            _toolDiscoveryService = toolDiscoveryService;
        }

        public bool IsConnected => _isConnected;
        public string TransportName => TransportDisplayName;
        public TransportState State => _state;

        private Task<List<ToolMetadata>> GetEnabledToolsOnMainThreadAsync(CancellationToken token)
        {
            return TransportCommandDispatcher.RunOnMainThreadAsync(
                () => _toolDiscoveryService?.GetEnabledTools() ?? new List<ToolMetadata>(),
                token);
        }

        public async Task<bool> StartAsync()
        {
            // Capture identity values on the main thread before any async context switching
            _projectName = ProjectIdentityUtility.GetProjectName();
            _projectHash = ProjectIdentityUtility.GetProjectHash();
            _unityVersion = Application.unityVersion;
            _useLocalTokenFile = !HttpEndpointUtility.IsRemoteScope();
            _apiKey = _useLocalTokenFile
                ? ReadLocalApiToken()
                : EditorPrefs.GetString(EditorPrefKeys.ApiKey, string.Empty);

            if (HttpEndpointUtility.IsRemoteScope()
                && !HttpEndpointUtility.IsCurrentRemoteUrlAllowed(out string remoteUrlError))
            {
                string message = remoteUrlError ?? "HTTP Remote URL is not allowed by current security settings.";
                _state = TransportState.Disconnected(TransportDisplayName, message);
                McpLog.Error($"[WebSocket] {message}");
                return false;
            }

            // Get project root path (strip /Assets from dataPath) for focus nudging
            string dataPath = Application.dataPath;
            if (!string.IsNullOrEmpty(dataPath))
            {
                string normalized = dataPath.TrimEnd('/', '\\');
                if (string.Equals(System.IO.Path.GetFileName(normalized), "Assets", StringComparison.Ordinal))
                {
                    _projectPath = System.IO.Path.GetDirectoryName(normalized) ?? normalized;
                }
                else
                {
                    _projectPath = normalized;  // Fallback if path doesn't end with Assets
                }
            }

            await StopAsync();

            _lifecycleCts = new CancellationTokenSource();
            _endpointUri = BuildWebSocketUri(HttpEndpointUtility.GetBaseUrl());
            _sessionId = null;

            if (!await EstablishConnectionAsync(_lifecycleCts.Token))
            {
                await StopAsync();
                return false;
            }

            // State is connected but session ID might be pending until 'registered' message
            _state = TransportState.Connected(TransportDisplayName, sessionId: "pending", details: _endpointUri.ToString());
            _isConnected = true;
            return true;
        }

        public async Task StopAsync()
        {
            if (_lifecycleCts == null)
            {
                return;
            }

            Interlocked.Increment(ref _connectionGen); // tüm eski döngüleri/kapanışları geçersiz kıl

            try
            {
                _lifecycleCts.Cancel();
            }
            catch { }

            await StopConnectionLoopsAsync().ConfigureAwait(false);

            if (_socket != null)
            {
                try
                {
                    if (_socket.State == WebSocketState.Open || _socket.State == WebSocketState.CloseReceived)
                    {
                        await _socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Shutdown", CancellationToken.None).ConfigureAwait(false);
                    }
                }
                catch { }
                finally
                {
                    _socket.Dispose();
                    _socket = null;
                }
            }

            _isConnected = false;
            _state = TransportState.Disconnected(TransportDisplayName);

            _lifecycleCts.Dispose();
            _lifecycleCts = null;
        }

        /// <summary>
        /// Synchronous teardown for use in beforeAssemblyReload where async is not possible.
        /// Skips the graceful WebSocket close handshake and just disposes resources immediately.
        /// The server handles ungraceful disconnects via its ping timeout.
        /// </summary>
        public void ForceStop()
        {
            Interlocked.Increment(ref _connectionGen); // tüm eski döngüleri/kapanışları geçersiz kıl
            try { _lifecycleCts?.Cancel(); } catch { }
            try { _connectionCts?.Cancel(); } catch { }

            if (_socket != null)
            {
                try { _socket.Abort(); } catch { }
                try { _socket.Dispose(); } catch { }
                _socket = null;
            }

            try { _connectionCts?.Dispose(); } catch { }
            _connectionCts = null;
            _receiveTask = null;
            _keepAliveTask = null;
            Interlocked.Exchange(ref _isReconnectingFlag, 0);
            _isConnected = false;
            _state = TransportState.Disconnected(TransportDisplayName);

            try { _lifecycleCts?.Dispose(); } catch { }
            _lifecycleCts = null;
        }

        public async Task<bool> VerifyAsync()
        {
            if (_socket == null || _socket.State != WebSocketState.Open)
            {
                return false;
            }

            if (_lifecycleCts == null)
            {
                return false;
            }

            try
            {
                using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(_lifecycleCts.Token);
                timeoutCts.CancelAfter(TimeSpan.FromSeconds(5));
                await SendPongAsync(timeoutCts.Token).ConfigureAwait(false);
                return true;
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[WebSocket] Verify ping failed: {ex.Message}");
                return false;
            }
        }

        public void Dispose()
        {
            if (_disposed)
            {
                return;
            }

            try
            {
                // Ensure background loops are stopped before disposing shared resources
                StopAsync().GetAwaiter().GetResult();
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[WebSocket] Dispose failed to stop cleanly: {ex.Message}");
            }

            _sendLock?.Dispose();
            _socket?.Dispose();
            _lifecycleCts?.Dispose();
            _disposed = true;
        }

        // quiet=true → arka plan reconnect denemesi: başarısızlık Debug'a düşer.
        // Sunucu kapalıyken 30 sn'de bir Error/Warn basıp konsolu spam'lememek için;
        // kullanıcı-tetikli ilk bağlantı hatası Error olarak kalır.
        private async Task<bool> EstablishConnectionAsync(CancellationToken token, bool quiet = false)
        {
            await StopConnectionLoopsAsync().ConfigureAwait(false);

            // Re-read on every attempt: the file is created when the local server
            // first launches, which can be after this Editor session started. Plain
            // file I/O, no Unity API, so it is safe off the main thread.
            if (_useLocalTokenFile)
            {
                _apiKey = ReadLocalApiToken();
            }

            int gen = Interlocked.Increment(ref _connectionGen);

            _connectionCts?.Dispose();
            _connectionCts = CancellationTokenSource.CreateLinkedTokenSource(token);
            CancellationToken connectionToken = _connectionCts.Token;

            Uri originalEndpoint = _endpointUri;
            Uri connectedEndpoint = null;
            Exception lastConnectError = null;

            foreach (Uri candidate in BuildConnectionCandidateUris(originalEndpoint))
            {
                connectionToken.ThrowIfCancellationRequested();

                _socket?.Dispose();
                _socket = new ClientWebSocket();
                // Protokol seviyesi keep-alive KAPALI (TimeSpan.Zero): Mono'nun
                // ManagedWebSocket'i bu frame'leri gönderdiğinde uvicorn tarafı
                // bağlantıyı kesiyor ve 15 sn'de bir reconnect+eviction fırtınası
                // dönüyordu ("session superseded" hatalarının kökü). Canlılık zaten
                // uygulama seviyesinde sağlanıyor: server 10 sn'de bir ping atar,
                // client pong'lar; client ayrıca 15 sn'de bir kendi pong'unu yollar.
                _socket.Options.KeepAliveInterval = TimeSpan.Zero;

                // Credential for the handshake: the user's API key in remote-hosted
                // mode, the machine-local shared secret otherwise. It rides in a
                // header rather than the URL so it stays out of access logs and out
                // of the endpoint string we surface in TransportState.
                if (!string.IsNullOrEmpty(_apiKey))
                {
                    _socket.Options.SetRequestHeader(AuthConstants.ApiKeyHeader, _apiKey);
                }

                try
                {
                    await _socket.ConnectAsync(candidate, connectionToken).ConfigureAwait(false);
                    connectedEndpoint = candidate;
                    break;
                }
                catch (OperationCanceledException) when (connectionToken.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    lastConnectError = ex;
                    McpLog.Debug($"[WebSocket] Connect failed for {candidate}: {ex.Message}");
                }
            }

            if (connectedEndpoint == null)
            {
                string errorMsg = "Connection failed. Check that the server URL is correct, the server is running, and your API key (if required) is valid.";
                if (quiet)
                    McpLog.Debug($"[WebSocket] Reconnect attempt failed: {lastConnectError?.Message ?? "Unknown error"}");
                else
                    McpLog.Error($"[WebSocket] {errorMsg} (Detail: {lastConnectError?.Message ?? "Unknown error"})");
                _state = TransportState.Disconnected(TransportDisplayName, errorMsg);
                return false;
            }

            if (!string.Equals(connectedEndpoint.Host, originalEndpoint.Host, StringComparison.OrdinalIgnoreCase))
            {
                McpLog.Warn($"[WebSocket] Connected via fallback host '{connectedEndpoint.Host}' after '{originalEndpoint.Host}' failed.");
                _endpointUri = connectedEndpoint;
            }

            StartBackgroundLoops(_socket, connectionToken, gen);

            try
            {
                await SendRegisterAsync(connectionToken).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                string regMsg = $"Registration with server failed: {ex.Message}";
                if (quiet)
                    McpLog.Debug($"[WebSocket] {regMsg}");
                else
                    McpLog.Error($"[WebSocket] {regMsg}");
                _state = TransportState.Disconnected(TransportDisplayName, regMsg);
                return false;
            }

            return true;
        }

        /// <summary>
        /// Stops the connection loops and disposes of the connection CTS.
        /// Particularly useful when reconnecting, we want to ensure that background loops are cancelled correctly before starting new oens
        /// </summary>
        /// <param name="awaitTasks">Whether to await the receive and keep alive tasks before disposing.</param>
        private async Task StopConnectionLoopsAsync(bool awaitTasks = true)
        {
            if (_connectionCts != null && !_connectionCts.IsCancellationRequested)
            {
                try { _connectionCts.Cancel(); } catch { }
            }

            if (_receiveTask != null)
            {
                if (awaitTasks)
                {
                    try { await _receiveTask.ConfigureAwait(false); } catch { }
                    _receiveTask = null;
                }
                else if (_receiveTask.IsCompleted)
                {
                    _receiveTask = null;
                }
            }

            if (_keepAliveTask != null)
            {
                if (awaitTasks)
                {
                    try { await _keepAliveTask.ConfigureAwait(false); } catch { }
                    _keepAliveTask = null;
                }
                else if (_keepAliveTask.IsCompleted)
                {
                    _keepAliveTask = null;
                }
            }

            if (_connectionCts != null)
            {
                _connectionCts.Dispose();
                _connectionCts = null;
            }
        }

        private void StartBackgroundLoops(ClientWebSocket socket, CancellationToken token, int gen)
        {
            // Her yeni bağlantı KENDİ döngülerini alır; eski döngüler token iptali +
            // nesil kontrolüyle kendiliğinden ölür. Eski "önceki task bitmediyse yeni
            // loop başlatma" davranışı, server'a kayıtlı ama client'ın hiç dinlemediği
            // sessiz soketler bırakıyordu.
            _receiveTask = Task.Run(() => ReceiveLoopAsync(socket, token, gen), CancellationToken.None);
            _keepAliveTask = Task.Run(() => KeepAliveLoopAsync(socket, token, gen), CancellationToken.None);
        }

        private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken token, int gen)
        {
            while (!token.IsCancellationRequested && gen == Volatile.Read(ref _connectionGen))
            {
                try
                {
                    string message = await ReceiveMessageAsync(socket, token, gen).ConfigureAwait(false);
                    if (message == null)
                    {
                        // Close frame işlendi ya da soket öldü — devam etmek kapalı sokete
                        // ReceiveAsync atıp ikinci bir exception + mükerrer log üretir.
                        if (socket.State != WebSocketState.Open)
                            break;
                        continue;
                    }
                    await HandleMessageAsync(message, token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (WebSocketException wse)
                {
                    // Kopuş uyarısını tek yerden (HandleSocketClosureAsync) basıyoruz;
                    // burada Warn basmak her kopuşta 2-3 mükerrer konsol satırı üretiyordu.
                    McpLog.Debug($"[WebSocket] Receive loop ended: {wse.Message}");
                    await HandleSocketClosureAsync(wse.Message, gen).ConfigureAwait(false);
                    break;
                }
                catch (Exception ex)
                {
                    McpLog.Debug($"[WebSocket] Receive loop ended unexpectedly: {ex.Message}");
                    await HandleSocketClosureAsync(ex.Message, gen).ConfigureAwait(false);
                    break;
                }
            }
        }

        private async Task<string> ReceiveMessageAsync(ClientWebSocket socket, CancellationToken token, int gen)
        {
            byte[] rentedBuffer = System.Buffers.ArrayPool<byte>.Shared.Rent(8192);
            var buffer = new ArraySegment<byte>(rentedBuffer);
            using var ms = new MemoryStream(8192);

            try
            {
                while (!token.IsCancellationRequested)
                {
                    WebSocketReceiveResult result = await socket.ReceiveAsync(buffer, token).ConfigureAwait(false);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await HandleSocketClosureAsync(result.CloseStatusDescription ?? "Server closed connection", gen).ConfigureAwait(false);
                        return null;
                    }

                    if (result.Count > 0)
                    {
                        ms.Write(buffer.Array!, buffer.Offset, result.Count);
                    }

                    if (result.EndOfMessage)
                    {
                        break;
                    }
                }

                if (ms.Length == 0)
                {
                    return null;
                }

                return Encoding.UTF8.GetString(ms.ToArray());
            }
            finally
            {
                System.Buffers.ArrayPool<byte>.Shared.Return(rentedBuffer);
            }
        }

        private async Task HandleMessageAsync(string message, CancellationToken token)
        {
            JObject payload;
            try
            {
                payload = JObject.Parse(message);
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[WebSocket] Invalid JSON payload: {ex.Message}");
                return;
            }

            string messageType = payload.Value<string>("type") ?? string.Empty;

            switch (messageType)
            {
                case "welcome":
                    ApplyWelcome(payload);
                    break;
                case "registered":
                    await HandleRegisteredAsync(payload, token).ConfigureAwait(false);
                    break;
                case "execute":
                    await HandleExecuteAsync(payload, token).ConfigureAwait(false);
                    break;
                case "ping":
                    await SendPongAsync(token).ConfigureAwait(false);
                    break;
                default:
                    // No-op for unrecognised types (keep-alives, telemetry, etc.)
                    break;
            }
        }

        private void ApplyWelcome(JObject payload)
        {
            int? keepAliveSeconds = payload.Value<int?>("keepAliveInterval");
            if (keepAliveSeconds.HasValue && keepAliveSeconds.Value > 0)
            {
                _keepAliveInterval = TimeSpan.FromSeconds(keepAliveSeconds.Value);
            }
        }

        private async Task HandleRegisteredAsync(JObject payload, CancellationToken token)
        {
            string newSessionId = payload.Value<string>("session_id");
            if (!string.IsNullOrEmpty(newSessionId))
            {
                _sessionId = newSessionId;
                ProjectIdentityUtility.SetSessionId(_sessionId);
                _state = TransportState.Connected(TransportDisplayName, sessionId: _sessionId, details: _endpointUri.ToString());
                McpLog.Info($"[WebSocket] Registered with session ID: {_sessionId}", false);

                await SendRegisterToolsAsync(token).ConfigureAwait(false);
            }
        }

        private async Task SendRegisterToolsAsync(CancellationToken token)
        {
            if (_toolDiscoveryService == null) return;

            token.ThrowIfCancellationRequested();
            var tools = await GetEnabledToolsOnMainThreadAsync(token).ConfigureAwait(false);
            token.ThrowIfCancellationRequested();
            McpLog.Info($"[WebSocket] Preparing to register {tools.Count} tool(s) with the bridge.", false);
            var toolsArray = new JArray();

            foreach (var tool in tools)
            {
                var toolObj = new JObject
                {
                    ["name"] = tool.Name,
                    ["description"] = tool.Description,
                    ["structured_output"] = tool.StructuredOutput,
                    ["requires_polling"] = tool.RequiresPolling,
                    ["poll_action"] = tool.PollAction ?? "status",
                    ["max_poll_seconds"] = tool.MaxPollSeconds,
                    ["group"] = string.IsNullOrWhiteSpace(tool.Group) ? "core" : tool.Group
                };

                var paramsArray = new JArray();
                if (tool.Parameters != null)
                {
                    foreach (var p in tool.Parameters)
                    {
                        paramsArray.Add(new JObject
                        {
                            ["name"] = p.Name,
                            ["description"] = p.Description,
                            ["type"] = p.Type,
                            ["required"] = p.Required,
                            ["default_value"] = p.DefaultValue
                        });
                    }
                }
                toolObj["parameters"] = paramsArray;
                toolsArray.Add(toolObj);
            }

            var payload = new JObject
            {
                ["type"] = "register_tools",
                ["tools"] = toolsArray
            };

            await SendJsonAsync(payload, token).ConfigureAwait(false);
            McpLog.Info($"[WebSocket] Sent {tools.Count} tools registration", false);
        }

        public async Task ReregisterToolsAsync()
        {
            if (!IsConnected || _lifecycleCts == null)
            {
                McpLog.Warn("[WebSocket] Cannot reregister tools: not connected");
                return;
            }

            try
            {
                await SendRegisterToolsAsync(_lifecycleCts.Token).ConfigureAwait(false);
                McpLog.Info("[WebSocket] Tool reregistration completed", false);
            }
            catch (System.OperationCanceledException)
            {
                McpLog.Warn("[WebSocket] Tool reregistration cancelled");
            }
            catch (System.Exception ex)
            {
                McpLog.Error($"[WebSocket] Tool reregistration failed: {ex.Message}");
            }
        }

        private async Task HandleExecuteAsync(JObject payload, CancellationToken token)
        {
            string commandId = payload.Value<string>("id");
            string commandName = payload.Value<string>("name");
            JObject parameters = payload.Value<JObject>("params") ?? new JObject();
            int timeoutSeconds = payload.Value<int?>("timeout") ?? (int)DefaultCommandTimeout.TotalSeconds;

            if (string.IsNullOrEmpty(commandId) || string.IsNullOrEmpty(commandName))
            {
                McpLog.Warn("[WebSocket] Invalid execute payload (missing id or name)");
                return;
            }

            var commandEnvelope = new JObject
            {
                ["type"] = commandName,
                ["params"] = parameters
            };

            string responseJson;
            try
            {
                using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(token);
                timeoutCts.CancelAfter(TimeSpan.FromSeconds(Math.Max(1, timeoutSeconds)));
                responseJson = await TransportCommandDispatcher.ExecuteCommandJsonAsync(commandEnvelope.ToString(Formatting.None), timeoutCts.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                responseJson = JsonConvert.SerializeObject(new
                {
                    status = "error",
                    error = $"Command '{commandName}' timed out after {timeoutSeconds} seconds"
                });
            }
            catch (Exception ex)
            {
                responseJson = JsonConvert.SerializeObject(new
                {
                    status = "error",
                    error = ex.Message
                });
            }

            JToken resultToken;
            try
            {
                resultToken = JToken.Parse(responseJson);
            }
            catch
            {
                resultToken = new JObject
                {
                    ["status"] = "error",
                    ["error"] = "Invalid response payload"
                };
            }

            var responsePayload = new JObject
            {
                ["type"] = "command_result",
                ["id"] = commandId,
                ["result"] = resultToken
            };

            await SendJsonAsync(responsePayload, token).ConfigureAwait(false);
        }

        private async Task KeepAliveLoopAsync(ClientWebSocket socket, CancellationToken token, int gen)
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    await Task.Delay(_keepAliveInterval, token).ConfigureAwait(false);
                    if (gen != Volatile.Read(ref _connectionGen))
                    {
                        break;
                    }
                    if (socket.State != WebSocketState.Open)
                    {
                        break;
                    }
                    await SendPongAsync(token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    McpLog.Debug($"[WebSocket] Keep-alive failed: {ex.Message}");
                    await HandleSocketClosureAsync(ex.Message, gen).ConfigureAwait(false);
                    break;
                }
            }
        }

        private async Task SendRegisterAsync(CancellationToken token)
        {
            var registerPayload = new JObject
            {
                ["type"] = "register",
                // session_id is now server-authoritative; omitted here or sent as null
                ["project_name"] = _projectName,
                ["project_hash"] = _projectHash,
                ["unity_version"] = _unityVersion,
                ["project_path"] = _projectPath
            };

            await SendJsonAsync(registerPayload, token).ConfigureAwait(false);
        }

        private Task SendPongAsync(CancellationToken token)
        {
            var payload = new JObject
            {
                ["type"] = "pong",
                ["session_id"] = _sessionId  // Include session ID for server-side tracking
            };
            return SendJsonAsync(payload, token);
        }

        private async Task SendJsonAsync(JObject payload, CancellationToken token)
        {
            if (_socket == null)
            {
                throw new InvalidOperationException("WebSocket is not initialised");
            }

            string json = payload.ToString(Formatting.None);
            byte[] bytes = Encoding.UTF8.GetBytes(json);
            var buffer = new ArraySegment<byte>(bytes);

            await _sendLock.WaitAsync(token).ConfigureAwait(false);
            try
            {
                if (_socket.State != WebSocketState.Open)
                {
                    throw new InvalidOperationException("WebSocket is not open");
                }

                await _socket.SendAsync(buffer, WebSocketMessageType.Text, true, token).ConfigureAwait(false);
            }
            finally
            {
                _sendLock.Release();
            }
        }

        private async Task HandleSocketClosureAsync(string reason, int gen)
        {
            // Capture stack trace for debugging disconnection triggers
            var stackTrace = new System.Diagnostics.StackTrace(true);
            McpLog.Debug($"[WebSocket] HandleSocketClosureAsync called (gen={gen}). Reason: {reason}\nStack trace:\n{stackTrace}");

            // Bayat nesilden (eski soketin döngüsünden) gelen kapanış → yok say.
            // Bu olmadan eski döngüler reconnect tetikleyip taze bağlantının
            // session'ını evict ettiriyordu.
            if (gen != Volatile.Read(ref _connectionGen))
            {
                return;
            }

            if (_lifecycleCts == null || _lifecycleCts.IsCancellationRequested)
            {
                return;
            }

            if (Interlocked.CompareExchange(ref _isReconnectingFlag, 1, 0) != 0)
            {
                return;
            }

            // Kopuş konsola YAZILMAZ (bağlantı durumu app UI'ında zaten görünür;
            // arka plan reconnect sessiz çalışır). Kalıcı kopuşta tek kırmızı
            // error'u AttemptReconnectAsync (schedule tükenince) basar.
            _isConnected = false;
            _state = _state.WithError(reason ?? "Connection closed");
            McpLog.Debug($"[WebSocket] Connection closed: {reason}");

            await StopConnectionLoopsAsync(awaitTasks: false).ConfigureAwait(false);

            _ = Task.Run(() => AttemptReconnectAsync(_lifecycleCts.Token), CancellationToken.None);
        }

        private async Task AttemptReconnectAsync(CancellationToken token)
        {
            try
            {
                await StopConnectionLoopsAsync().ConfigureAwait(false);

                foreach (TimeSpan delay in ReconnectSchedule)
                {
                    if (token.IsCancellationRequested)
                    {
                        return;
                    }

                    if (delay > TimeSpan.Zero)
                    {
                        try { await Task.Delay(delay, token).ConfigureAwait(false); }
                        catch (OperationCanceledException) { return; }
                    }

                    // Beklerken başka bir yol (StartAsync/diğer reconnect) bağlandıysa
                    // sağlıklı bağlantıyı ezme.
                    if (_isConnected)
                    {
                        return;
                    }

                    if (await EstablishConnectionAsync(token, quiet: true).ConfigureAwait(false))
                    {
                        _state = TransportState.Connected(TransportDisplayName, sessionId: _sessionId, details: _endpointUri.ToString());
                        _isConnected = true;
                        McpLog.Info("[WebSocket] Reconnected to MCP server", false);
                        return;
                    }
                }

                // Schedule exhausted — keep retrying every 30 s for ReconnectTailBudget, so a
                // server outage longer than ~49 s (a slow first start) still recovers.
                // Kalıcı kopuşun konsol bildirimi bu error ve bütçe bitince düşen error'dur.
                McpLog.Error($"[WebSocket] MCP sunucusuna yeniden bağlanılamadı — sunucu kapalı görünüyor. Arka planda {ReconnectTailBudget.TotalMinutes:0} dk boyunca {ReconnectTailInterval.TotalSeconds} sn'de bir denenecek.");
                _state = _state.WithError($"Server unreachable – retrying every {ReconnectTailInterval.TotalSeconds} s");
                DateTime deadline = DateTime.UtcNow + ReconnectTailBudget;
                while (!token.IsCancellationRequested)
                {
                    if (DateTime.UtcNow >= deadline)
                    {
                        McpLog.Error($"[WebSocket] MCP sunucusuna {ReconnectTailBudget.TotalMinutes:0} dk boyunca bağlanılamadı, deneme durdu. Yeniden bağlanmak için Gamachine'de Unity MCP'yi kapatıp açın.");
                        _state = _state.WithError("Server unreachable – stopped retrying; turn Unity MCP off and on in Gamachine");
                        return;
                    }

                    try { await Task.Delay(ReconnectTailInterval, token).ConfigureAwait(false); }
                    catch (OperationCanceledException) { return; }

                    if (_isConnected)
                    {
                        return;
                    }

                    if (await EstablishConnectionAsync(token, quiet: true).ConfigureAwait(false))
                    {
                        _state = TransportState.Connected(TransportDisplayName, sessionId: _sessionId, details: _endpointUri.ToString());
                        _isConnected = true;
                        McpLog.Info("[WebSocket] Reconnected to MCP server", false);
                        return;
                    }
                }
            }
            finally
            {
                Interlocked.Exchange(ref _isReconnectingFlag, 0);
            }
        }

        /// <summary>
        /// Reads the shared secret that guards a local (non-remote-hosted) MCP server.
        /// </summary>
        /// <remarks>
        /// A local server's plugin hub answers every process running as this user, and
        /// treats whatever connects as a Unity Editor: it can register itself as an
        /// instance and push tool definitions that the user's AI clients then see and
        /// call. The server therefore requires the same shared secret it uses for its
        /// /api routes, sent as an X-API-Key handshake header.
        ///
        /// The server persists that secret to a file instead of regenerating it per
        /// start precisely so this side can pick it up with nothing for the user to
        /// configure. ~/.unity-mcp is the directory this package already shares with
        /// the Python server on every platform (port registry, status files).
        ///
        /// Returns empty when the file is absent — an older or third-party server, or
        /// one that never wrote it. The handshake then goes out without the header and
        /// the server decides; a local server built with the gate will refuse it.
        /// </remarks>
        // Delegates to HttpEndpointUtility so the token location is defined once.
        // A second copy of this path would drift the moment either side moves.
        private static string ReadLocalApiToken() => HttpEndpointUtility.ReadLocalApiToken();

        private static Uri BuildWebSocketUri(string baseUrl)
        {
            if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out var httpUri))
            {
                throw new InvalidOperationException($"Invalid MCP base URL: {baseUrl}");
            }

            // Replace bind-only addresses for client connections
            // 0.0.0.0 and :: are only valid for server binding, not client connections
            string host = httpUri.Host;
            if (host == "0.0.0.0")
            {
                McpLog.Warn($"[WebSocket] Base URL host '{host}' is bind-only; using '127.0.0.1' for client connection.");
                host = "127.0.0.1";
            }
            else if (host == "::")
            {
                McpLog.Warn($"[WebSocket] Base URL host '{host}' is bind-only; using '::1' for client connection.");
                host = "::1";
            }

            var builder = new UriBuilder(httpUri)
            {
                Scheme = httpUri.Scheme.Equals("https", StringComparison.OrdinalIgnoreCase) ? "wss" : "ws",
                Host = host,
                Path = httpUri.AbsolutePath.TrimEnd('/') + "/hub/plugin"
            };

            return builder.Uri;
        }

        private static List<Uri> BuildConnectionCandidateUris(Uri endpointUri)
        {
            var candidates = new List<Uri>();
            if (endpointUri == null)
            {
                return candidates;
            }

            candidates.Add(endpointUri);

            if (!string.Equals(endpointUri.Host, "localhost", StringComparison.OrdinalIgnoreCase))
            {
                return candidates;
            }

            // Retry localhost using explicit loopback hosts to avoid DNS family ambiguity on some machines.
            TryAddCandidate(candidates, endpointUri, "127.0.0.1");
            TryAddCandidate(candidates, endpointUri, "::1");
            return candidates;
        }

        private static void TryAddCandidate(List<Uri> candidates, Uri template, string host)
        {
            try
            {
                var builder = new UriBuilder(template) { Host = host };
                Uri candidate = builder.Uri;
                foreach (Uri existing in candidates)
                {
                    if (Uri.Compare(existing, candidate, UriComponents.AbsoluteUri, UriFormat.SafeUnescaped, StringComparison.OrdinalIgnoreCase) == 0)
                    {
                        return;
                    }
                }
                candidates.Add(candidate);
            }
            catch
            {
                // Ignore malformed fallback candidate and continue with remaining options.
            }
        }
    }
}
