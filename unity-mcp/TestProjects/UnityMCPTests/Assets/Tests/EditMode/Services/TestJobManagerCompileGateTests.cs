using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// A finished job's pass counts only while the compile verdict read at poll time is clean.
    /// Jobs are inserted through the private state because StartJob triggers a real test run.
    /// </summary>
    public class TestJobManagerCompileGateTests
    {
        private const string JobId = "test-compile-gate-job";

        private FieldInfo _jobsField;
        private FieldInfo _currentJobIdField;
        private MethodInfo _persistMethod;
        private string _originalJobId;
        private Func<JObject> _originalSource;

        [SetUp]
        public void SetUp()
        {
            var managerType = typeof(TestJobManager);
            _jobsField = managerType.GetField("Jobs", BindingFlags.NonPublic | BindingFlags.Static);
            _currentJobIdField = managerType.GetField("_currentJobId", BindingFlags.NonPublic | BindingFlags.Static);
            _persistMethod = managerType.GetMethod("PersistToSessionState", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.NotNull(_jobsField);
            Assert.NotNull(_currentJobIdField);
            Assert.NotNull(_persistMethod);
            _originalJobId = _currentJobIdField.GetValue(null) as string;
            _originalSource = CompileGate.StatusSource;
        }

        [TearDown]
        public void TearDown()
        {
            CompileGate.StatusSource = _originalSource;
            _currentJobIdField.SetValue(null, _originalJobId);
            (_jobsField.GetValue(null) as IDictionary)?.Remove(JobId);
            _persistMethod.Invoke(null, new object[] { true });
        }

        private static TestRunResult PassedRun(int total)
        {
            var summary = new TestRunSummary(total, total, 0, 0, 0.1, "Passed");
            return new TestRunResult(summary, new List<TestRunTestResult>());
        }

        private TestJob InsertFinishedJob(TestJobStatus status, TestRunResult result, string error = null)
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var job = new TestJob
            {
                JobId = JobId,
                Status = status,
                Mode = "EditMode",
                StartedUnixMs = now - 2_000,
                FinishedUnixMs = now - 1_000,
                LastUpdateUnixMs = now - 1_000,
                TotalTests = result?.Total,
                CompletedTests = result?.Total ?? 0,
                FailuresSoFar = new List<TestJobFailure>(),
                Error = error,
                Result = result
            };
            ((IDictionary)_jobsField.GetValue(null))[JobId] = job;
            return job;
        }

        private static JObject Poll()
        {
            return JObject.FromObject(GetTestJob.HandleCommand(new JObject { ["job_id"] = JobId }));
        }

        [TestCase(1)]
        [TestCase(4)]
        [TestCase(0)]
        public void PassedRun_WhileCompileFailed_IsReportedAsCompileFailureKeepingItsResult(int total)
        {
            InsertFinishedJob(TestJobStatus.Succeeded, PassedRun(total));
            CompileGate.StatusSource = () => CompileGateTests.Status(failedNow: true, lastFailed: true);

            var json = Poll();

            Assert.AreEqual(false, json.Value<bool>("success"));
            Assert.AreEqual("compile", json.Value<string>("error"));
            StringAssert.StartsWith(
                total == 0 ? "The run found 0 tests." : $"The run reported a pass ({total} tests)",
                json.Value<string>("message"));
            var data = json["data"];
            Assert.AreEqual("failed", data.Value<string>("status"));
            Assert.AreEqual("compile", data.Value<string>("error"));
            Assert.AreEqual(total, data["result"]["summary"].Value<int>("total"));
            Assert.AreEqual(total, data["result"]["summary"].Value<int>("passed"));
            Assert.AreEqual("errors", data["compile"].Value<string>("verdict"));
        }

        [TestCase("compiling")]
        [TestCase("pending-import")]
        [TestCase("pending-reload")]
        [TestCase("stale")]
        public void PassedRun_WhileCompileIsNotSettled_IsNotGreen(string state)
        {
            InsertFinishedJob(TestJobStatus.Succeeded, PassedRun(4));
            CompileGate.StatusSource = () =>
            {
                switch (state)
                {
                    case "compiling": return CompileGateTests.Status(compiling: true);
                    case "pending-import": return CompileGateTests.Status(updating: true);
                    case "pending-reload": return CompileGateTests.Status(reloadDone: false);
                    default: return CompileGateTests.Status(changed: 1);
                }
            };

            var json = Poll();

            Assert.AreEqual(false, json.Value<bool>("success"));
            Assert.AreEqual("compile", json.Value<string>("error"));
            Assert.AreEqual("failed", json["data"].Value<string>("status"));
            Assert.AreEqual(4, json["data"]["result"]["summary"].Value<int>("passed"));
        }

        [Test]
        public void PassedRun_WhenTheCompileStatusCannotBeTrusted_IsNotGreen()
        {
            InsertFinishedJob(TestJobStatus.Succeeded, PassedRun(4));
            CompileGate.StatusSource = () => throw new InvalidOperationException("status read failed");

            var json = Poll();

            Assert.AreEqual(false, json.Value<bool>("success"));
            Assert.AreEqual("compile", json.Value<string>("error"));
            Assert.AreEqual("unknown", json["data"]["compile"].Value<string>("verdict"));
        }

        [Test]
        public void PassedRun_WhileCompileIsClean_StaysGreenAndCarriesTheVerdict()
        {
            InsertFinishedJob(TestJobStatus.Succeeded, PassedRun(4));
            CompileGate.StatusSource = () => CompileGateTests.Status();

            var json = Poll();

            Assert.AreEqual(true, json.Value<bool>("success"));
            var data = json["data"];
            Assert.AreEqual("succeeded", data.Value<string>("status"));
            Assert.AreEqual(4, data["result"]["summary"].Value<int>("total"));
            Assert.AreEqual("clean", data["compile"].Value<string>("verdict"));
        }

        [Test]
        public void FailedRun_KeepsItsOwnFailureAndCarriesTheVerdict()
        {
            var summary = new TestRunSummary(4, 3, 1, 0, 0.1, "Failed");
            InsertFinishedJob(TestJobStatus.Failed, new TestRunResult(summary, new List<TestRunTestResult>()));
            CompileGate.StatusSource = () => CompileGateTests.Status(failedNow: true, lastFailed: true);

            var json = Poll();

            Assert.AreEqual(true, json.Value<bool>("success"));
            Assert.AreEqual("failed", json["data"].Value<string>("status"));
            Assert.AreNotEqual("compile", json["data"].Value<string>("error"));
            Assert.AreEqual("errors", json["data"]["compile"].Value<string>("verdict"));
        }

        [Test]
        public void PassedRun_WhoseResultIsGone_IsStillRejected()
        {
            // Results are not persisted across a domain reload.
            InsertFinishedJob(TestJobStatus.Succeeded, null);
            CompileGate.StatusSource = () => CompileGateTests.Status(compiling: true);

            var json = Poll();

            Assert.AreEqual("compile", json.Value<string>("error"));
            Assert.AreEqual("failed", json["data"].Value<string>("status"));
            Assert.AreEqual(JTokenType.Null, json["data"]["result"].Type);
        }

        [Test]
        public void RunningJob_IsNotJudgedAndDoesNotReadTheCompileStatus()
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var job = new TestJob
            {
                JobId = JobId,
                Status = TestJobStatus.Running,
                Mode = "EditMode",
                StartedUnixMs = now,
                LastUpdateUnixMs = now,
                TotalTests = 3,
                FailuresSoFar = new List<TestJobFailure>()
            };
            ((IDictionary)_jobsField.GetValue(null))[JobId] = job;
            _currentJobIdField.SetValue(null, JobId);
            bool read = false;
            CompileGate.StatusSource = () =>
            {
                read = true;
                return CompileGateTests.Status();
            };

            var json = Poll();

            Assert.IsFalse(read, "a running job must not read the compile status");
            Assert.AreEqual(true, json.Value<bool>("success"));
            Assert.AreEqual("running", json["data"].Value<string>("status"));
            Assert.AreEqual(JTokenType.Null, json["data"]["compile"].Type);
        }

        [Test]
        public void RejectedByCompile_OnlyRejectsAPassedJob()
        {
            var unclean = CompileGate.Judge(CompileGateTests.Status(compiling: true));
            var clean = CompileGate.Judge(CompileGateTests.Status());

            var passed = new TestJob { Status = TestJobStatus.Succeeded };
            var failed = new TestJob { Status = TestJobStatus.Failed };
            var running = new TestJob { Status = TestJobStatus.Running };

            Assert.IsTrue(TestJobManager.RejectedByCompile(passed, unclean));
            Assert.IsFalse(TestJobManager.RejectedByCompile(passed, clean));
            Assert.IsFalse(TestJobManager.RejectedByCompile(passed, null));
            Assert.IsFalse(TestJobManager.RejectedByCompile(failed, unclean));
            Assert.IsFalse(TestJobManager.RejectedByCompile(running, unclean));
            Assert.IsFalse(TestJobManager.RejectedByCompile(null, unclean));
        }
    }
}
