using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using NUnit.Framework;
using MCPForUnity.Editor.Services;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// clear_stuck must release only a stuck or orphaned job, never one that is still progressing.
    /// Jobs are inserted through the private state because StartJob triggers a real test run.
    /// </summary>
    public class TestJobManagerClearStuckTests
    {
        private const string JobId = "test-clear-stuck-job";

        private FieldInfo _jobsField;
        private FieldInfo _currentJobIdField;
        private MethodInfo _persistMethod;
        private string _originalJobId;

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
        }

        [TearDown]
        public void TearDown()
        {
            _currentJobIdField.SetValue(null, _originalJobId);
            (_jobsField.GetValue(null) as IDictionary)?.Remove(JobId);
            _persistMethod.Invoke(null, new object[] { true });
        }

        private TestJob InsertRunningJob(long quietMs, int? totalTests, long? currentTestAgeMs = null, long initTimeoutMs = 0)
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var job = new TestJob
            {
                JobId = JobId,
                Status = TestJobStatus.Running,
                Mode = "EditMode",
                StartedUnixMs = now - Math.Max(quietMs, currentTestAgeMs ?? 0) - 1000,
                LastUpdateUnixMs = now - quietMs,
                TotalTests = totalTests,
                CurrentTestFullName = currentTestAgeMs.HasValue ? "Some.Test" : null,
                CurrentTestStartedUnixMs = currentTestAgeMs.HasValue ? now - currentTestAgeMs.Value : (long?)null,
                FailuresSoFar = new List<TestJobFailure>(),
                InitTimeoutMs = initTimeoutMs
            };
            ((IDictionary)_jobsField.GetValue(null))[JobId] = job;
            _currentJobIdField.SetValue(null, JobId);
            return job;
        }

        [Test]
        public void ProgressingJob_IsLeftRunning()
        {
            var job = InsertRunningJob(quietMs: 5_000, totalTests: 3, currentTestAgeMs: 5_000);

            var outcome = TestJobManager.ClearStuckJob(out string jobId, out long quietMs);

            Assert.AreEqual(TestJobManager.ClearStuckOutcome.StillProgressing, outcome);
            Assert.AreEqual(JobId, jobId);
            Assert.GreaterOrEqual(quietMs, 5_000);
            Assert.AreEqual(TestJobStatus.Running, job.Status);
            Assert.AreEqual(JobId, TestJobManager.CurrentJobId);
        }

        [Test]
        public void InitializingJob_WithinItsInitTimeout_IsLeftRunning()
        {
            var job = InsertRunningJob(quietMs: 90_000, totalTests: null, initTimeoutMs: 120_000);

            var outcome = TestJobManager.ClearStuckJob(out _, out _);

            Assert.AreEqual(TestJobManager.ClearStuckOutcome.StillProgressing, outcome);
            Assert.AreEqual(TestJobStatus.Running, job.Status);
            Assert.AreEqual(JobId, TestJobManager.CurrentJobId);
        }

        [Test]
        public void JobWithoutProgress_IsCleared()
        {
            var job = InsertRunningJob(quietMs: 90_000, totalTests: 3);

            var outcome = TestJobManager.ClearStuckJob(out _, out _);

            Assert.AreEqual(TestJobManager.ClearStuckOutcome.Cleared, outcome);
            Assert.AreEqual(TestJobStatus.Failed, job.Status);
            Assert.IsNull(TestJobManager.CurrentJobId);
        }

        [Test]
        public void InitializingJob_PastItsInitTimeout_IsCleared()
        {
            var job = InsertRunningJob(quietMs: 130_000, totalTests: null, initTimeoutMs: 120_000);

            var outcome = TestJobManager.ClearStuckJob(out _, out _);

            Assert.AreEqual(TestJobManager.ClearStuckOutcome.Cleared, outcome);
            Assert.AreEqual(TestJobStatus.Failed, job.Status);
            Assert.IsNull(TestJobManager.CurrentJobId);
        }

        [Test]
        public void TestRunningPastTheStuckThreshold_IsCleared()
        {
            var job = InsertRunningJob(quietMs: 1_000, totalTests: 3, currentTestAgeMs: 70_000);

            var outcome = TestJobManager.ClearStuckJob(out _, out _);

            Assert.AreEqual(TestJobManager.ClearStuckOutcome.Cleared, outcome);
            Assert.AreEqual(TestJobStatus.Failed, job.Status);
        }

        [Test]
        public void NoCurrentJob_ReportsNoJob()
        {
            _currentJobIdField.SetValue(null, null);

            Assert.AreEqual(TestJobManager.ClearStuckOutcome.NoJob, TestJobManager.ClearStuckJob(out _, out _));
        }
    }
}
