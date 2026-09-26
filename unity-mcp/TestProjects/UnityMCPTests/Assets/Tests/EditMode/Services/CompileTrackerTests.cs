using System;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.Compilation;
using MCPForUnity.Editor.Services;

namespace MCPForUnityTests.Editor.Services
{
    // Read-only against the live tracker: the epoch counters belong to the running
    // editor session, and a test that moved them would make every MCP client in that
    // session wait for a compile that never comes.
    [TestFixture]
    public class CompileTrackerTests
    {
        [Test]
        public void ToJson_ExtractsCodeAndNormalizesPath()
        {
            var message = new CompilerMessage
            {
                message = @"Assets\Scripts\Probe.cs(1,52): error CS0029: Cannot implicitly convert type 'string' to 'int'",
                file = @"Assets\Scripts\Probe.cs",
                line = 1,
                column = 52,
                type = CompilerMessageType.Error,
            };

            var json = CompileTracker.ToJson(message, "Library/ScriptAssemblies/Assembly-CSharp.dll");

            Assert.AreEqual("CS0029", json.Value<string>("code"));
            Assert.AreEqual("Assets/Scripts/Probe.cs", json.Value<string>("file"));
            Assert.AreEqual(1, json.Value<int>("line"));
            Assert.AreEqual(52, json.Value<int>("column"));
            Assert.AreEqual("Assembly-CSharp", json.Value<string>("assembly"));
        }

        [Test]
        public void ToJson_WithoutCode_LeavesCodeNull()
        {
            var json = CompileTracker.ToJson(new CompilerMessage { message = "Compilation failed", type = CompilerMessageType.Error }, null);

            Assert.IsNull(json.Value<string>("code"));
            Assert.IsNull(json.Value<string>("assembly"));
        }

        [TestCase(".git", true)]
        [TestCase("Samples~", true)]
        [TestCase("", true)]
        [TestCase("Scripts", false)]
        public void IsIgnoredFolder_MatchesUnityImportRules(string name, bool ignored)
        {
            Assert.AreEqual(ignored, CompileTracker.IsIgnoredFolder(name));
        }

        [TestCase("Assets/A.cs", true)]
        [TestCase("Assets/A.CS", true)]
        [TestCase("Assets/Game.asmdef", true)]
        [TestCase("Assets/Ref.asmref", true)]
        [TestCase("Assets/A.cs.meta", false)]
        [TestCase("Assets/Readme.txt", false)]
        public void IsScriptFile_OnlyCompilerInputs(string path, bool expected)
        {
            Assert.AreEqual(expected, CompileTracker.IsScriptFile(path));
        }

        [Test]
        public void ScanChangedScripts_CountsOnlyFilesNewerThanTheCutoff()
        {
            var everything = CompileTracker.ScanChangedScripts(0);
            Assert.Greater(everything.Value<int>("count"), 0, "this project has scripts under Assets/");
            Assert.LessOrEqual(((JArray)everything["paths"]).Count, 5);
            StringAssert.StartsWith("Assets/", everything["paths"][0].ToString());

            long future = DateTimeOffset.UtcNow.AddDays(1).ToUnixTimeMilliseconds();
            Assert.AreEqual(0, CompileTracker.ScanChangedScripts(future).Value<int>("count"));
        }

        [Test]
        public void GetStatus_ReportsEveryField()
        {
            var status = CompileTracker.GetStatus(scanChanges: true);

            foreach (var key in new[]
                     {
                         "is_compiling", "is_updating", "compilation_failed_now", "epoch", "finished_epoch",
                         "last_failed", "error_count", "warning_count", "errors", "warnings",
                         "reloaded_at", "reload_done_after_finish", "scripts_changed_since_compile",
                     })
            {
                Assert.IsTrue(status.ContainsKey(key), key);
            }
            Assert.GreaterOrEqual(status.Value<int>("epoch"), status.Value<int>("finished_epoch"));
            Assert.IsNotNull(status.Value<long?>("reloaded_at"), "the static ctor records the domain load");
            Assert.IsFalse(CompileTracker.GetStatus(scanChanges: false).ContainsKey("scripts_changed_since_compile"));
        }
    }
}
