using System;
using System.Reflection;
using MCPForUnity.Editor.Tools.Playtest;
using MCPForUnity.Runtime.Playtest;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.Playtest
{
    public class PlaytestSessionTests
    {
        private const string SessionKey = "MCPForUnity.Playtest.Session";
        private const BindingFlags Private = BindingFlags.NonPublic | BindingFlags.Static;

        private FieldInfo _stateField;
        private object _savedState;
        private string _savedJson;
        private float _captureDt;
        private float _fixedDt;
        private bool _runInBackground;

        [SetUp]
        public void SetUp()
        {
            _stateField = typeof(PlaytestSession).GetField("s", Private);
            _savedState = _stateField.GetValue(null);
            _savedJson = SessionState.GetString(SessionKey, "");
            _captureDt = Time.captureDeltaTime;
            _fixedDt = Time.fixedDeltaTime;
            _runInBackground = Application.runInBackground;
        }

        [TearDown]
        public void TearDown()
        {
            Time.captureDeltaTime = _captureDt;
            Time.fixedDeltaTime = _fixedDt;
            Application.runInBackground = _runInBackground;
            _stateField.SetValue(null, _savedState);
            SessionState.SetString(SessionKey, _savedJson);
        }

        private object NewState(params (string field, object value)[] values)
        {
            var state = Activator.CreateInstance(_savedState.GetType());
            foreach (var (field, value) in values) state.GetType().GetField(field).SetValue(state, value);
            _stateField.SetValue(null, state);
            return state;
        }

        [Test]
        public void FailedStart_RestoresRuntimeSettings()
        {
            var state = NewState(
                ("op", PlaytestSession.OpStart), ("phase", "apply"), ("timeout", 60.0),
                ("opStarted", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0),
                ("active", true), ("origCaptureDt", _captureDt), ("origFixedDt", _fixedDt),
                ("origRunInBackground", _runInBackground));
            Time.captureDeltaTime = 1f / 30f;
            Time.fixedDeltaTime = 1f / 30f;
            Application.runInBackground = !_runInBackground;

            typeof(PlaytestSession).GetMethod("Fail", Private).Invoke(null, new object[] { "level.restart failed: boom" });

            Assert.AreEqual(_captureDt, Time.captureDeltaTime, 1e-6f);
            Assert.AreEqual(_fixedDt, Time.fixedDeltaTime, 1e-6f);
            Assert.AreEqual(_runInBackground, Application.runInBackground);
            Assert.IsFalse((bool)state.GetType().GetField("active").GetValue(state));
            Assert.IsFalse(PlaytestSession.Busy);
            Assert.IsTrue(PlaytestSession.TakeOutcome(out _, out var isError, out var message));
            Assert.IsTrue(isError);
            StringAssert.Contains("level.restart failed", message);
        }

        [Test]
        public void StaleSessionInEditMode_RestoresRuntimeAndInputOriginals()
        {
            var state = NewState(
                ("active", true), ("origCaptureDt", _captureDt), ("origFixedDt", _fixedDt),
                ("origRunInBackground", _runInBackground));
            Time.captureDeltaTime = 1f / 30f;
            Time.fixedDeltaTime = 1f / 30f;
            Application.runInBackground = !_runInBackground;

            object settings = null;
            PropertyInfo background = null, editorBehavior = null;
            object background0 = null, editorBehavior0 = null;
            if (PlaytestInput.Supported)
            {
                settings = PlaytestInput.SettingsObject;
                background = settings.GetType().GetProperty("backgroundBehavior");
                editorBehavior = settings.GetType().GetProperty("editorInputBehaviorInPlayMode");
                background0 = background.GetValue(settings);
                editorBehavior0 = editorBehavior.GetValue(settings);
                PlaytestInput.BeginSession();
                // A domain reload clears PlaytestInput's statics; only the SessionState originals remain.
                typeof(PlaytestInput).GetField("s_SessionActive", Private).SetValue(null, false);
                typeof(PlaytestInput).GetField("s_Keyboard", Private).SetValue(null, null);
            }

            try
            {
                typeof(PlaytestSession).GetMethod("CloseStaleSession", Private).Invoke(null, null);

                Assert.AreEqual(_captureDt, Time.captureDeltaTime, 1e-6f);
                Assert.AreEqual(_fixedDt, Time.fixedDeltaTime, 1e-6f);
                Assert.AreEqual(_runInBackground, Application.runInBackground);
                Assert.IsFalse((bool)state.GetType().GetField("active").GetValue(state));
                if (settings != null)
                {
                    Assert.AreEqual(background0, background.GetValue(settings));
                    Assert.AreEqual(editorBehavior0, editorBehavior.GetValue(settings));
                    Assert.AreEqual("", SessionState.GetString("MCPForUnity.Playtest.InputOriginals", ""));
                }
            }
            finally
            {
                if (settings != null)
                {
                    PlaytestInput.EndSession();
                    background.SetValue(settings, background0);
                    editorBehavior.SetValue(settings, editorBehavior0);
                }
            }
        }
    }
}
