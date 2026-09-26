using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Records each script compilation as a numbered epoch, with the compiler's own
    /// messages, so a caller can snapshot the epoch before a change and later tell
    /// "this change compiled clean" from "no compile has happened yet".
    /// </summary>
    /// <remarks>
    /// Everything lives in SessionState. A successful compile ends in a domain reload
    /// that wipes statics, and the finish of that very compile is what callers wait on;
    /// SessionState survives the reload and dies with the editor session.
    /// The console is not a substitute: it reads 0 errors while a compile is pending,
    /// and still shows errors a later compile already fixed.
    /// </remarks>
    [InitializeOnLoad]
    internal static class CompileTracker
    {
        private const string Prefix = "MCPForUnity.CompileTracker.";
        private const string EpochKey = Prefix + "Epoch";
        private const string FinishedEpochKey = Prefix + "FinishedEpoch";
        private const string StartedKey = Prefix + "StartedUnixMs";
        private const string FinishedKey = Prefix + "FinishedUnixMs";
        private const string ReloadedKey = Prefix + "ReloadedUnixMs";
        private const string FailedKey = Prefix + "Failed";
        private const string ErrorsKey = Prefix + "Errors";
        private const string WarningsKey = Prefix + "Warnings";
        private const string ErrorCountKey = Prefix + "ErrorCount";
        private const string WarningCountKey = Prefix + "WarningCount";

        internal const int MaxStoredErrors = 200;
        internal const int MaxStoredWarnings = 50;
        private const int MaxChangedPaths = 5;

        private static readonly Regex CodePattern = new(@"\b(?:error|warning)\s+([A-Z]{2,}\d+)\b", RegexOptions.Compiled);

        // Messages of the running compile, buffered per domain and flushed to
        // SessionState on each assembly so a crash mid-compile loses at most one.
        private static List<JObject> _errors;
        private static List<JObject> _warnings;
        private static bool _builderActive;

        static CompileTracker()
        {
            // The static ctor of an [InitializeOnLoad] class runs once per domain load,
            // so this is the reload time even if afterAssemblyReload is not raised for
            // the first load of the session.
            SetUnixMs(ReloadedKey, NowUnixMs());

#pragma warning disable CS0618 // AssemblyBuilder is obsolete, but ExecuteCode still uses it
            CompilationPipeline.compilationStarted += context =>
            {
                // execute_code compiles its snippet with an AssemblyBuilder, which raises
                // the same pipeline events (measured: the context is the builder) but
                // never reloads the domain. Counted as an epoch it would leave every
                // later status "waiting for reload" until the next real compile.
                _builderActive = context is AssemblyBuilder;
                if (!_builderActive)
                    OnCompilationStarted();
            };
            CompilationPipeline.assemblyCompilationFinished += (path, messages) =>
            {
                if (!_builderActive)
                    OnAssemblyCompilationFinished(path, messages);
            };
            CompilationPipeline.compilationFinished += context =>
            {
                bool builder = _builderActive || context is AssemblyBuilder;
                _builderActive = false;
                if (!builder)
                    OnCompilationFinished();
            };
#pragma warning restore CS0618
            AssemblyReloadEvents.afterAssemblyReload += () => SetUnixMs(ReloadedKey, NowUnixMs());
        }

        internal static int Epoch => SessionState.GetInt(EpochKey, 0);
        internal static int FinishedEpoch => SessionState.GetInt(FinishedEpochKey, 0);

        internal static void OnCompilationStarted()
        {
            SessionState.SetInt(EpochKey, Epoch + 1);
            SetUnixMs(StartedKey, NowUnixMs());
            _errors = new List<JObject>();
            _warnings = new List<JObject>();
            SessionState.SetString(ErrorsKey, "[]");
            SessionState.SetString(WarningsKey, "[]");
            SessionState.SetInt(ErrorCountKey, 0);
            SessionState.SetInt(WarningCountKey, 0);
        }

        internal static void OnAssemblyCompilationFinished(string assemblyPath, CompilerMessage[] messages)
        {
            if (messages == null || messages.Length == 0)
                return;
            _errors ??= LoadList(ErrorsKey);
            _warnings ??= LoadList(WarningsKey);
            int errorCount = SessionState.GetInt(ErrorCountKey, 0);
            int warningCount = SessionState.GetInt(WarningCountKey, 0);

            foreach (var message in messages)
            {
                if (message.type == CompilerMessageType.Error)
                {
                    errorCount++;
                    if (_errors.Count < MaxStoredErrors)
                        _errors.Add(ToJson(message, assemblyPath));
                }
                else if (message.type == CompilerMessageType.Warning)
                {
                    warningCount++;
                    if (_warnings.Count < MaxStoredWarnings)
                        _warnings.Add(ToJson(message, assemblyPath));
                }
            }

            SessionState.SetString(ErrorsKey, JsonConvert.SerializeObject(_errors));
            SessionState.SetString(WarningsKey, JsonConvert.SerializeObject(_warnings));
            SessionState.SetInt(ErrorCountKey, errorCount);
            SessionState.SetInt(WarningCountKey, warningCount);
        }

        internal static void OnCompilationFinished()
        {
            // Not EditorUtility.scriptCompilationFailed: inside this event it still holds
            // the PREVIOUS compile's result (measured on 6000.4: a clean compile after a
            // failed one read true here, false a moment later).
            bool failed = SessionState.GetInt(ErrorCountKey, 0) > 0;
            SessionState.SetBool(FailedKey, failed);
            SetUnixMs(FinishedKey, NowUnixMs());
            SessionState.SetInt(FinishedEpochKey, Epoch);
        }

        internal static JObject ToJson(CompilerMessage message, string assemblyPath)
        {
            var match = CodePattern.Match(message.message ?? string.Empty);
            return new JObject
            {
                ["code"] = match.Success ? match.Groups[1].Value : null,
                ["file"] = message.file?.Replace('\\', '/'),
                ["line"] = message.line,
                ["column"] = message.column,
                ["message"] = message.message,
                ["assembly"] = string.IsNullOrEmpty(assemblyPath) ? null : Path.GetFileNameWithoutExtension(assemblyPath),
            };
        }

        /// <summary>
        /// Live status, read on the main thread. <paramref name="scanChanges"/> walks the
        /// script folders for files written after the last compile started.
        /// </summary>
        internal static JObject GetStatus(bool scanChanges)
        {
            int epoch = Epoch;
            int finishedEpoch = FinishedEpoch;
            long? startedAt = GetUnixMs(StartedKey);
            long? finishedAt = GetUnixMs(FinishedKey);
            long? reloadedAt = GetUnixMs(ReloadedKey);

            var status = new JObject
            {
                ["is_compiling"] = EditorApplication.isCompiling,
                ["is_updating"] = EditorApplication.isUpdating,
                ["compilation_failed_now"] = EditorUtility.scriptCompilationFailed,
                ["epoch"] = epoch,
                ["finished_epoch"] = finishedEpoch,
                ["last_failed"] = finishedEpoch > 0 && SessionState.GetBool(FailedKey, false),
                ["error_count"] = SessionState.GetInt(ErrorCountKey, 0),
                ["warning_count"] = SessionState.GetInt(WarningCountKey, 0),
                ["errors"] = LoadArray(ErrorsKey),
                ["warnings"] = LoadArray(WarningsKey),
                ["started_at"] = startedAt,
                ["finished_at"] = finishedAt,
                ["reloaded_at"] = reloadedAt,
                ["reload_done_after_finish"] = finishedAt.HasValue && reloadedAt.HasValue && reloadedAt.Value > finishedAt.Value,
                ["now"] = NowUnixMs(),
            };

            if (scanChanges)
            {
                // The last compile saw every file written before it started. With no
                // compile this session, the domain load is the newest known-good point.
                long? since = epoch > 0 ? startedAt : reloadedAt;
                status["scripts_changed_since_compile"] = since.HasValue
                    ? ScanChangedScripts(since.Value)
                    : null;
            }
            return status;
        }

        internal static JObject ScanChangedScripts(long sinceUnixMs)
        {
            var watch = System.Diagnostics.Stopwatch.StartNew();
            var since = DateTimeOffset.FromUnixTimeMilliseconds(sinceUnixMs).UtcDateTime;
            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            var paths = new List<string>();
            int count = 0;

            foreach (string root in ScriptRoots())
            {
                var pending = new Stack<string>();
                pending.Push(root);
                while (pending.Count > 0)
                {
                    string dir = pending.Pop();
                    string[] subdirs;
                    string[] files;
                    try
                    {
                        subdirs = Directory.GetDirectories(dir);
                        files = Directory.GetFiles(dir);
                    }
                    catch (Exception)
                    {
                        continue;
                    }
                    foreach (string sub in subdirs)
                    {
                        if (!IsIgnoredFolder(Path.GetFileName(sub)))
                            pending.Push(sub);
                    }
                    foreach (string file in files)
                    {
                        if (!IsScriptFile(file))
                            continue;
                        DateTime written;
                        try { written = File.GetLastWriteTimeUtc(file); }
                        catch (Exception) { continue; }
                        if (written <= since)
                            continue;
                        count++;
                        if (paths.Count < MaxChangedPaths)
                            paths.Add(RelativeTo(projectRoot, file));
                    }
                }
            }

            watch.Stop();
            return new JObject
            {
                ["count"] = count,
                ["paths"] = new JArray(paths),
                ["scan_ms"] = Math.Round(watch.Elapsed.TotalMilliseconds, 2),
            };
        }

        // Assets/ plus embedded packages under Packages/. Packages referenced by a
        // file: path elsewhere on disk are not scanned: finding them needs the
        // package manager's registered list, and a change there is still caught by
        // the epoch wait when it was made through the MCP tools.
        private static IEnumerable<string> ScriptRoots()
        {
            yield return Application.dataPath;
            string packages = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Packages");
            string[] embedded;
            try { embedded = Directory.Exists(packages) ? Directory.GetDirectories(packages) : Array.Empty<string>(); }
            catch (Exception) { embedded = Array.Empty<string>(); }
            foreach (string dir in embedded)
            {
                if (!IsIgnoredFolder(Path.GetFileName(dir)))
                    yield return dir;
            }
        }

        // Unity itself does not import folders that start with '.' or end with '~'.
        internal static bool IsIgnoredFolder(string name)
            => string.IsNullOrEmpty(name) || name.StartsWith(".", StringComparison.Ordinal) || name.EndsWith("~", StringComparison.Ordinal);

        internal static bool IsScriptFile(string path)
            => path.EndsWith(".cs", StringComparison.OrdinalIgnoreCase)
               || path.EndsWith(".asmdef", StringComparison.OrdinalIgnoreCase)
               || path.EndsWith(".asmref", StringComparison.OrdinalIgnoreCase);

        private static string RelativeTo(string root, string file)
        {
            string normalized = file.Replace('\\', '/');
            string prefix = root.Replace('\\', '/').TrimEnd('/') + "/";
            return normalized.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
                ? normalized.Substring(prefix.Length)
                : normalized;
        }

        private static List<JObject> LoadList(string key)
        {
            var list = new List<JObject>();
            foreach (var token in LoadArray(key))
            {
                if (token is JObject obj)
                    list.Add(obj);
            }
            return list;
        }

        private static JArray LoadArray(string key)
        {
            string raw = SessionState.GetString(key, "[]");
            try { return JArray.Parse(string.IsNullOrEmpty(raw) ? "[]" : raw); }
            catch (JsonException) { return new JArray(); }
        }

        private static long NowUnixMs() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        // SessionState has no long overload; a unix-ms value does not fit an int.
        internal static long? GetUnixMs(string key)
        {
            string raw = SessionState.GetString(key, string.Empty);
            return long.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out long value)
                ? value
                : (long?)null;
        }

        internal static void SetUnixMs(string key, long value)
            => SessionState.SetString(key, value.ToString(CultureInfo.InvariantCulture));
    }
}
