using System.Collections.Generic;
using MCPForUnity.Runtime.Playtest;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.Playtest
{
    public class PlaytestJsonTests
    {
        private enum Mode { Idle, Running }

        [Test]
        public void ToJson_VectorsAndColours_AreArrays()
        {
            Assert.IsTrue(JToken.DeepEquals(new JArray(1.0, 2.0, 3.0), PlaytestJson.ToJson(new Vector3(1, 2, 3))));
            Assert.IsTrue(JToken.DeepEquals(new JArray(1.0, 2.0), PlaytestJson.ToJson(new Vector2(1, 2))));
            Assert.IsTrue(JToken.DeepEquals(new JArray(0.0, 0.0, 0.0, 1.0), PlaytestJson.ToJson(Quaternion.identity)));
            Assert.IsTrue(JToken.DeepEquals(new JArray(1.0, 0.0, 0.0, 1.0), PlaytestJson.ToJson(Color.red)));
            Assert.IsTrue(JToken.DeepEquals(new JArray(1.0, 2.0, 3.0, 4.0), PlaytestJson.ToJson(new Vector4(1, 2, 3, 4))));
        }

        [Test]
        public void ToJson_Float_UsesShortestText()
        {
            Assert.AreEqual(0.1, PlaytestJson.ToJson(0.1f).Value<double>());
            Assert.AreEqual(JTokenType.Float, PlaytestJson.ToJson(0.1f).Type);
        }

        [Test]
        public void ToJson_EnumIsName_CollectionsRecurse()
        {
            Assert.AreEqual("Running", PlaytestJson.ToJson(Mode.Running).ToString());
            var list = PlaytestJson.ToJson(new List<Vector2> { Vector2.one });
            Assert.IsTrue(JToken.DeepEquals(new JArray(new JArray(1.0, 1.0)), list));
            var dict = (JObject)PlaytestJson.ToJson(new Dictionary<string, int> { ["a"] = 3 });
            Assert.AreEqual(3, dict["a"].Value<int>());
            Assert.AreEqual(JTokenType.Null, PlaytestJson.ToJson(null).Type);
        }

        [Test]
        public void FromJson_ReadsArraysObjectsAndEnumNames()
        {
            Assert.AreEqual(new Vector3(1, 2, 3), PlaytestJson.FromJson(new JArray(1, 2, 3), typeof(Vector3)));
            Assert.AreEqual(new Vector3(1, 2, 3), PlaytestJson.FromJson(new JObject { ["x"] = 1, ["y"] = 2, ["z"] = 3 }, typeof(Vector3)));
            Assert.AreEqual(Mode.Running, PlaytestJson.FromJson(new JValue("running"), typeof(Mode)));
            Assert.AreEqual(7, PlaytestJson.FromJson(new JValue(7), typeof(int)));
        }

        [Test]
        public void Compare_NumbersUseTolerance_ArraysElementwise()
        {
            Assert.IsTrue(PlaytestJson.Compare(new JValue(1.00001), "==", new JValue(1), out _));
            Assert.IsFalse(PlaytestJson.Compare(new JValue(1.001), "==", new JValue(1), out _));
            Assert.IsTrue(PlaytestJson.Compare(new JArray(1.0, 2.00001), "==", new JArray(1, 2), out _));
            Assert.IsTrue(PlaytestJson.Compare(new JValue(true), "==", new JValue(true), out _));
            Assert.IsTrue(PlaytestJson.Compare(new JValue(3), ">=", new JValue(3), out _));
            Assert.IsTrue(PlaytestJson.Compare(new JValue(13.9), ">", new JValue(12.5), out _));
            Assert.IsTrue(PlaytestJson.Compare(new JValue("a"), "!=", new JValue("b"), out _));
        }

        [Test]
        public void Compare_OrderingOnNonNumbers_ReportsError()
        {
            Assert.IsFalse(PlaytestJson.Compare(new JValue(true), ">", new JValue(1), out var err));
            StringAssert.Contains("needs numbers", err);
            Assert.IsFalse(PlaytestJson.Compare(new JValue(1), "~", new JValue(1), out err));
            StringAssert.Contains("unknown op", err);
        }

        [Test]
        public void StateHash_IgnoresKeyOrderAndNoiseBelowTolerance()
        {
            var a = new JObject { ["b"] = 1.00001, ["a"] = new JArray(0.0, 2.0) };
            var b = new JObject { ["a"] = new JArray(-0.0, 2.0), ["b"] = 1.0 };
            var c = new JObject { ["a"] = new JArray(0.0, 2.0), ["b"] = 1.001 };
            Assert.AreEqual(PlaytestJson.StateHash(a), PlaytestJson.StateHash(b));
            Assert.AreNotEqual(PlaytestJson.StateHash(a), PlaytestJson.StateHash(c));
            Assert.AreEqual(16, PlaytestJson.StateHash(a).Length);
        }
    }
}
