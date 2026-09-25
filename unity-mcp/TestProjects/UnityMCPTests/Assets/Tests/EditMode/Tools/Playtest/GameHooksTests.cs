using System.Linq;
using MCPForUnity.Editor.Tools.Playtest;
using MCPForUnity.Runtime.Playtest;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.Playtest
{
    internal static class PlaytestHookFixture
    {
        [GameHook("test.fixture.position")] public static Vector3 Position => new Vector3(1, 2, 3);
        [GameHook("test.fixture.count")] public static int Count = 4;
        public static int LastSeed;

        [GameHook("test.fixture.restart")]
        public static string Restart(int seed, int speed = 2)
        {
            LastSeed = seed;
            return $"{seed}:{speed}";
        }
    }

    public class GameHooksTests
    {
        [SetUp]
        public void SetUp() => PlaytestHookDiscovery.Ensure();

        [TearDown]
        public void TearDown()
        {
            GameHooks.Unregister("test.runtime.value");
            GameHooks.Unregister("test.fixture.count");
            GameHooks.Unregister("test.runtime.noargs");
        }

        [Test]
        public void Discovery_FindsPropertiesFieldsAndMethods()
        {
            var hooks = GameHooks.List();
            var pos = hooks.Single(h => h.Name == "test.fixture.position");
            Assert.AreEqual("state", pos.Kind);
            Assert.AreEqual("Vector3", pos.Type);
            Assert.AreEqual("state", hooks.Single(h => h.Name == "test.fixture.count").Kind);
            var restart = hooks.Single(h => h.Name == "test.fixture.restart");
            Assert.AreEqual("action", restart.Kind);
            Assert.AreEqual("string(int seed, int speed)", restart.Type);
        }

        [Test]
        public void Get_ReturnsSerialisedValue()
        {
            Assert.IsTrue(GameHooks.TryGet("test.fixture.position", out var v, out _));
            Assert.IsTrue(JToken.DeepEquals(new JArray(1.0, 2.0, 3.0), v));
        }

        [Test]
        public void Call_BindsArgsByName_UsesDefaults()
        {
            Assert.IsTrue(GameHooks.TryCall("test.fixture.restart", new JObject { ["seed"] = 9 }, out var r, out _));
            Assert.AreEqual("9:2", r.ToString());
            Assert.AreEqual(9, PlaytestHookFixture.LastSeed);
            Assert.IsFalse(GameHooks.TryCall("test.fixture.restart", new JObject(), out _, out var err));
            StringAssert.Contains("missing argument 'seed'", err);
        }

        [Test]
        public void Unknown_ListsCloseMatches()
        {
            Assert.IsFalse(GameHooks.TryGet("test.fixture.postion", out _, out var err));
            StringAssert.Contains("test.fixture.position", err);
        }

        [Test]
        public void RuntimeRegistration_OverridesAndUnregisters()
        {
            GameHooks.State("test.runtime.value", () => 5);
            GameHooks.State("test.fixture.count", () => 99);
            Assert.IsTrue(GameHooks.TryGet("test.runtime.value", out var v, out _));
            Assert.AreEqual(5, v.Value<int>());
            Assert.IsTrue(GameHooks.TryGet("test.fixture.count", out v, out _));
            Assert.AreEqual(99, v.Value<int>());
            GameHooks.Unregister("test.fixture.count");
            Assert.IsTrue(GameHooks.TryGet("test.fixture.count", out v, out _));
            Assert.AreEqual(4, v.Value<int>());

            GameHooks.Action("test.runtime.value", args => args?["x"]?.Value<int>() * 2);
            Assert.IsTrue(GameHooks.TryCall("test.runtime.value", new JObject { ["x"] = 21 }, out var r, out _));
            Assert.AreEqual(42, r.Value<int>());
        }

        [Test]
        public void Get_RefusesActionHooksWithoutInvokingThem()
        {
            int calls = 0;
            GameHooks.Action("test.runtime.noargs", _ => ++calls);
            PlaytestHookFixture.LastSeed = -1;

            Assert.IsFalse(GameHooks.TryGet("test.runtime.noargs", out var v, out var err));
            Assert.IsNull(v);
            StringAssert.Contains("is an action", err);
            Assert.IsFalse(GameHooks.TryGet("test.fixture.restart", out _, out _));

            var named = JObject.FromObject(GameHooksTool.HandleCommand(new JObject
            { ["action"] = "get", ["names"] = new JArray("test.fixture.count", "test.runtime.noargs") }));
            Assert.IsFalse((bool)named["success"]);
            StringAssert.Contains("game_hooks call", (string)named["error"]);
            Assert.AreEqual("action", (string)named["data"]["kind"]);

            var single = JObject.FromObject(GameHooksTool.HandleCommand(new JObject { ["action"] = "get", ["name"] = "test.fixture.restart" }));
            Assert.IsFalse((bool)single["success"]);

            var all = JObject.FromObject(GameHooksTool.HandleCommand(new JObject { ["action"] = "get" }));
            Assert.IsTrue((bool)all["success"]);
            var values = (JObject)all["data"]["values"];
            Assert.IsNotNull(values["test.fixture.count"]);
            Assert.IsNull(values["test.runtime.noargs"]);
            Assert.IsNull(values["test.fixture.restart"]);
            var actions = all["data"]["actions"].Select(t => (string)t).ToList();
            CollectionAssert.Contains(actions, "test.runtime.noargs");
            CollectionAssert.Contains(actions, "test.fixture.restart");

            Assert.AreEqual(0, calls);
            Assert.AreEqual(-1, PlaytestHookFixture.LastSeed);
        }

        [Test]
        public void GameHooksTool_ListGetCallAndUnknown()
        {
            var list = JObject.FromObject(GameHooksTool.HandleCommand(new JObject { ["action"] = "list" }));
            Assert.IsTrue((bool)list["success"]);
            Assert.IsTrue(list["data"]["hooks"].Any(h => (string)h["name"] == "test.fixture.count"));

            var get = JObject.FromObject(GameHooksTool.HandleCommand(new JObject { ["action"] = "get", ["names"] = new JArray("test.fixture.count") }));
            Assert.AreEqual(4, get["data"]["values"]["test.fixture.count"].Value<int>());

            var call = JObject.FromObject(GameHooksTool.HandleCommand(new JObject
            { ["action"] = "call", ["name"] = "test.fixture.restart", ["args"] = new JObject { ["seed"] = 1, ["speed"] = 5 } }));
            Assert.AreEqual("1:5", call["data"]["result"].ToString());

            var bad = JObject.FromObject(GameHooksTool.HandleCommand(new JObject { ["action"] = "get", ["names"] = new JArray("test.fixture.cont") }));
            Assert.IsFalse((bool)bad["success"]);
            StringAssert.Contains("test.fixture.count", (string)bad["error"]);
        }
    }
}
