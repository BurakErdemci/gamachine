using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Turns the compile tracker's status into one verdict and decides what the test tools may
    /// do with it. Lives in the Editor because every route reaches it: the MCP tools, the CLI's
    /// POST /api/command and the stdio bridge all end in RunTests / GetTestJob, and only the
    /// first of them passes through the Python server's own gate (services/tools/compile_status.py,
    /// whose live_verdict this mirrors).
    /// </summary>
    /// <remarks>
    /// "clean" is only produced from a status that proves it. A status that is missing a field
    /// or reads badly is "unknown", never clean.
    /// </remarks>
    internal static class CompileGate
    {
        internal const string Errors = "errors";
        internal const string Clean = "clean";
        internal const string Compiling = "compiling";
        internal const string Pending = "pending";
        internal const string Stale = "stale";
        internal const string Unknown = "unknown";

        /// <summary>Replaced by tests; the status is read on the main thread at call time.</summary>
        internal static Func<JObject> StatusSource = () => CompileTracker.GetStatus(scanChanges: true);

        internal static JObject Verdict()
        {
            JObject status;
            try
            {
                status = StatusSource();
            }
            catch (Exception ex)
            {
                return Base(Unknown, $"compile status could not be read ({ex.Message}); compile result unknown", null);
            }
            return Judge(status);
        }

        internal static string Kind(JObject verdict) => verdict?["verdict"]?.ToString() ?? Unknown;

        internal static bool IsClean(JObject verdict) => Kind(verdict) == Clean;

        internal static JObject Judge(JObject status)
        {
            if (status == null)
            {
                return Base(Unknown, "compile status could not be read (no status); compile result unknown", null);
            }

            string bad = MalformedField(status);
            if (bad != null)
            {
                return Base(Unknown, $"compile status is malformed ({bad}); compile result unknown - call compile_status", status);
            }

            int epoch = status["epoch"].Value<int>();
            int finished = status["finished_epoch"].Value<int>();
            if (status["is_compiling"].Value<bool>() || epoch > finished)
            {
                return Base(Compiling, "compilation in progress - error list is not final", status);
            }
            if (status["is_updating"].Value<bool>())
            {
                return Base(Pending, "asset import in progress - a compile may follow", status);
            }

            int changed = status["scripts_changed_since_compile"]["count"].Value<int>();
            bool lastFailed = status["last_failed"].Value<bool>();
            bool failedNow = status["compilation_failed_now"].Value<bool>();
            if (changed > 0)
            {
                var stale = Base(
                    Stale,
                    $"{changed} script file(s) changed on disk since the last compile started - call refresh_unity to compile them",
                    status);
                if (lastFailed)
                {
                    stale["last_compile_errors"] = status["errors"] as JArray ?? new JArray();
                }
                return stale;
            }

            if (epoch == 0)
            {
                return failedNow
                    ? UntrackedErrors(status, "Unity reports failed script compilation from before compiles were tracked in this session")
                    : Base(Clean, "no compile this session and no scripts changed since the domain loaded", status);
            }

            if (lastFailed)
            {
                var errors = Base(Errors, null, status);
                errors["errors"] = status["errors"] as JArray ?? new JArray();
                errors["error_count"] = status["error_count"];
                errors["warning_count"] = status["warning_count"];
                return errors;
            }
            if (!status["reload_done_after_finish"].Value<bool>())
            {
                return Base(Pending, "compile finished without errors; domain reload not done yet", status);
            }
            if (failedNow)
            {
                // A compile only rebuilds the assemblies whose sources changed, so a clean epoch
                // can follow a failed one while the failed assembly is still broken.
                return UntrackedErrors(
                    status,
                    $"Unity reports failed script compilation although the last tracked compile (epoch {epoch}) had no errors: an assembly it did not rebuild still fails");
            }

            var clean = Base(Clean, null, status);
            clean["error_count"] = 0;
            clean["warning_count"] = status["warning_count"];
            return clean;
        }

        private static JObject UntrackedErrors(JObject status, string note)
        {
            var verdict = Base(Errors, note + "; call refresh_unity(compile='request') to list the compiler errors", status);
            verdict["errors"] = new JArray();
            verdict["error_count"] = null;
            return verdict;
        }

        private static readonly string[] Flags =
            { "is_compiling", "is_updating", "compilation_failed_now", "last_failed", "reload_done_after_finish" };

        /// <summary>
        /// The first field a verdict depends on that is missing or in the wrong shape. A missing
        /// value must not default to "nothing changed" or "not compiling": that default reads as clean.
        /// </summary>
        internal static string MalformedField(JObject status)
        {
            foreach (string key in new[] { "epoch", "finished_epoch" })
            {
                var value = status[key];
                if (value == null || value.Type != JTokenType.Integer || value.Value<long>() < 0)
                {
                    return $"{key}={value?.ToString(Formatting.None) ?? "missing"}";
                }
            }
            if (status["finished_epoch"].Value<long>() > status["epoch"].Value<long>())
            {
                return $"finished_epoch={status["finished_epoch"]} is ahead of epoch={status["epoch"]}";
            }

            var changed = status["scripts_changed_since_compile"] as JObject;
            var count = changed?["count"];
            if (count == null || count.Type != JTokenType.Integer || count.Value<long>() < 0)
            {
                return $"scripts_changed_since_compile={status["scripts_changed_since_compile"]?.ToString(Formatting.None) ?? "missing"}";
            }

            foreach (string key in Flags)
            {
                if (status[key] == null || status[key].Type != JTokenType.Boolean)
                {
                    return $"{key}={status[key]?.ToString(Formatting.None) ?? "missing"}";
                }
            }
            return null;
        }

        private static JObject Base(string verdict, string note, JObject status)
        {
            var result = new JObject { ["verdict"] = verdict };
            if (!string.IsNullOrEmpty(note))
            {
                result["note"] = note;
            }
            if (status != null)
            {
                result["epoch"] = status["epoch"];
                result["finished_epoch"] = status["finished_epoch"];
                if (status["scripts_changed_since_compile"] is JObject changed)
                {
                    result["scripts_changed_since_compile"] = changed;
                }
            }
            return result;
        }

        /// <summary>The answer to a run_tests start while the verdict is not clean.</summary>
        internal static ErrorResponse Refusal(JObject verdict)
        {
            switch (Kind(verdict))
            {
                case Errors:
                    return new ErrorResponse(
                        "compile",
                        "Scripts do not compile (data.compile lists the errors). Tests would run against the last good "
                        + "assemblies, and a test assembly that failed to compile would report 0 tests. "
                        + "Fix the errors, then run the tests again.",
                        new { reason = "compile_errors", compile = verdict });
                case Stale:
                    return new ErrorResponse(
                        "compile",
                        "Script files changed on disk since the last compile; tests would run the old code. "
                        + "Call refresh_unity(compile='request') and run the tests once its verdict is clean.",
                        new { reason = "scripts_changed", compile = verdict });
                case Compiling:
                case Pending:
                    return new ErrorResponse(
                        "busy",
                        "A compile, asset import or domain reload is in progress, so no test run was started. "
                        + "Retry once compile_status says clean.",
                        new { reason = "compiling", retry_after_ms = 500, compile = verdict });
                default:
                    return new ErrorResponse(
                        "busy",
                        "The compile status could not be trusted (data.compile.note says why), so it is not known whether "
                        + "scripts compile. No test run was started; retry, or call compile_status.",
                        new { reason = "compile_status_unknown", retry_after_ms = 1000, compile = verdict });
            }
        }

        /// <summary>Why a finished run's pass does not count. <paramref name="total"/> is null when the result is gone.</summary>
        internal static string RejectedRunMessage(int? total, JObject verdict)
        {
            string prefix = total == 0
                ? "The run found 0 tests. "
                : total.HasValue
                    ? $"The run reported a pass ({total} tests), but it does not count. "
                    : "The run reported a pass, but it does not count. ";
            string body;
            switch (Kind(verdict))
            {
                case Errors:
                    body = "Scripts do not compile now (data.compile lists the errors), so the run may have tested the last "
                        + "good assemblies, and a test assembly that failed to compile reports no tests. "
                        + "Fix the errors, then run the tests again.";
                    break;
                case Stale:
                    body = "Script files changed on disk since the last compile, so the run tested the old code. "
                        + "Call refresh_unity(compile='request') and run the tests again once its verdict is clean.";
                    break;
                case Compiling:
                case Pending:
                    body = "A compile, asset import or domain reload is in progress now, so the scripts may differ from the "
                        + "ones the run tested and their compile result is not final. Wait until compile_status says clean, "
                        + "then run the tests again.";
                    break;
                default:
                    body = "The compile status could not be read or trusted (data.compile.note says why), so it is not known "
                        + "whether the run tested compiling, current scripts. Call compile_status and run the tests again "
                        + "once it is clean.";
                    break;
            }
            return prefix + body + " data.result keeps the run's summary.";
        }
    }
}
