using System.Diagnostics;
using System.IO;
using MCPForUnity.Editor.Tools.Playtest;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools.Playtest
{
    public class PlaytestRunnerTests
    {
        private string _tmp;
        private string _project;
        private string _link;

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
        public void RunPlaytest_RejectsPathOutsideAssets()
        {
            var run = JObject.FromObject(RunPlaytestTool.HandleCommand(new JObject { ["path"] = "Assets/../ProjectSettings" }));
            Assert.IsFalse((bool)run["success"]);
            StringAssert.Contains("outside the project's Assets folder", (string)run["error"]);
        }
    }
}
