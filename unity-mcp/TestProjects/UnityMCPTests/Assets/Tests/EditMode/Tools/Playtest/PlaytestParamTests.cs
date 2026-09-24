using MCPForUnity.Editor.Tools.Playtest;
using MCPForUnity.Runtime.Playtest;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools.Playtest
{
    public class PlaytestParamTests
    {
        private static string Parse(JObject p) => PlaytestStepper.Parse(p, out _);

        [Test]
        public void Step_FramesRange()
        {
            StringAssert.Contains("required", Parse(new JObject()));
            StringAssert.Contains("1..3600", Parse(new JObject { ["frames"] = 0 }));
            StringAssert.Contains("1..3600", Parse(new JObject { ["frames"] = 3601 }));
            Assert.IsNull(Parse(new JObject { ["frames"] = 3600 }));
        }

        [Test]
        public void Step_InputUntilCaptureValidation()
        {
            StringAssert.Contains("outside", Parse(new JObject { ["frames"] = 5, ["input"] = new JArray(new JObject { ["frame"] = 5, ["key"] = "space" }) }));
            StringAssert.Contains("until.op", Parse(new JObject { ["frames"] = 5, ["until"] = new JObject { ["hook"] = "a", ["op"] = "=" } }));
            StringAssert.Contains("capture", Parse(new JObject { ["frames"] = 5, ["capture"] = "start" }));
            StringAssert.Contains("outside", Parse(new JObject { ["frames"] = 5, ["capture"] = new JArray(7) }));

            Assert.IsNull(PlaytestStepper.Parse(new JObject
            {
                ["frames"] = 10,
                ["input"] = new JArray(new JObject { ["frame"] = 2, ["key"] = "space", ["do"] = "tap" }),
                ["until"] = new JObject { ["hook"] = "level.won", ["op"] = "==", ["value"] = true },
                ["capture"] = new JArray(0, 9),
                ["autopilot"] = true,
            }, out var spec));
            Assert.AreEqual(10, spec.Frames);
            Assert.IsTrue(spec.Autopilot);
            CollectionAssert.AreEqual(new[] { 0, 9 }, spec.CaptureFrames);
            Assert.AreEqual("level.won", spec.UntilHook);
        }

        [Test]
        public void Input_ScheduleRejectsBadEntries()
        {
            StringAssert.Contains("key, button, axis", PlaytestInput.Schedule(1, new JObject { ["do"] = "tap" }));
            StringAssert.Contains("press, release or tap", PlaytestInput.Schedule(1, new JObject { ["key"] = "space", ["do"] = "hold" }));
            if (!PlaytestInput.Supported)
                Assert.AreEqual(PlaytestInput.UnsupportedMessage, PlaytestInput.Schedule(1, new JObject { ["key"] = "space" }));
            PlaytestInput.ClearQueue();
        }

        [Test]
        public void Tools_RejectOutsidePlayModeOrBadArgs()
        {
            var step = JObject.FromObject(PlayStepTool.HandleCommand(new JObject { ["frames"] = 1 }).Result);
            Assert.IsFalse((bool)step["success"]);
            StringAssert.Contains("play_session start", (string)step["error"]);

            var cap = JObject.FromObject(PlayCaptureTool.HandleCommand(new JObject()));
            Assert.IsFalse((bool)cap["success"]);

            var sess = JObject.FromObject(PlaySessionTool.HandleCommand(new JObject { ["action"] = "jump" }));
            Assert.IsFalse((bool)sess["success"]);

            var bad = JObject.FromObject(PlaySessionTool.HandleCommand(new JObject { ["action"] = "start", ["scene"] = "Assets/NoSuchScene.unity" }));
            Assert.IsFalse((bool)bad["success"]);
            StringAssert.Contains("Scene not found", (string)bad["error"]);

            var run = JObject.FromObject(RunPlaytestTool.HandleCommand(new JObject { ["path"] = "Assets/NoSuchFolder" }));
            Assert.IsFalse((bool)run["success"]);
            StringAssert.Contains("not found", (string)run["error"]);
        }
    }
}
