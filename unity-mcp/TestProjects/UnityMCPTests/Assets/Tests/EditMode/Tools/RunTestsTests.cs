using System;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnityTests.Editor.Services;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// Tests for RunTests tool functionality.
    /// Note: We cannot easily test the full HandleCommand because it would create
    /// recursive test runner calls.
    /// </summary>
    public class RunTestsTests
    {
        [Test]
        public void HandleCommand_WhenTestsAlreadyRunning_ReturnsBusyError()
        {
            // Arrange: Force TestJobManager into a "busy" state without starting a real run.
            // We do this via reflection because TestJobManager is internal.
            var asm = typeof(MCPForUnity.Editor.Services.MCPServiceLocator).Assembly;
            var testJobManagerType = asm.GetType("MCPForUnity.Editor.Services.TestJobManager");
            Assert.NotNull(testJobManagerType, "Could not locate TestJobManager type via reflection");

            var currentJobIdField = testJobManagerType.GetField("_currentJobId", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.NotNull(currentJobIdField, "Could not locate TestJobManager._currentJobId field");

            var originalJobId = currentJobIdField.GetValue(null) as string;
            currentJobIdField.SetValue(null, "busy-test-job-id");

            try
            {
                var resultObj = MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject()).GetAwaiter().GetResult();

                Assert.IsInstanceOf<ErrorResponse>(resultObj);
                var err = (ErrorResponse)resultObj;
                Assert.AreEqual(false, err.Success);
                Assert.AreEqual("tests_running", err.Code);

                var data = err.Data != null ? JObject.FromObject(err.Data) : null;
                Assert.NotNull(data, "Expected data payload on tests_running error");
                Assert.AreEqual("tests_running", data["reason"]?.ToString());
                Assert.GreaterOrEqual(data["retry_after_ms"]?.Value<int>() ?? 0, 500);
            }
            finally
            {
                currentJobIdField.SetValue(null, originalJobId);
            }
        }

        // A start with a clean verdict would launch a real run from inside this run, so only the
        // refusals are exercised here; the compile-state matrix is in CompileGateTests. The job
        // slot is emptied for the call because this fixture may itself run as an MCP test job.
        private static JObject StartWith(JObject status)
        {
            var currentJobIdField = typeof(TestJobManager).GetField("_currentJobId", BindingFlags.NonPublic | BindingFlags.Static);
            var originalJobId = currentJobIdField.GetValue(null) as string;
            var originalSource = CompileGate.StatusSource;
            currentJobIdField.SetValue(null, null);
            CompileGate.StatusSource = () => status;
            try
            {
                var result = MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject()).GetAwaiter().GetResult();
                Assert.IsInstanceOf<ErrorResponse>(result);
                Assert.IsFalse(TestJobManager.HasRunningJob, "a refused start must not take the job slot");
                return JObject.FromObject(result);
            }
            finally
            {
                CompileGate.StatusSource = originalSource;
                currentJobIdField.SetValue(null, originalJobId);
            }
        }

        [Test]
        public void HandleCommand_WhenScriptsDoNotCompile_RefusesWithTheCompileVerdict()
        {
            var json = StartWith(CompileGateTests.Status(failedNow: true, lastFailed: true));

            Assert.AreEqual(false, json.Value<bool>("success"));
            Assert.AreEqual("compile", json.Value<string>("error"));
            StringAssert.Contains("do not compile", json.Value<string>("message"));
            Assert.AreEqual("errors", json["data"]["compile"].Value<string>("verdict"));
            Assert.AreEqual("CS0246", json["data"]["compile"]["errors"][0].Value<string>("code"));
        }

        [Test]
        public void HandleCommand_WhenScriptsChangedOnDisk_RefusesAsStale()
        {
            var json = StartWith(CompileGateTests.Status(changed: 2));

            Assert.AreEqual("compile", json.Value<string>("error"));
            Assert.AreEqual("stale", json["data"]["compile"].Value<string>("verdict"));
            StringAssert.Contains("refresh_unity", json.Value<string>("message"));
        }

        [TestCase(true, false, true)]
        [TestCase(false, true, true)]
        [TestCase(false, false, false)]
        public void HandleCommand_WhileCompilingOrReloadPending_IsBusy(bool compiling, bool updating, bool reloadDone)
        {
            var json = StartWith(CompileGateTests.Status(compiling: compiling, updating: updating, reloadDone: reloadDone));

            Assert.AreEqual("busy", json.Value<string>("error"));
            Assert.AreEqual("compiling", json["data"].Value<string>("reason"));
            Assert.Greater(json["data"].Value<int>("retry_after_ms"), 0);
        }

        [Test]
        public void HandleCommand_WhenTheCompileStatusIsMalformed_RefusesInsteadOfStarting()
        {
            var status = CompileGateTests.Status();
            status.Remove("epoch");

            var json = StartWith(status);

            Assert.AreEqual("busy", json.Value<string>("error"));
            Assert.AreEqual("compile_status_unknown", json["data"].Value<string>("reason"));
            Assert.AreEqual("unknown", json["data"]["compile"].Value<string>("verdict"));
        }

        [Test]
        public void HandleCommand_WhenTestsAlreadyRunning_ReportsThatBeforeTheCompileState()
        {
            var currentJobIdField = typeof(TestJobManager).GetField("_currentJobId", BindingFlags.NonPublic | BindingFlags.Static);
            var originalJobId = currentJobIdField.GetValue(null) as string;
            var originalSource = CompileGate.StatusSource;
            currentJobIdField.SetValue(null, "busy-test-job-id");
            CompileGate.StatusSource = () => CompileGateTests.Status(failedNow: true, lastFailed: true);
            try
            {
                var result = MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject()).GetAwaiter().GetResult();

                Assert.AreEqual("tests_running", JObject.FromObject(result).Value<string>("error"));
            }
            finally
            {
                CompileGate.StatusSource = originalSource;
                currentJobIdField.SetValue(null, originalJobId);
            }
        }

        [Test]
        public void HandleCommand_WithInvalidMode_ReturnsError()
        {
            var resultObj = MCPForUnity.Editor.Tools.RunTests.HandleCommand(new JObject
            {
                ["mode"] = "NotARealMode"
            }).GetAwaiter().GetResult();

            Assert.IsInstanceOf<ErrorResponse>(resultObj);
            var err = (ErrorResponse)resultObj;
            Assert.AreEqual(false, err.Success);
            Assert.IsTrue(err.Error.Contains("Unknown test mode", StringComparison.OrdinalIgnoreCase));
        }
    }
}
