using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;
// Hem System.Reflection hem UnityEditor.Compilation 'Assembly' tanımlar (CS0104)
using Assembly = System.Reflection.Assembly;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("execute_code", AutoRegister = false, Group = "scripting_ext")]
    public static class ExecuteCode
    {
        private const int MaxCodeLength = 50000;
        private const int MaxHistoryEntries = 50;
        private const int MaxHistoryCodePreview = 500;
        internal const int WrapperLineOffset = 10;
        private const string WrapperClassName = "MCPDynamicCode";
        private const string WrapperMethodName = "Execute";
        private const int CompileTimeoutSeconds = 60;

        private const string ActionExecute = "execute";
        private const string ActionGetHistory = "get_history";
        private const string ActionClearHistory = "clear_history";
        private const string ActionReplay = "replay";

        private static readonly List<HistoryEntry> _history = new List<HistoryEntry>();
        private static int _buildSeq;

        private static readonly HashSet<string> _blockedPatterns = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "System.IO.File.Delete",
            "System.IO.Directory.Delete",
            "FileUtil.DeleteFileOrDirectory",
            "AssetDatabase.DeleteAsset",
            "AssetDatabase.MoveAssetToTrash",
            "EditorApplication.Exit",
            "Process.Start",
            "Process.Kill",
            "while(true)",
            "while (true)",
            "for(;;)",
            "for (;;)",
        };

        public static async Task<object> HandleCommand(JObject @params)
        {
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");

            var p = new ToolParams(@params);
            var actionResult = p.GetRequired("action");
            if (!actionResult.IsSuccess)
                return new ErrorResponse(actionResult.ErrorMessage);

            string action = actionResult.Value.ToLowerInvariant();

            switch (action)
            {
                case ActionExecute:
                    return await HandleExecute(@params);
                case ActionGetHistory:
                    return HandleGetHistory(@params);
                case ActionClearHistory:
                    return HandleClearHistory();
                case ActionReplay:
                    return await HandleReplay(@params);
                default:
                    return new ErrorResponse(
                        $"Unknown action: '{action}'. Valid actions: {ActionExecute}, {ActionGetHistory}, {ActionClearHistory}, {ActionReplay}");
            }
        }

        private static async Task<object> HandleExecute(JObject @params)
        {
            string code = @params["code"]?.ToString();
            if (string.IsNullOrWhiteSpace(code))
                return new ErrorResponse("Required parameter 'code' is missing or empty.");

            if (code.Length > MaxCodeLength)
                return new ErrorResponse($"Code exceeds maximum length of {MaxCodeLength} characters.");

            bool safetyChecks = @params["safety_checks"]?.Value<bool>() ?? true;

            if (safetyChecks)
            {
                var violation = CheckBlockedPatterns(code);
                if (violation != null)
                    return new ErrorResponse($"Blocked pattern detected: {violation}");
            }

            try
            {
                var startTime = DateTime.UtcNow;
                var result = await CompileAndExecute(code);
                var elapsed = (DateTime.UtcNow - startTime).TotalMilliseconds;

                AddToHistory(code, result, elapsed, safetyChecks);
                return result;
            }
            catch (Exception e)
            {
                McpLog.Error($"[ExecuteCode] Execution failed: {e}");
                var errorResult = new ErrorResponse($"Execution failed: {e.Message}");
                AddToHistory(code, errorResult, 0, safetyChecks);
                return errorResult;
            }
        }

        private static object HandleGetHistory(JObject @params)
        {
            int limit = @params["limit"]?.Value<int>() ?? 10;
            limit = Math.Clamp(limit, 1, MaxHistoryEntries);

            if (_history.Count == 0)
                return new SuccessResponse("No execution history.", new { total = 0, entries = new object[0] });

            var entries = _history.Skip(Math.Max(0, _history.Count - limit)).ToList();
            return new SuccessResponse($"Returning {entries.Count} of {_history.Count} history entries.", new
            {
                total = _history.Count,
                entries = entries.Select((e, i) => new
                {
                    index = _history.Count - entries.Count + i,
                    codePreview = e.code.Length > MaxHistoryCodePreview
                        ? e.code.Substring(0, MaxHistoryCodePreview) + "..."
                        : e.code,
                    e.success,
                    e.resultPreview,
                    e.elapsedMs,
                    e.timestamp,
                    e.safetyChecksEnabled,
                }).ToList(),
            });
        }

        private static object HandleClearHistory()
        {
            int count = _history.Count;
            _history.Clear();
            return new SuccessResponse($"Cleared {count} history entries.");
        }

        private static async Task<object> HandleReplay(JObject @params)
        {
            if (_history.Count == 0)
                return new ErrorResponse("No execution history to replay.");

            int? index = @params["index"]?.Value<int>();
            if (index == null || index < 0 || index >= _history.Count)
                return new ErrorResponse($"Invalid history index. Valid range: 0-{_history.Count - 1}");

            var entry = _history[index.Value];
            // Çağıran `safety_checks` gönderdiyse ONUN dediği geçerli; geçmişteki
            // değer yalnız hiç gönderilmediğinde kullanılır ve o durumda da
            // güvenli tarafa (açık) düşülür.
            //
            // Eskiden koşulsuz `entry.safetyChecksEnabled` alınıyordu: bir kez
            // `safety_checks=false` ile koşan kod, sonraki her replay'de sessizce
            // yine kontrolsüz koşuyordu. Onay kartı da bunu gösteremiyordu, çünkü
            // kart yalnız gönderilen parametreleri yazıyor — kullanıcı
            // `action: replay, index: 3` görüp kontrollerin kapalı olduğunu
            // bilemiyordu. Miras alınan bir izin, verilmemiş bir izindir.
            bool replaySafety = @params["safety_checks"]?.Value<bool>() ?? true;
            var replayParams = JObject.FromObject(new
            {
                action = ActionExecute,
                code = entry.code,
                safety_checks = replaySafety,
            });
            return await HandleExecute(replayParams);
        }

        // ──────────────────── Compilation ────────────────────
        // Unity'nin KENDİ derleyicisi (AssemblyBuilder → Unity'nin Roslyn'i) kullanılır:
        // dil sürümü her zaman projenin C# sürümüyle aynı, referans çözümü Unity'de
        // (komut satırı uzunluk limiti yok), gömülü DLL/sürüm çakışması yok.

        private static async Task<object> CompileAndExecute(string code)
        {
            string wrappedSource = WrapUserCode(code);

            string dir = Path.GetFullPath(Path.Combine("Temp", "MCPExecuteCode"));
            Directory.CreateDirectory(dir);
            // Domain reload sayaçları sıfırlar → tick + sayaç birlikte benzersizlik sağlar.
            // Benzersiz assembly adı: aynı-isimli assembly'yi tekrar tekrar yüklemeyi önler.
            string stamp = $"{DateTime.UtcNow.Ticks:x}_{System.Threading.Interlocked.Increment(ref _buildSeq)}";
            string srcPath = Path.Combine(dir, $"{WrapperClassName}_{stamp}.cs");
            string dllPath = Path.Combine(dir, $"{WrapperClassName}_{stamp}.dll");

            File.WriteAllText(srcPath, wrappedSource, new UTF8Encoding(false));

            try
            {
                var (ok, messages) = await BuildWithUnityCompiler(srcPath, dllPath);
                if (!ok)
                {
                    var errors = messages
                        .Where(m => m.type == CompilerMessageType.Error)
                        .Select(m => $"Line {Math.Max(1, m.line - WrapperLineOffset)}: {StripFilePrefix(m.message)}")
                        .ToList();
                    if (errors.Count == 0)
                        errors.Add("Compilation failed (no compiler messages — editor may be busy).");
                    return new ErrorResponse("Compilation failed", new { errors });
                }

                // Byte'lardan yükle: dosya kilidi kalmaz, temp dosyası silinebilir.
                var assembly = Assembly.Load(File.ReadAllBytes(dllPath));
                return InvokeCompiled(assembly);
            }
            finally
            {
                try { File.Delete(srcPath); } catch { }
                try { File.Delete(dllPath); } catch { }
                try { File.Delete(Path.ChangeExtension(dllPath, ".pdb")); } catch { }
            }
        }

        private static Task<(bool ok, CompilerMessage[] messages)> BuildWithUnityCompiler(string srcPath, string dllPath)
        {
            var tcs = new TaskCompletionSource<(bool, CompilerMessage[])>(TaskCreationOptions.RunContinuationsAsynchronously);

            // Unity 6.4 deprecates AssemblyBuilder; replacement APIs differ in 6.4 vs 6.5+, so preserve behavior.
#pragma warning disable CS0618
            var builder = new AssemblyBuilder(dllPath, new[] { srcPath })
#pragma warning restore CS0618
            {
                flags = AssemblyBuilderFlags.EditorAssembly,
                referencesOptions = ReferencesOptions.UseEngineModules,
                additionalReferences = CollectProjectReferences(),
            };

            builder.buildFinished += (path, messages) =>
            {
                bool ok = File.Exists(dllPath) && messages.All(m => m.type != CompilerMessageType.Error);
                tcs.TrySetResult((ok, messages));
            };

            var start = DateTime.UtcNow;
            bool buildStarted = false;

            // EditorApplication.update ile sürüş (RefreshUnity deseninin aynısı):
            // editör derleme/import yaparken Build() reddedilir → hazır olana dek tick'le.
            void Tick()
            {
                try
                {
                    if (tcs.Task.IsCompleted)
                    {
                        EditorApplication.update -= Tick;
                        return;
                    }
                    if ((DateTime.UtcNow - start) > TimeSpan.FromSeconds(CompileTimeoutSeconds))
                    {
                        EditorApplication.update -= Tick;
                        tcs.TrySetException(new TimeoutException(
                            $"Compilation timed out after {CompileTimeoutSeconds}s (editor busy compiling/importing?)."));
                        return;
                    }
                    if (buildStarted)
                        return; // buildFinished bekleniyor
                    if (EditorApplication.isCompiling || EditorApplication.isUpdating)
                        return; // editör meşgul — sonraki tick'te dene
                    // Build() false: derleme hattı meşgul (başka bir AssemblyBuilder) → tekrar dene
                    if (builder.Build())
                        buildStarted = true;
                }
                catch (Exception ex)
                {
                    EditorApplication.update -= Tick;
                    tcs.TrySetException(ex);
                }
            }

            EditorApplication.update += Tick;
            try { EditorApplication.QueuePlayerLoopUpdate(); } catch { }
            return tcs.Task;
        }

        private static string[] CollectProjectReferences()
        {
            // Kullanıcı kodu projenin kendi tiplerine (Assembly-CSharp, paket assembly'leri)
            // ve Assets'teki üçüncü parti DLL'lere erişebilsin. UnityEngine/UnityEditor
            // modülleri EditorAssembly bayrağı + UseEngineModules'ten zaten gelir.
            var refs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (var asm in CompilationPipeline.GetAssemblies(AssembliesType.Editor))
            {
                if (!string.IsNullOrEmpty(asm.outputPath) && File.Exists(asm.outputPath))
                    refs.Add(Path.GetFullPath(asm.outputPath));
            }
            foreach (var path in CompilationPipeline.GetPrecompiledAssemblyPaths(
                         CompilationPipeline.PrecompiledAssemblySources.UserAssembly))
            {
                if (File.Exists(path))
                    refs.Add(Path.GetFullPath(path));
            }

            return refs.ToArray();
        }

        private static object InvokeCompiled(Assembly assembly)
        {
            var type = assembly.GetType(WrapperClassName);
            if (type == null)
                return new ErrorResponse("Internal error: failed to find compiled type.");

            var method = type.GetMethod(WrapperMethodName, BindingFlags.Public | BindingFlags.Static);
            if (method == null)
                return new ErrorResponse("Internal error: failed to find Execute method.");

            object result = null;
            Exception executionError = null;

            try
            {
                result = method.Invoke(null, null);
            }
            catch (TargetInvocationException tie)
            {
                executionError = tie.InnerException ?? tie;
            }
            catch (Exception e)
            {
                executionError = e;
            }

            if (executionError != null)
                return new ErrorResponse($"Runtime error: {executionError.Message}",
                    new { exceptionType = executionError.GetType().Name, stackTrace = executionError.StackTrace });

            if (result != null)
                return new SuccessResponse("Code executed successfully.",
                    new { result = SerializeResult(result) });

            return new SuccessResponse("Code executed successfully.");
        }

        // ──────────────────── Shared helpers ────────────────────

        private static string WrapUserCode(string code)
        {
            var sb = new StringBuilder();
            sb.AppendLine("using System;");
            sb.AppendLine("using System.Collections.Generic;");
            sb.AppendLine("using System.Linq;");
            sb.AppendLine("using System.Reflection;");
            sb.AppendLine("using UnityEngine;");
            sb.AppendLine("using UnityEditor;");
            sb.AppendLine($"public static class {WrapperClassName}");
            sb.AppendLine("{");
            sb.AppendLine($"    public static object {WrapperMethodName}()");
            sb.AppendLine("    {");
            sb.AppendLine(code);
            sb.AppendLine("    }");
            sb.AppendLine("}");
            return sb.ToString();
        }

        private static string StripFilePrefix(string message)
        {
            // CompilerMessage.message "Temp\...\X.cs(11,9): error CS0029: ..." formatında —
            // ham temp yolu ve İÇ satır numarası ajanı şaşırtır ("Line 1" ile çelişir).
            // "error/warning CODE: ..." kısmını bırak.
            int idx = message.IndexOf("): ", StringComparison.Ordinal);
            return idx >= 0 && idx + 3 < message.Length ? message.Substring(idx + 3) : message;
        }

        private static string CheckBlockedPatterns(string code)
        {
            foreach (var pattern in _blockedPatterns)
            {
                if (code.IndexOf(pattern, StringComparison.OrdinalIgnoreCase) >= 0)
                    // Mesaj bypass'ı ARTIK ÖĞRETMİYOR. Eski hâli
                    // "Disable safety checks with safety_checks=false if this is
                    // intentional" diyordu: engellenen bir deseni gören modele,
                    // aynı yanıtta engeli kaldırmanın tam parametresi veriliyordu.
                    // Bir savunmanın hata metni, o savunmanın kullanım kılavuzu
                    // olmamalı. Bayrak hâlâ var (kullanıcı bilinçli olarak
                    // kapatabilir) ama artık öneri olarak sunulmuyor.
                    return $"Code contains blocked pattern: '{pattern}'. Rewrite the code without it, or ask the user to allow this explicitly.";
            }
            return null;
        }

        private static void AddToHistory(string code, object result, double elapsedMs, bool safetyChecks)
        {
            string preview;
            if (result is SuccessResponse sr)
                preview = sr.Data?.ToString() ?? sr.Message;
            else if (result is ErrorResponse er)
                preview = er.Error;
            else
                preview = result?.ToString() ?? "null";

            if (preview != null && preview.Length > 200)
                preview = preview.Substring(0, 200) + "...";

            _history.Add(new HistoryEntry
            {
                code = code,
                success = result is SuccessResponse,
                resultPreview = preview,
                elapsedMs = Math.Round(elapsedMs, 1),
                timestamp = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"),
                safetyChecksEnabled = safetyChecks,
            });

            while (_history.Count > MaxHistoryEntries)
                _history.RemoveAt(0);
        }

        private static object SerializeResult(object result)
        {
            if (result == null) return null;

            var type = result.GetType();
            if (type.IsPrimitive || result is string || result is decimal)
                return result;

            try
            {
                return JToken.FromObject(result);
            }
            catch
            {
                return result.ToString();
            }
        }

        private class HistoryEntry
        {
            public string code;
            public bool success;
            public string resultPreview;
            public double elapsedMs;
            public string timestamp;
            public bool safetyChecksEnabled;
        }
    }
}
