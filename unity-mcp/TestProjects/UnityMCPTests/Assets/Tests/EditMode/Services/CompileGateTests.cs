using System;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// The verdict is a pure function of the tracker's status, so these feed it hand-built
    /// statuses; the shapes mirror the Python server's tests of live_verdict.
    /// </summary>
    [TestFixture]
    public class CompileGateTests
    {
        private Func<JObject> _originalSource;

        [SetUp]
        public void SetUp() => _originalSource = CompileGate.StatusSource;

        [TearDown]
        public void TearDown() => CompileGate.StatusSource = _originalSource;

        internal static JObject Status(
            bool compiling = false, bool updating = false, bool failedNow = false, bool lastFailed = false,
            int epoch = 3, int finishedEpoch = 3, bool reloadDone = true, int changed = 0)
        {
            return new JObject
            {
                ["is_compiling"] = compiling,
                ["is_updating"] = updating,
                ["compilation_failed_now"] = failedNow,
                ["epoch"] = epoch,
                ["finished_epoch"] = finishedEpoch,
                ["last_failed"] = lastFailed,
                ["reload_done_after_finish"] = reloadDone,
                ["error_count"] = lastFailed ? 1 : 0,
                ["warning_count"] = 0,
                ["errors"] = lastFailed
                    ? new JArray(new JObject { ["code"] = "CS0246", ["file"] = "Assets/Tests/ProbeTests.cs", ["line"] = 4 })
                    : new JArray(),
                ["scripts_changed_since_compile"] = new JObject { ["count"] = changed, ["paths"] = new JArray() },
            };
        }

        [Test]
        public void Judge_SettledCompileWithNoChanges_IsClean()
        {
            var verdict = CompileGate.Judge(Status());

            Assert.AreEqual("clean", CompileGate.Kind(verdict));
            Assert.IsTrue(CompileGate.IsClean(verdict));
            Assert.AreEqual(0, verdict.Value<int>("error_count"));
        }

        [Test]
        public void Judge_FailedCompile_IsErrorsWithTheCompilerMessages()
        {
            var verdict = CompileGate.Judge(Status(failedNow: true, lastFailed: true));

            Assert.AreEqual("errors", CompileGate.Kind(verdict));
            Assert.AreEqual("CS0246", verdict["errors"][0].Value<string>("code"));
            Assert.AreEqual(1, verdict.Value<int>("error_count"));
        }

        [Test]
        public void Judge_ScriptsChangedOnDisk_IsStale()
        {
            var verdict = CompileGate.Judge(Status(changed: 2));

            Assert.AreEqual("stale", CompileGate.Kind(verdict));
            Assert.AreEqual(2, verdict["scripts_changed_since_compile"].Value<int>("count"));
            Assert.IsNull(verdict["last_compile_errors"]);
        }

        [Test]
        public void Judge_StaleAfterAFailedCompile_KeepsThoseErrors()
        {
            var verdict = CompileGate.Judge(Status(changed: 1, lastFailed: true, failedNow: true));

            Assert.AreEqual("stale", CompileGate.Kind(verdict));
            Assert.AreEqual("CS0246", verdict["last_compile_errors"][0].Value<string>("code"));
        }

        [Test]
        public void Judge_CompileInProgress_IsCompiling()
        {
            Assert.AreEqual("compiling", CompileGate.Kind(CompileGate.Judge(Status(compiling: true))));
            // An epoch that started and has not finished is compiling even if isCompiling has dropped.
            Assert.AreEqual("compiling", CompileGate.Kind(CompileGate.Judge(Status(epoch: 4, finishedEpoch: 3))));
        }

        [Test]
        public void Judge_ImportOrReloadStillPending_IsPending()
        {
            Assert.AreEqual("pending", CompileGate.Kind(CompileGate.Judge(Status(updating: true))));
            Assert.AreEqual("pending", CompileGate.Kind(CompileGate.Judge(Status(reloadDone: false))));
        }

        [Test]
        public void Judge_CompilingWinsOverStaleAndPending()
        {
            var verdict = CompileGate.Judge(Status(compiling: true, updating: true, changed: 3, reloadDone: false));

            Assert.AreEqual("compiling", CompileGate.Kind(verdict));
        }

        [Test]
        public void Judge_NoCompileThisSession_IsCleanUnlessUnityReportsFailure()
        {
            Assert.AreEqual("clean", CompileGate.Kind(CompileGate.Judge(Status(epoch: 0, finishedEpoch: 0, reloadDone: false))));

            var failed = CompileGate.Judge(Status(epoch: 0, finishedEpoch: 0, failedNow: true, reloadDone: false));
            Assert.AreEqual("errors", CompileGate.Kind(failed));
            Assert.AreEqual(0, ((JArray)failed["errors"]).Count);
            Assert.AreEqual(JTokenType.Null, failed["error_count"].Type);
        }

        [Test]
        public void Judge_CleanEpochWhileUnityStillReportsFailure_IsErrors()
        {
            // A compile only rebuilds the assemblies whose sources changed.
            var verdict = CompileGate.Judge(Status(failedNow: true));

            Assert.AreEqual("errors", CompileGate.Kind(verdict));
            StringAssert.Contains("did not rebuild", verdict.Value<string>("note"));
        }

        [Test]
        public void Judge_MissingStatus_IsUnknown()
        {
            Assert.AreEqual("unknown", CompileGate.Kind(CompileGate.Judge(null)));
        }

        private static JObject Without(JObject status, string key)
        {
            status.Remove(key);
            return status;
        }

        private static JObject With(JObject status, string key, JToken value)
        {
            status[key] = value;
            return status;
        }

        [Test]
        public void Judge_MalformedStatus_IsUnknownNeverClean()
        {
            var malformed = new[]
            {
                Without(Status(), "epoch"),
                Without(Status(), "finished_epoch"),
                With(Status(), "epoch", "3"),
                With(Status(), "epoch", -1),
                With(Status(), "finished_epoch", 9),
                With(Status(), "is_compiling", JValue.CreateNull()),
                With(Status(), "last_failed", "false"),
                Without(Status(), "reload_done_after_finish"),
                Without(Status(), "scripts_changed_since_compile"),
                With(Status(), "scripts_changed_since_compile", JValue.CreateNull()),
                With(Status(), "scripts_changed_since_compile", new JObject()),
                With(Status(), "scripts_changed_since_compile", new JObject { ["count"] = "0" }),
                With(Status(), "scripts_changed_since_compile", new JObject { ["count"] = -1 }),
            };

            foreach (var status in malformed)
            {
                var verdict = CompileGate.Judge(status);
                Assert.AreEqual("unknown", CompileGate.Kind(verdict), status.ToString());
                StringAssert.Contains("malformed", verdict.Value<string>("note"));
            }
        }

        [Test]
        public void Verdict_ReadsTheInjectedStatusSource()
        {
            CompileGate.StatusSource = () => Status(changed: 1);

            Assert.AreEqual("stale", CompileGate.Kind(CompileGate.Verdict()));
        }

        [Test]
        public void Verdict_WhenTheStatusCannotBeRead_IsUnknown()
        {
            CompileGate.StatusSource = () => throw new InvalidOperationException("boom");

            var verdict = CompileGate.Verdict();

            Assert.AreEqual("unknown", CompileGate.Kind(verdict));
            StringAssert.Contains("boom", verdict.Value<string>("note"));
        }

        [Test]
        public void Refusal_Errors_NamesTheCompileStateAndCarriesTheVerdict()
        {
            var verdict = CompileGate.Judge(Status(failedNow: true, lastFailed: true));

            var json = JObject.FromObject(CompileGate.Refusal(verdict));

            Assert.AreEqual(false, json.Value<bool>("success"));
            Assert.AreEqual("compile", json.Value<string>("error"));
            StringAssert.Contains("do not compile", json.Value<string>("message"));
            Assert.AreEqual("compile_errors", json["data"].Value<string>("reason"));
            Assert.AreEqual("errors", json["data"]["compile"].Value<string>("verdict"));
            Assert.AreEqual("CS0246", json["data"]["compile"]["errors"][0].Value<string>("code"));
        }

        [Test]
        public void Refusal_Stale_PointsAtRefresh()
        {
            var json = JObject.FromObject(CompileGate.Refusal(CompileGate.Judge(Status(changed: 2))));

            Assert.AreEqual("compile", json.Value<string>("error"));
            StringAssert.Contains("refresh_unity", json.Value<string>("message"));
            Assert.AreEqual("stale", json["data"]["compile"].Value<string>("verdict"));
        }

        [TestCase(true, false, true)]
        [TestCase(false, true, true)]
        [TestCase(false, false, false)]
        public void Refusal_CompilingOrPending_IsBusyWithARetryDelay(bool compiling, bool updating, bool reloadDone)
        {
            var verdict = CompileGate.Judge(Status(compiling: compiling, updating: updating, reloadDone: reloadDone));

            var json = JObject.FromObject(CompileGate.Refusal(verdict));

            Assert.AreEqual("busy", json.Value<string>("error"));
            // The Python transport reads data.reason before it looks at the message text, so a
            // message that says "reload" is not taken for the editor answering "reloading".
            Assert.AreEqual("compiling", json["data"].Value<string>("reason"));
            Assert.Greater(json["data"].Value<int>("retry_after_ms"), 0);
        }

        [Test]
        public void Refusal_Unknown_IsBusyAndSaysTheStatusWasNotTrusted()
        {
            var json = JObject.FromObject(CompileGate.Refusal(CompileGate.Judge(null)));

            Assert.AreEqual("busy", json.Value<string>("error"));
            Assert.AreEqual("compile_status_unknown", json["data"].Value<string>("reason"));
            Assert.AreEqual("unknown", json["data"]["compile"].Value<string>("verdict"));
        }

        [Test]
        public void RejectedRunMessage_SaysWhatTheRunFound()
        {
            var errors = CompileGate.Judge(Status(failedNow: true, lastFailed: true));

            StringAssert.StartsWith("The run found 0 tests.", CompileGate.RejectedRunMessage(0, errors));
            StringAssert.StartsWith("The run reported a pass (4 tests)", CompileGate.RejectedRunMessage(4, errors));
            StringAssert.StartsWith("The run reported a pass, but", CompileGate.RejectedRunMessage(null, errors));
            StringAssert.Contains("keeps the run's summary", CompileGate.RejectedRunMessage(4, errors));
        }
    }
}
