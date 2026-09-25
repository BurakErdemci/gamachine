using System.Diagnostics;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools.Playtest;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.Playtest
{
    public class PlaytestRunnerTests
    {
        private string _tmp;
        private string _project;
        private string _link;

        // A folder ending in ~ is under Assets for the path check but never imported by Unity.
        private static string BudgetDir => Path.Combine(Application.dataPath, "PlaytestBudgetTests~");

        [SetUp]
        public void SetUp()
        {
            _tmp = Path.Combine(Path.GetTempPath(), "PlaytestRunnerTests_" + Path.GetRandomFileName());
            _project = Path.Combine(_tmp, "project");
            Directory.CreateDirectory(Path.Combine(_project, "Assets", "Playtests"));
            Directory.CreateDirectory(Path.Combine(_tmp, "outside"));
            File.WriteAllText(Path.Combine(_tmp, "outside", "case.playtest.json"), "{\"steps\":[{\"frames\":1}]}");
        }

        [TearDown]
        public void TearDown()
        {
            // Remove the link itself first so the recursive delete below can never walk into its target.
            if (_link != null && (Directory.Exists(_link) || File.Exists(_link)))
            {
                if (Path.DirectorySeparatorChar == '\\') Directory.Delete(_link);
                else File.Delete(_link);
            }
            if (Directory.Exists(_tmp)) Directory.Delete(_tmp, true);
            if (Directory.Exists(BudgetDir)) Directory.Delete(BudgetDir, true);
        }

        private static string WriteScenarios(string set, int count, int steps, int framesPerStep, double perfSeconds = 0)
        {
            string dir = Path.Combine(BudgetDir, set);
            Directory.CreateDirectory(dir);
            for (int i = 0; i < count; i++)
            {
                var sc = new JObject
                {
                    ["steps"] = new JArray(Enumerable.Range(0, steps).Select(_ => new JObject { ["frames"] = framesPerStep })),
                };
                if (perfSeconds > 0) sc["perf"] = new JObject { ["seconds"] = perfSeconds };
                File.WriteAllText(Path.Combine(dir, $"s{i:000}.playtest.json"), sc.ToString());
            }
            return $"Assets/PlaytestBudgetTests~/{set}";
        }

        private static string StartError(string path)
        {
            var run = JObject.FromObject(RunPlaytestTool.HandleCommand(new JObject { ["path"] = path }));
            Assert.IsFalse((bool)run["success"], $"a job started for {path}");
            return (string)run["error"];
        }

        [Test]
        public void RunPlaytest_RefusesJobsOverTheWorkCaps()
        {
            if (EditorApplication.isPlaying || PlaytestSession.Busy || PlaytestRunner.Running)
                Assert.Ignore("needs an idle editor");

            StringAssert.Contains($"at most {PlaytestRunner.MaxFramesPerScenario}",
                StartError(WriteScenarios("scenario", 1, 11, 3600)));
            StringAssert.Contains($"at most {PlaytestRunner.MaxFramesPerJob}",
                StartError(WriteScenarios("job", 4, 9, 3400)));
            StringAssert.Contains($"at most {PlaytestRunner.MaxScenariosPerJob}",
                StartError(WriteScenarios("count", PlaytestRunner.MaxScenariosPerJob + 1, 1, 1)));
            StringAssert.Contains("perf sampling",
                StartError(WriteScenarios("perf", 6, 1, 1, PlaytestRunner.MaxPerfSecondsPerScenario)));
        }

        [Test]
        public void StepTimeout_IsCappedAndNeverOutlivesTheJobBudget()
        {
            double cap = PlaytestRunner.MaxStepTimeoutSeconds;
            Assert.AreEqual(cap, PlaytestRunner.StepTimeout(null, PlaytestRunner.MaxSecondsPerJob));
            Assert.AreEqual(30, PlaytestRunner.StepTimeout(30, PlaytestRunner.MaxSecondsPerJob));
            Assert.AreEqual(cap, PlaytestRunner.StepTimeout(1e9, PlaytestRunner.MaxSecondsPerJob));
            Assert.AreEqual(cap, PlaytestRunner.StepTimeout(double.PositiveInfinity, PlaytestRunner.MaxSecondsPerJob));
            Assert.AreEqual(12, PlaytestRunner.StepTimeout(1e9, 12));
            Assert.LessOrEqual(PlaytestRunner.StepTimeout(30, -5), 0);
        }

        [Test]
        public void ConfineToAssets_AcceptsOnlyPathsUnderAssets()
        {
            Assert.IsNotNull(PlaytestRunner.ConfineToAssets(_project, "Assets/Playtests/a.playtest.json", out var err), err);
            Assert.IsNotNull(PlaytestRunner.ConfineToAssets(_project, Path.Combine(_project, "Assets", "Playtests"), out err), err);

            foreach (var bad in new[]
            {
                "Assets/../outside.playtest.json",
                "Assets/Playtests/../../../outside/case.playtest.json",
                "ProjectSettings/x.json",
                "AssetsX/case.playtest.json",
                Path.Combine(_tmp, "outside", "case.playtest.json"),
            })
            {
                Assert.IsNull(PlaytestRunner.ConfineToAssets(_project, bad, out err), bad);
                StringAssert.Contains("outside the project's Assets folder", err, bad);
            }
        }

        [Test]
        public void ConfineToAssets_RefusesLinkOutOfTheProject()
        {
            _link = Path.Combine(_project, "Assets", "Playtests", "linked");
            string target = Path.Combine(_tmp, "outside");
            var psi = Path.DirectorySeparatorChar == '\\'
                ? new ProcessStartInfo("cmd.exe", $"/c mklink /J \"{_link}\" \"{target}\"")
                : new ProcessStartInfo("ln", $"-s \"{target}\" \"{_link}\"");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            using (var p = Process.Start(psi))
            {
                if (!p.WaitForExit(10000)) p.Kill();
            }
            if (!File.Exists(Path.Combine(_link, "case.playtest.json")))
                Assert.Ignore("could not create a directory link on this machine");

            Assert.IsNull(PlaytestRunner.ConfineToAssets(_project, "Assets/Playtests/linked/case.playtest.json", out var err));
            StringAssert.Contains("junction or symbolic link", err);
            Assert.IsNull(PlaytestRunner.ConfineToAssets(_project, "Assets/Playtests/linked", out err));
            StringAssert.Contains("junction or symbolic link", err);
        }

        [Test]
        public void ConfineToAssets_RejectsLinkedAssetsRoot()
        {
            string linkedProject = Path.Combine(_tmp, "linked-project");
            Directory.CreateDirectory(linkedProject);
            _link = Path.Combine(linkedProject, "Assets");
            string target = Path.Combine(_tmp, "outside");
            var psi = Path.DirectorySeparatorChar == '\\'
                ? new ProcessStartInfo("cmd.exe", $"/c mklink /J \"{_link}\" \"{target}\"")
                : new ProcessStartInfo("ln", $"-s \"{target}\" \"{_link}\"");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            using (var p = Process.Start(psi))
            {
                if (!p.WaitForExit(10000)) p.Kill();
            }
            if (!File.Exists(Path.Combine(_link, "case.playtest.json")))
                Assert.Ignore("could not create a directory link on this machine");

            Assert.IsNull(PlaytestRunner.ConfineToAssets(linkedProject, "Assets/case.playtest.json", out var err));
            StringAssert.Contains("junction or symbolic link", err);
        }

        [Test]
        public void RunPlaytest_RejectsPathOutsideAssets()
        {
            var run = JObject.FromObject(RunPlaytestTool.HandleCommand(new JObject { ["path"] = "Assets/../ProjectSettings" }));
            Assert.IsFalse((bool)run["success"]);
            StringAssert.Contains("outside the project's Assets folder", (string)run["error"]);
        }
    }
}
